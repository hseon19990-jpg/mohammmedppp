"""
Advanced Telegram Account Manager Bot - Full Version
- Owner Panel (Fully Fixed)
- Add Account Flow
- Video System (Upload & Play)
- Store System (Categories & Services)
- Wallet & Balance
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

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

DATA_DIR = Path(os.environ.get("DATA_DIR", "/railway/volume/data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_DB = DATA_DIR / "users.json"

VIDEOS_DIR = DATA_DIR / "videos"
VIDEOS_DIR.mkdir(exist_ok=True)

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

def get_user(user_id: int) -> dict:
    users = load_json(DATA_DIR / "users.json")
    return users.get(str(user_id), {
        "balance": 0.0,
        "pending_balance": 0.0,
        "approved_accounts": [],
        "pending_requests": []
    })

def save_user(user_id: int, user_data: dict):
    users = load_json(DATA_DIR / "users.json")
    users[str(user_id)] = user_data
    save_json(DATA_DIR / "users.json", users)

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
    ]
    if user.id == OWNER_ID:
        rows.append([("⚙️ إعدادات المالك", "owner_panel")])

    text = "👋 مرحباً بك!\nاختر من القائمة أدناه:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb(*rows))
    else:
        await update.message.reply_text(text, reply_markup=kb(*rows))

# ==================== OWNER PANEL (FULLY FIXED) ====================
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    # ✅ إظهار الأزرار الكاملة للمالك
    await query.edit_message_text(
        "⚙️ *لوحة تحكم المالك*\n\nاختر الإعداد الذي تريد تعديله:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("💰 سعر كل حساب", "set_price")],
            [("📋 الطلبات", "approval_requests")],
            [("📹 قسم الفيديوهات", "videos_section")],
            [("🛒 المبيعات", "store_section")],
            [("🔙 القائمة الرئيسية", "main_menu")]
        ])
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

    await query.edit_message_text(
        "🛒 *إدارة المبيعات*\n\nاختر إجراءً:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("➕ إضافة فئة", "store_add_category")],
            [("📋 عرض الفئات", "store_list_categories")],
            [("🔙 إعدادات المالك", "owner_panel")]
        ])
    )

async def store_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text("✏️ أرسل اسم الفئة الجديدة:")
    context.user_data["store_action"] = "add_category"

async def store_list_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    config = load_json(DATA_DIR / "config.json")
    categories = config.get("store_categories", [])
    if not categories:
        await query.edit_message_text("📭 لا توجد فئات.", reply_markup=kb([("🔙 إدارة المبيعات", "owner_store_section")]))
        return

    msg = "📋 *الفئات:*\n\n"
    rows = []
    for cat in categories:
        msg += f"📂 {cat['name']}\n"
        rows.append([(f"📂 {cat['name']}", f"store_category:{cat['id']}")])
    rows.append([("🔙 إدارة المبيعات", "owner_store_section")])
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb(*rows))

async def store_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    cat_id = query.data.split(":", 1)[1]
    config = load_json(DATA_DIR / "config.json")
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.", reply_markup=kb([("🔙 إدارة المبيعات", "owner_store_section")]))
        return

    msg = f"📂 *{category['name']}*\n\n"
    services = category.get("services", [])
    if services:
        for s in services:
            msg += f"🛒 {s['name']} - ${s['price']:.2f}\n"
    else:
        msg += "لا توجد خدمات في هذه الفئة.\n"

    await query.edit_message_text(
        msg,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb([
            [("➕ إضافة خدمة", f"store_add_service:{cat_id}")],
            [("🔙 عرض الفئات", "store_list_categories")]
        ])
    )

async def store_add_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    context.user_data["current_category_id"] = cat_id
    context.user_data["store_action"] = "add_service_name"
    await query.edit_message_text("✏️ أرسل اسم الخدمة:")

async def handle_store_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    text = update.message.text.strip()
    action = context.user_data.get("store_action")

    if action == "add_category":
        config = load_json(DATA_DIR / "config.json")
        if "store_categories" not in config: config["store_categories"] = []
        config["store_categories"].append({"id": str(time.time_ns()), "name": text, "services": []})
        save_json(DATA_DIR / "config.json", config)
        await update.message.reply_text(f"✅ تم إضافة الفئة: {text}")
        context.user_data.pop("store_action", None)
        await main_menu(update, context)

    elif action == "add_service_name":
        context.user_data["store_service_name"] = text
        context.user_data["store_action"] = "add_service_price"
        await update.message.reply_text("💰 أرسل السعر:")

    elif action == "add_service_price":
        try:
            price = float(text)
            name = context.user_data["store_service_name"]
            cat_id = context.user_data["current_category_id"]
            
            config = load_json(DATA_DIR / "config.json")
            for cat in config["store_categories"]:
                if cat["id"] == cat_id:
                    cat["services"].append({"id": str(time.time_ns()), "name": name, "price": price})
                    break
            save_json(DATA_DIR / "config.json", config)
            await update.message.reply_text(f"✅ تم إضافة الخدمة: {name} بسعر ${price:.2f}")
            context.user_data.pop("store_action", None)
            context.user_data.pop("store_service_name", None)
            context.user_data.pop("current_category_id", None)
            await main_menu(update, context)
        except ValueError:
            await update.message.reply_text("⚠️ يرجى إرسال رقم صحيح.")

# ==================== ADD ACCOUNT FLOW ====================
async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    SESSIONS[uid] = Session(step="email")
    
    # ✅ جلب السعر من ملف الإعدادات
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
        user_data["pending_requests"].append({
            "email": session.email,
            "password": session.password,
            "totp": session.totp,
            "app_pass": session.app_pass,
            "amount": price,
            "timestamp": datetime.now(timezone.utc).isoformat()
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

    if context.user_data.get("store_action"):
        await handle_store_input(update, context)
        return

    await add_account_step(update, context)

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
    elif data == "store_list_categories": await store_list_categories(update, context)
    elif data.startswith("store_category:"): await store_category_menu(update, context)
    elif data.startswith("store_add_service:"): await store_add_service(update, context)
    elif data == "withdraw_store": await withdraw_store(update, context)
    elif data.startswith("user_category:"): await user_category_menu(update, context)
    elif data.startswith("user_buy:"): await user_buy_service(update, context)
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
            [("🔙 القائمة الرئيسية", "main_menu")]
        ])
    )

# ==================== MAIN ====================
def main():
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
