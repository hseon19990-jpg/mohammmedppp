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
    user_data = get_user(user.id)
    ban_msg = get_active_ban_message(user_data)
    prefix = (ban_msg + "\n\n") if ban_msg else ""
    buttons = [
        ("➕ إضافة حساب", "add_account"),
        ("🆕 بيع إيميل جديد", "sell_new_email"),
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
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=member_menu_keyboard(buttons))
        except Exception:
            await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=member_menu_keyboard(buttons))
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=member_menu_keyboard(buttons))


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
        await query.edit_message_text(
            "📭 لا توجد بيانات بيع بعد.",
            reply_markup=kb_vertical([back_btn]),
        )
        return
    lines = ["🏆 <b>الأكثر بيعاً</b>", ""]
    for idx, item in enumerate(top, 1):
        lines.append(f"{idx}- ID <code>{item['user_id']}</code> بيع <b>{item['total']}</b>")
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([back_btn]),
    )


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
        "email": "الإيميل غير صحيح أو غير مقبول",
        "password": "كلمة المرور غير صحيحة",
        "totp": "رمز المصادقة غير صحيح",
        "app_pass": "كلمة مرور التطبيق غير صحيحة",
        "phone": "يحتاج رقم هاتف",
        "other": "سبب آخر",
        "custom": "سبب مخصص",
        "unknown": "غير معروف",
        "imap_failed_24h": "فشل الفحص الثاني (24 ساعة)",
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

    user_data_check = get_user(uid)
    ban_msg = get_active_ban_message(user_data_check)
    if ban_msg:
        try:
            await update.callback_query.answer("🚫 أنت محظور حالياً.", show_alert=True)
        except Exception:
            pass
        await _safe_edit(
            update.callback_query,
            ban_msg, parse_mode=ParseMode.HTML,
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
                "reject_approved_uid", "reject_approved_token",
                "sell_new_email"):
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
        f"✨ <i>لن يُقبل الحساب نهائياً إلا بعد التحقق التلقائي الكامل عند إرسال كلمة مرور التطبيق.</i>\n\n"
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
        if active_status == "rejected":
            await update.message.reply_text(
                "❌ هذا الإيميل مرفوض سابقاً! لا يمكنك إعادة إرساله.",
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
            f"💰 <b>مكافأة إيميل + باسورد:</b> <b>${prices['tier_1']:.2f}</b>\n"
            f"🎁 <i>يمكنك مضاعفتها بإضافة رمز المصادقة وكلمة مرور التطبيق لاحقاً.</i>",
            parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))

    # ═══ الخطوة 2: الباسورد → حفظ مبدئي (بدون تحقق) ═══
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

        await update.message.reply_text(
            f"✅ <b>تم استلام بياناتك الأولية!</b>\n\n"
            f"📧 <code>{tg_html_escape(session.email)}</code>\n"
            f"🔑 الباسورد: ✅\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 <b>حصلت على ${price:.2f}</b> <i>(معلق حتى التحقق الكامل)</i>\n"
            f"━━━━━━━━━━━━━━━\n\n"
            f"🎁 <b>أكمل للحصول على المزيد:</b>\n\n"
            f"🔐 رمز المصادقة (2FA) → <b>${prices['tier_2']:.2f}</b>\n"
            f"🗝 ثم كلمة مرور التطبيق → <b>${prices['tier_3']:.2f}</b>\n\n"
            f"⚠️ <b>لن يتم قبول حسابك نهائياً حتى تُرسل كلمة مرور التطبيق ويتم التحقق منه.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("🚀 إكمال للحصول على المزيد", f"complete_more:{uid}"),
                ("✅ إنهاء", f"finish_request:{uid}"),
            ]))

    # ═══ الخطوة 3: TOTP → تحديث الطلب (بدون تحقق) ═══
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

            await update.message.reply_text(
                f"✅ <b>تم إضافة رمز المصادقة!</b>\n\n"
                f"🔐 المفتاح: <code>•••• •••• •••• ••••</code>\n"
                f"🔢 الكود الحالي: <code>{code}</code>\n\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💰 <b>الآن حصلت على ${new_price:.2f}</b> <i>(معلق حتى التحقق الكامل)</i>\n"
                f"━━━━━━━━━━━━━━━\n\n"
                f"🎁 <b>الخطوة الأخيرة:</b>\n\n"
                f"🗝 أضف كلمة مرور التطبيق → <b>${prices['tier_3']:.2f}</b>\n\n"
                f"⚠️ <b>لن يتم قبول حسابك نهائياً حتى يتم التحقق من كلمة مرور التطبيق.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_vertical([
                    ("🚀 إكمال للحصول على المزيد", f"complete_more:{uid}"),
                    ("✅ إنهاء", f"finish_request:{uid}"),
                ]))
        except Exception as e:
            await update.message.reply_text(f"⚠️ مفتاح 2FA غير صالح: {str(e)}")

    # ═══ الخطوة 4: App Password → التحقق التلقائي الحاسم ═══
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
                   "يرجى تغيير كلمة المرور وإرسال كلمة جديدة.\n\n"
                   "📌 الصيغة: XXXX XXXX XXXX XXXX")
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

        # احذف الرسالة التي تحتوي على كلمة المرور
        try:
            await update.message.delete()
        except Exception:
            pass

        # ═══ التحقق التلقائي الحاسم ═══
        allowed, wait = imap_rate_ok(session.email)
        if not allowed:
            await context.bot.send_message(
                chat_id=uid,
                text=(f"⏳ <b>يرجى الانتظار {wait} ثانية</b>\n\n"
                      f"تم تجاوز حد المحاولات لنفس الإيميل.\n"
                      f"حاول مرة أخرى بعد قليل."),
                parse_mode=ParseMode.HTML)
            return

        imap_rate_mark(session.email)
        verify_result = await verify_account_credentials(
            email=session.email,
            password=session.password,
            app_pass=cleaned,
            totp_secret=session.totp,
        )

        # ═══ حالة الفشل: احتفظ بالطلب في pending_requests ═══
        if not verify_result["imap_ok"]:
            pending[found_idx]["app_pass"] = cleaned
            pending[found_idx]["has_app_pass"] = True
            pending[found_idx]["amount"] = new_price
            pending[found_idx]["requested_amount"] = new_price
            pending[found_idx]["verification"] = {
                "level": verify_result["level"],
                "badge": verify_result["badge"],
                "message": verify_result["message"],
                "imap_ok": False,
                "totp_ok": verify_result["totp_ok"],
                "category": verify_result.get("category", "unknown"),
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "verified_by": "auto_on_completion",
            }
            pending[found_idx]["auto_verified"] = False
            user_data["pending_requests"] = pending
            user_data["pending_balance"] = clamp_money(
                float(user_data.get("pending_balance", 0.0)) - old_price + new_price)
            save_user(uid, user_data)

            session.app_pass = cleaned
            session.has_app_pass = True
            session.step = "submitted_tier_2"
            save_sessions()

            await context.bot.send_message(
                chat_id=uid,
                text=(f"❌ <b>فشل التحقق التلقائي من IMAP</b>\n\n"
                      f"📧 <code>{tg_html_escape(session.email)}</code>\n\n"
                      f"📝 <b>السبب:</b>\n<i>{tg_html_escape(verify_result['message'])}</i>\n\n"
                      f"━━━━━━━━━━━━━━━\n"
                      f"⚠️ <b>لم يتم إرسال حسابك للمالك.</b>\n"
                      f"الطلب لا يزال في قائمتك المعلقة.\n\n"
                      f"💡 <b>كيف تحل هذه المشكلة؟</b>\n"
                      f"• تأكد أن <b>App Password</b> حقيقي (وليس كلمة المرور العادية)\n"
                      f"• لحسابات Gmail: فعّل 2FA ثم أنشئ App Password من إعدادات Google\n"
                      f"• اضغط «✏️ تعديل حساباتي» لتصحيح البيانات والمحاولة مجدداً"),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_vertical([
                    ("✏️ تعديل الحساب الآن", "edit_my_accounts"),
                    ("🔙 القائمة الرئيسية", "main_menu"),
                ]))
            return

        # ═══ حالة النجاح: انقل مباشرة إلى approved_accounts ═══
        pending[found_idx]["app_pass"] = cleaned
        pending[found_idx]["has_app_pass"] = True
        pending[found_idx]["amount"] = new_price
        pending[found_idx]["requested_amount"] = new_price
        pending[found_idx]["verification"] = {
            "level": verify_result["level"],
            "badge": verify_result["badge"],
            "message": verify_result["message"],
            "imap_ok": True,
            "totp_ok": verify_result["totp_ok"],
            "category": "ok",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "verified_by": "auto_on_completion",
        }
        pending[found_idx]["auto_verified"] = True

        user = update.effective_user
        user_full_name = user.full_name or "غير معروف"
        user_username = user.username or "لا يوجد"

        approval_time = datetime.now(timezone.utc)
        account_record = {
            "email": str(session.email or ""),
            "password": str(session.password or ""),
            "totp": str(session.totp or ""),
            "app_pass": str(cleaned),
            "amount": new_price,
            "requested_amount": new_price,
            "timestamp": approval_time.isoformat(),
            "approval_time": approval_time.isoformat(),
            "release_at": (approval_time + timedelta(seconds=LEAVE_HOLD_SECONDS)).isoformat(),
            "hold_seconds": LEAVE_HOLD_SECONDS,
            "extracted": False,
            "has_totp": True,
            "has_app_pass": True,
            "user_name": user_full_name,
            "user_username": user_username,
            "approved_with_leave": True,
            "leave_confirmed": False,
            "auto_verified": True,
            "auto_approved": True,
            "acceptance_mode": "automatic",
            "admin_bonus_status": "not_applicable",
            "verification": pending[found_idx]["verification"],
        }

        pending.pop(found_idx)
        user_data["pending_requests"] = pending
        user_data["pending_balance"] = clamp_money(
            float(user_data.get("pending_balance", 0.0)) - old_price)
        user_data["hold_balance"] = clamp_money(
            float(user_data.get("hold_balance", 0.0)) + new_price)
        user_data["total_credited_balance"] = clamp_money(
            float(user_data.get("total_credited_balance", 0.0) or 0.0) + new_price)
        user_data["total_approved_emails"] = int(user_data.get("total_approved_emails", 0)) + 1
        user_data.setdefault("approved_accounts", []).append(account_record)
        add_transaction(user_data, "hold", new_price,
                        "حساب تم التحقق منه تلقائياً - معلق 24 ساعة", str(session.email))
        register_approval_reset(user_data)
        save_user(uid, user_data)

        session.app_pass = cleaned
        session.has_app_pass = True
        session.step = ""
        SESSIONS.pop(uid, None)
        save_sessions()

        try:
            await schedule_leave_check(context, uid, str(session.email),
                                       account_record["release_at"])
        except Exception:
            logger.exception("Failed to schedule second check for %s", session.email)

        try:
            await check_contest_award(context, uid)
        except Exception:
            logger.exception("Contest check failed")

        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=(f"✅ <b>حساب تم التحقق منه تلقائياً</b>\n\n"
                      f"👤 {tg_html_escape(user_full_name)} (@{tg_html_escape(user_username)})\n"
                      f"🆔 <code>{uid}</code>\n"
                      f"📧 <code>{tg_html_escape(session.email)}</code>\n"
                      f"🔑 <code>{tg_html_escape(session.password)}</code>\n"
                      f"🔐 <code>{tg_html_escape(session.totp)}</code>\n"
                      f"🗝 <code>{tg_html_escape(format_app_password(cleaned))}</code>\n"
                      f"💰 <b>${new_price:.2f}</b> (معلق 24 ساعة)\n\n"
                      f"⏰ <i>سيُعاد فحصه تلقائياً بعد 24 ساعة. إذا نجح، تُسلّم النقاط للعضو.</i>"),
                parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception("Failed to notify owner of verified account")

        referred_by = user_data.get("referred_by")
        if referred_by:
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=f"📢 *إشعار إحالة*\n\nالمستخدم `{uid}` تم التحقق من إيميله `{session.email}`.",
                    parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass

        await context.bot.send_message(
            chat_id=uid,
            text=(f"🎉 <b>تم التحقق التلقائي بنجاح!</b>\n\n"
                  f"📧 <code>{tg_html_escape(session.email)}</code>\n"
                  f"🔐 2FA: ✅\n"
                  f"🗝 كلمة مرور التطبيق: ✅\n\n"
                  f"━━━━━━━━━━━━━━━\n"
                  f"💰 <b>${new_price:.2f}</b> (معلق 24 ساعة)\n"
                  f"━━━━━━━━━━━━━━━\n\n"
                  f"✅ <b>حسابك الآن في قسم الحسابات المقبولة</b>\n"
                  f"⏰ <i>سيتم إعادة الفحص تلقائياً بعد 24 ساعة.</i>\n"
                  f"<i>إذا نجح الفحص الثاني، تُسلّم النقاط إلى رصيدك الدائم.</i>"),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))

    elif session.step in ("submitted_tier_1", "submitted_tier_2"):
        await update.message.reply_text(
            "📌 <i>يرجى استخدام الأزرار أعلاه للاختيار.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("🚀 إكمال للحصول على المزيد", f"complete_more:{uid}"),
                ("✅ إنهاء", f"finish_request:{uid}"),
            ]))


# ==================== SELL NEW EMAIL FLOW ====================
async def sell_new_email_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر «بيع إيميل جديد» — يولّد بيانات عشوائية ثم يطلب رمز المصادقة."""
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = update.effective_user.id

    user_data = get_user(uid)
    ban_msg = get_active_ban_message(user_data)
    if ban_msg:
        try:
            await query.answer("🚫 أنت محظور حالياً.", show_alert=True)
        except Exception:
            pass
        await _safe_edit(query, ban_msg, parse_mode=ParseMode.HTML,
                         reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    SESSIONS.pop(uid, None)
    save_sessions()
    clear_edit_state(context)
    context.user_data.pop("step", None)

    creds = generate_sell_email_credentials()
    context.user_data["sell_new_email"] = creds
    prices = get_tier_prices()

    text = (
        "🆕 <b>بيع إيميل جديد</b>\n\n"
        f"👤 <b>الاسم الأول:</b> {tg_html_escape(creds['first_name'])}\n\n"
        f"📧 <b>البريد الإلكتروني:</b>\n<code>{tg_html_escape(creds['email'])}</code>\n\n"
        f"🔑 <b>كلمة السر:</b>\n<code>{tg_html_escape(creds['password'])}</code>\n\n"
        "━━━━━━━━━━━━━━━\n"
        f"💰 <b>المكافأة الكاملة:</b> ${prices['tier_3']:.2f}\n"
        "━━━━━━━━━━━━━━━"
    )
    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            ("✅ إكمال", "sell_new_email_complete"),
            ("❌ إلغاء", "sell_new_email_cancel"),
        ]))


async def sell_new_email_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عند الضغط على «إكمال» — يطلب رمز المصادقة."""
    query = update.callback_query
    creds = context.user_data.get("sell_new_email")
    if not creds:
        await query.answer("⚠️ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
        await query.edit_message_text(
            "⚠️ انتهت الجلسة. اضغط على «بيع إيميل جديد» مجدداً.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    context.user_data["step"] = "sell_new_email_totp"
    await query.edit_message_text(
        "🔐 <b>يجب عليك إرسال رمز المصادقة</b>\n\n"
        f"📧 <code>{tg_html_escape(creds['email'])}</code>\n\n"
        "📌 أرسل مفتاح المصادقة (32 حرفاً Base32):\n\n"
        "<i>مثال: JBSW Y3DP EHPK 3PXP</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            ("📹 عرض كيف الحصول على رمز مصادقة", "sell_show_video:totp"),
            ("❌ إلغاء", "sell_new_email_cancel"),
        ]))


async def sell_new_email_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id
    context.user_data.pop("sell_new_email", None)
    context.user_data.pop("step", None)
    SESSIONS.pop(uid, None)
    save_sessions()
    await query.edit_message_text(
        "❌ تم الإلغاء.",
        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


async def sell_show_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض فيديو تعليمي بدون تعديل الرسالة الحالية."""
    query = update.callback_query
    await query.answer()
    vtype = query.data.split(":", 1)[1]
    config = load_config()
    path = config.get(f"video_{vtype}")
    if not path or not Path(path).exists():
        await query.answer("⚠️ الفيديو غير متوفر حالياً.", show_alert=True)
        return
    try:
        await context.bot.send_video(
            chat_id=query.from_user.id,
            video=open(path, "rb"),
            caption="📹 <b>فيديو تعليمي</b>\n\nشاهد الفيديو ثم ارسل المفتاح.",
            parse_mode=ParseMode.HTML,
            supports_streaming=True)
    except Exception:
        logger.exception("Failed to send sell video")
        await query.answer("⚠️ تعذر إرسال الفيديو.", show_alert=True)


async def handle_sell_new_email_totp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستلم مفتاح TOTP من العضو، يولّد الكود، ينشئ طلباً معلّقاً، ثم يطلب App Password."""
    uid = update.effective_user.id
    text = update.message.text.strip()
    creds = context.user_data.get("sell_new_email")
    if not creds:
        context.user_data.pop("step", None)
        await update.message.reply_text(
            "⚠️ انتهت الجلسة. اضغط على «بيع إيميل جديد» مجدداً.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    if text.casefold() in {"إلغاء", "الغاء", "cancel"}:
        context.user_data.pop("sell_new_email", None)
        context.user_data.pop("step", None)
        await update.message.reply_text(
            "❌ تم الإلغاء.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    cleaned = text.replace(" ", "").upper()
    if len(cleaned) != 32 or not re.match(r'^[A-Z2-7]{32}$', cleaned):
        await update.message.reply_text("⚠️ مفتاح المصادقة يجب أن يكون 32 حرفاً Base32.")
        return
    try:
        code = pyotp.TOTP(cleaned).now()
    except Exception:
        await update.message.reply_text("⚠️ مفتاح TOTP غير صالح.")
        return

    user = update.effective_user
    user_data = get_user(uid)
    prices = get_tier_prices()
    amount = prices["tier_2"]

    # تحقق من وجود طلب سابق بنفس الإيميل (نادر جداً لأن الإيميل عشوائي كل مرة)
    existing_idx = None
    for i, req in enumerate(user_data.get("pending_requests", [])):
        if normalize_email(req.get("email", "")) == normalize_email(creds["email"]):
            existing_idx = i
            break

    pending_request = {
        "email": creds["email"],
        "password": creds["password"],
        "totp": cleaned,
        "app_pass": "",
        "amount": amount,
        "requested_amount": amount,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "has_totp": True,
        "has_app_pass": False,
        "user_name": user.full_name or "غير معروف",
        "user_username": user.username or "لا يوجد",
        "first_name": creds.get("first_name", ""),
        "is_bot_generated": True,
    }
    if existing_idx is not None:
        old_amount = float(user_data["pending_requests"][existing_idx].get("amount", 0.0))
        user_data["pending_requests"][existing_idx] = pending_request
        user_data["pending_balance"] = clamp_money(
            float(user_data.get("pending_balance", 0.0)) - old_amount + amount)
    else:
        user_data.setdefault("pending_requests", []).append(pending_request)
        user_data["pending_balance"] = clamp_money(
            float(user_data.get("pending_balance", 0.0)) + amount)

    add_transaction(user_data, "hold", amount, "طلب بيع إيميل (رمز مصادقة)", creds["email"])
    save_user(uid, user_data)

    creds["totp"] = cleaned
    context.user_data["sell_new_email"] = creds
    context.user_data["step"] = "sell_new_email_app_pass"

    # إشعار المالك والأدمنية
    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(f"🆕 <b>طلب بيع إيميل جديد</b>\n\n"
                  f"👤 {tg_html_escape(user.full_name or 'غير معروف')} (@{tg_html_escape(user.username or 'لا يوجد')})\n"
                  f"🆔 <code>{uid}</code>\n"
                  f"🆔 <b>الاسم المولّد:</b> {tg_html_escape(creds.get('first_name', ''))}\n"
                  f"📧 <code>{tg_html_escape(creds['email'])}</code>\n"
                  f"🔑 <code>{tg_html_escape(creds['password'])}</code>\n"
                  f"🔐 <code>{tg_html_escape(cleaned)}</code>\n"
                  f"💰 <b>${amount:.2f}</b>"),
            parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Failed to notify owner of sell_new_email TOTP")

    await update.message.reply_text(
        f"✅ <b>تم استلام رمز المصادقة</b>\n\n"
        f"🔢 <b>الكود الحالي:</b> <code>{code}</code>\n\n"
        f"📧 <code>{tg_html_escape(creds['email'])}</code>\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 <b>أرسل كلمة مرور التطبيق:</b>\n"
        f"<i>مثال: abcd efgh ijkl mnop</i>\n\n"
        f"أو اضغط «انهاء» للإرسال بدون كلمة مرور التطبيق.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            ("✅ انهاء", "sell_new_email_finish"),
        ]))


async def handle_sell_new_email_app_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستلم App Password ويحدّث الطلب من tier_2 إلى tier_3."""
    uid = update.effective_user.id
    text = update.message.text.strip()
    creds = context.user_data.get("sell_new_email")
    if not creds:
        context.user_data.pop("step", None)
        await update.message.reply_text(
            "⚠️ انتهت الجلسة.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    if text.casefold() in {"إلغاء", "الغاء", "cancel"}:
        context.user_data.pop("sell_new_email", None)
        context.user_data.pop("step", None)
        await update.message.reply_text(
            "❌ تم الإلغاء.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    cleaned = text.replace(" ", "")
    if len(cleaned) != 16 or not re.match(r'^[A-Za-z0-9]{16}$', cleaned):
        await update.message.reply_text("⚠️ كلمة مرور التطبيق يجب أن تكون 16 حرفاً.")
        return
    if has_active_app_password(cleaned):
        await update.message.reply_text("⚠️ كلمة المرور هذه مستخدمة مسبقاً! أرسل كلمة مختلفة.")
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
        await update.message.reply_text(
            "⚠️ لم يتم العثور على الطلب.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    prices = get_tier_prices()
    old_amount = float(pending[found_idx].get("amount", 0.0))
    new_amount = prices["tier_3"]

    pending[found_idx]["app_pass"] = cleaned
    pending[found_idx]["has_app_pass"] = True
    pending[found_idx]["amount"] = new_amount
    pending[found_idx]["requested_amount"] = new_amount
    user_data["pending_requests"] = pending
    user_data["pending_balance"] = clamp_money(
        float(user_data.get("pending_balance", 0.0)) - old_amount + new_amount)
    save_user(uid, user_data)

    try:
        await update.message.delete()
    except Exception:
        pass

    context.user_data.pop("sell_new_email", None)
    context.user_data.pop("step", None)

    await update.message.reply_text(
        f"✅ <b>تم إرسال كلمة مرور التطبيق!</b>\n\n"
        f"📧 <code>{tg_html_escape(creds['email'])}</code>\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 <b>${new_amount:.2f}</b> ستُضاف لرصيدك بعد التحقق من قبل الإدارة.\n"
        f"━━━━━━━━━━━━━━━",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


async def sell_new_email_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عند الضغط على «انهاء» بعد رمز المصادقة — بدون App Password."""
    query = update.callback_query
    uid = update.effective_user.id
    creds = context.user_data.get("sell_new_email")
    context.user_data.pop("sell_new_email", None)
    context.user_data.pop("step", None)

    if not creds:
        await query.edit_message_text(
            "✅ تم إنهاء الطلب.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    await query.edit_message_text(
        f"✅ <b>تم إنهاء الطلب</b>\n\n"
        f"📧 <code>{tg_html_escape(creds.get('email', ''))}</code>\n\n"
        f"📌 سيتم مراجعة طلبك من قبل الإدارة.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


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
            f"• يجب أن يكون <b>32 حرف</b>\n"
            f"• مثال: <code>JBSW Y3DP EHPK 3PXP</code>\n\n"
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
            f"• يجب أن تكون <b>16 حرف</b>\n"
            f"• مثال: <code>abcd efgh ijkl mnop</code>\n\n"
            f"⚠️ <b>سيتم التحقق التلقائي من الحساب فور الإرسال.</b>\n"
            f"<i>إن لم يُطابق التحقق، لن يُقبل الحساب.</i>",
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
    """الفحص الثاني بعد 24 ساعة:
    - نجح → hold_balance → balance (تسليم النقاط)
    - فشل → نقل الحساب إلى rejected_requests + خصم hold_balance
    """
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

    if not recheck["imap_ok"]:
        user_data["hold_balance"] = clamp_money(
            float(user_data.get("hold_balance", 0.0)) - price)
        accounts.pop(account_idx)
        user_data["approved_accounts"] = accounts
        failed_account = dict(account)
        failed_account["rejected_at_24h"] = True
        failed_account["leave_confirmed"] = True
        failed_account["rejection_reason"] = f"فشل إعادة فحص IMAP بعد {hold_hours} ساعة"
        failed_account["verification_24h"] = {
            "level": recheck["level"],
            "badge": recheck["badge"],
            "message": recheck["message"],
            "imap_ok": recheck["imap_ok"],
            "totp_ok": recheck["totp_ok"],
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "verified_by": "second_check_failed",
        }
        move_approved_account_to_rejected(
            user_data, failed_account, "imap_failed_24h",
            f"فشل الفحص الثاني: {recheck['message'][:150]}")
        add_transaction(user_data, "debit", price,
                        f"رفض الحساب بعد فحص {hold_hours} ساعة", email)
        save_user(user_id, user_data)

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(f"❌ <b>فشل الفحص الثاني بعد {hold_hours} ساعة</b>\n\n"
                      f"📧 <code>{tg_html_escape(email)}</code>\n"
                      f"📝 <b>السبب:</b>\n<i>{tg_html_escape(recheck['message'])}</i>\n\n"
                      f"💰 <b>تم خصم ${price:.2f} من رصيدك المعلق.</b>\n"
                      f"🚫 <b>لا يمكن إعادة إرسال هذا الإيميل مرة أخرى.</b>"),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        except Exception:
            pass

        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=(f"🔴 <b>فشل حساب في الفحص الثاني</b>\n\n"
                      f"👤 المستخدم: <code>{user_id}</code>\n"
                      f"📧 <code>{tg_html_escape(email)}</code>\n"
                      f"💰 <code>${price:.2f}</code> (تم خصمها)\n\n"
                      f"📝 {tg_html_escape(recheck['message'][:200])}"),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return

    user_data["hold_balance"] = clamp_money(
        float(user_data.get("hold_balance", 0.0)) - price)
    user_data["balance"] = clamp_money(
        float(user_data.get("balance", 0.0)) + price)
    account["leave_confirmed"] = True
    account["auto_confirmed"] = True
    account["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    account["released_amount"] = price
    account["verification_24h"] = {
        "level": recheck["level"],
        "badge": recheck["badge"],
        "message": recheck["message"],
        "imap_ok": recheck["imap_ok"],
        "totp_ok": recheck["totp_ok"],
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verified_by": "second_check_passed",
    }
    accounts[account_idx] = account
    user_data["approved_accounts"] = accounts
    add_transaction(user_data, "release", price,
                    f"تحويل تلقائي بعد {hold_hours} ساعة", email)
    save_user(user_id, user_data)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(f"✅ <b>تم تأكيد الحساب وتسليم النقاط!</b>\n\n"
                  f"📧 الإيميل: <code>{tg_html_escape(email)}</code>\n"
                  f"💰 تم إضافة <b>${price:.2f}</b> إلى رصيدك الدائم.\n\n"
                  f"_شكراً لاستخدامك البوت 🤖_"),
            parse_mode=ParseMode.HTML)
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




__all__ = [name for name in globals() if not name.startswith("__")]
