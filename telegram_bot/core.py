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


# ==================== WITHDRAW STORE ====================

def _tg_lion_fetch_countries_sync() -> Tuple[Optional[List[dict]], str]:
    api_key = os.environ.get("TG_LION_API_KEY", "").strip()
    your_id = os.environ.get("TG_LION_USER_ID", "").strip()
    if not api_key or not your_id:
        return None, "not_configured"

    params = urllib.parse.urlencode({
        "action": "available_countries",
        "apiKey": api_key,
        "YourID": your_id,
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
            "code": code,
            "name": name,
            "flag": str(item.get("flag") or tg_lion_country_flag(code)),
            "calling_code": normalize_tg_lion_calling_code(raw_calling_code),
            "qty": quantity,
            "base_price_cents": base_price_cents,
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
    rows.append([
        InlineKeyboardButton("🔎 بحث عن الدولة", callback_data=f"tg_lion_search_start:{page}")
    ])
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
        await update.message.reply_text(
            "⚠️ تعذر تحميل قائمة الدول الآن. أعد إرسال الرمز بعد قليل."
        )
        return

    matches = tg_lion_find_countries_by_calling_code(countries, candidate)
    context.user_data.pop("tg_lion_search_page", None)
    back_keyboard = [[
        InlineKeyboardButton("↩️ الرجوع إلى الدول", callback_data=f"tg_lion_search_cancel:{page}")
    ]]
    if not matches:
        await update.message.reply_text(
            f"لم أجد دولة متاحة برمز الاتصال +{candidate}.",
            reply_markup=InlineKeyboardMarkup(back_keyboard),
        )
        return

    rows = []
    for country in matches:
        flag = country.get("flag") or tg_lion_country_flag(country["code"])
        label = (
            f"{flag} {country['name']} | "
            f"{cents_to_money(country['price_cents']):.2f}$"
        )
        rows.append([InlineKeyboardButton(
            label[:64],
            callback_data=f"tg_lion_search_result:{country['code']}:{page}",
        )])
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


async def show_tg_lion_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    country: dict,
    page: int,
    price_changed: bool = False,
):
    query = update.callback_query
    if not query:
        return
    price_cents = country["price_cents"]
    context.user_data["tg_lion_confirmation"] = {
        "code": country["code"],
        "price_cents": price_cents,
        "page": page,
        "expires_at": time.time() + 600,
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
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("إلغاء الطلب", callback_data=f"tg_lion_cancel:{page}")]
                ]),
            )
            return

        snapshot = {
            key: (key in user_data, user_data.get(key))
            for key in ("balance", "admin_received_balance", "spent_balance", "transactions")
        }
        if not debit_spendable_balance(user_data, total_cents):
            await query.edit_message_text("❌ رصيدك غير كافٍ. لم يتم تسجيل الطلب.")
            return
        add_transaction(
            user_data,
            "purchase",
            cents_to_money(total_cents),
            f"حسابات لكل الدول: {country['name']} ({country['code']})",
        )
        try:
            save_user(user.id, user_data)
        except Exception as exc:
            for key, (existed, value) in snapshot.items():
                if existed:
                    user_data[key] = value
                else:
                    user_data.pop(key, None)
            try:
                save_user(user.id, user_data)
            except Exception:
                logger.error("TG-Lion order balance rollback failed (%s)", type(exc).__name__)
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
            await context.bot.send_message(
                chat_id=purchase_channel,
                text=order_text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            for key, (existed, value) in snapshot.items():
                if existed:
                    user_data[key] = value
                else:
                    user_data.pop(key, None)
            try:
                save_user(user.id, user_data)
            except Exception:
                logger.error("TG-Lion order refund failed (%s)", type(exc).__name__)
            logger.warning("TG-Lion order receipt delivery failed (%s)", type(exc).__name__)
            await query.edit_message_text(
                "⚠️ تعذر إرسال الطلب إلى قروب الطلبات؛ أُعيد الرصيد إن أمكن. حاول مجدداً لاحقاً."
            )
            return

        context.user_data.pop("tg_lion_confirmation", None)
        await query.edit_message_text(
            f"✅ تم تسجيل طلبك وإرساله إلى قروب الطلبات.\n\n"
            f"🧾 رقم الطلب: <code>{order_ref}</code>\n"
            f"🌍 الدولة: <b>{html.escape(country['name'])}</b>\n"
            f"💰 المخصوم: <b>{cents_to_money(total_cents):.2f}$</b>\n"
            "سيتم تنفيذ الطلب يدوياً من الإدارة.",
            parse_mode=ParseMode.HTML,
        )


async def withdraw_store(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = update.effective_user.id
    send_message = (query.edit_message_text if query else update.effective_message.reply_text)
    config = load_config()
    categories = config.get("store_categories", [])
    lion_visible = config.get("tg_lion_store_visible", True)
    if not categories and not lion_visible:
        await send_message(t(uid, "store_empty"),
                           reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    buttons = [(f"📂 {cat['name']}", f"user_category:{cat['id']}") for cat in categories]
    if lion_visible:
        buttons.append(("🌍 حسابات لكل الدول", "tg_lion_catalog"))
    buttons.append((t(uid, "main_menu"), "main_menu"))
    await send_message(t(uid, "store_title"), parse_mode=ParseMode.MARKDOWN,
                       reply_markup=kb_vertical(buttons))


async def user_category_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    cat_id = query.data.split(":", 1)[1]
    config = load_config()
    category = next((c for c in config.get("store_categories", []) if c["id"] == cat_id), None)
    if not category:
        await query.edit_message_text("⚠️",
                                      reply_markup=kb_single(t(uid, "store_back"), "withdraw_store"))
        return
    services = category.get("services", [])
    if not services:
        await query.edit_message_text("📭",
                                      reply_markup=kb_single(t(uid, "store_back"), "withdraw_store"))
        return
    buttons = [(t(uid, "store_buy_btn", name=s['name'], price=s['price']),
                f"user_buy:{s['id']}:{cat_id}") for s in services]
    buttons.append((t(uid, "store_back"), "withdraw_store"))
    await query.edit_message_text(f"📂 *{category['name']}*",
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def user_buy_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
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
        await query.edit_message_text("⚠️",
                                      reply_markup=kb_single(t(uid, "store_back"), "withdraw_store"))
        return
    user_data = get_user(uid)
    price_cents = money_to_cents(service.get("price", 0))
    price = cents_to_money(price_cents)
    balance_cents = spendable_balance_cents(user_data)
    if balance_cents < price_cents:
        await query.edit_message_text(
            t(uid, "store_buy_insufficient",
              balance=cents_to_money(balance_cents), price=price),
            reply_markup=kb_single(t(uid, "store_back"), "withdraw_store"))
        return
    PENDING_PURCHASES[uid] = {
        "service_id": service_id,
        "service_name": service.get("name", ""),
        "service_price": price,
        "service_message": service.get("message", ""),
        "purchased_at": datetime.now().isoformat(),
    }
    save_pending_purchases()
    bot_username = (await context.bot.get_me()).username
    total_emails = user_data.get("total_approved_emails", 0)
    channel_1_text = (f"🛒 <b>طلب شراء جديد</b>\n\n"
                      f"🤖 @{html.escape(bot_username or 'غير معروف')}\n"
                      f"📦 <code>{html.escape(str(service.get('name', '')))}</code>\n"
                      f"💰 <code>${service['price']:.2f}</code>\n"
                      f"📧 عدد الإيميلات: <code>{total_emails}</code>")
    purchase_channel_1, _ = get_configured_purchase_channels()
    if purchase_channel_1:
        try:
            await context.bot.send_message(chat_id=purchase_channel_1, text=channel_1_text,
                                           parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception("Could not send purchase notification.")
    await query.edit_message_text(
        t(uid, "store_buy_success", name=service.get('name', ''),
          price=price, message=service.get('message', '')),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb_single(t(uid, "store_back"), "withdraw_store"))


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


async def handle_purchase_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram_bot.application import text_input
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in PENDING_PURCHASES:
        await text_input(update, context)
        return
    purchase = PENDING_PURCHASES[user_id]
    user_data = get_user(user_id)
    price_cents = money_to_cents(purchase.get("service_price", 0))
    price = cents_to_money(price_cents)
    balance_cents = spendable_balance_cents(user_data)
    if balance_cents < price_cents:
        await update.message.reply_text("❌ رصيدك غير كافٍ.",
                                        reply_markup=kb_single("🔙 قسم السحب", "withdraw_store"))
        PENDING_PURCHASES.pop(user_id, None)
        save_pending_purchases()
        return
    debit_spendable_balance(user_data, price_cents)
    add_transaction(user_data, "purchase", price, f"شراء: {purchase.get('service_name', '')}")
    save_user(user_id, user_data)
    user = update.effective_user
    user_name = user.full_name or "غير معروف"
    user_username = user.username or "لا يوجد"
    total_emails = user_data.get("total_approved_emails", 0)
    _, purchase_channel_2 = get_configured_purchase_channels()
    if purchase_channel_2:
        text2 = (f"📋 <b>طلب شراء مكتمل</b>\n\n"
                 f"👤 <b>{html.escape(user_name)}</b>\n"
                 f"🆔 @{html.escape(user_username)}\n"
                 f"🆔 <code>{user_id}</code>\n"
                 f"📦 <code>{html.escape(str(purchase['service_name']))}</code>\n"
                 f"💰 <code>${price:.2f}</code>\n"
                 f"📧 <code>{total_emails}</code>\n\n"
                 f"📝 <b>الرسالة:</b>\n<code>{html.escape(text)}</code>\n"
                 f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            await context.bot.send_message(chat_id=purchase_channel_2, text=text2,
                                           parse_mode=ParseMode.HTML,
                                           reply_markup=kb_single("✅ تم الإيصال", f"deliver_order:{user_id}"))
        except Exception:
            logger.exception("Could not send member message.")
    if OWNER_ID:
        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"📩 *رسالة من مستخدم*\n\n👤 `{user_name}`\n🆔 @{user_username}\n"
                     f"🆔 `{user_id}`\n📦 `{purchase['service_name']}`\n"
                     f"💰 `${price:.2f}`\n\n📝 `{text}`",
                parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"Error: {e}")
    await update.message.reply_text(
        f"✅ *تم استلام رسالتك!*\n\n💰 تم خصم `${price:.2f}`.\n"
        f"📝 `{text}`\n\n_📌 سيتم التواصل معك قريباً._",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
    PENDING_PURCHASES.pop(user_id, None)
    save_pending_purchases()


async def my_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram_bot.owner import admin_balance_stats
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    user = get_user(uid)
    if is_admin(uid):
        balances = admin_balance_stats(user, uid)
        await query.edit_message_text(
            f"💰 <b>Admin Wallet</b>\n\n"
            f"⏳ Pending: ${balances['pending']:.2f}\n"
            f"🔒 Hold (24/48h): ${balances['hold']:.2f}\n"
            f"🛠 Completed emails: {balances['completed_count']}\n"
            f"⏳ Restricted rewards: {balances['pending_count']}\n"
            f"✅ Released rewards: {balances['released_count']}\n"
            f"🛠 Admin rewards restricted: ${balances['admin_pending']:.2f}\n"
            f"✅ Total admin rewards received: ${balances['received']:.2f}\n"
            f"💳 Available admin balance: ${balances['available_admin']:.2f}\n"
            f"📊 Total available: ${balances['total_owned']:.2f}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))
        return
    text = (
        f"{t(uid, 'wallet_title')}\n\n"
        f"{t(uid, 'wallet_pending', amount=float(user.get('pending_balance', 0.0)))}\n"
        f"{t(uid, 'wallet_hold', amount=float(user.get('hold_balance', 0.0)))}\n"
        f"{t(uid, 'wallet_balance', amount=float(user.get('balance', 0.0)))}"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_single(t(uid, "main_menu"), "main_menu"))


async def tutorials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    config = load_config()
    buttons = []
    for key, label in [
        ("general", t(uid, "tutorials_general")),
        ("email", t(uid, "tutorials_email")),
        ("password", t(uid, "tutorials_password")),
        ("totp", t(uid, "tutorials_totp")),
        ("app_pass", t(uid, "tutorials_app_pass")),
        ("leave", t(uid, "tutorials_leave")),
        ("phone", t(uid, "tutorials_phone")),
    ]:
        if config.get(f"video_{key}") and Path(config.get(f"video_{key}", "")).exists():
            buttons.append((label, f"play_video:{key}"))
    buttons.append((t(uid, "main_menu"), "main_menu"))
    await query.edit_message_text(t(uid, "tutorials_title"),
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def play_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    vtype = query.data.split(":")[1]
    config = load_config()
    path = config.get(f"video_{vtype}")
    if path and Path(path).exists():
        try:
            await context.bot.send_video(chat_id=uid, video=open(path, "rb"),
                                         caption=f"📹 {t(uid, f'tutorials_{vtype}')}",
                                         parse_mode=ParseMode.MARKDOWN,
                                         supports_streaming=True)
            await tutorials(update, context)
        except Exception as e:
            logger.error(f"Error: {e}")
            await query.edit_message_text("⚠️",
                                          reply_markup=kb_single(t(uid, "btn_back"), "tutorials"))
    else:
        await query.edit_message_text("⚠️",
                                      reply_markup=kb_single(t(uid, "btn_back"), "tutorials"))


def generate_referral_code():
    return secrets.token_hex(4).upper()


async def referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = update.effective_user.id
    user_data = get_user(uid)
    referral_code = user_data.get("referral_code", "")
    if not referral_code:
        referral_code = generate_referral_code()
        user_data["referral_code"] = referral_code
        save_user(uid, user_data)
    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={referral_code}"
    msg = t(uid, "referral_title", link=referral_link,
            earnings=float(user_data.get('referral_earnings', 0.0)),
            count=user_data.get('total_referrals', 0))
    buttons = [
        (t(uid, "referral_btn_copy"), f"copy_referral:{referral_code}"),
        (t(uid, "main_menu"), "main_menu"),
    ]
    await query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_vertical(buttons))


async def copy_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_forced_channel(update, context):
        return
    query = update.callback_query
    uid = query.from_user.id
    code = query.data.split(":")[1]
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={code}"
    buttons = [
        (t(uid, "referral_btn_show"), "referral_menu"),
        (t(uid, "main_menu"), "main_menu"),
    ]
    await query.edit_message_text(t(uid, "referral_link_shown", link=link),
                                  parse_mode=ParseMode.MARKDOWN,
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


async def handle_store_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram_bot.members import main_menu
    if update.effective_user.id != OWNER_ID:
        return
    text = update.message.text.strip()
    action = context.user_data.get("store_action")
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
    elif action == "set_channel":
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
    elif action in {"set_purchase_channel_1", "set_purchase_channel_2"}:
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
    elif action == "add_service_name":
        context.user_data["store_service_name"] = text
        context.user_data["store_action"] = "add_service_price"
        await update.message.reply_text(
            "💰 *الخطوة 2/3*: أرسل السعر:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb_single("🔙 إلغاء",
                                   f"store_category:{context.user_data.get('current_category_id')}"))
    elif action == "add_service_price":
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
    elif action == "add_service_message":
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


# ==================== STORE SECTION ====================
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
