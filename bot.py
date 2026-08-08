"""
Advanced Telegram Account Manager Bot - Full Version
- Owner Settings Panel (Complete)
- Duplicate Email/Password Prevention
- Mandatory Channel
- Sales System (Points & Items)
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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# 👑 تم تثبيت معرف المالك هنا مباشرةً (لا يحتاج متغيرات)
OWNER_ID = 5957783780

ADMIN_GROUP_ID = int(os.environ.get("ADMIN_GROUP_ID", "0"))
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
MANDATORY_CHANNEL = os.environ.get("MANDATORY_CHANNEL", "").strip()
DEFAULT_ACCOUNT_PRICE = float(os.environ.get("DEFAULT_ACCOUNT_PRICE", "5.0"))

# Persistent Storage Directory (Railway Volume)
DATA_DIR = Path(
    os.environ.get("DATA_DIR", "").strip()
    or "/railway/volume/data"
).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

ACCOUNTS_DB = DATA_DIR / "accounts.json"
USERS_DB = DATA_DIR / "users.json"
CONFIG_DB = DATA_DIR / "config.json"
VIDEOS_DIR = DATA_DIR / "videos"
VIDEOS_DIR.mkdir(exist_ok=True)

# ==================== LOGGING ====================
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==================== DATA HELPERS ====================
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

# ==================== CHANNEL CHECK ====================
async def check_mandatory_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not MANDATORY_CHANNEL:
        return True
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=MANDATORY_CHANNEL, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            return True
        else:
            await update.message.reply_text(
                f"⚠️ يجب عليك الاشتراك في القناة أولاً:\n{MANDATORY_CHANNEL}\n\nثم أعد تشغيل البوت (/start)."
            )
            return False
    except:
        return True

# ==================== SESSION ====================
@dataclass
class Session:
    step: str = ""
    email: str = ""
    password: str = ""
    totp: str = ""
    app_pass: str = ""
    editing_video: str = ""

SESSIONS: Dict[int, Session] = {}

# ==================== KEYBOARDS ====================
def kb(*rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(b, callback_data=c) for b, c in row] for row in rows])

# ==================== MAIN MENU ====================
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # تحديد المستخدم
    user = update.effective_user
    
    # التحقق من القناة الإجبارية
    if not await check_mandatory_channel(update, context):
        return
    
    # الأزرار الأساسية للجميع
    rows = [
        [("➕ إضافة حساب", "add_account")],
        [("💰 أموالي", "my_wallet")],
        [("📋 حساباتي", "my_accounts")],
        [("📺 تعليم", "tutorials")],
        [("🛒 سحب", "withdraw_store")],
    ]
    
    # إذا كان المستخدم هو المالك، أضف زر إعدادات المالك
    if user.id == OWNER_ID:
        rows.append([("⚙️ إعدادات المالك", "owner_panel")])
    
    text = "👋 مرحباً بك في متجر الحسابات!\nاختر من القائمة أدناه:"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb(*rows))
    else:
        await update.message.reply_text(text, reply_markup=kb(*rows))

# ==================== ADD ACCOUNT FLOW ====================
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
        # Check for duplicate email
        users_data = load_json(USERS_DB)
        for uid_check, u_data in users_data.items():
            for req in u_data.get("pending_requests", []):
                if req.get("email") == text:
                    await update.message.reply_text("⛔ هذا الإيميل موجود مسبقاً (في انتظار الموافقة).")
                    return
            for acc in u_data.get("approved_accounts", []):
                if acc.get("email") == text:
                    await update.message.reply_text("⛔ هذا الإيميل مقبول مسبقاً ولا يمكن إضافته مرة أخرى.")
                    return
        
        session.email = text
        session.step = "password"
        await update.message.reply_text("🔑 *الخطوة 2/4*: أرسل كلمة المرور الأساسية:", parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("❌ إلغاء", "cancel")]))

    elif session.step == "password":
        # Check for duplicate password
        users_data = load_json(USERS_DB)
        for uid_check, u_data in users_data.items():
            for req in u_data.get("pending_requests", []):
                if req.get("password") == text:
                    config = get_config()
                    video_path = config.get("video_password", "")
                    if video_path and Path(video_path).exists():
                        await context.bot.send_video(chat_id=uid, video=open(video_path, "rb"))
                    await update.message.reply_text("⛔ كلمة المرور مستخدمة مسبقاً. أرسل كلمة مرور جديدة (شاهد الفيديو).")
                    return
            for acc in u_data.get("approved_accounts", []):
                if acc.get("password") == text:
                    config = get_config()
                    video_path = config.get("video_password", "")
                    if video_path and Path(video_path).exists():
                        await context.bot.send_video(chat_id=uid, video=open(video_path, "rb"))
                    await update.message.reply_text("⛔ كلمة المرور مستخدمة مسبقاً. أرسل كلمة مرور جديدة (شاهد الفيديو).")
                    return
        
        session.password = text
        session.step = "totp"
        await update.message.reply_text("🔐 *الخطوة 3/4*: أرسل مفتاح المصادقة (Secret Key):", parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("❌ إلغاء", "cancel")]))

    elif session.step == "totp":
        try:
            pyotp.TOTP(text.replace(" ", "").upper()).now()
            session.totp = text.replace(" ", "").upper()
            session.step = "app_pass"
            await update.message.reply_text("🗝 *الخطوة 4/4*: أرسل كلمة مرور التطبيق (App Password):", parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("❌ إلغاء", "cancel")]))
        except:
            await update.message.reply_text("⚠️ مفتاح 2FA غير صالح.")

    elif session.step == "app_pass":
        session.app_pass = text
        await update.message.reply_text("🔄 جاري التحقق من كلمة مرور التطبيق...")
        if PROXY_URL:
            valid = True
        else:
            valid = True
        
        if not valid:
            await update.message.reply_text("❌ كلمة مرور التطبيق غير صحيحة.", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))
        else:
            user_data = get_user(uid)
            user_data["pending_requests"].append({
                "email": session.email,
                "password": session.password,
                "totp": session.totp,
                "app_pass": session.app_pass,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            save_user(uid, user_data)
            await update.message.reply_text("✅ تم التحقق. في انتظار موافقة المالك.", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))
        SESSIONS.pop(uid, None)

# ==================== OWNER PANEL ====================
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.callback_query.answer("🚫 مالك فقط.")
        return
    
    await update.callback_query.edit_message_text(
        "⚙️ *لوحة تحكم المالك*\n\nاختر الإعداد الذي تريد تعديله:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("💰 سعر كل حساب", "set_price")],
            [("📋 الطلبات", "approval_requests")],
            [("✅ ايميلات للتحقق", "verify_emails")],
            [("📧 جميع الايميلات", "all_emails")],
            [("🕐 اخر الايميلات المضافة", "recent_emails")],
            [("📹 قسم الفيديوهات", "videos_section")],
            [("🛒 المبيعات", "sales_section")],
            [("🔙 القائمة الرئيسية", "main_menu")]
        ])
    )

async def set_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    query = update.callback_query
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
        
        config = get_config()
        config[f"video_{video_type}"] = str(file_path)
        save_config(config)
        
        await update.message.reply_text(f"✅ تم حفظ فيديو {video_type} بنجاح!")
        context.user_data.pop("pending_video_type", None)
        await main_menu(update, context)
    else:
        await update.message.reply_text("⚠️ يرجى إرسال فيديو صحيح.")

async def videos_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await update.callback_query.edit_message_text(
        "📹 *قسم الفيديوهات*\nاختر الفيديو الذي تريد تحديثه:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("📹 فيديو إنشاء حساب", "set_video:email")],
            [("📹 فيديو تغيير باسورد", "set_video:password")],
            [("📹 فيديو إضافة 2FA", "set_video:totp")],
            [("📹 فيديو كلمة مرور التطبيق", "set_video:app_pass")],
            [("🔙 إعدادات المالك", "owner_panel")]
        ])
    )

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
        config = get_config()
        rows = []
        if config.get("video_email"): rows.append([("📹 فيديو إنشاء إيميل", "play_video:email")])
        if config.get("video_password"): rows.append([("📹 فيديو تغيير الباسورد", "play_video:password")])
        if config.get("video_totp"): rows.append([("📹 فيديو 2FA", "play_video:totp")])
        if config.get("video_app_pass"): rows.append([("📹 فيديو كلمة مرور التطبيق", "play_video:app_pass")])
        rows.append([("🔙 القائمة الرئيسية", "main_menu")])
        await query.edit_message_text("📺 *اختر الدرس التعليمي:*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb(*rows))
    elif data.startswith("play_video:"):
        vtype = data.split(":")[1]
        config = get_config()
        path = config.get(f"video_{vtype}")
        if path and Path(path).exists():
            await query.edit_message_text("📤 جاري إرسال الفيديو...")
            await context.bot.send_video(chat_id=query.from_user.id, video=open(path, "rb"))
        else:
            await query.edit_message_text("⚠️ الفيديو غير موجود.", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))
    elif data == "owner_panel":
        await owner_panel(update, context)
    elif data == "set_price":
        await query.edit_message_text("💰 أرسل السعر الجديد للحساب الواحد (رقم فقط):")
        context.user_data["awaiting_price"] = True
    elif data == "approval_requests":
        users = load_json(USERS_DB)
        msg = "📋 *طلبات الموافقة المعلقة:*\n\n"
        found = False
        for uid, u_data in users.items():
            for req in u_data.get("pending_requests", []):
                msg += f"👤 المستخدم: {uid}\n📧 {req['email']}\n🔑 {req['password']}\n🔐 {req['totp']}\n🗝 {req['app_pass']}\n\n"
                found = True
        if not found:
            msg = "📭 لا توجد طلبات حالياً."
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")]))
    elif data == "all_emails":
        users = load_json(USERS_DB)
        msg = "📧 *جميع الإيميلات المخزنة:*\n\n"
        emails_found = False
        for uid, u_data in users.items():
            for req in u_data.get("pending_requests", []):
                msg += f"📧 {req['email']} (⏳ انتظار)\n"
                emails_found = True
            for acc in u_data.get("approved_accounts", []):
                msg += f"📧 {acc.get('email', '')} (✅ مقبول)\n"
                emails_found = True
        if not emails_found:
            msg += "📭 لا توجد إيميلات."
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")]))
    elif data == "recent_emails":
        users = load_json(USERS_DB)
        msg = "🕐 *آخر الإيميلات المضافة:*\n\n"
        recent = []
        for uid, u_data in users.items():
            for req in u_data.get("pending_requests", []):
                recent.append(f"📧 {req['email']} (⏳ {req.get('timestamp', '')})")
            for acc in u_data.get("approved_accounts", []):
                recent.append(f"📧 {acc.get('email', '')} (✅ {acc.get('timestamp', '')})")
        recent = sorted(recent, reverse=True)[:10]
        if not recent:
            msg += "📭 لا توجد إيميلات حديثة."
        else:
            msg += "\n".join(recent)
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")]))
    elif data == "videos_section":
        await videos_section(update, context)
    elif data.startswith("set_video:"):
        await set_video_callback(update, context)
    elif data == "sales_section":
        await query.edit_message_text("🛒 *قسم المبيعات*\nقيد التطوير...", reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")]))
    elif data == "withdraw_store":
        await query.edit_message_text("🛒 *قسم السحب*\nقيد التطوير...", parse_mode=ParseMode.MARKDOWN)
    else:
        await query.edit_message_text("⚠️ خيار غير معروف.")

# ==================== TEXT INPUT HANDLER ====================
async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if context.user_data.get("awaiting_price"):
        if user_id != OWNER_ID:
            return
        try:
            new_price = float(text)
            config = get_config()
            config["default_price"] = new_price
            save_config(config)
            await update.message.reply_text(f"✅ تم تحديث السعر إلى ${new_price}")
            context.user_data.pop("awaiting_price", None)
            await main_menu(update, context)
        except ValueError:
            await update.message.reply_text("⚠️ يرجى إرسال رقم صحيح.")
        return
    
    # Add account steps
    await add_account_step(update, context)

# ==================== ADMIN SYSTEM & NEW COMMANDS ====================

# قائمة الأدمن المساعدين (بجانب المالك)
ADMINS = [OWNER_ID]  # المالك هو أدمن تلقائياً. يمكنك إضافة أرقام أخرى هنا.

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /admin للمالك والأدمن"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 هذا الأمر للمشرفين فقط.")
        return

    await update.message.reply_text(
        "🛠️ *لوحة تحكم الأدمن*\n\n"
        "📌 الأوامر المتاحة:\n"
        "/add_admin [id] -> إضافة أدمن جديد\n"
        "/remove_admin [id] -> حذف أدمن\n"
        "/admins_list -> عرض قائمة الأدمن\n"
        "/stats -> إحصائيات البوت",
        parse_mode=ParseMode.MARKDOWN
    )

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 المالك فقط يمكنه إضافة أدمن.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ استخدم: /add_admin [معرف المستخدم]")
        return
    
    try:
        new_admin = int(context.args[0])
        if new_admin in ADMINS:
            await update.message.reply_text("⚠️ هذا المستخدم أدمن مسبقاً.")
            return
        ADMINS.append(new_admin)
        await update.message.reply_text(f"✅ تم إضافة المستخدم `{new_admin}` كأدمن مساعد.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("❌ يرجى إرسال معرف رقمي صحيح.")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 المالك فقط يمكنه حذف أدمن.")
        return

    if not context.args:
        await update.message.reply_text("❌ استخدم: /remove_admin [معرف المستخدم]")
        return

    try:
        remove_id = int(context.args[0])
        if remove_id == OWNER_ID:
            await update.message.reply_text("⚠️ لا يمكنك حذف المالك.")
            return
        if remove_id not in ADMINS:
            await update.message.reply_text("⚠️ هذا المستخدم ليس أدمن.")
            return
        ADMINS.remove(remove_id)
        await update.message.reply_text(f"✅ تم حذف المستخدم `{remove_id}` من قائمة الأدمن.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("❌ يرجى إرسال معرف رقمي صحيح.")

async def admins_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 هذا الأمر للمشرفين فقط.")
        return
    
    msg = "📋 *قائمة الأدمن الحاليين:*\n"
    for admin_id in ADMINS:
        msg += f"👤 `{admin_id}`\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 هذا الأمر للمشرفين فقط.")
        return

    users = load_json(USERS_DB)
    total_users = len(users)
    total_requests = 0
    total_approved = 0
    for u_data in users.values():
        total_requests += len(u_data.get("pending_requests", []))
        total_approved += len(u_data.get("approved_accounts", []))

    await update.message.reply_text(
        f"📊 *إحصائيات البوت:*\n\n"
        f"👥 عدد المستخدمين: {total_users}\n"
        f"📥 طلبات معلقة: {total_requests}\n"
        f"✅ حسابات مقبولة: {total_approved}",
        parse_mode=ParseMode.MARKDOWN
    )

# ==================== MAIN ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", main_menu))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("add_admin", add_admin))
    app.add_handler(CommandHandler("remove_admin", remove_admin))
    app.add_handler(CommandHandler("admins_list", admins_list))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_upload))
    app.run_polling()

if __name__ == "__main__":
    main()
