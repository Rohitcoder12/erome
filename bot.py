import os
import time
import requests
import asyncio
import threading
from yt_dlp import YoutubeDL
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask

# --- Flask Web Server Setup ---
# This part is to keep the bot alive on Koyeb's free Web Service tier.
server = Flask(__name__)

@server.route('/')
def health_check():
    """Health check endpoint to keep the service alive."""
    return "Bot is alive!", 200

def run_server():
    """Runs the Flask server in a separate thread."""
    # Get the port from the environment variable KOYEB_HTTP_PORT or default to 8080
    port = int(os.environ.get('PORT', 8080))
    server.run(host='0.0.0.0', port=port)

# --- Bot Configuration ---
# Read directly from environment variables provided by Koyeb
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID", 0)) # Default to 0 if not set
DOWNLOAD_LOCATION = "./downloads/"

SUPPORTED_SITES = ["xvideos.com", "pornhub.com", "xnxx.com", "xhamster.com", "erome.com"]

# --- Pyrogram Client ---
app = Client(
    "video_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# --- Helper Functions (No changes here) ---

def progress_hook(d, message: Message, start_time):
    if d['status'] == 'downloading':
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
        if total_bytes:
            downloaded_bytes = d.get('downloaded_bytes')
            speed = d.get('speed') or 0
            eta = d.get('eta') or 0
            percent = (downloaded_bytes / total_bytes) * 100
            
            now = time.time()
            if now - globals().get('last_update_time', 0) > 2:
                try:
                    asyncio.create_task(message.edit_text(
                        f"**Downloading...**\n"
                        f"**Progress:** {percent:.2f}% | **Speed:** {speed / 1024 / 1024:.2f} MB/s | **ETA:** {eta}s"
                    ))
                    globals()['last_update_time'] = now
                except Exception:
                    pass

async def upload_progress_callback(current, total, message: Message):
    percent = (current / total) * 100
    now = time.time()
    if now - globals().get('last_upload_update_time', 0) > 2:
        try:
            await message.edit_text(f"**Uploading to Telegram...**\n**Progress:** {percent:.2f}%")
            globals()['last_upload_update_time'] = now
        except Exception:
            pass

# --- Main Bot Logic (No changes here) ---

@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client: Client, message: Message):
    url = message.text.strip()
    
    if not any(site in url for site in SUPPORTED_SITES):
        await message.reply_text("❌ **Sorry, this website is not supported.**")
        return

    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True)

    video_path = None
    thumbnail_path = None
    try:
        await status_message.edit_text("🔄 **Fetching video metadata...**")
        
        ydl_opts = {
            'format': 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(DOWNLOAD_LOCATION, '%(title)s.%(ext)s'),
            'noplaylist': True, 'quiet': True,
            'progress_hooks': [lambda d: progress_hook(d, status_message, globals().get('last_update_time', 0))]
        }

        with YoutubeDL(ydl_opts) as ydl:
            globals()['last_update_time'] = time.time()
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Untitled Video')
            webpage_url = info.get('webpage_url', url)

            safe_title = "".join([c for c in video_title if c.isalpha() or c.isdigit() or c in ' ._-']).rstrip()
            # Let yt-dlp determine the final extension
            ydl.download([url])
            
            # Find the downloaded file, as ffmpeg might change the extension
            downloaded_files = [f for f in os.listdir(DOWNLOAD_LOCATION) if f.startswith(safe_title)]
            if not downloaded_files: raise FileNotFoundError("Downloaded file not found.")
            video_path = os.path.join(DOWNLOAD_LOCATION, downloaded_files[0])

        thumbnail_url = info.get('thumbnail')
        if thumbnail_url:
            thumbnail_path = os.path.join(DOWNLOAD_LOCATION, f"{safe_title}.jpg")
            try:
                with requests.get(thumbnail_url, stream=True) as r:
                    r.raise_for_status()
                    with open(thumbnail_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
            except Exception as e:
                print(f"Could not download thumbnail: {e}")
                thumbnail_path = None
        
        await status_message.edit_text("⬆️ **Uploading to Telegram...**")
        globals()['last_upload_update_time'] = time.time()
        
        caption = f"**Title:** {video_title}\n**Source:** {webpage_url}"

        sent_message = await client.send_video(
            chat_id=message.chat.id, video=video_path, caption=caption, thumb=thumbnail_path,
            supports_streaming=True, progress=upload_progress_callback, progress_args=(status_message,)
        )
        
        await status_message.edit_text("✅ **Upload complete!**")

        if sent_message and DUMP_CHANNEL_ID != 0:
            await sent_message.forward(DUMP_CHANNEL_ID)
            await status_message.edit_text("✅ **Upload complete and archived!**")

    except Exception as e:
        await status_message.edit_text(f"❌ **An error occurred:**\n`{e}`")
    finally:
        if video_path and os.path.exists(video_path): os.remove(video_path)
        if thumbnail_path and os.path.exists(thumbnail_path): os.remove(thumbnail_path)
        await asyncio.sleep(5)
        await status_message.delete()

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await message.reply_text(
        "**Hello! I am a Video Downloader Bot.**\n\n"
        "Send me a link from one of the supported sites, and I will download it for you.\n\n"
        "**Supported Sites:**\n• Pornhub\n• XVideos\n• XNXX\n• xHamster\n• Erome"
    )

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_LOCATION):
        os.makedirs(DOWNLOAD_LOCATION)
    
    # Start the Flask server in a background thread
    flask_thread = threading.Thread(target=run_server)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("Bot is starting...")
    # Start the Pyrogram bot. This will be the main blocking call.
    app.run()
    print("Bot has stopped.")