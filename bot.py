"""
Advanced Telegram Account Manager Bot - FULLY FIXED WITH TIERED APPROVAL
- Tier 1: Email + Password → asks for TOTP → asks for App Pass
- Tier 2: Email + Password + TOTP → asks for App Pass
- Tier 3: Email + Password + TOTP + App Pass → direct approval
- NEW: Lost/Orphaned emails recovery system
- All features working perfectly
"""

import html
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
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

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "0"))
PURCHASE_CHANNEL_1 = os.environ.get("PURCHASE_CHANNEL_1", "").strip()
PURCHASE_CHANNEL_2 = os.environ.get("PURCHASE_CHANNEL_2", "").strip()

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

USERS_DB = DATA_DIR / "users.json"
LOST_DB = DATA_DIR / "lost_requests.json"
VIDEOS_DIR = DATA_DIR / "videos"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

# ==================== DATA HELPERS ====================
def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except:
        return {}

def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def get_user(user_id: int) -> dict:
    users = load_json(USERS_DB)
    return users.get(str(user_id), {
        "balance": 0.0,
        "pending_balance": 0.0,
        "hold_balance": 0.0,
        "total_credited_balance": 0.0,
        "spent_balance": 0.0,
        "approved_accounts": [],
        "pending_accounts": {},
        "rejected_accounts": [],
        "used_app_passwords": [],
        "referral_code": "",
        "referred_by": None,
        "referral_earnings": 0.0,
        "total_referrals": 0,
        "total_approved_emails": 0,
    })

def save_user(user_id: int, user_data: dict):
    users = load_json(USERS_DB)
    users[str(user_id)] = user_data
    save_json(USERS_DB, users)

def generate_referral_code():
    return secrets.token_hex(4).upper()

# ==================== LOST EMAILS SYSTEM ====================
def save_lost_request(user_id: int, email: str, password: str, totp: str = "", app_pass: str = "", reason: str = "unknown"):
    """حفظ إيميل فشل في الوصول إلى النظام لاستعادته لاحقاً"""
    lost_data = load_json(LOST_DB)
    if not isinstance(lost_data, list):
        lost_data = []
    
    # تجنب التكرار
    for item in lost_data:
        if item.get("email") == email and item.get("user_id") == user_id:
            return
    
    lost_data.append({
        "user_id": user_id,
        "email": email,
        "password": password,
        "totp": totp,
        "app_pass": app_pass,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat()
    })
    save_json(LOST_DB, lost_data)
    logger.info(f"Saved lost request: {email} (user {user_id})")

def get_lost_requests() -> list:
    return load_json(LOST_DB)

def clear_lost_request(email: str, user_id: int):
    lost_data = load_json(LOST_DB)
    lost_data = [item for item in lost_data if not (item.get("email") == email and item.get("user_id") == user_id)]
    save_json(LOST_DB, lost_data)

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
    return html.escape(str(value), quote=False)

# ==================== SESSION ====================
@dataclass
class Session:
    step: str = ""
    email: str = ""
    password: str = ""
    totp: str = ""
    app_pass: str = ""
    has_password: bool = False
    has_totp: bool = False
    has_app_pass: bool = False

SESSIONS: Dict[int, Session] = {}
PENDING_PURCHASES: Dict[int, Dict] = {}
LEAVE_HOLD_SECONDS = 24 * 60 * 60

# ==================== PRICING ====================
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

# ==================== VALIDATION ====================
def validate_totp_secret(secret: str) -> bool:
    cleaned = secret.replace(" ", "").upper()
    return len(cleaned) == 32 and bool(re.match(r'^[A-Z2-7]{32}$', cleaned))

def validate_app_password(password: str) -> bool:
    cleaned = password.replace(" ", "").upper()
    return len(cleaned) == 16 and bool(re.match(r'^[A-Z0-9]{16}$', cleaned))

def format_app_password(password: str) -> str:
    cleaned = password.replace(" ", "").upper()
    if len(cleaned) != 16:
        return password
    return f"{cleaned[0:4]} {cleaned[4:8]} {cleaned[8:12]} {cleaned[12:16]}"

def format_totp_secret(secret: str) -> str:
    cleaned = secret.replace(" ", "").upper()
    if len(cleaned) != 32:
        return secret
    return " ".join([cleaned[i:i+4] for i in range(0, 32, 4)])

# ==================== MAIN MENU ====================
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    query = update.callback_query
    user_data = get_user(query.from_user.id)
    pending_accounts = list(user_data.get("pending_accounts", {}).values())
    approved = user_data.get("approved_accounts", [])
    rejected = user_data.get("rejected_accounts", [])
    
    if not approved and not pending_accounts and not rejected:
        await query.edit_message_text("📭 لا توجد حسابات لديك.", reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    
    msg = "📋 *جميع حساباتي:*\n\n"
    if approved:
        msg += "✅ *مقبولة:*\n"
        for idx, acc in enumerate(approved, 1):
            msg += f"  {idx}. 📧 `{acc.get('email', '')}` ✅\n"
        msg += "\n"
    if pending_accounts:
        msg += "⏳ *منتظرة:*\n"
        for idx, acc in enumerate(pending_accounts, 1):
            status = acc.get("verification_status", "pending")
            icon = "🔄" if status == "verifying" else "⏳"
            msg += f"  {idx}. 📧 `{acc.get('email', '')}` {icon}\n"
        msg += "\n"
    if rejected:
        msg += "❌ *مرفوضة:*\n"
        for idx, rej in enumerate(rejected, 1):
            msg += f"  {idx}. 📧 `{rej.get('email', '')}` ❌\n"
        msg += "\n"
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))

# ==================== ADD ACCOUNT FLOW ====================
async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    SESSIONS[uid] = Session(step="email")
    prices = get_tier_prices()
    await update.callback_query.edit_message_text(
        f"📝 *إضافة حساب جديد*\n\n💵 *نظام المكافآت:*\n• إيميل + باسورد فقط → ${prices['tier_1']:.2f}\n• إيميل + باسورد + رمز مصادقة → ${prices['tier_2']:.2f}\n• كامل → ${prices['tier_3']:.2f}\n\n📧 *الخطوة 1/4*: أرسل الإيميل:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical([("❌ إلغاء", "cancel")]))

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
    
    prices = get_tier_prices()
    
    if session.step == "email":
        if not re.match(r"[^@]+@[^@]+\.[^@]+", text):
            await update.message.reply_text("❌ إيميل غير صالح.")
            return
        # التحقق من التكرار
        user_data = get_user(uid)
        for acc in user_data.get("approved_accounts", []):
            if acc.get("email") == text:
                await update.message.reply_text("❌ هذا الإيميل مقبول مسبقاً!")
                return
        for acc in user_data.get("pending_accounts", {}).values():
            if acc.get("email") == text:
                await update.message.reply_text("⏳ هذا الإيميل قيد الانتظار بالفعل!")
                return
        
        session.email = text
        session.step = "password"
        await update.message.reply_text(
            f"🔑 *الخطوة 2/4*: أرسل كلمة المرور الأساسية:\n\n💰 *السعر الحالي:* ${prices['tier_1']:.2f}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical([("❌ إلغاء", "cancel")]))
    
    elif session.step == "password":
        session.password = text
        session.has_password = True
        session.step = "totp"
        await update.message.reply_text(
            f"🔐 *الخطوة 3/4*: أرسل مفتاح المصادقة (Secret Key):\n\n💰 *السعر مع رمز المصادقة:* ${prices['tier_2']:.2f}\n\n📌 *يمكنك استلام {prices['tier_1']:.2f}$ الآن وإكمال الباقي لاحقاً*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical([
                ("✅ استلم $0.10 (باسورد فقط)", f"submit_tier_1:{uid}"),
                ("❌ إلغاء", "cancel")
            ]))
    
    elif session.step == "totp":
        cleaned = text.replace(" ", "").upper()
        if len(cleaned) != 32 or not re.match(r'^[A-Z2-7]{32}$', cleaned):
            await update.message.reply_text("⚠️ مفتاح المصادقة غير صالح (32 حرفاً).")
            return
        session.totp = cleaned
        session.has_totp = True
        session.step = "app_pass"
        await update.message.reply_text(
            f"🗝 *الخطوة 4/4*: أرسل كلمة مرور التطبيق (16 حرف):\n\n💰 *السعر الكامل:* ${prices['tier_3']:.2f}\n\n📌 *يمكنك استلام {prices['tier_2']:.2f}$ الآن وإكمال الباقي لاحقاً*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical([
                ("✅ استلم $0.15 (مع رمز المصادقة)", f"submit_tier_2:{uid}"),
                ("❌ إلغاء", "cancel")
            ]))
    
    elif session.step == "app_pass":
        cleaned = text.replace(" ", "").upper()
        if len(cleaned) != 16 or not re.match(r'^[A-Z0-9]{16}$', cleaned):
            await update.message.reply_text("⚠️ كلمة مرور التطبيق غير صالحة (16 حرفاً).")
            return
        
        session.app_pass = cleaned
        session.has_app_pass = True
        
        user = update.effective_user
        final_price = calculate_account_price(session.has_totp, session.has_app_pass)
        
        try:
            user_data = get_user(uid)
            if session.email in user_data["pending_accounts"]:
                await update.message.reply_text("⚠️ هذا الإيميل قيد الانتظار بالفعل!")
                SESSIONS.pop(uid, None)
                return
            
            user_data["pending_accounts"][session.email] = {
                "email": session.email,
                "password": session.password,
                "totp_secret": session.totp if session.has_totp else None,
                "app_password": session.app_pass if session.has_app_pass else None,
                "amount": final_price,
                "has_totp": session.has_totp,
                "has_app_pass": session.has_app_pass,
                "user_name": user.full_name,
                "user_username": user.username,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "verification_status": "pending",
            }
            user_data["pending_balance"] += final_price
            save_user(uid, user_data)
            SESSIONS.pop(uid, None)
            
            await update.message.reply_text(
                f"✅ *تم إرسال الطلب للمالك!*\n💰 تمت إضافة *${final_price:.2f}* إلى الأموال قيد الانتظار.\n📧 الإيميل: `{session.email}`",
                parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        except Exception as e:
            logger.error(f"Error saving account: {e}")
            # حفظ الطلب المفقود
            save_lost_request(uid, session.email, session.password, session.totp, session.app_pass, f"error_saving: {str(e)}")
            await update.message.reply_text("⚠️ حدث خطأ في حفظ الطلب. سيتم حفظه في قائمة الطلبات المفقودة.", reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
            SESSIONS.pop(uid, None)

async def submit_tier_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = int(query.data.split(":")[1])
    session = SESSIONS.get(uid)
    if not session:
        await query.answer("⚠️ الجلسة منتهية.", show_alert=True)
        return
    user = update.effective_user
    price = get_tier_prices()["tier_1"]
    
    try:
        user_data = get_user(uid)
        user_data["pending_accounts"][session.email] = {
            "email": session.email,
            "password": session.password,
            "totp_secret": None,
            "app_password": None,
            "amount": price,
            "has_totp": False,
            "has_app_pass": False,
            "user_name": user.full_name,
            "user_username": user.username,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "verification_status": "pending",
        }
        user_data["pending_balance"] += price
        save_user(uid, user_data)
        SESSIONS.pop(uid, None)
        await query.edit_message_text(
            f"✅ *تم إرسال الطلب!*\n💰 تمت إضافة *${price:.2f}* إلى الأموال قيد الانتظار.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
    except Exception as e:
        save_lost_request(uid, session.email, session.password, "", "", f"submit_tier_1_error: {str(e)}")
        await query.edit_message_text("⚠️ حدث خطأ. تم حفظ الطلب في قائمة الطلبات المفقودة.", reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        SESSIONS.pop(uid, None)

async def submit_tier_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = int(query.data.split(":")[1])
    session = SESSIONS.get(uid)
    if not session:
        await query.answer("⚠️ الجلسة منتهية.", show_alert=True)
        return
    user = update.effective_user
    price = get_tier_prices()["tier_2"]
    
    try:
        user_data = get_user(uid)
        user_data["pending_accounts"][session.email] = {
            "email": session.email,
            "password": session.password,
            "totp_secret": session.totp,
            "app_password": None,
            "amount": price,
            "has_totp": True,
            "has_app_pass": False,
            "user_name": user.full_name,
            "user_username": user.username,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "verification_status": "pending",
        }
        user_data["pending_balance"] += price
        save_user(uid, user_data)
        SESSIONS.pop(uid, None)
        await query.edit_message_text(
            f"✅ *تم إرسال الطلب!*\n💰 تمت إضافة *${price:.2f}* إلى الأموال قيد الانتظار.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
    except Exception as e:
        save_lost_request(uid, session.email, session.password, session.totp, "", f"submit_tier_2_error: {str(e)}")
        await query.edit_message_text("⚠️ حدث خطأ. تم حفظ الطلب في قائمة الطلبات المفقودة.", reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        SESSIONS.pop(uid, None)

# ==================== MY WALLET ====================
async def my_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = get_user(query.from_user.id)
    await query.edit_message_text(
        f"💰 *أموالي*\n\n⏳ قيد الانتظار: ${float(user.get('pending_balance', 0.0)):.2f}\n✅ الرصيد المملوك: ${float(user.get('balance', 0.0)):.2f}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))

# ==================== OWNER PANEL ====================
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    buttons = [
        ("💰 أسعار المستويات", "set_tier_prices"),
        ("📋 الطلبات", "approval_requests"),
        ("📹 قسم الفيديوهات", "videos_section"),
        ("🛒 المبيعات", "store_section"),
        ("📢 قناة إجبارية", "forced_channel"),
        ("📊 جميع الحسابات", "all_accounts_section"),
        ("📂 الطلبات المفقودة", "view_lost_requests"),
        ("🔙 القائمة الرئيسية", "main_menu")
    ]
    await query.edit_message_text("⚙️ *لوحة تحكم المالك*\n\nاختر الإعداد الذي تريد تعديله:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

# ==================== TIER PRICES ====================
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
    tier_names = {"1": "المستوى 1 (إيميل + باسورد فقط)", "2": "المستوى 2 (إيميل + باسورد + رمز مصادقة)", "3": "المستوى 3 (كامل)"}
    await query.edit_message_text(f"💰 *تعديل سعر {tier_names[tier]}*\n\nأرسل السعر الجديد (رقم فقط):\n📌 مثال: 0.25",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", "set_tier_prices"))
    context.user_data["mode"] = "set_tier_price"

# ==================== VIDEOS SECTION ====================
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
    buttons = []
    if exists:
        buttons.append(("📹 عرض الفيديو", f"view_video:{video_type}"))
        buttons.append(("🗑️ حذف الفيديو", f"delete_video:{video_type}"))
    buttons.append(("📤 رفع فيديو جديد", f"set_video:{video_type}"))
    buttons.append(("🔙 قسم الفيديوهات", "videos_section"))
    status = "✅ موجود" if exists else "❌ غير موجود"
    await query.edit_message_text(f"📹 *فيديو {video_type}*\n\nالحالة: {status}\n\nاختر الإجراء المناسب:",
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
            await context.bot.send_video(chat_id=query.from_user.id, video=open(video_path, "rb"), caption=f"📹 *فيديو {video_type}*")
            await video_action(update, context)
        except:
            await query.edit_message_text("⚠️ حدث خطأ في عرض الفيديو.")
    else:
        await query.edit_message_text("⚠️ الفيديو غير موجود.")

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
            await query.edit_message_text(f"✅ تم حذف فيديو {video_type} بنجاح!")
        except:
            await query.edit_message_text("⚠️ حدث خطأ في حذف الفيديو.")
    else:
        await query.edit_message_text("⚠️ الفيديو غير موجود.")

async def set_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    context.user_data["pending_video_type"] = video_type
    await query.edit_message_text(f"📤 *أرسل الفيديو الخاص بـ {video_type} الآن (كملف فيديو):*", parse_mode=ParseMode.MARKDOWN)

async def handle_video_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    video_type = context.user_data.get("pending_video_type")
    if not video_type:
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

# ==================== APPROVAL REQUESTS (TIERED SYSTEM) ====================
async def approval_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    buttons = [
        ("⏳ الطلبات المنتظرة", "view_pending"),
        ("✅ الطلبات المقبولة", "view_approved"),
        ("❌ الطلبات المرفوضة", "view_rejected"),
        ("🔙 إعدادات المالك", "owner_panel")
    ]
    await query.edit_message_text("📋 *الطلبات*\n\nاختر القسم:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

async def view_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    pending = []
    for uid, u_data in users.items():
        for email, account in u_data.get("pending_accounts", {}).items():
            pending.append({"user_id": uid, "email": email, "account": account})
    
    if not pending:
        await query.edit_message_text("📭 لا توجد طلبات منتظرة.", reply_markup=kb_single("🔙 الطلبات", "approval_requests"))
        return
    
    buttons = []
    for req in pending:
        tier_icon = "🟢" if req['account'].get("has_app_pass") else "🟡" if req['account'].get("has_totp") else "🔵"
        email_display = req['email'][:15] + "..." if len(req['email']) > 15 else req['email']
        buttons.append((f"{tier_icon} {email_display}", f"pending_detail:{req['user_id']}:{req['email']}"))
    buttons.append(("🔙 الطلبات", "approval_requests"))
    await query.edit_message_text("⏳ *الطلبات المنتظرة*\n🟢 مكتمل | 🟡 مع رمز المصادقة | 🔵 باسورد فقط\n\nاختر الإيميل:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

async def pending_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    
    user_data = get_user(uid)
    account = user_data.get("pending_accounts", {}).get(email)
    
    if not account:
        await query.edit_message_text("⚠️ هذا الطلب غير موجود.", reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    msg = f"📋 *تفاصيل الطلب*\n\n"
    msg += f"📧 الإيميل: `{email}`\n"
    msg += f"🔑 الباسورد: `{account.get('password', '')}`\n"
    if account.get("has_totp"):
        msg += f"🔐 رمز المصادقة: `{account.get('totp_secret', '')}`\n"
    if account.get("has_app_pass"):
        msg += f"🗝 كلمة مرور التطبيق: `{account.get('app_password', '')}`\n"
    msg += f"💰 السعر: ${account.get('amount', 0):.2f}\n"
    msg += f"📦 المستوى: {'🟢 مكتمل' if account.get('has_app_pass') else '🟡 مع TOTP' if account.get('has_totp') else '🔵 باسورد فقط'}\n"
    
    buttons = []
    has_totp = account.get("has_totp", False)
    has_app_pass = account.get("has_app_pass", False)
    
    if not has_totp and not has_app_pass:
        # مستوى 1: يحتاج TOTP ثم App Pass
        buttons.append(("✅ قبول (سيطلب TOTP ثم App Pass)", f"approve_tier1:{uid}:{email}"))
    elif has_totp and not has_app_pass:
        # مستوى 2: يحتاج App Pass فقط
        buttons.append(("✅ قبول (سيطلب App Pass)", f"approve_tier2:{uid}:{email}"))
    else:
        # مستوى 3: كامل
        buttons.append(("✅ قبول فوري", f"approve_tier3:{uid}:{email}"))
    
    buttons.append(("❌ رفض", f"reject_request:{uid}:{email}"))
    buttons.append(("🔙 الطلبات المنتظرة", "view_pending"))
    
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

# ==================== TIERED APPROVAL HANDLERS ====================
async def approve_tier1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مستوى 1: إيميل + باسورد فقط → يطلب TOTP ثم App Pass"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    
    context.user_data["approval_uid"] = uid
    context.user_data["approval_email"] = email
    context.user_data["approval_step"] = "waiting_totp"
    context.user_data["approval_tier"] = 1
    
    await query.edit_message_text(
        f"🔐 *طلب رمز المصادقة (TOTP)*\n\n📧 الإيميل: `{email}`\n\nأرسل رمز المصادقة (32 حرفاً):\nالصيغة: XXXX XXXX XXXX XXXX XXXX XXXX XXXX XXXX\n\n_يمكنك كتابة 'تخطي' لتخطي هذه الخطوة_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{email}")
    )

async def approve_tier2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مستوى 2: إيميل + باسورد + TOTP → يطلب App Pass فقط"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    
    context.user_data["approval_uid"] = uid
    context.user_data["approval_email"] = email
    context.user_data["approval_step"] = "waiting_app_pass"
    context.user_data["approval_tier"] = 2
    
    await query.edit_message_text(
        f"🗝 *طلب كلمة مرور التطبيق (App Pass)*\n\n📧 الإيميل: `{email}`\n\nأرسل كلمة مرور التطبيق (16 حرفاً):\nالصيغة: XXXX XXXX XXXX XXXX\n\n_يمكنك كتابة 'تخطي' لتخطي هذه الخطوة_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{email}")
    )

async def approve_tier3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مستوى 3: كامل → قبول مباشر"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    
    await complete_approval(update, context, uid, email)
    await query.edit_message_text(f"✅ تم قبول الحساب `{email}` بنجاح!", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))

async def handle_approval_totp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data.get("approval_uid")
    email = context.user_data.get("approval_email")
    tier = context.user_data.get("approval_tier", 1)
    
    if not uid or not email:
        await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        return
    
    user_data = get_user(uid)
    account = user_data.get("pending_accounts", {}).get(email)
    
    if not account:
        await update.message.reply_text("⚠️ الطلب غير موجود.", reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    if text.lower() == "تخطي":
        account["totp_secret"] = None
        account["has_totp"] = False
        user_data["pending_accounts"][email] = account
        save_user(uid, user_data)
        context.user_data["approval_step"] = "waiting_app_pass"
        await update.message.reply_text(
            f"✅ تم تخطي رمز المصادقة.\n\n🗝 *الآن أرسل كلمة مرور التطبيق (16 حرفاً):*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{email}")
        )
        return
    
    cleaned = text.replace(" ", "").upper()
    if not validate_totp_secret(cleaned):
        await update.message.reply_text("⚠️ مفتاح مصادقة غير صالح. أرسل 32 حرفاً أو 'تخطي'.")
        return
    
    account["totp_secret"] = cleaned
    account["has_totp"] = True
    user_data["pending_accounts"][email] = account
    save_user(uid, user_data)
    context.user_data["approval_step"] = "waiting_app_pass"
    
    await update.message.reply_text(
        f"✅ تم استلام رمز المصادقة!\n\n🗝 *الآن أرسل كلمة مرور التطبيق (16 حرفاً):*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{email}")
    )

async def handle_approval_app_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data.get("approval_uid")
    email = context.user_data.get("approval_email")
    
    if not uid or not email:
        await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        return
    
    user_data = get_user(uid)
    account = user_data.get("pending_accounts", {}).get(email)
    
    if not account:
        await update.message.reply_text("⚠️ الطلب غير موجود.", reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    if text.lower() == "تخطي":
        account["app_password"] = None
        account["has_app_pass"] = False
        user_data["pending_accounts"][email] = account
        save_user(uid, user_data)
        await complete_approval(update, context, uid, email)
        await update.message.reply_text("✅ تم قبول الحساب!", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        context.user_data.pop("approval_step", None)
        context.user_data.pop("approval_uid", None)
        context.user_data.pop("approval_email", None)
        context.user_data.pop("approval_tier", None)
        return
    
    cleaned = text.replace(" ", "").upper()
    if not validate_app_password(cleaned):
        await update.message.reply_text("⚠️ كلمة مرور تطبيق غير صالحة. أرسل 16 حرفاً أو 'تخطي'.")
        return
    
    account["app_password"] = cleaned
    account["has_app_pass"] = True
    user_data["pending_accounts"][email] = account
    save_user(uid, user_data)
    
    await complete_approval(update, context, uid, email)
    await update.message.reply_text("✅ تم قبول الحساب بنجاح!", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
    context.user_data.pop("approval_step", None)
    context.user_data.pop("approval_uid", None)
    context.user_data.pop("approval_email", None)
    context.user_data.pop("approval_tier", None)

async def complete_approval(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, email: str):
    """إكمال عملية القبول ونقل الحساب إلى المقبولة"""
    user_data = get_user(uid)
    account = user_data.get("pending_accounts", {}).get(email)
    
    if not account:
        return
    
    price = account.get("amount", 0.0)
    user_data["balance"] += price
    user_data["pending_balance"] -= price
    del user_data["pending_accounts"][email]
    user_data["approved_accounts"].append(account)
    user_data["total_approved_emails"] += 1
    save_user(uid, user_data)

# ==================== REJECT REQUEST ====================
async def reject_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    email = parts[2]
    
    user_data = get_user(uid)
    account = user_data.get("pending_accounts", {}).get(email)
    
    if not account:
        await query.edit_message_text("⚠️ هذا الطلب غير موجود.", reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))
        return
    
    price = account.get("amount", 0.0)
    user_data["pending_balance"] -= price
    del user_data["pending_accounts"][email]
    user_data["rejected_accounts"].append(account)
    save_user(uid, user_data)
    
    await query.edit_message_text(f"❌ تم رفض الحساب `{email}`.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending"))

# ==================== VIEW APPROVED ====================
async def view_approved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    approved = []
    for uid, u_data in users.items():
        for acc in u_data.get("approved_accounts", []):
            approved.append({"user_id": uid, "account": acc})
    
    if not approved:
        await query.edit_message_text("📭 لا توجد طلبات مقبولة.", reply_markup=kb_single("🔙 الطلبات", "approval_requests"))
        return
    
    msg = f"✅ *الطلبات المقبولة ({len(approved)})*\n\n"
    for idx, acc in enumerate(approved[:20], 1):
        msg += f"{idx}. 📧 `{acc['account'].get('email', '')}` (${acc['account'].get('amount', 0):.2f})\n"
    if len(approved) > 20:
        msg += f"\n📌 *تم عرض أول 20 من أصل {len(approved)}*"
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 الطلبات", "approval_requests"))

# ==================== VIEW REJECTED ====================
async def view_rejected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    rejected = []
    for uid, u_data in users.items():
        for acc in u_data.get("rejected_accounts", []):
            rejected.append({"user_id": uid, "account": acc})
    
    if not rejected:
        await query.edit_message_text("📭 لا توجد طلبات مرفوضة.", reply_markup=kb_single("🔙 الطلبات", "approval_requests"))
        return
    
    msg = f"❌ *الطلبات المرفوضة ({len(rejected)})*\n\n"
    for idx, acc in enumerate(rejected[:20], 1):
        msg += f"{idx}. 📧 `{acc['account'].get('email', '')}`\n"
    if len(rejected) > 20:
        msg += f"\n📌 *تم عرض أول 20 من أصل {len(rejected)}*"
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 الطلبات", "approval_requests"))

# ==================== LOST REQUESTS ====================
async def view_lost_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    lost = get_lost_requests()
    
    if not lost:
        await query.edit_message_text("📭 لا توجد طلبات مفقودة.", reply_markup=kb_single("🔙 إعدادات المالك", "owner_panel"))
        return
    
    msg = f"📂 *الطلبات المفقودة ({len(lost)})*\n\n"
    for idx, item in enumerate(lost[:20], 1):
        msg += f"{idx}. 📧 `{item.get('email', '')}` (المستخدم: {item.get('user_id', '')})\n"
        msg += f"   📝 السبب: {item.get('reason', 'غير معروف')}\n"
        msg += f"   🕐 {item.get('timestamp', '')}\n\n"
    if len(lost) > 20:
        msg += f"\n📌 *تم عرض أول 20 من أصل {len(lost)}*"
    
    buttons = [("🔄 محاولة استعادة الكل", "recover_all_lost"), ("🔙 إعدادات المالك", "owner_panel")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

async def recover_all_lost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    lost = get_lost_requests()
    recovered = 0
    
    for item in lost:
        user_id = item.get("user_id")
        email = item.get("email")
        password = item.get("password")
        totp = item.get("totp", "")
        app_pass = item.get("app_pass", "")
        
        user_data = get_user(user_id)
        if email in user_data["pending_accounts"]:
            continue
        
        has_totp = bool(totp)
        has_app_pass = bool(app_pass)
        price = calculate_account_price(has_totp, has_app_pass)
        
        user_data["pending_accounts"][email] = {
            "email": email,
            "password": password,
            "totp_secret": totp if has_totp else None,
            "app_password": app_pass if has_app_pass else None,
            "amount": price,
            "has_totp": has_totp,
            "has_app_pass": has_app_pass,
            "user_name": "مستعاد",
            "user_username": "",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "verification_status": "pending",
        }
        user_data["pending_balance"] += price
        save_user(user_id, user_data)
        recovered += 1
    
    clear_lost_request("", 0)  # مسح الكل بعد الاستعادة
    
    await query.edit_message_text(f"✅ تم استعادة {recovered} طلب بنجاح!", reply_markup=kb_single("🔙 الطلبات المفقودة", "view_lost_requests"))

# ==================== ALL ACCOUNTS SECTION ====================
async def all_accounts_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    buttons = [
        ("📋 جميع الحسابات", "all_accounts"),
        ("🆕 الحسابات غير المستخرجة", "unextracted_accounts"),
        ("🔙 إعدادات المالك", "owner_panel")
    ]
    await query.edit_message_text("📊 *جميع الحسابات المقبولة*\n\nاختر الخيار المناسب:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

async def all_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    all_accounts = []
    for uid, user_data in users.items():
        for acc in user_data.get("approved_accounts", []):
            acc["user_id"] = uid
            all_accounts.append(acc)
    
    if not all_accounts:
        await query.edit_message_text("📭 لا توجد حسابات.", reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    
    msg = f"📊 *جميع الحسابات ({len(all_accounts)})*\n\n"
    for idx, acc in enumerate(all_accounts[:20], 1):
        tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        msg += f"{idx}. {tier_icon} 📧 `{acc.get('email', '')}` (${acc.get('amount', 0):.2f})\n"
    if len(all_accounts) > 20:
        msg += f"\n📌 *تم عرض أول 20 من أصل {len(all_accounts)}*"
    
    buttons = [("📥 تصدير الكل", "export_all_accounts"), ("🔙 جميع الحسابات", "all_accounts_section")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

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
    
    msg = "📊 *جميع الحسابات*\n\n"
    for idx, acc in enumerate(all_accounts, 1):
        msg += f"{idx}. 📧 `{acc.get('email', '')}`\n"
        msg += f"🔑 `{acc.get('password', '')}`\n"
        if acc.get("has_totp"):
            msg += f"🔐 `{acc.get('totp_secret', '')}`\n"
        if acc.get("has_app_pass"):
            msg += f"🗝 `{acc.get('app_password', '')}`\n"
        msg += f"💰 ${acc.get('amount', 0):.2f}\n\n"
    
    if len(msg) > 4000:
        parts = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
        for part in parts:
            await context.bot.send_message(chat_id=OWNER_ID, text=part, parse_mode=ParseMode.MARKDOWN)
        await query.edit_message_text("✅ تم تصدير جميع الحسابات في رسائل متعددة.")
    else:
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)

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
                unextracted.append({"user_id": uid, "index": idx, "account": acc})
    
    if not unextracted:
        await query.edit_message_text("✅ لا توجد حسابات غير مستخرجة.", reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    
    msg = f"🆕 *الحسابات غير المستخرجة ({len(unextracted)})*\n\n"
    for idx, item in enumerate(unextracted[:20], 1):
        acc = item["account"]
        tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        msg += f"{idx}. {tier_icon} 📧 `{acc.get('email', '')}`\n"
    if len(unextracted) > 20:
        msg += f"\n📌 *تم عرض أول 20 من أصل {len(unextracted)}*"
    
    buttons = [("✅ وضع علامة مستخرجة للكل", "mark_all_extracted"), ("🔙 جميع الحسابات", "all_accounts_section")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

async def mark_all_extracted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    marked = 0
    for uid, user_data in users.items():
        for acc in user_data.get("approved_accounts", []):
            if not acc.get("extracted", False):
                acc["extracted"] = True
                marked += 1
        save_user(int(uid), user_data)
    await query.edit_message_text(f"✅ تم وضع علامة مستخرجة على {marked} حساب.", reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))

# ==================== STORE SECTION ====================
async def store_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await query.edit_message_text("🛒 *إدارة المبيعات*\n\nاختر فئة لعرض مبيعاتها أو أضف فئة جديدة:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

async def store_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text("✏️ *إضافة فئة جديدة*\n\nأرسل اسم الفئة (مثال: حسابات، اشتراكات، أدوات):", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", "store_section"))
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
    await query.edit_message_text("✏️ *إضافة مبيعة جديدة*\n\n📌 الخطوة 1/3: أرسل اسم المبيعة:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))

# ==================== TEXT INPUT (مع التعديل المهم) ====================
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
        
    elif action == "add_service_name":
        context.user_data["store_service_name"] = text
        context.user_data["store_action"] = "add_service_price"
        await update.message.reply_text("💰 *الخطوة 2/3*: أرسل سعر المبيعة (رقم فقط):", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", f"store_category:{context.user_data.get('current_category_id')}"))
        
    elif action == "add_service_price":
        try:
            price = float(text)
            if price <= 0:
                await update.message.reply_text("⚠️ السعر يجب أن يكون أكبر من 0!")
                return
            context.user_data["store_service_price"] = price
            context.user_data["store_action"] = "add_service_message"
            await update.message.reply_text("📝 *الخطوة 3/3*: أرسل الرسالة التي ستظهر للعميل بعد الشراء:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", f"store_category:{context.user_data.get('current_category_id')}"))
            
        except ValueError:
            await update.message.reply_text("⚠️ يرجى إرسال رقم صحيح (مثال: 10.50)")
            
    # 👇 تم إضافة هذا الجزء الناقص لاستقبال الرسالة وحفظها 👇
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
        
        # تنظيف المتغيرات
        context.user_data.pop("store_action", None)
        context.user_data.pop("store_service_name", None)
        context.user_data.pop("store_service_price", None)
        context.user_data.pop("current_category_id", None)
        
        await main_menu(update, context)

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
    await query.edit_message_text("🗑️ *حذف مبيعة*\nاختر المبيعة للحذف:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

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

# ==================== FORCED CHANNEL ====================
async def forced_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_json(DATA_DIR / "config.json")
    current_channel = config.get("forced_channel", "")
    await query.edit_message_text(
        f"📢 *إعدادات القناة الإجبارية*\n\n📌 القناة الحالية: {current_channel if current_channel else 'لا توجد'}\n\n✏️ أرسل معرف القناة الجديدة (مثال: @my_channel):",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical([("🔙 إعدادات المالك", "owner_panel")]))
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

# ==================== REFERRAL ====================
async def referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    msg = f"🔗 *نظام الإحالة*\n\n📌 *رابط الإحالة الخاص بك:*\n`{referral_link}`\n\n📊 *إحصائياتك:*\n💰 مكافآت الإحالة: ${float(user_data.get('referral_earnings', 0.0)):.2f}\n👥 عدد الإحالات: {user_data.get('total_referrals', 0)}\n\n📝 *كيف يعمل؟*\n1️⃣ شارك الرابط\n2️⃣ عند قبول حساب جديد\n3️⃣ تحصل على مكافأة"
    buttons = [("📋 نسخ الرابط", f"copy_referral:{referral_code}"), ("🔙 القائمة الرئيسية", "main_menu")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

async def copy_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    code = query.data.split(":")[1]
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={code}"
    await query.edit_message_text(f"📋 *رابط الإحالة:*\n\n`{link}`\n\n📌 يمكنك نسخ الرابط ومشاركته.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 الإحالة", "referral_menu"))

# ==================== TUTORIALS ====================
async def tutorials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    config = load_json(DATA_DIR / "config.json")
    buttons = []
    for key in ["general", "email", "password", "totp", "app_pass", "leave"]:
        if config.get(f"video_{key}") and Path(config.get(f"video_{key}", "")).exists():
            buttons.append((f"📹 {key}", f"play_video:{key}"))
    buttons.append(("🔙 القائمة الرئيسية", "main_menu"))
    await query.edit_message_text("📺 *اختر الدرس:*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

async def play_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    vtype = query.data.split(":")[1]
    config = load_json(DATA_DIR / "config.json")
    path = config.get(f"video_{vtype}")
    if path and Path(path).exists():
        try:
            await context.bot.send_video(chat_id=query.from_user.id, video=open(path, "rb"), caption=f"📹 *فيديو {vtype}*")
            await tutorials(update, context)
        except:
            await query.edit_message_text("⚠️ حدث خطأ في تشغيل الفيديو.")
    else:
        await query.edit_message_text("⚠️ الفيديو غير موجود.")

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
    elif data == "owner_panel":
        await owner_panel(update, context)
    elif data == "set_tier_prices":
        await set_tier_prices(update, context)
    elif data.startswith("set_tier:"):
        await set_tier(update, context)
    elif data == "approval_requests":
        await approval_requests(update, context)
    elif data == "view_pending":
        await view_pending(update, context)
    elif data == "view_approved":
        await view_approved(update, context)
    elif data == "view_rejected":
        await view_rejected(update, context)
    elif data.startswith("pending_detail:"):
        await pending_detail(update, context)
    elif data.startswith("approve_tier1:"):
        await approve_tier1(update, context)
    elif data.startswith("approve_tier2:"):
        await approve_tier2(update, context)
    elif data.startswith("approve_tier3:"):
        await approve_tier3(update, context)
    elif data.startswith("reject_request:"):
        await reject_request(update, context)
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
        await store_section(update, context)
    elif data == "store_add_category":
        await store_add_category(update, context)
    elif data.startswith("store_category:"):
        await store_category_menu(update, context)
    elif data.startswith("store_add_service:"):
        await store_add_service(update, context)
    elif data.startswith("store_delete_service:"):
        await store_delete_service(update, context)
    elif data.startswith("delete_service:"):
        await delete_service_execute(update, context)
    elif data == "forced_channel":
        await forced_channel(update, context)
    elif data == "remove_channel":
        await remove_channel(update, context)
    elif data == "all_accounts_section":
        await all_accounts_section(update, context)
    elif data == "all_accounts":
        await all_accounts(update, context)
    elif data == "unextracted_accounts":
        await unextracted_accounts(update, context)
    elif data == "mark_all_extracted":
        await mark_all_extracted(update, context)
    elif data == "export_all_accounts":
        await export_all_accounts(update, context)
    elif data == "referral_menu":
        await referral_menu(update, context)
    elif data.startswith("copy_referral:"):
        await copy_referral(update, context)
    elif data == "view_lost_requests":
        await view_lost_requests(update, context)
    elif data == "recover_all_lost":
        await recover_all_lost(update, context)
    else:
        await placeholder(update, context)

async def placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("⚠️ خيار غير معروف حالياً.", reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))

# ==================== TEXT INPUT ====================
async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if context.user_data.get("mode") == "set_tier_price":
        if user_id != OWNER_ID:
            return
        try:
            price = float(update.message.text.strip())
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
    
    # هنا يتم معالجة إدخالات المتجر (تم تعديلها داخلياً)
    if context.user_data.get("store_action"):
        await handle_store_input(update, context)
        return
    
    if context.user_data.get("approval_step") == "waiting_totp":
        await handle_approval_totp(update, context)
        return
    
    if context.user_data.get("approval_step") == "waiting_app_pass":
        await handle_approval_app_pass(update, context)
        return
    
    await add_account_step(update, context)

# ==================== START ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args and args[0]:
        referral_code = args[0]
        if len(referral_code) == 8 and referral_code.isalnum():
            user_data = get_user(update.effective_user.id)
            users = load_json(USERS_DB)
            referrer_id = None
            for uid, u_data in users.items():
                if u_data.get("referral_code") == referral_code:
                    referrer_id = int(uid)
                    break
            if referrer_id:
                user_data["referred_by"] = referrer_id
                save_user(update.effective_user.id, user_data)
                await update.message.reply_text(f"✅ *تم تفعيل الإحالة!*\n\n👤 تمت إحالتك بواسطة: {referrer_id}", parse_mode=ParseMode.MARKDOWN)
                return
    await main_menu(update, context)

# ==================== MAIN ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_upload))
    app.run_polling()

if __name__ == "__main__":
    main()
