import { InlineKeyboard } from 'grammy';
import type { MyContext } from '../bot.js';
import { loadAccounts } from '../storage/store.js';
import { generateTOTP, getRemainingSeconds } from '../utils/totp.js';
import { sendMainMenu } from './menu.js';

export async function handleViewAll(ctx: MyContext): Promise<void> {
  const accounts = loadAccounts();

  if (accounts.length === 0) {
    await ctx.reply('📭 لا توجد حسابات محفوظة.', {
      reply_markup: new InlineKeyboard().text('◀️ رجوع', 'main_menu'),
    });
    return;
  }

  // Send each account as a formatted block
  for (const acc of accounts) {
    const recovery = acc.recoveryCodes.length > 0 ? acc.recoveryCodes.join(', ') : 'لا يوجد';
    const appPass = acc.appPassword || 'لا يوجد';

    const msg =
      `📧 \`${acc.email}\`\n` +
      `🔑 \`${acc.password}\`\n` +
      `🔐 \`${acc.totpSecret}\`\n` +
      `📋 \`${recovery}\`\n` +
      `🗝 \`${appPass}\``;

    await ctx.reply(msg, {
      parse_mode: 'Markdown',
      reply_markup: new InlineKeyboard()
        .text('🔢 كود المصادقة', `get_code:${acc.id}`)
        .text('✏️ تعديل', `edit:${acc.id}`)
        .text('🗑 حذف', `delete:${acc.id}`),
    });
  }

  // Summary line
  await ctx.reply(
    `📊 إجمالي الحسابات: *${accounts.length}*\n\n` +
    `الصيغة: إيميل | كلمة مرور | مفتاح 2FA | أكواد استرداد | كلمة مرور التطبيق`,
    {
      parse_mode: 'Markdown',
      reply_markup: new InlineKeyboard().text('◀️ رجوع', 'main_menu'),
    },
  );
}

export async function handleGetCode(ctx: MyContext, accountId: string): Promise<void> {
  const accounts = loadAccounts();
  const acc = accounts.find((a) => a.id === accountId);
  if (!acc) {
    await ctx.answerCallbackQuery('⚠️ الحساب غير موجود');
    return;
  }
  try {
    const code = generateTOTP(acc.totpSecret);
    const remaining = getRemainingSeconds();
    await ctx.answerCallbackQuery();
    await ctx.reply(
      `🔢 *كود المصادقة لـ* \`${acc.email}\`\n\n` +
      `\`${code}\`\n\n` +
      `⏱ ينتهي خلال *${remaining}* ثانية`,
      { parse_mode: 'Markdown' },
    );
  } catch {
    await ctx.answerCallbackQuery('❌ مفتاح المصادقة غير صالح');
  }
}

export async function handleDeleteConfirm(ctx: MyContext, accountId: string): Promise<void> {
  const accounts = loadAccounts();
  const acc = accounts.find((a) => a.id === accountId);
  if (!acc) {
    await ctx.answerCallbackQuery('⚠️ الحساب غير موجود');
    return;
  }
  await ctx.answerCallbackQuery();
  await ctx.reply(
    `⚠️ هل تريد حذف حساب \`${acc.email}\`؟`,
    {
      parse_mode: 'Markdown',
      reply_markup: new InlineKeyboard()
        .text('✅ نعم، احذف', `delete_yes:${accountId}`)
        .text('❌ إلغاء', 'main_menu'),
    },
  );
}

export async function handleDeleteYes(ctx: MyContext, accountId: string): Promise<void> {
  const { deleteAccount } = await import('../storage/store.js');
  const done = deleteAccount(accountId);
  await ctx.answerCallbackQuery();
  if (done) {
    await ctx.reply('🗑 تم حذف الحساب بنجاح.');
  } else {
    await ctx.reply('⚠️ لم يتم العثور على الحساب.');
  }
  await sendMainMenu(ctx);
}
