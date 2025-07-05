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

# --- Start Message Configuration (EDIT THESE) ---
START_PHOTO_URL = "https://telegra.ph/Wow-07-03-5"
MAINTAINED_BY_URL = "https://t.me/Rexonblood" # CHANGE THIS to your Telegram profile link

# --- Expanded List of Supported Sites ---
SUPPORTED_SITES = [
    # A-F
    "rock.porn", "hdsex.org", "beeg.com", "bravotube.net", "camwhores.tv", "camsoda.com", "chaturbate.com",
    "desitube.com", "drporn.com", "dtube.video", "e-hentai.org", "empflix.com", "eporner.com", "erome.com",
    "erome.io", "exhentai.org", "extremetube.com", "fapbox.com",
    # G-P
    "gaytube.com", "hclips.com", "hentai-foundry.com", "hentaivideos.net", "hentaistream.xxx", "hottystop.com",
    "iqtube.com", "ivxxx.com", "keezmovies.com", "livejasmin.com", "manyvids.com", "metacafe.com", "mofosex.net",
    "motherless.com", "mrdeepfakes.com", "myvidster.com", "noodlemagazine.com", "nuvid.com", "onlyfans.com",
    "perfectgirls.net", "pornhd.com", "pornhub.org", "pornhub.com", "pornteengirl.com", "porntube.com", "pornz.com",
    # R-T
    "redtube.net", "spankbang.com", "sunporno.com", "tnaflix.com", "tube8.es", "tubepleasure.com", "txxx.com", "txnhh.com",
    # U-Z & X-sites
    "vidmax.com", "vxxx.com", "pornoxo.com", "xanimu.com", "nuvid.com", "xhamster.com", "xhamster.desi",
    "xhamster43.desi", "xnxx.com", "xvideos.com", "xtube.com", "xvideos.es", "xvideos.fr", "xv1deos.com",
    "xhamster19.com", "youjizz.com", "youporn.com", "ytporn.com", "youtube.com", "instagram.com", "tiktok.com"
]

# --- Force Subscription Configuration ---
FORCE_SUB_CHANNEL = "@dailynewswalla"

# --- State Management & Other Setups ---
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
app = Client("video_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Helper Functions ---
def create_progress_bar(percentage):
    bar_length=10; filled_length=int(bar_length*percentage//100)
    return '🟢'*filled_length+'⚪'*(bar_length-filled_length)

# --- NEW: Helper function to generate the sites list text (for code reuse) ---
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

    # MODIFIED: Keyboard now uses a callback button for sites
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("• SUPPORTED SITES", callback_data="show_sites_list"),
            InlineKeyboardButton("• MAINTAINED BY", url=MAINTAINED_BY_URL)
        ]]
    )
    await message.reply_photo(photo=START_PHOTO_URL, caption=start_text, reply_markup=keyboard)

@app.on_message(filters.command("sites") & filters.private)
async def sites_command(client, message):
    # This command now uses the helper function
    sites_text = get_sites_list_text()
    await message.reply_text(sites_text)

# --- NEW: Callback handler for the "SUPPORTED SITES" button ---
@app.on_callback_query(filters.regex("^show_sites_list$"))
async def show_sites_handler(client, callback_query):
    # Get the formatted text from the helper function
    sites_text = get_sites_list_text()
    # Acknowledge the button press
    await callback_query.answer()
    # Send the list as a new message
    await callback_query.message.reply_text(sites_text)

@app.on_callback_query(filters.regex("^cancel_"))
async def cancel_handler(client, callback_query):
    user_id = int(callback_query.data.split("_")[1])
    if callback_query.from_user.id != user_id: await callback_query.answer("This is not for you!", show_alert=True); return
    CANCELLATION_REQUESTS.add(user_id)
    await callback_query.answer("Cancellation request sent.", show_alert=False)
    await callback_query.message.edit_text("🤚 **Cancellation requested...** Please wait.")

# The rest of your code is unchanged and correct. I've collapsed it for brevity.
@app.on_message(filters.private & filters.regex(r"https?://[^\s]+"))
async def link_handler(client, message):
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
        if any(erome_domain in url for erome_domain in ["erome.com", "erome.io"]): await handle_erome_album(url, message, status_message)
        else: await handle_single_video(url, message, status_message)
    except Exception as e: print(f"--- UNHANDLED ERROR IN LINK_HANDLER ---\n{traceback.format_exc()}\n--------------------"); await status_message.edit_text(f"❌ A critical error occurred: {e}")
    finally: CANCELLATION_REQUESTS.discard(user_id); DOWNLOAD_IN_PROGRESS = False
async def handle_single_video(url, message, status_message):
    ydl_opts = {'format':'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best','outtmpl':os.path.join(DOWNLOAD_LOCATION,'%(title)s.%(ext)s'),'noplaylist':True,'quiet':True,'progress_hooks':[lambda d:progress_hook(d,status_message,message.from_user.id)],'max_filesize':450*1024*1024}
    await process_video_url(url, ydl_opts, message, status_message)
async def handle_erome_album(url, message, status_message):
    album_limit = 10; user_id = message.from_user.id
    await status_message.edit_text("🔎 This looks like an Erome album...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    meta_opts = {'extract_flat': True, 'quiet': True, 'playlistend': album_limit}
    with YoutubeDL(meta_opts) as ydl: info = ydl.extract_info(url, download=False)
    original_entries, content_to_process, seen_filenames = info.get('entries', []), [], set()
    for entry in original_entries:
        filename = entry.get('url', '').split('/')[-1]
        if filename and filename not in seen_filenames: content_to_process.append(entry); seen_filenames.add(filename)
    if not content_to_process: await status_message.edit_text("❌ No content found in this Erome album."); return
    content_count = len(content_to_process)
    await status_message.edit_text(f"✅ Album found with **{content_count}** unique items (limit {album_limit}). Processing...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
    await asyncio.sleep(2)
    for i, entry in enumerate(content_to_process, 1):
        if user_id in CANCELLATION_REQUESTS: await status_message.edit_text("✅ **Album processing cancelled.**"); break
        entry_url = entry['url']
        if any(entry_url.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp']): await handle_photo_download(entry, f"[{i}/{content_count}] ", message)
        else:
            single_video_ydl_opts = {'format':'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best','outtmpl':os.path.join(DOWNLOAD_LOCATION,f"album_item_{i}_%(title)s.%(ext)s"),'quiet':True,'progress_hooks':[lambda d:progress_hook(d,status_message,user_id)],'max_filesize':450*1024*1024}
            await status_message.edit_text(f"Downloading video **{i}/{content_count}**...", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"cancel_{user_id}")]]))
            await process_video_url(entry_url, single_video_ydl_opts, message, status_message, is_album_item=True)
    if not user_id in CANCELLATION_REQUESTS: await status_message.edit_text(f"✅ Finished processing all {content_count} items!", reply_markup=None); await asyncio.sleep(5)
    await status_message.delete()
async def handle_photo_download(entry, prefix, message):
    photo_url, photo_title = entry.get('url'), prefix + entry.get('title', 'Untitled Photo')
    await message.reply_photo(photo=photo_url, caption=photo_title); await asyncio.sleep(1)
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
if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_LOCATION): os.makedirs(DOWNLOAD_LOCATION)
    print("Starting web server thread...")
    threading.Thread(target=run_server, daemon=True).start()
    print("Starting Pyrogram bot...")
    app.run()