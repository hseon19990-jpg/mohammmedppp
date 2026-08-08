# Telegram Account Manager Bot

This bot is implemented in Python and runs with `python-telegram-bot`.

## Environment

- `BOT_TOKEN` — token from BotFather.
- `OWNER_TELEGRAM_ID` — Telegram user ID that can view, edit, verify, and delete accounts.
- `DATA_DIR` — optional data directory. Defaults to `RAILWAY_VOLUME_MOUNT_PATH`, then `./data`.

The bot keeps the existing `accounts.json` format. Attach a Railway volume at
`/app/data` if account data must survive deployments.

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python main.py
```