"""
Advanced Telegram Account Manager Bot - Full Version with Referral System
- Owner Panel (Fully Fixed)
- Add Account Flow
- Video System (Upload & Play)
- Store System (Categories & Services)
- Wallet & Balance
- Forced Channel
- All Accounts Management
- Referral System
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
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
        "approved_accounts": [],
        "pending_requests": [],
        "referral_code": "",
        "referred_by": None,
        "referral_earnings": 0.0,
        "total_referrals": 0
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

SESSIONS: Dict[int, Session] = {}

# ==================== MAIN MENU ====================
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rows = [
        [("➕ إضافة حساب", "add_account")],
        [("💰 أموالي", "my_wallet")],
        [("📋 حساباتي", "my_accounts")],
        [("📺 تعليم", "tutorials")],
        [("🛒 سحب", "withdraw_store")],
        [("🔗 الإحالة", "referral_menu")],
    ]
    if user.id == OWNER_ID:
        rows.append([("⚙️ إعدادات المالك", "owner_panel")])

    # Check forced channel
    config = load_json(DATA_DIR / "config.json")
    forced_channel = config.get("forced_channel", "")
    if forced_channel:
        try:
            member = await context.bot.get_chat_member(forced_channel, user.id)
            if member.status not in ["member", "administrator", "creator"]:
                await update.message.reply_text(
                    f"📢 *يرجى الانضمام إلى القناة أولاً:*\n{forced_channel}\n\nثم اضغط /start مرة أخرى.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
        except:
            pass

    text = "👋 مرحباً بك!\nاختر من القائمة أدناه:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb(*rows))
    else:
        await update.message.reply_text(text, reply_markup=kb(*rows))

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
            [("💰 سعر كل حساب", "set_price")],
            [("📋 الطلبات", "approval_requests")],
            [("📹 قسم الفيديوهات", "videos_section")],
            [("🛒 المبيعات", "store_section")],
            [("📢 قناة إجبارية", "forced_channel")],
            [("📊 جميع الحسابات المقبولة", "all_accounts_section")],
            [("🔗 نظام الإحالة", "referral_settings")],
            [("🔙 القائمة الرئيسية", "main_menu")]
        ])
    )

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
            [("🔙 إعدادات المالك", "owner_panel")]
        ])
    )

async def all_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display all approved accounts across all users"""
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

    # Send accounts in batches of 5
    total = len(all_accounts)
    msg = f"📊 *إجمالي الحسابات: {total}*\n\n"
    
    for idx, acc in enumerate(all_accounts[:10], 1):  # Show first 10
        msg += f"{idx}. 📧 `{acc.get('email', '')}`\n"
        msg += f"   🔑 {acc.get('password', '')}\n"
        msg += f"   🔐 {acc.get('totp', '')}\n"
        msg += f"   🗝 {acc.get('app_pass', '')}\n"
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
            [("🔙 جميع الحسابات", "all_accounts_section")]
        ])
    )

async def unextracted_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display accounts that haven't been extracted yet"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    users = load_json(USERS_DB)
    unextracted = []
    
    for uid, user_data in users.items():
        for acc in user_data.get("approved_accounts", []):
            if not acc.get("extracted", False):
                acc_copy = acc.copy()
                acc_copy["user_id"] = uid
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
        msg += f"{idx}. 📧 `{acc.get('email', '')}`\n"
        msg += f"   🔑 {acc.get('password', '')}\n"
        msg += f"   🔐 {acc.get('totp', '')}\n"
        msg += f"   🗝 {acc.get('app_pass', '')}\n"
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
    """Menu to mark accounts as extracted"""
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
    """Mark a specific account as extracted"""
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
    """Export all approved accounts as a single message"""
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

    # Prepare the export message
    export_msg = "📊 *جميع الحسابات المقبولة*\n"
    export_msg += "═" * 30 + "\n\n"
    
    for idx, acc in enumerate(all_accounts, 1):
        export_msg += f"📧 {idx}. {acc.get('email', '')}\n"
        export_msg += f"🔑 {acc.get('password', '')}\n"
        export_msg += f"🔐 {acc.get('totp', '')}\n"
        export_msg += f"🗝 {acc.get('app_pass', '')}\n"
        export_msg += f"💰 ${acc.get('amount', 0):.2f}\n"
        export_msg += "─" * 20 + "\n"

    # Split into multiple messages if too long
    max_length = 4000
    if len(export_msg) > max_length:
        parts = [export_msg[i:i+max_length] for i in range(0, len(export_msg), max_length)]
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
    """Export only unextracted accounts"""
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
        export_msg += f"📧 {idx}. {acc.get('email', '')}\n"
        export_msg += f"🔑 {acc.get('password', '')}\n"
        export_msg += f"🔐 {acc.get('totp', '')}\n"
        export_msg += f"🗝 {acc.get('app_pass', '')}\n"
        export_msg += "─" * 20 + "\n"

    # Mark all as extracted after export
    for uid, user_data in users.items():
        for acc in user_data.get("approved_accounts", []):
            if not acc.get("extracted", False):
                acc["extracted"] = True
        save_user(int(uid), user_data)

    # Split into multiple messages if too long
    max_length = 4000
    if len(export_msg) > max_length:
        parts = [export_msg[i:i+max_length] for i in range(0, len(export_msg), max_length)]
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

# ==================== OWNER PANEL: SET PRICE ====================
async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text("💰 أرسل السعر الجديد للحساب الواحد (رقم فقط):")
    context.user_data["mode"] = "set_price"

# ==================== OWNER PANEL: APPROVAL REQUESTS ====================
async def approval_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    users = load_json(USERS_DB)
    found = False
    for uid, u_data in users.items():
        for req in u_data.get("pending_requests", []):
            found = True
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"📋 *طلب موافقة*\n👤 المستخدم: {uid}\n📧 {req['email']}\n🔑 {req['password']}\n🔐 {req['totp']}\n🗝 {req['app_pass']}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb([
                    [("✅ قبول", f"approve:{uid}:{req['email']}")],
                    [("❌ رفض", f"reject:{uid}:{req['email']}")]
                ])
            )
    
    if not found:
        await query.edit_message_text("📭 لا توجد طلبات حالياً.", reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")]))
    else:
        await query.edit_message_text("📋 *تم إرسال الطلبات إليك.*", reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")]))

async def approve_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    data = query.data.split(":")
    uid = int(data[1])
    email = data[2]

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
            reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")]),
        )
        return

    price = float(approved_request.get("amount", default_price))
    
    # Add to approved accounts (not extracted yet)
    approved_request["extracted"] = False
    user_data.setdefault("approved_accounts", []).append(approved_request)
    user_data["pending_balance"] = max(
        0.0,
        float(user_data.get("pending_balance", 0.0)) - price,
    )
    user_data["balance"] = float(user_data.get("balance", 0.0)) + price
    user_data["pending_requests"] = [
        req for req in user_data.get("pending_requests", []) if req["email"] != email
    ]
    save_user(uid, user_data)

    # Handle referral bonus
    referred_by = user_data.get("referred_by")
    if referred_by:
        referral_bonus = float(config.get("referral_bonus", 0.0))
        if referral_bonus > 0:
            referrer_data = get_user(referred_by)
            referrer_data["referral_earnings"] = float(referrer_data.get("referral_earnings", 0.0)) + referral_bonus
            referrer_data["total_referrals"] = int(referrer_data.get("total_referrals", 0)) + 1
            save_user(referred_by, referrer_data)
            
            # Notify referrer
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=f"🎉 *مبروك!*\nحصلت على مكافأة إحالة بقيمة ${referral_bonus:.2f}\n"
                         f"بسبب إحالة المستخدم {uid} الذي أضاف حساباً جديداً.",
                    parse_mode=ParseMode.MARKDOWN
                )
            except:
                pass

    await query.edit_message_text(
        f"✅ تم قبول الحساب `{email}`!\n💰 تم نقل ${price:.2f} من قيد الانتظار إلى الرصيد المملوك.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def reject_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    data = query.data.split(":")
    uid = int(data[1])
    email = data[2]

    user_data = get_user(uid)
    pending_requests = user_data.get("pending_requests", [])
    rejected_requests = [req for req in pending_requests if req["email"] == email]
    if not rejected_requests:
        await query.edit_message_text(
            "⚠️ هذا الطلب غير موجود أو تمت معالجته مسبقاً.",
            reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")]),
        )
        return

    rejected_total = sum(float(req.get("amount", 0.0)) for req in rejected_requests)
    user_data["pending_balance"] = max(
        0.0,
        float(user_data.get("pending_balance", 0.0)) - rejected_total,
    )
    user_data["pending_requests"] = [
        req for req in pending_requests if req["email"] != email
    ]
    save_user(uid, user_data)

    await query.edit_message_text(
        f"❌ تم رفض الحساب `{email}` وإزالة المبلغ من قيد الانتظار.",
        parse_mode=ParseMode.MARKDOWN,
    )

# ==================== OWNER PANEL: REFERRAL SETTINGS ====================
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

# ==================== REFERRAL MENU ====================
async def referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    user_data = get_user(user_id)
    referral_code = user_data.get("referral_code", "")
    
    # Generate referral code if not exists
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
        f"👥 عدد الإحالات: {user_data.get('total_referrals', 0)}\n\n"
        f"📝 *كيف يعمل النظام؟*\n"
        f"1️⃣ شارك رابط الإحالة مع أصدقائك\n"
        f"2️⃣ عند إضافة صديقك لحساب جديد وقبوله من المالك\n"
        f"3️⃣ ستحصل على مكافأة إحالة لكل حساب مقبول\n"
        f"4️⃣ كلما زاد عدد الحسابات، زادت مكافآتك!"
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
    """Handle copying referral link (just show it again)"""
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

# ==================== OWNER PANEL: VIDEO SECTION ====================
async def videos_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    await query.edit_message_text(
        "📹 *قسم الفيديوهات*\nاختر الفيديو الذي تريد تحديثه:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("📹 فيديو إنشاء إيميل", "set_video:email")],
            [("📹 فيديو تغيير باسورد", "set_video:password")],
            [("📹 فيديو إضافة 2FA", "set_video:totp")],
            [("📹 فيديو كلمة مرور التطبيق", "set_video:app_pass")],
            [("🔙 إعدادات المالك", "owner_panel")]
        ])
    )

async def set_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    video_type = query.data.split(":")[1]
    context.user_data["pending_video_type"] = video_type
    await query.edit_message_text(f"📤 *أرسل الفيديو الخاص بـ {video_type} الآن (كملف فيديو):*", parse_mode=ParseMode.MARKDOWN)

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

# ==================== OWNER PANEL: STORE SECTION ====================
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
        "✏️ *إضافة مبيعة جديدة*\n\n📌 الخطوة 1/2: أرسل اسم المبيعة:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 إلغاء", f"store_category:{cat_id}")])
    )

# ==================== DELETE SERVICE ====================
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

# ==================== ADD ACCOUNT FLOW ====================
async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    SESSIONS[uid] = Session(step="email")
    
    config = load_json(DATA_DIR / "config.json")
    price = config.get("default_price", 5.0)
    
    await update.callback_query.edit_message_text(
        f"📝 *إضافة حساب جديد*\n💵 *سعر الحساب الواحد هو ${price}*\n\n📧 *الخطوة 1/4*: أرسل الإيميل:",
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
            secret = text.replace(" ", "").upper()
            code = pyotp.TOTP(secret).now()
            session.totp = secret
            session.step = "app_pass"
            await update.message.reply_text(
                f"✅ مفتاح المصادقة صالح!\n\n🔢 *الكود الحالي:* `{code}`\n\n🗝 *الخطوة 4/4*: أرسل كلمة مرور التطبيق:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb([("❌ إلغاء", "cancel")])
            )
        except:
            await update.message.reply_text("⚠️ مفتاح 2FA غير صالح.")

    elif session.step == "app_pass":
        session.app_pass = text
        user_data = get_user(uid)
        config = load_json(DATA_DIR / "config.json")
        price = float(config.get("default_price", 5.0))
        
        # Check if user was referred
        referred_by = user_data.get("referred_by")
        if referred_by:
            referral_bonus = float(config.get("referral_bonus", 0.0))
            if referral_bonus > 0:
                # Add note about referral bonus
                pass  # Will be handled in approval
        
        user_data["pending_requests"].append({
            "email": session.email,
            "password": session.password,
            "totp": session.totp,
            "app_pass": session.app_pass,
            "amount": price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "extracted": False
        })
        user_data["pending_balance"] = float(
            user_data.get("pending_balance", 0.0)
        ) + price
        save_user(uid, user_data)
        SESSIONS.pop(uid, None)
        await update.message.reply_text(
            f"✅ تم إرسال الطلب للمالك للموافقة!\n⏳ تمت إضافة ${price:.2f} إلى الأموال قيد الانتظار.",
            reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
        )

# ==================== USER WITHDRAW STORE ====================
async def withdraw_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    config = load_json(DATA_DIR / "config.json")
    categories = config.get("store_categories", [])
    
    if not categories:
        await query.edit_message_text("🛒 *قسم السحب*\n\nلا توجد فئات حالياً.", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))
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
    query = update.callback_query
    cat_id = query.data.split(":", 1)[1]
    config = load_json(DATA_DIR / "config.json")
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.", reply_markup=kb([("🔙 قسم السحب", "withdraw_store")]))
        return
    
    services = category.get("services", [])
    if not services:
        await query.edit_message_text("📭 لا توجد خدمات في هذه الفئة.", reply_markup=kb([("🔙 قسم السحب", "withdraw_store")]))
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
    query = update.callback_query
    parts = query.data.split(":")
    service_id = parts[1]
    cat_id = parts[2]
    user_id = query.from_user.id

    config = load_json(DATA_DIR / "config.json")
    service = None
    for cat in config["store_categories"]:
        if cat["id"] == cat_id:
            for s in cat["services"]:
                if s["id"] == service_id:
                    service = s
                    break
            break
    
    if not service:
        await query.edit_message_text("⚠️ الخدمة غير موجودة.", reply_markup=kb([("🔙 قسم السحب", "withdraw_store")]))
        return

    user_data = get_user(user_id)
    if user_data["balance"] < service["price"]:
        await query.edit_message_text(f"❌ رصيدك غير كافٍ. الرصيد: ${user_data['balance']:.2f}, السعر: ${service['price']:.2f}")
        return

    user_data["balance"] -= service["price"]
    save_user(user_id, user_data)
    await query.edit_message_text(f"✅ تم شراء الخدمة بنجاح!\n🛒 {service['name']}\n💰 تم خصم ${service['price']:.2f}")

# ==================== MY WALLET / MY ACCOUNTS ====================
async def my_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = get_user(query.from_user.id)
    await query.edit_message_text(
        f"💰 *أموالي*\n\n⏳ قيد الانتظار: ${float(user.get('pending_balance', 0.0)):.2f}\n✅ الرصيد المملوك: ${float(user.get('balance', 0.0)):.2f}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
    )

async def my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = get_user(query.from_user.id)
    accs = user.get("approved_accounts", [])
    if not accs:
        await query.edit_message_text("📭 لا توجد حسابات لديك.")
    else:
        msg = "📋 *حساباتك:*\n"
        for a in accs:
            msg += f"📧 `{a.get('email', '')}`\n"
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))

# ==================== TEXT INPUT ====================
async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Handle referral code from /start command
    if context.user_data.get("referral_code"):
        ref_code = context.user_data.pop("referral_code")
        await handle_referral(update, context, ref_code)
        return

    if context.user_data.get("mode") == "set_price":
        if user_id != OWNER_ID: return
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
        if user_id != OWNER_ID: return
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
        config["forced_channel"] = text
        save_json(DATA_DIR / "config.json", config)
        await update.message.reply_text(f"✅ تم تعيين القناة: {text}")
        context.user_data.pop("store_action", None)
        await main_menu(update, context)

    elif action == "add_service_name":
        context.user_data["store_service_name"] = text
        context.user_data["store_action"] = "add_service_price"
        await update.message.reply_text(
            "💰 *الخطوة 2/2*: أرسل سعر المبيعة (رقم فقط):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb([("🔙 إلغاء", f"store_category:{context.user_data.get('current_category_id')}")])
        )

    elif action == "add_service_price":
        try:
            price = float(text)
            if price <= 0:
                await update.message.reply_text("⚠️ السعر يجب أن يكون أكبر من 0!")
                return
                
            name = context.user_data["store_service_name"]
            cat_id = context.user_data["current_category_id"]
            
            config = load_json(DATA_DIR / "config.json")
            for cat in config["store_categories"]:
                if cat["id"] == cat_id:
                    cat["services"].append({
                        "id": str(time.time_ns()), 
                        "name": name, 
                        "price": price
                    })
                    break
            save_json(DATA_DIR / "config.json", config)
            
            await update.message.reply_text(
                f"✅ تم إضافة المبيعة بنجاح!\n"
                f"📌 الاسم: {name}\n"
                f"💰 السعر: ${price:.2f}"
            )
            context.user_data.pop("store_action", None)
            context.user_data.pop("store_service_name", None)
            context.user_data.pop("current_category_id", None)
            await main_menu(update, context)
            
        except ValueError:
            await update.message.reply_text("⚠️ يرجى إرسال رقم صحيح (مثال: 10.50)")

# ==================== TUTORIALS ====================
async def tutorials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    config = load_json(DATA_DIR / "config.json")
    rows = []
    if config.get("video_email"): rows.append([("📹 إنشاء إيميل", "play_video:email")])
    if config.get("video_password"): rows.append([("📹 تغيير باسورد", "play_video:password")])
    if config.get("video_totp"): rows.append([("📹 إضافة 2FA", "play_video:totp")])
    if config.get("video_app_pass"): rows.append([("📹 كلمة مرور التطبيق", "play_video:app_pass")])
    rows.append([("🔙 القائمة الرئيسية", "main_menu")])
    await query.edit_message_text("📺 *اختر الدرس:*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb(*rows))

async def play_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    vtype = query.data.split(":")[1]
    config = load_json(DATA_DIR / "config.json")
    path = config.get(f"video_{vtype}")
    if path and Path(path).exists():
        await context.bot.send_video(chat_id=query.from_user.id, video=open(path, "rb"))
    else:
        await query.edit_message_text("⚠️ الفيديو غير موجود.", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))

# ==================== REFERRAL HANDLER ====================
async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE, referral_code: str):
    """Handle referral when user starts with a referral code"""
    user_id = update.effective_user.id
    
    # Don't allow self-referral
    if context.user_data.get("my_referral_code") == referral_code:
        await update.message.reply_text("⚠️ لا يمكنك استخدام رابط الإحالة الخاص بك!")
        return
    
    user_data = get_user(user_id)
    
    # Check if user already has a referrer
    if user_data.get("referred_by"):
        await update.message.reply_text("ℹ️ أنت بالفعل مشترك في نظام الإحالة.")
        return
    
    # Find user with this referral code
    users = load_json(USERS_DB)
    referrer_id = None
    for uid, u_data in users.items():
        if u_data.get("referral_code") == referral_code:
            referrer_id = int(uid)
            break
    
    if not referrer_id:
        await update.message.reply_text("❌ رابط الإحالة غير صالح.")
        return
    
    # Save referral
    user_data["referred_by"] = referrer_id
    save_user(user_id, user_data)
    
    await update.message.reply_text(
        "✅ *تم تفعيل الإحالة بنجاح!*\n\n"
        f"👤 تمت إحالتك بواسطة: {referrer_id}\n"
        "📌 ستتلقى أنت وصاحب الإحالة مكافآت عند قبول حساباتك.\n\n"
        "استخدم /start للبدء.",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Notify referrer
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

async def start_with_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with referral code"""
    args = context.args
    if args and args[0].startswith("ref_"):
        referral_code = args[0].replace("ref_", "")
        context.user_data["referral_code"] = referral_code
        
        # Get user's own referral code if exists
        user_data = get_user(update.effective_user.id)
        context.user_data["my_referral_code"] = user_data.get("referral_code", "")
        
        await handle_referral(update, context, referral_code)
    else:
        await main_menu(update, context)

# ==================== ROUTER ====================
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    await query.answer()
    data = query.data

    if data == "main_menu": await main_menu(update, context)
    elif data == "add_account": await add_account_start(update, context)
    elif data == "cancel": await add_account_cancel(update, context)
    elif data == "my_wallet": await my_wallet(update, context)
    elif data == "my_accounts": await my_accounts(update, context)
    elif data == "tutorials": await tutorials(update, context)
    elif data.startswith("play_video:"): await play_video(update, context)
    elif data == "owner_panel": await owner_panel(update, context)
    elif data == "set_price": await set_price(update, context)
    elif data == "approval_requests": await approval_requests(update, context)
    elif data.startswith("approve:"): await approve_request(update, context)
    elif data.startswith("reject:"): await reject_request(update, context)
    elif data == "videos_section": await videos_section(update, context)
    elif data.startswith("set_video:"): await set_video_callback(update, context)
    elif data == "store_section": await owner_store_section(update, context)
    elif data == "store_add_category": await store_add_category(update, context)
    elif data.startswith("store_category:"): await store_category_menu(update, context)
    elif data.startswith("store_add_service:"): await store_add_service(update, context)
    elif data.startswith("store_delete_service:"): await store_delete_service(update, context)
    elif data.startswith("delete_service:"): await delete_service_execute(update, context)
    elif data == "forced_channel": await forced_channel(update, context)
    elif data == "remove_channel": await remove_channel(update, context)
    elif data == "withdraw_store": await withdraw_store(update, context)
    elif data.startswith("user_category:"): await user_category_menu(update, context)
    elif data.startswith("user_buy:"): await user_buy_service(update, context)
    elif data == "all_accounts_section": await all_accounts_section(update, context)
    elif data == "all_accounts": await all_accounts(update, context)
    elif data == "unextracted_accounts": await unextracted_accounts(update, context)
    elif data == "export_all_accounts": await export_all_accounts(update, context)
    elif data == "export_unextracted": await export_unextracted(update, context)
    elif data == "mark_extracted_menu": await mark_extracted_menu(update, context)
    elif data.startswith("mark_extracted:"): await mark_extracted(update, context)
    elif data == "referral_menu": await referral_menu(update, context)
    elif data.startswith("copy_referral:"): await copy_referral(update, context)
    elif data == "referral_settings": await referral_settings(update, context)
    elif data == "set_referral_bonus": await set_referral_bonus(update, context)
    elif data == "referral_stats": await referral_stats(update, context)
    else: await placeholder(update, context)

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
            [("💰 سعر كل حساب", "set_price")],
            [("📋 الطلبات", "approval_requests")],
            [("📹 قسم الفيديوهات", "videos_section")],
            [("🛒 المبيعات", "store_section")],
            [("📢 قناة إجبارية", "forced_channel")],
            [("📊 جميع الحسابات المقبولة", "all_accounts_section")],
            [("🔗 نظام الإحالة", "referral_settings")],
            [("🔙 القائمة الرئيسية", "main_menu")]
        ])
    )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with or without referral code"""
    args = context.args
    if args and args[0]:
        # Check if it's a referral code
        referral_code = args[0]
        if len(referral_code) == 8 and referral_code.isalnum():  # Generated codes are 8 chars
            context.user_data["referral_code"] = referral_code
            user_data = get_user(update.effective_user.id)
            context.user_data["my_referral_code"] = user_data.get("referral_code", "")
            await handle_referral(update, context, referral_code)
            return
    
    await main_menu(update, context)

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
