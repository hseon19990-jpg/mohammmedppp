"""
Advanced Telegram Account Manager Bot - Full Version (COMPLETELY FIXED)
- Fixed: Email in buttons replaced with index to avoid 64-byte limit
- Fixed: approval_step vs step mismatch for TOTP and App Pass
- All features working perfectly
- NEW: 24-hour delayed verification with email deduplication
- NEW: Direct verification for owner
- NEW: Proxy pool management
- NEW: Smart verification engine
"""

import asyncio
import html
import json
import logging
import os
import re
import shutil
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, List, Any, Union

import pyotp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from dotenv import load_dotenv

load_dotenv()

# ==================== NEW IMPORTS ====================
from config import BotConfig
from email_manager import EmailManager
from delayed_verifier import DelayedVerifier
from utils import get_user, save_user, format_app_password, format_totp_secret, load_json, save_json
from data_structure import USER_DATA_TEMPLATE, PENDING_ACCOUNT_TEMPLATE
from proxy_pool_manager import SmartProxyPool
from smart_verifier import SmartVerifier
from account_manager import AccountManager
from advanced_monitor import AdvancedMonitor
from delayed_monitor import DelayedMonitor
from verification_engine import VerificationEngine
from fingerprint_generator import FingerprintGenerator

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "0"))
PURCHASE_CHANNEL_1 = os.environ.get("PURCHASE_CHANNEL_1", "").strip()
PURCHASE_CHANNEL_2 = os.environ.get("PURCHASE_CHANNEL_2", "").strip()

configured_data_dir = os.environ.get("DATA_DIR", "").strip()
railway_volume_dir = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
default_data_dir = Path("/app/data")
if not os.access(default_data_dir.parent, os.W_OK):
    default_data_dir = Path(__file__).resolve().parent / "data"
DATA_DIR = Path(
    configured_data_dir or railway_volume_dir or default_data_dir
).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ==================== DATA MIGRATION ====================
def migrate_legacy_data():
    legacy_dirs = {
        Path("/railway/volume/data"),
        Path("/app/data"),
        Path.cwd() / "data",
        Path(__file__).resolve().parent / "data",
    }
    legacy_dirs.discard(DATA_DIR)

    for legacy_dir in legacy_dirs:
        if not legacy_dir.exists():
            continue

        for filename in ("users.json", "config.json"):
            source = legacy_dir / filename
            destination = DATA_DIR / filename
            if source.is_file() and not destination.exists():
                shutil.copy2(source, destination)
                logger.info("Migrated %s to persistent storage.", filename)

        source_videos = legacy_dir / "videos"
        destination_videos = DATA_DIR / "videos"
        if source_videos.is_dir():
            for source_video in source_videos.iterdir():
                destination_video = destination_videos / source_video.name
                if source_video.is_file() and not destination_video.exists():
                    destination_videos.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_video, destination_video)
                    logger.info("Migrated video %s to persistent storage.", source_video.name)


migrate_legacy_data()

# ==================== CONSTANTS ====================
USERS_DB = DATA_DIR / "users.json"
VIDEOS_DIR = DATA_DIR / "videos"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

# ==================== INIT NEW COMPONENTS ====================
proxy_pool = SmartProxyPool()
delayed_verifier = DelayedVerifier(proxy_pool)
smart_verifier = SmartVerifier(proxy_pool)
email_manager = EmailManager()
account_manager = AccountManager()
advanced_monitor = AdvancedMonitor()
delayed_monitor = DelayedMonitor()
fingerprint_generator = FingerprintGenerator()


# ==================== KEYBOARD HELPERS ====================
def kb_vertical(buttons: List[Union[tuple, list]]) -> InlineKeyboardMarkup:
    rows = []
    for button in buttons:
        if isinstance(button, tuple) and len(button) == 2:
            rows.append([InlineKeyboardButton(button[0], callback_data=button[1])])
        elif isinstance(button, list):
            rows.append([InlineKeyboardButton(btn[0], callback_data=btn[1]) for btn in button])
    return InlineKeyboardMarkup(rows)


def kb_single(button_text: str, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(button_text, callback_data=callback_data)]])


def tg_html_escape(value: Any) -> str:
    """Escape dynamic values before inserting them into Telegram HTML text."""
    return html.escape(str(value), quote=False)


# ==================== SESSION ====================
@dataclass
class Session:
    step: str = ""
    email: str = ""
    password: str = ""
    totp: str = ""
    app_pass: str = ""
    editing_email: str = ""
    has_password: bool = False
    has_totp: bool = False
    has_app_pass: bool = False

SESSIONS: Dict[int, Session] = {}
PENDING_PURCHASES: Dict[int, Dict] = {}
LEAVE_HOLD_SECONDS = 24 * 60 * 60


# ==================== PRICING CONFIG ====================
def get_tier_prices() -> dict:
    config = load_json(DATA_DIR / "config.json")
    return {
        "tier_1": float(config.get("tier_1_price", 0.10)),
        "tier_2": float(config.get("tier_2_price", 0.15)),
        "tier_3": float(config.get("tier_3_price", 0.20)),
    }


def calculate_account_price(totp_submitted: bool, app_pass_submitted: bool) -> float:
    prices = get_tier_prices()
    if app_pass_submitted and totp_submitted:
        return prices["tier_3"]
    elif totp_submitted:
        return prices["tier_2"]
    else:
        return prices["tier_1"]


# ==================== VALIDATION HELPERS ====================
def validate_totp_secret(secret: str) -> bool:
    cleaned = secret.replace(" ", "").upper()
    if len(cleaned) != 32:
        return False
    if not re.match(r'^[A-Z2-7]{32}$', cleaned):
        return False
    return True


def validate_app_password(password: str) -> bool:
    cleaned = password.replace(" ", "").upper()
    if len(cleaned) != 16:
        return False
    if not re.match(r'^[A-Z0-9]{16}$', cleaned):
        return False
    return True


# ==================== FORCED CHANNEL CHECK ====================
def normalize_forced_channel(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://t\.me/", "", value, flags=re.IGNORECASE)
    value = value.split("?", 1)[0].split("/", 1)[0].strip()
    if value and not value.startswith("@") and not value.lstrip("-").isdigit():
        value = f"@{value}"
    return value


def get_configured_purchase_channels() -> tuple[str, str]:
    config = load_json(DATA_DIR / "config.json")
    channel_1 = str(config.get("purchase_channel_1") or PURCHASE_CHANNEL_1).strip()
    channel_2 = str(config.get("purchase_channel_2") or PURCHASE_CHANNEL_2).strip()
    return channel_1, channel_2


def normalize_chat_id(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://t\.me/", "", value, flags=re.IGNORECASE)
    value = value.split("?", 1)[0].split("/", 1)[0].strip()
    if value and not value.startswith("@") and not value.lstrip("-").isdigit():
        value = f"@{value}"
    return value


def forced_channel_link(channel: str, configured_link: str = "") -> str:
    configured_link = configured_link.strip()
    if configured_link.startswith(("http://", "https://")):
        return configured_link
    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"
    return ""


def forced_channel_keyboard(channel: str, configured_link: str = "") -> InlineKeyboardMarkup:
    rows = []
    join_link = forced_channel_link(channel, configured_link)
    if join_link:
        rows.append([InlineKeyboardButton("📢 الانضمام إلى القناة", url=join_link)])
    rows.append([InlineKeyboardButton("✅ تحققت من الاشتراك", callback_data="check_forced_channel")])
    return InlineKeyboardMarkup(rows)


async def check_forced_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    config = load_json(DATA_DIR / "config.json")
    forced_channel = normalize_forced_channel(str(config.get("forced_channel", "")))
    if not forced_channel:
        return True
    user_id = update.effective_user.id
    if user_id == OWNER_ID:
        return True
    try:
        member = await context.bot.get_chat_member(forced_channel, user_id)
        if member.status in {"member", "administrator", "creator"}:
            return True
    except Exception as exc:
        logger.warning("Could not verify forced-channel membership for %s: %s", user_id, exc)
    text = f"📢 *يرجى الانضمام إلى القناة أولاً:*\n{forced_channel}\n\nبعد الانضمام اضغط على زر «تحققت من الاشتراك»."
    reply_markup = forced_channel_keyboard(forced_channel, str(config.get("forced_channel_link", "")))
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        except Exception:
            await update.callback_query.answer("لم يتم العثور على اشتراكك بعد. انضم إلى القناة ثم أعد المحاولة.", show_alert=True)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    return False


async def check_forced_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_forced_channel(update, context):
        await main_menu(update, context)


# ==================== MAIN MENU ====================
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    user = update.effective_user
    buttons = [
        ("➕ إضافة حساب", "add_account"),
        ("💰 أموالي", "my_wallet"),
        ("📋 حساباتي", "my_accounts"),
        ("📺 تعليم", "tutorials"),
        ("🛒 سحب", "withdraw_store"),
        ("🔗 الإحالة", "referral_menu"),
        ("✏️ تعديل حساباتي", "edit_my_accounts"),
    ]
    if user.id == OWNER_ID:
        buttons.append(("⚙️ إعدادات المالك", "owner_panel"))
    text = "👋 مرحباً بك!\nاختر من القائمة أدناه:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb_vertical(buttons))
    else:
        await update.message.reply_text(text, reply_markup=kb_vertical(buttons))


# ==================== MY ACCOUNTS ====================
async def my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    user_data = get_user(query.from_user.id)
    
    # NEW: Get accounts from new structure
    pending_accounts = list(user_data.get("pending_accounts", {}).values())
    approved = user_data.get("approved_accounts", [])
    rejected = user_data.get("rejected_accounts", [])
    
    # Also get legacy pending_requests if they exist
    legacy_pending = user_data.get("pending_requests", [])
    
    if not approved and not pending_accounts and not legacy_pending and not rejected:
        await query.edit_message_text("📭 لا توجد حسابات لديك.", reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    
    msg = "📋 *جميع حساباتي:*\n\n"
    if approved:
        msg += "✅ *مقبولة:*\n"
        for idx, acc in enumerate(approved, 1):
            leave_status = ""
            if acc.get("approved_with_leave", False) and not acc.get("leave_confirmed", False):
                leave_status = " ⏳ (معلق 24 ساعة)"
            elif acc.get("approved_with_leave", False) and acc.get("leave_confirmed", False):
                leave_status = " ✅ (تم التحويل)"
            msg += f"  {idx}. 📧 `{acc.get('email', '')}` ✅{leave_status}\n"
        msg += "\n"
    
    if pending_accounts:
        msg += "⏳ *منتظرة (تحقق 24 ساعة):*\n"
        for idx, acc in enumerate(pending_accounts, 1):
            status = acc.get("verification_status", "pending")
            status_icon = "⏳" if status == "pending" else "🔄" if status == "verifying" else "✅" if status == "verified" else "❌"
            msg += f"  {idx}. 📧 `{acc.get('email', '')}` {status_icon}\n"
        msg += "\n"
    
    if legacy_pending:
        msg += "⏳ *منتظرة (قديم):*\n"
        for idx, req in enumerate(legacy_pending, 1):
            msg += f"  {idx}. 📧 `{req.get('email', '')}` ⏳\n"
        msg += "\n"
    
    if rejected:
        msg += "❌ *مرفوضة:*\n"
        for idx, rej in enumerate(rejected, 1):
            reason = rej.get('reject_reason', 'غير معروف')
            reason_map = {"email": "إيميل خطأ", "password": "باسورد خطأ", "totp": "رمز مصادقة خطأ",
                          "app_pass": "كلمة مرور تطبيق خطأ", "custom": "سبب مخصص"}
            reason_text = reason_map.get(reason, reason)
            msg += f"  {idx}. 📧 `{rej.get('email', '')}` ❌ - {reason_text}\n"
        msg += "\n"
    
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


# ==================== EDIT MY ACCOUNTS ====================
async def edit_my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    user_data = get_user(query.from_user.id)
    
    # Check both new and legacy pending
    pending_accounts = list(user_data.get("pending_accounts", {}).values())
    legacy_pending = user_data.get("pending_requests", [])
    
    if not pending_accounts and not legacy_pending:
        await query.edit_message_text("📭 لا توجد حسابات جارية للتعديل.", reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    
    buttons = []
    
    # Add new pending accounts
    for idx, acc in enumerate(pending_accounts):
        buttons.append((f"✏️ {acc.get('email', '')}", f"edit_pending_new:{query.from_user.id}:{idx}"))
    
    # Add legacy pending accounts
    for idx, req in enumerate(legacy_pending):
        buttons.append((f"✏️ {req.get('email', '')} (قديم)", f"edit_pending:{query.from_user.id}:{idx}"))
    
    buttons.append(("🔙 القائمة الرئيسية", "main_menu"))
    await query.edit_message_text("✏️ *تعديل الحسابات الجارية*\nاختر الحساب لتعديله:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def edit_pending_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text("⚠️ هذا الحساب غير موجود أو تمت معالجته.", reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))
        return
    request = pending[index]
    email = request.get("email", "")
    context.user_data["editing_email"] = email
    context.user_data["editing_index"] = index
    context.user_data["editing_type"] = "legacy"
    buttons = [
        ("🔑 تغيير الباسورد", f"edit_field:password:{uid}:{index}"),
        ("🔐 تغيير رمز المصادقة", f"edit_field:totp:{uid}:{index}"),
        ("🗝️ تغيير كلمة مرور التطبيق", f"edit_field:app_pass:{uid}:{index}"),
        ("🗑️ مسح الحساب", f"delete_pending:{uid}:{index}"),
        ("🔙 تعديل حساباتي", "edit_my_accounts")
    ]
    await query.edit_message_text(f"✏️ *تعديل الحساب:* `{email}`\n\nاختر ما تريد تعديله:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def edit_pending_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit new pending account from pending_accounts dict"""
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(uid)
    pending_accounts = list(user_data.get("pending_accounts", {}).values())
    
    if index >= len(pending_accounts):
        await query.edit_message_text("⚠️ هذا الحساب غير موجود أو تمت معالجته.", reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))
        return
    
    account = pending_accounts[index]
    email = account.get("email", "")
    context.user_data["editing_email"] = email
    context.user_data["editing_index"] = index
    context.user_data["editing_type"] = "new"
    
    buttons = [
        ("🔑 تغيير الباسورد", f"edit_field_new:password:{uid}:{index}"),
        ("🔐 تغيير رمز المصادقة", f"edit_field_new:totp:{uid}:{index}"),
        ("🗝️ تغيير كلمة مرور التطبيق", f"edit_field_new:app_pass:{uid}:{index}"),
        ("🗑️ مسح الحساب", f"delete_pending_new:{uid}:{index}"),
        ("🔙 تعديل حساباتي", "edit_my_accounts")
    ]
    await query.edit_message_text(f"✏️ *تعديل الحساب:* `{email}`\n\nاختر ما تريد تعديله:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    parts = query.data.split(":")
    field = parts[1]
    uid = int(parts[2])
    index = int(parts[3])
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text("⚠️ الحساب غير موجود.", reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))
        return
    email = pending[index].get("email", "")
    context.user_data["editing_field"] = field
    context.user_data["editing_uid"] = uid
    context.user_data["editing_index"] = index
    context.user_data["editing_type"] = "legacy"
    field_names = {"password": "كلمة المرور", "totp": "رمز المصادقة الثنائية", "app_pass": "كلمة مرور التطبيق"}
    await query.edit_message_text(f"✏️ *تعديل {field_names.get(field, field)}*\nللحساب: `{email}`\n\nأرسل القيمة الجديدة:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", f"edit_pending:{uid}:{index}"))
    context.user_data["step"] = "editing_field"


async def edit_field_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit field in new pending account"""
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    parts = query.data.split(":")
    field = parts[1]
    uid = int(parts[2])
    index = int(parts[3])
    user_data = get_user(uid)
    pending_accounts = list(user_data.get("pending_accounts", {}).values())
    
    if index >= len(pending_accounts):
        await query.edit_message_text("⚠️ الحساب غير موجود.", reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))
        return
    
    account = pending_accounts[index]
    email = account.get("email", "")
    context.user_data["editing_field"] = field
    context.user_data["editing_uid"] = uid
    context.user_data["editing_index"] = index
    context.user_data["editing_type"] = "new"
    field_names = {"password": "كلمة المرور", "totp": "رمز المصادقة الثنائية", "app_pass": "كلمة مرور التطبيق"}
    await query.edit_message_text(f"✏️ *تعديل {field_names.get(field, field)}*\nللحساب: `{email}`\n\nأرسل القيمة الجديدة:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", f"edit_pending_new:{uid}:{index}"))
    context.user_data["step"] = "editing_field"


async def delete_pending_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text("⚠️ الحساب غير موجود.", reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))
        return
    request = pending[index]
    pending.pop(index)
    user_data["pending_requests"] = pending
    user_data["pending_balance"] = max(0.0, float(user_data.get("pending_balance", 0.0)) - float(request.get("amount", 0.0)))
    save_user(uid, user_data)
    await query.edit_message_text(f"✅ تم مسح الحساب `{request.get('email', '')}` بنجاح.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))


async def delete_pending_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete new pending account"""
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(uid)
    pending_accounts = user_data.get("pending_accounts", {})
    pending_list = list(pending_accounts.items())
    
    if index >= len(pending_list):
        await query.edit_message_text("⚠️ الحساب غير موجود.", reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))
        return
    
    email, account = pending_list[index]
    del pending_accounts[email]
    user_data["pending_accounts"] = pending_accounts
    user_data["pending_balance"] = max(0.0, float(user_data.get("pending_balance", 0.0)) - float(account.get("amount", 0.0)))
    save_user(uid, user_data)
    await query.edit_message_text(f"✅ تم مسح الحساب `{email}` بنجاح.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))


async def handle_edit_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    field = context.user_data.get("editing_field")
    editing_uid = context.user_data.get("editing_uid")
    index = context.user_data.get("editing_index")
    editing_type = context.user_data.get("editing_type", "legacy")
    
    if not field or editing_uid is None or index is None:
        await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        return
    
    user_data = get_user(editing_uid)
    
    if editing_type == "new":
        # Edit in new pending_accounts
        pending_accounts = user_data.get("pending_accounts", {})
        pending_list = list(pending_accounts.items())
        if index >= len(pending_list):
            await update.message.reply_text("⚠️ الحساب غير موجود.")
            return
        email, account = pending_list[index]
        account[field] = text
        account["has_totp"] = bool(account.get("totp_secret"))
        account["has_app_pass"] = bool(account.get("app_password"))
        account["amount"] = calculate_account_price(account.get("has_totp", False), account.get("has_app_pass", False))
        pending_accounts[email] = account
        user_data["pending_accounts"] = pending_accounts
    else:
        # Edit in legacy pending_requests
        pending = user_data.get("pending_requests", [])
        if index >= len(pending):
            await update.message.reply_text("⚠️ الحساب غير موجود.")
            return
        pending[index][field] = text
        user_data["pending_requests"] = pending
    
    save_user(editing_uid, user_data)
    try:
        await update.message.delete()
    except:
        pass
    
    context.user_data.pop("editing_field", None)
    context.user_data.pop("editing_uid", None)
    context.user_data.pop("editing_index", None)
    context.user_data.pop("editing_type", None)
    context.user_data.pop("step", None)
    
    await update.message.reply_text(f"✅ تم تحديث {field} بنجاح للحساب `{email}`.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))


# ==================== ADD ACCOUNT FLOW ====================
async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    uid = update.effective_user.id
    SESSIONS[uid] = Session(step="email")
    config = load_json(DATA_DIR / "config.json")
    prices = get_tier_prices()
    has_email_video = config.get("video_email") and Path(config.get("video_email", "")).exists()
    buttons = []
    if has_email_video:
        buttons.append(("📹 طريقة إنشاء حساب", "show_video:email"))
    buttons.append(("❌ إلغاء", "cancel"))
    await update.callback_query.edit_message_text(
        f"📝 *إضافة حساب جديد*\n\n💵 *نظام المكافآت المتدرج:*\n• إيميل + باسورد فقط → ${prices['tier_1']:.2f}\n• إيميل + باسورد + رمز مصادقة → ${prices['tier_2']:.2f}\n• إيميل + باسورد + رمز مصادقة + كلمة مرور تطبيق → ${prices['tier_3']:.2f}\n\n📧 *الخطوة 1/4*: أرسل الإيميل:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def show_video_in_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    vtype = query.data.split(":")[1]
    config = load_json(DATA_DIR / "config.json")
    path = config.get(f"video_{vtype}")
    if path and Path(path).exists():
        try:
            await context.bot.send_video(chat_id=query.from_user.id, video=open(path, "rb"),
                                         caption="📹 *فيديو تعليمي*\nشاهد الفيديو لمعرفة الطريقة الصحيحة.",
                                         parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            await add_account_start(update, context)
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await query.edit_message_text("⚠️ حدث خطأ في تشغيل الفيديو.", reply_markup=kb_single("🔙 العودة", "add_account"))
    else:
        await query.edit_message_text("⚠️ الفيديو غير متوفر حالياً.", reply_markup=kb_single("🔙 العودة", "add_account"))


async def add_account_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    SESSIONS.pop(uid, None)
    await update.callback_query.edit_message_text("❌ تم الإلغاء.", reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


async def add_account_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    session = SESSIONS.get(uid)
    if not session or not session.step:
        return
    if context.user_data.get("step") == "editing_field":
        await handle_edit_field_input(update, context)
        return
    config = load_json(DATA_DIR / "config.json")
    prices = get_tier_prices()
    
    if session.step == "email":
        if not re.match(r"[^@]+@[^@]+\.[^@]+", text):
            await update.message.reply_text("❌ إيميل غير صالح.")
            return
        
        # NEW: Check for duplicate email using EmailManager
        can_add, message = email_manager.can_update_account(uid, text)
        if not can_add:
            await update.message.reply_text(
                f"🚫 *لا يمكن إضافة هذا الإيميل!*\n\n"
                f"📧 الإيميل: `{text}`\n"
                f"📝 السبب: {message}\n\n"
                f"_إذا كنت تريد تعديل بيانات الإيميل، استخدم خيار التعديل._",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu")
            )
            SESSIONS.pop(uid, None)
            return
        
        # Also check legacy pending_requests
        user_data = get_user(uid)
        for req in user_data.get("pending_requests", []):
            if req.get("email") == text:
                await update.message.reply_text("⏳ هذا الإيميل قيد الانتظار بالفعل! انتظر موافقة المالك.",
                                                reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
                return
        
        session.email = text
        session.step = "password"
        has_password_video = config.get("video_password") and Path(config.get("video_password", "")).exists()
        buttons = []
        if has_password_video:
            buttons.append(("📹 طريقة تغيير الباسورد", "show_video:password"))
        buttons.append(("❌ إلغاء", "cancel"))
        await update.message.reply_text(
            f"🔑 *الخطوة 2/4*: أرسل كلمة المرور الأساسية:\n\n💰 *السعر الحالي:* ${prices['tier_1']:.2f} (إيميل + باسورد)",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))
    
    elif session.step == "password":
        session.password = text
        session.has_password = True
        session.step = "totp"
        try:
            await update.message.delete()
        except:
            pass
        has_totp_video = config.get("video_totp") and Path(config.get("video_totp", "")).exists()
        buttons = [
            ("✅ استلم $0.10 (باسورد فقط)", f"submit_tier_1:{uid}")
        ]
        if has_totp_video:
            buttons.append(("📹 طريقة العثور على رمز المصادقة", "show_video:totp"))
        buttons.append(("❌ إلغاء", "cancel"))
        await update.message.reply_text(
            f"🔐 *الخطوة 3/4*: أرسل مفتاح المصادقة (Secret Key):\n\n💰 *السعر الحالي:* ${prices['tier_1']:.2f} (إيميل + باسورد)\n💰 *السعر مع رمز المصادقة:* ${prices['tier_2']:.2f}\n\n📌 *يمكنك استلام {prices['tier_1']:.2f}$ الآن وإكمال الباقي لاحقاً*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))
    
    elif session.step == "totp":
        try:
            cleaned = text.replace(" ", "").upper()
            if len(cleaned) != 32:
                await update.message.reply_text("⚠️ مفتاح المصادقة يجب أن يكون 32 حرفاً (مثل: XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX)")
                return
            if not re.match(r'^[A-Z2-7]{32}$', cleaned):
                await update.message.reply_text("⚠️ مفتاح المصادقة يحتوي على أحرف غير صالحة. يجب أن يكون Base32 فقط (A-Z و 2-7).")
                return

            secret = cleaned
            code = pyotp.TOTP(secret).now()
            session.totp = secret
            session.has_totp = True
            session.step = "app_pass"
            try:
                await update.message.delete()
            except:
                pass
            has_app_pass_video = config.get("video_app_pass") and Path(config.get("video_app_pass", "")).exists()
            buttons = [
                ("✅ استلم $0.15 (مع رمز المصادقة)", f"submit_tier_2:{uid}")
            ]
            if has_app_pass_video:
                buttons.append(("📹 طريقة الحصول على كلمة مرور التطبيق", "show_video:app_pass"))
            buttons.append(("❌ إلغاء", "cancel"))
            await update.message.reply_text(
                f"✅ مفتاح المصادقة صالح!\n\n🔢 *الكود الحالي:* `{code}`\n\n🗝 *الخطوة 4/4*: أرسل كلمة مرور التطبيق (16 حرف):\n📌 الصيغة: XXXX XXXX XXXX XXXX\n\n💰 *السعر الحالي:* ${prices['tier_2']:.2f} (مع رمز المصادقة)\n💰 *السعر الكامل:* ${prices['tier_3']:.2f} (مع كلمة مرور التطبيق)\n\n📌 *يمكنك استلام {prices['tier_2']:.2f}$ الآن وإكمال الباقي لاحقاً*",
                parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))
        except Exception as e:
            await update.message.reply_text(f"⚠️ مفتاح 2FA غير صالح: {str(e)}")
    
    elif session.step == "app_pass":
        cleaned = text.replace(" ", "").upper()
        if len(cleaned) != 16:
            await update.message.reply_text("⚠️ كلمة مرور التطبيق يجب أن تكون 16 حرفاً (مثل: XXXX XXXX XXXX XXXX)")
            return
        if not re.match(r'^[A-Z0-9]{16}$', cleaned):
            await update.message.reply_text("⚠️ كلمة مرور التطبيق تحتوي على أحرف غير صالحة. استخدم أحرف وأرقام فقط.")
            return

        user_data = get_user(uid)
        used_passwords = user_data.get("used_app_passwords", [])
        if cleaned in used_passwords:
            config = load_json(DATA_DIR / "config.json")
            video_path = config.get("video_app_pass")
            msg = "⚠️ *كلمة المرور هذه مستخدمة مسبقاً!*\n\nيرجى تغيير كلمة المرور وإرسال كلمة جديدة.\n\n📌 الصيغة: XXXX XXXX XXXX XXXX"
            if video_path and Path(video_path).exists():
                try:
                    await context.bot.send_video(
                        chat_id=uid,
                        video=open(video_path, "rb"),
                        caption=msg,
                        parse_mode=ParseMode.MARKDOWN,
                        supports_streaming=True
                    )
                except:
                    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            return

        session.app_pass = cleaned
        session.has_app_pass = True
        try:
            await update.message.delete()
        except:
            pass

        user = update.effective_user
        user_full_name = user.full_name or "غير معروف"
        user_username = user.username or "لا يوجد"
        final_price = calculate_account_price(session.has_totp, session.has_app_pass)

        used_passwords.append(cleaned)
        user_data["used_app_passwords"] = used_passwords

        # Check if email exists in pending_accounts (for update)
        is_used, existing_account = email_manager.is_email_used(uid, session.email)
        
        if is_used and existing_account and existing_account.get("verification_status") == "pending":
            # Update existing pending account
            new_data = {
                "password": session.password,
                "totp_secret": session.totp if session.has_totp else None,
                "app_password": session.app_pass if session.has_app_pass else None
            }
            email_manager.update_account_data(uid, session.email, new_data)
            
            # Update amount in pending_balance
            old_amount = existing_account.get("amount", 0)
            user_data["pending_balance"] = float(user_data.get("pending_balance", 0.0)) - old_amount + final_price
            
            await update.message.reply_text(
                f"✅ *تم تحديث بيانات الإيميل!*\n\n"
                f"📧 الإيميل: `{session.email}`\n"
                f"💰 السعر الجديد: *${final_price:.2f}*\n"
                f"🔄 تم تحديث البيانات بنجاح.\n"
                f"⏳ سيتم إعادة التحقق بعد 24 ساعة.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu")
            )
            SESSIONS.pop(uid, None)
            return

        # NEW: Add to pending_accounts using EmailManager
        account_data = {
            "email": session.email,
            "password": session.password,
            "totp_secret": session.totp if session.has_totp else None,
            "app_password": session.app_pass if session.has_app_pass else None,
            "amount": final_price,
            "has_totp": session.has_totp,
            "has_app_pass": session.has_app_pass,
            "user_name": user_full_name,
            "user_username": user_username,
            "section": "full" if (session.has_totp and session.has_app_pass) else "totp_only" if session.has_totp else "email_only"
        }
        
        email_manager.add_pending_account(uid, account_data)
        
        # Schedule verification after 24 hours
        release_time = await schedule_24h_verification(context, uid, session.email, account_data)
        
        save_user(uid, user_data)
        SESSIONS.pop(uid, None)

        referred_by = user_data.get("referred_by")
        if referred_by:
            try:
                await context.bot.send_message(chat_id=referred_by,
                                               text=f"📢 *إشعار إحالة*\n\nالمستخدم `{uid}` أضاف إيميل `{session.email}` وهو قيد الانتظار.\nستحصل على مكافأة عند قبول الإيميل.",
                                               parse_mode=ParseMode.MARKDOWN)
            except:
                pass

        await send_leave_video_to_user(context, uid, session.email)

        tier_text = ""
        if session.has_app_pass and session.has_totp:
            tier_text = "📦 *مكتمل (كامل المعلومات)*"
        elif session.has_totp:
            tier_text = "📦 *ناقص كلمة مرور التطبيق*"
        else:
            tier_text = "📦 *ناقص رمز المصادقة وكلمة مرور التطبيق*"

        # Format release time
        if release_time:
            hours = 24
            release_str = release_time.strftime('%Y-%m-%d %H:%M')
        else:
            hours = 24
            release_str = "بعد 24 ساعة"

        await update.message.reply_text(
            f"✅ *تم استلام الحساب بنجاح!*\n\n"
            f"{tier_text}\n"
            f"📧 الإيميل: `{session.email}`\n"
            f"💰 المبلغ: *${final_price:.2f}* (معلق)\n"
            f"⏳ سيتم التحقق خلال {hours} ساعة\n"
            f"📅 وقت الإصدار: {release_str}\n\n"
            f"📹 تم إرسال فيديو المغادرة إليك.\n"
            f"⚠️ قم بمغادرة الحساب لتجنب تأخير الدفعة.\n\n"
            f"_🔄 سيتم تحويل المبلغ إلى رصيدك بعد التحقق من صحة الحساب._",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


async def schedule_24h_verification(context: ContextTypes.DEFAULT_TYPE, user_id: int, email: str, account_data: dict):
    """Schedule 24-hour verification for an account"""
    # Calculate release time (24 hours from now)
    release_time = datetime.now(timezone.utc) + timedelta(hours=24)
    
    # Schedule job
    context.job_queue.run_once(
        callback=check_account_after_24h,
        when=86400,  # 24 hours in seconds
        data={
            "user_id": user_id,
            "email": email
        },
        name=f"24h_verify_{user_id}_{email}"
    )
    
    # Record in monitor
    delayed_monitor.record_scheduled(account_data.get("amount", 0.0))
    
    logger.info(f"Scheduled 24h verification for {email} at {release_time}")
    return release_time


async def check_account_after_24h(context: ContextTypes.DEFAULT_TYPE):
    """Called after 24 hours to verify the account"""
    job_data = context.job.data
    user_id = job_data["user_id"]
    email = job_data["email"]
    
    logger.info(f"Starting 24h verification for user {user_id}, email {email}")
    
    # Get user data
    user_data = get_user(user_id)
    
    # Find account in pending_accounts
    pending_accounts = user_data.get("pending_accounts", {})
    account = pending_accounts.get(email)
    
    if not account:
        logger.warning(f"Account {email} not found in pending list for user {user_id}")
        return
    
    # Perform verification
    start_time = time.time()
    result = await delayed_verifier.verify_after_24h(user_id, account)
    duration = time.time() - start_time
    
    # Record in monitor
    delayed_monitor.record_result(result, account.get("amount", 0.0), duration)
    
    # Process result
    if result["status"] == "verified":
        # ✅ Account is valid - transfer money
        amount = account.get("amount", 0.0)
        
        # Move from pending to balance
        user_data["balance"] = float(user_data.get("balance", 0.0)) + amount
        user_data["pending_balance"] = float(user_data.get("pending_balance", 0.0)) - amount
        
        # Move to approved accounts
        account["verified_at"] = datetime.now(timezone.utc).isoformat()
        account["verification_status"] = "verified"
        account["verification_result"] = result
        
        # Remove from pending
        del pending_accounts[email]
        user_data["pending_accounts"] = pending_accounts
        
        # Add to approved
        if "approved_accounts" not in user_data:
            user_data["approved_accounts"] = []
        user_data["approved_accounts"].append(account)
        
        save_user(user_id, user_data)
        
        # Record in account manager
        account_manager.move_to_ready(account, result)
        
        # Notify user
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ *تم التحقق من الحساب بنجاح!*\n\n"
                 f"📧 الإيميل: `{email}`\n"
                 f"💰 تم تحويل *${amount:.2f}* إلى رصيدك.\n"
                 f"💰 الرصيد الحالي: *${user_data['balance']:.2f}*\n\n"
                 f"_شكراً لاستخدامك البوت! 🎉_",
            parse_mode=ParseMode.MARKDOWN
        )
        
    else:
        # ❌ Verification failed - cancel money
        amount = account.get("amount", 0.0)
        
        # Remove from pending balance
        user_data["pending_balance"] = float(user_data.get("pending_balance", 0.0)) - amount
        
        # Move to rejected
        account["rejected_at"] = datetime.now(timezone.utc).isoformat()
        account["reject_reason"] = result.get("reason", "unknown")
        account["reject_message"] = result.get("message", "فشل التحقق")
        
        # Remove from pending
        del pending_accounts[email]
        user_data["pending_accounts"] = pending_accounts
        
        # Add to rejected
        if "rejected_accounts" not in user_data:
            user_data["rejected_accounts"] = []
        user_data["rejected_accounts"].append(account)
        
        save_user(user_id, user_data)
        
        # Notify user
        reason_map = {
            "account_banned": "الحساب محظور من Telegram",
            "phone_required": "يطلب رقم هاتف للتحقق",
            "password_incorrect": "كلمة المرور غير صحيحة حالياً",
            "totp_invalid": "رمز TOTP غير صحيح",
            "app_password_invalid": "كلمة مرور التطبيق غير صحيحة",
            "account_not_found": "الحساب غير موجود",
            "login_failed": "فشل تسجيل الدخول",
            "email_invalid": "الإيميل غير صحيح",
            "technical_error": "خطأ تقني"
        }
        
        reason_text = reason_map.get(result.get("reason", ""), result.get("message", "سبب غير معروف"))
        
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ *فشل التحقق من الحساب!*\n\n"
                 f"📧 الإيميل: `{email}`\n"
                 f"💰 لم يتم تحويل *${amount:.2f}*\n"
                 f"📝 السبب: {reason_text}\n\n"
                 f"_يرجى التأكد من صحة البيانات وإعادة المحاولة._",
            parse_mode=ParseMode.MARKDOWN
        )


async def submit_tier_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = int(query.data.split(":")[1])
    session = SESSIONS.get(uid)
    if not session:
        await query.answer("⚠️ الجلسة منتهية، حاول مرة أخرى.", show_alert=True)
        return
    if not session.email or not session.password:
        await query.answer("⚠️ يرجى إكمال الإيميل والباسورد أولاً.", show_alert=True)
        return
    user_data = get_user(uid)
    prices = get_tier_prices()
    price = prices["tier_1"]
    
    # Check for duplicate using EmailManager
    can_add, message = email_manager.can_update_account(uid, session.email)
    if not can_add:
        await query.edit_message_text(
            f"🚫 *لا يمكن إضافة هذا الإيميل!*\n\n"
            f"📧 الإيميل: `{session.email}`\n"
            f"📝 السبب: {message}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu")
        )
        return
    
    user = update.effective_user
    user_full_name = user.full_name or "غير معروف"
    user_username = user.username or "لا يوجد"
    
    # Add to pending_accounts
    account_data = {
        "email": session.email,
        "password": session.password,
        "totp_secret": None,
        "app_password": None,
        "amount": price,
        "has_totp": False,
        "has_app_pass": False,
        "user_name": user_full_name,
        "user_username": user_username,
        "section": "email_only"
    }
    
    email_manager.add_pending_account(uid, account_data)
    
    # Schedule 24h verification
    await schedule_24h_verification(context, uid, session.email, account_data)
    
    SESSIONS.pop(uid, None)
    await query.edit_message_text(
        f"✅ *تم استلام الطلب!*\n\n"
        f"📦 *المستوى 1: إيميل + باسورد فقط*\n"
        f"💰 المبلغ: *${price:.2f}* (معلق)\n"
        f"⏳ سيتم التحقق خلال 24 ساعة\n\n"
        f"_🔄 سيتم تحويل المبلغ إلى رصيدك بعد التحقق من صحة الحساب._",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


async def submit_tier_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = int(query.data.split(":")[1])
    session = SESSIONS.get(uid)
    if not session:
        await query.answer("⚠️ الجلسة منتهية، حاول مرة أخرى.", show_alert=True)
        return
    if not session.email or not session.password or not session.totp:
        await query.answer("⚠️ يرجى إكمال الإيميل والباسورد ورمز المصادقة أولاً.", show_alert=True)
        return
    user_data = get_user(uid)
    prices = get_tier_prices()
    price = prices["tier_2"]
    
    # Check for duplicate using EmailManager
    can_add, message = email_manager.can_update_account(uid, session.email)
    if not can_add:
        await query.edit_message_text(
            f"🚫 *لا يمكن إضافة هذا الإيميل!*\n\n"
            f"📧 الإيميل: `{session.email}`\n"
            f"📝 السبب: {message}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu")
        )
        return
    
    user = update.effective_user
    user_full_name = user.full_name or "غير معروف"
    user_username = user.username or "لا يوجد"
    
    # Add to pending_accounts
    account_data = {
        "email": session.email,
        "password": session.password,
        "totp_secret": session.totp,
        "app_password": None,
        "amount": price,
        "has_totp": True,
        "has_app_pass": False,
        "user_name": user_full_name,
        "user_username": user_username,
        "section": "totp_only"
    }
    
    email_manager.add_pending_account(uid, account_data)
    
    # Schedule 24h verification
    await schedule_24h_verification(context, uid, session.email, account_data)
    
    SESSIONS.pop(uid, None)
    await query.edit_message_text(
        f"✅ *تم استلام الطلب!*\n\n"
        f"📦 *المستوى 2: إيميل + باسورد + رمز مصادقة*\n"
        f"💰 المبلغ: *${price:.2f}* (معلق)\n"
        f"⏳ سيتم التحقق خلال 24 ساعة\n\n"
        f"_🔄 سيتم تحويل المبلغ إلى رصيدك بعد التحقق من صحة الحساب._",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


# ==================== LEAVE VIDEO SYSTEM ====================
async def send_leave_video_to_user(context: ContextTypes.DEFAULT_TYPE, user_id: int, email: str):
    config = load_json(DATA_DIR / "config.json")
    video_path = config.get("video_leave")
    text = f"📹 *فيديو المغادرة*\n\n📧 الإيميل: `{email}`\n\n⚠️ *تعليمات مهمة:*\n• قم بمغادرة الحساب الآن.\n• إذا لم تقم بمغادرة الحساب،\n• سيتم تأخير دفع المبلغ المستحق لك لمدة 24 ساعة.\n\n_شاهد الفيديو لمعرفة طريقة المغادرة الصحيحة_"
    if video_path and Path(video_path).exists():
        try:
            await context.bot.send_video(chat_id=user_id, video=open(video_path, "rb"), caption=text,
                                         parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            return True
        except Exception as e:
            logger.error(f"Error sending leave video to user {user_id}: {e}")
            try:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.MARKDOWN)
            except:
                pass
            return False
    else:
        try:
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.MARKDOWN)
        except:
            pass
        return False


async def send_leave_video_reminder(context: ContextTypes.DEFAULT_TYPE, user_id: int, email: str):
    text = f"⏰ *تذكير بمغادرة الحساب*\n\n📧 الإيميل: `{email}`\n\n⚠️ *لم تقم بمغادرة الحساب بعد!*\n\n📌 سيتم إضافة المبلغ إلى رصيدك خلال 24 ساعة أخرى\nبغض النظر عن مغادرة الحساب.\n\n_يمكنك مشاهدة فيديو المغادرة لتتعلم الطريقة الصحيحة_"
    config = load_json(DATA_DIR / "config.json")
    video_path = config.get("video_leave")
    if video_path and Path(video_path).exists():
        try:
            await context.bot.send_video(chat_id=user_id, video=open(video_path, "rb"), caption=text,
                                         parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
        except Exception as e:
            logger.error(f"Error sending leave video reminder to user {user_id}: {e}")
            try:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.MARKDOWN)
            except:
                pass
    else:
        try:
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.MARKDOWN)
        except:
            pass


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def schedule_leave_check(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    email: str,
    release_at: Optional[str] = None,
):
    job_name = f"leave_check_{user_id}_{email}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()
    release_time = parse_iso_datetime(release_at)
    if release_time is None:
        release_time = datetime.now(timezone.utc) + timedelta(seconds=LEAVE_HOLD_SECONDS)
        release_at = release_time.isoformat()
    delay = max(0, (release_time - datetime.now(timezone.utc)).total_seconds())
    context.job_queue.run_once(
        callback=check_leave_status,
        when=delay,
        data={"user_id": user_id, "email": email, "release_at": release_at},
        name=job_name,
    )
    logger.info("Scheduled auto-transfer for user %s, email %s in %.0f seconds", user_id, email, delay)


async def check_leave_status(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    user_id = job_data["user_id"]
    email = job_data["email"]
    release_at = parse_iso_datetime(job_data.get("release_at"))
    if release_at and release_at > datetime.now(timezone.utc):
        await schedule_leave_check(context, user_id, email, release_at.isoformat())
        return
    user_data = get_user(user_id)
    accounts = user_data.get("approved_accounts", [])
    account = next((acc for acc in accounts if acc.get("email") == email), None)
    if not account:
        logger.info(f"Account {email} not found for user {user_id}")
        return
    if account.get("leave_confirmed", False):
        logger.info(f"Leave already confirmed for {email}")
        return
    price = float(account.get("amount", 0.0))
    user_data["hold_balance"] = round(
        max(0.0, float(user_data.get("hold_balance", 0.0)) - price),
        2,
    )
    user_data["balance"] = round(float(user_data.get("balance", 0.0)) + price, 2)
    account["leave_confirmed"] = True
    account["auto_confirmed"] = True
    account["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    account["released_amount"] = price
    save_user(user_id, user_data)
    try:
        await context.bot.send_message(chat_id=user_id,
                                       text=f"✅ *تم إضافة المبلغ إلى رصيدك تلقائياً!*\n\n📧 الإيميل: `{email}`\n💰 تم إضافة *${price:.2f}* إلى رصيدك.\n\n🕐 *ملاحظة:* تم التحويل تلقائياً بعد 24 ساعة من موافقة المالك.\n\n_شكراً لاستخدامك البوت 🤖_",
                                       parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Could not send auto-confirmation to user {user_id}: {e}")
    logger.info(f"Auto-confirmed leave for {email}, user {user_id}, amount ${price:.2f}")


async def restore_leave_checks(application: Application):
    """Restore pending 24-hour transfers after a bot restart."""
    users = load_json(USERS_DB)
    changed = False
    now = datetime.now(timezone.utc)

    for user_id, user_data in users.items():
        for account in user_data.get("approved_accounts", []):
            if not account.get("approved_with_leave", False):
                continue
            if account.get("leave_confirmed", False):
                continue

            release_at = parse_iso_datetime(account.get("release_at"))
            if release_at is None:
                approval_time = parse_iso_datetime(account.get("approval_time"))
                if approval_time is None:
                    logger.warning(
                        "Cannot restore delayed transfer for user %s, email %s: missing approval time",
                        user_id,
                        account.get("email", ""),
                    )
                    continue
                release_at = approval_time + timedelta(seconds=LEAVE_HOLD_SECONDS)
                account["release_at"] = release_at.isoformat()
                changed = True

            email = account.get("email", "")
            job_name = f"leave_check_{user_id}_{email}"
            for job in application.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
            application.job_queue.run_once(
                callback=check_leave_status,
                when=max(0, (release_at - now).total_seconds()),
                data={
                    "user_id": int(user_id),
                    "email": email,
                    "release_at": release_at.isoformat(),
                },
                name=job_name,
            )

    if changed:
        save_json(USERS_DB, users)


def create_owner_stats_chart(path: Path, password_only: int, extra_sections: int) -> bool:
    """Create a small aggregate chart without exposing user data."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.exception("Pillow is required to create the owner statistics chart.")
        return False

    width, height = 1000, 620
    image = Image.new("RGB", (width, height), "#101827")
    draw = ImageDraw.Draw(image)

    font_paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    )
    font_path = next((candidate for candidate in font_paths if Path(candidate).exists()), None)
    if font_path:
        title_font = ImageFont.truetype(font_path, 34)
        label_font = ImageFont.truetype(font_path, 24)
        value_font = ImageFont.truetype(font_path, 30)
    else:
        title_font = label_font = value_font = ImageFont.load_default()

    draw.text((60, 42), "Accepted email breakdown", fill="#F8FAFC", font=title_font)
    draw.text((60, 92), "Password only vs. extra sections", fill="#94A3B8", font=label_font)

    values = [max(0, password_only), max(0, extra_sections)]
    labels = ["Email + password only", "Extra sections"]
    colors = ["#38BDF8", "#A78BFA"]
    max_value = max(max(values), 1)
    baseline = 500
    chart_top = 160
    chart_height = baseline - chart_top
    bar_width = 270
    bar_gap = 170
    start_x = 150

    draw.line((90, baseline, 910, baseline), fill="#475569", width=3)
    for index, (value, label, color) in enumerate(zip(values, labels, colors)):
        x = start_x + index * (bar_width + bar_gap)
        bar_height = int(chart_height * value / max_value)
        y = baseline - bar_height
        draw.rounded_rectangle(
            (x, y, x + bar_width, baseline),
            radius=18,
            fill=color,
        )
        value_box = draw.textbbox((0, 0), str(value), font=value_font)
        value_width = value_box[2] - value_box[0]
        draw.text(
            (x + (bar_width - value_width) / 2, max(y - 46, chart_top - 10)),
            str(value),
            fill="#F8FAFC",
            font=value_font,
        )
        label_box = draw.textbbox((0, 0), label, font=label_font)
        label_width = label_box[2] - label_box[0]
        draw.text(
            (x + (bar_width - label_width) / 2, baseline + 24),
            label,
            fill="#CBD5E1",
            font=label_font,
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return True


async def owner_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    await query.answer()
    users = load_json(USERS_DB)
    rows = []
    total_accepted = 0
    password_only = 0
    extra_sections = 0

    for user_id, user_data in users.items():
        approved_accounts = user_data.get("approved_accounts", [])
        user_password_only = 0
        user_extra_sections = 0
        for account in approved_accounts:
            if account.get("has_totp", False) or account.get("has_app_pass", False):
                user_extra_sections += 1
            else:
                user_password_only += 1

        total_accepted += len(approved_accounts)
        password_only += user_password_only
        extra_sections += user_extra_sections
        rows.append({
            "user_id": str(user_id),
            "points": round(float(user_data.get("balance", 0.0) or 0.0), 2),
            "accepted": len(approved_accounts),
        })

    top_users = sorted(
        rows,
        key=lambda row: (row["points"], row["accepted"]),
        reverse=True,
    )[:10]
    message = (
        "📊 <b>إحصائيات المستخدمين</b>\n\n"
        f"👥 عدد المستخدمين: <b>{len(rows)}</b>\n"
        f"📧 الإيميلات المقبولة: <b>{total_accepted}</b>\n"
        f"🔵 إيميل + باسورد فقط: <b>{password_only}</b>\n"
        f"🟣 أقسام إضافية: <b>{extra_sections}</b>\n\n"
        "🏆 <b>أكثر المستخدمين نقاطاً:</b>\n"
    )
    if top_users:
        for index, row in enumerate(top_users, 1):
            message += (
                f"{index}. المستخدم <code>{row['user_id']}</code> — "
                f"💰 {row['points']:.2f} نقطة — 📧 {row['accepted']} إيميل\n"
            )
    else:
        message += "لا توجد بيانات مستخدمين حتى الآن.\n"

    chart_path = DATA_DIR / "owner_stats_chart.png"
    if create_owner_stats_chart(chart_path, password_only, extra_sections):
        with chart_path.open("rb") as chart_file:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=chart_file,
                caption=message,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("🔙 إعدادات المالك", "owner_panel"),
            )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إعدادات المالك", "owner_panel"),
        )


async def check_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    context.user_data["step"] = "check_member_input"
    await query.edit_message_text(
        "🔎 <b>فحص عضو</b>\n\n"
        "أرسل معرف العضو الرقمي أو اليوزر مع @:\n"
        "مثال: <code>123456789</code>\n"
        "مثال: <code>@username</code>\n\n"
        "أو أرسل «إلغاء» للعودة.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"),
    )


async def handle_member_check_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    text = update.message.text.strip()
    if text.casefold() in {"إلغاء", "الغاء", "cancel"}:
        context.user_data.pop("step", None)
        await update.message.reply_text(
            "❌ تم إلغاء فحص العضو.",
            reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"),
        )
        return

    member_id = resolve_member_id(text)
    if member_id is None:
        await update.message.reply_text(
            "⚠️ لم يتم العثور على هذا العضو.\n"
            "أرسل المعرف الرقمي الصحيح أو اليوزر المسجل في البيانات.",
            reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"),
        )
        return

    users = load_json(USERS_DB)
    user_data = users.get(str(member_id), {})
    approved = user_data.get("approved_accounts", [])
    pending_accounts = list(user_data.get("pending_accounts", {}).values())
    pending_requests = user_data.get("pending_requests", [])
    rejected = user_data.get("rejected_accounts", [])
    
    submitted_accounts = approved + pending_accounts + pending_requests + rejected
    submitted_count = len(submitted_accounts)

    password_only = sum(
        1 for account in submitted_accounts
        if not account.get("has_totp", False) and not account.get("has_app_pass", False)
    )
    totp_only = sum(
        1 for account in submitted_accounts
        if account.get("has_totp", False) and not account.get("has_app_pass", False)
    )
    app_password = sum(
        1 for account in submitted_accounts
        if account.get("has_totp", False) and account.get("has_app_pass", False)
    )
    balances = member_balance_stats(user_data)

    display_username = next(
        (
            record.get("user_username")
            for records in (approved, pending_accounts, pending_requests, rejected)
            for record in records
            if record.get("user_username")
        ),
        user_data.get("user_username") or "لا يوجد",
    )
    display_name = next(
        (
            record.get("user_name")
            for records in (approved, pending_accounts, pending_requests, rejected)
            for record in records
            if record.get("user_name")
        ),
        user_data.get("user_name") or "غير معروف",
    )

    message = (
        "🔎 <b>تقرير فحص العضو</b>\n\n"
        f"👤 <b>الاسم:</b> {tg_html_escape(display_name)}\n"
        f"🆔 <b>المعرف:</b> <code>{member_id}</code>\n"
        f"🔗 <b>اليوزر:</b> @{tg_html_escape(display_username)}\n\n"
        f"📧 <b>عدد الإيميلات المقدمة:</b> <code>{submitted_count}</code>\n"
        f"✅ <b>الإيميلات المقبولة:</b> <code>{len(approved)}</code>\n"
        f"⏳ <b>الإيميلات قيد الانتظار:</b> <code>{len(pending_accounts)}</code>\n"
        f"❌ <b>الإيميلات المرفوضة:</b> <code>{len(rejected)}</code>\n\n"
        "📦 <b>تفصيل الإيميلات المقدمة</b>\n"
        f"🔵 إيميل + باسورد فقط: <code>{password_only}</code>\n"
        f"🟣 إيميل + باسورد + رمز مصادقة فقط: <code>{totp_only}</code>\n"
        f"🟢 إيميل + باسورد + رمز مصادقة + كلمة مرور التطبيق: <code>{app_password}</code>\n\n"
        "💰 <b>تفصيل الرصيد</b>\n"
        f"💵 الرصيد الحالي: <code>${balances['current']:.2f}</code>\n"
        f"📉 الرصيد المستهلك: <code>${balances['spent']:.2f}</code>\n"
        f"📊 الرصيد الكلي: <code>${balances['total']:.2f}</code>\n"
        f"⏳ رصيد قيد الانتظار: <code>${balances['pending']:.2f}</code>\n"
        f"🔒 رصيد معلّق للتحويل: <code>${balances['hold']:.2f}</code>"
    )

    context.user_data.pop("step", None)
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            ("🔎 فحص عضو آخر", "check_member"),
            ("🔙 لوحة المالك", "owner_panel"),
        ]),
    )


# ==================== RESOLVE MEMBER ====================
def resolve_member_id(user_input: str) -> Optional[int]:
    """Resolve a Telegram ID or username from the persisted users database."""
    users = load_json(USERS_DB)
    cleaned_input = user_input.strip()
    if cleaned_input.lstrip("-").isdigit():
        candidate = cleaned_input.lstrip("-")
        return int(cleaned_input) if candidate in users else None

    username = cleaned_input.removeprefix("@").casefold()
    if not username:
        return None

    for uid, user_data in users.items():
        candidates = [user_data.get("user_username"), user_data.get("username")]
        for collection_name in ("approved_accounts", "pending_accounts", "pending_requests", "rejected_accounts"):
            if collection_name == "pending_accounts":
                for record in user_data.get(collection_name, {}).values():
                    candidates.append(record.get("user_username"))
            else:
                for record in user_data.get(collection_name, []):
                    candidates.append(record.get("user_username"))
        if any(str(candidate or "").removeprefix("@").casefold() == username for candidate in candidates):
            try:
                return int(uid)
            except (TypeError, ValueError):
                return None
    return None


def member_balance_stats(user_data: dict) -> dict:
    """Return current, consumed, and total credited balance for a member."""
    current = round(float(user_data.get("balance", 0.0) or 0.0), 2)
    hold = round(float(user_data.get("hold_balance", 0.0) or 0.0), 2)
    approved_total = sum(
        float(account.get("amount", 0.0) or 0.0)
        for account in user_data.get("approved_accounts", [])
    )
    referral_earnings = float(user_data.get("referral_earnings", 0.0) or 0.0)
    recorded_total = float(user_data.get("total_credited_balance", 0.0) or 0.0)
    total_credited = max(approved_total + referral_earnings, recorded_total, current + hold)

    recorded_spent = user_data.get("spent_balance")
    if recorded_spent is None:
        spent = max(0.0, total_credited - current - hold)
    else:
        spent = max(0.0, float(recorded_spent or 0.0))

    total_balance = max(total_credited, current + spent + hold)
    return {
        "current": current,
        "spent": round(spent, 2),
        "hold": hold,
        "pending": round(float(user_data.get("pending_balance", 0.0) or 0.0), 2),
        "total": round(total_balance, 2),
    }


# ==================== OWNER PANEL ====================
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    context.user_data.pop("step", None)
    buttons = [
        ("💰 أسعار المستويات", "set_tier_prices"),
        ("📋 الطلبات", "approval_requests"),
        ("📹 قسم الفيديوهات", "videos_section"),
        ("🛒 المبيعات", "store_section"),
        ("📢 قناة إجبارية", "forced_channel"),
        ("📨 كروبات إشعارات الشراء", "purchase_channels"),
        ("📊 جميع الحسابات المقبولة", "all_accounts_section"),
        ("📈 إحصائيات المستخدمين", "owner_stats"),
        ("🔎 فحص عضو", "check_member"),
        ("🔗 نظام الإحالة", "referral_settings"),
        ("💰 خصم/منح نقاط", "points_management"),
        ("🔍 تحقق مباشر من الحسابات", "owner_verify_direct"),
        ("📊 تقرير التحقق المتأخر", "delayed_verification_report"),
        ("📈 تقرير الأداء", "performance_report"),
        ("🔙 القائمة الرئيسية", "main_menu")
    ]
    await query.edit_message_text("⚙️ *لوحة تحكم المالك*\n\nاختر الإعداد الذي تريد تعديله:", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


# ==================== OWNER PANEL: TIER PRICES ====================
async def set_tier_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    prices = get_tier_prices()
    buttons = [
        ("💲 المستوى 1 (باسورد فقط)", "set_tier:1"),
        ("💲 المستوى 2 (مع رمز المصادقة)", "set_tier:2"),
        ("💲 المستوى 3 (كامل)", "set_tier:3"),
        ("🔙 إعدادات المالك", "owner_panel")
    ]
    await query.edit_message_text(
        f"💰 *إعدادات أسعار المستويات*\n\n📌 *المستوى 1:* إيميل + باسورد فقط → `${prices['tier_1']:.2f}`\n📌 *المستوى 2:* إيميل + باسورد + رمز مصادقة → `${prices['tier_2']:.2f}`\n📌 *المستوى 3:* إيميل + باسورد + رمز مصادقة + كلمة مرور تطبيق → `${prices['tier_3']:.2f}`\n\nاختر المستوى لتعديل سعره:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def set_tier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    tier = query.data.split(":")[1]
    context.user_data["setting_tier"] = tier
    tier_names = {"1": "المستوى 1 (إيميل + باسورد فقط)", "2": "المستوى 2 (إيميل + باسورد + رمز مصادقة)",
                  "3": "المستوى 3 (إيميل + باسورد + رمز مصادقة + كلمة مرور تطبيق)"}
    await query.edit_message_text(f"💰 *تعديل سعر {tier_names[tier]}*\n\nأرسل السعر الجديد (رقم فقط):\n📌 مثال: 0.25",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", "set_tier_prices"))
    context.user_data["mode"] = "set_tier_price"


# ==================== OWNER PANEL: VIDEOS SECTION ====================
async def videos_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_json(DATA_DIR / "config.json")
    video_types = {"general": "📖 شرح عام للبوت", "email": "📹 فيديو إنشاء إيميل", "password": "📹 فيديو تغيير باسورد",
                   "totp": "📹 فيديو إضافة 2FA", "app_pass": "📹 فيديو كلمة مرور التطبيق",
                   "leave": "📹 فيديو المغادرة"}
    buttons = []
    for key, name in video_types.items():
        video_path = config.get(f"video_{key}")
        exists = video_path and Path(video_path).exists()
        status = "✅" if exists else "❌"
        buttons.append((f"{status} {name}", f"video_action:{key}"))
    buttons.append(("🔙 إعدادات المالك", "owner_panel"))
    await query.edit_message_text("📹 *قسم الفيديوهات*\n\n✅ = فيديو موجود\n❌ = فيديو غير موجود\n\nاختر الفيديو لإدارته:",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def video_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    config = load_json(DATA_DIR / "config.json")
    video_path = config.get(f"video_{video_type}")
    exists = video_path and Path(video_path).exists()
    video_names = {"general": "شرح عام للبوت", "email": "إنشاء إيميل", "password": "تغيير باسورد", "totp": "إضافة 2FA",
                   "app_pass": "كلمة مرور التطبيق", "leave": "المغادرة"}
    buttons = []
    if exists:
        buttons.append(("📹 عرض الفيديو", f"view_video:{video_type}"))
        buttons.append(("🗑️ حذف الفيديو", f"delete_video:{video_type}"))
    buttons.append(("📤 رفع فيديو جديد", f"set_video:{video_type}"))
    buttons.append(("🔙 قسم الفيديوهات", "videos_section"))
    status = "✅ موجود" if exists else "❌ غير موجود"
    await query.edit_message_text(f"📹 *فيديو {video_names.get(video_type, video_type)}*\n\nالحالة: {status}\n\nاختر الإجراء المناسب:",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def view_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    config = load_json(DATA_DIR / "config.json")
    video_path = config.get(f"video_{video_type}")
    if video_path and Path(video_path).exists():
        try:
            await context.bot.send_video(chat_id=query.from_user.id, video=open(video_path, "rb"),
                                         caption=f"📹 *فيديو {video_type}*", parse_mode=ParseMode.MARKDOWN,
                                         supports_streaming=True)
            await video_action(update, context)
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await query.edit_message_text("⚠️ حدث خطأ في عرض الفيديو.", reply_markup=kb_single("🔙 قسم الفيديوهات", "videos_section"))
    else:
        await query.edit_message_text("⚠️ الفيديو غير موجود.", reply_markup=kb_single("🔙 قسم الفيديوهات", "videos_section"))


async def delete_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    config = load_json(DATA_DIR / "config.json")
    video_path = config.get(f"video_{video_type}")
    if video_path and Path(video_path).exists():
        try:
            Path(video_path).unlink()
            config[f"video_{video_type}"] = ""
            save_json(DATA_DIR / "config.json", config)
            await query.edit_message_text(f"✅ تم حذف فيديو {video_type} بنجاح!", reply_markup=kb_single("🔙 قسم الفيديوهات", "videos_section"))
        except Exception as e:
            logger.error(f"Error deleting video: {e}")
            await query.edit_message_text("⚠️ حدث خطأ في حذف الفيديو.", reply_markup=kb_single("🔙 قسم الفيديوهات", "videos_section"))
    else:
        await query.edit_message_text("⚠️ الفيديو غير موجود.", reply_markup=kb_single("🔙 قسم الفيديوهات", "videos_section"))


async def set_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    context.user_data["pending_video_type"] = video_type
    await query.edit_message_text(f"📤 *أرسل الفيديو الخاص بـ {video_type} الآن (كملف فيديو):*\n\n📌 سيتم استبدال الفيديو القديم إن وجد.",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", f"video_action:{video_type}"))


async def handle_video_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    video_type = context.user_data.get("pending_video_type")
    if not video_type:
        await update.message.reply_text("⚠️ لم يتم تحديد نوع الفيديو.")
        return
    if update.message.video:
        file = await update.message.video.get_file()
        file_path = VIDEOS_DIR / f"{video_type}.mp4"
        await file.download_to_drive(file_path)
        config = load_json(DATA_DIR / "config.json")
        config[f"video_{video_type}"] = str(file_path)
        save_json(DATA_DIR / "config.json", config)
        await update.message.reply_text(f"✅ تم حفظ فيديو {video_type} بنجاح!")
        context.user_data.pop("pending_video_type", None)
        await main_menu(update, context)
    else:
        await update.message.reply_text("⚠️ يرجى إرسال فيديو صحيح.")


# ==================== OWNER PANEL: APPROVAL REQUESTS ====================
async def approval_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    buttons = [
        ("⏳ منتظرة", "view_pending"),
        ("✅ مقبولة", "view_approved"),
        ("❌ مرفوضة", "view_rejected"),
        ("🔙 إعدادات المالك", "owner_panel")
    ]
    await query.edit_message_text("📋 *الطلبات*\n\nاختر القسم:", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


# ==================== VIEW PENDING REQUESTS ====================
async def view_pending_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    pending = []
    
    for uid, u_data in users.items():
        # New pending accounts
        for email, account in u_data.get("pending_accounts", {}).items():
            acc_copy = account.copy()
            acc_copy["user_id"] = uid
            acc_copy["email"] = email
            acc_copy["pending_type"] = "new"
            pending.append(acc_copy)
        
        # Legacy pending requests
        for idx, req in enumerate(u_data.get("pending_requests", [])):
            req_copy = req.copy()
            req_copy["user_id"] = uid
            req_copy["index"] = idx
            req_copy["pending_type"] = "legacy"
            pending.append(req_copy)
    
    if not pending:
        await query.edit_message_text("📭 لا توجد طلبات منتظرة.", reply_markup=kb_single("🔙 الطلبات", "approval_requests"))
        return
    
    buttons = []
    for req in pending:
        tier_icon = "🔵"
        if req.get("has_app_pass", False) and req.get("has_totp", False):
            tier_icon = "🟢"
        elif req.get("has_totp", False):
            tier_icon = "🟡"
        email_display = req.get('email', '')[:15] + "..." if len(req.get('email', '')) > 15 else req.get('email', '')
        
        # Different callback for new vs legacy
        if req.get("pending_type") == "new":
            callback = f"pending_detail_new:{req['user_id']}:{req['email']}"
        else:
            callback = f"pending_detail:{req['user_id']}:{req['index']}"
        
        buttons.append((f"{tier_icon} {email_display}", callback))
    
    buttons.append(("🔙 الطلبات", "approval_requests"))
    await query.edit_message_text(
        "⏳ *الطلبات المنتظرة*\n🟢 مكتمل | 🟡 مع رمز المصادقة | 🔵 باسورد فقط\n\nاختر الإيميل لعرض التفاصيل:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


# ==================== PENDING DETAIL (Legacy) ====================
async def pending_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text("⚠️ هذا الطلب غير موجود أو تمت معالجته.", 
                                     reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    request = pending[index]
    await show_pending_detail(update, context, uid, request, "legacy", index)


# ==================== PENDING DETAIL (New) ====================
async def pending_detail_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    
    user_data = get_user(uid)
    pending = user_data.get("pending_accounts", {})
    request = pending.get(email)
    
    if not request:
        await query.edit_message_text("⚠️ هذا الطلب غير موجود أو تمت معالجته.", 
                                     reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    await show_pending_detail(update, context, uid, request, "new", email)


async def show_pending_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, request: dict, pending_type: str, identifier):
    """Show pending account details"""
    query = update.callback_query
    email = request.get("email", "")
    
    tier_icon = "🟢" if request.get("has_app_pass", False) else "🟡" if request.get("has_totp", False) else "🔵"
    tier_text = "مكتمل" if request.get("has_app_pass", False) else "مع رمز المصادقة" if request.get("has_totp", False) else "باسورد فقط"
    display_email = tg_html_escape(email)
    user_name = tg_html_escape(request.get("user_name", "غير معروف"))
    user_username = tg_html_escape(request.get("user_username", "لا يوجد"))

    msg = "📋 <b>تفاصيل الطلب</b>\n\n"
    msg += f"👤 <b>البائع:</b> {user_name}\n"
    msg += f"🆔 <b>اليوزر:</b> @{user_username}\n"
    msg += f"📧 <b>الإيميل:</b> <code>{display_email}</code>\n"
    msg += f"🔑 <b>الباسورد:</b> <code>{tg_html_escape(request.get('password', ''))}</code>\n"
    
    if request.get("has_totp", False):
        msg += f"🔐 <b>رمز المصادقة:</b> <code>{tg_html_escape(request.get('totp_secret') or request.get('totp', ''))}</code>\n"
    else:
        msg += "🔐 <b>رمز المصادقة:</b> ❌ غير مرسل\n"
    
    if request.get("has_app_pass", False):
        formatted_pass = tg_html_escape(format_app_password(request.get("app_password") or request.get("app_pass", "")))
        msg += f"🗝 <b>كلمة مرور التطبيق:</b> <code>{formatted_pass}</code>\n"
    else:
        msg += "🗝 <b>كلمة مرور التطبيق:</b> ❌ غير مرسل\n"
    
    msg += f"📦 <b>المستوى:</b> {tier_icon} {tier_text}\n"
    msg += f"👤 <b>المستخدم:</b> <code>{uid}</code>\n"
    msg += f"💰 <b>السعر:</b> ${request.get('amount', 0):.2f}\n"
    
    # Show verification status for new accounts
    if pending_type == "new":
        status = request.get("verification_status", "pending")
        status_map = {"pending": "⏳ في انتظار التحقق", "verifying": "🔄 جاري التحقق", "verified": "✅ تم التحقق"}
        msg += f"📌 <b>حالة التحقق:</b> {status_map.get(status, status)}\n"
        if request.get("submitted_at"):
            msg += f"📅 <b>تاريخ الإرسال:</b> {request.get('submitted_at')}\n"
    
    config = load_json(DATA_DIR / "config.json")
    has_leave_video = config.get("video_leave") and Path(config.get("video_leave", "")).exists()
    
    buttons = []
    
    if pending_type == "new":
        buttons.append(("✅ قبول", f"approve_new:{uid}:{email}"))
        buttons.append(("📹 قبول مع فيديو المغادرة", f"approve_leave_new:{uid}:{email}"))
        buttons.append(("❌ رفض", f"reject_new:{uid}:{email}"))
    else:
        buttons.append(("✅ قبول فوري", f"approve_request:{uid}:{identifier}"))
        if has_leave_video:
            buttons.append(("📹 قبول مع فيديو المغادرة", f"approve_with_leave:{uid}:{identifier}"))
        buttons.append(("❌ رفض", f"reject_request:{uid}:{identifier}"))
    
    buttons.append(("🔙 الطلبات المنتظرة", "view_pending"))
    
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


# ==================== APPROVE NEW ACCOUNT ====================
async def approve_new_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve a new pending account"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    with_leave = "leave" in query.data
    
    user_data = get_user(uid)
    pending = user_data.get("pending_accounts", {})
    account = pending.get(email)
    
    if not account:
        await query.edit_message_text("⚠️ هذا الطلب غير موجود أو تمت معالجته مسبقاً.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    # Complete approval for new account
    await complete_approval_new(update, context, uid, email, account, with_leave)
    
    display_email = tg_html_escape(email)
    await query.edit_message_text(
        f"✅ تم قبول الحساب <code>{display_email}</code> بنجاح!\n💰 تم نقل ${account.get('amount', 0):.2f} من قيد الانتظار إلى الرصيد.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"),
    )


async def complete_approval_new(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, email: str, account: dict, with_leave: bool = False):
    """Complete approval for a new account"""
    user_data = get_user(uid)
    config = load_json(DATA_DIR / "config.json")
    price = account.get("amount", 0.0)
    
    # Generate TOTP code if TOTP exists
    totp_code = ""
    if account.get("has_totp", False) and account.get("totp_secret"):
        try:
            totp = pyotp.TOTP(account.get("totp_secret"))
            totp_code = totp.now()
        except:
            totp_code = "غير متاح"
    
    account["extracted"] = False
    account["approved_with_leave"] = with_leave
    account["leave_confirmed"] = not with_leave
    account["totp_code"] = totp_code
    account["verified_at"] = datetime.now(timezone.utc).isoformat()
    account["verified_by_owner"] = True
    
    if with_leave:
        approval_time = datetime.now(timezone.utc)
        account["approval_time"] = approval_time.isoformat()
        account["release_at"] = (approval_time + timedelta(seconds=LEAVE_HOLD_SECONDS)).isoformat()
        user_data["hold_balance"] = round(float(user_data.get("hold_balance", 0.0)) + price, 2)
    else:
        user_data["balance"] = round(float(user_data.get("balance", 0.0)) + price, 2)
    
    user_data["pending_balance"] = max(0.0, float(user_data.get("pending_balance", 0.0)) - price)
    user_data["total_credited_balance"] = round(float(user_data.get("total_credited_balance", 0.0) or 0.0) + price, 2)
    
    # Remove from pending and add to approved
    pending = user_data.get("pending_accounts", {})
    del pending[email]
    user_data["pending_accounts"] = pending
    
    if "approved_accounts" not in user_data:
        user_data["approved_accounts"] = []
    user_data["approved_accounts"].append(account)
    user_data["total_approved_emails"] = int(user_data.get("total_approved_emails", 0)) + 1
    
    save_user(uid, user_data)
    
    # Referral bonus
    referred_by = user_data.get("referred_by")
    if referred_by:
        referral_bonus = float(config.get("referral_bonus", 0.0))
        if referral_bonus > 0:
            referrer_data = get_user(referred_by)
            referrer_data["referral_earnings"] = float(referrer_data.get("referral_earnings", 0.0)) + referral_bonus
            referrer_data["total_credited_balance"] = round(
                float(referrer_data.get("total_credited_balance", 0.0) or 0.0) + referral_bonus,
                2,
            )
            referrer_data["total_referrals"] = int(referrer_data.get("total_referrals", 0)) + 1
            save_user(referred_by, referrer_data)
            try:
                await context.bot.send_message(chat_id=referred_by,
                                               text=f"🎉 *مبروك!*\nحصلت على مكافأة إحالة بقيمة ${referral_bonus:.2f}\nبسبب إحالة المستخدم {uid} الذي أضاف حساباً جديداً.",
                                               parse_mode=ParseMode.MARKDOWN)
            except:
                pass
    
    # Send confirmation to user
    user_message = f"✅ <b>تم قبول طلبك!</b>\n\n📧 الإيميل: <code>{tg_html_escape(email)}</code>\n"
    if totp_code:
        user_message += f"🔢 <b>كود المصادقة:</b> <code>{tg_html_escape(totp_code)}</code>\n"
    if with_leave:
        user_message += f"💰 المبلغ المعلق: <b>${price:.2f}</b>\n\n⏰ <b>سيتم إضافة المبلغ إلى رصيدك تلقائياً بعد 24 ساعة.</b>\n\n📹 تم إرسال فيديو المغادرة إليك.\n⚠️ قم بمغادرة الحساب لتجنب أي تأخير."
    else:
        user_message += f"💰 تم إضافة <b>${price:.2f}</b> إلى رصيدك."
    
    try:
        await context.bot.send_message(chat_id=uid, text=user_message, parse_mode=ParseMode.HTML)
    except:
        pass
    
    # Schedule leave check if with_leave
    if with_leave:
        await send_leave_video_to_user(context, uid, email)
        await schedule_leave_check(context, uid, email, account.get("release_at"))


# ==================== REJECT NEW ACCOUNT ====================
async def reject_new_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject a new pending account"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    
    user_data = get_user(uid)
    pending = user_data.get("pending_accounts", {})
    account = pending.get(email)
    
    if not account:
        await query.edit_message_text("⚠️ هذا الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    # Remove from pending balance
    price = account.get("amount", 0.0)
    user_data["pending_balance"] = max(0.0, float(user_data.get("pending_balance", 0.0)) - price)
    
    # Move to rejected
    account["rejected_at"] = datetime.now(timezone.utc).isoformat()
    account["reject_reason"] = "rejected_by_owner"
    account["reject_message"] = "تم رفض الحساب من قبل المالك"
    
    del pending[email]
    user_data["pending_accounts"] = pending
    
    if "rejected_accounts" not in user_data:
        user_data["rejected_accounts"] = []
    user_data["rejected_accounts"].append(account)
    
    save_user(uid, user_data)
    
    # Notify user
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"❌ *تم رفض حسابك من قبل المالك!*\n\n"
                 f"📧 الإيميل: `{email}`\n"
                 f"📝 السبب: تم رفض الحساب من قبل المالك.\n\n"
                 f"_يمكنك إعادة المحاولة بإرسال بيانات صحيحة._",
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        pass
    
    display_email = tg_html_escape(email)
    await query.edit_message_text(
        f"❌ تم رفض الحساب <code>{display_email}</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"),
    )


# ==================== APPROVE REQUEST (Legacy) ====================
async def approve_request_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text("⚠️ هذا الطلب غير موجود أو تمت معالجته مسبقاً.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    approved_request = pending[index]
    email = approved_request.get("email", "")
    display_email = tg_html_escape(email)
    
    # Check if TOTP is missing
    if not approved_request.get("has_totp", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_index"] = index
        context.user_data["approval_step"] = "waiting_totp"
        context.user_data["approval_data"] = approved_request
        context.user_data["approval_with_leave"] = False
        context.user_data["approval_type"] = "legacy"
        await query.edit_message_text(
            f"🔐 <b>طلب رمز المصادقة</b>\n\n📧 الإيميل: <code>{display_email}</code>\n\n⚠️ هذا الحساب ليس لديه رمز مصادقة.\n📌 أرسل رمز المصادقة (32 حرفاً):\nالصيغة: XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX\n\n<i>يمكنك كتابة 'تخطي' لتخطي هذه الخطوة</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("🔙 إلغاء", f"pending_detail:{uid}:{index}")
            ])
        )
        return
    
    # Check if App Pass is missing
    if not approved_request.get("has_app_pass", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_index"] = index
        context.user_data["approval_step"] = "waiting_app_pass"
        context.user_data["approval_data"] = approved_request
        context.user_data["approval_with_leave"] = False
        context.user_data["approval_type"] = "legacy"
        await query.edit_message_text(
            f"🗝 <b>طلب كلمة مرور التطبيق</b>\n\n📧 الإيميل: <code>{display_email}</code>\n\n⚠️ هذا الحساب ليس لديه كلمة مرور تطبيق.\n📌 أرسل كلمة مرور التطبيق (16 حرفاً):\nالصيغة: XXXX XXXX XXXX XXXX\n\n<i>يمكنك كتابة 'تخطي' لتخطي هذه الخطوة</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("🔙 إلغاء", f"pending_detail:{uid}:{index}")
            ])
        )
        return
    
    # Complete approval
    await complete_approval_legacy(update, context, uid, index, approved_request, False)
    await query.edit_message_text(
        f"✅ تم قبول الحساب <code>{display_email}</code> بنجاح!\n💰 تم نقل ${approved_request.get('amount', 0):.2f} من قيد الانتظار إلى الرصيد.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"),
    )


async def complete_approval_legacy(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, index: int,
                                   approved_request: dict, with_leave: bool = False):
    """Complete approval for legacy requests"""
    user_data = get_user(uid)
    config = load_json(DATA_DIR / "config.json")
    price = approved_request.get("amount", 0.0)
    
    # Generate TOTP code if TOTP exists
    totp_code = ""
    if approved_request.get("has_totp", False) and approved_request.get("totp", ""):
        try:
            totp = pyotp.TOTP(approved_request.get("totp", ""))
            totp_code = totp.now()
        except:
            totp_code = "غير متاح"
    
    approved_request["extracted"] = False
    approved_request["approved_with_leave"] = with_leave
    approved_request["leave_confirmed"] = not with_leave
    approved_request["totp_code"] = totp_code
    
    if with_leave:
        approval_time = datetime.now(timezone.utc)
        approved_request["approval_time"] = approval_time.isoformat()
        approved_request["release_at"] = (approval_time + timedelta(seconds=LEAVE_HOLD_SECONDS)).isoformat()
        user_data["hold_balance"] = round(float(user_data.get("hold_balance", 0.0)) + price, 2)
    else:
        user_data["balance"] = round(float(user_data.get("balance", 0.0)) + price, 2)
    
    user_data["pending_balance"] = max(0.0, float(user_data.get("pending_balance", 0.0)) - price)
    user_data["total_credited_balance"] = round(float(user_data.get("total_credited_balance", 0.0) or 0.0) + price, 2)
    
    pending = user_data.get("pending_requests", [])
    if index < len(pending):
        pending.pop(index)
    user_data["pending_requests"] = pending
    
    if "approved_accounts" not in user_data:
        user_data["approved_accounts"] = []
    user_data["approved_accounts"].append(approved_request)
    user_data["total_approved_emails"] = int(user_data.get("total_approved_emails", 0)) + 1
    
    save_user(uid, user_data)
    
    # Referral bonus
    referred_by = user_data.get("referred_by")
    if referred_by:
        referral_bonus = float(config.get("referral_bonus", 0.0))
        if referral_bonus > 0:
            referrer_data = get_user(referred_by)
            referrer_data["referral_earnings"] = float(referrer_data.get("referral_earnings", 0.0)) + referral_bonus
            referrer_data["total_credited_balance"] = round(
                float(referrer_data.get("total_credited_balance", 0.0) or 0.0) + referral_bonus,
                2,
            )
            referrer_data["total_referrals"] = int(referrer_data.get("total_referrals", 0)) + 1
            save_user(referred_by, referrer_data)
            try:
                await context.bot.send_message(chat_id=referred_by,
                                               text=f"🎉 *مبروك!*\nحصلت على مكافأة إحالة بقيمة ${referral_bonus:.2f}\nبسبب إحالة المستخدم {uid} الذي أضاف حساباً جديداً.",
                                               parse_mode=ParseMode.MARKDOWN)
            except:
                pass
    
    # Send confirmation to user
    email = approved_request.get("email", "")
    user_message = f"✅ <b>تم قبول طلبك!</b>\n\n📧 الإيميل: <code>{tg_html_escape(email)}</code>\n"
    if totp_code:
        user_message += f"🔢 <b>كود المصادقة:</b> <code>{tg_html_escape(totp_code)}</code>\n"
    if with_leave:
        user_message += f"💰 المبلغ المعلق: <b>${price:.2f}</b>\n\n⏰ <b>سيتم إضافة المبلغ إلى رصيدك تلقائياً بعد 24 ساعة.</b>\n\n📹 تم إرسال فيديو المغادرة إليك.\n⚠️ قم بمغادرة الحساب لتجنب أي تأخير."
    else:
        user_message += f"💰 تم إضافة <b>${price:.2f}</b> إلى رصيدك."
    
    try:
        await context.bot.send_message(chat_id=uid, text=user_message, parse_mode=ParseMode.HTML)
    except:
        pass
    
    if with_leave:
        await send_leave_video_to_user(context, uid, email)
        await schedule_leave_check(context, uid, email, approved_request.get("release_at"))


async def approve_with_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text("⚠️ هذا الطلب غير موجود أو تمت معالجته مسبقاً.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    approved_request = pending[index]
    email = approved_request.get("email", "")
    display_email = tg_html_escape(email)
    
    if not approved_request.get("has_totp", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_index"] = index
        context.user_data["approval_step"] = "waiting_totp"
        context.user_data["approval_data"] = approved_request
        context.user_data["approval_with_leave"] = True
        context.user_data["approval_type"] = "legacy"
        await query.edit_message_text(
            f"🔐 <b>طلب رمز المصادقة</b>\n\n📧 الإيميل: <code>{display_email}</code>\n\n⚠️ هذا الحساب ليس لديه رمز مصادقة.\n📌 أرسل رمز المصادقة (32 حرفاً):\nالصيغة: XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX\n\n<i>يمكنك كتابة 'تخطي' لتخطي هذه الخطوة</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("🔙 إلغاء", f"pending_detail:{uid}:{index}")
            ])
        )
        return
    
    if not approved_request.get("has_app_pass", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_index"] = index
        context.user_data["approval_step"] = "waiting_app_pass"
        context.user_data["approval_data"] = approved_request
        context.user_data["approval_with_leave"] = True
        context.user_data["approval_type"] = "legacy"
        await query.edit_message_text(
            f"🗝 <b>طلب كلمة مرور التطبيق</b>\n\n📧 الإيميل: <code>{display_email}</code>\n\n⚠️ هذا الحساب ليس لديه كلمة مرور تطبيق.\n📌 أرسل كلمة مرور التطبيق (16 حرفاً):\nالصيغة: XXXX XXXX XXXX XXXX\n\n<i>يمكنك كتابة 'تخطي' لتخطي هذه الخطوة</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("🔙 إلغاء", f"pending_detail:{uid}:{index}")
            ])
        )
        return
    
    await complete_approval_legacy(update, context, uid, index, approved_request, True)
    await query.edit_message_text(
        f"✅ تم قبول الحساب <code>{display_email}</code> مع فيديو المغادرة!\n💰 المبلغ ${approved_request.get('amount', 0):.2f} معلق لمدة 24 ساعة.\n⏰ سيتم تحويله تلقائياً بعد 24 ساعة.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"),
    )


# ==================== REJECT REQUEST (Legacy) ====================
async def reject_request_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text("⚠️ هذا الطلب غير موجود.", reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    email = pending[index].get("email", "")
    display_email = tg_html_escape(email)
    context.user_data["reject_uid"] = uid
    context.user_data["reject_index"] = index
    context.user_data["reject_type"] = "legacy"
    
    buttons = [
        ("📧 إيميل خطأ", f"reject_reason:email:{uid}:{index}"),
        ("🔑 باسورد خطأ", f"reject_reason:password:{uid}:{index}"),
        ("🔐 رمز مصادقة خطأ", f"reject_reason:totp:{uid}:{index}"),
        ("🗝 كلمة مرور تطبيق خطأ", f"reject_reason:app_pass:{uid}:{index}"),
        ("📝 خطأ آخر (اكتب السبب)", f"reject_reason:other:{uid}:{index}"),
        ("🔙 التفاصيل", f"pending_detail:{uid}:{index}")
    ]
    await query.edit_message_text(
        f"❌ <b>رفض الطلب</b>\n\n📧 الإيميل: <code>{display_email}</code>\n\nاختر سبب الرفض:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical(buttons),
    )


async def execute_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    reason_type = parts[1]
    uid = int(parts[2])
    index = int(parts[3])
    
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text("⚠️ هذا الطلب غير موجود.", reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    request = pending[index]
    email = request.get("email", "")
    display_email = tg_html_escape(email)
    pending.pop(index)
    request["reject_reason"] = reason_type
    user_data.setdefault("rejected_requests", []).append(request)
    user_data["pending_balance"] = max(0.0, float(user_data.get("pending_balance", 0.0)) - float(request.get("amount", 0.0)))
    user_data["pending_requests"] = pending
    save_user(uid, user_data)
    
    reason_messages = {"email": "❌ الإيميل الذي أرسلته غير صحيح أو غير مقبول.",
                       "password": "❌ كلمة المرور التي أرسلتها غير صحيحة.",
                       "totp": "❌ رمز المصادقة الثنائية الذي أرسلته غير صحيح.",
                       "app_pass": "❌ كلمة مرور التطبيق التي أرسلتها غير صحيحة.",
                       "other": "❌ تم رفض طلبك لسبب آخر."}
    reason = reason_messages.get(reason_type, "❌ تم رفض طلبك.")
    
    config = load_json(DATA_DIR / "config.json")
    if reason_type in ["email", "password", "totp", "app_pass"]:
        video_key = {"email": "video_email", "password": "video_password", "totp": "video_totp",
                     "app_pass": "video_app_pass"}.get(reason_type)
        video_path = config.get(video_key)
        if video_path and Path(video_path).exists():
            try:
                await context.bot.send_video(chat_id=uid, video=open(video_path, "rb"),
                                             caption=f"{reason}\n\n📹 *شاهد الفيديو لمعرفة الطريقة الصحيحة:*",
                                             parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            except:
                pass
    else:
        context.user_data["reject_uid"] = uid
        context.user_data["reject_index"] = index
        context.user_data["reject_reason"] = "other"
        await query.edit_message_text(
            f"📝 <b>اكتب سبب الرفض</b>\n\nأرسل رسالة توضح سبب رفض طلب <code>{display_email}</code>:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{index}"),
        )
        context.user_data["step"] = "reject_reason_text"
        return
    
    try:
        await context.bot.send_message(chat_id=uid,
                                       text=f"{reason}\n\n📧 الإيميل: `{email}`\nيمكنك إعادة المحاولة بإرسال إيميل جديد.",
                                       parse_mode=ParseMode.MARKDOWN)
    except:
        pass
    await query.edit_message_text(
        f"✅ تم رفض الطلب <code>{display_email}</code> وإرسال السبب للمستخدم.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"),
    )


async def handle_reject_reason_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data.get("reject_uid")
    index = context.user_data.get("reject_index")
    text = update.message.text.strip()
    if not uid or index is None:
        await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        return
    
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await update.message.reply_text("⚠️ الطلب غير موجود.")
        return
    
    request = pending[index]
    email = request.get("email", "")
    
    try:
        await context.bot.send_message(chat_id=uid,
                                       text=f"❌ *تم رفض طلبك*\n\n📧 الإيميل: `{email}`\n📝 السبب: {text}\n\nيمكنك إعادة المحاولة بإرسال إيميل جديد.",
                                       parse_mode=ParseMode.MARKDOWN)
    except:
        pass
    
    context.user_data.pop("reject_uid", None)
    context.user_data.pop("reject_index", None)
    context.user_data.pop("reject_reason", None)
    context.user_data.pop("step", None)
    await update.message.reply_text(f"✅ تم رفض الطلب `{email}` وإرسال السبب للمستخدم.",
                                    reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))


# ==================== HANDLE APPROVAL TOTP ====================
async def handle_approval_totp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data.get("approval_uid")
    index = context.user_data.get("approval_index")
    approved_request = context.user_data.get("approval_data")
    with_leave = context.user_data.get("approval_with_leave", False)
    approval_type = context.user_data.get("approval_type", "legacy")
    
    if not uid or index is None or not approved_request:
        await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        return
    
    user_data = get_user(uid)
    
    if approval_type == "new":
        # Handle new account
        pending = user_data.get("pending_accounts", {})
        account = pending.get(index)  # index is email for new
        if not account:
            await update.message.reply_text("⚠️ الطلب غير موجود.")
            return
    else:
        # Handle legacy
        pending = user_data.get("pending_requests", [])
        if index >= len(pending):
            await update.message.reply_text("⚠️ الطلب غير موجود.")
            return
        account = pending[index]
    
    email = account.get("email", "")
    
    if text.lower() == "تخطي":
        account["totp"] = ""
        account["has_totp"] = False
        context.user_data["approval_data"] = account
        
        if not account.get("has_app_pass", False):
            context.user_data["approval_step"] = "waiting_app_pass"
            await update.message.reply_text(
                f"✅ تم تخطي رمز المصادقة.\n\n🗝 *الآن أرسل كلمة مرور التطبيق (16 حرفاً):*\nالصيغة: XXXX XXXX XXXX XXXX\n\n_يمكنك كتابة 'تخطي' لتخطي هذه الخطوة_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_vertical([
                    ("🔙 إلغاء", f"pending_detail:{uid}:{index}")
                ])
            )
        else:
            if approval_type == "new":
                await complete_approval_new(update, context, uid, email, account, with_leave)
            else:
                await complete_approval_legacy(update, context, uid, index, account, with_leave)
        return
    
    cleaned = text.replace(" ", "").upper()
    if len(cleaned) != 32:
        await update.message.reply_text("⚠️ مفتاح المصادقة يجب أن يكون 32 حرفاً.")
        return
    if not re.match(r'^[A-Z2-7]{32}$', cleaned):
        await update.message.reply_text("⚠️ مفتاح المصادقة يحتوي على أحرف غير صالحة.")
        return
    
    try:
        secret = cleaned
        code = pyotp.TOTP(secret).now()
        account["totp"] = secret
        account["has_totp"] = True
        context.user_data["approval_data"] = account
        
        formatted_secret = format_totp_secret(secret)
        
        if not account.get("has_app_pass", False):
            context.user_data["approval_step"] = "waiting_app_pass"
            await update.message.reply_text(
                f"✅ رمز المصادقة صالح!\n🔐 *المفتاح:* `{formatted_secret}`\n🔢 *كود المصادقة الحالي:* `{code}`\n⏰ *الوقت:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n🗝 *الآن أرسل كلمة مرور التطبيق (16 حرفاً):*\nالصيغة: XXXX XXXX XXXX XXXX\n\n_يمكنك كتابة 'تخطي' لتخطي هذه الخطوة_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_vertical([
                    ("🔙 إلغاء", f"pending_detail:{uid}:{index}")
                ])
            )
        else:
            if approval_type == "new":
                await complete_approval_new(update, context, uid, email, account, with_leave)
            else:
                await complete_approval_legacy(update, context, uid, index, account, with_leave)
    except Exception as e:
        await update.message.reply_text(f"⚠️ مفتاح 2FA غير صالح: {str(e)}\n\n📌 أرسل رمز المصادقة الصحيح أو اكتب 'تخطي' لتخطي هذه الخطوة.",
                                        parse_mode=ParseMode.MARKDOWN)


# ==================== HANDLE APPROVAL APP PASS ====================
async def handle_approval_app_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data.get("approval_uid")
    index = context.user_data.get("approval_index")
    approved_request = context.user_data.get("approval_data")
    with_leave = context.user_data.get("approval_with_leave", False)
    approval_type = context.user_data.get("approval_type", "legacy")
    
    if not uid or index is None or not approved_request:
        await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        return
    
    user_data = get_user(uid)
    
    if approval_type == "new":
        pending = user_data.get("pending_accounts", {})
        account = pending.get(index)  # index is email for new
        if not account:
            await update.message.reply_text("⚠️ الطلب غير موجود.")
            return
    else:
        pending = user_data.get("pending_requests", [])
        if index >= len(pending):
            await update.message.reply_text("⚠️ الطلب غير موجود.")
            return
        account = pending[index]
    
    email = account.get("email", "")
    
    if text.lower() == "تخطي":
        account["app_pass"] = ""
        account["has_app_pass"] = False
        context.user_data["approval_data"] = account
        
        if approval_type == "new":
            await complete_approval_new(update, context, uid, email, account, with_leave)
        else:
            await complete_approval_legacy(update, context, uid, index, account, with_leave)
        return
    
    cleaned = text.replace(" ", "").upper()
    if len(cleaned) != 16:
        await update.message.reply_text("⚠️ كلمة مرور التطبيق يجب أن تكون 16 حرفاً.")
        return
    if not re.match(r'^[A-Z0-9]{16}$', cleaned):
        await update.message.reply_text("⚠️ كلمة مرور التapplication تحتوي على أحرف غير صالحة.")
        return
    
    used_passwords = user_data.get("used_app_passwords", [])
    if cleaned in used_passwords:
        config = load_json(DATA_DIR / "config.json")
        video_path = config.get("video_app_pass")
        msg = "⚠️ *كلمة المرور هذه مستخدمة مسبقاً!*\n\nيرجى تغيير كلمة المرور وإرسال كلمة جديدة."
        if video_path and Path(video_path).exists():
            try:
                await context.bot.send_video(chat_id=uid, video=open(video_path, "rb"), caption=msg, parse_mode=ParseMode.MARKDOWN)
            except:
                await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        return
    
    account["app_pass"] = cleaned
    account["has_app_pass"] = True
    
    used_passwords.append(cleaned)
    user_data["used_app_passwords"] = used_passwords
    save_user(uid, user_data)
    
    context.user_data["approval_data"] = account
    
    formatted_pass = format_app_password(cleaned)
    await update.message.reply_text(
        f"✅ تم استلام كلمة مرور التطبيق.\n🗝 *كلمة المرور:* `{formatted_pass}`\n\n📌 سيتم إكمال الموافقة على الحساب `{email}`",
        parse_mode=ParseMode.MARKDOWN)
    
    if approval_type == "new":
        await complete_approval_new(update, context, uid, email, account, with_leave)
    else:
        await complete_approval_legacy(update, context, uid, index, account, with_leave)


# ==================== VIEW APPROVED REQUESTS ====================
async def view_approved_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    approved = []
    for uid, u_data in users.items():
        for idx, acc in enumerate(u_data.get("approved_accounts", [])):
            acc_copy = acc.copy()
            acc_copy["user_id"] = uid
            acc_copy["index"] = idx
            approved.append(acc_copy)
    if not approved:
        await query.edit_message_text("📭 لا توجد طلبات مقبولة.", reply_markup=kb_single("🔙 الطلبات", "approval_requests"))
        return
    buttons = []
    for acc in approved[:20]:
        tier_icon = "🟢" if acc.get("has_app_pass", False) else "🟡" if acc.get("has_totp", False) else "🔵"
        user_name = acc.get("user_name", "غير معروف")
        user_username = acc.get("user_username", "لا يوجد")
        display_name = f"{user_name} (@{user_username})" if user_username != "لا يوجد" else user_name
        email_display = acc.get('email', '')[:15] + "..." if len(acc.get('email', '')) > 15 else acc.get('email', '')
        buttons.append((f"{tier_icon} {email_display} - {display_name[:12]} (${acc.get('amount', 0):.2f})",
                        f"approved_detail:{acc['user_id']}:{acc.get('index', 0)}"))
    buttons.append(("🔙 الطلبات", "approval_requests"))
    await query.edit_message_text(f"✅ *الطلبات المقبولة* ({len(approved)})\n\n🟢 مكتمل | 🟡 مع رمز المصادقة | 🔵 باسورد فقط\n\nاختر الإيميل لعرض التفاصيل:",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


# ==================== APPROVED DETAIL ====================
async def approved_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    
    user_data = get_user(uid)
    accounts = user_data.get("approved_accounts", [])
    if index >= len(accounts):
        await query.edit_message_text("⚠️ هذا الحساب غير موجود.", reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved"))
        return
    
    account = accounts[index]
    tier_icon = "🟢" if account.get("has_app_pass", False) else "🟡" if account.get("has_totp", False) else "🔵"
    tier_text = "مكتمل" if account.get("has_app_pass", False) else "مع رمز المصادقة" if account.get("has_totp", False) else "باسورد فقط"
    user_name = account.get("user_name", "غير معروف")
    user_username = account.get("user_username", "لا يوجد")
    
    msg = f"📋 *تفاصيل الحساب المقبول*\n\n"
    msg += f"👤 *البائع:* {user_name}\n"
    msg += f"🆔 *اليوزر:* @{user_username}\n"
    msg += f"📧 *الإيميل:* `{account.get('email', '')}`\n"
    msg += f"🔑 *الباسورد:* `{account.get('password', '')}`\n"
    
    if account.get("has_totp", False):
        msg += f"🔐 *رمز المصادقة:* `{account.get('totp_secret') or account.get('totp', '')}`\n"
        totp_code = account.get("totp_code", "")
        if totp_code:
            msg += f"🔢 *كود المصادقة (الحالي):* `{totp_code}`\n"
        else:
            try:
                totp_secret = account.get("totp_secret") or account.get("totp", "")
                if totp_secret:
                    totp = pyotp.TOTP(totp_secret)
                    msg += f"🔢 *كود المصادقة (الحالي):* `{totp.now()}`\n"
            except:
                pass
    else:
        msg += f"🔐 *رمز المصادقة:* ❌ غير مرسل\n"
    
    if account.get("has_app_pass", False):
        formatted_pass = format_app_password(account.get("app_password") or account.get("app_pass", ""))
        msg += f"🗝 *كلمة مرور التطبيق:* `{formatted_pass}`\n"
    else:
        msg += f"🗝 *كلمة مرور التطبيق:* ❌ غير مرسل\n"
    
    msg += f"📦 *المستوى:* {tier_icon} {tier_text}\n"
    msg += f"👤 *المستخدم:* `{uid}`\n"
    msg += f"💰 *السعر:* ${account.get('amount', 0):.2f}\n"
    
    leave_status = ""
    if account.get("approved_with_leave", False) and not account.get("leave_confirmed", False):
        leave_status = "⏳ معلق (24 ساعة)"
    elif account.get("approved_with_leave", False) and account.get("leave_confirmed", False):
        leave_status = "✅ تم التحويل"
    if leave_status:
        msg += f"📌 *حالة المغادرة:* {leave_status}\n"

    if account.get("has_totp", False):
        msg += f"\n📌 *هل تريد الحصول على كود جديد لرمز المصادقة؟*"
        buttons = [
            ("🔄 كود جديد", f"new_totp_code:{uid}:{index}"),
            ("💰 خصم نقاط", f"deduct_points:{uid}:{index}"),
            ("🔙 الطلبات المقبولة", "view_approved")
        ]
    else:
        buttons = [
            ("💰 خصم نقاط", f"deduct_points:{uid}:{index}"),
            ("🔙 الطلبات المقبولة", "view_approved")
        ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


# ==================== NEW TOTP CODE ====================
async def new_totp_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])

    user_data = get_user(uid)
    accounts = user_data.get("approved_accounts", [])
    if index >= len(accounts):
        await query.edit_message_text("⚠️ الحساب غير موجود.", reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved"))
        return

    account = accounts[index]
    totp_secret = account.get("totp_secret") or account.get("totp", "")
    if not account.get("has_totp", False) or not totp_secret:
        await query.edit_message_text("⚠️ هذا الحساب لا يحتوي على رمز مصادقة.", reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved"))
        return

    try:
        totp = pyotp.TOTP(totp_secret)
        new_code = totp.now()
        account["totp_code"] = new_code
        accounts[index] = account
        user_data["approved_accounts"] = accounts
        save_user(uid, user_data)

        await query.edit_message_text(
            f"🔄 *كود المصادقة الجديد*\n\n📧 الإيميل: `{account.get('email', '')}`\n🔢 *الكود الحالي:* `{new_code}`\n⏰ *الوقت:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n_الكود يتغير كل 30 ثانية_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_vertical([
                ("🔄 تحديث الكود مرة أخرى", f"new_totp_code:{uid}:{index}"),
                ("🔙 الطلبات المقبولة", "view_approved")
            ])
        )
    except Exception as e:
        await query.edit_message_text(f"⚠️ حدث خطأ: {str(e)}", reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved"))


# ==================== DEDUCT POINTS ====================
async def deduct_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    
    user_data = get_user(uid)
    accounts = user_data.get("approved_accounts", [])
    if index >= len(accounts):
        await query.edit_message_text("⚠️ الحساب غير موجود.", reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved"))
        return
    
    email = accounts[index].get("email", "")
    context.user_data["deduct_uid"] = uid
    context.user_data["deduct_index"] = index
    await query.edit_message_text(
        f"💰 *خصم نقاط*\n\n📧 الإيميل: `{email}`\n👤 المستخدم: `{uid}`\n\n📌 أرسل المبلغ المراد خصمه من رصيد المستخدم:\n_مثال: 5.00_\n\n_أو أرسل 'إلغاء' للإلغاء_",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", f"approved_detail:{uid}:{index}"))
    context.user_data["step"] = "deduct_points_input"


async def handle_deduct_points_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "الغاء":
        context.user_data.pop("deduct_uid", None)
        context.user_data.pop("deduct_index", None)
        context.user_data.pop("step", None)
        await update.message.reply_text("❌ تم إلغاء عملية الخصم.", reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved"))
        return
    try:
        amount = float(text)
        if amount <= 0:
            await update.message.reply_text("⚠️ المبلغ يجب أن يكون أكبر من 0!")
            return
        uid = context.user_data.get("deduct_uid")
        index = context.user_data.get("deduct_index")
        if not uid or index is None:
            await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
            return
        user_data = get_user(uid)
        accounts = user_data.get("approved_accounts", [])
        if index >= len(accounts):
            await update.message.reply_text("⚠️ الحساب غير موجود.")
            return
        current_balance = float(user_data.get("balance", 0.0))
        if current_balance < amount:
            await update.message.reply_text(f"⚠️ رصيد المستخدم غير كافٍ!\n💰 الرصيد الحالي: ${current_balance:.2f}\n💰 المبلغ المطلوب خصمه: ${amount:.2f}")
            return
        user_data["balance"] = current_balance - amount
        save_user(uid, user_data)
        try:
            await context.bot.send_message(chat_id=uid,
                                           text=f"💰 *تم خصم نقاط من رصيدك!*\n\n📧 الإيميل: `{accounts[index].get('email', '')}`\n💰 المبلغ المخصوم: *${amount:.2f}*\n💰 الرصيد المتبقي: *${user_data['balance']:.2f}*\n\n_لمزيد من المعلومات، تواصل مع المالك_",
                                           parse_mode=ParseMode.MARKDOWN)
        except:
            pass
        context.user_data.pop("deduct_uid", None)
        context.user_data.pop("deduct_index", None)
        context.user_data.pop("step", None)
        await update.message.reply_text(
            f"✅ تم خصم ${amount:.2f} من رصيد المستخدم `{uid}` بنجاح!\n📧 الإيميل: `{accounts[index].get('email', '')}`\n💰 الرصيد المتبقي: ${user_data['balance']:.2f}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved"))
    except ValueError:
        await update.message.reply_text("⚠️ أرسل رقماً صحيحاً (مثال: 5.00)")


# ==================== VIEW REJECTED REQUESTS ====================
async def view_rejected_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    rejected = []
    for uid, u_data in users.items():
        for idx, req in enumerate(u_data.get("rejected_requests", [])):
            req_copy = req.copy()
            req_copy["user_id"] = uid
            req_copy["index"] = idx
            rejected.append(req_copy)
    if not rejected:
        await query.edit_message_text("📭 لا توجد طلبات مرفوضة.", reply_markup=kb_single("🔙 الطلبات", "approval_requests"))
        return
    buttons = []
    for rej in rejected[:20]:
        reason = rej.get('reject_reason', 'غير معروف')
        reason_map = {"email": "📧", "password": "🔑", "totp": "🔐", "app_pass": "🗝", "other": "📝"}
        icon = reason_map.get(reason, "❌")
        user_name = rej.get("user_name", "غير معروف")
        user_username = rej.get("user_username", "لا يوجد")
        display_name = f"{user_name} (@{user_username})" if user_username != "لا يوجد" else user_name
        email_display = rej.get('email', '')[:15] + "..." if len(rej.get('email', '')) > 15 else rej.get('email', '')
        buttons.append((f"{icon} {email_display} - {display_name[:12]}",
                        f"rejected_detail:{rej['user_id']}:{rej.get('index', 0)}"))
    buttons.append(("🔙 الطلبات", "approval_requests"))
    await query.edit_message_text(
        f"❌ *الطلبات المرفوضة* ({len(rejected)})\n\n📧 إيميل خطأ | 🔑 باسورد خطأ | 🔐 رمز مصادقة خطأ | 🗝 كلمة مرور تطبيق خطأ | 📝 سبب مخصص\n\nاختر الإيميل لعرض التفاصيل:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


# ==================== REJECTED DETAIL ====================
async def rejected_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    
    user_data = get_user(uid)
    rejected_list = user_data.get("rejected_requests", [])
    if index >= len(rejected_list):
        await query.edit_message_text("⚠️ هذا الطلب غير موجود.", reply_markup=kb_single("🔙 الطلبات المرفوضة", "view_rejected"))
        return
    
    request = rejected_list[index]
    reason = request.get('reject_reason', 'غير معروف')
    reason_map = {"email": "❌ الإيميل غير صحيح أو غير مقبول.", "password": "❌ كلمة المرور غير صحيحة.",
                  "totp": "❌ رمز المصادقة غير صحيح.", "app_pass": "❌ كلمة مرور التطبيق غير صحيحة.",
                  "other": "❌ تم رفض الطلب لسبب آخر.", "custom": "❌ سبب مخصص."}
    reason_text = reason_map.get(reason, reason)
    tier_icon = "🟢" if request.get("has_app_pass", False) else "🟡" if request.get("has_totp", False) else "🔵"
    tier_text = "مكتمل" if request.get("has_app_pass", False) else "مع رمز المصادقة" if request.get("has_totp", False) else "باسورد فقط"
    user_name = request.get("user_name", "غير معروف")
    user_username = request.get("user_username", "لا يوجد")
    
    msg = f"📋 *تفاصيل الطلب المرفوض*\n\n"
    msg += f"👤 *البائع:* {user_name}\n"
    msg += f"🆔 *اليوزر:* @{user_username}\n"
    msg += f"📧 *الإيميل:* `{request.get('email', '')}`\n"
    msg += f"🔑 *الباسورد:* `{request.get('password', '')}`\n"
    
    if request.get("has_totp", False):
        msg += f"🔐 *رمز المصادقة:* `{request.get('totp_secret') or request.get('totp', '')}`\n"
    else:
        msg += f"🔐 *رمز المصادقة:* ❌ غير مرسل\n"
    
    if request.get("has_app_pass", False):
        formatted_pass = format_app_password(request.get("app_password") or request.get("app_pass", ""))
        msg += f"🗝 *كلمة مرور التطبيق:* `{formatted_pass}`\n"
    else:
        msg += f"🗝 *كلمة مرور التطبيق:* ❌ غير مرسل\n"
    
    msg += f"📦 *المستوى:* {tier_icon} {tier_text}\n"
    msg += f"👤 *المستخدم:* `{uid}`\n"
    msg += f"💰 *السعر:* ${request.get('amount', 0):.2f}\n"
    msg += f"📝 *سبب الرفض:* {reason_text}\n\n"
    msg += f"📌 *هل تريد إعطاء نقاط للمستخدم رغم الرفض؟*"
    
    buttons = [
        ("💰 إعطاء نقاط", f"give_points:{uid}:{index}"),
        ("🔙 الطلبات المرفوضة", "view_rejected")
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


# ==================== GIVE POINTS ====================
async def give_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    
    user_data = get_user(uid)
    rejected_list = user_data.get("rejected_requests", [])
    if index >= len(rejected_list):
        await query.edit_message_text("⚠️ الطلب غير موجود.", reply_markup=kb_single("🔙 الطلبات المرفوضة", "view_rejected"))
        return
    
    email = rejected_list[index].get("email", "")
    context.user_data["give_uid"] = uid
    context.user_data["give_index"] = index
    await query.edit_message_text(
        f"💰 *إعطاء نقاط للمستخدم*\n\n📧 الإيميل المرفوض: `{email}`\n👤 المستخدم: `{uid}`\n\n📌 أرسل المبلغ المراد إضافته إلى رصيد المستخدم:\n_مثال: 2.50_\n\n_أو أرسل 'إلغاء' للإلغاء_",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", f"rejected_detail:{uid}:{index}"))
    context.user_data["step"] = "give_points_input"


async def handle_give_points_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "الغاء":
        context.user_data.pop("give_uid", None)
        context.user_data.pop("give_index", None)
        context.user_data.pop("step", None)
        await update.message.reply_text("❌ تم إلغاء عملية إعطاء النقاط.", reply_markup=kb_single("🔙 الطلبات المرفوضة", "view_rejected"))
        return
    try:
        amount = float(text)
        if amount <= 0:
            await update.message.reply_text("⚠️ المبلغ يجب أن يكون أكبر من 0!")
            return
        uid = context.user_data.get("give_uid")
        index = context.user_data.get("give_index")
        if not uid or index is None:
            await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
            return
        user_data = get_user(uid)
        user_data["balance"] = float(user_data.get("balance", 0.0)) + amount
        save_user(uid, user_data)
        
        rejected_list = user_data.get("rejected_requests", [])
        email = rejected_list[index].get("email", "") if index < len(rejected_list) else ""
        
        try:
            await context.bot.send_message(chat_id=uid,
                                           text=f"💰 *تم إضافة نقاط إلى رصيدك!*\n\n📧 الإيميل: `{email}`\n💰 المبلغ المضاف: *+${amount:.2f}*\n💰 الرصيد الجديد: *${user_data['balance']:.2f}*\n\n_تم إضافة هذه النقاط كتعويض عن طلبك المرفوض._",
                                           parse_mode=ParseMode.MARKDOWN)
        except:
            pass
        context.user_data.pop("give_uid", None)
        context.user_data.pop("give_index", None)
        context.user_data.pop("step", None)
        await update.message.reply_text(
            f"✅ تم إضافة ${amount:.2f} إلى رصيد المستخدم `{uid}` بنجاح!\n📧 الإيميل المرفوض: `{email}`\n💰 الرصيد الجديد: ${user_data['balance']:.2f}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 الطلبات المرفوضة", "view_rejected"))
    except ValueError:
        await update.message.reply_text("⚠️ أرسل رقماً صحيحاً (مثال: 2.50)")


# ==================== POINTS MANAGEMENT ====================
async def points_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    buttons = [
        ("➕ منح نقاط", "give_points_by_id"),
        ("➖ خصم نقاط", "deduct_points_by_id"),
        ("🔙 إعدادات المالك", "owner_panel")
    ]
    await query.edit_message_text("💰 *إدارة النقاط*\n\nاختر الإجراء:", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def give_points_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text(
        "➕ *منح نقاط لمستخدم*\n\nأرسل معرف المستخدم (ID) أو اليوزر (@username) ثم المبلغ:\n📌 مثال: `123456789 5.00`\n📌 مثال: `@user 10.00`\n\n_أو أرسل 'إلغاء' للإلغاء_",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", "points_management"))
    context.user_data["step"] = "give_points_by_id_input"


async def deduct_points_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text(
        "➖ *خصم نقاط من مستخدم*\n\nأرسل معرف المستخدم (ID) أو اليوزر (@username) ثم المبلغ:\n📌 مثال: `123456789 5.00`\n📌 مثال: `@user 10.00`\n\n_أو أرسل 'إلغاء' للإلغاء_",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", "points_management"))
    context.user_data["step"] = "deduct_points_by_id_input"


async def handle_points_by_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "الغاء":
        context.user_data.pop("step", None)
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=kb_single("🔙 إدارة النقاط", "points_management"))
        return
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text("⚠️ الصيغة غير صحيحة!\nأرسل: `معرف_المستخدم المبلغ`\nمثال: `123456789 5.00`")
        return
    user_input = parts[0]
    try:
        amount = float(parts[1])
        if amount <= 0:
            await update.message.reply_text("⚠️ المبلغ يجب أن يكون أكبر من 0!")
            return
    except ValueError:
        await update.message.reply_text("⚠️ المبلغ غير صحيح! أرسل رقم فقط.")
        return
    target_user_id = None
    if user_input.lstrip("-").isdigit():
        target_user_id = int(user_input)
    else:
        username = user_input
        if username.startswith("@"):
            username = username[1:]
        users = load_json(USERS_DB)
        for uid, u_data in users.items():
            if u_data.get("user_username", "").lower() == username.lower():
                target_user_id = int(uid)
                break
    if not target_user_id:
        await update.message.reply_text(f"⚠️ لم يتم العثور على مستخدم بـ {user_input}!\nتأكد من المعرف أو اليوزر.")
        return
    step = context.user_data.get("step")
    if step == "give_points_by_id_input":
        user_data = get_user(target_user_id)
        user_data["balance"] = float(user_data.get("balance", 0.0)) + amount
        user_data["total_credited_balance"] = round(
            float(user_data.get("total_credited_balance", 0.0) or 0.0) + amount,
            2,
        )
        save_user(target_user_id, user_data)
        try:
            await context.bot.send_message(chat_id=target_user_id,
                                           text=f"💰 *تم إضافة نقاط إلى رصيدك!*\n\n💰 المبلغ المضاف: *+${amount:.2f}*\n💰 الرصيد الجديد: *${user_data['balance']:.2f}*\n\n_تم إضافة هذه النقاط من قبل المالك._",
                                           parse_mode=ParseMode.MARKDOWN)
        except:
            pass
        await update.message.reply_text(
            f"✅ تم إضافة ${amount:.2f} إلى رصيد المستخدم `{target_user_id}` بنجاح!\n💰 الرصيد الجديد: ${user_data['balance']:.2f}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إدارة النقاط", "points_management"))
    elif step == "deduct_points_by_id_input":
        user_data = get_user(target_user_id)
        current_balance = float(user_data.get("balance", 0.0))
        if current_balance < amount:
            await update.message.reply_text(f"⚠️ رصيد المستخدم غير كافٍ!\n💰 الرصيد الحالي: ${current_balance:.2f}\n💰 المبلغ المطلوب خصمه: ${amount:.2f}")
            return
        user_data["balance"] = current_balance - amount
        user_data["spent_balance"] = round(
            float(user_data.get("spent_balance", 0.0) or 0.0) + amount,
            2,
        )
        save_user(target_user_id, user_data)
        try:
            await context.bot.send_message(chat_id=target_user_id,
                                           text=f"💰 *تم خصم نقاط من رصيدك!*\n\n💰 المبلغ المخصوم: *-${amount:.2f}*\n💰 الرصيد الجديد: *${user_data['balance']:.2f}*\n\n_تم خصم هذه النقاط من قبل المالك._",
                                           parse_mode=ParseMode.MARKDOWN)
        except:
            pass
        await update.message.reply_text(
            f"✅ تم خصم ${amount:.2f} من رصيد المستخدم `{target_user_id}` بنجاح!\n💰 الرصيد الجديد: ${user_data['balance']:.2f}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إدارة النقاط", "points_management"))
    context.user_data.pop("step", None)


# ==================== ALL ACCOUNTS SECTION ====================
async def all_accounts_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    buttons = [
        ("📋 جميع الحسابات", "all_accounts"),
        ("🆕 آخر الحسابات (غير المستخرجة)", "unextracted_accounts"),
        ("⏳ الحسابات المعلقة (24 ساعة)", "hold_accounts"),
        ("🔙 إعدادات المالك", "owner_panel")
    ]
    await query.edit_message_text("📊 *جميع الحسابات المقبولة*\n\nاختر الخيار المناسب:", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def hold_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    hold_accounts_list = []
    for uid, user_data in users.items():
        for acc in user_data.get("approved_accounts", []):
            if acc.get("approved_with_leave", False) and not acc.get("leave_confirmed", False):
                acc_copy = acc.copy()
                acc_copy["user_id"] = uid
                hold_accounts_list.append(acc_copy)
    if not hold_accounts_list:
        await query.edit_message_text("✅ لا توجد حسابات معلقة حالياً.",
                                      reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    total = len(hold_accounts_list)
    msg = f"⏳ *الحسابات المعلقة (في انتظار التحويل): {total}*\n\n"
    for idx, acc in enumerate(hold_accounts_list[:10], 1):
        approval_time = acc.get("approval_time", "غير معروف")
        try:
            dt = datetime.fromisoformat(approval_time)
            time_left = 86400 - (datetime.now(timezone.utc) - dt).total_seconds()
            if time_left > 0:
                hours_left = int(time_left // 3600)
                minutes_left = int((time_left % 3600) // 60)
                time_display = f"{hours_left} ساعة {minutes_left} دقيقة"
            else:
                time_display = "سيتم التحويل قريباً"
        except:
            time_display = "غير معروف"
        tier_icon = "🟢" if acc.get("has_app_pass", False) else "🟡" if acc.get("has_totp", False) else "🔵"
        msg += f"{idx}. {tier_icon} 📧 `{acc.get('email', '')}`\n"
        msg += f"   👤 المستخدم: {acc.get('user_id', '')}\n"
        msg += f"   💰 المبلغ: ${acc.get('amount', 0):.2f}\n"
        msg += f"   ⏳ الوقت المتبقي: {time_display}\n"
        msg += "   ─────────────\n"
    if total > 10:
        msg += f"\n📌 *ملاحظة:* تم عرض أول 10 حسابات من أصل {total}"
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))


async def all_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    all_accounts = []
    for uid, user_data in users.items():
        for acc in user_data.get("approved_accounts", []):
            acc_copy = acc.copy()
            acc_copy["user_id"] = uid
            all_accounts.append(acc_copy)
    if not all_accounts:
        await query.edit_message_text("📭 لا توجد حسابات مقبولة حالياً.",
                                      reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    total = len(all_accounts)
    msg = f"📊 *إجمالي الحسابات: {total}*\n\n"
    for idx, acc in enumerate(all_accounts[:10], 1):
        leave_status = ""
        if acc.get("approved_with_leave", False) and not acc.get("leave_confirmed", False):
            leave_status = " ⏳ (معلق)"
        tier_icon = "🟢" if acc.get("has_app_pass", False) else "🟡" if acc.get("has_totp", False) else "🔵"
        msg += f"{idx}. {tier_icon} 📧 `{acc.get('email', '')}`{leave_status}\n"
        msg += f"   🔑 `{acc.get('password', '')}`\n"
        if acc.get("has_totp", False):
            msg += f"   🔐 `{acc.get('totp_secret') or acc.get('totp', '')}`\n"
        if acc.get("has_app_pass", False):
            formatted_pass = format_app_password(acc.get("app_password") or acc.get("app_pass", ""))
            msg += f"   🗝 `{formatted_pass}`\n"
        msg += f"   👤 المستخدم: {acc.get('user_id', '')}\n"
        msg += f"   💰 السعر: ${acc.get('amount', 0):.2f}\n"
        msg += "   ─────────────\n"
    if total > 10:
        msg += f"\n📌 *ملاحظة:* تم عرض أول 10 حسابات من أصل {total}"
        msg += "\nلتصدير جميع الحسابات استخدم زر التصدير أدناه"
    buttons = [
        ("📥 تصدير جميع الحسابات", "export_all_accounts"),
        ("🆕 عرض الحسابات غير المستخرجة", "unextracted_accounts"),
        ("⏳ الحسابات المعلقة", "hold_accounts"),
        ("🔙 جميع الحسابات", "all_accounts_section")
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def unextracted_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    unextracted = []
    for uid, user_data in users.items():
        for idx, acc in enumerate(user_data.get("approved_accounts", [])):
            if not acc.get("extracted", False):
                acc_copy = acc.copy()
                acc_copy["user_id"] = uid
                acc_copy["index"] = idx
                unextracted.append(acc_copy)
    if not unextracted:
        await query.edit_message_text("✅ لا توجد حسابات غير مستخرجة.",
                                      reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    total = len(unextracted)
    msg = f"🆕 *الحسابات غير المستخرجة: {total}*\n\n"
    for idx, acc in enumerate(unextracted[:10], 1):
        tier_icon = "🟢" if acc.get("has_app_pass", False) else "🟡" if acc.get("has_totp", False) else "🔵"
        msg += f"{idx}. {tier_icon} 📧 `{acc.get('email', '')}`\n"
        msg += f"   🔑 `{acc.get('password', '')}`\n"
        if acc.get("has_totp", False):
            msg += f"   🔐 `{acc.get('totp_secret') or acc.get('totp', '')}`\n"
        if acc.get("has_app_pass", False):
            formatted_pass = format_app_password(acc.get("app_password") or acc.get("app_pass", ""))
            msg += f"   🗝 `{formatted_pass}`\n"
        msg += f"   👤 المستخدم: {acc.get('user_id', '')}\n"
        msg += "   ─────────────\n"
    if total > 10:
        msg += f"\n📌 *ملاحظة:* تم عرض أول 10 حسابات من أصل {total}"
    buttons = [
        ("📥 تصدير الحسابات غير المستخرجة", "export_unextracted"),
        ("✅ وضع علامة مستخرجة", "mark_extracted_menu"),
        ("🔙 جميع الحسابات", "all_accounts_section")
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def mark_extracted_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    unextracted = []
    for uid, user_data in users.items():
        for idx, acc in enumerate(user_data.get("approved_accounts", [])):
            if not acc.get("extracted", False):
                unextracted.append({"user_id": uid, "index": idx, "email": acc.get("email", ""), "acc": acc})
    if not unextracted:
        await query.edit_message_text("✅ لا توجد حسابات غير مستخرجة لتحديدها.",
                                      reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    buttons = []
    for item in unextracted[:10]:
        buttons.append((f"✅ {item['email']}", f"mark_extracted:{item['user_id']}:{item['index']}"))
    buttons.append(("🔙 جميع الحسابات", "all_accounts_section"))
    await query.edit_message_text("✅ *تحديد الحسابات المستخرجة*\nاختر الحسابات التي تم استخراجها:",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def mark_extracted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(uid)
    accounts = user_data.get("approved_accounts", [])
    if index < len(accounts):
        accounts[index]["extracted"] = True
        user_data["approved_accounts"] = accounts
        save_user(uid, user_data)
        await query.edit_message_text(f"✅ تم وضع علامة مستخرجة على الحساب: {accounts[index].get('email', '')}",
                                      reply_markup=kb_single("🔙 الحسابات غير المستخرجة", "unextracted_accounts"))
    else:
        await query.edit_message_text("⚠️ الحساب غير موجود.", reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))


async def export_all_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    all_accounts = []
    for uid, user_data in users.items():
        for acc in user_data.get("approved_accounts", []):
            all_accounts.append(acc)
    if not all_accounts:
        await query.edit_message_text("📭 لا توجد حسابات للتصدير.")
        return
    export_msg = "📊 *جميع الحسابات المقبولة*\n═" * 30 + "\n\n"
    for idx, acc in enumerate(all_accounts, 1):
        leave_status = ""
        if acc.get("approved_with_leave", False) and not acc.get("leave_confirmed", False):
            leave_status = " (معلق)"
        tier_icon = "🟢" if acc.get("has_app_pass", False) else "🟡" if acc.get("has_totp", False) else "🔵"
        export_msg += f"{tier_icon} {idx}. 📧 `{acc.get('email', '')}`{leave_status}\n"
        export_msg += f"🔑 `{acc.get('password', '')}`\n"
        if acc.get("has_totp", False):
            export_msg += f"🔐 `{acc.get('totp_secret') or acc.get('totp', '')}`\n"
        if acc.get("has_app_pass", False):
            formatted_pass = format_app_password(acc.get("app_password") or acc.get("app_pass", ""))
            export_msg += f"🗝 `{formatted_pass}`\n"
        export_msg += f"💰 ${acc.get('amount', 0):.2f}\n"
        export_msg += "─" * 20 + "\n"
    if len(export_msg) > 4000:
        parts = [export_msg[i:i + 4000] for i in range(0, len(export_msg), 4000)]
        for part in parts:
            await context.bot.send_message(chat_id=OWNER_ID, text=part, parse_mode=ParseMode.MARKDOWN)
        await query.edit_message_text("✅ تم تصدير جميع الحسابات في رسائل متعددة.")
    else:
        await query.edit_message_text(export_msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))


async def export_unextracted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    unextracted = []
    for uid, user_data in users.items():
        for acc in user_data.get("approved_accounts", []):
            if not acc.get("extracted", False):
                unextracted.append(acc)
    if not unextracted:
        await query.edit_message_text("✅ لا توجد حسابات غير مستخرجة.")
        return
    export_msg = "🆕 *الحسابات غير المستخرجة*\n═" * 30 + "\n\n"
    for idx, acc in enumerate(unextracted, 1):
        tier_icon = "🟢" if acc.get("has_app_pass", False) else "🟡" if acc.get("has_totp", False) else "🔵"
        export_msg += f"{tier_icon} {idx}. 📧 `{acc.get('email', '')}`\n"
        export_msg += f"🔑 `{acc.get('password', '')}`\n"
        if acc.get("has_totp", False):
            export_msg += f"🔐 `{acc.get('totp_secret') or acc.get('totp', '')}`\n"
        if acc.get("has_app_pass", False):
            formatted_pass = format_app_password(acc.get("app_password") or acc.get("app_pass", ""))
            export_msg += f"🗝 `{formatted_pass}`\n"
        export_msg += "─" * 20 + "\n"
    for uid, user_data in users.items():
        for acc in user_data.get("approved_accounts", []):
            if not acc.get("extracted", False):
                acc["extracted"] = True
        save_user(int(uid), user_data)
    if len(export_msg) > 4000:
        parts = [export_msg[i:i + 4000] for i in range(0, len(export_msg), 4000)]
        for part in parts:
            await context.bot.send_message(chat_id=OWNER_ID, text=part, parse_mode=ParseMode.MARKDOWN)
        await query.edit_message_text("✅ تم تصدير جميع الحسابات غير المستخرجة ووضع علامة مستخرجة عليها.")
    else:
        await query.edit_message_text(export_msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 الحسابات غير المستخرجة", "unextracted_accounts"))


# ==================== PURCHASE CHANNELS ====================
def purchase_channels_keyboard():
    return kb_vertical([
        ("1️⃣ ضبط الكروب الأول", "set_purchase_channel_1"),
        ("2️⃣ ضبط الكروب الثاني", "set_purchase_channel_2"),
        ("🔙 إعدادات المالك", "owner_panel"),
    ])


async def purchase_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    channel_1, channel_2 = get_configured_purchase_channels()
    await query.edit_message_text(
        f"📨 *إعدادات إشعارات الشراء*\n\n1️⃣ الكروب الأول: `{channel_1 or 'غير مضبوط'}`\n2️⃣ الكروب الثاني: `{channel_2 or 'غير مضبوط'}`\n\nأضف البوت إلى الكروبين مع صلاحية إرسال الرسائل، ثم اضبط كل معرف هنا.\nيمكنك استخدام @username أو رقم الكروب مثل -1001234567890.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=purchase_channels_keyboard())


async def set_purchase_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_number: int):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    context.user_data["store_action"] = f"set_purchase_channel_{channel_number}"
    await query.edit_message_text(f"✏️ أرسل معرف الكروب رقم {channel_number} الآن:\n\nمثال: `@my_group` أو `-1001234567890`",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=purchase_channels_keyboard())


# ==================== FORCED CHANNEL ====================
async def forced_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_json(DATA_DIR / "config.json")
    current_channel = config.get("forced_channel", "")
    buttons = [
        ("🗑️ إلغاء القناة", "remove_channel"),
        ("🔙 إعدادات المالك", "owner_panel")
    ]
    await query.edit_message_text(
        f"📢 *إعدادات القناة الإجبارية*\n\n📌 القناة الحالية: {current_channel if current_channel else 'لا توجد'}\n\n✏️ أرسل معرف القناة الجديدة (مثال: @my_channel):",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))
    context.user_data["store_action"] = "set_channel"


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_json(DATA_DIR / "config.json")
    config["forced_channel"] = ""
    save_json(DATA_DIR / "config.json", config)
    await query.edit_message_text("✅ تم إلغاء القناة الإجبارية.", reply_markup=kb_single("🔙 إعدادات المالك", "owner_panel"))


# ==================== USER WITHDRAW STORE ====================
async def withdraw_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    config = load_json(DATA_DIR / "config.json")
    categories = config.get("store_categories", [])
    if not categories:
        await query.edit_message_text("🛒 *قسم السحب*\n\nلا توجد فئات حالياً.",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    buttons = []
    for cat in categories:
        buttons.append((f"📂 {cat['name']}", f"user_category:{cat['id']}"))
    buttons.append(("🔙 القائمة الرئيسية", "main_menu"))
    await query.edit_message_text("🛒 *قسم السحب*\nاختر الفئة:", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def user_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    cat_id = query.data.split(":", 1)[1]
    config = load_json(DATA_DIR / "config.json")
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.", reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        return
    services = category.get("services", [])
    if not services:
        await query.edit_message_text("📭 لا توجد خدمات في هذه الفئة.", reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        return
    buttons = []
    for s in services:
        buttons.append((f"🛒 {s['name']} - ${s['price']:.2f}", f"user_buy:{s['id']}:{cat_id}"))
    buttons.append(("🔙 قسم السحب", "withdraw_store"))
    await query.edit_message_text(f"📂 *{category['name']}*\nاختر الخدمة:", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def user_buy_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    parts = query.data.split(":")
    service_id = parts[1]
    cat_id = parts[2]
    user_id = query.from_user.id
    config = load_json(DATA_DIR / "config.json")
    service = None
    service_name = ""
    service_message = ""
    for cat in config["store_categories"]:
        if cat["id"] == cat_id:
            for s in cat["services"]:
                if s["id"] == service_id:
                    service = s
                    service_name = s.get("name", "")
                    service_message = s.get("message", "شكراً لشرائك الخدمة!")
                    break
            break
    if not service:
        await query.edit_message_text("⚠️ الخدمة غير موجودة.", reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        return
    user_data = get_user(user_id)
    if user_data["balance"] < service["price"]:
        await query.edit_message_text(
            f"❌ رصيدك غير كافٍ. الرصيد: ${user_data['balance']:.2f}, السعر: ${service['price']:.2f}")
        return
    PENDING_PURCHASES[user_id] = {"service_id": service_id, "service_name": service_name,
                                  "service_price": service["price"], "service_message": service_message,
                                  "purchased_at": datetime.now().isoformat()}
    user = update.effective_user
    user_name = user.full_name or "غير معروف"
    user_username = user.username or "لا يوجد"
    total_emails = user_data.get("total_approved_emails", 0)
    bot_username = (await context.bot.get_me()).username
    channel_1_text = f"🛒 <b>طلب شراء جديد</b>\n\n🤖 يوزر البوت: @{html.escape(bot_username or 'غير معروف')}\n📦 الطلب: <code>{html.escape(str(service_name))}</code>\n💰 السعر: <code>${service['price']:.2f}</code>\n📧 عدد الإيميلات: <code>{total_emails}</code>"
    purchase_channel_1, _ = get_configured_purchase_channels()
    notification_channels = (("PURCHASE_CHANNEL_1", purchase_channel_1, channel_1_text, None),)
    for label, channel_id, message_text, reply_markup in notification_channels:
        if not channel_id:
            logger.error("%s غير مضبوط؛ لم يتم إرسال إشعار الشراء.", label)
            continue
        try:
            await context.bot.send_message(chat_id=channel_id, text=message_text, parse_mode=ParseMode.HTML,
                                           reply_markup=reply_markup)
            logger.info("Purchase notification sent to %s (%s).", label, channel_id)
        except Exception:
            logger.exception("Could not send purchase notification to %s (%s).", label, channel_id)
    await query.edit_message_text(
        f"✅ *تم طلب الخدمة بنجاح!*\n\n🛒 *الخدمة:* {service_name}\n💰 *السعر:* ${service['price']:.2f}\n\n📝 *ملاحظة:* {service_message}\n\n_📤 يرجى إرسال المعلومات المطلوبة في رسالة جديدة_\n_🔒 سيتم خصم المبلغ بعد إرسال المعلومات_",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))


async def deliver_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 هذا الزر للمالك فقط.", show_alert=True)
        return
    user_id = int(query.data.split(":")[1])
    user_data = get_user(user_id)
    total_emails = user_data.get("total_approved_emails", 0)
    try:
        await context.bot.send_message(chat_id=user_id,
                                       text=f"📦 *تم استلام طلبك بنجاح!*\n\n✅ تم استلام طلب السحب الخاص بك.\n🕐 سيتم التواصل معك قريباً.\n\n_شكراً لاستخدامك البوت 🤖_",
                                       parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Could not send delivery confirmation to user {user_id}: {e}")
    PENDING_PURCHASES.pop(user_id, None)
    await query.edit_message_text(
        f"✅ *تم إيصال الطلب للمستخدم!*\n\n👤 المستخدم: `{user_id}`\n📧 عدد الإيميلات: `{total_emails}`\n⏰ تم الإيصال: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode=ParseMode.MARKDOWN)
    await query.message.reply_text(f"✅ تم إعلام المستخدم `{user_id}` باستلام طلبه.", parse_mode=ParseMode.MARKDOWN)


# ==================== HANDLE PURCHASE MESSAGE ====================
async def handle_purchase_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in PENDING_PURCHASES:
        await text_input(update, context)
        return
    purchase = PENDING_PURCHASES[user_id]
    user_data = get_user(user_id)
    price = purchase["service_price"]
    if user_data["balance"] < price:
        await update.message.reply_text(
            f"❌ رصيدك غير كافٍ. الرصيد: ${user_data['balance']:.2f}, السعر: ${price:.2f}\nيرجى إعادة المحاولة.",
            reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        PENDING_PURCHASES.pop(user_id, None)
        return
    user_data["balance"] -= price
    user_data["spent_balance"] = round(
        float(user_data.get("spent_balance", 0.0) or 0.0) + price,
        2,
    )
    save_user(user_id, user_data)
    user = update.effective_user
    user_name = user.full_name or "غير معروف"
    user_username = user.username or "لا يوجد"
    total_emails = user_data.get("total_approved_emails", 0)
    _, purchase_channel_2 = get_configured_purchase_channels()
    if purchase_channel_2:
        channel_2_text = f"📋 <b>طلب شراء مكتمل</b>\n\n👤 <b>الاسم:</b> <code>{html.escape(user_name)}</code>\n🆔 <b>اليوزر:</b> @{html.escape(user_username)}\n🆔 <b>المعرف:</b> <code>{user_id}</code>\n📦 <b>الخدمة:</b> <code>{html.escape(str(purchase['service_name']))}</code>\n💰 <b>السعر المخصوم:</b> <code>${price:.2f}</code>\n📧 <b>عدد الإيميلات المقبولة:</b> <code>{total_emails}</code>\n\n📝 <b>رسالة العضو القابلة للنسخ:</b>\n<code>{html.escape(text)}</code>\n⏰ <b>وقت الإرسال:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n───────────────────\n<i>اضغط على الزر أدناه لإعلام المستخدم باستلام طلبه</i>"
        try:
            await context.bot.send_message(chat_id=purchase_channel_2, text=channel_2_text, parse_mode=ParseMode.HTML,
                                           reply_markup=kb_single("✅ تم الإيصال", f"deliver_order:{user_id}"))
            logger.info("Purchase member message sent to PURCHASE_CHANNEL_2 (%s).", purchase_channel_2)
        except Exception:
            logger.exception("Could not send member purchase message to PURCHASE_CHANNEL_2 (%s).", purchase_channel_2)
    else:
        logger.error("PURCHASE_CHANNEL_2 غير مضبوط؛ لم يتم إرسال رسالة العضو.")
    if OWNER_ID:
        try:
            await context.bot.send_message(chat_id=OWNER_ID,
                                           text=f"📩 *رسالة من مستخدم بعد الشراء*\n\n👤 *الاسم:* `{user_name}`\n🆔 *اليوزر:* @{user_username}\n🆔 *المعرف:* `{user_id}`\n📦 *الخدمة:* `{purchase['service_name']}`\n💰 *السعر المخصوم:* `${price:.2f}`\n📧 *عدد الإيميلات:* `{total_emails}`\n\n📝 *رسالة المستخدم:*\n`{text}`\n\n⏰ *وقت الإرسال:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                                           parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Error sending user message to owner: {e}")
    await update.message.reply_text(
        f"✅ *تم استلام رسالتك بنجاح!*\n\n💰 تم خصم `${price:.2f}` من رصيدك.\n📝 رسالتك: `{text}`\n\n_📌 سيتم التواصل معك قريباً من قبل المالك._\n_شكراً لاستخدامك البوت 🤖_",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
    PENDING_PURCHASES.pop(user_id, None)


# ==================== MY WALLET ====================
async def my_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    user = get_user(query.from_user.id)
    await query.edit_message_text(
        f"💰 *أموالي*\n\n⏳ قيد الانتظار (تحقق 24 ساعة): ${float(user.get('pending_balance', 0.0)):.2f}\n⏳ معلق (24 ساعة): ${float(user.get('hold_balance', 0.0)):.2f}\n✅ الرصيد المملوك: ${float(user.get('balance', 0.0)):.2f}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


# ==================== TUTORIALS ====================
async def tutorials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    config = load_json(DATA_DIR / "config.json")
    buttons = []
    if config.get("video_general") and Path(config.get("video_general", "")).exists():
        buttons.append(("📖 شرح عام للبوت", "play_video:general"))
    if config.get("video_email") and Path(config.get("video_email", "")).exists():
        buttons.append(("📹 إنشاء إيميل", "play_video:email"))
    if config.get("video_password") and Path(config.get("video_password", "")).exists():
        buttons.append(("📹 تغيير باسورد", "play_video:password"))
    if config.get("video_totp") and Path(config.get("video_totp", "")).exists():
        buttons.append(("📹 إضافة 2FA", "play_video:totp"))
    if config.get("video_app_pass") and Path(config.get("video_app_pass", "")).exists():
        buttons.append(("📹 كلمة مرور التطبيق", "play_video:app_pass"))
    if config.get("video_leave") and Path(config.get("video_leave", "")).exists():
        buttons.append(("📹 فيديو المغادرة", "play_video:leave"))
    buttons.append(("🔙 القائمة الرئيسية", "main_menu"))
    await query.edit_message_text("📺 *اختر الدرس:*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def play_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    vtype = query.data.split(":")[1]
    config = load_json(DATA_DIR / "config.json")
    path = config.get(f"video_{vtype}")
    video_names = {"general": "شرح عام للبوت", "email": "إنشاء إيميل", "password": "تغيير باسورد", "totp": "إضافة 2FA",
                   "app_pass": "كلمة مرور التطبيق", "leave": "فيديو المغادرة"}
    if path and Path(path).exists():
        try:
            await context.bot.send_video(chat_id=query.from_user.id, video=open(path, "rb"),
                                         caption=f"📹 *فيديو تعليمي: {video_names.get(vtype, vtype)}*",
                                         parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            await tutorials(update, context)
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await query.edit_message_text("⚠️ حدث خطأ في تشغيل الفيديو. حاول مرة أخرى.",
                                          reply_markup=kb_single("🔙 التعليم", "tutorials"))
    else:
        await query.edit_message_text("⚠️ الفيديو غير موجود حالياً.", reply_markup=kb_single("🔙 التعليم", "tutorials"))


# ==================== REFERRAL SYSTEM ====================
async def referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    user_id = update.effective_user.id
    user_data = get_user(user_id)
    referral_code = user_data.get("referral_code", "")
    if not referral_code:
        referral_code = generate_referral_code()
        user_data["referral_code"] = referral_code
        save_user(user_id, user_data)
    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={referral_code}"
    msg = f"🔗 *نظام الإحالة*\n\n📌 *رابط الإحالة الخاص بك:*\n`{referral_link}`\n\n📊 *إحصائياتك:*\n💰 مكافآت الإحالة: ${float(user_data.get('referral_earnings', 0.0)):.2f}\n👥 عدد الإحالات الناجحة: {user_data.get('total_referrals', 0)}\n\n📝 *كيف يعمل النظام؟*\n1️⃣ شارك رابط الإحالة مع أصدقائك\n2️⃣ عند إضافة صديقك لحساب جديد وقبوله من المالك\n3️⃣ ستحصل على مكافأة إحالة لكل حساب مقبول\n4️⃣ كلما زاد عدد الحسابات المقبولة، زادت مكافآتك!"
    buttons = [
        ("📋 نسخ الرابط", f"copy_referral:{referral_code}"),
        ("🔙 القائمة الرئيسية", "main_menu")
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def copy_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    code = query.data.split(":")[1]
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={code}"
    buttons = [
        ("🔗 عرض رابط الإحالة", "referral_menu"),
        ("🔙 القائمة الرئيسية", "main_menu")
    ]
    await query.edit_message_text(f"📋 *رابط الإحالة الخاص بك:*\n\n`{link}`\n\n📌 يمكنك نسخ الرابط ومشاركته مع أصدقائك.",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def referral_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_json(DATA_DIR / "config.json")
    referral_bonus = config.get("referral_bonus", 0.0)
    buttons = [
        ("💲 تغيير مكافأة الإحالة", "set_referral_bonus"),
        ("📊 إحصائيات الإحالة", "referral_stats"),
        ("🔙 إعدادات المالك", "owner_panel")
    ]
    await query.edit_message_text(
        f"🔗 *إعدادات الإحالة*\n\n💰 مكافأة الإحالة الحالية: ${referral_bonus:.2f}\n\n📌 *ملاحظة:* يحصل صاحب الإحالة على هذه المكافأة عند قبول كل حساب جديد من قبل المستخدم المُحال.\n\nاختر الإجراء المناسب:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def set_referral_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text("💰 *تغيير مكافأة الإحالة*\n\nأرسل المبلغ الجديد لمكافأة الإحالة (رقم فقط):\n📌 مثال: 1.5",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إعدادات الإحالة", "referral_settings"))
    context.user_data["mode"] = "set_referral_bonus"


async def referral_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    total_referrals = 0
    total_earnings = 0.0
    top_referrers = []
    for uid, user_data in users.items():
        if user_data.get("total_referrals", 0) > 0:
            total_referrals += user_data["total_referrals"]
            total_earnings += float(user_data.get("referral_earnings", 0.0))
            top_referrers.append(
                {"user_id": uid, "count": user_data["total_referrals"], "earnings": float(user_data.get("referral_earnings", 0.0))})
    top_referrers.sort(key=lambda x: x["count"], reverse=True)
    msg = f"📊 *إحصائيات الإحالة*\n\n👥 إجمالي الإحالات: {total_referrals}\n💰 إجمالي المكافآت المدفوعة: ${total_earnings:.2f}\n\n"
    if top_referrers:
        msg += "🏆 *أفضل المحالين:*\n"
        for idx, ref in enumerate(top_referrers[:5], 1):
            msg += f"{idx}. 👤 {ref['user_id']} - {ref['count']} إحالة - ${ref['earnings']:.2f}\n"
    if not top_referrers:
        msg += "📭 لا توجد إحالات حالياً."
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إعدادات الإحالة", "referral_settings"))


# ==================== OWNER DIRECT VERIFICATION ====================
async def owner_verify_direct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending accounts for owner to verify directly"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await query.answer("🚫 هذا الأمر للمالك فقط!", show_alert=True)
        return
    
    await query.answer()
    
    users = load_json(USERS_DB)
    pending_accounts = []
    
    for uid_str, user_data in users.items():
        uid = int(uid_str)
        for email, account in user_data.get("pending_accounts", {}).items():
            pending_accounts.append({
                "user_id": uid,
                "email": email,
                "account": account,
                "user_name": account.get("user_name", "غير معروف"),
                "user_username": account.get("user_username", "لا يوجد")
            })
    
    if not pending_accounts:
        await query.edit_message_text(
            "📭 *لا توجد حسابات معلقة للتحقق!*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 إعدادات المالك", "owner_panel")
        )
        return
    
    buttons = []
    for idx, acc in enumerate(pending_accounts[:20]):
        email_short = acc['email'][:20] + "..." if len(acc['email']) > 20 else acc['email']
        user_label = f"@{acc['user_username']}" if acc['user_username'] != "لا يوجد" else f"ID:{acc['user_id']}"
        
        has_totp = bool(acc['account'].get('totp_secret'))
        has_app_pass = bool(acc['account'].get('app_password'))
        
        if has_totp and has_app_pass:
            icon = "🔑"
        elif has_totp:
            icon = "🔐"
        else:
            icon = "📦"
        
        buttons.append((
            f"{icon} {email_short} - {user_label}",
            f"owner_verify_confirm:{acc['user_id']}:{acc['email']}"
        ))
    
    buttons.append(("🔙 إعدادات المالك", "owner_panel"))
    
    total = len(pending_accounts)
    msg = (
        f"🔍 *التحقق المباشر للمالك*\n\n"
        f"📋 عدد الحسابات المعلقة: *{total}*\n"
        f"📌 اختر حساباً للتحقق منه فوراً:\n\n"
        f"🔑 كامل | 🔐 TOTP فقط | 📦 إيميل + باسورد"
    )
    
    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_vertical(buttons)
    )


async def owner_verify_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and perform direct verification for an account"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await query.answer("🚫 هذا الأمر للمالك فقط!", show_alert=True)
        return
    
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer("⚠️ خطأ في البيانات", show_alert=True)
        return
    
    target_user_id = int(parts[1])
    email = parts[2]
    
    await query.answer()
    
    user_data = get_user(target_user_id)
    pending = user_data.get("pending_accounts", {})
    account = pending.get(email)
    
    if not account:
        await query.edit_message_text(
            f"⚠️ *الحساب غير موجود!*\n\n📧 الإيميل: `{email}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 التحقق المباشر", "owner_verify_direct")
        )
        return
    
    await query.edit_message_text(
        f"🔍 *جاري التحقق المباشر...*\n\n"
        f"📧 الإيميل: `{email}`\n"
        f"👤 المستخدم: `{target_user_id}`\n"
        f"⏳ يرجى الانتظار...",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Perform verification
    account_data = {
        "email": email,
        "password": account.get("password"),
        "totp_secret": account.get("totp_secret"),
        "app_password": account.get("app_password")
    }
    
    result = await delayed_verifier.verify_after_24h(target_user_id, account_data)
    
    if result["status"] == "verified":
        response = (
            f"✅ *نتيجة التحقق المباشر: نجاح!*\n\n"
            f"📧 الإيميل: `{email}`\n"
            f"👤 المستخدم: `{target_user_id}`\n"
            f"🔑 كلمة المرور: ✅ صحيحة\n"
        )
        
        has_totp = bool(account.get('totp_secret'))
        has_app_pass = bool(account.get('app_password'))
        
        if has_totp:
            response += f"🔐 رمز المصادقة: ✅ صحيح\n"
        if has_app_pass:
            response += f"🗝️ كلمة مرور التطبيق: ✅ صحيحة\n"
        
        response += f"\n💰 المبلغ: *${account.get('amount', 0):.2f}*\n\n📌 *ماذا تريد أن تفعل؟*"
        
        buttons = [
            ("✅ قبول الحساب", f"owner_accept_new:{target_user_id}:{email}"),
            ("❌ رفض الحساب", f"owner_reject_new:{target_user_id}:{email}"),
            ("🔙 التحقق المباشر", "owner_verify_direct")
        ]
        
        await query.edit_message_text(
            response,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_vertical(buttons)
        )
    else:
        reason = result.get("reason", "unknown")
        reason_map = {
            "email_invalid": "الإيميل غير موجود أو غير صالح",
            "account_not_found": "الحساب غير موجود",
            "password_incorrect": "كلمة المرور غير صحيحة",
            "totp_required": "مطلوب رمز مصادقة ولكن لم يتم إرساله",
            "totp_invalid": "رمز المصادقة غير صحيح",
            "app_password_required": "مطلوب كلمة مرور تطبيق ولكن لم يتم إرسالها",
            "app_password_invalid": "كلمة مرور التطبيق غير صحيحة",
            "account_banned": "🚫 الحساب محظور من Telegram",
            "phone_required": "📱 يطلب رقم هاتف للتحقق",
            "login_failed": "فشل تسجيل الدخول",
            "technical_error": "⚠️ خطأ تقني"
        }
        
        response = (
            f"❌ *نتيجة التحقق المباشر: فشل!*\n\n"
            f"📧 الإيميل: `{email}`\n"
            f"👤 المستخدم: `{target_user_id}`\n"
            f"📝 السبب: {reason_map.get(reason, result.get('message', 'سبب غير معروف'))}\n\n"
            f"📌 *ماذا تريد أن تفعل؟*"
        )
        
        buttons = [
            ("❌ رفض الحساب", f"owner_reject_new:{target_user_id}:{email}"),
            ("🔄 إعادة التحقق", f"owner_verify_confirm:{target_user_id}:{email}"),
            ("🔙 التحقق المباشر", "owner_verify_direct")
        ]
        
        await query.edit_message_text(
            response,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_vertical(buttons)
        )


async def owner_accept_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept account from direct verification"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await query.answer("🚫 مالك فقط!", show_alert=True)
        return
    
    parts = query.data.split(":")
    target_user_id = int(parts[1])
    email = parts[2]
    
    await query.answer()
    
    await complete_approval_new(update, context, target_user_id, email, None, False)
    
    await query.edit_message_text(
        f"✅ *تم قبول الحساب بنجاح!*\n\n📧 الإيميل: `{email}`\n👤 المستخدم: `{target_user_id}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 التحقق المباشر", "owner_verify_direct")
    )


async def owner_reject_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject account from direct verification"""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if user_id != OWNER_ID:
        await query.answer("🚫 مالك فقط!", show_alert=True)
        return
    
    parts = query.data.split(":")
    target_user_id = int(parts[1])
    email = parts[2]
    
    await query.answer()
    
    await reject_new_account(update, context)
    
    await query.edit_message_text(
        f"❌ *تم رفض الحساب!*\n\n📧 الإيميل: `{email}`\n👤 المستخدم: `{target_user_id}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 التحقق المباشر", "owner_verify_direct")
    )


# ==================== DELAYED VERIFICATION REPORT ====================
async def delayed_verification_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show delayed verification report to owner"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    await query.answer()
    report = delayed_monitor.get_report()
    
    await query.edit_message_text(
        report,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إعدادات المالك", "owner_panel")
    )


# ==================== PERFORMANCE REPORT ====================
async def performance_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show performance report to owner"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    await query.answer()
    report = advanced_monitor.get_detailed_report()
    
    await query.edit_message_text(
        report,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إعدادات المالك", "owner_panel")
    )


def generate_referral_code():
    return secrets.token_hex(4).upper()


# ==================== TEXT INPUT ====================
async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if user_id in PENDING_PURCHASES:
        await handle_purchase_message(update, context)
        return
        
    if not await check_forced_channel(update, context):
        return
        
    if context.user_data.get("step") == "reject_reason_text":
        await handle_reject_reason_text(update, context)
        return
        
    if context.user_data.get("step") == "deduct_points_input":
        await handle_deduct_points_input(update, context)
        return
        
    if context.user_data.get("step") == "give_points_input":
        await handle_give_points_input(update, context)
        return
        
    if context.user_data.get("step") == "give_points_by_id_input":
        await handle_points_by_id_input(update, context)
        return
        
    if context.user_data.get("step") == "deduct_points_by_id_input":
        await handle_points_by_id_input(update, context)
        return

    if context.user_data.get("step") == "check_member_input":
        await handle_member_check_input(update, context)
        return
    
    if context.user_data.get("approval_step") == "waiting_totp":
        await handle_approval_totp(update, context)
        return
        
    if context.user_data.get("approval_step") == "waiting_app_pass":
        await handle_approval_app_pass(update, context)
        return
        
    if context.user_data.get("mode") == "set_tier_price":
        if user_id != OWNER_ID:
            return
        try:
            price = float(text)
            if price <= 0:
                await update.message.reply_text("⚠️ السعر يجب أن يكون أكبر من 0!")
                return
            tier = context.user_data.get("setting_tier")
            if tier:
                config = load_json(DATA_DIR / "config.json")
                config[f"tier_{tier}_price"] = price
                save_json(DATA_DIR / "config.json", config)
                await update.message.reply_text(f"✅ تم تحديث سعر المستوى {tier} إلى ${price:.2f}")
                context.user_data.pop("mode", None)
                context.user_data.pop("setting_tier", None)
                await set_tier_prices(update, context)
            else:
                await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً صحيحاً (مثال: 0.25)")
        return
        
    if context.user_data.get("mode") == "set_price":
        if user_id != OWNER_ID:
            return
        try:
            price = float(text)
            config = load_json(DATA_DIR / "config.json")
            config["default_price"] = price
            save_json(DATA_DIR / "config.json", config)
            await update.message.reply_text(f"✅ تم تحديث السعر إلى ${price:.2f}")
            context.user_data.pop("mode", None)
            await main_menu(update, context)
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً صحيحاً.")
        return
        
    if context.user_data.get("mode") == "set_referral_bonus":
        if user_id != OWNER_ID:
            return
        try:
            bonus = float(text)
            config = load_json(DATA_DIR / "config.json")
            config["referral_bonus"] = bonus
            save_json(DATA_DIR / "config.json", config)
            await update.message.reply_text(f"✅ تم تحديث مكافأة الإحالة إلى ${bonus:.2f}")
            context.user_data.pop("mode", None)
            await owner_panel(update, context)
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً صحيحاً.")
        return
        
    if context.user_data.get("store_action"):
        await handle_store_input(update, context)
        return
        
    if context.user_data.get("step") == "editing_field":
        await handle_edit_field_input(update, context)
        return
        
    await add_account_step(update, context)


async def handle_store_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    text = update.message.text.strip()
    action = context.user_data.get("store_action")
    if action == "add_category":
        config = load_json(DATA_DIR / "config.json")
        if "store_categories" not in config:
            config["store_categories"] = []
        if any(cat["name"].lower() == text.lower() for cat in config["store_categories"]):
            await update.message.reply_text("⚠️ هذه الفئة موجودة مسبقاً!")
            return
        config["store_categories"].append({"id": str(time.time_ns()), "name": text, "services": []})
        save_json(DATA_DIR / "config.json", config)
        await update.message.reply_text(f"✅ تم إضافة الفئة: {text}")
        context.user_data.pop("store_action", None)
        await main_menu(update, context)
    elif action == "set_channel":
        config = load_json(DATA_DIR / "config.json")
        config["forced_channel"] = normalize_forced_channel(text)
        save_json(DATA_DIR / "config.json", config)
        await update.message.reply_text(f"✅ تم تعيين القناة: {config['forced_channel']}")
        context.user_data.pop("store_action", None)
        await main_menu(update, context)
    elif action in {"set_purchase_channel_1", "set_purchase_channel_2"}:
        channel_id = normalize_chat_id(text)
        if not channel_id or not (channel_id.startswith("@") or channel_id.lstrip("-").isdigit()):
            await update.message.reply_text("⚠️ المعرف غير صحيح. أرسل @username أو رقم الكروب مثل -1001234567890.")
            return
        channel_number = action.rsplit("_", 1)[1]
        config = load_json(DATA_DIR / "config.json")
        config[f"purchase_channel_{channel_number}"] = channel_id
        save_json(DATA_DIR / "config.json", config)
        context.user_data.pop("store_action", None)
        await update.message.reply_text(
            f"✅ تم حفظ الكروب رقم {channel_number}: {channel_id}\n\nتأكد أن البوت موجود في الكروب ولديه صلاحية إرسال الرسائل.",
            reply_markup=purchase_channels_keyboard())
    elif action == "add_service_name":
        context.user_data["store_service_name"] = text
        context.user_data["store_action"] = "add_service_price"
        await update.message.reply_text("💰 *الخطوة 2/3*: أرسل سعر المبيعة (رقم فقط):", parse_mode=ParseMode.MARKDOWN,
                                        reply_markup=kb_single("🔙 إلغاء", f"store_category:{context.user_data.get('current_category_id')}"))
    elif action == "add_service_price":
        try:
            price = float(text)
            if price <= 0:
                await update.message.reply_text("⚠️ السعر يجب أن يكون أكبر من 0!")
                return
            context.user_data["store_service_price"] = price
            context.user_data["store_action"] = "add_service_message"
            await update.message.reply_text(
                "📝 *الخطوة 3/3*: أرسل الرسالة التي ستظهر للعميل بعد الشراء:\n\nمثال: أرسل معرفك في ببجي ليتم إرسال الهدية.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_single("🔙 إلغاء", f"store_category:{context.user_data.get('current_category_id')}"))
        except ValueError:
            await update.message.reply_text("⚠️ يرجى إرسال رقم صحيح (مثال: 10.50)")
    elif action == "add_service_message":
        name = context.user_data.get("store_service_name")
        price = context.user_data.get("store_service_price")
        cat_id = context.user_data.get("current_category_id")
        config = load_json(DATA_DIR / "config.json")
        for cat in config["store_categories"]:
            if cat["id"] == cat_id:
                cat["services"].append({"id": str(time.time_ns()), "name": name, "price": price, "message": text})
                break
        save_json(DATA_DIR / "config.json", config)
        await update.message.reply_text(f"✅ تم إضافة المبيعة بنجاح!\n📌 الاسم: {name}\n💰 السعر: ${price:.2f}\n📝 الرسالة: {text}")
        context.user_data.pop("store_action", None)
        context.user_data.pop("store_service_name", None)
        context.user_data.pop("store_service_price", None)
        context.user_data.pop("current_category_id", None)
        await main_menu(update, context)


# ==================== STORE SECTION ====================
async def owner_store_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_json(DATA_DIR / "config.json")
    categories = config.get("store_categories", [])
    buttons = []
    if categories:
        for cat in categories:
            buttons.append((f"📂 {cat['name']}", f"store_category:{cat['id']}"))
    buttons.append(("➕ إضافة فئة جديدة", "store_add_category"))
    buttons.append(("🔙 إعدادات المالك", "owner_panel"))
    await query.edit_message_text("🛒 *إدارة المبيعات*\n\nاختر فئة لعرض مبيعاتها أو أضف فئة جديدة:",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def store_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text("✏️ *إضافة فئة جديدة*\n\nأرسل اسم الفئة (مثال: حسابات، اشتراكات، أدوات):",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", "store_section"))
    context.user_data["store_action"] = "add_category"


async def store_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    config = load_json(DATA_DIR / "config.json")
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.", reply_markup=kb_single("🔙 المبيعات", "store_section"))
        return
    services = category.get("services", [])
    msg = f"📂 *{category['name']}*\n\n"
    if services:
        for idx, s in enumerate(services, 1):
            msg += f"{idx}. 🛒 {s['name']} - 💰 ${s['price']:.2f}\n"
    else:
        msg += "📭 لا توجد مبيعات في هذه الفئة.\n"
    buttons = [
        ("➕ إضافة مبيعة", f"store_add_service:{cat_id}"),
        ("🗑️ حذف مبيعة", f"store_delete_service:{cat_id}"),
        ("🔙 المبيعات", "store_section")
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def store_add_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    context.user_data["current_category_id"] = cat_id
    context.user_data["store_action"] = "add_service_name"
    await query.edit_message_text("✏️ *إضافة مبيعة جديدة*\n\n📌 الخطوة 1/3: أرسل اسم المبيعة:", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))


async def store_add_service_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    context.user_data["current_category_id"] = cat_id
    context.user_data["store_action"] = "add_service_price"
    await query.edit_message_text("💰 *الخطوة 2/3*: أرسل سعر المبيعة (رقم فقط):", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))


async def store_add_service_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    context.user_data["current_category_id"] = cat_id
    context.user_data["store_action"] = "add_service_message"
    await query.edit_message_text("📝 *الخطوة 3/3*: أرسل الرسالة التي ستظهر للعميل بعد الشراء:\n\nمثال: أرسل معرفك في ببجي ليتم إرسال الهدية.",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))


async def store_delete_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    config = load_json(DATA_DIR / "config.json")
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.")
        return
    services = category.get("services", [])
    if not services:
        await query.edit_message_text("📭 لا توجد مبيعات لحذفها.", reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))
        return
    buttons = []
    for s in services:
        buttons.append((f"❌ {s['name']} - ${s['price']:.2f}", f"delete_service:{cat_id}:{s['id']}"))
    buttons.append(("🔙 الفئة", f"store_category:{cat_id}"))
    await query.edit_message_text("🗑️ *حذف مبيعة*\nاختر المبيعة للحذف:", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def delete_service_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    cat_id = parts[1]
    service_id = parts[2]
    config = load_json(DATA_DIR / "config.json")
    for cat in config.get("store_categories", []):
        if cat["id"] == cat_id:
            cat["services"] = [s for s in cat["services"] if s["id"] != service_id]
            break
    save_json(DATA_DIR / "config.json", config)
    await query.edit_message_text("✅ تم حذف المبيعة بنجاح.", reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))


# ==================== REFERRAL HANDLER ====================
async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE, referral_code: str):
    user_id = update.effective_user.id
    if context.user_data.get("my_referral_code") == referral_code:
        await update.message.reply_text("⚠️ لا يمكنك استخدام رابط الإحالة الخاص بك!")
        return
    user_data = get_user(user_id)
    if user_data.get("referred_by"):
        await update.message.reply_text("ℹ️ أنت بالفعل مشترك في نظام الإحالة.")
        return
    users = load_json(USERS_DB)
    referrer_id = None
    for uid, u_data in users.items():
        if u_data.get("referral_code") == referral_code:
            referrer_id = int(uid)
            break
    if not referrer_id:
        await update.message.reply_text("❌ رابط الإحالة غير صالح.")
        return
    user_data["referred_by"] = referrer_id
    save_user(user_id, user_data)
    await update.message.reply_text(
        f"✅ *تم تفعيل الإحالة بنجاح!*\n\n👤 تمت إحالتك بواسطة: {referrer_id}\n📌 ستتلقى أنت وصاحب الإحالة مكافآت عند قبول حساباتك.\n\nاستخدم /start للبدء.",
        parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.send_message(chat_id=referrer_id,
                                       text=f"🎉 *إحالة جديدة!*\n\n👤 المستخدم {user_id} انضم باستخدام رابط إحالتك.\n📌 ستحصل على مكافأة عند قبول حسابه من قبل المالك.",
                                       parse_mode=ParseMode.MARKDOWN)
    except:
        pass


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args and args[0]:
        referral_code = args[0]
        if len(referral_code) == 8 and referral_code.isalnum():
            context.user_data["referral_code"] = referral_code
            user_data = get_user(update.effective_user.id)
            context.user_data["my_referral_code"] = user_data.get("referral_code", "")
            await handle_referral(update, context, referral_code)
            return
    await main_menu(update, context)


# ==================== ROUTER ====================
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data
    
    if data == "main_menu":
        await main_menu(update, context)
    elif data == "add_account":
        await add_account_start(update, context)
    elif data == "cancel":
        await add_account_cancel(update, context)
    elif data.startswith("submit_tier_1:"):
        await submit_tier_1(update, context)
    elif data.startswith("submit_tier_2:"):
        await submit_tier_2(update, context)
    elif data == "my_wallet":
        await my_wallet(update, context)
    elif data == "my_accounts":
        await my_accounts(update, context)
    elif data == "tutorials":
        await tutorials(update, context)
    elif data.startswith("play_video:"):
        await play_video(update, context)
    elif data.startswith("show_video:"):
        await show_video_in_add(update, context)
    elif data == "owner_panel":
        await owner_panel(update, context)
    elif data == "check_member":
        await check_member(update, context)
    elif data == "set_tier_prices":
        await set_tier_prices(update, context)
    elif data.startswith("set_tier:"):
        await set_tier(update, context)
    elif data == "approval_requests":
        await approval_requests(update, context)
    elif data == "view_pending":
        await view_pending_requests(update, context)
    elif data == "view_approved":
        await view_approved_requests(update, context)
    elif data == "view_rejected":
        await view_rejected_requests(update, context)
    elif data.startswith("pending_detail:"):
        await pending_detail(update, context)
    elif data.startswith("pending_detail_new:"):
        await pending_detail_new(update, context)
    elif data.startswith("approved_detail:"):
        await approved_detail(update, context)
    elif data.startswith("new_totp_code:"):
        await new_totp_code(update, context)
    elif data.startswith("rejected_detail:"):
        await rejected_detail(update, context)
    elif data.startswith("deduct_points:"):
        await deduct_points(update, context)
    elif data.startswith("give_points:"):
        await give_points(update, context)
    elif data == "points_management":
        await points_management(update, context)
    elif data == "give_points_by_id":
        await give_points_by_id(update, context)
    elif data == "deduct_points_by_id":
        await deduct_points_by_id(update, context)
    elif data.startswith("approve_new:"):
        await approve_new_account(update, context)
    elif data.startswith("approve_leave_new:"):
        await approve_new_account(update, context)
    elif data.startswith("reject_new:"):
        await reject_new_account(update, context)
    elif data.startswith("approve_request:"):
        await approve_request_owner(update, context)
    elif data.startswith("approve_with_leave:"):
        await approve_with_leave(update, context)
    elif data.startswith("reject_request:"):
        await reject_request_reason(update, context)
    elif data.startswith("reject_reason:"):
        await execute_reject_reason(update, context)
    elif data == "videos_section":
        await videos_section(update, context)
    elif data.startswith("video_action:"):
        await video_action(update, context)
    elif data.startswith("view_video:"):
        await view_video(update, context)
    elif data.startswith("delete_video:"):
        await delete_video(update, context)
    elif data.startswith("set_video:"):
        await set_video_callback(update, context)
    elif data == "store_section":
        await owner_store_section(update, context)
    elif data == "store_add_category":
        await store_add_category(update, context)
    elif data.startswith("store_category:"):
        await store_category_menu(update, context)
    elif data.startswith("store_add_service:"):
        await store_add_service(update, context)
    elif data.startswith("store_add_service_price:"):
        await store_add_service_price(update, context)
    elif data.startswith("store_add_service_message:"):
        await store_add_service_message(update, context)
    elif data.startswith("store_delete_service:"):
        await store_delete_service(update, context)
    elif data.startswith("delete_service:"):
        await delete_service_execute(update, context)
    elif data == "forced_channel":
        await forced_channel(update, context)
    elif data == "check_forced_channel":
        await check_forced_channel_callback(update, context)
    elif data == "remove_channel":
        await remove_channel(update, context)
    elif data == "purchase_channels":
        await purchase_channels(update, context)
    elif data == "set_purchase_channel_1":
        await set_purchase_channel(update, context, 1)
    elif data == "set_purchase_channel_2":
        await set_purchase_channel(update, context, 2)
    elif data == "withdraw_store":
        await withdraw_store(update, context)
    elif data.startswith("user_category:"):
        await user_category_menu(update, context)
    elif data.startswith("user_buy:"):
        await user_buy_service(update, context)
    elif data.startswith("deliver_order:"):
        await deliver_order(update, context)
    elif data == "all_accounts_section":
        await all_accounts_section(update, context)
    elif data == "owner_stats":
        await owner_stats(update, context)
    elif data == "all_accounts":
        await all_accounts(update, context)
    elif data == "hold_accounts":
        await hold_accounts(update, context)
    elif data == "unextracted_accounts":
        await unextracted_accounts(update, context)
    elif data == "export_all_accounts":
        await export_all_accounts(update, context)
    elif data == "export_unextracted":
        await export_unextracted(update, context)
    elif data == "mark_extracted_menu":
        await mark_extracted_menu(update, context)
    elif data.startswith("mark_extracted:"):
        await mark_extracted(update, context)
    elif data == "referral_menu":
        await referral_menu(update, context)
    elif data.startswith("copy_referral:"):
        await copy_referral(update, context)
    elif data == "referral_settings":
        await referral_settings(update, context)
    elif data == "set_referral_bonus":
        await set_referral_bonus(update, context)
    elif data == "referral_stats":
        await referral_stats(update, context)
    elif data == "edit_my_accounts":
        await edit_my_accounts(update, context)
    elif data.startswith("edit_pending:"):
        await edit_pending_account(update, context)
    elif data.startswith("edit_pending_new:"):
        await edit_pending_new(update, context)
    elif data.startswith("edit_field:"):
        await edit_field(update, context)
    elif data.startswith("edit_field_new:"):
        await edit_field_new(update, context)
    elif data.startswith("delete_pending:"):
        await delete_pending_account(update, context)
    elif data.startswith("delete_pending_new:"):
        await delete_pending_new(update, context)
    elif data == "owner_verify_direct":
        await owner_verify_direct(update, context)
    elif data.startswith("owner_verify_confirm:"):
        await owner_verify_confirm(update, context)
    elif data.startswith("owner_accept_new:"):
        await owner_accept_new(update, context)
    elif data.startswith("owner_reject_new:"):
        await owner_reject_new(update, context)
    elif data == "delayed_verification_report":
        await delayed_verification_report(update, context)
    elif data == "performance_report":
        await performance_report(update, context)
    else:
        await placeholder(update, context)


async def placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("⚠️ خيار غير معروف حالياً.",
                                                  reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


# ==================== DEBUG & OWNER COMMANDS ====================
async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        f"🔎 *تشخيص البوت*\n\n🆔 رقم حسابك: `{user_id}`\n👑 رقم المالك المقروء: `{OWNER_ID}`\n✅ أنت المالك: {'نعم' if user_id == OWNER_ID else 'لا'}\n\nإذا كان رقم المالك 0 أو مختلفاً، عدّل OWNER_TELEGRAM_ID في Railway.",
        parse_mode=ParseMode.MARKDOWN)


async def owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 هذا الأمر للمالك فقط.")
        return
    buttons = [
        ("💰 أسعار المستويات", "set_tier_prices"),
        ("📋 الطلبات", "approval_requests"),
        ("📹 قسم الفيديوهات", "videos_section"),
        ("🛒 المبيعات", "store_section"),
        ("📢 قناة إجبارية", "forced_channel"),
        ("📊 جميع الحسابات المقبولة", "all_accounts_section"),
        ("📈 إحصائيات المستخدمين", "owner_stats"),
        ("🔎 فحص عضو", "check_member"),
        ("🔗 نظام الإحالة", "referral_settings"),
        ("💰 خصم/منح نقاط", "points_management"),
        ("🔍 تحقق مباشر من الحسابات", "owner_verify_direct"),
        ("📊 تقرير التحقق المتأخر", "delayed_verification_report"),
        ("📈 تقرير الأداء", "performance_report"),
        ("🔙 القائمة الرئيسية", "main_menu")
    ]
    await update.message.reply_text("⚙️ *لوحة تحكم المالك*\n\nاختر الإعداد الذي تريد تعديله:",
                                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def store_list_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await owner_store_section(update, context)
    else:
        config = load_json(DATA_DIR / "config.json")
        categories = config.get("store_categories", [])
        if not categories:
            await update.message.reply_text("📭 لا توجد فئات.")
            return
        msg = "📂 *الفئات المتاحة:*\n"
        for cat in categories:
            msg += f"- {cat['name']} ({len(cat.get('services', []))} خدمات)\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


# ==================== RESTORE ====================
async def restore_24h_verifications(application: Application):
    """Restore pending 24h verifications after bot restart"""
    users = load_json(USERS_DB)
    now = datetime.now(timezone.utc)
    
    for user_id_str, user_data in users.items():
        user_id = int(user_id_str)
        
        for email, account in user_data.get("pending_accounts", {}).items():
            if account.get("verification_status") != "pending":
                continue
            
            submitted_at = account.get("submitted_at")
            if not submitted_at:
                continue            
            try:
                submitted = datetime.fromisoformat(submitted_at)
                elapsed = (now - submitted).total_seconds()
                remaining = max(0, 86400 - elapsed)  # 24 hours in seconds
                
                if remaining > 0:
                    application.job_queue.run_once(
                        callback=check_account_after_24h,
                        when=remaining,
                        data={"user_id": user_id, "email": email},
                        name=f"24h_verify_{user_id}_{email}"
                    )
                    logger.info(f"Restored 24h verification for {email} ({remaining:.0f}s remaining)")
                else:
                    # Already passed 24 hours, verify immediately
                    application.job_queue.run_once(
                        callback=check_account_after_24h,
                        when=0,
                        data={"user_id": user_id, "email": email},
                        name=f"24h_verify_{user_id}_{email}"
                    )
                    logger.info(f"Running immediate verification for {email}")
            except Exception as e:
                logger.error(f"Error restoring verification for {email}: {e}")


# ==================== MAIN ====================
async def post_init(application: Application):
    """Restore scheduled jobs after the Telegram application is initialized."""
    await restore_leave_checks(application)
    await restore_24h_verifications(application)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log update errors without stopping the polling loop."""
    logger.error(
        "Unhandled Telegram update error: %s",
        context.error,
        exc_info=context.error,
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not configured. Set BOT_TOKEN before starting the bot."
        )

    retry_delay = 5
    while True:
        try:
            app = (
                Application.builder()
                .token(BOT_TOKEN)
                .post_init(post_init)
                .build()
            )
            app.add_handler(CommandHandler("start", start_command))
            app.add_handler(CommandHandler("debug", debug_command))
            app.add_handler(CommandHandler("owner", owner_command))
            app.add_handler(CallbackQueryHandler(router))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
            app.add_handler(MessageHandler(filters.VIDEO, handle_video_upload))
            app.add_error_handler(error_handler)

            logger.info("Starting Telegram polling.")
            # Keep the event loop available so a transient polling failure can
            # be recovered in this same process instead of leaving the service
            # stopped until a manual redeploy.
            app.run_polling(close_loop=False)
            logger.warning(
                "Telegram polling stopped without an exception; restarting."
            )
        except KeyboardInterrupt:
            logger.info("Shutdown requested.")
            return
        except Exception:
            logger.exception(
                "Telegram polling stopped unexpectedly; retrying in %s seconds.",
                retry_delay,
            )

        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 300)


if __name__ == "__main__":
    main()
