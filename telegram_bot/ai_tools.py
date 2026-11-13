"""أدوات مساعد الذكاء الاصطناعي (بدون استدعاء نموذج):
1) تتبّع إيميل العضو وشرح سبب عدم وصول نقاطه.
2) كشف طلب التحدث مع إنسان/أدمن وإرسال زر التواصل.
3) حقائق الثقة (إحصاءات حقيقية من قاعدة البيانات) لسؤال «هل أثق بالمالك؟».

أمان: التتبّع يقرأ سجلات العضو نفسه فقط (get_user(uid)) ولا يكشف بيانات أي عضو آخر.
"""

import re

from telegram_bot.core import *

# ==================== كشف النوايا ====================
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]{3,}@[A-Za-z0-9\-]+\.[A-Za-z0-9.\-]{2,}")
_BARE_USER_RE = re.compile(r"^[A-Za-z0-9._]{4,40}$")

# «نقاطي/رصيدي/فلوسي لم تصل / ناقصة / وين» وما شابه (بالعربية والإنجليزية والروسية)
_MONEY = (r"نقاط|نقطة|نقاطي|رصيد|رصيدي|فلوس|فلوسي|مبلغ|أرباح|ارباح|ربحي|مكافأة|مكافاة|"
          r"points?|balance|money|payment|reward|earnings?|баллы?|баланс|деньги|оплат")
_MISSING = (r"لم\s*(?:ت|ي)صل|ما\s*(?:ت|ي)?وصل|مو\s*واصل|ماوصل|لا\s*(?:ت|ي)صل|تأخر|تاخر|ناقص|"
            r"مختف|اختف|وين|فين|أين|اين|لماذا|ليش|ليه|شلون|"
            r"didn'?t|not\s+(?:received|arrive|show|added)|missing|haven'?t|where|why|"
            r"не\s+приш|нет|почему|где")
POINTS_INTENT_RE = re.compile(rf"(?:{_MONEY}).{{0,60}}(?:{_MISSING})|(?:{_MISSING}).{{0,60}}(?:{_MONEY})",
                              re.IGNORECASE | re.DOTALL)
EMAIL_STATUS_RE = re.compile(
    r"(?:حالة|وضع|سبب|ليش|لماذا|شو\s*صار|ماذا\s*حدث|تتبع|تحقق|status|why|track|check|статус|почему|провер).{0,50}"
    r"(?:إيميل|ايميل|بريد|حساب|email|e-mail|account|gmail|почт|аккаунт)"
    r"|(?:إيميل|ايميل|بريد|حساب|email|e-mail|account|gmail|почт|аккаунт).{0,50}"
    r"(?:رفض|مرفوض|معلق|بانتظار|انتظار|مقبول|rejected|pending|accepted|declined|отклон|ожида|принят)",
    re.IGNORECASE | re.DOTALL)

_WHO = (r"بشر|بشري|انسان|إنسان|شخص|ادمن|أدمن|عادم|مشرف|ادارة|إدارة|دعم|موظف|مسؤول|مسئول|"
        r"human|real\s*person|person|admin|agent|support|operator|staff|moderator|"
        r"человек|оператор|админ|поддержк|сотрудник|живой")
_ASK = (r"اتواصل|أتواصل|تواصل|اتكلم|أتكلم|تكلم|كلم|احكي|أحكي|اريد|أريد|ابي|أبي|ابغى|أبغى|بدي|عايز|"
        r"محتاج|ممكن|اعطني|أعطني|وصلني|حولني|حوّلني|خلني|"
        r"talk|speak|contact|connect|transfer|reach|chat|need|want|can\s+i|"
        r"хочу|свяж|поговор|нужен|соедин")
HUMAN_RE = re.compile(
    rf"(?:{_ASK}).{{0,40}}(?:{_WHO})|(?:{_WHO}).{{0,25}}(?:{_ASK})|"
    r"مو\s*بوت|مش\s*بوت|ماني\s*بوت|مو\s*ذكاء|مب\s*بوت|not\s+a\s+bot|не\s+бот",
    re.IGNORECASE | re.DOTALL)

# سؤال الثقة: كلمة ثقة + مستهدف (مالك/بوت/إدارة)
_TRUST_WORD = (r"وثوق|وثق|أثق|اثق|ثقة|موثوق|مصداق|نصب|نصاب|نصّاب|احتيال|حرامي|مضمون|أمان|امان|آمن|"
               r"trust|legit|scam|fraud|reliable|honest|safe|genuine|real\b|"
               r"довер|мошенн|надёжн|надежн|обман|честн")
_TRUST_TARGET = (r"مالك|صاحب|ادارة|إدارة|ادمن|أدمن|بوت|هذا\s*المكان|"
                 r"owner|admin|bot|you|this\s+(?:service|place)|"
                 r"владел|админ|бот")
_TRUST_WORD_RE = re.compile(_TRUST_WORD, re.IGNORECASE)
_TRUST_TARGET_RE = re.compile(_TRUST_TARGET, re.IGNORECASE)


def is_human_request(text: str) -> bool:
    return bool(HUMAN_RE.search(text or ""))


def is_trust_question(text: str) -> bool:
    text = text or ""
    return bool(_TRUST_WORD_RE.search(text) and _TRUST_TARGET_RE.search(text))


def extract_emails(text: str, allow_bare: bool = False) -> List[str]:
    """يستخرج حتى 3 إيميلات. allow_bare: يقبل اسم مستخدم فقط (مثل john.smith) ويضيف gmail.com."""
    found = []
    for m in EMAIL_RE.findall(text or ""):
        e = normalize_email(m)
        if e not in found:
            found.append(e)
    if not found and allow_bare:
        token = (text or "").strip()
        if _BARE_USER_RE.match(token):
            found.append(normalize_email(token + "@gmail.com"))
    return found[:3]


# ==================== زر التواصل مع الإدارة ====================
_SUPPORT_LABEL = {"ar": "📞 تواصل مع الدعم", "en": "📞 Contact support", "ru": "📞 Связаться с поддержкой"}
_BACK_LABEL = {"ar": "🔙 القائمة الرئيسية", "en": "🔙 Main menu", "ru": "🔙 Главное меню"}


def _lang(uid: int) -> str:
    lang = get_user_language(uid)
    return lang if lang in ("ar", "en", "ru") else "ar"


def support_keyboard(uid: int) -> InlineKeyboardMarkup:
    lang = _lang(uid)
    link = normalize_support_link(load_config().get("support_link"))
    if link:
        first = InlineKeyboardButton(_SUPPORT_LABEL[lang], url=link)
    else:
        first = InlineKeyboardButton(_SUPPORT_LABEL[lang], callback_data="support_link_unavailable")
    return InlineKeyboardMarkup([[first], [InlineKeyboardButton(_BACK_LABEL[lang], callback_data="main_menu")]])


HUMAN_TEXT = {
    "ar": "تمام 🤝 اضغط الزر بالأسفل وسيتواصل معك أحد أفراد الإدارة مباشرة.",
    "en": "Sure 🤝 Tap the button below and a member of the team will talk to you directly.",
    "ru": "Конечно 🤝 Нажмите кнопку ниже, и с вами свяжется сотрудник команды.",
}

ASK_EMAIL_TEXT = {
    "ar": "أكيد، أرسل لي الإيميل الذي تقصده (مثل name@gmail.com) وسأفحص لك حالته وأخبرك بالسبب 🔎",
    "en": "Sure, send me the email you mean (like name@gmail.com) and I'll check its status and tell you why 🔎",
    "ru": "Конечно, отправьте мне нужный email (например, name@gmail.com), и я проверю его статус и объясню причину 🔎",
}

# ==================== تشخيص الإيميل ====================
_T = {
    "ar": {
        "released": "✅ {email}\nمقبول، وتم تحويل نقاطك (${amount}) إلى رصيدك المتاح{when}.\nتجدها في «💰 أموالي» وفي «📜 سجل معاملاتي».",
        "hold": "⏳ {email}\nمقبول، ونقاطك (${amount}) محجوزة حالياً لمدة 24 ساعة، ثم يُجرى فحص ثانٍ للحساب.\nالمتبقي تقريباً: {remaining}. إذا نجح الفحص الثاني تتحول النقاط إلى رصيدك تلقائياً، وإذا فشل يُرفض الحساب.",
        "hold_due": "⏳ {email}\nمقبول، وموعد الفحص الثاني حان أو قارب. سيتم الفحص ثم تحويل نقاطك (${amount}) تلقائياً إذا نجح.",
        "frozen": "⏳ {email}\nمقبول، لكن تحويل نقاطك (${amount}) متوقف مؤقتاً لحين مراجعة من الإدارة. تواصل مع الدعم لمعرفة التفاصيل.",
        "pending": "🕓 {email}\nبانتظار مراجعة الإدارة{waited}. بعد القبول تُحجز النقاط (${amount}) لمدة 24 ساعة ثم تُحوَّل لرصيدك بعد الفحص الثاني. ما يلزمك أي إجراء الآن.",
        "rejected": "❌ {email}\nتم رفضه. السبب: {reason}.{attempts}",
        "rejected_second": "❌ {email}\nقُبل أولاً لكنه فشل في الفحص الثاني بعد 24 ساعة، فتم رفضه وخصم نقاطه (${amount}). لا يمكن إرسال هذا الإيميل مجدداً.",
        "blocked": "🚫 {email}\nهذا الإيميل غير مقبول للإرسال مجدداً.",
        "session": "📝 {email}\nلم يُرسل للمراجعة بعد، ما زال في خطوات الإنشاء. أكمل الخطوات من «🆕 بيع إيميل جديد» ليتم فحصه.",
        "ban": "⚠️ {email}\nلا أجد هذا الإيميل في حسابك. حسابك عليه حظر مؤقت من الإرسال حتى {until}، وعند بدء الحظر تُحذف الطلبات المعلقة. بعد انتهائه أعد إرسال الإيميل.",
        "other_member": "ℹ️ {email}\nلا أجده في سجلاتك، وهو مسجل في البوت عبر حساب آخر، لذلك لا يمكن إضافته مرة ثانية.",
        "none": "🔍 {email}\nلا أجد هذا الإيميل في سجلاتك. تأكد من كتابته بشكل صحيح، وإن كنت أرسلته من حساب تيليجرام آخر فاسألني من ذلك الحساب. إذا كنت متأكداً فتواصل مع الدعم.",
        "reasons": {"email": "إيميل غير صحيح", "password": "كلمة المرور غير صحيحة", "totp": "رمز المصادقة غير صحيح",
                     "app_pass": "كلمة مرور التطبيق غير صحيحة", "phone": "الحساب يحتاج رقم هاتف للتفعيل",
                     "other": "سبب آخر", "custom": "سبب حدده المراجع", "unknown": "غير محدد",
                     "owner_manual": "رفض من الإدارة"},
        "attempts_left": " يمكنك تصحيح البيانات وإعادة الإرسال (المحاولات المتبقية لهذا الإيميل: {n}).",
        "attempts_none": " وصل هذا الإيميل لحد الرفض المسموح، فلا يمكن إرساله مجدداً.",
        "when": " بتاريخ {d}", "waited": " منذ حوالي {h} س",
        "hours": "{h} س و{m} د", "mins": "{m} دقيقة",
        "footer": "\n\nإن بقيت لديك أي مشكلة، اضغط «تواصل مع الدعم».",
    },
    "en": {
        "released": "✅ {email}\nAccepted, and your points (${amount}) were moved to your available balance{when}.\nYou can see them in \"💰 أموالي\" and in your transaction history.",
        "hold": "⏳ {email}\nAccepted. Your points (${amount}) are on a 24-hour hold, then a second check is run on the account.\nTime left: about {remaining}. If the second check passes, the points move to your balance automatically; if it fails, the account is rejected.",
        "hold_due": "⏳ {email}\nAccepted, and the second check is due now or very soon. If it passes, your points (${amount}) are released automatically.",
        "frozen": "⏳ {email}\nAccepted, but the release of your points (${amount}) is temporarily paused pending an admin review. Contact support for details.",
        "pending": "🕓 {email}\nWaiting for admin review{waited}. After acceptance the points (${amount}) are held for 24 hours, then released after the second check. You don't need to do anything now.",
        "rejected": "❌ {email}\nRejected. Reason: {reason}.{attempts}",
        "rejected_second": "❌ {email}\nIt was accepted at first but failed the second check after 24 hours, so it was rejected and its points (${amount}) were deducted. This email can't be submitted again.",
        "blocked": "🚫 {email}\nThis email can no longer be submitted.",
        "session": "📝 {email}\nIt hasn't been submitted for review yet; it's still in the creation steps. Finish the steps from \"🆕 بيع إيميل جديد\" so it can be checked.",
        "ban": "⚠️ {email}\nI can't find this email in your account. Your account has a temporary submission ban until {until}, and pending requests are removed when a ban starts. Resend the email after it ends.",
        "other_member": "ℹ️ {email}\nIt's not in your records, and it's already registered in the bot under another account, so it can't be added again.",
        "none": "🔍 {email}\nI can't find this email in your records. Check the spelling, and if you sent it from a different Telegram account, ask me from that account. If you're sure, contact support.",
        "reasons": {"email": "incorrect email", "password": "incorrect password", "totp": "incorrect 2FA code",
                     "app_pass": "incorrect app password", "phone": "the account needs a phone number to be activated",
                     "other": "other reason", "custom": "a reason set by the reviewer", "unknown": "not specified",
                     "owner_manual": "rejected by the team"},
        "attempts_left": " You can fix the details and resend it ({n} attempts left for this email).",
        "attempts_none": " This email reached the allowed number of rejections, so it can't be sent again.",
        "when": " on {d}", "waited": " for about {h} hours",
        "hours": "{h}h {m}m", "mins": "{m} min",
        "footer": "\n\nIf you still have a problem, tap \"Contact support\".",
    },
    "ru": {
        "released": "✅ {email}\nПринят, и ваши баллы (${amount}) переведены на доступный баланс{when}.\nИх можно увидеть в «💰 أموالي» и в истории операций.",
        "hold": "⏳ {email}\nПринят. Ваши баллы (${amount}) удерживаются 24 часа, затем проводится вторая проверка аккаунта.\nОсталось примерно: {remaining}. Если вторая проверка пройдена, баллы автоматически переходят на баланс; если нет — аккаунт отклоняется.",
        "hold_due": "⏳ {email}\nПринят, вторая проверка вот-вот будет выполнена. Если она пройдена, баллы (${amount}) будут выплачены автоматически.",
        "frozen": "⏳ {email}\nПринят, но выплата баллов (${amount}) временно приостановлена до проверки администрацией. Свяжитесь с поддержкой.",
        "pending": "🕓 {email}\nОжидает проверки администрацией{waited}. После принятия баллы (${amount}) удерживаются 24 часа, затем выплачиваются после второй проверки. Сейчас ничего делать не нужно.",
        "rejected": "❌ {email}\nОтклонён. Причина: {reason}.{attempts}",
        "rejected_second": "❌ {email}\nСначала был принят, но не прошёл вторую проверку через 24 часа, поэтому отклонён, а баллы (${amount}) списаны. Этот email нельзя отправить повторно.",
        "blocked": "🚫 {email}\nЭтот email больше нельзя отправлять.",
        "session": "📝 {email}\nЕщё не отправлен на проверку, он на шагах создания. Завершите шаги в «🆕 بيع إيميل جديد», чтобы его проверили.",
        "ban": "⚠️ {email}\nНе нахожу этот email в вашем аккаунте. У вас временный запрет на отправку до {until}; при начале запрета ожидающие заявки удаляются. После окончания отправьте email снова.",
        "other_member": "ℹ️ {email}\nЕго нет в ваших записях, он уже зарегистрирован в боте через другой аккаунт, поэтому добавить его повторно нельзя.",
        "none": "🔍 {email}\nНе нахожу этот email в ваших записях. Проверьте написание; если отправляли с другого аккаунта Telegram, спросите оттуда. Если уверены — свяжитесь с поддержкой.",
        "reasons": {"email": "неверный email", "password": "неверный пароль", "totp": "неверный код 2FA",
                     "app_pass": "неверный пароль приложения", "phone": "аккаунту нужен номер телефона",
                     "other": "другая причина", "custom": "причина указана проверяющим", "unknown": "не указана",
                     "owner_manual": "отклонён администрацией"},
        "attempts_left": " Можно исправить данные и отправить снова (осталось попыток для этого email: {n}).",
        "attempts_none": " Для этого email исчерпан лимит отклонений, повторно отправить нельзя.",
        "when": " {d}", "waited": " около {h} ч.",
        "hours": "{h} ч {m} мин", "mins": "{m} мин",
        "footer": "\n\nЕсли проблема осталась, нажмите «Связаться с поддержкой».",
    },
}

MAX_REJECTIONS_PER_EMAIL = 3


def _fmt_dt(value: Any) -> str:
    dt = parse_iso_datetime(value) if isinstance(value, str) else None
    return dt.strftime("%Y-%m-%d") if dt else ""


def _same(rec: dict, email: str) -> bool:
    return normalize_email(rec.get("email", "")) == email


def _amount(rec: dict) -> str:
    try:
        return f"{float(rec.get('released_amount') or rec.get('amount') or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def diagnose_email(uid: int, email: str) -> str:
    """يشرح حالة إيميل واحد من سجلات العضو نفسه. نص عادي جاهز للإرسال (بلغة العضو)."""
    lang = _lang(uid)
    L = _T[lang]
    email = normalize_email(email)
    user_data = get_user(uid)
    now = datetime.now(timezone.utc)

    # 1) مقبول (ضمن النقاط)
    approved = [a for a in (user_data.get("approved_accounts") or []) if _same(a, email) and not a.get("rejected_at_24h")]
    if approved:
        acc = approved[-1]
        amount = _amount(acc)
        if acc.get("frozen"):
            return L["frozen"].format(email=email, amount=amount)
        if acc.get("leave_confirmed") or not acc.get("approved_with_leave"):
            d = _fmt_dt(acc.get("confirmed_at") or acc.get("approval_time") or acc.get("timestamp"))
            return L["released"].format(email=email, amount=amount, when=L["when"].format(d=d) if d else "")
        release_at = parse_iso_datetime(acc.get("release_at"))
        if release_at and release_at > now:
            secs = int((release_at - now).total_seconds())
            h, m = secs // 3600, (secs % 3600) // 60
            remaining = L["hours"].format(h=h, m=m) if h else L["mins"].format(m=max(m, 1))
            return L["hold"].format(email=email, amount=amount, remaining=remaining)
        return L["hold_due"].format(email=email, amount=amount)

    # 2) بانتظار مشرف
    pending = [r for r in (user_data.get("pending_requests") or []) if _same(r, email)]
    if pending:
        req = pending[-1]
        ts = parse_iso_datetime(req.get("timestamp"))
        waited = ""
        if ts:
            hours = int((now - ts).total_seconds() // 3600)
            if hours >= 1:
                waited = L["waited"].format(h=hours)
        return L["pending"].format(email=email, amount=_amount(req), waited=waited)

    # 3) مرفوض
    rejected_all = [r for r in (user_data.get("rejected_requests") or []) if _same(r, email)]
    if rejected_all:
        last = rejected_all[-1]
        if last.get("reject_reason") == "imap_failed_24h" or last.get("rejected_at_24h"):
            return L["rejected_second"].format(email=email, amount=_amount(last))
        count = len([r for r in rejected_all if not r.get("imap_auto_rejected")])
        left = MAX_REJECTIONS_PER_EMAIL - count
        attempts = L["attempts_left"].format(n=left) if left > 0 else L["attempts_none"]
        reason = L["reasons"].get(last.get("reject_reason", "unknown"), L["reasons"]["unknown"])
        if last.get("superseded"):
            # أُعيد إرساله لاحقاً ثم اختفى (غالباً بسبب حظر حذف الطلبات)
            pass
        else:
            return L["rejected"].format(email=email, reason=reason, attempts=attempts)

    # 4) محظور نهائياً
    try:
        if is_email_blocked(email):
            return L["blocked"].format(email=email)
    except Exception:
        pass

    # 5) إيميل جديد لم يكتمل
    for s in (user_data.get("sell_new_sessions") or []):
        if normalize_email(s.get("email", "")) == email:
            return L["session"].format(email=email)

    # 6) حظر نشط حذف الطلبات المعلقة
    ban_until = parse_iso_datetime(user_data.get("ban_until") or "")
    if ban_until and ban_until > now:
        return L["ban"].format(email=email, until=ban_until.strftime("%Y-%m-%d %H:%M UTC"))

    # 7) مسجل عند عضو آخر (دون كشف أي تفاصيل عنه)
    try:
        if get_active_account_status(email):
            return L["other_member"].format(email=email)
    except Exception:
        logger.exception("ai email diagnose: other-member lookup failed")

    return L["none"].format(email=email)


def diagnose_emails(uid: int, emails: List[str]) -> str:
    L = _T[_lang(uid)]
    return "\n\n".join(diagnose_email(uid, e) for e in emails) + L["footer"]


# ==================== حقائق الثقة (حقيقية فقط) ====================
def _floor_nice(value: float) -> int:
    """يقرّب للأسفل لرقم مقروء (لا نبالغ أبداً): 137 -> 130، 1280 -> 1200."""
    v = int(value)
    if v < 20:
        return v
    step = 10 if v < 100 else 50 if v < 1000 else 100
    return (v // step) * step


def trust_facts() -> str:
    """يبني نصاً بالحقائق الحقيقية من قاعدة البيانات. لا يخترع شيئاً ولا يبالغ."""
    lines = []
    try:
        users = load_json_readonly(USERS_DB)
        withdrawn, withdrawers, approved_total = 0.0, 0, 0
        for uid, u in users.items():
            if not isinstance(u, dict):
                continue
            try:
                spent = float(u.get("spent_balance", 0) or 0)
                approved_total += int(u.get("total_approved_emails", 0) or 0)
            except (TypeError, ValueError):
                continue
            if spent > 0 and str(uid) != str(OWNER_ID):
                withdrawn += spent
                withdrawers += 1
        if withdrawers:
            lines.append(f"Members have already withdrawn/spent more than ${_floor_nice(withdrawn)} in total "
                         f"(real, from the bot's own records), across {_floor_nice(withdrawers)}+ members.")
        if approved_total:
            lines.append(f"More than {_floor_nice(approved_total)} member emails have been accepted so far.")
    except Exception:
        logger.exception("trust_facts failed")
    return "\n".join(lines)
