import os
import time
import requests
import asyncio
import threading
import traceback
import io # <-- New import for in-memory file handling
from yt_dlp import YoutubeDL
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask
from pymongo import MongoClient
from datetime import datetime, timezone
from PIL import Image # <-- New import for Pillow

# --- Configuration ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID", 0))
DOWNLOAD_LOCATION = "./downloads/"
SUPPORTED_SITES = ["xvideos.com", "pornhub.com", "xnxx.com", "xhamster.com", "erome.com"]

# --- Flask Web Server Setup ---
server = Flask(__name__)
@server.route('/')
def health_check():
    return "Bot and Web Server are alive!", 200
def run_server():
    port = int(os.environ.get('PORT', 8080))
    server.run(host='0.0.0.0', port=port)

# --- Database Setup ---
try:
    db_client = MongoClient(MONGO_URI)
    db = db_client.get_database("VideoBotDB")
    users_collection = db.get_collection("users")
    print("Successfully connected to MongoDB.")
except Exception as e:
    print(f"Error connecting to MongoDB: {e}")
    users_collection = None

# --- Pyrogram Client ---
app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Helper Functions (No Changes) ---
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
                    asyncio.create_task(message.edit_text(f"**Downloading...**\n**Progress:** {percent:.2f}% | **Speed:** {speed / 1024 / 1024:.2f} MB/s | **ETA:** {eta}s"))
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

# --- Bot Commands & Handlers ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user = message.from_user
    if users_collection is not None:
        user_data = {"_id": user.id, "first_name": user.first_name, "last_name": user.last_name, "username": user.username, "last_started": datetime.now(timezone.utc)}
        try:
            users_collection.update_one({"_id": user.id}, {"$set": user_data}, upsert=True)
            print(f"User {user.id} ({user.first_name}) saved to DB.")
        except Exception as e:
            print(f"Error saving user to DB: {e}")
    await message.reply_text("Hello! I am a Video Downloader Bot. Send me a supported link to get started.")

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
            'progress_hooks': [lambda d: progress_hook(d, status_message, time.time())],
            'max_filesize': 450 * 1024 * 1024, # Limit files to 450 MB
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Untitled Video')
            webpage_url = info.get('webpage_url', url)
            safe_title = "".join([c for c in video_title if c.isalpha() or c.isdigit() or c in ' ._-']).rstrip()
            
            print(f"[{message.chat.id}] Starting download for: {video_title}")
            ydl.download([url])
            downloaded_files = [f for f in os.listdir(DOWNLOAD_LOCATION) if f.startswith(safe_title)]
            if not downloaded_files:
                raise FileNotFoundError("Downloaded file not found.")
            video_path = os.path.join(DOWNLOAD_LOCATION, downloaded_files[0])

        # --- NEW AND IMPROVED THUMBNAIL LOGIC ---
        thumbnail_url = info.get('thumbnail')
        if thumbnail_url:
            print(f"[{message.chat.id}] Downloading thumbnail from {thumbnail_url}")
            try:
                # Download image data using requests
                response = requests.get(thumbnail_url)
                response.raise_for_status() # Raise an exception for bad status codes

                # Use Pillow to open the image from memory and save as JPEG
                img_data = io.BytesIO(response.content)
                with Image.open(img_data) as img:
                    thumbnail_path = os.path.join(DOWNLOAD_LOCATION, f"{safe_title}.jpg")
                    img.convert("RGB").save(thumbnail_path, "jpeg")
                print(f"[{message.chat.id}] Thumbnail successfully saved to {thumbnail_path}")

            except Exception as e:
                print(f"[{message.chat.id}] Could not process thumbnail: {e}")
                thumbnail_path = None # Ensure path is None if it fails
        # --- END OF NEW THUMBNAIL LOGIC ---
        
        await status_message.edit_text("⬆️ **Uploading to Telegram...**")
        sent_message = await client.send_video(
            chat_id=message.chat.id, video=video_path,
            caption=f"**Title:** {video_title}\n**Source:** {webpage_url}",
            thumb=thumbnail_path,
            supports_streaming=True,
            progress=upload_progress_callback, progress_args=(status_message,)
        )
        await status_message.edit_text("✅ **Upload complete!**")
        if sent_message and DUMP_CHANNEL_ID != 0:
            await sent_message.forward(DUMP_CHANNEL_ID)
            await status_message.edit_text("✅ **Upload complete and archived!**")
    except Exception as e:
        error_message = f"❌ An error occurred: {type(e).__name__}"
        if "is larger than" in str(e):
             error_message = "❌ **Error:** Video is too large to download on the free plan."
        print("\n\n------ ERROR ------\n")
        traceback.print_exc()
        print("\n------ END ERROR ------\n\n")
        await status_message.edit_text(error_message)
    finally:
        if video_path and os.path.exists(video_path): os.remove(video_path)
        if thumbnail_path and os.path.exists(thumbnail_path): os.remove(thumbnail_path)
        await asyncio.sleep(5)
        try:
            await status_message.delete()
        except Exception:
            pass

# --- Main Entry Point ---
if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_LOCATION):
        os.makedirs(DOWNLOAD_LOCATION)
    print("Starting web server thread...")
    flask_thread = threading.Thread(target=run_server)
    flask_thread.daemon = True
    flask_thread.start()
    print("Starting Pyrogram bot...")
    app.run()