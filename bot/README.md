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

## Mandatory channel

The owner can open **➕ إضافة قناة إجبارية** from the owner panel and set one
channel that members must join before using the bot. The bot must be an
administrator in that channel so Telegram can verify each member.

For a public channel, send its `@username` or `https://t.me/username`. For a
private channel, send its numeric ID and invite link separated by `|`, for
example:

```text
-1001234567890 | https://t.me/+invite
```

The owner is exempt from the membership check. Members who have not joined see
a join button and can retry the check after joining.

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
python bot.py
```
