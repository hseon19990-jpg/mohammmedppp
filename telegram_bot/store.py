"""Purchase channels, catalog, wallet, store, and referrals."""

from telegram_bot.core import *


# ==================== PURCHASE CHANNELS ====================
def purchase_channels_keyboard():
    return kb_vertical([("1️⃣ ضبط الأول", "set_purchase_channel_1"),
                        ("2️⃣ ضبط الثاني", "set_purchase_channel_2"),
                        ("🔙 إعدادات المالك", "owner_panel")])


async def purchase_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    ch1, ch2 = get_configured_purchase_channels()
    await query.edit_message_text(
        f"📨 *إشعارات الشراء*\n\n1️⃣ `{ch1 or 'غير مضبوط'}`\n2️⃣ `{ch2 or 'غير مضبوط'}`",
        parse_mode=ParseMode.MARKDOWN, reply_markup=purchase_channels_keyboard())


async def set_purchase_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_number: int):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    context.user_data["store_action"] = f"set_purchase_channel_{channel_number}"
    await query.edit_message_text(f"✏️ أرسل معرف الكروب {channel_number}:",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=purchase_channels_keyboard())


async def forced_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    current_channel = config.get("forced_channel", "")
    buttons = [("🗑️ إلغاء القناة", "remove_channel"), ("🔙 إعدادات المالك", "owner_panel")]
    await query.edit_message_text(
        f"📢 *القناة الإجبارية*\n\n📌 الحالية: `{current_channel or 'لا توجد'}`\n\n"
        "أرسل معرف قناة عامة مثل `@channel` أو رابطها.\n"
        "للقناة الخاصة أرسل المعرف الرقمي ورابط الدعوة بهذا الشكل:\n"
        "`-1001234567890 | https://t.me/+invite`",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))
    context.user_data["store_action"] = "set_channel"


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    config["forced_channel"] = ""
    config.pop("forced_channel_link", None)
    save_config(config)
    context.user_data.pop("store_action", None)
    await query.edit_message_text("✅ تم إلغاء القناة.",
                                  reply_markup=kb_single("🔙 إعدادات المالك", "owner_panel"))


# ==================== TG-LION CATALOG ====================
def _tg_lion_fetch_countries_sync() -> Tuple[Optional[List[dict]], str]:
    api_key = os.environ.get("TG_LION_API_KEY", "").strip()
    your_id = os.environ.get("TG_LION_USER_ID", "").strip()
    if not api_key or not your_id:
        return None, "not_configured"
    params = urllib.parse.urlencode({
        "action": "available_countries", "apiKey": api_key, "YourID": your_id,
    })
    request = urllib.request.Request(
        f"{TG_LION_API_URL}?{params}",
        headers={"Accept": "application/json", "User-Agent": "mohammmedppp-bot"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw_body = response.read(1024 * 1024 + 1)
        if len(raw_body) > 1024 * 1024:
            return None, "invalid_response"
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        logger.warning("TG-Lion country-list request failed (%s)", type(exc).__name__)
        return None, "provider_error"
    if not isinstance(payload, dict) or str(payload.get("status", "")).lower() != "ok":
        return None, "provider_error"
    raw_countries = payload.get("countries")
    if isinstance(raw_countries, dict):
        entries = raw_countries.items()
    elif isinstance(raw_countries, list):
        entries = [(item.get("code", ""), item) for item in raw_countries if isinstance(item, dict)]
    else:
        return None, "invalid_response"
    countries = []
    seen_codes = set()
    for fallback_code, item in entries:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or fallback_code or "").strip().upper()
        if not code or len(code) > 12 or code in seen_codes:
            continue
        try:
            source_price = Decimal(str(item.get("price")))
            if not source_price.is_finite() or source_price < 0:
                continue
            base_price_cents = money_to_cents(source_price)
            quantity = int(item.get("qty", 0))
        except (InvalidOperation, TypeError, ValueError, OverflowError):
            continue
        if quantity <= 0:
            continue
        name = str(item.get("name") or code).strip()[:120]
        raw_calling_code = next(
            (item.get(key) for key in (
                "calling_code", "callingCode", "dial_code", "dialCode",
                "phone_code", "phoneCode", "prefix",
            ) if item.get(key)),
            _TG_LION_CALLING_CODES.get(code, ""),
        )
        countries.append({
            "code": code, "name": name,
            "flag": str(item.get("flag") or tg_lion_country_flag(code)),
            "calling_code": normalize_tg_lion_calling_code(raw_calling_code),
            "qty": quantity, "base_price_cents": base_price_cents,
            "price_cents": base_price_cents + 5,
        })
        seen_codes.add(code)
    return countries, "ok"


async def get_tg_lion_countries(force_refresh: bool = False) -> Tuple[Optional[List[dict]], str]:
    now = time.monotonic()
    cached = TG_LION_COUNTRY_CACHE.get("countries")
    if not force_refresh and cached is not None and now < TG_LION_COUNTRY_CACHE.get("expires_at", 0.0):
        return cached, "ok"
    countries, status = await asyncio.to_thread(_tg_lion_fetch_countries_sync)
    if status == "ok" and countries is not None:
        TG_LION_COUNTRY_CACHE["countries"] = countries
        TG_LION_COUNTRY_CACHE["expires_at"] = time.monotonic() + TG_LION_COUNTRY_CACHE_TTL
    return countries, status


def tg_lion_country_keyboard(countries: List[dict], page: int) -> Tuple[InlineKeyboardMarkup, int]:
    page_size = 14
    total_pages = max(1, (len(countries) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size
    rows = []
    page_items = countries[start:start + page_size]
    for index in range(0, len(page_items), 2):
        row = []
        for country in page_items[index:index + 2]:
            flag = country.get("flag") or tg_lion_country_flag(country["code"])
            label = f"{flag} {country['code']} | {cents_to_money(country['price_cents']):.2f}$"
            row.append(InlineKeyboardButton(label[:64], callback_data=f"tg_lion_country:{country['code']}:{page}"))
        rows.append(row)
    for start_page in range(0, total_pages, 7):
        rows.append([
            InlineKeyboardButton(
                f"[{page_num + 1}]" if page_num == page else str(page_num + 1),
                callback_data=f"tg_lion_page:{page_num}",
            )
            for page_num in range(start_page, min(start_page + 7, total_pages))
        ])
    rows.append([InlineKeyboardButton("🔎 بحث عن الدولة", callback_data=f"tg_lion_search_start:{page}")])
    return InlineKeyboardMarkup(rows), total_pages


async def tg_lion_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    if not load_config().get("tg_lion_store_visible", True):
        if query:
            await query.answer("هذا القسم مخفي حالياً.", show_alert=True)
        return
    if query:
        await query.answer()
    countries, status = await get_tg_lion_countries()
    if status != "ok" or countries is None:
        if query:
            await query.edit_message_text("⚠️ الخدمة غير متاحة حالياً. يرجى التواصل مع الإدارة.")
        return
    if not countries:
        if query:
            await query.edit_message_text("📭 لا توجد دول متاحة حالياً.")
        return
    keyboard, total_pages = tg_lion_country_keyboard(countries, page)
    page = max(0, min(int(page), total_pages - 1))
    if query:
        await query.edit_message_text("\u2063", reply_markup=keyboard)


async def tg_lion_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    query = update.callback_query
    if not query:
        return
    if not load_config().get("tg_lion_store_visible", True):
        await query.answer("هذا القسم مخفي حالياً.", show_alert=True)
        return
    await query.answer()
    context.user_data["tg_lion_search_page"] = max(0, page)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("↩️ الرجوع إلى الدول", callback_data=f"tg_lion_search_cancel:{max(0, page)}")
    ]])
    await query.edit_message_text(
        "أرسل رمز الاتصال الدولي للدولة، مثل 964 أو +964.",
        reply_markup=keyboard,
    )


async def tg_lion_search_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    context.user_data.pop("tg_lion_search_page", None)
    await tg_lion_catalog(update, context, page)


def tg_lion_find_countries_by_calling_code(countries: List[dict], calling_code: str) -> List[dict]:
    normalized = normalize_tg_lion_calling_code(calling_code)
    return [
        country for country in countries
        if normalize_tg_lion_calling_code(
            country.get("calling_code") or _TG_LION_CALLING_CODES.get(country.get("code", "").upper(), "")
        ) == normalized
    ]


async def tg_lion_search_country_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    page = max(0, int(context.user_data.get("tg_lion_search_page", 0)))
    raw_text = update.message.text.strip()
    candidate = raw_text.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if candidate.startswith("+"):
        candidate = candidate[1:]
    elif candidate.startswith("00"):
        candidate = candidate[2:]
    if not candidate.isascii() or not candidate.isdigit() or not 1 <= len(candidate) <= 4:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("↩️ الرجوع إلى الدول", callback_data=f"tg_lion_search_cancel:{page}")
        ]])
        await update.message.reply_text(
            "أرسل رمز اتصال دولياً بالأرقام فقط، مثل 964 أو +964.",
            reply_markup=keyboard,
        )
        return
    countries, status = await get_tg_lion_countries()
    if status != "ok" or countries is None:
        await update.message.reply_text("⚠️ تعذر تحميل قائمة الدول الآن. أعد إرسال الرمز بعد قليل.")
        return
    matches = tg_lion_find_countries_by_calling_code(countries, candidate)
    context.user_data.pop("tg_lion_search_page", None)
    back_keyboard = [[InlineKeyboardButton("↩️ الرجوع إلى الدول", callback_data=f"tg_lion_search_cancel:{page}")]]
    if not matches:
        await update.message.reply_text(
            f"لم أجد دولة متاحة برمز الاتصال +{candidate}.",
            reply_markup=InlineKeyboardMarkup(back_keyboard),
        )
        return
    rows = []
    for country in matches:
        flag = country.get("flag") or tg_lion_country_flag(country["code"])
        label = f"{flag} {country['name']} | {cents_to_money(country['price_cents']):.2f}$"
        rows.append([InlineKeyboardButton(label[:64], callback_data=f"tg_lion_search_result:{country['code']}:{page}")])
    rows.extend(back_keyboard)
    await update.message.reply_text(
        f"الدول المتاحة لرمز الاتصال +{candidate}:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def tg_lion_country_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str, page: int):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    if not load_config().get("tg_lion_store_visible", True):
        if query:
            await query.answer("هذا القسم مخفي حالياً.", show_alert=True)
        return
    if query:
        await query.answer()
    countries, status = await get_tg_lion_countries()
    if status != "ok" or countries is None:
        if query:
            await query.edit_message_text("⚠️ الخدمة غير متاحة حالياً. يرجى التواصل مع الإدارة.")
        return
    country = next((item for item in countries if item["code"] == code.upper()), None)
    if not country:
        if query:
            await query.edit_message_text("⚠️ لم تعد هذه الدولة متاحة. افتح القائمة وحاول مجدداً.")
        return
    await show_tg_lion_confirmation(update, context, country, page)


async def show_tg_lion_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                     country: dict, page: int, price_changed: bool = False):
    query = update.callback_query
    if not query:
        return
    price_cents = country["price_cents"]
    context.user_data["tg_lion_confirmation"] = {
        "code": country["code"], "price_cents": price_cents,
        "page": page, "expires_at": time.time() + 600,
    }
    change_note = "⚠️ تغيّر السعر؛ راجعه ثم أكّد الطلب مجدداً.\n\n" if price_changed else ""
    text = (
        f"{change_note}تأكيد طلب رقم من <b>{html.escape(country['name'])}</b>\n\n"
        f"💰 السعر النهائي: <b>{cents_to_money(price_cents):.2f}$</b>\n"
        f"📦 المتوفر عند المزوّد: {country['qty']}\n\n"
        "سيُخصم السعر من رصيدك ويُرسل الطلب إلى قروب الطلبات للتنفيذ اليدوي."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد الطلب", callback_data=f"tg_lion_confirm:{country['code']}:{page}")],
        [InlineKeyboardButton("إلغاء الطلب", callback_data=f"tg_lion_cancel:{page}")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def tg_lion_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    context.user_data.pop("tg_lion_confirmation", None)
    await tg_lion_catalog(update, context, page)


async def tg_lion_confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str, page: int):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    if not query:
        return
    config = load_config()
    if not config.get("tg_lion_store_visible", True):
        context.user_data.pop("tg_lion_confirmation", None)
        await query.answer("هذا القسم مخفي حالياً.", show_alert=True)
        return
    confirmation = context.user_data.get("tg_lion_confirmation")
    if (not confirmation or confirmation.get("code") != code.upper()
            or confirmation.get("page") != page or confirmation.get("expires_at", 0) < time.time()):
        context.user_data.pop("tg_lion_confirmation", None)
        await query.answer("انتهت صلاحية التأكيد. اختر الدولة مجدداً.", show_alert=True)
        return
    await query.answer()
    countries, status = await get_tg_lion_countries(force_refresh=True)
    if status != "ok" or countries is None:
        await query.edit_message_text("⚠️ تعذر تحديث قائمة الدول. لم يتم خصم أي رصيد؛ حاول مجدداً لاحقاً.")
        return
    country = next((item for item in countries if item["code"] == code.upper()), None)
    if not country:
        context.user_data.pop("tg_lion_confirmation", None)
        await query.edit_message_text("⚠️ لم تعد هذه الدولة متاحة. لم يتم خصم أي رصيد.")
        return
    if country["price_cents"] != confirmation["price_cents"]:
        await show_tg_lion_confirmation(update, context, country, page, price_changed=True)
        return
    _, purchase_channel = get_configured_purchase_channels()
    if not purchase_channel:
        await query.edit_message_text("⚠️ قروب الطلبات غير مضبوط. لم يتم خصم أي رصيد؛ تواصل مع الإدارة.")
        return
    async with TG_LION_ORDER_LOCK:
        confirmation = context.user_data.get("tg_lion_confirmation")
        if (not confirmation or confirmation.get("code") != code.upper()
                or confirmation.get("page") != page or confirmation.get("expires_at", 0) < time.time()):
            await query.edit_message_text("انتهت صلاحية هذا الطلب أو تم تسجيله مسبقاً.")
            return
        user = update.effective_user
        user_data = get_user(user.id)
        total_cents = country["price_cents"]
        if spendable_balance_cents(user_data) < total_cents:
            await query.edit_message_text(
                f"❌ رصيدك غير كافٍ. رصيدك: {cents_to_money(spendable_balance_cents(user_data)):.2f}$، "
                f"السعر: {cents_to_money(total_cents):.2f}$",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("إلغاء الطلب", callback_data=f"tg_lion_cancel:{page}")]]))
            return
        snapshot = {key: (key in user_data, user_data.get(key))
                    for key in ("balance", "admin_received_balance", "spent_balance", "transactions")}
        if not debit_spendable_balance(user_data, total_cents):
            await query.edit_message_text("❌ رصيدك غير كافٍ. لم يتم تسجيل الطلب.")
            return
        add_transaction(user_data, "purchase", cents_to_money(total_cents),
                        f"حسابات لكل الدول: {country['name']} ({country['code']})")
        try:
            save_user(user.id, user_data)
        except Exception as exc:
            for key, (existed, value) in snapshot.items():
                if existed: user_data[key] = value
                else: user_data.pop(key, None)
            try: save_user(user.id, user_data)
            except Exception: pass
            await query.edit_message_text("⚠️ تعذر تسجيل الخصم. لم يتم إرسال الطلب؛ حاول مجدداً لاحقاً.")
            return
        order_ref = secrets.token_hex(4).upper()
        username = f"@{user.username}" if user.username else "لا يوجد"
        order_text = (
            "📥 <b>طلب جديد — حسابات لكل الدول</b>\n\n"
            f"🧾 رقم الطلب: <code>{order_ref}</code>\n"
            f"👤 العضو: {html.escape(user.full_name or 'غير معروف')}\n"
            f"🆔 المعرف: {html.escape(username)} | <code>{user.id}</code>\n"
            f"🌍 الدولة: <b>{html.escape(country['name'])}</b> (<code>{html.escape(country['code'])}</code>)\n"
            f"💵 سعر المزوّد المعروض: {cents_to_money(country['base_price_cents']):.2f}$\n"
            f"💳 المخصوم من العضو: {cents_to_money(total_cents):.2f}$\n"
            f"📦 الكمية المتاحة وقت الطلب: {country['qty']}\n"
            "📝 الحالة: بانتظار التنفيذ اليدوي\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        try:
            await context.bot.send_message(chat_id=purchase_channel, text=order_text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            for key, (existed, value) in snapshot.items():
                if existed: user_data[key] = value
                else: user_data.pop(key, None)
            try: save_user(user.id, user_data)
            except Exception: pass
            logger.warning("TG-Lion order receipt delivery failed (%s)", type(exc).__name__)
            await query.edit_message_text("⚠️ تعذر إرسال الطلب إلى قروب الطلبات؛ أُعيد الرصيد إن أمكن. حاول مجدداً لاحقاً.")
            return
        context.user_data.pop("tg_lion_confirmation", None)
        await query.edit_message_text(
            f"✅ تم تسجيل طلبك وإرساله إلى قروب الطلبات.\n\n"
            f"🧾 رقم الطلب: <code>{order_ref}</code>\n"
            f"🌍 الدولة: <b>{html.escape(country['name'])}</b>\n"
            f"💰 المخصوم: <b>{cents_to_money(total_cents):.2f}$</b>\n"
            "سيتم تنفيذ الطلب يدوياً من الإدارة.",
            parse_mode=ParseMode.HTML)


# ==================== WITHDRAW STORE (USER) ====================
def _withdraw_limits_display() -> str:
    """يُعيد نصاً مختصراً لعرض الحدود في رسائل الاختيار."""
    limits = get_withdraw_limits()
    parts = []
    if limits["min"] is not None:
        parts.append(f"⬇️ أدنى: ${limits['min']:.2f}")
    if limits["max"] is not None:
        parts.append(f"⬆️ أعلى: ${limits['max']:.2f}")
    if not parts:
        return ""
    return "\n📊 " + " | ".join(parts)


def _validate_withdraw_amount(total: float) -> Optional[str]:
    """يُرجع رسالة خطأ إذا المبلغ خارج الحدود، أو None إذا كان صحيحاً."""
    limits = get_withdraw_limits()
    if limits["min"] is not None and total < limits["min"]:
        return (f"❌ <b>المبلغ الإجمالي أقل من الحد الأدنى</b>\n\n"
                f"💰 الإجمالي المطلوب: <b>${total:.2f}</b>\n"
                f"⬇️ الحد الأدنى: <b>${limits['min']:.2f}</b>\n\n"
                f"📌 يرجى اختيار كمية أكبر.")
    if limits["max"] is not None and total > limits["max"]:
        return (f"❌ <b>المبلغ الإجمالي يتجاوز الحد الأعلى</b>\n\n"
                f"💰 الإجمالي المطلوب: <b>${total:.2f}</b>\n"
                f"⬆️ الحد الأعلى: <b>${limits['max']:.2f}</b>\n\n"
                f"📌 يرجى اختيار كمية أقل.")
    return None


async def withdraw_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    send_message = (query.edit_message_text if query else update.effective_message.reply_text)
    uid = update.effective_user.id
    # clear any abandoned purchase when returning to store
    if query and query.data == "withdraw_store":
        PENDING_PURCHASES.pop(uid, None)
        save_pending_purchases()
    context.user_data.pop("buy_flow", None)
    user_data = get_user(uid)
    restriction = get_any_restriction_message(user_data)
    if restriction:
        await send_message(restriction, parse_mode=ParseMode.HTML,
                           reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    config = load_config()
    categories = config.get("store_categories", [])
    lion_visible = config.get("tg_lion_store_visible", True)
    if not categories and not lion_visible:
        await send_message("🛒 لا توجد فئات.",
                           reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    buttons = [(f"📂 {cat['name']}", f"user_category:{cat['id']}") for cat in categories]
    if lion_visible:
        buttons.append(("🌍 حسابات لكل الدول", "tg_lion_catalog"))
    buttons.append(("🔙 القائمة الرئيسية", "main_menu"))
    limits_text = _withdraw_limits_display()
    await send_message(f"🛒 *قسم السحب*\nاختر الفئة:{limits_text}",
                       parse_mode=ParseMode.MARKDOWN,
                       reply_markup=kb_vertical(buttons))


async def user_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    cat_id = query.data.split(":", 1)[1]
    config = load_config()
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.",
                                      reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        return
    services = category.get("services", [])
    if not services:
        await query.edit_message_text("📭 لا توجد خدمات.",
                                      reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        return
    buttons = [(f"🛒 {s['name']} - ${s['price']:.2f}", f"user_buy:{s['id']}:{cat_id}") for s in services]
    buttons.append(("🔙 قسم السحب", "withdraw_store"))
    await query.edit_message_text(f"📂 *{category['name']}*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def user_buy_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = update.effective_user.id
    user_data = get_user(uid)
    restriction = get_any_restriction_message(user_data)
    if restriction:
        await query.edit_message_text(restriction, parse_mode=ParseMode.HTML,
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    parts = query.data.split(":")
    service_id = parts[1]
    cat_id = parts[2]
    config = load_config()
    service = None
    for cat in config.get("store_categories", []):
        if cat["id"] == cat_id:
            for s in cat["services"]:
                if s["id"] == service_id:
                    service = s
                    break
            break
    if not service:
        await query.edit_message_text("⚠️ الخدمة غير موجودة.",
                                      reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        return
    unit_price = float(service.get("price", 0))
    if spendable_balance_cents(user_data) < money_to_cents(unit_price):
        await query.edit_message_text(
            f"❌ رصيدك غير كافٍ. الرصيد: ${cents_to_money(spendable_balance_cents(user_data)):.2f}, السعر: ${unit_price:.2f}")
        return

    # ═══ [جديد] سؤال عن طبيعة الرسالة المطلوبة أولاً ═══
    context.user_data["buy_flow"] = {
        "service_id": service_id,
        "service_name": service.get("name", ""),
        "service_price": unit_price,
        "service_message": service.get("message", "شكراً!"),
        "cat_id": cat_id,
        "purchased_at": datetime.now().isoformat(),
    }
    await _ask_message_type(update, context, service)
    return


# ═══════════════════════════════════════════════════════════════════
# ═══ [جديد] طبيعة الرسالة — أزرار اختيار النوع ═══
# ═══════════════════════════════════════════════════════════════════
MESSAGE_TYPE_LABELS = {
    "email": ("📧 إيميل", "إيميل"),
    "id": ("🆔 آيدي", "آيدي"),
    "user": ("👤 يوزر", "يوزر"),
    "phone": ("📱 رقم", "رقم"),
    "link": ("🔗 رابط", "رابط"),
    "wallet": ("💳 عنوان محفظة", "عنوان محفظة"),
}

MESSAGE_TYPE_HINTS = {
    "email": "مثال: <code>user@example.com</code>",
    "id": "مثال: <code>123456789</code>",
    "user": "مثال: <code>@username</code> أو <code>username</code>",
    "phone": "مثال: <code>+9647701234567</code>",
    "link": "مثال: <code>https://t.me/example</code>",
    "wallet": "مثال: <code>T...xyz</code> أو <code>0x...abc</code>",
}


async def _ask_message_type(update: Update, context: ContextTypes.DEFAULT_TYPE, service: dict):
    """يعرض أزرار اختيار نوع المعلومات المطلوبة."""
    query = update.callback_query
    flow = context.user_data.get("buy_flow") or {}
    unit_price = float(flow.get("service_price", service.get("price", 0)))
    limits_text = _withdraw_limits_display()
    buttons = [
        ("📧 إيميل", "buy_type:email"),
        ("🆔 آيدي", "buy_type:id"),
        ("👤 يوزر", "buy_type:user"),
        ("📱 رقم", "buy_type:phone"),
        ("🔗 رابط", "buy_type:link"),
        ("💳 عنوان محفظة", "buy_type:wallet"),
        ("🔙 إلغاء", "withdraw_store"),
    ]
    await query.edit_message_text(
        f"🛒 <b>{html.escape(service.get('name', ''))}</b>\n\n"
        f"💰 سعر الوحدة: <b>${unit_price:.2f}</b>{limits_text}\n\n"
        f"📌 <b>ما نوع المعلومات التي ستُرسلها؟</b>\n"
        f"<i>اختر النوع المناسب وسيُطلب منك إرسال القيمة فقط:</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical(buttons))


async def buy_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يُستدعى عند اختيار نوع المعلومات — ينتقل لخطوة الكمية."""
    query = update.callback_query
    uid = update.effective_user.id
    msg_type = query.data.split(":", 1)[1]
    flow = context.user_data.get("buy_flow")
    if not flow:
        await query.answer("⚠️ انتهت الجلسة.", show_alert=True)
        await query.edit_message_text(
            "⚠️ انتهت الجلسة. ابدأ من جديد.",
            reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        return
    if msg_type not in MESSAGE_TYPE_LABELS:
        await query.answer("⚠️", show_alert=True)
        return
    flow["message_type"] = msg_type
    flow["step"] = "quantity"
    context.user_data["buy_flow"] = flow
    label = MESSAGE_TYPE_LABELS[msg_type][0]
    unit_price = float(flow.get("service_price", 0))
    limits_text = _withdraw_limits_display()
    await query.edit_message_text(
        f"✅ <b>نوع المعلومات:</b> {label}\n\n"
        f"🛒 <b>{html.escape(flow.get('service_name', ''))}</b>\n"
        f"💰 سعر الوحدة: <b>${unit_price:.2f}</b>{limits_text}\n\n"
        f"📌 أرسل <b>العدد</b> الذي تريده (مثال: <code>3</code>):\n\n"
        f"<i>أو 'إلغاء' للعودة</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إلغاء", "withdraw_store"))


# ==================== PURCHASE MESSAGE HANDLER (Multi-step) ====================
async def handle_purchase_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعالج رسائل الشراء (flow الجديد عبر buy_flow + القديم عبر PENDING_PURCHASES)."""
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""

    # ═══ Flow الجديد عبر buy_flow ═══
    flow = context.user_data.get("buy_flow")
    if flow:
        await _handle_buy_flow_message(update, context, flow, text)
        return

    # ═══ Flow القديم عبر PENDING_PURCHASES (للتوافق) ═══
    if user_id not in PENDING_PURCHASES:
        return
    purchase = PENDING_PURCHASES[user_id]
    step = purchase.get("step", "message")

    if text.casefold() in {"إلغاء", "الغاء", "cancel", "отмена"}:
        PENDING_PURCHASES.pop(user_id, None)
        save_pending_purchases()
        await update.message.reply_text(
            "❌ تم الإلغاء.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    user_data = get_user(user_id)
    restriction = get_any_restriction_message(user_data)
    if restriction:
        PENDING_PURCHASES.pop(user_id, None)
        save_pending_purchases()
        await update.message.reply_text(restriction, parse_mode=ParseMode.HTML,
                                        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    if step == "quantity":
        try:
            quantity = int(text)
            if quantity <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ أرسل عدداً صحيحاً أكبر من صفر.")
            return
        unit_price = float(purchase.get("service_price", 0))
        total = round(quantity * unit_price, 2)

        limit_error = _validate_withdraw_amount(total)
        if limit_error:
            await update.message.reply_text(
                limit_error,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("🔙 إلغاء", "withdraw_store"))
            return

        if spendable_balance_cents(user_data) < money_to_cents(total):
            await update.message.reply_text(
                f"❌ رصيدك غير كافٍ.\n"
                f"💰 الإجمالي المطلوب: <b>${total:.2f}</b>\n"
                f"💳 رصيدك: <b>${cents_to_money(spendable_balance_cents(user_data)):.2f}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
            PENDING_PURCHASES.pop(user_id, None)
            save_pending_purchases()
            return
        purchase["quantity"] = quantity
        purchase["total"] = total
        purchase["step"] = "message"
        PENDING_PURCHASES[user_id] = purchase
        save_pending_purchases()
        await update.message.reply_text(
            f"✅ <b>تم تحديد الكمية</b>\n\n"
            f"📦 الكمية: <b>{quantity}</b>\n"
            f"💰 الإجمالي: <b>${total:.2f}</b>\n\n"
            f"📝 <b>أرسل الآن:</b>\n"
            f"<i>ID أو @username أو رابط فقط</i>\n"
            f"⚠️ <b>بدون أي كلام عربي!</b>\n\n"
            f"<i>أو 'إلغاء' للعودة</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", "withdraw_store"))
        return

    if step == "message":
        if contains_arabic(text):
            await update.message.reply_text(
                "❌ <b>الرسالة تحتوي على نص عربي!</b>\n\n"
                "📌 <b>يجب أن تحتوي الرسالة حصراً على:</b>\n"
                "• ID (أي طول)\n"
                "• @username أو #username أو username_123\n"
                "• أو رابط\n\n"
                "❌ <b>لا يُسمح بأي كلام عربي.</b>\n\n"
                "أرسل الرسالة مجدداً بالشكل الصحيح:",
                parse_mode=ParseMode.HTML)
            return
        if not text:
            return
        await _finalize_purchase(update, context, purchase, text, user_data)


async def _handle_buy_flow_message(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    flow: dict, text: str):
    """يعالج خطوات flow الشراء الجديد."""
    user_id = update.effective_user.id
    step = flow.get("step", "quantity")

    if text.casefold() in {"إلغاء", "الغاء", "cancel", "отмена"}:
        context.user_data.pop("buy_flow", None)
        await update.message.reply_text(
            "❌ تم الإلغاء.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    user_data = get_user(user_id)
    restriction = get_any_restriction_message(user_data)
    if restriction:
        context.user_data.pop("buy_flow", None)
        await update.message.reply_text(restriction, parse_mode=ParseMode.HTML,
                                        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return

    if step == "quantity":
        try:
            quantity = int(text)
            if quantity <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ أرسل عدداً صحيحاً أكبر من صفر.")
            return
        unit_price = float(flow.get("service_price", 0))
        total = round(quantity * unit_price, 2)

        limit_error = _validate_withdraw_amount(total)
        if limit_error:
            await update.message.reply_text(
                limit_error,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("🔙 إلغاء", "withdraw_store"))
            return

        if spendable_balance_cents(user_data) < money_to_cents(total):
            await update.message.reply_text(
                f"❌ رصيدك غير كافٍ.\n"
                f"💰 الإجمالي المطلوب: <b>${total:.2f}</b>\n"
                f"💳 رصيدك: <b>${cents_to_money(spendable_balance_cents(user_data)):.2f}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
            context.user_data.pop("buy_flow", None)
            return
        flow["quantity"] = quantity
        flow["total"] = total
        flow["step"] = "message"
        context.user_data["buy_flow"] = flow
        msg_type = flow.get("message_type", "id")
        type_label = MESSAGE_TYPE_LABELS.get(msg_type, ("📝", "معلومات"))[0]
        type_hint = MESSAGE_TYPE_HINTS.get(msg_type, "")
        await update.message.reply_text(
            f"✅ <b>تم تحديد الكمية</b>\n\n"
            f"📦 الكمية: <b>{quantity}</b>\n"
            f"💰 الإجمالي: <b>${total:.2f}</b>\n"
            f"📋 نوع المعلومات: {type_label}\n\n"
            f"📝 <b>أرسل الآن {MESSAGE_TYPE_LABELS.get(msg_type, ('', 'المعلومات'))[1]}:</b>\n"
            f"{type_hint}\n\n"
            f"⚠️ <b>بدون أي كلام عربي!</b>\n\n"
            f"<i>أو 'إلغاء' للعودة</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", "withdraw_store"))
        return

    if step == "message":
        if contains_arabic(text):
            msg_type = flow.get("message_type", "id")
            type_label = MESSAGE_TYPE_LABELS.get(msg_type, ("📝", "معلومات"))[0]
            await update.message.reply_text(
                f"❌ <b>الرسالة تحتوي على نص عربي!</b>\n\n"
                f"📌 <b>المطلوب إرساله:</b> {type_label} فقط\n\n"
                f"❌ <b>لا يُسمح بأي كلام عربي.</b>\n\n"
                f"أرسل القيمة مجدداً بالشكل الصحيح:",
                parse_mode=ParseMode.HTML)
            return
        if not text:
            return
        # Validate type-specific input
        msg_type = flow.get("message_type", "id")
        err = _validate_message_type_input(msg_type, text)
        if err:
            await update.message.reply_text(err, parse_mode=ParseMode.HTML)
            return
        await _finalize_purchase(update, context, flow, text, user_data, from_flow=True)


def _validate_message_type_input(msg_type: str, text: str) -> Optional[str]:
    """يتحقق من صحة القيمة حسب النوع المختار."""
    clean = text.strip()
    if not clean:
        return "⚠️ أرسل قيمة صحيحة."
    if msg_type == "email":
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", clean):
            return ("⚠️ <b>صيغة الإيميل غير صحيحة.</b>\n"
                    "📌 مثال: <code>user@example.com</code>")
    elif msg_type == "id":
        if not re.fullmatch(r"\d{3,}", clean):
            return ("⚠️ <b>الآيدي يجب أن يكون رقماً (3 أرقام على الأقل).</b>\n"
                    "📌 مثال: <code>123456789</code>")
    elif msg_type == "user":
        if not re.fullmatch(r"@?[A-Za-z0-9_]{4,32}", clean):
            return ("⚠️ <b>صيغة اليوزر غير صحيحة.</b>\n"
                    "📌 مثال: <code>@username</code> أو <code>username</code>")
    elif msg_type == "phone":
        digits = re.sub(r"\D", "", clean)
        if not (7 <= len(digits) <= 15):
            return ("⚠️ <b>صيغة الرقم غير صحيحة.</b>\n"
                    "📌 مثال: <code>+9647701234567</code>")
    elif msg_type == "link":
        if not re.match(r"^https?://\S+$", clean, re.IGNORECASE):
            return ("⚠️ <b>صيغة الرابط غير صحيحة.</b>\n"
                    "📌 يجب أن يبدأ بـ <code>http://</code> أو <code>https://</code>")
    elif msg_type == "wallet":
        if len(clean) < 10 or len(clean) > 120:
            return ("⚠️ <b>صيغة عنوان المحفظة غير صحيحة.</b>\n"
                    "📌 يجب أن يكون طول العنوان بين 10 و120 حرفاً.")
    return None


async def _finalize_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              purchase: dict, text: str,
                              user_data: dict, from_flow: bool = False):
    """يُنفّذ الخصم ويرسل الإشعارات ويُنهي العملية."""
    user_id = update.effective_user.id
    total = float(purchase.get("total", 0))
    total_cents = money_to_cents(total)
    quantity = int(purchase.get("quantity", 1))

    limit_error = _validate_withdraw_amount(total)
    if limit_error:
        await update.message.reply_text(
            limit_error, parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", "withdraw_store"))
        if from_flow:
            context.user_data.pop("buy_flow", None)
        else:
            PENDING_PURCHASES.pop(user_id, None)
            save_pending_purchases()
        return

    if spendable_balance_cents(user_data) < total_cents:
        await update.message.reply_text(
            "❌ رصيدك غير كافٍ.",
            reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        if from_flow:
            context.user_data.pop("buy_flow", None)
        else:
            PENDING_PURCHASES.pop(user_id, None)
            save_pending_purchases()
        return
    debit_spendable_balance(user_data, total_cents)
    add_transaction(user_data, "purchase", total,
                    f"شراء: {purchase.get('service_name', '')} x{quantity}")
    save_user(user_id, user_data)

    user = update.effective_user
    user_name = user.full_name or "غير معروف"
    user_username = user.username or "لا يوجد"
    total_emails = user_data.get("total_approved_emails", 0)
    msg_type = purchase.get("message_type", "")
    type_label = MESSAGE_TYPE_LABELS.get(msg_type, ("", ""))[1] if msg_type else ""
    type_line = f"📋 نوع المعلومات: <code>{html.escape(type_label)}</code>\n" if type_label else ""

    _, purchase_channel_2 = get_configured_purchase_channels()
    if purchase_channel_2:
        text2 = (
            f"📋 <b>طلب شراء مكتمل</b>\n\n"
            f"👤 <b>{html.escape(user_name)}</b>\n"
            f"🆔 @{html.escape(user_username)}\n"
            f"🆔 <code>{user_id}</code>\n"
            f"📦 <code>{html.escape(str(purchase.get('service_name', '')))}</code>\n"
            f"{type_line}"
            f"📊 الكمية: <code>{quantity}</code>\n"
            f"💰 سعر الوحدة: <code>${float(purchase.get('service_price', 0)):.2f}</code>\n"
            f"💰 الإجمالي: <code>${total:.2f}</code>\n"
            f"📧 <code>{total_emails}</code>\n\n"
            f"📝 <b>المعلومات:</b>\n<code>{html.escape(text)}</code>\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        try:
            await context.bot.send_message(
                chat_id=purchase_channel_2, text=text2,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("✅ تم الإيصال", f"deliver_order:{user_id}"))
        except Exception:
            logger.exception("Could not send member message.")
    if OWNER_ID:
        try:
            owner_type_line = f"📋 نوع: `{type_label}`\n" if type_label else ""
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=(f"📩 *رسالة من مستخدم*\n\n"
                      f"👤 `{user_name}`\n"
                      f"🆔 @{user_username}\n"
                      f"🆔 `{user_id}`\n"
                      f"📦 `{purchase.get('service_name', '')}`\n"
                      f"{owner_type_line}"
                      f"📊 الكمية: `{quantity}`\n"
                      f"💰 `${total:.2f}`\n\n"
                      f"📝 `{text}`"),
                parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Error: {e}")

    await update.message.reply_text(
        f"✅ *تم استلام رسالتك!*\n\n"
        f"📦 الكمية: `{quantity}`\n"
        f"💰 تم خصم `${total:.2f}`.\n"
        f"📝 `{text}`\n\n"
        f"_📌 سيتم التواصل معك قريباً._",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))

    if from_flow:
        context.user_data.pop("buy_flow", None)
    else:
        PENDING_PURCHASES.pop(user_id, None)
        save_pending_purchases()


async def deliver_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    user_id = int(query.data.split(":")[1])
    user_data = get_user(user_id)
    total_emails = user_data.get("total_approved_emails", 0)
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📦 *تم استلام طلبك!*\n\n✅ سيتم التواصل معك قريباً.",
            parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Could not deliver: {e}")
    PENDING_PURCHASES.pop(user_id, None)
    save_pending_purchases()
    await query.edit_message_text(
        f"✅ *تم الإيصال!*\n\n👤 `{user_id}`\n📧 `{total_emails}`\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode=ParseMode.MARKDOWN)


# ==================== WALLET ====================
async def my_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram_bot.owner import admin_balance_stats
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    user = get_user(query.from_user.id)
    if is_admin(query.from_user.id):
        balances = admin_balance_stats(user, query.from_user.id)
        await query.edit_message_text(
            f"💰 <b>أموالي — رصيد الأدمن</b>\n\n"
            f"⏳ <b>طلبات بانتظار الموافقة:</b> ${balances['pending']:.2f}\n"
            f"🔒 <b>النقاط المقيدة (24/48 ساعة):</b> ${balances['hold']:.2f}\n"
            f"🛠 <b>الإيميلات التي أكملها الأدمن:</b> {balances['completed_count']}\n"
            f"⏳ <b>منها مكافآت مقيدة:</b> {balances['pending_count']}\n"
            f"✅ <b>منها مكافآت واصلة:</b> {balances['released_count']}\n"
            f"🛠 <b>مكافآت الأدمن المقيدة (24/48 ساعة):</b> ${balances['admin_pending']:.2f}\n"
            f"✅ <b>إجمالي مكافآت الأدمن الواصلة:</b> ${balances['received']:.2f}\n"
            f"💳 <b>المتبقي منها للاستخدام:</b> ${balances['available_admin']:.2f}\n"
            f"📊 <b>الرصيد المتاح للاستخدام:</b> ${balances['total_owned']:.2f}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    await query.edit_message_text(
        f"💰 *أموالي*\n\n"
        f"⏳ قيد الانتظار: ${float(user.get('pending_balance', 0.0)):.2f}\n"
        f"⏳ معلق (24 ساعة): ${float(user.get('hold_balance', 0.0)):.2f}\n"
        f"✅ الرصيد المملوك: ${float(user.get('balance', 0.0)):.2f}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))


# ==================== TUTORIALS ====================
async def tutorials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    config = load_config()
    buttons = []
    for key, name in [("general", "📖 شرح عام"), ("email", "📹 إنشاء إيميل"),
                      ("password", "📹 تغيير باسورد"), ("totp", "📹 إضافة 2FA"),
                      ("app_pass", "📹 كلمة مرور التطبيق"), ("leave", "📹 فيديو المغادرة"),
                      ("phone", "📱 يحتاج رقم هاتف")]:
        if config.get(f"video_{key}") and Path(config.get(f"video_{key}", "")).exists():
            buttons.append((name, f"play_video:{key}"))
    buttons.append(("🔙 القائمة الرئيسية", "main_menu"))
    await query.edit_message_text("📺 *اختر الدرس:*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def play_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    vtype = query.data.split(":")[1]
    config = load_config()
    path = config.get(f"video_{vtype}")
    video_names = {"general": "شرح عام", "email": "إنشاء إيميل", "password": "تغيير باسورد",
                   "totp": "إضافة 2FA", "app_pass": "كلمة مرور التطبيق", "leave": "المغادرة",
                   "phone": "يحتاج رقم هاتف"}
    if path and Path(path).exists():
        try:
            await context.bot.send_video(chat_id=query.from_user.id, video=open(path, "rb"),
                                         caption=f"📹 *{video_names.get(vtype, vtype)}*",
                                         parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            await tutorials(update, context)
        except Exception as e:
            logger.error(f"Error: {e}")
            await query.edit_message_text("⚠️ خطأ.",
                                          reply_markup=kb_single("🔙 التعليم", "tutorials"))
    else:
        await query.edit_message_text("⚠️ الفيديو غير موجود.",
                                      reply_markup=kb_single("🔙 التعليم", "tutorials"))


# ==================== REFERRAL ====================
def generate_referral_code():
    return secrets.token_hex(4).upper()


async def referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    user_id = update.effective_user.id
    user_data = get_user(user_id)
    referral_code = user_data.get("referral_code", "")
    if not referral_code:
        referral_code = generate_referral_code()
        user_data["referral_code"] = referral_code
        save_user(user_id, user_data)
    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={referral_code}"
    msg = (f"🔗 *نظام الإحالة*\n\n📌 *رابطك:*\n`{referral_link}`\n\n"
           f"📊 *إحصائياتك:*\n💰 المكافآت: ${float(user_data.get('referral_earnings', 0.0)):.2f}\n"
           f"👥 عدد الإحالات: {user_data.get('total_referrals', 0)}\n\n"
           f"📝 *كيف يعمل؟*\n1️⃣ شارك الرابط\n2️⃣ عند قبول حساب صديقك\n3️⃣ ستحصل على مكافأة")
    buttons = [("📋 نسخ الرابط", f"copy_referral:{referral_code}"),
               ("🔙 القائمة الرئيسية", "main_menu")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def copy_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    code = query.data.split(":")[1]
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={code}"
    buttons = [("🔗 عرض رابط الإحالة", "referral_menu"),
               ("🔙 القائمة الرئيسية", "main_menu")]
    await query.edit_message_text(f"📋 *رابطك:*\n\n`{link}`", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def referral_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    referral_bonus = config.get("referral_bonus", 0.0)
    buttons = [("💲 تغيير المكافأة", "set_referral_bonus"),
               ("📊 إحصائيات", "referral_stats"),
               ("🔙 إعدادات المالك", "owner_panel")]
    await query.edit_message_text(
        f"🔗 *إعدادات الإحالة*\n\n💰 المكافأة الحالية: ${referral_bonus:.2f}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def set_referral_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text("💰 *مكافأة الإحالة*\n\nأرسل المبلغ الجديد:",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", "referral_settings"))
    context.user_data["mode"] = "set_referral_bonus"


async def referral_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    total_referrals = 0
    total_earnings = 0.0
    top = []
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        if data.get("total_referrals", 0) > 0:
            total_referrals += data["total_referrals"]
            total_earnings += float(data.get("referral_earnings", 0.0))
            top.append({"user_id": uid, "count": data["total_referrals"],
                        "earnings": float(data.get("referral_earnings", 0.0))})
    top.sort(key=lambda x: x["count"], reverse=True)
    msg = (f"📊 *إحصائيات الإحالة*\n\n👥 الإجمالي: {total_referrals}\n"
           f"💰 المكافآت: ${total_earnings:.2f}\n\n")
    if top:
        msg += "🏆 *أفضل المحالين:*\n"
        for idx, ref in enumerate(top[:5], 1):
            msg += f"{idx}. {ref['user_id']} - {ref['count']} - ${ref['earnings']:.2f}\n"
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إعدادات الإحالة", "referral_settings"))


# ==================== STORE INPUT HANDLER (owner) ====================
async def handle_store_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram_bot.members import main_menu
    if update.effective_user.id != OWNER_ID:
        return
    text = update.message.text.strip()
    action = context.user_data.get("store_action", "")

    # ═══ Store: add category ═══
    if action == "add_category":
        config = load_config()
        if "store_categories" not in config:
            config["store_categories"] = []
        if any(cat["name"].lower() == text.lower() for cat in config["store_categories"]):
            await update.message.reply_text("⚠️ الفئة موجودة!")
            return
        config["store_categories"].append({"id": str(time.time_ns()), "name": text, "services": []})
        save_config(config)
        await update.message.reply_text(f"✅ تم إضافة الفئة: {text}")
        context.user_data.pop("store_action", None)
        await main_menu(update, context)
        return

    # ═══ Forced channel ═══
    if action == "set_channel":
        channel, invite_link = parse_forced_channel_input(text)
        if not channel:
            await update.message.reply_text(
                "⚠️ صيغة القناة غير صحيحة.\n"
                "أرسل @username أو رابط قناة عامة. للقناة الخاصة أرسل:\n"
                "-1001234567890 | https://t.me/+invite")
            return
        try:
            chat = await context.bot.get_chat(channel)
            if chat.type not in {"channel", "supergroup"}:
                await update.message.reply_text("⚠️ المعرف لا يعود إلى قناة أو مجموعة.")
                return
            bot_user = await context.bot.get_me()
            bot_member = await context.bot.get_chat_member(chat.id, bot_user.id)
            if bot_member.status not in {"administrator", "creator"}:
                await update.message.reply_text("⚠️ أضف البوت مشرفاً في القناة أولاً، ثم أعد إرسال بياناتها.")
                return
        except Exception:
            logger.exception("Could not validate the forced channel")
            await update.message.reply_text("⚠️ تعذر الوصول إلى القناة. تأكد من صحة المعرف وأن البوت مشرف فيها.")
            return
        join_link = invite_link or (f"https://t.me/{chat.username}" if getattr(chat, "username", None) else "")
        if not join_link:
            await update.message.reply_text("⚠️ هذه قناة خاصة. أرسل المعرف الرقمي مع رابط الدعوة، مفصولين بعلامة |.")
            return
        config = load_config()
        config["forced_channel"] = str(chat.id)
        config["forced_channel_link"] = join_link
        save_config(config)
        await update.message.reply_text(
            f"✅ تمت إضافة القناة الإجبارية: {chat.title}\n"
            "يجب على الأعضاء الانضمام إليها قبل استخدام البوت.")
        context.user_data.pop("store_action", None)
        await main_menu(update, context)
        return

    # ═══ Purchase channel ═══
    if action in {"set_purchase_channel_1", "set_purchase_channel_2"}:
        channel_id = normalize_chat_id(text)
        if not channel_id or not (channel_id.startswith("@") or channel_id.lstrip("-").isdigit()):
            await update.message.reply_text("⚠️ المعرف غير صحيح.")
            return
        channel_number = action.rsplit("_", 1)[1]
        config = load_config()
        config[f"purchase_channel_{channel_number}"] = channel_id
        save_config(config)
        context.user_data.pop("store_action", None)
        await update.message.reply_text(
            f"✅ تم حفظ الكروب رقم {channel_number}: {channel_id}",
            reply_markup=purchase_channels_keyboard())
        return

    # ═══ Add service: name → price → message ═══
    if action == "add_service_name":
        context.user_data["store_service_name"] = text
        context.user_data["store_action"] = "add_service_price"
        await update.message.reply_text(
            "💰 *الخطوة 2/3*: أرسل السعر:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 إلغاء",
                                   f"store_category:{context.user_data.get('current_category_id')}"))
        return
    if action == "add_service_price":
        try:
            price = float(text)
            if price <= 0:
                await update.message.reply_text("⚠️ السعر > 0!")
                return
            context.user_data["store_service_price"] = price
            context.user_data["store_action"] = "add_service_message"
            await update.message.reply_text(
                "📝 *الخطوة 3/3*: أرسل الرسالة للعميل:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_single("🔙 إلغاء",
                                       f"store_category:{context.user_data.get('current_category_id')}"))
        except ValueError:
            await update.message.reply_text("⚠️ رقم صحيح!")
        return
    if action == "add_service_message":
        name = context.user_data.get("store_service_name")
        price = context.user_data.get("store_service_price")
        cat_id = context.user_data.get("current_category_id")
        config = load_config()
        for cat in config.get("store_categories", []):
            if cat["id"] == cat_id:
                cat["services"].append({"id": str(time.time_ns()), "name": name,
                                        "price": price, "message": text})
                break
        save_config(config)
        await update.message.reply_text("✅ تم إضافة المبيعة!")
        for key in ("store_action", "store_service_name", "store_service_price", "current_category_id"):
            context.user_data.pop(key, None)
        await main_menu(update, context)
        return

    # ═══ Edit existing service: name / price / message ═══
    if action.startswith("edit_service_"):
        field = action.replace("edit_service_", "")
        cat_id = context.user_data.get("edit_service_cat")
        service_id = context.user_data.get("edit_service_id")
        if not cat_id or not service_id:
            await update.message.reply_text("⚠️ حدث خطأ. أعد المحاولة.",
                                            reply_markup=kb_single("🔙 المبيعات", "store_section"))
            context.user_data.pop("store_action", None)
            return
        config = load_config()
        updated = False
        new_value = None
        for cat in config.get("store_categories", []):
            if cat["id"] != cat_id:
                continue
            for s in cat["services"]:
                if s["id"] != service_id:
                    continue
                if field == "name":
                    s["name"] = text
                    new_value = text
                    updated = True
                elif field == "price":
                    try:
                        price = float(text)
                        if price <= 0:
                            await update.message.reply_text("⚠️ السعر > 0!")
                            return
                        s["price"] = price
                        new_value = f"${price:.2f}"
                        updated = True
                    except ValueError:
                        await update.message.reply_text("⚠️ رقم صحيح!")
                        return
                elif field == "message":
                    s["message"] = text
                    new_value = text
                    updated = True
                break
            break
        if updated:
            save_config(config)
            field_labels = {"name": "الاسم", "price": "السعر", "message": "رسالة الطلب"}
            await update.message.reply_text(
                f"✅ تم تحديث {field_labels.get(field, field)} إلى: `{new_value}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))
        else:
            await update.message.reply_text("⚠️ لم يتم العثور على المبيعة.",
                                            reply_markup=kb_single("🔙 المبيعات", "store_section"))
        for key in ("store_action", "edit_service_cat", "edit_service_id"):
            context.user_data.pop(key, None)
        return


# ==================== OWNER STORE SECTION ====================
async def owner_store_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    categories = config.get("store_categories", [])
    buttons = [(f"📂 {cat['name']}", f"store_category:{cat['id']}") for cat in categories]
    visible = config.get("tg_lion_store_visible", True)
    toggle_label = "👁️ إخفاء حسابات لكل الدول عن الأعضاء" if visible else "👁️ إظهار حسابات لكل الدول للأعضاء"
    buttons.append((toggle_label, "toggle_tg_lion_store"))
    buttons.append(("➕ إضافة فئة", "store_add_category"))
    buttons.append(("🗑️ حذف فئة", "store_delete_category_menu"))
    buttons.append(("🔙 إعدادات المالك", "owner_panel"))
    await query.edit_message_text("🛒 *إدارة المبيعات*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def toggle_tg_lion_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    visible = not config.get("tg_lion_store_visible", True)
    config["tg_lion_store_visible"] = visible
    save_config(config)
    await query.answer("✅ تم إظهار القسم للأعضاء." if visible else "✅ تم إخفاء القسم عن الأعضاء.")
    await owner_store_section(update, context)


# ═══════════════════════════════════════════════════════════════════
# ═══ [جديد] حذف فئة من قسم المبيعات ═══
# ═══════════════════════════════════════════════════════════════════
async def store_delete_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض الفئات للحذف."""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    categories = config.get("store_categories", [])
    if not categories:
        await query.edit_message_text(
            "📭 لا توجد فئات للحذف.",
            reply_markup=kb_single("🔙 المبيعات", "store_section"))
        return
    buttons = []
    for cat in categories:
        services_count = len(cat.get("services", []))
        label = f"🗑️ {cat['name']} ({services_count} خدمة)"
        buttons.append((label[:64], f"store_delete_category_confirm:{cat['id']}"))
    buttons.append(("🔙 المبيعات", "store_section"))
    await query.edit_message_text(
        "🗑️ *حذف فئة*\n\n"
        "⚠️ <b>تحذير:</b> حذف الفئة يحذف كل الخدمات داخلها نهائياً.\n\n"
        "اختر الفئة التي تريد حذفها:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_vertical(buttons))


async def store_delete_category_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض تأكيد حذف الفئة."""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    try:
        cat_id = query.data.split(":", 1)[1]
    except (IndexError, ValueError):
        await query.answer("⚠️", show_alert=True)
        return
    config = load_config()
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.answer("⚠️ الفئة غير موجودة.", show_alert=True)
        await store_delete_category_menu(update, context)
        return
    services_count = len(category.get("services", []))
    await query.edit_message_text(
        f"⚠️ <b>تأكيد الحذف</b>\n\n"
        f"📂 <b>الفئة:</b> {html.escape(category['name'])}\n"
        f"📦 <b>عدد الخدمات داخلها:</b> {services_count}\n\n"
        f"🚨 <b>سيتم حذف الفئة وكل خدماتها نهائياً!</b>\n\n"
        f"هل أنت متأكد؟",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            ("✅ نعم، احذف نهائياً", f"store_delete_category_execute:{cat_id}"),
            ("❌ إلغاء", "store_delete_category_menu"),
        ]))


async def store_delete_category_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ينفذ حذف الفئة."""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    try:
        cat_id = query.data.split(":", 1)[1]
    except (IndexError, ValueError):
        await query.answer("⚠️", show_alert=True)
        return
    config = load_config()
    before = len(config.get("store_categories", []))
    config["store_categories"] = [
        c for c in config.get("store_categories", []) if c["id"] != cat_id
    ]
    after = len(config["store_categories"])
    if before == after:
        await query.answer("⚠️ لم يتم العثور على الفئة.", show_alert=True)
        await store_delete_category_menu(update, context)
        return
    save_config(config)
    await query.answer("✅ تم حذف الفئة.", show_alert=True)
    await owner_store_section(update, context)


# ═══════════════════════════════════════════════════════════════════
# ═══ إضافة فئة / إضافة خدمة ═══
# ═══════════════════════════════════════════════════════════════════
async def store_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await query.edit_message_text("✏️ *اسم الفئة:*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", "store_section"))
    context.user_data["store_action"] = "add_category"


async def store_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    config = load_config()
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.",
                                      reply_markup=kb_single("🔙 المبيعات", "store_section"))
        return
    services = category.get("services", [])
    msg = f"📂 *{category['name']}*\n\n"
    if services:
        for idx, s in enumerate(services, 1):
            msg += f"{idx}. 🛒 {s['name']} - 💰 ${s['price']:.2f}\n"
    else:
        msg += "📭 لا توجد مبيعات.\n"
    buttons = [("➕ إضافة", f"store_add_service:{cat_id}"),
               ("✏️ تعديل", f"store_edit_service:{cat_id}"),
               ("🗑️ حذف", f"store_delete_service:{cat_id}"),
               ("🔙 المبيعات", "store_section")]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def store_add_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    context.user_data["current_category_id"] = cat_id
    context.user_data["store_action"] = "add_service_name"
    await query.edit_message_text("✏️ *اسم المبيعة:*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))


async def store_edit_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    config = load_config()
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.")
        return
    services = category.get("services", [])
    if not services:
        await query.edit_message_text("📭 لا توجد مبيعات.",
                                      reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))
        return
    buttons = [(f"✏️ {s['name']} - ${s['price']:.2f}", f"store_edit_field:pick:{cat_id}:{s['id']}") for s in services]
    buttons.append(("🔙 الفئة", f"store_category:{cat_id}"))
    await query.edit_message_text("✏️ *اختر المبيعة للتعديل:*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def store_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    if len(parts) < 4:
        await query.answer("⚠️", show_alert=True)
        return
    field = parts[1]
    cat_id = parts[2]
    service_id = parts[3]

    config = load_config()
    service = None
    for cat in config.get("store_categories", []):
        if cat["id"] != cat_id:
            continue
        for s in cat["services"]:
            if s["id"] == service_id:
                service = s
                break
        break
    if not service:
        await query.edit_message_text("⚠️ المبيعة غير موجودة.",
                                      reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))
        return

    if field == "pick":
        buttons = [
            (f"✏️ الاسم الحالي: {service['name']}", f"store_edit_field:name:{cat_id}:{service_id}"),
            (f"💰 السعر الحالي: ${service['price']:.2f}", f"store_edit_field:price:{cat_id}:{service_id}"),
            (f"📝 الرسالة الحالية: {str(service.get('message',''))[:40]}", f"store_edit_field:message:{cat_id}:{service_id}"),
            ("🔙 الفئة", f"store_category:{cat_id}"),
        ]
        await query.edit_message_text(
            f"✏️ *تعديل المبيعة:* `{service['name']}`\n\n"
            f"📝 الرسالة: `{service.get('message','')}`\n\n"
            "اختر ما تريد تعديله:",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))
        return

    field_labels = {"name": "الاسم الجديد", "price": "السعر الجديد", "message": "رسالة الطلب الجديدة"}
    context.user_data["store_action"] = f"edit_service_{field}"
    context.user_data["edit_service_cat"] = cat_id
    context.user_data["edit_service_id"] = service_id
    await query.edit_message_text(
        f"✏️ *تعديل {field_labels.get(field, field)}*\n\n"
        f"للمبيعة: `{service['name']}`\n\n"
        f"📌 أرسل القيمة الجديدة:\n\n"
        f"<i>أو 'إلغاء'</i>",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))


async def store_delete_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    config = load_config()
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.")
        return
    services = category.get("services", [])
    if not services:
        await query.edit_message_text("📭 لا توجد مبيعات.",
                                      reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))
        return
    buttons = [(f"❌ {s['name']} - ${s['price']:.2f}", f"delete_service:{cat_id}:{s['id']}") for s in services]
    buttons.append(("🔙 الفئة", f"store_category:{cat_id}"))
    await query.edit_message_text("🗑️ *حذف مبيعة*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def delete_service_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    parts = query.data.split(":")
    cat_id = parts[1]
    service_id = parts[2]
    config = load_config()
    for cat in config.get("store_categories", []):
        if cat["id"] == cat_id:
            cat["services"] = [s for s in cat["services"] if s["id"] != service_id]
            break
    save_config(config)
    await query.edit_message_text("✅ تم الحذف.",
                                  reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))


# ==================== REFERRAL HANDLER ====================
async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE, referral_code: str):
    user_id = update.effective_user.id
    if context.user_data.get("my_referral_code") == referral_code:
        await update.message.reply_text("⚠️ لا يمكنك استخدام رابطك!")
        return
    user_data = get_user(user_id)
    if user_data.get("referred_by"):
        await update.message.reply_text("ℹ️ أنت مشترك بالفعل.")
        return
    users = load_json(USERS_DB)
    referrer_id = None
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        if data.get("referral_code") == referral_code:
            referrer_id = int(uid)
            break
    if not referrer_id:
        await update.message.reply_text("❌ رابط غير صالح.")
        return
    user_data["referred_by"] = referrer_id
    save_user(user_id, user_data)
    await update.message.reply_text(
        f"✅ *تم تفعيل الإحالة!*\n\n👤 بواسطة: {referrer_id}\nاستخدم /start للبدء.",
        parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.send_message(
            chat_id=referrer_id,
            text=f"🎉 *إحالة جديدة!*\n\n👤 المستخدم {user_id} انضم.",
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass


__all__ = [name for name in globals() if not name.startswith("__")]
