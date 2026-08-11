"""
Advanced Telegram Account Manager Bot - Full Version with All Features
- Owner Panel (Fully Fixed)
- Add Account Flow with Auto-Delete Sensitive Data
- Video System (Upload, Play & Delete)
- Store System (Categories & Services) with Custom Messages
- Wallet & Balance
- Forced Channel
- All Accounts Management
- Referral System (Bonus on Valid Email)
- Account Editing & Deletion
- Duplicate Email Protection
- Video Tutorials in Add Account Flow
- Advanced Approval System with Reasons
- User Accounts with Status (Approved, Rejected, Pending)
- Purchase System with Dual Channel Notifications
- General Bot Tutorial Video
- Leave Video System with Auto-Transfer after 24 Hours
- Tiered Reward System:
  - Email + Password: $0.10
  - Email + Password + TOTP: $0.15
  - Email + Password + TOTP + App Pass: $0.20
- Enhanced Approval: Owner can add missing TOTP and App Pass during approval
- Deduct points from approved accounts
- Give points to rejected accounts
"""

import asyncio
import html
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict
import secrets

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

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "0"))
PURCHASE_CHANNEL_1 = os.environ.get("PURCHASE_CHANNEL_1", "").strip()
PURCHASE_CHANNEL_2 = os.environ.get("PURCHASE_CHANNEL_2", "").strip()

configured_data_dir = os.environ.get("DATA_DIR", "").strip()
railway_volume_dir = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
DATA_DIR = Path(
    configured_data_dir or railway_volume_dir or "/app/data"
).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

def migrate_legacy_data():
    """Copy old data into the persistent directory without overwriting it."""
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
USERS_DB = DATA_DIR / "users.json"
VIDEOS_DIR = DATA_DIR / "videos"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

# ==================== DATA HELPERS ====================
def load_json(path: Path) -> dict:
    backup_path = path.with_name(f"{path.name}.bak")
    for candidate in (path, backup_path):
        if not candidate.exists():
            continue
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not read JSON data from %s.", candidate)
    return {}

def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    backup_path = path.with_name(f"{path.name}.bak")
    payload = json.dumps(data, indent=2, ensure_ascii=False)

    with temporary_path.open("w", encoding="utf-8") as temporary_file:
        temporary_file.write(payload)
        temporary_file.flush()
        os.fsync(temporary_file.fileno())

    if path.exists():
        shutil.copy2(path, backup_path)
    os.replace(temporary_path, path)

def get_user(user_id: int) -> dict:
    users = load_json(DATA_DIR / "users.json")
    return users.get(str(user_id), {
        "balance": 0.0,
        "pending_balance": 0.0,
        "hold_balance": 0.0,
        "approved_accounts": [],
        "pending_requests": [],
        "rejected_emails": [],
        "rejected_requests": [],
        "referral_code": "",
        "referred_by": None,
        "referral_earnings": 0.0,
        "total_referrals": 0,
        "total_approved_emails": 0,
        "pending_purchases": []
    })

def save_user(user_id: int, user_data: dict):
    users = load_json(DATA_DIR / "users.json")
    users[str(user_id)] = user_data
    save_json(DATA_DIR / "users.json", users)

def generate_referral_code():
    """Generate a unique referral code"""
    return secrets.token_hex(4).upper()

def kb(*rows):
    """Build keyboards from both row arguments and a list of rows."""
    if (
        len(rows) == 1
        and isinstance(rows[0], list)
        and rows[0]
        and isinstance(rows[0][0], list)
    ):
        rows = tuple(rows[0])

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(button_text, callback_data=callback_data)
                for button_text, callback_data in row
            ]
            for row in rows
        ]
    )

# ==================== SESSION ====================
@dataclass
class Session:
    step: str = ""
    email: str = ""
    password: str = ""
    totp: str = ""
    app_pass: str = ""
    editing_email: str = ""
    # Track which steps are completed for tiered pricing
    has_password: bool = False
    has_totp: bool = False
    has_app_pass: bool = False

SESSIONS: Dict[int, Session] = {}

PENDING_PURCHASES: Dict[int, Dict] = {}

# ==================== PRICING CONFIG ====================
def get_tier_prices() -> dict:
    """Get tiered prices from config"""
    config = load_json(DATA_DIR / "config.json")
    return {
        "tier_1": float(config.get("tier_1_price", 0.10)),  # Email + Password
        "tier_2": float(config.get("tier_2_price", 0.15)),  # Email + Password + TOTP
        "tier_3": float(config.get("tier_3_price", 0.20)),  # Email + Password + TOTP + App Pass
    }

def calculate_account_price(totp_submitted: bool, app_pass_submitted: bool) -> float:
    """Calculate price based on what information was submitted"""
    prices = get_tier_prices()
    
    if app_pass_submitted and totp_submitted:
        return prices["tier_3"]  # Full price
    elif totp_submitted:
        return prices["tier_2"]  # Email + Password + TOTP
    else:
        return prices["tier_1"]  # Email + Password only

# ==================== COMPLETE APPROVAL ====================
async def complete_approval(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, email: str, approved_request: dict):
    """Complete the approval process"""
    user_data = get_user(uid)
    config = load_json(DATA_DIR / "config.json")
    default_price = float(config.get("default_price", 5.0))
    price = float(approved_request.get("amount", default_price))
    
    approved_request["extracted"] = False
    approved_request["approved_with_leave"] = False
    approved_request["leave_confirmed"] = True
    user_data.setdefault("approved_accounts", []).append(approved_request)
    user_data["pending_balance"] = max(
        0.0,
        float(user_data.get("pending_balance", 0.0)) - price,
    )
    user_data["balance"] = float(user_data.get("balance", 0.0)) + price
    user_data["pending_requests"] = [
        req for req in user_data.get("pending_requests", []) if req["email"] != email
    ]
    
    user_data["total_approved_emails"] = int(user_data.get("total_approved_emails", 0)) + 1
    
    save_user(uid, user_data)

    referred_by = user_data.get("referred_by")
    if referred_by:
        referral_bonus = float(config.get("referral_bonus", 0.0))
        if referral_bonus > 0:
            referrer_data = get_user(referred_by)
            referrer_data["referral_earnings"] = float(referrer_data.get("referral_earnings", 0.0)) + referral_bonus
            referrer_data["total_referrals"] = int(referrer_data.get("total_referrals", 0)) + 1
            save_user(referred_by, referrer_data)
            
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=f"🎉 *مبروك!*\nحصلت على مكافأة إحالة بقيمة ${referral_bonus:.2f}\n"
                         f"بسبب إحالة المستخدم {uid} الذي أضاف حساباً جديداً.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
    
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"✅ *تم قبول طلبك!*\n\n"
                 f"📧 الإيميل: `{email}`\n"
                 f"💰 تم إضافة *${price:.2f}* إلى رصيدك.",
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        pass

    # Clear approval data
    context.user_data.pop("approval_uid", None)
    context.user_data.pop("approval_email", None)
    context.user_data.pop("approval_data", None)
    context.user_data.pop("approval_step", None)

# ==================== FORCED CHANNEL CHECK ====================
def normalize_forced_channel(value: str) -> str:
    """Normalize the channel value accepted from the owner."""
    value = value.strip()
    value = re.sub(r"^https?://t\.me/", "", value, flags=re.IGNORECASE)
    value = value.split("?", 1)[0].split("/", 1)[0].strip()
    if value and not value.startswith("@") and not value.lstrip("-").isdigit():
        value = f"@{value}"
    return value


def get_configured_purchase_channels() -> tuple[str, str]:
    """Read purchase chats at request time so owner changes apply immediately."""
    config = load_json(DATA_DIR / "config.json")
    channel_1 = str(config.get("purchase_channel_1") or PURCHASE_CHANNEL_1).strip()
    channel_2 = str(config.get("purchase_channel_2") or PURCHASE_CHANNEL_2).strip()
    return channel_1, channel_2


def normalize_chat_id(value: str) -> str:
    """Accept @usernames, numeric Telegram IDs, and public t.me links."""
    value = value.strip()
    value = re.sub(r"^https?://t\.me/", "", value, flags=re.IGNORECASE)
    value = value.split("?", 1)[0].split("/", 1)[0].strip()
    if value and not value.startswith("@") and not value.lstrip("-").isdigit():
        value = f"@{value}"
    return value


def forced_channel_link(channel: str, configured_link: str = "") -> str:
    """Build a join link when Telegram can expose a public channel URL."""
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
    """Check if user is in forced channel. Returns True if allowed."""
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
    
    text = (
        f"📢 *يرجى الانضمام إلى القناة أولاً:*\n{forced_channel}\n\n"
        "بعد الانضمام اضغط على زر «تحققت من الاشتراك»."
    )
    reply_markup = forced_channel_keyboard(
        forced_channel,
        str(config.get("forced_channel_link", "")),
    )
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup,
            )
        except Exception:
            await update.callback_query.answer(
                "لم يتم العثور على اشتراكك بعد. انضم إلى القناة ثم أعد المحاولة.",
                show_alert=True,
            )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup,
        )
    return False


async def check_forced_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-check membership after the user joins the forced channel."""
    if await check_forced_channel(update, context):
        await main_menu(update, context)

# ==================== MAIN MENU ====================
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    user = update.effective_user
    rows = [
        [("➕ إضافة حساب", "add_account")],
        [("💰 أموالي", "my_wallet")],
        [("📋 حساباتي", "my_accounts")],
        [("📺 تعليم", "tutorials")],
        [("🛒 سحب", "withdraw_store")],
        [("🔗 الإحالة", "referral_menu")],
        [("✏️ تعديل حساباتي", "edit_my_accounts")],
    ]
    if user.id == OWNER_ID:
        rows.append([("⚙️ إعدادات المالك", "owner_panel")])

    text = "👋 مرحباً بك!\nاختر من القائمة أدناه:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb(*rows))
    else:
        await update.message.reply_text(text, reply_markup=kb(*rows))

# ==================== MY ACCOUNTS ====================
async def my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    user_data = get_user(query.from_user.id)
    
    approved = user_data.get("approved_accounts", [])
    pending = user_data.get("pending_requests", [])
    rejected = user_data.get("rejected_requests", [])
    
    if not approved and not pending and not rejected:
        await query.edit_message_text(
            "📭 لا توجد حسابات لديك.",
            reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
        )
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
    
    if pending:
        msg += "⏳ *منتظرة:*\n"
        for idx, req in enumerate(pending, 1):
            msg += f"  {idx}. 📧 `{req.get('email', '')}` ⏳\n"
        msg += "\n"
    
    if rejected:
        msg += "❌ *مرفوضة:*\n"
        for idx, rej in enumerate(rejected, 1):
            reason = rej.get('reject_reason', 'غير معروف')
            reason_map = {
                "email": "إيميل خطأ",
                "password": "باسورد خطأ",
                "totp": "رمز مصادقة خطأ",
                "app_pass": "كلمة مرور تطبيق خطأ",
                "custom": "سبب مخصص"
            }
            reason_text = reason_map.get(reason, reason)
            msg += f"  {idx}. 📧 `{rej.get('email', '')}` ❌ - {reason_text}\n"
        msg += "\n"
    
    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
    )

# ==================== EDIT MY ACCOUNTS ====================
async def edit_my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    user_data = get_user(query.from_user.id)
    pending = user_data.get("pending_requests", [])
    
    if not pending:
        await query.edit_message_text(
            "📭 لا توجد حسابات جارية للتعديل.",
            reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
        )
        return
    
    rows = []
    for req in pending:
        rows.append([(f"✏️ {req.get('email', '')}", f"edit_pending:{req.get('email', '')}")])
    
    rows.append([("🔙 القائمة الرئيسية", "main_menu")])
    
    await query.edit_message_text(
        "✏️ *تعديل الحسابات الجارية*\nاختر الحساب لتعديله:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

async def edit_pending_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    email = query.data.split(":", 1)[1]
    user_data = get_user(query.from_user.id)
    pending = user_data.get("pending_requests", [])
    
    request = next((r for r in pending if r.get("email") == email), None)
    if not request:
        await query.edit_message_text(
            "⚠️ هذا الحساب غير موجود أو تمت معالجته.",
            reply_markup=kb([("🔙 تعديل حساباتي", "edit_my_accounts")])
        )
        return
    
    context.user_data["editing_email"] = email
    
    await query.edit_message_text(
        f"✏️ *تعديل الحساب:* `{email}`\n\nاختر ما تريد تعديله:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("🔑 تغيير الباسورد", f"edit_field:password:{email}")],
            [("🔐 تغيير رمز المصادقة", f"edit_field:totp:{email}")],
            [("🗝️ تغيير كلمة مرور التطبيق", f"edit_field:app_pass:{email}")],
            [("🗑️ مسح الحساب", f"delete_pending:{email}")],
            [("🔙 تعديل حساباتي", "edit_my_accounts")]
        ])
    )

async def edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    parts = query.data.split(":")
    field = parts[1]
    email = parts[2]
    
    context.user_data["editing_field"] = field
    context.user_data["editing_email"] = email
    
    field_names = {
        "password": "كلمة المرور",
        "totp": "رمز المصادقة الثنائية",
        "app_pass": "كلمة مرور التطبيق"
    }
    
    await query.edit_message_text(
        f"✏️ *تعديل {field_names.get(field, field)}*\n"
        f"للحساب: `{email}`\n\n"
        f"أرسل القيمة الجديدة:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إلغاء", f"edit_pending:{email}")])
    )
    context.user_data["step"] = "editing_field"

async def delete_pending_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    email = query.data.split(":", 1)[1]
    user_data = get_user(query.from_user.id)
    pending = user_data.get("pending_requests", [])
    
    request = next((r for r in pending if r.get("email") == email), None)
    if request:
        pending = [r for r in pending if r.get("email") != email]
        user_data["pending_requests"] = pending
        user_data["pending_balance"] = max(0.0, float(user_data.get("pending_balance", 0.0)) - float(request.get("amount", 0.0)))
        save_user(query.from_user.id, user_data)
        
        await query.edit_message_text(
            f"✅ تم مسح الحساب `{email}` بنجاح.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 تعديل حساباتي", "edit_my_accounts")])
        )
    else:
        await query.edit_message_text(
            "⚠️ الحساب غير موجود.",
            reply_markup=kb([("🔙 تعديل حساباتي", "edit_my_accounts")])
        )

async def handle_edit_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    field = context.user_data.get("editing_field")
    email = context.user_data.get("editing_email")
    
    if not field or not email:
        await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        return
    
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    
    for req in pending:
        if req.get("email") == email:
            req[field] = text
            break
    
    user_data["pending_requests"] = pending
    save_user(uid, user_data)
    
    try:
        await update.message.delete()
    except:
        pass
    
    context.user_data.pop("editing_field", None)
    context.user_data.pop("editing_email", None)
    context.user_data.pop("step", None)
    
    await update.message.reply_text(
        f"✅ تم تحديث {field} بنجاح للحساب `{email}`.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 تعديل حساباتي", "edit_my_accounts")])
    )

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
        f"📝 *إضافة حساب جديد*\n\n"
        f"💵 *نظام المكافآت المتدرج:*\n"
        f"• إيميل + باسورد فقط → ${prices['tier_1']:.2f}\n"
        f"• إيميل + باسورد + رمز مصادقة → ${prices['tier_2']:.2f}\n"
        f"• إيميل + باسورد + رمز مصادقة + كلمة مرور تطبيق → ${prices['tier_3']:.2f}\n\n"
        f"📧 *الخطوة 1/4*: أرسل الإيميل:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([buttons])
    )

async def show_video_in_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    vtype = query.data.split(":")[1]
    config = load_json(DATA_DIR / "config.json")
    path = config.get(f"video_{vtype}")
    if path and Path(path).exists():
        try:
            await context.bot.send_video(
                chat_id=query.from_user.id,
                video=open(path, "rb"),
                caption="📹 *فيديو تعليمي*\nشاهد الفيديو لمعرفة الطريقة الصحيحة.",
                parse_mode=ParseMode.MARKDOWN,
                supports_streaming=True
            )
            await add_account_start(update, context)
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await query.edit_message_text(
                "⚠️ حدث خطأ في تشغيل الفيديو.",
                reply_markup=kb([("🔙 العودة", "add_account")])
            )
    else:
        await query.edit_message_text(
            "⚠️ الفيديو غير متوفر حالياً.",
            reply_markup=kb([("🔙 العودة", "add_account")])
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

    if context.user_data.get("step") == "editing_field":
        await handle_edit_field_input(update, context)
        return

    config = load_json(DATA_DIR / "config.json")
    prices = get_tier_prices()

    if session.step == "email":
        if not re.match(r"[^@]+@[^@]+\.[^@]+", text):
            await update.message.reply_text("❌ إيميل غير صالح.")
            return
        
        user_data = get_user(uid)
        
        for acc in user_data.get("approved_accounts", []):
            if acc.get("email") == text:
                await update.message.reply_text(
                    "❌ هذا الإيميل مقبول مسبقاً! لا يمكنك إعادة إرساله.",
                    reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
                )
                return
        
        for req in user_data.get("pending_requests", []):
            if req.get("email") == text:
                await update.message.reply_text(
                    "⏳ هذا الإيميل قيد الانتظار بالفعل! انتظر موافقة المالك.",
                    reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
                )
                return
        
        rejected_emails = user_data.get("rejected_emails", [])
        if text in rejected_emails:
            rejection_count = sum(1 for email in rejected_emails if email == text)
            if rejection_count >= 3:
                await update.message.reply_text(
                    "🚫 تم رفض هذا الإيميل 3 مرات! لا يمكنك إعادة إرساله مرة أخرى.",
                    reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
                )
                return
            else:
                rejected_emails.remove(text)
                user_data["rejected_emails"] = rejected_emails
                save_user(uid, user_data)
        
        session.email = text
        session.step = "password"
        
        has_password_video = config.get("video_password") and Path(config.get("video_password", "")).exists()
        buttons = []
        if has_password_video:
            buttons.append(("📹 طريقة تغيير الباسورد", "show_video:password"))
        buttons.append(("❌ إلغاء", "cancel"))
        
        await update.message.reply_text(
            f"🔑 *الخطوة 2/4*: أرسل كلمة المرور الأساسية:\n\n"
            f"💰 *السعر الحالي:* ${prices['tier_1']:.2f} (إيميل + باسورد)",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([buttons])
        )

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
            f"🔐 *الخطوة 3/4*: أرسل مفتاح المصادقة (Secret Key):\n\n"
            f"💰 *السعر الحالي:* ${prices['tier_1']:.2f} (إيميل + باسورد)\n"
            f"💰 *السعر مع رمز المصادقة:* ${prices['tier_2']:.2f}\n\n"
            f"📌 *يمكنك استلام {prices['tier_1']:.2f}$ الآن وإكمال الباقي لاحقاً*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([buttons])
        )

    elif session.step == "totp":
        try:
            secret = text.replace(" ", "").upper()
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
                f"✅ مفتاح المصادقة صالح!\n\n"
                f"🔢 *الكود الحالي:* `{code}`\n\n"
                f"🗝 *الخطوة 4/4*: أرسل كلمة مرور التطبيق:\n\n"
                f"💰 *السعر الحالي:* ${prices['tier_2']:.2f} (مع رمز المصادقة)\n"
                f"💰 *السعر الكامل:* ${prices['tier_3']:.2f} (مع كلمة مرور التطبيق)\n\n"
                f"📌 *يمكنك استلام {prices['tier_2']:.2f}$ الآن وإكمال الباقي لاحقاً*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb([buttons])
            )
        except:
            await update.message.reply_text("⚠️ مفتاح 2FA غير صالح.")

    elif session.step == "app_pass":
        session.app_pass = text
        session.has_app_pass = True
        try:
            await update.message.delete()
        except:
            pass
        
        user_data = get_user(uid)
        
        # Calculate price based on what was submitted
        final_price = calculate_account_price(session.has_totp, session.has_app_pass)
        
        user_data["pending_requests"].append({
            "email": session.email,
            "password": session.password,
            "totp": session.totp if session.has_totp else "",
            "app_pass": session.app_pass,
            "amount": final_price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "extracted": False,
            "has_totp": session.has_totp,
            "has_app_pass": session.has_app_pass,
        })
        user_data["pending_balance"] = float(
            user_data.get("pending_balance", 0.0)
        ) + final_price
        save_user(uid, user_data)
        SESSIONS.pop(uid, None)
        
        referred_by = user_data.get("referred_by")
        if referred_by:
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=f"📢 *إشعار إحالة*\n\n"
                         f"المستخدم `{uid}` أضاف إيميل `{session.email}` وهو قيد الانتظار.\n"
                         f"ستحصل على مكافأة عند قبول الإيميل.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
        
        # Send leave video to user
        await send_leave_video_to_user(context, uid, session.email)
        
        tier_text = ""
        if session.has_app_pass and session.has_totp:
            tier_text = "📦 *مكتمل (كامل المعلومات)*"
        elif session.has_totp:
            tier_text = "📦 *ناقص كلمة مرور التطبيق*"
        else:
            tier_text = "📦 *ناقص رمز المصادقة وكلمة مرور التطبيق*"
        
        await update.message.reply_text(
            f"✅ *تم إرسال الطلب للمالك للموافقة!*\n\n"
            f"{tier_text}\n"
            f"💰 تمت إضافة *${final_price:.2f}* إلى الأموال قيد الانتظار.\n\n"
            f"📹 تم إرسال فيديو المغادرة إليك.\n"
            f"⚠️ قم بمغادرة الحساب لتجنب تأخير الدفعة.\n\n"
            f"_🔄 سيتم تحويل المبلغ إلى رصيدك بعد موافقة المالك_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
        )

async def submit_tier_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Submit account with only email + password (Tier 1)"""
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
    
    # Check for duplicate email
    for acc in user_data.get("approved_accounts", []):
        if acc.get("email") == session.email:
            await query.edit_message_text(
                "❌ هذا الإيميل مقبول مسبقاً!",
                reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
            )
            return
    
    for req in user_data.get("pending_requests", []):
        if req.get("email") == session.email:
            await query.edit_message_text(
                "⏳ هذا الإيميل قيد الانتظار بالفعل!",
                reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
            )
            return
    
    user_data["pending_requests"].append({
        "email": session.email,
        "password": session.password,
        "totp": "",
        "app_pass": "",
        "amount": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "extracted": False,
        "has_totp": False,
        "has_app_pass": False,
    })
    user_data["pending_balance"] = float(user_data.get("pending_balance", 0.0)) + price
    save_user(uid, user_data)
    SESSIONS.pop(uid, None)
    
    await query.edit_message_text(
        f"✅ *تم إرسال الطلب للمالك!*\n\n"
        f"📦 *المستوى 1: إيميل + باسورد فقط*\n"
        f"💰 تمت إضافة *${price:.2f}* إلى الأموال قيد الانتظار.\n\n"
        f"_🔄 سيتم تحويل المبلغ إلى رصيدك بعد موافقة المالك_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
    )

async def submit_tier_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Submit account with email + password + TOTP (Tier 2)"""
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
    
    # Check for duplicate email
    for acc in user_data.get("approved_accounts", []):
        if acc.get("email") == session.email:
            await query.edit_message_text(
                "❌ هذا الإيميل مقبول مسبقاً!",
                reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
            )
            return
    
    for req in user_data.get("pending_requests", []):
        if req.get("email") == session.email:
            await query.edit_message_text(
                "⏳ هذا الإيميل قيد الانتظار بالفعل!",
                reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
            )
            return
    
    user_data["pending_requests"].append({
        "email": session.email,
        "password": session.password,
        "totp": session.totp,
        "app_pass": "",
        "amount": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "extracted": False,
        "has_totp": True,
        "has_app_pass": False,
    })
    user_data["pending_balance"] = float(user_data.get("pending_balance", 0.0)) + price
    save_user(uid, user_data)
    SESSIONS.pop(uid, None)
    
    await query.edit_message_text(
        f"✅ *تم إرسال الطلب للمالك!*\n\n"
        f"📦 *المستوى 2: إيميل + باسورد + رمز مصادقة*\n"
        f"💰 تمت إضافة *${price:.2f}* إلى الأموال قيد الانتظار.\n\n"
        f"_🔄 سيتم تحويل المبلغ إلى رصيدك بعد موافقة المالك_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
    )

# ==================== LEAVE VIDEO SYSTEM ====================
async def send_leave_video_to_user(context: ContextTypes.DEFAULT_TYPE, user_id: int, email: str):
    """Send leave video to user with instructions"""
    config = load_json(DATA_DIR / "config.json")
    video_path = config.get("video_leave")
    
    text = (
        f"📹 *فيديو المغادرة*\n\n"
        f"📧 الإيميل: `{email}`\n\n"
        f"⚠️ *تعليمات مهمة:*\n"
        f"• قم بمغادرة الحساب الآن.\n"
        f"• إذا لم تقم بمغادرة الحساب،\n"
        f"• سيتم تأخير دفع المبلغ المستحق لك لمدة 24 ساعة.\n\n"
        f"_شاهد الفيديو لمعرفة طريقة المغادرة الصحيحة_"
    )
    
    if video_path and Path(video_path).exists():
        try:
            await context.bot.send_video(
                chat_id=user_id,
                video=open(video_path, "rb"),
                caption=text,
                parse_mode=ParseMode.MARKDOWN,
                supports_streaming=True
            )
            return True
        except Exception as e:
            logger.error(f"Error sending leave video to user {user_id}: {e}")
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
            return False
    else:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
        return False

async def send_leave_video_reminder(context: ContextTypes.DEFAULT_TYPE, user_id: int, email: str):
    """Send reminder to user after 24 hours"""
    text = (
        f"⏰ *تذكير بمغادرة الحساب*\n\n"
        f"📧 الإيميل: `{email}`\n\n"
        f"⚠️ *لم تقم بمغادرة الحساب بعد!*\n\n"
        f"📌 سيتم إضافة المبلغ إلى رصيدك خلال 24 ساعة أخرى\n"
        f"بغض النظر عن مغادرة الحساب.\n\n"
        f"_يمكنك مشاهدة فيديو المغادرة لتتعلم الطريقة الصحيحة_"
    )
    
    config = load_json(DATA_DIR / "config.json")
    video_path = config.get("video_leave")
    
    if video_path and Path(video_path).exists():
        try:
            await context.bot.send_video(
                chat_id=user_id,
                video=open(video_path, "rb"),
                caption=text,
                parse_mode=ParseMode.MARKDOWN,
                supports_streaming=True
            )
        except Exception as e:
            logger.error(f"Error sending leave video reminder to user {user_id}: {e}")
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass
    else:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass

async def schedule_leave_check(context: ContextTypes.DEFAULT_TYPE, user_id: int, email: str):
    """Schedule leave check after 24 hours"""
    job_name = f"leave_check_{user_id}_{email}"
    
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()
    
    context.job_queue.run_once(
        callback=check_leave_status,
        when=86400,
        data={"user_id": user_id, "email": email},
        name=job_name
    )
    
    logger.info(f"Scheduled auto-transfer for user {user_id}, email {email} in 24 hours")

async def check_leave_status(context: ContextTypes.DEFAULT_TYPE):
    """Check leave status after 24 hours - auto transfer amount"""
    job_data = context.job.data
    user_id = job_data["user_id"]
    email = job_data["email"]
    
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
    
    user_data["hold_balance"] = max(0.0, float(user_data.get("hold_balance", 0.0)) - price)
    user_data["balance"] = float(user_data.get("balance", 0.0)) + price
    
    account["leave_confirmed"] = True
    account["auto_confirmed"] = True
    account["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    
    save_user(user_id, user_data)
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ *تم إضافة المبلغ إلى رصيدك تلقائياً!*\n\n"
                 f"📧 الإيميل: `{email}`\n"
                 f"💰 تم إضافة *${price:.2f}* إلى رصيدك.\n\n"
                 f"🕐 *ملاحظة:* تم التحويل تلقائياً بعد 24 ساعة من موافقة المالك.\n\n"
                 f"_شكراً لاستخدامك البوت 🤖_",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Could not send auto-confirmation to user {user_id}: {e}")
    
    logger.info(f"Auto-confirmed leave for {email}, user {user_id}, amount ${price:.2f}")

# ==================== OWNER PANEL ====================
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    await query.edit_message_text(
        "⚙️ *لوحة تحكم المالك*\n\nاختر الإعداد الذي تريد تعديله:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("💰 أسعار المستويات", "set_tier_prices")],
            [("📋 الطلبات", "approval_requests")],
            [("📹 قسم الفيديوهات", "videos_section")],
            [("🛒 المبيعات", "store_section")],
            [("📢 قناة إجبارية", "forced_channel")],
            [("📨 كروبات إشعارات الشراء", "purchase_channels")],
            [("📊 جميع الحسابات المقبولة", "all_accounts_section")],
            [("🔗 نظام الإحالة", "referral_settings")],
            [("🔙 القائمة الرئيسية", "main_menu")]
        ])
    )

# ==================== OWNER PANEL: TIER PRICES ====================
async def set_tier_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    prices = get_tier_prices()
    
    await query.edit_message_text(
        f"💰 *إعدادات أسعار المستويات*\n\n"
        f"📌 *المستوى 1:* إيميل + باسورد فقط → `${prices['tier_1']:.2f}`\n"
        f"📌 *المستوى 2:* إيميل + باسورد + رمز مصادقة → `${prices['tier_2']:.2f}`\n"
        f"📌 *المستوى 3:* إيميل + باسورد + رمز مصادقة + كلمة مرور تطبيق → `${prices['tier_3']:.2f}`\n\n"
        f"اختر المستوى لتعديل سعره:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("💲 المستوى 1 (باسورد فقط)", "set_tier:1")],
            [("💲 المستوى 2 (مع رمز المصادقة)", "set_tier:2")],
            [("💲 المستوى 3 (كامل)", "set_tier:3")],
            [("🔙 إعدادات المالك", "owner_panel")]
        ])
    )

async def set_tier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    tier = query.data.split(":")[1]
    context.user_data["setting_tier"] = tier
    
    tier_names = {
        "1": "المستوى 1 (إيميل + باسورد فقط)",
        "2": "المستوى 2 (إيميل + باسورد + رمز مصادقة)",
        "3": "المستوى 3 (إيميل + باسورد + رمز مصادقة + كلمة مرور تطبيق)"
    }
    
    await query.edit_message_text(
        f"💰 *تعديل سعر {tier_names[tier]}*\n\n"
        f"أرسل السعر الجديد (رقم فقط):\n"
        f"📌 مثال: 0.25",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إلغاء", "set_tier_prices")])
    )
    context.user_data["mode"] = "set_tier_price"

# ==================== OWNER PANEL: VIDEOS SECTION ====================
async def videos_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    config = load_json(DATA_DIR / "config.json")
    
    video_types = {
        "general": "📖 شرح عام للبوت",
        "email": "📹 فيديو إنشاء إيميل",
        "password": "📹 فيديو تغيير باسورد",
        "totp": "📹 فيديو إضافة 2FA",
        "app_pass": "📹 فيديو كلمة مرور التطبيق",
        "leave": "📹 فيديو المغادرة"
    }
    
    rows = []
    for key, name in video_types.items():
        video_path = config.get(f"video_{key}")
        exists = video_path and Path(video_path).exists()
        status = "✅" if exists else "❌"
        rows.append([(f"{status} {name}", f"video_action:{key}")])
    
    rows.append([("🔙 إعدادات المالك", "owner_panel")])
    
    await query.edit_message_text(
        "📹 *قسم الفيديوهات*\n\n"
        "✅ = فيديو موجود\n"
        "❌ = فيديو غير موجود\n\n"
        "اختر الفيديو لإدارته:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

async def video_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    video_type = query.data.split(":", 1)[1]
    config = load_json(DATA_DIR / "config.json")
    video_path = config.get(f"video_{video_type}")
    exists = video_path and Path(video_path).exists()
    
    video_names = {
        "general": "شرح عام للبوت",
        "email": "إنشاء إيميل",
        "password": "تغيير باسورد",
        "totp": "إضافة 2FA",
        "app_pass": "كلمة مرور التطبيق",
        "leave": "المغادرة"
    }
    
    rows = []
    if exists:
        rows.append([("📹 عرض الفيديو", f"view_video:{video_type}")])
        rows.append([("🗑️ حذف الفيديو", f"delete_video:{video_type}")])
    rows.append([("📤 رفع فيديو جديد", f"set_video:{video_type}")])
    rows.append([("🔙 قسم الفيديوهات", "videos_section")])
    
    status = "✅ موجود" if exists else "❌ غير موجود"
    
    await query.edit_message_text(
        f"📹 *فيديو {video_names.get(video_type, video_type)}*\n\n"
        f"الحالة: {status}\n\n"
        f"اختر الإجراء المناسب:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

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
            await context.bot.send_video(
                chat_id=query.from_user.id,
                video=open(video_path, "rb"),
                caption=f"📹 *فيديو {video_type}*",
                parse_mode=ParseMode.MARKDOWN,
                supports_streaming=True
            )
            await video_action(update, context)
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await query.edit_message_text(
                "⚠️ حدث خطأ في عرض الفيديو.",
                reply_markup=kb([("🔙 قسم الفيديوهات", "videos_section")])
            )
    else:
        await query.edit_message_text(
            "⚠️ الفيديو غير موجود.",
            reply_markup=kb([("🔙 قسم الفيديوهات", "videos_section")])
        )

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
            await query.edit_message_text(
                f"✅ تم حذف فيديو {video_type} بنجاح!",
                reply_markup=kb([("🔙 قسم الفيديوهات", "videos_section")])
            )
        except Exception as e:
            logger.error(f"Error deleting video: {e}")
            await query.edit_message_text(
                "⚠️ حدث خطأ في حذف الفيديو.",
                reply_markup=kb([("🔙 قسم الفيديوهات", "videos_section")])
            )
    else:
        await query.edit_message_text(
            "⚠️ الفيديو غير موجود.",
            reply_markup=kb([("🔙 قسم الفيديوهات", "videos_section")])
        )

async def set_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    context.user_data["pending_video_type"] = video_type
    await query.edit_message_text(
        f"📤 *أرسل الفيديو الخاص بـ {video_type} الآن (كملف فيديو):*\n\n"
        f"📌 سيتم استبدال الفيديو القديم إن وجد.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إلغاء", f"video_action:{video_type}")])
    )

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

    await query.edit_message_text(
        "📋 *الطلبات*\n\nاختر القسم:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("⏳ منتظرة", "view_pending")],
            [("✅ مقبولة", "view_approved")],
            [("❌ مرفوضة", "view_rejected")],
            [("🔙 إعدادات المالك", "owner_panel")]
        ])
    )

# ==================== VIEW PENDING REQUESTS ====================
async def view_pending_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    users = load_json(USERS_DB)
    pending = []
    
    for uid, u_data in users.items():
        for req in u_data.get("pending_requests", []):
            req_copy = req.copy()
            req_copy["user_id"] = uid
            pending.append(req_copy)
    
    if not pending:
        await query.edit_message_text(
            "📭 لا توجد طلبات منتظرة.",
            reply_markup=kb([("🔙 الطلبات", "approval_requests")])
        )
        return
    
    rows = []
    for req in pending:
        tier_icon = "🔵"
        if req.get("has_app_pass", False) and req.get("has_totp", False):
            tier_icon = "🟢"
        elif req.get("has_totp", False):
            tier_icon = "🟡"
        rows.append([(f"{tier_icon} {req.get('email', '')} (${req.get('amount', 0):.2f})", f"pending_detail:{req['user_id']}:{req.get('email', '')}")])
    rows.append([("🔙 الطلبات", "approval_requests")])
    
    await query.edit_message_text(
        "⏳ *الطلبات المنتظرة*\n"
        "🟢 مكتمل | 🟡 مع رمز المصادقة | 🔵 باسورد فقط\n\n"
        "اختر الإيميل لعرض التفاصيل:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

# ==================== PENDING DETAIL ====================
async def pending_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    
    user_data = get_user(uid)
    request = next((req for req in user_data.get("pending_requests", []) if req.get("email") == email), None)
    
    if not request:
        await query.edit_message_text(
            "⚠️ هذا الطلب غير موجود.",
            reply_markup=kb([("🔙 الطلبات المنتظرة", "view_pending")])
        )
        return
    
    tier_icon = "🟢" if request.get("has_app_pass", False) else "🟡" if request.get("has_totp", False) else "🔵"
    tier_text = "مكتمل" if request.get("has_app_pass", False) else "مع رمز المصادقة" if request.get("has_totp", False) else "باسورد فقط"
    
    msg = f"📋 *تفاصيل الطلب*\n\n"
    msg += f"📧 *الإيميل:* `{request.get('email', '')}`\n"
    msg += f"🔑 *الباسورد:* `{request.get('password', '')}`\n"
    if request.get("has_totp", False):
        msg += f"🔐 *رمز المصادقة:* `{request.get('totp', '')}`\n"
    else:
        msg += f"🔐 *رمز المصادقة:* ❌ غير مرسل\n"
    if request.get("has_app_pass", False):
        msg += f"🗝 *كلمة مرور التطبيق:* `{request.get('app_pass', '')}`\n"
    else:
        msg += f"🗝 *كلمة مرور التطبيق:* ❌ غير مرسل\n"
    msg += f"📦 *المستوى:* {tier_icon} {tier_text}\n"
    msg += f"👤 *المستخدم:* `{uid}`\n"
    msg += f"💰 *السعر:* ${request.get('amount', 0):.2f}\n"
    
    config = load_json(DATA_DIR / "config.json")
    has_leave_video = config.get("video_leave") and Path(config.get("video_leave", "")).exists()
    
    buttons = [
        [("✅ قبول فوري", f"approve_request:{uid}:{email}")],
    ]
    
    if has_leave_video:
        buttons.append([("📹 قبول مع فيديو المغادرة", f"approve_with_leave:{uid}:{email}")])
    
    buttons.append([("❌ رفض", f"reject_request:{uid}:{email}")])
    buttons.append([("🔙 الطلبات المنتظرة", "view_pending")])
    
    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*buttons)
    )

# ==================== APPROVE REQUEST (WITH TOTP AND APP PASS PROMPTS) ====================
async def approve_request_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]

    user_data = get_user(uid)

    approved_request = next(
        (req for req in user_data.get("pending_requests", []) if req["email"] == email),
        None,
    )
    if not approved_request:
        await query.edit_message_text(
            "⚠️ هذا الطلب غير موجود أو تمت معالجته مسبقاً.",
            reply_markup=kb([("🔙 الطلبات المنتظرة", "view_pending")]),
        )
        return

    # Check if TOTP is missing - ask for it first
    if not approved_request.get("has_totp", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_email"] = email
        context.user_data["approval_step"] = "waiting_totp"
        context.user_data["approval_data"] = approved_request
        
        await query.edit_message_text(
            f"🔐 *طلب رمز المصادقة*\n\n"
            f"📧 الإيميل: `{email}`\n\n"
            f"⚠️ هذا الحساب ليس لديه رمز مصادقة.\n"
            f"📌 أرسل رمز المصادقة (Secret Key) لإكمال الموافقة:\n\n"
            f"_يمكنك كتابة 'تخطي' لتخطي هذه الخطوة_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 إلغاء", "view_pending")])
        )
        return
    
    # Check if App Pass is missing - ask for it
    if not approved_request.get("has_app_pass", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_email"] = email
        context.user_data["approval_step"] = "waiting_app_pass"
        context.user_data["approval_data"] = approved_request
        
        await query.edit_message_text(
            f"🗝 *طلب كلمة مرور التطبيق*\n\n"
            f"📧 الإيميل: `{email}`\n\n"
            f"⚠️ هذا الحساب ليس لديه كلمة مرور تطبيق.\n"
            f"📌 أرسل كلمة مرور التطبيق لإكمال الموافقة:\n\n"
            f"_يمكنك كتابة 'تخطي' لتخطي هذه الخطوة_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 إلغاء", "view_pending")])
        )
        return

    # Complete approval if both TOTP and App Pass exist
    await complete_approval(update, context, uid, email, approved_request)
    
    await query.edit_message_text(
        f"✅ تم قبول الحساب `{email}` بنجاح!\n"
        f"💰 تم نقل ${approved_request.get('amount', 0):.2f} من قيد الانتظار إلى الرصيد المملوك.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 الطلبات المنتظرة", "view_pending")])
    )

# ==================== APPROVE WITH LEAVE ====================
async def approve_with_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept request with leave video and delay payment 24 hours (auto transfer)"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]

    user_data = get_user(uid)
    config = load_json(DATA_DIR / "config.json")
    default_price = float(config.get("default_price", 5.0))

    approved_request = next(
        (req for req in user_data.get("pending_requests", []) if req["email"] == email),
        None,
    )
    if not approved_request:
        await query.edit_message_text(
            "⚠️ هذا الطلب غير موجود أو تمت معالجته مسبقاً.",
            reply_markup=kb([("🔙 الطلبات المنتظرة", "view_pending")]),
        )
        return

    # Check if TOTP is missing - ask for it first
    if not approved_request.get("has_totp", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_email"] = email
        context.user_data["approval_step"] = "waiting_totp"
        context.user_data["approval_data"] = approved_request
        context.user_data["approval_with_leave"] = True
        
        await query.edit_message_text(
            f"🔐 *طلب رمز المصادقة*\n\n"
            f"📧 الإيميل: `{email}`\n\n"
            f"⚠️ هذا الحساب ليس لديه رمز مصادقة.\n"
            f"📌 أرسل رمز المصادقة (Secret Key) لإكمال الموافقة:\n\n"
            f"_يمكنك كتابة 'تخطي' لتخطي هذه الخطوة_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 إلغاء", "view_pending")])
        )
        return
    
    # Check if App Pass is missing - ask for it
    if not approved_request.get("has_app_pass", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_email"] = email
        context.user_data["approval_step"] = "waiting_app_pass"
        context.user_data["approval_data"] = approved_request
        context.user_data["approval_with_leave"] = True
        
        await query.edit_message_text(
            f"🗝 *طلب كلمة مرور التطبيق*\n\n"
            f"📧 الإيميل: `{email}`\n\n"
            f"⚠️ هذا الحساب ليس لديه كلمة مرور تطبيق.\n"
            f"📌 أرسل كلمة مرور التطبيق لإكمال الموافقة:\n\n"
            f"_يمكنك كتابة 'تخطي' لتخطي هذه الخطوة_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 إلغاء", "view_pending")])
        )
        return

    # Complete approval with leave
    price = float(approved_request.get("amount", default_price))
    
    approved_request["extracted"] = False
    approved_request["leave_confirmed"] = False
    approved_request["leave_sent"] = True
    approved_request["approved_with_leave"] = True
    approved_request["approval_time"] = datetime.now(timezone.utc).isoformat()
    
    user_data.setdefault("approved_accounts", []).append(approved_request)
    
    user_data["pending_balance"] = max(
        0.0,
        float(user_data.get("pending_balance", 0.0)) - price,
    )
    
    user_data["hold_balance"] = float(user_data.get("hold_balance", 0.0)) + price
    
    user_data["pending_requests"] = [
        req for req in user_data.get("pending_requests", []) if req["email"] != email
    ]
    
    save_user(uid, user_data)

    await send_leave_video_to_user(context, uid, email)
    
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"✅ *تم قبول طلبك مع فيديو المغادرة!*\n\n"
                 f"📧 الإيميل: `{email}`\n"
                 f"💰 المبلغ المعلق: *${price:.2f}*\n\n"
                 f"⏰ *سيتم إضافة المبلغ إلى رصيدك تلقائياً بعد 24 ساعة.*\n\n"
                 f"📹 تم إرسال فيديو المغادرة إليك.\n"
                 f"⚠️ قم بمغادرة الحساب لتجنب أي تأخير.\n\n"
                 f"_ستتلقى إشعاراً عند إضافة المبلغ إلى رصيدك_",
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        pass
    
    await schedule_leave_check(context, uid, email)

    context.user_data.pop("approval_uid", None)
    context.user_data.pop("approval_email", None)
    context.user_data.pop("approval_data", None)
    context.user_data.pop("approval_step", None)
    context.user_data.pop("approval_with_leave", None)

    await query.edit_message_text(
        f"✅ تم قبول الحساب `{email}` مع فيديو المغادرة!\n"
        f"💰 المبلغ ${price:.2f} معلق لمدة 24 ساعة.\n"
        f"⏰ سيتم تحويله تلقائياً بعد 24 ساعة.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 الطلبات المنتظرة", "view_pending")])
    )

# ==================== REJECT REQUEST ====================
async def reject_request_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    
    context.user_data["reject_uid"] = uid
    context.user_data["reject_email"] = email
    
    await query.edit_message_text(
        f"❌ *رفض الطلب*\n\n"
        f"📧 الإيميل: `{email}`\n\n"
        f"اختر سبب الرفض:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("📧 إيميل خطأ", f"reject_reason:email:{uid}:{email}")],
            [("🔑 باسورد خطأ", f"reject_reason:password:{uid}:{email}")],
            [("🔐 رمز مصادقة خطأ", f"reject_reason:totp:{uid}:{email}")],
            [("🗝 كلمة مرور تطبيق خطأ", f"reject_reason:app_pass:{uid}:{email}")],
            [("📝 خطأ آخر (اكتب السبب)", f"reject_reason:other:{uid}:{email}")],
            [("🔙 التفاصيل", f"pending_detail:{uid}:{email}")]
        ])
    )

async def execute_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    parts = query.data.split(":")
    reason_type = parts[1]
    uid = int(parts[2])
    email = parts[3]
    
    user_data = get_user(uid)
    
    pending_requests = user_data.get("pending_requests", [])
    request = next((req for req in pending_requests if req.get("email") == email), None)
    
    if not request:
        await query.edit_message_text(
            "⚠️ هذا الطلب غير موجود.",
            reply_markup=kb([("🔙 الطلبات المنتظرة", "view_pending")])
        )
        return
    
    user_data["pending_requests"] = [req for req in pending_requests if req.get("email") != email]
    
    request["reject_reason"] = reason_type
    user_data.setdefault("rejected_requests", []).append(request)
    
    rejected_emails = user_data.get("rejected_emails", [])
    rejected_emails.append(email)
    user_data["rejected_emails"] = rejected_emails
    
    user_data["pending_balance"] = max(0.0, float(user_data.get("pending_balance", 0.0)) - float(request.get("amount", 0.0)))
    
    save_user(uid, user_data)
    
    reason_messages = {
        "email": "❌ الإيميل الذي أرسلته غير صحيح أو غير مقبول.",
        "password": "❌ كلمة المرور التي أرسلتها غير صحيحة.",
        "totp": "❌ رمز المصادقة الثنائية الذي أرسلته غير صحيح.",
        "app_pass": "❌ كلمة مرور التطبيق التي أرسلتها غير صحيحة.",
        "other": "❌ تم رفض طلبك لسبب آخر."
    }
    
    reason = reason_messages.get(reason_type, "❌ تم رفض طلبك.")
    
    config = load_json(DATA_DIR / "config.json")
    
    if reason_type == "email":
        video_path = config.get("video_email")
        if video_path and Path(video_path).exists():
            try:
                await context.bot.send_video(
                    chat_id=uid,
                    video=open(video_path, "rb"),
                    caption=f"{reason}\n\n📹 *شاهد الفيديو لمعرفة الطريقة الصحيحة لإنشاء الإيميل:*",
                    parse_mode=ParseMode.MARKDOWN,
                    supports_streaming=True
                )
            except:
                pass
    elif reason_type == "password":
        video_path = config.get("video_password")
        if video_path and Path(video_path).exists():
            try:
                await context.bot.send_video(
                    chat_id=uid,
                    video=open(video_path, "rb"),
                    caption=f"{reason}\n\n📹 *شاهد الفيديو لمعرفة الطريقة الصحيحة لتغيير الباسورد:*",
                    parse_mode=ParseMode.MARKDOWN,
                    supports_streaming=True
                )
            except:
                pass
    elif reason_type == "totp":
        video_path = config.get("video_totp")
        if video_path and Path(video_path).exists():
            try:
                await context.bot.send_video(
                    chat_id=uid,
                    video=open(video_path, "rb"),
                    caption=f"{reason}\n\n📹 *شاهد الفيديو لمعرفة الطريقة الصحيحة للعثور على رمز المصادقة:*",
                    parse_mode=ParseMode.MARKDOWN,
                    supports_streaming=True
                )
            except:
                pass
    elif reason_type == "app_pass":
        video_path = config.get("video_app_pass")
        if video_path and Path(video_path).exists():
            try:
                await context.bot.send_video(
                    chat_id=uid,
                    video=open(video_path, "rb"),
                    caption=f"{reason}\n\n📹 *شاهد الفيديو لمعرفة الطريقة الصحيحة للحصول على كلمة مرور التطبيق:*",
                    parse_mode=ParseMode.MARKDOWN,
                    supports_streaming=True
                )
            except:
                pass
    else:
        context.user_data["reject_uid"] = uid
        context.user_data["reject_email"] = email
        context.user_data["reject_reason"] = "other"
        
        await query.edit_message_text(
            f"📝 *اكتب سبب الرفض*\n\n"
            f"أرسل رسالة توضح سبب رفض طلب `{email}`:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 إلغاء", f"pending_detail:{uid}:{email}")])
        )
        context.user_data["step"] = "reject_reason_text"
        return
    
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"{reason}\n\n"
                 f"📧 الإيميل: `{email}`\n"
                 f"يمكنك إعادة المحاولة بإرسال إيميل جديد.",
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        pass
    
    await query.edit_message_text(
        f"✅ تم رفض الطلب `{email}` وإرسال السبب للمستخدم.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 الطلبات المنتظرة", "view_pending")])
    )

async def handle_reject_reason_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data.get("reject_uid")
    email = context.user_data.get("reject_email")
    text = update.message.text.strip()
    
    if not uid or not email:
        await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        return
    
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"❌ *تم رفض طلبك*\n\n"
                 f"📧 الإيميل: `{email}`\n"
                 f"📝 السبب: {text}\n\n"
                 f"يمكنك إعادة المحاولة بإرسال إيميل جديد.",
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        pass
    
    context.user_data.pop("reject_uid", None)
    context.user_data.pop("reject_email", None)
    context.user_data.pop("reject_reason", None)
    context.user_data.pop("step", None)
    
    await update.message.reply_text(
        f"✅ تم رفض الطلب `{email}` وإرسال السبب للمستخدم.",
        reply_markup=kb([("🔙 الطلبات المنتظرة", "view_pending")])
    )

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
        await query.edit_message_text(
            "📭 لا توجد طلبات مقبولة.",
            reply_markup=kb([("🔙 الطلبات", "approval_requests")])
        )
        return
    
    rows = []
    for acc in approved[:20]:
        tier_icon = "🟢" if acc.get("has_app_pass", False) else "🟡" if acc.get("has_totp", False) else "🔵"
        email_display = acc.get('email', '')[:20] + "..." if len(acc.get('email', '')) > 20 else acc.get('email', '')
        rows.append([(f"{tier_icon} {email_display} (${acc.get('amount', 0):.2f})", f"approved_detail:{acc['user_id']}:{acc.get('email', '')}:{acc.get('index', 0)}")])
    rows.append([("🔙 الطلبات", "approval_requests")])
    
    await query.edit_message_text(
        f"✅ *الطلبات المقبولة* ({len(approved)})\n\n"
        "🟢 مكتمل | 🟡 مع رمز المصادقة | 🔵 باسورد فقط\n\n"
        "اختر الإيميل لعرض التفاصيل:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

# ==================== APPROVED DETAIL ====================
async def approved_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    index = int(parts[3])
    
    user_data = get_user(uid)
    accounts = user_data.get("approved_accounts", [])
    
    if index >= len(accounts):
        await query.edit_message_text(
            "⚠️ هذا الحساب غير موجود.",
            reply_markup=kb([("🔙 الطلبات المقبولة", "view_approved")])
        )
        return
    
    account = accounts[index]
    
    tier_icon = "🟢" if account.get("has_app_pass", False) else "🟡" if account.get("has_totp", False) else "🔵"
    tier_text = "مكتمل" if account.get("has_app_pass", False) else "مع رمز المصادقة" if account.get("has_totp", False) else "باسورد فقط"
    
    msg = f"📋 *تفاصيل الحساب المقبول*\n\n"
    msg += f"📧 *الإيميل:* `{account.get('email', '')}`\n"
    msg += f"🔑 *الباسورد:* `{account.get('password', '')}`\n"
    if account.get("has_totp", False):
        msg += f"🔐 *رمز المصادقة:* `{account.get('totp', '')}`\n"
    else:
        msg += f"🔐 *رمز المصادقة:* ❌ غير مرسل\n"
    if account.get("has_app_pass", False):
        msg += f"🗝 *كلمة مرور التطبيق:* `{account.get('app_pass', '')}`\n"
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
    
    msg += f"\n📌 *هل تريد خصم نقاط من هذا الحساب؟*"
    
    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("💰 خصم نقاط", f"deduct_points:{uid}:{email}:{index}")],
            [("🔙 الطلبات المقبولة", "view_approved")]
        ])
    )

# ==================== DEDUCT POINTS ====================
async def deduct_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    index = int(parts[3])
    
    context.user_data["deduct_uid"] = uid
    context.user_data["deduct_email"] = email
    context.user_data["deduct_index"] = index
    
    await query.edit_message_text(
        f"💰 *خصم نقاط*\n\n"
        f"📧 الإيميل: `{email}`\n"
        f"👤 المستخدم: `{uid}`\n\n"
        f"📌 أرسل المبلغ المراد خصمه من رصيد المستخدم:\n"
        f"_مثال: 5.00_\n\n"
        f"_أو أرسل 'إلغاء' للإلغاء_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إلغاء", f"approved_detail:{uid}:{email}:{index}")])
    )
    context.user_data["step"] = "deduct_points_input"

# ==================== HANDLE DEDUCT POINTS INPUT ====================
async def handle_deduct_points_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if text.lower() == "الغاء":
        context.user_data.pop("deduct_uid", None)
        context.user_data.pop("deduct_email", None)
        context.user_data.pop("deduct_index", None)
        context.user_data.pop("step", None)
        await update.message.reply_text(
            "❌ تم إلغاء عملية الخصم.",
            reply_markup=kb([("🔙 الطلبات المقبولة", "view_approved")])
        )
        return
    
    try:
        amount = float(text)
        if amount <= 0:
            await update.message.reply_text("⚠️ المبلغ يجب أن يكون أكبر من 0!")
            return
        
        uid = context.user_data.get("deduct_uid")
        email = context.user_data.get("deduct_email")
        index = context.user_data.get("deduct_index")
        
        if not uid or not email or index is None:
            await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
            return
        
        user_data = get_user(uid)
        accounts = user_data.get("approved_accounts", [])
        
        if index >= len(accounts):
            await update.message.reply_text("⚠️ الحساب غير موجود.")
            return
        
        current_balance = float(user_data.get("balance", 0.0))
        
        if current_balance < amount:
            await update.message.reply_text(
                f"⚠️ رصيد المستخدم غير كافٍ!\n"
                f"💰 الرصيد الحالي: ${current_balance:.2f}\n"
                f"💰 المبلغ المطلوب خصمه: ${amount:.2f}"
            )
            return
        
        user_data["balance"] = current_balance - amount
        save_user(uid, user_data)
        
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"💰 *تم خصم نقاط من رصيدك!*\n\n"
                     f"📧 الإيميل: `{email}`\n"
                     f"💰 المبلغ المخصوم: *${amount:.2f}*\n"
                     f"💰 الرصيد المتبقي: *${user_data['balance']:.2f}*\n\n"
                     f"_لمزيد من المعلومات، تواصل مع المالك_",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
        
        context.user_data.pop("deduct_uid", None)
        context.user_data.pop("deduct_email", None)
        context.user_data.pop("deduct_index", None)
        context.user_data.pop("step", None)
        
        await update.message.reply_text(
            f"✅ تم خصم ${amount:.2f} من رصيد المستخدم `{uid}` بنجاح!\n"
            f"📧 الإيميل: `{email}`\n"
            f"💰 الرصيد المتبقي: ${user_data['balance']:.2f}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 الطلبات المقبولة", "view_approved")])
        )
        
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
        await query.edit_message_text(
            "📭 لا توجد طلبات مرفوضة.",
            reply_markup=kb([("🔙 الطلبات", "approval_requests")])
        )
        return
    
    rows = []
    for rej in rejected[:20]:
        reason = rej.get('reject_reason', 'غير معروف')
        reason_map = {
            "email": "📧",
            "password": "🔑",
            "totp": "🔐",
            "app_pass": "🗝",
            "other": "📝"
        }
        icon = reason_map.get(reason, "❌")
        email_display = rej.get('email', '')[:20] + "..." if len(rej.get('email', '')) > 20 else rej.get('email', '')
        rows.append([(f"{icon} {email_display}", f"rejected_detail:{rej['user_id']}:{rej.get('email', '')}:{rej.get('index', 0)}")])
    rows.append([("🔙 الطلبات", "approval_requests")])
    
    await query.edit_message_text(
        f"❌ *الطلبات المرفوضة* ({len(rejected)})\n\n"
        "📧 إيميل خطأ | 🔑 باسورد خطأ | 🔐 رمز مصادقة خطأ | 🗝 كلمة مرور تطبيق خطأ | 📝 سبب مخصص\n\n"
        "اختر الإيميل لعرض التفاصيل:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

# ==================== REJECTED DETAIL ====================
async def rejected_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    index = int(parts[3])
    
    user_data = get_user(uid)
    rejected_list = user_data.get("rejected_requests", [])
    
    if index >= len(rejected_list):
        await query.edit_message_text(
            "⚠️ هذا الطلب غير موجود.",
            reply_markup=kb([("🔙 الطلبات المرفوضة", "view_rejected")])
        )
        return
    
    request = rejected_list[index]
    
    reason = request.get('reject_reason', 'غير معروف')
    reason_map = {
        "email": "❌ الإيميل غير صحيح أو غير مقبول.",
        "password": "❌ كلمة المرور غير صحيحة.",
        "totp": "❌ رمز المصادقة غير صحيح.",
        "app_pass": "❌ كلمة مرور التطبيق غير صحيحة.",
        "other": "❌ تم رفض الطلب لسبب آخر.",
        "custom": "❌ سبب مخصص."
    }
    reason_text = reason_map.get(reason, reason)
    
    tier_icon = "🟢" if request.get("has_app_pass", False) else "🟡" if request.get("has_totp", False) else "🔵"
    tier_text = "مكتمل" if request.get("has_app_pass", False) else "مع رمز المصادقة" if request.get("has_totp", False) else "باسورد فقط"
    
    msg = f"📋 *تفاصيل الطلب المرفوض*\n\n"
    msg += f"📧 *الإيميل:* `{request.get('email', '')}`\n"
    msg += f"🔑 *الباسورد:* `{request.get('password', '')}`\n"
    if request.get("has_totp", False):
        msg += f"🔐 *رمز المصادقة:* `{request.get('totp', '')}`\n"
    else:
        msg += f"🔐 *رمز المصادقة:* ❌ غير مرسل\n"
    if request.get("has_app_pass", False):
        msg += f"🗝 *كلمة مرور التطبيق:* `{request.get('app_pass', '')}`\n"
    else:
        msg += f"🗝 *كلمة مرور التطبيق:* ❌ غير مرسل\n"
    msg += f"📦 *المستوى:* {tier_icon} {tier_text}\n"
    msg += f"👤 *المستخدم:* `{uid}`\n"
    msg += f"💰 *السعر:* ${request.get('amount', 0):.2f}\n"
    msg += f"📝 *سبب الرفض:* {reason_text}\n\n"
    
    msg += f"📌 *هل تريد إعطاء نقاط للمستخدم رغم الرفض؟*"
    
    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("💰 إعطاء نقاط", f"give_points:{uid}:{email}:{index}")],
            [("🔙 الطلبات المرفوضة", "view_rejected")]
        ])
    )

# ==================== GIVE POINTS ====================
async def give_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    index = int(parts[3])
    
    context.user_data["give_uid"] = uid
    context.user_data["give_email"] = email
    context.user_data["give_index"] = index
    
    await query.edit_message_text(
        f"💰 *إعطاء نقاط للمستخدم*\n\n"
        f"📧 الإيميل المرفوض: `{email}`\n"
        f"👤 المستخدم: `{uid}`\n\n"
        f"📌 أرسل المبلغ المراد إضافته إلى رصيد المستخدم:\n"
        f"_مثال: 2.50_\n\n"
        f"_أو أرسل 'إلغاء' للإلغاء_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إلغاء", f"rejected_detail:{uid}:{email}:{index}")])
    )
    context.user_data["step"] = "give_points_input"

# ==================== HANDLE GIVE POINTS INPUT ====================
async def handle_give_points_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if text.lower() == "الغاء":
        context.user_data.pop("give_uid", None)
        context.user_data.pop("give_email", None)
        context.user_data.pop("give_index", None)
        context.user_data.pop("step", None)
        await update.message.reply_text(
            "❌ تم إلغاء عملية إعطاء النقاط.",
            reply_markup=kb([("🔙 الطلبات المرفوضة", "view_rejected")])
        )
        return
    
    try:
        amount = float(text)
        if amount <= 0:
            await update.message.reply_text("⚠️ المبلغ يجب أن يكون أكبر من 0!")
            return
        
        uid = context.user_data.get("give_uid")
        email = context.user_data.get("give_email")
        index = context.user_data.get("give_index")
        
        if not uid or not email or index is None:
            await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
            return
        
        user_data = get_user(uid)
        user_data["balance"] = float(user_data.get("balance", 0.0)) + amount
        save_user(uid, user_data)
        
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"💰 *تم إضافة نقاط إلى رصيدك!*\n\n"
                     f"📧 الإيميل: `{email}`\n"
                     f"💰 المبلغ المضاف: *+${amount:.2f}*\n"
                     f"💰 الرصيد الجديد: *${user_data['balance']:.2f}*\n\n"
                     f"_تم إضافة هذه النقاط كتعويض عن طلبك المرفوض._",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
        
        context.user_data.pop("give_uid", None)
        context.user_data.pop("give_email", None)
        context.user_data.pop("give_index", None)
        context.user_data.pop("step", None)
        
        await update.message.reply_text(
            f"✅ تم إضافة ${amount:.2f} إلى رصيد المستخدم `{uid}` بنجاح!\n"
            f"📧 الإيميل المرفوض: `{email}`\n"
            f"💰 الرصيد الجديد: ${user_data['balance']:.2f}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 الطلبات المرفوضة", "view_rejected")])
        )
        
    except ValueError:
        await update.message.reply_text("⚠️ أرسل رقماً صحيحاً (مثال: 2.50)")

# ==================== HANDLE APPROVAL TOTP ====================
async def handle_approval_totp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data.get("approval_uid")
    email = context.user_data.get("approval_email")
    approved_request = context.user_data.get("approval_data")
    
    if not uid or not email or not approved_request:
        await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        return
    
    if text.lower() == "تخطي":
        # Skip TOTP
        context.user_data["approval_step"] = "waiting_app_pass"
        await update.message.reply_text(
            f"🗝 *طلب كلمة مرور التطبيق*\n\n"
            f"📧 الإيميل: `{email}`\n\n"
            f"⚠️ تم تخطي رمز المصادقة.\n"
            f"📌 أرسل كلمة مرور التطبيق لإكمال الموافقة:\n\n"
            f"_يمكنك كتابة 'تخطي' لتخطي هذه الخطوة أيضاً_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 إلغاء", "view_pending")])
        )
        return
    
    try:
        secret = text.replace(" ", "").upper()
        code = pyotp.TOTP(secret).now()
        
        # Update request with TOTP
        approved_request["totp"] = secret
        approved_request["has_totp"] = True
        
        # Recalculate price
        prices = get_tier_prices()
        if approved_request.get("has_app_pass", False):
            approved_request["amount"] = prices["tier_3"]
        else:
            approved_request["amount"] = prices["tier_2"]
        
        context.user_data["approval_data"] = approved_request
        context.user_data["approval_step"] = "waiting_app_pass"
        
        await update.message.reply_text(
            f"✅ رمز المصادقة صالح!\n"
            f"🔢 الكود الحالي: `{code}`\n\n"
            f"🗝 *الآن أرسل كلمة مرور التطبيق:*\n\n"
            f"_يمكنك كتابة 'تخطي' لتخطي هذه الخطوة_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 إلغاء", "view_pending")])
        )
        
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ مفتاح 2FA غير صالح: {str(e)}\n\n"
            f"📌 أرسل رمز المصادقة الصحيح أو اكتب 'تخطي' لتخطي هذه الخطوة.",
            parse_mode=ParseMode.MARKDOWN
        )

# ==================== HANDLE APPROVAL APP PASS ====================
async def handle_approval_app_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data.get("approval_uid")
    email = context.user_data.get("approval_email")
    approved_request = context.user_data.get("approval_data")
    with_leave = context.user_data.get("approval_with_leave", False)
    
    if not uid or not email or not approved_request:
        await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        return
    
    if text.lower() == "تخطي":
        # Skip App Pass
        approved_request["app_pass"] = ""
        approved_request["has_app_pass"] = False
        
        # Recalculate price
        prices = get_tier_prices()
        if approved_request.get("has_totp", False):
            approved_request["amount"] = prices["tier_2"]
        else:
            approved_request["amount"] = prices["tier_1"]
        
        context.user_data["approval_data"] = approved_request
        
        await update.message.reply_text(
            f"✅ تم تخطي كلمة مرور التطبيق.\n\n"
            f"📌 سيتم إكمال الموافقة على الحساب `{email}`",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Complete approval with or without leave
        if with_leave:
            await complete_approval_with_leave(update, context, uid, email, approved_request)
        else:
            await complete_approval(update, context, uid, email, approved_request)
        return
    
    # Set App Pass
    approved_request["app_pass"] = text
    approved_request["has_app_pass"] = True
    
    # Recalculate price
    prices = get_tier_prices()
    if approved_request.get("has_totp", False):
        approved_request["amount"] = prices["tier_3"]
    else:
        approved_request["amount"] = prices["tier_2"]
    
    context.user_data["approval_data"] = approved_request
    
    await update.message.reply_text(
        f"✅ تم استلام كلمة مرور التطبيق.\n\n"
        f"📌 سيتم إكمال الموافقة على الحساب `{email}`",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Complete approval with or without leave
    if with_leave:
        await complete_approval_with_leave(update, context, uid, email, approved_request)
    else:
        await complete_approval(update, context, uid, email, approved_request)

# ==================== COMPLETE APPROVAL WITH LEAVE ====================
async def complete_approval_with_leave(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, email: str, approved_request: dict):
    """Complete approval with leave video (hold balance for 24 hours)"""
    user_data = get_user(uid)
    config = load_json(DATA_DIR / "config.json")
    default_price = float(config.get("default_price", 5.0))
    price = float(approved_request.get("amount", default_price))
    
    approved_request["extracted"] = False
    approved_request["leave_confirmed"] = False
    approved_request["leave_sent"] = True
    approved_request["approved_with_leave"] = True
    approved_request["approval_time"] = datetime.now(timezone.utc).isoformat()
    
    user_data.setdefault("approved_accounts", []).append(approved_request)
    
    user_data["pending_balance"] = max(
        0.0,
        float(user_data.get("pending_balance", 0.0)) - price,
    )
    
    user_data["hold_balance"] = float(user_data.get("hold_balance", 0.0)) + price
    
    user_data["pending_requests"] = [
        req for req in user_data.get("pending_requests", []) if req["email"] != email
    ]
    
    save_user(uid, user_data)

    await send_leave_video_to_user(context, uid, email)
    
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"✅ *تم قبول طلبك مع فيديو المغادرة!*\n\n"
                 f"📧 الإيميل: `{email}`\n"
                 f"💰 المبلغ المعلق: *${price:.2f}*\n\n"
                 f"⏰ *سيتم إضافة المبلغ إلى رصيدك تلقائياً بعد 24 ساعة.*\n\n"
                 f"📹 تم إرسال فيديو المغادرة إليك.\n"
                 f"⚠️ قم بمغادرة الحساب لتجنب أي تأخير.\n\n"
                 f"_ستتلقى إشعاراً عند إضافة المبلغ إلى رصيدك_",
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        pass
    
    await schedule_leave_check(context, uid, email)

    context.user_data.pop("approval_uid", None)
    context.user_data.pop("approval_email", None)
    context.user_data.pop("approval_data", None)
    context.user_data.pop("approval_step", None)
    context.user_data.pop("approval_with_leave", None)

# ==================== ALL ACCOUNTS SECTION ====================
async def all_accounts_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    await query.edit_message_text(
        "📊 *جميع الحسابات المقبولة*\n\nاختر الخيار المناسب:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("📋 جميع الحسابات", "all_accounts")],
            [("🆕 آخر الحسابات (غير المستخرجة)", "unextracted_accounts")],
            [("⏳ الحسابات المعلقة (24 ساعة)", "hold_accounts")],
            [("🔙 إعدادات المالك", "owner_panel")]
        ])
    )

async def hold_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View pending accounts (waiting for auto transfer after 24 hours)"""
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
        await query.edit_message_text(
            "✅ لا توجد حسابات معلقة حالياً.",
            reply_markup=kb([("🔙 جميع الحسابات", "all_accounts_section")])
        )
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

    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 جميع الحسابات", "all_accounts_section")])
    )

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
        await query.edit_message_text(
            "📭 لا توجد حسابات مقبولة حالياً.",
            reply_markup=kb([("🔙 جميع الحسابات", "all_accounts_section")])
        )
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
            msg += f"   🔐 `{acc.get('totp', '')}`\n"
        if acc.get("has_app_pass", False):
            msg += f"   🗝 `{acc.get('app_pass', '')}`\n"
        msg += f"   👤 المستخدم: {acc.get('user_id', '')}\n"
        msg += f"   💰 السعر: ${acc.get('amount', 0):.2f}\n"
        msg += "   ─────────────\n"
    
    if total > 10:
        msg += f"\n📌 *ملاحظة:* تم عرض أول 10 حسابات من أصل {total}"
        msg += "\nلتصدير جميع الحسابات استخدم زر التصدير أدناه"

    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("📥 تصدير جميع الحسابات", "export_all_accounts")],
            [("🆕 عرض الحسابات غير المستخرجة", "unextracted_accounts")],
            [("⏳ الحسابات المعلقة", "hold_accounts")],
            [("🔙 جميع الحسابات", "all_accounts_section")]
        ])
    )

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
        await query.edit_message_text(
            "✅ لا توجد حسابات غير مستخرجة.",
            reply_markup=kb([("🔙 جميع الحسابات", "all_accounts_section")])
        )
        return

    total = len(unextracted)
    msg = f"🆕 *الحسابات غير المستخرجة: {total}*\n\n"
    
    for idx, acc in enumerate(unextracted[:10], 1):
        tier_icon = "🟢" if acc.get("has_app_pass", False) else "🟡" if acc.get("has_totp", False) else "🔵"
        msg += f"{idx}. {tier_icon} 📧 `{acc.get('email', '')}`\n"
        msg += f"   🔑 `{acc.get('password', '')}`\n"
        if acc.get("has_totp", False):
            msg += f"   🔐 `{acc.get('totp', '')}`\n"
        if acc.get("has_app_pass", False):
            msg += f"   🗝 `{acc.get('app_pass', '')}`\n"
        msg += f"   👤 المستخدم: {acc.get('user_id', '')}\n"
        msg += "   ─────────────\n"
    
    if total > 10:
        msg += f"\n📌 *ملاحظة:* تم عرض أول 10 حسابات من أصل {total}"

    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("📥 تصدير الحسابات غير المستخرجة", "export_unextracted")],
            [("✅ وضع علامة مستخرجة", "mark_extracted_menu")],
            [("🔙 جميع الحسابات", "all_accounts_section")]
        ])
    )

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
                unextracted.append({
                    "user_id": uid,
                    "index": idx,
                    "email": acc.get("email", ""),
                    "acc": acc
                })
    
    if not unextracted:
        await query.edit_message_text(
            "✅ لا توجد حسابات غير مستخرجة لتحديدها.",
            reply_markup=kb([("🔙 جميع الحسابات", "all_accounts_section")])
        )
        return

    rows = []
    for item in unextracted[:10]:
        rows.append([(f"✅ {item['email']}", f"mark_extracted:{item['user_id']}:{item['index']}")])
    
    rows.append([("🔙 جميع الحسابات", "all_accounts_section")])
    
    await query.edit_message_text(
        "✅ *تحديد الحسابات المستخرجة*\nاختر الحسابات التي تم استخراجها:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

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
        await query.edit_message_text(
            f"✅ تم وضع علامة مستخرجة على الحساب: {accounts[index].get('email', '')}",
            reply_markup=kb([("🔙 الحسابات غير المستخرجة", "unextracted_accounts")])
        )
    else:
        await query.edit_message_text(
            "⚠️ الحساب غير موجود.",
            reply_markup=kb([("🔙 جميع الحسابات", "all_accounts_section")])
        )

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

    export_msg = "📊 *جميع الحسابات المقبولة*\n"
    export_msg += "═" * 30 + "\n\n"
    
    for idx, acc in enumerate(all_accounts, 1):
        leave_status = ""
        if acc.get("approved_with_leave", False) and not acc.get("leave_confirmed", False):
            leave_status = " (معلق)"
        tier_icon = "🟢" if acc.get("has_app_pass", False) else "🟡" if acc.get("has_totp", False) else "🔵"
        export_msg += f"{tier_icon} {idx}. 📧 `{acc.get('email', '')}`{leave_status}\n"
        export_msg += f"🔑 `{acc.get('password', '')}`\n"
        if acc.get("has_totp", False):
            export_msg += f"🔐 `{acc.get('totp', '')}`\n"
        if acc.get("has_app_pass", False):
            export_msg += f"🗝 `{acc.get('app_pass', '')}`\n"
        export_msg += f"💰 ${acc.get('amount', 0):.2f}\n"
        export_msg += "─" * 20 + "\n"

    if len(export_msg) > 4000:
        parts = [export_msg[i:i+4000] for i in range(0, len(export_msg), 4000)]
        for part in parts:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=part,
                parse_mode=ParseMode.MARKDOWN
            )
        await query.edit_message_text("✅ تم تصدير جميع الحسابات في رسائل متعددة.")
    else:
        await query.edit_message_text(
            export_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 جميع الحسابات", "all_accounts_section")])
        )

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

    export_msg = "🆕 *الحسابات غير المستخرجة*\n"
    export_msg += "═" * 30 + "\n\n"
    
    for idx, acc in enumerate(unextracted, 1):
        tier_icon = "🟢" if acc.get("has_app_pass", False) else "🟡" if acc.get("has_totp", False) else "🔵"
        export_msg += f"{tier_icon} {idx}. 📧 `{acc.get('email', '')}`\n"
        export_msg += f"🔑 `{acc.get('password', '')}`\n"
        if acc.get("has_totp", False):
            export_msg += f"🔐 `{acc.get('totp', '')}`\n"
        if acc.get("has_app_pass", False):
            export_msg += f"🗝 `{acc.get('app_pass', '')}`\n"
        export_msg += "─" * 20 + "\n"

    for uid, user_data in users.items():
        for acc in user_data.get("approved_accounts", []):
            if not acc.get("extracted", False):
                acc["extracted"] = True
        save_user(int(uid), user_data)

    if len(export_msg) > 4000:
        parts = [export_msg[i:i+4000] for i in range(0, len(export_msg), 4000)]
        for part in parts:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=part,
                parse_mode=ParseMode.MARKDOWN
            )
        await query.edit_message_text("✅ تم تصدير جميع الحسابات غير المستخرجة ووضع علامة مستخرجة عليها.")
    else:
        await query.edit_message_text(
            export_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 الحسابات غير المستخرجة", "unextracted_accounts")])
        )

# ==================== OWNER PANEL: PURCHASE CHANNELS ====================
def purchase_channels_keyboard():
    return kb([
        [("1️⃣ ضبط الكروب الأول", "set_purchase_channel_1")],
        [("2️⃣ ضبط الكروب الثاني", "set_purchase_channel_2")],
        [("🔙 إعدادات المالك", "owner_panel")],
    ])


async def purchase_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    channel_1, channel_2 = get_configured_purchase_channels()
    await query.edit_message_text(
        "📨 *إعدادات إشعارات الشراء*\n\n"
        f"1️⃣ الكروب الأول: `{channel_1 or 'غير مضبوط'}`\n"
        f"2️⃣ الكروب الثاني: `{channel_2 or 'غير مضبوط'}`\n\n"
        "أضف البوت إلى الكروبين مع صلاحية إرسال الرسائل، ثم اضبط كل معرف هنا.\n"
        "يمكنك استخدام @username أو رقم الكروب مثل -1001234567890.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=purchase_channels_keyboard(),
    )


async def set_purchase_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    channel_number: int,
):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    context.user_data["store_action"] = f"set_purchase_channel_{channel_number}"
    await query.edit_message_text(
        f"✏️ أرسل معرف الكروب رقم {channel_number} الآن:\n\n"
        "مثال: `@my_group` أو `-1001234567890`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=purchase_channels_keyboard(),
    )

# ==================== FORCED CHANNEL ====================
async def forced_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    config = load_json(DATA_DIR / "config.json")
    current_channel = config.get("forced_channel", "")
    
    await query.edit_message_text(
        "📢 *إعدادات القناة الإجبارية*\n\n"
        f"📌 القناة الحالية: {current_channel if current_channel else 'لا توجد'}\n\n"
        "✏️ أرسل معرف القناة الجديدة (مثال: @my_channel):",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("🗑️ إلغاء القناة", "remove_channel")],
            [("🔙 إعدادات المالك", "owner_panel")]
        ])
    )
    context.user_data["store_action"] = "set_channel"

async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    config = load_json(DATA_DIR / "config.json")
    config["forced_channel"] = ""
    save_json(DATA_DIR / "config.json", config)
    
    await query.edit_message_text(
        "✅ تم إلغاء القناة الإجبارية.",
        reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")])
    )

# ==================== USER WITHDRAW STORE ====================
async def withdraw_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    config = load_json(DATA_DIR / "config.json")
    categories = config.get("store_categories", [])
    
    if not categories:
        await query.edit_message_text(
            "🛒 *قسم السحب*\n\nلا توجد فئات حالياً.", 
            reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
        )
        return
    
    rows = []
    for cat in categories:
        rows.append([(f"📂 {cat['name']}", f"user_category:{cat['id']}")])
    rows.append([("🔙 القائمة الرئيسية", "main_menu")])
    await query.edit_message_text(
        "🛒 *قسم السحب*\nاختر الفئة:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

async def user_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    cat_id = query.data.split(":", 1)[1]
    config = load_json(DATA_DIR / "config.json")
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text(
            "⚠️ الفئة غير موجودة.", 
            reply_markup=kb([("🔙 قسم السحب", "withdraw_store")])
        )
        return
    
    services = category.get("services", [])
    if not services:
        await query.edit_message_text(
            "📭 لا توجد خدمات في هذه الفئة.", 
            reply_markup=kb([("🔙 قسم السحب", "withdraw_store")])
        )
        return
    
    rows = []
    for s in services:
        rows.append([(f"🛒 {s['name']} - ${s['price']:.2f}", f"user_buy:{s['id']}:{cat_id}")])
    rows.append([("🔙 قسم السحب", "withdraw_store")])
    await query.edit_message_text(
        f"📂 *{category['name']}*\nاختر الخدمة:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

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
        await query.edit_message_text(
            "⚠️ الخدمة غير موجودة.", 
            reply_markup=kb([("🔙 قسم السحب", "withdraw_store")])
        )
        return

    user_data = get_user(user_id)
    
    if user_data["balance"] < service["price"]:
        await query.edit_message_text(
            f"❌ رصيدك غير كافٍ. الرصيد: ${user_data['balance']:.2f}, السعر: ${service['price']:.2f}"
        )
        return

    PENDING_PURCHASES[user_id] = {
        "service_id": service_id,
        "service_name": service_name,
        "service_price": service["price"],
        "service_message": service_message,
        "purchased_at": datetime.now().isoformat()
    }
    
    user = update.effective_user
    user_name = user.full_name or "غير معروف"
    user_username = user.username or "لا يوجد"
    user_id_str = str(user_id)
    total_emails = user_data.get("total_approved_emails", 0)
    bot_username = (await context.bot.get_me()).username
    order_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    channel_1_text = (
        "🛒 <b>طلب شراء جديد</b>\n\n"
        f"🤖 يوزر البوت: @{html.escape(bot_username or 'غير معروف')}\n"
        f"📦 الطلب: <code>{html.escape(str(service_name))}</code>\n"
        f"💰 السعر: <code>${service['price']:.2f}</code>\n"
        f"📧 عدد الإيميلات: <code>{total_emails}</code>"
    )
    purchase_channel_1, _ = get_configured_purchase_channels()
    notification_channels = (
        ("PURCHASE_CHANNEL_1", purchase_channel_1, channel_1_text, None),
    )
    for label, channel_id, message_text, reply_markup in notification_channels:
        if not channel_id:
            logger.error("%s غير مضبوط؛ لم يتم إرسال إشعار الشراء.", label)
            continue
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=message_text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            logger.info("Purchase notification sent to %s (%s).", label, channel_id)
        except Exception:
            logger.exception("Could not send purchase notification to %s (%s).", label, channel_id)
    
    await query.edit_message_text(
        f"✅ *تم طلب الخدمة بنجاح!*\n\n"
        f"🛒 *الخدمة:* {service_name}\n"
        f"💰 *السعر:* ${service['price']:.2f}\n\n"
        f"📝 *ملاحظة:* {service_message}\n\n"
        f"_📤 يرجى إرسال المعلومات المطلوبة في رسالة جديدة_\n"
        f"_🔒 سيتم خصم المبلغ بعد إرسال المعلومات_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 قسم السحب", "withdraw_store")])
    )

async def deliver_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 هذا الزر للمالك فقط.", show_alert=True)
        return
    
    user_id = int(query.data.split(":")[1])
    
    user_data = get_user(user_id)
    total_emails = user_data.get("total_approved_emails", 0)
    
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📦 *تم استلام طلبك بنجاح!*\n\n"
                 f"✅ تم استلام طلب السحب الخاص بك.\n"
                 f"🕐 سيتم التواصل معك قريباً.\n\n"
                 f"_شكراً لاستخدامك البوت 🤖_",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Could not send delivery confirmation to user {user_id}: {e}")
    
    PENDING_PURCHASES.pop(user_id, None)
    
    await query.edit_message_text(
        f"✅ *تم إيصال الطلب للمستخدم!*\n\n"
        f"👤 المستخدم: `{user_id}`\n"
        f"📧 عدد الإيميلات: `{total_emails}`\n"
        f"⏰ تم الإيصال: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode=ParseMode.MARKDOWN
    )
    
    await query.message.reply_text(
        f"✅ تم إعلام المستخدم `{user_id}` باستلام طلبه.",
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== HANDLE USER MESSAGE AFTER PURCHASE ====================
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
            f"❌ رصيدك غير كافٍ. الرصيد: ${user_data['balance']:.2f}, السعر: ${price:.2f}\n"
            f"يرجى إعادة المحاولة.",
            reply_markup=kb([("🔙 قسم السحب", "withdraw_store")])
        )
        PENDING_PURCHASES.pop(user_id, None)
        return
    
    user_data["balance"] -= price
    save_user(user_id, user_data)
    
    user = update.effective_user
    user_name = user.full_name or "غير معروف"
    user_username = user.username or "لا يوجد"
    total_emails = user_data.get("total_approved_emails", 0)
    _, purchase_channel_2 = get_configured_purchase_channels()

    if purchase_channel_2:
        channel_2_text = (
            "📋 <b>طلب شراء مكتمل</b>\n\n"
            f"👤 <b>الاسم:</b> <code>{html.escape(user_name)}</code>\n"
            f"🆔 <b>اليوزر:</b> @{html.escape(user_username)}\n"
            f"🆔 <b>المعرف:</b> <code>{user_id}</code>\n"
            f"📦 <b>الخدمة:</b> <code>{html.escape(str(purchase['service_name']))}</code>\n"
            f"💰 <b>السعر المخصوم:</b> <code>${price:.2f}</code>\n"
            f"📧 <b>عدد الإيميلات المقبولة:</b> <code>{total_emails}</code>\n\n"
            "📝 <b>رسالة العضو القابلة للنسخ:</b>\n"
            f"<code>{html.escape(text)}</code>\n"
            f"⏰ <b>وقت الإرسال:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            "───────────────────\n"
            "<i>اضغط على الزر أدناه لإعلام المستخدم باستلام طلبه</i>"
        )
        try:
            await context.bot.send_message(
                chat_id=purchase_channel_2,
                text=channel_2_text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb([("✅ تم الإيصال", f"deliver_order:{user_id}")]),
            )
            logger.info("Purchase member message sent to PURCHASE_CHANNEL_2 (%s).", purchase_channel_2)
        except Exception:
            logger.exception(
                "Could not send member purchase message to PURCHASE_CHANNEL_2 (%s).",
                purchase_channel_2,
            )
    else:
        logger.error("PURCHASE_CHANNEL_2 غير مضبوط؛ لم يتم إرسال رسالة العضو.")
    
    if OWNER_ID:
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"📩 *رسالة من مستخدم بعد الشراء*\n\n"
                     f"👤 *الاسم:* `{user_name}`\n"
                     f"🆔 *اليوزر:* @{user_username}\n"
                     f"🆔 *المعرف:* `{user_id}`\n"
                     f"📦 *الخدمة:* `{purchase['service_name']}`\n"
                     f"💰 *السعر المخصوم:* `${price:.2f}`\n"
                     f"📧 *عدد الإيميلات:* `{total_emails}`\n\n"
                     f"📝 *رسالة المستخدم:*\n"
                     f"`{text}`\n\n"
                     f"⏰ *وقت الإرسال:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Error sending user message to owner: {e}")
    
    await update.message.reply_text(
        f"✅ *تم استلام رسالتك بنجاح!*\n\n"
        f"💰 تم خصم `${price:.2f}` من رصيدك.\n"
        f"📝 رسالتك: `{text}`\n\n"
        f"_📌 سيتم التواصل معك قريباً من قبل المالك._\n"
        f"_شكراً لاستخدامك البوت 🤖_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
    )
    
    PENDING_PURCHASES.pop(user_id, None)

# ==================== MY WALLET ====================
async def my_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    user = get_user(query.from_user.id)
    await query.edit_message_text(
        f"💰 *أموالي*\n\n"
        f"⏳ قيد الانتظار: ${float(user.get('pending_balance', 0.0)):.2f}\n"
        f"⏳ معلق (24 ساعة): ${float(user.get('hold_balance', 0.0)):.2f}\n"
        f"✅ الرصيد المملوك: ${float(user.get('balance', 0.0)):.2f}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
    )

# ==================== TUTORIALS ====================
async def tutorials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    config = load_json(DATA_DIR / "config.json")
    rows = []
    
    if config.get("video_general") and Path(config.get("video_general", "")).exists():
        rows.append([("📖 شرح عام للبوت", "play_video:general")])
    
    if config.get("video_email") and Path(config.get("video_email", "")).exists():
        rows.append([("📹 إنشاء إيميل", "play_video:email")])
    if config.get("video_password") and Path(config.get("video_password", "")).exists():
        rows.append([("📹 تغيير باسورد", "play_video:password")])
    if config.get("video_totp") and Path(config.get("video_totp", "")).exists():
        rows.append([("📹 إضافة 2FA", "play_video:totp")])
    if config.get("video_app_pass") and Path(config.get("video_app_pass", "")).exists():
        rows.append([("📹 كلمة مرور التطبيق", "play_video:app_pass")])
    if config.get("video_leave") and Path(config.get("video_leave", "")).exists():
        rows.append([("📹 فيديو المغادرة", "play_video:leave")])
    rows.append([("🔙 القائمة الرئيسية", "main_menu")])
    await query.edit_message_text("📺 *اختر الدرس:*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb(*rows))

async def play_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    vtype = query.data.split(":")[1]
    config = load_json(DATA_DIR / "config.json")
    path = config.get(f"video_{vtype}")
    
    video_names = {
        "general": "شرح عام للبوت",
        "email": "إنشاء إيميل",
        "password": "تغيير باسورد",
        "totp": "إضافة 2FA",
        "app_pass": "كلمة مرور التطبيق",
        "leave": "فيديو المغادرة"
    }
    
    if path and Path(path).exists():
        try:
            await context.bot.send_video(
                chat_id=query.from_user.id,
                video=open(path, "rb"),
                caption=f"📹 *فيديو تعليمي: {video_names.get(vtype, vtype)}*",
                parse_mode=ParseMode.MARKDOWN,
                supports_streaming=True
            )
            await tutorials(update, context)
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await query.edit_message_text(
                "⚠️ حدث خطأ في تشغيل الفيديو. حاول مرة أخرى.",
                reply_markup=kb([("🔙 التعليم", "tutorials")])
            )
    else:
        await query.edit_message_text(
            "⚠️ الفيديو غير موجود حالياً.",
            reply_markup=kb([("🔙 التعليم", "tutorials")])
        )

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
    
    msg = (
        f"🔗 *نظام الإحالة*\n\n"
        f"📌 *رابط الإحالة الخاص بك:*\n"
        f"`{referral_link}`\n\n"
        f"📊 *إحصائياتك:*\n"
        f"💰 مكافآت الإحالة: ${float(user_data.get('referral_earnings', 0.0)):.2f}\n"
        f"👥 عدد الإحالات الناجحة: {user_data.get('total_referrals', 0)}\n\n"
        f"📝 *كيف يعمل النظام؟*\n"
        f"1️⃣ شارك رابط الإحالة مع أصدقائك\n"
        f"2️⃣ عند إضافة صديقك لحساب جديد وقبوله من المالك\n"
        f"3️⃣ ستحصل على مكافأة إحالة لكل حساب مقبول\n"
        f"4️⃣ كلما زاد عدد الحسابات المقبولة، زادت مكافآتك!"
    )
    
    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("📋 نسخ الرابط", f"copy_referral:{referral_code}")],
            [("🔙 القائمة الرئيسية", "main_menu")]
        ])
    )

async def copy_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    
    query = update.callback_query
    code = query.data.split(":")[1]
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={code}"
    
    await query.edit_message_text(
        f"📋 *رابط الإحالة الخاص بك:*\n\n"
        f"`{link}`\n\n"
        f"📌 يمكنك نسخ الرابط ومشاركته مع أصدقائك.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("🔗 عرض رابط الإحالة", "referral_menu")],
            [("🔙 القائمة الرئيسية", "main_menu")]
        ])
    )

async def referral_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    config = load_json(DATA_DIR / "config.json")
    referral_bonus = config.get("referral_bonus", 0.0)
    
    await query.edit_message_text(
        f"🔗 *إعدادات الإحالة*\n\n"
        f"💰 مكافأة الإحالة الحالية: ${referral_bonus:.2f}\n\n"
        f"📌 *ملاحظة:* يحصل صاحب الإحالة على هذه المكافأة عند قبول كل حساب جديد من قبل المستخدم المُحال.\n\n"
        f"اختر الإجراء المناسب:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("💲 تغيير مكافأة الإحالة", "set_referral_bonus")],
            [("📊 إحصائيات الإحالة", "referral_stats")],
            [("🔙 إعدادات المالك", "owner_panel")]
        ])
    )

async def set_referral_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    await query.edit_message_text(
        "💰 *تغيير مكافأة الإحالة*\n\n"
        "أرسل المبلغ الجديد لمكافأة الإحالة (رقم فقط):\n"
        "📌 مثال: 1.5",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إعدادات الإحالة", "referral_settings")])
    )
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
            top_referrers.append({
                "user_id": uid,
                "count": user_data["total_referrals"],
                "earnings": float(user_data.get("referral_earnings", 0.0))
            })
    
    top_referrers.sort(key=lambda x: x["count"], reverse=True)
    
    msg = f"📊 *إحصائيات الإحالة*\n\n"
    msg += f"👥 إجمالي الإحالات: {total_referrals}\n"
    msg += f"💰 إجمالي المكافآت المدفوعة: ${total_earnings:.2f}\n\n"
    
    if top_referrers:
        msg += "🏆 *أفضل المحالين:*\n"
        for idx, ref in enumerate(top_referrers[:5], 1):
            msg += f"{idx}. 👤 {ref['user_id']} - {ref['count']} إحالة - ${ref['earnings']:.2f}\n"
    
    if not top_referrers:
        msg += "📭 لا توجد إحالات حالياً."
    
    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إعدادات الإحالة", "referral_settings")])
    )

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

    # Handle approval steps (TOTP and App Pass)
    if context.user_data.get("step") == "waiting_totp":
        await handle_approval_totp(update, context)
        return
    
    if context.user_data.get("step") == "waiting_app_pass":
        await handle_approval_app_pass(update, context)
        return

    # Handle tier price setting
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
        
        config["store_categories"].append({
            "id": str(time.time_ns()), 
            "name": text, 
            "services": []
        })
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
        if not channel_id or not (
            channel_id.startswith("@") or channel_id.lstrip("-").isdigit()
        ):
            await update.message.reply_text(
                "⚠️ المعرف غير صحيح. أرسل @username أو رقم الكروب مثل -1001234567890."
            )
            return

        channel_number = action.rsplit("_", 1)[1]
        config = load_json(DATA_DIR / "config.json")
        config[f"purchase_channel_{channel_number}"] = channel_id
        save_json(DATA_DIR / "config.json", config)
        context.user_data.pop("store_action", None)
        await update.message.reply_text(
            f"✅ تم حفظ الكروب رقم {channel_number}: {channel_id}\n\n"
            "تأكد أن البوت موجود في الكروب ولديه صلاحية إرسال الرسائل.",
            reply_markup=purchase_channels_keyboard(),
        )

    elif action == "add_service_name":
        context.user_data["store_service_name"] = text
        context.user_data["store_action"] = "add_service_price"
        await update.message.reply_text(
            "💰 *الخطوة 2/3*: أرسل سعر المبيعة (رقم فقط):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 إلغاء", f"store_category:{context.user_data.get('current_category_id')}")])
        )

    elif action == "add_service_price":
        try:
            price = float(text)
            if price <= 0:
                await update.message.reply_text("⚠️ السعر يجب أن يكون أكبر من 0!")
                return
            
            context.user_data["store_service_price"] = price
            context.user_data["store_action"] = "add_service_message"
            await update.message.reply_text(
                "📝 *الخطوة 3/3*: أرسل الرسالة التي ستظهر للعميل بعد الشراء:\n\n"
                "مثال: أرسل معرفك في ببجي ليتم إرسال الهدية.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb([("🔙 إلغاء", f"store_category:{context.user_data.get('current_category_id')}")])
            )
        except ValueError:
            await update.message.reply_text("⚠️ يرجى إرسال رقم صحيح (مثال: 10.50)")

    elif action == "add_service_message":
        name = context.user_data.get("store_service_name")
        price = context.user_data.get("store_service_price")
        cat_id = context.user_data.get("current_category_id")
        
        config = load_json(DATA_DIR / "config.json")
        for cat in config["store_categories"]:
            if cat["id"] == cat_id:
                cat["services"].append({
                    "id": str(time.time_ns()), 
                    "name": name, 
                    "price": price,
                    "message": text
                })
                break
        save_json(DATA_DIR / "config.json", config)
        
        await update.message.reply_text(
            f"✅ تم إضافة المبيعة بنجاح!\n"
            f"📌 الاسم: {name}\n"
            f"💰 السعر: ${price:.2f}\n"
            f"📝 الرسالة: {text}"
        )
        context.user_data.pop("store_action", None)
        context.user_data.pop("store_service_name", None)
        context.user_data.pop("store_service_price", None)
        context.user_data.pop("current_category_id", None)
        await main_menu(update, context)

# ==================== STORE SECTION (LEGACY) ====================
async def owner_store_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    config = load_json(DATA_DIR / "config.json")
    categories = config.get("store_categories", [])
    
    rows = []
    if categories:
        for cat in categories:
            rows.append([(f"📂 {cat['name']}", f"store_category:{cat['id']}")])
    
    rows.append([("➕ إضافة فئة جديدة", "store_add_category")])
    rows.append([("🔙 إعدادات المالك", "owner_panel")])
    
    await query.edit_message_text(
        "🛒 *إدارة المبيعات*\n\nاختر فئة لعرض مبيعاتها أو أضف فئة جديدة:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

async def store_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    await query.edit_message_text(
        "✏️ *إضافة فئة جديدة*\n\nأرسل اسم الفئة (مثال: حسابات، اشتراكات، أدوات):",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إلغاء", "store_section")])
    )
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
        await query.edit_message_text("⚠️ الفئة غير موجودة.", reply_markup=kb([("🔙 المبيعات", "store_section")]))
        return

    services = category.get("services", [])
    msg = f"📂 *{category['name']}*\n\n"
    
    if services:
        for idx, s in enumerate(services, 1):
            msg += f"{idx}. 🛒 {s['name']} - 💰 ${s['price']:.2f}\n"
    else:
        msg += "📭 لا توجد مبيعات في هذه الفئة.\n"
    
    rows = [
        [("➕ إضافة مبيعة", f"store_add_service:{cat_id}")],
        [("🗑️ حذف مبيعة", f"store_delete_service:{cat_id}")],
        [("🔙 المبيعات", "store_section")]
    ]
    
    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

async def store_add_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    cat_id = query.data.split(":", 1)[1]
    context.user_data["current_category_id"] = cat_id
    context.user_data["store_action"] = "add_service_name"
    
    await query.edit_message_text(
        "✏️ *إضافة مبيعة جديدة*\n\n📌 الخطوة 1/3: أرسل اسم المبيعة:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إلغاء", f"store_category:{cat_id}")])
    )

async def store_add_service_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    cat_id = query.data.split(":", 1)[1]
    context.user_data["current_category_id"] = cat_id
    context.user_data["store_action"] = "add_service_price"
    
    await query.edit_message_text(
        "💰 *الخطوة 2/3*: أرسل سعر المبيعة (رقم فقط):",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إلغاء", f"store_category:{cat_id}")])
    )

async def store_add_service_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    cat_id = query.data.split(":", 1)[1]
    context.user_data["current_category_id"] = cat_id
    context.user_data["store_action"] = "add_service_message"
    
    await query.edit_message_text(
        "📝 *الخطوة 3/3*: أرسل الرسالة التي ستظهر للعميل بعد الشراء:\n\n"
        "مثال: أرسل معرفك في ببجي ليتم إرسال الهدية.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إلغاء", f"store_category:{cat_id}")])
    )

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
        await query.edit_message_text("📭 لا توجد مبيعات لحذفها.", reply_markup=kb([("🔙 الفئة", f"store_category:{cat_id}")]))
        return
    
    rows = []
    for s in services:
        rows.append([(f"❌ {s['name']} - ${s['price']:.2f}", f"delete_service:{cat_id}:{s['id']}")])
    rows.append([("🔙 الفئة", f"store_category:{cat_id}")])
    
    await query.edit_message_text(
        "🗑️ *حذف مبيعة*\nاختر المبيعة للحذف:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*rows)
    )

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
    await query.edit_message_text(
        "✅ تم حذف المبيعة بنجاح.",
        reply_markup=kb([("🔙 الفئة", f"store_category:{cat_id}")])
    )

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
        "✅ *تم تفعيل الإحالة بنجاح!*\n\n"
        f"👤 تمت إحالتك بواسطة: {referrer_id}\n"
        "📌 ستتلقى أنت وصاحب الإحالة مكافآت عند قبول حساباتك.\n\n"
        "استخدم /start للبدء.",
        parse_mode=ParseMode.MARKDOWN
    )
    
    try:
        await context.bot.send_message(
            chat_id=referrer_id,
            text=f"🎉 *إحالة جديدة!*\n\n"
                 f"👤 المستخدم {user_id} انضم باستخدام رابط إحالتك.\n"
                 f"📌 ستحصل على مكافأة عند قبول حسابه من قبل المالك.",
            parse_mode=ParseMode.MARKDOWN
        )
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
    elif data.startswith("approved_detail:"): 
        await approved_detail(update, context)
    elif data.startswith("rejected_detail:"): 
        await rejected_detail(update, context)
    elif data.startswith("deduct_points:"): 
        await deduct_points(update, context)
    elif data.startswith("give_points:"): 
        await give_points(update, context)
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
    elif data.startswith("edit_field:"): 
        await edit_field(update, context)
    elif data.startswith("delete_pending:"): 
        await delete_pending_account(update, context)
    else: 
        await placeholder(update, context)

async def placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("⚠️ خيار غير معروف حالياً.", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))

# ==================== DEBUG & OWNER COMMANDS ====================
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
        reply_markup=kb([
            [("💰 أسعار المستويات", "set_tier_prices")],
            [("📋 الطلبات", "approval_requests")],
            [("📹 قسم الفيديوهات", "videos_section")],
            [("🛒 المبيعات", "store_section")],
            [("📢 قناة إجبارية", "forced_channel")],
            [("📊 جميع الحسابات المقبولة", "all_accounts_section")],
            [("🔗 نظام الإحالة", "referral_settings")],
            [("🔙 القائمة الرئيسية", "main_menu")]
        ])
    )

async def store_list_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Legacy function - kept for compatibility"""
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

# ==================== MAIN ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("owner", owner_command))
    app.add_handler(CallbackQueryHandler(router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_upload))
    app.run_polling()

if __name__ == "__main__":
    main()
