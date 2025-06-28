import os
import time
import asyncio
import threading
import traceback
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
# ... (The helper functions progress_hook and upload_progress_callback are the same)
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

# --- Main Bot Logic (UPGRADED for ROBUST THUMBNAIL HANDLING) ---
@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client: Client, message: Message):
    url = message.text.strip()
    print(f"[{message.chat.id}] Received URL: {url}")

    if not any(site in url for site in SUPPORTED_SITES):
        await message.reply_text("❌ **Sorry, this website is not supported.**")
        return

    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True)

    video_path = None
    thumbnail_path = None
    try:
        await status_message.edit_text("🔄 **Downloading video and thumbnail...**")
        print(f"[{message.chat.id}] Handing over to yt-dlp...")
        
        # ---- WHAT'S CHANGED: YDL_OPTS ----
        # We now tell yt-dlp to download the thumbnail for us.
        ydl_opts = {
            'format': 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(DOWNLOAD_LOCATION, '%(title)s.%(ext)s'),
            'writethumbnail': True,  # <-- The magic setting!
            'noplaylist': True,
            'quiet': True,
            'progress_hooks': [lambda d: progress_hook(d, status_message, globals().get('last_update_time', 0))],
        }

        with YoutubeDL(ydl_opts) as ydl:
            globals()['last_update_time'] = time.time()
            # First get info to grab the title and URL
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Untitled Video')
            webpage_url = info.get('webpage_url', url)
            print(f"[{message.chat.id}] Metadata found. Title: {video_title}")

            # Now perform the actual download (video + thumbnail)
            ydl.download([url])
            print(f"[{message.chat.id}] yt-dlp download process finished.")
            
            # ---- WHAT'S CHANGED: FINDING THE FILES ----
            # We need to find the video and thumbnail file yt-dlp created.
            safe_title = "".join([c for c in video_title if c.isalpha() or c.isdigit() or c in ' ._-']).rstrip()
            all_files = os.listdir(DOWNLOAD_LOCATION)
            
            # Find the video file (it could be .mp4, .mkv, .webm, etc.)
            for f in all_files:
                if f.startswith(safe_title) and f.endswith(('.mp4', '.mkv', '.webm')):
                    video_path = os.path.join(DOWNLOAD_LOCATION, f)
                    break
            
            # Find the thumbnail file (it could be .jpg, .webp, etc.)
            for f in all_files:
                if f.startswith(safe_title) and f.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    thumbnail_path = os.path.join(DOWNLOAD_LOCATION, f)
                    break

            if not video_path:
                raise FileNotFoundError(f"Could not find downloaded video file for title: {safe_title}")

            print(f"[{message.chat.id}] Video path identified: {video_path}")
            print(f"[{message.chat.id}] Thumbnail path identified: {thumbnail_path}") # Will be None if not found
        
        await status_message.edit_text("⬆️ **Uploading to Telegram...**")
        print(f"[{message.chat.id}] Starting upload of {video_path} to user.")
        globals()['last_upload_update_time'] = time.time()
        
        caption = f"**Title:** {video_title}\n**Source:** {webpage_url}"

        # Send the video to the user
        sent_message_to_user = await client.send_video(
            chat_id=message.chat.id, video=video_path, caption=caption, thumb=thumbnail_path,
            supports_streaming=True, progress=upload_progress_callback, progress_args=(status_message,)
        )
        print(f"[{message.chat.id}] Upload to user successful.")
        
        await status_message.edit_text("✅ **Upload complete! Archiving...**")

        # Send a separate copy to the dump channel
        if sent_message_to_user and DUMP_CHANNEL_ID != 0:
            print(f"[{message.chat.id}] Sending copy to dump channel: {DUMP_CHANNEL_ID}")
            await client.send_video(
                chat_id=DUMP_CHANNEL_ID, video=video_path, thumb=thumbnail_path,
                caption=caption, supports_streaming=True
            )
            await status_message.edit_text("✅ **Upload complete and archived!**")

    except Exception as e:
        print("\n\n------ ERROR ------\n")
        traceback.print_exc()
        print("\n------ END ERROR ------\n\n")
        await status_message.edit_text(f"❌ **An error occurred.** Check logs for details.")
    finally:
        print(f"[{message.chat.id}] Starting cleanup process.")
        # The cleanup now needs to be more robust to catch all possible files
        if 'all_files' in locals() and 'safe_title' in locals():
            for f in all_files:
                if f.startswith(safe_title):
                    try:
                        os.remove(os.path.join(DOWNLOAD_LOCATION, f))
                        print(f"[{message.chat.id}] Cleaned up file: {f}")
                    except OSError as e:
                        print(f"Error removing file {f}: {e}")
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