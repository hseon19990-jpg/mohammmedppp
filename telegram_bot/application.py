"""Telegram update routing and application setup."""

from telegram_bot.core import *
from telegram_bot.members import *
from telegram_bot.owner import *
from telegram_bot.approvals import *
from telegram_bot.store import *
from telegram_bot.locales import TRANSLATIONS

from telegram_bot.members import restore_leave_checks


# ==================== TEXT INPUT ROUTER ====================
async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    user_id = update.effective_user.id

    if not await maintenance_access_allowed(update):
        return
    if not await check_forced_channel(update, context):
        return

    # ═══ 1. API management input ═══
    if context.user_data.get("api_add_step") == "key":
        await handle_api_key_input(update, context); return

    # ═══ 2. Global withdraw limits (owner) ═══
    mode = context.user_data.get("mode")
    if mode in ("withdraw_set_min", "withdraw_set_max"):
        await handle_withdraw_input(update, context); return

    # ═══ 3. Daily gift settings (owner) ═══
    if mode in ("daily_gift_set_emails", "daily_gift_set_reward"):
        await handle_daily_gift_input(update, context); return

    # ═══ 4. Store flow (buy_flow / legacy) ═══
    if context.user_data.get("buy_flow"):
        await handle_purchase_message(update, context); return
    if user_id in PENDING_PURCHASES:
        await handle_purchase_message(update, context); return

    # ═══ 5. Store action (add_category, add_service_*, set_channel, إلخ) ═══
    #     ⚠️ مهم جداً: هذا الفحص يجب أن يكون قبل أي فحص step آخر
    store_action = str(context.user_data.get("store_action", "") or "")
    if store_action:
        await handle_store_input(update, context); return

    # ═══ 6. Sell new email flow ═══
    sell_step = context.user_data.get("step")
    if sell_step == "sell_new_email_totp":
        await handle_sell_new_email_totp(update, context); return
    if sell_step == "sell_new_email_app_pass":
        await handle_sell_new_email_app_pass(update, context); return

    # ═══ 7. Support link input ═══
    if context.user_data.get("step") == "support_link_input":
        await handle_support_link_input(update, context); return

    # ═══ 8. TG-Lion search ═══
    if "tg_lion_search_page" in context.user_data:
        await tg_lion_search_country_input(update, context); return

    # ═══ 9. Contest build ═══
    if context.user_data.get("contest_build_step"):
        if user_id != OWNER_ID:
            return
        await handle_contest_build_input(update, context); return

    # ═══ 10. Account session (add_account_step) ═══
    session = SESSIONS.get(user_id)
    if session and session.step:
        admin_steps = ("admin_approval_step", "step", "mode", "store_action",
                       "contest_build_step")
        has_admin_step = any(context.user_data.get(k) for k in admin_steps)
        if not has_admin_step:
            await add_account_step(update, context); return

    # ═══ 11. Admin approval ═══
    admin_approval_step = context.user_data.get("admin_approval_step")
    if admin_approval_step == "waiting_totp":
        await handle_admin_totp_input(update, context); return
    if admin_approval_step == "waiting_app_pass":
        await handle_admin_app_pass_input(update, context); return
    if admin_approval_step == "waiting_acceptance_choice":
        await prompt_admin_acceptance_choice(
            update, context,
            context.user_data.get("admin_completing_uid"),
            context.user_data.get("admin_completing_index"))
        return

    # ═══ 12. Add admin input ═══
    if context.user_data.get("step") == "add_admin_input":
        await handle_add_admin_input(update, context); return

    # ═══ 13. Various step inputs ═══
    step = context.user_data.get("step")
    if step == "check_email_input":
        await handle_check_email_input(update, context); return
    if step == "reject_reason_text":
        await handle_reject_reason_text(update, context); return
    if step == "reject_approved_reason_text":
        await handle_reject_approved_reason_text(update, context); return
    if step == "deduct_points_input":
        await handle_deduct_points_input(update, context); return
    if step == "give_points_input":
        await handle_give_points_input(update, context); return
    if step == "give_points_by_id_input" or step == "deduct_points_by_id_input":
        await handle_points_by_id_input(update, context); return
    if step == "check_member_input":
        await handle_member_check_input(update, context); return
    if step == "editing_field":
        await handle_edit_field_input(update, context); return

    # ═══ 14. Approval TOTP / App Pass ═══
    approval_step = context.user_data.get("approval_step")
    if approval_step == "waiting_totp":
        await handle_approval_totp(update, context); return
    if approval_step == "waiting_app_pass":
        await handle_approval_app_pass(update, context); return

    # ═══ 15. Tier price ═══
    if mode == "set_tier_price":
        if user_id != OWNER_ID: return
        try:
            price = float(text)
            if price <= 0:
                await update.message.reply_text("⚠️ السعر > 0!")
                return
            tier = context.user_data.get("setting_tier")
            acc_type = context.user_data.get("setting_type", "old")
            if tier and acc_type:
                config = load_config()
                config[f"{acc_type}_tier_{tier}_price"] = price
                save_config(config)
                await update.message.reply_text(f"✅ تم تحديث سعر المستوى {tier} ({acc_type}): ${price:.2f}")
                context.user_data.pop("mode", None)
                context.user_data.pop("setting_tier", None)
                context.user_data.pop("setting_type", None)
                await set_tier_prices(update, context)
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً.")
        return

    # ═══ 16. Referral bonus ═══
    if mode == "set_referral_bonus":
        if user_id != OWNER_ID: return
        try:
            bonus = float(text)
            if bonus < 0:
                await update.message.reply_text("⚠️ المبلغ >= 0!")
                return
            config = load_config()
            config["referral_bonus"] = bonus
            save_config(config)
            await update.message.reply_text(f"✅ تم تحديث مكافأة الإحالة: ${bonus:.2f}")
            context.user_data.pop("mode", None)
            await owner_panel(update, context)
        except ValueError:
            await update.message.reply_text("⚠️ أرسل رقماً.")
        return

    # ═══ 17. Fallback: session step ═══
    if session and session.step:
        await add_account_step(update, context)
        return


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_access_allowed(update):
        return
    if not await check_forced_channel(update, context):
        return
    clear_edit_state(context)
    SESSIONS.pop(update.effective_user.id, None)
    save_sessions()
    args = context.args
    if args and args[0]:
        referral_code = args[0]
        if len(referral_code) == 8 and referral_code.isalnum():
            context.user_data["referral_code"] = referral_code
            user_data = get_user(update.effective_user.id)
            context.user_data["my_referral_code"] = user_data.get("referral_code", "")
            await handle_referral(update, context, referral_code)
            return
    await main_menu(update, context)


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_access_allowed(update):
        return
    await withdraw_store(update, context)


# ==================== ROUTER ====================
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data
    if data == "noop":
        await query.answer()
        return
    if not await maintenance_access_allowed(update):
        return
    await query.answer()
    if not await check_forced_channel(update, context):
        return

    if data == "main_menu":
        await main_menu(update, context)
    elif data == "language_menu":
        await language_menu(update, context)
    elif data.startswith("set_language:"):
        await set_language_handler(update, context)
    elif data == "daily_gift_view":
        await daily_gift_view(update, context)
    elif data == "add_account":
        await add_account_start(update, context)
    elif data == "sell_new_email":
        await sell_new_email_start(update, context)
    elif data == "sell_new_email_complete":
        await sell_new_email_complete(update, context)
    elif data == "sell_new_email_finish_prompt":
        await sell_new_email_finish_prompt(update, context)
    elif data == "sell_new_email_finish_confirm":
        await sell_new_email_finish_confirm(update, context)
    elif data == "sell_new_email_cancel":
        await sell_new_email_cancel(update, context)
    elif data == "sell_new_email_finish":
        await sell_new_email_finish(update, context)
    elif data.startswith("sell_show_video:"):
        await sell_show_video(update, context)
    elif data == "cancel":
        await add_account_cancel(update, context)
    elif data.startswith("complete_more:"):
        await complete_more_callback(update, context)
    elif data.startswith("finish_request:"):
        await finish_request_callback(update, context)
    elif data.startswith("back_to_offer:"):
        await back_to_offer_callback(update, context)
    elif data == "my_wallet":
        await my_wallet(update, context)
    elif data == "my_transactions":
        await my_transactions(update, context)
    elif data == "my_accounts":
        await my_accounts(update, context)
    elif data == "top_sellers" or data == "top_sellers_owner":
        await top_sellers_view(update, context)
    elif data == "rejected_emails":
        await view_member_rejected_emails(update, context)
    elif data == "tutorials":
        await tutorials(update, context)
    elif data.startswith("play_video:"):
        await play_video(update, context)
    elif data.startswith("show_video:"):
        await show_video_in_add(update, context)
    elif data == "owner_panel":
        await owner_panel(update, context)
    elif data == "maintenance_toggle":
        await toggle_maintenance(update, context)
    elif data == "support_link_settings":
        await support_link_settings(update, context)
    elif data == "support_link_cancel":
        await cancel_support_link(update, context)
    elif data == "support_link_unavailable":
        await query.edit_message_text(
            "ℹ️ لم يضبط المالك رابط الدعم بعد.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
    elif data == "withdraw_settings":
        await withdraw_settings(update, context)
    elif data == "withdraw_set_min":
        await withdraw_set_min(update, context)
    elif data == "withdraw_set_max":
        await withdraw_set_max(update, context)
    elif data == "withdraw_clear_limits":
        await withdraw_clear_limits(update, context)
    elif data == "daily_gift_settings":
        await daily_gift_settings(update, context)
    elif data == "daily_gift_toggle":
        await daily_gift_toggle(update, context)
    elif data == "daily_gift_set_emails":
        await daily_gift_set_emails(update, context)
    elif data == "daily_gift_set_reward":
        await daily_gift_set_reward(update, context)
    elif data == "contest_menu":
        await contest_menu(update, context)
    elif data == "contest_create_start":
        await contest_create_start(update, context)
    elif data == "contest_add_tier":
        await contest_add_tier(update, context)
    elif data == "contest_finalize":
        await contest_finalize(update, context)
    elif data == "contest_stop":
        await contest_stop(update, context)
    elif data == "contest_stats":
        await contest_stats(update, context)
    elif data == "sales_total":
        await sales_total_view(update, context)
    elif data == "sales_during_contest":
        await sales_during_contest_view(update, context)
    elif data == "admin_management":
        await admin_management(update, context)
    elif data == "add_admin":
        await add_admin_start(update, context)
    elif data == "remove_admin_menu":
        await remove_admin_menu(update, context)
    elif data.startswith("remove_admin_execute:"):
        await remove_admin_execute(update, context)
    elif data == "admin_panel":
        await admin_panel(update, context)
    elif data == "admin_settings":
        await admin_settings(update, context)
    elif data.startswith("admin_requests"):
        await admin_requests(update, context)
    elif data.startswith("admin_request_detail:"):
        await admin_request_detail(update, context)
    elif data.startswith("admin_complete_start:"):
        await admin_complete_start(update, context)
    elif data.startswith("admin_accept_auto:") or data.startswith("admin_accept_leave:"):
        await admin_accept_completed(update, context)
    elif data.startswith("admin_completion_cancel:"):
        await admin_completion_cancel(update, context)
    elif data.startswith("admin_show_code:"):
        await admin_show_code(update, context)
    elif data == "check_member":
        await check_member(update, context)
    elif data == "check_email_by_address":
        await check_email_by_address(update, context)
    elif data == "set_tier_prices":
        await set_tier_prices(update, context)
    elif data.startswith("set_tier:"):
        await set_tier(update, context)
    elif data == "approval_requests":
        await approval_requests(update, context)
    elif data.startswith("view_pending"):
        await view_pending_requests(update, context)
    elif data.startswith("view_approved"):
        await view_approved_requests(update, context)
    elif data.startswith("view_rejected"):
        await view_rejected_requests(update, context)
    elif data.startswith("pending_detail:"):
        await pending_detail(update, context)
    elif data.startswith("complete_request_owner:"):
        await complete_request_owner(update, context)
    elif data.startswith("auto_verify:"):
        await auto_verify_account(update, context)
    elif data.startswith("approved_detail:"):
        await approved_detail(update, context)
    elif data.startswith("reject_approved:"):
        await reject_approved_start(update, context)
    elif data.startswith("reject_approved_reason:"):
        await execute_reject_approved_reason(update, context)
    elif data.startswith("new_totp_code:"):
        await new_totp_code(update, context)
    elif data.startswith("rejected_detail:"):
        await rejected_detail(update, context)
    elif data.startswith("deduct_points:"):
        await deduct_points(update, context)
    elif data.startswith("give_points:"):
        await give_points(update, context)
    elif data == "points_management":
        await points_management(update, context)
    elif data == "give_points_by_id":
        await give_points_by_id(update, context)
    elif data == "deduct_points_by_id":
        await deduct_points_by_id(update, context)
    elif data.startswith("approve_request:"):
        await approve_request_owner(update, context)
    elif data.startswith("reject_request:"):
        await reject_request_reason(update, context)
    elif data.startswith("reject_reason:"):
        await execute_reject_reason(update, context)
    elif data == "videos_section":
        await videos_section(update, context)
    elif data.startswith("video_action:"):
        await video_action(update, context)
    elif data.startswith("view_video:"):
        await view_video(update, context)
    elif data.startswith("delete_video:"):
        await delete_video(update, context)
    elif data.startswith("set_video:"):
        await set_video_callback(update, context)
    elif data == "store_section":
        await owner_store_section(update, context)
    elif data == "store_add_category":
        await store_add_category(update, context)
    elif data == "store_delete_category_menu":
        await store_delete_category_menu(update, context)
    elif data.startswith("store_delete_category_confirm:"):
        await store_delete_category_confirm(update, context)
    elif data.startswith("store_delete_category_execute:"):
        await store_delete_category_execute(update, context)
    elif data.startswith("store_category:"):
        await store_category_menu(update, context)
    elif data.startswith("store_add_service:"):
        await store_add_service(update, context)
    elif data == "service_types_done":
        await service_types_done(update, context)
    elif data.startswith("service_toggle_type:"):
        await service_toggle_type(update, context)
    elif data.startswith("service_qty_skip_min:"):
        await service_qty_skip_min(update, context)
    elif data.startswith("service_qty_skip_max:"):
        await service_qty_skip_max(update, context)
    elif data.startswith("service_detail:"):
        await service_detail(update, context)
    elif data.startswith("service_edit_types:"):
        await service_edit_types(update, context)
    elif data.startswith("service_edit_toggle:"):
        await service_edit_toggle(update, context)
    elif data == "service_edit_types_save":
        await service_edit_types_save(update, context)
    elif data.startswith("store_delete_service:"):
        await store_delete_service(update, context)
    elif data.startswith("store_delete_service_confirm:"):
        await store_delete_service_confirm(update, context)
    elif data.startswith("delete_service:"):
        await delete_service_execute(update, context)
    elif data.startswith("store_edit_service:"):
        await store_edit_service(update, context)
    elif data.startswith("store_edit_field:"):
        await store_edit_field(update, context)
    elif data == "forced_channel":
        await forced_channel(update, context)
    elif data == "check_forced_channel":
        await check_forced_channel_callback(update, context)
    elif data == "remove_channel":
        await remove_channel(update, context)
    elif data == "purchase_channels":
        await purchase_channels(update, context)
    elif data == "set_purchase_channel_1":
        await set_purchase_channel(update, context, 1)
    elif data == "set_purchase_channel_2":
        await set_purchase_channel(update, context, 2)
    elif data == "withdraw_store":
        await withdraw_store(update, context)
    elif data == "tg_lion_catalog":
        await tg_lion_catalog(update, context)
    elif data.startswith("tg_lion_page:"):
        try:
            page = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            page = 0
        await tg_lion_catalog(update, context, page)
    elif data.startswith("tg_lion_search_start:"):
        try:
            page = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            page = 0
        await tg_lion_search_start(update, context, page)
    elif data.startswith("tg_lion_search_cancel:"):
        try:
            page = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            page = 0
        await tg_lion_search_cancel(update, context, page)
    elif data.startswith("tg_lion_search_result:"):
        parts = data.split(":")
        if len(parts) == 3:
            try:
                await tg_lion_country_menu(update, context, parts[1], int(parts[2]))
            except ValueError:
                await update.callback_query.answer("تعذر فتح الدولة.", show_alert=True)
    elif data.startswith("tg_lion_country:"):
        parts = data.split(":")
        if len(parts) == 3:
            try:
                await tg_lion_country_menu(update, context, parts[1], int(parts[2]))
            except ValueError:
                await update.callback_query.answer("تعذر فتح الدولة.", show_alert=True)
    elif data.startswith("tg_lion_confirm:"):
        parts = data.split(":")
        if len(parts) == 3:
            try:
                await tg_lion_confirm_order(update, context, parts[1], int(parts[2]))
            except ValueError:
                await update.callback_query.answer("تعذر تأكيد الطلب.", show_alert=True)
    elif data.startswith("tg_lion_cancel:"):
        try:
            page = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            page = 0
        await tg_lion_cancel_order(update, context, page)
    elif data == "toggle_tg_lion_store":
        await toggle_tg_lion_store(update, context)
    elif data.startswith("user_category:"):
        await user_category_menu(update, context)
    elif data.startswith("user_buy:"):
        await user_buy_service(update, context)
    elif data.startswith("deliver_order:"):
        await deliver_order(update, context)
    elif data == "all_accounts_section":
        await all_accounts_section(update, context)
    elif data == "owner_stats":
        await owner_stats(update, context)
    elif data == "all_accounts":
        await all_accounts(update, context, "all")
    elif data == "all_old":
        await all_accounts(update, context, "old")
    elif data == "all_new":
        await all_accounts(update, context, "new")
    elif data == "all_general":
        await all_accounts(update, context, "all")
    elif data == "unextracted_old":
        await unextracted_accounts(update, context, "old")
    elif data == "unextracted_new":
        await unextracted_accounts(update, context, "new")
    elif data.startswith("export_all_"):
        acc_type = data.replace("export_all_", "")
        await export_all_accounts(update, context, acc_type)
    elif data.startswith("export_unextracted_"):
        acc_type = data.replace("export_unextracted_", "")
        await export_unextracted(update, context, acc_type)
    elif data == "hold_accounts":
        await hold_accounts(update, context)
    elif data == "mark_extracted_menu":
        await mark_extracted_menu(update, context)
    elif data.startswith("mark_extracted:"):
        await mark_extracted(update, context)
    elif data == "referral_menu":
        await referral_menu(update, context)
    elif data.startswith("copy_referral:"):
        await copy_referral(update, context)
    elif data == "referral_settings":
        await referral_settings(update, context)
    elif data == "set_referral_bonus":
        await set_referral_bonus(update, context)
    elif data == "referral_stats":
        await referral_stats(update, context)
    elif data == "edit_my_accounts":
        await edit_my_accounts(update, context)
    elif data.startswith("edit_pending:"):
        await edit_pending_account(update, context)
    elif data.startswith("edit_field:"):
        await edit_field(update, context)
    elif data.startswith("delete_pending:"):
        await delete_pending_account(update, context)
    elif data.startswith("owner_show_code:"):
        await owner_show_code(update, context)
    elif data == "apis_menu":
        await apis_menu(update, context)
    elif data == "apis_list_details":
        context.user_data["apis_page"] = 0
        await show_api_details(update, context)
    elif data.startswith("apis_details_page:"):
        try:
            p = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            p = 0
        context.user_data["apis_page"] = p
        await show_api_details(update, context)
    elif data.startswith("api_view:"):
        api_id = data.split(":", 1)[1]
        await show_api_details(update, context, api_id=api_id)
    elif data == "api_add_start":
        await api_add_start(update, context)
    elif data.startswith("api_delete:"):
        await api_delete_execute(update, context)
    elif data == "bans_menu":
        await bans_menu(update, context)
    elif data.startswith("unban_member:"):
        await unban_member(update, context)
    else:
        await placeholder(update, context)


async def placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.callback_query.edit_message_text(
            "⚠️ خيار غير معروف.",
            reply_markup=kb_single("🔙 القائمة الرئيسية", "main_menu"))
    except Exception:
        pass


# ==================== COMMANDS ====================
async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_access_allowed(update):
        return
    if not await check_forced_channel(update, context):
        return
    user_id = update.effective_user.id
    admins = load_admins()
    apis = get_api_list()
    active_apis = [a for a in apis if a.get("status") == "active"]
    gift = get_daily_gift_config()
    limits = get_withdraw_limits()
    await update.message.reply_text(
        f"🔎 *تشخيص*\n\n🆔 رقمك: `{user_id}`\n👑 المالك: `{OWNER_ID}`\n"
        f"✅ أنت المالك: {'نعم' if user_id == OWNER_ID else 'لا'}\n"
        f"🛠 أدمن: {'نعم' if user_id in admins else 'لا'}\n"
        f"👥 عدد الأدمن: `{len(admins)}`\n"
        f"🔐 التشفير: {'مفعّل ✅' if CRYPTO_AVAILABLE else 'معطّل ❌'}\n"
        f"🔑 APIs: `{len(apis)}` (نشطة: `{len(active_apis)}`)\n"
        f"🛒 حدود السحب العامة: min={limits['min']}, max={limits['max']}\n"
        f"🎁 الهدية: {'مفعلة' if gift['enabled'] else 'معطلة'} ({gift['emails_required']} إيميل → ${gift['reward']:.2f})\n"
        f"💾 عدد المستخدمين: `{len(load_json(USERS_DB))}`",
        parse_mode=ParseMode.MARKDOWN)


async def owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("🚫 هذا الأمر للمالك فقط.")
        return
    buttons = [maintenance_toggle_button(),
               ("🔗 رابط الدعم", "support_link_settings"),
               ("👥 الإدارية", "admin_management"),
               ("🔑 إدارة APIs", "apis_menu"),
               ("🚫 إدارة الحظر", "bans_menu"),
               ("💰 أسعار المستويات", "set_tier_prices"),
               ("🛒 حدود السحب", "withdraw_settings"),
               ("🎁 إعدادات الهدية اليومية", "daily_gift_settings"),
               ("📋 الطلبات", "approval_requests"),
               ("🎯 إدارة المسابقة", "contest_menu"),
               ("🏆 الأكثر بيعاً", "top_sellers_owner"),
               ("📹 قسم الفيديوهات", "videos_section"),
               ("🛒 المبيعات", "store_section"),
               ("➕ إضافة قناة إجبارية", "forced_channel"),
               ("📊 جميع الحسابات المقبولة", "all_accounts_section"),
               ("📈 إحصائيات المستخدمين", "owner_stats"),
               ("🔎 فحص عضو", "check_member"),
               ("🔍 فحص إيميل", "check_email_by_address"),
               ("🔗 نظام الإحالة", "referral_settings"),
               ("💰 خصم/منح نقاط", "points_management"),
               ("🔙 القائمة الرئيسية", "main_menu")]
    await update.message.reply_text("⚙️ *لوحة تحكم المالك*", parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=kb_vertical(buttons))


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await maintenance_access_allowed(update):
        return
    if not await check_forced_channel(update, context):
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 هذا الأمر للأدمن فقط.")
        return
    buttons = [("⚙️ إعدادات الأدمن", "admin_settings"),
               ("🔑 إدارة APIs", "apis_menu"),
               ("🔙 القائمة الرئيسية", "main_menu")]
    await update.message.reply_text("🛠 *الإدارية*", parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=kb_vertical(buttons))


async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    await daily_backup_job(context)
    await update.message.reply_text("✅ تم إنشاء نسخة احتياطية.")


# ==================== MAIN ====================
async def initialize_application(application: Application):
    await restore_leave_checks(application)
    await application.bot.set_my_commands(
        [
            BotCommand("start", "check your account status"),
            BotCommand("buy", "open the purchase section"),
        ]
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN غير مضبوط.")
    if not OWNER_ID:
        logger.warning("⚠️ OWNER_TELEGRAM_ID = 0")
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(initialize_application)
           .build())
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CommandHandler("debug", debug_command))
    app.add_handler(CommandHandler("owner", owner_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CallbackQueryHandler(router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_upload))
    logger.info("🤖 Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()

__all__ = [name for name in globals() if not name.startswith("__")]
