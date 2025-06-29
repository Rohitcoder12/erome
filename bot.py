# bot.py

import os
import requests
import telebot
import yt_dlp
import subprocess
from bs4 import BeautifulSoup

# --- Configuration ---
# Get the bot token from an environment variable for security
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN environment variable not set.")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# --- Helper Functions ---

def embed_thumbnail(video_path, thumbnail_path, output_path):
    """
    Embeds a thumbnail into a video file using FFmpeg.
    Returns True on success, False on failure.
    """
    print(f"Embedding thumbnail '{thumbnail_path}' into '{video_path}'...")
    
    # FFmpeg command to create a new video with the embedded thumbnail
    # -i video.mp4          -> Input video
    # -i image.jpg          -> Input image
    # -map 0                -> Map all streams from the first input (video)
    # -map 1                -> Map all streams from the second input (image)
    # -c copy               -> Copy codecs, don't re-encode (fast, preserves quality)
    # -disposition:v:1 attached_pic -> Set the image as an "attached picture" (cover)
    # output.mp4            -> The final output file
    command = [
        'ffmpeg',
        '-i', video_path,
        '-i', thumbnail_path,
        '-map', '0',
        '-map', '1',
        '-c', 'copy',
        '-disposition:v:1', 'attached_pic',
        '-y', # Overwrite output file if it exists
        output_path
    ]

    try:
        # Run the command. We capture output to prevent spamming logs unless there's an error.
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"Successfully created video with embedded thumbnail: {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        # If FFmpeg fails, print the error for debugging
        print(f"Error during FFmpeg processing.")
        print(f"FFmpeg stdout: {e.stdout}")
        print(f"FFmpeg stderr: {e.stderr}")
        return False
    except FileNotFoundError:
        # This error happens if FFmpeg is not installed
        print("ERROR: FFmpeg command not found. Make sure FFmpeg is installed and in your PATH.")
        print("On Koyeb, ensure you have a 'packages.txt' file with 'ffmpeg' in it.")
        return False

# --- Telegram Bot Handlers ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Hello! Please send me an Erome album URL.")

@bot.message_handler(func=lambda message: True)
def process_url(message):
    url = message.text
    chat_id = message.chat.id

    # Check if the message text is a valid URL
    if not url.startswith('http'):
        bot.reply_to(message, "Please send a valid URL.")
        return

    status_message = bot.send_message(chat_id, "🔗 Processing URL...")

    # Define file paths that will be used
    album_name = "erome_download" # Default name
    original_video_path = None
    thumbnail_path = None
    final_video_path = None
    
    try:
        # 1. Scrape the page
        bot.edit_message_text("📄 Scraping page for video info...", chat_id, status_message.message_id)
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # Sanitize album name for use in filenames
        album_name_tag = soup.find('h1', class_='album-title')
        if album_name_tag:
            album_name = "".join(c for c in album_name_tag.text.strip() if c.isalnum() or c in (' ', '_')).rstrip()

        thumbnail_url = soup.find('img', class_='img-responsive')['src']
        video_url = soup.find('video', id='video-player').find('source')['src']

        # 2. Download Video
        bot.edit_message_text(f"📥 Downloading video: {album_name}", chat_id, status_message.message_id)
        ydl_opts = {'outtmpl': f'{album_name}.%(ext)s'}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            original_video_path = ydl.prepare_filename(info)

        # 3. Download Thumbnail
        bot.edit_message_text("🖼️ Downloading thumbnail...", chat_id, status_message.message_id)
        thumb_response = requests.get(thumbnail_url)
        thumbnail_path = f"{album_name}.jpg"
        with open(thumbnail_path, 'wb') as f:
            f.write(thumb_response.content)

        # 4. Embed Thumbnail with FFmpeg
        bot.edit_message_text("✨ Embedding thumbnail into video...", chat_id, status_message.message_id)
        file_extension = os.path.splitext(original_video_path)[1]
        final_video_path = f"{album_name}_final{file_extension}"
        
        success = embed_thumbnail(original_video_path, thumbnail_path, final_video_path)
        if not success:
            # If embedding fails, send a warning and prepare to send the original video
            bot.send_message(chat_id, "⚠️ Warning: Failed to embed thumbnail. FFmpeg might not be installed correctly. Sending original video.")
            final_video_path = original_video_path # Fallback to the original video

        # 5. Upload the final video
        bot.edit_message_text("⬆️ Uploading to Telegram...", chat_id, status_message.message_id)
        with open(final_video_path, 'rb') as video_file:
            bot.send_video(chat_id, video_file, supports_streaming=True)
        
        # Delete the status message after success
        bot.delete_message(chat_id, status_message.message_id)

    except Exception as e:
        print(f"An error occurred: {e}")
        bot.edit_message_text(f"❌ An error occurred: {e}", chat_id, status_message.message_id)

    finally:
        # 6. Clean up all temporary files, regardless of success or failure
        print("Cleaning up local files...")
        for f in [original_video_path, thumbnail_path, final_video_path]:
            if f and os.path.exists(f):
                os.remove(f)
                print(f"Removed {f}")


if __name__ == '__main__':
    print("Bot is starting...")
    bot.polling(non_stop=True)