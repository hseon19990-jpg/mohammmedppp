"""Account-review, approval, and rejection workflows."""

from telegram_bot.core import *

# ==================== VIDEOS ====================
async def videos_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    video_types = {
        "general": "📖 شرح عام للبوت",
        "email": "📹 فيديو إنشاء إيميل",
        "password": "📹 فيديو تغيير باسورد",
        "totp": "📹 فيديو إضافة 2FA",
        "app_pass": "📹 فيديو كلمة مرور التطبيق",
        "leave": "📹 فيديو المغادرة",
        "phone": "📱 فيديو يحتاج رقم هاتف",
    }
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
    from telegram_bot.members import main_menu
    if not await maintenance_access_allowed(update):
        return
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
    if not is_admin(query.from_user.id):
        await query.answer("🚫 أدمن فقط.", show_alert=True)
        return
    try:
        page = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    items = _collect_admin_requests()
    if not items:
        await query.edit_message_text("📭 لا توجد طلبات معلقة حالياً.",
                                      reply_markup=kb_single("🔙 إعدادات الأدمن", "admin_settings"))
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
    buttons.append(("🔙 إعدادات الأدمن", "admin_settings"))
    await query.edit_message_text(
        f"📋 *الطلبات المعلقة ({len(items)})*\n"
        f"📝 قديم | 🆕 جديد\n"
        f"🟢 مكتمل | 🟡 مع 2FA | 🔵 باسورد فقط\n\n"
        f"يمكنك إكمال طلبات (الجديد) غير المكتملة أو رفض أي طلب تريد رفضه.\n\n"
        f"اختر الطلب:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def admin_request_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫 أدمن فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    token = parts[2]
    user_data = get_user(uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text("⚠️ الطلب غير موجود أو تمت معالجته.",
                                      reply_markup=kb_single("🔙 الطلبات", "admin_requests:0"))
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

    type_text = "🆕 جديد" if acc_type == "new" else "📝 قديم"
    msg = f"📋 <b>تفاصيل الطلب ({type_text})</b>\n\n"
    msg += f"👤 <b>البائع:</b> {tg_html_escape(request.get('user_name', 'غير معروف'))}\n"
    msg += f"🆔 <b>اليوزر:</b> @{tg_html_escape(request.get('user_username', 'لا يوجد'))}\n"
    msg += f"📧 <b>الإيميل:</b> <code>{tg_html_escape(email)}</code>\n"
    msg += f"🔑 <b>الباسورد:</b> <code>{tg_html_escape(request.get('password', ''))}</code>\n"
    if has_totp:
        msg += f"🔐 <b>رمز المصادقة:</b> <code>{tg_html_escape(request.get('totp', ''))}</code>\n"
    else:
        msg += "🔐 <b>رمز المصادقة:</b> ❌ غير مرسل\n"
    msg += f"🗝 <b>كلمة مرور التطبيق:</b> {'✅' if has_app_pass else '❌ غير مرسل'}\n"
    msg += f"\n💰 <b>المبلغ الأصلي:</b> ${original_amount:.2f}\n"
    msg += f"💰 <b>السعر الكامل:</b> ${full_price:.2f}\n"
    msg += f"💵 <b>مكافأتك عند الإكمال:</b> ${admin_bonus:.2f}\n"

    buttons = []
    if has_totp and request.get("totp"):
        buttons.append(("🔢 عرض الكود", f"admin_show_code:{uid}:{account_callback_token(email)}"))
    
    if acc_type == "new":
        if not has_totp and not has_app_pass:
            buttons.append(("📝 إكمال الطلب (إضافة 2FA + App Pass)",
                            f"admin_complete_start:{uid}:{account_callback_token(email)}"))
        elif has_totp and not has_app_pass:
            buttons.append(("📝 إكمال الطلب (إضافة App Pass)",
                            f"admin_complete_start:{uid}:{account_callback_token(email)}"))
    else:
        msg += "\n⚠️ <i>هذا إيميل قديم، لا يمكن إكماله من الإدارة. يجب أن يكمل العضو الـ 4 خطوات بنفسه.</i>\n"

    buttons.append(("❌ رفض الطلب", f"reject_request:{uid}:{account_callback_token(email)}"))
    buttons.append(("🔙 الطلبات", "admin_requests:0"))
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def admin_show_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫 أدمن فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer("⚠️ بيانات غير صحيحة.", show_alert=True)
        return
    try:
        uid = int(parts[1])
        token = parts[2]
    except (ValueError, IndexError):
        await query.answer("⚠️ بيانات غير صحيحة.", show_alert=True)
        return

    user_data = get_user(uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    index, request = match
    email = request.get("email", "")
    totp_secret = request.get("totp", "")

    if not totp_secret:
        await query.answer("⚠️ لا يوجد رمز مصادقة لهذا الطلب.", show_alert=True)
        return

    ok, code, err, seconds = generate_totp_code_info(totp_secret)
    if not ok:
        await query.answer(f"⚠️ {err}", show_alert=True)
        return

    await query.answer("✅ تم إرسال الكود إليك.", show_alert=False)
    try:
        await send_totp_code_message(
            context=context,
            chat_id=query.from_user.id,
            totp_secret=totp_secret,
            email=email,
            title="🔢 كود المصادقة الحالي (أدمن)",
            back_callback=f"admin_show_code:{uid}:{account_callback_token(email)}",
            auto_refresh=True,
        )
    except Exception as exc:
        logger.exception("Failed to send TOTP code to admin")
        try:
            await query.edit_message_text(
                f"🔢 *الكود الحالي:* `{code}`\n\n⏰ صالح لمدة *{seconds}* ثانية",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_vertical([
                    ("🔄 كود جديد", f"admin_show_code:{uid}:{account_callback_token(email)}"),
                    ("🔙 تفاصيل الطلب", f"admin_request_detail:{uid}:{account_callback_token(email)}"),
                ]))
        except Exception:
            await query.answer(f"🔢 الكود: {code} | ⏰ {seconds}s", show_alert=True)


async def admin_complete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫 أدمن فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    token = parts[2]
    user_data = get_user(uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text("⚠️ الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات", "admin_requests:0"))
        return
    index, request = match
    if request.get("account_type", "old") == "old":
        await query.answer("🚫 لا يمكن إكمال الإيميلات القديمة من الإدارة.", show_alert=True)
        return
        
    context.user_data["admin_completing_uid"] = uid
    context.user_data["admin_completing_index"] = index
    context.user_data["admin_completing_token"] = account_callback_token(request.get("email", ""))

    if not request.get("has_totp", False):
        context.user_data["admin_approval_step"] = "waiting_totp"
        await query.edit_message_text(
            f"🔐 *الخطوة 1/2: رمز المصادقة*\n\n"
            f"📧 `{request.get('email', '')}`\n\n"
            f"📌 أرسل رمز المصادقة (32 حرفاً Base32):\n\n"
            f"_أو 'إلغاء'_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 إلغاء",
                                   f"admin_request_detail:{uid}:{account_callback_token(request.get('email', ''))}"))
        return
    if not request.get("has_app_pass", False):
        context.user_data["admin_approval_step"] = "waiting_app_pass"
        await query.edit_message_text(
            f"🗝 *الخطوة 2/2: كلمة مرور التطبيق*\n\n"
            f"📧 `{request.get('email', '')}`\n\n"
            f"📌 أرسل كلمة مرور التطبيق (16 حرفاً):\n\n"
            f"_أو 'إلغاء'_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 إلغاء",
                                   f"admin_request_detail:{uid}:{account_callback_token(request.get('email', ''))}"))
        return
    await query.edit_message_text("⚠️ الطلب مكتمل بالفعل.",
                                  reply_markup=kb_single("🔙 الطلبات", "admin_requests:0"))


async def handle_admin_totp_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.casefold() in {"إلغاء", "الغاء", "cancel"}:
        for k in ("admin_completing_uid", "admin_completing_index",
                  "admin_completing_token", "admin_approval_step"):
            context.user_data.pop(k, None)
        await update.message.reply_text("❌ تم الإلغاء.",
                                        reply_markup=kb_single("🔙 الطلبات", "admin_requests:0"))
        return
    uid = context.user_data.get("admin_completing_uid")
    index = context.user_data.get("admin_completing_index")
    token = context.user_data.get("admin_completing_token")
    if uid is None or (index is None and token is None):
        await update.message.reply_text("⚠️ حدث خطأ.")
        return
    cleaned = text.replace(" ", "").upper()
    if len(cleaned) != 32 or not re.match(r'^[A-Z2-7]{32}$', cleaned):
        await update.message.reply_text("⚠️ مفتاح المصادقة يجب أن يكون 32 حرفاً Base32.")
        return
    try:
        pyotp.TOTP(cleaned).now()
    except Exception:
        await update.message.reply_text("⚠️ مفتاح TOTP غير صالح.")
        return

    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if token:
        match = find_pending_request(user_data, token)
        if match is not None:
            index, _ = match
    if index is None or index >= len(pending):
        await update.message.reply_text("⚠️ الطلب غير موجود.")
        return
    pending[index]["totp"] = cleaned
    pending[index]["has_totp"] = True
    user_data["pending_requests"] = pending
    save_user(uid, user_data)

    if not pending[index].get("has_app_pass", False):
        context.user_data["admin_approval_step"] = "waiting_app_pass"
        try:
            await update.message.delete()
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ تم حفظ رمز المصادقة.\n\n"
            f"🗝 *الخطوة 2/2: كلمة مرور التطبيق*\n\n"
            f"📌 أرسل كلمة مرور التطبيق (16 حرفاً):\n\n_أو 'إلغاء'_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 إلغاء",
                                   f"admin_request_detail:{uid}:{account_callback_token(pending[index].get('email', ''))}"))
    else:
        await prompt_admin_acceptance_choice(update, context, uid, index)


async def handle_admin_app_pass_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.casefold() in {"إلغاء", "الغاء", "cancel"}:
        for k in ("admin_completing_uid", "admin_completing_index",
                  "admin_completing_token", "admin_approval_step"):
            context.user_data.pop(k, None)
        await update.message.reply_text("❌ تم الإلغاء.",
                                        reply_markup=kb_single("🔙 الطلبات", "admin_requests:0"))
        return
    uid = context.user_data.get("admin_completing_uid")
    index = context.user_data.get("admin_completing_index")
    token = context.user_data.get("admin_completing_token")
    if uid is None or (index is None and token is None):
        await update.message.reply_text("⚠️ حدث خطأ.")
        return
    cleaned = text.replace(" ", "")
    if len(cleaned) != 16 or not re.match(r'^[A-Za-z0-9]{16}$', cleaned):
        await update.message.reply_text("⚠️ كلمة مرور التطبيق يجب أن تكون 16 حرفاً.")
        return
    if has_active_app_password(cleaned):
        await update.message.reply_text("⚠️ كلمة المرور هذه مستخدمة مسبقاً!")
        return

    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if token:
        match = find_pending_request(user_data, token)
        if match is not None:
            index, _ = match
    if index is None or index >= len(pending):
        await update.message.reply_text("⚠️ الطلب غير موجود.")
        return
    pending[index]["app_pass"] = cleaned
    pending[index]["has_app_pass"] = True
    user_data["pending_requests"] = pending
    save_user(uid, user_data)
    try:
        await update.message.delete()
    except Exception:
        pass
    await prompt_admin_acceptance_choice(update, context, uid, index)


async def prompt_admin_acceptance_choice(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                          uid: int, index: int):
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index is None or index >= len(pending):
        await update.effective_message.reply_text("⚠️ الطلب غير موجود.")
        return

    request = pending[index]
    if not all(request.get(field) for field in ("email", "password", "totp", "app_pass")):
        await update.effective_message.reply_text("⚠️ الطلب غير مكتمل.")
        return

    context.user_data["admin_completing_uid"] = uid
    context.user_data["admin_completing_index"] = index
    token = account_callback_token(request.get("email", ""))
    context.user_data["admin_completing_token"] = token
    context.user_data["admin_approval_step"] = "waiting_acceptance_choice"
    await update.effective_message.reply_text(
        f"✅ تم استلام جميع المعلومات للحساب:\n"
        f"📧 `{request.get('email', '')}`\n\n"
        f"سيتم الآن التحقق التلقائي من IMAP.\n"
        f"إذا نجح → يُقبل الحساب.\n"
        f"إذا فشل → يُرفض تلقائياً.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ متابعة التحقق التلقائي",
                callback_data=f"admin_accept_auto:{uid}:{token}")],
            [InlineKeyboardButton(
                "❌ إلغاء",
                callback_data=f"admin_completion_cancel:{uid}:{token}")],
        ]))


async def admin_completion_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫 أدمن فقط.", show_alert=True)
        return
    for key in ("admin_completing_uid", "admin_completing_index",
                "admin_completing_token", "admin_approval_step"):
        context.user_data.pop(key, None)
    await admin_request_detail(update, context)


async def admin_accept_completed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫 أدمن فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    token = parts[2]
    match = find_pending_request(get_user(uid), token)
    if match is None:
        await query.edit_message_text(
            "⚠️ الطلب غير موجود أو تمت معالجته.",
            reply_markup=kb_single("🔙 الطلبات", "admin_requests:0"))
        return
    index, _ = match
    await admin_verify_and_store(update, context, uid, index, hold_seconds=LEAVE_HOLD_SECONDS)


async def admin_verify_and_store(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                  uid: int, index: int,
                                  hold_seconds: int = LEAVE_HOLD_SECONDS):
    from telegram_bot.members import schedule_leave_check
    admin_id = update.effective_user.id
    reply_target = update.effective_message
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if index >= len(pending):
        await reply_target.reply_text("⚠️ الطلب غير موجود.")
        return
    request = pending[index]
    email = request.get("email", "")
    password = request.get("password", "")
    totp = request.get("totp", "")
    app_pass = request.get("app_pass", "")
    acc_type = request.get("account_type", "old")

    if not email or not password or not totp or not app_pass:
        await reply_target.reply_text("⚠️ الطلب غير مكتمل.")
        return

    allowed, wait = imap_rate_ok(email)
    if not allowed:
        await reply_target.reply_text(
            f"⏳ انتظر {wait} ثانية قبل إعادة المحاولة لنفس الإيميل.",
            reply_markup=kb_single("🔙 الطلبات", "admin_requests:0"))
        return
    imap_rate_mark(email)

    progress_msg = await reply_target.reply_text(
        f"🔍 *جاري التحقق التلقائي من الحساب...*\n\n"
        f"📧 `{email}`\n\n"
        f"_يتم الاتصال بخادم البريد..._",
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
                                 f"فشل التحقق التلقائي (أدمن): {verify_result['message'][:150]}")
        user_data["pending_requests"] = pending
        if user_data.get("rejected_requests"):
            user_data["rejected_requests"][-1]["rejected_by"] = admin_id
            user_data["rejected_requests"][-1]["rejected_by_name"] = "نظام التحقق التلقائي"
            user_data["rejected_requests"][-1]["rejected_at"] = datetime.now(timezone.utc).isoformat()
        save_user(uid, user_data)

        await reply_target.reply_text(
            f"❌ <b>فشل التحقق التلقائي — تم رفض الحساب</b>\n\n"
            f"📧 <code>{tg_html_escape(email)}</code>\n\n"
            f"📝 <b>السبب:</b>\n<i>{tg_html_escape(verify_result['message'])}</i>\n\n"
            f"⚠️ <b>لم يتم قبول الحساب.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 الطلبات", "admin_requests:0"))

        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(f"❌ <b>تم رفض طلبك</b>\n\n"
                      f"📧 <code>{tg_html_escape(email)}</code>\n\n"
                      f"📝 السبب: فشل التحقق التلقائي من IMAP.\n\n"
                      f"💡 تأكد من صحة كلمة مرور التطبيق أو أعد المحاولة ببيانات صحيحة."),
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
        admin_name_str = admin_user.full_name or "غير معروف"
        admin_username = admin_user.username or "لا يوجد"
    except Exception:
        admin_name_str = "غير معروف"
        admin_username = "لا يوجد"

    approval_time = datetime.now(timezone.utc)
    account_record = {
        "email": email,
        "password": password,
        "totp": totp,
        "app_pass": app_pass,
        "amount": original_amount,
        "admin_bonus": admin_bonus,
        "completed_by_admin": admin_id,
        "completed_by_admin_name": admin_name_str,
        "completed_by_admin_username": admin_username,
        "completed_at": approval_time.isoformat(),
        "timestamp": approval_time.isoformat(),
        "approval_time": approval_time.isoformat(),
        "release_at": (approval_time + timedelta(seconds=hold_seconds)).isoformat(),
        "hold_seconds": hold_seconds,
        "acceptance_mode": "automatic",
        "admin_bonus_status": (
            "pending" if admin_id != OWNER_ID and admin_bonus > 0 else "not_applicable"
        ),
        "extracted": False,
        "has_totp": True,
        "has_app_pass": True,
        "user_name": request.get("user_name", "غير معروف"),
        "user_username": request.get("user_username", "لا يوجد"),
        "approved_with_leave": True,
        "leave_confirmed": False,
        "auto_verified": True,
        "account_type": acc_type,
        "verification": {
            "level": verify_result["level"],
            "badge": verify_result["badge"],
            "message": verify_result["message"],
            "imap_ok": verify_result["imap_ok"],
            "totp_ok": verify_result["totp_ok"],
            "verified_at": approval_time.isoformat(),
            "verified_by": "admin_completion",
            "admin_id": admin_id,
        },
    }

    user_data.setdefault("approved_accounts", []).append(account_record)
    user_data["pending_balance"] = clamp_money(
        float(user_data.get("pending_balance", 0.0)) - original_amount)
    user_data["hold_balance"] = clamp_money(
        float(user_data.get("hold_balance", 0.0)) + original_amount)
    user_data["total_credited_balance"] = clamp_money(
        float(user_data.get("total_credited_balance", 0.0) or 0.0) + original_amount)
    user_data["total_approved_emails"] = int(user_data.get("total_approved_emails", 0)) + 1
    pending.pop(index)
    user_data["pending_requests"] = pending
    hold_hours = hold_seconds // 3600
    add_transaction(user_data, "hold", original_amount,
                    f"أكمله الأدمن {admin_id} - معلق {hold_hours} ساعة", email)
    register_approval_reset(user_data)
    save_user(uid, user_data)

    try:
        await check_contest_award(context, uid)
    except Exception:
        logger.exception("Contest check failed")

    if admin_bonus > 0 and admin_id != OWNER_ID:
        admin_data = get_user(admin_id)
        admin_data["admin_pending_balance"] = clamp_money(
            float(admin_data.get("admin_pending_balance", 0.0)) + admin_bonus)
        add_transaction(admin_data, "admin_bonus_pending", admin_bonus,
                        f"مكافأة معلّقة حتى إعادة فحص {email}", email)
        save_user(admin_id, admin_data)
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(f"💵 *مكافأة أدمن معلّقة!*\n\n📧 `{email}`\n"
                      f"💰 ${admin_bonus:.2f}\n"
                      f"⏳ تصل بعد نجاح الفحص الثاني."),
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    await schedule_leave_check(context, uid, email, account_record["release_at"])

    referred_by = user_data.get("referred_by")
    if referred_by:
        config_ref = load_config()
        referral_bonus = float(config_ref.get("referral_bonus", 0.0))
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
                            f"مكافأة إحالة للمستخدم {uid}", email)
            save_user(referred_by, referrer_data)
            try:
                await context.bot.send_message(
                    chat_id=referred_by,
                    text=f"🎉 *مبروك!*\nحصلت على مكافأة إحالة بقيمة ${referral_bonus:.2f}",
                    parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
        try:
            await context.bot.send_message(
                chat_id=referred_by,
                text=f"📢 *إشعار إحالة*\n\nالمستخدم `{uid}` تم قبول إيميله `{email}`.",
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(f"🛠 *إكمال أدمن*\n\n"
                  f"👤 الأدمن: `{admin_id}` (@{admin_username})\n"
                  f"🆔 المستخدم: `{uid}`\n"
                  f"📧 `{email}`\n"
                  f"💰 مكافأة الأدمن: `${admin_bonus:.2f}`\n"
                  f"⏰ سيُعاد فحصه بعد {hold_hours} ساعة."),
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

    try:
        await progress_msg.delete()
    except Exception:
        pass

    try:
        await context.bot.send_message(
            chat_id=uid,
            text=(f"✅ *تم التحقق من الحساب بنجاح!*\n\n"
                  f"📧 `{email}`\n"
                  f"💰 تم إضافة *${original_amount:.2f}* إلى رصيدك المعلق\n\n"
                  f"⏰ سيتم فحص الحساب بعد *{hold_hours} ساعة* ويتم تسليمك النقاط."),
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

    for k in ("admin_completing_uid", "admin_completing_index", "admin_approval_step"):
        context.user_data.pop(k, None)

    await reply_target.reply_text(
        f"✅ *تم اعتماد الحساب بعد التحقق التلقائي!*\n\n"
        f"📧 `{email}`\n"
        f"💰 للعضو: `${original_amount:.2f}` (معلق {hold_hours} ساعة)\n"
        f"💵 مكافأتك: `${admin_bonus:.2f}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 الطلبات", "admin_requests:0"))


# ==================== TIER PRICES ====================
async def set_tier_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    prices_old = get_tier_prices("old")
    prices_new = get_tier_prices("new")
    buttons = [
        ("💲 قديم: مستوى 1", "set_tier:old:1"),
        ("💲 قديم: مستوى 2", "set_tier:old:2"),
        ("💲 قديم: مستوى 3", "set_tier:old:3"),
        ("💲 جديد: مستوى 1", "set_tier:new:1"),
        ("💲 جديد: مستوى 2", "set_tier:new:2"),
        ("💲 جديد: مستوى 3", "set_tier:new:3"),
        ("🔙 إعدادات المالك", "owner_panel")
    ]
    msg = (f"💰 *إعدادات الأسعار*\n\n"
           f"📝 *القديم:*\n"
           f"1. إيميل + باسورد: ${prices_old['tier_1']:.2f}\n"
           f"2. + 2FA: ${prices_old['tier_2']:.2f}\n"
           f"3. + App Pass: ${prices_old['tier_3']:.2f}\n\n"
           f"🆕 *الجديد:*\n"
           f"1. إيميل + باسورد: ${prices_new['tier_1']:.2f}\n"
           f"2. + 2FA: ${prices_new['tier_2']:.2f}\n"
           f"3. + App Pass: ${prices_new['tier_3']:.2f}\n\n"
           f"اختر المستوى لتعديله:")
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def set_tier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    acc_type = parts[1]
    tier = parts[2]
    context.user_data["setting_tier"] = tier
    context.user_data["setting_type"] = acc_type
    type_name = "القديم" if acc_type == "old" else "الجديد"
    await query.edit_message_text(f"💰 *تعديل سعر {type_name} - المستوى {tier}*\n\nأرسل السعر الجديد:",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", "set_tier_prices"))
    context.user_data["mode"] = "set_tier_price"


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
    pending = _collect_all("pending_requests", sort_order="oldest")
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
        acc_type = req.get("account_type", "old")
        type_icon = "🆕" if acc_type == "new" else "📝"
        return (f"{type_icon}{v_badge}{tier_icon} {email_display}",
                f"pending_detail:{req['user_id']}:{account_callback_token(email)}")

    buttons = paginate_buttons(pending, page, "view_pending", label)
    buttons.append(("🔙 الطلبات", "approval_requests"))
    await query.edit_message_text(
        f"⏳ *الطلبات المنتظرة ({len(pending)}) — الأقدم أولاً*\n"
        f"📝 قديم | 🆕 جديد\n"
        f"اختر الإيميل:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def pending_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    token = parts[2]
    user_data = get_user(uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text("⚠️ هذا الطلب غير موجود أو تمت معالجته.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))
        return
    index, request = match
    request_token = account_callback_token(request.get("email", ""))
    email = request.get("email", "")
    tier_icon = "🟢" if request.get("has_app_pass") else "🟡" if request.get("has_totp") else "🔵"
    tier_text = "مكتمل" if request.get("has_app_pass") else "مع رمز المصادقة" if request.get("has_totp") else "باسورد فقط"
    acc_type = request.get("account_type", "old")
    type_text = "🆕 جديد" if acc_type == "new" else "📝 قديم"
    
    msg = f"📋 <b>تفاصيل الطلب ({type_text})</b>\n\n"
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

    buttons = []
    if request.get("has_totp", False) and request.get("totp"):
        buttons.append(("🔢 عرض الكود", f"owner_show_code:{uid}:{request_token}"))
    
    if acc_type == "new":
        if not request.get("has_totp", False) or not request.get("has_app_pass", False):
            buttons.append(("📝 إكمال المعلومات قبل القبول",
                            f"complete_request_owner:{uid}:{request_token}"))
    else:
        msg += "\n⚠️ <i>هذا إيميل قديم، لا يمكن إكماله من الإدارة.</i>\n"

    buttons.append(("✅ قبول فوري (مع تحقق)",
                    f"approve_request:{uid}:{request_token}"))
    if request.get("password") or request.get("app_pass"):
        verify_label = "🔍 تحقق تلقائي"
        buttons.append((verify_label, f"auto_verify:{uid}:{request_token}"))
    buttons.append(("❌ رفض", f"reject_request:{uid}:{request_token}"))
    buttons.append(("🔙 الطلبات المنتظرة", "view_pending:0"))
    await query.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def auto_verify_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    token = parts[2]
    user_data = get_user(uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text("⚠️ هذا الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))
        return
    index, request = match
    email = request.get("email", "")
    password = request.get("password", "")
    app_pass = request.get("app_pass", "")
    totp_secret = request.get("totp", "")

    if not password and not app_pass:
        await query.answer("⚠️ لا توجد بيانات دخول للتحقق منها.", show_alert=True)
        return

    allowed, wait = imap_rate_ok(email)
    if not allowed:
        await query.answer(f"⏳ انتظر {wait} ثانية قبل إعادة المحاولة لنفس الإيميل.", show_alert=True)
        return
    imap_rate_mark(email)

    await query.answer("🔍 جاري التحقق…", show_alert=False)

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
    save_user(uid, user_data)

    if result["level"] == "verified":
        title = "🟢 <b>نجح التحقق التلقائي</b>"
    elif result["level"] == "partial":
        title = "🟡 <b>تحقق جزئي</b>"
    elif result["level"] == "unknown":
        title = "⚪ <b>تعذّر التحقق</b>"
    else:
        title = "🔴 <b>فشل التحقق التلقائي</b>"

    result_msg = f"{title}\n\n📧 <b>الإيميل:</b> <code>{tg_html_escape(email)}</code>\n\n"
    result_msg += f"📬 <b>IMAP:</b> {'✅ نجح' if result['imap_ok'] else '❌ فشل'}\n"
    if totp_secret:
        result_msg += f"🔐 <b>مفتاح 2FA:</b> {'✅ صالح' if result['totp_ok'] else '❌ غير صالح'}\n"
    result_msg += f"\n📝 <b>النتيجة:</b> {tg_html_escape(result['message'])}\n"
    result_msg += f"\n<i>📌 هذا مجرد تقرير — لم يتم قبول أو رفض الطلب.</i>"

    request_token = account_callback_token(email)
    buttons = [("🔍 تحقق مرة أخرى", f"auto_verify:{uid}:{request_token}"),
               ("✅ قبول فوري", f"approve_request:{uid}:{request_token}"),
               ("❌ رفض", f"reject_request:{uid}:{request_token}"),
               ("🔙 تفاصيل الطلب", f"pending_detail:{uid}:{request_token}")]
    await query.edit_message_text(result_msg, parse_mode=ParseMode.HTML,
                                  reply_markup=kb_vertical(buttons))


async def approve_request_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    token = parts[2]
    user_data = get_user(uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text("⚠️ الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))
        return
    index, _ = match
    await admin_verify_and_store(update, context, uid, index, hold_seconds=LEAVE_HOLD_SECONDS)


async def owner_show_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer("⚠️ بيانات غير صحيحة.", show_alert=True)
        return
    try:
        uid = int(parts[1])
        token = parts[2]
    except (ValueError, IndexError):
        await query.answer("⚠️ بيانات غير صحيحة.", show_alert=True)
        return

    user_data = get_user(uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    index, request = match
    email = request.get("email", "")
    totp_secret = request.get("totp", "")

    if not totp_secret:
        await query.answer("⚠️ لا يوجد رمز مصادقة لهذا الطلب.", show_alert=True)
        return

    ok, code, err, seconds = generate_totp_code_info(totp_secret)
    if not ok:
        await query.answer(f"⚠️ {err}", show_alert=True)
        return

    await query.answer("✅ تم إرسال الكود إليك.", show_alert=False)
    try:
        await send_totp_code_message(
            context=context,
            chat_id=query.from_user.id,
            totp_secret=totp_secret,
            email=email,
            title="🔢 كود المصادقة الحالي (مالك)",
            back_callback=f"owner_show_code:{uid}:{account_callback_token(email)}",
            auto_refresh=True,
        )
    except Exception as exc:
        logger.exception("Failed to send TOTP code to owner")
        try:
            await query.edit_message_text(
                f"🔢 *الكود الحالي:* `{code}`\n\n⏰ صالح لمدة *{seconds}* ثانية",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_vertical([
                    ("🔄 كود جديد", f"owner_show_code:{uid}:{account_callback_token(email)}"),
                    ("🔙 تفاصيل الطلب", f"pending_detail:{uid}:{account_callback_token(email)}"),
                ]))
        except Exception:
            await query.answer(f"🔢 الكود: {code} | ⏰ {seconds}s", show_alert=True)


async def complete_request_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    match = find_pending_request(get_user(int(parts[1])), parts[2])
    if match is None:
        await query.edit_message_text("⚠️ الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المنتظرة", "view_pending:0"))
        return
    uid = int(parts[1])
    index, approved_request = match
    if approved_request.get("account_type", "old") == "old":
        await query.answer("🚫 لا يمكن إكمال الإيميلات القديمة من الإدارة.", show_alert=True)
        return
        
    context.user_data["approval_uid"] = uid
    context.user_data["approval_index"] = index
    context.user_data["approval_token"] = parts[2]
    context.user_data["approval_data"] = approved_request
    context.user_data["approval_with_leave"] = False

    if not approved_request.get("has_totp", False):
        context.user_data["approval_step"] = "waiting_totp"
        await query.edit_message_text(
            f"🔐 <b>إكمال الطلب</b>\n\n📧 <code>{tg_html_escape(approved_request.get('email', ''))}</code>\n\n"
            f"📌 أرسل رمز المصادقة (32 حرفاً):\n\n<i>أو 'تخطي'</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{parts[2]}"))
        return

    if not approved_request.get("has_app_pass", False):
        context.user_data["approval_step"] = "waiting_app_pass"
        await query.edit_message_text(
            f"🗝 <b>إكمال الطلب</b>\n\n📧 <code>{tg_html_escape(approved_request.get('email', ''))}</code>\n\n"
            f"📌 أرسل كلمة مرور التطبيق (16 حرفاً):\n\n<i>أو 'تخطي'</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", f"pending_detail:{uid}:{parts[2]}"))
        return

    await query.edit_message_text("⚠️ الطلب مكتمل بالفعل.")


async def handle_approval_totp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data.get("approval_uid")
    index = context.user_data.get("approval_index")
    approved_request = context.user_data.get("approval_data")
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
                parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("ℹ️ الطلب جاهز. استخدم زر القبول من التفاصيل.")
        return
    cleaned = text.replace(" ", "").upper()
    if len(cleaned) != 32 or not re.match(r'^[A-Z2-7]{32}$', cleaned):
        await update.message.reply_text("⚠️ مفتاح المصادقة يجب أن يكون 32 حرفاً Base32.")
        return
    try:
        code = pyotp.TOTP(cleaned).now()
        approved_request["totp"] = cleaned
        approved_request["has_totp"] = True
        pending[index] = approved_request
        user_data["pending_requests"] = pending
        save_user(uid, user_data)
        context.user_data["approval_data"] = approved_request
        if not approved_request.get("has_app_pass", False):
            context.user_data["approval_step"] = "waiting_app_pass"
            await update.message.reply_text(
                f"✅ رمز المصادقة صالح!\n"
                f"🔐 *المفتاح:* `•••• •••• •••• ••••`\n"
                f"🔢 *الكود:* `{code}`\n\n"
                f"🗝 *أرسل كلمة مرور التطبيق (16 حرفاً):*\n\n_أو 'تخطي'_",
                parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("ℹ️ الطلب جاهز. استخدم زر القبول من التفاصيل.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ مفتاح 2FA غير صالح: {str(e)}")


async def handle_approval_app_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = context.user_data.get("approval_uid")
    index = context.user_data.get("approval_index")
    approved_request = context.user_data.get("approval_data")
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
    pending[index] = approved_request
    user_data["pending_requests"] = pending
    save_user(uid, user_data)
    context.user_data["approval_data"] = approved_request
    formatted = format_app_password(cleaned)
    await update.message.reply_text(
        f"✅ تم استلام كلمة مرور التطبيق.\n🗝 `{formatted}`\n\n"
        f"ℹ️ الطلب جاهز للتحقق. استخدم زر «✅ قبول فوري (مع تحقق)» من تفاصيل الطلب.")


async def view_approved_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    try:
        page = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        page = 0
    approved = _collect_all("approved_accounts", sort_order="newest")
    if not approved:
        await query.edit_message_text("📭 لا توجد طلبات مقبولة.",
                                      reply_markup=kb_single("🔙 الطلبات", "approval_requests"))
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
        user_name = acc.get("user_name", "غير معروف")
        user_username = acc.get("user_username", "لا يوجد")
        display_name = f"{user_name} (@{user_username})" if user_username != "لا يوجد" else user_name
        email_display = acc.get('email', '')[:15] + "..." if len(acc.get('email', '')) > 15 else acc.get('email', '')
        acc_type = acc.get("account_type", "old")
        type_icon = "🆕" if acc_type == "new" else "📝"
        return (f"{type_icon}{tier_icon} {email_display} - {display_name[:12]} (${acc.get('amount', 0):.2f})",
                f"approved_detail:{acc['user_id']}:{account_callback_token(acc.get('email', ''))}")

    buttons = paginate_buttons(approved, page, "view_approved", label)
    buttons.append(("🔙 الطلبات", "approval_requests"))
    await query.edit_message_text(
        f"✅ *الطلبات المقبولة ({len(approved)}) — الأحدث أولاً*\n\nاختر الإيميل:",
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
    if account.get("rejected_at_24h"):
        tier_icon = "🔴"
        tier_text = "مرفوض بعد فحص 24 ساعة"
    elif account.get("has_app_pass"):
        tier_icon = "🟢"
        tier_text = "مكتمل"
    elif account.get("has_totp"):
        tier_icon = "🟡"
        tier_text = "مع رمز المصادقة"
    else:
        tier_icon = "🔵"
        tier_text = "باسورد فقط"
        
    acc_type = account.get("account_type", "old")
    type_text = "🆕 جديد" if acc_type == "new" else "📝 قديم"
        
    msg = f"📋 *تفاصيل الحساب المقبول ({type_text})*\n\n"
    msg += f"👤 *البائع:* {account.get('user_name', 'غير معروف')}\n"
    msg += f"🆔 *اليوزر:* @{account.get('user_username', 'لا يوجد')}\n"
    msg += f"📧 *الإيميل:* `{account.get('email', '')}`\n"
    msg += f"🔑 *الباسورد:* `{account.get('password', '')}`\n"
    if account.get("has_totp", False):
        msg += f"🔐 *رمز المصادقة:* `{account.get('totp', '')}`\n"
    else:
        msg += f"🔐 *رمز المصادقة:* ❌ غير مرسل\n"
    if account.get("has_app_pass", False):
        msg += f"🗝 *كلمة مرور التطبيق:* `{format_app_password(account.get('app_pass', ''))}`\n"
    else:
        msg += f"🗝 *كلمة مرور التطبيق:* ❌ غير مرسل\n"
    msg += f"📦 *المستوى:* {tier_icon} {tier_text}\n"
    msg += f"👤 *المستخدم:* `{uid}`\n"
    msg += f"💰 *السعر:* ${account.get('amount', 0):.2f}\n"
    
    if account.get("rejected_at_24h"):
        msg += "📌 *الحالة:* ❌ مرفوض بعد الفحص الثاني\n"
    elif account.get("approved_with_leave", False) and not account.get("leave_confirmed", False):
        msg += "📌 *حالة المغادرة:* ⏳ معلق (24 ساعة)\n"
    elif account.get("approved_with_leave", False) and account.get("leave_confirmed", False):
        msg += "📌 *حالة المغادرة:* ✅ تم التحويل\n"
        
    buttons = []
    if account.get("has_totp", False) and account.get("totp", ""):
        buttons.append(("🔄 كود جديد", f"new_totp_code:{uid}:{account_token}"))
    buttons.append(("💰 خصم نقاط", f"deduct_points:{uid}:{account_token}"))
    if not account.get("rejected_at_24h"):
        buttons.insert(0, ("❌ رفض الإيميل المقبول", f"reject_approved:{uid}:{account_token}"))
    buttons.append(("🔙 الطلبات المقبولة", "view_approved:0"))
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def reject_approved_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await query.edit_message_text(
            "⚠️ هذا الحساب غير موجود.",
            reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved:0"))
        return
    _, account = account_match
    if account.get("rejected_at_24h"):
        await query.answer("⚠️ هذا الإيميل مرفوض مسبقاً.", show_alert=True)
        return
    email = account.get("email", "")
    display_email = tg_html_escape(email)
    await query.edit_message_text(
        f"⚠️ <b>رفض إيميل مقبول</b>\n\n"
        f"📧 <code>{display_email}</code>\n"
        "سيتم نقله إلى المرفوضة وإلغاء رصيده حسب حالته.\n\n"
        "اختر سبب الرفض:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            ("📧 إيميل خطأ", f"reject_approved_reason:email:{uid}:{account_callback_token(email)}"),
            ("🔑 باسورد خطأ", f"reject_approved_reason:password:{uid}:{account_callback_token(email)}"),
            ("🔐 رمز مصادقة خطأ", f"reject_approved_reason:totp:{uid}:{account_callback_token(email)}"),
            ("🗝 كلمة مرور تطبيق خطأ", f"reject_approved_reason:app_pass:{uid}:{account_callback_token(email)}"),
            ("📱 يحتاج رقم هاتف", f"reject_approved_reason:phone:{uid}:{account_callback_token(email)}"),
            ("📝 خطأ آخر (اكتب السبب)", f"reject_approved_reason:other:{uid}:{account_callback_token(email)}"),
            ("🔙 تفاصيل الحساب", f"approved_detail:{uid}:{account_callback_token(email)}"),
        ]))


async def execute_reject_approved_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    reason_type = parts[1]
    uid = int(parts[2])
    token = parts[3]
    user_data = get_user(uid)
    account_match = find_approved_account(user_data, token)
    if account_match is None:
        await query.edit_message_text("⚠️ هذا الحساب غير موجود.",
                                      reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved:0"))
        return
    index, account = account_match
    email = account.get("email", "")
    if reason_type == "other":
        context.user_data["reject_approved_uid"] = uid
        context.user_data["reject_approved_token"] = token
        context.user_data["step"] = "reject_approved_reason_text"
        await query.edit_message_text(
            f"📝 <b>اكتب سبب رفض الإيميل المقبول</b>\n\n📧 <code>{tg_html_escape(email)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", f"approved_detail:{uid}:{token}"))
        return
    await finalize_approved_rejection(update, context, uid, index, account, reason_type)


async def finalize_approved_rejection(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                       uid: int, index: int, account: dict,
                                       reason: str, reason_text: str = ""):
    user_data = get_user(uid)
    accounts = user_data.get("approved_accounts", [])
    if index >= len(accounts) or accounts[index].get("email") != account.get("email"):
        match = find_approved_account(user_data, account_callback_token(account.get("email", "")))
        if match is None:
            await update.effective_message.reply_text("⚠️ الحساب غير موجود أو تمت معالجته.")
            return
        index, account = match

    account = dict(account)
    email = account.get("email", "")
    amount = clamp_money(account.get("amount", 0.0))
    was_hold = account.get("approved_with_leave") and not account.get("leave_confirmed")
    was_released = bool(account.get("leave_confirmed")) and not account.get("rejected_at_24h")

    accounts.pop(index)
    user_data["approved_accounts"] = accounts
    move_approved_account_to_rejected(user_data, account, reason, reason_text)
    actor = update.effective_user
    if user_data.get("rejected_requests"):
        user_data["rejected_requests"][-1]["rejected_by"] = actor.id
        user_data["rejected_requests"][-1]["rejected_by_name"] = actor.full_name or "غير معروف"
        user_data["rejected_requests"][-1]["rejected_by_username"] = actor.username or ""

    if was_hold:
        user_data["hold_balance"] = clamp_money(float(user_data.get("hold_balance", 0.0)) - amount)
        add_transaction(user_data, "approved_rejection", amount,
                        "رفض حساب مقبول وإلغاء الرصيد المعلّق", email)
    elif was_released:
        user_data["balance"] = clamp_money(float(user_data.get("balance", 0.0)) - amount)
        user_data["spent_balance"] = clamp_money(float(user_data.get("spent_balance", 0.0)) + amount)
        add_transaction(user_data, "debit", amount,
                        "رفض حساب مقبول واسترداد المبلغ", email)
    save_user(uid, user_data)

    reason_label = REJECT_REASON_LABELS.get(reason, reason)
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=(f"❌ *تم رفض إيميل مقبول*\n\n"
                  f"📧 `{email}`\n"
                  f"📝 السبب: {reason_text or reason_label}\n"
                  "تم نقل الإيميل إلى قائمة المرفوضة."),
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

    for key in ("reject_approved_uid", "reject_approved_token", "step"):
        context.user_data.pop(key, None)
    await update.effective_message.reply_text(
        f"✅ تم رفض الإيميل المقبول `{email}` ونقله إلى المرفوضة.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 الطلبات المقبولة", "view_approved:0"))


async def handle_reject_approved_reason_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data.get("reject_approved_uid")
    token = context.user_data.get("reject_approved_token")
    if not uid or not token:
        await update.message.reply_text("⚠️ حدث خطأ.")
        return
    user_data = get_user(uid)
    match = find_approved_account(user_data, token)
    if match is None:
        await update.message.reply_text("⚠️ الحساب غير موجود أو تمت معالجته.")
        return
    index, account = match
    await finalize_approved_rejection(update, context, uid, index, account, "other", update.message.text.strip())


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
        amount_cents = money_to_cents(amount)
        current_balance = spendable_balance_cents(user_data)
        if current_balance < amount_cents:
            await update.message.reply_text(
                f"⚠️ رصيد المستخدم غير كافٍ!\n💰 الرصيد المتاح: ${cents_to_money(current_balance):.2f}")
            return
        debit_spendable_balance(user_data, amount_cents)
        add_transaction(user_data, "debit", amount, "خصم من المالك", account.get("email", ""))
        save_user(uid, user_data)
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
    rejected = _collect_all("rejected_requests", sort_order="newest")
    if not rejected:
        await query.edit_message_text("📭 لا توجد طلبات مرفوضة.",
                                      reply_markup=kb_single("🔙 الطلبات", "approval_requests"))
        return

    def label(rej):
        reason = rej.get('reject_reason', 'غير معروف')
        icon = REJECT_REASON_ICONS.get(reason, "❌")
        user_name = rej.get("user_name", "غير معروف")
        user_username = rej.get("user_username", "لا يوجد")
        display_name = f"{user_name} (@{user_username})" if user_username != "لا يوجد" else user_name
        email_display = rej.get('email', '')[:15] + "..." if len(rej.get('email', '')) > 15 else rej.get('email', '')
        return (f"{icon} {email_display} - {display_name[:12]}",
                f"rejected_detail:{rej['user_id']}:{rej.get('index', 0)}")

    buttons = paginate_buttons(rejected, page, "view_rejected", label)
    buttons.append(("🔙 الطلبات", "approval_requests"))
    await query.edit_message_text(
        f"❌ *الطلبات المرفوضة ({len(rejected)}) — الأحدث أولاً*\n\nاختر الإيميل:",
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
    reason_map = {
        "email": "❌ الإيميل غير صحيح.",
        "password": "❌ كلمة المرور غير صحيحة.",
        "totp": "❌ رمز المصادقة غير صحيح.",
        "app_pass": "❌ كلمة مرور التطبيق غير صحيحة.",
        "phone": "📱 هذا الحساب يحتاج رقم هاتف.",
        "other": "❌ سبب آخر.",
        "custom": "❌ سبب مخصص.",
        "imap_failed_24h": "🔴 فشل الفحص الثاني (24 ساعة).",
    }
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
        msg += f"🗝 *كلمة مرور التطبيق:* `{format_app_password(request.get('app_pass', ''))}`\n"
    msg += f"📦 *المستوى:* {tier_icon} {tier_text}\n"
    msg += f"👤 *المستخدم:* `{uid}`\n"
    msg += f"💰 *السعر:* ${request.get('amount', 0):.2f}\n"
    msg += f"📝 *سبب الرفض:* {tg_html_escape(str(reason_text))}\n"
    if request.get("rejected_by"):
        msg += f"\n❌ *من رفضه:*\n"
        msg += f"  • معرف: `{request.get('rejected_by')}`\n"
        if request.get("rejected_by_name"):
            msg += f"  • الاسم: {tg_html_escape(request.get('rejected_by_name'))}\n"
    msg += f"\n📌 *هل تريد إعطاء نقاط للمستخدم رغم الرفض؟*"
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
        await update.message.reply_text(
            f"✅ تم إضافة ${amount:.2f} لرصيد `{target_user_id}`.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 إدارة النقاط", "points_management"))
    elif step == "deduct_points_by_id_input":
        user_data = get_user(target_user_id)
        amount_cents = money_to_cents(amount)
        current_balance = spendable_balance_cents(user_data)
        if current_balance < amount_cents:
            await update.message.reply_text(
                f"⚠️ رصيد غير كافٍ! الرصيد المتاح: ${cents_to_money(current_balance):.2f}")
            return
        debit_spendable_balance(user_data, amount_cents)
        add_transaction(user_data, "debit", amount, "خصم من المالك")
        save_user(target_user_id, user_data)
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
    buttons = [
        ("🆕 غير المستخرجة (قديم)", "unextracted_old"),
        ("🆕 غير المستخرجة (جديد)", "unextracted_new"),
        ("📋 جميع الإيميلات (قديم)", "all_old"),
        ("📋 جميع الإيميلات (جديد)", "all_new"),
        ("📊 جميع الإيميلات (عام)", "all_general"),
        ("⏳ الحسابات المعلقة (24 ساعة)", "hold_accounts"),
        ("🔙 إعدادات المالك", "owner_panel")
    ]
    await query.edit_message_text("📊 *إدارة الحسابات المقبولة*", parse_mode=ParseMode.MARKDOWN,
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
    hold_list = sort_records_newest_first(hold_list)
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


async def all_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, acc_type="all"):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    all_accs = []
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        for acc in data.get("approved_accounts", []):
            atype = acc.get("account_type", "old")
            if acc_type == "all" or atype == acc_type:
                copy = dict(acc)
                copy["user_id"] = uid
                all_accs.append(copy)
    all_accs = sort_records_newest_first(all_accs)
    if not all_accs:
        await query.edit_message_text(f"📭 لا توجد حسابات ({acc_type}).",
                                      reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    
    type_name = "قديم" if acc_type == "old" else "جديد" if acc_type == "new" else "عام"
    total = len(all_accs)
    msg = f"📊 *جميع الإيميلات ({type_name}): {total} — الأحدث أولاً*\n\n"
    for idx, acc in enumerate(all_accs[:10], 1):
        if acc.get("rejected_at_24h"):
            tier_icon = "🔴"
        else:
            tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        msg += f"{idx}. {tier_icon} 📧 `{acc.get('email', '')}`\n"
        msg += f"   👤 {acc.get('user_id', '')} | 💰 ${acc.get('amount', 0):.2f}\n   ─────────────\n"
    if total > 10:
        msg += f"\n📌 أول 10 من {total}"
    
    buttons = [
        (f"📥 تصدير {type_name}", f"export_all_{acc_type}"),
        ("🔙 جميع الحسابات", "all_accounts_section")
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def unextracted_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, acc_type="all"):
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
                atype = acc.get("account_type", "old")
                if acc_type == "all" or atype == acc_type:
                    copy = dict(acc)
                    copy["user_id"] = uid
                    copy["index"] = idx
                    unextracted.append(copy)
    unextracted = sort_records_newest_first(unextracted)
    if not unextracted:
        await query.edit_message_text(f"✅ لا توجد حسابات غير مستخرجة ({acc_type}).",
                                      reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))
        return
    
    type_name = "قديم" if acc_type == "old" else "جديد"
    total = len(unextracted)
    msg = f"🆕 *غير مستخرجة ({type_name}): {total} — الأحدث أولاً*\n\n"
    for idx, acc in enumerate(unextracted[:10], 1):
        if acc.get("rejected_at_24h"):
            tier_icon = "🔴"
        else:
            tier_icon = "🟢" if acc.get("has_app_pass") else "🟡" if acc.get("has_totp") else "🔵"
        msg += f"{idx}. {tier_icon} 📧 `{acc.get('email', '')}`\n"
        msg += f"   👤 {acc.get('user_id', '')}\n   ─────────────\n"
    if total > 10:
        msg += f"\n📌 أول 10 من {total}"
    
    buttons = [
        (f"📥 تصدير {type_name}", f"export_unextracted_{acc_type}"),
        ("🔙 جميع الحسابات", "all_accounts_section")
    ]
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
                                    filename=filename, caption=f"✅ تم تصدير {len(accounts)} إيميل.")


async def export_all_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE, acc_type="all"):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
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
        await query.edit_message_text("📭 لا توجد حسابات.")
        return
    type_name = "قديم" if acc_type == "old" else "جديد" if acc_type == "new" else "عام"
    await send_accounts_export(context, all_accs, f"all_{acc_type}.txt", type_name)
    await query.edit_message_text(f"✅ تم تصدير {len(all_accs)} إيميل.",
                                  reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))


async def export_unextracted(update: Update, context: ContextTypes.DEFAULT_TYPE, acc_type="all"):
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
                atype = acc.get("account_type", "old")
                if acc_type == "all" or atype == acc_type:
                    unextracted.append(acc)
    if not unextracted:
        await query.edit_message_text("✅ لا توجد.")
        return
    type_name = "قديم" if acc_type == "old" else "جديد"
    await send_accounts_export(context, unextracted, f"unextracted_{acc_type}.txt", type_name)
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        changed = False
        for acc in data.get("approved_accounts", []):
            if not acc.get("extracted", False):
                atype = acc.get("account_type", "old")
                if acc_type == "all" or atype == acc_type:
                    acc["extracted"] = True
                    changed = True
        if changed:
            save_user(int(uid), data)
    await query.edit_message_text(f"✅ تم تصدير {len(unextracted)} إيميل.",
                                  reply_markup=kb_single("🔙 جميع الحسابات", "all_accounts_section"))


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
        await query.answer("🚫 هذا الإجراء للمالك والأدمن فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    uid = int(parts[1])
    token = parts[2]
    user_data = get_user(uid)
    match = find_pending_request(user_data, token)
    if match is None:
        await query.edit_message_text("⚠️ الطلب غير موجود.",
                                      reply_markup=kb_single("🔙 العودة", rejection_list_callback(actor_id)))
        return
    index, request = match
    if not can_reject_pending_request(actor_id, user_data, index):
        await query.answer("🚫 لا تملك صلاحية رفض هذا الطلب.", show_alert=True)
        return
    email = request.get("email", "")
    request_token = account_callback_token(email)
    display_email = tg_html_escape(email)
    detail_callback = (
        f"pending_detail:{uid}:{request_token}"
        if actor_id == OWNER_ID
        else f"admin_request_detail:{uid}:{request_token}"
    )
    context.user_data["reject_uid"] = uid
    context.user_data["reject_index"] = index
    buttons = [
        ("📧 إيميل خطأ", f"reject_reason:email:{uid}:{request_token}"),
        ("🔑 باسورد خطأ", f"reject_reason:password:{uid}:{request_token}"),
        ("🔐 رمز مصادقة خطأ", f"reject_reason:totp:{uid}:{request_token}"),
        ("🗝 كلمة مرور تطبيق خطأ", f"reject_reason:app_pass:{uid}:{request_token}"),
        ("📱 يحتاج رقم هاتف", f"reject_reason:phone:{uid}:{request_token}"),
        ("📝 خطأ آخر (اكتب السبب)", f"reject_reason:other:{uid}:{request_token}"),
        ("🔙 التفاصيل", detail_callback),
    ]
    await query.edit_message_text(
        f"❌ <b>رفض الطلب</b>\n\n📧 <code>{display_email}</code>\n\nاختر سبب الرفض:",
        parse_mode=ParseMode.HTML, reply_markup=kb_vertical(buttons))


async def execute_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    actor_id = update.effective_user.id
    try:
        if not is_admin_or_owner(actor_id):
            await query.answer("🚫 هذا الإجراء للمالك والأدمن فقط.", show_alert=True)
            return
        parts = query.data.split(":")
        if len(parts) < 4:
            await query.answer("⚠️ بيانات غير صحيحة.", show_alert=True)
            return
        reason_type = parts[1]
        uid = int(parts[2])
        token = parts[3]

        user_data = get_user(uid)
        match = find_pending_request(user_data, token)
        if match is None:
            await _safe_edit(query, "⚠️ الطلب غير موجود أو تمت معالجته.",
                             reply_markup=kb_single("🔙 العودة", rejection_list_callback(actor_id)))
            return
        index, request = match
        if not can_reject_pending_request(actor_id, user_data, index):
            await query.answer("🚫 لا تملك صلاحية رفض هذا الطلب.", show_alert=True)
            return

        email = request.get("email", "")
        request_token = account_callback_token(email)
        display_email = tg_html_escape(email)
        detail_callback = (
            f"pending_detail:{uid}:{request_token}"
            if actor_id == OWNER_ID
            else f"admin_request_detail:{uid}:{request_token}"
        )

        if reason_type == "other":
            context.user_data["reject_uid"] = uid
            context.user_data["reject_index"] = index
            context.user_data["reject_reason"] = "other"
            context.user_data["step"] = "reject_reason_text"
            await _safe_edit(
                query,
                f"📝 <b>اكتب سبب الرفض</b>\n\nأرسل رسالة توضح سبب رفض طلب <code>{display_email}</code>:",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("🔙 إلغاء", detail_callback))
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
            user_data["rejected_requests"][-1]["rejected_by_name"] = actor.full_name or "غير معروف"
            user_data["rejected_requests"][-1]["rejected_by_username"] = actor.username or ""
            user_data["rejected_requests"][-1]["rejected_at"] = datetime.now(timezone.utc).isoformat()
        save_user(uid, user_data)

        reason = REJECT_REASON_MESSAGES.get(reason_type, "❌ تم رفض طلبك.")

        config = load_config()
        video_key = REJECT_REASON_VIDEO_KEY.get(reason_type)
        video_path = config.get(video_key) if video_key else None
        if video_path and Path(video_path).exists():
            try:
                await context.bot.send_video(
                    chat_id=uid, video=open(video_path, "rb"),
                    caption=f"{reason}\n\n📹 *شاهد الفيديو:*",
                    parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            except Exception:
                logger.exception("Failed to send rejection video")

        ban_notice = ""
        if ban_result:
            _, duration_text = ban_result
            ban_notice = (f"\n\n🚫 *تم حظرك من إرسال حسابات جديدة لمدة {duration_text}* "
                          f"(3 رفضات متتالية).")

        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"{reason}\n\n📧 الإيميل: `{email}`\nيمكنك إعادة المحاولة.{ban_notice}",
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            logger.exception("Failed to notify rejected user")

        success_text = (
            f"✅ تم رفض الطلب <code>{display_email}</code>.\n"
            f"📝 السبب: {REJECT_REASON_LABELS.get(reason_type, reason_type)}"
        )
        if ban_result:
            success_text += f"\n🚫 تم حظر البائع لمدة {ban_result[1]}."
        await _safe_edit(query, success_text, parse_mode=ParseMode.HTML,
                         reply_markup=kb_single("🔙 العودة", rejection_list_callback(actor_id)))

    except Exception:
        logger.exception("execute_reject_reason failed")
        try:
            await query.answer("⚠️ حدث خطأ أثناء الرفض، حاول مرة أخرى.", show_alert=True)
        except Exception:
            pass


async def handle_reject_reason_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    actor_id = update.effective_user.id
    uid = context.user_data.get("reject_uid")
    index = context.user_data.get("reject_index")
    text = update.message.text.strip()
    if not uid or index is None:
        await update.message.reply_text("⚠️ حدث خطأ.")
        return
    user_data = get_user(uid)
    pending = user_data.get("pending_requests", [])
    if not can_reject_pending_request(actor_id, user_data, index):
        await update.message.reply_text("🚫 لا تملك صلاحية رفض هذا الطلب.")
        return
    if index >= len(pending):
        await update.message.reply_text("⚠️ الطلب غير موجود.")
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
        user_data["rejected_requests"][-1]["rejected_by_name"] = actor.full_name or "غير معروف"
        user_data["rejected_requests"][-1]["rejected_by_username"] = actor.username or ""
        user_data["rejected_requests"][-1]["rejected_at"] = datetime.now(timezone.utc).isoformat()
    save_user(uid, user_data)

    ban_notice = ""
    if ban_result:
        _, duration_text = ban_result
        ban_notice = f"\n\n🚫 *تم حظرك لمدة {duration_text}* بسبب 3 رفضات متتالية."

    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"❌ *تم رفض طلبك*\n\n📧 الإيميل: `{email}`\n📝 السبب: {text}{ban_notice}",
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("Failed to notify rejected user (custom reason)")
    for key in ("reject_uid", "reject_index", "reject_reason", "step"):
        context.user_data.pop(key, None)
    confirmation = f"✅ تم رفض الطلب `{email}`."
    if ban_result:
        confirmation += f"\n🚫 تم حظر البائع لمدة {ban_result[1]}."
    await update.message.reply_text(
        confirmation,
        reply_markup=kb_single("🔙 العودة", rejection_list_callback(actor_id)))


__all__ = [name for name in globals() if not name.startswith("__")]
