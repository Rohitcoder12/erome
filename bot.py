# app.py (Ultimate Debug Version with /ping command)
import os
import asyncio
import threading
import json
import logging
from flask import Flask
from pyrogram import Client, filters, errors
from pyrogram.types import Message

# --- Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(name)s] - %(levelname)s - %(message)s')
logger_control = logging.getLogger('ControlBot')
logger_user = logging.getLogger('UserBot')

flask_app = Flask(__name__)
@flask_app.route('/')
def health_check(): return "Bot is alive!", 200
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# --- Config and State Management ---
CONFIG_FILE_PATH = "data/config.json"
login_state = {}

def get_env(name, message, cast=str):
    if name in os.environ:
        return cast(os.environ[name])
    logging.critical(message)
    exit(1)

def load_config():
    if not os.path.exists(CONFIG_FILE_PATH): return {}
    with open(CONFIG_FILE_PATH, 'r') as f:
        try: return json.load(f)
        except json.JSONDecodeError: return {}

def save_config(config):
    with open(CONFIG_FILE_PATH, 'w') as f:
        json.dump(config, f, indent=4)

# --- Pyrogram Clients Setup ---
API_ID = get_env('API_ID', 'API_ID not set.', int)
API_HASH = get_env('API_HASH', 'API_HASH not set.')
BOT_TOKEN = get_env('BOT_TOKEN', 'BOT_TOKEN not set.')
ADMIN_ID = get_env('ADMIN_ID', 'ADMIN_ID not set.', int)

control_bot = Client(
    "data/control_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

user_bot = None
admin_filter = filters.user(ADMIN_ID)

# --- Control Bot Command Handlers ---

# NEW DEBUG COMMAND - NO ADMIN FILTER
@control_bot.on_message(filters.command("ping"))
async def ping_command(client, message: Message):
    logger_control.info(f"/ping command received from user ID: {message.from_user.id}. Bot is alive and responding.")
    await message.reply_text("Pong! The bot is receiving messages.")

@control_bot.on_message(filters.command("start") & admin_filter)
async def start_command(client, message: Message):
    logger_control.info(f"/start command received from user ID: {message.from_user.id}. Your configured ADMIN_ID is: {ADMIN_ID}")
    welcome_text = (
        "👋 **Welcome to your Forwarder Bot!**\n\n"
        "This bot helps you forward messages from private/restricted channels.\n\n"
        "**Available Commands:**\n"
        "🔹 `/login` - Start the process to log in with a user account.\n"
        "🔹 `/logout` - Log out the current user account.\n"
        "🔹 `/add <name> <dest_id>` - Create a new forward rule.\n"
        "🔹 `/addsource <name> <src_id>` - Add a source to a rule.\n"
        "🔹 `/delete <name>` - Delete a forward rule.\n"
        "🔹 `/list` - Show all configured rules.\n\n"
        "To begin, please use the `/login` command."
    )
    await message.reply_text(welcome_text)

# (All other commands like /login, /add, etc. remain the same and are omitted here for brevity)
# ... The rest of your command handlers go here ...


# --- Main Application Start ---
async def main():
    await control_bot.start()
    logger_control.info("Control Bot started.")
    # The userbot management logic is also unchanged
    # asyncio.create_task(manage_userbot())
    await asyncio.Event().wait()


if __name__ == "__main__":
    if not os.path.exists('data'): os.makedirs('data')
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    logger_control.info("Starting application...")
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Application stopped.")