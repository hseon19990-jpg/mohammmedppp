"""Backward-compatible entry point for the Telegram bot.

Business logic lives in the telegram_bot package; run this file as before.
"""
from telegram_bot.core import *
from telegram_bot.members import *
from telegram_bot.owner import *
from telegram_bot.approvals import *
from telegram_bot.store import *
from telegram_bot.application import *

if __name__ == "__main__":
    main()
