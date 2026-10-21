"""Owner and administrator panels, reporting, and settings."""

from telegram_bot.core import *

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
            "completed_count": 0,
            "pending_count": 0,
            "released_count": 0,
        }
    )
    admin_pending = admin_ledger["pending"]
    received = clamp_money(user_data.get("admin_received_balance", 0.0))
    received_total = max(received, admin_ledger["released"])

    ordinary = clamp_money(user_data.get("balance", 0.0))
    total_owned = clamp_money(ordinary + received)
    return {
        "pending": pending,
        "hold": hold,
        "admin_pending": admin_pending,
        "received": received_total,
        "available_admin": received,
        "completed_count": admin_ledger["completed_count"],
        "pending_count": admin_ledger["pending_count"],
        "released_count": admin_ledger["released_count"],
        "ordinary": ordinary,
        "total_owned": total_owned,
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
    admin_balances = (
        admin_balance_stats(user_data, member_id)
        if is_admin(member_id)
        else None
    )
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
    
    acc_type = rec.get("account_type", "old")
    type_text = "🆕 جديد" if acc_type == "new" else "📝 قديم"
    lines.append(f"<b>نوع الإيميل:</b> {type_text}")

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
        maintenance_toggle_button(),
        ("🔗 رابط الدعم", "support_link_settings"),
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
        ("📈 إحصائيات المستخدمين", "owner_stats"),
        ("🔎 فحص عضو", "check_member"),
        ("🔍 فحص إيميل", "check_email_by_address"),
        ("🔗 نظام الإحالة", "referral_settings"),
        ("💰 خصم/منح نقاط", "points_management"),
        ("🔙 القائمة الرئيسية", "main_menu")
    ]
    maintenance_status = "مفعّل" if maintenance_is_enabled() else "متوقف"
    await query.edit_message_text(
        f"⚙️ *لوحة تحكم المالك*\n\nوضع الصيانة: *{maintenance_status}*\nاختر الإعداد الذي تريد تعديله:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.edit_message_text("🚫 هذا الخيار للمالك فقط.")
        return
    config = load_config()
    config["maintenance_mode"] = not bool(config.get("maintenance_mode", False))
    save_config(config)
    await owner_panel(update, context)


async def support_link_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.edit_message_text("🚫 هذا الخيار للمالك فقط.")
        return
    context.user_data["step"] = "support_link_input"
    current_link = normalize_support_link(load_config().get("support_link")) or "غير مضبوط"
    await query.edit_message_text(
        "🔗 إعداد رابط الدعم\n\n"
        f"الرابط الحالي: {current_link}\n\n"
        "أرسل رابطاً يبدأ بـ https://، أو أرسل «حذف» لإزالته.",
        reply_markup=kb_single("❌ إلغاء", "support_link_cancel"))


async def handle_support_link_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        context.user_data.pop("step", None)
        return
    text = update.message.text.strip()
    if text.casefold() in {"حذف", "مسح", "delete", "remove"}:
        config = load_config()
        config.pop("support_link", None)
        save_config(config)
        context.user_data.pop("step", None)
        await update.message.reply_text(
            "✅ تم حذف رابط الدعم.",
            reply_markup=kb_single("⚙️ لوحة المالك", "owner_panel"))
        return
    support_link = normalize_support_link(text)
    if not support_link:
        await update.message.reply_text(
            "⚠️ الرابط غير صالح. أرسل رابط HTTPS صحيحاً، مثل https://t.me/your_support.")
        return
    config = load_config()
    config["support_link"] = support_link
    save_config(config)
    context.user_data.pop("step", None)
    await update.message.reply_text(
        "✅ تم حفظ رابط الدعم وسيظهر للأعضاء في القائمة الرئيسية.",
        reply_markup=kb_single("⚙️ لوحة المالك", "owner_panel"))


async def cancel_support_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.callback_query.edit_message_text("🚫 هذا الخيار للمالك فقط.")
        return
    context.user_data.pop("step", None)
    await owner_panel(update, context)


# ==================== CONTEST MENU ====================
async def contest_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    context.user_data.pop("contest_build", None)
    context.user_data.pop("contest_build_step", None)
    contest = get_contest()
    header = "🎯 *إدارة المسابقة*\n\n" + "\n".join(contest_summary_lines(contest))
    buttons = []
    if contest and contest.get("active"):
        buttons.append(("🛑 إيقاف المسابقة", "contest_stop"))
        buttons.append(("📊 إحصائيات المسابقة", "contest_stats"))
    else:
        buttons.append(("🆕 إنشاء مسابقة جديدة", "contest_create_start"))
        if contest:
            buttons.append(("📊 إحصائيات المسابقة (القديمة)", "contest_stats"))
    buttons.append(("📈 البيع الكلي", "sales_total"))
    buttons.append(("📈 البيع أثناء المسابقة", "sales_during_contest"))
    buttons.append(("🔙 إعدادات المالك", "owner_panel"))
    await query.edit_message_text(
        header, parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical(buttons))


async def contest_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    context.user_data["contest_build"] = {"tiers": []}
    context.user_data["contest_build_step"] = "max_winners"
    await query.edit_message_text(
        "🆕 *إنشاء مسابقة جديدة*\n\n"
        "📌 *الخطوة 1*: أرسل الحد الأقصى لعدد الفائزين لكل جائزة.\n"
        "مثال: `2000`\n\n_أو 'إلغاء'_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", "contest_menu"))


async def contest_add_tier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    build = context.user_data.get("contest_build") or {}
    if not build:
        await query.answer("⚠️ لا توجد مسابقة قيد الإنشاء.", show_alert=True)
        return
    context.user_data["contest_build_step"] = "tier_emails"
    tier_num = len(build.get("tiers", [])) + 1
    await query.edit_message_text(
        f"🎁 *الجائزة رقم {tier_num}*\n\n"
        f"📌 أرسل عدد الإيميلات المطلوبة للفوز بهذه الجائزة.\n"
        f"مثال: `100`\n\n_أو 'إلغاء'_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", "contest_menu"))


async def contest_finalize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    build = context.user_data.get("contest_build") or {}
    tiers = build.get("tiers", [])
    max_winners = int(build.get("max_winners", 0) or 0)
    if not tiers or max_winners <= 0:
        await query.answer("⚠️ لم يتم استكمال بيانات المسابقة.", show_alert=True)
        return
    contest = {
        "active": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "max_winners": max_winners,
        "tiers": tiers,
        "winners": [],
        "total_emails_delivered": 0,
        "total_points_awarded": 0.0,
    }
    save_contest(contest)
    context.user_data.pop("contest_build", None)
    context.user_data.pop("contest_build_step", None)
    await query.edit_message_text(
        "🎉 <b>تم إنشاء المسابقة وتفعيلها</b>\n\n" + "\n".join(contest_summary_lines(contest)),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إدارة المسابقة", "contest_menu"))


async def handle_contest_build_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    text = update.message.text.strip()
    if text.casefold() in {"إلغاء", "الغاء", "cancel"}:
        context.user_data.pop("contest_build", None)
        context.user_data.pop("contest_build_step", None)
        await update.message.reply_text(
            "❌ تم الإلغاء.",
            reply_markup=kb_single("🔙 إدارة المسابقة", "contest_menu"))
        return
    step = context.user_data.get("contest_build_step")
    build = context.user_data.get("contest_build") or {}

    if step == "max_winners":
        try:
            v = int(float(text))
            if v <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
            return
        build["max_winners"] = v
        context.user_data["contest_build"] = build
        context.user_data["contest_build_step"] = "tier_emails"
        await update.message.reply_text(
            f"✅ الحد الأقصى للفائزين لكل جائزة: <code>{v}</code>\n\n"
            f"🎁 *الجائزة 1*: أرسل عدد الإيميلات المطلوبة.\nمثال: `100`\n\n_أو 'إلغاء'_",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", "contest_menu"))
        return

    if step == "tier_emails":
        try:
            v = int(float(text))
            if v <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
            return
        build["_current_emails"] = v
        context.user_data["contest_build"] = build
        context.user_data["contest_build_step"] = "tier_reward"
        await update.message.reply_text(
            f"✅ عدد الإيميلات: <code>{v}</code>\n\n"
            f"💰 أرسل مكافأة هذه الجائزة (بالدولار).\nمثال: `3`\n\n_أو 'إلغاء'_",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", "contest_menu"))
        return

    if step == "tier_reward":
        try:
            r = float(text)
            if r <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
            return
        emails = int(build.get("_current_emails", 0) or 0)
        tiers = build.get("tiers", [])
        tiers.append({"emails": emails, "reward": round(r, 2)})
        build["tiers"] = tiers
        build.pop("_current_emails", None)
        context.user_data["contest_build"] = build
        context.user_data.pop("contest_build_step", None)

        lines = ["📋 <b>ملخص المسابقة حتى الآن</b>", ""]
        lines.append(f"🏅 الحد الأقصى للفائزين (لكل جائزة): <code>{build['max_winners']}</code>")
        lines.append("")
        lines.append("🎁 <b>الجوائز:</b>")
        for i, t in enumerate(tiers, 1):
            lines.append(f"  {i}. عند <b>{t['emails']}</b> إيميل → <code>${t['reward']:.2f}</code>")
        await update.message.reply_text(
            "\n".join(lines) + "\n\nهل تريد إضافة جائزة أخرى أم إنهاء المسابقة؟",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_vertical([
                ("➕ إضافة جائزة", "contest_add_tier"),
                ("✅ إنشاء المسابقة", "contest_finalize"),
                ("❌ إلغاء", "contest_menu"),
            ]))
        return


async def contest_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    contest = get_contest()
    if not contest:
        await query.answer("⚠️ لا توجد مسابقة.", show_alert=True)
        return
    contest["active"] = False
    contest["stopped_at"] = datetime.now(timezone.utc).isoformat()
    save_contest(contest)
    await query.edit_message_text(
        "🛑 <b>تم إيقاف المسابقة</b>\n\n" + "\n".join(contest_summary_lines(contest)),
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إدارة المسابقة", "contest_menu"))


async def contest_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    contest = get_contest()
    if not contest or not contest.get("started_at"):
        await query.edit_message_text(
            "📭 لا توجد مسابقة.",
            reply_markup=kb_single("🔙 إدارة المسابقة", "contest_menu"))
        return
    started_at = contest.get("started_at")
    delivered = int(contest.get("total_emails_delivered", 0) or 0)
    winners = contest.get("winners", []) or []
    tiers = contest.get("tiers", []) or []
    total_pts = float(contest.get("total_points_awarded", 0) or 0)
    lines = ["📊 <b>إحصائيات المسابقة</b>", ""]
    lines.append(f"📌 <b>الحالة:</b> {'🟢 نشطة' if contest.get('active') else '🔴 متوقفة'}")
    if started_at:
        lines.append(f"🕐 <b>البداية:</b> <code>{tg_html_escape(_format_iso_time(started_at))}</code>")
    lines.append("")
    lines.append(f"✅ <b>عدد الفائزين الكلي:</b> <code>{len(winners)}</code>")
    lines.append(f"📨 <b>عدد الإيميلات الواصلة:</b> <code>{delivered}</code>")
    lines.append(f"💵 <b>النقاط الممنوحة:</b> <code>${total_pts:.2f}</code>")
    lines.append("")
    lines.append("🎁 <b>تفصيل كل جائزة:</b>")
    for i, tier in enumerate(tiers):
        emails = int(tier.get("emails", 0) or 0)
        reward = float(tier.get("reward", 0) or 0)
        tier_winners = [w for w in winners if int(w.get("tier_index", -1)) == i]
        lines.append(
            f"  {i+1}. عند <b>{emails}</b> إيميل → <code>${reward:.2f}</code> — فائزون: <code>{len(tier_winners)}</code>"
        )
    await query.edit_message_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إدارة المسابقة", "contest_menu"))


async def sales_total_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    stats = compute_sales_stats(since_iso=None)
    lines = ["📈 <b>البيع الكلي (كل الفترات)</b>", ""]
    lines.append(f"📦 <b>إجمالي الحسابات:</b> <code>{stats['total_accounts']}</code>")
    lines.append("")
    lines.append("🏆 <b>أعلى 10 أعضاء بيعاً:</b>")
    if not stats["top"]:
        lines.append("_لا توجد بيانات._")
    for idx, item in enumerate(stats["top"], 1):
        lines.append(f"{idx}- ID <code>{item['user_id']}</code> بيع <b>{item['total']}</b>")
    await query.edit_message_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إدارة المسابقة", "contest_menu"))


async def sales_during_contest_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    contest = get_contest()
    started_at = contest.get("started_at") if contest else None
    if not started_at:
        await query.edit_message_text(
            "📭 لا توجد مسابقة نشطة أو سابقة.",
            reply_markup=kb_single("🔙 إدارة المسابقة", "contest_menu"))
        return
    stats = compute_sales_stats(since_iso=started_at)
    lines = ["📈 <b>البيع أثناء المسابقة</b>", ""]
    lines.append(f"🕐 <b>من:</b> <code>{tg_html_escape(_format_iso_time(started_at))}</code>")
    lines.append("")
    lines.append(f"📦 <b>إجمالي الحسابات:</b> <code>{stats['total_accounts']}</code>")
    lines.append("")
    lines.append("🏆 <b>أعلى 10 أعضاء بيعاً خلال المسابقة:</b>")
    if not stats["top"]:
        lines.append("_لا توجد بيانات._")
    for idx, item in enumerate(stats["top"], 1):
        lines.append(f"{idx}- ID <code>{item['user_id']}</code> بيع <b>{item['total']}</b>")
    await query.edit_message_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إدارة المسابقة", "contest_menu"))


# ==================== ADMIN MANAGEMENT (OWNER) ====================
async def admin_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    admins = load_admins()
    buttons = [
        ("➕ إضافة أدمن", "add_admin"),
        ("🗑️ مسح أدمن", "remove_admin_menu"),
        ("🔙 إعدادات المالك", "owner_panel"),
    ]
    current = "\n".join(f"• `{a}`" for a in sorted(admins)) if admins else "_لا يوجد أدمن حالياً_"
    await query.edit_message_text(
        f"👥 *الإدارية*\n\n"
        f"📊 عدد الأدمن: *{len(admins)}*\n\n"
        f"*القائمة الحالية:*\n{current}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def add_admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    context.user_data["step"] = "add_admin_input"
    await query.edit_message_text(
        "➕ *إضافة أدمن*\n\nأرسل معرف العضو الرقمي:\n"
        "مثال: `123456789`\n\n_أو 'إلغاء'_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", "admin_management"))


async def handle_add_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    text = update.message.text.strip()
    if text.casefold() in {"إلغاء", "الغاء", "cancel"}:
        context.user_data.pop("step", None)
        await update.message.reply_text("❌ تم الإلغاء.",
                                        reply_markup=kb_single("🔙 الإدارية", "admin_management"))
        return
    if not text.lstrip("-").isdigit():
        await update.message.reply_text("⚠️ أرسل معرفاً رقمياً صحيحاً.")
        return
    new_admin_id = int(text)
    if new_admin_id == OWNER_ID:
        await update.message.reply_text("⚠️ المالك لديه صلاحيات كاملة بالفعل.")
        return
    admins = load_admins()
    if new_admin_id in admins:
        await update.message.reply_text("ℹ️ هذا المستخدم أدمن بالفعل.")
        return
    admins.add(new_admin_id)
    save_admins(admins)
    context.user_data.pop("step", None)
    await update.message.reply_text(
        f"✅ تم إضافة الأدمن `{new_admin_id}` بنجاح.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 الإدارية", "admin_management"))
    try:
        await context.bot.send_message(
            chat_id=new_admin_id,
            text="🎉 *تم تعيينك أدمن!*\n\nاستخدم /start لعرض لوحة الإدارة.",
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass


async def remove_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    admins = load_admins()
    if not admins:
        await query.edit_message_text("📭 لا يوجد أدمن حالياً.",
                                      reply_markup=kb_single("🔙 الإدارية", "admin_management"))
        return
    buttons = []
    for admin_id in sorted(admins):
        buttons.append((f"🗑️ {admin_id}", f"remove_admin_execute:{admin_id}"))
    buttons.append(("🔙 الإدارية", "admin_management"))
    await query.edit_message_text("🗑️ *اختر الأدمن لمسحه:*",
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def remove_admin_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    admin_id = int(query.data.split(":")[1])
    admins = load_admins()
    if admin_id in admins:
        admins.discard(admin_id)
        save_admins(admins)
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text="ℹ️ تم إزالة صلاحياتك كأدمن.",
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
    await query.edit_message_text(f"✅ تم مسح الأدمن `{admin_id}`.",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 الإدارية", "admin_management"))


# ==================== ADMIN PANEL ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫 أدمن فقط.", show_alert=True)
        return
    context.user_data.pop("step", None)
    buttons = [
        ("⚙️ إعدادات الأدمن", "admin_settings"),
        ("🔙 القائمة الرئيسية", "main_menu"),
    ]
    await query.edit_message_text(
        "🛠 *الإدارية*\n\nاختر القسم:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫 أدمن فقط.", show_alert=True)
        return
    buttons = [
        ("📋 طلبات", "admin_requests:0"),
        ("🔙 الإدارية", "admin_panel"),
    ]
    await query.edit_message_text(
        "⚙️ *إعدادات الأدمن*\n\nاختر الإجراء:",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


__all__ = [name for name in globals() if not name.startswith("__")]
