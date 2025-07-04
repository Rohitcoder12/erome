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
# NEW: Import more error types for robust broadcasting
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
DOWNLOAD_IN_PROGRESS = False
CANCELLATION_REQUESTS = set()
SITES_LIST = []
# NEW: State for broadcasting
BROADCAST_IN_PROGRESS = {}

server = Flask(__name__)
@server.route('/')
def health_check(): return "Bot and Web Server are alive!", 200
def run_server(): server.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
try:
    db_client = MongoClient(MONGO_URI)
    db = db_client.get_database("VideoBotDB")
    users_collection = db.get_collection("users")
    downloads_collection = db.get_collection("downloads_history")
    sites_collection = db.get_collection("supported_sites")
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
    sorted_sites = sorted(list(set(SITES_LIST)))
    if not sorted_sites: return "No sites are currently supported."
    num_sites = len(sorted_sites); sites_per_column = (num_sites + 2) // 3
    columns = [sorted_sites[i:i + sites_per_column] for i in range(0, num_sites, sites_per_column)]
    for row in zip_longest(*columns, fillvalue=""):
        reply_text += f"{row[0]:<25}{row[1]:<25}{row[2]:<25}\n"
    reply_text += "```"
    return reply_text

# ... progress hooks are unchanged ...
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
    await message.reply_text(get_sites_list_text())

# NEW: Help command
@app.on_message(filters.command("help") & filters.private)
async def help_command(client, message):
    help_text = (
        "**How to use RX Downloader Bot:**\n\n"
        "1. **Send a Link:** Simply paste a video link from a supported site and send it to me. I will process it and send you the video.\n\n"
        "2. **Check Supported Sites:** Use the /sites command to see a full list of websites I can download from.\n\n"
        "3. **Cancel a Download:** If a download is taking too long or you sent the wrong link, just click the 'Cancel' button.\n\n"
        "If a link fails, you will see a 'Report Link' button. Please use it to help me improve!\n\n"
        "Enjoy the bot!"
    )
    await message.reply_text(help_text)

# --- Admin Commands ---
@app.on_message(filters.command("stats") & filters.user(OWNER_ID))
async def stats_command(client, message):
    total_users = users_collection.count_documents({})
    await message.reply_text(f"📊 **Bot Stats**\n\nTotal Users: `{total_users}`")

@app.on_message(filters.command("addsite") & filters.user(OWNER_ID))
async def add_site_command(client, message):
    try:
        domain = message.text.split(" ", 1)[1].strip().lower()
        if not domain: await message.reply_text("Usage: `/addsite example.com`"); return
        if sites_collection.find_one({"domain": domain}): await message.reply_text(f"`{domain}` is already in the list."); return
        sites_collection.insert_one({"domain": domain})
        SITES_LIST.append(domain)
        await message.reply_text(f"✅ Successfully added `{domain}`.")
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
            await message.reply_text(f"✅ Successfully removed `{domain}`.")
        else: await message.reply_text(f"`{domain}` was not found.")
    except IndexError: await message.reply_text("Usage: `/delsite example.com`")
    except Exception as e: await message.reply_text(f"An error occurred: {e}")

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def broadcast_command(client, message):
    if message.from_user.id in BROADCAST_IN_PROGRESS:
        await message.reply_text("A broadcast is already in progress. Send /cancelbroadcast to stop it.")
        return
    BROADCAST_IN_PROGRESS[message.from_user.id] = True
    await message.reply_text("You have started broadcast mode. Send me the message (text, photo, video, album, etc.) you want to send to all users. To exit, send /cancelbroadcast.")

@app.on_message(filters.command("cancelbroadcast") & filters.user(OWNER_ID))
async def cancel_broadcast_command(client, message):
    if message.from_user.id in BROADCAST_IN_PROGRESS:
        del BROADCAST_IN_PROGRESS[message.from_user.id]
        await message.reply_text("Broadcast mode has been cancelled.")
    else:
        await message.reply_text("You are not currently in broadcast mode.")

# --- Callback Handlers ---
@app.on_callback_query(filters.regex("^show_sites_list$"))
async def show_sites_handler(client, callback_query):
    await callback_query.answer()
    await callback_query.message.reply_text(get_sites_list_text())
@app.on_callback_query(filters.regex("^report_"))
async def report_link_handler(client, callback_query):
    try:
        encoded_url = callback_query.data.split("_", 1)[1]
        decoded_url = base64.urlsafe_b64decode(encoded_url).decode('utf-8')
        user = callback_query.from_user
        report_text = (f"🚨 **Link Report**\n\n**User:** {user.mention} (`{user.id}`)\n**Reported URL:** `{decoded_url}`")
        await client.send_message(chat_id=REPORT_CHANNEL_ID, text=report_text)
        await callback_query.answer("✅ Thank you! The link has been reported.", show_alert=True)
        await callback_query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        print(f"Error handling report: {e}"); await callback_query.answer("Could not send report.", show_alert=True)
@app.on_callback_query(filters.regex("^cancel_"))
async def cancel_handler(client, callback_query):
    user_id = int(callback_query.data.split("_")[1])
    if callback_query.from_user.id != user_id: await callback_query.answer("This is not for you!", show_alert=True); return
    CANCELLATION_REQUESTS.add(user_id); await callback_query.answer("Cancellation request sent.", show_alert=False); await callback_query.message.edit_text("🤚 **Cancellation requested...** Please wait.")

# --- Message Handlers (Link and Broadcast) ---
@app.on_message(filters.private)
async def main_message_handler(client, message):
    # This handler acts as a router
    user_id = message.from_user.id

    # 1. Check for Broadcast Mode
    if user_id in BROADCAST_IN_PROGRESS:
        del BROADCAST_IN_PROGRESS[user_id]
        all_users = [user['_id'] for user in users_collection.find({}, {'_id': 1})]
        total_users = len(all_users)
        success_count, failed_count = 0, 0
        
        status_msg = await message.reply_text(f"Broadcasting started to {total_users} users...")
        
        for i, user_id in enumerate(all_users):
            try:
                await message.forward(chat_id=user_id)
                success_count += 1
            except (UserIsBlocked, InputUserDeactivated):
                failed_count += 1
            except Exception as e:
                failed_count += 1
                print(f"Broadcast error to user {user_id}: {e}")
            
            if (i + 1) % 20 == 0 or (i + 1) == total_users:
                await status_msg.edit_text(
                    f"**Broadcast Progress**\n\n"
                    f"Sent: {success_count}/{total_users}\n"
                    f"Failed: {failed_count}"
                )
                await asyncio.sleep(1) # Sleep to avoid hitting flood limits
        
        await status_msg.edit_text(f"✅ **Broadcast Complete**\n\nSent to: {success_count} users\nFailed for: {failed_count} users")
        return # Stop further processing

    # 2. Check for Link
    if message.text and message.text.startswith(('http://', 'https://')):
        await link_handler(client, message)
    else:
        # Optional: Reply to non-link, non-command messages
        await message.reply_text("Please send me a valid video link, or use /help to see what I can do.")

async def link_handler(client: Client, message: Message):
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
    if not any(site in url for site in SITES_LIST): await message.reply_text("❌ **Sorry, this website is not supported.**\n\nUse /sites to see the full list."); return
    DOWNLOAD_IN_PROGRESS = True; CANCELLATION_REQUESTS.discard(user_id)
    status_message = await message.reply_text("✅ **URL received. Starting process...**", quote=True, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    try:
        await process_video_url(url, {}, message, status_message)
    except Exception as e: print(f"--- UNHANDLED ERROR IN LINK_HANDLER ---\n{traceback.format_exc()}\n--------------------"); await status_message.edit_text(f"❌ A critical error occurred: {e}")
    finally: CANCELLATION_REQUESTS.discard(user_id); DOWNLOAD_IN_PROGRESS = False

async def process_video_url(url, ydl_opts_override, original_message, status_message, is_album_item=False):
    # This function now sets default ydl_opts and can be used for all downloads
    video_path, thumbnail_path = None, None; user_id = original_message.from_user.id
    download_log_id = ObjectId()
    if downloads_collection is not None: downloads_collection.insert_one({"_id": download_log_id, "user_id": user_id, "url": url, "status": "processing", "start_time": datetime.now(timezone.utc)})
    
    ydl_opts = {
        'format':'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl':os.path.join(DOWNLOAD_LOCATION,'%(title)s.%(ext)s'),
        'noplaylist':True,
        'quiet':True,
        'progress_hooks':[lambda d:progress_hook(d,status_message,message.from_user.id)],
        'max_filesize':450*1024*1024,
    }
    ydl_opts.update(ydl_opts_override)

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
        error_message = f"❌ An error occurred: {type(e).__name__}"; report_markup = None
        if "cancelled by user" in str(e): error_message = "✅ **Operation cancelled."
        else:
            if "is larger than" in str(e): error_message = "❌ **Error:** Video is too large."
            encoded_url = base64.urlsafe_b64encode(url.encode()).decode()
            report_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🚨 Report Link", callback_data=f"report_{encoded_url}")]])
        if downloads_collection is not None: downloads_collection.update_one({"_id": download_log_id}, {"$set": {"status": "failed", "error_message": str(e)}})
        print(f"--- PROCESS_VIDEO_URL ERROR ---\n{traceback.format_exc()}\n--------------------")
        if not is_album_item: await status_message.edit_text(error_message, reply_markup=report_markup)
    finally:
        if video_path and os.path.exists(video_path): os.remove(video_path)
        if thumbnail_path and os.path.exists(thumbnail_path): os.remove(thumbnail_path)
        if not is_album_item:
            await asyncio.sleep(5)
            try: await status_message.delete()
            except Exception: pass

# --- Main Entry Point ---
def load_sites_from_db():
    global SITES_LIST
    sites = sites_collection.find()
    db_