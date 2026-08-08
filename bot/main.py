"""Telegram account manager bot.

This is the Python implementation of the original bot.  It intentionally
keeps the existing JSON data format so a previously attached Railway volume
can be reused without a migration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import smtplib
import ssl
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Awaitable, Callable

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


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_TELEGRAM_ID = os.environ.get("OWNER_TELEGRAM_ID", "").strip()
DATA_DIR = Path(
    os.environ.get("DATA_DIR", "").strip()
    or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    or Path.cwd() / "data"
).resolve()
DB_FILE = DATA_DIR / "accounts.json"
BACKUP_FILE = DATA_DIR / "accounts.json.bak"


@dataclass
class SessionData:
    step: str | None = None
    pending_account: dict[str, Any] = field(default_factory=dict)
    editing_id: str | None = None
    edit_field: str | None = None


SESSIONS: dict[int, SessionData] = {}

FIELD_LABELS = {
    "email": "📧 الإيميل",
    "password": "🔑 كلمة المرور",
    "totpSecret": "🔐 مفتاح المصادقة الثنائية",
    "recoveryCodes": "📋 أكواد الاسترداد",
    "appPassword": "🗝 كلمة مرور التطبيق",
}

SMTP_CONFIGS = {
    "gmail.com": ("smtp.gmail.com", 587, False),
    "googlemail.com": ("smtp.gmail.com", 587, False),
    "outlook.com": ("smtp.office365.com", 587, False),
    "hotmail.com": ("smtp.office365.com", 587, False),
    "live.com": ("smtp.office365.com", 587, False),
    "msn.com": ("smtp.office365.com", 587, False),
    "yahoo.com": ("smtp.mail.yahoo.com", 587, False),
    "yahoo.co.uk": ("smtp.mail.yahoo.com", 587, False),
    "icloud.com": ("smtp.mail.me.com", 587, False),
    "me.com": ("smtp.mail.me.com", 587, False),
    "mac.com": ("smtp.mail.me.com", 587, False),
    "protonmail.com": ("smtp.protonmail.ch", 587, False),
    "proton.me": ("smtp.protonmail.ch", 587, False),
    "zoho.com": ("smtp.zoho.com", 587, False),
    "aol.com": ("smtp.aol.com", 587, False),
}


def session_for(update: Update) -> SessionData:
    user_id = update.effective_user.id if update.effective_user else 0
    return SESSIONS.setdefault(user_id, SessionData())


def clear_session(update: Update) -> None:
    SESSIONS[update.effective_user.id if update.effective_user else 0] = SessionData()


def is_owner(update: Update) -> bool:
    if not OWNER_TELEGRAM_ID or not update.effective_user:
        return False
    try:
        return update.effective_user.id == int(OWNER_TELEGRAM_ID)
    except ValueError:
        return False


async def reject_non_owner(update: Update, query: Any) -> bool:
    if is_owner(update):
        return False
    await query.answer("🚫 هذا الإجراء متاح للمالك فقط.")
    return True


def keyboard(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return keyboard([("❌ إلغاء", "cancel")])


def main_menu_keyboard(update: Update) -> InlineKeyboardMarkup:
    rows: list[list[tuple[str, str]]] = [[("➕ إضافة حساب", "add_account")]]
    if is_owner(update):
        rows.extend(
            [
                [("📋 عرض جميع الحسابات", "view_all")],
                [("✏️ تعديل حساب", "edit_list")],
                [("✅ التحقق من حساب", "verify_list")],
            ]
        )
    else:
        rows.append([("ℹ️ الخدمات العامة متاحة للجميع", "main_menu")])
    return keyboard(*rows)


async def send_main_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str = "🔐 القائمة الرئيسية"
) -> None:
    clear_session(update)
    if update.effective_message:
        await update.effective_message.reply_text(
            text, reply_markup=main_menu_keyboard(update)
        )


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_accounts() -> list[dict[str, Any]]:
    ensure_data_dir()
    if not DB_FILE.exists():
        return []
    try:
        value = json.loads(DB_FILE.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError("expected a JSON list")
        return value
    except Exception as exc:
        raise RuntimeError(
            f"تعذر قراءة {DB_FILE}. لم تتم الكتابة فوق الملف حفاظًا على البيانات. "
            f"يمكن استعادته من {BACKUP_FILE} عند الحاجة. السبب: {exc}"
        ) from exc


def save_accounts(accounts: list[dict[str, Any]]) -> None:
    ensure_data_dir()
    temporary = DB_FILE.with_name(f"{DB_FILE.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.chmod(0o600)
    with temporary.open("rb") as file:
        os.fsync(file.fileno())
    if DB_FILE.exists():
        BACKUP_FILE.write_bytes(DB_FILE.read_bytes())
        BACKUP_FILE.chmod(0o600)
    temporary.replace(DB_FILE)
    DB_FILE.chmod(0o600)


def add_account(data: dict[str, Any]) -> dict[str, Any]:
    accounts = load_accounts()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    account = {
        **data,
        "id": str(time.time_ns()),
        "createdAt": now,
        "updatedAt": now,
    }
    accounts.append(account)
    save_accounts(accounts)
    return account


def update_account(account_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    accounts = load_accounts()
    for index, account in enumerate(accounts):
        if account.get("id") != account_id:
            continue
        updated = {**account, **updates}
        updated["updatedAt"] = datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        accounts[index] = updated
        save_accounts(accounts)
        return updated
    return None


def delete_account(account_id: str) -> bool:
    accounts = load_accounts()
    filtered = [account for account in accounts if account.get("id") != account_id]
    if len(filtered) == len(accounts):
        return False
    save_accounts(filtered)
    return True


def get_account(account_id: str) -> dict[str, Any] | None:
    return next(
        (account for account in load_accounts() if account.get("id") == account_id),
        None,
    )


def clean_totp_secret(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def validate_totp_secret(secret: str) -> bool:
    try:
        pyotp.TOTP(clean_totp_secret(secret)).now()
        return True
    except Exception:
        return False


def generate_totp(secret: str) -> str:
    try:
        return pyotp.TOTP(clean_totp_secret(secret)).now()
    except Exception as exc:
        raise ValueError("مفتاح المصادقة الثنائية غير صالح") from exc


def remaining_seconds() -> int:
    return 30 - (int(time.time()) % 30)


async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await send_main_menu(
        update,
        context,
        "👋 مرحباً بك في بوت إدارة الحسابات\n\n"
        "🔒 يتم حفظ البيانات في ملف التخزين المحلي للبوت.",
    )


async def menu_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    await send_main_menu(update, context)


async def start_add_flow(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    state = session_for(update)
    state.step = "add_email"
    state.pending_account = {}
    await update.effective_message.reply_text(
        "📝 *إضافة حساب جديد*\n\nالخطوة 1/4 — أرسل الإيميل:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_keyboard(),
    )


async def finish_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = session_for(update)
    pending = state.pending_account
    account = add_account(
        {
            "email": pending["email"],
            "password": pending["password"],
            "totpSecret": pending["totpSecret"],
            "recoveryCodes": pending.get("recoveryCodes", []),
            "appPassword": pending.get("appPassword", ""),
        }
    )
    await update.effective_message.reply_text(
        f"✅ *تم حفظ الحساب بنجاح!*\n\n📧 الإيميل: `{account['email']}`",
        parse_mode=ParseMode.MARKDOWN,
    )
    await send_main_menu(update, context)


async def handle_add_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    state = session_for(update)
    pending = state.pending_account

    if state.step == "add_email":
        pending["email"] = text
        state.step = "add_password"
        await update.effective_message.reply_text(
            "🔑 الخطوة 2/4 — أرسل كلمة المرور:",
            reply_markup=cancel_keyboard(),
        )
    elif state.step == "add_password":
        pending["password"] = text
        state.step = "add_totp"
        await update.effective_message.reply_text(
            "🔐 الخطوة 3/4 — أرسل مفتاح المصادقة الثنائية *(Secret Key)*\n\n"
            "_هو المفتاح الذي تحصل عليه عند إعداد التحقق بخطوتين، وليس الكود المؤقت_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=cancel_keyboard(),
        )
    elif state.step == "add_totp":
        secret = clean_totp_secret(text)
        if not validate_totp_secret(secret):
            await update.effective_message.reply_text(
                "⚠️ المفتاح غير صالح، حاول مرة أخرى أو تأكد من نسخه بشكل صحيح."
            )
            return
        pending["totpSecret"] = secret
        state.step = "add_appPassword"
        await update.effective_message.reply_text(
            f"✅ مفتاح المصادقة صالح!\n\n"
            f"🔢 *الكود الحالي:* `{generate_totp(secret)}`\n"
            f"⏱ ينتهي خلال {remaining_seconds()} ثانية\n\n"
            "🗝 الخطوة 4/4 — أرسل كلمة مرور التطبيق *(App Password)*:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard(
                [("⏭ تخطي", "skip_app_password"), ("❌ إلغاء", "cancel")]
            ),
        )
    elif state.step == "add_appPassword":
        pending["appPassword"] = text
        await finish_add(update, context)


async def handle_skip_app_password(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    session_for(update).pending_account["appPassword"] = ""
    await finish_add(update, context)


async def handle_view_all(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    accounts = load_accounts()
    if not accounts:
        await update.effective_message.reply_text(
            "📭 لا توجد حسابات محفوظة.",
            reply_markup=keyboard([("◀️ رجوع", "main_menu")]),
        )
        return
    for account in accounts:
        recovery = ", ".join(account.get("recoveryCodes", [])) or "لا يوجد"
        app_password = account.get("appPassword") or "لا يوجد"
        message = (
            f"📧 `{account.get('email', '')}`\n"
            f"🔑 `{account.get('password', '')}`\n"
            f"🔐 `{account.get('totpSecret', '')}`\n"
            f"📋 `{recovery}`\n"
            f"🗝 `{app_password}`"
        )
        await update.effective_message.reply_text(
            message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard(
                [
                    ("🔢 كود المصادقة", f"get_code:{account['id']}"),
                    ("✏️ تعديل", f"edit:{account['id']}"),
                    ("🗑 حذف", f"delete:{account['id']}"),
                ]
            ),
        )
    await update.effective_message.reply_text(
        f"📊 إجمالي الحسابات: *{len(accounts)}*\n\n"
        "الصيغة: إيميل | كلمة مرور | مفتاح 2FA | أكواد استرداد | كلمة مرور التطبيق",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard([("◀️ رجوع", "main_menu")]),
    )


async def handle_get_code(
    update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: str
) -> None:
    query = update.callback_query
    account = get_account(account_id)
    if not account:
        await query.answer("⚠️ الحساب غير موجود")
        return
    try:
        code = generate_totp(account["totpSecret"])
    except ValueError:
        await query.answer("❌ مفتاح المصادقة غير صالح")
        return
    await query.answer()
    await query.message.reply_text(
        f"🔢 *كود المصادقة لـ* `{account['email']}`\n\n"
        f"`{code}`\n\n"
        f"⏱ ينتهي خلال *{remaining_seconds()}* ثانية",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_edit_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    accounts = load_accounts()
    if not accounts:
        await update.effective_message.reply_text(
            "📭 لا توجد حسابات.",
            reply_markup=keyboard([("◀️ رجوع", "main_menu")]),
        )
        return
    rows = [[(f"📧 {account['email']}", f"edit:{account['id']}")] for account in accounts]
    rows.append([("◀️ رجوع", "main_menu")])
    await update.effective_message.reply_text(
        "✏️ اختر الحساب الذي تريد تعديله:", reply_markup=keyboard(*rows)
    )


async def handle_edit_account(
    update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: str
) -> None:
    query = update.callback_query
    account = get_account(account_id)
    if not account:
        await query.answer("⚠️ الحساب غير موجود")
        return
    await query.answer()
    rows = [[(label, f"edit_field:{account_id}:{field}")] for field, label in FIELD_LABELS.items()]
    rows.extend(
        [
            [("🗑 حذف الحساب", f"delete:{account_id}")],
            [("◀️ رجوع", "edit_list")],
        ]
    )
    await query.message.reply_text(
        f"✏️ تعديل الحساب: `{account['email']}`\n\nاختر الحقل المراد تعديله:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard(*rows),
    )


async def handle_edit_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: str, field: str
) -> None:
    query = update.callback_query
    await query.answer()
    state = session_for(update)
    state.step = "edit_entering_value"
    state.editing_id = account_id
    state.edit_field = field
    hint = ""
    if field == "recoveryCodes":
        hint = "\n_أرسلها مفصولة بفاصلة أو سطر جديد_"
    if field == "totpSecret":
        hint = "\n_أرسل المفتاح وليس الكود المؤقت_"
    await query.message.reply_text(
        f"✏️ أرسل القيمة الجديدة لـ *{FIELD_LABELS.get(field, field)}*:{hint}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_keyboard(),
    )


async def handle_edit_value_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    state = session_for(update)
    if not state.editing_id or not state.edit_field:
        return
    value: Any = text
    if state.edit_field == "totpSecret":
        value = clean_totp_secret(text)
        if not validate_totp_secret(value):
            await update.effective_message.reply_text(
                "⚠️ المفتاح غير صالح، حاول مرة أخرى."
            )
            return
    elif state.edit_field == "recoveryCodes":
        value = [item.strip() for item in re.split(r"[\n,،]+", text) if item.strip()]
    updated = update_account(state.editing_id, {state.edit_field: value})
    if not updated:
        await update.effective_message.reply_text("⚠️ لم يتم العثور على الحساب.")
    else:
        await update.effective_message.reply_text(
            f"✅ تم تحديث *{FIELD_LABELS.get(state.edit_field, state.edit_field)}* بنجاح.",
            parse_mode=ParseMode.MARKDOWN,
        )
    await send_main_menu(update, context)


async def handle_delete_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: str
) -> None:
    query = update.callback_query
    account = get_account(account_id)
    if not account:
        await query.answer("⚠️ الحساب غير موجود")
        return
    await query.answer()
    await query.message.reply_text(
        f"⚠️ هل تريد حذف حساب `{account['email']}`؟",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard(
            [
                ("✅ نعم، احذف", f"delete_yes:{account_id}"),
                ("❌ إلغاء", "main_menu"),
            ]
        ),
    )


async def handle_delete_yes(
    update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: str
) -> None:
    query = update.callback_query
    await query.answer()
    if delete_account(account_id):
        await query.message.reply_text("🗑 تم حذف الحساب بنجاح.")
    else:
        await query.message.reply_text("⚠️ لم يتم العثور على الحساب.")
    await send_main_menu(update, context)


def verify_email_credentials_sync(email: str, app_password: str) -> tuple[bool, str]:
    domain = email.split("@", 1)[1].lower() if "@" in email else ""
    config = SMTP_CONFIGS.get(domain)
    if not config:
        return False, f"⚠️ نوع البريد غير مدعوم للتحقق التلقائي\n({domain})"
    host, port, secure = config
    try:
        if secure:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                host, port, timeout=10, context=ssl.create_default_context()
            )
        else:
            server = smtplib.SMTP(host, port, timeout=10)
            server.starttls(context=ssl.create_default_context())
        with server:
            server.login(email, app_password)
        return True, "✅ بيانات الاعتماد صحيحة"
    except smtplib.SMTPAuthenticationError:
        return False, "❌ إيميل أو كلمة مرور التطبيق غير صحيحة"
    except Exception as exc:
        return False, f"❌ فشل الاتصال: {exc}"


async def handle_verify_list(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    accounts = load_accounts()
    if not accounts:
        await update.effective_message.reply_text(
            "📭 لا توجد حسابات.",
            reply_markup=keyboard([("◀️ رجوع", "main_menu")]),
        )
        return
    rows = [[(f"📧 {account['email']}", f"verify:{account['id']}")] for account in accounts]
    rows.append([("◀️ رجوع", "main_menu")])
    await update.effective_message.reply_text(
        "✅ اختر الحساب للتحقق منه:", reply_markup=keyboard(*rows)
    )


async def handle_verify_account(
    update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: str
) -> None:
    query = update.callback_query
    account = get_account(account_id)
    if not account:
        await query.answer("⚠️ الحساب غير موجود")
        return
    await query.answer("⏳ جاري التحقق...")
    loading = await query.message.reply_text(
        f"🔄 جاري التحقق من `{account['email']}`...",
        parse_mode=ParseMode.MARKDOWN,
    )
    _, result = await asyncio.to_thread(
        verify_email_credentials_sync, account["email"], account.get("appPassword", "")
    )
    await loading.edit_text(
        f"📧 *{account['email']}*\n\n{result}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard([("◀️ رجوع", "main_menu")]),
    )


async def callback_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    data = query.data or ""

    if data == "main_menu":
        await query.answer()
        await send_main_menu(update, context)
    elif data == "add_account":
        await query.answer()
        await start_add_flow(update, context)
    elif data == "cancel":
        await query.answer()
        await send_main_menu(update, context, "❌ تم الإلغاء")
    elif data == "skip_app_password":
        await handle_skip_app_password(update, context)
    elif data in {"view_all", "edit_list", "verify_list"}:
        if await reject_non_owner(update, query):
            return
        await query.answer()
        if data == "view_all":
            await handle_view_all(update, context)
        elif data == "edit_list":
            await handle_edit_list(update, context)
        else:
            await handle_verify_list(update, context)
    else:
        match = re.fullmatch(r"get_code:(.+)", data)
        if match:
            if not await reject_non_owner(update, query):
                await handle_get_code(update, context, match.group(1))
            return
        match = re.fullmatch(r"edit:(.+)", data)
        if match:
            if not await reject_non_owner(update, query):
                await handle_edit_account(update, context, match.group(1))
            return
        match = re.fullmatch(r"edit_field:([^:]+):(.+)", data)
        if match:
            if not await reject_non_owner(update, query):
                await handle_edit_field(
                    update, context, match.group(1), match.group(2)
                )
            return
        match = re.fullmatch(r"delete:(.+)", data)
        if match:
            if not await reject_non_owner(update, query):
                await handle_delete_confirm(update, context, match.group(1))
            return
        match = re.fullmatch(r"delete_yes:(.+)", data)
        if match:
            if not await reject_non_owner(update, query):
                await handle_delete_yes(update, context, match.group(1))
            return
        match = re.fullmatch(r"verify:(.+)", data)
        if match:
            if not await reject_non_owner(update, query):
                await handle_verify_account(update, context, match.group(1))


async def text_router(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    state = session_for(update)
    if not state.step:
        await send_main_menu(update, context)
    elif state.step in {
        "add_email",
        "add_password",
        "add_totp",
        "add_appPassword",
    }:
        await handle_add_step(update, context)
    elif state.step == "edit_entering_value":
        await handle_edit_value_input(update, context)


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logger.exception("Bot error while handling update", exc_info=context.error)


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set")
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_router)
    )
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    application = build_application()
    logger.info("Starting bot...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()