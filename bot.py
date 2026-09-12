"""
Advanced Telegram Account Manager Bot - v4
- Owner-triggered IMAP verification only (no auto verification on submit)
- Works for tier 1 (email+password), tier 2 (+2FA), tier 3 (+app password)
- Encryption at rest (Fernet)
- Session & pending-purchase persistence
- Config cache with TTL
- Transaction history
- IMAP rate limiting (60s per email)
- Daily auto backup
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
OWNER_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "0"))
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
        for filename in ("users.json", "config.json"):
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
VIDEOS_DIR = DATA_DIR / "videos"
BACKUP_DIR = DATA_DIR / "backups"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

PAGE_SIZE = 8
LEAVE_HOLD_SECONDS = 24 * 60 * 60
IMAP_RATE_LIMIT_SECONDS = 60

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


# ==================== USER DATA ====================
DEFAULT_USER_FIELDS = {
    "balance": 0.0,
    "pending_balance": 0.0,
    "hold_balance": 0.0,
    "total_credited_balance": 0.0,
    "spent_balance": 0.0,
    "approved_accounts": [],
    "pending_requests": [],
    "rejected_emails": [],
    "rejected_requests": [],
    "referral_code": "",
    "referred_by": None,
    "referral_earnings": 0.0,
    "total_referrals": 0,
    "total_approved_emails": 0,
    "pending_purchases": [],
    "used_app_passwords": [],
    "transactions": [],
}


def get_user(user_id: int) -> dict:
    users = load_json(USERS_DB)
    raw = users.get(str(user_id), {})
    merged = {**DEFAULT_USER_FIELDS, **raw}
    merged["balance"] = clamp_money(merged.get("balance"))
    merged["pending_balance"] = clamp_money(merged.get("pending_balance"))
    merged["hold_balance"] = clamp_money(merged.get("hold_balance"))
    merged["total_credited_balance"] = clamp_money(merged.get("total_credited_balance"))
    merged["spent_balance"] = clamp_money(merged.get("spent_balance"))
    merged["referral_earnings"] = clamp_money(merged.get("referral_earnings"))
    return decrypt_user_data(merged)


def save_user(user_id: int, user_data: dict):
    users = load_json(USERS_DB)
    for field_name in ("balance", "pending_balance", "hold_balance",
                       "total_credited_balance", "spent_balance", "referral_earnings"):
        if field_name in user_data:
            user_data[field_name] = clamp_money(user_data[field_name])
    if isinstance(user_data.get("transactions"), list) and len(user_data["transactions"]) > 200:
        user_data["transactions"] = user_data["transactions"][-200:]
    users[str(user_id)] = encrypt_user_data(user_data)
    save_json(USERS_DB, users)


def add_transaction(user_data: dict, kind: str, amount: float, note: str = "", email: str = ""):
    user_data.setdefault("transactions", []).append({
        "kind": kind,
        "amount": round(float(amount), 2),
        "note": note[:200],
        "email": email,
        "at": datetime.now(timezone.utc).isoformat(),
    })


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


def account_callback_token(email: Any) -> str:
    return hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()[:12]


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
    """Check IMAP transport stages before authentication without exposing secrets."""
    host, port = get_imap_host(email)
    if not host:
        return False, "⚠️ فشل تحديد مزود الإيميل: لا يوجد خادم IMAP معروف لهذا النطاق."

    started = time.monotonic()

    def elapsed() -> str:
        return f"{time.monotonic() - started:.1f} ثوانٍ"

    # Stage 1: DNS resolution.
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return False, f"❌ فشل DNS في تحويل {host} إلى عنوان IP بعد {elapsed()}: {exc}"
    except (socket.timeout, TimeoutError) as exc:
        return False, f"❌ انتهت مهلة DNS لـ {host} بعد {elapsed()}: {exc}"
    except OSError as exc:
        return False, f"❌ فشل DNS لـ {host} بعد {elapsed()}: {exc}"

    # Stage 2: TCP connection to IMAPS.
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

    # Stage 3: TLS handshake.
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

    # Stage 4: IMAP authentication, only after transport is confirmed.
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
            return False, (
            prefix + f"رد Gmail العام: {err}. لا يحدد IMAP هل السبب كلمة المرور أو App Password أو سياسة الحساب."
        )
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
        return False, f"❌ خطأ غير متوقع بعد نجاح فحص الشبكة ({elapsed()}). راجع سجل الخدمة دون تسجيل بيانات الاعتماد."


NETWORK_ERROR_MARKERS = (
    "timeout", "dns", "ssl", "connect", "unreachable", "refused",
    "تعذّر الاتصال", "انتهت مهلة",
)
AUTH_ERROR_MARKERS = (
    "invalid credentials", "authenticationfailed",
    "username and password not accepted",
    "بيانات الدخول غير صحيحة",
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
        "level": "failed",
        "badge": "🔴",
        "message": "",
        "imap_ok": False,
        "totp_ok": False,
        "category": "unknown",
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

    if imap_pass:
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
        result["message"] = ("🟢 تم التحقق الكامل عبر IMAP باستخدام كلمة مرور التطبيق."
                             if app_pass else "🟢 تم تسجيل الدخول عبر IMAP بنجاح.")
    elif result["category"] == "2fa":
        # Gmail's IMAP response is only a provider/policy hint; it does not prove
        # that this account has 2FA or that the supplied credentials are correct.
        result["level"] = "partial" if result["totp_ok"] else "unknown"
        result["badge"] = "🟡" if result["totp_ok"] else "⚪"
        if result["totp_ok"]:
            result["message"] = (
                "🟡 رفض Gmail مصادقة IMAP بطريقة تشير إلى App Password/2FA؛ "
                "مفتاح TOTP صالح محليًا، لكن لم يتم إثبات ارتباطه بهذا الحساب."
            )
        else:
            result["message"] = (
                "⚪ رفض Gmail مصادقة IMAP بطريقة تشير إلى App Password/2FA؛ "
                "لا يمكن إثبات حالة 2FA أو صحة بيانات الحساب من هذا الرد."
            )
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


# ==================== FORCED CHANNEL ====================
def normalize_forced_channel(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://t\.me/", "", value, flags=re.IGNORECASE)
    value = value.split("?", 1)[0].split("/", 1)[0].strip()
    if value and not value.startswith("@") and not value.lstrip("-").isdigit():
        value = f"@{value}"
    return value


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
    try:
        member = await context.bot.get_chat_member(forced_channel, user_id)
        if member.status in {"member", "administrator", "creator"}:
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


# ==================== MAIN MENU ====================
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    SESSIONS.pop(update.effective_user.id, None)
    save_sessions()
    user = update.effective_user
    buttons = [
        ("➕ إضافة حساب", "add_account"),
        ("💰 أموالي", "my_wallet"),
        ("📜 سجل معاملاتي", "my_transactions"),
        ("📋 حساباتي", "my_accounts"),
        ("📧 الإيميلات المرفوضة", "rejected_emails"),
        ("📺 تعليم", "tutorials"),
        ("🛒 سحب", "withdraw_store"),
        ("🔗 الإحالة", "referral_menu"),
        ("✏️ تعديل حساباتي", "edit_my_accounts"),
    ]
    if user.id == OWNER_ID:
        buttons.append(("⚙️ إعدادات المالك", "owner_panel"))
    text = "👋 مرحباً بك!\nاختر من القائمة أدناه:"
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb_vertical(buttons))
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=kb_vertical(buttons))
    else:
        await update.message.reply_text(text, reply_markup=kb_vertical(buttons))


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
                  "referral": "🎁", "purchase": "🛒"}
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
    approved = user_data.get("approved_accounts", [])
    pending = user_data.get("pending_requests", [])
    rejected = user_data.get("rejected_requests", [])
    if not approved and not pending and not rejected:
        await query.edit_message_text("📭 لا توجد حسابات لديك.",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
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
            reason_map = {"email": "إيميل خطأ", "password": "باسورد خطأ", "totp": "رمز مصادقة خطأ",
                          "app_pass": "كلمة مرور تطبيق خطأ", "custom": "سبب مخصص"}
            reason_text = reason_map.get(reason, reason)
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
    rejected = user_data.get("rejected_requests", [])
    if not rejected:
        rejected = [{"email": email, "reject_reason": "unknown"}
                    for email in user_data.get("rejected_emails", [])]
    if not rejected:
        await query.edit_message_text("📭 لا توجد لديك إيميلات مرفوضة حاليًا.",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    reason_map = {"email": "الإيميل غير صحيح أو غير مقبول",
                  "password": "كلمة المرور غير صحيحة",
                  "totp": "رمز المصادقة غير صحيح",
                  "app_pass": "كلمة مرور التطبيق غير صحيحة",
                  "other": "سبب آخر", "custom": "سبب مخصص", "unknown": "غير معروف"}
    lines = ["❌ <b>الإيميلات المرفوضة</b>", ""]
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
        float(user_data.get("pending_balance", 0.0)) - float(request.get("amount", 0.0))
    )
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


# ==================== ADD ACCOUNT FLOW ====================
async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    uid = update.effective_user.id
    clear_edit_state(context)
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
        f"📝 *إضافة حساب جديد*\n\n💵 *نظام المكافآت المتدرج:*\n"
        f"• إيميل + باسورد فقط → ${prices['tier_1']:.2f}\n"
        f"• إيميل + باسورد + رمز مصادقة → ${prices['tier_2']:.2f}\n"
        f"• إيميل + باسورد + رمز مصادقة + كلمة مرور تطبيق → ${prices['tier_3']:.2f}\n\n"
        f"📧 *الخطوة 1/4*: أرسل الإيميل:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


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

    if session.step == "email":
        email = normalize_email(text)
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            await update.message.reply_text("❌ إيميل غير صالح.")
            return
        active_status = get_active_account_status(email)
        if active_status == "approved":
            await update.message.reply_text("❌ هذا الإيميل مقبول مسبقاً! لا يمكنك إعادة إرساله.",
                                            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
            SESSIONS.pop(uid, None); save_sessions()
            return
        if active_status == "pending":
            await update.message.reply_text("⏳ هذا الإيميل قيد الانتظار بالفعل! انتظر موافقة المالك.",
                                            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
            SESSIONS.pop(uid, None); save_sessions()
            return
        session.email = email
        session.step = "password"
        save_sessions()
        has_password_video = config.get("video_password") and Path(config.get("video_password", "")).exists()
        buttons = []
        if has_password_video:
            buttons.append(("📹 طريقة تغيير الباسورد", "show_video:password"))
        buttons.append(("❌ إلغاء", "cancel"))
        await update.message.reply_text(
            f"🔑 *الخطوة 2/4*: أرسل كلمة المرور الأساسية:\n\n💰 *السعر الحالي:* ${prices['tier_1']:.2f} (إيميل + باسورد)",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

    elif session.step == "password":
        if has_active_account_password(text):
            await update.message.reply_text(
                "⚠️ كلمة المرور مستخدمة مسبقاً في حساب مقبول أو قيد الانتظار.",
                reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
            SESSIONS.pop(uid, None); save_sessions()
            return
        session.password = text
        session.has_password = True
        session.step = "totp"
        save_sessions()
        try:
            await update.message.delete()
        except Exception:
            pass
        has_totp_video = config.get("video_totp") and Path(config.get("video_totp", "")).exists()
        buttons = [("✅ استلم $0.10 (باسورد فقط)", f"submit_tier_1:{uid}")]
        if has_totp_video:
            buttons.append(("📹 طريقة العثور على رمز المصادقة", "show_video:totp"))
        buttons.append(("❌ إلغاء", "cancel"))
        await update.message.reply_text(
            f"🔐 *الخطوة 3/4*: أرسل مفتاح المصادقة (Secret Key):\n\n"
            f"💰 *السعر الحالي:* ${prices['tier_1']:.2f} (إيميل + باسورد)\n"
            f"💰 *السعر مع رمز المصادقة:* ${prices['tier_2']:.2f}\n\n"
            f"📌 *يمكنك استلام {prices['tier_1']:.2f}$ الآن وإكمال الباقي لاحقاً*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))

    elif session.step == "totp":
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
            session.totp = secret
            session.has_totp = True
            session.step = "app_pass"
            save_sessions()
            try:
                await update.message.delete()
            except Exception:
                pass
            has_app_pass_video = config.get("video_app_pass") and Path(config.get("video_app_pass", "")).exists()
            buttons = [("✅ استلم $0.15 (مع رمز المصادقة)", f"submit_tier_2:{uid}")]
            if has_app_pass_video:
                buttons.append(("📹 طريقة الحصول على كلمة مرور التطبيق", "show_video:app_pass"))
            buttons.append(("❌ إلغاء", "cancel"))
            await update.message.reply_text(
                f"✅ مفتاح المصادقة صالح!\n\n🔢 *الكود الحالي:* `{code}`\n\n"
                f"🗝 *الخطوة 4/4*: أرسل كلمة مرور التطبيق (16 حرف):\n"
                f"📌 الصيغة: XXXX XXXX XXXX XXXX\n\n"
                f"💰 *السعر الحالي:* ${prices['tier_2']:.2f} (مع رمز المصادقة)\n"
                f"💰 *السعر الكامل:* ${prices['tier_3']:.2f} (مع كلمة مرور التطبيق)\n\n"
                f"📌 *يمكنك استلام {prices['tier_2']:.2f}$ الآن وإكمال الباقي لاحقاً*",
                parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))
        except Exception as e:
            await update.message.reply_text(f"⚠️ مفتاح 2FA غير صالح: {str(e)}")

    elif session.step == "app_pass":
        cleaned = text.replace(" ", "")
        if len(cleaned) != 16:
            await update.message.reply_text("⚠️ كلمة مرور التطبيق يجب أن تكون 16 حرفاً.")
            return
        if not re.match(r'^[A-Za-z0-9]{16}$', cleaned):
            await update.message.reply_text("⚠️ كلمة مرور التطبيق تحتوي على أحرف غير صالحة.")
            return
        user_data = get_user(uid)
        active_status = get_active_account_status(session.email)
        if active_status:
            message = "❌ هذا الإيميل مقبول مسبقاً!" if active_status == "approved" else "⏳ هذا الإيميل قيد الانتظار بالفعل!"
            await update.message.reply_text(message, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
            SESSIONS.pop(uid, None); save_sessions()
            return
        if has_active_account_password(session.password):
            await update.message.reply_text("⚠️ كلمة المرور مستخدمة مسبقاً.",
                                            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
            SESSIONS.pop(uid, None); save_sessions()
            return
        if has_active_app_password(cleaned):
            config_video = config.get("video_app_pass")
            msg = "⚠️ *كلمة المرور هذه مستخدمة مسبقاً!*\n\nيرجى تغيير كلمة المرور وإرسال كلمة جديدة.\n\n📌 الصيغة: XXXX XXXX XXXX XXXX"
            if config_video and Path(config_video).exists():
                try:
                    await context.bot.send_video(chat_id=uid, video=open(config_video, "rb"),
                                                 caption=msg, parse_mode=ParseMode.MARKDOWN,
                                                 supports_streaming=True)
                except Exception:
                    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
            return
        session.app_pass = cleaned
        session.has_app_pass = True
        try:
            await update.message.delete()
        except Exception:
            pass
        user = update.effective_user
        user_full_name = user.full_name or "غير معروف"
        user_username = user.username or "لا يوجد"
        final_price = calculate_account_price(session.has_totp, session.has_app_pass)
        clear_rejected_email_records(user_data, session.email)
        user_data["pending_requests"].append({
            "email": session.email,
            "password": session.password,
            "totp": session.totp if session.has_totp else "",
            "app_pass": session.app_pass,
            "amount": final_price,
            "requested_amount": final_price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "extracted": False,
            "has_totp": session.has_totp,
            "has_app_pass": session.has_app_pass,
            "user_name": user_full_name,
            "user_username": user_username,
        })
        user_data["pending_balance"] = clamp_money(
            float(user_data.get("pending_balance", 0.0)) + final_price
        )
        add_transaction(user_data, "hold", final_price, "طلب جديد قيد الانتظار", session.email)
        save_user(uid, user_data)
        SESSIONS.pop(uid, None)
        save_sessions()
        referred_by = user_data.get("referred_by")
        if referred_by:
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=f"📢 *إشعار إحالة*\n\nالمستخدم `{uid}` أضاف إيميل `{session.email}` وهو قيد الانتظار.",
                    parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
        await send_leave_video_to_user(context, uid, session.email)
        if session.has_app_pass and session.has_totp:
            tier_text = "📦 *مكتمل (كامل المعلومات)*"
        elif session.has_totp:
            tier_text = "📦 *ناقص كلمة مرور التطبيق*"
        else:
            tier_text = "📦 *ناقص رمز المصادقة وكلمة مرور التطبيق*"
        await update.message.reply_text(
            f"✅ *تم إرسال الطلب للمالك للموافقة!*\n\n{tier_text}\n"
            f"💰 تمت إضافة *${final_price:.2f}* إلى الأموال قيد الانتظار.\n\n"
            f"📹 تم إرسال فيديو المغادرة إليك.\n"
            f"⚠️ قم بمغادرة الحساب لتجنب تأخير الدفعة.\n\n"
            f"_🔄 سيتم تحويل المبلغ إلى رصيدك بعد موافقة المالك_",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


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
    active_status = get_active_account_status(session.email)
    if active_status == "approved":
        await query.edit_message_text("❌ هذا الإيميل مقبول مسبقاً!",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        SESSIONS.pop(uid, None); save_sessions()
        return
    if active_status == "pending":
        await query.edit_message_text("⏳ هذا الإيميل قيد الانتظار بالفعل!",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        SESSIONS.pop(uid, None); save_sessions()
        return
    if has_active_account_password(session.password):
        await query.edit_message_text("⚠️ كلمة المرور مستخدمة مسبقاً.",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        SESSIONS.pop(uid, None); save_sessions()
        return
    user = update.effective_user
    user_full_name = user.full_name or "غير معروف"
    user_username = user.username or "لا يوجد"
    clear_rejected_email_records(user_data, session.email)
    user_data["pending_requests"].append({
        "email": session.email, "password": session.password,
        "totp": "", "app_pass": "", "amount": price, "requested_amount": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "extracted": False, "has_totp": False, "has_app_pass": False,
        "user_name": user_full_name, "user_username": user_username,
    })
    user_data["pending_balance"] = clamp_money(float(user_data.get("pending_balance", 0.0)) + price)
    add_transaction(user_data, "hold", price, "طلب (باسورد فقط)", session.email)
    save_user(uid, user_data)
    SESSIONS.pop(uid, None); save_sessions()
    await query.edit_message_text(
        f"✅ *تم إرسال الطلب للمالك!*\n\n📦 *المستوى 1: إيميل + باسورد فقط*\n"
        f"💰 تمت إضافة *${price:.2f}* إلى الأموال قيد الانتظار.\n\n"
        f"_🔄 سيتم تحويل المبلغ إلى رصيدك بعد موافقة المالك_",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


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
    active_status = get_active_account_status(session.email)
    if active_status == "approved":
        await query.edit_message_text("❌ هذا الإيميل مقبول مسبقاً!",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        SESSIONS.pop(uid, None); save_sessions()
        return
    if active_status == "pending":
        await query.edit_message_text("⏳ هذا الإيميل قيد الانتظار بالفعل!",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        SESSIONS.pop(uid, None); save_sessions()
        return
    if has_active_account_password(session.password):
        await query.edit_message_text("⚠️ كلمة المرور مستخدمة مسبقاً.",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        SESSIONS.pop(uid, None); save_sessions()
        return
    user = update.effective_user
    user_full_name = user.full_name or "غير معروف"
    user_username = user.username or "لا يوجد"
    clear_rejected_email_records(user_data, session.email)
    user_data["pending_requests"].append({
        "email": session.email, "password": session.password,
        "totp": session.totp, "app_pass": "", "amount": price, "requested_amount": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "extracted": False, "has_totp": True, "has_app_pass": False,
        "user_name": user_full_name, "user_username": user_username,
    })
    user_data["pending_balance"] = clamp_money(float(user_data.get("pending_balance", 0.0)) + price)
    add_transaction(user_data, "hold", price, "طلب (مع 2FA)", session.email)
    save_user(uid, user_data)
    SESSIONS.pop(uid, None); save_sessions()
    await query.edit_message_text(
        f"✅ *تم إرسال الطلب للمالك!*\n\n📦 *المستوى 2: إيميل + باسورد + رمز مصادقة*\n"
        f"💰 تمت إضافة *${price:.2f}* إلى الأموال قيد الانتظار.\n\n"
        f"_🔄 سيتم تحويل المبلغ إلى رصيدك بعد موافقة المالك_",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


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
    user_data["hold_balance"] = clamp_money(float(user_data.get("hold_balance", 0.0)) - price)
    user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) + price)
    account["leave_confirmed"] = True
    account["auto_confirmed"] = True
    account["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    account["released_amount"] = price
    add_transaction(user_data, "release", price, "تحويل تلقائي بعد 24 ساعة", email)
    save_user(user_id, user_data)
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ *تم إضافة المبلغ إلى رصيدك تلقائياً!*\n\n"
                 f"📧 الإيميل: `{email}`\n💰 تم إضافة *${price:.2f}* إلى رصيدك.\n\n"
                 f"_شكراً لاستخدامك البوت 🤖_",
            parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Could not send auto-confirmation to user {user_id}: {e}")


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
        for source_name in ("users.json", "config.json"):
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
    context.user_data.pop("step", None)
    await update.message.reply_text(message, parse_mode=ParseMode.HTML,
                                    reply_markup=kb_vertical([("🔎 فحص عضو آخر", "check_member"),
                                                              ("🔙 لوحة المالك", "owner_panel")]))


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
        ("🔙 القائمة الرئيسية", "main_menu")
    ]
    await query.edit_message_text("⚙️ *لوحة تحكم المالك*\n\nاختر الإعداد الذي تريد تعديله:",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


# ==================== TIER PRICES ====================
async def set_tier_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    prices = get_tier_prices()
    buttons = [("💲 المستوى 1 (باسورد فقط)", "set_tier:1"),
               ("💲 المستوى 2 (مع رمز المصادقة)", "set_tier:2"),
               ("💲 المستوى 3 (كامل)", "set_tier:3"),
               ("🔙 إعدادات المالك", "owner_panel")]
    await query.edit_message_text(
        f"💰 *إعدادات أسعار المستويات*\n\n"
        f"📌 *المستوى 1:* `${prices['tier_1']:.2f}`\n"
        f"📌 *المستوى 2:* `${prices['tier_2']:.2f}`\n"
        f"📌 *المستوى 3:* `${prices['tier_3']:.2f}`\n\n"
        f"اختر المستوى لتعديل سعره:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def set_tier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    tier = query.data.split(":")[1]
    context.user_data["setting_tier"] = tier
    tier_names = {"1": "المستوى 1", "2": "المستوى 2", "3": "المستوى 3"}
    await query.edit_message_text(f"💰 *تعديل سعر {tier_names[tier]}*\n\nأرسل السعر الجديد:",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", "set_tier_prices"))
    context.user_data["mode"] = "set_tier_price"


# ==================== VIDEOS ====================
async def videos_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    video_types = {"general": "📖 شرح عام للبوت", "email": "📹 فيديو إنشاء إيميل",
                   "password": "📹 فيديو تغيير باسورد", "totp": "📹 فيديو إضافة 2FA",
                   "app_pass": "📹 فيديو كلمة مرور التطبيق", "leave": "📹 فيديو المغادرة"}
    buttons = []
    for key, name in video_types.items():
        video_path = config.get(f"video_{key}")
        exists = video_path and Path(video_path).exists()
        buttons.append((f"{'✅' if exists else '❌'} {name}", f"video_action:{key}"))
    buttons.append(("🔙 إعدادات المالك", "owner_panel"))
    await query.edit_message_text("📹 *قسم الفيديوهات*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def video_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    config = load_config()
    video_path = config.get(f"video_{video_type}")
    exists = video_path and Path(video_path).exists()
    buttons = []
    if exists:
        buttons.append(("📹 عرض الفيديو", f"view_video:{video_type}"))
        buttons.append(("🗑️ حذف الفيديو", f"delete_video:{video_type}"))
    buttons.append(("📤 رفع فيديو جديد", f"set_video:{video_type}"))
    buttons.append(("🔙 قسم الفيديوهات", "videos_section"))
    status = "✅ موجود" if exists else "❌ غير موجود"
    await query.edit_message_text(f"📹 *فيديو {video_type}*\n\nالحالة: {status}",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def view_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    config = load_config()
    video_path = config.get(f"video_{video_type}")
    if video_path and Path(video_path).exists():
        try:
            await context.bot.send_video(chat_id=query.from_user.id, video=open(video_path, "rb"),
                                         caption=f"📹 *فيديو {video_type}*", parse_mode=ParseMode.MARKDOWN,
                                         supports_streaming=True)
            await video_action(update, context)
        except Exception as e:
            logger.error(f"Error: {e}")
            await query.edit_message_text("⚠️ خطأ.",
                                          reply_markup=kb_single("🔙 قسم الفيديوهات", "videos_section"))
    else:
        await query.edit_message_text("⚠️ الفيديو غير موجود.",
                                      reply_markup=kb_single("🔙 قسم الفيديوهات", "videos_section"))


async def delete_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    config = load_config()
    video_path = config.get(f"video_{video_type}")
    if video_path and Path(video_path).exists():
        try:
            Path(video_path).unlink()
        except OSError:
            pass
        config[f"video_{video_type}"] = ""
        save_config(config)
        await query.edit_message_text("✅ تم حذف الفيديو!",
                                      reply_markup=kb_single("🔙 قسم الفيديوهات", "videos_section"))
    else:
        await query.edit_message_text("⚠️ الفيديو غير موجود.",
                                      reply_markup=kb_single("🔙 قسم الفيديوهات", "videos_section"))


async def set_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    context.user_data["pending_video_type"] = video_type
    await query.edit_message_text(f"📤 *أرسل الفيديو الخاص بـ {video_type} الآن:*",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", f"video_action:{video_type}"))


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
        config = load_config()
        config[f"video_{video_type}"] = str(file_path)
        save_config(config)
        await update.message.reply_text(f"✅ تم حفظ فيديو {video_type} بنجاح!")
        context.user_data.pop("pending_video_type", None)
        await main_menu(update, context)
    else:
        await update.message.reply_text("⚠️ يرجى إرسال فيديو صحيح.")


# ==================== APPROVAL REQUESTS ====================
async def approval_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    buttons = [("⏳ منتظرة", "view_pending:0"),
               ("✅ مقبولة", "view_approved:0"),
               ("❌ مرفوضة", "view_rejected:0"),
               ("🔙 إعدادات المالك", "owner_panel")]
    await query.edit_message_text("📋 *الطلبات*\n\nاختر القسم:", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


def _collect_all(items_field: str) -> List[dict]:
    users = load_json(USERS_DB)
    items = []
    for uid, encrypted_data in users.items():
        user_data = decrypt_user_data(encrypted_data)
        for idx, item in enumerate(user_data.get(items_field, [])):
            copy = dict(item)
            copy["user_id"] = uid
            copy["index"] = idx
            items.append(copy)
    return items


def paginate_buttons(items: list, page: int, prefix: str, labeler) -> List[tuple]:
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    buttons = []
    for item in items[start:end]:
        buttons.append(labeler(item))
    nav = []
    if page > 0:
        nav.append(("⬅️ السابق", f"{prefix}:{page-1}"))
    nav.append((f"صفحة {page+1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        nav.append(("التالي ➡️", f"{prefix}:{page+1}"))
    if len(nav) > 1:
        buttons.append(nav)
    return buttons


async def view_pending_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    try:
        page = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    pending = _collect_all("pending_requests")
    if not pending:
        await query.edit_message_text("📭 لا توجد طلبات منتظرة.",
                                      reply_markup=kb_single("🔙 الطلبات", "approval_requests"))
        return

    def label(req):
        tier_icon = "🔵"
        if req.get("has_app_pass", False) and req.get("has_totp", False):
            tier_icon = "🟢"
        elif req.get("has_totp", False):
            tier_icon = "🟡"
        v = req.get("verification") or {}
        v_badge = v.get("badge", "⚪")
        email = req.get("email", "")
        email_display = email[:15] + "..." if len(email) > 15 else email
        return (f"{v_badge}{tier_icon} {email_display}",
                f"pending_detail:{req['user_id']}:{req['index']}")

    buttons = paginate_buttons(pending, page, "view_pending", label)
    buttons.append(("🔙 الطلبات", "approval_requests"))
    await query.edit_message_text(
        f"⏳ *الطلبات المنتظرة ({len(pending)})*\n"
        f"🟢 مكتمل | 🟡 مع 2FA | 🔵 باسورد فقط\n"
        f"⚪ لم يُتحقق | 🟢 تحقق كامل | 🟡 جزئي | 🔴 فشل\n\n"
        f"اختر الإيميل:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


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
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))
        return
    request = pending[index]
    email = request.get("email", "")
    tier_icon = "🟢" if request.get("has_app_pass") else "🟡" if request.get("has_totp") else "🔵"
    tier_text = "مكتمل" if request.get("has_app_pass") else "مع رمز المصادقة" if request.get("has_totp") else "باسورد فقط"
    msg = "📋 <b>تفاصيل الطلب</b>\n\n"
    msg += f"👤 <b>البائع:</b> {tg_html_escape(request.get('user_name', 'غير معروف'))}\n"
    msg += f"🆔 <b>اليوزر:</b> @{tg_html_escape(request.get('user_username', 'لا يوجد'))}\n"
    msg += f"📧 <b>الإيميل:</b> <code>{tg_html_escape(email)}</code>\n"
    msg += f"🔑 <b>الباسورد:</b> <code>{tg_html_escape(request.get('password', ''))}</code>\n"
    if request.get("has_totp", False):
        msg += f"🔐 <b>رمز المصادقة:</b> <code>{tg_html_escape(request.get('totp', ''))}</code>\n"
    else:
        msg += "🔐 <b>رمز المصادقة:</b> ❌ غير مرسل\n"
    if request.get("has_app_pass", False):
        formatted = tg_html_escape(format_app_password(request.get("app_pass", "")))
        msg += f"🗝 <b>كلمة مرور التطبيق:</b> <code>{formatted}</code>\n"
    else:
        msg += "🗝 <b>كلمة مرور التطبيق:</b> ❌ غير مرسل\n"
    msg += f"📦 <b>المستوى:</b> {tier_icon} {tier_text}\n"
    msg += f"👤 <b>المستخدم:</b> <code>{uid}</code>\n"
    msg += f"💰 <b>السعر:</b> ${request.get('amount', 0):.2f}\n"

    verification = request.get("verification") or {}
    if verification:
        v_badge = verification.get("badge", "⚪")
        v_level = verification.get("level", "unknown")
        v_msg = tg_html_escape(verification.get("message", ""))
        v_time = verification.get("verified_at", "")
        level_text = {"verified": "🟢 تحقق كامل", "partial": "🟡 تحقق جزئي",
                      "failed": "🔴 فشل التحقق", "unknown": "⚪ غير معروف"}.get(v_level, "⚪ غير محدد")
        msg += f"\n🔍 <b>آخر تحقق تلقائي:</b>\n   {v_badge} {level_text}\n"
        if v_msg:
            msg += f"   <i>{v_msg}</i>\n"
        if v_time:
            try:
                dt = datetime.fromisoformat(v_time)
                msg += f"   🕐 {dt.strftime('%Y-%m-%d %H:%M:%S')}\n"
            except Exception:
                pass

    config = load_config()
    has_leave_video = config.get("video_leave") and Path(config.get("video_leave", "")).exists()
    buttons = [("✅ قبول فوري", f"approve_request:{uid}:{index}")]
    if has_leave_video:
        buttons.append(("📹 قبول مع فيديو المغادرة", f"approve_with_leave:{uid}:{index}"))
    # 🔍 زر التحقق التلقائي — يظهر لأي طلب فيه إيميل + باسورد
    if request.get("password") or request.get("app_pass"):
        if request.get("has_app_pass", False):
            verify_label = "🔍 تحقق تلقائي (App Password)"
        elif request.get("has_totp", False):
            verify_label = "🔍 تحقق تلقائي (IMAP + 2FA)"
        else:
            verify_label = "🔍 تحقق تلقائي (IMAP)"
        buttons.append((verify_label, f"auto_verify:{uid}:{index}"))
    buttons.append(("❌ رفض", f"reject_request:{uid}:{index}"))
    buttons.append(("🔙 الطلبات المنتظرة", "view_pending:0"))
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


# ==================== AUTO VERIFY (OWNER-TRIGGERED) ====================
async def auto_verify_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحقق يدوي من الطلب — يعرض النتيجة فقط، لا يقبل ولا يرفض."""
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
        await query.edit_message_text("⚠️ هذا الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))
        return
    request = pending[index]
    email = request.get("email", "")
    password = request.get("password", "")
    app_pass = request.get("app_pass", "")
    totp_secret = request.get("totp", "")

    if not password and not app_pass:
        await query.answer("⚠️ لا توجد بيانات دخول للتحقق منها.", show_alert=True)
        return

    # 🚦 Rate limit
    allowed, wait = imap_rate_ok(email)
    if not allowed:
        await query.answer(f"⏳ انتظر {wait} ثانية قبل إعادة المحاولة لنفس الإيميل.", show_alert=True)
        return
    imap_rate_mark(email)

    await query.answer("🔍 جاري التحقق…", show_alert=False)
    loading_text = (f"🔍 <b>جاري التحقق التلقائي…</b>\n\n"
                    f"📧 <code>{tg_html_escape(email)}</code>\n\n"
                    f"<i>يتم الاتصال بخادم البريد والتحقق من البيانات…</i>")
    try:
        await query.edit_message_text(loading_text, parse_mode=ParseMode.HTML,
                                      reply_markup=kb_single("⏳ يرجى الانتظار",
                                                             f"pending_detail:{uid}:{index}"))
    except Exception:
        pass

    result = await verify_account_credentials(email=email, password=password,
                                              app_pass=app_pass, totp_secret=totp_secret)

    request["verification"] = {
        "level": result["level"], "badge": result["badge"], "message": result["message"],
        "imap_ok": result["imap_ok"], "totp_ok": result["totp_ok"],
        "category": result.get("category", "unknown"),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verified_by": "owner_manual",
    }
    pending[index] = request
    user_data["pending_requests"] = pending
    save_user(uid, user_data)

    if result["level"] == "verified":
        title = "🟢 <b>نجح التحقق التلقائي</b>"
    elif result["level"] == "partial":
        title = "🟡 <b>تحقق جزئي</b>"
    elif result["level"] == "unknown":
        if category in {"2fa", "auth_or_policy"}:
            title = "⚪ <b>تعذّر التحقق (رد Gmail عام)</b>"
        else:
            title = "⚪ <b>تعذّر التحقق (خطأ شبكة)</b>"
    else:
        title = "🔴 <b>فشل التحقق التلقائي</b>"

    # شرح نوع الحساب
    category = result.get("category", "unknown")
    if category == "2fa":
        category_hint = (
            "⚠️ رد Gmail يشير إلى App Password/2FA، لكنه لا يثبت أن الحساب محمي بـ2FA "
            "ولا يثبت صحة الإيميل أو كلمة المرور. صلاحية TOTP إن ظهرت هي فحص محلي للمفتاح فقط."
        )
    elif category in {"auth", "auth_or_policy"}:
        category_hint = (
            "⚠️ Gmail أعاد رفضاً عاماً للمصادقة. الشبكة سليمة، لكن IMAP لا يكشف السبب الداخلي؛ "
            "قد يكون App Password أو OAuth أو سياسة الحساب. راجع إعدادات Google دون إعادة المحاولة المتكررة."
        )
    elif category == "network":
        category_hint = "🌐 تعذّر الوصول لخادم البريد — قد يكون حجب من IP السيرفر."
    elif category == "ok":
        category_hint = "✅ البيانات صحيحة، الحساب موجود."
    else:
        category_hint = ""

    result_msg = f"{title}\n\n📧 <b>الإيميل:</b> <code>{tg_html_escape(email)}</code>\n\n"
    result_msg += f"📬 <b>IMAP:</b> {'✅ نجح' if result['imap_ok'] else '❌ فشل'}\n"
    if totp_secret:
        result_msg += f"🔐 <b>مفتاح 2FA:</b> {'✅ صالح' if result['totp_ok'] else '❌ غير صالح'}\n"
    result_msg += f"\n📝 <b>النتيجة:</b> {tg_html_escape(result['message'])}\n"
    if category_hint:
        result_msg += f"\n💡 {category_hint}\n"
    result_msg += f"\n<i>📌 هذا مجرد تقرير — لم يتم قبول أو رفض الطلب. القرار يبقى لك.</i>"

    buttons = [("🔍 تحقق مرة أخرى", f"auto_verify:{uid}:{index}"),
               ("✅ قبول فوري", f"approve_request:{uid}:{index}")]
    config = load_config()
    has_leave_video = config.get("video_leave") and Path(config.get("video_leave", "")).exists()
    if has_leave_video:
        buttons.append(("📹 قبول مع فيديو المغادرة", f"approve_with_leave:{uid}:{index}"))
    buttons.append(("❌ رفض", f"reject_request:{uid}:{index}"))
    buttons.append(("🔙 تفاصيل الطلب", f"pending_detail:{uid}:{index}"))
    buttons.append(("🔙 الطلبات المنتظرة", "view_pending:0"))

    await query.edit_message_text(result_msg, parse_mode=ParseMode.HTML,
                                  reply_markup=kb_vertical(buttons))


# ==================== COMPLETE APPROVAL ====================
async def complete_approval(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, index: int,
                            approved_request: dict, with_leave: bool = False):
    user_data = get_user(uid)
    config = load_config()
    requested_amount = approved_request.get("requested_amount", approved_request.get("amount"))
    if requested_amount is None:
        requested_amount = calculate_account_price(
            bool(approved_request.get("has_totp", False)),
            bool(approved_request.get("has_app_pass", False)))
    price = round(float(requested_amount), 2)
    approved_request["amount"] = price
    totp_code = ""
    if approved_request.get("has_totp", False) and approved_request.get("totp", ""):
        try:
            totp_code = pyotp.TOTP(approved_request.get("totp", "")).now()
        except Exception:
            totp_code = "غير متاح"
    approved_request["extracted"] = False
    approved_request["approved_with_leave"] = with_leave
    approved_request["leave_confirmed"] = not with_leave
    approved_request["totp_code"] = totp_code
    if with_leave:
        approval_time = datetime.now(timezone.utc)
        approved_request["approval_time"] = approval_time.isoformat()
        approved_request["release_at"] = (approval_time + timedelta(seconds=LEAVE_HOLD_SECONDS)).isoformat()
    user_data.setdefault("approved_accounts", []).append(approved_request)
    user_data["pending_balance"] = clamp_money(float(user_data.get("pending_balance", 0.0)) - price)
    if with_leave:
        user_data["hold_balance"] = clamp_money(float(user_data.get("hold_balance", 0.0)) + price)
        add_transaction(user_data, "hold", price, "معلق 24 ساعة", approved_request.get("email", ""))
    else:
        user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) + price)
        add_transaction(user_data, "credit", price, "قبول حساب", approved_request.get("email", ""))
    user_data["total_credited_balance"] = clamp_money(
        float(user_data.get("total_credited_balance", 0.0) or 0.0) + price)
    pending = user_data.get("pending_requests", [])
    if index < len(pending):
        pending.pop(index)
    user_data["pending_requests"] = pending
    user_data["total_approved_emails"] = int(user_data.get("total_approved_emails", 0)) + 1
    save_user(uid, user_data)
    referred_by = user_data.get("referred_by")
    if referred_by:
        referral_bonus = float(config.get("referral_bonus", 0.0))
        if referral_bonus > 0:
            referrer_data = get_user(referred_by)
            referrer_data["referral_earnings"] = clamp_money(
                float(referrer_data.get("referral_earnings", 0.0)) + referral_bonus)
            referrer_data["balance"] = clamp_money(
                float(referrer_data.get("balance", 0.0)) + referral_bonus)
            referrer_data["total_credited_balance"] = clamp_money(
                float(referrer_data.get("total_credited_balance", 0.0) or 0.0) + referral_bonus)
            referrer_data["total_referrals"] = int(referrer_data.get("total_referrals", 0)) + 1
            add_transaction(referrer_data, "referral", referral_bonus,
                            f"مكافأة إحالة للمستخدم {uid}", approved_request.get("email", ""))
            save_user(referred_by, referrer_data)
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=f"🎉 *مبروك!*\nحصلت على مكافأة إحالة بقيمة ${referral_bonus:.2f}",
                    parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
    email = approved_request.get("email", "")
    user_message = f"✅ <b>تم قبول طلبك!</b>\n\n📧 الإيميل: <code>{tg_html_escape(email)}</code>\n"
    if totp_code:
        user_message += f"🔢 <b>كود المصادقة:</b> <code>{tg_html_escape(totp_code)}</code>\n"
    if with_leave:
        user_message += (f"💰 المبلغ المعلق: <b>${price:.2f}</b>\n\n"
                         f"⏰ <b>سيتم إضافة المبلغ إلى رصيدك تلقائياً بعد 24 ساعة.</b>")
    else:
        user_message += f"💰 تم إضافة <b>${price:.2f}</b> إلى رصيدك."
    try:
        await context.bot.send_message(chat_id=uid, text=user_message, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    if with_leave:
        await send_leave_video_to_user(context, uid, email)
        await schedule_leave_check(context, uid, email, approved_request.get("release_at"))
    for key in ("approval_uid", "approval_index", "approval_data",
                "approval_step", "approval_with_leave"):
        context.user_data.pop(key, None)


# ==================== APPROVE / REJECT ====================
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
        await query.edit_message_text("⚠️ الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))
        return
    approved_request = pending[index]
    display_email = tg_html_escape(approved_request.get("email", ""))
    if not approved_request.get("has_totp", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_index"] = index
        context.user_data["approval_step"] = "waiting_totp"
        context.user_data["approval_data"] = approved_request
        context.user_data["approval_with_leave"] = False
        await query.edit_message_text(
            f"🔐 <b>طلب رمز المصادقة</b>\n\n📧 <code>{display_email}</code>\n\n"
            f"📌 أرسل رمز المصادقة (32 حرفاً):\n\n<i>أو 'تخطي'</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{index}"))
        return
    if not approved_request.get("has_app_pass", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_index"] = index
        context.user_data["approval_step"] = "waiting_app_pass"
        context.user_data["approval_data"] = approved_request
        context.user_data["approval_with_leave"] = False
        await query.edit_message_text(
            f"🗝 <b>طلب كلمة مرور التطبيق</b>\n\n📧 <code>{display_email}</code>\n\n"
            f"📌 أرسل كلمة مرور التطبيق (16 حرفاً):\n\n<i>أو 'تخطي'</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{index}"))
        return
    await complete_approval(update, context, uid, index, approved_request, False)
    await query.edit_message_text(
        f"✅ تم قبول الحساب <code>{display_email}</code> بنجاح!",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))


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
        await query.edit_message_text("⚠️ الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))
        return
    approved_request = pending[index]
    display_email = tg_html_escape(approved_request.get("email", ""))
    if not approved_request.get("has_totp", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_index"] = index
        context.user_data["approval_step"] = "waiting_totp"
        context.user_data["approval_data"] = approved_request
        context.user_data["approval_with_leave"] = True
        await query.edit_message_text(
            f"🔐 <b>طلب رمز المصادقة</b>\n\n📧 <code>{display_email}</code>\n\n"
            f"📌 أرسل رمز المصادقة (32 حرفاً):\n\n<i>أو 'تخطي'</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{index}"))
        return
    if not approved_request.get("has_app_pass", False):
        context.user_data["approval_uid"] = uid
        context.user_data["approval_index"] = index
        context.user_data["approval_step"] = "waiting_app_pass"
        context.user_data["approval_data"] = approved_request
        context.user_data["approval_with_leave"] = True
        await query.edit_message_text(
            f"🗝 <b>طلب كلمة مرور التطبيق</b>\n\n📧 <code>{display_email}</code>\n\n"
            f"📌 أرسل كلمة مرور التطبيق (16 حرفاً):\n\n<i>أو 'تخطي'</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{index}"))
        return
    await complete_approval(update, context, uid, index, approved_request, True)
    await query.edit_message_text(
        f"✅ تم قبول الحساب <code>{display_email}</code> مع فيديو المغادرة!\n"
        f"💰 المبلغ معلق لمدة 24 ساعة.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))


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
        await query.edit_message_text("⚠️ الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))
        return
    email = pending[index].get("email", "")
    display_email = tg_html_escape(email)
    context.user_data["reject_uid"] = uid
    context.user_data["reject_index"] = index
    buttons = [("📧 إيميل خطأ", f"reject_reason:email:{uid}:{index}"),
               ("🔑 باسورد خطأ", f"reject_reason:password:{uid}:{index}"),
               ("🔐 رمز مصادقة خطأ", f"reject_reason:totp:{uid}:{index}"),
               ("🗝 كلمة مرور تطبيق خطأ", f"reject_reason:app_pass:{uid}:{index}"),
               ("📝 خطأ آخر (اكتب السبب)", f"reject_reason:other:{uid}:{index}"),
               ("🔙 التفاصيل", f"pending_detail:{uid}:{index}")]
    await query.edit_message_text(
        f"❌ <b>رفض الطلب</b>\n\n📧 <code>{display_email}</code>\n\nاختر سبب الرفض:",
        parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


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
        await query.edit_message_text("⚠️ الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))
        return
    request = pending[index]
    email = request.get("email", "")
    display_email = tg_html_escape(email)
    pending.pop(index)
    move_request_to_rejected(user_data, request, reason_type)
    user_data["pending_requests"] = pending
    save_user(uid, user_data)
    reason_messages = {"email": "❌ الإيميل غير صحيح أو غير مقبول.",
                       "password": "❌ كلمة المرور غير صحيحة.",
                       "totp": "❌ رمز المصادقة غير صحيح.",
                       "app_pass": "❌ كلمة مرور التطبيق غير صحيحة.",
                       "other": "❌ تم رفض طلبك لسبب آخر."}
    reason = reason_messages.get(reason_type, "❌ تم رفض طلبك.")
    config = load_config()
    if reason_type in ["email", "password", "totp", "app_pass"]:
        video_key = {"email": "video_email", "password": "video_password",
                     "totp": "video_totp", "app_pass": "video_app_pass"}.get(reason_type)
        video_path = config.get(video_key)
        if video_path and Path(video_path).exists():
            try:
                await context.bot.send_video(chat_id=uid, video=open(video_path, "rb"),
                                             caption=f"{reason}\n\n📹 *شاهد الفيديو:*",
                                             parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            except Exception:
                pass
    else:
        context.user_data["reject_uid"] = uid
        context.user_data["reject_index"] = index
        context.user_data["reject_reason"] = "other"
        await query.edit_message_text(
            f"📝 <b>اكتب سبب الرفض</b>\n\nأرسل رسالة توضح سبب رفض طلب <code>{display_email}</code>:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{index}"))
        context.user_data["step"] = "reject_reason_text"
        return
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"{reason}\n\n📧 الإيميل: `{email}`\nيمكنك إعادة المحاولة.",
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    await query.edit_message_text(
        f"✅ تم رفض الطلب <code>{display_email}</code>.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))


async def handle_reject_reason_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data.get("reject_uid")
    index = context.user_data.get("reject_index")
    text = update.message.text.strip()
    if not uid or index is None:
        await update.message.reply_text("⚠️ حدث خطأ.")
        return
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await update.message.reply_text("⚠️ الطلب غير موجود.")
        return
    request = pending[index]
    email = request.get("email", "")
    pending.pop(index)
    move_request_to_rejected(user_data, request, "other", text)
    user_data["pending_requests"] = pending
    save_user(uid, user_data)
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"❌ *تم رفض طلبك*\n\n📧 الإيميل: `{email}`\n📝 السبب: {text}",
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    for key in ("reject_uid", "reject_index", "reject_reason", "step"):
        context.user_data.pop(key, None)
    await update.message.reply_text(f"✅ تم رفض الطلب `{email}`.",
                                    reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))


async def handle_approval_totp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data.get("approval_uid")
    index = context.user_data.get("approval_index")
    approved_request = context.user_data.get("approval_data")
    with_leave = context.user_data.get("approval_with_leave", False)
    if not uid or index is None or not approved_request:
        await update.message.reply_text("⚠️ حدث خطأ.")
        return
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await update.message.reply_text("⚠️ الطلب غير موجود.")
        return
    if text.lower() == "تخطي":
        approved_request["totp"] = ""
        approved_request["has_totp"] = False
        context.user_data["approval_data"] = approved_request
        if not approved_request.get("has_app_pass", False):
            context.user_data["approval_step"] = "waiting_app_pass"
            await update.message.reply_text(
                "✅ تم تخطي رمز المصادقة.\n\n🗝 *أرسل كلمة مرور التطبيق (16 حرفاً):*\n\n_أو 'تخطي'_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{index}"))
        else:
            await complete_approval(update, context, uid, index, approved_request, with_leave)
        return
    cleaned = text.replace(" ", "").upper()
    if len(cleaned) != 32 or not re.match(r'^[A-Z2-7]{32}$', cleaned):
        await update.message.reply_text("⚠️ مفتاح المصادقة يجب أن يكون 32 حرفاً Base32.")
        return
    try:
        secret = cleaned
        code = pyotp.TOTP(secret).now()
        approved_request["totp"] = secret
        approved_request["has_totp"] = True
        context.user_data["approval_data"] = approved_request
        formatted_secret = format_totp_secret(secret)
        if not approved_request.get("has_app_pass", False):
            context.user_data["approval_step"] = "waiting_app_pass"
            await update.message.reply_text(
                f"✅ رمز المصادقة صالح!\n🔐 *المفتاح:* `{formatted_secret}`\n🔢 *الكود:* `{code}`\n\n"
                f"🗝 *أرسل كلمة مرور التطبيق (16 حرفاً):*\n\n_أو 'تخطي'_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{index}"))
        else:
            await complete_approval(update, context, uid, index, approved_request, with_leave)
    except Exception as e:
        await update.message.reply_text(f"⚠️ مفتاح 2FA غير صالح: {str(e)}")


async def handle_approval_app_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data.get("approval_uid")
    index = context.user_data.get("approval_index")
    approved_request = context.user_data.get("approval_data")
    with_leave = context.user_data.get("approval_with_leave", False)
    if not uid or index is None or not approved_request:
        await update.message.reply_text("⚠️ حدث خطأ.")
        return
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await update.message.reply_text("⚠️ الطلب غير موجود.")
        return
    if text.lower() == "تخطي":
        approved_request["app_pass"] = ""
        approved_request["has_app_pass"] = False
        context.user_data["approval_data"] = approved_request
        await update.message.reply_text("✅ تم تخطي كلمة مرور التطبيق.")
        await complete_approval(update, context, uid, index, approved_request, with_leave)
        return
    cleaned = text.replace(" ", "")
    if len(cleaned) != 16 or not re.match(r'^[A-Za-z0-9]{16}$', cleaned):
        await update.message.reply_text("⚠️ كلمة مرور التطبيق يجب أن تكون 16 حرفاً.")
        return
    if has_active_app_password(cleaned):
        await update.message.reply_text("⚠️ كلمة المرور هذه مستخدمة مسبقاً!")
        return
    approved_request["app_pass"] = cleaned
    approved_request["has_app_pass"] = True
    context.user_data["approval_data"] = approved_request
    formatted = format_app_password(cleaned)
    await update.message.reply_text(f"✅ تم استلام كلمة مرور التطبيق.\n🗝 `{formatted}`",
                                    parse_mode=ParseMode.MARKDOWN)
    await complete_approval(update, context, uid, index, approved_request, with_leave)


# ==================== VIEW APPROVED / REJECTED ====================
async def view_approved_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    try:
        page = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    approved = _collect_all("approved_accounts")
    if not approved:
        await query.edit_message_text("📭 لا توجد طلبات مقبولة.",
                                      reply_markup=kb_single("🔙 الطلبات", "approval_requests"))
        return

    def label(acc):
        tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        user_name = acc.get("user_name", "غير معروف")
        user_username = acc.get("user_username", "لا يوجد")
        display_name = f"{user_name} (@{user_username})" if user_username != "لا يوجد" else user_name
        email_display = acc.get('email', '')[:15] + "..." if len(acc.get('email', '')) > 15 else acc.get('email', '')
        return (f"{tier_icon} {email_display} - {display_name[:12]} (${acc.get('amount', 0):.2f})",
                f"approved_detail:{acc['user_id']}:{account_callback_token(acc.get('email', ''))}")

    buttons = paginate_buttons(approved, page, "view_approved", label)
    buttons.append(("🔙 الطلبات", "approval_requests"))
    await query.edit_message_text(
        f"✅ *الطلبات المقبولة ({len(approved)})*\n\nاختر الإيميل:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def approved_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    token = parts[2]
    user_data = get_user(uid)
    account_match = find_approved_account(user_data, token)
    if account_match is None:
        await query.edit_message_text("⚠️ هذا الحساب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved:0"))
        return
    index, account = account_match
    account_token = account_callback_token(account.get("email", ""))
    tier_icon = "🟢" if account.get("has_app_pass") else "🟡" if account.get("has_totp") else "🔵"
    tier_text = "مكتمل" if account.get("has_app_pass") else "مع رمز المصادقة" if account.get("has_totp") else "باسورد فقط"
    msg = f"📋 *تفاصيل الحساب المقبول*\n\n"
    msg += f"👤 *البائع:* {account.get('user_name', 'غير معروف')}\n"
    msg += f"🆔 *اليوزر:* @{account.get('user_username', 'لا يوجد')}\n"
    msg += f"📧 *الإيميل:* `{account.get('email', '')}`\n"
    msg += f"🔑 *الباسورد:* `{account.get('password', '')}`\n"
    if account.get("has_totp", False):
        msg += f"🔐 *رمز المصادقة:* `{account.get('totp', '')}`\n"
        totp_code = account.get("totp_code", "")
        if totp_code:
            msg += f"🔢 *كود المصادقة (الحالي):* `{totp_code}`\n"
        else:
            try:
                msg += f"🔢 *كود المصادقة (الحالي):* `{pyotp.TOTP(account.get('totp', '')).now()}`\n"
            except Exception:
                pass
    else:
        msg += f"🔐 *رمز المصادقة:* ❌ غير مرسل\n"
    if account.get("has_app_pass", False):
        msg += f"🗝 *كلمة مرور التطبيق:* `{format_app_password(account.get('app_pass', ''))}`\n"
    else:
        msg += f"🗝 *كلمة مرور التطبيق:* ❌ غير مرسل\n"
    msg += f"📦 *المستوى:* {tier_icon} {tier_text}\n"
    msg += f"👤 *المستخدم:* `{uid}`\n"
    msg += f"💰 *السعر:* ${account.get('amount', 0):.2f}\n"
    if account.get("approved_with_leave", False) and not account.get("leave_confirmed", False):
        msg += "📌 *حالة المغادرة:* ⏳ معلق (24 ساعة)\n"
    elif account.get("approved_with_leave", False) and account.get("leave_confirmed", False):
        msg += "📌 *حالة المغادرة:* ✅ تم التحويل\n"
    if account.get("has_totp", False) and account.get("totp", ""):
        buttons = [("🔄 كود جديد", f"new_totp_code:{uid}:{account_token}"),
                   ("💰 خصم نقاط", f"deduct_points:{uid}:{account_token}"),
                   ("🔙 الطلبات المقبولة", "view_approved:0")]
    else:
        buttons = [("💰 خصم نقاط", f"deduct_points:{uid}:{account_token}"),
                   ("🔙 الطلبات المقبولة", "view_approved:0")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def new_totp_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    token = parts[2]
    user_data = get_user(uid)
    account_match = find_approved_account(user_data, token)
    if account_match is None:
        await query.edit_message_text("⚠️ الحساب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved:0"))
        return
    index, account = account_match
    account_token = account_callback_token(account.get("email", ""))
    accounts = user_data.get("approved_accounts", [])
    if not account.get("has_totp", False) or not account.get("totp", ""):
        await query.edit_message_text("⚠️ لا يوجد رمز مصادقة.",
                                      reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved:0"))
        return
    try:
        new_code = pyotp.TOTP(account.get("totp", "")).now()
        account["totp_code"] = new_code
        accounts[index] = account
        user_data["approved_accounts"] = accounts
        save_user(uid, user_data)
        await query.edit_message_text(
            f"🔄 *كود المصادقة الجديد*\n\n📧 `{account.get('email', '')}`\n"
            f"🔢 *الكود:* `{new_code}`\n⏰ *الوقت:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_vertical([("🔄 تحديث", f"new_totp_code:{uid}:{account_token}"),
                                      ("🔙 الطلبات المقبولة", "view_approved:0")]))
    except Exception as e:
        await query.edit_message_text(f"⚠️ خطأ: {str(e)}",
                                      reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved:0"))


async def deduct_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    token = parts[2]
    user_data = get_user(uid)
    account_match = find_approved_account(user_data, token)
    if account_match is None:
        await query.edit_message_text("⚠️ الحساب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved:0"))
        return
    _, account = account_match
    email = account.get("email", "")
    context.user_data["deduct_uid"] = uid
    context.user_data["deduct_token"] = account_callback_token(email)
    await query.edit_message_text(
        f"💰 *خصم نقاط*\n\n📧 `{email}`\n👤 `{uid}`\n\n📌 أرسل المبلغ:\n\n_أو 'إلغاء'_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", f"approved_detail:{uid}:{account_callback_token(email)}"))
    context.user_data["step"] = "deduct_points_input"


async def handle_deduct_points_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() in ("الغاء", "إلغاء"):
        for key in ("deduct_uid", "deduct_token", "step"):
            context.user_data.pop(key, None)
        await update.message.reply_text("❌ تم الإلغاء.",
                                        reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved:0"))
        return
    try:
        amount = float(text)
        if amount <= 0:
            await update.message.reply_text("⚠️ المبلغ يجب أن يكون أكبر من 0!")
            return
        uid = context.user_data.get("deduct_uid")
        token = context.user_data.get("deduct_token")
        if not uid or not token:
            await update.message.reply_text("⚠️ حدث خطأ.")
            return
        user_data = get_user(uid)
        account_match = find_approved_account(user_data, token)
        if account_match is None:
            await update.message.reply_text("⚠️ الحساب غير موجود.")
            return
        _, account = account_match
        current_balance = float(user_data.get("balance", 0.0))
        if current_balance < amount:
            await update.message.reply_text(f"⚠️ رصيد المستخدم غير كافٍ!\n💰 الرصيد: ${current_balance:.2f}")
            return
        user_data["balance"] = clamp_money(current_balance - amount)
        user_data["spent_balance"] = clamp_money(float(user_data.get("spent_balance", 0.0)) + amount)
        add_transaction(user_data, "debit", amount, "خصم من المالك", account.get("email", ""))
        save_user(uid, user_data)
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"💰 *تم خصم نقاط من رصيدك!*\n\n📧 `{account.get('email', '')}`\n"
                     f"💰 المخصوم: *${amount:.2f}*\n💰 الرصيد: *${user_data['balance']:.2f}*",
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
        for key in ("deduct_uid", "deduct_token", "step"):
            context.user_data.pop(key, None)
        await update.message.reply_text(
            f"✅ تم خصم ${amount:.2f} من رصيد `{uid}`.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved:0"))
    except ValueError:
        await update.message.reply_text("⚠️ أرسل رقماً صحيحاً.")


async def view_rejected_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    try:
        page = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    rejected = _collect_all("rejected_requests")
    if not rejected:
        await query.edit_message_text("📭 لا توجد طلبات مرفوضة.",
                                      reply_markup=kb_single("🔙 الطلبات", "approval_requests"))
        return

    def label(rej):
        reason = rej.get('reject_reason', 'غير معروف')
        reason_map = {"email": "📧", "password": "🔑", "totp": "🔐", "app_pass": "🗝", "other": "📝"}
        icon = reason_map.get(reason, "❌")
        user_name = rej.get("user_name", "غير معروف")
        user_username = rej.get("user_username", "لا يوجد")
        display_name = f"{user_name} (@{user_username})" if user_username != "لا يوجد" else user_name
        email_display = rej.get('email', '')[:15] + "..." if len(rej.get('email', '')) > 15 else rej.get('email', '')
        return (f"{icon} {email_display} - {display_name[:12]}",
                f"rejected_detail:{rej['user_id']}:{rej.get('index', 0)}")

    buttons = paginate_buttons(rejected, page, "view_rejected", label)
    buttons.append(("🔙 الطلبات", "approval_requests"))
    await query.edit_message_text(
        f"❌ *الطلبات المرفوضة ({len(rejected)})*\n\nاختر الإيميل:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


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
        await query.edit_message_text("⚠️ الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المرفوضة", "view_rejected:0"))
        return
    request = rejected_list[index]
    reason = request.get('reject_reason', 'غير معروف')
    reason_map = {"email": "❌ الإيميل غير صحيح.", "password": "❌ كلمة المرور غير صحيحة.",
                  "totp": "❌ رمز المصادقة غير صحيح.", "app_pass": "❌ كلمة مرور التطبيق غير صحيحة.",
                  "other": "❌ سبب آخر.", "custom": "❌ سبب مخصص."}
    reason_text = request.get("reject_reason_text") or reason_map.get(reason, reason)
    tier_icon = "🟢" if request.get("has_app_pass") else "🟡" if request.get("has_totp") else "🔵"
    tier_text = "مكتمل" if request.get("has_app_pass") else "مع رمز المصادقة" if request.get("has_totp") else "باسورد فقط"
    msg = f"📋 *تفاصيل الطلب المرفوض*\n\n"
    msg += f"👤 *البائع:* {request.get('user_name', 'غير معروف')}\n"
    msg += f"🆔 *اليوزر:* @{request.get('user_username', 'لا يوجد')}\n"
    msg += f"📧 *الإيميل:* `{request.get('email', '')}`\n"
    msg += f"🔑 *الباسورد:* `{request.get('password', '')}`\n"
    if request.get("has_totp", False):
        msg += f"🔐 *رمز المصادقة:* `{request.get('totp', '')}`\n"
    if request.get("has_app_pass", False):
        msg += f"🗝 *كلمة مرور التطبيق:* `{request.get('app_pass', '')}`\n"
    msg += f"📦 *المستوى:* {tier_icon} {tier_text}\n"
    msg += f"👤 *المستخدم:* `{uid}`\n"
    msg += f"💰 *السعر:* ${request.get('amount', 0):.2f}\n"
    msg += f"📝 *سبب الرفض:* {tg_html_escape(str(reason_text))}\n\n"
    msg += f"📌 *هل تريد إعطاء نقاط للمستخدم رغم الرفض؟*"
    buttons = [("💰 إعطاء نقاط", f"give_points:{uid}:{index}"),
               ("🔙 الطلبات المرفوضة", "view_rejected:0")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


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
        await query.edit_message_text("⚠️ الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المرفوضة", "view_rejected:0"))
        return
    email = rejected_list[index].get("email", "")
    context.user_data["give_uid"] = uid
    context.user_data["give_index"] = index
    await query.edit_message_text(
        f"💰 *إعطاء نقاط*\n\n📧 `{email}`\n👤 `{uid}`\n\n📌 أرسل المبلغ:\n\n_أو 'إلغاء'_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", f"rejected_detail:{uid}:{index}"))
    context.user_data["step"] = "give_points_input"


async def handle_give_points_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() in ("الغاء", "إلغاء"):
        for key in ("give_uid", "give_index", "step"):
            context.user_data.pop(key, None)
        await update.message.reply_text("❌ تم الإلغاء.",
                                        reply_markup=kb_single("🔙 الطلبات المرفوضة", "view_rejected:0"))
        return
    try:
        amount = float(text)
        if amount <= 0:
            await update.message.reply_text("⚠️ المبلغ يجب أن يكون أكبر من 0!")
            return
        uid = context.user_data.get("give_uid")
        index = context.user_data.get("give_index")
        if not uid or index is None:
            await update.message.reply_text("⚠️ حدث خطأ.")
            return
        user_data = get_user(uid)
        user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) + amount)
        user_data["total_credited_balance"] = clamp_money(
            float(user_data.get("total_credited_balance", 0.0)) + amount)
        rejected_list = user_data.get("rejected_requests", [])
        email = rejected_list[index].get("email", "") if index < len(rejected_list) else ""
        add_transaction(user_data, "credit", amount, "تعويض عن طلب مرفوض", email)
        save_user(uid, user_data)
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"💰 *تم إضافة نقاط إلى رصيدك!*\n\n📧 `{email}`\n"
                     f"💰 المبلغ: *+${amount:.2f}*\n💰 الرصيد: *${user_data['balance']:.2f}*",
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
        for key in ("give_uid", "give_index", "step"):
            context.user_data.pop(key, None)
        await update.message.reply_text(
            f"✅ تم إضافة ${amount:.2f} لرصيد `{uid}`.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 الطلبات المرفوضة", "view_rejected:0"))
    except ValueError:
        await update.message.reply_text("⚠️ أرسل رقماً صحيحاً.")


async def points_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    buttons = [("➕ منح نقاط", "give_points_by_id"),
               ("➖ خصم نقاط", "deduct_points_by_id"),
               ("🔙 إعدادات المالك", "owner_panel")]
    await query.edit_message_text("💰 *إدارة النقاط*\n\nاختر الإجراء:", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def give_points_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text(
        "➕ *منح نقاط*\n\nأرسل معرف + المبلغ:\n📌 مثال: `123456789 5.00`\n\n_أو 'إلغاء'_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", "points_management"))
    context.user_data["step"] = "give_points_by_id_input"


async def deduct_points_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text(
        "➖ *خصم نقاط*\n\nأرسل معرف + المبلغ:\n📌 مثال: `123456789 5.00`\n\n_أو 'إلغاء'_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", "points_management"))
    context.user_data["step"] = "deduct_points_by_id_input"


async def handle_points_by_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() in ("الغاء", "إلغاء"):
        context.user_data.pop("step", None)
        await update.message.reply_text("❌ تم الإلغاء.",
                                        reply_markup=kb_single("🔙 إدارة النقاط", "points_management"))
        return
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text("⚠️ الصيغة: `معرف المبلغ`")
        return
    user_input = parts[0]
    try:
        amount = float(parts[1])
        if amount <= 0:
            await update.message.reply_text("⚠️ المبلغ يجب أن يكون أكبر من 0!")
            return
    except ValueError:
        await update.message.reply_text("⚠️ المبلغ غير صحيح!")
        return
    target_user_id = None
    if user_input.lstrip("-").isdigit():
        target_user_id = int(user_input)
    else:
        username = user_input.removeprefix("@")
        users = load_json(USERS_DB)
        for uid, encrypted_data in users.items():
            data = decrypt_user_data(encrypted_data)
            if str(data.get("user_username", "")).lower() == username.lower():
                target_user_id = int(uid)
                break
    if not target_user_id:
        await update.message.reply_text("⚠️ لم يتم العثور على مستخدم.")
        return
    step = context.user_data.get("step")
    if step == "give_points_by_id_input":
        user_data = get_user(target_user_id)
        user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) + amount)
        user_data["total_credited_balance"] = clamp_money(
            float(user_data.get("total_credited_balance", 0.0)) + amount)
        add_transaction(user_data, "credit", amount, "منح من المالك")
        save_user(target_user_id, user_data)
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"💰 *تم إضافة نقاط!*\n\n💰 +${amount:.2f}\n💰 الرصيد: *${user_data['balance']:.2f}*",
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ تم إضافة ${amount:.2f} لرصيد `{target_user_id}`.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 إدارة النقاط", "points_management"))
    elif step == "deduct_points_by_id_input":
        user_data = get_user(target_user_id)
        current_balance = float(user_data.get("balance", 0.0))
        if current_balance < amount:
            await update.message.reply_text(f"⚠️ رصيد غير كافٍ! الرصيد: ${current_balance:.2f}")
            return
        user_data["balance"] = clamp_money(current_balance - amount)
        user_data["spent_balance"] = clamp_money(float(user_data.get("spent_balance", 0.0)) + amount)
        add_transaction(user_data, "debit", amount, "خصم من المالك")
        save_user(target_user_id, user_data)
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"💰 *تم خصم نقاط!*\n\n💰 -${amount:.2f}\n💰 الرصيد: *${user_data['balance']:.2f}*",
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ تم خصم ${amount:.2f} من `{target_user_id}`.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 إدارة النقاط", "points_management"))
    context.user_data.pop("step", None)


async def all_accounts_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    buttons = [("📋 جميع الحسابات", "all_accounts"),
               ("🆕 آخر الحسابات (غير المستخرجة)", "unextracted_accounts"),
               ("⏳ الحسابات المعلقة (24 ساعة)", "hold_accounts"),
               ("🔙 إعدادات المالك", "owner_panel")]
    await query.edit_message_text("📊 *جميع الحسابات المقبولة*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def hold_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    hold_list = []
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        for acc in data.get("approved_accounts", []):
            if acc.get("approved_with_leave", False) and not acc.get("leave_confirmed", False):
                copy = dict(acc)
                copy["user_id"] = uid
                hold_list.append(copy)
    if not hold_list:
        await query.edit_message_text("✅ لا توجد حسابات معلقة.",
                                      reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    total = len(hold_list)
    msg = f"⏳ *الحسابات المعلقة: {total}*\n\n"
    for idx, acc in enumerate(hold_list[:10], 1):
        approval_time = acc.get("approval_time", "")
        time_display = "غير معروف"
        try:
            dt = datetime.fromisoformat(approval_time)
            time_left = 86400 - (datetime.now(timezone.utc) - dt).total_seconds()
            if time_left > 0:
                time_display = f"{int(time_left//3600)} س {int((time_left%3600)//60)} د"
            else:
                time_display = "قريباً"
        except Exception:
            pass
        tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        msg += f"{idx}. {tier_icon} 📧 `{acc.get('email', '')}`\n"
        msg += f"   👤 {acc.get('user_id', '')} | 💰 ${acc.get('amount', 0):.2f}\n"
        msg += f"   ⏳ {time_display}\n   ─────────────\n"
    if total > 10:
        msg += f"\n📌 أول 10 من {total}"
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))


async def all_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    all_accs = []
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        for acc in data.get("approved_accounts", []):
            copy = dict(acc)
            copy["user_id"] = uid
            all_accs.append(copy)
    if not all_accs:
        await query.edit_message_text("📭 لا توجد حسابات.",
                                      reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    total = len(all_accs)
    msg = f"📊 *إجمالي: {total}*\n\n"
    for idx, acc in enumerate(all_accs[:10], 1):
        leave_status = ""
        if acc.get("approved_with_leave") and not acc.get("leave_confirmed"):
            leave_status = " ⏳"
        tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        msg += f"{idx}. {tier_icon} 📧 `{acc.get('email', '')}`{leave_status}\n"
        msg += f"   🔑 `{acc.get('password', '')}`\n"
        if acc.get("has_totp"):
            msg += f"   🔐 `{acc.get('totp', '')}`\n"
        if acc.get("has_app_pass"):
            msg += f"   🗝 `{format_app_password(acc.get('app_pass', ''))}`\n"
        msg += f"   👤 {acc.get('user_id', '')} | 💰 ${acc.get('amount', 0):.2f}\n   ─────────────\n"
    if total > 10:
        msg += f"\n📌 أول 10 من {total}"
    buttons = [("📥 تصدير جميع الحسابات", "export_all_accounts"),
               ("🆕 الحسابات غير المستخرجة", "unextracted_accounts"),
               ("⏳ المعلقة", "hold_accounts"),
               ("🔙 جميع الحسابات", "all_accounts_section")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def unextracted_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    unextracted = []
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        for idx, acc in enumerate(data.get("approved_accounts", [])):
            if not acc.get("extracted", False):
                copy = dict(acc)
                copy["user_id"] = uid
                copy["index"] = idx
                unextracted.append(copy)
    if not unextracted:
        await query.edit_message_text("✅ لا توجد حسابات غير مستخرجة.",
                                      reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    total = len(unextracted)
    msg = f"🆕 *غير مستخرجة: {total}*\n\n"
    for idx, acc in enumerate(unextracted[:10], 1):
        tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        msg += f"{idx}. {tier_icon} 📧 `{acc.get('email', '')}`\n"
        msg += f"   🔑 `{acc.get('password', '')}`\n"
        if acc.get("has_totp"):
            msg += f"   🔐 `{acc.get('totp', '')}`\n"
        if acc.get("has_app_pass"):
            msg += f"   🗝 `{format_app_password(acc.get('app_pass', ''))}`\n"
        msg += f"   👤 {acc.get('user_id', '')}\n   ─────────────\n"
    if total > 10:
        msg += f"\n📌 أول 10 من {total}"
    buttons = [("📥 تصدير", "export_unextracted"),
               ("✅ وضع علامة مستخرجة", "mark_extracted_menu"),
               ("🔙 جميع الحسابات", "all_accounts_section")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def mark_extracted_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    unextracted = []
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        for idx, acc in enumerate(data.get("approved_accounts", [])):
            if not acc.get("extracted", False):
                unextracted.append({"user_id": uid, "index": idx, "email": acc.get("email", "")})
    if not unextracted:
        await query.edit_message_text("✅ لا توجد.",
                                      reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    buttons = []
    for item in unextracted[:10]:
        buttons.append((f"✅ {item['email']}", f"mark_extracted:{item['user_id']}:{item['index']}"))
    buttons.append(("🔙 جميع الحسابات", "all_accounts_section"))
    await query.edit_message_text("✅ *تحديد المستخرجة*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


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
        await query.edit_message_text("✅ تم وضع علامة مستخرجة.",
                                      reply_markup=kb_single("🔙 الحسابات غير المستخرجة", "unextracted_accounts"))
    else:
        await query.edit_message_text("⚠️ الحساب غير موجود.",
                                      reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))


def build_accounts_export(title: str, accounts: list) -> bytes:
    lines = [title, "=" * 60, ""]
    for idx, acc in enumerate(accounts, 1):
        leave_status = ""
        if acc.get("approved_with_leave") and not acc.get("leave_confirmed"):
            leave_status = " (معلق)"
        tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        lines.append(f"{tier_icon} {idx}. البريد: {acc.get('email', '')}{leave_status}")
        lines.append(f"🔑 كلمة المرور: {acc.get('password', '')}")
        if acc.get("has_totp"):
            lines.append(f"🔐 TOTP: {acc.get('totp', '')}")
        if acc.get("has_app_pass"):
            lines.append(f"🗝 كلمة مرور التطبيق: {format_app_password(acc.get('app_pass', ''))}")
        if "amount" in acc:
            try:
                lines.append(f"💰 المبلغ: ${float(acc.get('amount', 0) or 0):.2f}")
            except (TypeError, ValueError):
                lines.append(f"💰 المبلغ: {acc.get('amount', '')}")
        lines.extend(["─" * 20, ""])
    return "\n".join(lines).encode("utf-8")


async def send_accounts_export(context: ContextTypes.DEFAULT_TYPE, accounts: list,
                                filename: str, caption: str):
    document = io.BytesIO(build_accounts_export(caption, accounts))
    await context.bot.send_document(chat_id=OWNER_ID, document=document,
                                    filename=filename, caption="📥 تم تجهيز ملف الحسابات.")


async def export_all_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    all_accs = []
    for encrypted_data in users.values():
        data = decrypt_user_data(encrypted_data)
        all_accs.extend(data.get("approved_accounts", []))
    if not all_accs:
        await query.edit_message_text("📭 لا توجد حسابات.")
        return
    await send_accounts_export(context, all_accs, "all_accounts.txt", "جميع الحسابات المقبولة")
    await query.edit_message_text(f"✅ تم إرسال {len(all_accs)} حساب.",
                                  reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))


async def export_unextracted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    unextracted = []
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        for acc in data.get("approved_accounts", []):
            if not acc.get("extracted", False):
                unextracted.append(acc)
    if not unextracted:
        await query.edit_message_text("✅ لا توجد.")
        return
    await send_accounts_export(context, unextracted, "unextracted_accounts.txt", "غير المستخرجة")
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        changed = False
        for acc in data.get("approved_accounts", []):
            if not acc.get("extracted", False):
                acc["extracted"] = True
                changed = True
        if changed:
            save_user(int(uid), data)
    await query.edit_message_text(f"✅ تم إرسال {len(unextracted)} حساب.",
                                  reply_markup=kb_single("🔙 الحسابات غير المستخرجة", "unextracted_accounts"))


# ==================== PURCHASE CHANNELS ====================
def purchase_channels_keyboard():
    return kb_vertical([("1️⃣ ضبط الأول", "set_purchase_channel_1"),
                        ("2️⃣ ضبط الثاني", "set_purchase_channel_2"),
                        ("🔙 إعدادات المالك", "owner_panel")])


async def purchase_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    ch1, ch2 = get_configured_purchase_channels()
    await query.edit_message_text(
        f"📨 *إشعارات الشراء*\n\n1️⃣ `{ch1 or 'غير مضبوط'}`\n2️⃣ `{ch2 or 'غير مضبوط'}`",
        parse_mode=ParseMode.MARKDOWN, reply_markup=purchase_channels_keyboard())


async def set_purchase_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_number: int):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    context.user_data["store_action"] = f"set_purchase_channel_{channel_number}"
    await query.edit_message_text(f"✏️ أرسل معرف الكروب {channel_number}:",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=purchase_channels_keyboard())


async def forced_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    current_channel = config.get("forced_channel", "")
    buttons = [("🗑️ إلغاء القناة", "remove_channel"), ("🔙 إعدادات المالك", "owner_panel")]
    await query.edit_message_text(
        f"📢 *القناة الإجبارية*\n\n📌 الحالية: `{current_channel or 'لا توجد'}`\n\n"
        f"✏️ أرسل معرف القناة الجديدة:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))
    context.user_data["store_action"] = "set_channel"


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    config["forced_channel"] = ""
    save_config(config)
    await query.edit_message_text("✅ تم إلغاء القناة.",
                                  reply_markup=kb_single("🔙 إعدادات المالك", "owner_panel"))


# ==================== WITHDRAW STORE ====================
async def withdraw_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    config = load_config()
    categories = config.get("store_categories", [])
    if not categories:
        await query.edit_message_text("🛒 لا توجد فئات.",
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    buttons = [(f"📂 {cat['name']}", f"user_category:{cat['id']}") for cat in categories]
    buttons.append(("🔙 القائمة الرئيسية", "main_menu"))
    await query.edit_message_text("🛒 *قسم السحب*\nاختر الفئة:", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def user_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    cat_id = query.data.split(":", 1)[1]
    config = load_config()
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.",
                                      reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        return
    services = category.get("services", [])
    if not services:
        await query.edit_message_text("📭 لا توجد خدمات.",
                                      reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        return
    buttons = [(f"🛒 {s['name']} - ${s['price']:.2f}", f"user_buy:{s['id']}:{cat_id}") for s in services]
    buttons.append(("🔙 قسم السحب", "withdraw_store"))
    await query.edit_message_text(f"📂 *{category['name']}*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def user_buy_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    parts = query.data.split(":")
    service_id = parts[1]
    cat_id = parts[2]
    user_id = query.from_user.id
    config = load_config()
    service = None
    for cat in config.get("store_categories", []):
        if cat["id"] == cat_id:
            for s in cat["services"]:
                if s["id"] == service_id:
                    service = s
                    break
            break
    if not service:
        await query.edit_message_text("⚠️ الخدمة غير موجودة.",
                                      reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        return
    user_data = get_user(user_id)
    price_cents = money_to_cents(service.get("price", 0))
    price = cents_to_money(price_cents)
    balance_cents = money_to_cents(user_data.get("balance", 0))
    if balance_cents < price_cents:
        await query.edit_message_text(
            f"❌ رصيدك غير كافٍ. الرصيد: ${cents_to_money(balance_cents):.2f}, السعر: ${price:.2f}")
        return
    PENDING_PURCHASES[user_id] = {"service_id": service_id,
                                   "service_name": service.get("name", ""),
                                   "service_price": price,
                                   "service_message": service.get("message", "شكراً!"),
                                   "purchased_at": datetime.now().isoformat()}
    save_pending_purchases()
    bot_username = (await context.bot.get_me()).username
    total_emails = user_data.get("total_approved_emails", 0)
    channel_1_text = (f"🛒 <b>طلب شراء جديد</b>\n\n"
                      f"🤖 @{html.escape(bot_username or 'غير معروف')}\n"
                      f"📦 <code>{html.escape(str(service.get('name', '')))}</code>\n"
                      f"💰 <code>${service['price']:.2f}</code>\n"
                      f"📧 عدد الإيميلات: <code>{total_emails}</code>")
    purchase_channel_1, _ = get_configured_purchase_channels()
    if purchase_channel_1:
        try:
            await context.bot.send_message(chat_id=purchase_channel_1, text=channel_1_text,
                                           parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception("Could not send purchase notification.")
    await query.edit_message_text(
        f"✅ *تم طلب الخدمة!*\n\n🛒 *{service.get('name', '')}*\n"
        f"💰 *${price:.2f}*\n\n📝 {service.get('message', '')}\n\n"
        f"_📤 أرسل المعلومات في رسالة جديدة_",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))


async def deliver_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    user_id = int(query.data.split(":")[1])
    user_data = get_user(user_id)
    total_emails = user_data.get("total_approved_emails", 0)
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📦 *تم استلام طلبك!*\n\n✅ سيتم التواصل معك قريباً.",
            parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Could not deliver: {e}")
    PENDING_PURCHASES.pop(user_id, None)
    save_pending_purchases()
    await query.edit_message_text(
        f"✅ *تم الإيصال!*\n\n👤 `{user_id}`\n📧 `{total_emails}`\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode=ParseMode.MARKDOWN)


async def handle_purchase_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in PENDING_PURCHASES:
        await text_input(update, context)
        return
    purchase = PENDING_PURCHASES[user_id]
    user_data = get_user(user_id)
    price_cents = money_to_cents(purchase.get("service_price", 0))
    price = cents_to_money(price_cents)
    balance_cents = money_to_cents(user_data.get("balance", 0))
    if balance_cents < price_cents:
        await update.message.reply_text("❌ رصيدك غير كافٍ.",
                                        reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        PENDING_PURCHASES.pop(user_id, None)
        save_pending_purchases()
        return
    user_data["balance"] = cents_to_money(balance_cents - price_cents)
    user_data["spent_balance"] = cents_to_money(
        money_to_cents(user_data.get("spent_balance", 0)) + price_cents)
    add_transaction(user_data, "purchase", price, f"شراء: {purchase.get('service_name', '')}")
    save_user(user_id, user_data)
    user = update.effective_user
    user_name = user.full_name or "غير معروف"
    user_username = user.username or "لا يوجد"
    total_emails = user_data.get("total_approved_emails", 0)
    _, purchase_channel_2 = get_configured_purchase_channels()
    if purchase_channel_2:
        text2 = (f"📋 <b>طلب شراء مكتمل</b>\n\n"
                 f"👤 <b>{html.escape(user_name)}</b>\n"
                 f"🆔 @{html.escape(user_username)}\n"
                 f"🆔 <code>{user_id}</code>\n"
                 f"📦 <code>{html.escape(str(purchase['service_name']))}</code>\n"
                 f"💰 <code>${price:.2f}</code>\n"
                 f"📧 <code>{total_emails}</code>\n\n"
                 f"📝 <b>الرسالة:</b>\n<code>{html.escape(text)}</code>\n"
                 f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            await context.bot.send_message(chat_id=purchase_channel_2, text=text2,
                                           parse_mode=ParseMode.HTML,
                                           reply_markup=kb_single("✅ تم الإيصال", f"deliver_order:{user_id}"))
        except Exception:
            logger.exception("Could not send member message.")
    if OWNER_ID:
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"📩 *رسالة من مستخدم*\n\n👤 `{user_name}`\n🆔 @{user_username}\n"
                     f"🆔 `{user_id}`\n📦 `{purchase['service_name']}`\n"
                     f"💰 `${price:.2f}`\n\n📝 `{text}`",
                parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Error: {e}")
    await update.message.reply_text(
        f"✅ *تم استلام رسالتك!*\n\n💰 تم خصم `${price:.2f}`.\n"
        f"📝 `{text}`\n\n_📌 سيتم التواصل معك قريباً._",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
    PENDING_PURCHASES.pop(user_id, None)
    save_pending_purchases()


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
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


async def tutorials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    config = load_config()
    buttons = []
    for key, name in [("general", "📖 شرح عام"), ("email", "📹 إنشاء إيميل"),
                      ("password", "📹 تغيير باسورد"), ("totp", "📹 إضافة 2FA"),
                      ("app_pass", "📹 كلمة مرور التطبيق"), ("leave", "📹 فيديو المغادرة")]:
        if config.get(f"video_{key}") and Path(config.get(f"video_{key}", "")).exists():
            buttons.append((name, f"play_video:{key}"))
    buttons.append(("🔙 القائمة الرئيسية", "main_menu"))
    await query.edit_message_text("📺 *اختر الدرس:*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def play_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    vtype = query.data.split(":")[1]
    config = load_config()
    path = config.get(f"video_{vtype}")
    video_names = {"general": "شرح عام", "email": "إنشاء إيميل", "password": "تغيير باسورد",
                   "totp": "إضافة 2FA", "app_pass": "كلمة مرور التطبيق", "leave": "المغادرة"}
    if path and Path(path).exists():
        try:
            await context.bot.send_video(chat_id=query.from_user.id, video=open(path, "rb"),
                                         caption=f"📹 *{video_names.get(vtype, vtype)}*",
                                         parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            await tutorials(update, context)
        except Exception as e:
            logger.error(f"Error: {e}")
            await query.edit_message_text("⚠️ خطأ.",
                                          reply_markup=kb_single("🔙 التعليم", "tutorials"))
    else:
        await query.edit_message_text("⚠️ الفيديو غير موجود.",
                                      reply_markup=kb_single("🔙 التعليم", "tutorials"))


def generate_referral_code():
    return secrets.token_hex(4).upper()


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
    msg = (f"🔗 *نظام الإحالة*\n\n📌 *رابطك:*\n`{referral_link}`\n\n"
           f"📊 *إحصائياتك:*\n💰 المكافآت: ${float(user_data.get('referral_earnings', 0.0)):.2f}\n"
           f"👥 عدد الإحالات: {user_data.get('total_referrals', 0)}\n\n"
           f"📝 *كيف يعمل؟*\n1️⃣ شارك الرابط\n2️⃣ عند قبول حساب صديقك\n3️⃣ ستحصل على مكافأة")
    buttons = [("📋 نسخ الرابط", f"copy_referral:{referral_code}"),
               ("🔙 القائمة الرئيسية", "main_menu")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def copy_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    code = query.data.split(":")[1]
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={code}"
    buttons = [("🔗 عرض رابط الإحالة", "referral_menu"),
               ("🔙 القائمة الرئيسية", "main_menu")]
    await query.edit_message_text(f"📋 *رابطك:*\n\n`{link}`", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def referral_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    referral_bonus = config.get("referral_bonus", 0.0)
    buttons = [("💲 تغيير المكافأة", "set_referral_bonus"),
               ("📊 إحصائيات", "referral_stats"),
               ("🔙 إعدادات المالك", "owner_panel")]
    await query.edit_message_text(
        f"🔗 *إعدادات الإحالة*\n\n💰 المكافأة الحالية: ${referral_bonus:.2f}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def set_referral_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text("💰 *مكافأة الإحالة*\n\nأرسل المبلغ الجديد:",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", "referral_settings"))
    context.user_data["mode"] = "set_referral_bonus"


async def referral_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    total_referrals = 0
    total_earnings = 0.0
    top = []
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        if data.get("total_referrals", 0) > 0:
            total_referrals += data["total_referrals"]
            total_earnings += float(data.get("referral_earnings", 0.0))
            top.append({"user_id": uid, "count": data["total_referrals"],
                        "earnings": float(data.get("referral_earnings", 0.0))})
    top.sort(key=lambda x: x["count"], reverse=True)
    msg = (f"📊 *إحصائيات الإحالة*\n\n👥 الإجمالي: {total_referrals}\n"
           f"💰 المكافآت: ${total_earnings:.2f}\n\n")
    if top:
        msg += "🏆 *أفضل المحالين:*\n"
        for idx, ref in enumerate(top[:5], 1):
            msg += f"{idx}. {ref['user_id']} - {ref['count']} - ${ref['earnings']:.2f}\n"
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إعدادات الإحالة", "referral_settings"))


# ==================== TEXT INPUT ROUTER ====================
async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if user_id in PENDING_PURCHASES:
        await handle_purchase_message(update, context)
        return

    if not await check_forced_channel(update, context):
        return

    step = context.user_data.get("step")
    if step == "reject_reason_text":
        await handle_reject_reason_text(update, context); return
    if step == "deduct_points_input":
        await handle_deduct_points_input(update, context); return
    if step == "give_points_input":
        await handle_give_points_input(update, context); return
    if step == "give_points_by_id_input" or step == "deduct_points_by_id_input":
        await handle_points_by_id_input(update, context); return
    if step == "check_member_input":
        await handle_member_check_input(update, context); return
    if step == "editing_field":
        await handle_edit_field_input(update, context); return

    approval_step = context.user_data.get("approval_step")
    if approval_step == "waiting_totp":
        await handle_approval_totp(update, context); return
    if approval_step == "waiting_app_pass":
        await handle_approval_app_pass(update, context); return

    mode = context.user_data.get("mode")
    if mode == "set_tier_price":
        if user_id != OWNER_ID: return
        try:
            price = float(text)
            if price <= 0:
                await update.message.reply_text("⚠️ السعر > 0!")
                return
            tier = context.user_data.get("setting_tier")
            if tier:
                config = load_config()
                config[f"tier_{tier}_price"] = price
                save_config(config)
                await update.message.reply_text(f"✅ تم تحديث سعر المستوى {tier}: ${price:.2f}")
                context.user_data.pop("mode", None)
                context.user_data.pop("setting_tier", None)
                await set_tier_prices(update, context)
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً.")
        return

    if mode == "set_referral_bonus":
        if user_id != OWNER_ID: return
        try:
            bonus = float(text)
            if bonus < 0:
                await update.message.reply_text("⚠️ المبلغ >= 0!")
                return
            config = load_config()
            config["referral_bonus"] = bonus
            save_config(config)
            await update.message.reply_text(f"✅ تم تحديث مكافأة الإحالة: ${bonus:.2f}")
            context.user_data.pop("mode", None)
            await owner_panel(update, context)
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً.")
        return

    if context.user_data.get("store_action"):
        await handle_store_input(update, context); return

    await add_account_step(update, context)


async def handle_store_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    text = update.message.text.strip()
    action = context.user_data.get("store_action")
    if action == "add_category":
        config = load_config()
        if "store_categories" not in config:
            config["store_categories"] = []
        if any(cat["name"].lower() == text.lower() for cat in config["store_categories"]):
            await update.message.reply_text("⚠️ الفئة موجودة!")
            return
        config["store_categories"].append({"id": str(time.time_ns()), "name": text, "services": []})
        save_config(config)
        await update.message.reply_text(f"✅ تم إضافة الفئة: {text}")
        context.user_data.pop("store_action", None)
        await main_menu(update, context)
    elif action == "set_channel":
        config = load_config()
        config["forced_channel"] = normalize_forced_channel(text)
        save_config(config)
        await update.message.reply_text(f"✅ تم تعيين القناة: {config['forced_channel']}")
        context.user_data.pop("store_action", None)
        await main_menu(update, context)
    elif action in {"set_purchase_channel_1", "set_purchase_channel_2"}:
        channel_id = normalize_chat_id(text)
        if not channel_id or not (channel_id.startswith("@") or channel_id.lstrip("-").isdigit()):
            await update.message.reply_text("⚠️ المعرف غير صحيح.")
            return
        channel_number = action.rsplit("_", 1)[1]
        config = load_config()
        config[f"purchase_channel_{channel_number}"] = channel_id
        save_config(config)
        context.user_data.pop("store_action", None)
        await update.message.reply_text(
            f"✅ تم حفظ الكروب رقم {channel_number}: {channel_id}",
            reply_markup=purchase_channels_keyboard())
    elif action == "add_service_name":
        context.user_data["store_service_name"] = text
        context.user_data["store_action"] = "add_service_price"
        await update.message.reply_text(
            "💰 *الخطوة 2/3*: أرسل السعر:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 إلغاء",
                                   f"store_category:{context.user_data.get('current_category_id')}"))
    elif action == "add_service_price":
        try:
            price = float(text)
            if price <= 0:
                await update.message.reply_text("⚠️ السعر > 0!")
                return
            context.user_data["store_service_price"] = price
            context.user_data["store_action"] = "add_service_message"
            await update.message.reply_text(
                "📝 *الخطوة 3/3*: أرسل الرسالة للعميل:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_single("🔙 إلغاء",
                                       f"store_category:{context.user_data.get('current_category_id')}"))
        except ValueError:
            await update.message.reply_text("⚠️ رقم صحيح!")
    elif action == "add_service_message":
        name = context.user_data.get("store_service_name")
        price = context.user_data.get("store_service_price")
        cat_id = context.user_data.get("current_category_id")
        config = load_config()
        for cat in config.get("store_categories", []):
            if cat["id"] == cat_id:
                cat["services"].append({"id": str(time.time_ns()), "name": name,
                                        "price": price, "message": text})
                break
        save_config(config)
        await update.message.reply_text("✅ تم إضافة المبيعة!")
        for key in ("store_action", "store_service_name", "store_service_price", "current_category_id"):
            context.user_data.pop(key, None)
        await main_menu(update, context)


# ==================== STORE SECTION ====================
async def owner_store_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    categories = config.get("store_categories", [])
    buttons = [(f"📂 {cat['name']}", f"store_category:{cat['id']}") for cat in categories]
    buttons.append(("➕ إضافة فئة", "store_add_category"))
    buttons.append(("🔙 إعدادات المالك", "owner_panel"))
    await query.edit_message_text("🛒 *إدارة المبيعات*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def store_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text("✏️ *اسم الفئة:*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", "store_section"))
    context.user_data["store_action"] = "add_category"


async def store_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    config = load_config()
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.",
                                      reply_markup=kb_single("🔙 المبيعات", "store_section"))
        return
    services = category.get("services", [])
    msg = f"📂 *{category['name']}*\n\n"
    if services:
        for idx, s in enumerate(services, 1):
            msg += f"{idx}. 🛒 {s['name']} - 💰 ${s['price']:.2f}\n"
    else:
        msg += "📭 لا توجد مبيعات.\n"
    buttons = [("➕ إضافة", f"store_add_service:{cat_id}"),
               ("🗑️ حذف", f"store_delete_service:{cat_id}"),
               ("🔙 المبيعات", "store_section")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def store_add_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    context.user_data["current_category_id"] = cat_id
    context.user_data["store_action"] = "add_service_name"
    await query.edit_message_text("✏️ *اسم المبيعة:*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))


async def store_delete_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    config = load_config()
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.")
        return
    services = category.get("services", [])
    if not services:
        await query.edit_message_text("📭 لا توجد مبيعات.",
                                      reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))
        return
    buttons = [(f"❌ {s['name']} - ${s['price']:.2f}", f"delete_service:{cat_id}:{s['id']}") for s in services]
    buttons.append(("🔙 الفئة", f"store_category:{cat_id}"))
    await query.edit_message_text("🗑️ *حذف مبيعة*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def delete_service_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    cat_id = parts[1]
    service_id = parts[2]
    config = load_config()
    for cat in config.get("store_categories", []):
        if cat["id"] == cat_id:
            cat["services"] = [s for s in cat["services"] if s["id"] != service_id]
            break
    save_config(config)
    await query.edit_message_text("✅ تم الحذف.",
                                  reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))


# ==================== REFERRAL HANDLER ====================
async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE, referral_code: str):
    user_id = update.effective_user.id
    if context.user_data.get("my_referral_code") == referral_code:
        await update.message.reply_text("⚠️ لا يمكنك استخدام رابطك!")
        return
    user_data = get_user(user_id)
    if user_data.get("referred_by"):
        await update.message.reply_text("ℹ️ أنت مشترك بالفعل.")
        return
    users = load_json(USERS_DB)
    referrer_id = None
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        if data.get("referral_code") == referral_code:
            referrer_id = int(uid)
            break
    if not referrer_id:
        await update.message.reply_text("❌ رابط غير صالح.")
        return
    user_data["referred_by"] = referrer_id
    save_user(user_id, user_data)
    await update.message.reply_text(
        f"✅ *تم تفعيل الإحالة!*\n\n👤 بواسطة: {referrer_id}\nاستخدم /start للبدء.",
        parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.send_message(
            chat_id=referrer_id,
            text=f"🎉 *إحالة جديدة!*\n\n👤 المستخدم {user_id} انضم.",
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_edit_state(context)
    SESSIONS.pop(update.effective_user.id, None)
    save_sessions()
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
    data = query.data
    if data == "noop":
        await query.answer()
        return
    await query.answer()

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
    elif data == "my_transactions":
        await my_transactions(update, context)
    elif data == "my_accounts":
        await my_accounts(update, context)
    elif data == "rejected_emails":
        await view_member_rejected_emails(update, context)
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
    elif data.startswith("view_pending"):
        await view_pending_requests(update, context)
    elif data.startswith("view_approved"):
        await view_approved_requests(update, context)
    elif data.startswith("view_rejected"):
        await view_rejected_requests(update, context)
    elif data.startswith("pending_detail:"):
        await pending_detail(update, context)
    elif data.startswith("auto_verify:"):
        await auto_verify_account(update, context)
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
    elif data.startswith("edit_field:"):
        await edit_field(update, context)
    elif data.startswith("delete_pending:"):
        await delete_pending_account(update, context)
    else:
        await placeholder(update, context)


async def placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.callback_query.edit_message_text(
            "⚠️ خيار غير معروف.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
    except Exception:
        pass


# ==================== COMMANDS ====================
async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        f"🔎 *تشخيص*\n\n🆔 رقمك: `{user_id}`\n👑 المالك: `{OWNER_ID}`\n"
        f"✅ أنت المالك: {'نعم' if user_id == OWNER_ID else 'لا'}\n"
        f"🔐 التشفير: {'مفعّل ✅' if CRYPTO_AVAILABLE else 'معطّل ❌'}\n"
        f"💾 عدد المستخدمين: `{len(load_json(USERS_DB))}`",
        parse_mode=ParseMode.MARKDOWN)


async def owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 هذا الأمر للمالك فقط.")
        return
    buttons = [("💰 أسعار المستويات", "set_tier_prices"),
               ("📋 الطلبات", "approval_requests"),
               ("📹 قسم الفيديوهات", "videos_section"),
               ("🛒 المبيعات", "store_section"),
               ("📢 قناة إجبارية", "forced_channel"),
               ("📊 جميع الحسابات المقبولة", "all_accounts_section"),
               ("📈 إحصائيات المستخدمين", "owner_stats"),
               ("🔎 فحص عضو", "check_member"),
               ("🔗 نظام الإحالة", "referral_settings"),
               ("💰 خصم/منح نقاط", "points_management"),
               ("🔙 القائمة الرئيسية", "main_menu")]
    await update.message.reply_text("⚙️ *لوحة تحكم المالك*", parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=kb_vertical(buttons))


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await daily_backup_job(context)
    await update.message.reply_text("✅ تم إنشاء نسخة احتياطية.")


# ==================== MAIN ====================
def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN غير مضبوط.")
    if not OWNER_ID:
        logger.warning("⚠️ OWNER_TELEGRAM_ID = 0")
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(restore_leave_checks)
           .build())
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("owner", owner_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CallbackQueryHandler(router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_upload))
    logger.info("🤖 Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()
