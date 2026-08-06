"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.handleEditList = handleEditList;
exports.handleEditAccount = handleEditAccount;
exports.handleEditField = handleEditField;
exports.handleEditValueInput = handleEditValueInput;
const grammy_1 = require("grammy");
const store_js_1 = require("../storage/store.js");
const totp_js_1 = require("../utils/totp.js");
const menu_js_1 = require("./menu.js");
const FIELD_LABELS = {
    email: '📧 الإيميل',
    password: '🔑 كلمة المرور',
    totpSecret: '🔐 مفتاح المصادقة الثنائية',
    recoveryCodes: '📋 أكواد الاسترداد',
    appPassword: '🗝 كلمة مرور التطبيق',
};
async function handleEditList(ctx) {
    const accounts = (0, store_js_1.loadAccounts)();
    if (accounts.length === 0) {
        await ctx.reply('📭 لا توجد حسابات.', {
            reply_markup: new grammy_1.InlineKeyboard().text('◀️ رجوع', 'main_menu'),
        });
        return;
    }
    const kb = new grammy_1.InlineKeyboard();
    for (const acc of accounts) {
        kb.text(`📧 ${acc.email}`, `edit:${acc.id}`).row();
    }
    kb.text('◀️ رجوع', 'main_menu');
    await ctx.reply('✏️ اختر الحساب الذي تريد تعديله:', { reply_markup: kb });
}
async function handleEditAccount(ctx, accountId) {
    const accounts = (0, store_js_1.loadAccounts)();
    const acc = accounts.find((a) => a.id === accountId);
    if (!acc) {
        await ctx.answerCallbackQuery('⚠️ الحساب غير موجود');
        return;
    }
    await ctx.answerCallbackQuery();
    const kb = new grammy_1.InlineKeyboard();
    for (const [field, label] of Object.entries(FIELD_LABELS)) {
        kb.text(label, `edit_field:${accountId}:${field}`).row();
    }
    kb.text('🗑 حذف الحساب', `delete:${accountId}`).row();
    kb.text('◀️ رجوع', 'edit_list');
    await ctx.reply(`✏️ تعديل الحساب: \`${acc.email}\`\n\nاختر الحقل المراد تعديله:`, { parse_mode: 'Markdown', reply_markup: kb });
}
async function handleEditField(ctx, accountId, field) {
    await ctx.answerCallbackQuery();
    ctx.session.step = 'edit_entering_value';
    ctx.session.editingId = accountId;
    ctx.session.editField = field;
    const label = FIELD_LABELS[field] ?? field;
    let hint = '';
    if (field === 'recoveryCodes')
        hint = '\n_أرسلها مفصولة بفاصلة أو سطر جديد_';
    if (field === 'totpSecret')
        hint = '\n_أرسل المفتاح وليس الكود المؤقت_';
    await ctx.reply(`✏️ أرسل القيمة الجديدة لـ *${label}*:${hint}`, {
        parse_mode: 'Markdown',
        reply_markup: new grammy_1.InlineKeyboard().text('❌ إلغاء', 'main_menu'),
    });
}
async function handleEditValueInput(ctx) {
    const text = ctx.message?.text?.trim();
    if (!text)
        return;
    const { editingId, editField } = ctx.session;
    if (!editingId || !editField)
        return;
    let value = text;
    if (editField === 'totpSecret') {
        const clean = text.replace(/\s+/g, '').toUpperCase();
        if (!(0, totp_js_1.validateTOTPSecret)(clean)) {
            await ctx.reply('⚠️ المفتاح غير صالح، حاول مرة أخرى.');
            return;
        }
        value = clean;
    }
    else if (editField === 'recoveryCodes') {
        value = text.split(/[\n,،]+/).map((c) => c.trim()).filter(Boolean);
    }
    const updated = (0, store_js_1.updateAccount)(editingId, { [editField]: value });
    if (!updated) {
        await ctx.reply('⚠️ لم يتم العثور على الحساب.');
    }
    else {
        await ctx.reply(`✅ تم تحديث *${FIELD_LABELS[editField] ?? editField}* بنجاح.`, {
            parse_mode: 'Markdown',
        });
    }
    await (0, menu_js_1.sendMainMenu)(ctx);
}
