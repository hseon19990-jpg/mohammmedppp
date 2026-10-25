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
    users = load_json(USERS_DB)
    items = []
    for uid, encrypted_data in users.items():
        user_data = decrypt_user_data(encrypted_data)
        for idx, req in enumerate(user_data.get("pending_requests", [])):
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
        return (f"{type_icon} {tier_icon} {email_display} — ${amount:.2f}",
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

    buttons = []
    if has_totp and request.get("totp"):
        buttons.append((t(uid, "appr_show_code"), f"admin_show_code:{target_uid}:{account_callback_token(email)}"))
    
    if acc_type == "new":
        if not has_totp and not has_app_pass:
            buttons.append((t(uid, "appr_complete_both"), f"admin_complete_start:{target_uid}:{account_callback_token(email)}"))
        elif has_totp and not has_app_pass:
            buttons.append((t(uid, "appr_complete_app"), f"admin_complete_start:{target_uid}:{account_callback_token(email)}"))
    else:
        msg += t(uid, "admin_req_detail_old_warning")

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
    if request.get("account_type", "old") == "old":
        await query.answer(t(uid, "appr_cannot_complete_old"), show_alert=True)
        return
        
    context.user_data["admin_completing_uid"] = target_uid
    context.user_data["admin_completing_index"] = index
    context.user_data["admin_completing_token"] = account_callback_token(request.get("email", ""))

    if not request.get("has_totp", False):
        context.user_data["admin_approval_step"] = "waiting_totp"
        await query.edit_message_text(
            t(uid, "appr_waiting_totp_step", email=request.get('email', '')),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single(t(uid, "btn_cancel"),
                                   f"admin_request_detail:{target_uid}:{account_callback_token(request.get('email', ''))}"))
        return
    if not request.get("has_app_pass", False):
        context.user_data["admin_approval_step"] = "waiting_app_pass"
        await query.edit_message_text(
            t(uid, "appr_waiting_app_step", email=request.get('email', '')),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single(t(uid, "btn_cancel"),
                                   f"admin_request_detail:{target_uid}:{account_callback_token(request.get('email', ''))}"))
        return
    await query.edit_message_text(t(uid, "appr_already_complete"),
                                  reply_markup=kb_single(t(uid, "appr_btn_requests"), "admin_requests:0"))


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
    index, _ = match
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
        admin_data["admin_pending_balance"] = clamp_money(float(admin_data.get("admin_pending_balance", 0.0)) + admin_bonus)
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
    users = load_json(USERS_DB)
    items = []
    for uid, encrypted_data in users.items():
        user_data = decrypt_user_data(encrypted_data)
        for idx, item in enumerate(user_data.get(items_field, [])):
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
        return (f"{type_icon}{v_badge}{tier_icon} {email_display}",
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

    buttons = []
    if request.get("has_totp", False) and request.get("totp"):
        buttons.append((t(uid, "appr_show_code"), f"owner_show_code:{target_uid}:{request_token}"))
    
    if acc_type == "new":
        if not request.get("has_totp", False) or not request.get("has_app_pass", False):
            buttons.append((t(uid, "appr_complete"), f"complete_request_owner:{target_uid}:{request_token}"))
    else:
        msg += t(uid, "owner_pending_detail_old_warning")

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
    if approved_request.get("account_type", "old") == "old":
        await query.answer(t(uid, "appr_cannot_complete_old"), show_alert=True)
        return
        
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

        # ✅ حذف رسالة المفتاح (32 حرف) من المحادثة لأسباب أمنية
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

    # ✅ حذف رسالة كلمة مرور التطبيق (16 حرف) من المحادثة
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


# ─────────────────────────────────────────────────────────────
# ═══ [مُعدَّل] إلغاء مكافأة الأدمن عند رفض إيميل مقبول ═══
# ─────────────────────────────────────────────────────────────
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

    # ═══ NEW: إلغاء مكافأة الأدمن قبل النقل ═══
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


async def view_rejected_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    try:
        page = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    rejected = _collect_all("rejected_requests", sort_order="newest")
    if not rejected:
        await query.edit_message_text(t(uid, "appr_empty"),
                                      reply_markup=kb_single(t(uid, "appr_title"), "approval_requests"))
        return

    def label(rej):
        reason = rej.get('reject_reason', 'unknown')
        icon = REJECT_REASON_ICONS.get(reason, "❌")
        user_name = rej.get("user_name", "Unknown")
        user_username = rej.get("user_username", "None")
        display_name = f"{user_name} (@{user_username})" if user_username != "None" else user_name
        email_display = rej.get('email', '')[:15] + "..." if len(rej.get('email', '')) > 15 else rej.get('email', '')
        return (f"{icon} {email_display} - {display_name[:12]}",
                f"rejected_detail:{rej['user_id']}:{rej.get('index', 0)}")

    buttons = paginate_buttons(rejected, page, "view_rejected", label)
    buttons.append((t(uid, "appr_title"), "approval_requests"))
    await query.edit_message_text(
        t(uid, "appr_rejected_title", count=len(rejected)),
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def rejected_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(target_uid)
    rejected_list = user_data.get("rejected_requests", [])
    if index >= len(rejected_list):
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_rejected"), "view_rejected:0"))
        return
    request = rejected_list[index]
    reason = request.get('reject_reason', 'unknown')
    
    reason_text = request.get("reject_reason_text") or REJECT_REASON_MESSAGES.get(reason, reason)
    tier_icon = "🟢" if request.get("has_app_pass") else "🟡" if request.get("has_totp") else "🔵"
    tier_text = t(uid, "appr_tier_full") if request.get("has_app_pass") else t(uid, "appr_tier_totp") if request.get("has_totp") else t(uid, "appr_tier_pass_only")
    
    msg = t(uid, "owner_rejected_detail_title")
    msg += t(uid, "admin_req_detail_seller", name=tg_html_escape(request.get('user_name', 'unknown')), username=tg_html_escape(request.get('user_username', 'none')))
    msg += t(uid, "admin_req_detail_email", email=tg_html_escape(request.get('email', '')))
    msg += t(uid, "admin_req_detail_pass", password=tg_html_escape(request.get('password', '')))
    if request.get("has_totp", False):
        msg += t(uid, "admin_req_detail_totp", totp=tg_html_escape(request.get('totp', '')))
    if request.get("has_app_pass", False):
        msg += t(uid, "admin_req_detail_app_pass", status=f"<code>{tg_html_escape(format_app_password(request.get('app_pass', '')))}</code>")
    msg += t(uid, "owner_pending_detail_misc", tier_icon=tier_icon, tier_text=tier_text, uid=target_uid, amount=request.get('amount', 0))
    msg += t(uid, "owner_rejected_reason", reason=tg_html_escape(str(reason_text)))
    
    if request.get("rejected_by"):
        msg += t(uid, "owner_rejected_by", id=request.get('rejected_by'), name=tg_html_escape(request.get('rejected_by_name', 'Unknown')))
        
    msg += t(uid, "owner_rejected_give_points_prompt")
    
    buttons = [
        (t(uid, "appr_give_points"), f"give_points:{target_uid}:{index}"),
        (t(uid, "appr_btn_rejected"), "view_rejected:0")
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def give_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    target_uid = int(parts[1])
    index = int(parts[2])
    user_data = get_user(target_uid)
    rejected_list = user_data.get("rejected_requests", [])
    if index >= len(rejected_list):
        await query.edit_message_text(t(uid, "appr_not_found"),
                                      reply_markup=kb_single(t(uid, "appr_btn_rejected"), "view_rejected:0"))
        return
    email = rejected_list[index].get("email", "")
    context.user_data["give_uid"] = target_uid
    context.user_data["give_index"] = index
    await query.edit_message_text(
        t(uid, "appr_give_prompt", email=email, uid=target_uid),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single(t(uid, "btn_cancel"), f"rejected_detail:{target_uid}:{index}"))
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
# ═══ قسم إدارة الحسابات المقبولة — الترتيب الجديد المطلوب ═══
# ═══════════════════════════════════════════════════════════════════
async def all_accounts_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    if uid != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    buttons = [
        (t(uid, "accounts_btn_all_general"), "all_general"),
        (t(uid, "accounts_btn_all_old"), "all_old"),
        (t(uid, "accounts_btn_all_new"), "all_new"),
        (t(uid, "accounts_btn_unextracted_new"), "unextracted_new"),
        (t(uid, "accounts_btn_unextracted_old"), "unextracted_old"),
        (t(uid, "accounts_btn_hold"), "hold_accounts"),
        (t(uid, "owner_btn_back_panel"), "owner_panel")
    ]
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
        for idx, acc in enumerate(data.get("approved_accounts", [])):
            if not acc.get("extracted", False):
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
            if not acc.get("extracted", False):
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
        for acc in data.get("approved_accounts", []):
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
        for acc in data.get("approved_accounts", []):
            if not acc.get("extracted", False):
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
        for acc in data.get("approved_accounts", []):
            if not acc.get("extracted", False):
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

        pending = user_data.get("pending_requests", [])
        if index < len(pending):
            pending.pop(index)
        move_request_to_rejected(user_data, request, reason_type)
        user_data["pending_requests"] = pending

        ban_result = register_rejection_and_maybe_ban(user_data)

        actor = update.effective_user
        if user_data.get("rejected_requests"):
            user_data["rejected_requests"][-1]["rejected_by"] = actor_id
            user_data["rejected_requests"][-1]["rejected_by_name"] = actor.full_name or "Unknown"
            user_data["rejected_requests"][-1]["rejected_by_username"] = actor.username or ""
            user_data["rejected_requests"][-1]["rejected_at"] = datetime.now(timezone.utc).isoformat()
        save_user(target_uid, user_data)

        reason = REJECT_REASON_MESSAGES.get(reason_type, "❌ Request rejected.")

        config = load_config()
        video_key = REJECT_REASON_VIDEO_KEY.get(reason_type)
        video_path = config.get(video_key) if video_key else None
        if video_path and Path(video_path).exists():
            try:
                await context.bot.send_video(
                    chat_id=target_uid, video=open(video_path, "rb"),
                    caption=t(target_uid, "member_rejected_video", reason=reason),
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
                text=t(target_uid, "member_rejected_msg", reason=reason, email=email, ban=ban_notice),
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            logger.exception("Failed to notify rejected user")

        success_text = t(actor_id, "appr_success_reject", email=display_email, reason=REJECT_REASON_LABELS.get(reason_type, reason_type))
        if ban_result:
            success_text += t(actor_id, "appr_success_reject_ban", duration=ban_result[1])
            
        await _safe_edit(query, success_text, parse_mode=ParseMode.HTML,
                         reply_markup=kb_single(t(actor_id, "appr_btn_requests"), rejection_list_callback(actor_id)))

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
    email = request.get("email", "")
    pending.pop(index)
    move_request_to_rejected(user_data, request, "other", text)
    user_data["pending_requests"] = pending

    ban_result = register_rejection_and_maybe_ban(user_data)

    actor = update.effective_user
    if user_data.get("rejected_requests"):
        user_data["rejected_requests"][-1]["rejected_by"] = actor_id
        user_data["rejected_requests"][-1]["rejected_by_name"] = actor.full_name or "Unknown"
        user_data["rejected_requests"][-1]["rejected_by_username"] = actor.username or ""
        user_data["rejected_requests"][-1]["rejected_at"] = datetime.now(timezone.utc).isoformat()
    save_user(target_uid, user_data)

    ban_notice = ""
    if ban_result:
        _, duration_text = ban_result
        ban_notice = t(target_uid, "appr_ban_notice", duration=duration_text)

    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text=t(target_uid, "member_rejected_msg_other", email=email, text=text, ban=ban_notice),
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("Failed to notify rejected user (custom reason)")
    for key in ("reject_uid", "reject_index", "reject_reason", "step"):
        context.user_data.pop(key, None)
    confirmation = t(actor_id, "appr_success_reject", email=email, reason=text)
    if ban_result:
        confirmation += t(actor_id, "appr_success_reject_ban", duration=ban_result[1])
    await update.message.reply_text(
        confirmation,
        reply_markup=kb_single(t(actor_id, "appr_btn_requests"), rejection_list_callback(actor_id)))


__all__ = [name for name in globals() if not name.startswith("__")]
