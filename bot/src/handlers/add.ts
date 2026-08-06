import type { MyContext } from '../bot.js';
import { addAccount } from '../storage/store.js';
import { generateTOTP, getRemainingSeconds, validateTOTPSecret } from '../utils/totp.js';
import { sendMainMenu } from './menu.js';
import { InlineKeyboard } from 'grammy';

export async function startAddFlow(ctx: MyContext): Promise<void> {
  ctx.session.step = 'add_email';
  ctx.session.pendingAccount = {};
  await ctx.reply(
    '📝 *إضافة حساب جديد*\n\nالخطوة 1/5 — أرسل الإيميل:',
    {
      parse_mode: 'Markdown',
      reply_markup: new InlineKeyboard().text('❌ إلغاء', 'cancel'),
    },
  );
}

export async function handleAddStep(ctx: MyContext): Promise<void> {
  const text = ctx.message?.text?.trim();
  if (!text) return;

  const step = ctx.session.step;
  const pending = ctx.session.pendingAccount ?? {};

  if (step === 'add_email') {
    pending.email = text;
    ctx.session.pendingAccount = pending;
    ctx.session.step = 'add_password';
    await ctx.reply(
      '🔑 الخطوة 2/5 — أرسل كلمة المرور:',
      { reply_markup: new InlineKeyboard().text('❌ إلغاء', 'cancel') },
    );

  } else if (step === 'add_password') {
    pending.password = text;
    ctx.session.pendingAccount = pending;
    ctx.session.step = 'add_totp';
    await ctx.reply(
      '🔐 الخطوة 3/5 — أرسل مفتاح المصادقة الثنائية *(Secret Key)*\n\n' +
      '_هو المفتاح الذي تحصل عليه عند إعداد التحقق بخطوتين، وليس الكود المؤقت_',
      {
        parse_mode: 'Markdown',
        reply_markup: new InlineKeyboard().text('❌ إلغاء', 'cancel'),
      },
    );

  } else if (step === 'add_totp') {
    const secret = text.replace(/\s+/g, '').toUpperCase();
    if (!validateTOTPSecret(secret)) {
      await ctx.reply('⚠️ المفتاح غير صالح، حاول مرة أخرى أو تأكد من نسخه بشكل صحيح.');
      return;
    }
    pending.totpSecret = secret;
    ctx.session.pendingAccount = pending;
    ctx.session.step = 'add_recovery';

    const code = generateTOTP(secret);
    const remaining = getRemainingSeconds();
    await ctx.reply(
      `✅ مفتاح المصادقة صالح!\n\n` +
      `🔢 *الكود الحالي:* \`${code}\`\n` +
      `⏱ ينتهي خلال ${remaining} ثانية\n\n` +
      '📋 الخطوة 4/5 — أرسل أكواد الاسترداد\n' +
      '_يمكنك إرسالها مفصولة بفاصلة أو سطر جديد_',
      {
        parse_mode: 'Markdown',
        reply_markup: new InlineKeyboard().text('⏭ تخطي', 'skip_recovery').text('❌ إلغاء', 'cancel'),
      },
    );

  } else if (step === 'add_recovery') {
    const codes = text
      .split(/[\n,،]+/)
      .map((c) => c.trim())
      .filter(Boolean);
    pending.recoveryCodes = codes;
    ctx.session.pendingAccount = pending;
    ctx.session.step = 'add_appPassword';
    await ctx.reply(
      '🗝 الخطوة 5/5 — أرسل كلمة مرور التطبيق *(App Password)*:',
      {
        parse_mode: 'Markdown',
        reply_markup: new InlineKeyboard().text('⏭ تخطي', 'skip_app_password').text('❌ إلغاء', 'cancel'),
      },
    );

  } else if (step === 'add_appPassword') {
    pending.appPassword = text;
    ctx.session.pendingAccount = pending;
    await finishAdd(ctx);
  }
}

export async function handleSkipRecovery(ctx: MyContext): Promise<void> {
  const pending = ctx.session.pendingAccount ?? {};
  pending.recoveryCodes = [];
  ctx.session.pendingAccount = pending;
  ctx.session.step = 'add_appPassword';
  await ctx.answerCallbackQuery();
  await ctx.reply(
    '🗝 الخطوة 5/5 — أرسل كلمة مرور التطبيق *(App Password)*:',
    {
      parse_mode: 'Markdown',
      reply_markup: new InlineKeyboard().text('⏭ تخطي', 'skip_app_password').text('❌ إلغاء', 'cancel'),
    },
  );
}

export async function handleSkipAppPassword(ctx: MyContext): Promise<void> {
  const pending = ctx.session.pendingAccount ?? {};
  pending.appPassword = '';
  ctx.session.pendingAccount = pending;
  await ctx.answerCallbackQuery();
  await finishAdd(ctx);
}

async function finishAdd(ctx: MyContext): Promise<void> {
  const p = ctx.session.pendingAccount!;
  const acc = addAccount({
    email: p.email!,
    password: p.password!,
    totpSecret: p.totpSecret!,
    recoveryCodes: p.recoveryCodes ?? [],
    appPassword: p.appPassword ?? '',
  });
  await ctx.reply(
    `✅ *تم حفظ الحساب بنجاح!*\n\n📧 الإيميل: \`${acc.email}\``,
    { parse_mode: 'Markdown' },
  );
  await sendMainMenu(ctx);
}
