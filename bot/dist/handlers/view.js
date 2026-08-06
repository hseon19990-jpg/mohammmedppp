"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.handleViewAll = handleViewAll;
exports.handleGetCode = handleGetCode;
exports.handleDeleteConfirm = handleDeleteConfirm;
exports.handleDeleteYes = handleDeleteYes;
const grammy_1 = require("grammy");
const store_js_1 = require("../storage/store.js");
const totp_js_1 = require("../utils/totp.js");
const menu_js_1 = require("./menu.js");
async function handleViewAll(ctx) {
    const accounts = (0, store_js_1.loadAccounts)();
    if (accounts.length === 0) {
        await ctx.reply('📭 لا توجد حسابات محفوظة.', {
            reply_markup: new grammy_1.InlineKeyboard().text('◀️ رجوع', 'main_menu'),
        });
        return;
    }
    // Send each account as a formatted block
    for (const acc of accounts) {
        const recovery = acc.recoveryCodes.length > 0 ? acc.recoveryCodes.join(', ') : 'لا يوجد';
        const appPass = acc.appPassword || 'لا يوجد';
        const msg = `📧 \`${acc.email}\`\n` +
            `🔑 \`${acc.password}\`\n` +
            `🔐 \`${acc.totpSecret}\`\n` +
            `📋 \`${recovery}\`\n` +
            `🗝 \`${appPass}\``;
        await ctx.reply(msg, {
            parse_mode: 'Markdown',
            reply_markup: new grammy_1.InlineKeyboard()
                .text('🔢 كود المصادقة', `get_code:${acc.id}`)
                .text('✏️ تعديل', `edit:${acc.id}`)
                .text('🗑 حذف', `delete:${acc.id}`),
        });
    }
    // Summary line
    await ctx.reply(`📊 إجمالي الحسابات: *${accounts.length}*\n\n` +
        `الصيغة: إيميل | كلمة مرور | مفتاح 2FA | أكواد استرداد | كلمة مرور التطبيق`, {
        parse_mode: 'Markdown',
        reply_markup: new grammy_1.InlineKeyboard().text('◀️ رجوع', 'main_menu'),
    });
}
async function handleGetCode(ctx, accountId) {
    const accounts = (0, store_js_1.loadAccounts)();
    const acc = accounts.find((a) => a.id === accountId);
    if (!acc) {
        await ctx.answerCallbackQuery('⚠️ الحساب غير موجود');
        return;
    }
    try {
        const code = (0, totp_js_1.generateTOTP)(acc.totpSecret);
        const remaining = (0, totp_js_1.getRemainingSeconds)();
        await ctx.answerCallbackQuery();
        await ctx.reply(`🔢 *كود المصادقة لـ* \`${acc.email}\`\n\n` +
            `\`${code}\`\n\n` +
            `⏱ ينتهي خلال *${remaining}* ثانية`, { parse_mode: 'Markdown' });
    }
    catch {
        await ctx.answerCallbackQuery('❌ مفتاح المصادقة غير صالح');
    }
}
async function handleDeleteConfirm(ctx, accountId) {
    const accounts = (0, store_js_1.loadAccounts)();
    const acc = accounts.find((a) => a.id === accountId);
    if (!acc) {
        await ctx.answerCallbackQuery('⚠️ الحساب غير موجود');
        return;
    }
    await ctx.answerCallbackQuery();
    await ctx.reply(`⚠️ هل تريد حذف حساب \`${acc.email}\`؟`, {
        parse_mode: 'Markdown',
        reply_markup: new grammy_1.InlineKeyboard()
            .text('✅ نعم، احذف', `delete_yes:${accountId}`)
            .text('❌ إلغاء', 'main_menu'),
    });
}
async function handleDeleteYes(ctx, accountId) {
    const { deleteAccount } = await Promise.resolve().then(() => __importStar(require('../storage/store.js')));
    const done = deleteAccount(accountId);
    await ctx.answerCallbackQuery();
    if (done) {
        await ctx.reply('🗑 تم حذف الحساب بنجاح.');
    }
    else {
        await ctx.reply('⚠️ لم يتم العثور على الحساب.');
    }
    await (0, menu_js_1.sendMainMenu)(ctx);
}
