import { InlineKeyboard } from 'grammy';
import type { MyContext } from '../bot.js';
import { isOwner } from '../middleware/owner.js';

export function mainMenuKeyboard(ctx: MyContext): InlineKeyboard {
  const keyboard = new InlineKeyboard().text('➕ إضافة حساب', 'add_account').row();

  if (isOwner(ctx)) {
    keyboard
      .text('📋 عرض جميع الحسابات', 'view_all').row()
      .text('✏️ تعديل حساب', 'edit_list').row()
      .text('✅ التحقق من حساب', 'verify_list');
  } else {
    keyboard.text('ℹ️ الخدمات العامة متاحة للجميع');
  }

  return keyboard;
}

export async function sendMainMenu(ctx: MyContext, text = '🔐 القائمة الرئيسية'): Promise<void> {
  ctx.session.step = undefined;
  ctx.session.pendingAccount = undefined;
  ctx.session.editingId = undefined;
  ctx.session.editField = undefined;
  await ctx.reply(text, { reply_markup: mainMenuKeyboard(ctx) });
}
