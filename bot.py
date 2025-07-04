import os
import time
import requests
import asyncio
import threading
import traceback
import io
import base64 
from itertools import zip_longest
from yt_dlp import YoutubeDL
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from pyrogram.enums import ChatMemberStatus

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

# --- NEW: Owner & Report Configuration (SET THESE IN YOUR ENVIRONMENT) ---
OWNER_ID = int(os.environ.get("OWNER_ID"))
REPORT_CHANNEL_ID = int(os.environ.get("REPORT_CHANNEL_ID"))
# --------------------------------------------------------------------

# --- Start Message Configuration ---
START_PHOTO_URL = "https://telegra.ph/Wow-07-03-5"
MAINTAINED_BY_URL = "https://t.me/Rexonblood"

# --- Default Supported Sites (Used only if DB is empty) ---
DEFAULT_SITES = [
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

# --- Force Subscription Configuration ---
FORCE_SUB_CHANNEL = "@dailynewswalla"

# --- State Management & DB Setup ---
DOWNLOAD_IN_PROGRESS = False
CANCELLATION_REQUESTS = set()
SITES_LIST = [] # Will be loaded from DB on startup

server = Flask(__name__)
@server.route('/')
def health_check(): return "Bot and Web Server are alive!", 200
def run_server(): server.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

try:
    db_client = MongoClient(MONGO_URI)
    db = db_client.get_database("VideoBotDB")
    users_collection = db.get_collection("users")
    downloads_collection = db.get_collection("downloads_history")
    sites_collection = db.get_collection("supported_sites") # NEW: Collection for sites
    print("Successfully connected to MongoDB.")
except Exception as e:
    print(f"Error connecting to MongoDB: {e}"); exit()

app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Helper Functions ---
def create_progress_bar(percentage):
    bar_length=10; filled_length=int(bar_length*percentage//100)
    return '🟢'*filled_length+'⚪'*(bar_length-filled_length)

def get_sites_list_text():
    reply_text = "✅ **Here are the currently supported sites:**\n\n```\n"
    # Uses the global SITES_LIST loaded from the DB
    sorted_sites = sorted(list(set(SITES_LIST)))
    if not sorted_sites: return "No sites are currently supported."
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
            try:asyncio.create_task(m.edit_text(f"⏳ **Downloading...**\n{create_progress_bar(p)} {p:.2f}%", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]])));globals()['last_update_time']=time.time()
            except:pass
async def upload_progress_callback(c, t, m, user_id):
    if user_id in CANCELLATION_REQUESTS: raise Exception("Upload cancelled by user.")
    p=c/t*100
    if(time.time()-globals().get('last_upload_update_time',0))>2:
        try:await m.edit_text(f"⏫ **Uploading...**\n{create_progress_bar(p)} {p:.2f}%", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]));globals()['last_upload_update_time']=time.time()
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
        join_button = InlineKeyboardMarkup([[InlineKeyboardButton("Join Our Channel", url=f"https://t.me/{FORCE_SUB_CHANNEL.lstrip('@')}")]]])
        await message.reply_text("To use this bot, you must join our channel. After joining, please send /start again.", reply_markup=join_button); return
    except Exception as e: print(f"Error during force sub check: {e}"); await message.reply_text("An error occurred while checking your membership status."); return
    u = message.from_user
    if users_collection is not None:
        ud={"_id":u.id,"first_name":u.first_name,"last_name":u.last_name,"username":u.username,"last_started":datetime.now(timezone.utc)}
        try:users_collection.update_one({"_id":u.id},{"$set":ud},upsert=True);print(f"User {u.id} saved.")
        except Exception as e:print(f"DB Error: {e}")
    start_text = ("» **I'M RX Downloader BOT**\n\n" + "📥 **I CAN DOWNLOAD VIDEOS FROM:**\n" + "• YOUTUBE, INSTAGRAM, TIKTOK\n" + "• PORNHUB, XVIDEOS, XNXX\n" + "• AND 1000+ OTHER SITES!\n\n" + "🚀 **JUST SEND ME A LINK!**")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("• SUPPORTED SITES", callback_data="show_sites_list"), InlineKeyboardButton("• MAINTAINED BY", url=MAINTAINED_BY_URL)]])
    await message.reply_photo(photo=START_PHOTO_URL, caption=start_text, reply_markup=keyboard)

@app.on_message(filters.command("sites") & filters.private)
async def sites_command(client, message):
    sites_text = get_sites_list_text()
    await message.reply_text(sites_text)

# --- NEW: Admin Commands for Site Management ---
@app.on_message(filters.command("addsite") & filters.user(OWNER_ID))
async def add_site_command(client, message):
    try:
        domain = message.text.split(" ", 1)[1].strip().lower()
        if not domain: await message.reply_text("Usage: `/addsite example.com`"); return
        if sites_collection.find_one({"domain": domain}): await message.reply_text(f"`{domain}` is already in the supported sites list."); return
        sites_collection.insert_one({"domain": domain})
        SITES_LIST.append(domain)
        await message.reply_text(f"✅ Successfully added `{domain}` to the supported sites list.")
    except IndexError: await message.reply_text("Usage: `/addsite example.com`")
    except Exception as e: await message.reply_text(f"An error occurred: {e}")

@app.on_message(filters.command("delsite") & filters.user(OWNER_ID))
async def del_site_command(client, message):
    try:
        domain = message.text.split(" ", 1)[1].strip().lower()
        if not domain: await message.reply_text("Usage: `/delsite example.com`"); return
        result = sites_collection.delete_one({"domain": domain})
        if result.deleted_count > 0:
            if domain in SITES_LIST: SITES_LIST.remove(domain)
            await message.reply_text(f"✅ Successfully removed `{domain}` from the supported sites list.")
        else: await message.reply_text(f"`{domain}` was not found in the list.")
    except IndexError: await message.reply_text("Usage: `/delsite example.com`")
    except Exception as e: await message.reply_text(f"An error occurred: {e}")

# --- Callback Handlers ---
@app.on_callback_query(filters.regex("^show_sites_list$"))
async def show_sites_handler(client, callback_query):
    sites_text = get_sites_list_text()
    await callback_query.answer()
    await callback_query.message.reply_text(sites_text)
@app.on_callback_query(filters.regex("^report_"))
async def report_link_handler(client, callback_query):
    try:
        encoded_url = callback_query.data.split("_", 1)[1]
        decoded_url = base64.urlsafe_b64decode(encoded_url).decode('utf-8')
        user = callback_query.from_user
        report_text = (f"🚨 **Link Report**\n\n" + f"**User:** {user.mention} (`{user.id}`)\n" + f"**Reported URL:** `{decoded_url}`")
        await client.send_message(chat_id=REPORT_CHANNEL_ID, text=report_text)
        await callback_query.answer("✅ Thank you! The link has been reported to the admin.", show_alert=True)
        await callback_query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        print(f"Error handling report: {e}")
        await callback_query.answer("Could not send report. Please contact the admin.", show_alert=True)
@app.on_callback_query(filters.regex("^cancel_"))
async def cancel_handler(client, callback_query):
    user_id = int(callback_query.data.split("_")[1]);
    if callback_query.from_user.id != user_id: await callback_query.answer("This is not for you!", show_alert=True); return
    CANCELLATION_REQUESTS.add(user_id); await callback_query.answer("Cancellation request sent.", show_alert=False); await callback_query.message.edit_text("🤚 **Cancellation requested...** Please wait.")

# --- Link Handler & Processing Logic ---
@app.on_message(filters.private & filters.text & ~filters.command())
async def link_handler(client, message):
    user_id = message.from_user.id
    try:
        member = await client.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        if member.status in [ChatMemberStatus.BANNED, ChatMemberStatus.RESTRICTED]: await message.reply_text("You are banned from using this bot."); return
    except UserNotParticipant:
        join_button = InlineKeyboardMarkup([[InlineKeyboardButton("Join Our Channel", url=f"https://t.me/{FORCE_SUB_CHANNEL.lstrip('@')}")]]])
        await message.reply_text("To use this bot, you must join our channel. After joining, please send the link again.", reply_markup=join_button); return
    except Exception as e: print(f"Error during force sub check: {e}"); await message.reply_text("An error occurred while checking your membership status."); return
    global DOWNLOAD_IN_PROGRESS
    if DOWNLOAD_IN_PROGRESS: await message.reply_text("🤚 **Bot is busy!** Please try again in a few minutes."); return
    url = message.text.strip()
    if not any(site in url for site in SITES_LIST): await message.reply_text("❌ **Sorry, this website is not supported.**\n\nUse /sites to see the full list."); return
    DOWNLOAD_IN_PROGRESS = True; CANCELLATION_REQUESTS.discard(user_id)
    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    try:
        if "erome.com" in url or "erome.io" in url: await handle_erome_album(url, message, status_message)
        else: await process_video_url(url, {}, message, status_message)
    except Exception as e: print(f"--- UNHANDLED ERROR IN LINK_HANDLER ---\n{traceback.format_exc()}\n--------------------"); await status_message.edit_text(f"❌ A critical error occurred: {e}")
    finally: CANCELLATION_REQUESTS.discard(user_id); DOWNLOAD_IN_PROGRESS = False

async def handle_erome_album(url, message, status_message):
    album_limit = 10; user_id = message.from_user.id;
    await status_message.edit_text("🔎 This looks like an Erome album, checking content...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    meta_opts = {'extract_flat': True, 'quiet': True, 'playlistend': album_limit}
    with YoutubeDL(meta_opts) as ydl: info = ydl.extract_info(url, download=False)
    original_entries, content_to_process, seen_filenames = info.get('entries', []), [], set()
    if not original_entries: await status_message.edit_text("❌ No content found in this Erome album."); return
    for entry in original_entries:
        filename = entry.get('url', '').split('/')[-1]
        if filename and filename not in seen_filenames: content_to_process.append(entry); seen_filenames.add(filename)
    content_count = len(content_to_process)
    await status_message.edit_text(f"✅ Album found with **{content_count}** unique items (limit {album_limit}).\nProcessing one by one...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    for i, entry in enumerate(content_to_process, 1):
        if user_id in CANCELLATION_REQUESTS: await status_message.edit_text("✅ **Album processing cancelled.**"); break
        entry_url = entry['url']
        if any(entry_url.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp']):
            await message.reply_photo(photo=entry_url, caption=f"Photo {i}/{content_count}"); await asyncio.sleep(1)
        else:
            single_video_ydl_opts = {'outtmpl':os.path.join(DOWNLOAD_LOCATION,f"album_item_{i}_%(id)s.%(ext)s")}
            await status_message.edit_text(f"Downloading video **{i}/{content_count}**...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
            await process_video_url(entry_url, single_video_ydl_opts, message, status_message, is_album_item=True)
    if user_id not in CANCELLATION_REQUESTS: await status_message.edit_text(f"✅ Finished processing all {content_count} items from the album!", reply_markup=None); await asyncio.sleep(5)
    await status_message.delete()

async def process_video_url(url, ydl_opts_override, original_message, status_message, is_album_item=False):
    video_path, thumbnail_path = None, None; user_id = original_message.from_user.id
    if downloads_collection: downloads_collection.insert_one({"user_id": user_id, "url": url, "status": "processing"})
    ydl_opts = {'format':'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best','outtmpl':os.path.join(DOWNLOAD_LOCATION,'%(id)s.%(ext)s'),'noplaylist':True,'quiet':True,'progress_hooks':[lambda d:progress_hook(d,status_message,user_id)],'max_filesize':450*1024*1024}
    ydl_opts.update(ydl_opts_override)
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False); video_title = info.get('title', 'Untitled Video')
            ydl.download([url])
            list_of_files = [os.path.join(DOWNLOAD_LOCATION, f) for f in os.listdir(DOWNLOAD_LOCATION)]
            if not list_of_files: raise FileNotFoundError("Download folder is empty.")
            video_path = max(list_of_files, key=os.path.getctime)
        if thumbnail_url := info.get('thumbnail'):
            try:
                r=requests.get(thumbnail_url); r.raise_for_status()
                with Image.open(io.BytesIO(r.content)) as img: thumbnail_path = os.path.join(DOWNLOAD_LOCATION, "thumb.jpg"); img.convert("RGB").save(thumbnail_path, "jpeg")
            except Exception as e: print(f"Thumb Error: {e}"); thumbnail_path = None
        await status_message.edit_text("⬆️ **Uploading to Telegram...**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
        sent_message = await app.send_video(chat_id=user_id, video=video_path, caption=f"**Title:** {video_title}", thumb=thumbnail_path, supports_streaming=True, progress=upload_progress_callback, progress_args=(status_message, user_id))
        if not is_album_item: await status_message.edit_text("✅ **Upload complete!**", reply_markup=None)
        if sent_message and DUMP_CHANNEL_ID != 0: await sent_message.forward(DUMP_CHANNEL_ID)
    except Exception as e:
        error_message = f"❌ An error occurred: {type(e).__name__}"; report_markup = None
        if "cancelled by user" in str(e): error_message = "✅ **Operation cancelled."
        else:
            if "is larger than" in str(e): error_message = "❌ **Error:** Video is too large."
            encoded_url = base64.urlsafe_b64encode(url.encode()).decode()
            report_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🚨 Report Link", callback_data=f"report_{encoded_url}")]])
        print(f"--- PROCESS_VIDEO_URL ERROR ---\n{traceback.format_exc()}\n---")
        if not is_album_item: await status_message.edit_text(error_message, reply_markup=report_markup)
    finally:
        if video_path and os.path.exists(video_path): os.remove(video_path)
        if thumbnail_path and os.path.exists(thumbnail_path): os.remove(thumbnail_path)
        if not is_album_item:
            await asyncio.sleep(5)
            try: await status_message.delete()
            except Exception: pass

def load_sites_from_db():
    global SITES_LIST
    if db_client is None: SITES_LIST = DEFAULT_SITES; return
    try:
        db_sites = [s['domain'] for s in sites_collection.find()]
        if not db_sites:
            sites_collection.insert_many([{"domain": s} for s in DEFAULT_SITES])
            SITES_LIST = DEFAULT_SITES
        else: SITES_LIST = db_sites
        print(f"Loaded {len(SITES_LIST)} supported sites.")
    except Exception as e: print(f"DB Error loading sites: {e}"); SITES_LIST = DEFAULT_SITES

async def main():
    threading.Thread(target=lambda: web_server.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000))), daemon=True).start()
    load_sites_from_db()
    await app.start()
    print("Pyrogram bot started successfully!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_LOCATION): os.makedirs(DOWNLOAD_LOCATION)
    asyncio.run(main())