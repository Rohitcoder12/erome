import os
import time
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- Your Bot Configuration ---
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
# -----------------------------

bot = Client("ytdl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Progress bar function
def progress(current, total, *args):
    message, start_time = args
    elapsed_time = time.time() - start_time
    if elapsed_time == 0:
        elapsed_time = 0.01
    speed = current / elapsed_time
    percentage = current * 100 / total
    progress_bar = "{0}{1}".format(
        ''.join(["●" for i in range(int(percentage / 10))]),
        ''.join(["○" for i in range(10 - int(percentage / 10))])
    )
    progress_message = (
        f"Downloading...\n"
        f"[{progress_bar}] {percentage:.2f}%\n"
        f"Speed: {speed / 1024 / 1024:.2f} MB/s\n"
        f"Downloaded: {current / 1024 / 1024:.2f} MB / {total / 1024 / 1024:.2f} MB"
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
    sent_message = message.reply_text("Processing your link...")

    # --- THIS IS THE PART YOU NEED TO CHANGE ---
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'progress_hooks': [lambda d: progress(d.get('downloaded_bytes', 0), d.get('total_bytes', 0), sent_message, time.time())],
        
        # --- NEW OPTIONS START HERE ---
        'writethumbnail': True,  # Tell yt-dlp to download the thumbnail image
        'postprocessors': [{
            'key': 'FFmpegMetadata',   # Use the FFmpeg post-processor to write metadata
        }, {
            'key': 'EmbedThumbnail',   # Specify the task of embedding the thumbnail
            'already_have_thumbnail': False, # Tell it to use the thumbnail it just downloaded
        }],
        # --- NEW OPTIONS END HERE ---
    }
    # ---------------------------------------------

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        
        bot.edit_message_text(sent_message.chat.id, sent_message.id, "Download complete! Now uploading...")

        # Send the video file
        bot.send_video(
            chat_id=message.chat.id,
            video=filename,
            caption=info.get('title', 'Video'),
            supports_streaming=True
        )

        # Clean up
        os.remove(filename)
        # Also remove the downloaded thumbnail if it exists
        thumbnail_file = filename.replace('.mp4', '.jpg', 1) # Or .webp, .png etc. common formats
        if os.path.exists(thumbnail_file):
            os.remove(thumbnail_file)

        bot.delete_messages(sent_message.chat.id, sent_message.id)

    except Exception as e:
        bot.edit_message_text(sent_message.chat.id, sent_message.id, f"An error occurred: {e}")

bot.run()