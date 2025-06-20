import logging
import os
import requests
import uuid
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURATION ---
# Replace 'YOUR_TELEGRAM_BOT_TOKEN' with the token you got from BotFather
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

# --- NEW: DUMP CHANNEL CONFIGURATION ---
# Replace with your channel's ID (e.g., -1001234567890). Leave as None to disable.
# Your bot MUST be an admin in this channel.
DUMP_CHANNEL_ID = None  # Example: -1001234567890

# --- SETUP ---

# Enable logging to see errors and bot activity
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- SCRAPER FUNCTION ---

def get_erome_video_urls(album_url: str) -> list:
    """
    Scrapes an Erome album page to find direct video URLs.
    """
    video_urls = []
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(album_url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        video_tags = soup.find_all('video')
        for video in video_tags:
            source_tag = video.find('source')
            if source_tag and source_tag.get('src'):
                video_urls.append(source_tag['src'])
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching Erome page: {e}")
        return []
    except Exception as e:
        logger.error(f"An unexpected error occurred during scraping: {e}")
        return []
    return video_urls


# --- TELEGRAM BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    await update.message.reply_text(
        "Hi! I am the Erome Downloader Bot.\n\n"
        "Send me a link to an Erome album (e.g., https://www.erome.com/a/albumId) "
        "and I will download and send you the videos."
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles messages containing Erome links."""
    message_text = update.message.text
    user = update.effective_user
    chat_id = update.effective_chat.id

    if "erome.com/a/" not in message_text:
        await update.message.reply_text("This doesn't look like a valid Erome album link. Please send a link that starts with `https://www.erome.com/a/...`")
        return

    processing_message = await update.message.reply_text("🔗 Link received! Processing...")
    video_urls = get_erome_video_urls(message_text)

    if not video_urls:
        await processing_message.edit_text("❌ No videos found on this page, or the page could not be accessed.")
        return

    await processing_message.edit_text(f"✅ Found {len(video_urls)} video(s). Starting download and upload process...")

    for i, video_url in enumerate(video_urls, 1):
        file_path = f"{uuid.uuid4()}.mp4"
        
        try:
            await context.bot.send_message(chat_id, f"Downloading video {i} of {len(video_urls)}...")
            
            with requests.get(video_url, stream=True) as r:
                r.raise_for_status()
                with open(file_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            # --- MODIFIED: Clearer message for file size limit ---
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if file_size_mb > 49.5: # A little buffer
                too_large_message = (
                    f"⚠️ Video {i} is {file_size_mb:.2f} MB, which is larger than the 50 MB limit "
                    f"for Telegram bots. I cannot upload this file."
                )
                await context.bot.send_message(chat_id, too_large_message)
                # --- NEW: Send link to dump channel if it's too big for user ---
                if DUMP_CHANNEL_ID:
                    await context.bot.send_message(DUMP_CHANNEL_ID, f"File too large for user but here is the link:\n{video_url}")
                continue

            # --- NEW: Upload to Dump Channel First ---
            if DUMP_CHANNEL_ID:
                try:
                    dump_caption = (
                        f"Source: {message_text}\n"
                        f"User: {user.first_name} (@{user.username}, ID: {user.id})"
                    )
                    with open(file_path, 'rb') as video_file:
                        await context.bot.send_video(
                            chat_id=DUMP_CHANNEL_ID, 
                            video=video_file, 
                            caption=dump_caption,
                            supports_streaming=True
                        )
                except Exception as e:
                    logger.error(f"Failed to send video to dump channel: {e}")
                    # Optionally notify an admin
                    # await context.bot.send_message(DUMP_CHANNEL_ID, f"Failed to log video. Error: {e}")

            # --- Upload to User ---
            await context.bot.send_message(chat_id, f"Uploading video {i} of {len(video_urls)}...")
            with open(file_path, 'rb') as video_file:
                await context.bot.send_video(chat_id=chat_id, video=video_file, supports_streaming=True)

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download video {i}: {e}")
            await context.bot.send_message(chat_id, f"❌ Failed to download video {i}.")
        except Exception as e:
            logger.error(f"An error occurred with video {i}: {e}")
            await context.bot.send_message(chat_id, f"❌ An error occurred while processing video {i}.")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    await context.bot.send_message(chat_id, "✨ All done!")


# --- MAIN FUNCTION TO RUN THE BOT ---

def main() -> None:
    """Start the bot."""
    if BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print("!!! ERROR: Please replace 'YOUR_TELEGRAM_BOT_TOKEN' in the script with your actual bot token. !!!")
        return

    # --- NEW: Check for dump channel configuration ---
    if DUMP_CHANNEL_ID:
        print(f"✅ Dump channel is configured (ID: {DUMP_CHANNEL_ID}). Make sure the bot is an admin in this channel.")
    else:
        print("ℹ️ No dump channel configured. Videos will only be sent to users.")
        
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    print("Bot is running... Press Ctrl-C to stop.")
    application.run_polling()


if __name__ == "__main__":
    main()
