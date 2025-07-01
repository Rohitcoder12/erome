# bot.py
import os
import time
import requests
import asyncio
import threading
import traceback
from yt_dlp import YoutubeDL
from pyrogram import Client, filters
from pyrogram.types import Message
from flask import Flask
from pymongo import MongoClient
from datetime import datetime

# --- Configuration ---
# You will set these in the Render Environment Variables
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI") # For the database
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID", 0))
DOWNLOAD_LOCATION = "./downloads/"
SUPPORTED_SITES = ["xvideos.com", "pornhub.com", "xnxx.com", "xhamster.com", "erome.com"]

# --- Flask Web Server Setup (to keep Render service alive) ---
server = Flask(__name__)

@server.route('/')
def health_check():
    return "Bot and Web Server are alive!", 200

def run_server():
    # This function will be run in a separate thread
    port = int(os.environ.get('PORT', 8080))
    server.run(host='0.0.0.0', port=port)

# --- Database Setup ---
# Make sure to handle potential connection errors in a real-world app
db_client = MongoClient(MONGO_URI)
db = db_client.get_database("VideoBotDB")
users_collection = db.get_collection("users")

# --- Pyrogram Client ---
app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Helper Functions (Your existing functions) ---
# ... (progress_hook and upload_progress_callback functions go here, no changes) ...
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


# --- Bot Commands & Handlers ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user = message.from_user
    # --- NEW FEATURE: Add/Update user in database ---
    user_data = {
        "_id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "last_started": datetime.utcnow()
    }
    try:
        users_collection.update_one({"_id": user.id}, {"$set": user_data}, upsert=True)
        print(f"User {user.id} ({user.first_name}) saved to DB.")
    except Exception as e:
        print(f"Error saving user to DB: {e}")
    # -----------------------------------------------
    await message.reply_text("Hello! I am a Video Downloader Bot. Send me a supported link to get started.")

@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client: Client, message: Message):
    # --- Your existing link_handler function ---
    # ... (Just copy-paste your full link_handler function here) ...
    # This is a placeholder, use your full function.
    await message.reply_text("Processing your link...")


# --- Main Entry Point ---
if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_LOCATION):
        os.makedirs(DOWNLOAD_LOCATION)

    # Start the Flask server in a separate thread
    print("Starting web server thread...")
    flask_thread = threading.Thread(target=run_server)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Start the Pyrogram bot in the main thread
    print("Starting Pyrogram bot...")
    app.run()
    print("Bot has stopped.")