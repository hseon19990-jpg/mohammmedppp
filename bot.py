"""
Advanced Telegram Account Manager Bot - v6.0 FINAL
====================================================
Full auto-verify flow, referral bonus in all paths, short messages.

Features:
- New flow: email → password → totp → app_pass
- Instant owner notification after each stage
- "Continue for more" / "Finish" buttons after each tier
- Auto-verify on 4th info submission (email+password+totp+app_pass)
- Auto-approve on success → hold 24h → recheck → release
- Auto-reject on failure (no owner notification)
- Short, clear verification messages (no technical jargon)
- Referral bonus awarded in ALL approval paths (owner + admin + auto)
- Admin completion triggers auto-verify too
- Recheck after 24h: success = release / fail = nothing
- Auto-ban after 3 consecutive rejections: 1 day, then 1 week, then weekly
"""

import asyncio
import hashlib
import html
import imaplib
import io
import json
import logging
import os
import re
import secrets
import shutil
import socket
import ssl
import time
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, List, Any, Union, Tuple

import pyotp
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
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

try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    Fernet = None
    InvalidToken = Exception

load_dotenv()

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
owner_id_value = (
    os.environ.get("OWNER_TELEGRAM_ID")
    or os.environ.get("OWNER_ID")
    or "0"
).strip()
try:
    OWNER_ID = int(owner_id_value)
except ValueError:
    OWNER_ID = 0
PURCHASE_CHANNEL_1 = os.environ.get("PURCHASE_CHANNEL_1", "").strip()
PURCHASE_CHANNEL_2 = os.environ.get("PURCHASE_CHANNEL_2", "").strip()

configured_data_dir = os.environ.get("DATA_DIR", "").strip()
railway_volume_dir = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
DATA_DIR = Path(configured_data_dir or railway_volume_dir or "/app/data").resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

MONEY_QUANTUM = Decimal("0.01")
DEFAULT_REFERRAL_BONUS = 0.05


def money_to_cents(value: Any) -> int:
    try:
        amount = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return 0
    return int(amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP) * 100)


def cents_to_money(cents: int) -> float:
    return round(cents / 100, 2)


def clamp_money(value: Any) -> float:
    try:
        return max(0.0, round(float(value or 0.0), 2))
    except (TypeError, ValueError):
        return 0.0


def spendable_balance_cents(user_data: dict) -> int:
    return (
        money_to_cents(user_data.get("balance", 0))
        + money_to_cents(user_data.get("admin_received_balance", 0))
    )


def debit_spendable_balance(user_data: dict, amount_cents: int) -> bool:
    if amount_cents < 0 or spendable_balance_cents(user_data) < amount_cents:
        return False
    regular_cents = money_to_cents(user_data.get("balance", 0))
    admin_cents = money_to_cents(user_data.get("admin_received_balance", 0))
    regular_debit = min(regular_cents, amount_cents)
    admin_debit = amount_cents - regular_debit
    user_data["balance"] = cents_to_money(regular_cents - regular_debit)
    user_data["admin_received_balance"] = cents_to_money(admin_cents - admin_debit)
    user_data["spent_balance"] = cents_to_money(
        money_to_cents(user_data.get("spent_balance", 0)) + amount_cents
    )
    return True


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==================== ENCRYPTION ====================
ENCRYPTION_KEY_FILE = DATA_DIR / ".encryption_key"
SECRET_FIELDS = {"password", "totp", "app_pass"}
_FERNET: Optional["Fernet"] = None


def _init_fernet() -> Optional["Fernet"]:
    global _FERNET
    if _FERNET is not None:
        return _FERNET
    if not CRYPTO_AVAILABLE:
        logger.warning("cryptography غير مثبت — تخزين بدون تشفير.")
        return None
    env_key = os.environ.get("ENCRYPTION_KEY", "").strip()
    key_bytes = env_key.encode() if env_key else None
    if not key_bytes:
        if ENCRYPTION_KEY_FILE.exists():
            try:
                key_bytes = ENCRYPTION_KEY_FILE.read_bytes().strip()
            except OSError:
                key_bytes = None
        if not key_bytes:
            key_bytes = Fernet.generate_key()
            try:
                ENCRYPTION_KEY_FILE.write_bytes(key_bytes)
                os.chmod(ENCRYPTION_KEY_FILE, 0o600)
                logger.info("تم توليد مفتاح تشفير جديد.")
            except OSError:
                logger.exception("فشل حفظ مفتاح التشفير.")
    try:
        _FERNET = Fernet(key_bytes)
    except Exception:
        logger.exception("مفتاح التشفير غير صالح.")
        _FERNET = None
    return _FERNET


def enc(value: Any) -> str:
    if value is None or value == "":
        return ""
    f = _init_fernet()
    if f is None:
        return str(value)
    try:
        return f.encrypt(str(value).encode("utf-8")).decode("ascii")
    except Exception:
        logger.exception("فشل التشفير.")
        return str(value)


def dec(value: Any) -> str:
    if value is None or value == "":
        return ""
    f = _init_fernet()
    if f is None:
        return str(value)
    try:
        return f.decrypt(str(value).encode("ascii")).decode("utf-8")
    except Exception:
        return str(value)


def encrypt_record(record: dict) -> dict:
    if not isinstance(record, dict):
        return record
    result = dict(record)
    for field_name in SECRET_FIELDS:
        if field_name in result and result[field_name]:
            raw = str(result[field_name])
            if not raw.startswith("gAAAAA"):
                result[field_name] = enc(raw)
    return result


def decrypt_record(record: dict) -> dict:
    if not isinstance(record, dict):
        return record
    result = dict(record)
    for field_name in SECRET_FIELDS:
        if field_name in result and result[field_name]:
            result[field_name] = dec(result[field_name])
    return result


def encrypt_user_data(user_data: dict) -> dict:
    if not isinstance(user_data, dict):
        return user_data
    result = dict(user_data)
    for collection_name in ("approved_accounts", "pending_requests", "rejected_requests"):
        items = result.get(collection_name)
        if isinstance(items, list):
            result[collection_name] = [encrypt_record(it) for it in items]
    return result


def decrypt_user_data(user_data: dict) -> dict:
    if not isinstance(user_data, dict):
        return user_data
    result = dict(user_data)
    for collection_name in ("approved_accounts", "pending_requests", "rejected_requests"):
        items = result.get(collection_name)
        if isinstance(items, list):
            result[collection_name] = [decrypt_record(it) for it in items]
    return result


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
        for filename in ("users.json", "config.json", "admins.json"):
            source = legacy_dir / filename
            destination = DATA_DIR / filename
            if source.is_file() and not destination.exists():
                try:
                    shutil.copy2(source, destination)
                    logger.info("Migrated %s.", filename)
                except OSError:
                    logger.exception("Migration failed.")
        source_videos = legacy_dir / "videos"
        destination_videos = DATA_DIR / "videos"
        if source_videos.is_dir():
            for source_video in source_videos.iterdir():
                destination_video = destination_videos / source_video.name
                if source_video.is_file() and not destination_video.exists():
                    destination_videos.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(source_video, destination_video)
                    except OSError:
                        pass


migrate_legacy_data()

# ==================== CONSTANTS ====================
USERS_DB = DATA_DIR / "users.json"
SESSIONS_DB = DATA_DIR / "sessions.json"
PENDING_PURCHASES_DB = DATA_DIR / "pending_purchases.json"
ADMINS_DB = DATA_DIR / "admins.json"
VIDEOS_DIR = DATA_DIR / "videos"
BACKUP_DIR = DATA_DIR / "backups"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

PAGE_SIZE = 8
LEAVE_HOLD_SECONDS = 24 * 60 * 60
LEAVE_HOLD_48H_SECONDS = 48 * 60 * 60
IMAP_RATE_LIMIT_SECONDS = 60
AUTO_VERIFY_ENABLED = True
ADMIN_TIER3_PRICE = 0.20

BAN_THRESHOLD = 3
BAN_FIRST_DURATION_HOURS = 24
BAN_WEEKLY_DURATION_HOURS = 24 * 7

# ==================== SHORT VERIFY MESSAGES ====================
SHORT_VERIFY_MESSAGES = {
    "success": "✅ تحقق ناجح",
    "auth_failed": "❌ الإيميل أو كلمة مرور التطبيق غير صحيحة",
    "need_app_pass": "⚠️ هذا الحساب يحتاج كلمة مرور التطبيق (App Password)",
    "need_2fa": "⚠️ الحساب محمي بـ 2FA — أضف رمز المصادقة",
    "network_fail": "🌐 فشل الاتصال بخادم البريد — أعد المحاولة",
    "timeout": "⏱ انتهت مهلة الاتصال — أعد المحاولة",
    "disabled": "❌ الحساب معطّل من المزود",
    "rate_limit": "⏱ محاولات كثيرة — حاول لاحقاً",
    "totp_only": "🔐 رمز المصادقة صالح",
    "unknown": "❌ فشل التحقق — حاول مرة أخرى",
}


def shorten_verify_message(category: str, fallback: str = "") -> str:
    return SHORT_VERIFY_MESSAGES.get(category, fallback or SHORT_VERIFY_MESSAGES["unknown"])


def detect_short_category(verify_result: dict) -> str:
    category = verify_result.get("category", "")
    message = str(verify_result.get("message", "")).lower()

    if verify_result.get("imap_ok"):
        return "success"
    if category == "unsupported_auth":
        return "need_app_pass"
    if category == "2fa":
        return "need_2fa" if verify_result.get("totp_ok") else "auth_failed"
    if category == "network":
        if "timeout" in message or "مهلة" in message:
            return "timeout"
        return "network_fail"
    if category in {"auth", "auth_or_policy"}:
        if "disabled" in message or "معطل" in message:
            return "disabled"
        if "rate" in message or "limit" in message or "too many" in message:
            return "rate_limit"
        if "timeout" in message or "مهلة" in message:
            return "timeout"
        if any(m in message for m in ("dns", "connect", "ssl", "unreachable", "refused")):
            return "network_fail"
        return "auth_failed"
    if category == "no_credentials":
        return "auth_failed"
    if verify_result.get("totp_ok") and not verify_result.get("imap_ok"):
        return "totp_only"
    return "unknown"


# ==================== REJECT REASONS ====================
REJECT_REASON_KEYS = ("email", "password", "totp", "app_pass", "phone", "other")

REJECT_REASON_ICONS = {
    "email": "📧", "password": "🔑", "totp": "🔐", "app_pass": "🗝",
    "phone": "📱", "other": "📝", "custom": "📝", "unknown": "❌",
}

REJECT_REASON_LABELS = {
    "email": "إيميل خطأ", "password": "باسورد خطأ",
    "totp": "رمز مصادقة خطأ", "app_pass": "كلمة مرور تطبيق خطأ",
    "phone": "يحتاج رقم هاتف", "other": "خطأ آخر",
    "custom": "سبب مخصص", "unknown": "غير معروف",
    "owner_manual": "رفض يدوي من المالك",
    "auto_verify_failed": "فشل التحقق التلقائي",
}

REJECT_REASON_MESSAGES = {
    "email": "❌ الإيميل غير صحيح أو غير مقبول.",
    "password": "❌ كلمة المرور غير صحيحة.",
    "totp": "❌ رمز المصادقة غير صحيح.",
    "app_pass": "❌ كلمة مرور التطبيق غير صحيحة.",
    "phone": "📱 هذا الحساب يحتاج رقم هاتف للتفعيل.",
    "other": "❌ تم رفض طلبك لسبب آخر.",
}

REJECT_REASON_VIDEO_KEY = {
    "email": "video_email", "password": "video_password",
    "totp": "video_totp", "app_pass": "video_app_pass", "phone": "video_phone",
}


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
            logger.exception("Could not read %s.", candidate)
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
        try:
            shutil.copy2(path, backup_path)
        except OSError:
            pass
    os.replace(temporary_path, path)


# ==================== CONFIG CACHE ====================
_CONFIG_CACHE: Dict[str, Any] = {"data": None, "mtime": 0.0, "loaded_at": 0.0}
_CONFIG_TTL = 30


def invalidate_config_cache():
    _CONFIG_CACHE["data"] = None
    _CONFIG_CACHE["mtime"] = 0.0
    _CONFIG_CACHE["loaded_at"] = 0.0


def load_config() -> dict:
    config_path = DATA_DIR / "config.json"
    now = time.time()
    cached = _CONFIG_CACHE.get("data")
    if cached is not None and (now - _CONFIG_CACHE.get("loaded_at", 0.0)) < _CONFIG_TTL:
        return cached
    try:
        mtime = config_path.stat().st_mtime if config_path.exists() else 0.0
    except OSError:
        mtime = 0.0
    if cached is not None and mtime == _CONFIG_CACHE.get("mtime"):
        _CONFIG_CACHE["loaded_at"] = now
        return cached
    data = load_json(config_path)
    _CONFIG_CACHE["data"] = data
    _CONFIG_CACHE["mtime"] = mtime
    _CONFIG_CACHE["loaded_at"] = now
    return data


def save_config(data: dict):
    save_json(DATA_DIR / "config.json", data)
    invalidate_config_cache()


def get_referral_bonus() -> float:
    config = load_config()
    try:
        value = config.get("referral_bonus")
        if value is None:
            return DEFAULT_REFERRAL_BONUS
        return max(0.0, round(float(value), 2))
    except (TypeError, ValueError):
        return DEFAULT_REFERRAL_BONUS


# ==================== ADMINS ====================
def load_admins() -> set:
    data = load_json(ADMINS_DB)
    admins = data.get("admins", [])
    result = set()
    for a in admins:
        try:
            result.add(int(a))
        except (TypeError, ValueError):
            continue
    return result


def save_admins(admins: set):
    save_json(ADMINS_DB, {"admins": sorted(int(a) for a in admins)})


def is_admin(user_id: int) -> bool:
    return user_id in load_admins()


def is_admin_or_owner(user_id: int) -> bool:
    return user_id == OWNER_ID or is_admin(user_id)


# ==================== USER DATA ====================
DEFAULT_USER_FIELDS = {
    "balance": 0.0, "pending_balance": 0.0, "hold_balance": 0.0,
    "admin_pending_balance": 0.0, "admin_received_balance": 0.0,
    "total_credited_balance": 0.0, "spent_balance": 0.0,
    "approved_accounts": [], "pending_requests": [],
    "rejected_emails": [], "rejected_requests": [],
    "referral_code": "", "referred_by": None,
    "referral_earnings": 0.0, "total_referrals": 0,
    "total_approved_emails": 0, "pending_purchases": [],
    "used_app_passwords": [], "transactions": [], "contest_wins": [],
    "consecutive_rejections": 0, "ban_until": "", "ban_level": 0, "total_rejections": 0,
}


def get_user(user_id: int) -> dict:
    users = load_json(USERS_DB)
    raw = users.get(str(user_id), {})
    merged = {**DEFAULT_USER_FIELDS, **raw}
    merged["balance"] = clamp_money(merged.get("balance"))
    merged["pending_balance"] = clamp_money(merged.get("pending_balance"))
    merged["hold_balance"] = clamp_money(merged.get("hold_balance"))
    merged["admin_pending_balance"] = clamp_money(merged.get("admin_pending_balance"))
    merged["admin_received_balance"] = clamp_money(merged.get("admin_received_balance"))
    merged["total_credited_balance"] = clamp_money(merged.get("total_credited_balance"))
    merged["spent_balance"] = clamp_money(merged.get("spent_balance"))
    merged["referral_earnings"] = clamp_money(merged.get("referral_earnings"))
    return decrypt_user_data(merged)


def save_user(user_id: int, user_data: dict):
    users = load_json(USERS_DB)
    for field_name in ("balance", "pending_balance", "hold_balance",
                       "admin_pending_balance", "admin_received_balance",
                       "total_credited_balance", "spent_balance", "referral_earnings"):
        if field_name in user_data:
            user_data[field_name] = clamp_money(user_data[field_name])
    if isinstance(user_data.get("transactions"), list) and len(user_data["transactions"]) > 200:
        user_data["transactions"] = user_data["transactions"][-200:]
    users[str(user_id)] = encrypt_user_data(user_data)
    save_json(USERS_DB, users)


def add_transaction(user_data: dict, kind: str, amount: float, note: str = "", email: str = ""):
    user_data.setdefault("transactions", []).append({
        "kind": kind, "amount": round(float(amount), 2), "note": note[:200],
        "email": email, "at": datetime.now(timezone.utc).isoformat(),
    })


# ==================== REFERRAL AWARD ====================
async def award_referral_bonus(context: ContextTypes.DEFAULT_TYPE,
                                referred_uid: int,
                                user_data: dict,
                                email: str = ""):
    referred_by = user_data.get("referred_by")
    if not referred_by:
        return
    try:
        referred_by_id = int(referred_by)
    except (TypeError, ValueError):
        return
    if referred_by_id == referred_uid:
        return
    referral_bonus = get_referral_bonus()
    if referral_bonus <= 0:
        return

    try:
        referrer_data = get_user(referred_by_id)
        referrer_data["referral_earnings"] = clamp_money(
            float(referrer_data.get("referral_earnings", 0.0)) + referral_bonus)
        referrer_data["balance"] = clamp_money(
            float(referrer_data.get("balance", 0.0)) + referral_bonus)
        referrer_data["total_credited_balance"] = clamp_money(
            float(referrer_data.get("total_credited_balance", 0.0) or 0.0) + referral_bonus)
        referrer_data["total_referrals"] = int(referrer_data.get("total_referrals", 0)) + 1
        add_transaction(referrer_data, "referral", referral_bonus,
                        f"مكافأة إحالة للمستخدم {referred_uid}", email)
        save_user(referred_by_id, referrer_data)
        try:
            await context.bot.send_message(
                chat_id=referred_by_id,
                text=(f"🎉 <b>مبروك!</b>\n\n"
                      f"📢 تم قبول حساب أحد المُحالين بواسطتك.\n"
                      f"💰 حصلت على مكافأة إحالة: <b>${referral_bonus:.2f}</b>\n"
                      f"👤 المستخدم: <code>{referred_uid}</code>"),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        logger.info("Awarded referral bonus %.2f to %s (from %s)",
                    referral_bonus, referred_by_id, referred_uid)
    except Exception:
        logger.exception("Failed to award referral bonus")


# ==================== AUTO-BAN HELPERS ====================
def _ban_duration_hours_for_level(level: int) -> int:
    return BAN_FIRST_DURATION_HOURS if level <= 1 else BAN_WEEKLY_DURATION_HOURS


def get_active_ban_message(user_data: dict) -> Optional[str]:
    ban_until_str = str(user_data.get("ban_until", "") or "").strip()
    if not ban_until_str:
        return None
    try:
        ban_until = datetime.fromisoformat(ban_until_str)
    except ValueError:
        return None
    if ban_until.tzinfo is None:
        ban_until = ban_until.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if now >= ban_until:
        return None
    remaining = ban_until - now
    hours = int(remaining.total_seconds() // 3600)
    minutes = int((remaining.total_seconds() % 3600) // 60)
    return (f"🚫 <b>أنت محظور من إرسال حسابات جديدة</b>\n"
            f"⏳ الوقت المتبقي: {hours} ساعة و {minutes} دقيقة.\n"
            f"<i>السبب: 3 رفضات متتالية.</i>")


def register_rejection_and_maybe_ban(user_data: dict) -> Optional[Tuple[int, str]]:
    consecutive = int(user_data.get("consecutive_rejections", 0) or 0) + 1
    user_data["consecutive_rejections"] = consecutive
    user_data["total_rejections"] = int(user_data.get("total_rejections", 0) or 0) + 1

    if consecutive < BAN_THRESHOLD:
        return None

    ban_level = int(user_data.get("ban_level", 0) or 0) + 1
    user_data["ban_level"] = ban_level
    user_data["consecutive_rejections"] = 0
    duration_hours = _ban_duration_hours_for_level(ban_level)
    ban_until = datetime.now(timezone.utc) + timedelta(hours=duration_hours)
    user_data["ban_until"] = ban_until.isoformat()
    duration_text = "يوم واحد" if ban_level == 1 else "أسبوع"
    return ban_level, duration_text


def register_approval_reset(user_data: dict):
    user_data["consecutive_rejections"] = 0


# ==================== SESSION PERSISTENCE ====================
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
_IMAP_RATE: Dict[str, float] = {}


def _sessions_to_json() -> dict:
    return {str(uid): asdict(sess) for uid, sess in SESSIONS.items()}


def load_sessions_from_disk():
    raw = load_json(SESSIONS_DB)
    for key, data in raw.items():
        try:
            uid = int(key)
            SESSIONS[uid] = Session(**{**asdict(Session()), **data})
        except (ValueError, TypeError):
            continue
    logger.info("Loaded %d active sessions.", len(SESSIONS))


def save_sessions():
    try:
        save_json(SESSIONS_DB, _sessions_to_json())
    except Exception:
        logger.exception("فشل حفظ الجلسات.")


def save_pending_purchases():
    try:
        save_json(PENDING_PURCHASES_DB, {str(k): v for k, v in PENDING_PURCHASES.items()})
    except Exception:
        logger.exception("فشل حفظ مشتريات معلقة.")


def load_pending_purchases_from_disk():
    raw = load_json(PENDING_PURCHASES_DB)
    for key, data in raw.items():
        try:
            PENDING_PURCHASES[int(key)] = data
        except (ValueError, TypeError):
            continue
    logger.info("Loaded %d pending purchases.", len(PENDING_PURCHASES))


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


def clear_edit_state(context: ContextTypes.DEFAULT_TYPE):
    for key in ("step", "editing_email", "editing_field", "editing_uid", "editing_index"):
        context.user_data.pop(key, None)


def tg_html_escape(value: Any) -> str:
    return html.escape(str(value), quote=False)


async def _safe_edit(query, text: str, **kwargs):
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception:
        logger.exception("edit_message_text failed, falling back to send_message")
        try:
            await query.message.reply_text(text, **kwargs)
        except Exception:
            logger.exception("send_message fallback failed")


# ==================== PRICING ====================
def get_tier_prices() -> dict:
    config = load_config()
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
    cleaned = password.replace(" ", "")
    return len(cleaned) == 16 and bool(re.match(r'^[A-Za-z0-9]{16}$', cleaned))


def format_app_password(password: str) -> str:
    cleaned = password.replace(" ", "")
    if len(cleaned) != 16:
        return password
    return f"{cleaned[0:4]} {cleaned[4:8]} {cleaned[8:12]} {cleaned[12:16]}"


def format_totp_secret(secret: str) -> str:
    cleaned = secret.replace(" ", "").upper()
    if len(cleaned) != 32:
        return secret
    return " ".join([cleaned[i:i+4] for i in range(0, 32, 4)])


def normalize_email(email: Any) -> str:
    return str(email or "").strip().casefold()


def clear_rejected_email_records(user_data: dict, email: str):
    normalized_email = normalize_email(email)
    if not normalized_email:
        return
    user_data["rejected_requests"] = [
        request for request in user_data.get("rejected_requests", [])
        if normalize_email(request.get("email", "")) != normalized_email
    ]
    user_data["rejected_emails"] = [
        stored_email for stored_email in user_data.get("rejected_emails", [])
        if normalize_email(stored_email) != normalized_email
    ]


def move_request_to_rejected(user_data: dict, request: dict, reason: str, reason_text: str = ""):
    rejected_request = dict(request)
    rejected_request["reject_reason"] = reason
    if reason_text:
        rejected_request["reject_reason_text"] = reason_text
    user_data.setdefault("rejected_requests", []).append(rejected_request)
    email = request.get("email", "")
    rejected_emails = user_data.get("rejected_emails", [])
    if not isinstance(rejected_emails, list):
        rejected_emails = []
    rejected_emails.append(email)
    user_data["rejected_emails"] = rejected_emails
    user_data["pending_balance"] = clamp_money(
        float(user_data.get("pending_balance", 0.0)) - float(request.get("amount", 0.0))
    )


def move_approved_account_to_rejected(
    user_data: dict, account: dict, reason: str, reason_text: str = ""
):
    rejected_account = dict(account)
    rejected_account["reject_reason"] = reason
    rejected_account["rejected_from_approved"] = True
    rejected_account["rejected_at"] = datetime.now(timezone.utc).isoformat()
    if reason_text:
        rejected_account["reject_reason_text"] = reason_text
    user_data.setdefault("rejected_requests", []).append(rejected_account)

    email = account.get("email", "")
    rejected_emails = user_data.get("rejected_emails", [])
    if not isinstance(rejected_emails, list):
        rejected_emails = []
    if not any(normalize_email(item) == normalize_email(email) for item in rejected_emails):
        rejected_emails.append(email)
    user_data["rejected_emails"] = rejected_emails


def account_callback_token(email: Any) -> str:
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()[:12]


def find_pending_request(user_data: dict, token: str) -> Optional[Tuple[int, dict]]:
    pending = user_data.get("pending_requests", [])
    if not isinstance(pending, list):
        return None
    for index, request in enumerate(pending):
        if account_callback_token(request.get("email", "")) == token:
            return index, request
    if token.isdigit():
        index = int(token)
        if 0 <= index < len(pending):
            return index, pending[index]
    return None


def find_approved_account(user_data: dict, token: str) -> Optional[Tuple[int, dict]]:
    accounts = user_data.get("approved_accounts", [])
    if not isinstance(accounts, list):
        return None
    for index, account in enumerate(accounts):
        if account_callback_token(account.get("email", "")) == token:
            return index, account
    if token.isdigit():
        index = int(token)
        if 0 <= index < len(accounts):
            return index, accounts[index]
    return None


def get_active_account_status(email: str) -> Optional[str]:
    normalized_email = normalize_email(email)
    if not normalized_email:
        return None
    users = load_json(USERS_DB)
    for user_data in users.values():
        for account in user_data.get("approved_accounts", []):
            if account.get("rejected_at_24h"):
                continue
            if normalize_email(dec(account.get("email", ""))) == normalized_email:
                return "approved"
    for user_data in users.values():
        for request in user_data.get("pending_requests", []):
            if normalize_email(dec(request.get("email", ""))) == normalized_email:
                return "pending"
    return None


def has_active_app_password(password: str) -> bool:
    cleaned_password = str(password or "").replace(" ", "").upper()
    if not cleaned_password:
        return False
    users = load_json(USERS_DB)
    for user_data in users.values():
        for collection_name in ("approved_accounts", "pending_requests"):
            for record in user_data.get(collection_name, []):
                if record.get("rejected_at_24h"):
                    continue
                stored = str(dec(record.get("app_pass", "")) or "").replace(" ", "").upper()
                if stored == cleaned_password:
                    return True
    return False


def has_active_account_password(password: str) -> bool:
    candidate = str(password or "").strip()
    if not candidate:
        return False
    users = load_json(USERS_DB)
    for user_data in users.values():
        for collection_name in ("approved_accounts", "pending_requests"):
            for record in user_data.get(collection_name, []):
                if record.get("rejected_at_24h"):
                    continue
                stored = str(dec(record.get("password", "")) or "").strip()
                if stored and stored == candidate:
                    return True
    return False


# ==================== IMAP VERIFICATION ====================
IMAP_PROVIDERS = {
    "gmail.com": ("imap.gmail.com", 993),
    "googlemail.com": ("imap.gmail.com", 993),
    "outlook.com": ("outlook.office365.com", 993),
    "outlook.sa": ("outlook.office365.com", 993),
    "hotmail.com": ("outlook.office365.com", 993),
    "hotmail.sa": ("outlook.office365.com", 993),
    "live.com": ("outlook.office365.com", 993),
    "msn.com": ("outlook.office365.com", 993),
    "yahoo.com": ("imap.mail.yahoo.com", 993),
    "ymail.com": ("imap.mail.yahoo.com", 993),
    "icloud.com": ("imap.mail.me.com", 993),
    "me.com": ("imap.mail.me.com", 993),
    "mac.com": ("imap.mail.me.com", 993),
    "aol.com": ("imap.aol.com", 993),
    "zoho.com": ("imap.zoho.com", 993),
    "yandex.com": ("imap.yandex.com", 993),
    "yandex.ru": ("imap.yandex.com", 993),
    "mail.ru": ("imap.mail.ru", 993),
    "gmx.com": ("imap.gmx.com", 993),
    "gmx.net": ("imap.gmx.net", 993),
}


def get_imap_host(email: str) -> Tuple[str, int]:
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    if domain in IMAP_PROVIDERS:
        return IMAP_PROVIDERS[domain]
    if domain:
        return (f"imap.{domain}", 993)
    return ("", 0)


def _imap_login_sync(email: str, password: str, timeout: int = 15) -> Tuple[bool, str]:
    host, port = get_imap_host(email)
    if not host:
        return False, "⚠️ فشل تحديد مزود الإيميل: لا يوجد خادم IMAP معروف لهذا النطاق."

    started = time.monotonic()

    def elapsed() -> str:
        return f"{time.monotonic() - started:.1f} ثوانٍ"

    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return False, f"❌ فشل DNS في تحويل {host} إلى عنوان IP بعد {elapsed()}: {exc}"
    except (socket.timeout, TimeoutError) as exc:
        return False, f"❌ انتهت مهلة DNS لـ {host} بعد {elapsed()}: {exc}"
    except OSError as exc:
        return False, f"❌ فشل DNS لـ {host} بعد {elapsed()}: {exc}"

    raw_socket = None
    try:
        raw_socket = socket.create_connection((host, port), timeout=timeout)
    except (socket.timeout, TimeoutError) as exc:
        return False, (f"❌ فشل TCP: انتهت مهلة فتح الاتصال إلى {host}:{port} "
                       f"بعد {elapsed()}. {exc}")
    except ConnectionRefusedError as exc:
        return False, f"❌ فشل TCP: الخادم رفض الاتصال بـ {host}:{port} بعد {elapsed()}: {exc}"
    except OSError as exc:
        return False, f"❌ فشل TCP إلى {host}:{port} بعد {elapsed()}: {exc}"

    try:
        ctx = ssl.create_default_context()
        with raw_socket:
            with ctx.wrap_socket(raw_socket, server_hostname=host):
                pass
    except (socket.timeout, TimeoutError) as exc:
        return False, f"❌ فشل TLS: انتهت مهلة المصافحة مع {host} بعد {elapsed()}: {exc}"
    except ssl.SSLError as exc:
        return False, f"❌ فشل TLS مع {host} بعد {elapsed()}: {exc}"
    except OSError as exc:
        return False, f"❌ فشل TLS/الشبكة مع {host} بعد {elapsed()}: {exc}"

    try:
        with imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=timeout) as imap:
            imap.login(email, password)
            try:
                imap.logout()
            except Exception:
                pass
            return True, f"✅ نجحت مراحل DNS وTCP وTLS ومصادقة IMAP خلال {elapsed()}."
    except imaplib.IMAP4.error as exc:
        err = str(exc)
        low = err.lower()
        prefix = f"✅ الشبكة سليمة (DNS/TCP/TLS)، لكن فشلت مصادقة IMAP بعد {elapsed()}: "
        if "application-specific password required" in low:
            return False, prefix + "يتطلب كلمة مرور تطبيق (App Password) وليس كلمة المرور العادية."
        if "invalid credentials" in low or "authenticationfailed" in low or ("auth" in low and "fail" in low):
            return False, (prefix + f"رد Gmail العام: {err}. لا يحدد IMAP هل السبب كلمة المرور أو App Password أو سياسة الحساب.")
        if "account is disabled" in low or "disabled" in low:
            return False, prefix + "الحساب معطّل من قبل المزود."
        if "too many" in low or "rate" in low or "limit" in low:
            return False, prefix + "تم تجاوز عدد محاولات الدخول. حاول لاحقاً."
        return False, prefix + f"رد IMAP: {err}"
    except (socket.timeout, TimeoutError) as exc:
        return False, f"❌ نجحت مراحل DNS/TCP مبدئياً، لكن انتهت مهلة IMAP/TLS بعد {elapsed()}: {exc}"
    except ssl.SSLError as exc:
        return False, f"❌ نجحت الشبكة مبدئياً، لكن فشل TLS أثناء جلسة IMAP بعد {elapsed()}: {exc}"
    except OSError as exc:
        return False, f"❌ نجحت DNS مبدئياً، لكن فشلت جلسة IMAP بعد {elapsed()}: {exc}"
    except Exception:
        logger.exception("IMAP verification error for %s", email)
        return False, f"❌ خطأ غير متوقع بعد نجاح فحص الشبكة ({elapsed()})."


NETWORK_ERROR_MARKERS = (
    "timeout", "dns", "ssl", "connect", "unreachable", "refused",
    "تعذّر الاتصال", "انتهت مهلة",
)
AUTH_ERROR_MARKERS = (
    "invalid credentials", "authenticationfailed",
    "username and password not accepted",
    "بيانات الدخول غير صحيحة",
)
TWO_FA_ERROR_MARKERS = (
    "application-specific password", "app password",
    "two-factor", "2fa", "2-step", "app-specific",
    "يتطلب كلمة مرور تطبيق",
)


def classify_imap_error(message: str) -> str:
    low = (message or "").lower()
    if any(m in low for m in TWO_FA_ERROR_MARKERS):
        return "2fa"
    if any(m in low for m in AUTH_ERROR_MARKERS):
        return "auth_or_policy"
    if any(m in low for m in NETWORK_ERROR_MARKERS):
        return "network"
    return "unknown"


async def verify_account_credentials(
    email: str,
    password: str = "",
    app_pass: str = "",
    totp_secret: str = "",
) -> dict:
    result = {
        "level": "failed", "badge": "🔴", "message": "",
        "imap_ok": False, "totp_ok": False, "category": "unknown",
    }
    if totp_secret:
        cleaned_totp = totp_secret.replace(" ", "").upper()
        if validate_totp_secret(cleaned_totp):
            try:
                pyotp.TOTP(cleaned_totp).now()
                result["totp_ok"] = True
            except Exception:
                result["totp_ok"] = False

    imap_pass = ""
    if app_pass:
        imap_pass = app_pass.replace(" ", "")
    elif password:
        imap_pass = password

    is_gmail_account = email.rsplit("@", 1)[-1].lower() in {"gmail.com", "googlemail.com"}
    if password and not app_pass and is_gmail_account:
        result["level"] = "unknown"
        result["badge"] = "⚪"
        result["category"] = "unsupported_auth"
        result["message"] = "⚠️ يحتاج App Password"
    elif imap_pass:
        ok, msg = await asyncio.to_thread(_imap_login_sync, email, imap_pass)
        result["imap_ok"] = ok
        result["message"] = msg
        result["category"] = "ok" if ok else classify_imap_error(msg)
    else:
        result["message"] = "لا توجد بيانات دخول للتحقق منها."
        result["category"] = "no_credentials"

    if result["imap_ok"]:
        result["level"] = "verified"
        result["badge"] = "🟢"
        result["message"] = "✅ تحقق ناجح"
    elif result["category"] == "2fa":
        result["level"] = "partial" if result["totp_ok"] else "unknown"
        result["badge"] = "🟡" if result["totp_ok"] else "⚪"
        result["message"] = "⚠️ الحساب محمي بـ 2FA" if result["totp_ok"] else "❌ فشل التحقق"
    elif result["category"] == "unsupported_auth":
        result["message"] = "⚠️ يحتاج App Password"
    elif result["category"] == "network":
        result["level"] = "unknown"
        result["badge"] = "⚪"
        result["message"] = "🌐 فشل الاتصال بخادم البريد"
    elif result["totp_ok"]:
        result["level"] = "partial"
        result["badge"] = "🟡"
        result["message"] = "🔐 رمز المصادقة صالح"
    else:
        result["level"] = "failed"
        result["badge"] = "🔴"
        result["message"] = "❌ الإيميل أو كلمة مرور التطبيق غير صحيحة"

    result["short_category"] = detect_short_category(result)
    result["short_message"] = shorten_verify_message(result["short_category"], result["message"])
    return result


def imap_rate_ok(email: str) -> Tuple[bool, int]:
    normalized = normalize_email(email)
    last = _IMAP_RATE.get(normalized, 0.0)
    now = time.time()
    if now - last < IMAP_RATE_LIMIT_SECONDS:
        return False, int(IMAP_RATE_LIMIT_SECONDS - (now - last))
    return True, 0


def imap_rate_mark(email: str):
    _IMAP_RATE[normalize_email(email)] = time.time()


# ==================== TOTP CODE GENERATION ====================
def generate_totp_code_info(totp_secret: str) -> Tuple[bool, str, str, int]:
    if not totp_secret:
        return False, "", "لا يوجد رمز مصادقة لهذا الطلب.", 0
    cleaned = totp_secret.replace(" ", "").upper()
    if not validate_totp_secret(cleaned):
        return False, "", "رمز المصادقة غير صالح (يجب أن يكون 32 حرفاً Base32).", 0
    try:
        totp = pyotp.TOTP(cleaned)
        code = totp.now()
        if not code or len(code) != 6 or not code.isdigit():
            return False, "", "فشل توليد الكود.", 0
        seconds_remaining = 30 - (int(time.time()) % 30)
        if seconds_remaining <= 0:
            seconds_remaining = 30
        return True, code, "", seconds_remaining
    except Exception as exc:
        logger.exception("TOTP generation error")
        return False, "", f"خطأ في توليد الكود: {exc}", 0


async def send_totp_code_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    totp_secret: str,
    email: str = "",
    title: str = "🔢 كود المصادقة الحالي",
    back_callback: str = "",
    auto_refresh: bool = False,
):
    ok, code, err, seconds = generate_totp_code_info(totp_secret)
    if not ok:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ {err}", parse_mode=ParseMode.MARKDOWN)
        return None

    email_line = f"\n📧 `{email}`\n" if email else "\n"
    text = (
        f"{title}\n"
        f"{email_line}\n"
        f"🔢 *الكود:* `{code}`\n\n"
        f"⏰ صالح لمدة *{seconds}* ثانية\n\n"
        f"_اضغط «🔄 كود جديد» للحصول على كود محدّث._"
    )
    buttons = []
    if auto_refresh and back_callback:
        buttons.append(("🔄 كود جديد", back_callback))
    if back_callback:
        buttons.append(("🔙 رجوع", back_callback))
    reply_markup = kb_vertical(buttons) if buttons else None
    try:
        return await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except Exception:
        logger.exception("Failed to send TOTP code message")
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"🔢 الكود: {code}\n⏰ {seconds} ثانية")
        except Exception:
            pass
        return None


# ==================== FORCED CHANNEL ====================
def normalize_forced_channel(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://t\.me/", "", value, flags=re.IGNORECASE)
    value = value.split("?", 1)[0].split("/", 1)[0].strip()
    if value and not value.startswith("@") and not value.lstrip("-").isdigit():
        value = f"@{value}"
    return value


def parse_forced_channel_input(value: str) -> Tuple[str, str]:
    channel_value, separator, link_value = value.partition("|")
    channel = normalize_forced_channel(channel_value)
    invite_link = link_value.strip() if separator else ""
    valid_handle = bool(re.fullmatch(r"@[A-Za-z0-9_]{5,32}", channel))
    valid_chat_id = bool(re.fullmatch(r"-100\d+", channel))
    valid_invite_link = not invite_link or bool(
        re.fullmatch(
            r"https?://t\.me/(?:\+[A-Za-z0-9_-]+|joinchat/[A-Za-z0-9_-]+)",
            invite_link,
            flags=re.IGNORECASE,
        )
    )
    if not (valid_handle or valid_chat_id) or not valid_invite_link:
        return "", ""
    return channel, invite_link


def get_configured_purchase_channels() -> Tuple[str, str]:
    config = load_config()
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
    config = load_config()
    forced_channel = normalize_forced_channel(str(config.get("forced_channel", "")))
    if not forced_channel:
        return True
    user_id = update.effective_user.id
    if user_id == OWNER_ID:
        return True
    if context.user_data.get("_forced_channel_checked_update_id") == update.update_id:
        return True
    try:
        member = await context.bot.get_chat_member(forced_channel, user_id)
        if member.status in {"member", "administrator", "creator"} or (
            member.status == "restricted" and getattr(member, "is_member", False)
        ):
            context.user_data["_forced_channel_checked_update_id"] = update.update_id
            return True
    except Exception as exc:
        logger.warning("Forced-channel check failed for %s: %s", user_id, exc)
    text = f"📢 *يرجى الانضمام إلى القناة أولاً:*\n{forced_channel}\n\nبعد الانضمام اضغط على زر «تحققت من الاشتراك»."
    reply_markup = forced_channel_keyboard(forced_channel, str(config.get("forced_channel_link", "")))
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        except Exception:
            await update.callback_query.answer("لم يتم العثور على اشتراكك بعد.", show_alert=True)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    return False


async def check_forced_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_forced_channel(update, context):
        await main_menu(update, context)


# ==================== SORT HELPERS ====================
def _record_sort_key(record: dict) -> str:
    return str(
        record.get("timestamp")
        or record.get("approval_time")
        or record.get("rejected_at")
        or record.get("completed_at")
        or record.get("confirmed_at")
        or ""
    )


def sort_records_newest_first(records: List[dict]) -> List[dict]:
    if not isinstance(records, list):
        return []
    try:
        return sorted(records, key=_record_sort_key, reverse=True)
    except Exception:
        return list(records)


def sort_records_oldest_first(records: List[dict]) -> List[dict]:
    if not isinstance(records, list):
        return []
    try:
        return sorted(records, key=_record_sort_key)
    except Exception:
        return list(records)


# ==================== TOP SELLERS & SALES STATS ====================
def compute_top_sellers(limit: int = 10) -> List[dict]:
    users = load_json(USERS_DB)
    rows = []
    for raw_uid, encrypted_data in users.items():
        try:
            uid = int(raw_uid)
        except (TypeError, ValueError):
            continue
        user_data = decrypt_user_data(encrypted_data)
        total = (
            len(user_data.get("approved_accounts", []) or [])
            + len(user_data.get("pending_requests", []) or [])
            + len(user_data.get("rejected_requests", []) or [])
        )
        if total <= 0:
            continue
        rows.append({"user_id": uid, "total": total})
    rows.sort(key=lambda r: (-r["total"], r["user_id"]))
    return rows[:limit]


def compute_sales_stats(since_iso: Optional[str] = None) -> dict:
    start_dt = None
    if since_iso:
        try:
            start_dt = datetime.fromisoformat(since_iso)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            start_dt = None

    users = load_json(USERS_DB)
    total_accounts = 0
    per_user: Dict[int, int] = {}

    def _record_in_window(rec: dict) -> bool:
        if start_dt is None:
            return True
        t = _record_sort_key(rec)
        if not t:
            return False
        try:
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        return dt >= start_dt

    for raw_uid, encrypted_data in users.items():
        try:
            uid = int(raw_uid)
        except (TypeError, ValueError):
            continue
        ud = decrypt_user_data(encrypted_data)
        count = 0
        for coll in ("approved_accounts", "pending_requests", "rejected_requests"):
            for rec in ud.get(coll, []) or []:
                if _record_in_window(rec):
                    count += 1
        if count > 0:
            per_user[uid] = count
            total_accounts += count

    top = sorted(per_user.items(), key=lambda x: (-x[1], x[0]))[:10]
    top_list = [{"user_id": uid, "total": c} for uid, c in top]
    return {"total_accounts": total_accounts, "top": top_list, "since": since_iso}


# ==================== CONTEST SYSTEM ====================
def get_contest() -> dict:
    config = load_config()
    contest = config.get("contest")
    if not isinstance(contest, dict):
        return {}
    if "tiers" not in contest:
        old_target = int(contest.get("target_emails", 0) or 0)
        old_reward = float(contest.get("reward_per_winner", 0) or 0)
        if old_target > 0 and old_reward > 0:
            contest["tiers"] = [{"emails": old_target, "reward": old_reward}]
            migrated = []
            for w in contest.get("winners", []) or []:
                w2 = dict(w)
                w2.setdefault("tier_index", 0)
                w2.setdefault("tier_emails", old_target)
                migrated.append(w2)
            contest["winners"] = migrated
        else:
            contest["tiers"] = []
    if "max_winners" not in contest:
        contest["max_winners"] = 0
    return contest


def save_contest(contest: dict):
    config = load_config()
    config["contest"] = contest
    save_config(config)


def _count_approved_in_window(user_data: dict, start_iso: Optional[str]) -> int:
    if not start_iso:
        return 0
    try:
        start_dt = datetime.fromisoformat(start_iso)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    count = 0
    for acc in user_data.get("approved_accounts", []) or []:
        if acc.get("rejected_at_24h"):
            continue
        t = _record_sort_key(acc)
        if not t:
            continue
        try:
            dt = datetime.fromisoformat(t)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt >= start_dt:
            count += 1
    return count


def contest_summary_lines(contest: dict) -> List[str]:
    if not contest:
        return ["📭 لا توجد مسابقة حالياً."]
    active = bool(contest.get("active"))
    started_at = contest.get("started_at")
    max_winners = int(contest.get("max_winners", 0) or 0)
    tiers = contest.get("tiers", []) or []
    winners = contest.get("winners", []) or []
    total_pts = float(contest.get("total_points_awarded", 0) or 0)
    total_emails_delivered = int(contest.get("total_emails_delivered", 0) or 0)
    lines = []
    lines.append(f"📌 <b>الحالة:</b> {'🟢 نشطة' if active else '🔴 متوقفة'}")
    if started_at:
        lines.append(f"🕐 <b>بدأت:</b> <code>{tg_html_escape(_format_iso_time(started_at))}</code>")
    lines.append(f"🏅 <b>الحد الأقصى للفائزين (لكل جائزة):</b> <code>{max_winners}</code>")
    lines.append("")
    lines.append("🎁 <b>الجوائز:</b>")
    if not tiers:
        lines.append("_لا توجد جوائز._")
    for i, tier in enumerate(tiers, 1):
        emails = int(tier.get("emails", 0) or 0)
        reward = float(tier.get("reward", 0) or 0)
        lines.append(f"  {i}. عند <b>{emails}</b> إيميل → <code>${reward:.2f}</code>")
    lines.append("")
    lines.append(f"✅ <b>عدد الفائزين الحالي:</b> <code>{len(winners)}</code>")
    lines.append(f"📨 <b>عدد الإيميلات الواصلة (خلال المسابقة):</b> <code>{total_emails_delivered}</code>")
    lines.append(f"💵 <b>النقاط الممنوحة:</b> <code>${total_pts:.2f}</code>")
    return lines


async def check_contest_award(context: ContextTypes.DEFAULT_TYPE, uid: int):
    contest = get_contest()
    if not contest or not contest.get("active"):
        return
    started_at = contest.get("started_at")
    if not started_at:
        return
    tiers = contest.get("tiers", []) or []
    max_winners = int(contest.get("max_winners", 0) or 0)
    if not tiers or max_winners <= 0:
        return

    user_data = get_user(uid)
    count = _count_approved_in_window(user_data, started_at)

    contest["total_emails_delivered"] = int(contest.get("total_emails_delivered", 0) or 0) + 1
    save_contest(contest)

    winners = contest.get("winners", []) or []
    awarded_now: List[Tuple[int, float]] = []

    for tier_index, tier in enumerate(tiers):
        tier_emails = int(tier.get("emails", 0) or 0)
        tier_reward = float(tier.get("reward", 0) or 0)
        if tier_emails <= 0 or tier_reward <= 0:
            continue
        if count < tier_emails:
            continue
        already_won = any(
            int(w.get("user_id", 0)) == uid and int(w.get("tier_index", -1)) == tier_index
            for w in winners
        )
        if already_won:
            continue
        tier_winners_count = sum(
            1 for w in winners if int(w.get("tier_index", -1)) == tier_index
        )
        if tier_winners_count >= max_winners:
            continue

        user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) + tier_reward)
        user_data["total_credited_balance"] = clamp_money(
            float(user_data.get("total_credited_balance", 0.0) or 0.0) + tier_reward)
        add_transaction(user_data, "credit", tier_reward,
                        f"جائزة المسابقة (هدف {tier_emails} إيميل)", "")
        winners.append({
            "user_id": uid, "tier_index": tier_index, "tier_emails": tier_emails,
            "reward": tier_reward, "awarded_at": datetime.now(timezone.utc).isoformat(),
        })
        awarded_now.append((tier_emails, tier_reward))

    if not awarded_now:
        return

    wins = user_data.get("contest_wins", []) or []
    for tier_emails, tier_reward in awarded_now:
        wins.append({
            "contest_started_at": started_at,
            "awarded_at": datetime.now(timezone.utc).isoformat(),
            "tier_emails": tier_emails, "reward": tier_reward,
        })
    user_data["contest_wins"] = wins
    save_user(uid, user_data)

    contest["winners"] = winners
    total_now = sum(r for _, r in awarded_now)
    contest["total_points_awarded"] = float(contest.get("total_points_awarded", 0) or 0) + total_now
    save_contest(contest)

    tiers_lines = "\n".join([f"  • عند {e} إيميل → <code>${r:.2f}</code>" for e, r in awarded_now])
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=(f"🎉 <b>مبروك! فزت في المسابقة</b>\n\n"
                  f"✅ <b>عدد الإيميلات الحالية:</b> <code>{count}</code>\n"
                  f"💰 <b>المكافآت المكتسبة:</b>\n{tiers_lines}\n\n"
                  f"💵 <b>الإجمالي الممنوح الآن:</b> <code>${total_now:.2f}</code>\n"
                  f"تم إضافة المكافأة إلى رصيدك مباشرة."),
            parse_mode=ParseMode.HTML)
    except Exception:
        pass
    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(f"🎉 <b>فائز جديد في المسابقة</b>\n\n"
                  f"👤 المستخدم: <code>{uid}</code>\n"
                  f"✅ الإيميلات الحالية: <code>{count}</code>\n"
                  f"💵 إجمالي الممنوح: <code>${total_now:.2f}</code>\n"
                  f"🎁 مكافآت: <code>{len(awarded_now)}</code>"),
            parse_mode=ParseMode.HTML)
    except Exception:
        pass


# ==================== MAIN MENU ====================
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    SESSIONS.pop(update.effective_user.id, None)
    save_sessions()
    user = update.effective_user
    user_data = get_user(user.id)
    ban_msg = get_active_ban_message(user_data)
    prefix = (ban_msg + "\n\n") if ban_msg else ""
    buttons = [
        ("➕ إضافة حساب", "add_account"),
        ("💰 أموالي", "my_wallet"),
        ("📜 سجل معاملاتي", "my_transactions"),
        ("📋 حساباتي", "my_accounts"),
        ("🏆 الأكثر بيعاً", "top_sellers"),
        ("📧 الإيميلات المرفوضة", "rejected_emails"),
        ("📺 تعليم", "tutorials"),
        ("🛒 سحب", "withdraw_store"),
        ("🔗 الإحالة", "referral_menu"),
        ("✏️ تعديل حساباتي", "edit_my_accounts"),
    ]
    if is_admin(user.id):
        buttons.append(("🛠 الإدارية", "admin_panel"))
    if user.id == OWNER_ID:
        buttons.append(("⚙️ إعدادات المالك", "owner_panel"))
    text = prefix + "👋 مرحباً بك!\nاختر من القائمة أدناه:"
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))
        except Exception:
            await update.callback_query.message.reply_text(
                text, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))
    else:
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


# ==================== TOP SELLERS VIEW ====================
async def top_sellers_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    top = compute_top_sellers(limit=10)
    back_btn = ("🔙 إعدادات المالك", "owner_panel") if (
        update.effective_user.id == OWNER_ID and query.data == "top_sellers_owner"
    ) else ("🔙 القائمة الرئيسية", "main_menu")
    if not top:
        await query.edit_message_text("📭 لا توجد بيانات بيع بعد.", reply_markup=kb_vertical([back_btn]))
        return
    lines = ["🏆 <b>الأكثر بيعاً</b>", ""]
    for idx, item in enumerate(top, 1):
        lines.append(f"{idx}- ID <code>{item['user_id']}</code> بيع <b>{item['total']}</b>")
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                  reply_markup=kb_vertical([back_btn]))


# ==================== TRANSACTIONS LOG ====================
async def my_transactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    user_data = get_user(query.from_user.id)
    transactions = user_data.get("transactions", [])
    if not transactions:
        await query.edit_message_text("📭 لا توجد معاملات بعد.",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    kind_icons = {"credit": "➕", "debit": "➖", "hold": "🔒", "release": "🔓",
                  "referral": "🎁", "purchase": "🛒", "admin_bonus": "🛠",
                  "admin_bonus_pending": "⏳", "admin_bonus_release": "✅",
                  "approved_rejection": "❌", "admin_bonus_reversal": "↩️"}
    lines = ["📜 <b>آخر 20 معاملة:</b>", ""]
    for tx in transactions[-20:][::-1]:
        icon = kind_icons.get(tx.get("kind", ""), "•")
        amount = float(tx.get("amount", 0))
        when = tx.get("at", "")[:19].replace("T", " ")
        note = tg_html_escape(tx.get("note", ""))[:40]
        email = tg_html_escape(tx.get("email", ""))[:30]
        detail = f" — {note}" if note else ""
        email_part = f"\n   📧 <code>{email}</code>" if email else ""
        lines.append(f"{icon} <b>${amount:.2f}</b>{detail}{email_part}\n   🕐 <code>{when}</code>")
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                  reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


# ==================== MY ACCOUNTS ====================
async def my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    user_data = get_user(query.from_user.id)
    approved = sort_records_newest_first(user_data.get("approved_accounts", []) or [])
    pending = sort_records_oldest_first(user_data.get("pending_requests", []) or [])
    rejected = sort_records_newest_first(user_data.get("rejected_requests", []) or [])
    if not approved and not pending and not rejected:
        await query.edit_message_text("📭 لا توجد حسابات لديك.",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    msg = "📋 *جميع حساباتي:*\n\n"
    if approved:
        msg += "✅ *مقبولة (الأحدث أولاً):*\n"
        for idx, acc in enumerate(approved, 1):
            leave_status = ""
            if acc.get("rejected_at_24h"):
                leave_status = " ❌ (رفض بعد 24 ساعة)"
            elif acc.get("approved_with_leave", False) and not acc.get("leave_confirmed", False):
                leave_status = " ⏳ (معلق 24 ساعة)"
            elif acc.get("approved_with_leave", False) and acc.get("leave_confirmed", False):
                leave_status = " ✅ (تم التحويل)"
            msg += f"  {idx}. 📧 `{acc.get('email', '')}` ✅{leave_status}\n"
        msg += "\n"
    if pending:
        msg += "⏳ *منتظرة (الأقدم أولاً):*\n"
        for idx, req in enumerate(pending, 1):
            msg += f"  {idx}. 📧 `{req.get('email', '')}` ⏳\n"
        msg += "\n"
    if rejected:
        msg += "❌ *مرفوضة (الأحدث أولاً):*\n"
        for idx, rej in enumerate(rejected, 1):
            reason = rej.get('reject_reason', 'غير معروف')
            reason_text = REJECT_REASON_LABELS.get(reason, reason)
            msg += f"  {idx}. 📧 `{rej.get('email', '')}` ❌ - {reason_text}\n"
        msg += "\n"
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


# ==================== MEMBER REJECTED EMAILS ====================
async def view_member_rejected_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    user_data = get_user(query.from_user.id)
    rejected = sort_records_newest_first(user_data.get("rejected_requests", []) or [])
    if not rejected:
        rejected = [{"email": email, "reject_reason": "unknown"}
                    for email in (user_data.get("rejected_emails", []) or [])]
    if not rejected:
        await query.edit_message_text("📭 لا توجد لديك إيميلات مرفوضة حاليًا.",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    reason_map = {
        "email": "الإيميل غير صحيح أو غير مقبول", "password": "كلمة المرور غير صحيحة",
        "totp": "رمز المصادقة غير صحيح", "app_pass": "كلمة مرور التطبيق غير صحيحة",
        "phone": "يحتاج رقم هاتف", "other": "سبب آخر",
        "custom": "سبب مخصص", "auto_verify_failed": "فشل التحقق التلقائي",
        "unknown": "غير معروف",
    }
    lines = ["❌ <b>الإيميلات المرفوضة (الأحدث أولاً)</b>", ""]
    for index, request in enumerate(rejected, 1):
        email = tg_html_escape(str(request.get("email", "غير معروف")))
        password = tg_html_escape(str(request.get("password", "") or "")) or "❌ غير مرسل"
        totp = tg_html_escape(str(request.get("totp", "") or "")) or "❌ غير مرسل"
        app_pass = tg_html_escape(str(request.get("app_pass", "") or "")) or "❌ غير مرسل"
        reason = request.get("reject_reason", "unknown")
        reason_text = request.get("reject_reason_text") or reason_map.get(reason, str(reason))
        if request.get("has_app_pass", False):
            tier_text = "إيميل + باسورد + TOTP + كلمة مرور تطبيق"
        elif request.get("has_totp", False):
            tier_text = "إيميل + باسورد + TOTP"
        else:
            tier_text = "إيميل + باسورد"
        lines.append(f"{index}. 📧 <code>{email}</code>")
        lines.append(f"   🔑 الباسورد: <code>{password}</code>")
        lines.append(f"   🔐 رمز المصادقة: <code>{totp}</code>")
        lines.append(f"   🗝 كلمة مرور التطبيق: <code>{app_pass}</code>")
        lines.append(f"   📦 المستوى: {tg_html_escape(tier_text)}")
        lines.append(f"   السبب: {tg_html_escape(str(reason_text))}")
        if index < len(rejected):
            lines.append("")
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                  reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


# ==================== EDIT MY ACCOUNTS ====================
async def edit_my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    query = update.callback_query
    user_data = get_user(query.from_user.id)
    pending = user_data.get("pending_requests", [])
    if not pending:
        await query.edit_message_text("📭 لا توجد حسابات جارية للتعديل.",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    buttons = []
    for idx, req in enumerate(pending):
        buttons.append((f"✏️ {req.get('email', '')}", f"edit_pending:{query.from_user.id}:{idx}"))
    buttons.append(("🔙 القائمة الرئيسية", "main_menu"))
    await query.edit_message_text("✏️ *تعديل الحسابات الجارية*\nاختر الحساب لتعديله:",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def edit_pending_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    query = update.callback_query
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text("⚠️ هذا الحساب غير موجود أو تمت معالجته.",
                                      reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))
        return
    request = pending[index]
    email = request.get("email", "")
    context.user_data["editing_email"] = email
    context.user_data["editing_index"] = index
    buttons = [
        ("🔑 تغيير الباسورد", f"edit_field:password:{uid}:{index}"),
        ("🔐 تغيير رمز المصادقة", f"edit_field:totp:{uid}:{index}"),
        ("🗝️ تغيير كلمة مرور التطبيق", f"edit_field:app_pass:{uid}:{index}"),
        ("🗑️ مسح الحساب", f"delete_pending:{uid}:{index}"),
        ("🔙 تعديل حساباتي", "edit_my_accounts")
    ]
    await query.edit_message_text(f"✏️ *تعديل الحساب:* `{email}`\n\nاختر ما تريد تعديله:",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    query = update.callback_query
    parts = query.data.split(":")
    field = parts[1]
    uid = int(parts[2])
    index = int(parts[3])
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text("⚠️ الحساب غير موجود.",
                                      reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))
        return
    email = pending[index].get("email", "")
    context.user_data["editing_field"] = field
    context.user_data["editing_uid"] = uid
    context.user_data["editing_index"] = index
    field_names = {"password": "كلمة المرور", "totp": "رمز المصادقة الثنائية", "app_pass": "كلمة مرور التطبيق"}
    await query.edit_message_text(
        f"✏️ *تعديل {field_names.get(field, field)}*\nللحساب: `{email}`\n\nأرسل القيمة الجديدة:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", f"edit_pending:{uid}:{index}"))
    context.user_data["step"] = "editing_field"


async def delete_pending_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    query = update.callback_query
    parts = query.data.split(":")
    uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text("⚠️ الحساب غير موجود.",
                                      reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))
        return
    request = pending[index]
    pending.pop(index)
    user_data["pending_requests"] = pending
    user_data["pending_balance"] = clamp_money(
        float(user_data.get("pending_balance", 0.0)) - float(request.get("amount", 0.0)))
    save_user(uid, user_data)
    await query.edit_message_text(f"✅ تم مسح الحساب `{request.get('email', '')}` بنجاح.",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))


async def handle_edit_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    field = context.user_data.get("editing_field")
    editing_uid = context.user_data.get("editing_uid")
    index = context.user_data.get("editing_index")
    if not field or editing_uid is None or index is None:
        await update.message.reply_text("⚠️ حدث خطأ، حاول مرة أخرى.")
        return
    user_data = get_user(editing_uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        clear_edit_state(context)
        await update.message.reply_text("⚠️ انتهت جلسة تعديل الحساب.")
        return
    pending[index][field] = text
    user_data["pending_requests"] = pending
    save_user(editing_uid, user_data)
    try:
        await update.message.delete()
    except Exception:
        pass
    for key in ("editing_field", "editing_uid", "editing_index", "step"):
        context.user_data.pop(key, None)
    await update.message.reply_text(
        f"✅ تم تحديث {field} بنجاح للحساب `{pending[index].get('email', '')}`.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 تعديل حساباتي", "edit_my_accounts"))


# ==================== AUTO PROCESS VERIFICATION ====================
async def auto_process_verification(
    context: ContextTypes.DEFAULT_TYPE,
    uid: int,
    request_index: int,
    request: dict,
    short_category: str,
    short_message: str,
) -> bool:
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if request_index >= len(pending):
        return False

    if short_category == "success":
        email = request.get("email", "")
        price = float(request.get("amount", 0.0))

        approval_time = datetime.now(timezone.utc)
        account_record = {
            "email": str(email),
            "password": str(request.get("password", "")),
            "totp": str(request.get("totp", "")),
            "app_pass": str(request.get("app_pass", "")),
            "amount": price,
            "timestamp": approval_time.isoformat(),
            "approval_time": approval_time.isoformat(),
            "release_at": (approval_time + timedelta(seconds=LEAVE_HOLD_SECONDS)).isoformat(),
            "hold_seconds": LEAVE_HOLD_SECONDS,
            "extracted": False,
            "has_totp": bool(request.get("has_totp", False)),
            "has_app_pass": bool(request.get("has_app_pass", False)),
            "user_name": request.get("user_name", "غير معروف"),
            "user_username": request.get("user_username", "لا يوجد"),
            "approved_with_leave": True,
            "leave_confirmed": False,
            "auto_verified": True,
            "auto_approved": True,
            "verification": {
                "level": "verified", "badge": "🟢", "message": short_message,
                "short_category": short_category, "imap_ok": True,
                "totp_ok": bool(request.get("has_totp", False)),
                "verified_at": approval_time.isoformat(),
                "verified_by": "auto_on_submit",
            },
        }

        user_data.setdefault("approved_accounts", []).append(account_record)
        user_data["pending_balance"] = clamp_money(
            float(user_data.get("pending_balance", 0.0)) - price)
        user_data["hold_balance"] = clamp_money(
            float(user_data.get("hold_balance", 0.0)) + price)
        user_data["total_credited_balance"] = clamp_money(
            float(user_data.get("total_credited_balance", 0.0) or 0.0) + price)
        user_data["total_approved_emails"] = int(user_data.get("total_approved_emails", 0)) + 1
        pending.pop(request_index)
        user_data["pending_requests"] = pending
        add_transaction(user_data, "hold", price, "تحقق تلقائي ناجح - معلق 24 ساعة", email)
        register_approval_reset(user_data)
        save_user(uid, user_data)

        try:
            await check_contest_award(context, uid)
        except Exception:
            logger.exception("Contest check failed")

        try:
            await award_referral_bonus(context, uid, user_data, email)
        except Exception:
            logger.exception("Referral bonus failed")

        try:
            await schedule_leave_check(context, uid, email, account_record["release_at"])
        except Exception:
            logger.exception("Schedule leave check failed")

        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(f"✅ <b>تم قبول حسابك تلقائياً!</b>\n\n"
                      f"📧 <code>{tg_html_escape(email)}</code>\n"
                      f"💰 تم إضافة <b>${price:.2f}</b> إلى رصيدك المعلق\n\n"
                      f"⏰ سيتم فحص الحساب مرة أخرى بعد <b>24 ساعة</b>.\n"
                      f"📌 <i>لا تنسى مغادرة الحساب.</i>"),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass

        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=(f"🟢 <b>قبول تلقائي ناجح</b>\n\n"
                      f"👤 <code>{uid}</code> — {tg_html_escape(request.get('user_name', 'غير معروف'))}\n"
                      f"📧 <code>{tg_html_escape(email)}</code>\n"
                      f"💰 <b>${price:.2f}</b>\n\n"
                      f"⏰ سيُعاد فحصه تلقائياً بعد 24 ساعة."),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass

        try:
            await send_leave_video_to_user(context, uid, email)
        except Exception:
            logger.exception("Leave video failed")

        return True

    email = request.get("email", "")
    pending.pop(request_index)
    user_data["pending_requests"] = pending
    move_request_to_rejected(user_data, request, "auto_verify_failed", short_message)
    register_rejection_and_maybe_ban(user_data)
    if user_data.get("rejected_requests"):
        user_data["rejected_requests"][-1]["rejected_by"] = "auto"
        user_data["rejected_requests"][-1]["rejected_by_name"] = "التحقق التلقائي"
        user_data["rejected_requests"][-1]["rejected_at"] = datetime.now(timezone.utc).isoformat()
    save_user(uid, user_data)

    try:
        await context.bot.send_message(
            chat_id=uid,
            text=(f"❌ <b>فشل التحقق من حسابك</b>\n\n"
                  f"📧 <code>{tg_html_escape(email)}</code>\n\n"
                  f"📝 <b>السبب:</b> {tg_html_escape(short_message)}\n\n"
                  f"<i>يمكنك المحاولة مرة أخرى بإرسال حساب آخر.</i>"),
            parse_mode=ParseMode.HTML)
    except Exception:
        pass

    return False


# ==================== ADD ACCOUNT FLOW ====================
async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    uid = update.effective_user.id

    user_data_check = get_user(uid)
    ban_msg = get_active_ban_message(user_data_check)
    if ban_msg:
        try:
            await update.callback_query.answer("🚫 أنت محظور حالياً.", show_alert=True)
        except Exception:
            pass
        await _safe_edit(update.callback_query, ban_msg, parse_mode=ParseMode.HTML,
                         reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    SESSIONS.pop(uid, None)
    save_sessions()
    clear_edit_state(context)

    for key in ("step", "editing_field", "editing_uid", "editing_index",
                "admin_completing_uid", "admin_completing_index",
                "admin_completing_token", "admin_approval_step",
                "approval_uid", "approval_index", "approval_token",
                "approval_data", "approval_step", "approval_with_leave",
                "reject_uid", "reject_index", "reject_reason",
                "deduct_uid", "deduct_token", "give_uid", "give_index",
                "mode", "store_action", "setting_tier", "pending_video_type",
                "contest_build", "contest_build_step",
                "reject_approved_uid", "reject_approved_token"):
        context.user_data.pop(key, None)

    SESSIONS[uid] = Session(step="email")
    save_sessions()
    config = load_config()
    prices = get_tier_prices()
    has_email_video = config.get("video_email") and Path(config.get("video_email", "")).exists()
    buttons = []
    if has_email_video:
        buttons.append(("📹 طريقة إنشاء حساب", "show_video:email"))
    buttons.append(("❌ إلغاء", "cancel"))
    await update.callback_query.edit_message_text(
        f"📝 <b>إضافة حساب جديد</b>\n\n"
        f"💵 <b>نظام المكافآت المتدرج:</b>\n"
        f"• إيميل + باسورد → <b>${prices['tier_1']:.2f}</b>\n"
        f"• + رمز مصادقة → <b>${prices['tier_2']:.2f}</b>\n"
        f"• + كلمة مرور التطبيق → <b>${prices['tier_3']:.2f}</b>\n\n"
        f"✨ <i>كل مرحلة تُرسل للمالك فوراً، ويمكنك إكمال الباقي متى شئت.</i>\n\n"
        f"📧 <b>الخطوة 1/2</b>: أرسل الإيميل:",
        parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def show_video_in_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    vtype = query.data.split(":")[1]
    config = load_config()
    path = config.get(f"video_{vtype}")
    if path and Path(path).exists():
        try:
            await context.bot.send_video(chat_id=query.from_user.id, video=open(path, "rb"),
                                         caption="📹 *فيديو تعليمي*\nشاهد الفيديو لمعرفة الطريقة الصحيحة.",
                                         parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await query.edit_message_text("⚠️ حدث خطأ في تشغيل الفيديو.",
                                          reply_markup=kb_single("🔙 العودة", "add_account"))
    else:
        await query.edit_message_text("⚠️ الفيديو غير متوفر حالياً.",
                                      reply_markup=kb_single("🔙 العودة", "add_account"))


async def add_account_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    SESSIONS.pop(uid, None)
    save_sessions()
    clear_edit_state(context)
    await update.callback_query.edit_message_text("❌ تم الإلغاء.",
                                                  reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


async def add_account_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    session = SESSIONS.get(uid)
    if not session or not session.step:
        return
    if context.user_data.get("step") == "editing_field":
        await handle_edit_field_input(update, context)
        return
    config = load_config()
    prices = get_tier_prices()

    # ═══ الخطوة 1: الإيميل ═══
    if session.step == "email":
        first_line = text.splitlines()[0].strip() if text else ""
        email = normalize_email(first_line)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            await update.message.reply_text("❌ إيميل غير صالح. أرسل إيميلاً صحيحاً:")
            return
        active_status = get_active_account_status(email)
        if active_status == "approved":
            await update.message.reply_text(
                "❌ هذا الإيميل مقبول مسبقاً! لا يمكنك إعادة إرساله.",
                reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
            SESSIONS.pop(uid, None); save_sessions()
            return
        if active_status == "pending":
            user_data = get_user(uid)
            pending = user_data.get("pending_requests", [])
            old_amounts = [float(r.get("amount", 0.0)) for r in pending
                           if normalize_email(r.get("email", "")) == email]
            new_pending = [r for r in pending
                           if normalize_email(r.get("email", "")) != email]
            if len(new_pending) != len(pending):
                user_data["pending_requests"] = new_pending
                user_data["pending_balance"] = clamp_money(
                    float(user_data.get("pending_balance", 0.0)) - sum(old_amounts))
                save_user(uid, user_data)
        session.password = ""
        session.totp = ""
        session.app_pass = ""
        session.has_password = False
        session.has_totp = False
        session.has_app_pass = False
        session.email = email
        session.step = "password"
        save_sessions()
        has_password_video = config.get("video_password") and Path(config.get("video_password", "")).exists()
        buttons = []
        if has_password_video:
            buttons.append(("📹 طريقة تغيير الباسورد", "show_video:password"))
        buttons.append(("❌ إلغاء", "cancel"))
        await update.message.reply_text(
            f"🔑 <b>الخطوة 2/2</b>: أرسل كلمة المرور الأساسية:\n\n"
            f"💰 <b>مكافأة إيميل + باسورد:</b> <b>${prices['tier_1']:.2f}</b>",
            parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))

    # ═══ الخطوة 2: الباسورد ═══
    elif session.step == "password":
        if has_active_account_password(text):
            await update.message.reply_text(
                "⚠️ كلمة المرور مستخدمة مسبقاً في حساب مقبول أو قيد الانتظار.",
                reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
            SESSIONS.pop(uid, None); save_sessions()
            return

        session.password = text
        session.has_password = True

        user_data = get_user(uid)
        user = update.effective_user
        user_full_name = user.full_name or "غير معروف"
        user_username = user.username or "لا يوجد"
        clear_rejected_email_records(user_data, session.email)

        price = prices["tier_1"]

        new_request = {
            "email": str(session.email or ""),
            "password": str(session.password or ""),
            "totp": "",
            "app_pass": "",
            "amount": price,
            "requested_amount": price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "extracted": False,
            "has_totp": False,
            "has_app_pass": False,
            "user_name": user_full_name,
            "user_username": user_username,
        }
        user_data.setdefault("pending_requests", []).append(new_request)
        user_data["pending_balance"] = clamp_money(
            float(user_data.get("pending_balance", 0.0)) + price)
        user_data["user_name"] = user_full_name
        user_data["user_username"] = user_username
        add_transaction(user_data, "hold", price, "طلب (إيميل + باسورد)", session.email)
        save_user(uid, user_data)

        session.step = "submitted_tier_1"
        save_sessions()

        try:
            await update.message.delete()
        except Exception:
            pass

        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=(f"📥 <b>طلب جديد — المستوى 1</b>\n\n"
                      f"👤 {tg_html_escape(user_full_name)} (@{tg_html_escape(user_username)})\n"
                      f"🆔 <code>{uid}</code>\n"
                      f"📧 <code>{tg_html_escape(session.email)}</code>\n"
                      f"🔑 <code>{tg_html_escape(session.password)}</code>\n"
                      f"💰 <b>${price:.2f}</b>\n\n"
                      f"⏳ <i>العضو يمكنه إكمال البيانات للحصول على المزيد.</i>"),
                parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception("Failed to notify owner (tier_1)")

        await update.message.reply_text(
            f"✅ <b>تم إرسال بياناتك بنجاح!</b>\n\n"
            f"📧 <code>{tg_html_escape(session.email)}</code>\n"
            f"🔑 الباسورد: ✅\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 <b>حصلت على ${price:.2f}</b>\n"
            f"   <i>(إيميل + باسورد)</i>\n"
            f"━━━━━━━━━━━━━━━\n\n"
            f"🎁 <b>هل تريد الحصول على المزيد؟</b>\n\n"
            f"🔐 أضف رمز المصادقة (2FA) → <b>${prices['tier_2']:.2f}</b>\n"
            f"🗝 ثم كلمة مرور التطبيق → <b>${prices['tier_3']:.2f}</b>\n\n"
            f"<i>اضغط على زر الإكمال لبدء الإضافة، أو إنهاء إذا كنت راضياً.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("🚀 إكمال للحصول على المزيد", f"complete_more:{uid}"),
                ("✅ إنهاء", f"finish_request:{uid}"),
            ]))

    # ═══ الخطوة 3: TOTP ═══
    elif session.step == "totp":
        if not session.email or not session.password:
            await update.message.reply_text(
                "⚠️ حدث خطأ في الجلسة. يرجى البدء من جديد.",
                reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
            SESSIONS.pop(uid, None); save_sessions()
            return
        try:
            cleaned = text.replace(" ", "").upper()
            if len(cleaned) != 32:
                await update.message.reply_text("⚠️ مفتاح المصادقة يجب أن يكون 32 حرفاً.")
                return
            if not re.match(r'^[A-Z2-7]{32}$', cleaned):
                await update.message.reply_text("⚠️ مفتاح المصادقة يحتوي على أحرف غير صالحة.")
                return
            secret = cleaned
            code = pyotp.TOTP(secret).now()

            user_data = get_user(uid)
            pending = user_data.get("pending_requests", [])
            found_idx = None
            for i, req in enumerate(pending):
                if normalize_email(req.get("email", "")) == normalize_email(session.email):
                    found_idx = i
                    break
            if found_idx is None:
                await update.message.reply_text(
                    "⚠️ لم يتم العثور على طلبك. ابدأ من جديد.",
                    reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
                SESSIONS.pop(uid, None); save_sessions()
                return

            old_price = float(pending[found_idx].get("amount", 0.0))
            new_price = prices["tier_2"]
            pending[found_idx]["totp"] = secret
            pending[found_idx]["has_totp"] = True
            pending[found_idx]["amount"] = new_price
            pending[found_idx]["requested_amount"] = new_price
            user_data["pending_requests"] = pending
            user_data["pending_balance"] = clamp_money(
                float(user_data.get("pending_balance", 0.0)) - old_price + new_price)
            save_user(uid, user_data)

            session.totp = secret
            session.has_totp = True
            session.step = "submitted_tier_2"
            save_sessions()

            try:
                await update.message.delete()
            except Exception:
                pass

            user = update.effective_user
            user_full_name = user.full_name or "غير معروف"
            user_username = user.username or "لا يوجد"

            try:
                await context.bot.send_message(
                    chat_id=OWNER_ID,
                    text=(f"📥 <b>تحديث الطلب — إضافة 2FA</b>\n\n"
                          f"👤 {tg_html_escape(user_full_name)} (@{tg_html_escape(user_username)})\n"
                          f"🆔 <code>{uid}</code>\n"
                          f"📧 <code>{tg_html_escape(session.email)}</code>\n"
                          f"🔑 <code>{tg_html_escape(session.password)}</code>\n"
                          f"🔐 <code>{tg_html_escape(secret)}</code>\n"
                          f"🔢 الكود الحالي: <code>{code}</code>\n"
                          f"💰 <b>${new_price:.2f}</b>"),
                    parse_mode=ParseMode.HTML)
            except Exception:
                logger.exception("Failed to notify owner (tier_2)")

            await update.message.reply_text(
                f"✅ <b>تم إضافة رمز المصادقة!</b>\n\n"
                f"🔢 <b>الكود الحالي:</b> <code>{code}</code>\n\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💰 <b>الآن حصلت على ${new_price:.2f}</b>\n"
                f"━━━━━━━━━━━━━━━\n\n"
                f"🎁 <b>هل تريد الحصول على المزيد؟</b>\n\n"
                f"🗝 أضف كلمة مرور التطبيق → <b>${prices['tier_3']:.2f}</b>\n\n"
                f"<i>اضغط إكمال لمتابعة الإضافة، أو إنهاء إذا كنت راضياً.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_vertical([
                    ("🚀 إكمال للحصول على المزيد", f"complete_more:{uid}"),
                    ("✅ إنهاء", f"finish_request:{uid}"),
                ]))
        except Exception as e:
            await update.message.reply_text(f"⚠️ مفتاح 2FA غير صالح: {str(e)}")

    # ═══ الخطوة 4: App Password ═══
    elif session.step == "app_pass":
        cleaned = text.replace(" ", "")
        if len(cleaned) != 16:
            await update.message.reply_text("⚠️ كلمة مرور التطبيق يجب أن تكون 16 حرفاً.")
            return
        if not re.match(r'^[A-Za-z0-9]{16}$', cleaned):
            await update.message.reply_text("⚠️ كلمة مرور التطبيق تحتوي على أحرف غير صالحة.")
            return
        if has_active_app_password(cleaned):
            config_video = config.get("video_app_pass")
            msg = ("⚠️ <b>كلمة المرور هذه مستخدمة مسبقاً!</b>\n\n"
                   "يرجى تغيير كلمة المرور وإرسال كلمة جديدة.")
            if config_video and Path(config_video).exists():
                try:
                    await context.bot.send_video(chat_id=uid, video=open(config_video, "rb"),
                                                 caption=msg, parse_mode=ParseMode.HTML,
                                                 supports_streaming=True)
                except Exception:
                    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
            else:
                await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
            return

        user_data = get_user(uid)
        pending = user_data.get("pending_requests", [])
        found_idx = None
        for i, req in enumerate(pending):
            if normalize_email(req.get("email", "")) == normalize_email(session.email):
                found_idx = i
                break
        if found_idx is None:
            await update.message.reply_text(
                "⚠️ لم يتم العثور على طلبك. ابدأ من جديد.",
                reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
            SESSIONS.pop(uid, None); save_sessions()
            return

        old_price = float(pending[found_idx].get("amount", 0.0))
        new_price = prices["tier_3"]
        pending[found_idx]["app_pass"] = cleaned
        pending[found_idx]["has_app_pass"] = True
        pending[found_idx]["amount"] = new_price
        pending[found_idx]["requested_amount"] = new_price
        user_data["pending_requests"] = pending
        user_data["pending_balance"] = clamp_money(
            float(user_data.get("pending_balance", 0.0)) - old_price + new_price)
        save_user(uid, user_data)

        session.app_pass = cleaned
        session.has_app_pass = True
        save_sessions()

        try:
            await update.message.delete()
        except Exception:
            pass

        allowed, wait = imap_rate_ok(session.email)
        if not allowed:
            await update.message.reply_text(
                f"⏳ انتظر {wait} ثانية قبل إعادة المحاولة.",
                reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
            return
        imap_rate_mark(session.email)

        progress_msg = await update.message.reply_text(
            f"🔍 <b>جاري التحقق التلقائي من الحساب...</b>\n\n"
            f"📧 <code>{tg_html_escape(session.email)}</code>",
            parse_mode=ParseMode.HTML)

        verify_result = await verify_account_credentials(
            email=session.email,
            password=session.password,
            app_pass=session.app_pass,
            totp_secret=session.totp if session.has_totp else "",
        )

        short_cat = verify_result.get("short_category", "unknown")
        short_msg = verify_result.get("short_message", "❌ فشل التحقق")

        try:
            await progress_msg.delete()
        except Exception:
            pass

        user_data = get_user(uid)
        pending = user_data.get("pending_requests", [])
        found_idx2 = None
        for i, req in enumerate(pending):
            if normalize_email(req.get("email", "")) == normalize_email(session.email):
                found_idx2 = i
                break

        if found_idx2 is not None:
            await auto_process_verification(
                context, uid, found_idx2, pending[found_idx2], short_cat, short_msg)

        SESSIONS.pop(uid, None)
        save_sessions()

    # ═══ حالة انتظار الأزرار ═══
    elif session.step in ("submitted_tier_1", "submitted_tier_2"):
        await update.message.reply_text(
            "📌 <i>يرجى استخدام الأزرار أعلاه للاختيار.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("🚀 إكمال للحصول على المزيد", f"complete_more:{uid}"),
                ("✅ إنهاء", f"finish_request:{uid}"),
            ]))


# ==================== CONTINUE / FINISH ====================
async def complete_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        uid = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.answer("⚠️ بيانات غير صحيحة.", show_alert=True)
        return
    if query.from_user.id != uid:
        await query.answer("⚠️ غير مصرح.", show_alert=True)
        return
    session = SESSIONS.get(uid)
    if not session:
        await query.answer("⚠️ الجلسة منتهية. ابدأ من جديد.", show_alert=True)
        return

    config = load_config()
    prices = get_tier_prices()

    if not session.has_totp:
        session.step = "totp"
        save_sessions()
        has_video = config.get("video_totp") and Path(config.get("video_totp", "")).exists()
        buttons = []
        if has_video:
            buttons.append(("📹 كيف أجد رمز المصادقة؟", "show_video:totp"))
        buttons.append(("🔙 رجوع", f"back_to_offer:{uid}"))
        await query.edit_message_text(
            f"🔐 <b>إضافة رمز المصادقة (2FA)</b>\n\n"
            f"📧 <code>{tg_html_escape(session.email)}</code>\n\n"
            f"💰 <b>المكافأة بعد الإضافة: ${prices['tier_2']:.2f}</b>\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📌 <b>أرسل مفتاح المصادقة (Secret Key):</b>\n\n"
            f"• يجب أن يكون <b>32 حرف</b>\n\n"
            f"<i>لمعرفة كيفية الحصول عليه، اضغط على زر الفيديو أعلاه.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical(buttons))
    elif not session.has_app_pass:
        session.step = "app_pass"
        save_sessions()
        has_video = config.get("video_app_pass") and Path(config.get("video_app_pass", "")).exists()
        buttons = []
        if has_video:
            buttons.append(("📹 كيف أحصل على كلمة مرور التطبيق؟", "show_video:app_pass"))
        buttons.append(("🔙 رجوع", f"back_to_offer:{uid}"))
        await query.edit_message_text(
            f"🗝 <b>إضافة كلمة مرور التطبيق</b>\n\n"
            f"📧 <code>{tg_html_escape(session.email)}</code>\n\n"
            f"💰 <b>المكافأة بعد الإضافة: ${prices['tier_3']:.2f}</b>\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📌 <b>أرسل كلمة مرور التطبيق:</b>\n\n"
            f"• يجب أن تكون <b>16 حرف</b>\n\n"
            f"<i>لمعرفة كيفية الحصول عليها، اضغط على زر الفيديو أعلاه.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical(buttons))
    else:
        await query.answer("✅ الحساب مكتمل بالفعل.", show_alert=True)


async def finish_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        uid = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.answer("⚠️ بيانات غير صحيحة.", show_alert=True)
        return
    if query.from_user.id != uid:
        await query.answer("⚠️ غير مصرح.", show_alert=True)
        return
    SESSIONS.pop(uid, None)
    save_sessions()
    await query.edit_message_text(
        "✅ <b>تم إنهاء الطلب</b>\n\n"
        "📌 سيتم مراجعة طلبك من قبل المالك.\n\n"
        "<i>شكراً لاستخدامك البوت 🤖</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


async def back_to_offer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        uid = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.answer("⚠️ بيانات غير صحيحة.", show_alert=True)
        return
    if query.from_user.id != uid:
        await query.answer("⚠️ غير مصرح.", show_alert=True)
        return
    session = SESSIONS.get(uid)
    if not session:
        await query.answer("⚠️ الجلسة منتهية.", show_alert=True)
        return

    prices = get_tier_prices()
    if not session.has_totp:
        session.step = "submitted_tier_1"
        save_sessions()
        current_price = prices["tier_1"]
        next_price = prices["tier_2"]
        next_label = "🔐 رمز المصادقة (2FA)"
    elif not session.has_app_pass:
        session.step = "submitted_tier_2"
        save_sessions()
        current_price = prices["tier_2"]
        next_price = prices["tier_3"]
        next_label = "🗝 كلمة مرور التطبيق"
    else:
        await query.edit_message_text("✅ الحساب مكتمل بالفعل.")
        return

    await query.edit_message_text(
        f"💰 <b>حصلت على ${current_price:.2f}</b>\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🎁 <b>هل تريد الحصول على المزيد؟</b>\n\n"
        f"{next_label} → <b>${next_price:.2f}</b>\n"
        f"━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            ("🚀 إكمال للحصول على المزيد", f"complete_more:{uid}"),
            ("✅ إنهاء", f"finish_request:{uid}"),
        ]))


# ==================== LEAVE VIDEO ====================
async def send_leave_video_to_user(context: ContextTypes.DEFAULT_TYPE, user_id: int, email: str):
    config = load_config()
    video_path = config.get("video_leave")
    text = (f"📹 *فيديو المغادرة*\n\n📧 الإيميل: `{email}`\n\n⚠️ *تعليمات مهمة:*\n"
            f"• قم بمغادرة الحساب الآن.\n"
            f"• إذا لم تقم بمغادرة الحساب،\n"
            f"• سيتم تأخير دفع المبلغ المستحق لك لمدة 24 ساعة.\n\n"
            f"_شاهد الفيديو لمعرفة طريقة المغادرة الصحيحة_")
    if video_path and Path(video_path).exists():
        try:
            await context.bot.send_video(chat_id=user_id, video=open(video_path, "rb"), caption=text,
                                         parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            return True
        except Exception as e:
            logger.error(f"Error sending leave video to user {user_id}: {e}")
            try:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
            return False
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    return False


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


def _format_iso_time(value: Any) -> str:
    if not value or not isinstance(value, str):
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return value[:19]


async def schedule_leave_check(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                                email: str, release_at: Optional[str] = None):
    job_name = f"leave_check_{user_id}_{email}"
    for job in context.job_queue.get_jobs_by_name(job_name):
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
    if not account or account.get("leave_confirmed", False):
        return

    price = float(account.get("amount", 0.0))
    hold_hours = max(1, int(account.get("hold_seconds", LEAVE_HOLD_SECONDS)) // 3600)

    allowed, wait = imap_rate_ok(email)
    if not allowed:
        await schedule_leave_check(
            context, user_id, email,
            (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat())
        return
    imap_rate_mark(email)

    recheck = await verify_account_credentials(
        email=account.get("email", ""),
        password=account.get("password", ""),
        app_pass=account.get("app_pass", ""),
        totp_secret=account.get("totp", ""),
    )

    short_cat = recheck.get("short_category", "unknown")
    short_msg = recheck.get("short_message", "")

    if not recheck["imap_ok"]:
        user_data["hold_balance"] = clamp_money(
            float(user_data.get("hold_balance", 0.0)) - price)

        if account.get("admin_bonus_status") == "pending":
            admin_id = account.get("completed_by_admin")
            admin_bonus = float(account.get("admin_bonus", 0.0) or 0.0)
            if admin_id and admin_bonus > 0:
                admin_data = get_user(int(admin_id))
                admin_data["admin_pending_balance"] = clamp_money(
                    float(admin_data.get("admin_pending_balance", 0.0)) - admin_bonus)
                add_transaction(admin_data, "admin_bonus_reversal", admin_bonus,
                                f"فشل إعادة فحص {email}", email)
                save_user(int(admin_id), admin_data)
                account["admin_bonus_status"] = "rejected"

        account["leave_confirmed"] = True
        account["rejected_at_24h"] = True
        account["rejection_reason"] = f"فشل إعادة الفحص بعد {hold_hours} ساعة: {short_msg}"
        account["verification_24h"] = {
            "level": recheck["level"], "badge": recheck["badge"],
            "message": short_msg, "short_category": short_cat,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        add_transaction(user_data, "debit", price,
                        f"رفض بعد فحص {hold_hours} ساعة", email)
        save_user(user_id, user_data)

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(f"❌ <b>فشل الحساب في الفحص الثاني</b>\n\n"
                      f"📧 <code>{tg_html_escape(email)}</code>\n"
                      f"📝 <b>السبب:</b> {tg_html_escape(short_msg)}\n\n"
                      f"💰 تم خصم <b>${price:.2f}</b> من رصيدك المعلق."),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return

    user_data["hold_balance"] = clamp_money(
        float(user_data.get("hold_balance", 0.0)) - price)
    user_data["balance"] = clamp_money(
        float(user_data.get("balance", 0.0)) + price)

    if account.get("admin_bonus_status") == "pending":
        admin_id = account.get("completed_by_admin")
        admin_bonus = float(account.get("admin_bonus", 0.0) or 0.0)
        if admin_id and admin_bonus > 0:
            admin_data = get_user(int(admin_id))
            admin_data["admin_pending_balance"] = clamp_money(
                float(admin_data.get("admin_pending_balance", 0.0)) - admin_bonus)
            admin_data["admin_received_balance"] = clamp_money(
                float(admin_data.get("admin_received_balance", 0.0)) + admin_bonus)
            add_transaction(admin_data, "admin_bonus_release", admin_bonus,
                            f"وصول مكافأة إكمال {email}", email)
            save_user(int(admin_id), admin_data)
            account["admin_bonus_status"] = "released"

    account["leave_confirmed"] = True
    account["auto_confirmed"] = True
    account["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    account["released_amount"] = price
    account["verification_24h"] = {
        "level": recheck["level"], "badge": recheck["badge"],
        "message": short_msg, "short_category": short_cat,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    add_transaction(user_data, "release", price,
                    f"تحويل تلقائي بعد {hold_hours} ساعة", email)
    save_user(user_id, user_data)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(f"✅ <b>تم إضافة المبلغ إلى رصيدك!</b>\n\n"
                  f"📧 <code>{tg_html_escape(email)}</code>\n"
                  f"💰 تم إضافة <b>${price:.2f}</b> إلى رصيدك الدائم.\n\n"
                  f"<i>شكراً لاستخدامك البوت 🤖</i>"),
            parse_mode=ParseMode.HTML)
    except Exception:
        pass


async def restore_leave_checks(application: Application):
    users = load_json(USERS_DB)
    changed = False
    now = datetime.now(timezone.utc)
    for user_id, encrypted_data in users.items():
        user_data = decrypt_user_data(encrypted_data)
        for account in user_data.get("approved_accounts", []):
            if not account.get("approved_with_leave", False):
                continue
            if account.get("leave_confirmed", False):
                continue
            release_at = parse_iso_datetime(account.get("release_at"))
            if release_at is None:
                approval_time = parse_iso_datetime(account.get("approval_time"))
                if approval_time is None:
                    continue
                hold_seconds = int(account.get("hold_seconds", LEAVE_HOLD_SECONDS))
                release_at = approval_time + timedelta(seconds=hold_seconds)
                account["release_at"] = release_at.isoformat()
                changed = True
            email = account.get("email", "")
            job_name = f"leave_check_{user_id}_{email}"
            for job in application.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()
            application.job_queue.run_once(
                callback=check_leave_status,
                when=max(0, (release_at - now).total_seconds()),
                data={"user_id": int(user_id), "email": email, "release_at": release_at.isoformat()},
                name=job_name,
            )
    if changed:
        save_json(USERS_DB, users)
    load_sessions_from_disk()
    load_pending_purchases_from_disk()
    application.job_queue.run_repeating(daily_backup_job, interval=24 * 3600, first=3600, name="daily_backup")
    logger.info("Restore complete.")


async def daily_backup_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        for source_name in ("users.json", "config.json", "admins.json"):
            source = DATA_DIR / source_name
            if source.exists():
                shutil.copy2(source, BACKUP_DIR / f"{source.stem}_{stamp}.json")
        cutoff = time.time() - 7 * 86400
        for backup in BACKUP_DIR.iterdir():
            try:
                if backup.is_file() and backup.stat().st_mtime < cutoff:
                    backup.unlink()
            except OSError:
                pass
        logger.info("Daily backup completed.")
    except Exception:
        logger.exception("Backup failed.")


# ==================== OWNER STATS CHART ====================
def create_owner_stats_chart(path: Path, password_only: int, extra_sections: int) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.exception("Pillow is required.")
        return False
    width, height = 1000, 620
    image = Image.new("RGB", (width, height), "#101827")
    draw = ImageDraw.Draw(image)
    font_paths = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                  "/usr/share/fonts/dejavu/DejaVuSans.ttf")
    font_path = next((c for c in font_paths if Path(c).exists()), None)
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
        draw.rounded_rectangle((x, y, x + bar_width, baseline), radius=18, fill=color)
        value_box = draw.textbbox((0, 0), str(value), font=value_font)
        value_width = value_box[2] - value_box[0]
        draw.text((x + (bar_width - value_width) / 2, max(y - 46, chart_top - 10)),
                  str(value), fill="#F8FAFC", font=value_font)
        label_box = draw.textbbox((0, 0), label, font=label_font)
        label_width = label_box[2] - label_box[0]
        draw.text((x + (bar_width - label_width) / 2, baseline + 24),
                  label, fill="#CBD5E1", font=label_font)
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
    for user_id, encrypted_data in users.items():
        user_data = decrypt_user_data(encrypted_data)
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
        rows.append({"user_id": str(user_id),
                     "points": round(float(user_data.get("balance", 0.0) or 0.0), 2),
                     "accepted": len(approved_accounts)})
    top_users = sorted(rows, key=lambda r: (r["points"], r["accepted"]), reverse=True)[:10]
    message = ("📊 <b>إحصائيات المستخدمين</b>\n\n"
               f"👥 عدد المستخدمين: <b>{len(rows)}</b>\n"
               f"📧 الإيميلات المقبولة: <b>{total_accepted}</b>\n"
               f"🔵 إيميل + باسورد فقط: <b>{password_only}</b>\n"
               f"🟣 أقسام إضافية: <b>{extra_sections}</b>\n\n"
               "🏆 <b>أكثر المستخدمين نقاطاً:</b>\n")
    if top_users:
        for index, row in enumerate(top_users, 1):
            message += (f"{index}. المستخدم <code>{row['user_id']}</code> — "
                        f"💰 {row['points']:.2f} نقطة — 📧 {row['accepted']} إيميل\n")
    else:
        message += "لا توجد بيانات.\n"
    chart_path = DATA_DIR / "owner_stats_chart.png"
    if create_owner_stats_chart(chart_path, password_only, extra_sections):
        with chart_path.open("rb") as chart_file:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=chart_file,
                                         caption=message, parse_mode=ParseMode.HTML,
                                         reply_markup=kb_single("🔙 إعدادات المالك", "owner_panel"))
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=message,
                                       parse_mode=ParseMode.HTML,
                                       reply_markup=kb_single("🔙 إعدادات المالك", "owner_panel"))


# ==================== MEMBER CHECK ====================
def resolve_member_id(user_input: str) -> Optional[int]:
    users = load_json(USERS_DB)
    cleaned = user_input.strip()
    if cleaned.lstrip("-").isdigit():
        candidate = cleaned.lstrip("-")
        return int(cleaned) if candidate in users else None
    username = cleaned.removeprefix("@").casefold()
    if not username:
        return None
    for uid, encrypted_data in users.items():
        user_data = decrypt_user_data(encrypted_data)
        candidates = [user_data.get("user_username"), user_data.get("username")]
        for collection_name in ("approved_accounts", "pending_requests", "rejected_requests"):
            for record in user_data.get(collection_name, []):
                candidates.append(record.get("user_username"))
        if any(str(c or "").removeprefix("@").casefold() == username for c in candidates):
            try:
                return int(uid)
            except (TypeError, ValueError):
                return None
    return None


def member_balance_stats(user_data: dict) -> dict:
    current = clamp_money(user_data.get("balance", 0.0))
    hold = clamp_money(user_data.get("hold_balance", 0.0))
    approved_total = sum(float(a.get("amount", 0.0) or 0.0) for a in user_data.get("approved_accounts", []))
    referral_earnings = float(user_data.get("referral_earnings", 0.0) or 0.0)
    recorded_total = float(user_data.get("total_credited_balance", 0.0) or 0.0)
    total_credited = max(approved_total + referral_earnings, recorded_total, current + hold)
    recorded_spent = user_data.get("spent_balance")
    if recorded_spent is None:
        spent = max(0.0, total_credited - current - hold)
    else:
        spent = max(0.0, float(recorded_spent or 0.0))
    total_balance = max(total_credited, current + spent + hold)
    return {"current": current, "spent": round(spent, 2), "hold": hold,
            "pending": clamp_money(user_data.get("pending_balance", 0.0)),
            "total": round(total_balance, 2)}


def calculate_member_hold_balance(user_data: dict) -> float:
    return clamp_money(sum(
        float(account.get("amount", 0.0) or 0.0)
        for account in user_data.get("approved_accounts", [])
        if account.get("approved_with_leave")
        and not account.get("leave_confirmed")
    ))


def calculate_admin_ledger(admin_id: int) -> dict:
    pending_cents = 0
    released_cents = 0
    legacy_released_cents = 0
    completed_count = 0
    pending_count = 0
    released_count = 0
    for encrypted_data in load_json(USERS_DB).values():
        owner_data = decrypt_user_data(encrypted_data)
        for account in owner_data.get("approved_accounts", []):
            if str(account.get("completed_by_admin")) != str(admin_id):
                continue
            status = account.get("admin_bonus_status")
            if status in {"rejected", "not_applicable"}:
                continue
            if account.get("rejected_at_24h"):
                continue
            completed_count += 1
            bonus_cents = money_to_cents(account.get("admin_bonus", 0.0))
            if status == "pending":
                pending_cents += bonus_cents
                pending_count += 1
            elif status == "released":
                released_cents += bonus_cents
                released_count += 1
            elif account.get("approved_with_leave") and not account.get("leave_confirmed"):
                pending_cents += bonus_cents
                pending_count += 1
            else:
                legacy_released_cents += bonus_cents
                released_count += 1

    released_total_cents = released_cents + legacy_released_cents
    if released_total_cents <= 0:
        released_total_cents = sum(
            money_to_cents(tx.get("amount", 0.0))
            for raw_uid, encrypted_data in load_json(USERS_DB).items()
            if str(raw_uid) == str(admin_id)
            for tx in decrypt_user_data(encrypted_data).get("transactions", [])
            if tx.get("kind") in {"admin_bonus", "admin_bonus_release"}
        )

    return {
        "pending": cents_to_money(pending_cents),
        "released": cents_to_money(released_total_cents),
        "completed_count": completed_count,
        "pending_count": pending_count,
        "released_count": released_count,
    }


def admin_balance_stats(user_data: dict, admin_id: Optional[int] = None) -> dict:
    pending = clamp_money(user_data.get("pending_balance", 0.0))
    hold = calculate_member_hold_balance(user_data)
    admin_ledger = (
        calculate_admin_ledger(admin_id)
        if admin_id is not None
        else {
            "pending": clamp_money(user_data.get("admin_pending_balance", 0.0)),
            "released": clamp_money(user_data.get("admin_received_balance", 0.0)),
            "completed_count": 0, "pending_count": 0, "released_count": 0,
        }
    )
    admin_pending = admin_ledger["pending"]
    received = clamp_money(user_data.get("admin_received_balance", 0.0))
    received_total = max(received, admin_ledger["released"])
    ordinary = clamp_money(user_data.get("balance", 0.0))
    total_owned = clamp_money(ordinary + received)
    return {
        "pending": pending, "hold": hold, "admin_pending": admin_pending,
        "received": received_total, "available_admin": received,
        "completed_count": admin_ledger["completed_count"],
        "pending_count": admin_ledger["pending_count"],
        "released_count": admin_ledger["released_count"],
        "ordinary": ordinary, "total_owned": total_owned,
    }


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
        reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"))


async def handle_member_check_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    text = update.message.text.strip()
    if text.casefold() in {"إلغاء", "الغاء", "cancel"}:
        context.user_data.pop("step", None)
        await update.message.reply_text("❌ تم إلغاء فحص العضو.",
                                        reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"))
        return
    member_id = resolve_member_id(text)
    if member_id is None:
        await update.message.reply_text("⚠️ لم يتم العثور على هذا العضو.",
                                        reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"))
        return
    user_data = get_user(member_id)
    approved = user_data.get("approved_accounts", [])
    pending = user_data.get("pending_requests", [])
    rejected = user_data.get("rejected_requests", [])
    submitted_accounts = approved + pending + rejected
    submitted_count = len(submitted_accounts)
    password_only = sum(1 for a in submitted_accounts if not a.get("has_totp") and not a.get("has_app_pass"))
    totp_only = sum(1 for a in submitted_accounts if a.get("has_totp") and not a.get("has_app_pass"))
    app_password = sum(1 for a in submitted_accounts if a.get("has_totp") and a.get("has_app_pass"))
    balances = member_balance_stats(user_data)
    admin_balances = (admin_balance_stats(user_data, member_id) if is_admin(member_id) else None)
    display_username = next(
        (r.get("user_username") for records in (approved, pending, rejected)
         for r in records if r.get("user_username")),
        user_data.get("user_username") or "لا يوجد")
    display_name = next(
        (r.get("user_name") for records in (approved, pending, rejected)
         for r in records if r.get("user_name")),
        user_data.get("user_name") or "غير معروف")
    message = (
        "🔎 <b>تقرير فحص العضو</b>\n\n"
        f"👤 <b>الاسم:</b> {tg_html_escape(display_name)}\n"
        f"🆔 <b>المعرف:</b> <code>{member_id}</code>\n"
        f"🔗 <b>اليوزر:</b> @{tg_html_escape(display_username)}\n\n"
        f"📧 <b>عدد الإيميلات المقدمة:</b> <code>{submitted_count}</code>\n"
        f"✅ <b>الإيميلات المقبولة:</b> <code>{len(approved)}</code>\n"
        f"⏳ <b>الإيميلات قيد الانتظار:</b> <code>{len(pending)}</code>\n"
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
    if admin_balances is not None:
        message += (
            "\n\n🛠 <b>تفصيل رصيد الأدمن</b>\n"
            f"⏳ طلبات بانتظار الموافقة: <code>${admin_balances['pending']:.2f}</code>\n"
            f"🔒 النقاط المقيدة (24/48 ساعة): <code>${admin_balances['hold']:.2f}</code>\n"
            f"🛠 الإيميلات التي أكملها الأدمن: <code>{admin_balances['completed_count']}</code>\n"
            f"⏳ منها مكافآت مقيدة: <code>{admin_balances['pending_count']}</code>\n"
            f"✅ منها مكافآت واصلة: <code>{admin_balances['released_count']}</code>\n"
            f"🛠 مكافآت الأدمن المقيدة (24/48 ساعة): <code>${admin_balances['admin_pending']:.2f}</code>\n"
            f"✅ إجمالي مكافآت الأدمن الواصلة: <code>${admin_balances['received']:.2f}</code>\n"
            f"💳 المتبقي منها للاستخدام: <code>${admin_balances['available_admin']:.2f}</code>\n"
            f"📊 الرصيد المتاح للاستخدام: <code>${admin_balances['total_owned']:.2f}</code>"
        )
    context.user_data.pop("step", None)
    await update.message.reply_text(message, parse_mode=ParseMode.HTML,
                                    reply_markup=kb_vertical([("🔎 فحص عضو آخر", "check_member"),
                                                              ("🔙 لوحة المالك", "owner_panel")]))


# ==================== CHECK EMAIL BY ADDRESS (OWNER) ====================
def _search_email_in_all_records(email: str) -> List[dict]:
    normalized = normalize_email(email)
    if not normalized:
        return []
    results: List[dict] = []
    users = load_json(USERS_DB)
    for raw_uid, encrypted_data in users.items():
        try:
            uid = int(raw_uid)
        except (TypeError, ValueError):
            continue
        user_data = decrypt_user_data(encrypted_data)
        for idx, acc in enumerate(user_data.get("approved_accounts", []) or []):
            if normalize_email(acc.get("email", "")) == normalized:
                results.append({"record_type": "approved", "user_id": uid,
                                "index": idx, "data": acc, "user_data": user_data})
        for idx, req in enumerate(user_data.get("pending_requests", []) or []):
            if normalize_email(req.get("email", "")) == normalized:
                results.append({"record_type": "pending", "user_id": uid,
                                "index": idx, "data": req, "user_data": user_data})
        for idx, rej in enumerate(user_data.get("rejected_requests", []) or []):
            if normalize_email(rej.get("email", "")) == normalized:
                results.append({"record_type": "rejected", "user_id": uid,
                                "index": idx, "data": rej, "user_data": user_data})
    approved = [i for i in results if i["record_type"] == "approved"]
    pending = [i for i in results if i["record_type"] == "pending"]
    rejected = [i for i in results if i["record_type"] == "rejected"]
    approved.sort(key=lambda i: _record_sort_key(i["data"]), reverse=True)
    rejected.sort(key=lambda i: _record_sort_key(i["data"]), reverse=True)
    pending.sort(key=lambda i: _record_sort_key(i["data"]))
    return approved + pending + rejected


def _format_record_status(record_type: str, rec: dict) -> str:
    if record_type == "pending":
        return "⏳ قيد الانتظار (مراجعة)"
    if record_type == "approved":
        if rec.get("rejected_at_24h"):
            return "🔴 مرفوض بعد فحص 24 ساعة"
        if rec.get("approved_with_leave") and not rec.get("leave_confirmed"):
            return "🟡 مقبول — رصيد معلّق (24 ساعة)"
        if rec.get("approved_with_leave") and rec.get("leave_confirmed"):
            return "🟢 مقبول — تم تحويل الرصيد"
        return "🟢 مقبول"
    reason = rec.get("reject_reason", "unknown")
    return f"❌ مرفوض — {REJECT_REASON_LABELS.get(reason, reason)}"


def _build_email_record_report(match: dict, position: int, total: int) -> str:
    record_type = match["record_type"]
    uid = match["user_id"]
    rec = match["data"]
    user_data = match["user_data"]

    lines: List[str] = []
    lines.append(f"<b>━━━ سجل {position}/{total} ━━━</b>")
    lines.append(f"<b>الحالة:</b> {_format_record_status(record_type, rec)}")
    lines.append(f"<b>نوع السجل:</b> <code>{record_type}</code>")

    display_name = rec.get("user_name") or user_data.get("user_name") or "غير معروف"
    display_username = rec.get("user_username") or user_data.get("user_username") or "لا يوجد"
    lines.append("")
    lines.append("<b>👤 بيانات البائع (من أرسله):</b>")
    lines.append(f"  • الاسم: {tg_html_escape(display_name)}")
    lines.append(f"  • اليوزر: @{tg_html_escape(display_username)}")
    lines.append(f"  • معرف: <code>{uid}</code>")

    lines.append("")
    lines.append("<b>📧 بيانات الحساب:</b>")
    lines.append(f"  • الإيميل: <code>{tg_html_escape(rec.get('email', ''))}</code>")
    password = rec.get("password", "")
    if password:
        lines.append(f"  • الباسورد: <code>{tg_html_escape(password)}</code>")
    else:
        lines.append("  • الباسورد: ❌ غير موجود")
    totp = rec.get("totp", "")
    if totp:
        lines.append(f"  • TOTP: <code>{tg_html_escape(totp)}</code>")
        try:
            current_code = pyotp.TOTP(totp).now()
            seconds_left = 30 - (int(time.time()) % 30)
            lines.append(f"  • الكود الحالي: <code>{current_code}</code> (⏰ {seconds_left}s)")
        except Exception:
            lines.append("  • الكود الحالي: ⚠️ تعذّر توليده")
    else:
        lines.append("  • TOTP: ❌ غير موجود")
    app_pass = rec.get("app_pass", "")
    if app_pass:
        lines.append(f"  • App Password: <code>{tg_html_escape(format_app_password(app_pass))}</code>")
    else:
        lines.append("  • App Password: ❌ غير موجود")

    if rec.get("has_app_pass") and rec.get("has_totp"):
        tier = "🟢 كامل (إيميل+باسورد+TOTP+App Pass)"
    elif rec.get("has_totp"):
        tier = "🟡 (إيميل+باسورد+TOTP)"
    else:
        tier = "🔵 (إيميل+باسورد فقط)"
    lines.append(f"  • المستوى: {tier}")

    amount = rec.get("amount")
    requested = rec.get("requested_amount")
    if amount is not None or requested is not None:
        lines.append("")
        lines.append("<b>💰 المبالغ:</b>")
        if amount is not None:
            try:
                lines.append(f"  • المبلغ: <code>${float(amount):.2f}</code>")
            except (TypeError, ValueError):
                lines.append(f"  • المبلغ: <code>{tg_html_escape(str(amount))}</code>")
        if requested is not None:
            try:
                lines.append(f"  • المبلغ المطلوب: <code>${float(requested):.2f}</code>")
            except (TypeError, ValueError):
                pass

    ts_entries = [
        ("وقت الإرسال", rec.get("timestamp")),
        ("وقت القبول", rec.get("approval_time")),
        ("موعد التحرير", rec.get("release_at")),
        ("وقت الرفض", rec.get("rejected_at")),
    ]
    rendered = [(label, _format_iso_time(v)) for label, v in ts_entries if v]
    if rendered:
        lines.append("")
        lines.append("<b>🕐 الأوقات:</b>")
        for label, v in rendered:
            lines.append(f"  • {label}: <code>{tg_html_escape(v)}</code>")

    if record_type == "approved":
        lines.append("")
        lines.append("<b>📌 حالة المغادرة/التحويل:</b>")
        if rec.get("leave_confirmed"):
            lines.append("  • ✅ تم التأكيد والتحويل")
        elif rec.get("approved_with_leave"):
            lines.append("  • ⏳ معلّق — لم يؤكد بعد")
        else:
            lines.append("  • ➖ لا ينطبق (قبول فوري)")
        if rec.get("auto_confirmed"):
            lines.append("  • 🤖 تأكيد تلقائي: نعم")
        if rec.get("auto_approved"):
            lines.append("  • ⚡ قبول تلقائي: نعم")
        if rec.get("rejected_at_24h"):
            lines.append("  • ❌ مرفوض بعد إعادة الفحص")
        if rec.get("rejection_reason"):
            lines.append(f"  • سبب الرفض (24h): {tg_html_escape(rec.get('rejection_reason'))}")

    if rec.get("completed_by_admin"):
        lines.append("")
        lines.append("<b>🛠 من قبله/أكمله:</b>")
        lines.append(f"  • معرف الأدمن: <code>{rec.get('completed_by_admin')}</code>")
        if rec.get("completed_by_admin_name"):
            lines.append(f"  • الاسم: {tg_html_escape(rec.get('completed_by_admin_name'))}")
        if rec.get("completed_by_admin_username"):
            lines.append(f"  • اليوزر: @{tg_html_escape(rec.get('completed_by_admin_username'))}")
        if rec.get("completed_at"):
            lines.append(f"  • وقت الإكمال: <code>{tg_html_escape(_format_iso_time(rec.get('completed_at')))}</code>")
        if rec.get("acceptance_mode"):
            mode_map = {"leave_video": "📹 مع فيديو المغادرة", "automatic": "✅ تلقائي"}
            lines.append(f"  • طريقة القبول: {mode_map.get(rec.get('acceptance_mode'), rec.get('acceptance_mode'))}")
        if rec.get("admin_bonus") is not None:
            try:
                lines.append(f"  • مكافأة الأدمن: <code>${float(rec.get('admin_bonus', 0)):.2f}</code>")
            except (TypeError, ValueError):
                pass
        if rec.get("admin_bonus_status"):
            status_map = {"pending": "⏳ معلّقة", "released": "✅ واصلة",
                          "rejected": "❌ ملغاة", "not_applicable": "➖ لا ينطبق"}
            lines.append(f"  • حالة المكافأة: {status_map.get(rec.get('admin_bonus_status'), rec.get('admin_bonus_status'))}")

    if rec.get("rejected_by"):
        lines.append("")
        lines.append("<b>❌ من رفضه:</b>")
        lines.append(f"  • معرف: <code>{rec.get('rejected_by')}</code>")
        if rec.get("rejected_by_name"):
            lines.append(f"  • الاسم: {tg_html_escape(rec.get('rejected_by_name'))}")
        if rec.get("rejected_by_username"):
            lines.append(f"  • اليوزر: @{tg_html_escape(rec.get('rejected_by_username'))}")
        if rec.get("rejected_at"):
            lines.append(f"  • وقت الرفض: <code>{tg_html_escape(_format_iso_time(rec.get('rejected_at')))}</code>")

    if record_type == "rejected" or rec.get("reject_reason"):
        reason = rec.get("reject_reason")
        if reason:
            lines.append("")
            lines.append("<b>📝 تفاصيل الرفض:</b>")
            lines.append(f"  • السبب: {REJECT_REASON_LABELS.get(reason, reason)}")
            if rec.get("reject_reason_text"):
                lines.append(f"  • نص السبب: {tg_html_escape(rec.get('reject_reason_text'))}")
            if rec.get("rejected_from_approved"):
                lines.append("  • 📌 مرفوض من سجل المقبولة")
            if rec.get("rejected_at"):
                lines.append(f"  • وقت: <code>{tg_html_escape(_format_iso_time(rec.get('rejected_at')))}</code>")

    verification = rec.get("verification") or {}
    if verification:
        lines.append("")
        lines.append("<b>🔍 التحقق الأولي:</b>")
        level = verification.get("level", "unknown")
        level_text = {"verified": "🟢 كامل", "partial": "🟡 جزئي",
                      "failed": "🔴 فشل", "unknown": "⚪ غير معروف"}.get(level, level)
        lines.append(f"  • المستوى: {level_text}")
        if verification.get("imap_ok") is not None:
            lines.append(f"  • IMAP: {'✅' if verification.get('imap_ok') else '❌'}")
        if verification.get("totp_ok") is not None:
            lines.append(f"  • TOTP: {'✅' if verification.get('totp_ok') else '❌'}")
        if verification.get("verified_by"):
            lines.append(f"  • بواسطة: <code>{tg_html_escape(verification.get('verified_by'))}</code>")
        if verification.get("verified_at"):
            lines.append(f"  • وقت: <code>{tg_html_escape(_format_iso_time(verification.get('verified_at')))}</code>")
        if verification.get("message"):
            lines.append(f"  • الرسالة: {tg_html_escape(verification.get('message'))}")

    verification_24h = rec.get("verification_24h") or {}
    if verification_24h:
        lines.append("")
        lines.append("<b>🔍 إعادة فحص 24 ساعة:</b>")
        level = verification_24h.get("level", "unknown")
        level_text = {"verified": "🟢 كامل", "partial": "🟡 جزئي",
                      "failed": "🔴 فشل", "unknown": "⚪ غير معروف"}.get(level, level)
        lines.append(f"  • المستوى: {level_text}")
        if verification_24h.get("verified_at"):
            lines.append(f"  • وقت: <code>{tg_html_escape(_format_iso_time(verification_24h.get('verified_at')))}</code>")
        if verification_24h.get("message"):
            lines.append(f"  • الرسالة: {tg_html_escape(verification_24h.get('message'))}")

    if rec.get("extracted"):
        lines.append("")
        lines.append("📤 <b>مستخرج:</b> ✅ نعم")

    return "\n".join(lines)


async def check_email_by_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    context.user_data["step"] = "check_email_input"
    await query.edit_message_text(
        "🔍 <b>فحص إيميل</b>\n\n"
        "أرسل عنوان الإيميل الذي تريد فحصه بالكامل:\n"
        "مثال: <code>user@example.com</code>\n\n"
        "سيتم عرض جميع السجلات المتعلقة به (منتظر / مقبول / مرفوض) "
        "مع من أرسله، من قبله، من رفضه، وكل التفاصيل الكاملة.\n\n"
        "أو أرسل «إلغاء» للعودة.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"))


async def handle_check_email_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    text = (update.message.text or "").strip()
    if text.casefold() in {"إلغاء", "الغاء", "cancel"}:
        context.user_data.pop("step", None)
        await update.message.reply_text(
            "❌ تم الإلغاء.",
            reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"))
        return

    email = text
    if "@" not in email or len(email) < 5:
        await update.message.reply_text("⚠️ أرسل عنوان إيميل صحيحاً.")
        return

    matches = _search_email_in_all_records(email)
    context.user_data.pop("step", None)

    if not matches:
        await update.message.reply_text(
            f"📭 <b>لا توجد نتائج</b>\n\n"
            f"📧 <code>{tg_html_escape(email)}</code>\n\n"
            f"لم يتم العثور على هذا الإيميل في أي سجل (منتظر/مقبول/مرفوض).",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("🔍 فحص إيميل آخر", "check_email_by_address"),
                ("🔙 لوحة المالك", "owner_panel"),
            ]))
        return

    await update.message.reply_text(
        f"🔍 <b>تقرير فحص الإيميل</b>\n\n"
        f"📧 <code>{tg_html_escape(email)}</code>\n"
        f"📊 عدد السجلات المطابقة: <b>{len(matches)}</b>\n\n"
        f"سيتم إرسال كل سجل بتفاصيله الكاملة…",
        parse_mode=ParseMode.HTML)

    for i, match in enumerate(matches, 1):
        report = _build_email_record_report(match, i, len(matches))
        if len(report) > 4000:
            report = report[:3900] + "\n\n<i>… (تم اقتصاص التقرير)</i>"
        try:
            await update.message.reply_text(report, parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception("Failed to send email record report")
            plain = re.sub(r"<[^>]+>", "", report)
            await update.message.reply_text(plain[:4000])

    unique_users = sorted({m["user_id"] for m in matches})
    footer_lines = ["<b>📌 ملخص سريع</b>"]
    footer_lines.append(f"• عدد السجلات: <b>{len(matches)}</b>")
    footer_lines.append(f"• عدد البائعين المختلفين: <b>{len(unique_users)}</b>")
    approved_ct = sum(1 for m in matches if m["record_type"] == "approved")
    pending_ct = sum(1 for m in matches if m["record_type"] == "pending")
    rejected_ct = sum(1 for m in matches if m["record_type"] == "rejected")
    footer_lines.append(f"• ✅ مقبولة: {approved_ct}")
    footer_lines.append(f"• ⏳ منتظرة: {pending_ct}")
    footer_lines.append(f"• ❌ مرفوضة: {rejected_ct}")
    if len(unique_users) == 1:
        uid = unique_users[0]
        u = get_user(uid)
        stats = member_balance_stats(u)
        footer_lines.append("")
        footer_lines.append(f"<b>💰 رصيد البائع (<code>{uid}</code>):</b>")
        footer_lines.append(f"  • الحالي: <code>${stats['current']:.2f}</code>")
        footer_lines.append(f"  • قيد الانتظار: <code>${stats['pending']:.2f}</code>")
        footer_lines.append(f"  • معلّق: <code>${stats['hold']:.2f}</code>")
        footer_lines.append(f"  • المستهلك: <code>${stats['spent']:.2f}</code>")
        footer_lines.append(f"  • الكلي: <code>${stats['total']:.2f}</code>")

    await update.message.reply_text(
        "\n".join(footer_lines),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            ("🔍 فحص إيميل آخر", "check_email_by_address"),
            ("🔙 لوحة المالك", "owner_panel"),
        ]))


# ==================== OWNER PANEL ====================
async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    context.user_data.pop("step", None)
    context.user_data.pop("store_action", None)
    context.user_data.pop("contest_build", None)
    context.user_data.pop("contest_build_step", None)
    buttons = [
        ("👥 الإدارية", "admin_management"),
        ("💰 أسعار المستويات", "set_tier_prices"),
        ("📋 الطلبات", "approval_requests"),
        ("🎯 إدارة المسابقة", "contest_menu"),
        ("🏆 الأكثر بيعاً", "top_sellers_owner"),
        ("📹 قسم الفيديوهات", "videos_section"),
        ("🛒 المبيعات", "store_section"),
        ("➕ إضافة قناة إجبارية", "forced_channel"),
        ("📨 كروبات إشعارات الشراء", "purchase_channels"),
        ("📊 جميع الحسابات المقبولة", "all_accounts_section"),
        ("❌ رفض إيميل مقبول بالعنوان", "reject_approved_by_email"),
        ("📈 إحصائيات المستخدمين", "owner_stats"),
        ("🔎 فحص عضو", "check_member"),
        ("🔍 فحص إيميل", "check_email_by_address"),
        ("🔗 نظام الإحالة", "referral_settings"),
        ("💰 خصم/منح نقاط", "points_management"),
        ("🔙 القائمة الرئيسية", "main_menu")
    ]
    await query.edit_message_text("⚙️ *لوحة تحكم المالك*\n\nاختر الإعداد الذي تريد تعديله:",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))
