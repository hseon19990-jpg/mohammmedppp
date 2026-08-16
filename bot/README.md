# Telegram Account Manager Bot

This bot is implemented in Python and runs with `python-telegram-bot`.

## Environment

- `BOT_TOKEN` — token from BotFather.
- `OWNER_TELEGRAM_ID` — Telegram user ID that can view, edit, verify, and delete accounts.
- `PURCHASE_CHANNEL_1` — optional first group/channel chat ID or `@username` for purchase notifications.
- `PURCHASE_CHANNEL_2` — optional second group/channel chat ID or `@username` for purchase notifications.
- `DATA_DIR` — optional data directory. If it is not set, the bot uses
  `RAILWAY_VOLUME_MOUNT_PATH`, then `/app/data`.

For purchase notifications, add the bot to both chats (with permission to send
messages) and set both `PURCHASE_CHANNEL_1` and `PURCHASE_CHANNEL_2`. The bot
uses escaped HTML formatting so user-entered names and service text cannot
break Telegram message delivery.

The bot stores users, balances, requests, settings, and uploaded videos in this
directory. It also writes a `.bak` copy of each JSON file and saves changes
atomically, so restarts and interrupted writes do not erase the data.

To keep data after Railway redeploys, attach a **Railway Volume** to the bot
service and mount it at `/app/data` (or set `DATA_DIR` to the volume mount path).
The code automatically migrates existing files from the old data paths when the
new persistent directory is empty. Do not delete the volume.

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python main.py
```
