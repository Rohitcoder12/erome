import os
import time
import requests
import asyncio
import threading
import traceback
import io
from yt_dlp import YoutubeDL
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from pymongo import MongoClient
from datetime import datetime, timezone
from PIL import Image
from bson.objectid import ObjectId
from playwright.async_api import async_playwright # <-- NEW IMPORT

# --- Configuration (Your settings are preserved) ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID", 0))
DOWNLOAD_LOCATION = "./downloads/"
SUPPORTED_SITES = ["xvv1deos.com", "pornhub.org", "txnhh.com", "xhamster.com", "erome.com", "xhamster43.desi", "eporner.com"]

# --- State Management ---
DOWNLOAD_IN_PROGRESS = False
CANCELLATION_REQUESTS = set()

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

# --- Helper Functions (Your working code) ---
def create_progress_bar(percentage):
    bar_length=10; filled_length=int(bar_length*percentage//100)
    return '🔴'*filled_length+'⚪'*(bar_length-filled_length)
def progress_hook(d, m, user_id):
    if user_id in CANCELLATION_REQUESTS: raise Exception("Download cancelled by user.")
    if d['status']=='downloading' and (total_bytes := d.get('total_bytes') or d.get('total_bytes_estimate')):
        p=(db:=d.get('downloaded_bytes'))/total_bytes*100
        if(time.time()-globals().get('last_update_time',0))>2:
            try:asyncio.create_task(m.edit_text(f"⏳ **Downloading...**\n{create_progress_bar(p)} {p:.2f}% [{db/(1024*1024):.1f}MB]", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]])));globals()['last_update_time']=time.time()
            except:pass
async def upload_progress_callback(c, t, m, user_id):
    if user_id in CANCELLATION_REQUESTS: raise Exception("Upload cancelled by user.")
    p=c/t*100
    if(time.time()-globals().get('last_upload_update_time',0))>2:
        try:await m.edit_text(f"⏫ **Uploading...**\n{create_progress_bar(p)} {p:.2f}% [{c/(1024*1024):.1f}MB / {t/(1024*1024):.1f}MB]", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]));globals()['last_upload_update_time']=time.time()
        except:pass

# --- Bot Commands (Your working code) ---
@app.on_message(filters.command("start")&filters.private)
async def start_command(c,m):
    u=m.from_user
    if users_collection is not None:
        ud={"_id":u.id,"first_name":u.first_name,"last_name":u.last_name,"username":u.username,"last_started":datetime.now(timezone.utc)}
        try:users_collection.update_one({"_id":u.id},{"$set":ud},upsert=True);print(f"User {u.id} saved.")
        except Exception as e:print(f"DB Error: {e}")
    await m.reply_text("Hello! Send me a supported link to get started.")
@app.on_callback_query(filters.regex("^cancel_"))
async def cancel_handler(client, callback_query):
    user_id = int(callback_query.data.split("_")[1])
    if callback_query.from_user.id != user_id: await callback_query.answer("This is not for you!", show_alert=True); return
    CANCELLATION_REQUESTS.add(user_id)
    await callback_query.answer("Cancellation request sent.", show_alert=False)
    await callback_query.message.edit_text("🤚 **Cancellation requested...** Please wait.")

# --- Link Handler & Processing Logic (Your working code) ---
@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client: Client, message: Message):
    global DOWNLOAD_IN_PROGRESS
    if DOWNLOAD_IN_PROGRESS: await message.reply_text("🤚 **Bot is busy!** Another download is in progress. Please try again in a few minutes."); return
    url = message.text.strip()
    if not any(site in url for site in SUPPORTED_SITES): await message.reply_text("❌ **Sorry, this website is not supported.**"); return
    DOWNLOAD_IN_PROGRESS = True
    user_id = message.from_user.id
    CANCELLATION_REQUESTS.discard(user_id)
    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    try:
        # UPDATED: Route erome links to the new Playwright handler
        if "erome.com" in url: await handle_erome_album_with_playwright(url, message, status_message)
        else: await handle_single_video(url, message, status_message)
    except Exception as e:
        print(f"--- UNHANDLED ERROR IN LINK_HANDLER ---\n{traceback.format_exc()}\n--------------------")
        await status_message.edit_text(f"❌ A critical error occurred: {e}")
    finally:
        CANCELLATION_REQUESTS.discard(user_id)
        DOWNLOAD_IN_PROGRESS = False

async def handle_single_video(url, message, status_message):
    ydl_opts = {'format':'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best','outtmpl':os.path.join(DOWNLOAD_LOCATION,'%(title)s.%(ext)s'),'noplaylist':True,'quiet':True,'progress_hooks':[lambda d:progress_hook(d,status_message,message.from_user.id)],'max_filesize':450*1024*1024}
    await process_video_url(url, ydl_opts, message, status_message)

# --- NEW, ROBUST EROME HANDLER (VIDEOS ONLY) ---
async def handle_erome_album_with_playwright(url, message, status_message):
    album_limit = 15; user_id = message.from_user.id
    await status_message.edit_text("🔎 **Erome detected.** Launching browser to get album content...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    
    video_urls = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto(url, wait_until='networkidle', timeout=60000)
            
            # Wait for video players to be present on the page
            await page.wait_for_selector('video.video-player', timeout=30000)
            
            # Locate all video elements
            locators = page.locator('video.video-player')
            count = await locators.count()
            
            for i in range(min(count, album_limit)):
                src = await locators.nth(i).get_attribute('src')
                if src and src not in video_urls:
                    video_urls.append(src)
            
            if not video_urls:
                await status_message.edit_text("❌ No videos found in this Erome album."); await browser.close(); return

            content_count = len(video_urls)
            await status_message.edit_text(f"✅ Album found with **{content_count}** videos.\nProcessing one by one...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
            await asyncio.sleep(2)
            
            for i, media_url in enumerate(video_urls, 1):
                if user_id in CANCELLATION_REQUESTS: await status_message.edit_text("✅ **Album processing cancelled by user.**"); break
                
                await status_message.edit_text(f"Processing video {i}/{content_count}...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
                await download_erome_video(page, media_url, f"Video {i}/{content_count}", message, status_message)
            
            await browser.close()

    except Exception as e:
        await status_message.edit_text(f"❌ Failed to process Erome album: {e}")
        return

    if user_id not in CANCELLATION_REQUESTS:
        await status_message.edit_text(f"✅ Finished processing all {content_count} videos from the album!", reply_markup=None)
        await asyncio.sleep(5)
    await status_message.delete()

async def download_erome_video(page, media_url, caption, message, status_message):
    user_id = message.from_user.id
    file_path = os.path.join(DOWNLOAD_LOCATION, f"{user_id}_{int(time.time())}.mp4")
    try:
        async with page.expect_download() as download_info:
            await page.goto(media_url)
        download = await download_info.value
        await download.save_as(file_path)
        
        await status_message.edit_text("⬆️ **Uploading to Telegram...**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
        await app.send_video(chat_id=user_id, video=file_path, caption=caption, supports_streaming=True, progress=upload_progress_callback, progress_args=(status_message, user_id))
            
    except Exception as e:
        print(f"Failed to process Erome video {media_url}: {e}")
        await message.reply_text(f"⚠️ Could not process video: {caption}")
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

# --- Your original process_video_url function for Non-Erome sites ---
async def process_video_url(url, ydl_opts, original_message, status_message, is_album_item=False):
    video_path, thumbnail_path = None, None
    user_id = original_message.from_user.id
    download_log_id = ObjectId()
    if downloads_collection is not None:
        downloads_collection.insert_one({"_id": download_log_id, "user_id": user_id, "url": url, "status": "processing", "start_time": datetime.now(timezone.utc)})
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Untitled Video')
            if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"video_title": video_title}})
            print(f"[{user_id}] Starting download for: {video_title}")
            ydl.download([url])
            list_of_files = [os.path.join(DOWNLOAD_LOCATION, f) for f in os.listdir(DOWNLOAD_LOCATION)];
            if not list_of_files: raise FileNotFoundError("Download folder is empty.")
            video_path = max(list_of_files, key=os.path.getctime)
            file_size_mb = round(os.path.getsize(video_path) / (1024 * 1024), 2)
        if thumbnail_url := info.get('thumbnail'):
            try:
                r=requests.get(thumbnail_url); r.raise_for_status()
                with Image.open(io.BytesIO(r.content)) as img:
                    thumbnail_path = os.path.join(DOWNLOAD_LOCATION, "thumb.jpg")
                    img.convert("RGB").save(thumbnail_path, "jpeg")
            except Exception as e: print(f"Thumb Error: {e}"); thumbnail_path = None
        await status_message.edit_text("⬆️ **Uploading to Telegram...**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
        upload_progress_args = (status_message, user_id)
        sent_message = await app.send_video(chat_id=user_id, video=video_path, caption=f"**Title:** {video_title}\n**Source:** {info.get('webpage_url', url)}", thumb=thumbnail_path, supports_streaming=True, progress=upload_progress_callback, progress_args=upload_progress_args)
        if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": "success", "end_time": datetime.now(timezone.utc), "file_size_mb": file_size_mb}})
        if not is_album_item: await status_message.edit_text("✅ **Upload complete!**", reply_markup=None)
        if sent_message and DUMP_CHANNEL_ID != 0: await sent_message.forward(DUMP_CHANNEL_ID)
    except Exception as e:
        if "cancelled by user" in str(e):
            user_error_message = "✅ **Operation cancelled.**"
            if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": "cancelled", "end_time": datetime.now(timezone.utc), "error_message": "User cancellation"}})
        else:
            user_error_message = f"❌ An error occurred: {type(e).__name__}"
            if "is larger than" in str(e): user_error_message = "❌ **Error:** Video is too large."
            if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": "failed", "end_time": datetime.now(timezone.utc), "error_message": str(e)}})
            print(f"--- PROCESS_VIDEO_URL ERROR ---\n{traceback.format_exc()}\n--------------------")
        if not is_album_item: await status_message.edit_text(user_error_message, reply_markup=None)
    finally:
        if video_path and os.path.exists(video_path): os.remove(video_path)
        if thumbnail_path and os.path.exists(thumbnail_path): os.remove(thumbnail_path)
        if not is_album_item:
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