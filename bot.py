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
from pyrogram.errors import UserNotParticipant, UserIsBlocked, InputUserDeactivated
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
OWNER_ID = int(os.environ.get("OWNER_ID"))
REPORT_CHANNEL_ID = int(os.environ.get("REPORT_CHANNEL_ID"))
START_PHOTO_URL = "https://telegra.ph/Wow-07-03-5"
MAINTAINED_BY_URL = "https://t.me/Rexonblood"
FORCE_SUB_CHANNEL = "@dailynewswalla"

# --- Default Supported Sites ---
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

# --- State Management & DB Setup ---
DOWNLOAD_IN_PROGRESS = False; CANCELLATION_REQUESTS = set()
SITES_LIST = []; BROADCAST_IN_PROGRESS = {}
try:
    db_client = MongoClient(MONGO_URI)
    db = db_client.get_database("VideoBotDB")
    users_collection = db.get_collection("users"); downloads_collection = db.get_collection("downloads_history")
    sites_collection = db.get_collection("supported_sites"); print("Successfully connected to MongoDB.")
except Exception as e: print(f"Error connecting to MongoDB: {e}"); db_client = None
app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
web_server = Flask(__name__)

@web_server.route('/')
def health_check(): return "Bot is alive!", 200

def create_progress_bar(percentage):
    bar_length=10; filled_length=int(bar_length*percentage//100)
    return '🟢'*filled_length+'⚪'*(bar_length-filled_length)
def get_sites_list_text():
    reply_text = "✅ **Here are the currently supported sites:**\n\n```\n"
    sorted_sites = sorted(list(set(SITES_LIST)))
    if not sorted_sites: return "No sites are currently supported."
    num_sites = len(sorted_sites); sites_per_column = (num_sites + 2) // 3
    columns = [sorted_sites[i:i + sites_per_column] for i in range(0, num_sites, sites_per_column)]
    for row in zip_longest(*columns, fillvalue=""):
        reply_text += f"{row[0]:<25}{row[1]:<25}{row[2]:<25}\n"
    reply_text += "```"; return reply_text
def progress_hook(d, m, user_id):
    if user_id in CANCELLATION_REQUESTS: raise Exception("Download cancelled by user.")
    if d['status']=='downloading' and (total_bytes := d.get('total_bytes') or d.get('total_bytes_estimate')):
        p=(db:=d.get('downloaded_bytes'))/total_bytes*100
        if(time.time()-globals().get('last_update_time',0))>2:
            try:asyncio.create_task(m.edit_text(f"⏳ Downloading... {p:.1f}%", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]])));globals()['last_update_time']=time.time()
            except:pass
async def upload_progress_callback(c, t, m, user_id):
    if user_id in CANCELLATION_REQUESTS: raise Exception("Upload cancelled by user.")
    p=c/t*100
    if(time.time()-globals().get('last_upload_update_time',0))>2:
        try:await m.edit_text(f"⏫ Uploading... {p:.1f}%", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]));globals()['last_upload_update_time']=time.time()
        except:pass

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    try:
        await client.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=message.from_user.id)
    except UserNotParticipant:
        await message.reply_text("Join our channel to use me.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Join Channel", url=f"https://t.me/{FORCE_SUB_CHANNEL.lstrip('@')}")]])); return
    except Exception: pass
    if users_collection: users_collection.update_one({"_id":message.from_user.id},{"$set":{"first_name":message.from_user.first_name,"last_name":message.from_user.last_name,"username":message.from_user.username}},upsert=True)
    start_text = ("» **I'M RX Downloader BOT**\n\n" + "📥 **I CAN DOWNLOAD VIDEOS FROM:**\n" + "• YOUTUBE, INSTAGRAM, TIKTOK\n" + "• PORNHUB, XVIDEOS, XNXX\n" + "• AND 1000+ OTHER SITES!\n\n" + "🚀 **JUST SEND ME A LINK!**")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("• SUPPORTED SITES", callback_data="show_sites_list"), InlineKeyboardButton("• MAINTAINED BY", url=MAINTAINED_BY_URL)]])
    await message.reply_photo(photo=START_PHOTO_URL, caption=start_text, reply_markup=keyboard)

@app.on_message(filters.command("sites") & filters.private)
async def sites_command(client, message): await message.reply_text(get_sites_list_text())
@app.on_message(filters.command("help") & filters.private)
async def help_command(client, message):
    help_text = ("**How to use RX Downloader Bot:**\n\n" + "1. **Send a Link:** Simply paste a video link.\n" + "2. **Check Supported Sites:** Use /sites to see the full list.\n" + "3. **Cancel a Download:** Click the 'Cancel' button.\n" + "If a link fails, use the 'Report Link' button to help me improve!")
    await message.reply_text(help_text)

@app.on_message(filters.command("stats") & filters.user(OWNER_ID))
async def stats_command(client, message):
    total_users = users_collection.count_documents({}) if users_collection else 0
    await message.reply_text(f"📊 **Bot Stats**\n\nTotal Users: `{total_users}`")
@app.on_message(filters.command("addsite") & filters.user(OWNER_ID))
async def add_site_command(client, message):
    try:
        domain = message.text.split(" ", 1)[1].strip().lower()
        if sites_collection.find_one({"domain": domain}): await message.reply_text(f"`{domain}` is already in the list."); return
        sites_collection.insert_one({"domain": domain}); SITES_LIST.append(domain)
        await message.reply_text(f"✅ Added `{domain}`.")
    except Exception: await message.reply_text("Usage: `/addsite example.com`")
@app.on_message(filters.command("delsite") & filters.user(OWNER_ID))
async def del_site_command(client, message):
    try:
        domain = message.text.split(" ", 1)[1].strip().lower()
        if sites_collection.delete_one({"domain": domain}).deleted_count > 0:
            if domain in SITES_LIST: SITES_LIST.remove(domain)
            await message.reply_text(f"✅ Removed `{domain}`.")
        else: await message.reply_text(f"`{domain}` was not found.")
    except Exception: await message.reply_text("Usage: `/delsite example.com`")
@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast_command(client, message):
    if message.from_user.id in BROADCAST_IN_PROGRESS: await message.reply_text("Broadcast already in progress. /cancelbroadcast to stop."); return
    BROADCAST_IN_PROGRESS[message.from_user.id] = True
    await message.reply_text("Broadcast mode started. Send the message to broadcast, or /cancelbroadcast.")
@app.on_message(filters.command("cancelbroadcast") & filters.user(OWNER_ID))
async def cancel_broadcast_command(client, message):
    if message.from_user.id in BROADCAST_IN_PROGRESS:
        del BROADCAST_IN_PROGRESS[message.from_user.id]
        await message.reply_text("Broadcast mode cancelled.")
    else: await message.reply_text("You are not in broadcast mode.")

@app.on_callback_query(filters.regex("^show_sites_list$"))
async def show_sites_handler(client, c_q): await c_q.answer(); await c_q.message.reply_text(get_sites_list_text())
@app.on_callback_query(filters.regex("^report_"))
async def report_link_handler(client, c_q):
    try:
        url = base64.urlsafe_b64decode(c_q.data.split("_", 1)[1]).decode('utf-8')
        await client.send_message(REPORT_CHANNEL_ID, f"🚨 **Link Report**\n\n**User:** {c_q.from_user.mention} (`{c_q.from_user.id}`)\n**URL:** `{url}`")
        await c_q.answer("✅ Thank you! The link has been reported.", show_alert=True)
        await c_q.edit_message_reply_markup(None)
    except Exception as e: print(f"Report error: {e}"); await c_q.answer("Could not send report.", show_alert=True)
@app.on_callback_query(filters.regex("^cancel_"))
async def cancel_handler(client, c_q):
    user_id = int(c_q.data.split("_")[1])
    if c_q.from_user.id != user_id: await c_q.answer("This is not for you!", show_alert=True); return
    CANCELLATION_REQUESTS.add(user_id); await c_q.answer("Cancellation request sent.", show_alert=False); await c_q.message.edit_text("🤚 **Cancellation requested...**")

# --- THIS IS THE CORRECTED FILTER FOR NON-COMMAND MESSAGES ---
@app.on_message(filters.private & filters.text & ~filters.command)
async def main_message_handler(client, message):
    user_id = message.from_user.id
    if user_id in BROADCAST_IN_PROGRESS:
        del BROADCAST_IN_PROGRESS[user_id]
        all_users = [u['_id'] for u in users_collection.find({}, {'_id': 1})]
        total, success, failed = len(all_users), 0, 0
        status_msg = await message.reply_text(f"Broadcasting to {total} users...")
        for i, user_id_to_send in enumerate(all_users):
            try:
                await message.copy(chat_id=user_id_to_send)
                success += 1
            except (UserIsBlocked, InputUserDeactivated): failed += 1
            except Exception as e: failed += 1; print(f"Broadcast error to {user_id_to_send}: {e}")
            if (i + 1) % 20 == 0 or (i + 1) == total:
                await status_msg.edit_text(f"**Broadcast Progress**\n\nSent: {success}/{total}\nFailed: {failed}"); await asyncio.sleep(1)
        await status_msg.edit_text(f"✅ **Broadcast Complete**\nSent: {success}\nFailed: {failed}"); return

    await link_processor(client, message)

async def link_processor(client, message):
    user_id = message.from_user.id
    try:
        await client.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
    except UserNotParticipant:
        await message.reply_text("Join our channel to use me.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Join Channel", url=f"https://t.me/{FORCE_SUB_CHANNEL.lstrip('@')}")]])); return
    except Exception as e: print(f"Force sub error: {e}"); await message.reply_text("Error checking membership."); return
    global DOWNLOAD_IN_PROGRESS
    if DOWNLOAD_IN_PROGRESS: await message.reply_text("🤚 **Bot is busy!**"); return
    url = message.text.strip() if message.text else ""
    if not url.startswith(('http://', 'https://')): await message.reply_text("Please send a valid link or use /help."); return
    if not any(site in url for site in SITES_LIST): await message.reply_text("❌ **Sorry, this site is not supported.**\nUse /sites to check."); return
    DOWNLOAD_IN_PROGRESS = True; CANCELLATION_REQUESTS.discard(user_id)
    status_msg = await message.reply_text("✅ **URL received, starting...**", quote=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    try:
        if "erome.com" in url or "erome.io" in url:
            await handle_erome_album(url, message, status_msg)
        else:
            await process_video_url(url, {}, message, status_msg)
    except Exception as e: print(f"--- LINK HANDLER ERROR ---\n{traceback.format_exc()}\n---"); await status_msg.edit_text(f"❌ Critical error: {e}")
    finally: CANCELLATION_REQUESTS.discard(user_id); DOWNLOAD_IN_PROGRESS = False

async def handle_erome_album(url, message, status_message):
    album_limit = 15; user_id = message.from_user.id;
    await status_message.edit_text("🔎 Erome album detected, checking content...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    meta_opts = {'extract_flat': True, 'quiet': True, 'playlistend': album_limit}
    with YoutubeDL(meta_opts) as ydl: info = ydl.extract_info(url, download=False)
    original_entries, content_to_process, seen_filenames = info.get('entries', []), [], set()
    for entry in original_entries:
        filename = entry.get('url', '').split('/')[-1]
        if filename and filename not in seen_filenames: content_to_process.append(entry); seen_filenames.add(filename)
    if not content_to_process: await status_message.edit_text("❌ No content found in this Erome album."); return
    content_count = len(content_to_process)
    await status_message.edit_text(f"✅ Found **{content_count}** unique items (limit {album_limit}). Processing...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    for i, entry in enumerate(content_to_process, 1):
        if user_id in CANCELLATION_REQUESTS: await status_message.edit_text("✅ **Album processing cancelled.**"); break
        entry_url = entry['url']
        if any(ext in entry_url for ext in ['.jpg', '.jpeg', '.png', '.gif']):
            await message.reply_photo(photo=entry_url, caption=f"Photo {i}/{content_count}")
        else:
            await status_message.edit_text(f"Downloading video **{i}/{content_count}**...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
            await process_video_url(entry_url, {}, message, status_message, is_album_item=True)
    if user_id not in CANCELLATION_REQUESTS: await status_message.edit_text(f"✅ Finished processing all {content_count} items!", reply_markup=None); await asyncio.sleep(5)
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