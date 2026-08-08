"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.startAddFlow = startAddFlow;
exports.handleAddStep = handleAddStep;
exports.handleSkipAppPassword = handleSkipAppPassword;
const store_js_1 = require("../storage/store.js");
const totp_js_1 = require("../utils/totp.js");
const menu_js_1 = require("./menu.js");
const grammy_1 = require("grammy");
async function startAddFlow(ctx) {
    ctx.session.step = 'add_email';
    ctx.session.pendingAccount = {};
    await ctx.reply('📝 *إضافة حساب جديد*\n\nالخطوة 1/4 — أرسل الإيميل:', {
        parse_mode: 'Markdown',
        reply_markup: new grammy_1.InlineKeyboard().text('❌ إلغاء', 'cancel'),
    });
}
async function handleAddStep(ctx) {
    const text = ctx.message?.text?.trim();
    if (!text)
        return;
    const step = ctx.session.step;
    const pending = ctx.session.pendingAccount ?? {};
    if (step === 'add_email') {
        pending.email = text;
        ctx.session.pendingAccount = pending;
        ctx.session.step = 'add_password';
        await ctx.reply('🔑 الخطوة 2/4 — أرسل كلمة المرور:', { reply_markup: new grammy_1.InlineKeyboard().text('❌ إلغاء', 'cancel') });
    }
    else if (step === 'add_password') {
        pending.password = text;
        ctx.session.pendingAccount = pending;
        ctx.session.step = 'add_totp';
        await ctx.reply('🔐 الخطوة 3/4 — أرسل مفتاح المصادقة الثنائية *(Secret Key)*\n\n' +
            '_هو المفتاح الذي تحصل عليه عند إعداد التحقق بخطوتين، وليس الكود المؤقت_', {
            parse_mode: 'Markdown',
            reply_markup: new grammy_1.InlineKeyboard().text('❌ إلغاء', 'cancel'),
        });
    }
    else if (step === 'add_totp') {
        const secret = text.replace(/\s+/g, '').toUpperCase();
        if (!(0, totp_js_1.validateTOTPSecret)(secret)) {
            await ctx.reply('⚠️ المفتاح غير صالح، حاول مرة أخرى أو تأكد من نسخه بشكل صحيح.');
            return;
        }
        pending.totpSecret = secret;
        ctx.session.pendingAccount = pending;
        ctx.session.step = 'add_appPassword';
        const code = (0, totp_js_1.generateTOTP)(secret);
        const remaining = (0, totp_js_1.getRemainingSeconds)();
        await ctx.reply(`✅ مفتاح المصادقة صالح!\n\n` +
            `🔢 *الكود الحالي:* \`${code}\`\n` +
            `⏱ ينتهي خلال ${remaining} ثانية\n\n` +
            '🗝 الخطوة 4/4 — أرسل كلمة مرور التطبيق *(App Password)*:', {
            parse_mode: 'Markdown',
            reply_markup: new grammy_1.InlineKeyboard().text('⏭ تخطي', 'skip_app_password').text('❌ إلغاء', 'cancel'),
        });
    }
    else if (step === 'add_appPassword') {
        pending.appPassword = text;
        ctx.session.pendingAccount = pending;
        await finishAdd(ctx);
    }
}
async function handleSkipAppPassword(ctx) {
    const pending = ctx.session.pendingAccount ?? {};
    pending.appPassword = '';
    ctx.session.pendingAccount = pending;
    await ctx.answerCallbackQuery();
    await finishAdd(ctx);
}
async function finishAdd(ctx) {
    const p = ctx.session.pendingAccount;
    const acc = (0, store_js_1.addAccount)({
        email: p.email,
        password: p.password,
        totpSecret: p.totpSecret,
        recoveryCodes: p.recoveryCodes ?? [],
        appPassword: p.appPassword ?? '',
    });
    await ctx.reply(`✅ *تم حفظ الحساب بنجاح!*\n\n📧 الإيميل: \`${acc.email}\``, { parse_mode: 'Markdown' });
    await (0, menu_js_1.sendMainMenu)(ctx);
}