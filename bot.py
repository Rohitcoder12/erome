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
SUPPORTED_SITES = ["xvideos.com", "pornhub.com", "xnxx.com", "xhamster.com", "erome.com", "Instagram.com"]

# --- Global lock for all downloads ---
DOWNLOAD_IN_PROGRESS = False

# --- Flask & DB Setup ---
server = Flask(__name__)
@server.route('/')
def health_check(): return "Bot and Web Server are alive!", 200
def run_server(): server.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
try:
    db_client = MongoClient(MONGO_URI)
    db = db_client.get_database("VideoBotDB")
    users_collection = db.get_collection("users")
    downloads_collection = db.get_collection("downloads_history")
    print("Successfully connected to MongoDB.")
except Exception as e:
    print(f"Error connecting to MongoDB: {e}"); users_collection=None; downloads_collection=None

# --- Pyrogram Client ---
app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Helper Functions (Unchanged) ---
def create_progress_bar(percentage):
    bar_length=10; filled_length=int(bar_length*percentage//100)
    return '🔴'*filled_length+'⚪'*(bar_length-filled_length)
def progress_hook(d,m,s):
    if d['status']=='downloading' and (total_bytes := d.get('total_bytes') or d.get('total_bytes_estimate')):
        p=(db:=d.get('downloaded_bytes'))/total_bytes*100
        if(time.time()-globals().get('last_update_time',0))>2:
            try:asyncio.create_task(m.edit_text(f"⏳ **Downloading...**\n{create_progress_bar(p)} {p:.2f}% [{db/(1024*1024):.1f}MB]"));globals()['last_update_time']=time.time()
            except:pass
async def upload_progress_callback(c,t,m):
    p=c/t*100
    if(time.time()-globals().get('last_upload_update_time',0))>2:
        try:await m.edit_text(f"⏫ **Uploading...**\n{create_progress_bar(p)} {p:.2f}% [{c/(1024*1024):.1f}MB / {t/(1024*1024):.1f}MB]");globals()['last_upload_update_time']=time.time()
        except:pass

# --- Bot Commands ---
@app.on_message(filters.command("start")&filters.private)
async def start_command(c,m):
    u=m.from_user
    # --- FIXED: Correct check for collection ---
    if users_collection is not None:
        ud={"_id":u.id,"first_name":u.first_name,"last_name":u.last_name,"username":u.username,"last_started":datetime.now(timezone.utc)}
        try:users_collection.update_one({"_id":u.id},{"$set":ud},upsert=True);print(f"User {u.id} saved.")
        except Exception as e:print(f"DB Error: {e}")
    await m.reply_text("Hello! Send me a supported link to get started.")

# --- THE RE-ENGINEERED LINK HANDLER ---
@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client: Client, message: Message):
    global DOWNLOAD_IN_PROGRESS
    if DOWNLOAD_IN_PROGRESS:
        await message.reply_text("🤚 **Bot is busy!** Another download is in progress. Please try again in a few minutes.")
        return

    url = message.text.strip()
    if not any(site in url for site in SUPPORTED_SITES):
        await message.reply_text("❌ **Sorry, this website is not supported.**")
        return
    
    DOWNLOAD_IN_PROGRESS = True
    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True)
    
    try:
        if "erome.com" in url: await handle_erome_album(url, message, status_message)
        else: await handle_single_video(url, message, status_message)
            
    except Exception as e:
        print(f"--- UNHANDLED ERROR IN LINK_HANDLER ---\n{traceback.format_exc()}\n--------------------")
        await status_message.edit_text(f"❌ A critical error occurred: {e}")
        
    finally:
        DOWNLOAD_IN_PROGRESS = False

async def handle_single_video(url, message, status_message):
    ydl_opts = {'format':'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best','outtmpl':os.path.join(DOWNLOAD_LOCATION,'%(title)s.%(ext)s'),'noplaylist':True,'quiet':True,'progress_hooks':[lambda d:progress_hook(d,status_message,time.time())],'max_filesize':450*1024*1024}
    await process_video_url(url, ydl_opts, message, status_message)

async def handle_erome_album(url, message, status_message):
    album_limit = 10
    await status_message.edit_text("🔎 This looks like an Erome album. Checking for videos...")
    meta_opts = {'extract_flat': True, 'quiet': True, 'playlistend': album_limit}
    with YoutubeDL(meta_opts) as ydl: info = ydl.extract_info(url, download=False)
    videos_to_download = info.get('entries', [])
    if not videos_to_download: await status_message.edit_text("❌ No videos found in this Erome album."); return
    
    video_count = len(videos_to_download)
    await status_message.edit_text(f"✅ Album found with **{video_count}** videos (limit is {album_limit}).\nStarting to download them one by one..."); await asyncio.sleep(2)

    for i, video_entry in enumerate(videos_to_download, 1):
        video_url = video_entry['url']
        single_video_ydl_opts = {'format':'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best','outtmpl':os.path.join(DOWNLOAD_LOCATION,f"album_video_{i}_%(title)s.%(ext)s"),'quiet':True,'progress_hooks':[lambda d:progress_hook(d,status_message,time.time())],'max_filesize':450*1024*1024}
        temp_status_msg = await message.reply_text(f"Downloading video **{i}/{video_count}**...")
        await process_video_url(video_url, single_video_ydl_opts, message, temp_status_msg)
    
    await status_message.edit_text(f"✅ Finished processing all {video_count} videos from the album!"); await asyncio.sleep(5)
    await status_message.delete()

async def process_video_url(url, ydl_opts, original_message, status_message):
    video_path, thumbnail_path = None, None
    user_id = original_message.from_user.id
    download_log_id = ObjectId()
    
    # --- FIXED: Correct check for collection ---
    if downloads_collection is not None:
        log_data = {"_id": download_log_id, "user_id": user_id, "url": url, "status": "processing", "start_time": datetime.now(timezone.utc)}
        downloads_collection.insert_one(log_data)
        
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Untitled Video')
            # --- FIXED: Correct check for collection ---
            if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"video_title": video_title}})
            
            print(f"[{user_id}] Starting download for: {video_title}")
            ydl.download([url])
            
            list_of_files = [os.path.join(DOWNLOAD_LOCATION, f) for f in os.listdir(DOWNLOAD_LOCATION)]
            if not list_of_files: raise FileNotFoundError("Download folder is empty.")
            video_path = max(list_of_files, key=os.path.getctime)
            if not os.path.exists(video_path): raise FileNotFoundError("Downloaded file not found.")
            file_size_mb = round(os.path.getsize(video_path) / (1024 * 1024), 2)
        
        if thumbnail_url := info.get('thumbnail'):
            try:
                r = requests.get(thumbnail_url); r.raise_for_status()
                with Image.open(io.BytesIO(r.content)) as img:
                    thumbnail_path = os.path.join(DOWNLOAD_LOCATION, "thumb.jpg")
                    img.convert("RGB").save(thumbnail_path, "jpeg")
            except Exception as e: print(f"Thumb Error: {e}"); thumbnail_path = None
            
        await status_message.edit_text("⬆️ **Uploading to Telegram...**")
        sent_message = await app.send_video(
            chat_id=user_id, video=video_path,
            caption=f"**Title:** {video_title}\n**Source:** {info.get('webpage_url', url)}",
            thumb=thumbnail_path, supports_streaming=True,
            progress=upload_progress_callback, progress_args=(status_message,))
        
        # --- FIXED: Correct check for collection ---
        if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": "success", "end_time": datetime.now(timezone.utc), "file_size_mb": file_size_mb}})
        await status_message.edit_text("✅ **Upload complete!**")
        if sent_message and DUMP_CHANNEL_ID != 0: await sent_message.forward(DUMP_CHANNEL_ID)

    except Exception as e:
        user_error_message = f"❌ An error occurred: {type(e).__name__}"
        if "is larger than" in str(e): user_error_message = "❌ **Error:** Video is too large."
        # --- FIXED: Correct check for collection ---
        if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": "failed", "end_time": datetime.now(timezone.utc), "error_message": str(e)}})
        print(f"--- PROCESS_VIDEO_URL ERROR ---\n{traceback.format_exc()}\n--------------------")
        await status_message.edit_text(user_error_message)
        
    finally:
        if video_path and os.path.exists(video_path): os.remove(video_path)
        if thumbnail_path and os.path.exists(thumbnail_path): os.remove(thumbnail_path)
        await asyncio.sleep(5)
        try: await status_message.delete()
        except Exception: pass

# --- Main Entry Point ---
if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_LOCATION): os.makedirs(DOWNLOAD_LOCATION)
    print("Starting web server thread...")
    threading.Thread(target=run_server, daemon=True).start()
    print("Starting Pyrogram bot...")
    app.run()