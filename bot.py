import os
import time
import requests
import asyncio
import threading
import traceback
import io
from itertools import zip_longest
from yt_dlp import YoutubeDL
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from pyrogram.enums import ChatMemberStatus
# NEW: Import Playwright
from playwright.async_api import async_playwright

from flask import Flask
from pymongo import MongoClient
from datetime import datetime, timezone
from PIL import Image
from bson.objectid import ObjectId

# --- Configuration (Unchanged) ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID", 0))
DOWNLOAD_LOCATION = "./downloads/"
START_PHOTO_URL = "https://telegra.ph/Wow-07-03-5"
MAINTAINED_BY_URL = "https://t.me/Rexonblood"
SUPPORTED_SITES = [
    "rock.porn", "hdsex.org", "beeg.com", "bravotube.net", "camwhores.tv", "camsoda.com", "chaturbate.com",
    "desitube.com", "drporn.com", "dtube.video", "e-hentai.org", "empflix.com", "eporner.com", "erome.com",
    "erome.io", "exhentai.org", "extremetube.com", "fapbox.com", "gaytube.com", "hclips.com", "hentai-foundry.com", 
    "hentaivideos.net", "hentaistream.xxx", "hottystop.com", "iqtube.com", "ivxxx.com", "keezmovies.com", 
    "livejasmin.com", "manyvids.com", "metacafe.com", "mofosex.net", "motherless.com", "mrdeepfakes.com", "myvidster.com", 
    "noodlemagazine.com", "nuvid.com", "onlyfans.com", "perfectgirls.net", "pornhd.com", "pornhub.org", "pornhub.com", 
    "pornteengirl.com", "porntube.com", "pornz.com", "redtube.net", "spankbang.com", "sunporno.com", "tnaflix.com", 
    "tube8.es", "tubepleasure.com", "txxx.com", "txnhh.com", "vidmax.com", "vxxx.com", "pornoxo.com", "xanimu.com", 
    "xhamster.com", "xhamster.desi", "xhamster43.desi", "xnxx.com", "xvideos.com", "xtube.com", "xvideos.es", 
    "xvideos.fr", "xv1deos.com", "xhamster19.com", "youjizz.com", "youporn.com", "ytporn.com", "youtube.com", 
    "instagram.com", "tiktok.com"
]
FORCE_SUB_CHANNEL = "@dailynewswalla"

# --- State Management & Other Setups ---
DOWNLOAD_IN_PROGRESS = False; CANCELLATION_REQUESTS = set()
server = Flask(__name__);
@server.route('/')
def health_check(): return "Bot and Web Server are alive!", 200
def run_server(): server.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
try:
    db_client = MongoClient(MONGO_URI); db = db_client.get_database("VideoBotDB")
    users_collection = db.get_collection("users"); downloads_collection = db.get_collection("downloads_history")
    print("Successfully connected to MongoDB.")
except Exception as e:
    print(f"Error connecting to MongoDB: {e}"); users_collection=None; downloads_collection=None
app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Helper Functions ---
def create_progress_bar(percentage):
    bar_length=10; filled_length=int(bar_length*percentage//100)
    return '🟢'*filled_length+'⚪'*(bar_length-filled_length)
def get_sites_list_text():
    reply_text = "✅ **Here are the currently supported sites:**\n\n```\n"
    sorted_sites = sorted(list(set(SUPPORTED_SITES)))
    num_sites = len(sorted_sites)
    sites_per_column = (num_sites + 2) // 3
    columns = [sorted_sites[i:i + sites_per_column] for i in range(0, num_sites, sites_per_column)]
    for row in zip_longest(*columns, fillvalue=""):
        reply_text += f"{row[0]:<25}{row[1]:<25}{row[2]:<25}\n"
    reply_text += "```"
    return reply_text
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

# --- Bot Commands ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user_id = message.from_user.id
    try:
        member = await client.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        if member.status in [ChatMemberStatus.BANNED, ChatMemberStatus.RESTRICTED]:
            await message.reply_text("You are banned from using this bot."); return
    except UserNotParticipant:
        join_button = InlineKeyboardMarkup([[InlineKeyboardButton("Join Our Channel", url=f"https://t.me/{FORCE_SUB_CHANNEL.lstrip('@')}")]])
        await message.reply_text("To use this bot, you must join our channel. After joining, please send /start again.", reply_markup=join_button); return
    except Exception as e:
        print(f"Error during force sub check: {e}")
        await message.reply_text("An error occurred while checking your membership status. Please ensure the bot is an admin in the channel."); return
    u = message.from_user
    if users_collection is not None:
        ud={"_id":u.id,"first_name":u.first_name,"last_name":u.last_name,"username":u.username,"last_started":datetime.now(timezone.utc)}
        try:users_collection.update_one({"_id":u.id},{"$set":ud},upsert=True);print(f"User {u.id} saved.")
        except Exception as e:print(f"DB Error: {e}")
    start_text = (
        "» **I'M RX Downloader BOT**\n\n"
        "📥 **I CAN DOWNLOAD VIDEOS FROM:**\n"
        "• YOUTUBE, INSTAGRAM, TIKTOK\n"
        "• PORNHUB, XVIDEOS, XNXX\n"
        "• AND 1000+ OTHER SITES!\n\n"
        "🚀 **JUST SEND ME A LINK!**"
    )
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("• SUPPORTED SITES", callback_data="show_sites_list"),
            InlineKeyboardButton("• MAINTAINED BY", url=MAINTAINED_BY_URL)
        ]]
    )
    await message.reply_photo(photo=START_PHOTO_URL, caption=start_text, reply_markup=keyboard)

@app.on_message(filters.command("sites") & filters.private)
async def sites_command(client, message):
    sites_text = get_sites_list_text()
    await message.reply_text(sites_text)

@app.on_callback_query(filters.regex("^show_sites_list$"))
async def show_sites_handler(client, callback_query):
    sites_text = get_sites_list_text()
    await callback_query.answer()
    await callback_query.message.reply_text(sites_text)

@app.on_callback_query(filters.regex("^cancel_"))
async def cancel_handler(client, callback_query):
    user_id = int(callback_query.data.split("_")[1])
    if callback_query.from_user.id != user_id: await callback_query.answer("This is not for you!", show_alert=True); return
    CANCELLATION_REQUESTS.add(user_id)
    await callback_query.answer("Cancellation request sent.", show_alert=False)
    await callback_query.message.edit_text("🤚 **Cancellation requested...** Please wait.")

@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client: Client, message: Message):
    user_id = message.from_user.id
    try:
        member = await client.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        if member.status in [ChatMemberStatus.BANNED, ChatMemberStatus.RESTRICTED]: await message.reply_text("You are banned from using this bot."); return
    except UserNotParticipant:
        join_button = InlineKeyboardMarkup([[InlineKeyboardButton("Join Our Channel", url=f"https://t.me/{FORCE_SUB_CHANNEL.lstrip('@')}")]])
        await message.reply_text("To use this bot, you must join our channel. After joining, please send the link again.", reply_markup=join_button); return
    except Exception as e: print(f"Error during force sub check: {e}"); await message.reply_text("An error occurred while checking your membership status. Please ensure the bot is an admin in the channel."); return
    global DOWNLOAD_IN_PROGRESS
    if DOWNLOAD_IN_PROGRESS: await message.reply_text("🤚 **Bot is busy!** Please try again in a few minutes."); return
    url = message.text.strip()
    if not any(site in url for site in SUPPORTED_SITES): await message.reply_text("❌ **Sorry, this website is not supported.**\n\nUse /sites to see the full list."); return
    DOWNLOAD_IN_PROGRESS = True; CANCELLATION_REQUESTS.discard(user_id)
    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    try:
        # --- MODIFIED: Route to the new, better Erome handler ---
        if "erome.com" in url or "erome.io" in url:
            await handle_erome_album_with_playwright(url, message, status_message)
        else: await handle_single_video(url, message, status_message)
    except Exception as e: print(f"--- UNHANDLED ERROR IN LINK_HANDLER ---\n{traceback.format_exc()}\n--------------------"); await status_message.edit_text(f"❌ A critical error occurred: {e}")
    finally: CANCELLATION_REQUESTS.discard(user_id); DOWNLOAD_IN_PROGRESS = False

async def handle_single_video(url, message, status_message):
    ydl_opts = {'format':'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best','outtmpl':os.path.join(DOWNLOAD_LOCATION,'%(title)s.%(ext)s'),'noplaylist':True,'quiet':True,'progress_hooks':[lambda d:progress_hook(d,status_message,message.from_user.id)],'max_filesize':450*1024*1024}
    await process_video_url(url, ydl_opts, message, status_message)

# --- NEW: Playwright-based Erome handler ---
async def handle_erome_album_with_playwright(url, message, status_message):
    album_limit = 100; user_id = message.from_user.id
    await status_message.edit_text("🔎 **Erome detected.** Launching browser...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    media_items = []; browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto(url, wait_until='networkidle', timeout=60000)
            if await page.evaluate("document.title.includes('404 Not Found')"):
                await status_message.edit_text("❌ **Erome album not found (404).** It may be deleted."); return
            try:
                age_gate_button = page.locator('button#age-gate-button')
                if await age_gate_button.is_visible(timeout=5000):
                    await status_message.edit_text("✅ Age gate detected. Clicking..."); await age_gate_button.click()
                    await page.wait_for_load_state('networkidle', timeout=30000)
            except Exception as age_gate_error: print(f"No age gate or error clicking: {age_gate_error}")
            await page.wait_for_selector('video.video-player, a[data-fancybox="gallery"]', timeout=30000)
            video_locators = page.locator('video.video-player')
            for i in range(await video_locators.count()):
                if src := await video_locators.nth(i).get_attribute('src'): media_items.append({'type': 'video', 'url': src})
            image_locators = page.locator('a[data-fancybox="gallery"]')
            for i in range(await image_locators.count()):
                if href := await image_locators.nth(i).get_attribute('href'):
                    if href.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')): media_items.append({'type': 'image', 'url': href})
            if not media_items: await status_message.edit_text("❌ No videos or images found in this Erome album."); return
            media_items = media_items[:album_limit]
            content_count = len(media_items)
            video_count = sum(1 for item in media_items if item['type'] == 'video')
            image_count = sum(1 for item in media_items if item['type'] == 'image')
            await status_message.edit_text(f"✅ Album found with **{video_count} videos** & **{image_count} images**. Processing...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
            await asyncio.sleep(2)
            for i, item in enumerate(media_items, 1):
                if user_id in CANCELLATION_REQUESTS: await status_message.edit_text("✅ **Album processing cancelled.**"); break
                item_type, item_url, caption = item['type'], item['url'], f"{item['type'].capitalize()} {i}/{content_count}"
                await status_message.edit_text(f"Processing {item_type} {i}/{content_count}...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
                if item_type == 'video': await download_erome_media(item_url, caption, message, status_message, is_video=True)
                else: await download_erome_media(item_url, caption, message, status_message, is_video=False)
    except Exception as e:
        await status_message.edit_text(f"❌ **Failed to process Erome album.**\nError: `{type(e).__name__}`"); print(f"--- EROME HANDLER ERROR ---\n{traceback.format_exc()}\n---")
    finally:
        if browser: await browser.close()
    if user_id not in CANCELLATION_REQUESTS and media_items:
        await status_message.edit_text(f"✅ Finished processing all {len(media_items)} items!", reply_markup=None); await asyncio.sleep(5)
    try: await status_message.delete()
    except: pass
async def download_erome_media(media_url, caption, message, status_message, is_video):
    user_id = message.from_user.id; ext = "mp4" if is_video else "jpg"
    file_path = os.path.join(DOWNLOAD_LOCATION, f"{user_id}_{int(time.time())}.{ext}")
    try:
        with requests.get(media_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(file_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        await status_message.edit_text("⬆️ **Uploading to Telegram...**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
        if is_video: await app.send_video(chat_id=user_id, video=file_path, caption=caption, supports_streaming=True, progress=upload_progress_callback, progress_args=(status_message, user_id))
        else: await app.send_photo(chat_id=user_id, photo=file_path, caption=caption)
    except Exception as e: print(f"Failed to process Erome media {media_url}: {e}"); await message.reply_text(f"⚠️ Could not process: {caption}")
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def process_video_url(url, ydl_opts, original_message, status_message, is_album_item=False):
    video_path, thumbnail_path = None, None; user_id = original_message.from_user.id
    download_log_id = ObjectId()
    if downloads_collection is not None: downloads_collection.insert_one({"_id": download_log_id, "user_id": user_id, "url": url, "status": "processing", "start_time": datetime.now(timezone.utc)})
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False); video_title = info.get('title', 'Untitled Video')
            if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"video_title": video_title}})
            print(f"[{user_id}] Starting download for: {video_title}"); ydl.download([url])
            list_of_files = [os.path.join(DOWNLOAD_LOCATION, f) for f in os.listdir(DOWNLOAD_LOCATION)]
            if not list_of_files: raise FileNotFoundError("Download folder is empty.")
            video_path = max(list_of_files, key=os.path.getctime)
            file_size_mb = round(os.path.getsize(video_path) / (1024 * 1024), 2)
        if thumbnail_url := info.get('thumbnail'):
            try:
                r=requests.get(thumbnail_url); r.raise_for_status()
                with Image.open(io.BytesIO(r.content)) as img: thumbnail_path = os.path.join(DOWNLOAD_LOCATION, "thumb.jpg"); img.convert("RGB").save(thumbnail_path, "jpeg")
            except Exception as e: print(f"Thumb Error: {e}"); thumbnail_path = None
        await status_message.edit_text("⬆️ **Uploading to Telegram...**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
        sent_message = await app.send_video(chat_id=user_id, video=video_path, caption=f"**Title:** {video_title}\n**Source:** {info.get('webpage_url', url)}", thumb=thumbnail_path, supports_streaming=True, progress=upload_progress_callback, progress_args=(status_message, user_id))
        if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": "success", "end_time": datetime.now(timezone.utc), "file_size_mb": file_size_mb}})
        if not is_album_item: await status_message.edit_text("✅ **Upload complete!**", reply_markup=None)
        if sent_message and DUMP_CHANNEL_ID != 0: await sent_message.forward(DUMP_CHANNEL_ID)
    except Exception as e:
        if "cancelled by user" in str(e): user_error_message = "✅ **Operation cancelled."
        else: user_error_message = f"❌ An error occurred: {type(e).__name__}";
        if "is larger than" in str(e): user_error_message = "❌ **Error:** Video is too large."
        if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": "failed" if "cancelled" not in user_error_message else "cancelled", "end_time": datetime.now(timezone.utc), "error_message": str(e)}})
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