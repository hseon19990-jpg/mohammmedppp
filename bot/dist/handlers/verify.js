"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.handleVerifyList = handleVerifyList;
exports.handleVerifyAccount = handleVerifyAccount;
const grammy_1 = require("grammy");
const store_js_1 = require("../storage/store.js");
const email_verify_js_1 = require("../utils/email-verify.js");
async function handleVerifyList(ctx) {
    const accounts = (0, store_js_1.loadAccounts)();
    if (accounts.length === 0) {
        await ctx.reply('📭 لا توجد حسابات.', {
            reply_markup: new grammy_1.InlineKeyboard().text('◀️ رجوع', 'main_menu'),
        });
        return;
    }
    const kb = new grammy_1.InlineKeyboard();
    for (const acc of accounts) {
        kb.text(`📧 ${acc.email}`, `verify:${acc.id}`).row();
    }
    kb.text('◀️ رجوع', 'main_menu');
    await ctx.reply('✅ اختر الحساب للتحقق منه:', { reply_markup: kb });
}
async function handleVerifyAccount(ctx, accountId) {
    const accounts = (0, store_js_1.loadAccounts)();
    const acc = accounts.find((a) => a.id === accountId);
    if (!acc) {
        await ctx.answerCallbackQuery('⚠️ الحساب غير موجود');
        return;
    }
    await ctx.answerCallbackQuery('⏳ جاري التحقق...');
    const loadingMsg = await ctx.reply(`🔄 جاري التحقق من \`${acc.email}\`...`, {
        parse_mode: 'Markdown',
    });
    const result = await (0, email_verify_js_1.verifyEmailCredentials)(acc.email, acc.appPassword);
    await ctx.api.editMessageText(ctx.chat.id, loadingMsg.message_id, `📧 *${acc.email}*\n\n${result.message}`, {
        parse_mode: 'Markdown',
        reply_markup: new grammy_1.InlineKeyboard().text('◀️ رجوع', 'main_menu'),
    });
}
