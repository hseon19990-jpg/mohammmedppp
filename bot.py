"""
Advanced Telegram Account Manager Bot - Full Store System
- Owner Approval System with Price
- Wallet System (Pending & Available)
- Store System (Games, Cards, etc.)
- Integrated Video Tutorials
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

# Video URLs
VIDEO_EMAIL = clean_env_value("VIDEO_EMAIL")
VIDEO_PASSWORD = clean_env_value("VIDEO_PASSWORD")
VIDEO_2FA = clean_env_value("VIDEO_2FA")
VIDEO_APP_PASS = clean_env_value("VIDEO_APP_PASS")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/railway/volume/data")).resolve()
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
    return users.get(str(user_id), {
        "balance": 0.0,
        "pending_balance": 0.0,
        "approved_accounts": [],
        "pending_requests": [],
        "uid": str(user_id)
    })

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

# ==================== SESSION ====================
@dataclass
class Session:
    step: str = ""
    email: str = ""
    password: str = ""
    totp: str = ""
    app_pass: str = ""
    is_owner_pending: bool = False

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
    ]
    if user.id == OWNER_ID:
        rows.append([("⚙️ إعدادات المالك", "owner_panel")])

    text = "👋 مرحباً بك!\nاختر من القائمة أدناه:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb(*rows))
    else:
        await update.message.reply_text(text, reply_markup=kb(*rows))

# ==================== ADD ACCOUNT FLOW ====================
async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    SESSIONS[uid] = Session(step="email")
    
    # جلب سعر الحساب الحالي
    config = get_config()
    price = config.get("default_price", DEFAULT_ACCOUNT_PRICE)
    
    text = (
        f"📝 *إضافة حساب جديد*\n"
        f"💵 *سعر الحساب الواحد هو ${price}*\n\n"
        f"📧 *الخطوة 1/4*: أرسل الإيميل:"
    )
    await update.callback_query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(
            [("📹 مشاهدة طريقة انشاء ايميل", "tutorial_email")],
            [("❌ إلغاء", "cancel")]
        )
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
        await update.message.reply_text(
            "🔑 *الخطوة 2/4*: أرسل كلمة المرور الأساسية:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb(
                [("🔐 نسيت كلمة السر", "tutorial_password")],
                [("❌ إلغاء", "cancel")]
            )
        )

    elif session.step == "password":
        session.password = text
        session.step = "totp"
        await update.message.reply_text(
            "🔐 *الخطوة 3/4*: أرسل رمز المصادقة (Secret Key):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb(
                [("📹 كيف ارسال رمز المصادقة", "tutorial_2fa")],
                [("❌ إلغاء", "cancel")]
            )
        )

    elif session.step == "totp":
        try:
            secret = text.replace(" ", "").upper()
            code = pyotp.TOTP(secret).now()
            session.totp = secret
            session.step = "app_pass"
            await update.message.reply_text(
                f"✅ مفتاح المصادقة صالح!\n\n🔢 *الكود الحالي:* `{code}`\n\n🗝 *الخطوة 4/4*: أرسل كلمة مرور التطبيق (App Password):",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb(
                    [("📹 كيف العثور على كلمة مرور التطبيق", "tutorial_app_pass")],
                    [("❌ إلغاء", "cancel")]
                )
            )
        except:
            await update.message.reply_text("⚠️ مفتاح 2FA غير صالح.")

    elif session.step == "app_pass":
        session.app_pass = text
        user_data = get_user(uid)
        user_data["pending_requests"].append({
            "email": session.email,
            "password": session.password,
            "totp": session.totp,
            "app_pass": session.app_pass,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        save_user(uid, user_data)
        SESSIONS.pop(uid, None)
        await update.message.reply_text(
            "✅ تم إرسال الطلب للمالك للموافقة!",
            reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")])
        )

# ==================== OWNER PANEL ====================
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

async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text("💰 أرسل السعر الجديد للحساب الواحد (رقم فقط):")
    context.user_data["awaiting_price"] = True

async def approval_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    users = load_json(USERS_DB)
    msg = "📋 *طلبات الموافقة المعلقة:*\n\n"
    found = False
    for uid, u_data in users.items():
        for req in u_data.get("pending_requests", []):
            msg += f"👤 المستخدم: {uid}\n📧 {req['email']}\n🔑 {req['password']}\n🔐 {req['totp']}\n🗝 {req['app_pass']}\n\n"
            msg += "🔽 اختر إجراءً:\n"
            found = True
    
    if not found:
        msg = "📭 لا توجد طلبات حالياً."
        await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")]))
        return

    # عرض الإيميلات الأولى فقط مع أزرار القبول والرفض
    # (للتبسيط، سنعرض أول طلب في الرسالة)
    for uid, u_data in users.items():
        for req in u_data.get("pending_requests", []):
            await query.message.reply_text(
                f"📋 *طلب موافقة*\n👤 المستخدم: {uid}\n📧 {req['email']}\n🔑 {req['password']}\n🔐 {req['totp']}\n🗝 {req['app_pass']}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb(
                    [("✅ قبول", f"approve:{uid}:{req['email']}")],
                    [("❌ رفض", f"reject:{uid}:{req['email']}")]
                )
            )
    
    await query.edit_message_text("📋 *تم عرض الطلبات أعلاه.*", reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")]))

async def approve_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    data = query.data.split(":")
    uid = int(data[1])
    email = data[2]
    
    user_data = get_user(uid)
    config = get_config()
    price = config.get("default_price", DEFAULT_ACCOUNT_PRICE)
    
    # إضافة الحساب إلى القائمة المقبولة
    for req in user_data.get("pending_requests", []):
        if req["email"] == email:
            user_data["approved_accounts"].append(req)
            user_data["pending_balance"] = user_data.get("pending_balance", 0.0) + price
            break
    
    # إزالة الطلب من قائمة المعلقة
    user_data["pending_requests"] = [r for r in user_data.get("pending_requests", []) if r["email"] != email]
    save_user(uid, user_data)
    
    await query.edit_message_text(
        f"✅ تم قبول الحساب `{email}`!\n💰 تم إضافة ${price} إلى رصيد المستخدم (قيد الانتظار).",
        parse_mode=ParseMode.MARKDOWN
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
    
    # إزالة الطلب من قائمة المعلقة (بدون إضافة نقاط)
    user_data["pending_requests"] = [r for r in user_data.get("pending_requests", []) if r["email"] != email]
    save_user(uid, user_data)
    
    await query.edit_message_text(
        f"❌ تم رفض الحساب `{email}`.\nلم يتم إضافة أي نقاط.",
        parse_mode=ParseMode.MARKDOWN
    )

async def all_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    users = load_json(USERS_DB)
    msg = "📧 *جميع الإيميلات المخزنة:*\n\n"
    found = False
    for uid, u_data in users.items():
        for acc in u_data.get("approved_accounts", []):
            msg += f"📧 {acc.get('email', '')} (✅ مقبول - صاحبها: {uid})\n"
            found = True
    if not found:
        msg += "📭 لا توجد إيميلات مقبولة بعد."
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("🔙 إعدادات المالك", "owner_panel")]))

async def videos_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text(
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
        config = get_config()
        config[f"video_{video_type}"] = str(file_path)
        save_config(config)
        await update.message.reply_text(f"✅ تم حفظ فيديو {video_type} بنجاح!")
        context.user_data.pop("pending_video_type", None)
        await main_menu(update, context)
    else:
        await update.message.reply_text("⚠️ يرجى إرسال فيديو صحيح.")

# ==================== MY WALLET ====================
async def my_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = get_user(query.from_user.id)
    pending = user.get("pending_balance", 0.0)
    available = user.get("balance", 0.0)
    await query.edit_message_text(
        f"💰 *أموالي*\n\n"
        f"⏳ الأموال قيد الانتظار: ${pending:.2f}\n"
        f"✅ الأموال المستلمة: ${available:.2f}\n\n"
        f"عندما يوافق المالك على حساباتك، تنتقل الأموال إلى الرصيد المستلم.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([("🛒 سحب", "withdraw_store"), ("🔙 القائمة الرئيسية", "main_menu")])
    )

# ==================== WITHDRAW STORE ====================
async def withdraw_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    store_items = [
        [("شدات ببجي", "store_pubg")],
        [("جواهر فري فاير", "store_freefire")],
        [("نجوم تيلجرام", "store_tgstars")],
        [("رصيد اسيا سيل", "store_asiacell")],
        [("رصيد اثير", "store_ether")],
        [("مطورات تلي", "store_tgdev")],
        [("تيلجرام مميز", "store_tgpremium")],
        [("نقاط بوت ارشقلي", "store_rashqly")],
        [("🔙 القائمة الرئيسية", "main_menu")]
    ]
    await query.edit_message_text(
        "🛒 *قسم السحب*\nاختر الخدمة التي تريد شراءها:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb(*store_items)
    )

async def store_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🛒 قيد التطوير... سيتم تفعيل قريباً.", show_alert=True)

# ==================== ROUTER ====================
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
            await my_wallet(update, context)
        elif data == "my_accounts":
            user = get_user(query.from_user.id)
            accs = user.get("approved_accounts", [])
            if not accs:
                await query.edit_message_text("📭 لا توجد حسابات لديك بعد.")
            else:
                msg = "📋 *حساباتك:*\n"
                for a in accs:
                    msg += f"📧 `{a.get('email', '')}`\n"
                await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))
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
            vtype = data.split(":", 1)[1]
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
            await set_price(update, context)
        elif data == "approval_requests":
            await approval_requests(update, context)
        elif data.startswith("approve:"):
            await approve_request(update, context)
        elif data.startswith("reject:"):
            await reject_request(update, context)
        elif data == "all_emails":
            await all_emails(update, context)
        elif data == "videos_section":
            await videos_section(update, context)
        elif data.startswith("set_video:"):
            await set_video_callback(update, context)
        elif data == "withdraw_store":
            await withdraw_store(update, context)
        elif data.startswith("store_"):
            await store_purchase(update, context)
        else:
            await query.edit_message_text("⚠️ خيار غير معروف.", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))
    except BadRequest:
        try:
            await query.answer("تعذر تنفيذ الطلب، أرسل /start وحاول مرة أخرى.", show_alert=True)
        except:
            pass

# ==================== TEXT INPUT ====================
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
    
    await add_account_step(update, context)

# ==================== DEBUG COMMANDS ====================
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

# ==================== MAIN ====================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN غير مضبوط. أضف توكن البوت من BotFather.")
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", main_menu))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("owner", owner_command))
    app.add_handler(CallbackQueryHandler(router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_upload))
    app.run_polling()

if __name__ == "__main__":
    main()
