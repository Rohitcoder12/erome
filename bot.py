import os
import time
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- Bot Configuration ---
# Fetches all variables from your Koyeb Environment Variables
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID")) # Getting the dump channel ID
# -------------------------

bot = Client("ytdl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Progress bar function (no changes needed here)
def progress(current, total, *args):
    message, start_time = args
    if total == 0: return # Avoid division by zero for streams
    elapsed_time = time.time() - start_time
    if elapsed_time == 0: elapsed_time = 0.01
    speed = current / elapsed_time
    percentage = current * 100 / total
    progress_bar = "{0}{1}".format(
        ''.join(["●" for i in range(int(percentage / 10))]),
        ''.join(["○" for i in range(10 - int(percentage / 10))])
    )
    progress_message = (
        f"**Downloading...**\n"
        f"[{progress_bar}] {percentage:.1f}%\n"
        f"**Speed:** {speed / 1024 / 1024:.2f} MB/s\n"
        f"**Size:** {current / 1024 / 1024:.2f} MB / {total / 1024 / 1024:.2f} MB"
    )
    try:
        # Edit the message only if the content has changed
        if message.text != progress_message:
            bot.edit_message_text(message.chat.id, message.id, progress_message)
    except Exception:
        pass

@bot.on_message(filters.command("start"))
def start(client, message):
    message.reply_text("Hello! I am a video downloader bot. Send me a link from a supported site to get started.")

@bot.on_message(filters.text & ~filters.command("start"))
def download_video(client, message):
    url = message.text
    sent_message = message.reply_text("⏳ `Processing your link...`")

    # --- UPDATED YT-DLP OPTIONS WITH THUMBNAIL EMBEDDING ---
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'progress_hooks': [lambda d: progress(d.get('downloaded_bytes', 0), d.get('total_bytes', 0), sent_message, time.time())],
        
        # -- THIS IS THE NEW PART FOR THUMBNAILS --
        'writethumbnail': True,
        'postprocessors': [{
            'key': 'FFmpegMetadata',
        }, {
            'key': 'EmbedThumbnail',
            'already_have_thumbnail': False,
        }],
        # ----------------------------------------
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        
        bot.edit_message_text(sent_message.chat.id, sent_message.id, "✅ `Download complete! Now uploading...`")

        bot.send_video(
            chat_id=message.chat.id,
            video=filename,
            caption=info.get('title', 'Video'),
            supports_streaming=True
        )

        # Clean up local files
        os.remove(filename)
        # Attempt to remove the thumbnail file as well
        thumbnail_file = filename.rsplit('.', 1)[0] + '.jpg'
        if os.path.exists(thumbnail_file):
            os.remove(thumbnail_file)
        
        bot.delete_messages(sent_message.chat.id, sent_message.id)

    except Exception as e:
        # --- ERROR HANDLING WITH DUMP CHANNEL ---
        error_message_to_user = f"❌ **An error occurred.**\n\n`The developers have been notified.`"
        bot.edit_message_text(sent_message.chat.id, sent_message.id, error_message_to_user)
        
        # Send detailed error to your dump channel
        error_details = (
            f"**BOT ERROR LOG**\n\n"
            f"**User:** {message.from_user.mention}\n"
            f"**User ID:** `{message.from_user.id}`\n"
            f"**Link:** `{url}`\n\n"
            f"**Error:**\n`{str(e)}`"
        )
        bot.send_message(
            chat_id=DUMP_CHANNEL_ID,
            text=error_details
        )
        # ---------------------------------------------

print("Bot is starting...")
bot.run()