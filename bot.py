import os
import time
import requests
import asyncio
import threading
import traceback # <-- Import the traceback module
from yt_dlp import YoutubeDL
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask

# --- Flask Web Server Setup ---
server = Flask(__name__)
@server.route('/')
def health_check():
    return "Bot is alive!", 200

def run_server():
    port = int(os.environ.get('PORT', 8080))
    server.run(host='0.0.0.0', port=port)

# --- Bot Configuration ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID", 0))
DOWNLOAD_LOCATION = "./downloads/"
SUPPORTED_SITES = ["xvideos.com", "pornhub.com", "xnxx.com", "xhamster.com", "erome.com"]

# --- Pyrogram Client ---
app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Helper Functions (Unchanged) ---
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
                except Exception: pass

async def upload_progress_callback(current, total, message: Message):
    percent = (current / total) * 100
    now = time.time()
    if now - globals().get('last_upload_update_time', 0) > 2:
        try:
            await message.edit_text(f"**Uploading to Telegram...**\n**Progress:** {percent:.2f}%")
            globals()['last_upload_update_time'] = now
        except Exception: pass

# --- Main Bot Logic (UPGRADED WITH DETAILED LOGGING) ---
@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client: Client, message: Message):
    url = message.text.strip()
    print(f"[{message.chat.id}] Received URL: {url}") # <-- LOG

    if not any(site in url for site in SUPPORTED_SITES):
        print(f"[{message.chat.id}] Unsupported site.") # <-- LOG
        await message.reply_text("❌ **Sorry, this website is not supported.**")
        return

    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True)

    video_path = None
    thumbnail_path = None
    try:
        await status_message.edit_text("🔄 **Fetching video metadata...**")
        print(f"[{message.chat.id}] Fetching metadata from yt-dlp...") # <-- LOG
        
        ydl_opts = {
            'format': 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(DOWNLOAD_LOCATION, '%(title)s.%(ext)s'),
            'noplaylist': True, 'quiet': True,
            'progress_hooks': [lambda d: progress_hook(d, status_message, globals().get('last_update_time', 0))],
            # 'max_filesize': 50 * 1024 * 1024, # <-- Optional: Uncomment this line to test with small files
        }

        with YoutubeDL(ydl_opts) as ydl:
            globals()['last_update_time'] = time.time()
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Untitled Video')
            webpage_url = info.get('webpage_url', url)
            print(f"[{message.chat.id}] Metadata found. Title: {video_title}") # <-- LOG

            safe_title = "".join([c for c in video_title if c.isalpha() or c.isdigit() or c in ' ._-']).rstrip()
            
            print(f"[{message.chat.id}] Starting download...") # <-- LOG
            ydl.download([url])
            print(f"[{message.chat.id}] Download function finished.") # <-- LOG
            
            downloaded_files = [f for f in os.listdir(DOWNLOAD_LOCATION) if f.startswith(safe_title)]
            if not downloaded_files:
                print(f"[{message.chat.id}] ERROR: File not found after download!") # <-- LOG
                raise FileNotFoundError("Downloaded file not found in the directory.")
            
            video_path = os.path.join(DOWNLOAD_LOCATION, downloaded_files[0])
            print(f"[{message.chat.id}] Video path identified: {video_path}") # <-- LOG

        thumbnail_url = info.get('thumbnail')
        if thumbnail_url:
            thumbnail_path = os.path.join(DOWNLOAD_LOCATION, f"{safe_title}.jpg")
            print(f"[{message.chat.id}] Downloading thumbnail from {thumbnail_url}") # <-- LOG
            try:
                with requests.get(thumbnail_url, stream=True) as r:
                    r.raise_for_status()
                    with open(thumbnail_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
            except Exception as e:
                print(f"[{message.chat.id}] Could not download thumbnail: {e}") # <-- LOG
                thumbnail_path = None
        
        await status_message.edit_text("⬆️ **Uploading to Telegram...**")
        print(f"[{message.chat.id}] Starting upload of {video_path} to Telegram.") # <-- LOG
        globals()['last_upload_update_time'] = time.time()
        
        caption = f"**Title:** {video_title}\n**Source:** {webpage_url}"

        sent_message = await client.send_video(
            chat_id=message.chat.id, video=video_path, caption=caption, thumb=thumbnail_path,
            supports_streaming=True, progress=upload_progress_callback, progress_args=(status_message,)
        )
        print(f"[{message.chat.id}] Upload to user successful.") # <-- LOG
        
        await status_message.edit_text("✅ **Upload complete!**")

        if sent_message and DUMP_CHANNEL_ID != 0:
            print(f"[{message.chat.id}] Forwarding message to dump channel: {DUMP_CHANNEL_ID}") # <-- LOG
            await sent_message.forward(DUMP_CHANNEL_ID)
            await status_message.edit_text("✅ **Upload complete and archived!**")

    except Exception as e:
        # ---- THIS IS THE MOST IMPORTANT CHANGE ----
        # It will print the full, detailed error to your logs
        print("\n\n------ ERROR ------\n")
        traceback.print_exc()
        print("\n------ END ERROR ------\n\n")
        await status_message.edit_text(f"❌ **An error occurred during the process.**\n\nPlease check the logs for details.")
    finally:
        print(f"[{message.chat.id}] Starting cleanup process.") # <-- LOG
        if video_path and os.path.exists(video_path):
            os.remove(video_path)
            print(f"[{message.chat.id}] Cleaned up video file: {video_path}") # <-- LOG
        if thumbnail_path and os.path.exists(thumbnail_path):
            os.remove(thumbnail_path)
            print(f"[{message.chat.id}] Cleaned up thumbnail file: {thumbnail_path}") # <-- LOG
        await asyncio.sleep(5)
        await status_message.delete()

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await message.reply_text("Hello! I am a Video Downloader Bot.")

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_LOCATION):
        os.makedirs(DOWNLOAD_LOCATION)
    
    flask_thread = threading.Thread(target=run_server)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("Bot is starting...")
    app.run()