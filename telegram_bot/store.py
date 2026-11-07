"""Purchase channels, catalog, wallet, store, crypto withdrawals, and referrals."""

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


def forced_channels_panel_text(channels: List[dict]) -> str:
    if channels:
        listing = "\n".join(f"{i}. `{item['channel']}`" for i, item in enumerate(channels, 1))
    else:
        listing = "لا توجد قنوات."
    return (f"📢 *القنوات الإجبارية* ({len(channels)}/{MAX_FORCED_CHANNELS})\n\n{listing}\n\n"
            "كل الأعضاء (عدا المالك) يجب أن ينضموا لكل هذه القنوات.")


def forced_channels_panel_keyboard(channels: List[dict]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"🗑 حذف {item['channel']}", callback_data=f"remove_channel:{i}")]
            for i, item in enumerate(channels)]
    rows.append([InlineKeyboardButton("➕ إضافة قناة", callback_data="add_forced_channel")])
    rows.append([InlineKeyboardButton("🔙 إعدادات المالك", callback_data="owner_panel")])
    return InlineKeyboardMarkup(rows)


async def forced_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    context.user_data.pop("store_action", None)
    channels = get_forced_channels()
    await query.edit_message_text(
        forced_channels_panel_text(channels), parse_mode=ParseMode.MARKDOWN,
        reply_markup=forced_channels_panel_keyboard(channels))


async def add_forced_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    if len(get_forced_channels()) >= MAX_FORCED_CHANNELS:
        await query.answer("وصلت للحد الأقصى من القنوات.", show_alert=True)
        return
    context.user_data["store_action"] = "set_channel"
    await query.edit_message_text(
        "أرسل معرف قناة عامة مثل `@channel` أو رابطها.\n"
        "للقناة الخاصة أرسل المعرف الرقمي ورابط الدعوة بهذا الشكل:\n"
        "`-1001234567890 | https://t.me/+invite`\n\n"
        "⚠️ يجب أن يكون البوت مشرفاً في القناة.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 رجوع", "forced_channel"))


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    channels = get_forced_channels(config)
    _, _, raw_index = (query.data or "").partition(":")
    if raw_index.isdigit() and int(raw_index) < len(channels):
        channels.pop(int(raw_index))
    elif not raw_index:
        channels = []  # زر قديم: إلغاء كل القنوات
    set_forced_channels(config, channels)
    save_config(config)
    context.user_data.pop("store_action", None)
    await query.answer("✅ تم الحذف.")
    await query.edit_message_text(
        forced_channels_panel_text(channels), parse_mode=ParseMode.MARKDOWN,
        reply_markup=forced_channels_panel_keyboard(channels))


# ==================== TG-LION CATALOG ====================
TG_LION_FLAT_PROFIT_CENTS = 10          # ربح ثابت: 10 سنت لكل رقم
TG_LION_PERCENT_PROFIT_THRESHOLD_CENTS = 100  # أكثر من 1$ => ربح نسبة مئوية
TG_LION_PERCENT_PROFIT = 10             # 10%


def tg_lion_final_price_cents(base_price_cents: int) -> int:
    """سعر البيع = سعر المزوّد + الربح.
    - سعر المزوّد 1$ أو أقل: +10 سنت ثابتة.
    - سعر المزوّد أكثر من 1$: +10% (تقريب لأقرب سنت للأعلى عند .5).
    """
    base = int(base_price_cents)
    if base > TG_LION_PERCENT_PROFIT_THRESHOLD_CENTS:
        profit = (base * TG_LION_PERCENT_PROFIT + 50) // 100
        return base + profit
    return base + TG_LION_FLAT_PROFIT_CENTS


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
            "price_cents": tg_lion_final_price_cents(base_price_cents),
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
        order_record = {
            "type": "tg_lion",
            "country_code": country["code"],
            "country_name": country["name"],
            "user_id": user.id,
            "user_name": user.full_name or "غير معروف",
            "user_username": user.username or "",
            "total": cents_to_money(total_cents),
            "price_cents": total_cents,
            "base_price": cents_to_money(country["base_price_cents"]),
            "quantity": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "description": f"{country['name']} ({country['code']})",
        }
        order_code = save_order_record(order_record)
        username = f"@{user.username}" if user.username else "لا يوجد"
        order_text = (
            "📥 <b>طلب جديد — حسابات لكل الدول</b>\n\n"
            f"🧾 رقم الطلب: <code>{order_code}</code>\n"
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
            await context.bot.send_message(
                chat_id=purchase_channel, text=order_text, parse_mode=ParseMode.HTML,
                reply_markup=kb_single("✅ اكتمل الطلب", f"complete_order:{order_code}"))
        except Exception as exc:
            for key, (existed, value) in snapshot.items():
                if existed: user_data[key] = value
                else: user_data.pop(key, None)
            try: save_user(user.id, user_data)
            except Exception: pass
            delete_order_record(order_code)
            logger.warning("TG-Lion order receipt delivery failed (%s)", type(exc).__name__)
            await query.edit_message_text("⚠️ تعذر إرسال الطلب إلى قروب الطلبات؛ أُعيد الرصيد إن أمكن. حاول مجدداً لاحقاً.")
            return
        context.user_data.pop("tg_lion_confirmation", None)
        await query.edit_message_text(
            f"✅ تم تسجيل طلبك وإرساله إلى قروب الطلبات.\n\n"
            f"🧾 رقم الطلب: <code>{order_code}</code>\n"
            f"🌍 الدولة: <b>{html.escape(country['name'])}</b>\n"
            f"💰 المخصوم: <b>{cents_to_money(total_cents):.2f}$</b>\n"
            "سيتم تنفيذ الطلب يدوياً من الإدارة.",
            parse_mode=ParseMode.HTML)


# ==================== CONSTANTS: MESSAGE TYPES ====================
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


def _validate_message_input_by_types(types: list, values: dict) -> Optional[str]:
    for t in types:
        clean = (values.get(t) or "").strip()
        if not clean:
            return f"⚠️ لم تُدخل قيمة صحيحة للنوع: {MESSAGE_TYPE_LABELS[t][1]}"
        if t == "email":
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", clean):
                return "⚠️ <b>صيغة الإيميل غير صحيحة.</b>"
        elif t == "id":
            if not re.fullmatch(r"\d{3,}", clean):
                return "⚠️ <b>الآيدي يجب أن يكون رقماً (3 أرقام على الأقل).</b>"
        elif t == "user":
            if not re.fullmatch(r"@?[A-Za-z0-9_]{4,32}", clean):
                return "⚠️ <b>صيغة اليوزر غير صحيحة.</b>"
        elif t == "phone":
            digits = re.sub(r"\D", "", clean)
            if not (7 <= len(digits) <= 15):
                return "⚠️ <b>صيغة الرقم غير صحيحة.</b>"
        elif t == "link":
            if not re.match(r"^https?://\S+$", clean, re.IGNORECASE):
                return "⚠️ <b>صيغة الرابط غير صحيحة (يجب أن يبدأ بـ http:// أو https://).</b>"
        elif t == "wallet":
            if len(clean) < 10 or len(clean) > 120:
                return "⚠️ <b>صيغة عنوان المحفظة غير صحيحة (10-120 حرفاً).</b>"
    return None


# ==================== CRYPTO WITHDRAWAL ====================
async def crypto_withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    user_data = get_user(uid)
    restriction = get_any_restriction_message(user_data)
    if restriction:
        await query.edit_message_text(restriction, parse_mode=ParseMode.HTML,
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    balance = spendable_balance_cents(user_data) / 100
    await query.edit_message_text(
        f"💎 <b>سحب العملات الرقمية</b>\n\n"
        f"💳 <b>رصيدك المتاح:</b> <code>${balance:.2f}</code>\n\n"
        f"اختر الشبكة/العملة التي تريد السحب عليها:\n\n"
        f"⚠️ <i>العمولة تُضاف فوق المبلغ الذي تطلبه.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=crypto_menu_keyboard())


async def crypto_pick_network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    try:
        key = query.data.split(":", 1)[1]
    except (IndexError, ValueError):
        await query.answer("⚠️", show_alert=True)
        return
    info = CRYPTO_WITHDRAWALS.get(key)
    if not info:
        await query.answer("⚠️", show_alert=True)
        return
    user_data = get_user(uid)
    balance = spendable_balance_cents(user_data) / 100
    if balance < info["min"]:
        await query.answer(
            f"⚠️ الحد الأدنى لهذه الشبكة ${info['min']:.2f} — رصيدك ${balance:.2f}",
            show_alert=True)
        return
    context.user_data["crypto_withdraw"] = {"network": key, "info": info}
    if info["needs"] == "binance_id":
        prompt = ("🆔 أرسل <b>معرّف Binance (Pay ID)</b> الخاص بك:\n"
                  "<i>مثال: 123456789</i>")
        context.user_data["crypto_withdraw"]["stage"] = "awaiting_binance_id"
    else:
        prompt = (f"📬 أرسل <b>عنوان محفظة {info['name']}</b>:\n"
                  f"<i>مثال: 0x... أو T...xyz</i>")
        context.user_data["crypto_withdraw"]["stage"] = "awaiting_wallet"
    await query.edit_message_text(
        f"{info['emoji']} <b>{info['name']}</b>\n\n"
        f"💰 الحد الأدنى: <b>${info['min']:.2f}</b>\n"
        f"💸 العمولة: <b>${info['fee']:.2f}</b>\n"
        f"💳 رصيدك: <b>${balance:.2f}</b>\n\n"
        f"{prompt}\n\n"
        f"<i>أو 'إلغاء' للعودة</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إلغاء", "crypto_withdraw_menu"))


async def crypto_handle_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    if text.casefold() in {"إلغاء", "الغاء", "cancel", "отмена"}:
        context.user_data.pop("crypto_withdraw", None)
        await update.message.reply_text("❌ تم الإلغاء.",
                                        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    if not (10 <= len(text) <= 120):
        await update.message.reply_text("⚠️ عنوان المحفظة غير صالح (10-120 حرفاً).")
        return
    state = context.user_data.get("crypto_withdraw")
    if not state:
        return
    state["target"] = text
    state["stage"] = "awaiting_amount"
    info = state["info"]
    user_data = get_user(uid)
    balance = spendable_balance_cents(user_data) / 100
    await update.message.reply_text(
        f"✅ <b>تم حفظ العنوان.</b>\n\n"
        f"💰 أرسل المبلغ الذي تريد سحبه (USDT):\n"
        f"📌 الحد الأدنى: <b>${info['min']:.2f}</b>\n"
        f"💸 العمولة: <b>${info['fee']:.2f}</b>\n"
        f"💳 رصيدك: <b>${balance:.2f}</b>\n\n"
        f"<i>مثال: 10</i>\n"
        f"<i>أو 'إلغاء' للعودة</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إلغاء", "crypto_withdraw_menu"))


async def crypto_handle_binance_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""
    if text.casefold() in {"إلغاء", "الغاء", "cancel", "отмена"}:
        context.user_data.pop("crypto_withdraw", None)
        await update.message.reply_text("❌ تم الإلغاء.",
                                        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    if not text.isdigit() or not (5 <= len(text) <= 20):
        await update.message.reply_text("⚠️ معرّف Binance يجب أن يكون رقماً (5-20 خانة).")
        return
    state = context.user_data.get("crypto_withdraw")
    if not state:
        return
    state["target"] = text
    state["stage"] = "awaiting_amount"
    info = state["info"]
    user_data = get_user(uid)
    balance = spendable_balance_cents(user_data) / 100
    await update.message.reply_text(
        f"✅ <b>تم حفظ معرّف Binance.</b>\n\n"
        f"💰 أرسل المبلغ الذي تريد سحبه (USDT):\n"
        f"📌 الحد الأدنى: <b>${info['min']:.2f}</b>\n"
        f"💸 العمولة: <b>${info['fee']:.2f}</b>\n"
        f"💳 رصيدك: <b>${balance:.2f}</b>\n\n"
        f"<i>مثال: 10</i>\n"
        f"<i>أو 'إلغاء' للعودة</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إلغاء", "crypto_withdraw_menu"))


async def crypto_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    state = context.user_data.get("crypto_withdraw")
    if not state or state.get("stage") != "awaiting_amount":
        return False
    text = update.message.text.strip() if update.message.text else ""
    if text.casefold() in {"إلغاء", "الغاء", "cancel", "отмена"}:
        context.user_data.pop("crypto_withdraw", None)
        await update.message.reply_text("❌ تم الإلغاء.",
                                        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return True
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return True
    info = state["info"]
    if amount < info["min"]:
        await update.message.reply_text(f"⚠️ الحد الأدنى ${info['min']:.2f}.")
        return True
    user_data = get_user(uid)
    balance = spendable_balance_cents(user_data) / 100
    total_deduct = round(amount + info["fee"], 2)
    if balance < total_deduct:
        await update.message.reply_text(
            f"❌ رصيدك غير كافٍ.\n"
            f"💰 المطلوب: <b>${total_deduct:.2f}</b> "
            f"(${amount:.2f} + ${info['fee']:.2f} عمولة)\n"
            f"💳 رصيدك: <b>${balance:.2f}</b>",
            parse_mode=ParseMode.HTML)
        return True
    if not debit_spendable_balance(user_data, money_to_cents(total_deduct)):
        await update.message.reply_text("❌ رصيدك غير كافٍ. لم يتم تسجيل الطلب.")
        return True
    target = state.get("target", "")
    target_label = "معرّف Binance" if info["needs"] == "binance_id" else "عنوان المحفظة"
    add_transaction(user_data, "crypto_withdraw", total_deduct,
                    f"سحب {info['name']} — مبلغ ${amount:.2f} + عمولة ${info['fee']:.2f}", "")
    save_user(uid, user_data)

    user = update.effective_user
    order_record = {
        "type": "crypto_withdraw",
        "network_key": state.get("network", ""),
        "network_name": info["name"],
        "target": target,
        "target_label": target_label,
        "user_id": uid,
        "user_name": user.full_name or "غير معروف",
        "user_username": user.username or "",
        "total": total_deduct,
        "amount": amount,
        "unit_fee": info["fee"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "description": f"سحب {info['name']} — ${amount:.2f}",
    }
    order_code = save_order_record(order_record)

    _, purchase_channel = get_configured_purchase_channels()
    order_text = (
        f"💎 <b>طلب سحب عملة رقمية</b>\n\n"
        f"🧾 <b>كود الطلب:</b> <code>{order_code}</code>\n"
        f"👤 {html.escape(user.full_name or 'غير معروف')}\n"
        f"🆔 @{html.escape(user.username or 'لا يوجد')} | <code>{uid}</code>\n"
        f"🌐 الشبكة: <b>{info['name']}</b>\n"
        f"📬 {target_label}: <code>{html.escape(target)}</code>\n"
        f"💰 المبلغ المطلوب: <b>${amount:.2f}</b>\n"
        f"💸 العمولة: <b>${info['fee']:.2f}</b>\n"
        f"💳 إجمالي المخصوم: <b>${total_deduct:.2f}</b>\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    for chat_id in filter(None, [OWNER_ID, purchase_channel]):
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=order_text, parse_mode=ParseMode.HTML,
                reply_markup=kb_single("✅ اكتمل الطلب", f"complete_order:{order_code}"))
        except Exception:
            logger.exception("Failed to send crypto withdraw order to %s", chat_id)

    await update.message.reply_text(
        f"✅ <b>تم تسجيل طلب السحب</b>\n\n"
        f"🧾 <b>كود الطلب:</b> <code>{order_code}</code>\n"
        f"🌐 {info['name']}\n"
        f"💰 المبلغ: <b>${amount:.2f}</b>\n"
        f"💸 العمولة: <b>${info['fee']:.2f}</b>\n"
        f"💳 إجمالي المخصوم: <b>${total_deduct:.2f}</b>\n"
        f"📬 {target_label}: <code>{html.escape(target)}</code>\n\n"
        f"<i>سيتم التنفيذ يدوياً خلال 24 ساعة.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
    context.user_data.pop("crypto_withdraw", None)
    return True


# ==================== MASTER & BALANCES (NEW RATE MODEL) ====================
async def master_balances_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة ماستر وأرصدة (ماستر / آسيا / أثير) — تعرض أسعاراً ديناميكية."""
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    user_data = get_user(uid)
    restriction = get_any_restriction_message(user_data)
    if restriction:
        await query.edit_message_text(restriction, parse_mode=ParseMode.HTML,
                                      reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return
    balance = spendable_balance_cents(user_data) / 100
    options = get_master_balance_config()
    buttons = []
    for key, info in options.items():
        rate_int = int(info['rate'])
        min_units = MASTER_BALANCE_MIN_UNITS
        # ✅ السعر الأساسي فقط (بدون ذكر العمولة في الزر)
        min_cost = round(min_units / info['rate'], 2)
        label = f"{info['emoji']} {info['name']} — {rate_int} وحدة=$1 | حد ${min_cost:.2f}"
        buttons.append((label[:64], f"master_pick:{key}"))
    buttons.append(("🔙 قسم السحب", "withdraw_store"))
    # ✅ عرض جميع الأسعار ديناميكياً
    rates_lines = "\n".join(
        f"   {info['emoji']} {info['name']}: <code>{int(info['rate'])} وحدة = $1</code>"
        for info in options.values()
    )
    await query.edit_message_text(
        f"🎖️ <b>ماستر وأرصدة</b>\n\n"
        f"💳 <b>رصيدك المتاح:</b> <code>${balance:.2f}</code>\n\n"
        f"💱 <b>الأسعار:</b>\n{rates_lines}\n"
        f"📦 <b>الحد الأدنى:</b> <code>{MASTER_BALANCE_MIN_UNITS} وحدة</code>\n\n"
        f"اختر ما تريد شراءه:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical(buttons))


async def master_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    try:
        key = query.data.split(":", 1)[1]
    except (IndexError, ValueError):
        await query.answer("⚠️", show_alert=True)
        return
    info = get_master_balance_config().get(key)
    if not info:
        await query.answer("⚠️", show_alert=True)
        return
    user_data = get_user(uid)
    balance = spendable_balance_cents(user_data) / 100
    min_units = MASTER_BALANCE_MIN_UNITS
    min_cost = round(min_units / info['rate'], 2)
    min_total = round(min_cost + info["fee"], 2)
    if balance < min_total:
        await query.answer(
            f"⚠️ رصيدك غير كافٍ.\nالحد الأدنى للطلب: ${min_total:.2f} "
            f"(${min_cost:.2f} + ${info['fee']:.2f} عمولة)",
            show_alert=True)
        return
    context.user_data["master_balance"] = {"key": key, "info": info, "stage": "awaiting_quantity"}
    if info["fee"] > 0:
        fee_line = f"💸 <b>العمولة (مرة واحدة):</b> <b>${info['fee']:.2f}</b>\n"
    else:
        fee_line = "💸 <b>العمولة:</b> <b>لا توجد</b>\n"
    await query.edit_message_text(
        f"{info['emoji']} <b>{info['name']}</b>\n\n"
        f"💱 <b>السعر:</b> <code>{int(info['rate'])} وحدة = $1</code>\n"
        f"📦 <b>الحد الأدنى:</b> <code>{min_units}</code> وحدة (= ${min_cost:.2f})\n"
        f"{fee_line}"
        f"💳 <b>رصيدك:</b> <b>${balance:.2f}</b>\n\n"
        f"📌 <b>أرسل عدد الوحدات التي تريد شراءها:</b>\n"
        f"<i>مثال: 1500 (=$1)</i>\n"
        f"<i>مثال: 15000 (=$10)</i>\n\n"
        f"<i>💡 العمولة تُخصم مرة واحدة فقط لكل طلب</i>\n\n"
        f"<i>أو 'إلغاء' للعودة</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إلغاء", "master_balances_menu"))


async def master_quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """يستقبل الكمية بالوحدات. العمولة تُخصم مرة واحدة فقط لكل طلب."""
    uid = update.effective_user.id
    state = context.user_data.get("master_balance")
    if not state or state.get("stage") != "awaiting_quantity":
        return False
    text = update.message.text.strip() if update.message.text else ""
    if text.casefold() in {"إلغاء", "الغاء", "cancel", "отмена"}:
        context.user_data.pop("master_balance", None)
        await update.message.reply_text("❌ تم الإلغاء.",
                                        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
        return True
    try:
        quantity = int(text)
        if quantity <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ أرسل عدداً صحيحاً أكبر من صفر.")
        return True

    if quantity < MASTER_BALANCE_MIN_UNITS:
        await update.message.reply_text(
            f"⚠️ <b>الحد الأدنى هو {MASTER_BALANCE_MIN_UNITS} وحدة</b>\n\n"
            f"📌 أرسل عدداً أكبر من أو يساوي <code>{MASTER_BALANCE_MIN_UNITS}</code>.",
            parse_mode=ParseMode.HTML)
        return True

    info = state["info"]
    key = state["key"]

    # ═══ السعر: quantity / rate + العمولة مرة واحدة ═══
    subtotal = round(quantity / info["rate"], 2)
    total = round(subtotal + info["fee"], 2)

    user_data = get_user(uid)
    if spendable_balance_cents(user_data) < money_to_cents(total):
        await update.message.reply_text(
            f"❌ رصيدك غير كافٍ.\n"
            f"💰 المطلوب: <b>${total:.2f}</b>\n"
            f"💳 رصيدك: <b>${cents_to_money(spendable_balance_cents(user_data)):.2f}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", "master_balances_menu"))
        return True
    if not debit_spendable_balance(user_data, money_to_cents(total)):
        await update.message.reply_text("❌ رصيدك غير كافٍ. لم يتم تسجيل الطلب.")
        return True

    now = datetime.now(timezone.utc)
    order = {
        "type": "master_balance",
        "balance_key": key,
        "balance_name": info["name"],
        "quantity": quantity,
        "unit_price": round(1.0 / info["rate"], 8),
        "unit_fee": info["fee"],
        "subtotal": subtotal,
        "total": total,
        "user_id": uid,
        "user_name": update.effective_user.full_name or "غير معروف",
        "user_username": update.effective_user.username or "",
        "created_at": now.isoformat(),
        "status": "pending",
        "description": f"{info['name']} × {quantity} وحدة",
    }
    order_code = save_order_record(order)
    add_transaction(user_data, "purchase", total,
                    f"شراء {info['name']} × {quantity} وحدة — كود {order_code}")
    save_user(uid, user_data)
    context.user_data.pop("master_balance", None)

    _, purchase_channel_2 = get_configured_purchase_channels()
    if info["fee"] > 0:
        fee_block = (
            f"📦 <b>العدد:</b> {quantity} وحدة\n"
            f"💵 <b>السعر:</b> {quantity} / {int(info['rate'])} = ${subtotal:.2f}\n"
            f"💸 <b>العمولة (مرة واحدة):</b> ${info['fee']:.2f}\n"
        )
    else:
        fee_block = (
            f"📦 <b>العدد:</b> {quantity} وحدة\n"
            f"💵 <b>السعر:</b> {quantity} / {int(info['rate'])} = ${subtotal:.2f}\n"
            f"💸 <b>العمولة:</b> لا توجد\n"
        )
    order_text = (
        f"🎖️ <b>طلب ماستر وأرصدة</b>\n\n"
        f"🧾 <b>كود الطلب:</b> <code>{order_code}</code>\n"
        f"👤 {html.escape(order['user_name'])}\n"
        f"🆔 @{html.escape(order['user_username'] or 'لا يوجد')} | <code>{uid}</code>\n"
        f"🎖️ <b>النوع:</b> {info['emoji']} {info['name']}\n"
        f"{fee_block}"
        f"💳 <b>الإجمالي المخصوم:</b> ${total:.2f}\n"
        f"⏰ {now.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    for chat in filter(None, [OWNER_ID, purchase_channel_2]):
        try:
            await context.bot.send_message(
                chat_id=chat, text=order_text, parse_mode=ParseMode.HTML,
                reply_markup=kb_single("✅ اكتمل الطلب", f"complete_order:{order_code}"))
        except Exception:
            logger.exception("Failed to send master balance order to %s", chat)

    if info["fee"] > 0:
        detail_line = (
            f"📦 {info['emoji']} {info['name']} × {quantity} وحدة\n"
            f"💵 {quantity} / {int(info['rate'])} = <b>${subtotal:.2f}</b>\n"
            f"💸 العمولة (مرة واحدة): <b>${info['fee']:.2f}</b>\n"
            f"💰 <b>الإجمالي المخصوم: ${total:.2f}</b>"
        )
    else:
        detail_line = (
            f"📦 {info['emoji']} {info['name']} × {quantity} وحدة\n"
            f"💰 <b>الإجمالي المخصوم: ${total:.2f}</b>"
        )
    await update.message.reply_text(
        f"✅ <b>تم استلام طلبك</b>\n\n"
        f"🧾 <b>كود الطلب:</b> <code>{order_code}</code>\n"
        f"{detail_line}\n\n"
        f"<i>سيتم التواصل معك عند اكتمال التنفيذ.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
    return True


# ==================== COMPLETE ORDER ====================
async def complete_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    actor = update.effective_user
    if not is_admin_or_owner(actor.id):
        await query.answer("🚫", show_alert=True)
        return
    try:
        code = query.data.split(":", 1)[1]
    except (IndexError, ValueError):
        await query.answer("⚠️", show_alert=True)
        return
    order = get_order_record(code)
    if not order:
        await query.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    details = format_order_details_html(order)
    if order.get("status") == "completed":
        await query.answer("ℹ️ هذا الطلب مكتمل مسبقاً.", show_alert=True)
        await query.edit_message_text(details, parse_mode=ParseMode.HTML,
                                      reply_markup=kb_single("🔙 إغلاق", "noop"))
        return
    await query.edit_message_text(
        details, parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            ("✅ تأكيد الإكمال", f"confirm_complete_order:{code}"),
            ("❌ إلغاء", "noop"),
        ]))


async def confirm_complete_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    actor = update.effective_user
    if not is_admin_or_owner(actor.id):
        await query.answer("🚫", show_alert=True)
        return
    try:
        code = query.data.split(":", 1)[1]
    except (IndexError, ValueError):
        await query.answer("⚠️", show_alert=True)
        return
    order = get_order_record(code)
    if not order:
        await query.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    if order.get("status") == "completed":
        await query.answer("ℹ️ مكتمل مسبقاً.", show_alert=True)
        return
    now = datetime.now(timezone.utc).isoformat()
    update_order_record(
        code, status="completed", completed_at=now,
        completed_by=actor.id,
        completed_by_name=actor.full_name or "",
        completed_by_username=actor.username or "")
    order = get_order_record(code) or order
    user_id = order.get("user_id")
    if user_id:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(f"✅ <b>تم إكمال طلبك</b>\n\n"
                      f"🧾 <b>كود الطلب:</b> <code>{code}</code>\n"
                      f"📝 {tg_html_escape(order.get('description', ''))}\n"
                      f"💰 المبلغ: <b>${float(order.get('total', 0)):.2f}</b>\n\n"
                      f"<i>شكراً لاستخدامك البوت.</i>"),
                parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception("Failed to notify member about order completion")
    await query.edit_message_text(
        format_order_details_html(order), parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إغلاق", "noop"))


# ==================== WITHDRAW STORE ====================
async def withdraw_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    send_message = (query.edit_message_text if query else update.effective_message.reply_text)
    uid = update.effective_user.id
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
        buttons = [("🎖️ ماستر وأرصدة", "master_balances_menu"),
                   ("💎 سحب عملات رقمية", "crypto_withdraw_menu"),
                   ("🔙 القائمة الرئيسية", "main_menu")]
        await send_message("🛒 *قسم السحب*\nاختر الفئة:",
                           parse_mode=ParseMode.MARKDOWN,
                           reply_markup=kb_vertical(buttons))
        return
    buttons = [(f"📂 {cat['name']}", f"user_category:{cat['id']}") for cat in categories]
    buttons.append(("🎖️ ماستر وأرصدة", "master_balances_menu"))
    if lion_visible:
        buttons.append(("🌍 حسابات لكل الدول", "tg_lion_catalog"))
    buttons.append(("💎 سحب عملات رقمية", "crypto_withdraw_menu"))
    buttons.append(("🔙 القائمة الرئيسية", "main_menu"))
    await send_message("🛒 *قسم السحب*\nاختر الفئة:",
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

    types = service.get("message_types") or []
    if not types:
        await query.edit_message_text(
            "⚠️ هذه الخدمة لم تُضبط بعد من المالك (لا يوجد نوع معلومات محدد).",
            reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        return

    min_qty = service.get("min_qty")
    max_qty = service.get("max_qty")
    if min_qty is None and max_qty is None:
        context.user_data["buy_flow"] = {
            "service_id": service_id,
            "service_name": service.get("name", ""),
            "service_price": unit_price,
            "service_message": service.get("message", "شكراً!"),
            "cat_id": cat_id,
            "quantity": 1,
            "total": round(unit_price, 2),
            "message_types": types,
            "collected": {},
            "step": "collect_info",
            "purchased_at": datetime.now().isoformat(),
        }
        await _ask_next_message_type(update, context)
        return

    context.user_data["buy_flow"] = {
        "service_id": service_id,
        "service_name": service.get("name", ""),
        "service_price": unit_price,
        "service_message": service.get("message", "شكراً!"),
        "cat_id": cat_id,
        "message_types": types,
        "collected": {},
        "min_qty": min_qty,
        "max_qty": max_qty,
        "step": "quantity",
        "purchased_at": datetime.now().isoformat(),
    }
    limits_text = ""
    parts_txt = []
    if min_qty is not None:
        parts_txt.append(f"أدنى: {int(min_qty)}")
    if max_qty is not None:
        parts_txt.append(f"أعلى: {int(max_qty)}")
    if parts_txt:
        limits_text = "\n📊 " + " | ".join(parts_txt)
    await query.edit_message_text(
        f"🛒 <b>{html.escape(service.get('name', ''))}</b>\n\n"
        f"💰 سعر الوحدة: <b>${unit_price:.2f}</b>{limits_text}\n\n"
        f"📌 أرسل <b>العدد</b> الذي تريده:\n\n"
        f"<i>أو 'إلغاء' للعودة</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إلغاء", "withdraw_store"))


async def _ask_next_message_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    send_msg = (query.edit_message_text if query else update.effective_message.reply_text)
    flow = context.user_data.get("buy_flow")
    if not flow:
        return
    types = flow.get("message_types", [])
    if not types:
        await _finalize_purchase(update, context, flow, from_flow=True)
        return

    qty = flow.get("quantity", 1)
    total = flow.get("total", flow.get("service_price", 0))

    if len(types) == 1:
        current_type = types[0]
        label = MESSAGE_TYPE_LABELS[current_type][0]
        hint = MESSAGE_TYPE_HINTS.get(current_type, "")
        prompt = f"📝 <b>أرسل الآن ({label}):</b>\n{hint}"
    else:
        labels_line = " أو ".join(MESSAGE_TYPE_LABELS[t][0] for t in types)
        hints_lines = "\n".join(
            f"  • {MESSAGE_TYPE_LABELS[t][1]}: {MESSAGE_TYPE_HINTS.get(t, '')}"
            for t in types
        )
        prompt = (
            f"📝 <b>أرسل أحد المعلومات التالية (واحد فقط يكفي):</b>\n"
            f"{labels_line}\n\n"
            f"{hints_lines}"
        )

    await send_msg(
        f"🛒 <b>{html.escape(flow.get('service_name', ''))}</b>\n\n"
        f"📦 الكمية: <b>{qty}</b>\n"
        f"💰 الإجمالي: <b>${float(total):.2f}</b>\n\n"
        f"{prompt}\n\n"
        f"⚠️ <b>بدون أي كلام عربي!</b>\n\n"
        f"<i>أو 'إلغاء' للعودة</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إلغاء", "withdraw_store"))


# ==================== PURCHASE MESSAGE HANDLER ====================
async def handle_purchase_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip() if update.message.text else ""

    flow = context.user_data.get("buy_flow")
    if flow:
        await _handle_buy_flow_message(update, context, flow, text)
        return

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
            f"📝 <b>أرسل الآن المعلومات:</b>\n"
            f"⚠️ <b>بدون أي كلام عربي!</b>\n\n"
            f"<i>أو 'إلغاء' للعودة</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single("🔙 إلغاء", "withdraw_store"))
        return

    if step == "message":
        if contains_arabic(text):
            await update.message.reply_text(
                "❌ <b>الرسالة تحتوي على نص عربي!</b>\n\n"
                "أرسل القيمة مجدداً بدون كلام عربي:",
                parse_mode=ParseMode.HTML)
            return
        if not text:
            return
        await _finalize_purchase(update, context, purchase, text, user_data)


async def _handle_buy_flow_message(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                    flow: dict, text: str):
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
        min_qty = flow.get("min_qty")
        max_qty = flow.get("max_qty")
        if min_qty is not None and quantity < int(min_qty):
            await update.message.reply_text(
                f"⚠️ الحد الأدنى للكمية: <b>{int(min_qty)}</b>",
                parse_mode=ParseMode.HTML)
            return
        if max_qty is not None and quantity > int(max_qty):
            await update.message.reply_text(
                f"⚠️ الحد الأعلى للكمية: <b>{int(max_qty)}</b>",
                parse_mode=ParseMode.HTML)
            return
        unit_price = float(flow.get("service_price", 0))
        total = round(quantity * unit_price, 2)
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
        flow["step"] = "collect_info"
        context.user_data["buy_flow"] = flow
        await _ask_next_message_type(update, context)
        return

    if step == "collect_info":
        types = flow.get("message_types", [])
        if not types:
            await _finalize_purchase(update, context, flow, from_flow=True)
            return
        if contains_arabic(text):
            labels = " أو ".join(MESSAGE_TYPE_LABELS[t][0] for t in types)
            await update.message.reply_text(
                f"❌ <b>الرسالة تحتوي على نص عربي!</b>\n\n"
                f"📌 المطلوب: {labels} فقط",
                parse_mode=ParseMode.HTML)
            return
        if not text:
            return

        if len(types) == 1:
            t = types[0]
            err = _validate_message_input_by_types([t], {t: text})
            if err:
                await update.message.reply_text(err, parse_mode=ParseMode.HTML)
                return
            flow["collected"] = {t: text}
        else:
            matched_type = None
            for t in types:
                if _validate_message_input_by_types([t], {t: text}) is None:
                    matched_type = t
                    break

            if not matched_type:
                labels = " أو ".join(MESSAGE_TYPE_LABELS[t][0] for t in types)
                hints_lines = "\n".join(
                    f"  • {MESSAGE_TYPE_LABELS[t][1]}: {MESSAGE_TYPE_HINTS.get(t, '')}"
                    for t in types
                )
                await update.message.reply_text(
                    f"⚠️ <b>لم يتم التعرف على نوع المعلومات المُرسلة.</b>\n\n"
                    f"📌 يمكنك إرسال: {labels}\n\n"
                    f"{hints_lines}",
                    parse_mode=ParseMode.HTML)
                return

            flow["collected"] = {matched_type: text}

        context.user_data["buy_flow"] = flow
        await _finalize_purchase(update, context, flow, from_flow=True)
        return


async def _finalize_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              purchase: dict, text: str = "",
                              user_data: Optional[dict] = None, from_flow: bool = False):
    user_id = update.effective_user.id
    total = float(purchase.get("total", 0))
    total_cents = money_to_cents(total)
    quantity = int(purchase.get("quantity", 1))

    if user_data is None:
        user_data = get_user(user_id)

    if spendable_balance_cents(user_data) < total_cents:
        await update.effective_message.reply_text(
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

    if from_flow:
        collected = purchase.get("collected", {})
        info_lines = []
        for t, v in collected.items():
            if t in MESSAGE_TYPE_LABELS:
                label = MESSAGE_TYPE_LABELS[t][1]
                info_lines.append(f"  • {label}: <code>{html.escape(str(v))}</code>")
        info_block = "\n".join(info_lines) if info_lines else "—"
    else:
        info_block = f"<code>{html.escape(text)}</code>"

    order_record = {
        "type": "store_purchase",
        "service_name": purchase.get("service_name", ""),
        "quantity": quantity,
        "unit_price": float(purchase.get("service_price", 0)),
        "total": total,
        "user_id": user_id,
        "user_name": user_name,
        "user_username": user_username,
        "info_block": info_block,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
        "description": f"{purchase.get('service_name', '')} × {quantity}",
    }
    order_code = save_order_record(order_record)

    _, purchase_channel_2 = get_configured_purchase_channels()
    if purchase_channel_2:
        text2 = (
            f"📋 <b>طلب شراء مكتمل</b>\n\n"
            f"🧾 <b>كود الطلب:</b> <code>{order_code}</code>\n"
            f"👤 <b>{html.escape(user_name)}</b>\n"
            f"🆔 @{html.escape(user_username)}\n"
            f"🆔 <code>{user_id}</code>\n"
            f"📦 <code>{html.escape(str(purchase.get('service_name', '')))}</code>\n"
            f"📊 الكمية: <code>{quantity}</code>\n"
            f"💰 سعر الوحدة: <code>${float(purchase.get('service_price', 0)):.2f}</code>\n"
            f"💰 الإجمالي: <code>${total:.2f}</code>\n"
            f"📧 <code>{total_emails}</code>\n\n"
            f"📝 <b>المعلومات:</b>\n{info_block}\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        try:
            await context.bot.send_message(
                chat_id=purchase_channel_2, text=text2,
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("✅ اكتمل الطلب", f"complete_order:{order_code}"))
        except Exception:
            logger.exception("Could not send member message.")
    if OWNER_ID:
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=(f"📩 *رسالة من مستخدم*\n\n"
                      f"🧾 كود الطلب: `{order_code}`\n"
                      f"👤 `{user_name}`\n"
                      f"🆔 @{user_username}\n"
                      f"🆔 `{user_id}`\n"
                      f"📦 `{purchase.get('service_name', '')}`\n"
                      f"📊 الكمية: `{quantity}`\n"
                      f"💰 `${total:.2f}`\n\n"
                      f"📝 المعلومات:\n{re.sub('<[^>]+>', '', info_block)}"),
                parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Error: {e}")

    await update.effective_message.reply_text(
        f"✅ *تم استلام رسالتك!*\n\n"
        f"🧾 كود الطلب: `{order_code}`\n"
        f"📦 الكمية: `{quantity}`\n"
        f"💰 تم خصم `${total:.2f}`.\n\n"
        f"📝 المعلومات:\n{re.sub('<[^>]+>', '', info_block)}\n\n"
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
        # NEW: عرض رصيد الأدمن مع مراعاة الدين
        if balances['received'] < 0:
            received_line = f"🚨 <b>إجمالي مكافآت الأدمن:</b> دين <code>${balances['debt']:.2f}</code>\n"
        else:
            received_line = f"✅ <b>إجمالي مكافآت الأدمن الواصلة:</b> ${balances['received']:.2f}\n"

        text = (
            f"💰 <b>أموالي — رصيد الأدمن</b>\n\n"
            f"⏳ <b>طلبات بانتظار الموافقة:</b> ${balances['pending']:.2f}\n"
            f"🔒 <b>النقاط المقيدة (24/48 ساعة):</b> ${balances['hold']:.2f}\n"
            f"🛠 <b>الإيميلات التي أكملها الأدمن:</b> {balances['completed_count']}\n"
            f"⏳ <b>منها مكافآت مقيدة:</b> {balances['pending_count']}\n"
            f"✅ <b>منها مكافآت واصلة:</b> {balances['released_count']}\n"
            f"🛠 <b>مكافآت الأدمن المقيدة (24/48 ساعة):</b> ${balances['admin_pending']:.2f}\n"
            + received_line +
            f"💳 <b>المتبقي منها للاستخدام:</b> ${balances['available_admin']:.2f}\n"
            f"📊 <b>الرصيد المتاح للاستخدام:</b> ${balances['total_owned']:.2f}"
        )
        if balances['debt'] > 0:
            text += f"\n\n🚨 <b>دين مستحق عليك:</b> <code>${balances['debt']:.2f}</code>"
            text += f"\n<i>سيتم اقتطاعه من مكافآتك القادمة تلقائياً.</i>"
        await query.edit_message_text(
            text,
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


def referral_links(bot_username: str, code: str) -> Tuple[str, str]:
    return (f"https://t.me/{bot_username}?start={code}",
            f"https://t.me/{bot_username}?start=B2_{code}")


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
    link1, link2 = referral_links(bot_username, referral_code)
    config = load_config()
    bonus2 = float(config.get("ref2_bonus", 0.0) or 0.0)
    msg = (f"🔗 *نظام الإحالة*\n\n"
           f"📊 *إحصائياتك:*\n💰 المكافآت: ${float(user_data.get('referral_earnings', 0.0)):.2f}\n"
           f"👥 عدد الإحالات: {user_data.get('total_referrals', 0)}\n\n"
           f"1️⃣ *الإحالة العادية* — ينضم صديقك للقنوات فقط، وتحصل على مكافأة عند قبول إيميله:\n`{link1}`\n\n"
           f"2️⃣ *إحالة البوت الأول* — ينضم صديقك للقنوات ويفعّل البوت الأول، "
           f"ويجب أن يكون جديداً في البوتين. تحصل على *${bonus2:.2f}* مرة واحدة فقط عن كل شخص:\n`{link2}`")
    buttons = [("📋 نسخ الرابط العادي", f"copy_referral:{referral_code}"),
               ("📋 نسخ رابط البوت الأول", f"copy_referral2:{referral_code}")]
    if get_ref_contest().get("active"):
        buttons.append(("🏆 مسابقة الإحالات", "rc_view"))
    buttons.append(("🔙 القائمة الرئيسية", "main_menu"))
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


async def copy_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    kind, _, code = (query.data or "").partition(":")
    bot_username = (await context.bot.get_me()).username
    link1, link2 = referral_links(bot_username, code)
    link = link2 if kind == "copy_referral2" else link1
    buttons = [("🔗 عرض روابط الإحالة", "referral_menu"),
               ("🔙 القائمة الرئيسية", "main_menu")]
    await query.edit_message_text(f"📋 *رابطك:*\n\n`{link}`", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def referral_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    config = load_config()
    referral_bonus = float(config.get("referral_bonus", 0.0) or 0.0)
    ref2_bonus = float(config.get("ref2_bonus", 0.0) or 0.0)
    ref2_link = config.get("ref2_partner_link") or "غير مضبوط"
    ref2_api = config.get("ref2_api_url") or "غير مضبوط"
    key_state = "✅ موجود" if os.environ.get("PARTNER_API_KEY", "").strip() else "❌ غير مضبوط"
    buttons = [("💲 مكافأة الإحالة العادية", "set_referral_bonus"),
               ("💲 مكافأة إحالة البوت الأول", "set_ref2_bonus"),
               ("🔗 رابط البوت الأول", "set_ref2_link"),
               ("🌐 رابط API للبوت الأول", "set_ref2_api"),
               ("🏆 مسابقة الإحالات", "rc_menu"),
               ("📊 إحصائيات", "referral_stats"),
               ("🔙 إعدادات المالك", "owner_panel")]
    await query.edit_message_text(
        "🔗 *إعدادات الإحالة*\n\n"
        f"💰 مكافأة الإحالة العادية (لكل إيميل مقبول): ${referral_bonus:.2f}\n"
        f"💰 مكافأة إحالة البوت الأول (مرة واحدة): ${ref2_bonus:.2f}\n"
        f"🔗 رابط البوت الأول: `{ref2_link}`\n"
        f"🌐 رابط API: `{ref2_api}`\n"
        f"🔑 مفتاح PARTNER\\_API\\_KEY: {key_state}",
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


async def set_ref2_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يبدأ إدخال إعدادات إحالة البوت الأول: set_ref2_bonus | set_ref2_link | set_ref2_api."""
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    prompts = {
        "set_ref2_bonus": "💰 أرسل مكافأة إحالة البوت الأول بالدولار (تُدفع مرة واحدة):",
        "set_ref2_link": ("🔗 أرسل رابط البوت الأول كما تريد أن يظهر للأعضاء، مثل `https://t.me/BotName?start=xxx`.\n"
                          "سيظهر نفسه لكل الأعضاء دون أي تغيير."),
        "set_ref2_api": ("🌐 أرسل رابط خادم البوت الأول، مثل `https://your-bot.up.railway.app`.\n"
                         "المفتاح السري يوضع في متغير البيئة `PARTNER_API_KEY` في البوتين بنفس القيمة."),
    }
    mode = query.data
    if mode not in prompts:
        return
    await query.edit_message_text(prompts[mode], parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", "referral_settings"))
    context.user_data["mode"] = mode


def normalize_partner_link(value: str) -> str:
    """يقبل رابط تيليجرام كما كتبه المالك ويُرجعه دون أي تغيير.

    الصيغة @BotName فقط تتحول إلى https://t.me/BotName. يعيد سلسلة فارغة
    إن لم يكن الرابط رابط تيليجرام صالحاً.
    """
    value = (value or "").strip()
    if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", value):
        return f"https://t.me/{value[1:]}"
    if re.match(r"(?:t|telegram)\.me/", value, flags=re.IGNORECASE):
        value = f"https://{value}"
    if len(value) > 500 or any(ch.isspace() for ch in value):
        return ""
    parts = urlsplit(value)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in {"t.me", "telegram.me"}:
        return ""
    if not parts.path.strip("/"):
        return ""
    return value


def normalize_partner_api_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if value and "://" not in value:
        value = f"https://{value}"
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or "." not in parts.hostname:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


async def handle_ref2_setting_input(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, text: str) -> bool:
    """يعالج نص المالك لإعدادات إحالة البوت الأول. يعيد True إن عالج الرسالة."""
    if update.effective_user.id != OWNER_ID:
        return True
    config = load_config()
    if mode == "set_ref2_bonus":
        try:
            bonus = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً.")
            return True
        if bonus < 0 or bonus > 1000:
            await update.message.reply_text("⚠️ المبلغ يجب أن يكون بين 0 و1000.")
            return True
        config["ref2_bonus"] = round(bonus, 2)
        reply = f"✅ مكافأة إحالة البوت الأول: ${bonus:.2f}"
    elif mode == "set_ref2_link":
        link = normalize_partner_link(text)
        if not link:
            await update.message.reply_text("⚠️ رابط غير صالح. أرسل رابط تيليجرام يبدأ بـ https://t.me/")
            return True
        config["ref2_partner_link"] = link
        reply = f"✅ تم حفظ رابط البوت الأول: {link}"
    else:
        api = normalize_partner_api_url(text)
        if not api:
            await update.message.reply_text("⚠️ رابط غير صالح. أرسل مثل https://your-bot.up.railway.app")
            return True
        config["ref2_api_url"] = api
        reply = f"✅ تم حفظ رابط API: {api}"
    save_config(config)
    context.user_data.pop("mode", None)
    await update.message.reply_text(
        reply, reply_markup=kb_single("🔙 إعدادات الإحالة", "referral_settings"))
    return True


async def referral_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    users = load_json(USERS_DB)
    total_referrals = 0
    total_earnings = 0.0
    ref2_paid = 0
    ref2_pending = 0
    top = []
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        if data.get("total_referrals", 0) > 0:
            total_referrals += data["total_referrals"]
            total_earnings += float(data.get("referral_earnings", 0.0))
            top.append({"user_id": uid, "count": data["total_referrals"],
                        "earnings": float(data.get("referral_earnings", 0.0))})
        ref2 = data.get("ref2")
        if isinstance(ref2, dict):
            if ref2.get("status") == "paid":
                ref2_paid += 1
            elif ref2.get("status") == "pending":
                ref2_pending += 1
    top.sort(key=lambda x: x["count"], reverse=True)
    msg = (f"📊 *إحصائيات الإحالة*\n\n👥 الإجمالي: {total_referrals}\n"
           f"💰 المكافآت: ${total_earnings:.2f}\n"
           f"🤝 إحالات البوت الأول المدفوعة: {ref2_paid}\n"
           f"⏳ بانتظار التفعيل: {ref2_pending}\n\n")
    if top:
        msg += "🏆 *أفضل المحالين:*\n"
        for idx, ref in enumerate(top[:5], 1):
            msg += f"{idx}. {ref['user_id']} - {ref['count']} - ${ref['earnings']:.2f}\n"
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إعدادات الإحالة", "referral_settings"))


# ==================== REFERRAL CONTEST (إحالة البوت الأول) ====================
# مسابقة مفتوحة: جوائز مالية و/أو نصية، فائز بالترتيب أو معيَّن يدوياً، تسليم تلقائي
# أو يدوي، وكل الإعدادات قابلة للتعديل في أي وقت أثناء المسابقة أو بعدها.
def get_ref_contest() -> dict:
    contest = load_config().get("ref_contest")
    return contest if isinstance(contest, dict) else {}


def save_ref_contest(contest: dict) -> None:
    config = load_config()
    config["ref_contest"] = contest
    save_config(config)


def _parse_iso(value) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _md(text: str) -> str:
    for ch in ("\\", "_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def ref_contest_ranking(contest: dict) -> List[dict]:
    """ترتيب المحيلين: الأكثر إحالات مكتملة (النوع الثاني فقط) خلال مدة المسابقة.

    تُحتسب الإحالة فقط إذا كانت «paid»: المُحال جديد في البوتين وانضم للقنوات
    وفعّل البوت الأول. عند التعادل يفوز من وصل لعدده أولاً.
    """
    start = _parse_iso(contest.get("started_at"))
    if not start:
        return []
    end = _parse_iso(contest.get("ended_at") or contest.get("ends_at"))
    stats: Dict[int, dict] = {}
    for _uid, encrypted in load_json(USERS_DB).items():
        ref2 = decrypt_user_data(encrypted).get("ref2")
        if not isinstance(ref2, dict) or ref2.get("status") != "paid":
            continue
        paid_at = _parse_iso(ref2.get("paid_at"))
        referrer = ref2.get("referrer")
        if not paid_at or not referrer or paid_at < start or (end and paid_at > end):
            continue
        entry = stats.setdefault(int(referrer), {"user_id": int(referrer), "count": 0, "last": paid_at})
        entry["count"] += 1
        entry["last"] = max(entry["last"], paid_at)
    return sorted(stats.values(), key=lambda e: (-e["count"], e["last"]))


def ref_contest_winners(contest: dict) -> List[dict]:
    """الفائزون لكل مركز له جائزة: المعيَّنون يدوياً أولاً ثم الترتيب الطبيعي."""
    prizes = contest.get("prizes", []) or []
    ranking = ref_contest_ranking(contest)
    counts = {e["user_id"]: e["count"] for e in ranking}
    min_count = int(contest.get("min_count", 0) or 0)
    overrides = {}
    for key, value in (contest.get("overrides") or {}).items():
        try:
            overrides[int(key)] = int(value)
        except (TypeError, ValueError):
            continue
    pool = [e for e in ranking if e["count"] >= max(min_count, 1) and e["user_id"] not in overrides.values()]
    winners = []
    for rank in range(1, len(prizes) + 1):
        if rank in overrides:
            uid, manual = overrides[rank], True
        elif pool:
            uid, manual = pool.pop(0)["user_id"], False
        else:
            continue
        winners.append({"rank": rank, "user_id": uid, "count": counts.get(uid, 0),
                        "manual": manual, "prize": prizes[rank - 1]})
    return winners


def _mask_uid(uid: int) -> str:
    text = str(uid)
    return f"{text[:2]}***{text[-3:]}" if len(text) > 5 else text


def _prize_label(prize: dict) -> str:
    parts = []
    if float(prize.get("money", 0) or 0) > 0:
        parts.append(f"${float(prize['money']):.2f}")
    if prize.get("text"):
        parts.append(_md(str(prize["text"])))
    return " + ".join(parts) or "—"


def _fmt_prizes(prizes: List[dict]) -> str:
    medals = ["🥇", "🥈", "🥉"]
    if not prizes:
        return "لا توجد جوائز محددة بعد."
    return "\n".join(f"{medals[i] if i < 3 else f'{i + 1}.'} المركز {i + 1}: {_prize_label(p)}"
                     for i, p in enumerate(prizes))


def parse_prizes_input(text: str) -> List[dict]:
    """كل سطر = مركز. رقم فقط = مبلغ بالدولار، نص = جائزة نصية، وبينهما + = الاثنان.

    مثال: «10» أو «5$» أو «اشتراك شهر» أو «10 + اشتراك شهر».
    """
    prizes = []
    raw_lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(raw_lines) == 1 and re.fullmatch(r"[\d.\s$,]+", raw_lines[0]):
        raw_lines = raw_lines[0].replace(",", ".").replace("$", "").split()  # «10 5 2»
    for line in raw_lines[:20]:
        money, texts = 0.0, []
        for part in line.split("+"):
            part = part.strip()
            if not part:
                continue
            number = part.replace("$", "").replace(",", ".").strip()
            try:
                value = float(number)
                if 0 < value <= 100000:
                    money += value
                    continue
            except ValueError:
                pass
            texts.append(part[:200])
        if money > 0 or texts:
            prizes.append({"money": round(money, 2), "text": " + ".join(texts)})
    return prizes


def _contest_end_text(contest: dict) -> str:
    parts = []
    ends_at = _parse_iso(contest.get("ends_at"))
    if ends_at:
        parts.append(f"⏰ تنتهي: {ends_at.astimezone().strftime('%Y-%m-%d %H:%M')}")
    target = int(contest.get("target_count", 0) or 0)
    if target:
        parts.append(f"🎯 أو عند وصول أحدهم إلى {target} إحالة")
    return "\n".join(parts) or "🔓 مفتوحة بلا موعد انتهاء (تنتهي بقرار المالك)"


def _contest_summary(contest: dict) -> str:
    lines = []
    if contest.get("title"):
        lines.append(f"*{_md(contest['title'])}*")
    lines.append("🟢 نشطة" if contest.get("active") else "🔴 منتهية/متوقفة")
    lines += ["", _fmt_prizes(contest.get("prizes", [])), "", _contest_end_text(contest)]
    min_count = int(contest.get("min_count", 0) or 0)
    if min_count:
        lines.append(f"🔢 الحد الأدنى للفوز: {min_count} إحالة")
    lines.append(f"🤖 التسليم التلقائي عند الانتهاء: {'مفعّل' if contest.get('auto_deliver') else 'متوقف (يدوي)'}")
    if contest.get("description"):
        lines += ["", _md(contest["description"])]
    return "\n".join(lines)


async def _deliver_rank(context: ContextTypes.DEFAULT_TYPE, rank: int) -> bool:
    """يسلّم جائزة مركز واحد مرة واحدة: المال لرصيد الفائز، والنص بإشعار له وللمالك."""
    contest = get_ref_contest()
    paid = contest.setdefault("paid", {})
    winner = next((w for w in ref_contest_winners(contest) if w["rank"] == rank), None)
    if not winner or str(rank) in paid:
        return False
    prize = winner["prize"]
    money = clamp_money(prize.get("money", 0))
    paid[str(rank)] = {"user_id": winner["user_id"], "prize": prize,
                       "at": datetime.now(timezone.utc).isoformat()}
    save_ref_contest(contest)  # نسجّل أولاً لمنع التسليم المزدوج
    if money > 0:
        user = get_user(winner["user_id"])
        user["balance"] = clamp_money(float(user.get("balance", 0.0)) + money)
        user["total_credited_balance"] = clamp_money(float(user.get("total_credited_balance", 0.0) or 0.0) + money)
        add_transaction(user, "credit", money, f"جائزة مسابقة الإحالات - المركز {rank}")
        save_user(winner["user_id"], user)
    message = f"🎉 *مبروك!* فزت بالمركز {rank} في مسابقة الإحالات.\n🎁 جائزتك: {_prize_label(prize)}"
    if money > 0:
        message += "\n💰 أُضيف المبلغ إلى رصيدك."
    if prize.get("text"):
        message += "\n📩 سيتواصل معك المالك لتسليم الجائزة."
    try:
        await context.bot.send_message(winner["user_id"], message, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    if prize.get("text"):
        try:
            await context.bot.send_message(
                OWNER_ID, f"🎁 جائزة نصية تحتاج تسليماً يدوياً\nالمركز {rank} — المستخدم `{winner['user_id']}`\n{_prize_label(prize)}",
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
    return True


async def close_ref_contest(context: ContextTypes.DEFAULT_TYPE, reason: str) -> None:
    contest = get_ref_contest()
    if not contest or not contest.get("active"):
        return
    contest["active"] = False
    contest["ended_at"] = datetime.now(timezone.utc).isoformat()
    contest["end_reason"] = reason
    save_ref_contest(contest)
    winners = ref_contest_winners(contest)
    lines = [f"🏁 *انتهت مسابقة الإحالات* ({reason})\n"]
    for w in winners:
        lines.append(f"{w['rank']}. `{w['user_id']}` — {w['count']} إحالة — {_prize_label(w['prize'])}"
                     + (" (معيَّن)" if w["manual"] else ""))
    if not winners:
        lines.append("لا يوجد فائزون.")
    delivered = False
    if contest.get("auto_deliver"):
        for w in winners:
            delivered = await _deliver_rank(context, w["rank"]) or delivered
        lines.append("\n🤖 تم التسليم تلقائياً." if delivered else "")
    else:
        lines.append("\n💵 التسليم يدوي من «الترتيب والتسليم».")
    try:
        await context.bot.send_message(OWNER_ID, "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
                                       reply_markup=kb_single("🏆 مسابقة الإحالات", "rc_menu"))
    except Exception:
        pass


async def check_ref_contest_end(context: ContextTypes.DEFAULT_TYPE) -> None:
    """يغلق المسابقة عند انتهاء الوقت أو وصول أحدهم للعدد (إن حُدّدا)."""
    contest = get_ref_contest()
    if not contest or not contest.get("active"):
        return
    ends_at = _parse_iso(contest.get("ends_at"))
    if ends_at and datetime.now(timezone.utc) >= ends_at:
        await close_ref_contest(context, "انتهى الوقت")
        return
    target = int(contest.get("target_count", 0) or 0)
    if target:
        ranking = ref_contest_ranking(contest)
        if ranking and ranking[0]["count"] >= target:
            await close_ref_contest(context, f"وصل أحدهم إلى {target} إحالة")


async def ref_contest_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await check_ref_contest_end(context)
    except Exception:
        logger.exception("ref contest tick failed")


def _owner_only(update: Update) -> bool:
    return update.effective_user.id == OWNER_ID


async def rc_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _owner_only(update):
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    for key in ("rc_build", "rc_flow"):
        context.user_data.pop(key, None)
    if str(context.user_data.get("mode", "")).startswith("rc_"):
        context.user_data.pop("mode", None)
    contest = get_ref_contest()
    if not contest:
        await query.edit_message_text(
            "🏆 *مسابقة الإحالات (إحالة البوت الأول)*\n\n📭 لا توجد مسابقة.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_vertical([("🆕 إنشاء مسابقة جديدة", "rc_create"),
                                      ("🔙 إعدادات الإحالة", "referral_settings")]))
        return
    active = bool(contest.get("active"))
    buttons = [("📊 الترتيب والفائزون والتسليم", "rc_board"),
               ("🎁 تعديل الجوائز", "rc_edit:prizes"),
               ("➕ تمديد المسابقة", "rc_edit:extend"),
               ("⏰ تعديل المدة", "rc_edit:days"),
               ("🎯 تعديل عدد الإحالات المنهي", "rc_edit:target"),
               ("🔢 الحد الأدنى للفوز", "rc_edit:min"),
               ("📝 العنوان والوصف", "rc_edit:desc"),
               (f"🤖 التسليم التلقائي: {'مفعّل ✅' if contest.get('auto_deliver') else 'متوقف ❌'}", "rc_toggle_auto"),
               ("🛑 إنهاء المسابقة الآن" if active else "▶️ إعادة فتح المسابقة",
                "rc_stop" if active else "rc_reopen")]
    if not active:
        buttons.append(("🆕 إنشاء مسابقة جديدة", "rc_create"))
    buttons.append(("🔙 إعدادات الإحالة", "referral_settings"))
    await query.edit_message_text("🏆 *مسابقة الإحالات (إحالة البوت الأول)*\n\n" + _contest_summary(contest),
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_vertical(buttons))


PRIZES_HELP = ("أرسل الجوائز، *سطر لكل مركز*:\n"
               "• رقم = مبلغ بالدولار: `10`\n"
               "• نص = جائزة أخرى: `اشتراك شهر`\n"
               "• الاثنان معاً: `10 + اشتراك شهر`\n"
               "• مراكز مالية سريعة في سطر واحد: `10 5 2`")


async def rc_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _owner_only(update):
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    if get_ref_contest().get("active"):
        await query.answer("أنهِ المسابقة الحالية أولاً.", show_alert=True)
        return
    context.user_data["rc_build"] = {}
    context.user_data["rc_flow"] = "create"
    context.user_data["mode"] = "rc_prizes"
    await query.edit_message_text("🆕 *مسابقة إحالات جديدة*\n\n*الخطوة 1/3* — " + PRIZES_HELP,
                                  parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 إلغاء", "rc_menu"))


EDIT_PROMPTS = {
    "prizes": "🎁 *تعديل الجوائز* — " + PRIZES_HELP,
    "extend": "➕ أرسل عدد الأيام التي تريد *إضافتها* إلى موعد الانتهاء الحالي (يقبل كسراً مثل `0.5`). إن كانت المسابقة منتهية تُعاد فتحها.",
    "days": "⏰ أرسل المدة بالأيام *من الآن* (يقبل كسراً مثل `0.5`)، أو `0` لجعلها مفتوحة بلا موعد انتهاء.",
    "target": "🎯 أرسل عدد الإحالات الذي تنتهي المسابقة عند وصول أحدهم إليه، أو `0` لإلغاء هذا الشرط.",
    "min": "🔢 أرسل الحد الأدنى للإحالات ليستحق العضو الفوز، أو `0` بلا حد أدنى.",
    "desc": "📝 أرسل عنوان المسابقة في أول سطر، ووصفها (اختياري) في بقية الأسطر. أرسل `-` لحذفهما.",
}


async def rc_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _owner_only(update):
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    field = (query.data or "").partition(":")[2]
    if field not in EDIT_PROMPTS or not get_ref_contest():
        await query.answer("⚠️ غير متاح.", show_alert=True)
        return
    context.user_data["rc_flow"] = "edit"
    context.user_data["mode"] = {"prizes": "rc_prizes", "days": "rc_days", "extend": "rc_extend", "target": "rc_target",
                                 "min": "rc_min", "desc": "rc_desc"}[field]
    await query.edit_message_text(EDIT_PROMPTS[field], parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", "rc_menu"))


async def handle_rc_input(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str, text: str) -> bool:
    if not _owner_only(update):
        return True
    cancel = kb_single("🔙 إلغاء", "rc_menu")
    flow = context.user_data.get("rc_flow", "edit")
    build = context.user_data.setdefault("rc_build", {})
    text = (text or "").strip()

    async def done(message: str):
        context.user_data.pop("mode", None)
        context.user_data.pop("rc_flow", None)
        await update.message.reply_text(message, reply_markup=kb_single("🏆 مسابقة الإحالات", "rc_menu"))

    if mode == "rc_assign":
        rank = int(context.user_data.get("rc_assign_rank", 0) or 0)
        contest = get_ref_contest()
        if not text.lstrip("-").isdigit() or not rank or not contest:
            await update.message.reply_text("⚠️ أرسل رقم معرّف المستخدم (أرقام فقط).", reply_markup=cancel)
            return True
        if str(rank) in (contest.get("paid") or {}):
            await done("⚠️ جائزة هذا المركز سُلّمت مسبقاً ولا يمكن تغييرها.")
            return True
        contest.setdefault("overrides", {})[str(rank)] = int(text)
        save_ref_contest(contest)
        context.user_data.pop("rc_assign_rank", None)
        await done(f"✅ عُيّن `{text}` فائزاً بالمركز {rank}.")
        return True

    if mode == "rc_prizes":
        prizes = parse_prizes_input(text)
        if not prizes:
            await update.message.reply_text("⚠️ لم أفهم الجوائز. أرسل مثل: 10 5 2", reply_markup=cancel)
            return True
        if flow == "create":
            build["prizes"] = prizes
            context.user_data["mode"] = "rc_days"
            await update.message.reply_text(
                "*الخطوة 2/3* — أرسل مدة المسابقة بالأيام، أو `0` لتكون مفتوحة بلا موعد انتهاء.",
                parse_mode=ParseMode.MARKDOWN, reply_markup=cancel)
            return True
        contest = get_ref_contest()
        contest["prizes"] = prizes
        save_ref_contest(contest)
        await done("✅ تم تحديث الجوائز.")
        return True

    if mode == "rc_desc":
        contest = get_ref_contest()
        if text == "-":
            contest["title"], contest["description"] = "", ""
        else:
            title, _, description = text.partition("\n")
            contest["title"], contest["description"] = title.strip()[:100], description.strip()[:800]
        save_ref_contest(contest)
        await done("✅ تم التحديث.")
        return True

    number = text.replace(",", ".")
    try:
        value = float(number)
    except ValueError:
        value = -1
    if value < 0 or value > 100000 or (mode != "rc_days" and value != int(value)):
        await update.message.reply_text("⚠️ أرسل رقماً صحيحاً (0 أو أكثر).", reply_markup=cancel)
        return True

    if mode == "rc_extend":
        if value <= 0:
            await update.message.reply_text("⚠️ أرسل عدداً أكبر من 0.", reply_markup=cancel)
            return True
        contest = get_ref_contest()
        now = datetime.now(timezone.utc)
        base = _parse_iso(contest.get("ends_at"))
        base = base if base and base > now else now
        contest["ends_at"] = (base + timedelta(days=value)).isoformat()
        if not contest.get("active"):
            contest["active"] = True
            contest.pop("ended_at", None)
            contest.pop("end_reason", None)
        save_ref_contest(contest)
        await done(f"✅ تم التمديد. تنتهي الآن: {_parse_iso(contest['ends_at']).astimezone().strftime('%Y-%m-%d %H:%M')}")
        return True

    if mode == "rc_days":
        if flow == "create":
            build["days"] = value
            context.user_data["mode"] = "rc_target"
            await update.message.reply_text(
                "*الخطوة 3/3* — أرسل عدد الإحالات الذي تنتهي المسابقة عند وصول أحدهم إليه، أو `0` بلا عدد محدد.",
                parse_mode=ParseMode.MARKDOWN, reply_markup=cancel)
            return True
        contest = get_ref_contest()
        contest["ends_at"] = (datetime.now(timezone.utc) + timedelta(days=value)).isoformat() if value > 0 else ""
        save_ref_contest(contest)
        await done("✅ تم تحديث المدة." if value > 0 else "✅ أصبحت المسابقة مفتوحة بلا موعد انتهاء.")
        return True

    if mode == "rc_target":
        if flow == "create":
            now = datetime.now(timezone.utc)
            days = float(build.get("days", 0))
            old = get_ref_contest()
            if old:
                config = load_config()
                history = config.get("ref_contest_history", [])
                config["ref_contest_history"] = (history + [old])[-10:]
                save_config(config)
            save_ref_contest({
                "active": True, "started_at": now.isoformat(),
                "ends_at": (now + timedelta(days=days)).isoformat() if days > 0 else "",
                "target_count": int(value), "min_count": 0, "auto_deliver": False,
                "prizes": build.get("prizes", []), "paid": {}, "overrides": {},
                "title": "", "description": "",
            })
            context.user_data.pop("rc_build", None)
            await done("✅ بدأت المسابقة. يمكنك الآن تعديل أي شيء منها من القائمة (الحد الأدنى، "
                       "التسليم التلقائي، العنوان والوصف، تعيين فائز...).\n"
                       "يُحتسب فقط من أحاله العضو برابط البوت الأول وكان جديداً في البوتين.")
            return True
        contest = get_ref_contest()
        contest["target_count"] = int(value)
        save_ref_contest(contest)
        await done("✅ تم التحديث.")
        return True

    contest = get_ref_contest()  # rc_min
    contest["min_count"] = int(value)
    save_ref_contest(contest)
    await done("✅ تم تحديث الحد الأدنى.")
    return True


async def rc_toggle_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _owner_only(update):
        await update.callback_query.answer("🚫 مالك فقط.", show_alert=True)
        return
    contest = get_ref_contest()
    contest["auto_deliver"] = not contest.get("auto_deliver")
    save_ref_contest(contest)
    await rc_menu(update, context)


async def rc_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _owner_only(update):
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    await close_ref_contest(context, "أنهاها المالك")
    await query.answer("🛑 تم إنهاء المسابقة")
    await rc_menu(update, context)


async def rc_reopen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _owner_only(update):
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    contest = get_ref_contest()
    if not contest:
        await query.answer("لا توجد مسابقة.", show_alert=True)
        return
    contest["active"] = True
    contest.pop("ended_at", None)
    contest.pop("end_reason", None)
    ends_at = _parse_iso(contest.get("ends_at"))
    if ends_at and ends_at <= datetime.now(timezone.utc):
        contest["ends_at"] = ""  # الموعد القديم فات: تصبح مفتوحة
    save_ref_contest(contest)
    await query.answer("▶️ أُعيد فتح المسابقة")
    await rc_menu(update, context)


async def rc_board(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _owner_only(update):
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    contest = get_ref_contest()
    if not contest:
        await query.answer("لا توجد مسابقة.", show_alert=True)
        return
    paid = contest.get("paid", {}) or {}
    ranking = ref_contest_ranking(contest)
    winners = ref_contest_winners(contest)
    lines = [f"📊 *الترتيب* ({'🟢 نشطة' if contest.get('active') else '🔴 منتهية'})\n", "*🔝 المتصدرون:*"]
    for i, entry in enumerate(ranking[:10], 1):
        lines.append(f"{i}. `{entry['user_id']}` — {entry['count']} إحالة")
    if not ranking:
        lines.append("لا توجد إحالات مكتملة بعد.")
    lines.append("\n*🏅 الفائزون:*")
    rows = []
    for w in winners:
        done = str(w["rank"]) in paid
        lines.append(f"{w['rank']}. `{w['user_id']}` — {w['count']} إحالة — {_prize_label(w['prize'])}"
                     + (" 👤معيَّن" if w["manual"] else "") + (" ✅ سُلّمت" if done else ""))
        if not done:
            rows.append([InlineKeyboardButton(f"🎁 تسليم المركز {w['rank']}", callback_data=f"rc_pay:{w['rank']}")])
    if not winners:
        lines.append("لا يوجد فائزون بعد.")
    if any(str(w["rank"]) not in paid for w in winners):
        rows.append([InlineKeyboardButton("🎁 تسليم الكل", callback_data="rc_payall")])
    rows.append([InlineKeyboardButton("👤 تعيين/تغيير فائز يدوياً", callback_data="rc_assign")])
    rows.append([InlineKeyboardButton("🔙 مسابقة الإحالات", callback_data="rc_menu")])
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=InlineKeyboardMarkup(rows))


async def rc_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _owner_only(update):
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    rank_text = (query.data or "").partition(":")[2]
    if not rank_text.isdigit() or not await _deliver_rank(context, int(rank_text)):
        await query.answer("⚠️ غير متاح أو سُلّمت مسبقاً.", show_alert=True)
        return
    await query.answer("✅ تم التسليم")
    await rc_board(update, context)


async def rc_payall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _owner_only(update):
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    count = 0
    for w in ref_contest_winners(get_ref_contest()):
        count += 1 if await _deliver_rank(context, w["rank"]) else 0
    await query.answer(f"✅ سُلّمت {count} جائزة")
    await rc_board(update, context)


async def rc_assign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض المراكز لاختيار مركز تعيّن له فائزاً، أو يلغي التعيين اليدوي."""
    query = update.callback_query
    if not _owner_only(update):
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    contest = get_ref_contest()
    prizes = contest.get("prizes", []) or []
    if not prizes:
        await query.answer("أضف جوائز أولاً.", show_alert=True)
        return
    overrides = contest.get("overrides", {}) or {}
    paid = contest.get("paid", {}) or {}
    rows = []
    for rank in range(1, len(prizes) + 1):
        if str(rank) in paid:
            continue
        current = overrides.get(str(rank))
        label = f"المركز {rank}" + (f" (معيَّن: {current})" if current else "")
        rows.append([InlineKeyboardButton(f"👤 {label}", callback_data=f"rc_assign_rank:{rank}")])
        if current:
            rows.append([InlineKeyboardButton(f"❌ إلغاء تعيين المركز {rank}", callback_data=f"rc_unassign:{rank}")])
    rows.append([InlineKeyboardButton("🔙 الترتيب", callback_data="rc_board")])
    await query.edit_message_text("👤 اختر المركز الذي تريد تعيين فائزه يدوياً (يتجاوز الترتيب):",
                                  reply_markup=InlineKeyboardMarkup(rows))


async def rc_assign_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _owner_only(update):
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    rank_text = (query.data or "").partition(":")[2]
    if not rank_text.isdigit():
        return
    context.user_data["rc_assign_rank"] = int(rank_text)
    context.user_data["mode"] = "rc_assign"
    await query.edit_message_text(f"👤 أرسل رقم معرّف (ID) المستخدم الفائز بالمركز {rank_text}:",
                                  reply_markup=kb_single("🔙 إلغاء", "rc_assign"))


async def rc_unassign(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _owner_only(update):
        await query.answer("🚫 مالك فقط.", show_alert=True)
        return
    rank_text = (query.data or "").partition(":")[2]
    contest = get_ref_contest()
    if contest.get("overrides", {}).pop(rank_text, None) is not None:
        save_ref_contest(contest)
    await query.answer("تم إلغاء التعيين")
    await rc_assign(update, context)


async def rc_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المسابقة للأعضاء."""
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    contest = get_ref_contest()
    back = kb_single("🔙 الإحالة", "referral_menu")
    if not contest.get("active"):
        await query.edit_message_text("📭 لا توجد مسابقة إحالات حالياً.", reply_markup=back)
        return
    ranking = ref_contest_ranking(contest)
    uid = update.effective_user.id
    mine = next(((i + 1, e["count"]) for i, e in enumerate(ranking) if e["user_id"] == uid), None)
    head = f"🏆 *{_md(contest['title'])}*" if contest.get("title") else "🏆 *مسابقة الإحالات*"
    lines = [head, "الأكثر إحالات عبر *رابط البوت الأول* يفوز.\n"]
    if contest.get("description"):
        lines += [_md(contest["description"]), ""]
    lines += [_fmt_prizes(contest.get("prizes", [])), "", _contest_end_text(contest)]
    min_count = int(contest.get("min_count", 0) or 0)
    if min_count:
        lines.append(f"🔢 الحد الأدنى للفوز: {min_count} إحالة")
    lines += ["", "🔝 *المتصدرون:*"]
    for i, entry in enumerate(ranking[:10], 1):
        lines.append(f"{i}. {_mask_uid(entry['user_id'])} — {entry['count']}")
    if not ranking:
        lines.append("لا توجد إحالات بعد، كن الأول!")
    lines.append(f"\n📌 ترتيبك: {mine[0]} ({mine[1]} إحالة)" if mine else "\n📌 ليس لديك إحالات مكتملة بعد.")
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=back)


# ==================== STORE INPUT HANDLER (owner) ====================
async def handle_store_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram_bot.members import main_menu
    if update.effective_user.id != OWNER_ID:
        return
    text = update.message.text.strip() if update.message.text else ""
    action = context.user_data.get("store_action", "")

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
                await update.message.reply_text("⚠️ أضف البوت مشرفاً في القناة أولاً.")
                return
        except Exception:
            logger.exception("Could not validate the forced channel")
            await update.message.reply_text("⚠️ تعذر الوصول إلى القناة.")
            return
        join_link = invite_link or (f"https://t.me/{chat.username}" if getattr(chat, "username", None) else "")
        if not join_link:
            await update.message.reply_text("⚠️ قناة خاصة: أرسل المعرف الرقمي مع رابط الدعوة مفصولين بـ |.")
            return
        config = load_config()
        channels = get_forced_channels(config)
        if any(item["channel"] == str(chat.id) for item in channels):
            await update.message.reply_text("ℹ️ هذه القناة مضافة مسبقاً.")
            return
        if len(channels) >= MAX_FORCED_CHANNELS:
            await update.message.reply_text("⚠️ وصلت للحد الأقصى من القنوات.")
            return
        channels.append({"channel": str(chat.id), "link": join_link})
        set_forced_channels(config, channels)
        save_config(config)
        context.user_data.pop("store_action", None)
        await update.message.reply_text(
            f"✅ تمت إضافة القناة الإجبارية: {chat.title}\n\n"
            + forced_channels_panel_text(channels),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=forced_channels_panel_keyboard(channels))
        return

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

    if action == "add_service_name":
        if not text:
            await update.message.reply_text("⚠️ أرسل اسماً صالحاً.")
            return
        context.user_data["new_service_name"] = text
        context.user_data["new_service_types"] = []
        context.user_data["store_action"] = "add_service_types"
        cat_id = context.user_data.get("current_category_id")
        await update.message.reply_text(
            "📋 *الخطوة 2/5*: اختر نوع المعلومات المطلوبة من المشتري\n\n"
            "<i>يمكنك اختيار أكثر من نوع — ثم اضغط 'متابعة'</i>",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_service_types_keyboard([]))
        return

    if action == "add_service_price":
        try:
            price = float(text)
            if price <= 0:
                await update.message.reply_text("⚠️ السعر > 0!")
                return
            context.user_data["new_service_price"] = price
            context.user_data["new_service_min"] = None
            context.user_data["new_service_max"] = None
            context.user_data["store_action"] = "add_service_qty_min"
            cat_id = context.user_data.get("current_category_id")
            await update.message.reply_text(
                "🔢 *الخطوة 4/5*: الحد الأدنى للكمية\n\n"
                "📌 أرسل الحد الأدنى (رقم صحيح ≥ 1):\n"
                "<i>مثال: 1</i>\n\n"
                "⚠️ <b>أو أرسل 'تخطي' لجعل الكمية ثابتة = 1 (لن يظهر للعضو حقل العدد)</b>",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb_single("⏭️ تخطي (كمية = 1)", f"service_qty_skip_min:{cat_id}"))
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return

    if action == "add_service_qty_min":
        if text.casefold() in {"تخطي", "skip"}:
            await _skip_qty_min(update, context)
            return
        try:
            v = int(text)
            if v < 1:
                raise ValueError
            context.user_data["new_service_min"] = v
            context.user_data["store_action"] = "add_service_qty_max"
            cat_id = context.user_data.get("current_category_id")
            await update.message.reply_text(
                f"✅ الحد الأدنى: <b>{v}</b>\n\n"
                "🔢 *الخطوة 5/5*: الحد الأعلى للكمية\n\n"
                "📌 أرسل الحد الأعلى (رقم صحيح ≥ الحد الأدنى):\n"
                f"<i>مثال: {v + 4}</i>\n\n"
                "⚠️ <b>أو أرسل 'تخطي' لإلغاء الحد الأعلى (بلا سقف)</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("⏭️ تخطي (بلا سقف)", f"service_qty_skip_max:{cat_id}"))
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً صحيحاً ≥ 1.")
        return

    if action == "add_service_qty_max":
        if text.casefold() in {"تخطي", "skip"}:
            await _skip_qty_max(update, context)
            return
        try:
            v = int(text)
            mn = context.user_data.get("new_service_min")
            if v < (mn or 1):
                await update.message.reply_text(f"⚠️ الحد الأعلى يجب أن يكون ≥ {mn or 1}.")
                return
            context.user_data["new_service_max"] = v
            context.user_data["store_action"] = "add_service_message"
            cat_id = context.user_data.get("current_category_id")
            await update.message.reply_text(
                f"✅ الحد الأعلى: <b>{v}</b>\n\n"
                "📝 *أرسل رسالة الطلب للعميل (ما يظهر له بعد الشراء):*",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً صحيحاً.")
        return

    if action == "add_service_message":
        name = context.user_data.get("new_service_name")
        price = context.user_data.get("new_service_price")
        types = context.user_data.get("new_service_types") or []
        mn = context.user_data.get("new_service_min")
        mx = context.user_data.get("new_service_max")
        cat_id = context.user_data.get("current_category_id")
        if not name or not price or not types:
            await update.message.reply_text(
                "⚠️ بيانات الخدمة غير مكتملة. أعد العملية من البداية.",
                reply_markup=kb_single("🔙 المبيعات", "store_section"))
            for key in ("store_action", "new_service_name", "new_service_price",
                        "new_service_types", "new_service_min", "new_service_max",
                        "current_category_id"):
                context.user_data.pop(key, None)
            return
        config = load_config()
        for cat in config.get("store_categories", []):
            if cat["id"] == cat_id:
                cat["services"].append({
                    "id": str(time.time_ns()),
                    "name": name,
                    "price": price,
                    "message": text,
                    "message_types": types,
                    "min_qty": mn,
                    "max_qty": mx,
                })
                break
        save_config(config)
        await update.message.reply_text(
            f"✅ *تم إضافة الخدمة بنجاح!*\n\n"
            f"📝 الاسم: {html.escape(name)}\n"
            f"💰 السعر: ${price:.2f}\n"
            f"📋 المعلومات: {' + '.join(MESSAGE_TYPE_LABELS[t][1] for t in types)}\n",
            parse_mode=ParseMode.MARKDOWN)
        for key in ("store_action", "new_service_name", "new_service_price",
                    "new_service_types", "new_service_min", "new_service_max",
                    "current_category_id"):
            context.user_data.pop(key, None)
        await main_menu(update, context)
        return

    if action.startswith("edit_service_"):
        field = action.replace("edit_service_", "")
        cat_id = context.user_data.get("edit_service_cat")
        service_id = context.user_data.get("edit_service_id")
        if not cat_id or not service_id:
            await update.message.reply_text("⚠️ حدث خطأ.",
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
                elif field == "qty_min":
                    if text.casefold() in {"تخطي", "skip"}:
                        s["min_qty"] = None
                        new_value = "تخطي"
                    else:
                        try:
                            v = int(text)
                            if v < 1:
                                raise ValueError
                            s["min_qty"] = v
                            new_value = str(v)
                        except ValueError:
                            await update.message.reply_text("⚠️ رقم صحيح ≥ 1.")
                            return
                    updated = True
                elif field == "qty_max":
                    if text.casefold() in {"تخطي", "skip"}:
                        s["max_qty"] = None
                        new_value = "بلا سقف"
                    else:
                        try:
                            v = int(text)
                            mn = s.get("min_qty")
                            if v < (mn or 1):
                                await update.message.reply_text(f"⚠️ يجب أن يكون ≥ {mn or 1}.")
                                return
                            s["max_qty"] = v
                            new_value = str(v)
                        except ValueError:
                            await update.message.reply_text("⚠️ رقم صحيح.")
                            return
                    updated = True
                break
            break
        if updated:
            save_config(config)
            field_labels = {"name": "الاسم", "price": "السعر", "message": "الرسالة",
                            "qty_min": "الحد الأدنى", "qty_max": "الحد الأعلى"}
            await update.message.reply_text(
                f"✅ تم تحديث {field_labels.get(field, field)} إلى: <b>{new_value}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_single("🔙 الخدمة", f"service_detail:{cat_id}:{service_id}"))
        else:
            await update.message.reply_text("⚠️ لم يتم العثور على الخدمة.",
                                            reply_markup=kb_single("🔙 المبيعات", "store_section"))
        for key in ("store_action", "edit_service_cat", "edit_service_id"):
            context.user_data.pop(key, None)
        return


def _service_types_keyboard(selected: list) -> InlineKeyboardMarkup:
    rows = []
    for key, (label, _) in MESSAGE_TYPE_LABELS.items():
        mark = "✅ " if key in selected else "☐ "
        rows.append([InlineKeyboardButton(f"{mark}{label}", callback_data=f"service_toggle_type:{key}")])
    if selected:
        rows.append([InlineKeyboardButton("✅ متابعة", callback_data="service_types_done")])
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="store_section")])
    return InlineKeyboardMarkup(rows)


async def service_toggle_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    try:
        key = query.data.split(":", 1)[1]
    except (IndexError, ValueError):
        await query.answer("⚠️", show_alert=True)
        return
    if key not in MESSAGE_TYPE_LABELS:
        await query.answer("⚠️", show_alert=True)
        return
    selected = list(context.user_data.get("new_service_types") or [])
    if key in selected:
        selected.remove(key)
    else:
        selected.append(key)
    context.user_data["new_service_types"] = selected
    await query.answer()
    await query.edit_message_text(
        "📋 *الخطوة 2/5*: اختر نوع المعلومات المطلوبة من المشتري\n\n"
        "<i>يمكنك اختيار أكثر من نوع</i>",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_service_types_keyboard(selected))


async def service_types_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    selected = list(context.user_data.get("new_service_types") or [])
    if not selected:
        await query.answer("⚠️ اختر نوعاً واحداً على الأقل.", show_alert=True)
        return
    context.user_data["store_action"] = "add_service_price"
    cat_id = context.user_data.get("current_category_id")
    await query.edit_message_text(
        f"✅ الأنواع المختارة: {' + '.join(MESSAGE_TYPE_LABELS[t][1] for t in selected)}\n\n"
        "💰 *الخطوة 3/5*: أرسل سعر الوحدة (بالدولار):\n"
        "<i>مثال: 5.00</i>",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))


async def _skip_qty_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_service_min"] = None
    context.user_data["new_service_max"] = None
    context.user_data["store_action"] = "add_service_message"
    cat_id = context.user_data.get("current_category_id")
    await update.effective_message.reply_text(
        "✅ تم تخطي الحدود — <b>الكمية ستكون ثابتة = 1</b>\n"
        "لن يظهر حقل العدد للعضو إطلاقاً.\n\n"
        "📝 *أرسل رسالة الطلب للعميل (ما يظهر له بعد الشراء):*",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))


async def _skip_qty_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_service_max"] = None
    context.user_data["store_action"] = "add_service_message"
    cat_id = context.user_data.get("current_category_id")
    mn = context.user_data.get("new_service_min")
    await update.effective_message.reply_text(
        f"✅ تم تخطي الحد الأعلى (بلا سقف). الحد الأدنى: <b>{mn}</b>\n\n"
        "📝 *أرسل رسالة الطلب للعميل (ما يظهر له بعد الشراء):*",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))


async def service_qty_skip_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    context.user_data["store_action"] = "add_service_qty_min"
    await _skip_qty_min(update, context)


async def service_qty_skip_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    context.user_data["store_action"] = "add_service_qty_max"
    await _skip_qty_max(update, context)


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
    toggle_label = "👁️ إخفاء حسابات لكل الدول" if visible else "👁️ إظهار حسابات لكل الدول"
    buttons.append((toggle_label, "toggle_tg_lion_store"))
    buttons.append(("➕ إضافة فئة", "store_add_category"))
    buttons.append(("🗑️ حذف فئة", "store_delete_category_menu"))
    buttons.append(("🔙 إعدادات المالك", "owner_panel"))
    await query.edit_message_text("🛒 *إدارة المبيعات*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def toggle_tg_lion_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    config = load_config()
    visible = not config.get("tg_lion_store_visible", True)
    config["tg_lion_store_visible"] = visible
    save_config(config)
    await query.answer("✅ تم إظهار القسم." if visible else "✅ تم إخفاء القسم.")
    await owner_store_section(update, context)


async def store_delete_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
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
        n = len(cat.get("services", []))
        buttons.append((f"🗑️ {cat['name']} ({n} خدمة)"[:64], f"store_delete_category_confirm:{cat['id']}"))
    buttons.append(("🔙 المبيعات", "store_section"))
    await query.edit_message_text(
        "🗑️ *حذف فئة*\n\n"
        "⚠️ <b>حذف الفئة يحذف كل الخدمات داخلها نهائياً.</b>\n\n"
        "اختر الفئة:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_vertical(buttons))


async def store_delete_category_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
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
    n = len(category.get("services", []))
    await query.edit_message_text(
        f"⚠️ <b>تأكيد الحذف</b>\n\n"
        f"📂 <b>الفئة:</b> {html.escape(category['name'])}\n"
        f"📦 <b>الخدمات:</b> {n}\n\n"
        f"🚨 <b>سيتم حذف الفئة وكل خدماتها نهائياً!</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            ("✅ نعم، احذف نهائياً", f"store_delete_category_execute:{cat_id}"),
            ("❌ إلغاء", "store_delete_category_menu"),
        ]))


async def store_delete_category_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    try:
        cat_id = query.data.split(":", 1)[1]
    except (IndexError, ValueError):
        await query.answer("⚠️", show_alert=True)
        return
    config = load_config()
    before = len(config.get("store_categories", []))
    config["store_categories"] = [c for c in config.get("store_categories", []) if c["id"] != cat_id]
    if len(config["store_categories"]) == before:
        await query.answer("⚠️ لم يتم العثور على الفئة.", show_alert=True)
        await store_delete_category_menu(update, context)
        return
    save_config(config)
    await query.answer("✅ تم حذف الفئة.", show_alert=True)
    await owner_store_section(update, context)


async def store_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    await query.edit_message_text("✏️ *اسم الفئة:*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single("🔙 إلغاء", "store_section"))
    context.user_data["store_action"] = "add_category"


async def store_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
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
            types = s.get("message_types") or []
            types_str = " + ".join(MESSAGE_TYPE_LABELS[t][1] for t in types if t in MESSAGE_TYPE_LABELS) or "—"
            msg += f"{idx}. 🛒 {s['name']} — 💰 ${float(s['price']):.2f}\n   📋 {types_str}\n"
    else:
        msg += "📭 لا توجد خدمات.\n"
    buttons = [
        ("➕ إضافة خدمة", f"store_add_service:{cat_id}"),
        ("✏️ تعديل خدمة", f"store_edit_service:{cat_id}"),
        ("🗑️ حذف خدمة", f"store_delete_service:{cat_id}"),
        ("🔙 المبيعات", "store_section"),
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def store_add_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    context.user_data["current_category_id"] = cat_id
    context.user_data["new_service_types"] = []
    context.user_data["store_action"] = "add_service_name"
    await query.edit_message_text(
        "📝 *الخطوة 1/5*: أرسل اسم الخدمة:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", f"store_category:{cat_id}"))


async def store_edit_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    config = load_config()
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.")
        return
    services = category.get("services", [])
    if not services:
        await query.edit_message_text("📭 لا توجد خدمات.",
                                      reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))
        return
    buttons = [(f"✏️ {s['name']} — ${float(s['price']):.2f}",
                f"service_detail:{cat_id}:{s['id']}") for s in services]
    buttons.append(("🔙 الفئة", f"store_category:{cat_id}"))
    await query.edit_message_text("✏️ *اختر الخدمة:*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def store_delete_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    cat_id = query.data.split(":", 1)[1]
    config = load_config()
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️ الفئة غير موجودة.")
        return
    services = category.get("services", [])
    if not services:
        await query.edit_message_text("📭 لا توجد خدمات.",
                                      reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))
        return
    buttons = [(f"❌ {s['name']} — ${float(s['price']):.2f}",
                f"store_delete_service_confirm:{cat_id}:{s['id']}") for s in services]
    buttons.append(("🔙 الفئة", f"store_category:{cat_id}"))
    await query.edit_message_text("🗑️ *حذف خدمة*", parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def store_delete_service_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    cat_id = parts[1]
    service_id = parts[2]
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
        await query.answer("⚠️ الخدمة غير موجودة.", show_alert=True)
        return
    await query.edit_message_text(
        f"⚠️ <b>تأكيد حذف الخدمة</b>\n\n"
        f"🛒 الاسم: {html.escape(service['name'])}\n\n"
        f"سيتم الحذف نهائياً — لا يمكن التراجع.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_vertical([
            ("✅ نعم، احذف", f"delete_service:{cat_id}:{service_id}"),
            ("❌ إلغاء", f"service_detail:{cat_id}:{service_id}"),
        ]))


async def delete_service_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
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
    await query.answer("✅ تم الحذف.")
    await query.edit_message_text("✅ تم حذف الخدمة.",
                                  reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))


async def service_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer("⚠️", show_alert=True)
        return
    cat_id = parts[1]
    service_id = parts[2]
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
        await query.answer("⚠️ الخدمة غير موجودة.", show_alert=True)
        return
    types = service.get("message_types") or []
    types_str = " + ".join(MESSAGE_TYPE_LABELS[t][1] for t in types if t in MESSAGE_TYPE_LABELS) or "—"
    msg = (f"🛒 *تفاصيل الخدمة*\n\n"
           f"📝 الاسم: {html.escape(service['name'])}\n"
           f"💰 السعر: ${float(service['price']):.2f}\n"
           f"📋 المعلومات المطلوبة: {types_str}\n")
    mn = service.get("min_qty")
    mx = service.get("max_qty")
    if mn is None and mx is None:
        msg += "🔢 الكمية: 🔒 ثابتة = 1 (لا يظهر حقل العدد)\n"
    else:
        parts_qty = []
        if mn is not None:
            parts_qty.append(f"أدنى: {int(mn)}")
        if mx is not None:
            parts_qty.append(f"أعلى: {int(mx)}")
        msg += "🔢 الكمية: " + " | ".join(parts_qty) + "\n"
    msg += f"\n📝 رسالة الطلب:\n<code>{html.escape(service.get('message', ''))}</code>"

    buttons = [
        ("✏️ تعديل الاسم", f"store_edit_field:name:{cat_id}:{service_id}"),
        ("💰 تعديل السعر", f"store_edit_field:price:{cat_id}:{service_id}"),
        ("📋 تعديل المعلومات المطلوبة", f"service_edit_types:{cat_id}:{service_id}"),
        ("🔢 تعديل الحد الأدنى", f"store_edit_field:qty_min:{cat_id}:{service_id}"),
        ("🔢 تعديل الحد الأعلى", f"store_edit_field:qty_max:{cat_id}:{service_id}"),
        ("📝 تعديل رسالة الطلب", f"store_edit_field:message:{cat_id}:{service_id}"),
        ("🗑️ حذف الخدمة", f"store_delete_service_confirm:{cat_id}:{service_id}"),
        ("🔙 الفئة", f"store_category:{cat_id}"),
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def store_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
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
        await query.edit_message_text("⚠️ الخدمة غير موجودة.",
                                      reply_markup=kb_single("🔙 الفئة", f"store_category:{cat_id}"))
        return

    context.user_data["store_action"] = f"edit_service_{field}"
    context.user_data["edit_service_cat"] = cat_id
    context.user_data["edit_service_id"] = service_id
    field_labels = {"name": "الاسم الجديد", "price": "السعر الجديد",
                    "message": "الرسالة الجديدة", "qty_min": "الحد الأدنى الجديد",
                    "qty_max": "الحد الأعلى الجديد"}
    extra_hint = ""
    if field == "qty_min":
        extra_hint = "\n\n⚠️ <i>أرسل 'تخطي' لجعل الكمية ثابتة = 1 (مع إلغاء الحد الأعلى أيضاً)</i>"
    elif field == "qty_max":
        extra_hint = "\n\n⚠️ <i>أرسل 'تخطي' لإلغاء السقف الأعلى</i>"
    await query.edit_message_text(
        f"✏️ *تعديل {field_labels.get(field, field)}*\n\n"
        f"للخدمة: `{service['name']}`\n\n"
        f"📌 أرسل القيمة الجديدة:{extra_hint}\n\n"
        f"<i>أو 'إلغاء'</i>",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single("🔙 إلغاء", f"service_detail:{cat_id}:{service_id}"))


async def service_edit_types(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    parts = query.data.split(":")
    cat_id = parts[1]
    service_id = parts[2]
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
        await query.answer("⚠️ الخدمة غير موجودة.", show_alert=True)
        return
    context.user_data["edit_types_cat"] = cat_id
    context.user_data["edit_types_service"] = service_id
    context.user_data["edit_types_selected"] = list(service.get("message_types") or [])
    await query.edit_message_text(
        "📋 *تعديل أنواع المعلومات المطلوبة*\n\n"
        "<i>اضغط لتبديل التحديد ثم اضغط متابعة</i>",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_service_edit_types_keyboard(context.user_data["edit_types_selected"]))


def _service_edit_types_keyboard(selected: list) -> InlineKeyboardMarkup:
    rows = []
    for key, (label, _) in MESSAGE_TYPE_LABELS.items():
        mark = "✅ " if key in selected else "☐ "
        rows.append([InlineKeyboardButton(f"{mark}{label}", callback_data=f"service_edit_toggle:{key}")])
    rows.append([InlineKeyboardButton("💾 حفظ", callback_data="service_edit_types_save")])
    rows.append([InlineKeyboardButton("❌ إلغاء", callback_data="store_section")])
    return InlineKeyboardMarkup(rows)


async def service_edit_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    try:
        key = query.data.split(":", 1)[1]
    except (IndexError, ValueError):
        await query.answer("⚠️", show_alert=True)
        return
    if key not in MESSAGE_TYPE_LABELS:
        await query.answer("⚠️", show_alert=True)
        return
    selected = list(context.user_data.get("edit_types_selected") or [])
    if key in selected:
        selected.remove(key)
    else:
        selected.append(key)
    context.user_data["edit_types_selected"] = selected
    await query.answer()
    await query.edit_message_text(
        "📋 *تعديل أنواع المعلومات المطلوبة*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_service_edit_types_keyboard(selected))


async def service_edit_types_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫", show_alert=True)
        return
    selected = list(context.user_data.get("edit_types_selected") or [])
    if not selected:
        await query.answer("⚠️ اختر نوعاً واحداً على الأقل.", show_alert=True)
        return
    cat_id = context.user_data.get("edit_types_cat")
    service_id = context.user_data.get("edit_types_service")
    config = load_config()
    updated = False
    for cat in config.get("store_categories", []):
        if cat["id"] != cat_id:
            continue
        for s in cat["services"]:
            if s["id"] == service_id:
                s["message_types"] = selected
                updated = True
                break
        break
    if updated:
        save_config(config)
        context.user_data.pop("edit_types_cat", None)
        context.user_data.pop("edit_types_service", None)
        context.user_data.pop("edit_types_selected", None)
        await query.answer("✅ تم الحفظ.")
        await query.edit_message_text(
            "✅ تم تحديث أنواع المعلومات.",
            reply_markup=kb_single("🔙 تفاصيل الخدمة", f"service_detail:{cat_id}:{service_id}"))
    else:
        await query.answer("⚠️ لم يتم الحفظ.", show_alert=True)


# ==================== REFERRAL HANDLER ====================
def parse_referral_payload(arg: str) -> Tuple[int, str]:
    """(النوع، الكود): 1 = إحالة عادية، 2 = إحالة البوت الأول، 0 = غير صالح."""
    arg = (arg or "").strip()
    if arg.upper().startswith("B2_"):
        code, kind = arg[3:], 2
    else:
        code, kind = arg, 1
    if len(code) == 8 and code.isalnum():
        return kind, code
    return 0, ""


def register_start(user_id: int, payload: str) -> str:
    """يسجّل المستخدم عند أول /start ويحفظ الإحالة قبل بوابة الاشتراك.

    كان الكود يضيع سابقاً عند ظهور بوابة القنوات؛ الحفظ هنا يبقيه حتى ينضم
    العضو. «الجديد في هذا البوت» = لا يوجد له سجل قبل هذا الـ /start.
    يعيد رسالة تنبيه (أو نصاً فارغاً).
    """
    existed = str(user_id) in load_json(USERS_DB)
    user_data = get_user(user_id)
    changed = not existed
    notice = ""
    kind, code = parse_referral_payload(payload)
    if kind and user_id != OWNER_ID:
        if kind == 1:
            if not user_data.get("referred_by") and not user_data.get("pending_ref1"):
                user_data["pending_ref1"] = code
                changed = True
        elif existed:
            if not user_data.get("ref2"):
                notice = "⚠️ رابط إحالة البوت الأول للأعضاء الجدد فقط، ولا تُحتسب الإحالة لك."
        elif not user_data.get("ref2"):
            user_data["ref2"] = {"code": code, "status": "pending", "new_in_bot2": True,
                                 "started_at": datetime.now(timezone.utc).isoformat()}
            changed = True
    if changed:
        save_user(user_id, user_data)
    return notice


async def _send_or_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
            return
        except Exception:
            pass
    await context.bot.send_message(update.effective_chat.id, text,
                                   parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)


def build_partner_link(config: dict, user_id: int = 0) -> str:
    """رابط البوت الأول كما ضبطه المالك، نفسه لكل الأعضاء."""
    return str(config.get("ref2_partner_link") or "").strip()


async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE, referral_code: str):
    """الإحالة العادية (النوع 1)."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    user_data = get_user(user_id)
    if user_data.get("referral_code") == referral_code:
        await context.bot.send_message(chat_id, "⚠️ لا يمكنك استخدام رابطك!")
        return
    if user_data.get("referred_by"):
        await context.bot.send_message(chat_id, "ℹ️ أنت مشترك بالفعل.")
        return
    users = load_json(USERS_DB)
    referrer_id = None
    for uid, encrypted_data in users.items():
        data = decrypt_user_data(encrypted_data)
        if data.get("referral_code") == referral_code:
            referrer_id = int(uid)
            break
    if not referrer_id or referrer_id == user_id:
        await context.bot.send_message(chat_id, "❌ رابط غير صالح.")
        return
    user_data["referred_by"] = referrer_id
    save_user(user_id, user_data)
    await context.bot.send_message(
        chat_id, f"✅ *تم تفعيل الإحالة!*\n\n👤 بواسطة: {referrer_id}",
        parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.send_message(
            chat_id=referrer_id,
            text=f"🎉 *إحالة جديدة!*\n\n👤 المستخدم {user_id} انضم.",
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass


async def show_ref2_activation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = build_partner_link(load_config(), update.effective_user.id)
    rows = []
    text = ("🎁 *إحالة البوت الأول*\n\n"
            "لتُحتسب الإحالة لصديقك الذي دعاك:\n"
            "1️⃣ اضغط «تفعيل البوت الأول» ثم اضغط Start هناك\n"
            "2️⃣ ارجع إلى هنا واضغط «تحققت من التفعيل»\n\n"
            "⚠️ يجب أن تكون جديداً في البوتين.")
    if link:
        rows.append([InlineKeyboardButton("🚀 تفعيل البوت الأول", url=link)])
    else:
        text += "\n\n⚠️ رابط البوت الأول غير متوفر حالياً."
    rows.append([InlineKeyboardButton("✅ تحققت من التفعيل", callback_data="ref2_verify")])
    rows.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")])
    await _send_or_edit(update, context, text, InlineKeyboardMarkup(rows))


async def process_pending_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """يُستدعى بعد اجتياز بوابة القنوات. يعيد True إن عرض شاشة بدل القائمة الرئيسية."""
    user_id = update.effective_user.id
    user_data = get_user(user_id)
    code1 = user_data.get("pending_ref1")
    if code1:
        user_data["pending_ref1"] = ""
        save_user(user_id, user_data)
        await handle_referral(update, context, code1)
        user_data = get_user(user_id)
    ref2 = user_data.get("ref2")
    if isinstance(ref2, dict) and ref2.get("status") == "pending":
        await show_ref2_activation(update, context)
        return True
    return False


def _fetch_partner_status_sync(user_id: int) -> Optional[dict]:
    api = normalize_partner_api_url(str(load_config().get("ref2_api_url") or ""))
    key = os.environ.get("PARTNER_API_KEY", "").strip()
    if not api or not key:
        return None
    request = urllib.request.Request(
        f"{api}/partner/ref-status?uid={int(user_id)}",
        headers={"X-Partner-Key": key, "Accept": "application/json",
                 "User-Agent": "mohammmedppp-bot"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read(65536).decode("utf-8"))
    except Exception as exc:
        logger.warning("Partner status request failed (%s)", type(exc).__name__)
        return None
    return payload if isinstance(payload, dict) else None


def _is_new_in_partner(status: dict, ref2: dict) -> bool:
    """جديد في البوت الأول = أول /start له هناك جاء بعد دخوله هذا البوت بالرابط.

    المستخدم القديم (بلا وقت مسجّل) أو من فتح البوت الأول قبل دخوله هنا لا
    يُعدّ جديداً. نسمح بفارق دقيقتين بين ساعتي الخادمين.
    """
    try:
        first_seen = datetime.fromisoformat(str(status.get("first_seen")))
        started_at = datetime.fromisoformat(str(ref2.get("started_at")))
    except (TypeError, ValueError):
        return False
    return first_seen >= started_at - timedelta(minutes=2)


async def _credit_ref2_referrer(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    """يدفع المكافأة مرة واحدة. يعيد: paid | invalid | already."""
    user_data = get_user(user_id)
    ref2 = user_data.get("ref2")
    if not isinstance(ref2, dict) or ref2.get("status") != "pending":
        return "already"
    referrer_id = None
    for uid, encrypted_data in load_json(USERS_DB).items():
        if decrypt_user_data(encrypted_data).get("referral_code") == ref2.get("code"):
            referrer_id = int(uid)
            break
    if not referrer_id or referrer_id == user_id:
        ref2["status"] = "invalid"
        user_data["ref2"] = ref2
        save_user(user_id, user_data)
        return "invalid"
    # نحفظ حالة «مدفوع» قبل أي انتظار آخر حتى لا تُدفع المكافأة مرتين.
    ref2.update({"status": "paid", "referrer": referrer_id,
                 "paid_at": datetime.now(timezone.utc).isoformat()})
    user_data["ref2"] = ref2
    save_user(user_id, user_data)
    bonus = clamp_money(float(load_config().get("ref2_bonus", 0.0) or 0.0))
    referrer = get_user(referrer_id)
    referrer["total_referrals"] = int(referrer.get("total_referrals", 0)) + 1
    referrer["ref2_count"] = int(referrer.get("ref2_count", 0)) + 1
    if bonus > 0:
        referrer["referral_earnings"] = clamp_money(float(referrer.get("referral_earnings", 0.0)) + bonus)
        referrer["balance"] = clamp_money(float(referrer.get("balance", 0.0)) + bonus)
        referrer["total_credited_balance"] = clamp_money(
            float(referrer.get("total_credited_balance", 0.0) or 0.0) + bonus)
        add_transaction(referrer, "referral", bonus, f"Bot-1 referral for user {user_id}")
    save_user(referrer_id, referrer)
    try:
        await context.bot.send_message(
            chat_id=referrer_id,
            text=(f"🎉 *إحالة جديدة مكتملة!*\n\nفعّل المستخدم `{user_id}` البوت الأول وانضم للقنوات."
                  + (f"\n💰 حصلت على ${bonus:.2f}" if bonus > 0 else "")),
            parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    await check_ref_contest_end(context)
    return "paid"


async def ref2_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await check_forced_channel(update, context):
        return
    user_id = update.effective_user.id
    ref2 = get_user(user_id).get("ref2")
    back = kb_single("🏠 القائمة الرئيسية", "main_menu")
    if not isinstance(ref2, dict) or ref2.get("status") != "pending":
        await query.answer("لا توجد إحالة قيد التحقق.", show_alert=True)
        return
    await query.answer("⏳ جاري التحقق...")
    status = await asyncio.to_thread(_fetch_partner_status_sync, user_id)
    if status is None:
        await query.answer("⚠️ تعذر الاتصال بالبوت الأول الآن، حاول بعد قليل.", show_alert=True)
        return
    if not status.get("started"):
        await query.answer("لم نجد تفعيلاً بعد. افتح البوت الأول من الزر واضغط Start ثم عد.", show_alert=True)
        return
    if not _is_new_in_partner(status, ref2):
        user_data = get_user(user_id)
        if isinstance(user_data.get("ref2"), dict) and user_data["ref2"].get("status") == "pending":
            user_data["ref2"]["status"] = "invalid"
            save_user(user_id, user_data)
        await _send_or_edit(update, context,
                            "⚠️ كنت مسجلاً في البوت الأول من قبل، لذلك لا تُحتسب هذه الإحالة.", back)
        return
    result = await _credit_ref2_referrer(context, user_id)
    if result == "paid":
        text = "✅ تم التحقق من تفعيل البوت الأول واحتُسبت الإحالة لصديقك. أهلاً بك!"
    elif result == "invalid":
        text = "⚠️ رابط الإحالة غير صالح."
    else:
        text = "ℹ️ هذه الإحالة محتسبة مسبقاً."
    await _send_or_edit(update, context, text, back)


__all__ = [name for name in globals() if not name.startswith("__")]
