# Bot.py (Reverted to original Erome logic with de-duplication fix)

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
from pyrogram.errors import UserNotParticipant, FloodWait, UserIsBlocked, InputUserDeactivated, UserDeactivated
from pyrogram.enums import ChatMemberStatus

from flask import Flask
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta
from PIL import Image
from bson.objectid import ObjectId

# --- Configuration ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID", 0))
DOWNLOAD_LOCATION = "./downloads/"
REPORT_CHANNEL_ID = int(os.environ.get("REPORT_CHANNEL_ID", 0))

# --- Robust handling for ADMIN_ID ---
admin_id_str = os.environ.get("ADMIN_ID")
if not admin_id_str:
    print("FATAL ERROR: The ADMIN_ID environment variable is not set. Please add it to your configuration and restart the bot.")
    exit(1)
try:
    ADMIN_ID = int(admin_id_str)
except ValueError:
    print(f"FATAL ERROR: The ADMIN_ID '{admin_id_str}' is not a valid integer. Please correct it.")
    exit(1)

# --- Start Message Configuration ---
START_PHOTO_URL = "https://telegra.ph/Wow-07-03-5"
MAINTAINED_BY_URL = "https://t.me/Rexonblood"

# --- INITIAL list for the database ---
INITIAL_SUPPORTED_SITES = [
    "rock.porn", "hdsex.org", "beeg.com", "bravotube.net", "camwhores.tv", "camsoda.com", "chaturbate.com",
    "desitube.com", "drporn.com", "dtube.video", "e-hentai.org", "empflix.com", "eporner.com", "erome.com",
    "erome.io", "exhentai.org", "extremetube.com", "fapbox.com", "gaytube.com", "hclips.com", "hentai-foundry.com",
    "hentaivideos.net", "hentaistream.xxx", "hottystop.com", "iqtube.com", "ivxxx.com", "keezmovies.com",
    "livejasmin.com", "manyvids.com", "metacafe.com", "mofosex.net", "motherless.com", "mrdeepfakes.com",
    "myvidster.com", "noodlemagazine.com", "nuvid.com", "onlyfans.com", "perfectgirls.net", "pornhd.com",
    "pornhub.org", "pornhub.com", "pornteengirl.com", "porntube.com", "pornz.com", "redtube.net", "spankbang.com",
    "sunporno.com", "tnaflix.com", "tube8.es", "tubepleasure.com", "txxx.com", "txnhh.com", "vidmax.com", "vxxx.com",
    "pornoxo.com", "xanimu.com", "xhamster.com", "xhamster.desi", "xhamster43.desi", "xnxx.com", "xvideos.com",
    "xtube.com", "xvideos.es", "xvideos.fr", "xv1deos.com", "xhamster19.com", "youjizz.com", "youporn.com",
    "ytporn.com", "youtube.com", "instagram.com", "tiktok.com"
]
# --- Force Subscription Configuration ---
FORCE_SUB_CHANNEL = "@dailynewswalla"

# --- State Management & Other Setups ---
DOWNLOAD_IN_PROGRESS = False
CANCELLATION_REQUESTS = set()
SUPPORTED_SITES_CACHE = set()
server = Flask(__name__)
@server.route('/')
def health_check(): return "Bot and Web Server are alive!", 200
def run_server(): server.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- Database Setup ---
try:
    db_client = MongoClient(MONGO_URI)
    db = db_client.get_database("VideoBotDB")
    users_collection = db.get_collection("users")
    downloads_collection = db.get_collection("downloads_history")
    config_collection = db.get_collection("config")
    print("Successfully connected to MongoDB.")
except Exception as e:
    print(f"Error connecting to MongoDB: {e}"); users_collection=None; downloads_collection=None; config_collection=None
app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Helper Functions ---
def initialize_supported_sites():
    global SUPPORTED_SITES_CACHE
    if config_collection is None:
        print("MongoDB not connected. Falling back to initial site list."); SUPPORTED_SITES_CACHE = set(INITIAL_SUPPORTED_SITES); return
    sites_doc = config_collection.find_one({"_id": "supported_sites"})
    if sites_doc:
        SUPPORTED_SITES_CACHE = set(sites_doc.get("sites", [])); print(f"Loaded {len(SUPPORTED_SITES_CACHE)} supported sites from DB.")
    else:
        print("No site list found in DB. Initializing with default list..."); config_collection.insert_one({"_id": "supported_sites", "sites": INITIAL_SUPPORTED_SITES}); SUPPORTED_SITES_CACHE = set(INITIAL_SUPPORTED_SITES); print(f"Saved {len(SUPPORTED_SITES_CACHE)} sites to DB.")
def create_progress_bar(percentage):
    bar_length=10; filled_length=int(bar_length*percentage//100)
    return '🟢'*filled_length+'⚪'*(bar_length-filled_length)
def get_sites_list_text():
    reply_text = "✅ **Here are the currently supported sites:**\n\n```\n"
    sorted_sites = sorted(list(SUPPORTED_SITES_CACHE))
    if not sorted_sites: return "❌ No supported sites found. The admin needs to add some!"
    num_sites = len(sorted_sites); sites_per_column = (num_sites + 2) // 3
    columns = [sorted_sites[i:i + sites_per_column] for i in range(0, num_sites, sites_per_column)]
    for row in zip_longest(*columns, fillvalue=""): reply_text += f"{row[0]:<25}{row[1]:<25}{row[2]:<25}\n"
    reply_text += "```"; return reply_text
def progress_hook(d, m, user_id):
    if user_id in CANCELLATION_REQUESTS: raise Exception("Download cancelled by user.")
    if d['status']=='downloading' and (total_bytes := d.get('total_bytes') or d.get('total_bytes_estimate')):
        p=(db_bytes:=d.get('downloaded_bytes'))/total_bytes*100
        if(time.time()-globals().get('last_update_time',0))>2:
            try:asyncio.create_task(m.edit_text(f"⏳ **Downloading...**\n{create_progress_bar(p)} {p:.2f}% [{db_bytes/(1024*1024):.1f}MB]", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]])));globals()['last_update_time']=time.time()
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
        if member.status in [ChatMemberStatus.BANNED, ChatMemberStatus.RESTRICTED]: await message.reply_text("You are banned from using this bot."); return
    except UserNotParticipant:
        join_button = InlineKeyboardMarkup([[InlineKeyboardButton("Join Our Channel", url=f"https://t.me/{FORCE_SUB_CHANNEL.lstrip('@')}")]])
        await message.reply_text("To use this bot, you must join our channel. After joining, please send /start again.", reply_markup=join_button); return
    except Exception as e: print(f"Error during force sub check: {e}"); await message.reply_text("An error occurred while checking your membership status. Please ensure the bot is an admin in the channel."); return
    u = message.from_user
    if users_collection is not None:
        ud={"_id":u.id,"first_name":u.first_name,"last_name":u.last_name,"username":u.username,"last_started":datetime.now(timezone.utc)}
        try:users_collection.update_one({"_id":u.id},{"$set":ud},upsert=True);print(f"User {u.id} saved.")
        except Exception as e:print(f"DB Error: {e}")
    start_text = ("» **I'M RX Downloader BOT**\n\n📥 **I CAN DOWNLOAD VIDEOS FROM:**\n• YOUTUBE, INSTAGRAM, TIKTOK\n• PORNHUB, XVIDEOS, XNXX\n• AND MANY OTHER SITES!\n\n🚀 **JUST SEND ME A LINK!**")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("• SUPPORTED SITES", callback_data="show_sites_list"), InlineKeyboardButton("• MAINTAINED BY", url=MAINTAINED_BY_URL)]])
    await message.reply_photo(photo=START_PHOTO_URL, caption=start_text, reply_markup=keyboard)
@app.on_message(filters.command("sites") & filters.private)
async def sites_command(client, message):
    sites_text = get_sites_list_text(); await message.reply_text(sites_text)

# --- Admin Panel Section ---
admin_filter = filters.user(ADMIN_ID) & filters.private
@app.on_message(filters.command("help") & filters.private)
async def help_command(client, message):
    user_help_text = """**Here's how to use me:**\n\n/start - Start the bot.\n/sites - See all supported websites.\n/help - Show this help message.\n\n➡️ Just send me a link to a video from a supported site and I'll download it for you!"""
    admin_help_text = """\n\n--- **Admin Commands** ---\n/stats - Get bot usage statistics.\n/users - See the 10 most recent users.\n/broadcast <msg> - Send a message to all users.\n/addsite <domain> - Add a new supported site.\n/delsite <domain> - Remove a supported site."""
    if message.from_user.id == ADMIN_ID: await message.reply_text(user_help_text + admin_help_text)
    else: await message.reply_text(user_help_text)
@app.on_message(filters.command("stats") & admin_filter)
async def stats_command(client, message):
    if users_collection is None or downloads_collection is None: await message.reply_text("❌ Database not connected. Cannot fetch stats."); return
    await message.reply_text("🔄 `Fetching stats...`")
    total_users = users_collection.count_documents({})
    total_downloads = downloads_collection.count_documents({})
    successful_downloads = downloads_collection.count_documents({"status": "success"})
    failed_downloads = downloads_collection.count_documents({"status": "failed"})
    twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    downloads_today = downloads_collection.count_documents({"start_time": {"$gte": twenty_four_hours_ago}})
    stats_text = f"""📊 **Bot Statistics** 📊\n\n👤 **Users:**\n- **Total Users:** `{total_users}`\n\n📥 **Downloads:**\n- **Total Processed:** `{total_downloads}`\n- **Successful:** `{successful_downloads}`\n- **Failed/Cancelled:** `{failed_downloads}`\n- **In Last 24 Hours:** `{downloads_today}`"""
    await message.reply_text(stats_text, quote=True)
@app.on_message(filters.command("broadcast") & admin_filter)
async def broadcast_command(client, message):
    if users_collection is None: await message.reply_text("❌ Database not connected. Cannot fetch users."); return
    try: broadcast_text = message.text.split(None, 1)[1]
    except IndexError: await message.reply_text("**Usage:** `/broadcast <your_message>`"); return
    all_users_cursor = users_collection.find({}, {"_id": 1})
    user_ids = [user["_id"] for user in all_users_cursor]
    total_users = len(user_ids)
    if total_users == 0: await message.reply_text("No users found in the database."); return
    status_msg = await message.reply_text(f"📣 Starting broadcast to `{total_users}` users...")
    success_count, failed_count, start_time = 0, 0, time.time()
    for i, user_id in enumerate(user_ids):
        try:
            await client.send_message(user_id, broadcast_text)
            success_count += 1
        except (UserIsBlocked, InputUserDeactivated, UserDeactivated): failed_count += 1
        except FloodWait as e:
            print(f"FloodWait for {e.value} seconds. Sleeping...")
            await asyncio.sleep(e.value + 2)
            try: await client.send_message(user_id, broadcast_text); success_count += 1
            except Exception: failed_count += 1
        except Exception as e:
            failed_count += 1; print(f"Broadcast error with user {user_id}: {type(e).__name__}")
        await asyncio.sleep(0.1)
        if (i + 1) % 50 == 0 or (i + 1) == total_users:
            elapsed_time = round(time.time() - start_time)
            await status_msg.edit_text(f"📣 **Broadcasting...**\n- Processed: `{i + 1}/{total_users}`\n- Successful: `{success_count}`\n- Failed: `{failed_count}`\n- Time: `{elapsed_time}s`")
    elapsed_time = round(time.time() - start_time)
    await status_msg.edit_text(f"✅ **Broadcast Complete!**\n\n- Sent to: `{success_count}` users\n- Failed for: `{failed_count}` users\n- Total time: `{elapsed_time}` seconds.")
@app.on_message(filters.command("users") & admin_filter)
async def get_users_command(client, message):
    if users_collection is None: await message.reply_text("❌ Database not connected. Cannot fetch users."); return
    await message.reply_text("🔄 `Fetching recent users...`")
    recent_users_cursor = users_collection.find().sort("last_started", -1).limit(10)
    users_list_text, user_count = "👥 **10 Most Recent Users:**\n\n", 0
    for user in recent_users_cursor:
        user_count += 1
        user_id = user["_id"]; first_name = user.get("first_name", "N/A")
        username = f"@{user['username']}" if user.get("username") else "N/A"
        last_started = user.get('last_started', 'N/A').strftime("%Y-%m-%d %H:%M")
        users_list_text += f"**{user_count}.** `{user_id}`\n   - **Name:** {first_name}\n   - **Username:** {username}\n   - **Last Start:** {last_started} UTC\n"
    if user_count == 0: await message.reply_text("No users found in the database yet.")
    else: await message.reply_text(users_list_text, quote=True)
@app.on_message(filters.command("addsite") & admin_filter)
async def add_site_command(client, message):
    if config_collection is None: await message.reply_text("❌ Database not connected."); return
    try: site_to_add = message.text.split(maxsplit=1)[1].strip().lower()
    except IndexError: await message.reply_text("⚠️ Usage: `/addsite example.com`"); return
    result = config_collection.update_one({"_id": "supported_sites"}, {"$addToSet": {"sites": site_to_add}})
    if result.modified_count > 0: SUPPORTED_SITES_CACHE.add(site_to_add); await message.reply_text(f"✅ **Success!** `{site_to_add}` has been added.")
    else: await message.reply_text(f"ℹ️ `{site_to_add}` is already in the list.")
@app.on_message(filters.command("delsite") & admin_filter)
async def del_site_command(client, message):
    if config_collection is None: await message.reply_text("❌ Database not connected."); return
    try: site_to_remove = message.text.split(maxsplit=1)[1].strip().lower()
    except IndexError: await message.reply_text("⚠️ Usage: `/delsite example.com`"); return
    result = config_collection.update_one({"_id": "supported_sites"}, {"$pull": {"sites": site_to_remove}})
    if result.modified_count > 0: SUPPORTED_SITES_CACHE.discard(site_to_remove); await message.reply_text(f"✅ **Success!** `{site_to_remove}` has been removed.")
    else: await message.reply_text(f"ℹ️ `{site_to_remove}` was not found in the list.")

# --- Callback Handlers ---
@app.on_callback_query(filters.regex("^show_sites_list$"))
async def show_sites_handler(client, callback_query):
    sites_text = get_sites_list_text(); await callback_query.answer(); await callback_query.message.reply_text(sites_text)
@app.on_callback_query(filters.regex("^cancel_"))
async def cancel_handler(client, callback_query):
    user_id = int(callback_query.data.split("_")[1])
    if callback_query.from_user.id != user_id: await callback_query.answer("This is not for you!", show_alert=True); return
    CANCELLATION_REQUESTS.add(user_id); await callback_query.answer("Cancellation request sent.", show_alert=False); await callback_query.message.edit_text("🤚 **Cancellation requested...** Please wait.")
@app.on_callback_query(filters.regex("^report_"))
async def report_link_handler(client, callback_query):
    try: _, from_chat_id_str, message_id_str = callback_query.data.split("_")
    except ValueError: await callback_query.answer("Invalid report format.", show_alert=True); return
    if REPORT_CHANNEL_ID == 0: await callback_query.answer("Reporting feature is disabled.", show_alert=True); await callback_query.message.edit_reply_markup(None); return
    try:
        await client.forward_messages(chat_id=REPORT_CHANNEL_ID, from_chat_id=int(from_chat_id_str), message_ids=int(message_id_str))
        await callback_query.answer("Report sent successfully! Thank you.", show_alert=True)
        await callback_query.message.edit_text(f"{callback_query.message.text}\n\n✅ **Report sent to admin.**", reply_markup=None)
    except FloodWait as e: await asyncio.sleep(e.value)
    except Exception as e:
        print(f"--- ERROR IN REPORT HANDLER ---\n{traceback.format_exc()}\n--------------------")
        await callback_query.answer("Could not send report.", show_alert=True)

# --- Core Logic Handlers ---
@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client, message):
    user_id = message.from_user.id
    try:
        member = await client.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        if member.status in [ChatMemberStatus.BANNED, ChatMemberStatus.RESTRICTED]: await message.reply_text("You are banned from using this bot."); return
    except UserNotParticipant:
        join_button = InlineKeyboardMarkup([[InlineKeyboardButton("Join Our Channel", url=f"https://t.me/{FORCE_SUB_CHANNEL.lstrip('@')}")]])
        await message.reply_text("To use this bot, you must join our channel. After joining, please send the link again.", reply_markup=join_button); return
    except Exception as e: print(f"Error during force sub check: {e}"); await message.reply_text("An error occurred while checking your membership status."); return
    global DOWNLOAD_IN_PROGRESS
    if DOWNLOAD_IN_PROGRESS: await message.reply_text("🤚 **Bot is busy!** Please try again in a few minutes."); return
    url = message.text.strip()
    if not any(site in url for site in SUPPORTED_SITES_CACHE):
        error_text = "❌ **Sorry, this website is not supported.**\n\nUse /sites to see the full list."
        reply_markup = None
        if REPORT_CHANNEL_ID != 0:
            callback_data = f"report_{message.chat.id}_{message.id}"
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🐞 Report Link", callback_data=callback_data)]])
        await message.reply_text(error_text, reply_markup=reply_markup); return
    DOWNLOAD_IN_PROGRESS = True; CANCELLATION_REQUESTS.discard(user_id)
    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    try:
        if any(erome_domain in url for erome_domain in ["erome.com", "erome.io"]): await handle_erome_album(url, message, status_message)
        else: await handle_single_video(url, message, status_message)
    except Exception as e: print(f"--- UNHANDLED ERROR IN LINK_HANDLER ---\n{traceback.format_exc()}\n--------------------"); await status_message.edit_text(f"❌ A critical error occurred: {e}")
    finally: CANCELLATION_REQUESTS.discard(user_id); DOWNLOAD_IN_PROGRESS = False

async def handle_single_video(url, message, status_message):
    ydl_opts = {'format':'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best','outtmpl':os.path.join(DOWNLOAD_LOCATION,'%(title)s.%(ext)s'),'noplaylist':True,'quiet':True,'progress_hooks':[lambda d:progress_hook(d,status_message,message.from_user.id)],'max_filesize':450*1024*1024}
    await process_video_url(url, ydl_opts, message, status_message)

async def handle_erome_album(url, message, status_message):
    album_limit = 10
    user_id = message.from_user.id
    await status_message.edit_text("🔎 This looks like an Erome album...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    
    meta_opts = {'extract_flat': True, 'quiet': True, 'playlistend': album_limit}
    
    with YoutubeDL(meta_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    
    original_entries = info.get('entries', [])
    content_to_process = []
    seen_ids = set()

    for entry in original_entries:
        entry_id = entry.get('id')
        if entry_id and entry_id not in seen_ids:
            content_to_process.append(entry)
            seen_ids.add(entry_id)

    if not content_to_process:
        await status_message.edit_text("❌ No content found in this Erome album."); return
    
    content_count = len(content_to_process)
    await status_message.edit_text(
        f"✅ Album found with **{content_count}** unique items (limit {album_limit}). Processing...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    await asyncio.sleep(2)

    for i, entry in enumerate(content_to_process, 1):
        if user_id in CANCELLATION_REQUESTS:
            await status_message.edit_text("✅ **Album processing cancelled.**"); break
        
        entry_url = entry.get('url')
        
        if any(entry_url.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp']):
            await handle_photo_download(entry, f"[{i}/{content_count}] ", message)
        else:
            ydl_opts = {
                'format':'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl':os.path.join(DOWNLOAD_LOCATION, f"album_item_{i}_%(title)s.%(ext)s"),
                'quiet':True,
                'progress_hooks':[lambda d:progress_hook(d, status_message, user_id)],
                'max_filesize':450*1024*1024
            }
            await status_message.edit_text(f"Downloading item **{i}/{content_count}**...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
            await process_video_url(entry_url, ydl_opts, message, status_message, is_album_item=True)

    if user_id not in CANCELLATION_REQUESTS:
        await status_message.edit_text(f"✅ Finished processing all {content_count} items!", reply_markup=None)
        await asyncio.sleep(5)
    
    try:
        await status_message.delete()
    except Exception:
        pass

async def handle_photo_download(entry, prefix, message):
    photo_url, photo_title = entry.get('url'), prefix + entry.get('title', 'Untitled Photo')
    await message.reply_photo(photo=photo_url, caption=photo_title); await asyncio.sleep(1)

async def process_video_url(url, ydl_opts, original_message, status_message, is_album_item=False):
    video_path, thumbnail_path = None, None; user_id = original_message.from_user.id
    download_log_id = ObjectId()
    if downloads_collection is not None: downloads_collection.insert_one({"_id": download_log_id, "user_id": user_id, "url": url, "status": "processing", "start_time": datetime.now(timezone.utc)})
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_title = info.get('title', 'Untitled Video')
            if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"video_title": video_title}})
            
            print(f"[{user_id}] Starting download for: {video_title}")
            ydl.download([url])

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
        if downloads_collection is not None:
            downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": "success", "end_time": datetime.now(timezone.utc), "file_size_mb": file_size_mb}})
        if not is_album_item:
            await status_message.edit_text("✅ **Upload complete!**", reply_markup=None)
        if sent_message and DUMP_CHANNEL_ID != 0:
            await sent_message.forward(DUMP_CHANNEL_ID)
    except Exception as e:
        user_error_message = "✅ **Operation cancelled." if "cancelled by user" in str(e) else "❌ **Error:** Video is too large." if "is larger than" in str(e) else "❌ **Download Failed.**\nThis could be a temporary issue."
        db_status = "cancelled" if "cancelled" in user_error_message else "failed"
        if downloads_collection is not None:
            downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": db_status, "end_time": datetime.now(timezone.utc), "error_message": str(e)}})
        print(f"--- PROCESS_VIDEO_URL ERROR ---\n{traceback.format_exc()}\n--------------------")
        reply_markup = None
        if REPORT_CHANNEL_ID != 0 and "cancelled" not in user_error_message:
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🐞 Report Link", callback_data=f"report_{original_message.chat.id}_{original_message.id}")]])
        if not is_album_item:
            await status_message.edit_text(user_error_message, reply_markup=reply_markup)
    finally:
        if video_path and os.path.exists(video_path):
            os.remove(video_path)
        if thumbnail_path and os.path.exists(thumbnail_path):
            os.remove(thumbnail_path)
        if not is_album_item and "failed" not in user_error_message.lower() and "cancelled" not in user_error_message.lower():
            await asyncio.sleep(5)
            try: await status_message.delete()
            except Exception: pass

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_LOCATION):
        os.makedirs(DOWNLOAD_LOCATION)
    
    initialize_supported_sites()
    
    print("Starting web server thread...")
    threading.Thread(target=run_server, daemon=True).start()
    
    print("Waiting 5 seconds for web server to start...")
    time.sleep(5)
    
    print("Starting Pyrogram bot...")
    app.run()