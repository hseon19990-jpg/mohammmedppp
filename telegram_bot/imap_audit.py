"""Owner-only bulk IMAP audit.

The owner sends a list of ``email:app_password`` lines (as a message or as a
.txt/.csv file). The bot logs in to every mailbox over IMAP, finds the member
who sold each email, shows the ones that failed, and lets the owner decide per
account (or in bulk) whether to deduct the seller, the admin who accepted it,
both, or keep it as it is.
"""

from telegram_bot.core import *


IMAP_AUDIT_MODE = "imap_check_wait"
IMAP_AUDIT_MAX_EMAILS = 300
IMAP_AUDIT_CONCURRENCY = 4
IMAP_AUDIT_MAX_FILE_BYTES = 2 * 1024 * 1024
IMAP_AUDIT_MAX_CARDS = 60
IMAP_AUDIT_KEEP_SCANS = 5

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_LEAD_SEP_RE = re.compile(r"^[\s:|,;=\-]+")
_FIELD_SPLIT_RE = re.compile(r"[:|,;\t]+")

ST_OK = "ok"
ST_FAILED = "failed"
ST_UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════════════
# Parsing
# ═══════════════════════════════════════════════════════════════════
def _pick_app_password(rest: str) -> str:
    """Pick the app password out of whatever follows the email on a line.

    Google shows app passwords as "abcd efgh ijkl mnop", so spaces inside the
    value are removed. If the line has several fields (email:pass:totp:app) the
    last one is used.
    """
    fields = [f.strip() for f in _FIELD_SPLIT_RE.split(rest) if f.strip()]
    if not fields:
        return ""
    candidate = fields[-1]
    squashed = re.sub(r"\s+", "", candidate)
    if len(squashed) == 16:
        return squashed
    tokens = candidate.split()
    if len(tokens) > 1 and len(tokens[-1]) == 16 and tokens[-1].isalnum():
        return tokens[-1]
    return squashed


def parse_imap_audit_input(text: str) -> Tuple[List[Tuple[str, str]], List[str], int]:
    """Return (entries, bad_lines, duplicate_count). Entries are (email, app_pass)."""
    entries: List[Tuple[str, str]] = []
    bad: List[str] = []
    seen = set()
    duplicates = 0
    for raw in (text or "").replace("\r", "\n").split("\n"):
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        match = _EMAIL_RE.search(line)
        if not match:
            bad.append(line[:40])
            continue
        email = match.group(0)
        rest = _LEAD_SEP_RE.sub("", line[match.end():])
        if not rest.strip():
            rest = _LEAD_SEP_RE.sub("", line[:match.start()][::-1])[::-1]
        app_pass = _pick_app_password(rest)
        if not app_pass:
            bad.append(line[:40])
            continue
        key = normalize_email(email)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        entries.append((email, app_pass))
    return entries, bad, duplicates


def _decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1256", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="ignore")


# ═══════════════════════════════════════════════════════════════════
# IMAP result classification
# ═══════════════════════════════════════════════════════════════════
def classify_audit_result(result: dict) -> Tuple[str, str]:
    """Map verify_account_credentials() output to (status, short Arabic reason).

    Only a real credential rejection counts as a failure. Network problems,
    rate limits and unsupported providers are "unknown" so nobody is penalised
    for something the server could not decide.
    """
    if result.get("imap_ok"):
        return ST_OK, "تسجيل الدخول ناجح"
    category = result.get("category", "unknown")
    message = str(result.get("message", ""))
    if category == "2fa":
        return ST_FAILED, "كلمة مرور التطبيق غير صحيحة (Google يطلب App Password صالحة)"
    if category == "auth_or_policy":
        return ST_FAILED, "رفض الخادم بيانات الدخول (الإيميل أو كلمة مرور التطبيق)"
    if "معطّل" in message:
        return ST_FAILED, "الحساب معطّل من المزود"
    if category == "network":
        return ST_UNKNOWN, "تعذّر الاتصال بالخادم (شبكة/مهلة) — لم يُحسم"
    if category == "unsupported_auth":
        return ST_UNKNOWN, "المزود لا يسمح بهذا النوع من التحقق"
    return ST_UNKNOWN, "نتيجة غير محسومة — أعد الفحص لاحقاً"


# ═══════════════════════════════════════════════════════════════════
# Finding the seller / accepting admin
# ═══════════════════════════════════════════════════════════════════
def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_email_index(emails: set) -> Dict[str, List[dict]]:
    """One pass over users.json -> {normalized_email: [active/rejected records]}."""
    index: Dict[str, List[dict]] = {}
    users = load_json(USERS_DB)
    for raw_uid, encrypted in users.items():
        uid = _safe_int(raw_uid)
        if not uid:
            continue
        try:
            user_data = decrypt_user_data(encrypted)
        except Exception:
            logger.exception("Could not decrypt user %s for IMAP audit", raw_uid)
            continue
        groups = (("approved", "approved_accounts"), ("pending", "pending_requests"),
                  ("rejected", "rejected_requests"))
        for kind, key in groups:
            for rec in user_data.get(key, []) or []:
                norm = normalize_email(rec.get("email", ""))
                if norm not in emails:
                    continue
                if kind == "approved" and rec.get("rejected_at_24h"):
                    kind_eff = "rejected"
                else:
                    kind_eff = kind
                admin_id = _safe_int(rec.get("completed_by_admin"))
                index.setdefault(norm, []).append({
                    "uid": uid,
                    "kind": kind_eff,
                    "token": account_callback_token(rec.get("email", "")),
                    "email": rec.get("email", ""),
                    "name": rec.get("user_name") or user_data.get("user_name") or "غير معروف",
                    "username": rec.get("user_username") or user_data.get("user_username") or "",
                    "amount": clamp_money(rec.get("amount", 0.0)),
                    "hold": bool(rec.get("approved_with_leave") and not rec.get("leave_confirmed")),
                    "released": bool(rec.get("leave_confirmed")),
                    "admin_id": admin_id,
                    "admin_name": rec.get("completed_by_admin_name", ""),
                    "admin_username": rec.get("completed_by_admin_username", ""),
                    "admin_bonus": cents_to_money(money_to_cents(rec.get("admin_bonus", 0.0))),
                    "bonus_status": str(rec.get("admin_bonus_status") or ""),
                })
    return index


def _admin_is_penalisable(case: dict) -> bool:
    return (case["kind"] == "approved"
            and case["admin_id"] not in (0, OWNER_ID)
            and money_to_cents(case["admin_bonus"]) > 0
            and case["bonus_status"] in ("pending", "released"))


def _state_label(case: dict) -> str:
    if case["kind"] == "pending":
        return "⏳ قيد المراجعة (لم يُقبل بعد)"
    if case["released"]:
        return "🟢 قُبل وتم تحويل الرصيد للبائع"
    if case["hold"]:
        return "🟡 قُبل — الرصيد معلّق"
    return "🟢 مقبول"


def _bonus_label(case: dict) -> str:
    return {"pending": "معلّقة", "released": "واصلة للمشرف"}.get(case["bonus_status"], case["bonus_status"] or "—")


# ═══════════════════════════════════════════════════════════════════
# Applying deductions
# ═══════════════════════════════════════════════════════════════════
def apply_audit_case(case: dict, do_seller: bool, do_admin: bool,
                     actor_id: int, actor_name: str, reason_text: str) -> dict:
    """Mutate balances for one failed account. Synchronous on purpose: the
    read-modify-write on users.json happens without any ``await`` in between.
    """
    uid, email = case["uid"], case["email"]
    out: Dict[str, Any] = {
        "found": False, "seller": False, "seller_amount": 0.0, "seller_short": 0.0,
        "seller_source": "", "admin": "", "admin_id": 0, "admin_amount": 0.0,
    }
    user_data = get_user(uid)
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── Pending request: nothing was paid yet, so only the seller side exists ──
    if case["kind"] == "pending":
        if not do_seller:
            return out
        match = find_pending_request(user_data, case["token"])
        if match is None:
            return out
        index, request = match
        out["found"] = True
        pending = user_data.get("pending_requests", [])
        pending.pop(index)
        user_data["pending_requests"] = pending
        move_request_to_rejected(user_data, request, "other", reason_text)
        rec = user_data["rejected_requests"][-1]
        rec["rejected_by"] = actor_id
        rec["rejected_by_name"] = actor_name
        rec["rejected_at"] = now_iso
        amount = clamp_money(request.get("amount", 0.0))
        add_transaction(user_data, "approved_rejection", amount,
                        "Pending request rejected (owner IMAP audit)", email)
        save_user(uid, user_data)
        out.update(seller=True, seller_amount=amount, seller_source="pending")
        return out

    # ── Approved account ──
    match = find_approved_account(user_data, case["token"])
    if match is None:
        return out
    index, account = match
    if account.get("rejected_at_24h"):
        return out
    out["found"] = True
    account = dict(account)
    accounts = user_data.get("approved_accounts", [])
    amount = clamp_money(account.get("amount", 0.0))

    admin_id = _safe_int(account.get("completed_by_admin"))
    bonus_cents = money_to_cents(account.get("admin_bonus", 0.0))
    bonus_status = str(account.get("admin_bonus_status") or "")
    penalise_released = False

    if admin_id not in (0, OWNER_ID) and bonus_cents > 0:
        if bonus_status == "pending" and (do_admin or do_seller):
            # The bonus was never paid out: cancelling it is plain bookkeeping and
            # is always needed when the account is rejected (same as the existing
            # approved-account rejection).
            if settle_admin_bonus(account, "rejected", owner_data=user_data, owner_id=uid):
                account["admin_bonus_status"] = "rejected"
                out.update(admin="cancelled", admin_id=admin_id, admin_amount=cents_to_money(bonus_cents))
        elif bonus_status == "released" and do_admin:
            account["admin_bonus_status"] = "rejected"
            account["admin_penalized_imap"] = True
            penalise_released = True

    account["imap_owner_check"] = {"failed": True, "at": now_iso, "by": actor_id,
                                   "message": reason_text[:150]}

    if do_seller:
        was_hold = bool(account.get("approved_with_leave") and not account.get("leave_confirmed"))
        was_released = bool(account.get("leave_confirmed"))
        accounts.pop(index)
        user_data["approved_accounts"] = accounts
        move_approved_account_to_rejected(user_data, account, "other", reason_text)
        rec = user_data["rejected_requests"][-1]
        rec["rejected_by"] = actor_id
        rec["rejected_by_name"] = actor_name
        if was_hold:
            user_data["hold_balance"] = clamp_money(float(user_data.get("hold_balance", 0.0)) - amount)
            add_transaction(user_data, "approved_rejection", amount,
                            "Rejected after owner IMAP audit, hold canceled", email)
            out.update(seller=True, seller_amount=amount, seller_source="hold")
        elif was_released:
            have = money_to_cents(user_data.get("balance", 0.0))
            want = money_to_cents(amount)
            take = min(have, want)
            user_data["balance"] = cents_to_money(have - take)
            user_data["spent_balance"] = clamp_money(
                money_to_cents(user_data.get("spent_balance", 0.0)) / 100 + cents_to_money(take))
            add_transaction(user_data, "debit", cents_to_money(take),
                            "Deducted after owner IMAP audit", email)
            out.update(seller=True, seller_amount=cents_to_money(take),
                       seller_short=cents_to_money(want - take), seller_source="balance")
        else:
            out.update(seller=True, seller_amount=0.0, seller_source="none")
    else:
        accounts[index] = account
        user_data["approved_accounts"] = accounts

    save_user(uid, user_data)

    # Released bonus: this goes through its own get_user/save_user, so it must run
    # after the seller's record has been saved (they can be the same person).
    if penalise_released:
        if deduct_from_admin_balance(admin_id, bonus_cents,
                                     "فشل فحص IMAP لإيميل قبله", email):
            out.update(admin="deducted", admin_id=admin_id, admin_amount=cents_to_money(bonus_cents))
    return out


def _result_lines(email: str, out: dict) -> str:
    lines = [f"📧 <code>{tg_html_escape(email)}</code>"]
    if not out["found"]:
        lines.append("ℹ️ لم يعد السجل موجوداً (تمت معالجته سابقاً أو تغيّر).")
        return "\n".join(lines)
    if out["seller"]:
        src = {"hold": "من الرصيد المعلّق", "balance": "من رصيده", "pending": "من رصيد الانتظار",
               "none": ""}.get(out["seller_source"], "")
        lines.append(f"💸 <b>البائع:</b> تم خصم <b>${out['seller_amount']:.2f}</b> {src}".rstrip())
        if out["seller_short"] > 0:
            lines.append(f"⚠️ رصيده لم يكفِ: بقي <b>${out['seller_short']:.2f}</b> غير مخصوم.")
        lines.append("📌 نُقل الإيميل إلى المرفوضة.")
    if out["admin"] == "deducted":
        lines.append(f"🛡 <b>المشرف</b> <code>{out['admin_id']}</code>: تم خصم <b>${out['admin_amount']:.2f}</b>")
    elif out["admin"] == "cancelled":
        lines.append(f"🛡 <b>المشرف</b> <code>{out['admin_id']}</code>: أُلغيت مكافأته المعلّقة <b>${out['admin_amount']:.2f}</b>")
    if not out["seller"] and not out["admin"]:
        lines.append("ℹ️ لا شيء للخصم (لا مكافأة مشرف أو سبق خصمها).")
    return "\n".join(lines)


async def _notify_parties(context: ContextTypes.DEFAULT_TYPE, case: dict, out: dict):
    email = case["email"]
    if out.get("seller"):
        try:
            if case["kind"] == "pending":
                await context.bot.send_message(
                    chat_id=case["uid"], parse_mode=ParseMode.HTML,
                    text=t(case["uid"], "member_rejected_imap_fail", email=tg_html_escape(email)))
            else:
                await context.bot.send_message(
                    chat_id=case["uid"], parse_mode=ParseMode.MARKDOWN,
                    text=t(case["uid"], "member_rejected_approved_msg", email=email,
                           reason="فشل فحص IMAP"))
        except Exception:
            logger.warning("Could not notify seller %s", case["uid"])
    if out.get("admin") in ("deducted", "cancelled") and out.get("admin_id"):
        verb = ("تم خصم" if out["admin"] == "deducted" else "تم إلغاء مكافأتك المعلّقة")
        try:
            await context.bot.send_message(
                chat_id=out["admin_id"], parse_mode=ParseMode.HTML,
                text=(f"⚠️ <b>خصم مشرف — فشل فحص IMAP</b>\n\n"
                      f"📧 <code>{tg_html_escape(email)}</code>\n"
                      f"💰 {verb} <b>${out['admin_amount']:.2f}</b>\n"
                      f"📌 <i>السبب: الإيميل الذي قبلته فشل في فحص IMAP.</i>"))
        except Exception:
            logger.warning("Could not notify admin %s", out.get("admin_id"))


# ═══════════════════════════════════════════════════════════════════
# Scan state (in memory only — never stores app passwords)
# ═══════════════════════════════════════════════════════════════════
def _scans(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data.setdefault("imap_scans", {})


def _remember_scan(context: ContextTypes.DEFAULT_TYPE, scan: dict):
    scans = _scans(context)
    scans[scan["id"]] = scan
    while len(scans) > IMAP_AUDIT_KEEP_SCANS:
        scans.pop(next(iter(scans)))


def _case_keyboard(scan_id: str, idx: int, case: dict) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("💸 خصم البائع", callback_data=f"imapx:s:{scan_id}:{idx}")]]
    if _admin_is_penalisable(case):
        rows[0].append(InlineKeyboardButton("🛡 خصم المشرف", callback_data=f"imapx:a:{scan_id}:{idx}"))
        rows.append([InlineKeyboardButton("💸🛡 خصم الاثنين", callback_data=f"imapx:b:{scan_id}:{idx}")])
    rows.append([InlineKeyboardButton("✋ إبقاء كما هو", callback_data=f"imapx:k:{scan_id}:{idx}")])
    return InlineKeyboardMarkup(rows)


def _case_card(case: dict) -> str:
    uname = f" (@{tg_html_escape(case['username'])})" if case["username"] and case["username"] != "None" else ""
    lines = [
        "🔴 <b>فشل فحص IMAP</b>",
        f"📧 <code>{tg_html_escape(case['email'])}</code>",
        f"⚠️ {tg_html_escape(case['reason'])}",
        "━━━━━━━━━━━━━━",
        f"👤 <b>البائع:</b> {tg_html_escape(case['name'])}{uname} — <code>{case['uid']}</code>",
        f"📌 {_state_label(case)}",
        f"💰 المبلغ: <b>${case['amount']:.2f}</b>",
    ]
    if case["kind"] == "approved" and case["admin_id"]:
        if case["admin_id"] == OWNER_ID:
            lines.append("🛠 قبله المالك (لا مكافأة مشرف).")
        else:
            aname = tg_html_escape(case["admin_name"] or "غير معروف")
            lines.append(f"🛡 <b>المشرف:</b> {aname} — <code>{case['admin_id']}</code>")
            if money_to_cents(case["admin_bonus"]) > 0:
                lines.append(f"   مكافأته: <b>${case['admin_bonus']:.2f}</b> ({_bonus_label(case)})")
            else:
                lines.append("   لا توجد مكافأة مشرف لهذا الحساب.")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Entry points
# ═══════════════════════════════════════════════════════════════════
async def imap_check_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    context.user_data["mode"] = IMAP_AUDIT_MODE
    context.user_data.pop("step", None)
    await query.edit_message_text(
        "🔐 <b>فحص IMAP للإيميلات</b>\n\n"
        "أرسل الإيميلات <b>كرسالة</b> أو <b>كملف</b> <code>.txt</code>/<code>.csv</code>، "
        "سطر لكل حساب بالشكل:\n\n"
        "<code>email@gmail.com:abcdefghijklmnop</code>\n\n"
        "• تُقبل الفواصل <code>: | , ;</code> أو مسافة، وكلمة مرور التطبيق بمسافات أو بدونها.\n"
        f"• الحد الأقصى <b>{IMAP_AUDIT_MAX_EMAILS}</b> إيميل في الدفعة.\n"
        "• للقوائم الطويلة استخدم ملفاً (الرسالة محدودة بـ 4096 حرفاً).\n\n"
        "سيسجّل البوت الدخول لكل إيميل عبر IMAP، ثم يعرض الفاشلة مع بائعها "
        "والمشرف الذي قبلها، وتقرر أنت: خصم البائع / المشرف / الاثنين / إبقاء.\n\n"
        "<i>أو أرسل «إلغاء».</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"))


async def handle_imap_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called from text_input when mode == IMAP_AUDIT_MODE."""
    if update.effective_user.id != OWNER_ID:
        context.user_data.pop("mode", None)
        return
    text = (update.message.text or "").strip()
    if text.casefold() in {"إلغاء", "الغاء", "cancel"}:
        context.user_data.pop("mode", None)
        await update.message.reply_text("❌ تم الإلغاء.",
                                        reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"))
        return
    await _begin_scan(update, context, text)


async def handle_imap_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """MessageHandler(filters.Document.ALL). Silent unless the owner opened the menu."""
    if not update.message or not update.message.document:
        return
    if update.effective_user.id != OWNER_ID or context.user_data.get("mode") != IMAP_AUDIT_MODE:
        return
    doc = update.message.document
    if doc.file_size and doc.file_size > IMAP_AUDIT_MAX_FILE_BYTES:
        await update.message.reply_text("⚠️ الملف أكبر من 2MB. قسّمه إلى ملفات أصغر.")
        return
    try:
        tg_file = await doc.get_file()
        data = bytes(await tg_file.download_as_bytearray())
    except Exception:
        logger.exception("IMAP audit: could not download file")
        await update.message.reply_text("⚠️ تعذّر تنزيل الملف. أعد إرساله.")
        return
    await _begin_scan(update, context, _decode_bytes(data))


async def _begin_scan(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    entries, bad, dups = parse_imap_audit_input(text)
    try:  # the message holds app passwords: don't leave it in the chat
        await update.message.delete()
    except Exception:
        pass
    chat_id = update.effective_chat.id
    back = kb_single("🔙 لوحة المالك", "owner_panel")
    if not entries:
        sample = ("\n\nأسطر لم تُفهم:\n" + "\n".join(f"• {tg_html_escape(b)}" for b in bad[:5])) if bad else ""
        await context.bot.send_message(
            chat_id, "⚠️ لم أجد أي سطر بصيغة <code>email:app_password</code>." + sample,
            parse_mode=ParseMode.HTML, reply_markup=back)
        return
    if context.application.bot_data.get("imap_scan_running"):
        await context.bot.send_message(chat_id, "⏳ هناك فحص يعمل الآن. انتظر انتهاءه ثم أعد المحاولة.",
                                       reply_markup=back)
        return
    truncated = 0
    if len(entries) > IMAP_AUDIT_MAX_EMAILS:
        truncated = len(entries) - IMAP_AUDIT_MAX_EMAILS
        entries = entries[:IMAP_AUDIT_MAX_EMAILS]
    context.user_data.pop("mode", None)
    context.application.bot_data["imap_scan_running"] = True
    # Long job: run it outside the update handler so other users are not blocked.
    context.application.create_task(
        _run_scan(context, chat_id, entries, bad, dups, truncated), update=update)


async def _run_scan(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                    entries: List[Tuple[str, str]], bad: List[str], dups: int, truncated: int):
    try:
        await _run_scan_inner(context, chat_id, entries, bad, dups, truncated)
    except Exception:
        logger.exception("IMAP audit crashed")
        try:
            await context.bot.send_message(chat_id, "❌ حدث خطأ أثناء الفحص. راجع سجل الخدمة.",
                                           reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"))
        except Exception:
            pass
    finally:
        context.application.bot_data["imap_scan_running"] = False


async def _run_scan_inner(context, chat_id, entries, bad, dups, truncated):
    total = len(entries)
    progress = await context.bot.send_message(chat_id, f"⏳ جارٍ فحص IMAP… 0/{total}")
    sem = asyncio.Semaphore(IMAP_AUDIT_CONCURRENCY)
    results: List[Optional[dict]] = [None] * total
    state = {"done": 0, "last_edit": 0.0}

    async def work(i: int, email: str, app_pass: str):
        async with sem:
            try:
                res = await asyncio.wait_for(
                    verify_account_credentials(email=email, app_pass=app_pass), timeout=75)
            except asyncio.TimeoutError:
                res = {"imap_ok": False, "category": "network", "message": "انتهت مهلة الفحص"}
            except Exception:
                logger.exception("IMAP audit check crashed for %s", email)
                res = {"imap_ok": False, "category": "unknown", "message": "خطأ غير متوقع"}
            imap_rate_mark(email)
            status, reason = classify_audit_result(res)
            results[i] = {"email": email, "status": status, "reason": reason,
                          "raw": str(res.get("message", ""))}
            state["done"] += 1
            now = time.monotonic()
            if now - state["last_edit"] > 4 or state["done"] == total:
                state["last_edit"] = now
                try:
                    await progress.edit_text(f"⏳ جارٍ فحص IMAP… {state['done']}/{total}")
                except Exception:
                    pass

    await asyncio.gather(*(work(i, e, p) for i, (e, p) in enumerate(entries)))
    try:
        await progress.delete()
    except Exception:
        pass

    norm_set = {normalize_email(r["email"]) for r in results}
    index = build_email_index(norm_set)

    scan_id = secrets.token_hex(4)
    cases: List[dict] = []
    report_rows: List[str] = []
    counts = {ST_OK: 0, ST_FAILED: 0, ST_UNKNOWN: 0}
    failed_not_in_bot: List[str] = []
    failed_already_rejected: List[str] = []

    for r in results:
        counts[r["status"]] += 1
        records = index.get(normalize_email(r["email"]), [])
        active = [x for x in records if x["kind"] in ("approved", "pending")]
        seller_txt = ", ".join(f"{x['uid']}({x['kind']})" for x in active) or "-"
        admin_txt = ",".join(str(x["admin_id"]) for x in active if x["admin_id"]) or "-"
        report_rows.append(f"{r['email']} | {r['status']} | {r['reason']} | seller={seller_txt} | admin={admin_txt}")
        if r["status"] != ST_FAILED:
            continue
        if not records:
            failed_not_in_bot.append(r["email"])
            continue
        if not active:
            failed_already_rejected.append(r["email"])
            continue
        for rec in active:
            case = dict(rec)
            case.update(reason=r["reason"], done=False, chat_id=chat_id, msg_id=None)
            cases.append(case)

    scan = {"id": scan_id, "cases": cases, "chat_id": chat_id,
            "created": datetime.now(timezone.utc).isoformat()}
    _remember_scan(context, scan)

    summary = [
        "🔐 <b>نتيجة فحص IMAP</b>", "",
        f"📦 المفحوصة: <b>{total}</b>",
        f"✅ سليمة: <b>{counts[ST_OK]}</b>",
        f"❌ فاشلة: <b>{counts[ST_FAILED]}</b>",
        f"⚠️ غير محسومة (شبكة/مهلة): <b>{counts[ST_UNKNOWN]}</b>",
    ]
    if dups:
        summary.append(f"♻️ مكررة تم تجاهلها: {dups}")
    if bad:
        summary.append(f"🗑 أسطر غير مفهومة: {len(bad)}")
    if truncated:
        summary.append(f"✂️ تجاوزت الحد وتم تجاهل آخر {truncated} إيميل — أعد إرسالها بدفعة ثانية.")
    if failed_not_in_bot:
        summary.append(f"\n🚫 فاشلة لكنها <b>غير موجودة في البوت</b> ({len(failed_not_in_bot)}):")
        summary.extend(f"  • <code>{tg_html_escape(e)}</code>" for e in failed_not_in_bot[:15])
        if len(failed_not_in_bot) > 15:
            summary.append(f"  … و{len(failed_not_in_bot) - 15} أخرى (في الملف)")
    if failed_already_rejected:
        summary.append(f"\n🗂 فاشلة ومرفوضة مسبقاً في البوت ({len(failed_already_rejected)}):")
        summary.extend(f"  • <code>{tg_html_escape(e)}</code>" for e in failed_already_rejected[:15])
    if cases:
        summary.append(f"\n👇 <b>{len(cases)}</b> حساب فاشل لدى أعضاء — قرّر لكل واحد، أو استخدم الأزرار أدناه.")
    markup_rows = []
    if cases:
        markup_rows = [
            [InlineKeyboardButton("💸 خصم كل البائعين الفاشلين", callback_data=f"imapx:ALLs:{scan_id}")],
            [InlineKeyboardButton("💸🛡 خصم البائعين + المشرفين", callback_data=f"imapx:ALLb:{scan_id}")],
            [InlineKeyboardButton("✋ إبقاء الكل", callback_data=f"imapx:ALLk:{scan_id}")],
        ]
    markup_rows.append([InlineKeyboardButton("🔐 فحص دفعة جديدة", callback_data="imap_check_menu")])
    markup_rows.append([InlineKeyboardButton("🔙 لوحة المالك", callback_data="owner_panel")])
    summary_text = "\n".join(summary)
    if len(summary_text) > 4000:
        summary_text = summary_text[:3900] + "\n…"
    await context.bot.send_message(chat_id, summary_text, parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup(markup_rows))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    buf = io.BytesIO("\n".join(["email | result | reason | seller | admin"] + report_rows).encode("utf-8"))
    buf.name = f"imap_check_{stamp}.txt"
    try:
        await context.bot.send_document(chat_id, buf, caption="📄 التقرير الكامل (بدون كلمات المرور)")
    except Exception:
        logger.exception("Could not send IMAP audit report file")

    for idx, case in enumerate(cases[:IMAP_AUDIT_MAX_CARDS]):
        try:
            msg = await context.bot.send_message(
                chat_id, _case_card(case), parse_mode=ParseMode.HTML,
                reply_markup=_case_keyboard(scan_id, idx, case))
            case["msg_id"] = msg.message_id
        except Exception:
            logger.exception("Could not send IMAP audit card")
        await asyncio.sleep(0.4)
    if len(cases) > IMAP_AUDIT_MAX_CARDS:
        await context.bot.send_message(
            chat_id, f"ℹ️ عُرضت أول {IMAP_AUDIT_MAX_CARDS} بطاقة فقط. الباقي ({len(cases) - IMAP_AUDIT_MAX_CARDS}) "
                     f"يدخل ضمن أزرار «الكل» أعلاه.")


# ═══════════════════════════════════════════════════════════════════
# Callbacks
# ═══════════════════════════════════════════════════════════════════
async def _alert(query, text: str):
    try:
        await query.answer(text, show_alert=True)
    except Exception:
        try:
            await query.message.reply_text(text)
        except Exception:
            pass


async def _process_case(context: ContextTypes.DEFAULT_TYPE, case: dict,
                        do_seller: bool, do_admin: bool, actor) -> dict:
    out = apply_audit_case(case, do_seller, do_admin, actor.id,
                           actor.full_name or "Owner", "فشل فحص IMAP (فحص المالك)")
    case["done"] = True
    await _notify_parties(context, case, out)
    return out


async def imap_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    actor = update.effective_user
    if actor.id != OWNER_ID:
        await _alert(query, "🚫 مالك فقط.")
        return
    data = query.data
    if data == "imap_check_menu":
        await imap_check_menu(update, context)
        return

    parts = data.split(":")
    if len(parts) < 3:
        return
    action, scan_id = parts[1], parts[2]
    scan = _scans(context).get(scan_id)
    if scan is None:
        await _alert(query, "⌛ انتهت صلاحية هذا الفحص (أُعيد تشغيل البوت). أعد الفحص من جديد.")
        return

    # ── per-account ──
    if action in ("s", "a", "b", "k") and len(parts) == 4:
        try:
            case = scan["cases"][int(parts[3])]
        except (ValueError, IndexError):
            await _alert(query, "⚠️ الحساب غير موجود.")
            return
        if case["done"]:
            await _alert(query, "ℹ️ تمت معالجة هذا الحساب مسبقاً.")
            return
        if action == "k":
            case["done"] = True
            await query.edit_message_text(
                f"✋ <b>أُبقي كما هو</b>\n📧 <code>{tg_html_escape(case['email'])}</code>",
                parse_mode=ParseMode.HTML)
            return
        out = await _process_case(context, case, do_seller=action in ("s", "b"),
                                  do_admin=action in ("a", "b"), actor=actor)
        await query.edit_message_text("✅ <b>تم التنفيذ</b>\n\n" + _result_lines(case["email"], out),
                                      parse_mode=ParseMode.HTML)
        return

    # ── bulk: ask for confirmation first ──
    open_cases = [c for c in scan["cases"] if not c["done"]]
    if action in ("ALLs", "ALLb"):
        if not open_cases:
            await _alert(query, "ℹ️ لا توجد حسابات بانتظار القرار.")
            return
        with_admin = sum(1 for c in open_cases if _admin_is_penalisable(c))
        total_seller = sum(c["amount"] for c in open_cases)
        text = (f"⚠️ <b>تأكيد الخصم الجماعي</b>\n\n"
                f"👥 حسابات البائعين: <b>{len(open_cases)}</b> (إجمالي ≈ ${total_seller:.2f})\n")
        go = "GOs"
        if action == "ALLb":
            go = "GOb"
            text += f"🛡 حسابات فيها مكافأة مشرف: <b>{with_admin}</b>\n"
        text += "\nسيتم خصم البائعين ونقل الإيميلات إلى المرفوضة. لا يمكن التراجع."
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، نفّذ", callback_data=f"imapx:{go}:{scan_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data=f"imapx:BACK:{scan_id}")],
        ]))
        return
    if action == "BACK":
        await query.edit_message_text(
            f"🔐 بانتظار قرارك: <b>{len(open_cases)}</b> حساب.",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💸 خصم كل البائعين الفاشلين", callback_data=f"imapx:ALLs:{scan_id}")],
                [InlineKeyboardButton("💸🛡 خصم البائعين + المشرفين", callback_data=f"imapx:ALLb:{scan_id}")],
                [InlineKeyboardButton("✋ إبقاء الكل", callback_data=f"imapx:ALLk:{scan_id}")],
                [InlineKeyboardButton("🔙 لوحة المالك", callback_data="owner_panel")],
            ]))
        return
    if action == "ALLk":
        for c in open_cases:
            c["done"] = True
            await _freeze_card(context, c, "✋ أُبقي كما هو")
        await query.edit_message_text(f"✋ أُبقيت {len(open_cases)} حساب كما هي.",
                                      reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"))
        return
    if action in ("GOs", "GOb"):
        if not open_cases:
            await _alert(query, "ℹ️ لا توجد حسابات بانتظار القرار.")
            return
        do_admin = action == "GOb"
        await query.edit_message_text(f"⏳ جارٍ التنفيذ على {len(open_cases)} حساب…")
        n_ok = n_missing = 0
        seller_sum = admin_sum = 0.0
        for c in open_cases:
            try:
                out = await _process_case(context, c, do_seller=True, do_admin=do_admin, actor=actor)
            except Exception:
                logger.exception("IMAP audit bulk apply failed for %s", c.get("email"))
                continue
            if not out["found"]:
                n_missing += 1
                await _freeze_card(context, c, "ℹ️ لم يعد السجل موجوداً")
                continue
            n_ok += 1
            seller_sum += out["seller_amount"]
            admin_sum += out["admin_amount"] if out["admin"] == "deducted" else 0.0
            await _freeze_card(context, c, "✅ تم الخصم")
            await asyncio.sleep(0.1)
        lines = [f"✅ <b>اكتمل التنفيذ</b>", f"👥 حسابات معالجة: <b>{n_ok}</b>",
                 f"💸 مجموع خصم البائعين: <b>${seller_sum:.2f}</b>"]
        if do_admin:
            lines.append(f"🛡 مجموع خصم المشرفين: <b>${admin_sum:.2f}</b>")
        if n_missing:
            lines.append(f"ℹ️ سجلات لم تعد موجودة: {n_missing}")
        await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                      reply_markup=kb_single("🔙 لوحة المالك", "owner_panel"))


async def _freeze_card(context: ContextTypes.DEFAULT_TYPE, case: dict, label: str):
    """Replace an account card's buttons with its outcome (best effort)."""
    if not case.get("msg_id"):
        return
    try:
        await context.bot.edit_message_text(
            chat_id=case["chat_id"], message_id=case["msg_id"], parse_mode=ParseMode.HTML,
            text=f"{label}\n📧 <code>{tg_html_escape(case['email'])}</code>")
    except Exception:
        pass


__all__ = [name for name in globals() if not name.startswith("__")]
