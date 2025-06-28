import os
import time
import requests
import asyncio
from yt_dlp import YoutubeDL
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Configuration ---
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
DUMP_CHANNEL_ID = int(os.getenv("DUMP_CHANNEL_ID"))
DOWNLOAD_LOCATION = "./downloads/"

# List of supported sites (domains)
SUPPORTED_SITES = ["xvideos.com", "pornhub.com", "xnxx.com", "xhamster.com", "erome.com"]

# --- Pyrogram Client ---
app = Client(
    "video_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# --- Helper Functions ---

# Progress hook for yt-dlp to show download status
def progress_hook(d, message: Message, start_time):
    if d['status'] == 'downloading':
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
        if total_bytes:
            downloaded_bytes = d.get('downloaded_bytes')
            speed = d.get('speed') or 0
            eta = d.get('eta') or 0
            percent = (downloaded_bytes / total_bytes) * 100
            
            # Throttle updates to avoid hitting Telegram API limits
            now = time.time()
            if now - start_time > 2: # Update every 2 seconds
                try:
                    # Non-blocking edit message
                    asyncio.create_task(message.edit_text(
                        f"**Downloading...**\n"
                        f"**Progress:** {percent:.2f}%\n"
                        f"**Speed:** {speed / 1024 / 1024:.2f} MB/s\n"
                        f"**ETA:** {eta}s"
                    ))
                    globals()['last_update_time'] = now
                except Exception:
                    pass

# Progress callback for Pyrogram to show upload status
async def upload_progress_callback(current, total, message: Message):
    percent = (current / total) * 100
    now = time.time()
    
    # Throttle updates
    if now - globals().get('last_upload_update_time', 0) > 2:
        try:
            await message.edit_text(
                f"**Uploading to Telegram...**\n"
                f"**Progress:** {percent:.2f}%"
            )
            globals()['last_upload_update_time'] = now
        except Exception:
            pass

# --- The Main Handler ---

@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client: Client, message: Message):
    url = message.text.strip()
    
    # Check if the URL is from a supported site
    if not any(site in url for site in SUPPORTED_SITES):
        await message.reply_text("❌ **Sorry, this website is not supported.**")
        return

    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True)

    try:
        # --- 1. Get Video Info & Download ---
        await status_message.edit_text("🔄 **Fetching video metadata...**")
        
        ydl_opts = {
            'format': 'best[ext=mp4][height<=720]/best[ext=mp4]/best', # Prioritize 720p mp4
            'outtmpl': os.path.join(DOWNLOAD_LOCATION, '%(title)s.%(ext)s'),
            'noplaylist': True,
            'quiet': True,
            'progress_hooks': [lambda d: progress_hook(d, status_message, globals().get('last_update_time', 0))]
        }

        with YoutubeDL(ydl_opts) as ydl:
            globals()['last_update_time'] = time.time()
            info = ydl.extract_info(url, download=False) # First, get info without downloading
            
            video_title = info.get('title', 'Untitled Video')
            video_ext = info.get('ext', 'mp4')
            thumbnail_url = info.get('thumbnail')
            webpage_url = info.get('webpage_url', url)
            
            # Clean filename
            safe_title = "".join([c for c in video_title if c.isalpha() or c.isdigit() or c in ' ._-']).rstrip()
            video_path = os.path.join(DOWNLOAD_LOCATION, f"{safe_title}.{video_ext}")

            ydl.download([url]) # Now, download the video

        # --- 2. Download Thumbnail ---
        thumbnail_path = None
        if thumbnail_url:
            thumbnail_path = os.path.join(DOWNLOAD_LOCATION, f"{safe_title}.jpg")
            try:
                with requests.get(thumbnail_url, stream=True) as r:
                    r.raise_for_status()
                    with open(thumbnail_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
            except Exception as e:
                print(f"Could not download thumbnail: {e}")
                thumbnail_path = None # Reset if download fails
        
        # --- 3. Upload Video to User ---
        await status_message.edit_text("⬆️ **Uploading to Telegram...**")
        globals()['last_upload_update_time'] = time.time()
        
        caption = f"**Title:** {video_title}\n**Source:** {webpage_url}"

        sent_message = await client.send_video(
            chat_id=message.chat.id,
            video=video_path,
            caption=caption,
            thumb=thumbnail_path,
            file_name=f"{safe_title}.{video_ext}",
            supports_streaming=True,
            progress=upload_progress_callback,
            progress_args=(status_message,)
        )
        
        await status_message.edit_text("✅ **Upload complete!**")

        # --- 4. Forward to Dump Channel ---
        if sent_message:
            await sent_message.forward(DUMP_CHANNEL_ID)
            await status_message.edit_text("✅ **Upload complete and archived!**")

    except Exception as e:
        await status_message.edit_text(f"❌ **An error occurred:**\n`{e}`")
    finally:
        # --- 5. Cleanup ---
        if 'video_path' in locals() and os.path.exists(video_path):
            os.remove(video_path)
        if 'thumbnail_path' in locals() and thumbnail_path and os.path.exists(thumbnail_path):
            os.remove(thumbnail_path)
        # We can delete the status message after a few seconds
        await asyncio.sleep(5)
        await status_message.delete()


@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await message.reply_text(
        "**Hello! I am a Video Downloader Bot.**\n\n"
        "Send me a link from one of the supported sites, and I will download it for you.\n\n"
        "**Supported Sites:**\n"
        "• Pornhub\n• XVideos\n• XNXX\n• xHamster\n• Erome"
    )

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_LOCATION):
        os.makedirs(DOWNLOAD_LOCATION)
    
    print("Bot is starting...")
    app.run()
    print("Bot has stopped.")
