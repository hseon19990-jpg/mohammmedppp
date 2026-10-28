"""
Advanced Telegram Account Manager Bot - v6.6
"""

import asyncio
import hashlib
import html
import imaplib
import io
import json
import logging
import os
import random
import re
import secrets
import shutil
import socket
import ssl
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
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
logger = logging.getLogger("bot")

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

# API rotation system limits
MAX_APIS_TOTAL = 30
MAX_APIS_PER_DAY_PER_ADMIN = 3
API_ADD_BONUS = 0.05
API_WARNING_THRESHOLD = 50
VERIFICATION_FAILURES_THRESHOLD = 3
RESTRICTION_DURATION_HOURS = 1

# Daily gift
IRAQ_TZ_OFFSET_HOURS = 3

# Sell-new-session expiry (30 days — effectively "forever" for user purposes)
SELL_NEW_SESSION_MAX_AGE_DAYS = 30

# ==================== SELL NEW EMAIL GENERATOR ====================
FIRST_NAMES_EN = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph",
    "Thomas", "Charles", "Christopher", "Daniel", "Matthew", "Anthony", "Mark",
    "Donald", "Steven", "Paul", "Andrew", "Joshua", "Kenneth", "Kevin", "Brian",
    "George", "Edward", "Ronald", "Timothy", "Jason", "Jeffrey", "Ryan",
    "Jacob", "Gary", "Nicholas", "Eric", "Jonathan", "Stephen", "Larry",
    "Justin", "Scott", "Brandon", "Benjamin", "Samuel", "Frank", "Gregory",
    "Alexander", "Patrick", "Jack", "Dennis", "Jerry", "Tyler", "Aaron",
    "Jose", "Adam", "Nathan", "Henry", "Douglas", "Zachary", "Peter",
    "Ethan", "Walter", "Kyle", "Harold", "Jeremy", "Terry", "Keith", "Roger",
]


def generate_sell_email_credentials() -> dict:
    first_name = random.choice(FIRST_NAMES_EN)
    letters = "".join(secrets.choice(string.ascii_lowercase) for _ in range(18))
    digits = "".join(secrets.choice(string.digits) for _ in range(5))
    email = f"{letters}{digits}@gmail.com"
    password = "".join(secrets.choice(string.ascii_letters) for _ in range(10))
    return {"first_name": first_name, "email": email, "password": password}


# ==================== EMAIL AVAILABILITY CHECKER ====================
_EMAIL_AVAILABILITY_CACHE: Dict[str, Tuple[float, Optional[bool]]] = {}
_LAST_CHECK_TIME = 0.0
_CHECK_MIN_INTERVAL = 1.5

_TAKEN_MARKERS = (
    "username is taken",
    "that username is taken",
    "already taken",
    "already in use",
    "not available",
    "someone already has",
    "has already been taken",
    "sorry, this username isn't available",
    "this username isn't available",
)


def check_email_availability_sync(username: str) -> Optional[bool]:
    try:
        url = "https://accounts.google.com/InputValidator?resource=SignUp"
        payload = json.dumps({
            "input01": {
                "Input": "GmailAddress",
                "GmailAddress": username,
                "FirstName": "Test",
                "LastName": "User",
            }
        }).encode("utf-8")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }
        req = Request(url, data=payload, headers=headers, method="POST")
        with urlopen(req, timeout=10) as response:
            if getattr(response, "status", 200) != 200:
                logger.warning(
                    "InputValidator HTTP %s for %s",
                    getattr(response, "status", "?"), username,
                )
                return None
            body = response.read().decode("utf-8", errors="ignore")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.warning("InputValidator non-JSON response for %s", username)
            return None

        if not isinstance(data, dict):
            logger.warning(
                "InputValidator non-dict response for %s: %s",
                username, type(data).__name__,
            )
            return None

        errors = data.get("errors")
        if errors:
            error_messages: List[str] = []
            for e in errors:
                if isinstance(e, dict):
                    error_messages.append(str(e.get("message", "")))
                else:
                    error_messages.append(str(e))
            combined = " ".join(error_messages).lower()
            logger.info("InputValidator errors for %s: %s", username, error_messages)

            if any(marker in combined for marker in _TAKEN_MARKERS):
                return False
            return None

        return True
    except Exception as exc:
        logger.warning(
            "Availability check failed for %s: %s: %s",
            username, type(exc).__name__, exc,
        )
        return None


async def check_email_availability(email: str, use_cache: bool = True) -> Optional[bool]:
    global _LAST_CHECK_TIME
    if use_cache:
        cached = _EMAIL_AVAILABILITY_CACHE.get(email)
        if cached and (time.time() - cached[0]) < 180:
            return cached[1]
    elapsed = time.time() - _LAST_CHECK_TIME
    if elapsed < _CHECK_MIN_INTERVAL:
        await asyncio.sleep(_CHECK_MIN_INTERVAL - elapsed)
    _LAST_CHECK_TIME = time.time()
    username = email.split("@")[0]
    result = await asyncio.to_thread(check_email_availability_sync, username)
    if result is not None:
        _EMAIL_AVAILABILITY_CACHE[email] = (time.time(), result)
    return result


# ==================== ABSTRACTAPI CHECKER ====================
def _call_abstractapi_key(api_key: str, email: str) -> Optional[bool]:
    meta = _call_abstractapi_with_meta(api_key, email)
    return meta.get("result")


def _call_abstractapi_with_meta(api_key: str, email: str) -> dict:
    meta = {
        "result": None,
        "limit_header": None,
        "remaining_header": None,
        "quota_exceeded": False,
        "invalid_key": False,
        "error": None,
    }
    try:
        url = "https://emailreputation.abstractapi.com/v1/"
        params = urllib.parse.urlencode({"api_key": api_key, "email": email})
        req = Request(f"{url}?{params}", headers={"Accept": "application/json"})
        with urlopen(req, timeout=10) as response:
            headers = dict(response.headers)
            body = response.read().decode("utf-8", errors="ignore")
        data = json.loads(body)

        err = data.get("error")
        if err:
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            meta["error"] = msg[:150]
            low = msg.lower()
            if "quota" in low or "exceeded" in low or "limit reached" in low or "rate limit" in low:
                meta["quota_exceeded"] = True
            if "invalid api key" in low or "unauthorized" in low or "authentication" in low:
                meta["invalid_key"] = True
            return meta

        for name, value in headers.items():
            lower = name.lower()
            if "ratelimit-limit" in lower or "rate-limit-limit" in lower:
                try:
                    meta["limit_header"] = int(value)
                except (ValueError, TypeError):
                    pass
            elif "ratelimit-remaining" in lower or "rate-limit-remaining" in lower:
                try:
                    meta["remaining_header"] = int(value)
                except (ValueError, TypeError):
                    pass

        if meta["limit_header"] is None:
            quota = data.get("quota") or data.get("rate_limit") or {}
            if isinstance(quota, dict):
                if quota.get("limit") and quota.get("remaining") is not None:
                    try:
                        meta["limit_header"] = int(quota["limit"])
                        meta["remaining_header"] = int(quota["remaining"])
                    except (ValueError, TypeError):
                        pass

        is_valid = data.get("is_valid")
        deliverability = str((data.get("email_deliverability") or {}).get("status", "")).lower()
        is_smtp_valid = (data.get("is_smtp_valid") or {}).get("value")
        is_mx_found = (data.get("is_mx_found") or {}).get("value")
        if is_valid is True or deliverability in ("deliverable", "risky"):
            meta["result"] = True
        elif is_smtp_valid is True and is_mx_found is True:
            meta["result"] = True
        elif is_valid is False or deliverability in ("undeliverable", "invalid"):
            meta["result"] = False

        return meta
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            meta["quota_exceeded"] = True
        if exc.code in (401, 403):
            meta["invalid_key"] = True
        try:
            body = exc.read().decode("utf-8", errors="ignore")
            meta["error"] = f"HTTP {exc.code}: {body[:100]}"
        except Exception:
            meta["error"] = f"HTTP {exc.code}"
        return meta
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}"
        return meta


def check_email_exists_via_abstractapi_sync(email: str) -> Optional[bool]:
    api_key = os.environ.get("ABSTRACT_API_KEY", "").strip()
    if not api_key:
        logger.warning("⚠️ ABSTRACT_API_KEY is missing in environment variables!")
        return None
    return _call_abstractapi_key(api_key, email)


# ==================== API ROTATION SYSTEM ====================
def get_api_list() -> list:
    config = load_config()
    apis = config.get("email_verification_apis", [])
    return apis if isinstance(apis, list) else []


def save_api_list(apis: list):
    config = load_config()
    config["email_verification_apis"] = apis
    save_config(config)


def get_active_api() -> Optional[dict]:
    for api in get_api_list():
        if api.get("status") == "active":
            if int(api.get("used", 0)) < int(api.get("limit", 0)):
                return api
    return None


def mark_api_invalid(api_id: str):
    apis = get_api_list()
    for api in apis:
        if api.get("id") == api_id:
            api["status"] = "invalid"
            break
    save_api_list(apis)


def mark_api_exhausted(api_id: str):
    apis = get_api_list()
    for api in apis:
        if api.get("id") == api_id:
            api["status"] = "exhausted"
            api["used"] = int(api.get("limit", 0))
            break
    save_api_list(apis)


def count_admin_api_additions_today(admin_id: int) -> int:
    config = load_config()
    log = config.get("api_additions_log", {}) or {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return sum(1 for d in log.get(str(admin_id), []) if d == today)


def record_admin_api_addition(admin_id: int):
    config = load_config()
    log = config.setdefault("api_additions_log", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.setdefault(str(admin_id), []).append(today)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    for uid in list(log.keys()):
        log[uid] = [d for d in log[uid] if d >= cutoff]
    save_config(config)


def test_api_key_sync(api_key: str) -> dict:
    meta = _call_abstractapi_with_meta(api_key, "test@gmail.com")

    if meta.get("invalid_key"):
        return {"ok": False, "message": "مفتاح غير صالح أو منتهي الصلاحية",
                "limit": 0, "used": 0, "status": "invalid"}

    if meta.get("quota_exceeded"):
        return {"ok": True,
                "message": "⚠️ المفتاح صحيح لكن الحصة منتهية (429) — سيُضاف بحالة مستنفد",
                "limit": 100, "used": 100, "status": "exhausted"}

    if meta.get("error"):
        return {"ok": False, "message": f"خطأ: {meta['error'][:150]}",
                "limit": 0, "used": 0, "status": "invalid"}

    if meta["result"] is None:
        return {"ok": False,
                "message": "رد غير متوقع من المزوّد (لا يحتوي على بيانات صالحة)",
                "limit": 0, "used": 0, "status": "invalid"}

    if meta.get("limit_header") and meta["limit_header"] > 0:
        limit = int(meta["limit_header"])
        used = 1
        if meta.get("remaining_header") is not None:
            used = max(0, limit - int(meta["remaining_header"]))
        return {"ok": True,
                "message": f"✅ تم اكتشاف الحد تلقائياً من المزوّد: {limit}",
                "limit": limit, "used": used, "status": "active"}

    return {"ok": True,
            "message": "✅ تم التحقق (الحد الافتراضي: 100 — سيُصحّح عند أول استعمال)",
            "limit": 100, "used": 1, "status": "active"}


def _compute_api_warning() -> Optional[dict]:
    active = [a for a in get_api_list() if a.get("status") == "active"]
    if len(active) == 0:
        return {"warning": True, "remaining": 0, "api": None}
    if len(active) == 1:
        remaining = int(active[0].get("limit", 0)) - int(active[0].get("used", 0))
        if remaining <= API_WARNING_THRESHOLD:
            return {"warning": True, "remaining": max(0, remaining), "api": active[0]}
    return None


def _update_api_from_meta(api_id: str, meta: dict) -> Optional[dict]:
    apis = get_api_list()
    now_iso = datetime.now(timezone.utc).isoformat()
    for api in apis:
        if api.get("id") == api_id:
            if meta.get("limit_header") and meta["limit_header"] > 0:
                api["limit"] = int(meta["limit_header"])
            if meta.get("remaining_header") is not None and api.get("limit"):
                limit = int(api["limit"])
                remaining = int(meta["remaining_header"])
                api["used"] = max(0, limit - remaining)
            else:
                api["used"] = int(api.get("used", 0)) + 1
            api["last_used_at"] = now_iso
            if int(api.get("used", 0)) >= int(api.get("limit", 0)):
                api["status"] = "exhausted"
            break
    save_api_list(apis)
    return _compute_api_warning()


async def notify_api_warning(context, warning: dict):
    if not warning or not warning.get("warning"):
        return
    remaining = int(warning.get("remaining", 0))
    if warning.get("api"):
        text = (f"⚠️ *تنبيه: APIs على وشك الانتهاء*\n\n"
                f"📊 متبقي في آخر API: *{remaining}* استعمال\n\n"
                f"📌 يرجى إضافة API جديد من لوحة المالك/الأدمن.")
    else:
        text = ("🚨 *تنبيه عاجل: لا توجد APIs فعّالة!*\n\n"
                "تم استهلاك جميع APIs. الفحص التلقائي للإيميلات معطّل حالياً.\n\n"
                "📌 يرجى إضافة API جديد فوراً من لوحة المالك/الأدمن.")
    targets = {OWNER_ID}
    try:
        targets.update(load_admins())
    except Exception:
        pass
    for target in targets:
        try:
            await context.bot.send_message(chat_id=target, text=text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass


async def check_email_via_rotation_api(email: str, context=None) -> Optional[bool]:
    tried_ids = set()
    for _ in range(5):
        api = None
        for candidate in get_api_list():
            if candidate.get("status") != "active":
                continue
            if int(candidate.get("used", 0)) >= int(candidate.get("limit", 0)):
                continue
            if candidate.get("id") in tried_ids:
                continue
            api = candidate
            break
        if api is None:
            return None
        tried_ids.add(api.get("id"))

        meta = await asyncio.to_thread(
            _call_abstractapi_with_meta, api.get("api_key", ""), email
        )

        if meta.get("invalid_key"):
            mark_api_invalid(api.get("id"))
            continue

        if meta.get("quota_exceeded"):
            mark_api_exhausted(api.get("id"))
            warning = _compute_api_warning()
            if warning and context:
                try:
                    await notify_api_warning(context, warning)
                except Exception:
                    logger.exception("Failed to notify API warning")
            continue

        warning = _update_api_from_meta(api.get("id"), meta)
        if warning and context:
            try:
                await notify_api_warning(context, warning)
            except Exception:
                logger.exception("Failed to notify API warning")

        if meta["result"] is not None:
            return meta["result"]
    return None


# ==================== STRICT EMAIL EXISTENCE VERIFICATION ====================
async def verify_gmail_account_exists_strict(email: str, context=None) -> Tuple[str, Optional[bool]]:
    """
    التحقق الصارم من وجود حساب Gmail.
    يُستخدم فقط في تدفق 'بيع إيميل جديد'.
    """
    try:
        result_api = await check_email_via_rotation_api(email, context=context)
    except Exception:
        logger.exception("Rotation API check failed")
        result_api = None
    if result_api is True:
        return "exists", True
    if result_api is False:
        return "available", False

    env_key = os.environ.get("ABSTRACT_API_KEY", "").strip()
    if env_key:
        result_env = await asyncio.to_thread(_call_abstractapi_key, env_key, email)
        if result_env is True:
            return "exists", True
        if result_env is False:
            return "available", False

    last_result: Optional[bool] = None
    for attempt in range(3):
        try:
            result = await check_email_availability(email, use_cache=False)
        except Exception:
            logger.exception("check_email_availability raised unexpectedly")
            result = None
        last_result = result
        if result is True:
            return "available", False
        if result is False:
            return "exists", True
        if attempt < 2:
            await asyncio.sleep(2 + attempt)

    logger.warning(
        "Email existence verification unknown for %s after all attempts (last=%s)",
        email, last_result,
    )
    return "unknown", None


# ==================== WITHDRAW LIMITS ====================
def get_withdraw_limits() -> dict:
    config = load_config()
    min_val = config.get("withdraw_min")
    max_val = config.get("withdraw_max")
    return {
        "min": float(min_val) if min_val is not None else None,
        "max": float(max_val) if max_val is not None else None,
    }


# ==================== DAILY GIFT ====================
DAILY_GIFT_LOCK = asyncio.Lock()


def get_iraq_date_str() -> str:
    iraq_now = datetime.now(timezone.utc) + timedelta(hours=IRAQ_TZ_OFFSET_HOURS)
    return iraq_now.strftime("%Y-%m-%d")


def get_daily_gift_config() -> dict:
    config = load_config()
    gift = config.get("daily_gift", {})
    if not isinstance(gift, dict):
        return {"enabled": False, "emails_required": 0, "reward": 0.0}
    return {
        "enabled": bool(gift.get("enabled", False)),
        "emails_required": int(gift.get("emails_required", 0) or 0),
        "reward": float(gift.get("reward", 0) or 0),
    }


def check_and_reset_daily_gift_progress(user_data: dict) -> dict:
    today = get_iraq_date_str()
    progress = user_data.get("daily_gift_progress") or {}
    if not isinstance(progress, dict):
        progress = {}
    if progress.get("date") != today:
        progress = {"date": today, "count": 0, "claimed": False}
        user_data["daily_gift_progress"] = progress
    return progress


async def process_daily_gift_after_approval(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """يُستدعى عند تحرير نقاط إيميل للعضو. يحسب التقدم ويصرف الهدية عند الوصول للهدف."""
    gift = get_daily_gift_config()
    if not gift["enabled"] or gift["emails_required"] <= 0 or gift["reward"] <= 0:
        return
    async with DAILY_GIFT_LOCK:
        user_data = get_user(user_id)
        progress = check_and_reset_daily_gift_progress(user_data)
        if progress.get("claimed"):
            save_user(user_id, user_data)
            return
        progress["count"] = int(progress.get("count", 0)) + 1
        awarded = False
        if progress["count"] >= gift["emails_required"]:
            user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) + gift["reward"])
            user_data["total_credited_balance"] = clamp_money(
                float(user_data.get("total_credited_balance", 0.0)) + gift["reward"])
            add_transaction(user_data, "credit", gift["reward"],
                            f"هدية يومية ({gift['emails_required']} إيميلات)")
            progress["claimed"] = True
            awarded = True
        user_data["daily_gift_progress"] = progress
        save_user(user_id, user_data)

    if awarded:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(f"🎁 <b>مبروك! حصلت على الهدية اليومية</b>\n\n"
                      f"📧 أكملت <b>{gift['emails_required']}</b> إيميلات اليوم\n"
                      f"💰 مكافأة: <b>${gift['reward']:.2f}</b>\n\n"
                      f"🔄 العداد يتجدد تلقائياً عند منتصف الليل بتوقيت العراق."),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=(f"🎁 <b>هدية يومية صُرفت</b>\n\n"
                      f"👤 المستخدم: <code>{user_id}</code>\n"
                      f"📧 الإيميلات: <code>{gift['emails_required']}</code>\n"
                      f"💰 المكافأة: <code>${gift['reward']:.2f}</code>"),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass


# ==================== REJECT REASONS ====================
REJECT_REASON_KEYS = ("email", "password", "totp", "app_pass", "phone", "other")

REJECT_REASON_ICONS = {
    "email": "📧", "password": "🔑", "totp": "🔐", "app_pass": "🗝",
    "phone": "📱", "other": "📝", "custom": "📝", "unknown": "❌",
    "imap_failed_24h": "🔴",
}

REJECT_REASON_LABELS = {
    "email": "إيميل خطأ", "password": "باسورد خطأ", "totp": "رمز مصادقة خطأ",
    "app_pass": "كلمة مرور تطبيق خطأ", "phone": "يحتاج رقم هاتف",
    "other": "خطأ آخر", "custom": "سبب مخصص", "unknown": "غير معروف",
    "owner_manual": "رفض يدوي من المالك",
    "imap_failed_24h": "فشل الفحص الثاني (24 ساعة)",
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
    "email": "video_email", "password": "video_password", "totp": "video_totp",
    "app_pass": "video_app_pass", "phone": "video_phone",
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


def normalize_support_link(value: Any) -> str:
    link = str(value or "").strip()
    if not link or len(link) > 500 or any(character.isspace() for character in link):
        return ""
    try:
        parsed = urlsplit(link)
    except ValueError:
        return ""
    if (parsed.scheme.lower() != "https" or not parsed.hostname
            or parsed.username or parsed.password):
        return ""
    return link


def maintenance_is_enabled() -> bool:
    return bool(load_config().get("maintenance_mode", False))


def maintenance_toggle_button():
    if maintenance_is_enabled():
        return ("🛑 إيقاف الصيانة", "maintenance_toggle")
    return ("🛠️ تشغيل الصيانة", "maintenance_toggle")


async def maintenance_access_allowed(update: Update) -> bool:
    user = update.effective_user
    if not maintenance_is_enabled() or (user and user.id == OWNER_ID):
        return True
    notice = "🛠️ البوت تحت الصيانة حالياً. يرجى المحاولة لاحقاً."
    if update.callback_query:
        await update.callback_query.answer(notice, show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(notice)
    return False


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
    "approved_accounts": [], "pending_requests": [], "rejected_emails": [],
    "rejected_requests": [], "referral_code": "", "referred_by": None,
    "referral_earnings": 0.0, "total_referrals": 0, "total_approved_emails": 0,
    "pending_purchases": [], "used_app_passwords": [], "transactions": [],
    "contest_wins": [], "consecutive_rejections": 0, "ban_until": "",
    "ban_level": 0, "total_rejections": 0,
    "language": "ar",
    "created_at": "",
    "last_sold_at": "",
    "last_reminder_at": "",
    "verification_failures": 0,
    "restricted_until": "",
    "daily_gift_progress": {},
    "sell_new_sessions": [],  # ✅ جلسات بيع إيميل جديد محفوظة (للاستئناف)
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
    if not merged.get("created_at"):
        merged["created_at"] = datetime.now(timezone.utc).isoformat()
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
        "kind": kind, "amount": round(float(amount), 2),
        "note": note[:200], "email": email,
        "at": datetime.now(timezone.utc).isoformat(),
    })


# ==================== SELL-NEW SESSIONS PERSISTENCE ====================
def get_user_sell_sessions(user_id: int) -> list:
    """يعيد جلسات بيع الإيميل الجديد المحفوظة للعضو."""
    user_data = get_user(user_id)
    sessions = user_data.get("sell_new_sessions", [])
    return sessions if isinstance(sessions, list) else []


def _purge_old_sell_sessions(sessions: list) -> list:
    """يحذف الجلسات الأقدم من SELL_NEW_SESSION_MAX_AGE_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=SELL_NEW_SESSION_MAX_AGE_DAYS)
    kept = []
    for s in sessions:
        try:
            created = datetime.fromisoformat(s.get("created_at", ""))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created >= cutoff:
                kept.append(s)
        except (ValueError, TypeError):
            kept.append(s)
    return kept


def upsert_sell_session(user_id: int, session: dict) -> None:
    """يضيف/يحدّث جلسة بيع إيميل جديد ويحفظها بشكل دائم."""
    user_data = get_user(user_id)
    sessions = user_data.get("sell_new_sessions", [])
    if not isinstance(sessions, list):
        sessions = []
    sessions = [s for s in sessions if s.get("email") != session.get("email")]
    sessions.append(session)
    sessions = _purge_old_sell_sessions(sessions)
    user_data["sell_new_sessions"] = sessions
    save_user(user_id, user_data)


def update_sell_session(user_id: int, email: str, **fields) -> None:
    """تحديث حقول جلسة محددة."""
    user_data = get_user(user_id)
    sessions = user_data.get("sell_new_sessions", [])
    if not isinstance(sessions, list):
        return
    updated = False
    for s in sessions:
        if s.get("email") == email:
            s.update(fields)
            updated = True
            break
    if updated:
        user_data["sell_new_sessions"] = sessions
        save_user(user_id, user_data)


def remove_sell_session(user_id: int, email: str) -> None:
    """يحذف جلسة بيع إيميل جديد (بعد الإكمال أو الإلغاء)."""
    user_data = get_user(user_id)
    sessions = user_data.get("sell_new_sessions", [])
    if not isinstance(sessions, list):
        return
    new_sessions = [s for s in sessions if s.get("email") != email]
    if len(new_sessions) != len(sessions):
        user_data["sell_new_sessions"] = new_sessions
        save_user(user_id, user_data)


def get_sell_session(user_id: int, email: str) -> Optional[dict]:
    for s in get_user_sell_sessions(user_id):
        if s.get("email") == email:
            return s
    return None


def get_pending_sell_sessions(user_id: int) -> list:
    """يعيد فقط الجلسات غير المكتملة (التي لم تُسلَّم بعد)."""
    return [s for s in get_user_sell_sessions(user_id)
            if s.get("status") in ("pending_creation", "created_verified", "totp_ready")]


# ==================== ADMIN BONUS SETTLEMENT ====================
def settle_admin_bonus(account: dict, new_status: str,
                       owner_data: Optional[dict] = None,
                       owner_id: Optional[int] = None) -> bool:
    """
    Settle the admin bonus tied to an approved account.

    new_status: "released" or "rejected"
    - "released": moves the bonus from admin_pending_balance → admin_received_balance
    - "rejected": removes the bonus from admin_pending_balance

    If `admin_id == owner_id` and `owner_data` is provided, the updates are applied
    to `owner_data` in memory (NOT saved here). Otherwise, the admin's data is loaded,
    updated, and saved automatically.
    """
    if not isinstance(account, dict) or new_status not in ("released", "rejected"):
        return False
    admin_id_raw = account.get("completed_by_admin")
    if admin_id_raw is None:
        return False
    try:
        admin_id = int(admin_id_raw)
    except (TypeError, ValueError):
        return False
    if admin_id == OWNER_ID:
        return False
    bonus_cents = money_to_cents(account.get("admin_bonus", 0.0))
    if bonus_cents <= 0:
        return False
    current_status = str(account.get("admin_bonus_status") or "")
    if current_status not in ("pending", ""):
        return False

    same_user = owner_id is not None and admin_id == owner_id and owner_data is not None
    admin_data = owner_data if same_user else get_user(admin_id)

    pending_cents = max(0, money_to_cents(admin_data.get("admin_pending_balance", 0.0)) - bonus_cents)
    admin_data["admin_pending_balance"] = cents_to_money(pending_cents)

    email = account.get("email", "")
    if new_status == "released":
        received_cents = money_to_cents(admin_data.get("admin_received_balance", 0.0)) + bonus_cents
        admin_data["admin_received_balance"] = cents_to_money(received_cents)
        add_transaction(admin_data, "admin_bonus_release", cents_to_money(bonus_cents),
                        f"Released admin bonus for {email}", email)
    else:
        add_transaction(admin_data, "admin_bonus_reversal", cents_to_money(bonus_cents),
                        f"Reversed admin bonus for {email}", email)

    if not same_user:
        save_user(admin_id, admin_data)
    return True


# ==================== LANGUAGE HELPERS ====================
SUPPORTED_LANGUAGES = ("ar", "en", "ru")


def get_user_language(user_id: int) -> str:
    user_data = get_user(user_id)
    lang = str(user_data.get("language", "ar") or "ar").lower()
    if lang not in SUPPORTED_LANGUAGES:
        return "ar"
    return lang


def set_user_language(user_id: int, lang: str):
    if lang not in SUPPORTED_LANGUAGES:
        return
    user_data = get_user(user_id)
    user_data["language"] = lang
    save_user(user_id, user_data)


def t(user_id: int, key: str, **kwargs) -> str:
    from telegram_bot.locales import TRANSLATIONS
    lang = get_user_language(user_id)
    text = (
        TRANSLATIONS.get(lang, {}).get(key)
        or TRANSLATIONS.get("ar", {}).get(key)
        or key
    )
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError):
        return text


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
    pending = user_data.get("pending_requests", []) or []
    if pending:
        total_pending = 0.0
        for req in pending:
            try:
                total_pending += float(req.get("amount", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
        user_data["pending_balance"] = clamp_money(
            float(user_data.get("pending_balance", 0.0)) - total_pending)
    user_data["pending_requests"] = []
    duration_text = "يوم واحد" if ban_level == 1 else "أسبوع"
    return ban_level, duration_text


def register_approval_reset(user_data: dict):
    user_data["consecutive_rejections"] = 0
    user_data["verification_failures"] = 0


# ==================== RESTRICTION SYSTEM ====================
ARABIC_PATTERN = re.compile(
    r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]'
)


def contains_arabic(text: str) -> bool:
    return bool(ARABIC_PATTERN.search(text or ""))


def get_active_restriction_message(user_data: dict) -> Optional[str]:
    until_str = str(user_data.get("restricted_until", "") or "").strip()
    if not until_str:
        return None
    try:
        until = datetime.fromisoformat(until_str)
    except ValueError:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if now >= until:
        return None
    remaining_seconds = int((until - now).total_seconds())
    minutes = remaining_seconds // 60
    seconds = remaining_seconds % 60
    return (f"🚫 <b>أنت مقيد مؤقتاً</b>\n"
            f"⏳ الوقت المتبقي: <b>{minutes} دقيقة و {seconds} ثانية</b>\n"
            f"<i>السبب: 3 محاولات فاشلة لإرسال إيميل غير صحيح.</i>\n"
            f"<i>لا يمكنك إضافة إيميلات أو سحب الأرباح خلال هذه الفترة.</i>")


def get_any_restriction_message(user_data: dict) -> Optional[str]:
    ban_msg = get_active_ban_message(user_data)
    if ban_msg:
        return ban_msg
    return get_active_restriction_message(user_data)


def apply_verification_failure(user_data: dict) -> Tuple[int, bool]:
    failures = int(user_data.get("verification_failures", 0) or 0) + 1
    if failures >= VERIFICATION_FAILURES_THRESHOLD:
        user_data["restricted_until"] = (
            datetime.now(timezone.utc) + timedelta(hours=RESTRICTION_DURATION_HOURS)
        ).isoformat()
        user_data["verification_failures"] = 0
        return 0, True
    user_data["verification_failures"] = failures
    return VERIFICATION_FAILURES_THRESHOLD - failures, False


def mark_user_sold(user_data: dict):
    user_data["last_sold_at"] = datetime.now(timezone.utc).isoformat()


# ==================== BAN / RESTRICTION MANAGEMENT ====================
def get_banned_users() -> list:
    users = load_json(USERS_DB)
    rows = []
    now = datetime.now(timezone.utc)
    for raw_uid, enc in users.items():
        try:
            uid = int(raw_uid)
        except (TypeError, ValueError):
            continue
        ud = decrypt_user_data(enc)
        info = {"user_id": uid, "kind": None, "until": None, "name": "", "username": ""}
        ban_str = str(ud.get("ban_until", "") or "").strip()
        if ban_str:
            try:
                dt = datetime.fromisoformat(ban_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > now:
                    info["kind"] = "ban"
                    info["until"] = dt.isoformat()
            except ValueError:
                pass
        if info["kind"] is None:
            rest_str = str(ud.get("restricted_until", "") or "").strip()
            if rest_str:
                try:
                    dt = datetime.fromisoformat(rest_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt > now:
                        info["kind"] = "restriction"
                        info["until"] = dt.isoformat()
                except ValueError:
                    pass
        if info["kind"] is None:
            continue
        for coll in ("approved_accounts", "pending_requests", "rejected_requests"):
            for rec in ud.get(coll, []) or []:
                if rec.get("user_name") and not info["name"]:
                    info["name"] = rec.get("user_name")
                if rec.get("user_username") and not info["username"]:
                    info["username"] = rec.get("user_username")
                if info["name"] and info["username"]:
                    break
            if info["name"] and info["username"]:
                break
        rows.append(info)
    return rows


def clear_user_restriction(user_id: int) -> bool:
    user_data = get_user(user_id)
    changed = False
    if user_data.get("ban_until"):
        user_data["ban_until"] = ""
        user_data["ban_level"] = 0
        user_data["consecutive_rejections"] = 0
        changed = True
    if user_data.get("restricted_until"):
        user_data["restricted_until"] = ""
        user_data["verification_failures"] = 0
        changed = True
    if changed:
        save_user(user_id, user_data)
    return changed


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
TG_LION_API_URL = "https://TG-Lion.net/"
TG_LION_COUNTRY_CACHE = {"countries": None, "expires_at": 0.0}
TG_LION_COUNTRY_CACHE_TTL = 60
TG_LION_ORDER_LOCK = asyncio.Lock()
_IMAP_RATE: Dict[str, float] = {}

_TG_LION_CALLING_CODES = {
    "AD": "376", "AE": "971", "AF": "93", "AG": "1268", "AI": "1264",
    "AL": "355", "AM": "374", "AO": "244", "AR": "54", "AS": "1684",
    "AT": "43", "AU": "61", "AW": "297", "AX": "358", "AZ": "994",
    "BA": "387", "BB": "1246", "BD": "880", "BE": "32", "BF": "226",
    "BG": "359", "BH": "973", "BI": "257", "BJ": "229", "BL": "590",
    "BM": "1441", "BN": "673", "BO": "591", "BQ": "599", "BR": "55",
    "BS": "1242", "BT": "975", "BW": "267", "BY": "375", "BZ": "501",
    "CA": "1", "CC": "61", "CD": "243", "CF": "236", "CG": "242",
    "CH": "41", "CI": "225", "CK": "682", "CL": "56", "CM": "237",
    "CN": "86", "CO": "57", "CR": "506", "CU": "53", "CV": "238",
    "CW": "599", "CX": "61", "CY": "357", "CZ": "420", "DE": "49",
    "DJ": "253", "DK": "45", "DM": "1767", "DO": "1809", "DZ": "213",
    "EC": "593", "EE": "372", "EG": "20", "ER": "291", "ES": "34",
    "ET": "251", "FI": "358", "FJ": "679", "FK": "500", "FM": "691",
    "FO": "298", "FR": "33", "GA": "241", "GB": "44", "GD": "1473",
    "GE": "995", "GF": "594", "GG": "44", "GH": "233", "GI": "350",
    "GL": "299", "GM": "220", "GN": "224", "GP": "590", "GQ": "240",
    "GR": "30", "GT": "502", "GU": "1671", "GW": "245", "GY": "592",
    "HK": "852", "HN": "504", "HR": "385", "HT": "509", "HU": "36",
    "ID": "62", "IE": "353", "IL": "972", "IM": "44", "IN": "91",
    "IO": "246", "IQ": "964", "IR": "98", "IS": "354", "IT": "39",
    "JE": "44", "JM": "1876", "JO": "962", "JP": "81", "KE": "254",
    "KG": "996", "KH": "855", "KI": "686", "KM": "269", "KN": "1869",
    "KP": "850", "KR": "82", "KW": "965", "KY": "1345", "KZ": "7",
    "LA": "856", "LB": "961", "LC": "1758", "LI": "423", "LK": "94",
    "LR": "231", "LS": "266", "LT": "370", "LU": "352", "LV": "371",
    "LY": "218", "MA": "212", "MC": "377", "MD": "373", "ME": "382",
    "MF": "590", "MG": "261", "MH": "692", "MK": "389", "ML": "223",
    "MM": "95", "MN": "976", "MO": "853", "MP": "1670", "MQ": "596",
    "MR": "222", "MS": "1664", "MT": "356", "MU": "230", "MV": "960",
    "MW": "265", "MX": "52", "MY": "60", "MZ": "258", "NA": "264",
    "NC": "687", "NE": "227", "NF": "672", "NG": "234", "NI": "505",
    "NL": "31", "NO": "47", "NP": "977", "NR": "674", "NU": "683",
    "NZ": "64", "OM": "968", "PA": "507", "PE": "51", "PF": "689",
    "PG": "675", "PH": "63", "PK": "92", "PL": "48", "PM": "508",
    "PR": "1787", "PS": "970", "PT": "351", "PW": "680", "PY": "595",
    "QA": "974", "RE": "262", "RO": "40", "RS": "381", "RU": "7",
    "RW": "250", "SA": "966", "SB": "677", "SC": "248", "SD": "249",
    "SE": "46", "SG": "65", "SH": "290", "SI": "386", "SK": "421",
    "SL": "232", "SM": "378", "SN": "221", "SO": "252", "SR": "597",
    "SS": "211", "ST": "239", "SV": "503", "SX": "1721", "SY": "963",
    "SZ": "268", "TC": "1649", "TD": "235", "TG": "228", "TH": "66",
    "TJ": "992", "TK": "690", "TL": "670", "TM": "993", "TN": "216",
    "TO": "676", "TR": "90", "TT": "1868", "TV": "688", "TW": "886",
    "TZ": "255", "UA": "380", "UG": "256", "US": "1", "UY": "598",
    "UZ": "998", "VA": "379", "VC": "1784", "VE": "58", "VG": "1284",
    "VI": "1340", "VN": "84", "VU": "678", "WF": "681", "WS": "685",
    "YE": "967", "YT": "262", "ZA": "27", "ZM": "260", "ZW": "263",
}


def tg_lion_country_flag(country_code: str) -> str:
    code = str(country_code or "").upper()
    if len(code) != 2 or not all("A" <= letter <= "Z" for letter in code):
        return "🌐"
    return "".join(chr(ord(letter) + 127397) for letter in code)


def normalize_tg_lion_calling_code(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[2:] if digits.startswith("00") else digits


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
def kb_vertical(buttons: List[Union[tuple, list, InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    rows = []
    pending_button = None
    for button in buttons:
        if isinstance(button, tuple) and len(button) == 2:
            inline_button = InlineKeyboardButton(button[0], callback_data=button[1])
        elif isinstance(button, InlineKeyboardButton):
            inline_button = button
        elif isinstance(button, list):
            if pending_button is not None:
                rows.append([pending_button])
                pending_button = None
            rows.append([InlineKeyboardButton(btn[0], callback_data=btn[1]) for btn in button])
            continue
        else:
            continue
        if pending_button is None:
            pending_button = inline_button
        else:
            rows.append([pending_button, inline_button])
            pending_button = None
    if pending_button is not None:
        rows.append([pending_button])
    return InlineKeyboardMarkup(rows)


def kb_single(button_text: str, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(button_text, callback_data=callback_data)]])


def member_menu_keyboard(buttons: List[Union[tuple, list]]) -> InlineKeyboardMarkup:
    support_link = normalize_support_link(load_config().get("support_link"))
    if support_link:
        support_button = InlineKeyboardButton("📞 تواصل مع الدعم", url=support_link)
    else:
        support_button = InlineKeyboardButton("📞 تواصل مع الدعم", callback_data="support_link_unavailable")
    return kb_vertical(list(buttons) + [support_button])


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
def get_tier_prices(account_type: str = "old") -> dict:
    config = load_config()
    prefix = f"{account_type}_tier_"
    return {
        "tier_1": float(config.get(f"{prefix}1_price", 0.10 if account_type == "old" else 0.30)),
        "tier_2": float(config.get(f"{prefix}2_price", 0.15 if account_type == "old" else 0.35)),
        "tier_3": float(config.get(f"{prefix}3_price", 0.20 if account_type == "old" else 0.40)),
    }


def calculate_account_price(account_type: str, totp_submitted: bool, app_pass_submitted: bool) -> float:
    prices = get_tier_prices(account_type)
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


def move_approved_account_to_rejected(user_data: dict, account: dict, reason: str, reason_text: str = ""):
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
    for user_data in users.values():
        for rejected in user_data.get("rejected_requests", []):
            if normalize_email(dec(rejected.get("email", ""))) == normalized_email:
                return "rejected"
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
    "gmail.com": ("imap.gmail.com", 993), "googlemail.com": ("imap.gmail.com", 993),
    "outlook.com": ("outlook.office365.com", 993), "outlook.sa": ("outlook.office365.com", 993),
    "hotmail.com": ("outlook.office365.com", 993), "hotmail.sa": ("outlook.office365.com", 993),
    "live.com": ("outlook.office365.com", 993), "msn.com": ("outlook.office365.com", 993),
    "yahoo.com": ("imap.mail.yahoo.com", 993), "ymail.com": ("imap.mail.yahoo.com", 993),
    "icloud.com": ("imap.mail.me.com", 993), "me.com": ("imap.mail.me.com", 993),
    "mac.com": ("imap.mail.me.com", 993), "aol.com": ("imap.aol.com", 993),
    "zoho.com": ("imap.zoho.com", 993), "yandex.com": ("imap.yandex.com", 993),
    "yandex.ru": ("imap.yandex.com", 993), "mail.ru": ("imap.mail.ru", 993),
    "gmx.com": ("imap.gmx.com", 993), "gmx.net": ("imap.gmx.net", 993),
}


def get_imap_host(email: str) -> Tuple[str, int]:
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    if domain in IMAP_PROVIDERS:
        return IMAP_PROVIDERS[domain]
    if domain:
        return (f"imap.{domain}", 993)
    return ("", 0)


def is_valid_email_domain(email: str) -> bool:
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].strip().lower()
    if not domain:
        return False
    host, port = get_imap_host(email)
    if not host:
        return False
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return True
    except socket.gaierror:
        try:
            socket.getaddrinfo(domain, 25, type=socket.SOCK_STREAM)
            return True
        except socket.gaierror:
            return False
    except Exception:
        return True


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
                       f"بعد {elapsed()}. قد يكون الخروج من Railway محجوباً أو لا يوجد رد من الخادم. {exc}")
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
            return False, prefix + f"رد Gmail العام: {err}. لا يحدد IMAP هل السبب كلمة المرور أو App Password أو سياسة الحساب."
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
        return False, f"❌ خطأ غير متوقع بعد نجاح فحص الشبكة ({elapsed()}). راجع سجل الخدمة."


NETWORK_ERROR_MARKERS = ("timeout", "dns", "ssl", "connect", "unreachable", "refused", "تعذّر الاتصال", "انتهت مهلة")
AUTH_ERROR_MARKERS = ("invalid credentials", "authenticationfailed", "username and password not accepted", "بيانات الدخول غير صحيحة")
TWO_FA_ERROR_MARKERS = ("application-specific password", "app password", "two-factor", "2fa", "2-step", "app-specific", "يتطلب كلمة مرور تطبيق")


def classify_imap_error(message: str) -> str:
    low = (message or "").lower()
    if any(m in low for m in TWO_FA_ERROR_MARKERS):
        return "2fa"
    if any(m in low for m in AUTH_ERROR_MARKERS):
        return "auth_or_policy"
    if any(m in low for m in NETWORK_ERROR_MARKERS):
        return "network"
    return "unknown"


async def verify_account_credentials(email: str, password: str = "", app_pass: str = "", totp_secret: str = "") -> dict:
    result = {"level": "failed", "badge": "🔴", "message": "", "imap_ok": False, "totp_ok": False, "category": "unknown"}
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
        result["message"] = "⚪ Gmail لا يسمح بالتحقق من الياسورد العادي عبر IMAP. استخدم App Password أو OAuth2."
    elif imap_pass:
        ok, msg = await asyncio.to_thread(_imap_login_sync, email, imap_pass)
        result["imap_ok"] = ok
        result["message"] = msg
        if not ok:
            result["category"] = classify_imap_error(msg)
        else:
            result["category"] = "ok"
    else:
        result["message"] = "لا توجد بيانات دخول للتحقق منها."
        result["category"] = "no_credentials"

    if result["imap_ok"]:
        result["level"] = "verified"
        result["badge"] = "🟢"
        result["message"] = ("🟢 تم التحقق الكامل عبر IMAP باستخدام كلمة مرور التطبيق." if app_pass else "🟢 تم تسجيل الدخول عبر IMAP بنجاح.")
    elif result["category"] == "2fa":
        result["level"] = "partial" if result["totp_ok"] else "unknown"
        result["badge"] = "🟡" if result["totp_ok"] else "⚪"
        if result["totp_ok"]:
            result["message"] = "🟡 رفض Gmail مصادقة IMAP بطريقة تشير إلى App Password/2FA؛ مفتاح TOTP صالح محليًا."
        else:
            result["message"] = "⚪ رفض Gmail مصادقة IMAP بطريقة تشير إلى App Password/2FA."
    elif result["category"] == "unsupported_auth":
        pass
    elif result["category"] == "network":
        result["level"] = "unknown"
        result["badge"] = "⚪"
    elif result["totp_ok"]:
        result["level"] = "partial"
        result["badge"] = "🟡"
        if not result["message"]:
            result["message"] = "🟡 مفتاح 2FA صالح، لكن لا يمكن التحقق الكامل."
    else:
        result["level"] = "failed"
        result["badge"] = "🔴"
        if not result["message"]:
            result["message"] = "❌ فشل التحقق التلقائي."
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


async def send_totp_code_message(context, chat_id: int, totp_secret: str, email: str = "",
                                  title: str = "🔢 كود المصادقة الحالي", back_callback: str = "",
                                  auto_refresh: bool = False):
    ok, code, err, seconds = generate_totp_code_info(totp_secret)
    if not ok:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ {err}", parse_mode=ParseMode.MARKDOWN)
        return None
    email_line = f"\n📧 `{email}`\n" if email else "\n"
    text = (f"{title}\n{email_line}\n🔢 *الكود:* `{code}`\n\n⏰ صالح لمدة *{seconds}* ثانية\n\n"
            f"_اضغط «🔄 كود جديد» للحصول على كود محدّث._")
    buttons = []
    if auto_refresh and back_callback:
        buttons.append(("🔄 كود جديد", back_callback))
    if back_callback:
        buttons.append(("🔙 رجوع", back_callback))
    reply_markup = kb_vertical(buttons) if buttons else None
    try:
        msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        return msg
    except Exception as exc:
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
    valid_invite_link = not invite_link or bool(re.fullmatch(r"https?://t\.me/(?:\+[A-Za-z0-9_-]+|joinchat/[A-Za-z0-9_-]+)", invite_link, flags=re.IGNORECASE))
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
        if member.status in {"member", "administrator", "creator"} or (member.status == "restricted" and getattr(member, "is_member", False)):
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
    from telegram_bot.members import main_menu
    if await check_forced_channel(update, context):
        await main_menu(update, context)


# ==================== SORT HELPERS ====================
def _record_sort_key(record: dict) -> str:
    return str(record.get("timestamp") or record.get("approval_time") or record.get("rejected_at") or record.get("completed_at") or record.get("confirmed_at") or "")


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
        total = (len(user_data.get("approved_accounts", []) or []) + len(user_data.get("pending_requests", []) or []) + len(user_data.get("rejected_requests", []) or []))
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
    lines.append(f"🏅 <b>الحد الأقصى للفائزين:</b> <code>{max_winners}</code>")
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
    lines.append(f"📨 <b>عدد الإيميلات الواصلة:</b> <code>{total_emails_delivered}</code>")
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
        already_won = any(int(w.get("user_id", 0)) == uid and int(w.get("tier_index", -1)) == tier_index for w in winners)
        if already_won:
            continue
        tier_winners_count = sum(1 for w in winners if int(w.get("tier_index", -1)) == tier_index)
        if tier_winners_count >= max_winners:
            continue
        user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) + tier_reward)
        user_data["total_credited_balance"] = clamp_money(float(user_data.get("total_credited_balance", 0.0) or 0.0) + tier_reward)
        add_transaction(user_data, "credit", tier_reward, f"جائزة المسابقة (هدف {tier_emails} إيميل)", "")
        winners.append({"user_id": uid, "tier_index": tier_index, "tier_emails": tier_emails,
                        "reward": tier_reward, "awarded_at": datetime.now(timezone.utc).isoformat()})
        awarded_now.append((tier_emails, tier_reward))
    if not awarded_now:
        return
    wins = user_data.get("contest_wins", []) or []
    for tier_emails, tier_reward in awarded_now:
        wins.append({"contest_started_at": started_at, "awarded_at": datetime.now(timezone.utc).isoformat(),
                     "tier_emails": tier_emails, "reward": tier_reward})
    user_data["contest_wins"] = wins
    save_user(uid, user_data)
    contest["winners"] = winners
    total_now = sum(r for _, r in awarded_now)
    contest["total_points_awarded"] = float(contest.get("total_points_awarded", 0) or 0) + total_now
    save_contest(contest)
    tiers_lines = "\n".join([f"  • عند {e} إيميل → <code>${r:.2f}</code>" for e, r in awarded_now])
    try:
        await context.bot.send_message(chat_id=uid, text=("🎉 <b>مبروك! فزت في المسابقة</b>\n\n"
            f"✅ <b>عدد الإيميلات الحالية:</b> <code>{count}</code>\n"
            f"💰 <b>المكافآت المكتسبة:</b>\n{tiers_lines}\n\n"
            f"💵 <b>الإجمالي الممنوح الآن:</b> <code>${total_now:.2f}</code>\n"
            "تم إضافة المكافأة إلى رصيدك مباشرة."), parse_mode=ParseMode.HTML)
    except Exception:
        pass
    try:
        await context.bot.send_message(chat_id=OWNER_ID, text=("🎉 <b>فائز جديد في المسابقة</b>\n\n"
            f"👤 المستخدم: <code>{uid}</code>\n✅ الإيميلات الحالية: <code>{count}</code>\n"
            f"💵 إجمالي الممنوح: <code>${total_now:.2f}</code>\n"
            f"🎁 مكافآت: <code>{len(awarded_now)}</code>"), parse_mode=ParseMode.HTML)
    except Exception:
        pass


# ==================== AUTO CLEANUP JOB ====================
async def cleanup_fake_requests_job(context: ContextTypes.DEFAULT_TYPE):
    users = load_json(USERS_DB)
    changed = False
    for uid, encrypted_data in users.items():
        ud = decrypt_user_data(encrypted_data)
        pending = ud.get("pending_requests", [])
        new_pending = []
        rejected_any = False
        for req in pending:
            if req.get("has_app_pass"):
                new_pending.append(req)
                continue
            timestamp = req.get("timestamp")
            is_old = False
            if timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - dt).total_seconds() > 12 * 3600:
                        is_old = True
                except Exception:
                    pass
            email = req.get("email", "")
            valid_domain = await asyncio.to_thread(is_valid_email_domain, email)
            if not valid_domain or is_old:
                move_request_to_rejected(ud, req, "other", "رفض تلقائي: إيميل وهمي أو لم يكتمل خلال 12 ساعة")
                rejected_any = True
                changed = True
            else:
                new_pending.append(req)
        if rejected_any:
            ud["pending_requests"] = new_pending
            save_user(int(uid), ud)
    if changed:
        logger.info("Auto-rejected fake or abandoned pending requests.")


# ==================== INACTIVE MEMBER REMINDER ====================
async def inactive_member_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    users = load_json(USERS_DB)
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    for raw_uid, enc in users.items():
        try:
            uid = int(raw_uid)
        except (TypeError, ValueError):
            continue
        ud = decrypt_user_data(enc)
        last_sold_str = ud.get("last_sold_at", "") or ""
        if last_sold_str:
            try:
                dt = datetime.fromisoformat(last_sold_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > week_ago:
                    continue
            except ValueError:
                pass
        else:
            created = ud.get("created_at", "") or ""
            if not created:
                continue
            try:
                dt = datetime.fromisoformat(created)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > week_ago:
                    continue
            except ValueError:
                continue
        last_rem = ud.get("last_reminder_at", "") or ""
        if last_rem:
            try:
                dt = datetime.fromisoformat(last_rem)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > week_ago:
                    continue
            except ValueError:
                pass
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(f"👋 <b>مرحباً!</b>\n\n"
                      f"لاحظنا أنك لم تقم ببيع أي إيميل منذ أسبوع.\n"
                      f"📌 أضف إيميلاً الآن واربح من خلال /start"),
                parse_mode=ParseMode.HTML)
            ud["last_reminder_at"] = now.isoformat()
            save_user(uid, ud)
        except Exception:
            pass


# ==================== DATE / TIME HELPERS ====================
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


# ==================== CRYPTO WITHDRAWAL CONFIGURATION ====================
CRYPTO_WITHDRAWALS: Dict[str, Dict[str, Any]] = {
    "bsc":     {"name": "BSC (BEP20)",  "symbol": "USDT", "min": 5.0, "fee": 0.01, "needs": "wallet",     "emoji": "🟡", "network_label": "BSC / BEP20"},
    "opbnb":   {"name": "opBNB",        "symbol": "USDT", "min": 5.0, "fee": 0.10, "needs": "wallet",     "emoji": "🟠", "network_label": "opBNB"},
    "trx":     {"name": "TRX (TRC20)",  "symbol": "USDT", "min": 5.0, "fee": 1.50, "needs": "wallet",     "emoji": "🔴", "network_label": "TRON / TRC20"},
    "eth":     {"name": "Ethereum",     "symbol": "USDT", "min": 3.0, "fee": 0.60, "needs": "wallet",     "emoji": "🔷", "network_label": "Ethereum / ERC20"},
    "pol":     {"name": "Polygon",      "symbol": "USDT", "min": 3.0, "fee": 0.03, "needs": "wallet",     "emoji": "🟣", "network_label": "Polygon / POL"},
    "ton":     {"name": "TON",          "symbol": "USDT", "min": 3.0, "fee": 0.30, "needs": "wallet",     "emoji": "💎", "network_label": "TON"},
    "binance": {"name": "Binance Pay",  "symbol": "USDT", "min": 0.3, "fee": 0.00, "needs": "binance_id", "emoji": "🟨", "network_label": "Binance Pay ID"},
}


def crypto_menu_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key, info in CRYPTO_WITHDRAWALS.items():
        label = f"{info['emoji']} {info['name']} | حد ${info['min']:.2f} | عمولة ${info['fee']:.2f}"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"crypto_pick:{key}")])
    rows.append([InlineKeyboardButton("🔙 قسم السحب", callback_data="withdraw_store")])
    return InlineKeyboardMarkup(rows)


__all__ = [name for name in globals() if not name.startswith("__")]
