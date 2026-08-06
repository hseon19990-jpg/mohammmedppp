import { InlineKeyboard } from 'grammy';
import type { MyContext } from '../bot.js';

export function mainMenuKeyboard(): InlineKeyboard {
  return new InlineKeyboard()
    .text('➕ إضافة حساب', 'add_account').row()
    .text('📋 عرض جميع الحسابات', 'view_all').row()
    .text('✏️ تعديل حساب', 'edit_list').row()
    .text('✅ التحقق من حساب', 'verify_list');
}

export async function sendMainMenu(ctx: MyContext, text = '🔐 القائمة الرئيسية'): Promise<void> {
  ctx.session.step = undefined;
  ctx.session.pendingAccount = undefined;
  ctx.session.editingId = undefined;
  ctx.session.editField = undefined;
  await ctx.reply(text, { reply_markup: mainMenuKeyboard() });
}
