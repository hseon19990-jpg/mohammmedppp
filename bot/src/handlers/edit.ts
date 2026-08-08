import { InlineKeyboard } from 'grammy';
import type { MyContext } from '../bot.js';
import { loadAccounts, updateAccount } from '../storage/store.js';
import { validateTOTPSecret } from '../utils/totp.js';
import { sendMainMenu } from './menu.js';

const FIELD_LABELS: Record<string, string> = {
  email:         '📧 الإيميل',
  password:      '🔑 كلمة المرور',
  totpSecret:    '🔐 مفتاح المصادقة الثنائية',
  recoveryCodes: '📋 أكواد الاسترداد',
  appPassword:   '🗝 كلمة مرور التطبيق',
};

export async function handleEditList(ctx: MyContext): Promise<void> {
  const accounts = loadAccounts();
  if (accounts.length === 0) {
    await ctx.reply('📭 لا توجد حسابات.', {
      reply_markup: new InlineKeyboard().text('◀️ رجوع', 'main_menu'),
    });
    return;
  }

  const kb = new InlineKeyboard();
  for (const acc of accounts) {
    kb.text(`📧 ${acc.email}`, `edit:${acc.id}`).row();
  }
  kb.text('◀️ رجوع', 'main_menu');

  await ctx.reply('✏️ اختر الحساب الذي تريد تعديله:', { reply_markup: kb });
}

export async function handleEditAccount(ctx: MyContext, accountId: string): Promise<void> {
  const accounts = loadAccounts();
  const acc = accounts.find((a) => a.id === accountId);
  if (!acc) {
    await ctx.answerCallbackQuery('⚠️ الحساب غير موجود');
    return;
  }
  await ctx.answerCallbackQuery();

  const kb = new InlineKeyboard();
  for (const [field, label] of Object.entries(FIELD_LABELS)) {
    kb.text(label, `edit_field:${accountId}:${field}`).row();
  }
  kb.text('🗑 حذف الحساب', `delete:${accountId}`).row();
  kb.text('◀️ رجوع', 'edit_list');

  await ctx.reply(
    `✏️ تعديل الحساب: \`${acc.email}\`\n\nاختر الحقل المراد تعديله:`,
    { parse_mode: 'Markdown', reply_markup: kb },
  );
}

export async function handleEditField(
  ctx: MyContext,
  accountId: string,
  field: string,
): Promise<void> {
  await ctx.answerCallbackQuery();
  ctx.session.step = 'edit_entering_value';
  ctx.session.editingId = accountId;
  ctx.session.editField = field as any;

  const label = FIELD_LABELS[field] ?? field;
  let hint = '';
  if (field === 'recoveryCodes') hint = '\n_أرسلها مفصولة بفاصلة أو سطر جديد_';
  if (field === 'totpSecret') hint = '\n_أرسل المفتاح وليس الكود المؤقت_';

  await ctx.reply(
    `✏️ أرسل القيمة الجديدة لـ *${label}*:${hint}`,
    {
      parse_mode: 'Markdown',
      reply_markup: new InlineKeyboard().text('❌ إلغاء', 'main_menu'),
    },
  );
}

export async function handleEditValueInput(ctx: MyContext): Promise<void> {
  const text = ctx.message?.text?.trim();
  if (!text) return;

  const { editingId, editField } = ctx.session;
  if (!editingId || !editField) return;

  let value: any = text;

  if (editField === 'totpSecret') {
    const clean = text.replace(/\s+/g, '').toUpperCase();
    if (!validateTOTPSecret(clean)) {
      await ctx.reply('⚠️ المفتاح غير صالح، حاول مرة أخرى.');
      return;
    }
    value = clean;
  } else if (editField === 'recoveryCodes') {
    value = text.split(/[\n,،]+/).map((c) => c.trim()).filter(Boolean);
  }

  const updated = updateAccount(editingId, { [editField]: value });
  if (!updated) {
    await ctx.reply('⚠️ لم يتم العثور على الحساب.');
  } else {
    await ctx.reply(`✅ تم تحديث *${FIELD_LABELS[editField] ?? editField}* بنجاح.`, {
      parse_mode: 'Markdown',
    });
  }

  await sendMainMenu(ctx);
}
