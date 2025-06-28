import os
import time
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- Bot Configuration ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DUMP_CHANNEL_ID = int(os.environ.get("DUMP_CHANNEL_ID"))
# -------------------------

bot = Client("ytdl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Progress bar function (no changes needed)
def progress(current, total, *args):
    message, start_time = args
    if total == 0: return
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
        if message.text != progress_message:
            bot.edit_message_text(message.chat.id, message.id, progress_message)
    except Exception:
        pass

# --- THIS IS THE UPDATED START COMMAND HANDLER ---
@bot.on_message(filters.command("start") & filters.private)
def start(client, message):
    # The new, more informative welcome message
    start_text = """
Hello! I am a video downloader bot powered by `yt-dlp`.

I can download videos from **over 1,800 websites**, including:
- YouTube, Twitter, TikTok, Instagram, Facebook
- Xvideos, Pornhub, XNXX, xHamster, Erome
- And many, many more news, social media, and educational sites.

Just send me a link to get started!
    """
    # Create a button that links to the full list of sites
    reply_markup = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "View Full List of Supported Sites",
                url="https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md"
            )
        ]]
    )
    
    message.reply_text(
        text=start_text,
        reply_markup=reply_markup,
        disable_web_page_preview=True # Optional: keeps the message clean
    )
# ---------------------------------------------------

@bot.on_message(filters.text & filters.private & ~filters.command("start"))
def download_video(client, message):
    url = message.text
    sent_message = message.reply_text("⏳ `Processing your link...`")
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'progress_hooks': [lambda d: progress(d.get('downloaded_bytes', 0), d.get('total_bytes', 0), sent_message, time.time())],
        'writethumbnail': True,
        'postprocessors': [{
            'key': 'FFmpegMetadata',
        }, {
            'key': 'EmbedThumbnail',
            'already_have_thumbnail': False,
        }],
        'age_limit': 21,
    }

    filename = None
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

    except Exception as e:
        error_message_to_user = f"❌ **An error occurred.**\n\n`The developers have been notified.`"
        bot.edit_message_text(sent_message.chat.id, sent_message.id, error_message_to_user)
        
        error_details = (
            f"**BOT ERROR LOG**\n\n"
            f"**User:** {message.from_user.mention}\n"
            f"**User ID:** `{message.from_user.id}`\n"
            f"**Link:** `{url}`\n\n"
            f"**Error:**\n`{str(e)}`"
        )
        bot.send_message(chat_id=DUMP_CHANNEL_ID, text=error_details)

    finally:
        if filename and os.path.exists(filename):
            os.remove(filename)
            thumbnail_file = filename.rsplit('.', 1)[0] + '.jpg'
            if os.path.exists(thumbnail_file):
                os.remove(thumbnail_file)
        
        bot.delete_messages(sent_message.chat.id, sent_message.id, revoke=True)

print("Bot is starting...")
bot.run()