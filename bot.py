"""
Advanced Telegram Account Manager Bot
- Fully working with 2FA code generation
- Owner panel fixed
"""

import asyncio
import json
import logging
import os
import re
import time
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pyotp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Curl_ffi for Chrome impersonation
try:
    from curl_cffi import requests
except ImportError:
    import requests

# ==================== CONFIGURATION ====================
def clean_env_value(name: str) -> str:
    return os.environ.get(name, "").strip().strip("\"'")

def parse_int_env(*names: str) -> int:
    for name in names:
        raw_value = clean_env_value(name)
        match = re.search(r"-?\d+", raw_value)
        if match:
            return int(match.group())
    return 0

BOT_TOKEN = clean_env_value("BOT_TOKEN")
OWNER_ID = parse_int_env("OWNER_TELEGRAM_ID", "OWNER_ID", "TELEGRAM_OWNER_ID")
PROXY_URL = clean_env_value("PROXY_URL")
MANDATORY_CHANNEL = clean_env_value("MANDATORY_CHANNEL")
DEFAULT_ACCOUNT_PRICE = float(os.environ.get("DEFAULT_ACCOUNT_PRICE", "5.0"))

DATA_DIR = Path(os.environ.get("DATA_DIR", "/railway/volume/data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

ACCOUNTS_DB = DATA_DIR / "accounts.json"
USERS_DB = DATA_DIR / "users.json"
CONFIG_DB = DATA_DIR / "config.json"
VIDEOS_DIR = DATA_DIR / "videos"
VIDEOS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except:
        return {}

def save_json(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def get_config() -> dict:
    return load_json(CONFIG_DB)

def save_config(config: dict):
    save_json(CONFIG_DB, config)

def get_user(user_id: int) -> dict:
    users = load_json(USERS_DB)
    return users.get(str(user_id), {"balance": 0.0, "approved_accounts": [], "pending_requests": [], "uid": str(user_id)})

def save_user(user_id: int, user_data: dict):
    users = load_json(USERS_DB)
    users[str(user_id)] = user_data
    save_json(USERS_DB, users)

def kb(*rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(b, callback_data=c) for b, c in row] for row in rows])

def owner_panel_markup():
    return kb(
        [("💰 سعر كل حساب", "set_price")],
        [("📋 الطلبات", "approval_requests")],
        [("📧 جميع الايميلات", "all_emails")],
        [("📹 قسم الفيديوهات", "videos_section")],
        [("🔙 القائمة الرئيسية", "main_menu")],
    )

@dataclass
class Session:
    step: str = ""
    email: str = ""
    password: str = ""
    totp: str = ""
    app_pass: str = ""

SESSIONS: Dict[int, Session] = {}

# MAIN MENU
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rows = [
        [("➕ إضافة حساب", "add_account")],
        [("💰 أموالي", "my_wallet")],
        [("📋 حساباتي", "my_accounts")],
        [("📺 تعليم", "tutorials")],
        [("🛒 سحب", "withdraw_store")],
    ]
    if user.id == OWNER_ID:
        rows.append([("⚙️ إعدادات المالك", "owner_panel")])

    text = "👋 مرحباً بك!\nاختر من القائمة أدناه:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb(*rows))
    else:
        await update.message.reply_text(text, reply_markup=kb(*rows))

# OWNER PANEL
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    (DATA_DIR / "videos").mkdir(parents=True, exist_ok=True)
    await query.edit_message_text(
        "⚙️ *لوحة تحكم المالك*\n\nاختر الإعداد الذي تريد تعديله:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=owner_panel_markup(),
    )

# ACCOUNT ADD FLOW
async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    SESSIONS[uid] = Session(step="email")
    await update.callback_query.edit_message_text(
        "📧 *الخطوة 1/4*: أرسل الإيميل:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("❌ إلغاء", "cancel")])
    )

async def add_account_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    SESSIONS.pop(uid, None)
    await update.callback_query.edit_message_text("❌ تم الإلغاء.", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))

async def add_account_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    session = SESSIONS.get(uid)
    if not session or not session.step:
        return

    if session.step == "email":
        if not re.match(r"[^@]+@[^@]+\.[^@]+", text):
            await update.message.reply_text("❌ إيميل غير صالح.")
            return
        session.email = text
        session.step = "password"
        await update.message.reply_text("🔑 *الخطوة 2/4*: أرسل كلمة المرور الأساسية:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("❌ إلغاء", "cancel")]))

    elif session.step == "password":
        session.password = text
        session.step = "totp"
        await update.message.reply_text("🔐 *الخطوة 3/4*: أرسل مفتاح المصادقة (Secret Key):", parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("❌ إلغاء", "cancel")]))

    elif session.step == "totp":
        try:
            # 🔥 هنا يتم توليد الرمز وعرضه مباشرة
            secret = text.replace(" ", "").upper()
            code = pyotp.TOTP(secret).now()
            session.totp = secret
            session.step = "app_pass"
            await update.message.reply_text(
                f"✅ مفتاح المصادقة صالح!\n\n🔢 *الكود الحالي:* `{code}`\n\n🗝 *الخطوة 4/4*: أرسل كلمة مرور التطبيق (App Password):",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb([("❌ إلغاء", "cancel")])
            )
        except:
            await update.message.reply_text("⚠️ مفتاح 2FA غير صالح.")

    elif session.step == "app_pass":
        session.app_pass = text
        # Save account logic here
        user_data = get_user(uid)
        user_data["approved_accounts"].append({
            "email": session.email,
            "password": session.password,
            "totp": session.totp,
            "app_pass": session.app_pass
        })
        save_user(uid, user_data)
        SESSIONS.pop(uid, None)
        await update.message.reply_text("✅ تم حفظ الحساب بنجاح!", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))

# ROUTER
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
        data = query.data or ""

        if data == "main_menu":
            await main_menu(update, context)
        elif data == "add_account":
            await add_account_start(update, context)
        elif data == "cancel":
            await add_account_cancel(update, context)
        elif data == "my_wallet":
            user = get_user(query.from_user.id)
            await query.edit_message_text(f"💰 *رصيدك الحالي:* ${user['balance']:.2f}", parse_mode=ParseMode.MARKDOWN)
        elif data == "my_accounts":
            user = get_user(query.from_user.id)
            accs = user.get("approved_accounts", [])
            if not accs:
                await query.edit_message_text("📭 لا توجد حسابات لديك بعد.")
            else:
                msg = "📋 *حساباتك:*\n"
                for a in accs:
                    msg += f"📧 `{a.get('email', '')}`\n"
                await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
        elif data == "tutorials":
            await query.edit_message_text("📺 *قسم التعليم*\nقيد التطوير...", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))
        elif data == "owner_panel":
            await owner_panel(update, context)
        elif data == "withdraw_store":
            await query.edit_message_text("🛒 *قسم السحب*\nقيد التطوير...", parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text("⚠️ خيار غير معروف.", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))
    except BadRequest:
        try:
            await query.answer("تعذر تنفيذ الطلب، أرسل /start وحاول مرة أخرى.", show_alert=True)
        except:
            pass

# TEXT INPUT
async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    await add_account_step(update, context)

# DEBUG COMMANDS
async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "🔎 *تشخيص البوت*\n\n"
        f"🆔 رقم حسابك: `{user_id}`\n"
        f"👑 رقم المالك المقروء: `{OWNER_ID}`\n"
        f"✅ أنت المالك: {'نعم' if user_id == OWNER_ID else 'لا'}\n\n"
        "إذا كان رقم المالك 0 أو مختلفاً، عدّل OWNER_TELEGRAM_ID في Railway.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 هذا الأمر للمالك فقط.")
        return
    await update.message.reply_text(
        "⚙️ *لوحة تحكم المالك*\n\nاختر الإعداد الذي تريد تعديله:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=owner_panel_markup(),
    )

# MAIN
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", main_menu))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("owner", owner_command))
    app.add_handler(CallbackQueryHandler(router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    app.run_polling()

if __name__ == "__main__":
    main()
