"""
Advanced Telegram Account Manager Bot - Full Version with Button Customization
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
- Button Customization (Colors, Stickers, Backgrounds)
"""

import asyncio
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
        "approved_accounts": [],
        "pending_requests": [],
        "rejected_emails": [],
        "rejected_requests": [],
        "referral_code": "",
        "referred_by": None,
        "referral_earnings": 0.0,
        "total_referrals": 0,
        "total_approved_emails": 0
    })

def save_user(user_id: int, user_data: dict):
    users = load_json(DATA_DIR / "users.json")
    users[str(user_id)] = user_data
    save_json(DATA_DIR / "users.json", users)

def generate_referral_code():
    """Generate a unique referral code"""
    return secrets.token_hex(4).upper()

# ==================== BUTTON CUSTOMIZATION SYSTEM ====================
BUTTONS_CONFIG_FILE = DATA_DIR / "buttons_config.json"
BUTTON_BACKGROUNDS_DIR = DATA_DIR / "button_backgrounds"
BUTTON_BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)

# ==================== COLORS ====================
COLORS = {
    "red": {"name": "🔴 أحمر", "hex": "#FF0000", "emoji": "🔴"},
    "green": {"name": "🟢 أخضر", "hex": "#00FF00", "emoji": "🟢"},
    "blue": {"name": "🔵 أزرق", "hex": "#0000FF", "emoji": "🔵"},
    "pink": {"name": "💗 وردي", "hex": "#FF69B4", "emoji": "💗"},
    "purple": {"name": "🟣 بنفسجي", "hex": "#8B00FF", "emoji": "🟣"},
    "brown": {"name": "🟤 جوزي", "hex": "#8B4513", "emoji": "🟤"},
    "black": {"name": "⚫ أسود", "hex": "#000000", "emoji": "⚫"},
    "white": {"name": "⚪ أبيض", "hex": "#FFFFFF", "emoji": "⚪"},
    "orange": {"name": "🟠 برتقالي", "hex": "#FF8C00", "emoji": "🟠"},
    "gold": {"name": "🟡 ذهبي", "hex": "#FFD700", "emoji": "🟡"},
    "cyan": {"name": "🔷 فيروزي", "hex": "#00CED1", "emoji": "🔷"},
    "indigo": {"name": "🟣 نيلي", "hex": "#4B0082", "emoji": "🟣"},
    "deep_pink": {"name": "🌹 زهري", "hex": "#FF1493", "emoji": "🌹"},
    "coral": {"name": "🍊 مرجاني", "hex": "#FF7F50", "emoji": "🍊"},
    "olive": {"name": "🌿 زيتوني", "hex": "#808000", "emoji": "🌿"},
    "teal": {"name": "🦚 تيل", "hex": "#008080", "emoji": "🦚"},
    "lavender": {"name": "💜 لافندر", "hex": "#E6E6FA", "emoji": "💜"},
    "maroon": {"name": "🟤 كستنائي", "hex": "#800000", "emoji": "🟤"},
    "navy": {"name": "🔵 كحلي", "hex": "#000080", "emoji": "🔵"},
    "salmon": {"name": "🐟 سلموني", "hex": "#FA8072", "emoji": "🐟"},
    "violet": {"name": "💜 بنفسجي", "hex": "#8B00FF", "emoji": "💜"},
    "turquoise": {"name": "🔷 فيروزي", "hex": "#40E0D0", "emoji": "🔷"},
    "magenta": {"name": "💗 ماجنتا", "hex": "#FF00FF", "emoji": "💗"},
    "lime": {"name": "🟢 ليموني", "hex": "#00FF00", "emoji": "🟢"},
    "sky_blue": {"name": "🔵 سماوي", "hex": "#87CEEB", "emoji": "🔵"},
}

# ==================== DECORATIONS ====================
DECORATIONS = {
    "none": {"name": "بدون زخرفة", "symbol": ""},
    "star": {"name": "⭐ نجمة", "symbol": "⭐"},
    "heart": {"name": "❤️ قلب", "symbol": "❤️"},
    "fire": {"name": "🔥 نار", "symbol": "🔥"},
    "diamond": {"name": "💎 ألماس", "symbol": "💎"},
    "crown": {"name": "👑 تاج", "symbol": "👑"},
    "rocket": {"name": "🚀 صاروخ", "symbol": "🚀"},
    "sparkle": {"name": "✨ بريق", "symbol": "✨"},
    "lightning": {"name": "⚡ صاعقة", "symbol": "⚡"},
    "target": {"name": "🎯 هدف", "symbol": "🎯"},
    "trophy": {"name": "🏆 كأس", "symbol": "🏆"},
    "medal": {"name": "🎖️ ميدالية", "symbol": "🎖️"},
    "gem": {"name": "💠 جوهرة", "symbol": "💠"},
    "clover": {"name": "🍀 برسيم", "symbol": "🍀"},
    "rose": {"name": "🌹 وردة", "symbol": "🌹"},
    "sun": {"name": "☀️ شمس", "symbol": "☀️"},
    "moon": {"name": "🌙 قمر", "symbol": "🌙"},
    "rainbow": {"name": "🌈 قوس قزح", "symbol": "🌈"},
    "unicorn": {"name": "🦄 يونيكورن", "symbol": "🦄"},
    "dragon": {"name": "🐉 تنين", "symbol": "🐉"},
    "phoenix": {"name": "🔥 فينيكس", "symbol": "🔥"},
    "crystal": {"name": "💎 كريستال", "symbol": "💎"},
    "mystic": {"name": "🌀 غامض", "symbol": "🌀"},
    "flower": {"name": "🌸 زهرة", "symbol": "🌸"},
    "butterfly": {"name": "🦋 فراشة", "symbol": "🦋"},
    "snowflake": {"name": "❄️ ثلج", "symbol": "❄️"},
    "music": {"name": "🎵 موسيقى", "symbol": "🎵"},
}

# ==================== BUTTONS CONFIG ====================
def get_default_buttons_config():
    """إعدادات الأزرار الافتراضية"""
    return {
        "main_menu": [
            {"id": "btn1", "name": "إضافة حساب", "color": "#6C5CE7", "sticker": None, "sticker_file_id": None, "background": None, "callback": "add_account"},
            {"id": "btn2", "name": "أموالي", "color": "#00B894", "sticker": None, "sticker_file_id": None, "background": None, "callback": "my_wallet"},
            {"id": "btn3", "name": "حساباتي", "color": "#0984E3", "sticker": None, "sticker_file_id": None, "background": None, "callback": "my_accounts"},
            {"id": "btn4", "name": "تعليم", "color": "#FFD700", "sticker": None, "sticker_file_id": None, "background": None, "callback": "tutorials"},
            {"id": "btn5", "name": "سحب", "color": "#FF6B6B", "sticker": None, "sticker_file_id": None, "background": None, "callback": "withdraw_store"},
            {"id": "btn6", "name": "الإحالة", "color": "#FF8C00", "sticker": None, "sticker_file_id": None, "background": None, "callback": "referral_menu"},
            {"id": "btn7", "name": "تعديل حساباتي", "color": "#8B00FF", "sticker": None, "sticker_file_id": None, "background": None, "callback": "edit_my_accounts"},
        ],
        "owner_panel": [
            {"id": "owner1", "name": "سعر كل حساب", "color": "#FFD700", "sticker": None, "sticker_file_id": None, "background": None, "callback": "set_price"},
            {"id": "owner2", "name": "الطلبات", "color": "#0984E3", "sticker": None, "sticker_file_id": None, "background": None, "callback": "approval_requests"},
            {"id": "owner3", "name": "الفيديوهات", "color": "#FF6B6B", "sticker": None, "sticker_file_id": None, "background": None, "callback": "videos_section"},
            {"id": "owner4", "name": "المبيعات", "color": "#00B894", "sticker": None, "sticker_file_id": None, "background": None, "callback": "store_section"},
            {"id": "owner5", "name": "قناة إجبارية", "color": "#8B00FF", "sticker": None, "sticker_file_id": None, "background": None, "callback": "forced_channel"},
            {"id": "owner6", "name": "جميع الحسابات", "color": "#4B0082", "sticker": None, "sticker_file_id": None, "background": None, "callback": "all_accounts_section"},
            {"id": "owner7", "name": "نظام الإحالة", "color": "#FF8C00", "sticker": None, "sticker_file_id": None, "background": None, "callback": "referral_settings"},
        ],
        "decorations": {
            "header": "━━━━━━━━━━━━━━━━━",
            "star": "✦ ✧ ✦ ✧ ✦ ✧ ✦ ✧ ✦",
            "dot": "•",
            "line": "─────────────────",
            "double_line": "═════════════════════"
        }
    }

def load_buttons_config():
    """تحميل إعدادات الأزرار"""
    if not BUTTONS_CONFIG_FILE.exists():
        config = get_default_buttons_config()
        save_json(BUTTONS_CONFIG_FILE, config)
        return config
    return load_json(BUTTONS_CONFIG_FILE)

def save_buttons_config(config):
    """حفظ إعدادات الأزرار"""
    save_json(BUTTONS_CONFIG_FILE, config)

def get_color_emoji(color_hex):
    """تحويل اللون إلى إيموجي"""
    color_map = {
        "#FF0000": "🔴", "#00FF00": "🟢", "#0000FF": "🔵",
        "#FF69B4": "💗", "#8B00FF": "🟣", "#8B4513": "🟤",
        "#000000": "⚫", "#FFFFFF": "⚪", "#FF8C00": "🟠",
        "#FFD700": "🟡", "#00CED1": "🔷", "#4B0082": "🟣",
        "#FF1493": "🌹", "#FF7F50": "🍊", "#808000": "🌿",
        "#008080": "🦚", "#E6E6FA": "💜", "#800000": "🟤",
        "#000080": "🔵", "#FA8072": "🐟", "#6C5CE7": "💜",
        "#00B894": "💚", "#0984E3": "💙", "#FF6B6B": "❤️",
        "#FFD700": "💛", "#00CEC9": "💠", "#E17055": "🧡",
        "#A8E6CF": "💚", "#636E72": "🤍", "#40E0D0": "🔷",
        "#FF00FF": "💗", "#87CEEB": "🔵",
    }
    return color_map.get(color_hex, "")

def create_custom_button(btn_data):
    """إنشاء زر مخصص مع لون وملصق وخلفية"""
    name = btn_data.get("name", "زر")
    color = btn_data.get("color", "#6C5CE7")
    sticker_file_id = btn_data.get("sticker_file_id")
    background_file_id = btn_data.get("background")
    
    # إضافة اللون كإيموجي
    color_emoji = get_color_emoji(color)
    if color_emoji:
        name = f"{color_emoji} {name}"
    
    # إذا كان هناك ملصق، نضيفه كإيموجي (لكن تليجرام لا يدعم الملصقات في الأزرار)
    # لذلك نستخدم رمز بديل
    if sticker_file_id:
        name = f"🖼️ {name}"
    
    # إذا كان هناك خلفية، نضيف رمز
    if background_file_id:
        name = f"{name} 🎨"
    
    return InlineKeyboardButton(name, callback_data=btn_data.get("callback", "null"))

def build_custom_menu(buttons_data, rows_per_row=2):
    """بناء قائمة أزرار مخصصة"""
    keyboard = []
    row = []
    for btn_data in buttons_data:
        row.append(create_custom_button(btn_data))
        if len(row) >= rows_per_row:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

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

SESSIONS: Dict[int, Session] = {}

# ==================== MAIN MENU ====================
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # تحميل إعدادات الأزرار
    config_buttons = load_buttons_config()
    main_buttons = config_buttons.get("main_menu", [])
    decorations = config_buttons.get("decorations", {})
    
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

    user_data = get_user(user_id)
    coin = user_data.get("balance", 0)
    
    text = f"""
🌟 <b>مرحباً بك في البوت</b>
{decorations.get('header', '━━━━━━━━━━━━━━━━━')}
📊 <b>مستخدم:</b> <code>{user_id}</code>
💰 <b>نقاطك:</b> <code>{coin}</code>
{decorations.get('header', '━━━━━━━━━━━━━━━━━')}

✨ <b>اختر من القائمة أدناه:</b>
"""
    
    # إضافة زر المالك إذا كان المستخدم مالكاً
    if user.id == OWNER_ID:
        owner_btn = {"id": "owner_btn", "name": "⚙️ إعدادات المالك", "color": "#FFD700", "sticker": None, "sticker_file_id": None, "background": None, "callback": "owner_panel"}
        main_buttons.append(owner_btn)
    
    reply_markup = build_custom_menu(main_buttons, rows_per_row=2)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

# ==================== OWNER PANEL ====================
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return

    config_buttons = load_buttons_config()
    owner_buttons = config_buttons.get("owner_panel", [])
    decorations = config_buttons.get("decorations", {})
    
    # إضافة زر تخصيص الأزرار
    customize_btn = {"id": "customize_btn", "name": "🎨 تخصيص الأزرار", "color": "#6C5CE7", "sticker": None, "sticker_file_id": None, "background": None, "callback": "customize_buttons"}
    owner_buttons.append(customize_btn)
    
    # إضافة زر العودة
    back_btn = {"id": "back_btn", "name": "🔙 القائمة الرئيسية", "color": "#636E72", "sticker": None, "sticker_file_id": None, "background": None, "callback": "main_menu"}
    owner_buttons.append(back_btn)

    text = f"""
⚙️ <b>لوحة تحكم المالك</b>
{decorations.get('header', '━━━━━━━━━━━━━━━━━')}

اختر الإعداد الذي تريد تعديله:
"""
    
    reply_markup = build_custom_menu(owner_buttons, rows_per_row=2)
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

# ==================== BUTTON CUSTOMIZATION ====================
async def customize_buttons_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض لوحة تخصيص الأزرار"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    text = """
🎨 *لوحة تخصيص الأزرار*
━━━━━━━━━━━━━━━━━

📌 اختر القسم لتعديله:

• 🔘 الأزرار الرئيسية
• ⚙️ أزرار المالك
• 🎨 الألوان
• 🏷️ الملصقات (اختر أي ملصق من تليجرام)
• 🖼️ الخلفيات (ارفع صورة لكل زر)
"""
    
    buttons = [
        [InlineKeyboardButton("🔘 الأزرار الرئيسية", callback_data="customize_main")],
        [InlineKeyboardButton("⚙️ أزرار المالك", callback_data="customize_owner")],
        [InlineKeyboardButton("🎨 إدارة الألوان", callback_data="customize_colors")],
        [InlineKeyboardButton("🏷️ إدارة الملصقات", callback_data="customize_stickers")],
        [InlineKeyboardButton("🖼️ إدارة الخلفيات", callback_data="customize_backgrounds")],
        [InlineKeyboardButton("🔙 إعدادات المالك", callback_data="owner_panel")],
    ]
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def customize_main_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تخصيص الأزرار الرئيسية"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    config = load_buttons_config()
    main_buttons = config.get("main_menu", [])
    
    text = "🔘 *الأزرار الرئيسية*\n\nاختر زراً لتعديله:\n"
    
    buttons = []
    for btn in main_buttons:
        if btn.get("callback") not in ["owner_panel"]:
            name = btn.get("name", "زر")
            color_emoji = get_color_emoji(btn.get("color", "#6C5CE7"))
            has_sticker = "📎" if btn.get("sticker_file_id") else ""
            has_bg = "🖼️" if btn.get("background") else ""
            display_name = f"{has_sticker}{has_bg} {name}"
            buttons.append([InlineKeyboardButton(
                f"{color_emoji} {display_name[:25]}",
                callback_data=f"edit_btn:{btn['id']}"
            )])
    
    buttons.append([InlineKeyboardButton("➕ إضافة زر جديد", callback_data="add_main_btn")])
    buttons.append([InlineKeyboardButton("🔙 تخصيص الأزرار", callback_data="customize_buttons")])
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def customize_owner_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تخصيص أزرار المالك"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    config = load_buttons_config()
    owner_buttons = config.get("owner_panel", [])
    
    text = "⚙️ *أزرار المالك*\n\nاختر زراً لتعديله:\n"
    
    buttons = []
    for btn in owner_buttons:
        if btn.get("callback") not in ["customize_buttons", "main_menu"]:
            name = btn.get("name", "زر")
            color_emoji = get_color_emoji(btn.get("color", "#6C5CE7"))
            has_sticker = "📎" if btn.get("sticker_file_id") else ""
            has_bg = "🖼️" if btn.get("background") else ""
            display_name = f"{has_sticker}{has_bg} {name}"
            buttons.append([InlineKeyboardButton(
                f"{color_emoji} {display_name[:25]}",
                callback_data=f"edit_btn_owner:{btn['id']}"
            )])
    
    buttons.append([InlineKeyboardButton("➕ إضافة زر جديد", callback_data="add_owner_btn")])
    buttons.append([InlineKeyboardButton("🔙 تخصيص الأزرار", callback_data="customize_buttons")])
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def edit_button_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعديل تفاصيل زر معين"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    btn_id = query.data.split(":")[1]
    context.user_data["editing_btn_id"] = btn_id
    
    config = load_buttons_config()
    btn = None
    menu_type = "main_menu"
    
    for menu in ["main_menu", "owner_panel"]:
        for b in config.get(menu, []):
            if b["id"] == btn_id:
                btn = b
                menu_type = menu
                break
        if btn:
            break
    
    if not btn:
        await query.edit_message_text("❌ الزر غير موجود!")
        return
    
    has_sticker = "✅" if btn.get("sticker_file_id") else "❌"
    has_bg = "✅" if btn.get("background") else "❌"
    
    text = f"""
✏️ *تعديل الزر*
━━━━━━━━━━━━━━━━━

📌 المعرف: `{btn_id}`
📝 الاسم: {btn.get('name', '')}
🎨 اللون: {get_color_emoji(btn.get('color', '#6C5CE7'))} `{btn.get('color', '#6C5CE7')}`
🏷️ الملصق: {has_sticker}
🖼️ الخلفية: {has_bg}

اختر الخاصية لتعديلها:
"""
    
    buttons = [
        [InlineKeyboardButton("📝 تغيير الاسم", callback_data=f"edit_btn_name:{btn_id}")],
        [InlineKeyboardButton("🎨 تغيير اللون", callback_data=f"edit_btn_color:{btn_id}")],
        [InlineKeyboardButton("🏷️ إضافة/تغيير ملصق", callback_data=f"edit_btn_sticker:{btn_id}")],
        [InlineKeyboardButton("🗑️ حذف الملصق", callback_data=f"remove_btn_sticker:{btn_id}")],
        [InlineKeyboardButton("🖼️ إضافة/تغيير خلفية", callback_data=f"edit_btn_bg:{btn_id}")],
        [InlineKeyboardButton("🗑️ حذف الخلفية", callback_data=f"remove_btn_bg:{btn_id}")],
        [InlineKeyboardButton("❌ حذف الزر", callback_data=f"delete_btn:{btn_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"customize_{menu_type}")],
    ]
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def edit_btn_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تغيير اسم الزر"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    btn_id = query.data.split(":")[1]
    context.user_data["edit_btn_id"] = btn_id
    context.user_data["edit_mode"] = "name"
    
    await query.edit_message_text(
        f"✏️ *تغيير اسم الزر*\n\n"
        f"🆔 المعرف: `{btn_id}`\n\n"
        f"📝 أرسل الاسم الجديد للزر:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 إلغاء", callback_data=f"edit_btn:{btn_id}")]
        ])
    )

async def edit_btn_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تغيير لون الزر"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    btn_id = query.data.split(":")[1]
    
    text = f"🎨 *اختر لون الزر*\n\n"
    buttons = []
    for color_id, color_data in COLORS.items():
        buttons.append([InlineKeyboardButton(
            f"{color_data['emoji']} {color_data['name']}",
            callback_data=f"set_btn_color:{btn_id}:{color_data['hex']}"
        )])
    
    buttons.append([InlineKeyboardButton("🔙 إلغاء", callback_data=f"edit_btn:{btn_id}")])
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def edit_btn_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة أو تغيير ملصق الزر (ملصق من تليجرام)"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    btn_id = query.data.split(":")[1]
    context.user_data["edit_btn_id"] = btn_id
    context.user_data["edit_mode"] = "sticker"
    
    await query.edit_message_text(
        f"🏷️ *إضافة ملصق للزر*\n\n"
        f"🆔 المعرف: `{btn_id}`\n\n"
        f"📤 أرسل الملصق (Sticker) الذي تريد وضعه كملصق للزر:\n\n"
        f"📌 يمكنك إرسال أي ملصق من تليجرام",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 إلغاء", callback_data=f"edit_btn:{btn_id}")]
        ])
    )

async def remove_btn_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف ملصق الزر"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    btn_id = query.data.split(":")[1]
    
    config = load_buttons_config()
    for menu in ["main_menu", "owner_panel"]:
        for btn in config.get(menu, []):
            if btn["id"] == btn_id:
                btn["sticker_file_id"] = None
                save_buttons_config(config)
                await query.answer("✅ تم حذف الملصق!", show_alert=True)
                await edit_button_details(update, context)
                return

async def edit_btn_background(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة أو تغيير خلفية الزر (صورة من المستخدم)"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    btn_id = query.data.split(":")[1]
    context.user_data["edit_btn_id"] = btn_id
    context.user_data["edit_mode"] = "background"
    
    await query.edit_message_text(
        f"🖼️ *إضافة خلفية للزر*\n\n"
        f"🆔 المعرف: `{btn_id}`\n\n"
        f"📤 أرسل الصورة التي تريدها كخلفية للزر:\n\n"
        f"📌 يمكنك إرسال أي صورة (jpg, png)",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 إلغاء", callback_data=f"edit_btn:{btn_id}")]
        ])
    )

async def remove_btn_background(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف خلفية الزر"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    btn_id = query.data.split(":")[1]
    
    config = load_buttons_config()
    for menu in ["main_menu", "owner_panel"]:
        for btn in config.get(menu, []):
            if btn["id"] == btn_id:
                # حذف ملف الخلفية
                if btn.get("background"):
                    bg_path = BUTTON_BACKGROUNDS_DIR / btn["background"]
                    if bg_path.exists():
                        try:
                            bg_path.unlink()
                        except:
                            pass
                btn["background"] = None
                save_buttons_config(config)
                await query.answer("✅ تم حذف الخلفية!", show_alert=True)
                await edit_button_details(update, context)
                return

async def handle_button_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال تعديل الزر (الاسم)"""
    if update.effective_user.id != OWNER_ID:
        return
    
    text = update.message.text.strip()
    btn_id = context.user_data.get("edit_btn_id")
    mode = context.user_data.get("edit_mode")
    
    if not btn_id:
        return
    
    if mode == "name":
        config = load_buttons_config()
        for menu in ["main_menu", "owner_panel"]:
            for btn in config.get(menu, []):
                if btn["id"] == btn_id:
                    btn["name"] = text
                    save_buttons_config(config)
                    await update.message.reply_text("✅ تم تحديث اسم الزر!")
                    context.user_data.pop("edit_btn_id", None)
                    context.user_data.pop("edit_mode", None)
                    await main_menu(update, context)
                    return
        await update.message.reply_text("❌ الزر غير موجود!")

async def handle_sticker_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال الملصق"""
    if update.effective_user.id != OWNER_ID:
        return
    
    btn_id = context.user_data.get("edit_btn_id")
    if not btn_id:
        return
    
    if update.message.sticker:
        sticker_file_id = update.message.sticker.file_id
        config = load_buttons_config()
        
        for menu in ["main_menu", "owner_panel"]:
            for btn in config.get(menu, []):
                if btn["id"] == btn_id:
                    btn["sticker_file_id"] = sticker_file_id
                    save_buttons_config(config)
                    await update.message.reply_text("✅ تم إضافة الملصق للزر!")
                    context.user_data.pop("edit_btn_id", None)
                    context.user_data.pop("edit_mode", None)
                    await main_menu(update, context)
                    return
    else:
        await update.message.reply_text("❌ يرجى إرسال ملصق (Sticker) وليس صورة!")

async def handle_background_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال الخلفية (صورة)"""
    if update.effective_user.id != OWNER_ID:
        return
    
    btn_id = context.user_data.get("edit_btn_id")
    if not btn_id:
        return
    
    if update.message.photo:
        # الحصول على الصورة بأعلى جودة
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        # حفظ الصورة
        file_name = f"{btn_id}_{int(time.time())}.jpg"
        file_path = BUTTON_BACKGROUNDS_DIR / file_name
        await file.download_to_drive(file_path)
        
        config = load_buttons_config()
        for menu in ["main_menu", "owner_panel"]:
            for btn in config.get(menu, []):
                if btn["id"] == btn_id:
                    # حذف الخلفية القديمة
                    if btn.get("background"):
                        old_path = BUTTON_BACKGROUNDS_DIR / btn["background"]
                        if old_path.exists():
                            try:
                                old_path.unlink()
                            except:
                                pass
                    btn["background"] = file_name
                    save_buttons_config(config)
                    await update.message.reply_text("✅ تم إضافة الخلفية للزر!")
                    context.user_data.pop("edit_btn_id", None)
                    context.user_data.pop("edit_mode", None)
                    await main_menu(update, context)
                    return
    else:
        await update.message.reply_text("❌ يرجى إرسال صورة (Photo) وليس ملف!")

async def set_btn_color_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تطبيق تغيير لون الزر"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    parts = query.data.split(":")
    btn_id = parts[1]
    color_hex = parts[2]
    
    config = load_buttons_config()
    for menu in ["main_menu", "owner_panel"]:
        for btn in config.get(menu, []):
            if btn["id"] == btn_id:
                btn["color"] = color_hex
                save_buttons_config(config)
                await query.answer("✅ تم تحديث لون الزر!", show_alert=True)
                await edit_button_details(update, context)
                return

async def delete_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف زر"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    btn_id = query.data.split(":")[1]
    config = load_buttons_config()
    
    for menu in ["main_menu", "owner_panel"]:
        for i, btn in enumerate(config.get(menu, [])):
            if btn["id"] == btn_id:
                # حذف الخلفية إذا وجدت
                if btn.get("background"):
                    bg_path = BUTTON_BACKGROUNDS_DIR / btn["background"]
                    if bg_path.exists():
                        try:
                            bg_path.unlink()
                        except:
                            pass
                del config[menu][i]
                save_buttons_config(config)
                await query.answer("✅ تم حذف الزر!", show_alert=True)
                await customize_buttons_menu(update, context)
                return
    
    await query.answer("❌ الزر غير موجود!", show_alert=True)

async def add_main_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة زر رئيسي جديد"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    context.user_data["add_btn_menu"] = "main_menu"
    context.user_data["add_btn_step"] = "name"
    
    await query.edit_message_text(
        "➕ *إضافة زر جديد*\n\n"
        "📝 أرسل اسم الزر الجديد:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 إلغاء", callback_data="customize_main")]
        ])
    )

async def add_owner_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة زر مالك جديد"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    context.user_data["add_btn_menu"] = "owner_panel"
    context.user_data["add_btn_step"] = "name"
    
    await query.edit_message_text(
        "➕ *إضافة زر جديد*\n\n"
        "📝 أرسل اسم الزر الجديد:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 إلغاء", callback_data="customize_owner")]
        ])
    )

async def handle_add_button_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال إضافة زر جديد"""
    if update.effective_user.id != OWNER_ID:
        return
    
    text = update.message.text.strip()
    menu = context.user_data.get("add_btn_menu")
    step = context.user_data.get("add_btn_step")
    
    if not menu or step != "name":
        return
    
    btn_id = f"btn_{int(time.time())}"
    new_btn = {
        "id": btn_id,
        "name": text,
        "color": "#6C5CE7",
        "sticker": None,
        "sticker_file_id": None,
        "background": None,
        "callback": f"custom_btn_{btn_id}"
    }
    
    config = load_buttons_config()
    config[menu].append(new_btn)
    save_buttons_config(config)
    
    context.user_data.pop("add_btn_menu", None)
    context.user_data.pop("add_btn_step", None)
    
    await update.message.reply_text(f"✅ تم إضافة الزر: {text}")
    
    if menu == "main_menu":
        await customize_main_buttons(update, context)
    else:
        await customize_owner_buttons(update, context)

async def customize_colors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة الألوان"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    text = "🎨 *قائمة الألوان المتاحة*\n\n"
    for color_id, color_data in COLORS.items():
        text += f"{color_data['emoji']} {color_data['name']} - `{color_data['hex']}`\n"
    
    text += "\n📌 يمكنك استخدام هذه الألوان عند تخصيص الأزرار"
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 تخصيص الأزرار", callback_data="customize_buttons")]
        ])
    )

async def customize_stickers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تعليمات الملصقات"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    text = """
🏷️ *إدارة الملصقات*

📌 يمكنك إضافة أي ملصق من تليجرام كملصق للزر.

🔹 *الطريقة:*
1️⃣ اختر الزر الذي تريد إضافة ملصق له
2️⃣ اختر "إضافة/تغيير ملصق"
3️⃣ أرسل الملصق (Sticker) من تليجرام

✨ *ملاحظة:* يمكنك استخدام أي ملصق من أي بوت أو مجموعة!
"""
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 تخصيص الأزرار", callback_data="customize_buttons")]
        ])
    )

async def customize_backgrounds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تعليمات الخلفيات"""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    
    text = """
🖼️ *إدارة الخلفيات*

📌 يمكنك إضافة أي صورة كخلفية للزر.

🔹 *الطريقة:*
1️⃣ اختر الزر الذي تريد إضافة خلفية له
2️⃣ اختر "إضافة/تغيير خلفية"
3️⃣ أرسل الصورة التي تريدها

✨ *ملاحظة:* يمكنك استخدام أي صورة (jpg, png)
"""
    
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 تخصيص الأزرار", callback_data="customize_buttons")]
        ])
    )

# ==================== PLACEHOLDER FUNCTIONS ====================
async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def my_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def tutorials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def withdraw_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def edit_my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def approval_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def videos_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def store_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def forced_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def all_accounts_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

async def referral_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جارٍ التطوير...", show_alert=True)

# ==================== ROUTER ====================
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query: return
    await query.answer()
    data = query.data

    if data == "main_menu": await main_menu(update, context)
    elif data == "add_account": await add_account_start(update, context)
    elif data == "my_wallet": await my_wallet(update, context)
    elif data == "my_accounts": await my_accounts(update, context)
    elif data == "tutorials": await tutorials(update, context)
    elif data == "withdraw_store": await withdraw_store(update, context)
    elif data == "referral_menu": await referral_menu(update, context)
    elif data == "edit_my_accounts": await edit_my_accounts(update, context)
    elif data == "owner_panel": await owner_panel(update, context)
    elif data == "set_price": await set_price(update, context)
    elif data == "approval_requests": await approval_requests(update, context)
    elif data == "videos_section": await videos_section(update, context)
    elif data == "store_section": await store_section(update, context)
    elif data == "forced_channel": await forced_channel(update, context)
    elif data == "all_accounts_section": await all_accounts_section(update, context)
    elif data == "referral_settings": await referral_settings(update, context)
    elif data == "customize_buttons": await customize_buttons_menu(update, context)
    elif data == "customize_main": await customize_main_buttons(update, context)
    elif data == "customize_owner": await customize_owner_buttons(update, context)
    elif data == "customize_colors": await customize_colors(update, context)
    elif data == "customize_stickers": await customize_stickers(update, context)
    elif data == "customize_backgrounds": await customize_backgrounds(update, context)
    elif data == "add_main_btn": await add_main_button(update, context)
    elif data == "add_owner_btn": await add_owner_button(update, context)
    elif data.startswith("edit_btn:"): await edit_button_details(update, context)
    elif data.startswith("edit_btn_owner:"): await edit_button_details(update, context)
    elif data.startswith("edit_btn_name:"): await edit_btn_name(update, context)
    elif data.startswith("edit_btn_color:"): await edit_btn_color(update, context)
    elif data.startswith("edit_btn_sticker:"): await edit_btn_sticker(update, context)
    elif data.startswith("remove_btn_sticker:"): await remove_btn_sticker(update, context)
    elif data.startswith("edit_btn_bg:"): await edit_btn_background(update, context)
    elif data.startswith("remove_btn_bg:"): await remove_btn_background(update, context)
    elif data.startswith("set_btn_color:"): await set_btn_color_callback(update, context)
    elif data.startswith("delete_btn:"): await delete_button(update, context)
    else: await placeholder(update, context)

async def placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("⚠️ خيار غير معروف حالياً.", reply_markup=kb([("🔙 القائمة الرئيسية", "main_menu")]))

# ==================== TEXT & MEDIA INPUT ====================
async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input"""
    if context.user_data.get("edit_mode") == "name":
        await handle_button_edit_input(update, context)
    elif context.user_data.get("add_btn_step") == "name":
        await handle_add_button_input(update, context)
    else:
        await update.message.reply_text("⚠️ أرسل الأمر من القوائم.")

async def media_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle media input (sticker, photo)"""
    if context.user_data.get("edit_mode") == "sticker":
        await handle_sticker_input(update, context)
    elif context.user_data.get("edit_mode") == "background":
        await handle_background_input(update, context)

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
    await owner_panel(update, context)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await main_menu(update, context)

# ==================== MAIN ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("owner", owner_command))
    app.add_handler(CallbackQueryHandler(router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    app.add_handler(MessageHandler(filters.Sticker.ALL, media_input))
    app.add_handler(MessageHandler(filters.PHOTO, media_input))
    app.run_polling()

if __name__ == "__main__":
    main()
