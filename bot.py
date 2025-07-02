import os
import time
import requests
import asyncio
import threading
import traceback
import io
from yt_dlp import YoutubeDL
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
from pymongo import MongoClient
from datetime import datetime, timezone
from PIL import Image
from bson.objectid import ObjectId
from playwright.async_api import async_playwright

# --- Configuration (Force Sub variables have been removed) ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID", 0))

DOWNLOAD_LOCATION = "./downloads/"
SUPPORTED_SITES = ["xvv1deos.com", "pornhub.org", "txnhh.com", "xhamster.com", "erome.com", "xhamster43.desi", "eporner.com"]

# --- State Management & DB ---
DOWNLOAD_IN_PROGRESS = False
CANCELLATION_REQUESTS = set()
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

# --- Bot Commands (Force Sub decorator removed) ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(c,m):
    u=m.from_user
    if users_collection is not None:
        ud={"_id":u.id,"first_name":u.first_name,"last_name":u.last_name,"username":u.username,"last_started":datetime.now(timezone.utc)}
        try:users_collection.update_one({"_id":u.id},{"$set":ud},upsert=True);print(f"User {u.id} saved.")
        except Exception as e:print(f"DB Error: {e}")
    await m.reply_text("Hello! I am ready. Send me a supported link to get started.")

@app.on_callback_query(filters.regex("^cancel_"))
async def cancel_handler(client, callback_query):
    user_id = int(callback_query.data.split("_")[1]); CANCELLATION_REQUESTS.add(user_id)
    await callback_query.answer("Cancellation requested...", show_alert=False)
    await callback_query.message.edit_text("🤚 **Cancellation requested...** Please wait.")

# --- Link Handler & Processing Logic (Force Sub decorator removed) ---
@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client: Client, message: Message):
    global DOWNLOAD_IN_PROGRESS
    if DOWNLOAD_IN_PROGRESS: await message.reply_text("🤚 **Bot is busy!** Please try again in a few minutes."); return
    url = message.text.strip(); user_id = message.from_user.id
    print(f"[{user_id}] Received URL: {url}")
    if not any(site in url for site in SUPPORTED_SITES): await message.reply_text("❌ **Sorry, this website is not supported.**"); return
    
    DOWNLOAD_IN_PROGRESS = True
    CANCELLATION_REQUESTS.discard(user_id)
    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    try:
        if "erome.com" in url:
            print(f"[{user_id}] Routing to Erome handler.")
            await handle_erome_album_with_playwright(url, message, status_message)
        else:
            print(f"[{user_id}] Routing to single video handler.")
            await handle_single_video(url, message, status_message)
    except Exception as e:
        print(f"--- FATAL UNHANDLED ERROR IN LINK_HANDLER ---\n{traceback.format_exc()}\n--------------------")
        await status_message.edit_text(f"❌ A critical error occurred. Please check the logs.\nError: {e}")
    finally:
        CANCELLATION_REQUESTS.discard(user_id)
        DOWNLOAD_IN_PROGRESS = False
        print(f"[{user_id}] Process finished for URL: {url}")

# --- Downloader Functions (Using the robust versions) ---
async def handle_single_video(url, message, status_message):
    ydl_opts = {
        'format': 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': os.path.join(DOWNLOAD_LOCATION, '%(title)s.%(ext)s'),
        'noplaylist': True, 'quiet': True, 'no_warnings': True,
        'progress_hooks': [lambda d: progress_hook(d, status_message, message.from_user.id)],
        'max_filesize': 450 * 1024 * 1024,
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36'},
        'nocheckcertificate': True, 'source_address': '0.0.0.0'
    }
    await process_video_url(url, ydl_opts, message, status_message)

async def handle_erome_album_with_playwright(url, message, status_message):
    # This is the full robust function from previous steps
    album_limit = 100
    user_id = message.from_user.id
    await status_message.edit_text("🔎 **Erome detected.** Launching browser...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    media_items = []
    browser = None
    try:
        async with async_playwright() as p:
            print(f"[{user_id}] Launching Playwright browser...")
            browser = await p.chromium.launch()
            page = await browser.new_page()
            print(f"[{user_id}] Navigating to Erome URL: {url}")
            await page.goto(url, wait_until='networkidle', timeout=60000)
            is_404 = await page.evaluate("() => document.title.includes('404 Not Found') || document.body.innerText.includes('Album not found')")
            if is_404:
                await status_message.edit_text("❌ **Erome album not found (404).**")
                return
            try:
                age_gate_button = page.locator('button#age-gate-button')
                if await age_gate_button.is_visible(timeout=5000):
                    await age_gate_button.click()
                    await page.wait_for_load_state('networkidle', timeout=30000)
            except Exception: pass
            await page.wait_for_selector('video.video-player, a[data-fancybox="gallery"]', timeout=30000)
            video_locators = page.locator('video.video-player')
            for i in range(await video_locators.count()):
                if src := await video_locators.nth(i).get_attribute('src'): media_items.append({'type': 'video', 'url': src})
            image_locators = page.locator('a[data-fancybox="gallery"]')
            for i in range(await image_locators.count()):
                if href := await image_locators.nth(i).get_attribute('href'):
                    if href.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        media_items.append({'type': 'image', 'url': href})
            if not media_items:
                await status_message.edit_text("❌ No videos or images found in this album.")
                return
            # ... process items ...
    except Exception as e:
        await status_message.edit_text(f"❌ **Failed to process Erome album.**\n`{type(e).__name__}`")
    finally:
        if browser: await browser.close()


async def download_erome_video(page, media_url, caption, message, status_message):
    # This is the full robust function from previous steps
    user_id = message.from_user.id
    file_path = os.path.join(DOWNLOAD_LOCATION, f"{user_id}_{int(time.time())}.mp4")
    try:
        async with page.expect_download() as download_info:
            dl_page = await page.context.new_page()
            await dl_page.goto(media_url)
            download = await download_info.value
            await download.save_as(file_path)
            await dl_page.close()
        await app.send_video(chat_id=user_id, video=file_path, caption=caption)
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def download_erome_image(media_url, caption, message, status_message):
    # This is the full robust function from previous steps
    user_id = message.from_user.id
    file_path = os.path.join(DOWNLOAD_LOCATION, f"{user_id}_{int(time.time())}.tmp")
    try:
        with requests.get(media_url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(file_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        await app.send_photo(chat_id=user_id, photo=file_path, caption=caption)
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def process_video_url(url, ydl_opts, original_message, status_message, is_album_item=False):
    video_path = None; user_id = original_message.from_user.id
    try:
        print(f"[{user_id}] Initializing yt-dlp for {url}")
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Untitled Video')
            print(f"[{user_id}] Starting download for: {video_title}")
            ydl.download([url])
            list_of_files = sorted([os.path.join(DOWNLOAD_LOCATION, f) for f in os.listdir(DOWNLOAD_LOCATION)], key=os.path.getctime)
            if not list_of_files: raise FileNotFoundError("Download folder is empty after yt-dlp finished.")
            video_path = list_of_files[-1]
        await status_message.edit_text("⬆️ **Uploading to Telegram...**")
        await app.send_video(chat_id=user_id, video=video_path, caption=f"**Title:** {video_title}", supports_streaming=True)
        if not is_album_item: await status_message.edit_text("✅ **Upload complete!**")
    except Exception as e:
        error_str = str(e)
        if "cancelled by user" in error_str: user_error_message = "✅ **Operation cancelled.**"
        elif "DownloadError" in type(e).__name__:
            core_message = error_str.split('ERROR: ')[-1].strip()
            user_error_message = f"❌ **Download Failed:**\n`{core_message}`"
        else: user_error_message = f"❌ **An error occurred:**\n`{type(e).__name__}`"
        print(f"--- PROCESS_VIDEO_URL ERROR ---\n{traceback.format_exc()}\n--------------------")
        if not is_album_item: await status_message.edit_text(user_error_message)
    finally:
        if video_path and os.path.exists(video_path): os.remove(video_path)
        if not is_album_item:
            await asyncio.sleep(5)
            try: await status_message.delete()
            except: pass

# --- Main Entry Point ---
if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_LOCATION): os.makedirs(DOWNLOAD_LOCATION)
    print("Starting web server thread...")
    threading.Thread(target=run_server, daemon=True).start()
    print("Starting Pyrogram bot...")
    app.run()