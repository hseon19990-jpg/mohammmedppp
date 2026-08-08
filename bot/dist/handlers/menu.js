"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.mainMenuKeyboard = mainMenuKeyboard;
exports.sendMainMenu = sendMainMenu;
const grammy_1 = require("grammy");
const owner_js_1 = require("../middleware/owner.js");
function mainMenuKeyboard(ctx) {
    const keyboard = new grammy_1.InlineKeyboard().text('➕ إضافة حساب', 'add_account').row();
    if ((0, owner_js_1.isOwner)(ctx)) {
        keyboard
            .text('📋 عرض جميع الحسابات', 'view_all').row()
            .text('✏️ تعديل حساب', 'edit_list').row()
            .text('✅ التحقق من حساب', 'verify_list');
    }
    else {
        keyboard.text('ℹ️ الخدمات العامة متاحة للجميع');
    }
    return keyboard;
}
async function sendMainMenu(ctx, text = '🔐 القائمة الرئيسية') {
    ctx.session.step = undefined;
    ctx.session.pendingAccount = undefined;
    ctx.session.editingId = undefined;
    ctx.session.editField = undefined;
    await ctx.reply(text, { reply_markup: mainMenuKeyboard(ctx) });
}
