import os
import time
import requests
import asyncio
import threading
import traceback
import io
from yt_dlp import YoutubeDL
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask
from pymongo import MongoClient
from datetime import datetime, timezone
from PIL import Image
from bson.objectid import ObjectId

# --- Configuration ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID", 0))
DOWNLOAD_LOCATION = "./downloads/"
SUPPORTED_SITES = ["xvv1deos.com", "pornhub.org", "xnxx.com", "xhamster.com", "erome.com"]

# --- NEW: State management for active downloads ---
# A set to store the user IDs of those currently downloading.
ACTIVE_DOWNLOADS = set()
# -------------------------------------------------

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
    downloads_collection = db.get_collection("downloads_history")
    print("Successfully connected to MongoDB.")
except Exception as e:
    print(f"Error connecting to MongoDB: {e}")
    users_collection = None
    downloads_collection = None

# --- Pyrogram Client ---
app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Helper Functions (No Changes) ---
def create_progress_bar(percentage):
    bar_length = 10; filled_length = int(bar_length * percentage // 100)
    return '🔴' * filled_length + '⚪' * (bar_length - filled_length)
def progress_hook(d, message: Message, start_time):
    if d['status'] == 'downloading':
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
        if total_bytes:
            percent = (downloaded_bytes := d.get('downloaded_bytes')) / total_bytes * 100
            if (time.time() - globals().get('last_update_time', 0)) > 2:
                try: asyncio.create_task(message.edit_text(f"⏳ **Files are downloading/converting:**\n{create_progress_bar(percent)} {percent:.2f}% [{downloaded_bytes / (1024*1024):.1f}MB]")); globals()['last_update_time'] = time.time()
                except Exception: pass
async def upload_progress_callback(current, total, message: Message):
    percent = (current / total) * 100
    if (time.time() - globals().get('last_upload_update_time', 0)) > 2:
        try: await message.edit_text(f"⏫ **Files are uploading:**\n{create_progress_bar(percent)} {percent:.2f}% [{current / (1024*1024):.1f}MB / {total / (1024*1024):.1f}MB]"); globals()['last_upload_update_time'] = time.time()
        except Exception: pass

# --- Bot Commands & Handlers ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user = message.from_user
    if users_collection is not None:
        user_data = {"_id": user.id, "first_name": user.first_name, "last_name": user.last_name, "username": user.username, "last_started": datetime.now(timezone.utc)}
        try: users_collection.update_one({"_id": user.id}, {"$set": user_data}, upsert=True); print(f"User {user.id} ({user.first_name}) saved to DB.")
        except Exception as e: print(f"Error saving user to DB: {e}")
    await message.reply_text("Hello! I am a Video Downloader Bot. Send me a supported link to get started.")

@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client: Client, message: Message):
    user_id = message.from_user.id
    
    # --- NEW: Check if user is already downloading ---
    if user_id in ACTIVE_DOWNLOADS:
        await message.reply_text("🤚 **Please wait!** You already have a download in progress. I'll let you know when it's done.")
        return
    # -------------------------------------------------

    url = message.text.strip()
    if not any(site in url for site in SUPPORTED_SITES):
        await message.reply_text("❌ **Sorry, this website is not supported.**")
        return

    # --- NEW: Add user to the active set to "lock" them ---
    ACTIVE_DOWNLOADS.add(user_id)
    # -----------------------------------------------------

    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True)
    video_path = None
    thumbnail_path = None
    download_log_id = ObjectId()
    if downloads_collection is not None:
        log_data = {"_id": download_log_id, "user_id": user_id, "url": url, "status": "processing", "start_time": datetime.now(timezone.utc), "video_title": None, "file_size_mb": None, "error_message": None}
        downloads_collection.insert_one(log_data)

    try:
        await status_message.edit_text("🔄 **Fetching video metadata...**")
        ydl_opts = {'format': 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best', 'outtmpl': os.path.join(DOWNLOAD_LOCATION, '%(title)s.%(ext)s'), 'noplaylist': True, 'quiet': True, 'progress_hooks': [lambda d: progress_hook(d, status_message, time.time())], 'max_filesize': 450 * 1024 * 1024}

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Untitled Video')
            if downloads_collection is not None:
                downloads_collection.update_one({"_id": download_log_id}, {"$set": {"video_title": video_title}})
            webpage_url = info.get('webpage_url', url)
            safe_title = "".join([c for c in video_title if c.isalpha() or c.isdigit() or c in ' ._-']).rstrip()
            print(f"[{user_id}] Starting download for: {video_title}")
            ydl.download([url])
            downloaded_files = [f for f in os.listdir(DOWNLOAD_LOCATION) if f.startswith(safe_title)]
            if not downloaded_files: raise FileNotFoundError("Downloaded file not found.")
            video_path = os.path.join(DOWNLOAD_LOCATION, downloaded_files[0])
            file_size_mb = round(os.path.getsize(video_path) / (1024 * 1024), 2)

        thumbnail_url = info.get('thumbnail')
        if thumbnail_url:
            try:
                response = requests.get(thumbnail_url); response.raise_for_status()
                img_data = io.BytesIO(response.content)
                with Image.open(img_data) as img:
                    thumbnail_path = os.path.join(DOWNLOAD_LOCATION, f"{safe_title}.jpg")
                    img.convert("RGB").save(thumbnail_path, "jpeg")
            except Exception as e: print(f"[{user_id}] Could not process thumbnail: {e}"); thumbnail_path = None
        
        await status_message.edit_text("⬆️ **Uploading to Telegram...**")
        sent_message = await client.send_video(chat_id=message.chat.id, video=video_path, caption=f"**Title:** {video_title}\n**Source:** {webpage_url}", thumb=thumbnail_path, supports_streaming=True, progress=upload_progress_callback, progress_args=(status_message,))

        if downloads_collection is not None:
            downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": "success", "end_time": datetime.now(timezone.utc), "file_size_mb": file_size_mb}})
        await status_message.edit_text("✅ **Upload complete!**")
        if sent_message and DUMP_CHANNEL_ID != 0:
            await sent_message.forward(DUMP_CHANNEL_ID); await status_message.edit_text("✅ **Upload complete and archived!**")
            
    except Exception as e:
        user_error_message = f"❌ An error occurred: {type(e).__name__}"
        if "is larger than" in str(e):
            user_error_message = "❌ **Error:** Video is too large to download on the free plan."
        if downloads_collection is not None:
            downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": "failed", "end_time": datetime.now(timezone.utc), "error_message": str(e)}})
        print("\n\n------ ERROR ------\n"); traceback.print_exc(); print("\n------ END ERROR ------\n\n")
        await status_message.edit_text(user_error_message)
        
    finally:
        # --- NEW: Remove user from the active set to "unlock" them ---
        # This runs whether the download succeeds or fails.
        ACTIVE_DOWNLOADS.discard(user_id)
        # -------------------------------------------------------------
        
        if video_path and os.path.exists(video_path): os.remove(video_path)
        if thumbnail_path and os.path.exists(thumbnail_path): os.remove(thumbnail_path)
        await asyncio.sleep(5)
        try: await status_message.delete()
        except Exception: pass

# --- Main Entry Point ---
if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_LOCATION): os.makedirs(DOWNLOAD_LOCATION)
    print("Starting web server thread...")
    flask_thread = threading.Thread(target=run_server)
    flask_thread.daemon = True
    flask_thread.start()
    print("Starting Pyrogram bot...")
    app.run()