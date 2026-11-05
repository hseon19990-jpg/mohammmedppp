"""التطوع لأصبح أدمن: شروط قابلة للتعديل من المالك + طلبات تُراجَع من المالك."""

from telegram_bot.core import *

# ==================== SETTINGS ====================
VOLUNTEER_DB = DATA_DIR / "admin_volunteer_requests.json"

# الشروط الافتراضية (يعدلها المالك من لوحة التحكم)
VOLUNTEER_DEFAULTS = {
    "invites": 50,         # عدد الأشخاص المدعوين للبوت
    "active_invites": 10,  # عدد المدعوين الذين باعوا إيميلاً وتم قبوله
    "emails": 10,          # عدد الإيميلات المقبولة التي باعها العضو نفسه
}

VOLUNTEER_LABELS = {
    "invites": "👥 عدد المدعوين",
    "active_invites": "✅ مدعوون باعوا إيميلاً مقبولاً",
    "emails": "📧 إيميلاتك المباعة",
}

_CANCEL_WORDS = {"إلغاء", "الغاء", "cancel", "отмена"}


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_volunteer_config() -> dict:
    raw = load_config().get("admin_volunteer", {})
    if not isinstance(raw, dict):
        raw = {}
    result = {}
    for key, default in VOLUNTEER_DEFAULTS.items():
        value = _to_int(raw.get(key), default)
        result[key] = value if value >= 0 else default
    return result


def get_volunteer_progress(user_id: int) -> dict:
    """يحسب تقدم العضو في الشروط الثلاثة من بيانات المستخدمين."""
    users = load_json(USERS_DB)
    invited = 0
    active = 0
    for data in users.values():
        if not isinstance(data, dict):
            continue
        if _to_int(data.get("referred_by")) != user_id:
            continue
        invited += 1
        if _to_int(data.get("total_approved_emails")) >= 1:
            active += 1
    own = users.get(str(user_id), {})
    sold = _to_int(own.get("total_approved_emails")) if isinstance(own, dict) else 0
    return {"invites": invited, "active_invites": active, "emails": sold}


def volunteer_conditions_met(progress: dict, cfg: dict) -> bool:
    return all(progress.get(key, 0) >= cfg[key] for key in VOLUNTEER_DEFAULTS)


def _mark(done: bool) -> str:
    return "✅" if done else "❌"


def _conditions_text(progress: dict, cfg: dict) -> str:
    return (
        "<b>الشروط:</b>\n\n"
        f"1- دعوة {cfg['invites']} شخص للبوت "
        f"({progress['invites']}/{cfg['invites']}) {_mark(progress['invites'] >= cfg['invites'])}\n\n"
        f"2- {cfg['active_invites']} أشخاص ممن دعوتهم يقومون ببيع إيميل ويتم قبوله "
        f"({progress['active_invites']}/{cfg['active_invites']}) "
        f"{_mark(progress['active_invites'] >= cfg['active_invites'])}\n\n"
        f"3- أن تكون قد بعت {cfg['emails']} إيميلات كحد أدنى "
        f"({progress['emails']}/{cfg['emails']}) {_mark(progress['emails'] >= cfg['emails'])}"
    )


# ==================== REQUESTS STORAGE ====================
def _load_requests() -> dict:
    return load_json(VOLUNTEER_DB)


def _save_requests(data: dict):
    save_json(VOLUNTEER_DB, data)


def _pending_requests() -> dict:
    return {uid: r for uid, r in _load_requests().items()
            if isinstance(r, dict) and r.get("status") == "pending"}


async def _owner_only(update: Update) -> bool:
    if update.effective_user.id == OWNER_ID:
        return True
    try:
        await update.callback_query.answer("🚫 مالك فقط.", show_alert=True)
    except Exception:
        pass
    return False


# ==================== MEMBER SCREENS ====================
async def volunteer_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    if uid == OWNER_ID or is_admin(uid):
        await query.edit_message_text(
            "ℹ️ أنت أدمن بالفعل.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    cfg = get_volunteer_config()
    progress = get_volunteer_progress(uid)
    text = (
        "🙋 <b>التطوع لتصبح أدمن</b>\n\n"
        "لكي تصبح أدمن يجب عليك إكمال الشروط اللازمة.\n\n"
        + _conditions_text(progress, cfg)
    )
    buttons = [
        ("ℹ️ فوائد الأدمن", "volunteer_benefits"),
        ("📨 طلب أن أصبح أدمن", "volunteer_request"),
        ("🔙 القائمة الرئيسية", "main_menu"),
    ]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=kb_vertical(buttons))


async def volunteer_benefits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    text = (
        "👋 <b>مرحباً بك</b>\n\n"
        "هل تريد أن تكون أدمن؟\n\n"
        "<b>فوائد الأدمن هي:</b>\n"
        "• أن تكون أحد مسؤولي البوت\n"
        "• تحصل على قسم من أرباح البوت\n"
        "• قد تصل أرباحك يومياً إلى 5$\n"
        "• ساعات عمل قليلة\n"
        "• مشاكلك ستكون مع مدير البوت مباشرة وليس مع المشرفين\n"
        "• صلاحيات أخرى قد تتم إضافتها مستقبلاً"
    )
    buttons = [
        ("📨 طلب أن أصبح أدمن", "volunteer_request"),
        ("🔙 رجوع", "volunteer_menu"),
    ]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=kb_vertical(buttons))


async def volunteer_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    uid = user.id
    back = kb_single("🔙 رجوع", "volunteer_menu")

    if uid == OWNER_ID or is_admin(uid):
        await query.edit_message_text("ℹ️ أنت أدمن بالفعل.", reply_markup=back)
        return

    requests = _load_requests()
    existing = requests.get(str(uid))
    if isinstance(existing, dict) and existing.get("status") == "pending":
        await query.edit_message_text(
            "⏳ طلبك وصل إلى المالك وهو قيد المراجعة، يرجى الانتظار.",
            reply_markup=back)
        return

    cfg = get_volunteer_config()
    progress = get_volunteer_progress(uid)

    if not volunteer_conditions_met(progress, cfg):
        await query.edit_message_text(
            "⚠️ <b>أكمل الشروط أولاً</b>\n\n" + _conditions_text(progress, cfg),
            parse_mode=ParseMode.HTML, reply_markup=back)
        return

    requests[str(uid)] = {
        "status": "pending",
        "at": datetime.now(timezone.utc).isoformat(),
        "username": user.username or "",
        "name": user.full_name or "",
        "progress": progress,
    }
    _save_requests(requests)

    await query.edit_message_text(
        "✅ <b>تم وصول طلبك إلى المالك ليراجع به.</b>\n\nسيصلك إشعار بالقرار.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))

    username = f"@{tg_html_escape(user.username)}" if user.username else "—"
    owner_text = (
        "🙋 <b>طلب جديد: عضو يريد أن يصبح أدمن</b>\n\n"
        f"👤 الاسم: {tg_html_escape(user.full_name or '—')}\n"
        f"🔗 المعرف: {username}\n"
        f"🆔 ID: <code>{uid}</code>\n\n"
        f"👥 المدعوون: <b>{progress['invites']}</b>\n"
        f"✅ مدعوون باعوا إيميلاً مقبولاً: <b>{progress['active_invites']}</b>\n"
        f"📧 إيميلاته المباعة: <b>{progress['emails']}</b>"
    )
    buttons = [
        ("✅ قبول", f"volunteer_approve:{uid}"),
        ("❌ رفض", f"volunteer_reject:{uid}"),
    ]
    try:
        await context.bot.send_message(
            chat_id=OWNER_ID, text=owner_text, parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical(buttons))
    except Exception:
        logger.exception("Could not notify owner about volunteer request %s", uid)


# ==================== OWNER REVIEW ====================
def _parse_target(data: str) -> Optional[int]:
    try:
        return int(data.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


async def volunteer_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    query = update.callback_query
    target = _parse_target(query.data)
    back = kb_single("🔙 طلبات التطوع", "volunteer_pending_list")
    requests = _load_requests()
    req = requests.get(str(target)) if target else None
    if not isinstance(req, dict) or req.get("status") != "pending":
        await query.edit_message_text("ℹ️ هذا الطلب تمت معالجته مسبقاً.", reply_markup=back)
        return

    admins = load_admins()
    admins.add(target)
    save_admins(admins)
    ensure_admin_activity_tracked(target)

    req["status"] = "approved"
    req["decided_at"] = datetime.now(timezone.utc).isoformat()
    requests[str(target)] = req
    _save_requests(requests)

    await query.edit_message_text(
        f"✅ تم قبول الطلب، والعضو <code>{target}</code> أصبح أدمن.",
        parse_mode=ParseMode.HTML, reply_markup=back)
    try:
        await context.bot.send_message(
            chat_id=target,
            text="🎉 <b>مبروك! تم قبول طلبك وأصبحت أدمن.</b>\n\nاستخدم /start لعرض لوحة الإدارة.",
            parse_mode=ParseMode.HTML)
    except Exception:
        pass


async def volunteer_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    query = update.callback_query
    target = _parse_target(query.data)
    back = kb_single("🔙 طلبات التطوع", "volunteer_pending_list")
    requests = _load_requests()
    req = requests.get(str(target)) if target else None
    if not isinstance(req, dict) or req.get("status") != "pending":
        await query.edit_message_text("ℹ️ هذا الطلب تمت معالجته مسبقاً.", reply_markup=back)
        return

    req["status"] = "rejected"
    req["decided_at"] = datetime.now(timezone.utc).isoformat()
    requests[str(target)] = req
    _save_requests(requests)

    await query.edit_message_text(
        f"❌ تم رفض طلب العضو <code>{target}</code>.",
        parse_mode=ParseMode.HTML, reply_markup=back)
    try:
        await context.bot.send_message(
            chat_id=target,
            text="❌ نأسف، لم يتم قبول طلبك لتصبح أدمن حالياً. يمكنك التقديم مرة أخرى لاحقاً.")
    except Exception:
        pass


async def volunteer_pending_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    query = update.callback_query
    pending = _pending_requests()
    back = kb_single("🔙 إعدادات التطوع", "volunteer_settings")
    if not pending:
        await query.edit_message_text("📭 لا توجد طلبات تطوع معلّقة.", reply_markup=back)
        return
    lines = ["📥 <b>طلبات التطوع المعلّقة</b>", ""]
    buttons = []
    for uid, req in list(pending.items())[:10]:
        prog = req.get("progress", {}) if isinstance(req.get("progress"), dict) else {}
        name = tg_html_escape(req.get("name") or "—")
        lines.append(
            f"• <code>{uid}</code> — {name}\n"
            f"   👥 {prog.get('invites', 0)} | ✅ {prog.get('active_invites', 0)} | "
            f"📧 {prog.get('emails', 0)}")
        buttons.append((f"✅ قبول {uid}", f"volunteer_approve:{uid}"))
        buttons.append((f"❌ رفض {uid}", f"volunteer_reject:{uid}"))
    buttons.append(("🔙 إعدادات التطوع", "volunteer_settings"))
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                  reply_markup=kb_vertical(buttons))


# ==================== OWNER SETTINGS ====================
async def volunteer_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    query = update.callback_query
    context.user_data.pop("mode", None)
    context.user_data.pop("volunteer_key", None)
    cfg = get_volunteer_config()
    pending_count = len(_pending_requests())
    text = (
        "🙋 <b>إعدادات التطوع للأدمن</b>\n\n"
        "<b>الشروط الحالية:</b>\n"
        f"1- دعوة <code>{cfg['invites']}</code> شخص\n"
        f"2- <code>{cfg['active_invites']}</code> من المدعوين باعوا إيميلاً وتم قبوله\n"
        f"3- بيع <code>{cfg['emails']}</code> إيميلات مقبولة\n\n"
        f"📥 الطلبات المعلّقة: <b>{pending_count}</b>\n\n"
        "اضغط على شرط لتغيير رقمه."
    )
    buttons = [
        (f"📥 طلبات التطوع ({pending_count})", "volunteer_pending_list"),
        (f"{VOLUNTEER_LABELS['invites']}: {cfg['invites']}", "volunteer_set:invites"),
        (f"{VOLUNTEER_LABELS['active_invites']}: {cfg['active_invites']}", "volunteer_set:active_invites"),
        (f"{VOLUNTEER_LABELS['emails']}: {cfg['emails']}", "volunteer_set:emails"),
        ("🔙 إعدادات المالك", "owner_panel"),
    ]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=kb_vertical(buttons))


async def volunteer_set_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update):
        return
    query = update.callback_query
    key = query.data.split(":", 1)[1] if ":" in query.data else ""
    if key not in VOLUNTEER_DEFAULTS:
        return
    context.user_data["mode"] = "volunteer_set_value"
    context.user_data["volunteer_key"] = key
    cfg = get_volunteer_config()
    await query.edit_message_text(
        f"{VOLUNTEER_LABELS[key]}\n\n"
        f"📌 الحالي: <code>{cfg[key]}</code>\n\n"
        "أرسل الرقم الجديد (مثال: <code>200</code>)\n"
        "<i>أو 'إلغاء' للعودة.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إلغاء", "volunteer_settings"))


async def handle_volunteer_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        context.user_data.pop("mode", None)
        context.user_data.pop("volunteer_key", None)
        return
    text = update.message.text.strip()
    back = kb_single("🔙 إعدادات التطوع", "volunteer_settings")

    if text.casefold() in _CANCEL_WORDS:
        context.user_data.pop("mode", None)
        context.user_data.pop("volunteer_key", None)
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=back)
        return

    key = context.user_data.get("volunteer_key")
    if key not in VOLUNTEER_DEFAULTS:
        context.user_data.pop("mode", None)
        return
    if not text.isdigit() or int(text) > 1_000_000:
        await update.message.reply_text("⚠️ أرسل عدداً صحيحاً (0 أو أكبر).")
        return

    value = int(text)
    config = load_config()
    section = config.get("admin_volunteer", {})
    if not isinstance(section, dict):
        section = {}
    section[key] = value
    config["admin_volunteer"] = section
    save_config(config)

    context.user_data.pop("mode", None)
    context.user_data.pop("volunteer_key", None)
    await update.message.reply_text(
        f"✅ تم تحديث «{VOLUNTEER_LABELS[key]}» إلى <code>{value}</code>.",
        parse_mode=ParseMode.HTML, reply_markup=back)


__all__ = [name for name in globals() if not name.startswith("__")]
