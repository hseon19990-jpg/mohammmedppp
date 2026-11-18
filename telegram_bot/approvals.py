"""Account-review, approval, and rejection workflows."""

from telegram_bot.core import *

# ==================== VIDEOS ====================
async def videos_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    config = load_config()
    video_types = {
        "general": t(uid, "videos_general"),
        "email": t(uid, "videos_email"),
        "password": t(uid, "videos_password"),
        "totp": t(uid, "videos_totp"),
        "app_pass": t(uid, "videos_app_pass"),
        "leave": t(uid, "videos_leave"),
        "phone": t(uid, "videos_phone"),
    }
    buttons = []
    for key, name in video_types.items():
        video_path = config.get(f"video_{key}")
        exists = video_path and Path(video_path).exists()
        buttons.append((f"{'✅' if exists else '❌'} {name}", f"video_action:{key}"))
    buttons.append((t(uid, "owner_btn_back_panel"), "owner_panel"))
    await query.edit_message_text(t(uid, "videos_title"), parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def video_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    config = load_config()
    video_path = config.get(f"video_{video_type}")
    exists = video_path and Path(video_path).exists()
    buttons = []
    if exists:
        buttons.append((t(uid, "videos_view"), f"view_video:{video_type}"))
        buttons.append((t(uid, "videos_delete"), f"delete_video:{video_type}"))
    buttons.append((t(uid, "videos_upload"), f"set_video:{video_type}"))
    buttons.append((t(uid, "videos_back"), "videos_section"))
    status = t(uid, "videos_exists") if exists else t(uid, "videos_not_exists")
    await query.edit_message_text(t(uid, "videos_status", type=video_type, status=status),
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def view_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    config = load_config()
    video_path = config.get(f"video_{video_type}")
    if video_path and Path(video_path).exists():
        try:
            await context.bot.send_video(chat_id=uid, video=open(video_path, "rb"),
                                         caption=t(uid, "videos_caption", type=video_type), parse_mode=ParseMode.MARKDOWN,
                                         supports_streaming=True)
            await video_action(update, context)
        except Exception as e:
            logger.error(f"Error: {e}")
            await query.edit_message_text(t(uid, "msg_error"),
                                          reply_markup=kb_single(t(uid, "videos_back"), "videos_section"))
    else:
        await query.edit_message_text(t(uid, "videos_not_exists"),
                                      reply_markup=kb_single(t(uid, "videos_back"), "videos_section"))


async def delete_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
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
        await query.edit_message_text(t(uid, "videos_deleted"),
                                      reply_markup=kb_single(t(uid, "videos_back"), "videos_section"))
    else:
        await query.edit_message_text(t(uid, "videos_not_exists"),
                                      reply_markup=kb_single(t(uid, "videos_back"), "videos_section"))


async def set_video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    video_type = query.data.split(":", 1)[1]
    context.user_data["pending_video_type"] = video_type
    await query.edit_message_text(t(uid, "videos_send_now", type=video_type),
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single(t(uid, "btn_cancel"), f"video_action:{video_type}"))


async def handle_video_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram_bot.members import main_menu
    if not await maintenance_access_allowed(update):
        return
    uid = update.effective_user.id
    if uid != OWNER_ID:
        return
    video_type = context.user_data.get("pending_video_type")
    if not video_type:
        await update.message.reply_text(t(uid, "videos_no_type"))
        return
    if update.message.video:
        file = await update.message.video.get_file()
        file_path = VIDEOS_DIR / f"{video_type}.mp4"
        await file.download_to_drive(file_path)
        config = load_config()
        config[f"video_{video_type}"] = str(file_path)
        save_config(config)
        await update.message.reply_text(t(uid, "videos_saved", type=video_type))
        context.user_data.pop("pending_video_type", None)
        await main_menu(update, context)
    else:
        await update.message.reply_text(t(uid, "videos_invalid"))


# ==================== ADMIN REQUESTS ====================
def _collect_admin_requests() -> List[dict]:
    # للقراءة فقط: الحقول السرّية غير مستخدمة في القوائم، لذلك لا حاجة لفك التشفير (أسرع بكثير)
    users = load_json_readonly(USERS_DB)
    items = []
    for uid, user_data in users.items():
        for idx, req in enumerate(user_data.get("pending_requests", []) or []):
            copy = dict(req)
            copy["user_id"] = uid
            copy["index"] = idx
            items.append(copy)
    try:
        items.sort(key=_record_sort_key)
    except Exception:
        pass
    return items


async def admin_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if not is_admin(uid):
        await query.answer("🚫", show_alert=True)
        return
    try:
        page = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    items = _collect_admin_requests()
    # الإيميل الذي رفضه هذا الأدمن يختفي عنده ويبقى عند بقية الأدمنية
    items = [r for r in items if not admin_already_rejected(r, uid)]
    if not items:
        await query.edit_message_text(t(uid, "appr_empty"),
                                      reply_markup=kb_single(t(uid, "admin_btn_settings"), "admin_settings"))
        return

    def label(req):
        if req.get("has_app_pass"):
            tier_icon = "🟢"
        elif req.get("has_totp"):
            tier_icon = "🟡"
        else:
            tier_icon = "🔵"
        amount = float(req.get("amount", 0.0))
        email = req.get("email", "")
        email_display = email[:18] + "..." if len(email) > 18 else email
        acc_type = req.get("account_type", "old")
        type_icon = "🆕" if acc_type == "new" else "📝"
        # المشرف لا يرى أن إيميلاً رُفض سابقاً من مشرف آخر (يظهر له كأنه جديد)
        rj_badge = ""
        return (f"{type_icon} {tier_icon} {email_display} — ${amount:.2f}{rj_badge}",
                f"admin_request_detail:{req['user_id']}:{account_callback_token(email)}")

    buttons = paginate_buttons(items, page, "admin_requests", label)
    buttons.append((t(uid, "admin_btn_settings"), "admin_settings"))
    await query.edit_message_text(
        t(uid, "admin_req_list_title", count=len(items)),
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def admin_request_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if not is_admin(uid):
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    token = parts[2]
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_requests"), "admin_requests:0"))
        return
    index, request = match
    if admin_already_rejected(request, uid):
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_requests"), "admin_requests:0"))
        return
    email = request.get("email", "")
    has_totp = request.get("has_totp", False)
    has_app_pass = request.get("has_app_pass", False)
    original_amount = float(request.get("amount", 0.0))
    acc_type = request.get("account_type", "old")
    full_price_cents = money_to_cents(get_tier_prices(acc_type)["tier_3"])
    original_amount_cents = money_to_cents(original_amount)
    original_amount = cents_to_money(original_amount_cents)
    full_price = cents_to_money(full_price_cents)
    admin_bonus = cents_to_money(max(0, full_price_cents - original_amount_cents))

    type_text = t(uid, "appr_type_new") if acc_type == "new" else t(uid, "appr_type_old")
    msg = t(uid, "admin_req_detail_title", type=type_text)
    msg += t(uid, "admin_req_detail_seller", name=tg_html_escape(request.get('user_name', 'unknown')), username=tg_html_escape(request.get('user_username', 'none')))
    msg += t(uid, "admin_req_detail_email", email=tg_html_escape(email))
    msg += t(uid, "admin_req_detail_pass", password=tg_html_escape(request.get('password', '')))
    if has_totp:
        msg += t(uid, "admin_req_detail_totp", totp=tg_html_escape(request.get('totp', '')))
    else:
        msg += t(uid, "admin_req_detail_totp_none")
    msg += t(uid, "admin_req_detail_app_pass", status="✅" if has_app_pass else t(uid, "appr_none"))
    msg += t(uid, "admin_req_detail_amounts", orig=original_amount, full=full_price, bonus=admin_bonus)

    # ═══ NEW: عرض حالة الرفض المتعدد ═══
    rejected_by_admins = request.get("rejected_by_admins", []) or []
    if rejected_by_admins and uid == OWNER_ID:
        msg += f"\n\n⚠️ <b>عدد المشرفين الذين رفضوه:</b> <code>{len(rejected_by_admins)}/{REJECTIONS_REQUIRED_FOR_FINAL}</code>\n"
        for rj in rejected_by_admins:
            msg += f"  • <code>{rj.get('admin_id')}</code> — {tg_html_escape(rj.get('admin_name', 'Unknown'))} ({REJECT_REASON_LABELS.get(rj.get('reason', ''), rj.get('reason', ''))})\n"

    buttons = []
    if has_totp and request.get("totp"):
        buttons.append((t(uid, "appr_show_code"), f"admin_show_code:{target_uid}:{account_callback_token(email)}"))

    # يمكن للأدمن إكمال الإيميل (قديم أو جديد) إذا كان ناقصاً
    if not has_totp:
        buttons.append((t(uid, "appr_complete_both"), f"admin_complete_start:{target_uid}:{account_callback_token(email)}"))
    elif not has_app_pass:
        buttons.append((t(uid, "appr_complete_app"), f"admin_complete_start:{target_uid}:{account_callback_token(email)}"))

    buttons.append((t(uid, "appr_reject"), f"reject_request:{target_uid}:{account_callback_token(email)}"))
    buttons.append((t(uid, "appr_btn_requests"), "admin_requests:0"))
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))






async def admin_show_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if not is_admin(uid):
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer(t(uid, "msg_error"), show_alert=True)
        return
    try:
        target_uid = int(parts[1])
        token = parts[2]
    except (ValueError, IndexError):
        await query.answer(t(uid, "msg_error"), show_alert=True)
        return

    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.answer(t(uid, "appr_not_found"), show_alert=True)
        return
    index, request = match
    if recovery_check_applies(uid) and not request.get("recovery_verified"):
        await admin_recovery_step(update, context, target_uid, token)
        return
    email = request.get("email", "")
    totp_secret = request.get("totp", "")

    if not totp_secret:
        await query.answer(t(uid, "appr_no_totp"), show_alert=True)
        return

    ok, code, err, seconds = generate_totp_code_info(totp_secret)
    if not ok:
        await query.answer(t(uid, "msg_error"), show_alert=True)
        return

    await query.answer(t(uid, "appr_code_sent"), show_alert=False)
    try:
        await send_totp_code_message(
            context=context,
            chat_id=uid,
            totp_secret=totp_secret,
            email=email,
            title=t(uid, "appr_code_title_admin"),
            back_callback=f"admin_show_code:{target_uid}:{account_callback_token(email)}",
            auto_refresh=True,
        )
    except Exception as exc:
        logger.exception("Failed to send TOTP code to admin")
        await query.edit_message_text(
            t(uid, "appr_code_fallback", code=code, seconds=seconds),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_vertical([
                (t(uid, "appr_new_code"), f"admin_show_code:{target_uid}:{account_callback_token(email)}"),
                (t(uid, "appr_btn_back_detail"), f"admin_request_detail:{target_uid}:{account_callback_token(email)}"),
            ]))


async def admin_complete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if not is_admin(uid):
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    token = parts[2]
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_requests"), "admin_requests:0"))
        return
    index, request = match

    if recovery_check_applies(uid) and not request.get("recovery_verified"):
        await admin_recovery_step(update, context, target_uid, token)
        return

    context.user_data["admin_completing_uid"] = target_uid
    context.user_data["admin_completing_index"] = index
    context.user_data["admin_completing_token"] = account_callback_token(request.get("email", ""))
    _tok = account_callback_token(request.get("email", ""))

    if not recovery_can_complete(uid):
        rows = [_finish_button(target_uid, _tok)] if recovery_can_finish(uid) else []
        rows.append((t(uid, "btn_cancel"), f"admin_request_detail:{target_uid}:{_tok}"))
        await query.edit_message_text(
            "🚫 المالك لم يمنحك صلاحية «الإكمال» لهذا النوع." +
            ("\nيمكنك «إنهاء» الإيميل ببريد الاسترداد فقط." if recovery_can_finish(uid) else ""),
            reply_markup=kb_vertical(rows))
        return

    def _with_finish(cancel_cb: str):
        rows = []
        if recovery_can_finish(uid) and request.get("recovery_verified"):
            rows.append(_finish_button(target_uid, _tok))
        rows.append((t(uid, "btn_cancel"), cancel_cb))
        return kb_vertical(rows)

    if not request.get("has_totp", False):
        context.user_data["admin_approval_step"] = "waiting_totp"
        await query.edit_message_text(
            t(uid, "appr_waiting_totp_step", email=request.get('email', '')),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_with_finish(f"admin_request_detail:{target_uid}:{_tok}"))
        return
    if not request.get("has_app_pass", False):
        context.user_data["admin_approval_step"] = "waiting_app_pass"
        await query.edit_message_text(
            t(uid, "appr_waiting_app_step", email=request.get('email', '')),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_with_finish(f"admin_request_detail:{target_uid}:{_tok}"))
        return
    await query.edit_message_text(t(uid, "appr_already_complete"),
                                  reply_markup=kb_single(t(uid, "appr_btn_requests"), "admin_requests:0"))


async def _recovery_gate_for_input(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                   target_uid, index, token) -> bool:
    """للمشرفين المحددين من المالك: لا يُقبل أي إدخال 2FA أو App Password قبل التحقق من بريد الاسترداد.
    يرجع True إذا حوّل المشرف إلى خطوة بريد الاسترداد (ويجب إيقاف المعالج الحالي)."""
    uid = update.effective_user.id
    if not recovery_check_applies(uid):
        return False
    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    request = None
    if token:
        match = find_pending_request(user_data, token)
        if match is not None:
            index, request = match
    if request is None and index is not None and 0 <= index < len(pending):
        request = pending[index]
    if request is None or request.get("recovery_verified"):
        return False
    await admin_recovery_step(update, context, target_uid, account_callback_token(request.get("email", "")))
    return True


async def handle_admin_totp_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if text.casefold() in {"إلغاء", "الغاء", "cancel", "отмена"}:
        for k in ("admin_completing_uid", "admin_completing_index",
                  "admin_completing_token", "admin_approval_step"):
            context.user_data.pop(k, None)
        await update.message.reply_text(t(uid, "msg_cancelled"),
                                        reply_markup=kb_single(t(uid, "appr_btn_requests"), "admin_requests:0"))
        return
    target_uid = context.user_data.get("admin_completing_uid")
    index = context.user_data.get("admin_completing_index")
    token = context.user_data.get("admin_completing_token")
    if target_uid is None or (index is None and token is None):
        await update.message.reply_text(t(uid, "msg_error"))
        return
    if await _recovery_gate_for_input(update, context, target_uid, index, token):
        return
    cleaned = text.replace(" ", "").upper()
    if len(cleaned) != 32 or not re.match(r'^[A-Z2-7]{32}$', cleaned):
        await update.message.reply_text(t(uid, "add_totp_invalid"))
        return
    try:
        pyotp.TOTP(cleaned).now()
    except Exception:
        await update.message.reply_text(t(uid, "add_totp_bad"))
        return

    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    if token:
        match = find_pending_request(user_data, token)
        if match is not None:
            index, _ = match
    if index is None or index >= len(pending):
        await update.message.reply_text(t(uid, "appr_not_found"))
        return
    pending[index]["totp"] = cleaned
    pending[index]["has_totp"] = True
    user_data["pending_requests"] = pending
    save_user(target_uid, user_data)

    if not pending[index].get("has_app_pass", False):
        context.user_data["admin_approval_step"] = "waiting_app_pass"
        try:
            await update.message.delete()
        except Exception:
            pass
        await update.message.reply_text(
            t(uid, "appr_totp_saved_wait_app"),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single(t(uid, "btn_cancel"),
                                   f"admin_request_detail:{target_uid}:{account_callback_token(pending[index].get('email', ''))}"))
    else:
        await prompt_admin_acceptance_choice(update, context, target_uid, index)


async def handle_admin_app_pass_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if text.casefold() in {"إلغاء", "الغاء", "cancel", "отмена"}:
        for k in ("admin_completing_uid", "admin_completing_index",
                  "admin_completing_token", "admin_approval_step"):
            context.user_data.pop(k, None)
        await update.message.reply_text(t(uid, "msg_cancelled"),
                                        reply_markup=kb_single(t(uid, "appr_btn_requests"), "admin_requests:0"))
        return
    target_uid = context.user_data.get("admin_completing_uid")
    index = context.user_data.get("admin_completing_index")
    token = context.user_data.get("admin_completing_token")
    if target_uid is None or (index is None and token is None):
        await update.message.reply_text(t(uid, "msg_error"))
        return
    if await _recovery_gate_for_input(update, context, target_uid, index, token):
        return
    cleaned = text.replace(" ", "")
    if len(cleaned) != 16 or not re.match(r'^[A-Za-z0-9]{16}$', cleaned):
        await update.message.reply_text(t(uid, "add_app_pass_invalid"))
        return
    if has_active_app_password(cleaned):
        await update.message.reply_text(t(uid, "add_app_pass_used"))
        return

    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    if token:
        match = find_pending_request(user_data, token)
        if match is not None:
            index, _ = match
    if index is None or index >= len(pending):
        await update.message.reply_text(t(uid, "appr_not_found"))
        return
    pending[index]["app_pass"] = cleaned
    pending[index]["has_app_pass"] = True
    user_data["pending_requests"] = pending
    save_user(target_uid, user_data)
    try:
        await update.message.delete()
    except Exception:
        pass
    await prompt_admin_acceptance_choice(update, context, target_uid, index)


async def prompt_admin_acceptance_choice(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                          target_uid: int, index: int):
    uid = update.effective_user.id
    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    if index is None or index >= len(pending):
        await update.effective_message.reply_text(t(uid, "appr_not_found"))
        return

    request = pending[index]
    if not all(request.get(field) for field in ("email", "password", "totp", "app_pass")):
        await update.effective_message.reply_text(t(uid, "appr_incomplete"))
        return

    context.user_data["admin_completing_uid"] = target_uid
    context.user_data["admin_completing_index"] = index
    token = account_callback_token(request.get("email", ""))
    context.user_data["admin_completing_token"] = token
    context.user_data["admin_approval_step"] = "waiting_acceptance_choice"

    await update.effective_message.reply_text(
        t(uid, "appr_all_info_received", email=request.get('email', '')),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                t(uid, "appr_btn_continue_verify"),
                callback_data=f"admin_accept_auto:{target_uid}:{token}")],
            [InlineKeyboardButton(
                t(uid, "btn_cancel"),
                callback_data=f"admin_completion_cancel:{target_uid}:{token}")],
        ]))


async def admin_completion_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if not is_admin(uid):
        await query.answer("🚫", show_alert=True)
        return
    for key in ("admin_completing_uid", "admin_completing_index",
                "admin_completing_token", "admin_approval_step"):
        context.user_data.pop(key, None)
    await admin_request_detail(update, context)


async def admin_accept_completed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if not is_admin(uid):
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    token = parts[2]
    match = find_pending_request(get_user(target_uid), token)
    if match is None:
        await query.edit_message_text(
            t(uid, "appr_not_found"),
            reply_markup=kb_single(t(uid, "appr_btn_requests"), "admin_requests:0"))
        return
    index, _req = match
    if recovery_check_applies(uid) and not _req.get("recovery_verified"):
        await admin_recovery_step(update, context, target_uid, token)
        return
    await admin_verify_and_store(update, context, target_uid, index, hold_seconds=LEAVE_HOLD_SECONDS)


async def admin_verify_and_store(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                  target_uid: int, index: int,
                                  hold_seconds: int = LEAVE_HOLD_SECONDS):
    from telegram_bot.members import schedule_leave_check
    admin_id = update.effective_user.id
    reply_target = update.effective_message
    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await reply_target.reply_text(t(admin_id, "appr_not_found"))
        return
    request = pending[index]
    email = request.get("email", "")
    password = request.get("password", "")
    totp = request.get("totp", "")
    app_pass = request.get("app_pass", "")
    acc_type = request.get("account_type", "old")

    if not email or not password or not totp or not app_pass:
        await reply_target.reply_text(t(admin_id, "appr_incomplete"))
        return

    allowed, wait = imap_rate_ok(email)
    if not allowed:
        await reply_target.reply_text(
            t(admin_id, "add_rate_wait", wait=wait),
            reply_markup=kb_single(t(admin_id, "appr_btn_requests"), "admin_requests:0"))
        return
    imap_rate_mark(email)

    progress_msg = await reply_target.reply_text(
        t(admin_id, "appr_verifying_imap", email=email),
        parse_mode=ParseMode.MARKDOWN)

    verify_result = await verify_account_credentials(
        email=email, password=password, app_pass=app_pass, totp_secret=totp)

    if not verify_result["imap_ok"]:
        try:
            await progress_msg.delete()
        except Exception:
            pass

        pending.pop(index)
        move_request_to_rejected(user_data, request, "other",
                                 f"Auto IMAP failed (admin): {verify_result['message'][:150]}")
        user_data["pending_requests"] = pending
        if user_data.get("rejected_requests"):
            user_data["rejected_requests"][-1]["rejected_by"] = admin_id
            user_data["rejected_requests"][-1]["rejected_by_name"] = "Auto Verify"
            user_data["rejected_requests"][-1]["rejected_at"] = datetime.now(timezone.utc).isoformat()
            user_data["rejected_requests"][-1]["imap_auto_rejected"] = True  # NEW
        save_user(target_uid, user_data)

        await reply_target.reply_text(
            t(admin_id, "appr_fail_verify_rejected", email=tg_html_escape(email), msg=tg_html_escape(verify_result['message'])),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single(t(admin_id, "appr_btn_requests"), "admin_requests:0"))

        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=t(target_uid, "member_rejected_imap_fail", email=tg_html_escape(email)),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return

    # ═══ نجح التحقق → قبول الحساب ═══
    original_amount_cents = money_to_cents(request.get("amount", 0.0))
    full_price_cents = money_to_cents(get_tier_prices(acc_type)["tier_3"])
    original_amount = cents_to_money(original_amount_cents)
    admin_bonus = cents_to_money(max(0, full_price_cents - original_amount_cents))

    # ═══ NEW: تسجيل نشاط الأدمن ═══
    record_admin_activity(admin_id, "accept")

    # ═══ NEW: عقوبة المشرفين الذين رفضوا هذا الحساب سابقاً ═══
    rejected_by_admins = request.get("rejected_by_admins", []) or []
    if rejected_by_admins and admin_id != OWNER_ID and admin_bonus > 0:
        penalty_cents = money_to_cents(admin_bonus)
        for rj in rejected_by_admins:
            try:
                rj_admin_id = int(rj.get("admin_id", 0))
            except (TypeError, ValueError):
                continue
            if rj_admin_id and rj_admin_id != admin_id and rj_admin_id != OWNER_ID:
                deduct_from_admin_balance(
                    rj_admin_id, penalty_cents,
                    "رفض خاطئ ثم قبول من مشرف آخر", email)
                try:
                    await context.bot.send_message(
                        chat_id=rj_admin_id,
                        text=(f"⚠️ <b>عقوبة رفض خاطئ</b>\n\n"
                              f"📧 <code>{tg_html_escape(email)}</code>\n"
                              f"💰 تم خصم <b>${admin_bonus:.2f}</b> من رصيدك\n"
                              f"📌 <i>السبب: رفضت إيميلاً وقبله مشرف آخر.</i>"),
                        parse_mode=ParseMode.HTML)
                except Exception:
                    pass

    try:
        admin_user = update.effective_user
        admin_name_str = admin_user.full_name or "Unknown"
        admin_username = admin_user.username or "None"
    except Exception:
        admin_name_str = "Unknown"
        admin_username = "None"

    approval_time = datetime.now(timezone.utc)
    account_record = {
        "email": email, "password": password, "totp": totp, "app_pass": app_pass,
        "amount": original_amount, "admin_bonus": admin_bonus,
        "completed_by_admin": admin_id, "completed_by_admin_name": admin_name_str,
        "completed_by_admin_username": admin_username, "completed_at": approval_time.isoformat(),
        "timestamp": approval_time.isoformat(), "approval_time": approval_time.isoformat(),
        "release_at": (approval_time + timedelta(seconds=hold_seconds)).isoformat(),
        "hold_seconds": hold_seconds, "acceptance_mode": "automatic",
        "admin_bonus_status": ("pending" if admin_id != OWNER_ID and admin_bonus > 0 else "not_applicable"),
        "extracted": False, "has_totp": True, "has_app_pass": True,
        "user_name": request.get("user_name", "Unknown"),
        "user_username": request.get("user_username", "None"),
        "approved_with_leave": True, "leave_confirmed": False,
        "auto_verified": True, "account_type": acc_type,
        "verification": {
            "level": verify_result["level"], "badge": verify_result["badge"],
            "message": verify_result["message"], "imap_ok": verify_result["imap_ok"],
            "totp_ok": verify_result["totp_ok"], "verified_at": approval_time.isoformat(),
            "verified_by": "admin_completion", "admin_id": admin_id,
        },
    }

    user_data.setdefault("approved_accounts", []).append(account_record)
    user_data["pending_balance"] = clamp_money(float(user_data.get("pending_balance", 0.0)) - original_amount)
    user_data["hold_balance"] = clamp_money(float(user_data.get("hold_balance", 0.0)) + original_amount)
    user_data["total_credited_balance"] = clamp_money(float(user_data.get("total_credited_balance", 0.0) or 0.0) + original_amount)
    user_data["total_approved_emails"] = int(user_data.get("total_approved_emails", 0)) + 1
    pending.pop(index)
    user_data["pending_requests"] = pending
    hold_hours = hold_seconds // 3600
    add_transaction(user_data, "hold", original_amount, f"Completed by admin {admin_id} - hold {hold_hours}h", email)
    register_approval_reset(user_data)
    save_user(target_uid, user_data)

    try:
        await check_contest_award(context, target_uid)
    except Exception:
        logger.exception("Contest check failed")

    if admin_bonus > 0 and admin_id != OWNER_ID:
        admin_data = get_user(admin_id)
        admin_data["admin_pending_balance"] = round(
            float(admin_data.get("admin_pending_balance", 0.0)) + admin_bonus, 2)
        add_transaction(admin_data, "admin_bonus_pending", admin_bonus, f"Pending bonus for {email}", email)
        save_user(admin_id, admin_data)
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=t(admin_id, "admin_bonus_pending_msg", email=email, bonus=admin_bonus),
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    await schedule_leave_check(context, target_uid, email, account_record["release_at"])

    referred_by = user_data.get("referred_by")
    if referred_by:
        config_ref = load_config()
        referral_bonus = float(config_ref.get("referral_bonus", 0.0))
        if referral_bonus > 0:
            referrer_data = get_user(referred_by)
            referrer_data["referral_earnings"] = clamp_money(float(referrer_data.get("referral_earnings", 0.0)) + referral_bonus)
            referrer_data["balance"] = clamp_money(float(referrer_data.get("balance", 0.0)) + referral_bonus)
            referrer_data["total_credited_balance"] = clamp_money(float(referrer_data.get("total_credited_balance", 0.0) or 0.0) + referral_bonus)
            referrer_data["total_referrals"] = int(referrer_data.get("total_referrals", 0)) + 1
            add_transaction(referrer_data, "referral", referral_bonus, f"Referral bonus for user {target_uid}", email)
            save_user(referred_by, referrer_data)
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=t(referred_by, "referral_bonus_received", bonus=referral_bonus),
                    parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
        try:
            await context.bot.send_message(
                chat_id=referred_by,
                text=t(referred_by, "referral_approved_msg", uid=target_uid, email=email),
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=t(OWNER_ID, "owner_admin_completed_msg", admin_id=admin_id, admin_username=admin_username,
                   uid=target_uid, email=email, bonus=admin_bonus, hours=hold_hours),
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

    try:
        await progress_msg.delete()
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=t(target_uid, "member_account_verified_msg", email=email, amount=original_amount, hours=hold_hours),
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

    for k in ("admin_completing_uid", "admin_completing_index", "admin_approval_step"):
        context.user_data.pop(k, None)

    await reply_target.reply_text(
        t(admin_id, "admin_account_accepted_msg", email=email, amount=original_amount, bonus=admin_bonus),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single(t(admin_id, "appr_btn_requests"), "admin_requests:0"))


# ==================== TIER PRICES ====================
async def set_tier_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    prices_old = get_tier_prices("old")
    prices_new = get_tier_prices("new")
    buttons = [
        (t(uid, "price_old_1"), "set_tier:old:1"),
        (t(uid, "price_old_2"), "set_tier:old:2"),
        (t(uid, "price_old_3"), "set_tier:old:3"),
        (t(uid, "price_new_1"), "set_tier:new:1"),
        (t(uid, "price_new_2"), "set_tier:new:2"),
        (t(uid, "price_new_3"), "set_tier:new:3"),
        (t(uid, "owner_btn_back_panel"), "owner_panel")
    ]
    msg = t(uid, "price_menu", o1=prices_old['tier_1'], o2=prices_old['tier_2'], o3=prices_old['tier_3'],
            n1=prices_new['tier_1'], n2=prices_new['tier_2'], n3=prices_new['tier_3'])
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def set_tier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    acc_type = parts[1]
    tier = parts[2]
    context.user_data["setting_tier"] = tier
    context.user_data["setting_type"] = acc_type
    type_name = t(uid, "price_old") if acc_type == "old" else t(uid, "price_new")
    await query.edit_message_text(t(uid, "price_edit_prompt", type=type_name, tier=tier),
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single(t(uid, "btn_cancel"), "set_tier_prices"))
    context.user_data["mode"] = "set_tier_price"


# ==================== APPROVAL REQUESTS ====================
async def approval_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    buttons = [
        (t(uid, "appr_btn_pending"), "view_pending:0"),
        (t(uid, "appr_btn_approved"), "view_approved:0"),
        (t(uid, "appr_btn_rejected"), "view_rejected:0"),
        (t(uid, "owner_btn_back_panel"), "owner_panel")
    ]
    await query.edit_message_text(t(uid, "appr_title"), parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


def _collect_all(items_field: str, sort_order: str = "newest") -> List[dict]:
    users = load_json_readonly(USERS_DB)
    items = []
    for uid, user_data in users.items():
        for idx, item in enumerate(user_data.get(items_field, []) or []):
            copy = dict(item)
            copy["user_id"] = uid
            copy["index"] = idx
            items.append(copy)
    try:
        if sort_order == "oldest":
            items.sort(key=_record_sort_key)
        else:
            items.sort(key=_record_sort_key, reverse=True)
    except Exception:
        pass
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
        nav.append((t(OWNER_ID, "pagination_prev"), f"{prefix}:{page-1}"))
    nav.append((t(OWNER_ID, "pagination_page", page=page+1, total=total_pages), "noop"))
    if page < total_pages - 1:
        nav.append((t(OWNER_ID, "pagination_next"), f"{prefix}:{page+1}"))
    if len(nav) > 1:
        buttons.append(nav)
    return buttons


async def view_pending_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    try:
        page = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    pending = _collect_all("pending_requests", sort_order="oldest")
    if not pending:
        await query.edit_message_text(t(uid, "appr_empty"),
                                      reply_markup=kb_single(t(uid, "appr_title"), "approval_requests"))
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
        acc_type = req.get("account_type", "old")
        type_icon = "🆕" if acc_type == "new" else "📝"
        rj_count = request_rejection_count(req)
        rj_badge = f" {rejection_squares(rj_count)}" if rj_count > 0 else ""
        return (f"{type_icon}{v_badge}{tier_icon} {email_display}{rj_badge}",
                f"pending_detail:{req['user_id']}:{account_callback_token(email)}")

    buttons = paginate_buttons(pending, page, "view_pending", label)
    buttons.append((t(uid, "appr_title"), "approval_requests"))
    await query.edit_message_text(
        t(uid, "appr_pending_title", count=len(pending)),
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def pending_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    token = parts[2]
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_pending"), "view_pending:0"))
        return
    index, request = match
    request_token = account_callback_token(request.get("email", ""))
    email = request.get("email", "")
    tier_icon = "🟢" if request.get("has_app_pass") else "🟡" if request.get("has_totp") else "🔵"
    tier_text = t(uid, "appr_tier_full") if request.get("has_app_pass") else t(uid, "appr_tier_totp") if request.get("has_totp") else t(uid, "appr_tier_pass_only")
    acc_type = request.get("account_type", "old")
    type_text = t(uid, "appr_type_new") if acc_type == "new" else t(uid, "appr_type_old")

    msg = t(uid, "owner_pending_detail_title", type=type_text)
    msg += t(uid, "admin_req_detail_seller", name=tg_html_escape(request.get('user_name', 'unknown')), username=tg_html_escape(request.get('user_username', 'none')))
    msg += t(uid, "admin_req_detail_email", email=tg_html_escape(email))
    msg += t(uid, "admin_req_detail_pass", password=tg_html_escape(request.get('password', '')))
    if request.get("has_totp", False):
        msg += t(uid, "admin_req_detail_totp", totp=tg_html_escape(request.get('totp', '')))
    else:
        msg += t(uid, "admin_req_detail_totp_none")
    if request.get("has_app_pass", False):
        formatted = tg_html_escape(format_app_password(request.get("app_pass", "")))
        msg += t(uid, "admin_req_detail_app_pass", status=f"<code>{formatted}</code>")
    else:
        msg += t(uid, "admin_req_detail_app_pass", status=t(uid, "appr_none"))
    msg += t(uid, "owner_pending_detail_misc", tier_icon=tier_icon, tier_text=tier_text, uid=target_uid, amount=request.get('amount', 0))

    # ═══ NEW: عرض حالة الرفض المتعدد ═══
    rejected_by_admins = request.get("rejected_by_admins", []) or []
    if rejected_by_admins:
        msg += f"\n\n⚠️ <b>رفض من {len(rejected_by_admins)}/{REJECTIONS_REQUIRED_FOR_FINAL} مشرف:</b>\n"
        for rj in rejected_by_admins:
            msg += f"  • <code>{rj.get('admin_id')}</code> — {tg_html_escape(rj.get('admin_name', 'Unknown'))} ({REJECT_REASON_LABELS.get(rj.get('reason', ''), rj.get('reason', ''))})\n"

    buttons = []
    if request.get("has_totp", False) and request.get("totp"):
        buttons.append((t(uid, "appr_show_code"), f"owner_show_code:{target_uid}:{request_token}"))

    # يمكن للمالك إكمال الإيميل (قديم أو جديد) إذا كان ناقصاً
    if not request.get("has_totp", False) or not request.get("has_app_pass", False):
        buttons.append((t(uid, "appr_complete"), f"complete_request_owner:{target_uid}:{request_token}"))

    buttons.append((t(uid, "appr_accept"), f"approve_request:{target_uid}:{request_token}"))
    if request.get("password") or request.get("app_pass"):
        buttons.append((t(uid, "appr_auto_verify"), f"auto_verify:{target_uid}:{request_token}"))
    buttons.append((t(uid, "appr_reject"), f"reject_request:{target_uid}:{request_token}"))
    buttons.append((t(uid, "appr_btn_pending"), "view_pending:0"))
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def auto_verify_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    token = parts[2]
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_pending"), "view_pending:0"))
        return
    index, request = match
    email = request.get("email", "")
    password = request.get("password", "")
    app_pass = request.get("app_pass", "")
    totp_secret = request.get("totp", "")

    if not password and not app_pass:
        await query.answer(t(uid, "appr_no_credentials"), show_alert=True)
        return

    allowed, wait = imap_rate_ok(email)
    if not allowed:
        await query.answer(t(uid, "add_rate_wait", wait=wait), show_alert=True)
        return
    imap_rate_mark(email)

    await query.answer(t(uid, "appr_verifying_short"), show_alert=False)

    result = await verify_account_credentials(email=email, password=password,
                                              app_pass=app_pass, totp_secret=totp_secret)

    request["verification"] = {
        "level": result["level"], "badge": result["badge"], "message": result["message"],
        "imap_ok": result["imap_ok"], "totp_ok": result["totp_ok"],
        "category": result.get("category", "unknown"),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "verified_by": "owner_manual",
    }
    user_data["pending_requests"][index] = request
    save_user(target_uid, user_data)

    if result["level"] == "verified":
        title = t(uid, "appr_verify_ok")
    elif result["level"] == "partial":
        title = t(uid, "appr_verify_partial")
    elif result["level"] == "unknown":
        title = t(uid, "appr_verify_unknown")
    else:
        title = t(uid, "appr_verify_failed")

    result_msg = f"{title}\n\n"
    result_msg += t(uid, "appr_verify_result", email=tg_html_escape(email), imap="✅" if result['imap_ok'] else "❌")
    if totp_secret:
        result_msg += t(uid, "appr_verify_totp", status="✅" if result['totp_ok'] else "❌")
    result_msg += t(uid, "appr_verify_msg", msg=tg_html_escape(result['message']))

    request_token = account_callback_token(email)
    buttons = [
        (t(uid, "appr_verify_again"), f"auto_verify:{target_uid}:{request_token}"),
        (t(uid, "appr_accept"), f"approve_request:{target_uid}:{request_token}"),
        (t(uid, "appr_reject"), f"reject_request:{target_uid}:{request_token}"),
        (t(uid, "appr_btn_back_detail"), f"pending_detail:{target_uid}:{request_token}")
    ]
    await query.edit_message_text(result_msg, parse_mode=ParseMode.HTML,
                                  reply_markup=kb_vertical(buttons))


async def approve_request_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    token = parts[2]
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_pending"), "view_pending:0"))
        return
    index, _ = match
    await admin_verify_and_store(update, context, target_uid, index, hold_seconds=LEAVE_HOLD_SECONDS)


async def owner_show_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer(t(uid, "msg_error"), show_alert=True)
        return
    try:
        target_uid = int(parts[1])
        token = parts[2]
    except (ValueError, IndexError):
        await query.answer(t(uid, "msg_error"), show_alert=True)
        return

    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.answer(t(uid, "appr_not_found"), show_alert=True)
        return
    index, request = match
    email = request.get("email", "")
    totp_secret = request.get("totp", "")

    if not totp_secret:
        await query.answer(t(uid, "appr_no_totp"), show_alert=True)
        return

    ok, code, err, seconds = generate_totp_code_info(totp_secret)
    if not ok:
        await query.answer(t(uid, "msg_error"), show_alert=True)
        return

    await query.answer(t(uid, "appr_code_sent"), show_alert=False)
    try:
        await send_totp_code_message(
            context=context,
            chat_id=uid,
            totp_secret=totp_secret,
            email=email,
            title=t(uid, "appr_code_title_owner"),
            back_callback=f"owner_show_code:{target_uid}:{account_callback_token(email)}",
            auto_refresh=True,
        )
    except Exception as exc:
        logger.exception("Failed to send TOTP code to owner")
        await query.edit_message_text(
            t(uid, "appr_code_fallback", code=code, seconds=seconds),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_vertical([
                (t(uid, "appr_new_code"), f"owner_show_code:{target_uid}:{account_callback_token(email)}"),
                (t(uid, "appr_btn_back_detail"), f"pending_detail:{target_uid}:{account_callback_token(email)}"),
            ]))


async def complete_request_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    match = find_pending_request(get_user(int(parts[1])), parts[2])
    if match is None:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_pending"), "view_pending:0"))
        return
    target_uid = int(parts[1])
    index, approved_request = match

    context.user_data["approval_uid"] = target_uid
    context.user_data["approval_index"] = index
    context.user_data["approval_token"] = parts[2]
    context.user_data["approval_data"] = approved_request
    context.user_data["approval_with_leave"] = False

    if not approved_request.get("has_totp", False):
        context.user_data["approval_step"] = "waiting_totp"
        await query.edit_message_text(
            t(uid, "appr_owner_complete_title_totp", email=tg_html_escape(approved_request.get('email', ''))),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single(t(uid, "btn_cancel"), f"pending_detail:{target_uid}:{parts[2]}"))
        return

    if not approved_request.get("has_app_pass", False):
        context.user_data["approval_step"] = "waiting_app_pass"
        await query.edit_message_text(
            t(uid, "appr_owner_complete_title_app", email=tg_html_escape(approved_request.get('email', ''))),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single(t(uid, "btn_cancel"), f"pending_detail:{target_uid}:{parts[2]}"))
        return

    await query.edit_message_text(t(uid, "appr_already_complete"))


async def handle_approval_totp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    target_uid = context.user_data.get("approval_uid")
    index = context.user_data.get("approval_index")
    approved_request = context.user_data.get("approval_data")
    if not target_uid or index is None or not approved_request:
        await update.message.reply_text(t(uid, "msg_error"))
        return
    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await update.message.reply_text(t(uid, "appr_not_found"))
        return
    if text.lower() == "تخطي" or text.lower() == "skip":
        approved_request["totp"] = ""
        approved_request["has_totp"] = False
        context.user_data["approval_data"] = approved_request
        if not approved_request.get("has_app_pass", False):
            context.user_data["approval_step"] = "waiting_app_pass"
            await update.message.reply_text(
                t(uid, "appr_skip_totp_wait_app"),
                parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(t(uid, "appr_ready_for_accept"))
        return
    cleaned = text.replace(" ", "").upper()
    if len(cleaned) != 32 or not re.match(r'^[A-Z2-7]{32}$', cleaned):
        await update.message.reply_text(t(uid, "add_totp_invalid"))
        return
    try:
        code = pyotp.TOTP(cleaned).now()
        approved_request["totp"] = cleaned
        approved_request["has_totp"] = True
        pending[index] = approved_request
        user_data["pending_requests"] = pending
        save_user(target_uid, user_data)
        context.user_data["approval_data"] = approved_request

        try:
            await update.message.delete()
        except Exception:
            pass

        if not approved_request.get("has_app_pass", False):
            context.user_data["approval_step"] = "waiting_app_pass"
            await context.bot.send_message(
                chat_id=uid,
                text=t(uid, "appr_totp_valid_wait_app", code=code),
                parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_message(
                chat_id=uid,
                text=t(uid, "appr_totp_valid_wait_app", code=code),
                parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(t(uid, "add_totp_bad"))


async def handle_approval_app_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    target_uid = context.user_data.get("approval_uid")
    index = context.user_data.get("approval_index")
    approved_request = context.user_data.get("approval_data")
    if not target_uid or index is None or not approved_request:
        await update.message.reply_text(t(uid, "msg_error"))
        return
    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await update.message.reply_text(t(uid, "appr_not_found"))
        return
    if text.lower() == "تخطي" or text.lower() == "skip":
        approved_request["app_pass"] = ""
        approved_request["has_app_pass"] = False
        context.user_data["approval_data"] = approved_request
        await update.message.reply_text(t(uid, "appr_skip_app_pass"))
        return
    cleaned = text.replace(" ", "")
    if len(cleaned) != 16 or not re.match(r'^[A-Za-z0-9]{16}$', cleaned):
        await update.message.reply_text(t(uid, "add_app_pass_invalid"))
        return
    if has_active_app_password(cleaned):
        await update.message.reply_text(t(uid, "add_app_pass_used"))
        return
    approved_request["app_pass"] = cleaned
    approved_request["has_app_pass"] = True
    pending[index] = approved_request
    user_data["pending_requests"] = pending
    save_user(target_uid, user_data)
    context.user_data["approval_data"] = approved_request

    try:
        await update.message.delete()
    except Exception:
        pass

    formatted = format_app_password(cleaned)
    await context.bot.send_message(
        chat_id=uid,
        text=t(uid, "appr_app_pass_received", formatted=formatted),
        parse_mode=ParseMode.MARKDOWN)


async def view_approved_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    try:
        page = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    approved = _collect_all("approved_accounts", sort_order="newest")
    if not approved:
        await query.edit_message_text(t(uid, "appr_empty"),
                                      reply_markup=kb_single(t(uid, "appr_title"), "approval_requests"))
        return

    def label(acc):
        if acc.get("rejected_at_24h"):
            tier_icon = "🔴"
        elif acc.get("has_app_pass"):
            tier_icon = "🟢"
        elif acc.get("has_totp"):
            tier_icon = "🟡"
        else:
            tier_icon = "🔵"
        user_name = acc.get("user_name", "Unknown")
        user_username = acc.get("user_username", "None")
        display_name = f"{user_name} (@{user_username})" if user_username != "None" else user_name
        email_display = acc.get('email', '')[:15] + "..." if len(acc.get('email', '')) > 15 else acc.get('email', '')
        acc_type = acc.get("account_type", "old")
        type_icon = "🆕" if acc_type == "new" else "📝"
        return (f"{type_icon}{tier_icon} {email_display} - {display_name[:12]} (${acc.get('amount', 0):.2f})",
                f"approved_detail:{acc['user_id']}:{account_callback_token(acc.get('email', ''))}")

    buttons = paginate_buttons(approved, page, "view_approved", label)
    buttons.append((t(uid, "appr_title"), "approval_requests"))
    await query.edit_message_text(
        t(uid, "appr_approved_title", count=len(approved)),
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def approved_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    token = parts[2]
    user_data = get_user(target_uid)
    account_match = find_approved_account(user_data, token)
    if account_match is None:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_approved"), "view_approved:0"))
        return
    index, account = account_match
    account_token = account_callback_token(account.get("email", ""))
    if account.get("rejected_at_24h"):
        tier_icon = "🔴"
        tier_text = t(uid, "appr_tier_rejected_24")
    elif account.get("has_app_pass"):
        tier_icon = "🟢"
        tier_text = t(uid, "appr_tier_full")
    elif account.get("has_totp"):
        tier_icon = "🟡"
        tier_text = t(uid, "appr_tier_totp")
    else:
        tier_icon = "🔵"
        tier_text = t(uid, "appr_tier_pass_only")

    acc_type = account.get("account_type", "old")
    type_text = t(uid, "appr_type_new") if acc_type == "new" else t(uid, "appr_type_old")

    msg = t(uid, "owner_approved_detail_title", type=type_text)
    msg += t(uid, "admin_req_detail_seller", name=tg_html_escape(account.get('user_name', 'unknown')), username=tg_html_escape(account.get('user_username', 'none')))
    msg += t(uid, "admin_req_detail_email", email=tg_html_escape(account.get('email', '')))
    msg += t(uid, "admin_req_detail_pass", password=tg_html_escape(account.get('password', '')))
    if account.get("has_totp", False):
        msg += t(uid, "admin_req_detail_totp", totp=tg_html_escape(account.get('totp', '')))
    else:
        msg += t(uid, "admin_req_detail_totp_none")
    if account.get("has_app_pass", False):
        msg += t(uid, "admin_req_detail_app_pass", status=f"<code>{tg_html_escape(format_app_password(account.get('app_pass', '')))}</code>")
    else:
        msg += t(uid, "admin_req_detail_app_pass", status=t(uid, "appr_none"))

    msg += t(uid, "owner_pending_detail_misc", tier_icon=tier_icon, tier_text=tier_text, uid=target_uid, amount=account.get('amount', 0))

    if account.get("rejected_at_24h"):
        msg += t(uid, "owner_approved_status_rejected")
    elif account.get("approved_with_leave", False) and not account.get("leave_confirmed", False):
        msg += t(uid, "owner_approved_status_hold")
    elif account.get("approved_with_leave", False) and account.get("leave_confirmed", False):
        msg += t(uid, "owner_approved_status_done")

    buttons = []
    if account.get("has_totp", False) and account.get("totp", ""):
        buttons.append((t(uid, "appr_new_code"), f"new_totp_code:{target_uid}:{account_token}"))
    buttons.append((t(uid, "appr_deduct_points"), f"deduct_points:{target_uid}:{account_token}"))
    if not account.get("rejected_at_24h"):
        buttons.insert(0, (t(uid, "appr_reject_approved"), f"reject_approved:{target_uid}:{account_token}"))
    buttons.append((t(uid, "appr_btn_approved"), "view_approved:0"))
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def reject_approved_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    token = parts[2]
    user_data = get_user(target_uid)
    account_match = find_approved_account(user_data, token)
    if account_match is None:
        await query.edit_message_text(
            t(uid, "appr_not_found"),
            reply_markup=kb_single(t(uid, "appr_btn_approved"), "view_approved:0"))
        return
    _, account = account_match
    if account.get("rejected_at_24h"):
        await query.answer(t(uid, "appr_already_rejected"), show_alert=True)
        return
    email = account.get("email", "")
    display_email = tg_html_escape(email)

    await query.edit_message_text(
        t(uid, "appr_reject_approved_title", email=display_email),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            (t(uid, "appr_reason_email"), f"reject_approved_reason:email:{target_uid}:{account_callback_token(email)}"),
            (t(uid, "appr_reason_password"), f"reject_approved_reason:password:{target_uid}:{account_callback_token(email)}"),
            (t(uid, "appr_reason_totp"), f"reject_approved_reason:totp:{target_uid}:{account_callback_token(email)}"),
            (t(uid, "appr_reason_app_pass"), f"reject_approved_reason:app_pass:{target_uid}:{account_callback_token(email)}"),
            (t(uid, "appr_reason_phone"), f"reject_approved_reason:phone:{target_uid}:{account_callback_token(email)}"),
            (t(uid, "appr_reason_other"), f"reject_approved_reason:other:{target_uid}:{account_callback_token(email)}"),
            (t(uid, "appr_btn_back_detail"), f"approved_detail:{target_uid}:{account_callback_token(email)}"),
        ]))


async def execute_reject_approved_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    reason_type = parts[1]
    target_uid = int(parts[2])
    token = parts[3]
    user_data = get_user(target_uid)
    account_match = find_approved_account(user_data, token)
    if account_match is None:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_approved"), "view_approved:0"))
        return
    index, account = account_match
    email = account.get("email", "")
    if reason_type == "other":
        context.user_data["reject_approved_uid"] = target_uid
        context.user_data["reject_approved_token"] = token
        context.user_data["step"] = "reject_approved_reason_text"
        await query.edit_message_text(
            t(uid, "appr_write_reason", email=tg_html_escape(email)),
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single(t(uid, "btn_cancel"), f"approved_detail:{target_uid}:{token}"))
        return
    await finalize_approved_rejection(update, context, target_uid, index, account, reason_type)


async def finalize_approved_rejection(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                       target_uid: int, index: int, account: dict,
                                       reason: str, reason_text: str = ""):
    admin_id = update.effective_user.id
    user_data = get_user(target_uid)
    accounts = user_data.get("approved_accounts", [])
    if index >= len(accounts) or accounts[index].get("email") != account.get("email"):
        match = find_approved_account(user_data, account_callback_token(account.get("email", "")))
        if match is None:
            await update.effective_message.reply_text(t(admin_id, "appr_not_found"))
            return
        index, account = match

    account = dict(account)
    email = account.get("email", "")
    amount = clamp_money(account.get("amount", 0.0))
    was_hold = account.get("approved_with_leave") and not account.get("leave_confirmed")
    was_released = bool(account.get("leave_confirmed")) and not account.get("rejected_at_24h")

    # ═══ إلغاء مكافأة الأدمن قبل النقل ═══
    try:
        if settle_admin_bonus(account, "rejected",
                               owner_data=user_data, owner_id=target_uid):
            account["admin_bonus_status"] = "rejected"
    except Exception:
        logger.exception("Failed to reject admin bonus on approved rejection")

    accounts.pop(index)
    user_data["approved_accounts"] = accounts
    move_approved_account_to_rejected(user_data, account, reason, reason_text)
    actor = update.effective_user
    if user_data.get("rejected_requests"):
        user_data["rejected_requests"][-1]["rejected_by"] = actor.id
        user_data["rejected_requests"][-1]["rejected_by_name"] = actor.full_name or "Unknown"
        user_data["rejected_requests"][-1]["rejected_by_username"] = actor.username or ""

    if was_hold:
        user_data["hold_balance"] = clamp_money(float(user_data.get("hold_balance", 0.0)) - amount)
        add_transaction(user_data, "approved_rejection", amount, "Rejected approved account, canceled hold", email)
    elif was_released:
        user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) - amount)
        user_data["spent_balance"] = clamp_money(float(user_data.get("spent_balance", 0.0)) + amount)
        add_transaction(user_data, "debit", amount, "Rejected approved account, refunded", email)
    save_user(target_uid, user_data)

    reason_label = REJECT_REASON_LABELS.get(reason, reason)
    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=t(target_uid, "member_rejected_approved_msg", email=email, reason=reason_text or reason_label),
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

    for key in ("reject_approved_uid", "reject_approved_token", "step"):
        context.user_data.pop(key, None)
    await update.effective_message.reply_text(
        t(admin_id, "appr_reject_success", email=email),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single(t(admin_id, "appr_btn_approved"), "view_approved:0"))


async def handle_reject_approved_reason_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    target_uid = context.user_data.get("reject_approved_uid")
    token = context.user_data.get("reject_approved_token")
    if not target_uid or not token:
        await update.message.reply_text(t(uid, "msg_error"))
        return
    user_data = get_user(target_uid)
    match = find_approved_account(user_data, token)
    if match is None:
        await update.message.reply_text(t(uid, "appr_not_found"))
        return
    index, account = match
    await finalize_approved_rejection(update, context, target_uid, index, account, "other", update.message.text.strip())


async def new_totp_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    token = parts[2]
    user_data = get_user(target_uid)
    account_match = find_approved_account(user_data, token)
    if account_match is None:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_approved"), "view_approved:0"))
        return
    index, account = account_match
    account_token = account_callback_token(account.get("email", ""))
    accounts = user_data.get("approved_accounts", [])
    if not account.get("has_totp", False) or not account.get("totp", ""):
        await query.edit_message_text(t(uid, "appr_no_totp"),
                                      reply_markup=kb_single(t(uid, "appr_btn_approved"), "view_approved:0"))
        return
    try:
        new_code = pyotp.TOTP(account.get("totp", "")).now()
        account["totp_code"] = new_code
        accounts[index] = account
        user_data["approved_accounts"] = accounts
        save_user(target_uid, user_data)
        await query.edit_message_text(
            t(uid, "appr_new_code_msg", email=account.get('email', ''), code=new_code, time=datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_vertical([(t(uid, "appr_new_code"), f"new_totp_code:{target_uid}:{account_token}"),
                                      (t(uid, "appr_btn_approved"), "view_approved:0")]))
    except Exception as e:
        await query.edit_message_text(t(uid, "msg_error"),
                                      reply_markup=kb_single(t(uid, "appr_btn_approved"), "view_approved:0"))


async def deduct_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    token = parts[2]
    user_data = get_user(target_uid)
    account_match = find_approved_account(user_data, token)
    if account_match is None:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_approved"), "view_approved:0"))
        return
    _, account = account_match
    email = account.get("email", "")
    context.user_data["deduct_uid"] = target_uid
    context.user_data["deduct_token"] = account_callback_token(email)
    await query.edit_message_text(
        t(uid, "appr_deduct_prompt", email=email, uid=target_uid),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single(t(uid, "btn_cancel"), f"approved_detail:{target_uid}:{account_callback_token(email)}"))
    context.user_data["step"] = "deduct_points_input"


async def handle_deduct_points_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if text.lower() in ("الغاء", "إلغاء", "cancel"):
        for key in ("deduct_uid", "deduct_token", "step"):
            context.user_data.pop(key, None)
        await update.message.reply_text(t(uid, "msg_cancelled"),
                                        reply_markup=kb_single(t(uid, "appr_btn_approved"), "view_approved:0"))
        return
    try:
        amount = float(text)
        if amount <= 0:
            await update.message.reply_text(t(uid, "appr_amount_positive"))
            return
        target_uid = context.user_data.get("deduct_uid")
        token = context.user_data.get("deduct_token")
        if not target_uid or not token:
            await update.message.reply_text(t(uid, "msg_error"))
            return
        user_data = get_user(target_uid)
        account_match = find_approved_account(user_data, token)
        if account_match is None:
            await update.message.reply_text(t(uid, "appr_not_found"))
            return
        _, account = account_match
        amount_cents = money_to_cents(amount)
        current_balance = spendable_balance_cents(user_data)
        if current_balance < amount_cents:
            await update.message.reply_text(t(uid, "appr_insufficient_balance", balance=cents_to_money(current_balance)))
            return
        debit_spendable_balance(user_data, amount_cents)
        add_transaction(user_data, "debit", amount, "Deducted by owner", account.get("email", ""))
        save_user(target_uid, user_data)
        for key in ("deduct_uid", "deduct_token", "step"):
            context.user_data.pop(key, None)
        await update.message.reply_text(
            t(uid, "appr_deduct_success", amount=amount, uid=target_uid),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single(t(uid, "appr_btn_approved"), "view_approved:0"))
    except ValueError:
        await update.message.reply_text(t(uid, "appr_invalid_amount"))






async def give_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    user_data = get_user(target_uid)
    found = _find_rejected(user_data, parts[2])
    if found is None:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_rejected"), "view_rejected:0"))
        return
    index, rec = found
    email = rec.get("email", "")
    context.user_data["give_uid"] = target_uid
    context.user_data["give_index"] = index
    await query.edit_message_text(
        t(uid, "appr_give_prompt", email=email, uid=target_uid),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single(t(uid, "btn_cancel"), f"rej_view:{target_uid}:{account_callback_token(email)}"))
    context.user_data["step"] = "give_points_input"


async def handle_give_points_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if text.lower() in ("الغاء", "إلغاء", "cancel"):
        for key in ("give_uid", "give_index", "step"):
            context.user_data.pop(key, None)
        await update.message.reply_text(t(uid, "msg_cancelled"),
                                        reply_markup=kb_single(t(uid, "appr_btn_rejected"), "view_rejected:0"))
        return
    try:
        amount = float(text)
        if amount <= 0:
            await update.message.reply_text(t(uid, "appr_amount_positive"))
            return
        target_uid = context.user_data.get("give_uid")
        index = context.user_data.get("give_index")
        if not target_uid or index is None:
            await update.message.reply_text(t(uid, "msg_error"))
            return
        user_data = get_user(target_uid)
        user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) + amount)
        user_data["total_credited_balance"] = clamp_money(float(user_data.get("total_credited_balance", 0.0)) + amount)
        rejected_list = user_data.get("rejected_requests", [])
        email = rejected_list[index].get("email", "") if index < len(rejected_list) else ""
        add_transaction(user_data, "credit", amount, "Compensation for rejected request", email)
        save_user(target_uid, user_data)
        for key in ("give_uid", "give_index", "step"):
            context.user_data.pop(key, None)
        await update.message.reply_text(
            t(uid, "appr_give_success", amount=amount, uid=target_uid),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single(t(uid, "appr_btn_rejected"), "view_rejected:0"))
    except ValueError:
        await update.message.reply_text(t(uid, "appr_invalid_amount"))


async def points_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    buttons = [
        (t(uid, "points_btn_add"), "give_points_by_id"),
        (t(uid, "points_btn_deduct"), "deduct_points_by_id"),
        (t(uid, "owner_btn_back_panel"), "owner_panel")
    ]
    await query.edit_message_text(t(uid, "points_title"), parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def give_points_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    await query.edit_message_text(
        t(uid, "points_give_prompt"),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single(t(uid, "btn_cancel"), "points_management"))
    context.user_data["step"] = "give_points_by_id_input"


async def deduct_points_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    await query.edit_message_text(
        t(uid, "points_deduct_prompt"),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single(t(uid, "btn_cancel"), "points_management"))
    context.user_data["step"] = "deduct_points_by_id_input"


async def handle_points_by_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if text.lower() in ("الغاء", "إلغاء", "cancel"):
        context.user_data.pop("step", None)
        await update.message.reply_text(t(uid, "msg_cancelled"),
                                        reply_markup=kb_single(t(uid, "points_title"), "points_management"))
        return
    parts = text.split()
    if len(parts) != 2:
        await update.message.reply_text(t(uid, "points_invalid_format"))
        return
    user_input = parts[0]
    try:
        amount = float(parts[1])
        if amount <= 0:
            await update.message.reply_text(t(uid, "appr_amount_positive"))
            return
    except ValueError:
        await update.message.reply_text(t(uid, "appr_invalid_amount"))
        return
    target_user_id = None
    if user_input.lstrip("-").isdigit():
        target_user_id = int(user_input)
    else:
        username = user_input.removeprefix("@")
        users = load_json(USERS_DB)
        for user_id, encrypted_data in users.items():
            data = decrypt_user_data(encrypted_data)
            if str(data.get("user_username", "")).lower() == username.lower():
                target_user_id = int(user_id)
                break
    if not target_user_id:
        await update.message.reply_text(t(uid, "appr_user_not_found"))
        return
    step = context.user_data.get("step")
    if step == "give_points_by_id_input":
        user_data = get_user(target_user_id)
        user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) + amount)
        user_data["total_credited_balance"] = clamp_money(float(user_data.get("total_credited_balance", 0.0)) + amount)
        add_transaction(user_data, "credit", amount, "Granted by owner")
        save_user(target_user_id, user_data)
        await update.message.reply_text(
            t(uid, "appr_give_success", amount=amount, uid=target_user_id),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single(t(uid, "points_title"), "points_management"))
    elif step == "deduct_points_by_id_input":
        user_data = get_user(target_user_id)
        amount_cents = money_to_cents(amount)
        current_balance = spendable_balance_cents(user_data)
        if current_balance < amount_cents:
            await update.message.reply_text(t(uid, "appr_insufficient_balance", balance=cents_to_money(current_balance)))
            return
        debit_spendable_balance(user_data, amount_cents)
        add_transaction(user_data, "debit", amount, "Deducted by owner")
        save_user(target_user_id, user_data)
        await update.message.reply_text(
            t(uid, "appr_deduct_success", amount=amount, uid=target_user_id),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single(t(uid, "points_title"), "points_management"))
    context.user_data.pop("step", None)


# ═══════════════════════════════════════════════════════════════════
# ═══ قسم إدارة الحسابات المقبولة ═══
# ═══════════════════════════════════════════════════════════════════
async def all_accounts_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    buttons = [[b] for b in [
        (t(uid, "accounts_btn_all_general"), "all_general"),
        (t(uid, "accounts_btn_all_old"), "all_old"),
        (t(uid, "accounts_btn_all_new"), "all_new"),
        (t(uid, "accounts_btn_unextracted_new"), "unextracted_new"),
        (t(uid, "accounts_btn_unextracted_old"), "unextracted_old"),
        (t(uid, "accounts_btn_hold"), "hold_accounts"),
        ("📮 بريد الاسترداد", "recovery_accounts_menu"),
        ("❄️ تجميد / فتح الإفراج", "freeze_menu"),
        ("🗑 ازالة الايميل من الاستخراج", "extraction_remove_start"),
        (t(uid, "owner_btn_back_panel"), "owner_panel")
    ]]
    await query.edit_message_text(t(uid, "accounts_title_admin"), parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def hold_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    users = load_json(USERS_DB)
    hold_list = []
    for target_uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        for acc in data.get("approved_accounts", []):
            if acc.get("approved_with_leave", False) and not acc.get("leave_confirmed", False):
                copy = dict(acc)
                copy["user_id"] = target_uid
                hold_list.append(copy)
    hold_list = sort_records_newest_first(hold_list)
    if not hold_list:
        await query.edit_message_text(t(uid, "accounts_no_hold"),
                                      reply_markup=kb_single(t(uid, "accounts_title_admin"), "all_accounts_section"))
        return
    total = len(hold_list)
    msg = t(uid, "accounts_hold_title", count=total)
    for idx, acc in enumerate(hold_list[:10], 1):
        approval_time = acc.get("approval_time", "")
        time_display = t(uid, "msg_unknown")
        try:
            dt = datetime.fromisoformat(approval_time)
            time_left = 86400 - (datetime.now(timezone.utc) - dt).total_seconds()
            if time_left > 0:
                time_display = t(uid, "accounts_time_left", h=int(time_left//3600), m=int((time_left%3600)//60))
            else:
                time_display = t(uid, "accounts_time_soon")
        except Exception:
            pass
        tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        msg += t(uid, "accounts_hold_item", idx=idx, icon=tier_icon, email=acc.get('email', ''), uid=acc.get('user_id', ''), amount=acc.get('amount', 0), time=time_display)
    if total > 10:
        msg += t(uid, "accounts_hold_more", total=total)
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single(t(uid, "accounts_title_admin"), "all_accounts_section"))


async def all_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, acc_type="all"):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    users = load_json(USERS_DB)
    all_accs = []
    for target_uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        for acc in data.get("approved_accounts", []):
            atype = acc.get("account_type", "old")
            if acc_type == "all" or atype == acc_type:
                copy = dict(acc)
                copy["user_id"] = target_uid
                all_accs.append(copy)
    all_accs = sort_records_newest_first(all_accs)
    if not all_accs:
        await query.edit_message_text(t(uid, "accounts_no_type", type=acc_type),
                                      reply_markup=kb_single(t(uid, "accounts_title_admin"), "all_accounts_section"))
        return

    type_name = t(uid, "accounts_type_old") if acc_type == "old" else t(uid, "accounts_type_new") if acc_type == "new" else t(uid, "accounts_type_all")
    total = len(all_accs)
    msg = t(uid, "accounts_all_title", type=type_name, count=total)
    for idx, acc in enumerate(all_accs[:10], 1):
        if acc.get("rejected_at_24h"):
            tier_icon = "🔴"
        else:
            tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        msg += t(uid, "accounts_all_item", idx=idx, icon=tier_icon, email=acc.get('email', ''), uid=acc.get('user_id', ''), amount=acc.get('amount', 0))
    if total > 10:
        msg += t(uid, "accounts_hold_more", total=total)

    buttons = [
        (t(uid, "accounts_btn_export", type=type_name), f"export_all_{acc_type}"),
        (t(uid, "accounts_title_admin"), "all_accounts_section")
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def unextracted_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, acc_type="all"):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    users = load_json(USERS_DB)
    unextracted = []
    for target_uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        for idx, acc in enumerate(_extraction_pool(data, acc_type)):
            if not acc.get("extracted", False) and _normal_extractable(acc):
                atype = acc.get("account_type", "old")
                if acc_type == "all" or atype == acc_type:
                    copy = dict(acc)
                    copy["user_id"] = target_uid
                    copy["index"] = idx
                    unextracted.append(copy)
    unextracted = sort_records_newest_first(unextracted)
    if not unextracted:
        await query.edit_message_text(t(uid, "accounts_none_unextracted", type=acc_type),
                                      reply_markup=kb_single(t(uid, "accounts_title_admin"), "all_accounts_section"))
        return

    type_name = t(uid, "accounts_type_old") if acc_type == "old" else t(uid, "accounts_type_new")
    total = len(unextracted)
    msg = t(uid, "accounts_unextracted_title", type=type_name, count=total)
    for idx, acc in enumerate(unextracted[:10], 1):
        if acc.get("rejected_at_24h"):
            tier_icon = "🔴"
        else:
            tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        msg += t(uid, "accounts_unextracted_item", idx=idx, icon=tier_icon, email=acc.get('email', ''), uid=acc.get('user_id', ''))
    if total > 10:
        msg += t(uid, "accounts_hold_more", total=total)

    buttons = [
        (t(uid, "accounts_btn_export", type=type_name), f"export_unextracted_{acc_type}"),
        (t(uid, "accounts_title_admin"), "all_accounts_section")
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def mark_extracted_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    users = load_json(USERS_DB)
    unextracted = []
    for target_uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        for idx, acc in enumerate(data.get("approved_accounts", [])):
            if not acc.get("extracted", False) and _normal_extractable(acc):
                unextracted.append({"user_id": target_uid, "index": idx, "email": acc.get("email", "")})
    if not unextracted:
        await query.edit_message_text(t(uid, "accounts_none_unextracted", type="all"),
                                      reply_markup=kb_single(t(uid, "accounts_title_admin"), "all_accounts_section"))
        return
    buttons = []
    for item in unextracted[:10]:
        buttons.append((t(uid, "accounts_mark_btn", email=item['email']), f"mark_extracted:{item['user_id']}:{item['index']}"))
    buttons.append((t(uid, "accounts_title_admin"), "all_accounts_section"))
    await query.edit_message_text(t(uid, "accounts_mark_title"), parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def mark_extracted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(target_uid)
    accounts = user_data.get("approved_accounts", [])
    if index < len(accounts):
        accounts[index]["extracted"] = True
        user_data["approved_accounts"] = accounts
        save_user(target_uid, user_data)
        await query.edit_message_text(t(uid, "accounts_mark_success"),
                                      reply_markup=kb_single(t(uid, "accounts_unextracted_title", type="old", count=0), "unextracted_accounts"))
    else:
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "accounts_title_admin"), "all_accounts_section"))


def build_accounts_export(title: str, accounts: list) -> bytes:
    lines = []
    for acc in accounts:
        email = str(acc.get('email', ''))
        password = str(acc.get('password', ''))
        totp = str(acc.get('totp', ''))
        app_pass = str(acc.get('app_pass', ''))
        if app_pass and " " not in app_pass and len(app_pass) == 16:
            app_pass = format_app_password(app_pass)
        lines.append(f"{email}:{password}:{totp}:{app_pass}")
    return "\n".join(lines).encode("utf-8")


async def send_accounts_export(context: ContextTypes.DEFAULT_TYPE, accounts: list,
                                filename: str, caption: str):
    document = io.BytesIO(build_accounts_export(caption, accounts))
    await context.bot.send_document(chat_id=OWNER_ID, document=document,
                                    filename=filename, caption=t(OWNER_ID, "accounts_export_caption", count=len(accounts)))


async def export_all_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, acc_type="all"):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    users = load_json(USERS_DB)
    all_accs = []
    for encrypted_data in users.values():
        data = decrypt_user_data(encrypted_data)
        for acc in _extraction_pool(data, acc_type):
            if acc.get("removed_from_extraction"):
                continue
            atype = acc.get("account_type", "old")
            if acc_type == "all" or atype == acc_type:
                all_accs.append(acc)
    if not all_accs:
        await query.edit_message_text(t(uid, "accounts_no_type", type=acc_type))
        return
    type_name = t(uid, "accounts_type_old") if acc_type == "old" else t(uid, "accounts_type_new") if acc_type == "new" else t(uid, "accounts_type_all")
    await send_accounts_export(context, all_accs, f"all_{acc_type}.txt", type_name)
    await query.edit_message_text(t(uid, "accounts_export_success", count=len(all_accs)),
                                  reply_markup=kb_single(t(uid, "accounts_title_admin"), "all_accounts_section"))


async def export_unextracted(update: Update, context: ContextTypes.DEFAULT_TYPE, acc_type="all"):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    users = load_json(USERS_DB)
    unextracted = []
    for target_uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        for acc in _extraction_pool(data, acc_type):
            if not acc.get("extracted", False) and _normal_extractable(acc):
                atype = acc.get("account_type", "old")
                if acc_type == "all" or atype == acc_type:
                    unextracted.append(acc)
    if not unextracted:
        await query.edit_message_text(t(uid, "accounts_none_unextracted", type=acc_type))
        return
    type_name = t(uid, "accounts_type_old") if acc_type == "old" else t(uid, "accounts_type_new")
    await send_accounts_export(context, unextracted, f"unextracted_{acc_type}.txt", type_name)
    for target_uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        changed = False
        for acc in _extraction_pool(data, acc_type):
            if not acc.get("extracted", False) and _normal_extractable(acc):
                atype = acc.get("account_type", "old")
                if acc_type == "all" or atype == acc_type:
                    acc["extracted"] = True
                    changed = True
        if changed:
            save_user(int(target_uid), data)
    await query.edit_message_text(t(uid, "accounts_export_success", count=len(unextracted)),
                                  reply_markup=kb_single(t(uid, "accounts_title_admin"), "all_accounts_section"))


def can_reject_pending_request(actor_id: int, user_data: dict, index: int) -> bool:
    if actor_id == OWNER_ID:
        return True
    if not is_admin(actor_id):
        return False
    pending = user_data.get("pending_requests", [])
    return 0 <= index < len(pending)


def rejection_list_callback(actor_id: int) -> str:
    return "view_pending:0" if actor_id == OWNER_ID else "admin_requests:0"


async def reject_request_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    actor_id = update.effective_user.id
    if not is_admin_or_owner(actor_id):
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    token = parts[2]
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text(t(actor_id, "appr_not_found"),
                                      reply_markup=kb_single(t(actor_id, "appr_btn_requests"), rejection_list_callback(actor_id)))
        return
    index, request = match
    if not can_reject_pending_request(actor_id, user_data, index):
        await query.answer("🚫", show_alert=True)
        return
    email = request.get("email", "")
    request_token = account_callback_token(email)
    display_email = tg_html_escape(email)
    detail_callback = (
        f"pending_detail:{target_uid}:{request_token}"
        if actor_id == OWNER_ID
        else f"admin_request_detail:{target_uid}:{request_token}"
    )

    context.user_data.pop("force_final", None)
    if query.data.startswith("reject_request:") and actor_id == OWNER_ID:
        await query.edit_message_text(
            f"📧 <code>{display_email}</code>\n\nماذا تريد أن تفعل بهذا الطلب؟\n\n"
            f"• <b>رفض (1/3)</b>: يُسجَّل رفضك ويبقى الطلب لمشرف آخر.\n"
            f"• <b>رفض نهائي فوري</b>: ينقل الطلب مباشرة إلى المرفوضة.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("❌ رفض عادي (يحتاج 3 رفضات)", f"reject_continue:{target_uid}:{request_token}"),
                ("⛔ رفض نهائي فوري", f"reject_final_now:{target_uid}:{request_token}"),
                (t(actor_id, "appr_btn_back_detail"), detail_callback),
            ]))
        return
    if query.data.startswith("reject_final_now:") and actor_id == OWNER_ID:
        context.user_data["force_final"] = True

    context.user_data["reject_uid"] = target_uid
    context.user_data["reject_index"] = index

    buttons = [
        (t(actor_id, "appr_reason_email"), f"reject_reason:email:{target_uid}:{request_token}"),
        (t(actor_id, "appr_reason_password"), f"reject_reason:password:{target_uid}:{request_token}"),
        (t(actor_id, "appr_reason_totp"), f"reject_reason:totp:{target_uid}:{request_token}"),
        (t(actor_id, "appr_reason_app_pass"), f"reject_reason:app_pass:{target_uid}:{request_token}"),
        (t(actor_id, "appr_reason_phone"), f"reject_reason:phone:{target_uid}:{request_token}"),
        (t(actor_id, "appr_reason_other"), f"reject_reason:other:{target_uid}:{request_token}"),
        (t(actor_id, "appr_btn_back_detail"), detail_callback),
    ]
    await query.edit_message_text(
        t(actor_id, "appr_reject_reason", email=display_email),
        parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def execute_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رفض بسيط - يسجل في rejected_by_admins ويكون نهائياً فقط عند 3 رفضات."""
    query = update.callback_query
    actor_id = update.effective_user.id
    try:
        if not is_admin_or_owner(actor_id):
            await query.answer("🚫", show_alert=True)
            return
        parts = query.data.split(":")
        if len(parts) < 4:
            await query.answer(t(actor_id, "msg_error"), show_alert=True)
            return
        reason_type = parts[1]
        target_uid = int(parts[2])
        token = parts[3]

        user_data = get_user(target_uid)
        match = find_pending_request(user_data, token)
        if match is None:
            await _safe_edit(query, t(actor_id, "appr_not_found"),
                             reply_markup=kb_single(t(actor_id, "appr_btn_requests"), rejection_list_callback(actor_id)))
            return
        index, request = match
        if not can_reject_pending_request(actor_id, user_data, index):
            await query.answer("🚫", show_alert=True)
            return

        email = request.get("email", "")
        request_token = account_callback_token(email)
        display_email = tg_html_escape(email)
        detail_callback = (
            f"pending_detail:{target_uid}:{request_token}"
            if actor_id == OWNER_ID
            else f"admin_request_detail:{target_uid}:{request_token}"
        )

        if reason_type == "other":
            context.user_data["reject_uid"] = target_uid
            context.user_data["reject_index"] = index
            context.user_data["reject_reason"] = "other"
            context.user_data["step"] = "reject_reason_text"
            await _safe_edit(
                query,
                t(actor_id, "appr_write_reason", email=display_email),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single(t(actor_id, "btn_cancel"), detail_callback))
            return

        # ═══ NEW: تسجيل نشاط الأدمن ═══
        record_admin_activity(actor_id, "reject")
        await _apply_admin_rejection(
            update, context, actor_id, target_uid, index, request,
            reason_type, REJECT_REASON_MESSAGES.get(reason_type, "❌ تم الرفض"),
            is_custom=False)

    except Exception:
        logger.exception("execute_reject_reason failed")
        try:
            await query.answer(t(actor_id, "msg_error"), show_alert=True)
        except Exception:
            pass


async def handle_reject_reason_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor_id = update.effective_user.id
    target_uid = context.user_data.get("reject_uid")
    index = context.user_data.get("reject_index")
    text = update.message.text.strip()
    if not target_uid or index is None:
        await update.message.reply_text(t(actor_id, "msg_error"))
        return
    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    if not can_reject_pending_request(actor_id, user_data, index):
        await update.message.reply_text("🚫")
        return
    if index >= len(pending):
        await update.message.reply_text(t(actor_id, "appr_not_found"))
        return
    request = pending[index]
    record_admin_activity(actor_id, "reject")
    for key in ("reject_uid", "reject_index", "reject_reason", "step"):
        context.user_data.pop(key, None)
    await _apply_admin_rejection(
        update, context, actor_id, target_uid, index, request,
        "other", text, is_custom=True)


async def _apply_admin_rejection(update, context, actor_id, target_uid, index,
                                  request, reason_type, reason_text, is_custom=False):
    """تطبق رفض الأدمن مع نظام متعدد المشرفين (3 رفضات = نهائي)."""
    email = request.get("email", "")
    request_token = account_callback_token(email)
    display_email = tg_html_escape(email)
    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        target_msg = update.message if not update.callback_query else update.callback_query.message
        await target_msg.reply_text(t(actor_id, "appr_not_found"))
        return

    actor = update.effective_user
    rejected_by_admins = request.get("rejected_by_admins", [])
    if not isinstance(rejected_by_admins, list):
        rejected_by_admins = []
    # استبدال تسجيل سابق لنفس الأدمن (تحديث السبب)
    rejected_by_admins = [r for r in rejected_by_admins
                          if int(r.get("admin_id", 0)) != actor_id]
    rejected_by_admins.append({
        "admin_id": actor_id,
        "admin_name": actor.full_name or "Unknown",
        "admin_username": actor.username or "",
        "reason": reason_type,
        "reason_text": reason_text,
        "rejected_at": datetime.now(timezone.utc).isoformat(),
    })
    request["rejected_by_admins"] = rejected_by_admins
    pending[index] = request
    user_data["pending_requests"] = pending

    count = len(rejected_by_admins)
    force_final = bool(context.user_data.pop("force_final", False)) and actor_id == OWNER_ID
    is_final = force_final or count >= REJECTIONS_REQUIRED_FOR_FINAL

    if is_final:
        # ═══ الرفض نهائي ═══
        pending.pop(index)
        user_data["pending_requests"] = pending
        move_request_to_rejected(user_data, request, reason_type,
                                  reason_text if is_custom else "")
        ban_result = register_rejection_and_maybe_ban(user_data)
        if user_data.get("rejected_requests"):
            user_data["rejected_requests"][-1]["rejected_by"] = actor_id
            user_data["rejected_requests"][-1]["rejected_by_name"] = actor.full_name or "Unknown"
            user_data["rejected_requests"][-1]["rejected_by_username"] = actor.username or ""
            user_data["rejected_requests"][-1]["rejected_at"] = datetime.now(timezone.utc).isoformat()
            user_data["rejected_requests"][-1]["rejected_by_admins"] = rejected_by_admins
        save_user(target_uid, user_data)

        # إشعار العضو + فيديو السبب
        config = load_config()
        video_key = REJECT_REASON_VIDEO_KEY.get(reason_type)
        video_path = config.get(video_key) if video_key else None
        if video_path and Path(video_path).exists():
            try:
                await context.bot.send_video(
                    chat_id=target_uid, video=open(video_path, "rb"),
                    caption=t(target_uid, "member_rejected_video", reason=reason_text),
                    parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            except Exception:
                logger.exception("Failed to send rejection video")
        ban_notice = ""
        if ban_result:
            _, duration_text = ban_result
            ban_notice = t(target_uid, "appr_ban_notice", duration=duration_text)
        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=t(target_uid, "member_rejected_msg", reason=reason_text,
                       email=email, ban=ban_notice),
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            logger.exception("Failed to notify rejected user")

        success_text = (f"✅ <b>تم الرفض النهائي</b> ({'مباشر من المالك' if force_final else '3/3'})\n\n"
                        f"📧 <code>{display_email}</code>\n"
                        f"📝 السبب: {REJECT_REASON_LABELS.get(reason_type, reason_type)}")
        if ban_result:
            success_text += t(actor_id, "appr_success_reject_ban", duration=ban_result[1])

        target_msg = update.callback_query if update.callback_query else None
        if target_msg:
            await _safe_edit(query=target_msg, text=success_text,
                             parse_mode=ParseMode.HTML,
                             reply_markup=kb_single(t(actor_id, "appr_btn_requests"),
                                                    rejection_list_callback(actor_id)))
        else:
            await update.message.reply_text(success_text, parse_mode=ParseMode.HTML,
                                             reply_markup=kb_single(t(actor_id, "appr_btn_requests"),
                                                                    rejection_list_callback(actor_id)))
        return

    # ═══ ليس نهائياً - يبقى في pending ═══
    save_user(target_uid, user_data)
    remaining = REJECTIONS_REQUIRED_FOR_FINAL - count
    if actor_id == OWNER_ID:
        success_text = (f"⚠️ <b>تم تسجيل رفضك ({count}/{REJECTIONS_REQUIRED_FOR_FINAL})</b>\n\n"
                        f"📧 <code>{display_email}</code>\n"
                        f"📝 السبب: {REJECT_REASON_LABELS.get(reason_type, reason_type)}\n\n"
                        f"📌 <i>يحتاج {remaining} رفض إضافي من مشرفين آخرين ليصبح نهائياً.</i>\n"
                        f"<i>الإيميل الآن متاح لمشرف آخر للقبول أو الرفض.</i>")
    else:
        success_text = (f"✅ <b>تم تسجيل رفضك</b>\n\n"
                        f"📧 <code>{display_email}</code>\n"
                        f"📝 السبب: {REJECT_REASON_LABELS.get(reason_type, reason_type)}\n\n"
                        f"<i>تم إخفاء الإيميل من قائمتك، وسيراجعه مشرف آخر.</i>")
    detail_callback = (
        f"pending_detail:{target_uid}:{request_token}"
        if actor_id == OWNER_ID
        else f"admin_request_detail:{target_uid}:{request_token}"
    )
    target_msg = update.callback_query if update.callback_query else None
    if target_msg:
        await _safe_edit(query=target_msg, text=success_text,
                         parse_mode=ParseMode.HTML,
                         reply_markup=kb_vertical([
                             (t(actor_id, "appr_btn_back_detail"), detail_callback),
                             (t(actor_id, "appr_btn_requests"),
                              rejection_list_callback(actor_id)),
                         ]))
    else:
        await update.message.reply_text(success_text, parse_mode=ParseMode.HTML,
                                         reply_markup=kb_vertical([
                                             (t(actor_id, "appr_btn_back_detail"), detail_callback),
                                             (t(actor_id, "appr_btn_requests"),
                                              rejection_list_callback(actor_id)),
                                         ]))


# ==================== AUTO-FINAL REJECTION AFTER 24H ====================
STALE_REJECTION_HOURS = 24


async def finalize_stale_rejections_job(context: ContextTypes.DEFAULT_TYPE):
    """إيميل رفضه مشرف ولم يُرفض ولم يُقبل خلال 24 ساعة من آخر رفض يُرفض نهائياً.

    يحمل الرفض نفس آثار الرفض النهائي العادي (نقله إلى المرفوضة، إشعار العضو،
    وعدّاد الرفض المتتالي). للعضو أن يُدخل الإيميل مرة أخرى متى شاء.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=STALE_REJECTION_HOURS)
    stale: List[Tuple[str, str]] = []  # (user_id, email)
    for uid, encrypted in load_json(USERS_DB).items():
        user_data = decrypt_user_data(encrypted)
        for req in user_data.get("pending_requests", []) or []:
            rejections = req.get("rejected_by_admins")
            if not isinstance(rejections, list) or not rejections:
                continue
            times = []
            for rj in rejections:
                try:
                    moment = datetime.fromisoformat(str(rj.get("rejected_at")))
                except (TypeError, ValueError):
                    continue
                times.append(moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc))
            if times and max(times) <= cutoff:
                stale.append((uid, req.get("email", "")))
    for uid, email in stale:
        try:
            await _finalize_stale_request(context, int(uid), email)
        except Exception:
            logger.exception("Auto-final rejection failed for %s", uid)


async def _finalize_stale_request(context: ContextTypes.DEFAULT_TYPE, target_uid: int, email: str):
    user_data = get_user(target_uid)
    pending = user_data.get("pending_requests", []) or []
    index = next((i for i, r in enumerate(pending) if r.get("email") == email
                  and r.get("rejected_by_admins")), None)
    if index is None:
        return
    request = pending[index]
    rejections = request.get("rejected_by_admins", [])
    last = rejections[-1]
    reason_type = last.get("reason", "other")
    reason_text = last.get("reason_text") or REJECT_REASON_MESSAGES.get(reason_type, "❌ تم الرفض")
    is_custom = reason_type == "other"

    pending.pop(index)
    user_data["pending_requests"] = pending
    move_request_to_rejected(user_data, request, reason_type, reason_text if is_custom else "")
    ban_result = register_rejection_and_maybe_ban(user_data)
    if user_data.get("rejected_requests"):
        record = user_data["rejected_requests"][-1]
        record["rejected_by"] = 0
        record["rejected_by_name"] = f"تلقائي ({STALE_REJECTION_HOURS} ساعة)"
        record["rejected_at"] = datetime.now(timezone.utc).isoformat()
        record["rejected_by_admins"] = rejections
        record["auto_rejected_stale"] = True
    save_user(target_uid, user_data)

    config = load_config()
    video_key = REJECT_REASON_VIDEO_KEY.get(reason_type)
    video_path = config.get(video_key) if video_key else None
    if video_path and Path(video_path).exists():
        try:
            with open(video_path, "rb") as video_file:
                await context.bot.send_video(
                    chat_id=target_uid, video=video_file,
                    caption=t(target_uid, "member_rejected_video", reason=reason_text),
                    parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
        except Exception:
            logger.exception("Failed to send rejection video")
    ban_notice = ""
    if ban_result:
        ban_notice = t(target_uid, "appr_ban_notice", duration=ban_result[1])
    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=t(target_uid, "member_rejected_msg", reason=reason_text, email=email, ban=ban_notice),
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("Failed to notify rejected user")


# ==================== EXTRACTION HELPERS ====================
def _is_recovery_account(acc: dict) -> bool:
    """سجل قديم قُبل بطريقة ايميل الاسترداد (لم يمرّ بفحص IMAP)."""
    return acc.get("acceptance_mode") == "recovery_email" or bool(acc.get("recovery_email"))


def _passed_first_imap(acc: dict) -> bool:
    """نجح في فحص IMAP الأول (نتيجة الفحص الثاني لا تهم)."""
    if _is_recovery_account(acc):
        return False
    ver = acc.get("verification") or {}
    return acc.get("auto_verified") is True or ver.get("imap_ok") is True


def _is_complete_account(acc: dict) -> bool:
    """مكتمل: ايميل + باسورد + مصادقة (TOTP)."""
    return all(str(acc.get(k) or "").strip() for k in ("email", "password", "totp"))


def _extraction_eligible(acc: dict) -> bool:
    return _passed_first_imap(acc) and _is_complete_account(acc)


def _normal_extractable(acc: dict) -> bool:
    """يظهر في الاستخراج: غير محذوف منه، نجح في IMAP الأول، ومكتمل."""
    return not acc.get("removed_from_extraction") and _extraction_eligible(acc)


def _extraction_pool(data: dict, acc_type: str = "all") -> list:
    """حسابات المستخدم المرشحة للاستخراج (نفس كائنات القاموس، فالتعديل عليها يُحفظ).
    حصراً الحسابات التي نجحت في IMAP الأول ومكتملة (ايميل+باسورد+مصادقة).
    تبقى في القوائم سواء نجحت أو فشلت في الفحص الثاني (24 ساعة):
    الفاشلة في الثاني تُجمع من المرفوضة للنوع الجديد."""
    pool = [a for a in (data.get("approved_accounts", []) or []) if _extraction_eligible(a)]
    if acc_type == "new":
        for rec in data.get("rejected_requests", []) or []:
            if rec.get("rejected_from_approved") and (
                    rec.get("rejected_at_24h") or rec.get("reject_reason") == "imap_failed_24h"
            ) and _extraction_eligible(rec):
                pool.append(rec)
    return pool


# ==================== ONE-TIME CLEANUP: RECOVERY-ACCEPTED ACCOUNTS ====================
def _scan_cleanup() -> dict:
    users = load_json(USERS_DB)
    info = {"targets": [], "member_hold": 0.0, "member_released": 0.0, "admin_bonus": {},
            "hidden_no_imap": 0, "hidden_incomplete": 0}
    for uid_str, enc in users.items():
        data = decrypt_user_data(enc)
        for acc in data.get("approved_accounts", []) or []:
            if _is_recovery_account(acc):
                amt = clamp_money(acc.get("amount", 0.0))
                released = bool(acc.get("leave_confirmed"))
                info["targets"].append(uid_str)
                info["member_released" if released else "member_hold"] += amt
                try:
                    aid = int(acc.get("completed_by_admin"))
                except (TypeError, ValueError):
                    aid = None
                if aid and aid != OWNER_ID and money_to_cents(acc.get("admin_bonus", 0.0)) > 0 \
                        and str(acc.get("admin_bonus_status") or "") in ("pending", "", "released"):
                    info["admin_bonus"][aid] = round(info["admin_bonus"].get(aid, 0.0)
                                                      + float(acc.get("admin_bonus", 0.0)), 2)
            else:
                if not _passed_first_imap(acc):
                    info["hidden_no_imap"] += 1
                elif not _is_complete_account(acc):
                    info["hidden_incomplete"] += 1
    return info


async def cleanup_recovery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cleanup_recovery — للمالك فقط: معاينة ثم تأكيد."""
    uid = update.effective_user.id
    if uid != OWNER_ID:
        return
    info = _scan_cleanup()
    n = len(info["targets"])
    admins_txt = "\n".join(f"   • {a}: ${v:.2f}" for a, v in info["admin_bonus"].items()) or "   —"
    msg = (f"🧹 معاينة التنظيف\n\n"
           f"📧 ايميلات ستُرفض (قُبلت بايميل استرداد): {n}\n"
           f"   • في فترة الانتظار (يُلغى المعلّق): ${info['member_hold']:.2f}\n"
           f"   • مستلمة (تُخصم من رصيد العضو): ${info['member_released']:.2f}\n"
           f"👮 مكافآت مشرفين ستُخصم:\n{admins_txt}\n\n"
           f"🙈 مخفية من الاستخراج (لا تُحذف، فقط لا تظهر):\n"
           f"   • لم تنجح في IMAP الأول: {info['hidden_no_imap']}\n"
           f"   • ناقصة (بدون باسورد أو مصادقة): {info['hidden_incomplete']}")
    if n == 0:
        await update.message.reply_text(msg + "\n\n✅ لا يوجد ما يُرفض.")
        return
    await update.message.reply_text(msg, reply_markup=kb_vertical([
        (f"✅ تنفيذ رفض {n} ايميل وخصم النقاط", "cleanup_recovery_run"),
        ("❌ إلغاء", "cleanup_recovery_cancel"),
    ]))


async def cleanup_recovery_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    await query.edit_message_text("تم الإلغاء. لم يتغير شيء.")


async def cleanup_recovery_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    await query.edit_message_text("⏳ جارٍ التنفيذ...")
    reason_text = "تم إلغاء الايميل (قُبل بايميل استرداد) واسترجاع النقاط"
    uids = sorted({int(u) for u in _scan_cleanup()["targets"]})
    done, member_total, shortfalls, admin_ded = 0, 0.0, [], {}
    for target_uid in uids:
        try:
            data = get_user(target_uid)   # قراءة جديدة: قد يكون عُدّل كمشرف سابقاً
            accounts = data.get("approved_accounts", []) or []
            targets = [a for a in accounts if _is_recovery_account(a)]
            if not targets:
                continue
            data["approved_accounts"] = [a for a in accounts if not _is_recovery_account(a)]
            admin_ops, notices = [], []
            for acc in targets:
                account = dict(acc)
                email = account.get("email", "")
                amount = clamp_money(account.get("amount", 0.0))
                released = bool(account.get("leave_confirmed"))

                # 1) خصم المشرف
                try:
                    aid = int(account.get("completed_by_admin"))
                except (TypeError, ValueError):
                    aid = None
                bonus_c = money_to_cents(account.get("admin_bonus", 0.0))
                status = str(account.get("admin_bonus_status") or "")
                if aid and aid != OWNER_ID and bonus_c > 0:
                    if status in ("pending", ""):
                        if settle_admin_bonus(account, "rejected", owner_data=data, owner_id=target_uid):
                            account["admin_bonus_status"] = "rejected"
                            admin_ded[aid] = round(admin_ded.get(aid, 0.0) + cents_to_money(bonus_c), 2)
                    elif status == "released":
                        admin_ops.append((aid, bonus_c, email))
                        account["admin_bonus_status"] = "rejected"
                        admin_ded[aid] = round(admin_ded.get(aid, 0.0) + cents_to_money(bonus_c), 2)

                # 2) خصم العضو
                if released:
                    bal = clamp_money(data.get("balance", 0.0))
                    ded = min(bal, amount)
                    data["balance"] = clamp_money(bal - ded)
                    data["spent_balance"] = clamp_money(float(data.get("spent_balance", 0.0)) + ded)
                    add_transaction(data, "debit", ded, "Cancelled recovery-email account, refunded", email)
                    member_total += ded
                    if amount - ded > 0.004:
                        shortfalls.append((target_uid, email, round(amount - ded, 2)))
                else:
                    data["hold_balance"] = clamp_money(float(data.get("hold_balance", 0.0)) - amount)
                    add_transaction(data, "approved_rejection", amount,
                                    "Cancelled recovery-email account, canceled hold", email)
                    member_total += amount

                move_approved_account_to_rejected(data, account, "other", reason_text)
                if data.get("rejected_requests"):
                    rec = data["rejected_requests"][-1]
                    rec["rejected_by"] = OWNER_ID
                    rec["rejected_by_name"] = "Owner (cleanup)"
                notices.append(email)
                done += 1
            save_user(target_uid, data)
            for aid, bonus_c, email in admin_ops:     # بعد حفظ العضو (قد يكون هو المشرف نفسه)
                deduct_from_admin_balance(aid, bonus_c, "recovery email cancelled", email)
            for email in notices:
                try:
                    await context.bot.send_message(
                        chat_id=target_uid,
                        text=t(target_uid, "member_rejected_approved_msg", email=email, reason=reason_text),
                        parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    pass
        except Exception:
            logger.exception("cleanup_recovery failed for user %s", target_uid)
    lines = [f"✅ تم رفض {done} ايميل.",
             f"👤 إجمالي ما أُلغي/خُصم من الأعضاء: ${member_total:.2f}"]
    if admin_ded:
        lines.append("👮 خُصم من المشرفين:")
        lines += [f"   • {a}: ${v:.2f}" for a, v in admin_ded.items()]
    if shortfalls:
        lines.append(f"\n⚠️ عجز (رصيد العضو أقل من المطلوب، لا يمكن أن يصبح سالباً): {len(shortfalls)}")
        lines += [f"   • {u} | {e} | ${v:.2f}" for u, e, v in shortfalls[:40]]
    text = "\n".join(lines)
    for i in range(0, len(text), 3500):
        await context.bot.send_message(chat_id=OWNER_ID, text=text[i:i + 3500])


async def extraction_remove_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    context.user_data["step"] = "extraction_remove_input"
    await query.edit_message_text(
        "🗑 أرسل الايميل (أو عدة ايميلات، كل واحد في سطر) لإزالته من الاستخراج.",
        reply_markup=kb_single(t(uid, "btn_cancel"), "all_accounts_section"))


async def handle_extraction_remove_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        return
    context.user_data.pop("step", None)
    wanted = {normalize_email(e) for e in re.split(r"[\s,;]+", update.message.text or "") if "@" in e}
    back = kb_single(t(uid, "accounts_title_admin"), "all_accounts_section")
    if not wanted:
        await update.message.reply_text("⚠️ لم أجد ايميلاً في رسالتك.", reply_markup=back)
        return
    removed = {}
    users = load_json(USERS_DB)
    for target_uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        changed = False
        for acc in _extraction_pool(data, "new"):
            key = normalize_email(acc.get("email", ""))
            if key in wanted:
                acc["removed_from_extraction"] = True
                acc["extracted"] = True
                removed[key] = removed.get(key, 0) + 1
                changed = True
        if changed:
            save_user(int(target_uid), data)
    lines = []
    for e in sorted(wanted):
        lines.append(f"{'✅' if e in removed else '❌ غير موجود:'} {e}")
    await update.message.reply_text(
        "🗑 نتيجة الإزالة من الاستخراج:\n\n" + "\n".join(lines), reply_markup=back)


# ==================== FREEZE / UNFREEZE RELEASE ====================
def _acc_release_dt(acc: dict):
    dt = parse_iso_datetime(acc.get("release_at"))
    if dt is None:
        base = parse_iso_datetime(acc.get("approval_time"))
        if base is not None:
            dt = base + timedelta(seconds=int(acc.get("hold_seconds", LEAVE_HOLD_SECONDS)))
    return dt


def _is_hold_acc(acc: dict) -> bool:
    return bool(acc.get("approved_with_leave")) and not acc.get("leave_confirmed") \
        and not acc.get("rejected_at_24h")


def _freeze_key_matches(acc: dict, key: str) -> bool:
    aid = acc.get("completed_by_admin")
    if aid is None:
        return False
    try:
        aid = int(aid)
    except (TypeError, ValueError):
        return False
    if aid == OWNER_ID:
        return False
    return key == "all" or str(aid) == key


def _hold_stats_by_admin() -> dict:
    stats = {}
    users = load_json(USERS_DB)
    for _uid, enc in users.items():
        data = decrypt_user_data(enc)
        for acc in data.get("approved_accounts", []) or []:
            if not _is_hold_acc(acc):
                continue
            aid = acc.get("completed_by_admin")
            try:
                aid = int(aid)
            except (TypeError, ValueError):
                continue
            if aid == OWNER_ID:
                continue
            st = stats.setdefault(aid, {"name": acc.get("completed_by_admin_name") or "Unknown",
                                        "hold": 0, "frozen": 0})
            st["hold"] += 1
            if acc.get("frozen"):
                st["frozen"] += 1
    return stats


async def freeze_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    context.user_data.pop("step", None)
    stats = _hold_stats_by_admin()
    fz = {str(x) for x in (load_config().get("frozen_admins") or [])}
    all_frozen = "all" in fz
    msg = ("❄️ <b>تجميد الإفراج</b>\n\n"
           "التجميد يوقف خروج المال للأعضاء ومكافأة المشرف لحسابات الانتظار "
           "(ولأي حساب جديد يقبله المشرف المجمّد). المدة المتبقية تتوقف أثناء التجميد.\n\n")
    if all_frozen:
        msg += "🔒 <b>التجميد مفعّل لكل المشرفين.</b>\n\n"
    if not stats and not fz:
        msg += "لا توجد حسابات انتظار حالياً."
    buttons = []
    for aid, st in sorted(stats.items(), key=lambda kv: -kv[1]["hold"]):
        name = tg_html_escape(str(st["name"]))[:20]
        is_fz = all_frozen or str(aid) in fz
        icon = "🔒" if is_fz else "🟢"
        msg += f"{icon} <code>{aid}</code> {name} — انتظار: {st['hold']} | مجمّد: {st['frozen']}\n"
        if all_frozen:
            continue
        if str(aid) in fz:
            buttons.append([(f"🔥 فتح تجميد {name}", f"unfreeze_pick:{aid}")])
        else:
            buttons.append([(f"❄️ تجميد {name} ({st['hold']})", f"freeze_do:{aid}")])
    if not all_frozen:
        buttons.append([("❄️ تجميد كل المشرفين", "freeze_do:all")])
    if fz:
        buttons.append([("🔥 فتح تجميد الكل", "unfreeze_pick:all")])
    buttons.append([(t(uid, "accounts_title_admin"), "all_accounts_section")])
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def freeze_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    key = query.data.split(":", 1)[1]
    cfg = load_config()
    fz = {str(x) for x in (cfg.get("frozen_admins") or [])}
    fz.add(key)
    cfg["frozen_admins"] = sorted(fz)
    save_config(cfg)
    now = datetime.now(timezone.utc)
    count = 0
    users = load_json(USERS_DB)
    for target_uid, enc in users.items():
        data = decrypt_user_data(enc)
        changed = False
        for acc in data.get("approved_accounts", []) or []:
            if _is_hold_acc(acc) and not acc.get("frozen") and _freeze_key_matches(acc, key):
                rel = _acc_release_dt(acc)
                remaining = max(0, int((rel - now).total_seconds())) if rel else 0
                acc["frozen"] = True
                acc["frozen_at"] = now.isoformat()
                acc["frozen_remaining"] = remaining
                changed = True
                count += 1
        if changed:
            save_user(int(target_uid), data)
    await query.answer(f"❄️ تم تجميد {count} حساب", show_alert=True)
    await freeze_menu(update, context)


async def unfreeze_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    key = query.data.split(":", 1)[1]
    label = "كل المشرفين" if key == "all" else f"المشرف {key}"
    buttons = [
        [("▶️ استكمال المدة المتبقية", f"unfreeze_do:{key}:r")],
        [("⏱ 12 ساعة من الآن", f"unfreeze_do:{key}:12")],
        [("⏱ 24 ساعة من الآن", f"unfreeze_do:{key}:24")],
        [("⏱ 48 ساعة من الآن", f"unfreeze_do:{key}:48")],
        [("🔙 رجوع", "freeze_menu")],
    ]
    await query.edit_message_text(
        f"🔥 <b>فتح التجميد: {label}</b>\n\nاختر كم تمشي الحسابات بعد الفتح قبل الإفراج "
        "(وبعدها يتم الفحص العادي وتصل النقاط):",
        parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def unfreeze_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram_bot.members import schedule_leave_check
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    _, key, mode = query.data.split(":", 2)
    cfg = load_config()
    if key == "all":
        cfg["frozen_admins"] = []
    else:
        cfg["frozen_admins"] = [x for x in (cfg.get("frozen_admins") or []) if str(x) != key]
    save_config(cfg)
    now = datetime.now(timezone.utc)
    count = 0
    users = load_json(USERS_DB)
    for target_uid, enc in users.items():
        data = decrypt_user_data(enc)
        changed = False
        to_schedule = []
        for acc in data.get("approved_accounts", []) or []:
            if not (_is_hold_acc(acc) and acc.get("frozen") and _freeze_key_matches(acc, key)):
                continue
            if mode == "r":
                secs = max(60, int(acc.get("frozen_remaining", 0) or 0))
            else:
                secs = int(mode) * 3600
            rel = now + timedelta(seconds=secs)
            acc["release_at"] = rel.isoformat()
            acc["frozen"] = False
            acc.pop("frozen_at", None)
            acc.pop("frozen_remaining", None)
            to_schedule.append((acc.get("email", ""), rel.isoformat()))
            changed = True
            count += 1
        if changed:
            save_user(int(target_uid), data)
            for email, rel_iso in to_schedule:
                await schedule_leave_check(context, int(target_uid), email, rel_iso)
    await query.answer(f"🔥 تم فتح التجميد لـ {count} حساب", show_alert=True)
    await freeze_menu(update, context)


# ==================== REJECTED SECTION (موحّد للمالك والأدمن) ====================
# مفاتيح تخص مرحلة الرفض/القبول السابقة وتُزال عند إعادة الطلب إلى المنتظرة
_REVIVE_DROP_KEYS = (
    "recovery_verified", "recovery_verified_at", "recovery",
    "reject_reason", "reject_reason_text", "rejected_by", "rejected_by_name",
    "rejected_by_username", "rejected_at", "rejected_from_approved", "rejected_at_24h",
    "final_rejected", "final_rejected_by", "final_rejected_at", "superseded",
    "approved_with_leave", "leave_confirmed", "leave_confirmed_at", "approval_time",
    "release_at", "hold_seconds", "admin_bonus_status", "admin_bonus", "extracted",
    "removed_from_extraction", "acceptance_mode", "auto_verified",
)


def _rej_is_owner(uid: int) -> bool:
    return uid == OWNER_ID


_REJ_LAST_TYPE: dict = {}   # آخر قسم (old/new) فتحه مشرف بريد الاسترداد، ليعود إليه زر الرجوع


def _rej_split_applies(uid: int) -> bool:
    """تقسيم المرفوضة (قديم/جديد) يراه فقط المشرف المفعّل له شرط بريد الاسترداد."""
    return uid != OWNER_ID and recovery_check_applies(uid)


def _rej_list_cb(uid: int) -> str:
    if uid == OWNER_ID:
        return "view_rejected:0"
    if _rej_split_applies(uid) and _REJ_LAST_TYPE.get(uid) in ("old", "new"):
        return f"admin_rejected_t:{_REJ_LAST_TYPE[uid]}:0"
    return "admin_rejected:0"


def _find_rejected(user_data: dict, token: str) -> Optional[Tuple[int, dict]]:
    """يبحث عن سجل مرفوض بالتوكن (ثابت) بدل رقم الترتيب الذي يتغيّر. الأحدث أولاً."""
    lst = user_data.get("rejected_requests", []) or []
    found = None
    for i, rec in enumerate(lst):
        if is_visible_rejected(rec) and account_callback_token(rec.get("email", "")) == token:
            found = (i, rec)
    if found:
        return found
    if str(token).isdigit():  # توافق مع أزرار قديمة
        i = int(token)
        if 0 <= i < len(lst) and is_visible_rejected(lst[i]):
            return i, lst[i]
    return None


def revive_rejected_request(user_data: dict, token: str, actor_id: int):
    """ينقل سجلاً مرفوضاً إلى المنتظرة. يرجع (index, request) أو "duplicate" أو None. لا يحفظ."""
    match = _find_rejected(user_data, token)
    if match is None:
        return None
    r_index, rec = match
    email = rec.get("email", "")
    normalized = normalize_email(email)
    pending = user_data.get("pending_requests")
    if not isinstance(pending, list):
        pending = []
    if any(normalize_email(p.get("email", "")) == normalized for p in pending):
        return "duplicate"
    status = get_active_account_status(email)
    if status in ("approved", "pending"):
        return "duplicate"
    new_request = dict(rec)
    for key in _REVIVE_DROP_KEYS:
        new_request.pop(key, None)
    new_request["rejected_by_admins"] = []
    new_request["recompleted_at"] = datetime.now(timezone.utc).isoformat()
    new_request["recompleted_by"] = actor_id
    pending.append(new_request)
    user_data["pending_requests"] = pending
    user_data["pending_balance"] = clamp_money(
        float(user_data.get("pending_balance", 0.0)) + float(new_request.get("amount", 0.0)))
    user_data["rejected_requests"].pop(r_index)
    user_data["rejected_emails"] = [
        e for e in (user_data.get("rejected_emails", []) or []) if normalize_email(e) != normalized]
    return len(pending) - 1, new_request


async def _rej_guard(query, actor_id: int) -> bool:
    if not is_admin_or_owner(actor_id):
        await query.answer("🚫", show_alert=True)
        return False
    return True


async def _show_rejected_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if not await _rej_guard(query, uid):
        return
    owner = _rej_is_owner(uid)
    prefix = "view_rejected" if owner else "admin_rejected"
    back = ("approval_requests", t(uid, "appr_title")) if owner else ("admin_settings", t(uid, "admin_btn_settings"))
    parts = query.data.split(":")
    type_filter = None
    split = _rej_split_applies(uid)
    if parts[0] == "admin_rejected_t":
        # قسم محدد: يُقبل فقط لمن يملك صلاحية التقسيم (تحقق من جهة الخادم وليس من الزر)
        if not split or len(parts) < 2 or parts[1] not in ("old", "new"):
            await query.answer("🚫", show_alert=True)
            return
        type_filter = parts[1]
        prefix = f"admin_rejected_t:{type_filter}"
        _REJ_LAST_TYPE[uid] = type_filter
        page_raw = parts[2] if len(parts) > 2 else "0"
    else:
        page_raw = parts[1] if len(parts) > 1 else "0"
    try:
        page = int(page_raw)
    except ValueError:
        page = 0
    rejected = [r for r in _collect_all("rejected_requests", sort_order="newest") if is_visible_rejected(r)]
    if split and type_filter is None:
        _REJ_LAST_TYPE.pop(uid, None)
        n_new = sum(1 for r in rejected if r.get("account_type") == "new")
        n_old = len(rejected) - n_new
        await query.edit_message_text(
            "❌ <b>الحسابات المرفوضة</b>\n\nاختر القسم:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                (f"📝 المرفوضة للإيميلات القديمة ({n_old})", "admin_rejected_t:old:0"),
                (f"🆕 المرفوضة للإيميلات الجديدة ({n_new})", "admin_rejected_t:new:0"),
                (back[1], back[0]),
            ]))
        return
    if type_filter:
        rejected = [r for r in rejected
                    if (r.get("account_type") == "new") == (type_filter == "new")]
        back = ("admin_rejected:0", "🔙 أقسام المرفوضة")
    if not rejected:
        await query.edit_message_text(t(uid, "appr_empty"), reply_markup=kb_single(back[1], back[0]))
        return

    def label(rej):
        icon = REJECT_REASON_ICONS.get(rej.get("reject_reason", "unknown"), "❌")
        tier_icon = "🟢" if rej.get("has_app_pass") else "🟡" if rej.get("has_totp") else "🔵"
        type_icon = "🆕" if rej.get("account_type") == "new" else "📝"
        lock = "🔒" if rej.get("final_rejected") else ""
        email = str(rej.get("email", ""))
        email_display = email[:16] + "..." if len(email) > 16 else email
        extra = ""
        if owner:
            name = str(rej.get("user_name", "") or "")
            extra = f" - {name[:10]}" if name else ""
        return (f"{type_icon}{tier_icon}{icon}{lock} {email_display}{extra}",
                f"rej_view:{rej['user_id']}:{account_callback_token(email)}")

    buttons = paginate_buttons(rejected, page, prefix, label)
    buttons.append((back[1], back[0]))
    await query.edit_message_text(
        t(uid, "appr_rejected_title", count=len(rejected)),
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def admin_rejected_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_rejected_list(update, context)


async def view_rejected_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_rejected_list(update, context)


async def rejected_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفاصيل الطلب المرفوض بنفس ترتيب المنتظر: الإيميل ثم الباسورد ثم رمز المصادقة ثم كلمة مرور التطبيق."""
    query = update.callback_query
    uid = query.from_user.id
    if not await _rej_guard(query, uid):
        return
    owner = _rej_is_owner(uid)
    back_btn = (t(uid, "appr_btn_rejected"), _rej_list_cb(uid))
    try:
        parts = query.data.split(":")
        target_uid = int(parts[1])
        token = parts[2]
    except (IndexError, ValueError):
        await query.edit_message_text(t(uid, "appr_not_found"), reply_markup=kb_single(*back_btn))
        return
    user_data = get_user(target_uid)
    found = _find_rejected(user_data, token)
    if found is None:
        await query.edit_message_text(t(uid, "appr_not_found"), reply_markup=kb_single(*back_btn))
        return
    _, request = found
    token = account_callback_token(request.get("email", ""))
    reason = request.get("reject_reason", "unknown")
    reason_text = request.get("reject_reason_text") or REJECT_REASON_MESSAGES.get(reason, reason)
    has_totp = bool(request.get("has_totp"))
    has_app_pass = bool(request.get("has_app_pass"))
    tier_icon = "🟢" if has_app_pass else "🟡" if has_totp else "🔵"
    tier_text = (t(uid, "appr_tier_full") if has_app_pass
                 else t(uid, "appr_tier_totp") if has_totp else t(uid, "appr_tier_pass_only"))
    type_text = t(uid, "appr_type_new") if request.get("account_type") == "new" else t(uid, "appr_type_old")

    msg = "📋 <b>تفاصيل الطلب المرفوض (" + type_text + ")</b>\n\n"
    msg += t(uid, "admin_req_detail_seller", name=tg_html_escape(request.get("user_name", "unknown")),
             username=tg_html_escape(request.get("user_username", "none")))
    msg += t(uid, "admin_req_detail_email", email=tg_html_escape(request.get("email", "")))
    msg += t(uid, "admin_req_detail_pass", password=tg_html_escape(request.get("password", "")))
    if has_totp:
        msg += t(uid, "admin_req_detail_totp", totp=tg_html_escape(request.get("totp", "")))
    else:
        msg += t(uid, "admin_req_detail_totp_none")
    if has_app_pass:
        msg += t(uid, "admin_req_detail_app_pass",
                 status=f"<code>{tg_html_escape(format_app_password(request.get('app_pass', '')))}</code>")
    else:
        msg += t(uid, "admin_req_detail_app_pass", status=t(uid, "appr_none"))
    msg += t(uid, "owner_pending_detail_misc", tier_icon=tier_icon, tier_text=tier_text,
             uid=target_uid, amount=float(request.get("amount", 0) or 0))
    msg += "\n📝 <b>سبب الرفض:</b> " + tg_html_escape(str(reason_text)) + "\n"
    if request.get("rejected_by"):
        msg += (f"❌ <b>من رفضه:</b> {tg_html_escape(str(request.get('rejected_by_name', 'Unknown')))}"
                f" (<code>{request.get('rejected_by')}</code>)\n")
    admins_rj = request.get("rejected_by_admins") or []
    if admins_rj:
        msg += f"⚠️ <b>عدد الرفضات:</b> <code>{len(admins_rj)}</code>\n"
    locked = bool(request.get("final_rejected"))
    if locked:
        msg += "🔒 <b>رفض نهائي مؤكد</b>\n"

    buttons = []
    if not locked or owner:
        if not (has_totp and has_app_pass):
            buttons.append(("📝 إكمال المعلومات", f"rej_complete:{target_uid}:{token}"))
        buttons.append(("✅ قبول", f"rej_accept:{target_uid}:{token}"))
        buttons.append(("❌ رفض", f"rej_reject:{target_uid}:{token}"))
    if owner:
        buttons.append((t(uid, "appr_give_points"), f"give_points:{target_uid}:{token}"))
    buttons.append(back_btn)
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def _rej_revive_or_report(update, context, target_uid: int, token: str, actor_id: int):
    """يُخرج السجل إلى المنتظرة ويحفظ. يرجع (index, request) أو None بعد إبلاغ المستخدم."""
    query = update.callback_query
    back = kb_single(t(actor_id, "appr_btn_rejected"), _rej_list_cb(actor_id))
    user_data = get_user(target_uid)
    res = revive_rejected_request(user_data, token, actor_id)
    if res is None:
        await query.edit_message_text(t(actor_id, "appr_not_found"), reply_markup=back)
        return None
    if res == "duplicate":
        await query.edit_message_text(
            "⚠️ هذا الإيميل موجود حالياً كطلب منتظر أو حساب مقبول، لذلك لا يمكن إعادته.",
            reply_markup=back)
        return None
    save_user(target_uid, user_data)
    return res


async def rej_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    actor_id = query.from_user.id
    if not await _rej_guard(query, actor_id):
        return
    parts = query.data.split(":")
    target_uid, token = int(parts[1]), parts[2]
    res = await _rej_revive_or_report(update, context, target_uid, token, actor_id)
    if res is None:
        return
    record_admin_activity(actor_id, "recomplete")
    token = account_callback_token(res[1].get("email", ""))
    if actor_id == OWNER_ID:
        query.data = f"complete_request_owner:{target_uid}:{token}"
        await complete_request_owner(update, context)
    else:
        query.data = f"admin_complete_start:{target_uid}:{token}"
        await admin_complete_start(update, context)


async def rej_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قبول طلب مرفوض: يعود للمنتظرة ثم يُفحص ويُقبل (الأدمن والعضو يحصلان على نقاطهما كالمعتاد)."""
    query = update.callback_query
    actor_id = query.from_user.id
    if not await _rej_guard(query, actor_id):
        return
    parts = query.data.split(":")
    target_uid, token = int(parts[1]), parts[2]
    found = _find_rejected(get_user(target_uid), token)
    if found is None:
        await query.edit_message_text(t(actor_id, "appr_not_found"),
                                      reply_markup=kb_single(t(actor_id, "appr_btn_rejected"), _rej_list_cb(actor_id)))
        return
    rec = found[1]
    if rec.get("final_rejected") and actor_id != OWNER_ID:
        await query.answer("🔒 رفض نهائي — للمالك فقط.", show_alert=True)
        return
    if not (rec.get("has_totp") and rec.get("has_app_pass")):
        await query.answer("⚠️ المعلومات ناقصة. اضغط «إكمال المعلومات» أولاً.", show_alert=True)
        return
    res = await _rej_revive_or_report(update, context, target_uid, token, actor_id)
    if res is None:
        return
    await admin_verify_and_store(update, context, target_uid, res[0], hold_seconds=LEAVE_HOLD_SECONDS)


async def rej_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة خيارات الرفض في المرفوضة: رفض نهائي (+ خيارات إضافية للمالك)."""
    query = update.callback_query
    actor_id = query.from_user.id
    if not await _rej_guard(query, actor_id):
        return
    parts = query.data.split(":")
    target_uid, token = int(parts[1]), parts[2]
    found = _find_rejected(get_user(target_uid), token)
    if found is None:
        await query.edit_message_text(t(actor_id, "appr_not_found"),
                                      reply_markup=kb_single(t(actor_id, "appr_btn_rejected"), _rej_list_cb(actor_id)))
        return
    rec = found[1]
    token = account_callback_token(rec.get("email", ""))
    buttons = []
    text = f"📧 <code>{tg_html_escape(rec.get('email', ''))}</code>\n\n"
    if actor_id == OWNER_ID:
        text += ("ماذا تريد أن تفعل بهذا الطلب المرفوض؟\n\n"
                 "• <b>رفض نهائي</b>: تثبيت الرفض (🔒).\n"
                 "• <b>إعادة للمنتظرة</b>: يعود الطلب لقائمة الانتظار كما هو.\n"
                 "• <b>حذف السجل</b>: يُزال من المرفوضة نهائياً.")
    else:
        text += "هل تريد تثبيت <b>الرفض النهائي</b>؟"
    buttons.append(("🔒 رفض نهائي", f"rej_final:{target_uid}:{token}"))
    if actor_id == OWNER_ID:
        buttons.append(("↩️ إعادة للمنتظرة", f"rej_requeue:{target_uid}:{token}"))
        buttons.append(("🗑 حذف السجل", f"rej_delete:{target_uid}:{token}"))
    buttons.append((t(actor_id, "appr_btn_back_detail"), f"rej_view:{target_uid}:{token}"))
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def rej_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    actor = update.effective_user
    if not await _rej_guard(query, actor.id):
        return
    parts = query.data.split(":")
    target_uid, token = int(parts[1]), parts[2]
    user_data = get_user(target_uid)
    found = _find_rejected(user_data, token)
    if found is None:
        await query.edit_message_text(t(actor.id, "appr_not_found"),
                                      reply_markup=kb_single(t(actor.id, "appr_btn_rejected"), _rej_list_cb(actor.id)))
        return
    idx, rec = found
    rec["final_rejected"] = True
    rec["final_rejected_by"] = actor.id
    rec["final_rejected_at"] = datetime.now(timezone.utc).isoformat()
    user_data["rejected_requests"][idx] = rec
    save_user(target_uid, user_data)
    record_admin_activity(actor.id, "reject")
    await query.edit_message_text(
        f"🔒 <b>تم تثبيت الرفض النهائي</b>\n\n📧 <code>{tg_html_escape(rec.get('email', ''))}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single(t(actor.id, "appr_btn_rejected"), _rej_list_cb(actor.id)))


async def rej_requeue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """للمالك: إعادة الطلب إلى المنتظرة كما هو."""
    query = update.callback_query
    actor_id = query.from_user.id
    if actor_id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid, token = int(parts[1]), parts[2]
    res = await _rej_revive_or_report(update, context, target_uid, token, actor_id)
    if res is None:
        return
    record_admin_activity(actor_id, "recomplete")
    email = res[1].get("email", "")
    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=(f"🔄 <b>تمت إعادة طلبك للمراجعة</b>\n\n📧 <code>{tg_html_escape(email)}</code>\n\n"
                  f"<i>سيتم مراجعة حسابك من جديد من قبل الإدارة.</i>"),
            parse_mode=ParseMode.HTML)
    except Exception:
        pass
    query.data = f"pending_detail:{target_uid}:{account_callback_token(email)}"
    await pending_detail(update, context)


async def rej_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """للمالك: حذف سجل مرفوض (مع تأكيد)."""
    query = update.callback_query
    actor_id = query.from_user.id
    if actor_id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid, token = int(parts[1]), parts[2]
    user_data = get_user(target_uid)
    found = _find_rejected(user_data, token)
    if found is None:
        await query.edit_message_text(t(actor_id, "appr_not_found"),
                                      reply_markup=kb_single(t(actor_id, "appr_btn_rejected"), _rej_list_cb(actor_id)))
        return
    idx, rec = found
    if len(parts) < 4 or parts[3] != "ok":
        await query.edit_message_text(
            f"🗑 حذف السجل نهائياً؟\n\n📧 <code>{tg_html_escape(rec.get('email', ''))}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("✅ نعم، احذف", f"rej_delete:{target_uid}:{token}:ok"),
                (t(actor_id, "btn_cancel"), f"rej_view:{target_uid}:{token}"),
            ]))
        return
    user_data["rejected_requests"].pop(idx)
    normalized = normalize_email(rec.get("email", ""))
    user_data["rejected_emails"] = [
        e for e in (user_data.get("rejected_emails", []) or []) if normalize_email(e) != normalized]
    save_user(target_uid, user_data)
    await query.edit_message_text("✅ تم حذف السجل.",
                                  reply_markup=kb_single(t(actor_id, "appr_btn_rejected"), _rej_list_cb(actor_id)))



# ==================== RECOVERY FINISH (إنهاء ببريد استرداد فقط) ====================
def _finish_button(target_uid: int, token: str) -> tuple:
    return ("🏁 إنهاء (بريد استرداد فقط)", f"admin_recovery_finish:{target_uid}:{token}")


def _finish_guard(uid: int, request: Optional[dict]) -> Optional[str]:
    if not recovery_can_finish(uid):
        return "🚫 المالك لم يمنحك صلاحية «الإنهاء»."
    if request is None:
        return "⚠️ الطلب غير موجود."
    rec = request.get("recovery") or {}
    if not request.get("recovery_verified") or not rec.get("email"):
        return "⚠️ لم يتم التحقق من بريد الاسترداد بعد."
    return None


async def admin_recovery_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شاشة تأكيد قبل الإنهاء."""
    query = update.callback_query
    uid = query.from_user.id
    parts = query.data.split(":")
    target_uid, token = int(parts[1]), parts[2]
    match = find_pending_request(get_user(target_uid), token)
    request = match[1] if match else None
    err = _finish_guard(uid, request)
    if err:
        await query.answer(err, show_alert=True)
        return
    rec = request["recovery"]
    amount = cents_to_money(money_to_cents(request.get("amount", 0.0)))
    bonus = RECOVERY_FINISH_ADMIN_BONUS if uid != OWNER_ID else 0.0
    token = account_callback_token(request.get("email", ""))
    await query.edit_message_text(
        "🏁 <b>تأكيد إنهاء الإيميل ببريد استرداد فقط</b>\n\n"
        f"📧 <code>{tg_html_escape(request.get('email', ''))}</code>\n"
        f"🔑 <code>{tg_html_escape(request.get('password', ''))}</code>\n"
        f"📮 بريد الاسترداد: <code>{tg_html_escape(rec.get('email', ''))}</code>\n\n"
        f"💰 سعر العضو: <b>${amount:.2f}</b> (يُحجز 24 ساعة ثم يتحرر تلقائياً بدون فحص ثانٍ)\n"
        f"👮 مكافأتك: <b>${bonus:.2f}</b>\n\n"
        "سيذهب الإيميل إلى قسم «بريد الاسترداد» عند المالك بصيغة إيميل:باسورد:بريد_الاسترداد.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([("✅ تأكيد الإنهاء", f"admin_recovery_finish_ok:{target_uid}:{token}"),
                                  ("❌ إلغاء", f"admin_completion_cancel:{target_uid}:{token}")]))


async def admin_recovery_finish_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram_bot.members import schedule_leave_check
    query = update.callback_query
    admin_id = query.from_user.id
    parts = query.data.split(":")
    target_uid, token = int(parts[1]), parts[2]
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    request = match[1] if match else None
    err = _finish_guard(admin_id, request)
    if err:
        await query.answer(err, show_alert=True)
        return
    index = match[0]
    pending = user_data.get("pending_requests", [])
    rec = request.get("recovery") or {}
    email = request.get("email", "")
    amount = cents_to_money(money_to_cents(request.get("amount", 0.0)))
    bonus = RECOVERY_FINISH_ADMIN_BONUS if admin_id != OWNER_ID else 0.0
    now = datetime.now(timezone.utc)
    admin_user = update.effective_user
    admin_name = admin_user.full_name or "Unknown"
    admin_username = admin_user.username or "None"

    record = {k: v for k, v in request.items()
              if k not in ("recovery", "recovery_verified", "recovery_verified_at", "rejected_by_admins")}
    record.update({
        "amount": amount, "admin_bonus": bonus,
        "completed_by_admin": admin_id, "completed_by_admin_name": admin_name,
        "completed_by_admin_username": admin_username, "completed_at": now.isoformat(),
        "timestamp": now.isoformat(), "approval_time": now.isoformat(),
        "release_at": (now + timedelta(seconds=LEAVE_HOLD_SECONDS)).isoformat(),
        "hold_seconds": LEAVE_HOLD_SECONDS,
        "acceptance_mode": "recovery_finish", "recovery_finish": True,
        "recovery_mail": rec.get("email", ""), "recovery_finished_at": now.isoformat(),
        "skip_recheck": True,           # لا يوجد App Password: يتحرر بعد 24 ساعة بدون فحص IMAP ثانٍ
        "admin_bonus_status": "pending" if (admin_id != OWNER_ID and bonus > 0) else "not_applicable",
        "extracted": False, "approved_with_leave": True, "leave_confirmed": False,
        "auto_verified": False,
        "user_name": request.get("user_name", "Unknown"),
        "user_username": request.get("user_username", "None"),
        "account_type": request.get("account_type", "old"),
        "verification": {"level": "recovery", "badge": "📮", "imap_ok": False, "totp_ok": False,
                         "message": "Finished with recovery email only",
                         "verified_at": now.isoformat(), "verified_by": "recovery_finish", "admin_id": admin_id},
    })
    user_data.setdefault("approved_accounts", []).append(record)
    user_data["pending_balance"] = clamp_money(float(user_data.get("pending_balance", 0.0)) - amount)
    user_data["hold_balance"] = clamp_money(float(user_data.get("hold_balance", 0.0)) + amount)
    user_data["total_credited_balance"] = clamp_money(float(user_data.get("total_credited_balance", 0.0) or 0.0) + amount)
    user_data["total_approved_emails"] = int(user_data.get("total_approved_emails", 0)) + 1
    pending.pop(index)
    user_data["pending_requests"] = pending
    add_transaction(user_data, "hold", amount, f"Finished (recovery only) by admin {admin_id} - hold 24h", email)
    register_approval_reset(user_data)
    save_user(target_uid, user_data)
    record_admin_activity(admin_id, "accept")
    mark_recovery_used(rec.get("email", ""), email)

    try:
        await check_contest_award(context, target_uid)
    except Exception:
        logger.exception("Contest check failed")
    if bonus > 0 and admin_id != OWNER_ID:
        admin_data = get_user(admin_id)
        admin_data["admin_pending_balance"] = round(float(admin_data.get("admin_pending_balance", 0.0)) + bonus, 2)
        add_transaction(admin_data, "admin_bonus_pending", bonus, f"Pending bonus for {email}", email)
        save_user(admin_id, admin_data)
    await schedule_leave_check(context, target_uid, email, record["release_at"])

    referred_by = user_data.get("referred_by")
    if referred_by:
        ref_bonus = float(load_config().get("referral_bonus", 0.0))
        if ref_bonus > 0:
            ref = get_user(referred_by)
            ref["referral_earnings"] = clamp_money(float(ref.get("referral_earnings", 0.0)) + ref_bonus)
            ref["balance"] = clamp_money(float(ref.get("balance", 0.0)) + ref_bonus)
            ref["total_credited_balance"] = clamp_money(float(ref.get("total_credited_balance", 0.0) or 0.0) + ref_bonus)
            ref["total_referrals"] = int(ref.get("total_referrals", 0)) + 1
            add_transaction(ref, "referral", ref_bonus, f"Referral bonus for user {target_uid}", email)
            save_user(referred_by, ref)
    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=t(target_uid, "member_account_verified_msg", email=email, amount=amount, hours=LEAVE_HOLD_SECONDS // 3600),
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(f"📮 <b>إيميل أُنهي ببريد استرداد فقط</b>\n👮 {admin_id} @{tg_html_escape(admin_username)}\n"
                  f"👤 {target_uid}\n📧 <code>{tg_html_escape(email)}</code>\n"
                  f"📮 <code>{tg_html_escape(rec.get('email', ''))}</code>\n💰 ${amount:.2f} | مكافأة المشرف ${bonus:.2f}"),
            parse_mode=ParseMode.HTML)
    except Exception:
        pass
    for k in ("admin_completing_uid", "admin_completing_index", "admin_completing_token", "admin_approval_step"):
        context.user_data.pop(k, None)
    await query.edit_message_text(
        f"✅ تم إنهاء <code>{tg_html_escape(email)}</code> ببريد الاسترداد.\n"
        f"💰 العضو: ${amount:.2f} (24 ساعة) | 👮 مكافأتك: ${bonus:.2f}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single(t(admin_id, "appr_btn_requests"), "admin_requests:0"))


# ==================== OWNER: RECOVERY ACCOUNTS SECTION ====================
def _recovery_records(acc_type: str, only_unextracted: bool) -> list:
    out = []
    for uid_str, enc in load_json(USERS_DB).items():
        data = decrypt_user_data(enc)
        for acc in data.get("approved_accounts", []) or []:
            if not acc.get("recovery_finish") or acc.get("rejected_at_24h"):
                continue
            if (acc.get("account_type") == "new") != (acc_type == "new"):
                continue
            if only_unextracted and (acc.get("extracted") or acc.get("removed_from_extraction")):
                continue
            row = dict(acc)
            row["user_id"] = uid_str
            out.append(row)
    return sort_records_newest_first(out)


def build_recovery_export(accounts: list) -> bytes:
    """3 خانات دائماً: الإيميل:الباسورد:بريد الاسترداد"""
    return "\n".join(f"{a.get('email', '')}:{a.get('password', '')}:{a.get('recovery_mail', '')}"
                     for a in accounts).encode("utf-8")


async def recovery_accounts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    c = {(sc, ty): len(_recovery_records(ty, sc == "un")) for sc in ("un", "all") for ty in ("old", "new")}
    buttons = [
        (f"📮 بريد الاسترداد غير المستخرج — قديم ({c[('un', 'old')]})", "rec_acc:un:old"),
        (f"📮 بريد الاسترداد غير المستخرج — جديد ({c[('un', 'new')]})", "rec_acc:un:new"),
        (f"📮 جميع بريد الاسترداد — قديم ({c[('all', 'old')]})", "rec_acc:all:old"),
        (f"📮 جميع بريد الاسترداد — جديد ({c[('all', 'new')]})", "rec_acc:all:new"),
        ("🔙 إدارة الحسابات", "all_accounts_section"),
    ]
    await query.edit_message_text("📮 <b>بريد الاسترداد</b>\nالصيغة: إيميل:باسورد:بريد_الاسترداد",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def recovery_accounts_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    _, scope, acc_type = query.data.split(":")
    rows = _recovery_records(acc_type, scope == "un")
    title = f"{'غير المستخرج' if scope == 'un' else 'الجميع'} — {'جديد' if acc_type == 'new' else 'قديم'}"
    back = ("🔙 بريد الاسترداد", "recovery_accounts_menu")
    if not rows:
        await query.edit_message_text(f"📮 {title}\n\nلا توجد حسابات.", reply_markup=kb_single(*back[::1]))
        return
    lines = [f"📮 <b>{title}</b> ({len(rows)})", ""]
    for i, a in enumerate(rows[:10], 1):
        flag = "✅" if a.get("extracted") else "🆕"
        lines.append(f"{i}. {flag} <code>{tg_html_escape(a.get('email', ''))}:{tg_html_escape(a.get('password', ''))}:"
                     f"{tg_html_escape(a.get('recovery_mail', ''))}</code>")
    if len(rows) > 10:
        lines.append(f"… و{len(rows) - 10} آخرين (يظهرون في التصدير)")
    buttons = [(f"📥 تصدير {title}", f"rec_export:{scope}:{acc_type}"), back]
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def recovery_accounts_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    _, scope, acc_type = query.data.split(":")
    rows = _recovery_records(acc_type, scope == "un")
    back = kb_single("🔙 بريد الاسترداد", "recovery_accounts_menu")
    if not rows:
        await query.edit_message_text("لا توجد حسابات للتصدير.", reply_markup=back)
        return
    name = f"recovery_{scope}_{acc_type}.txt"
    await context.bot.send_document(chat_id=OWNER_ID, document=io.BytesIO(build_recovery_export(rows)),
                                    filename=name, caption=f"📮 {len(rows)} حساب (إيميل:باسورد:بريد_الاسترداد)")
    if scope == "un":
        wanted = {(r["user_id"], normalize_email(r.get("email", ""))) for r in rows}
        for uid_str, enc in load_json(USERS_DB).items():
            data = decrypt_user_data(enc)
            changed = False
            for acc in data.get("approved_accounts", []) or []:
                if acc.get("recovery_finish") and (uid_str, normalize_email(acc.get("email", ""))) in wanted \
                        and not acc.get("extracted"):
                    acc["extracted"] = True
                    changed = True
            if changed:
                save_user(int(uid_str), data)
    await query.edit_message_text(f"✅ تم تصدير {len(rows)} حساب.", reply_markup=back)


# ==================== OWNER: PER-ADMIN RECOVERY PERMISSIONS ====================
async def recovery_perm_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    try:
        admin_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        return
    perms = recovery_perms(admin_id)
    buttons = [
        (f"{'✅' if perms['complete'] else '⬜'} حق الإكمال (2FA + كلمة مرور التطبيق)", f"recovery_perm_toggle:{admin_id}:complete"),
        (f"{'✅' if perms['finish'] else '⬜'} حق الإنهاء (بريد استرداد فقط)", f"recovery_perm_toggle:{admin_id}:finish"),
        ("🔙 شرط بريد الاسترداد", "recovery_admins_menu"),
    ]
    await query.edit_message_text(
        f"⚙️ <b>صلاحيات المشرف</b> <code>{admin_id}</code>\n\n"
        "بعد التحقق من بريد الاسترداد يظهر للمشرف:\n"
        "• <b>إكمال</b>: يضيف رمز المصادقة وكلمة مرور التطبيق.\n"
        "• <b>إنهاء</b>: ينهي الإيميل ببريد الاسترداد فقط (يذهب لقسم بريد الاسترداد).",
        parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def recovery_perm_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    try:
        _, admin_raw, key = query.data.split(":")
        admin_id = int(admin_raw)
    except ValueError:
        return
    if key not in ("complete", "finish"):
        return
    config = dict(load_config())
    cfg = dict(config.get("recovery_perms") or {})
    perms = recovery_perms(admin_id)
    perms[key] = not perms[key]
    cfg[str(admin_id)] = perms
    config["recovery_perms"] = cfg
    save_config(config)
    query.data = f"recovery_perm_menu:{admin_id}"
    await recovery_perm_menu(update, context)


# ==================== RECOVERY-EMAIL GATE (بريد الاسترداد قبل الإكمال) ====================
# قبل أن يُكمل المشرف أي إيميل: يحدد البوت بريد استرداد (من أحدث حساب مقبول يعمل له IMAP)،
# يطلب من المشرف وضعه كبريد استرداد للحساب، ثم يفحص صندوق وارد هذا البريد ليرى هل وصل
# إشعار Google يخص الحساب. بدون ذلك لا يكمل المشرف (المالك مُستثنى).
RECOVERY_FOLDERS = ("INBOX", "[Gmail]/Spam")
RECOVERY_MAX_LOGIN_TESTS = 25   # أقصى عدد محاولات دخول IMAP في كل مرحلة اختيار
_RECOVERY_RESERVED: dict = {}   # بريد -> {"until": وقت, "for": الحساب المحجوز له} حتى لا يأخذه مشرفان معاً


def recovery_check_admins() -> set:
    """معرّفات المشرفين الذين يُطبَّق عليهم شرط بريد الاسترداد (يحددها المالك). الباقون على الطريقة القديمة."""
    raw = load_config().get("recovery_check_admins", [])
    out = set()
    for x in raw if isinstance(raw, list) else []:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out


def recovery_check_applies(admin_id: int) -> bool:
    return admin_id != OWNER_ID and int(admin_id) in recovery_check_admins()


RECOVERY_FINISH_ADMIN_BONUS = 0.02   # مكافأة المشرف عند «إنهاء» إيميل ببريد استرداد فقط


def recovery_perms(admin_id: int) -> dict:
    """صلاحيات مشرف بريد الاسترداد (يحددها المالك): الإكمال (2FA+App Password) والإنهاء (بريد استرداد فقط).
    الافتراضي: الإكمال مسموح (السلوك القديم)، والإنهاء ممنوع حتى يفعّله المالك."""
    cfg = load_config().get("recovery_perms") or {}
    p = cfg.get(str(admin_id)) if isinstance(cfg, dict) else None
    p = p if isinstance(p, dict) else {}
    return {"complete": bool(p.get("complete", True)), "finish": bool(p.get("finish", False))}


def recovery_can_complete(admin_id: int) -> bool:
    return True if not recovery_check_applies(admin_id) else recovery_perms(admin_id)["complete"]


def recovery_can_finish(admin_id: int) -> bool:
    return recovery_check_applies(admin_id) and recovery_perms(admin_id)["finish"]


def _recovery_candidates(exclude_email: str) -> list:
    exclude = normalize_email(exclude_email)
    found = []
    for _uid, encrypted in load_json(USERS_DB).items():
        for acc in decrypt_user_data(encrypted).get("approved_accounts", []) or []:
            mail = str(acc.get("email", "")).strip()
            pw = str(acc.get("app_pass", "")).replace(" ", "")
            if not mail or not pw or normalize_email(mail) == exclude:
                continue
            found.append((str(acc.get("completed_at") or acc.get("timestamp") or ""), mail, pw))
    found.sort(reverse=True)
    seen, out = set(), []
    for _, m, p in found:
        key = normalize_email(m)
        if key not in seen:
            seen.add(key)
            out.append((m, p))
    return out


def _recovery_used_map() -> dict:
    data = load_config().get("recovery_mailboxes", {})
    return data if isinstance(data, dict) else {}


def mark_recovery_used(mailbox: str, target_email: str) -> None:
    config = load_config()
    used = config.get("recovery_mailboxes", {})
    if not isinstance(used, dict):
        used = {}
    key = normalize_email(mailbox)
    entry = used.get(key) if isinstance(used.get(key), dict) else {}
    used[key] = {"at": datetime.now(timezone.utc).isoformat(), "count": int(entry.get("count", 0) or 0) + 1,
                 "last_for": normalize_email(target_email)}
    config["recovery_mailboxes"] = used
    save_config(config)
    _RECOVERY_RESERVED.pop(key, None)


async def pick_recovery_mailbox(exclude_email: str, skip: tuple = (), allow_reuse: bool = True) -> Optional[dict]:
    """يختار بريد استرداد لم يُستعمل من قبل. لا يُعاد استعمال بريد إلا إذا انتهت كل البريدات
    الصالحة (التي يعمل لها IMAP) — عندها فقط يُختار الأقدم استعمالاً."""
    target_key = normalize_email(exclude_email)
    skip_keys = {normalize_email(x) for x in skip}
    now = time.time()
    used = _recovery_used_map()
    candidates = await asyncio.to_thread(_recovery_candidates, exclude_email)

    def free(mail: str) -> bool:
        key = normalize_email(mail)
        res = _RECOVERY_RESERVED.get(key)
        reserved_by_other = bool(res and res["until"] > now and res["for"] != target_key)
        return key not in skip_keys and not reserved_by_other

    def reserve(mail: str):
        _RECOVERY_RESERVED[normalize_email(mail)] = {"until": time.time() + 1800, "for": target_key}

    unused = [(m, p) for m, p in candidates if normalize_email(m) not in used and free(m)]
    for mail, pw in unused[:RECOVERY_MAX_LOGIN_TESTS]:
        ok, _msg = await asyncio.to_thread(_imap_login_sync, mail, pw)
        if ok:
            reserve(mail)
            return {"email": mail, "app_pass": pw, "reused": False}
    if not allow_reuse:
        return None
    # لا يوجد بريد غير مستعمل يعمل: نسمح بتكرار الأقدم استعمالاً (حالة الانتهاء فقط)
    reuse = sorted([(m, p) for m, p in candidates if normalize_email(m) in used and free(m)],
                   key=lambda mp: str((used.get(normalize_email(mp[0])) or {}).get("at", "")))
    for mail, pw in reuse[:RECOVERY_MAX_LOGIN_TESTS]:
        ok, _msg = await asyncio.to_thread(_imap_login_sync, mail, pw)
        if ok:
            reserve(mail)
            return {"email": mail, "app_pass": pw, "reused": True}
    return None


RECOVERY_KEYWORDS = (
    "recovery", "backup email", "alternate email", "استرداد", "الاسترداد", "استعادة", "احتياطي",
)


def _looks_like_recovery_notice(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in RECOVERY_KEYWORDS)


def _mentions_account(text: str, local: str, domain: str) -> bool:
    low = text.lower()
    if f"{local}@{domain}" in low:
        return True
    if domain in ("gmail.com", "googlemail.com") and "." in local:
        if f"{local.replace('.', '')}@{domain}" in low.replace(".", "") or re.sub(r"\.", "", local) + "@" in low.replace(".", ""):
            return True
    for k in range(1, min(3, len(local)) + 1):
        if re.search(re.escape(local[:k]) + r"[*\u2022\u00b7\u2024\u2026]+[^@\s]{0,3}@" + re.escape(domain), low):
            return True
    return len(local) >= 6 and local in low


RECOVERY_SCAN_BUDGET = 3600      # ثوانٍ: عملياً بلا حد (يقرأ الصندوق كله)
RECOVERY_SCAN_CHUNK = 40         # عدد الرسائل في كل دفعة جلب
RECOVERY_SCAN_MAX_PER_FOLDER = None  # بلا حد: كل الرسائل في كل مجلد


def _imap_list_folders(imap) -> list:
    """كل المجلدات القابلة للقراءة (وارد، كل البريد، مزعج، محذوف، مرسل...) بدون المجلدات المكررة (المهمة/المميزة)."""
    import re as _re
    folders = []
    try:
        typ, data = imap.list()
    except Exception:
        return ["INBOX"]
    if typ != "OK":
        return ["INBOX"]
    for raw in data or []:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        m = _re.match(r'\((?P<flags>[^)]*)\)\s+"?(?P<sep>[^"\s]*)"?\s+(?P<name>.+)$', line)
        if not m:
            continue
        flags = m.group("flags").lower()
        name = m.group("name").strip().strip('"')
        if "\\noselect" in flags or "\\nonexistent" in flags:
            continue
        folders.append(name)
    if "INBOX" not in folders:
        folders.insert(0, "INBOX")
    return folders


def _search_recovery_notice_sync(mailbox: str, app_pass: str, target_email: str, since_iso: str):
    """يقرأ صندوق بريد الاسترداد كله (كل المجلدات، المقروء وغير المقروء، القديم والجديد) رسالةً رسالة.

    يرجع (وُجد؟، عنوان الرسالة، قائمة رسائل Google بعد التحديد، عدد الرسائل المقروءة).
    - تُقبل أي رسالة (من أي مرسل وبأي تاريخ) تذكر الحساب كاملاً أو مخفياً.
    - وتُقبل رسالة Google جديدة (بعد التحديد) تبدو رسالة استرداد/تحقق.
    """
    import email as emaillib
    import html as _html
    from email.header import decode_header, make_header
    from email.utils import parsedate_to_datetime
    host, port = get_imap_host(mailbox)
    since = datetime.fromisoformat(since_iso) - timedelta(minutes=60)
    local, _, domain = str(target_email).strip().lower().partition("@")
    seen, scanned = [], 0
    strong_hit = ""
    deadline = time.monotonic() + RECOVERY_SCAN_BUDGET
    with imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=30) as imap:
        imap.login(mailbox, app_pass)
        for folder in _imap_list_folders(imap):
            if strong_hit or time.monotonic() > deadline:
                break
            try:
                typ, _ = imap.select(f'"{folder}"', readonly=True)
            except Exception:
                continue
            if typ != "OK":
                continue
            typ, data = imap.uid("SEARCH", None, "ALL")
            if typ != "OK" or not data or not data[0]:
                continue
            uids = data[0].split()
            for i in range(len(uids) - 1, -1, -RECOVERY_SCAN_CHUNK):  # من الأحدث للأقدم
                if time.monotonic() > deadline:
                    break
                chunk = uids[max(0, i - RECOVERY_SCAN_CHUNK + 1): i + 1]
                typ, msgdata = imap.uid("FETCH", b",".join(chunk).decode(), "(BODY.PEEK[])")
                if typ != "OK":
                    continue
                chunk_has_recent = False
                for item in msgdata or []:
                    if not isinstance(item, tuple):
                        continue
                    scanned += 1
                    msg = emaillib.message_from_bytes(item[1])
                    # لا تُقبل إلا الرسائل الواصلة بعد وقت تحديد بريد الاسترداد (تجاهل القديم تماماً)
                    try:
                        msg_dt = parsedate_to_datetime(msg.get("Date", ""))
                        if msg_dt.tzinfo is None:
                            msg_dt = msg_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        msg_dt = None
                    if msg_dt is None or msg_dt < since:
                        continue
                    chunk_has_recent = True
                    try:
                        subject = str(make_header(decode_header(msg.get("Subject", ""))))
                    except Exception:
                        subject = str(msg.get("Subject", ""))
                    chunks = [subject]
                    for part in msg.walk():
                        if part.get_content_type() in ("text/plain", "text/html"):
                            payload = part.get_payload(decode=True) or b""
                            chunks.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
                    text = " ".join(chunks)
                    try:
                        _un = _html.unescape(text)
                        text = text + " " + _un + " " + re.sub(r"<[^>]+>", "", _un)
                    except Exception:
                        pass
                    if "google" in str(msg.get("From", "")).lower():
                        seen.append(f"{folder}: {subject[:80]}")
                    # الدليل الوحيد: رسالة حديثة تذكر الحساب المستهدف نفسه (كاملاً أو مخفياً)
                    if _mentions_account(text, local, domain):
                        strong_hit = subject or "(بدون عنوان)"
                        break
                if strong_hit:
                    break
                if not chunk_has_recent:
                    break  # الأحدث أولاً: بقية هذا المجلد أقدم من وقت التحديد
    if strong_hit:
        return True, strong_hit, seen, scanned
    return False, "", seen, scanned


def _recovery_creds_block(request: dict) -> str:
    """بيانات الحساب للمشرف: الإيميل ثم الباسورد ثم رمز المصادقة إن وُجد (كلمة مرور التطبيق غير موجودة في هذه المرحلة)."""
    lines = [f"📧 الإيميل:\n<code>{tg_html_escape(request.get('email', ''))}</code>"]
    if request.get("password"):
        lines.append(f"🔑 الباسورد:\n<code>{tg_html_escape(request.get('password', ''))}</code>")
    if request.get("has_totp") and request.get("totp"):
        lines.append(f"🔐 رمز المصادقة:\n<code>{tg_html_escape(request.get('totp', ''))}</code>")
    return "\n\n".join(lines)


def _recovery_kb(target_uid: int, token: str, extra: list = None):
    rows = list(extra or [])
    rows.append(("❌ إلغاء", f"admin_completion_cancel:{target_uid}:{token}"))
    return kb_vertical(rows)


async def _recovery_show(update: Update, text: str, markup):
    """يعدّل رسالة الزر إن وُجدت، وإلا يرسل رسالة جديدة (عند الاستدعاء من إدخال نصي)."""
    q = update.callback_query
    if q is not None:
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
            return
        except Exception as exc:
            if "not modified" in str(exc).lower():
                return
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def admin_recovery_step(update: Update, context: ContextTypes.DEFAULT_TYPE, target_uid: int, token: str,
                              skip: tuple = ()):
    """الخطوة الأولى: البوت يقيّد الطلب ببريد استرداد لم يُستعمل أبداً، أو يختار المشرف بريداً من عنده
    (موجود مسبقاً على الحساب). في الحالتين يتحقق البوت قبل المتابعة."""
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await _recovery_show(update, "⚠️ الطلب غير موجود.", kb_single("🔙 الطلبات", "admin_requests:0"))
        return
    index, request = match
    token = account_callback_token(request.get("email", ""))
    context.user_data["admin_completing_uid"] = target_uid
    context.user_data["admin_completing_index"] = index
    context.user_data["admin_completing_token"] = token
    rec = request.get("recovery") or {}
    note = ""
    if not (rec.get("email") and rec.get("app_pass")):
        picked = await pick_recovery_mailbox(request.get("email", ""), skip=skip, allow_reuse=False)
        if picked:
            rec = {"email": picked["email"], "app_pass": picked["app_pass"], "source": "bot",
                   "assigned_at": datetime.now(timezone.utc).isoformat()}
            user_data = get_user(target_uid)
            match = find_pending_request(user_data, token)
            if match is not None:
                pending = user_data.get("pending_requests", [])
                pending[match[0]]["recovery"] = rec
                user_data["pending_requests"] = pending
                save_user(target_uid, user_data)
        else:
            rec = {}
            note = ("⚠️ لا يوجد حالياً بريد استرداد من البوت لم يُستعمل من قبل.\n"
                    "أرسل بريد استرداد من عندك (يجب أن يكون من حسابات البوت ولم يُستعمل سابقاً).\n\n")
    if rec.get("email"):
        context.user_data.pop("admin_approval_step", None)
        source = "من البوت" if rec.get("source") != "own" else "من عندك"
        text = ("📮 <b>الخطوة الأولى: بريد الاسترداد</b>\n\n"
                f"{_recovery_creds_block(request)}\n\n"
                f"📮 بريد الاسترداد المقيَّد ({source}):\n<code>{tg_html_escape(rec['email'])}</code>\n\n"
                "➡️ ضع هذا البريد كبريد استرداد للحساب ثم اضغط «تحقق».\n"
                "إذا كان الحساب فيه بريد استرداد موضوع مسبقاً أو تريد استعمال بريد من عندك فاضغط الزر الثاني.")
        buttons = [("🔎 وضعته — تحقق الآن", f"admin_recovery_check:{target_uid}:{token}"),
                   ("📮 بريد استرداد موجود مسبقاً / من عندي", f"admin_recovery_own:{target_uid}:{token}"),
                   ("🔄 بريد آخر من البوت", f"admin_recovery_reset:{target_uid}:{token}")]
        await _recovery_show(update, text, _recovery_kb(target_uid, token, buttons))
        return
    context.user_data["admin_approval_step"] = "waiting_recovery_email"
    await _recovery_show(
        update,
        "📮 <b>الخطوة الأولى: بريد الاسترداد</b>\n\n" + note +
        f"{_recovery_creds_block(request)}\n\n"
        "➡️ أرسل لي هنا <b>بريد الاسترداد</b> الموضوع على الحساب.\n"
        "⚠️ يجب أن يكون من الحسابات التي يملكها البوت (له IMAP يعمل)، ولم يُستعمل من قبل كبريد استرداد لأي إيميل.",
        _recovery_kb(target_uid, token))


async def admin_recovery_own(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """المشرف يختار بريد استرداد من عنده (أو موجود مسبقاً على الحساب)."""
    query = update.callback_query
    if not recovery_check_applies(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid, token = int(parts[1]), parts[2]
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text("⚠️ الطلب غير موجود.", reply_markup=kb_single("🔙 الطلبات", "admin_requests:0"))
        return
    pending = user_data.get("pending_requests", [])
    old = pending[match[0]].pop("recovery", None) or {}
    if old.get("email"):
        _RECOVERY_RESERVED.pop(normalize_email(old["email"]), None)
    user_data["pending_requests"] = pending
    save_user(target_uid, user_data)
    request = pending[match[0]]
    token = account_callback_token(request.get("email", ""))
    context.user_data["admin_completing_uid"] = target_uid
    context.user_data["admin_completing_index"] = match[0]
    context.user_data["admin_completing_token"] = token
    context.user_data["admin_approval_step"] = "waiting_recovery_email"
    await query.edit_message_text(
        "📮 <b>بريد الاسترداد من عندك</b>\n\n"
        f"{_recovery_creds_block(request)}\n\n"
        "➡️ أرسل لي بريد الاسترداد الموضوع على الحساب (حتى لو وضعته سابقاً).\n"
        "سأتأكد أنه: (1) لم يُستعمل كبريد استرداد لأي إيميل من قبل، (2) من حسابات البوت وله IMAP يعمل، "
        "ثم أفحص وصول إشعار Google.",
        parse_mode=ParseMode.HTML, reply_markup=_recovery_kb(target_uid, token))


async def admin_recovery_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    await admin_recovery_step(update, context, int(parts[1]), parts[2])


async def handle_admin_recovery_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل بريد الاسترداد من المشرف، يتحقق أنه ضمن حسابات البوت، ثم يفحص وارده."""
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    target_uid = context.user_data.get("admin_completing_uid")
    token = context.user_data.get("admin_completing_token")
    if text.casefold() in {"إلغاء", "الغاء", "cancel", "отмена"}:
        for k in ("admin_completing_uid", "admin_completing_index", "admin_completing_token", "admin_approval_step"):
            context.user_data.pop(k, None)
        await update.message.reply_text(t(uid, "msg_cancelled"),
                                        reply_markup=kb_single(t(uid, "appr_btn_requests"), "admin_requests:0"))
        return
    if target_uid is None or not token:
        await update.message.reply_text(t(uid, "msg_error"))
        return
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await update.message.reply_text(t(uid, "appr_not_found"))
        return
    index, request = match
    again = _recovery_kb(target_uid, token)
    mail = text.strip().lower()
    if "@" not in mail or " " in mail:
        await update.message.reply_text("⚠️ أرسل بريد الاسترداد بشكل صحيح (مثال: name@gmail.com).", reply_markup=again)
        return
    key = normalize_email(mail)
    if key == normalize_email(request.get("email", "")):
        await update.message.reply_text("⚠️ لا يمكن أن يكون بريد الاسترداد هو نفس الحساب.", reply_markup=again)
        return
    cands = await asyncio.to_thread(_recovery_candidates, request.get("email", ""))
    owned = {normalize_email(m): (m, p) for m, p in cands}
    if key not in owned:
        await update.message.reply_text(
            "❌ هذا البريد ليس من الحسابات التي يملكها البوت. استخدم بريداً من حسابات البوت المقبولة.",
            reply_markup=again)
        return
    used = _recovery_used_map()
    if key in used:
        await update.message.reply_text(
            "❌ هذا البريد استُعمل من قبل كبريد استرداد لإيميل آخر. اختر بريداً لم يُستعمل أبداً.", reply_markup=again)
        return
    res = _RECOVERY_RESERVED.get(key)
    if res and res["until"] > time.time() and res["for"] != normalize_email(request.get("email", "")):
        await update.message.reply_text("❌ هذا البريد محجوز الآن لطلب آخر. اختر بريداً آخر.", reply_markup=again)
        return
    mailbox, app_pass = owned[key]
    ok, msg = await asyncio.to_thread(_imap_login_sync, mailbox, app_pass)
    if not ok:
        await update.message.reply_text("❌ لا يعمل IMAP لهذا البريد حالياً، اختر بريداً آخر.", reply_markup=again)
        return
    rec = {"email": mailbox, "app_pass": app_pass, "assigned_at": datetime.now(timezone.utc).isoformat(),
           "source": "own", "lookback_min": 360}
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is not None:
        pending = user_data.get("pending_requests", [])
        pending[match[0]]["recovery"] = rec
        user_data["pending_requests"] = pending
        save_user(target_uid, user_data)
    _RECOVERY_RESERVED[key] = {"until": time.time() + 1800, "for": normalize_email(request.get("email", ""))}
    context.user_data.pop("admin_approval_step", None)
    progress = await update.message.reply_text("🔎 جاري فحص صندوق وارد بريد الاسترداد... قد يستغرق بضع ثوانٍ.")
    body, markup = await _recovery_check_core(target_uid, token, uid)
    await progress.edit_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)


async def _recovery_check_core(target_uid: int, token: str, admin_id: Optional[int] = None):
    """يفحص وارد بريد الاسترداد المحفوظ في الطلب. يرجع (نص HTML، لوحة أزرار)."""
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    if match is None:
        return "⚠️ الطلب غير موجود.", kb_single("🔙 الطلبات", "admin_requests:0")
    index, request = match
    rec = request.get("recovery") or {}
    retry = [("🔄 افحص مرة أخرى", f"admin_recovery_check:{target_uid}:{token}"),
             ("📮 بريد استرداد موجود مسبقاً / من عندي", f"admin_recovery_own:{target_uid}:{token}"),
             ("🔄 بريد آخر من البوت", f"admin_recovery_reset:{target_uid}:{token}")]
    try:
        since = datetime.fromisoformat(rec.get("assigned_at") or datetime.now(timezone.utc).isoformat()) - timedelta(
            minutes=int(rec.get("lookback_min", 60) or 60))
        found, subject, seen, scanned = await asyncio.wait_for(
            asyncio.to_thread(_search_recovery_notice_sync, rec["email"], rec["app_pass"],
                              request.get("email", ""), since.isoformat()),
            timeout=3700)
    except Exception as exc:
        logger.warning("recovery check failed for %s: %s", request.get("email"), exc)
        return ("⚠️ تعذّر فحص صندوق الوارد الآن (مشكلة اتصال أو IMAP). أعد المحاولة بعد قليل.",
                _recovery_kb(target_uid, token, retry))
    logger.info("recovery check mailbox=%s target=%s found=%s scanned=%s google_mails=%s",
                rec.get("email"), request.get("email"), found, scanned, seen[:6])
    if not found:
        return ("❌ لم يصل إلى هذا البريد إشعار من Google بأنه بريد استرداد لهذا الحساب.\n"
                "تأكد أنك وضعته بالضبط على الحساب، وانتظر دقيقة ثم أعد الفحص.\n"
                f"📬 قرأتُ {scanned} رسالة من كل مجلدات البريد، ورسائل Google الجديدة بينها: {len(seen)}\n\n"
                f"{_recovery_creds_block(request)}\n\n"
                f"📮 بريد الاسترداد المُدخل:\n<code>{tg_html_escape(rec['email'])}</code>",
                _recovery_kb(target_uid, token, retry))
    pending = user_data.get("pending_requests", [])
    mark_recovery_used(rec["email"], request.get("email", ""))
    pending[index]["recovery_verified"] = True
    pending[index]["recovery_verified_at"] = datetime.now(timezone.utc).isoformat()
    user_data["pending_requests"] = pending
    save_user(target_uid, user_data)
    complete = all(request.get(f) for f in ("totp", "app_pass"))
    nxt = (("➡️ متابعة التحقق", f"admin_accept_auto:{target_uid}:{token}") if complete
           else ("➡️ إكمال (إضافة 2FA وكلمة مرور التطبيق)", f"admin_complete_start:{target_uid}:{token}"))
    rows = []
    if admin_id is None or recovery_can_complete(admin_id):
        rows.append(nxt)
    if admin_id is not None and recovery_can_finish(admin_id):
        rows.append(_finish_button(target_uid, token))
    if not rows:
        return ("✅ تأكد بريد الاسترداد، لكن المالك لم يمنحك صلاحية الإكمال ولا الإنهاء.",
                _recovery_kb(target_uid, token))
    return "✅ تأكد بريد الاسترداد: وصل إشعار Google. اختر: إكمال أو إنهاء.", _recovery_kb(target_uid, token, rows)


async def admin_recovery_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid, token = int(parts[1]), parts[2]
    match = find_pending_request(get_user(target_uid), token)
    if match is None:
        await query.edit_message_text("⚠️ الطلب غير موجود.", reply_markup=kb_single("🔙 الطلبات", "admin_requests:0"))
        return
    rec = match[1].get("recovery") or {}
    if not rec.get("email") or not rec.get("app_pass"):
        await admin_recovery_step(update, context, target_uid, token)
        return
    await query.edit_message_text("🔎 جاري فحص صندوق وارد بريد الاسترداد... قد يستغرق بضع ثوانٍ.")
    body, markup = await _recovery_check_core(target_uid, token, query.from_user.id)
    await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)


async def admin_recovery_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بريد آخر من البوت (يتخطى البريد السابق)."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid, token = int(parts[1]), parts[2]
    user_data = get_user(target_uid)
    match = find_pending_request(user_data, token)
    skip = ()
    if match is not None:
        pending = user_data.get("pending_requests", [])
        old_rec = pending[match[0]].pop("recovery", None) or {}
        if old_rec.get("email"):
            _RECOVERY_RESERVED.pop(normalize_email(old_rec["email"]), None)
            skip = (old_rec["email"],)
        user_data["pending_requests"] = pending
        save_user(target_uid, user_data)
    await admin_recovery_step(update, context, target_uid, token, skip=skip)


async def recovery_admins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """للمالك: يحدد المشرفين الذين يُطبَّق عليهم شرط بريد الاسترداد."""
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    try:
        selected = recovery_check_admins()
        admins = sorted(int(a) for a in load_admins())
        buttons = []
        for a in admins:
            row = [(f"{'✅' if a in selected else '⬜'} {a}", f"recovery_admin_toggle:{a}")]
            if a in selected:
                perms = recovery_perms(a)
                row.append((f"⚙️ إكمال{'✅' if perms['complete'] else '⬜'} إنهاء{'✅' if perms['finish'] else '⬜'}",
                            f"recovery_perm_menu:{a}"))
            buttons.append(row)
        buttons.append(("🔙 الإدارية", "admin_management"))
        text = ("📮 شرط بريد الاسترداد\n\n"
                "✅ = يُطبَّق عليه الشرط (يرسل بريد الاسترداد ويفحصه البوت قبل الإكمال)\n"
                "⬜ = بالطريقة القديمة (بدون هذا الشرط)\n\n"
                "اضغط اسم المشرف للتبديل، وزر ⚙️ لتحديد حق الإكمال وحق الإنهاء." + ("" if admins else "\n\nلا يوجد أدمن حالياً"))
        await query.edit_message_text(text, reply_markup=kb_vertical(buttons))
    except Exception as exc:
        if "not modified" in str(exc).lower():
            return
        logger.exception("recovery_admins_menu failed")
        try:
            await query.message.reply_text(f"⚠️ تعذر عرض القائمة: {exc}")
        except Exception:
            pass


async def recovery_admin_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    try:
        admin_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        return
    try:
        selected = set(recovery_check_admins())
        selected.symmetric_difference_update({admin_id})
        config = dict(load_config())
        config["recovery_check_admins"] = sorted(selected)
        save_config(config)
    except Exception as exc:
        logger.exception("recovery_admin_toggle failed")
        await query.message.reply_text(f"⚠️ تعذر حفظ الإعداد: {exc}")
        return
    await recovery_admins_menu(update, context)


async def recovery_check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/recovery_check [off] — للمالك: عرض المشرفين المطبَّق عليهم الشرط، أو إلغاؤه عن الجميع."""
    if update.effective_user.id != OWNER_ID:
        return
    config = load_config()
    if context.args and context.args[0].lower() == "off":
        config["recovery_check_admins"] = []
        save_config(config)
    ids = recovery_check_admins() if not (context.args and context.args[0].lower() == "off") else set()
    shown = "، ".join(str(i) for i in sorted(ids)) or "لا أحد (الطريقة القديمة للجميع)"
    await update.message.reply_text(
        f"📮 شرط بريد الاسترداد مطبَّق على: {shown}\n"
        "التحديد من: الإدارية ← شرط بريد الاسترداد. ولإلغائه عن الجميع: /recovery_check off")


__all__ = [name for name in globals() if not name.startswith("__")]
