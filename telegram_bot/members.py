"""Member-facing menus, account workflows, and leave checks."""

from telegram_bot.core import *


# ==================== MAIN MENU ====================
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_access_allowed(update):
        return
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    SESSIONS.pop(update.effective_user.id, None)
    save_sessions()
    user = update.effective_user
    uid = user.id
    user_data = get_user(uid)
    ban_msg = get_active_ban_message(user_data)
    restriction_msg = get_active_restriction_message(user_data)
    prefix = ""
    if ban_msg:
        prefix = ban_msg + "\n\n"
    elif restriction_msg:
        prefix = restriction_msg + "\n\n"

    prices_old = get_tier_prices("old")
    prices_new = get_tier_prices("new")
    welcome_text = t(uid, "welcome",
                     old_price=prices_old["tier_3"],
                     new_price=prices_new["tier_3"])

    buttons = [
        (t(uid, "btn_sell_old"), "add_account"),
        (t(uid, "btn_sell_new"), "sell_new_email"),
        (t(uid, "btn_wallet"), "my_wallet"),
    ]
    gift_config = get_daily_gift_config()
    if gift_config["enabled"]:
        buttons.append(("🎁 الهدية اليومية", "daily_gift_view"))
    buttons.extend([
        (t(uid, "btn_transactions"), "my_transactions"),
        (t(uid, "btn_my_accounts"), "my_accounts"),
        (t(uid, "btn_top_sellers"), "top_sellers"),
        (t(uid, "btn_rejected"), "rejected_emails"),
        (t(uid, "btn_tutorials"), "tutorials"),
        (t(uid, "btn_store"), "withdraw_store"),
        (t(uid, "btn_referral"), "referral_menu"),
        (t(uid, "btn_edit_accounts"), "edit_my_accounts"),
        (t(uid, "btn_language"), "language_menu"),
    ])
    if is_admin(uid):
        buttons.append((t(uid, "btn_admin"), "admin_panel"))
    if uid == OWNER_ID:
        buttons.append((t(uid, "btn_owner"), "owner_panel"))

    text = prefix + welcome_text
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML,
                reply_markup=member_menu_keyboard(buttons))
        except Exception:
            await update.callback_query.message.reply_text(
                text, parse_mode=ParseMode.HTML,
                reply_markup=member_menu_keyboard(buttons))
    else:
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=member_menu_keyboard(buttons))


# ==================== DAILY GIFT VIEW ====================
async def daily_gift_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    gift = get_daily_gift_config()
    if not gift["enabled"]:
        await query.edit_message_text(
            "🎁 الهدية اليومية غير مفعلة حالياً.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    user_data = get_user(uid)
    progress = check_and_reset_daily_gift_progress(user_data)
    save_user(uid, user_data)
    count = int(progress.get("count", 0))
    claimed = progress.get("claimed", False)
    required = gift["emails_required"]
    reward = gift["reward"]
    remaining = max(0, required - count)

    if claimed:
        text = (f"🎁 <b>الهدية اليومية</b>\n\n"
                f"✅ <b>حصلت على هدية اليوم!</b>\n"
                f"💰 المكافأة: <b>${reward:.2f}</b>\n\n"
                f"📌 تتجدد الهدية تلقائياً في اليوم التالي.")
    else:
        progress_pct = int(count * 100 / required) if required > 0 else 0
        text = (f"🎁 <b>الهدية اليومية</b>\n\n"
                f"📧 <b>الهدف:</b> أكمل <b>{required}</b> إيميل\n"
                f"✅ <b>أكملت اليوم:</b> <b>{count}</b> إيميل\n"
                f"⏳ <b>المتبقي:</b> <b>{remaining}</b> إيميل\n"
                f"💰 <b>المكافأة:</b> <b>${reward:.2f}</b>\n\n"
                f"📊 التقدم: {progress_pct}%\n\n"
                f"<i>يُحتسب التقدم فقط بعد تحويل نقاط كل إيميل إلى رصيدك.</i>")

    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


# ==================== LANGUAGE MENU ====================
async def language_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    text = t(uid, "choose_language")
    buttons = [
        ("🇮🇶 العربية", "set_language:ar"),
        ("🇬🇧 English", "set_language:en"),
        ("🇷🇺 Русский", "set_language:ru"),
        (t(uid, "main_menu"), "main_menu"),
    ]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=kb_vertical(buttons))


async def set_language_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    parts = query.data.split(":", 1)
    lang = parts[1] if len(parts) > 1 else "ar"
    set_user_language(uid, lang)
    await query.answer(t(uid, "language_set"), show_alert=True)
    await main_menu(update, context)


# ==================== TOP SELLERS ====================
async def top_sellers_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    top = compute_top_sellers(limit=10)
    back_btn = (("🔙 إعدادات المالك", "owner_panel") if (
        uid == OWNER_ID and query.data == "top_sellers_owner"
    ) else (t(uid, "main_menu"), "main_menu"))
    if not top:
        await query.edit_message_text(
            t(uid, "top_sellers_empty"),
            reply_markup=kb_vertical([back_btn]))
        return
    lines = [t(uid, "top_sellers_title"), ""]
    for idx, item in enumerate(top, 1):
        lines.append(t(uid, "top_sellers_item",
                       idx=idx, uid=item['user_id'], count=item['total']))
    await query.edit_message_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([back_btn]))


# ==================== TRANSACTIONS ====================
async def my_transactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    user_data = get_user(uid)
    transactions = user_data.get("transactions", [])
    if not transactions:
        await query.edit_message_text(t(uid, "transactions_empty"),
                                      reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    kind_icons = {"credit": "➕", "debit": "➖", "hold": "🔒", "release": "🔓",
                  "referral": "🎁", "purchase": "🛒", "admin_bonus": "🛠",
                  "admin_bonus_pending": "⏳", "admin_bonus_release": "✅",
                  "approved_rejection": "❌", "admin_bonus_reversal": "↩️"}
    lines = [t(uid, "transactions_title"), ""]
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
                                  reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))


# ==================== MY ACCOUNTS ====================
async def my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    user_data = get_user(uid)
    approved = sort_records_newest_first(user_data.get("approved_accounts", []) or [])
    pending = sort_records_oldest_first(user_data.get("pending_requests", []) or [])
    rejected = sort_records_newest_first(user_data.get("rejected_requests", []) or [])
    if not approved and not pending and not rejected:
        await query.edit_message_text(t(uid, "accounts_empty"),
                                      reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    msg = t(uid, "accounts_title") + "\n\n"
    if approved:
        msg += t(uid, "accounts_approved_header") + "\n"
        for idx, acc in enumerate(approved, 1):
            leave_status = ""
            if acc.get("rejected_at_24h"):
                leave_status = t(uid, "accounts_leave_rejected")
            elif acc.get("approved_with_leave", False) and not acc.get("leave_confirmed", False):
                leave_status = t(uid, "accounts_leave_hold")
            elif acc.get("approved_with_leave", False) and acc.get("leave_confirmed", False):
                leave_status = t(uid, "accounts_leave_done")
            msg += f"  {idx}. 📧 `{acc.get('email', '')}` ✅{leave_status}\n"
        msg += "\n"
    if pending:
        msg += t(uid, "accounts_pending_header") + "\n"
        for idx, req in enumerate(pending, 1):
            msg += f"  {idx}. 📧 `{req.get('email', '')}` ⏳\n"
        msg += "\n"
    if rejected:
        msg += t(uid, "accounts_rejected_header") + "\n"
        for idx, rej in enumerate(rejected, 1):
            reason = rej.get('reject_reason', 'unknown')
            reason_text = REJECT_REASON_LABELS.get(reason, reason)
            msg += f"  {idx}. 📧 `{rej.get('email', '')}` ❌ - {reason_text}\n"
        msg += "\n"
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))


# ==================== REJECTED EMAILS ====================
async def view_member_rejected_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    user_data = get_user(uid)
    rejected = sort_records_newest_first(user_data.get("rejected_requests", []) or [])
    if not rejected:
        rejected = [{"email": email, "reject_reason": "unknown"}
                    for email in (user_data.get("rejected_emails", []) or [])]
    if not rejected:
        await query.edit_message_text(t(uid, "rejected_empty"),
                                      reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    reason_map = {
        "email": "الإيميل غير صحيح", "password": "كلمة المرور غير صحيحة",
        "totp": "رمز المصادقة غير صحيح", "app_pass": "كلمة مرور التطبيق غير صحيحة",
        "phone": "يحتاج رقم هاتف", "other": "سبب آخر", "custom": "سبب مخصص",
        "unknown": "غير معروف", "imap_failed_24h": "فشل الفحص الثاني (24 ساعة)",
    }
    lines = [t(uid, "rejected_title"), ""]
    for index, request in enumerate(rejected, 1):
        email = tg_html_escape(str(request.get("email", "غير معروف")))
        password = tg_html_escape(str(request.get("password", "") or "")) or "❌"
        totp = tg_html_escape(str(request.get("totp", "") or "")) or "❌"
        app_pass = tg_html_escape(str(request.get("app_pass", "") or "")) or "❌"
        reason = request.get("reject_reason", "unknown")
        reason_text = request.get("reject_reason_text") or reason_map.get(reason, str(reason))
        lines.append(f"{index}. 📧 <code>{email}</code>")
        lines.append(f"   🔑 {password}")
        lines.append(f"   🔐 {totp}")
        lines.append(f"   🗝 {app_pass}")
        lines.append(f"   📝 {tg_html_escape(str(reason_text))}")
        if index < len(rejected):
            lines.append("")
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                  reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))


# ==================== EDIT MY ACCOUNTS ====================
async def edit_my_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    query = update.callback_query
    uid = query.from_user.id
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if not pending:
        await query.edit_message_text(t(uid, "edit_empty"),
                                      reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    buttons = []
    for idx, req in enumerate(pending):
        buttons.append((f"✏️ {req.get('email', '')}", f"edit_pending:{uid}:{idx}"))
    buttons.append((t(uid, "main_menu"), "main_menu"))
    await query.edit_message_text(t(uid, "edit_title"), parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def edit_pending_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    query = update.callback_query
    uid = query.from_user.id
    parts = query.data.split(":")
    target_uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text(t(uid, "edit_missing"),
                                      reply_markup=kb_single(t(uid, "edit_btn_back"), "edit_my_accounts"))
        return
    email = pending[index].get("email", "")
    context.user_data["editing_email"] = email
    context.user_data["editing_index"] = index
    buttons = [
        (t(uid, "edit_btn_password"), f"edit_field:password:{target_uid}:{index}"),
        (t(uid, "edit_btn_totp"), f"edit_field:totp:{target_uid}:{index}"),
        (t(uid, "edit_btn_app_pass"), f"edit_field:app_pass:{target_uid}:{index}"),
        (t(uid, "edit_btn_delete"), f"delete_pending:{target_uid}:{index}"),
        (t(uid, "edit_btn_back"), "edit_my_accounts"),
    ]
    await query.edit_message_text(
        t(uid, "edit_account_title", email=email),
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    query = update.callback_query
    uid = query.from_user.id
    parts = query.data.split(":")
    field = parts[1]
    target_uid = int(parts[2])
    index = int(parts[3])
    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text(t(uid, "edit_missing"),
                                      reply_markup=kb_single(t(uid, "edit_btn_back"), "edit_my_accounts"))
        return
    email = pending[index].get("email", "")
    context.user_data["editing_field"] = field
    context.user_data["editing_uid"] = target_uid
    context.user_data["editing_index"] = index
    field_names = {
        "password": t(uid, "edit_field_password"),
        "totp": t(uid, "edit_field_totp"),
        "app_pass": t(uid, "edit_field_app_pass"),
    }
    await query.edit_message_text(
        t(uid, "edit_prompt_value", field=field_names.get(field, field), email=email),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single(t(uid, "btn_cancel"), f"edit_pending:{target_uid}:{index}"))
    context.user_data["step"] = "editing_field"


async def delete_pending_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    query = update.callback_query
    uid = query.from_user.id
    parts = query.data.split(":")
    target_uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await query.edit_message_text(t(uid, "edit_missing"),
                                      reply_markup=kb_single(t(uid, "edit_btn_back"), "edit_my_accounts"))
        return
    request = pending[index]
    pending.pop(index)
    user_data["pending_requests"] = pending
    user_data["pending_balance"] = clamp_money(
        float(user_data.get("pending_balance", 0.0)) - float(request.get("amount", 0.0)))
    save_user(target_uid, user_data)
    await query.edit_message_text(
        t(uid, "edit_deleted", email=request.get('email', '')),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single(t(uid, "edit_btn_back"), "edit_my_accounts"))


async def handle_edit_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    field = context.user_data.get("editing_field")
    editing_uid = context.user_data.get("editing_uid")
    index = context.user_data.get("editing_index")
    if not field or editing_uid is None or index is None:
        await update.message.reply_text("⚠️ Error.")
        return
    user_data = get_user(editing_uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        clear_edit_state(context)
        await update.message.reply_text(t(uid, "edit_missing"))
        return
    pending[index][field] = text
    user_data["pending_requests"] = pending
    save_user(editing_uid, user_data)
    try:
        await update.message.delete()
    except Exception:
        pass
    field_names = {
        "password": t(uid, "edit_field_password"),
        "totp": t(uid, "edit_field_totp"),
        "app_pass": t(uid, "edit_field_app_pass"),
    }
    for key in ("editing_field", "editing_uid", "editing_index", "step"):
        context.user_data.pop(key, None)
    await update.message.reply_text(
        t(uid, "edit_success", field=field_names.get(field, field),
          email=pending[index].get('email', '')),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single(t(uid, "edit_btn_back"), "edit_my_accounts"))


# ==================== VERIFICATION WRAPPER (NEW email flow only) ====================
async def _verify_and_handle_failure(update: Update, context: ContextTypes.DEFAULT_TYPE, email: str) -> Tuple[str, Optional[bool], int, bool]:
    """
    Wrapper for strict existence check + 3-strike restriction.
    Used ONLY in the NEW email flow (bot-generated).
    Returns: (status, exists, remaining_attempts, restricted_now)
    """
    uid = update.effective_user.id
    status, exists = await verify_gmail_account_exists_strict(email, context=context)
    if status == "available":
        user_data = get_user(uid)
        remaining, restricted_now = apply_verification_failure(user_data)
        save_user(uid, user_data)
        return status, exists, remaining, restricted_now
    return status, exists, VERIFICATION_FAILURES_THRESHOLD, False


# ==================== ADD ACCOUNT FLOW (OLD EMAIL — NO RESTRICTIONS) ====================
async def add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء تدفق بيع الإيميل القديم — بلا حظر أو تقييد."""
    if not await check_forced_channel(update, context):
        return
    uid = update.effective_user.id
    # ═══ لا حظر ولا تقييد في الإيميل القديم — IMAP هو الفيصل ═══

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
                "reject_approved_uid", "reject_approved_token",
                "sell_new_email", "account_type"):
        context.user_data.pop(key, None)

    context.user_data["account_type"] = "old"
    SESSIONS[uid] = Session(step="email")
    save_sessions()
    config = load_config()
    prices = get_tier_prices("old")
    has_email_video = config.get("video_email") and Path(config.get("video_email", "")).exists()
    buttons = []
    if has_email_video:
        buttons.append((t(uid, "add_btn_video_email"), "show_video:email"))
    buttons.append((t(uid, "btn_cancel"), "cancel"))
    text = (
        f"{t(uid, 'add_old_title')}\n\n"
        f"{t(uid, 'add_old_pricing')}\n"
        f"{t(uid, 'add_old_tier1', price=prices['tier_1'])}\n"
        f"{t(uid, 'add_old_tier2', price=prices['tier_2'])}\n"
        f"{t(uid, 'add_old_tier3', price=prices['tier_3'])}\n\n"
        f"{t(uid, 'add_old_note')}\n\n"
        f"{t(uid, 'add_old_warning_steps')}\n\n"
        f"{t(uid, 'add_step1_email')}"
    )
    await update.callback_query.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


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
                                         caption="📹", parse_mode=ParseMode.MARKDOWN,
                                         supports_streaming=True)
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await query.edit_message_text("⚠️",
                                          reply_markup=kb_single(t(query.from_user.id, "btn_back"), "add_account"))
    else:
        await query.edit_message_text("⚠️",
                                      reply_markup=kb_single(t(query.from_user.id, "btn_back"), "add_account"))


async def add_account_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    SESSIONS.pop(uid, None)
    save_sessions()
    clear_edit_state(context)
    context.user_data.pop("account_type", None)
    await update.callback_query.edit_message_text(t(uid, "msg_cancelled"),
                                                  reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))



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
    prices = get_tier_prices("old")

    if session.step == "email":
        # ═══ لا حظر ولا تقييد — IMAP هو الفيصل ═══
        # ═══ لا فحص صيغة ولا DNS — نقبل أي نص، والتحقق عبر IMAP لاحقاً ═══
        first_line = text.splitlines()[0].strip() if text else ""
        if not first_line:
            await update.message.reply_text(t(uid, "add_invalid_email"))
            return
        email = first_line  # نحفظ النص كما هو — التحقق سيأتي في الخطوة 4

        # فحص التكرار فقط إذا كان يبدو كإيميل (يحتوي @)
        if "@" in email:
            active_status = get_active_account_status(email)
            if active_status == "approved":
                await update.message.reply_text(
                    t(uid, "add_email_approved"),
                    reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
                SESSIONS.pop(uid, None); save_sessions()
                return
            if active_status == "pending":
                user_data = get_user(uid)
                pending = user_data.get("pending_requests", [])
                old_amounts = [float(r.get("amount", 0.0)) for r in pending
                               if normalize_email(r.get("email", "")) == normalize_email(email)]
                new_pending = [r for r in pending
                               if normalize_email(r.get("email", "")) != normalize_email(email)]
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
            buttons.append((t(uid, "add_btn_video_password"), "show_video:password"))
        buttons.append((t(uid, "btn_cancel"), "cancel"))
        await update.message.reply_text(
            t(uid, "add_step2_password", price=prices['tier_3']),
            parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))

    elif session.step == "password":
        # ═══ لا IMAP هنا — أي باسورد يُقبل مبدئياً، التحقق في الخطوة 4 ═══
        if has_active_account_password(text):
            await update.message.reply_text(
                t(uid, "add_password_used"),
                reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
            SESSIONS.pop(uid, None); save_sessions()
            return
        session.password = text
        session.has_password = True
        session.step = "totp"
        save_sessions()
        await update.message.reply_text(
            t(uid, "add_step3_totp"), parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                (t(uid, "add_btn_video_totp"), "show_video:totp"),
                (t(uid, "btn_cancel"), "cancel"),
            ]))

    elif session.step == "totp":
        cleaned = text.replace(" ", "").upper()
        if len(cleaned) != 32 or not re.match(r'^[A-Z2-7]{32}$', cleaned):
            await update.message.reply_text(t(uid, "add_totp_invalid"))
            return
        try:
            code = pyotp.TOTP(cleaned).now()
        except Exception:
            await update.message.reply_text(t(uid, "add_totp_bad"))
            return

        try:
            await update.message.delete()
        except Exception:
            pass

        session.totp = cleaned
        session.has_totp = True
        session.step = "app_pass"
        save_sessions()

        await context.bot.send_message(
            chat_id=uid,
            text=t(uid, "add_totp_received_with_code",
                   code=code, price=prices["tier_3"]),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                (t(uid, "add_btn_video_app_pass"), "show_video:app_pass"),
                (t(uid, "btn_cancel"), "cancel"),
            ]))

    elif session.step == "app_pass":
        # ═══ التحقق الوحيد الفعلي — IMAP ═══
        cleaned = text.replace(" ", "")
        if len(cleaned) != 16 or not re.match(r'^[A-Za-z0-9]{16}$', cleaned):
            await update.message.reply_text(t(uid, "add_app_pass_invalid"))
            return
        if has_active_app_password(cleaned):
            await update.message.reply_text(t(uid, "add_app_pass_used"))
            return
        user_data = get_user(uid)
        pending = user_data.get("pending_requests", [])
        pending = [r for r in pending if normalize_email(r.get("email", "")) != normalize_email(session.email)]
        try:
            await update.message.delete()
        except Exception:
            pass
        allowed, wait = imap_rate_ok(session.email)
        if not allowed:
            await context.bot.send_message(
                chat_id=uid, text=t(uid, "add_rate_wait", wait=wait),
                parse_mode=ParseMode.HTML)
            return
        imap_rate_mark(session.email)
        verify_result = await verify_account_credentials(
            email=session.email, password=session.password,
            app_pass=cleaned, totp_secret=session.totp)
        if not verify_result["imap_ok"]:
            await context.bot.send_message(
                chat_id=uid,
                text=t(uid, "add_verify_failed",
                       email=tg_html_escape(session.email),
                       message=tg_html_escape(verify_result['message'])),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single(t(uid, "btn_cancel"), "cancel"))
            return
        user = update.effective_user
        approval_time = datetime.now(timezone.utc)
        price = prices["tier_3"]
        account_record = {
            "email": str(session.email or ""),
            "password": str(session.password or ""),
            "totp": str(session.totp or ""),
            "app_pass": str(cleaned),
            "amount": price,
            "requested_amount": price,
            "timestamp": approval_time.isoformat(),
            "approval_time": approval_time.isoformat(),
            "release_at": (approval_time + timedelta(seconds=LEAVE_HOLD_SECONDS)).isoformat(),
            "hold_seconds": LEAVE_HOLD_SECONDS,
            "extracted": False, "has_totp": True, "has_app_pass": True,
            "user_name": user.full_name or "unknown",
            "user_username": user.username or "none",
            "approved_with_leave": True, "leave_confirmed": False,
            "auto_verified": True, "auto_approved": True,
            "account_type": "old", "acceptance_mode": "automatic",
            "admin_bonus_status": "not_applicable",
            "verification": {
                "level": verify_result["level"], "badge": verify_result["badge"],
                "message": verify_result["message"], "imap_ok": True,
                "totp_ok": verify_result["totp_ok"], "category": "ok",
                "verified_at": approval_time.isoformat(),
                "verified_by": "auto_on_completion",
            },
        }
        user_data["pending_requests"] = pending
        user_data["hold_balance"] = clamp_money(float(user_data.get("hold_balance", 0.0)) + price)
        user_data["total_credited_balance"] = clamp_money(float(user_data.get("total_credited_balance", 0.0)) + price)
        user_data["total_approved_emails"] = int(user_data.get("total_approved_emails", 0)) + 1
        user_data.setdefault("approved_accounts", []).append(account_record)
        add_transaction(user_data, "hold", price, "auto-verified old account", str(session.email))
        register_approval_reset(user_data)
        mark_user_sold(user_data)
        save_user(uid, user_data)
        SESSIONS.pop(uid, None)
        save_sessions()
        try:
            await schedule_leave_check(context, uid, str(session.email), account_record["release_at"])
            await check_contest_award(context, uid)
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=uid,
            text=t(uid, "add_success", email=tg_html_escape(session.email), price=price),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
    
async def sell_new_email_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = update.effective_user.id
    user_data = get_user(uid)
    ban_msg = get_active_ban_message(user_data)
    if ban_msg:
        try:
            await query.answer("🚫", show_alert=True)
        except Exception:
            pass
        await _safe_edit(query, ban_msg, parse_mode=ParseMode.HTML,
                         reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    restriction_msg = get_active_restriction_message(user_data)
    if restriction_msg:
        try:
            await query.answer("🚫", show_alert=True)
        except Exception:
            pass
        await _safe_edit(query, restriction_msg, parse_mode=ParseMode.HTML,
                         reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    SESSIONS.pop(uid, None)
    save_sessions()
    clear_edit_state(context)
    context.user_data.pop("step", None)
    context.user_data["account_type"] = "new"
    await query.edit_message_text(t(uid, "sell_new_generating"), parse_mode=ParseMode.HTML)
    creds = None
    for attempt in range(3):
        candidate = generate_sell_email_credentials()
        result = await check_email_availability(candidate["email"], use_cache=False)
        if result is True or result is None:
            creds = candidate
            break
    if creds is None:
        await query.edit_message_text(
            t(uid, "sell_new_no_available"), parse_mode=ParseMode.HTML,
            reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    context.user_data["sell_new_email"] = creds
    prices = get_tier_prices("new")
    await query.edit_message_text(
        t(uid, "sell_new_title", first_name=tg_html_escape(creds['first_name']),
          email=tg_html_escape(creds['email']),
          password=tg_html_escape(creds['password']),
          price=prices['tier_3']),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            (t(uid, "sell_new_btn_complete"), "sell_new_email_complete"),
            (t(uid, "btn_cancel"), "sell_new_email_cancel"),
        ]))


async def sell_new_email_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    creds = context.user_data.get("sell_new_email")
    if not creds:
        await query.answer("⚠️", show_alert=True)
        await query.edit_message_text(
            t(uid, "sell_new_session_expired"),
            reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return

    await query.edit_message_text(
        t(uid, "sell_new_checking_created", email=tg_html_escape(creds['email'])),
        parse_mode=ParseMode.HTML)

    status, _, remaining, restricted_now = await _verify_and_handle_failure(update, context, creds["email"])

    if restricted_now:
        fresh_user = get_user(uid)
        restriction_msg = get_active_restriction_message(fresh_user)
        await query.edit_message_text(
            restriction_msg or "🚫 تم تقييدك لمدة ساعة.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return

    if status == "unknown":
        await query.edit_message_text(
            t(uid, "sell_new_verify_unknown", email=tg_html_escape(creds['email'])),
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_markup=kb_vertical([
                (t(uid, "sell_new_btn_recheck"), "sell_new_email_complete"),
                (t(uid, "btn_cancel"), "sell_new_email_cancel"),
            ]))
        return

    if status == "available":
        await query.edit_message_text(
            f"❌ <b>لم تُنشئ الإيميل بعد!</b>\n\n"
            f"📧 <code>{tg_html_escape(creds['email'])}</code>\n\n"
            f"⚠️ <b>الفرص المتبقية:</b> <code>{remaining}</code> من 3\n\n"
            f"📌 <b>الخطوات:</b>\n"
            f"1. اذهب إلى <a href=\"https://accounts.google.com/signup\">صفحة إنشاء حساب جوجل</a>\n"
            f"2. استخدم البريد والباسورد المعروضة\n"
            f"3. أكمل الإنشاء\n"
            f"4. ارجع واضغط «أعد الفحص»",
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_markup=kb_vertical([
                (t(uid, "sell_new_btn_recheck"), "sell_new_email_complete"),
                (t(uid, "btn_cancel"), "sell_new_email_cancel"),
            ]))
        return

    context.user_data["step"] = "sell_new_email_totp"
    await query.edit_message_text(
        t(uid, "sell_new_verified_created", email=tg_html_escape(creds['email'])),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            (t(uid, "add_btn_video_totp"), "sell_show_video:totp"),
            (t(uid, "sell_new_btn_skip_2fa"), "sell_new_email_finish_prompt"),
            (t(uid, "btn_cancel"), "sell_new_email_cancel"),
        ]))


async def sell_new_email_finish_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    creds = context.user_data.get("sell_new_email")
    if not creds:
        await query.answer("⚠️ انتهت الجلسة.", show_alert=True)
        await query.edit_message_text(t(uid, "sell_new_session_expired"), reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    prices = get_tier_prices("new")
    await query.edit_message_text(
        t(uid, "sell_new_finish_prompt", full_price=prices["tier_3"]),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            (t(uid, "sell_new_btn_finish_confirm"), "sell_new_email_finish_confirm"),
            (t(uid, "sell_new_btn_complete_2fa"), "sell_new_email_complete"),
            (t(uid, "btn_cancel"), "sell_new_email_cancel"),
        ]))


async def sell_new_email_finish_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    creds = context.user_data.get("sell_new_email")
    if not creds:
        await query.answer("⚠️ انتهت الجلسة.", show_alert=True)
        await query.edit_message_text(t(uid, "sell_new_session_expired"), reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return

    await query.edit_message_text(
        t(uid, "sell_new_checking_created", email=tg_html_escape(creds['email'])),
        parse_mode=ParseMode.HTML)

    status, _, remaining, restricted_now = await _verify_and_handle_failure(update, context, creds["email"])

    if restricted_now:
        fresh_user = get_user(uid)
        restriction_msg = get_active_restriction_message(fresh_user)
        await query.edit_message_text(
            restriction_msg or "🚫 تم تقييدك لمدة ساعة.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return

    if status == "unknown":
        await query.edit_message_text(
            t(uid, "sell_new_verify_unknown", email=tg_html_escape(creds['email'])),
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_markup=kb_vertical([
                (t(uid, "sell_new_btn_recheck"), "sell_new_email_finish_confirm"),
                (t(uid, "sell_new_btn_complete_2fa"), "sell_new_email_complete"),
                (t(uid, "btn_cancel"), "sell_new_email_cancel"),
            ]))
        return

    if status == "available":
        await query.edit_message_text(
            f"❌ <b>لم تُنشئ الإيميل بعد!</b>\n\n"
            f"📧 <code>{tg_html_escape(creds['email'])}</code>\n\n"
            f"⚠️ <b>الفرص المتبقية:</b> <code>{remaining}</code> من 3\n\n"
            f"📌 أكمل إنشاء الإيميل أولاً",
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_markup=kb_vertical([
                (t(uid, "sell_new_btn_recheck"), "sell_new_email_finish_confirm"),
                (t(uid, "sell_new_btn_complete_2fa"), "sell_new_email_complete"),
                (t(uid, "btn_cancel"), "sell_new_email_cancel"),
            ]))
        return

    user = update.effective_user
    user_data = get_user(uid)
    prices = get_tier_prices("new")
    amount = prices["tier_1"]

    user_data["pending_requests"] = [r for r in user_data.get("pending_requests", []) if normalize_email(r.get("email", "")) != normalize_email(creds["email"])]

    pending_request = {
        "email": creds["email"],
        "password": creds["password"],
        "totp": "",
        "app_pass": "",
        "amount": amount,
        "requested_amount": amount,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "has_totp": False,
        "has_app_pass": False,
        "account_type": "new",
        "user_name": user.full_name or "غير معروف",
        "user_username": user.username or "لا يوجد",
        "first_name": creds.get("first_name", ""),
        "is_bot_generated": True,
    }
    user_data.setdefault("pending_requests", []).append(pending_request)
    user_data["pending_balance"] = clamp_money(float(user_data.get("pending_balance", 0.0)) + amount)
    add_transaction(user_data, "hold", amount, "طلب بيع إيميل جديد (بدون 2FA)", creds["email"])
    mark_user_sold(user_data)
    save_user(uid, user_data)

    context.user_data.pop("sell_new_email", None)
    context.user_data.pop("step", None)

    await query.edit_message_text(
        t(uid, "sell_new_finished", email=tg_html_escape(creds["email"])),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single(t(uid, "main_menu"), "main_menu")
    )
    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(f"🆕 <b>طلب بيع إيميل جديد (بدون 2FA)</b>\n\n"
                  f"👤 {tg_html_escape(user.full_name or 'غير معروف')} (@{tg_html_escape(user.username or 'لا يوجد')})\n"
                  f"🆔 <code>{uid}</code>\n"
                  f"📧 <code>{tg_html_escape(creds['email'])}</code>"),
            parse_mode=ParseMode.HTML)
    except Exception:
        pass


async def sell_new_email_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    context.user_data.pop("sell_new_email", None)
    context.user_data.pop("step", None)
    context.user_data.pop("account_type", None)
    SESSIONS.pop(uid, None)
    save_sessions()
    await query.edit_message_text(t(uid, "msg_cancelled"),
                                  reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))


async def sell_show_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    await query.answer()
    vtype = query.data.split(":", 1)[1]
    config = load_config()
    path = config.get(f"video_{vtype}")
    if not path or not Path(path).exists():
        await query.answer("⚠️", show_alert=True)
        return
    try:
        await context.bot.send_video(chat_id=uid, video=open(path, "rb"),
                                     caption="📹", parse_mode=ParseMode.HTML,
                                     supports_streaming=True)
    except Exception:
        logger.exception("Failed to send sell video")
        await query.answer("⚠️", show_alert=True)


async def handle_sell_new_email_totp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    creds = context.user_data.get("sell_new_email")
    if not creds:
        context.user_data.pop("step", None)
        await update.message.reply_text(t(uid, "sell_new_session_expired"),
                                        reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    if text.casefold() in {"إلغاء", "الغاء", "cancel", "отмена"}:
        context.user_data.pop("sell_new_email", None)
        context.user_data.pop("step", None)
        await update.message.reply_text(t(uid, "msg_cancelled"),
                                        reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    cleaned = text.replace(" ", "").upper()
    if len(cleaned) != 32 or not re.match(r'^[A-Z2-7]{32}$', cleaned):
        await update.message.reply_text(t(uid, "add_totp_invalid"))
        return
    try:
        code = pyotp.TOTP(cleaned).now()
    except Exception:
        await update.message.reply_text(t(uid, "add_totp_bad"))
        return

    try:
        await update.message.delete()
    except Exception:
        pass

    await context.bot.send_message(chat_id=uid, text=t(uid, "add_checking_account"))
    ok, msg = await asyncio.to_thread(_imap_login_sync, creds["email"], creds["password"])
    is_2fa_error = any(m in msg.lower() for m in TWO_FA_ERROR_MARKERS)
    is_network_error = any(m in msg.lower() for m in NETWORK_ERROR_MARKERS)
    if not ok and not is_2fa_error and not is_network_error:
        await context.bot.send_message(chat_id=uid,
            text=t(uid, "sell_new_wrong_credentials"),
            reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    user = update.effective_user
    user_data = get_user(uid)
    prices = get_tier_prices("new")
    amount = prices["tier_2"]
    pending_request = {
        "email": creds["email"], "password": creds["password"],
        "totp": cleaned, "app_pass": "", "amount": amount, "requested_amount": amount,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "has_totp": True, "has_app_pass": False, "account_type": "new",
        "user_name": user.full_name or "unknown",
        "user_username": user.username or "none",
        "first_name": creds.get("first_name", ""), "is_bot_generated": True,
    }
    user_data.setdefault("pending_requests", []).append(pending_request)
    user_data["pending_balance"] = clamp_money(float(user_data.get("pending_balance", 0.0)) + amount)
    add_transaction(user_data, "hold", amount, "new email request (totp)", creds["email"])
    mark_user_sold(user_data)
    save_user(uid, user_data)
    creds["totp"] = cleaned
    context.user_data["sell_new_email"] = creds
    context.user_data["step"] = "sell_new_email_app_pass"
    try:
        await context.bot.send_message(chat_id=OWNER_ID,
            text=f"🆕 New email request\n👤 {user.full_name} (@{user.username})\n🆔 {uid}\n📧 {creds['email']}\n🔐 {cleaned}",
            parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=uid,
        text=t(uid, "sell_new_totp_received", code=code), parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            (t(uid, "add_btn_video_app_pass"), "sell_show_video:app_pass"),
            (t(uid, "sell_new_btn_finish"), "sell_new_email_finish"),
        ]))


async def handle_sell_new_email_app_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    creds = context.user_data.get("sell_new_email")
    if not creds:
        context.user_data.pop("step", None)
        await update.message.reply_text(t(uid, "sell_new_session_expired"),
                                        reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    if text.casefold() in {"إلغاء", "الغاء", "cancel", "отмена"}:
        context.user_data.pop("sell_new_email", None)
        context.user_data.pop("step", None)
        await update.message.reply_text(t(uid, "msg_cancelled"),
                                        reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    cleaned = text.replace(" ", "")
    if len(cleaned) != 16 or not re.match(r'^[A-Za-z0-9]{16}$', cleaned):
        await update.message.reply_text(t(uid, "add_app_pass_invalid"))
        return
    if has_active_app_password(cleaned):
        await update.message.reply_text(t(uid, "add_app_pass_used"))
        return
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    found_idx = None
    for i, req in enumerate(pending):
        if normalize_email(req.get("email", "")) == normalize_email(creds["email"]):
            found_idx = i
            break
    if found_idx is None:
        context.user_data.pop("sell_new_email", None)
        context.user_data.pop("step", None)
        await update.message.reply_text(t(uid, "edit_missing"),
                                        reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    try:
        await update.message.delete()
    except Exception:
        pass
    allowed, wait = imap_rate_ok(creds["email"])
    if not allowed:
        await context.bot.send_message(chat_id=uid,
            text=t(uid, "add_rate_wait", wait=wait), parse_mode=ParseMode.HTML)
        return
    imap_rate_mark(creds["email"])
    verify_result = await verify_account_credentials(
        email=creds["email"], password=creds["password"],
        app_pass=cleaned, totp_secret=creds.get("totp", ""))
    if not verify_result["imap_ok"]:
        await context.bot.send_message(
            chat_id=uid,
            text=t(uid, "add_verify_failed",
                   email=tg_html_escape(creds['email']),
                   message=tg_html_escape(verify_result['message'])),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single(t(uid, "btn_cancel"), "sell_new_email_cancel"))
        return
    prices = get_tier_prices("new")
    old_amount = float(pending[found_idx].get("amount", 0.0))
    new_amount = prices["tier_3"]
    approval_time = datetime.now(timezone.utc)
    user = update.effective_user
    account_record = {
        "email": str(creds["email"]), "password": str(creds["password"]),
        "totp": str(creds.get("totp", "")), "app_pass": str(cleaned),
        "amount": new_amount, "requested_amount": new_amount,
        "timestamp": approval_time.isoformat(), "approval_time": approval_time.isoformat(),
        "release_at": (approval_time + timedelta(seconds=LEAVE_HOLD_SECONDS)).isoformat(),
        "hold_seconds": LEAVE_HOLD_SECONDS, "extracted": False,
        "has_totp": True, "has_app_pass": True,
        "user_name": user.full_name or "unknown",
        "user_username": user.username or "none",
        "approved_with_leave": True, "leave_confirmed": False,
        "auto_verified": True, "auto_approved": True,
        "account_type": "new", "acceptance_mode": "automatic",
        "admin_bonus_status": "not_applicable",
        "verification": {
            "level": verify_result["level"], "badge": verify_result["badge"],
            "message": verify_result["message"], "imap_ok": True,
            "totp_ok": verify_result["totp_ok"], "category": "ok",
            "verified_at": approval_time.isoformat(),
            "verified_by": "auto_on_completion",
        },
    }
    pending.pop(found_idx)
    user_data["pending_requests"] = pending
    user_data["pending_balance"] = clamp_money(float(user_data.get("pending_balance", 0.0)) - old_amount)
    user_data["hold_balance"] = clamp_money(float(user_data.get("hold_balance", 0.0)) + new_amount)
    user_data["total_credited_balance"] = clamp_money(float(user_data.get("total_credited_balance", 0.0)) + new_amount)
    user_data["total_approved_emails"] = int(user_data.get("total_approved_emails", 0)) + 1
    user_data.setdefault("approved_accounts", []).append(account_record)
    add_transaction(user_data, "hold", new_amount, "auto-verified new email", str(creds["email"]))
    register_approval_reset(user_data)
    mark_user_sold(user_data)
    save_user(uid, user_data)
    context.user_data.pop("sell_new_email", None)
    context.user_data.pop("step", None)
    try:
        await schedule_leave_check(context, uid, str(creds["email"]), account_record["release_at"])
        await check_contest_award(context, uid)
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=uid,
        text=t(uid, "add_success", email=tg_html_escape(creds['email']), price=new_amount),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))


async def sell_new_email_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    creds = context.user_data.get("sell_new_email")
    context.user_data.pop("sell_new_email", None)
    context.user_data.pop("step", None)
    if not creds:
        await query.edit_message_text(t(uid, "sell_new_finished", email=""),
                                      parse_mode=ParseMode.HTML,
                                      reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    await query.edit_message_text(
        t(uid, "sell_new_finished", email=tg_html_escape(creds.get('email', ''))),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))


# ==================== LEAVE VIDEO ====================
async def send_leave_video_to_user(context: ContextTypes.DEFAULT_TYPE, user_id: int, email: str):
    config = load_config()
    video_path = config.get("video_leave")
    text = f"📹 Leave video\n📧 {email}"
    if video_path and Path(video_path).exists():
        try:
            await context.bot.send_video(chat_id=user_id, video=open(video_path, "rb"),
                                         caption=text, parse_mode=ParseMode.MARKDOWN,
                                         supports_streaming=True)
            return True
        except Exception as e:
            logger.error(f"Error sending leave video to {user_id}: {e}")
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
        callback=check_leave_status, when=delay,
        data={"user_id": user_id, "email": email, "release_at": release_at},
        name=job_name)


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
    account_idx = None
    account = None
    for i, acc in enumerate(accounts):
        if normalize_email(acc.get("email", "")) == normalize_email(email):
            account_idx = i
            account = acc
            break
    if account is None or account.get("leave_confirmed", False):
        return
    price = float(account.get("amount", 0.0))
    hold_hours = max(1, int(account.get("hold_seconds", LEAVE_HOLD_SECONDS)) // 3600)
    allowed, wait = imap_rate_ok(email)
    if not allowed:
        await schedule_leave_check(context, user_id, email,
            (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat())
        return
    imap_rate_mark(email)
    recheck = await verify_account_credentials(
        email=account.get("email", ""), password=account.get("password", ""),
        app_pass=account.get("app_pass", ""), totp_secret=account.get("totp", ""))
    if not recheck["imap_ok"]:
        user_data["hold_balance"] = clamp_money(float(user_data.get("hold_balance", 0.0)) - price)
        accounts.pop(account_idx)
        user_data["approved_accounts"] = accounts
        failed_account = dict(account)
        failed_account["rejected_at_24h"] = True
        failed_account["leave_confirmed"] = True
        failed_account["rejection_reason"] = f"IMAP recheck failed after {hold_hours}h"
        failed_account["verification_24h"] = {
            "level": recheck["level"], "badge": recheck["badge"],
            "message": recheck["message"], "imap_ok": recheck["imap_ok"],
            "totp_ok": recheck["totp_ok"],
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "verified_by": "second_check_failed",
        }
        move_approved_account_to_rejected(user_data, failed_account, "imap_failed_24h",
                                          f"Second check failed: {recheck['message'][:150]}")
        add_transaction(user_data, "debit", price, f"rejected after {hold_hours}h check", email)
        save_user(user_id, user_data)
        try:
            await context.bot.send_message(chat_id=user_id,
                text=f"❌ Second check failed after {hold_hours}h\n📧 {email}\n💰 -${price:.2f}",
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        try:
            await context.bot.send_message(chat_id=OWNER_ID,
                text=f"🔴 Second check failed\n👤 {user_id}\n📧 {email}\n💰 ${price:.2f}",
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return

    # ═══ Success — release points and trigger daily gift ═══
    user_data["hold_balance"] = clamp_money(float(user_data.get("hold_balance", 0.0)) - price)
    user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) + price)
    account["leave_confirmed"] = True
    account["auto_confirmed"] = True
    account["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    account["released_amount"] = price
    account["verification_24h"] = {
        "level": recheck["level"], "badge": recheck["badge"],
        "message": recheck["message"], "imap_ok": recheck["imap_ok"],
        "totp_ok": recheck["totp_ok"],
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verified_by": "second_check_passed",
    }
    accounts[account_idx] = account
    user_data["approved_accounts"] = accounts
    add_transaction(user_data, "release", price, f"auto transfer after {hold_hours}h", email)
    save_user(user_id, user_data)

    try:
        await process_daily_gift_after_approval(context, user_id)
    except Exception:
        logger.exception("Daily gift processing failed")

    try:
        await context.bot.send_message(chat_id=user_id,
            text=f"✅ Confirmed!\n📧 {email}\n💰 +${price:.2f}",
            parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Could not send auto-confirmation to {user_id}: {e}")


# ==================== RESTORE JOBS ====================
async def restore_leave_checks(application: Application):
    users = load_json(USERS_DB)
    changed = False
    now = datetime.now(timezone.utc)
    for user_id, encrypted_data in users.items():
        user_data = decrypt_user_data(encrypted_data)
        if not user_data.get("created_at"):
            user_data["created_at"] = now.isoformat()
            users[user_id] = encrypt_user_data(user_data)
            changed = True
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
                name=job_name)
    if changed:
        save_json(USERS_DB, users)
    load_sessions_from_disk()
    load_pending_purchases_from_disk()
    application.job_queue.run_repeating(daily_backup_job, interval=24 * 3600,
                                        first=3600, name="daily_backup")
    application.job_queue.run_repeating(inactive_member_reminder_job, interval=24 * 3600,
                                        first=7200, name="inactive_reminder")
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


__all__ = [name for name in globals() if not name.startswith("__")]
