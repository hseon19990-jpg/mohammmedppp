import { InlineKeyboard } from 'grammy';
import type { MyContext } from '../bot.js';
import { loadAccounts } from '../storage/store.js';
import { verifyEmailCredentials } from '../utils/email-verify.js';
import { sendMainMenu } from './menu.js';

export async function handleVerifyList(ctx: MyContext): Promise<void> {
  const accounts = loadAccounts();
  if (accounts.length === 0) {
    await ctx.reply('📭 لا توجد حسابات.', {
      reply_markup: new InlineKeyboard().text('◀️ رجوع', 'main_menu'),
    });
    return;
  }

  const kb = new InlineKeyboard();
  for (const acc of accounts) {
    kb.text(`📧 ${acc.email}`, `verify:${acc.id}`).row();
  }
  kb.text('◀️ رجوع', 'main_menu');

  await ctx.reply('✅ اختر الحساب للتحقق منه:', { reply_markup: kb });
}

export async function handleVerifyAccount(ctx: MyContext, accountId: string): Promise<void> {
  const accounts = loadAccounts();
  const acc = accounts.find((a) => a.id === accountId);
  if (!acc) {
    await ctx.answerCallbackQuery('⚠️ الحساب غير موجود');
    return;
  }
  await ctx.answerCallbackQuery('⏳ جاري التحقق...');

  const loadingMsg = await ctx.reply(`🔄 جاري التحقق من \`${acc.email}\`...`, {
    parse_mode: 'Markdown',
  });

  const result = await verifyEmailCredentials(acc.email, acc.appPassword);

  await ctx.api.editMessageText(
    ctx.chat!.id,
    loadingMsg.message_id,
    `📧 *${acc.email}*\n\n${result.message}`,
    {
      parse_mode: 'Markdown',
      reply_markup: new InlineKeyboard().text('◀️ رجوع', 'main_menu'),
    },
  );
}
