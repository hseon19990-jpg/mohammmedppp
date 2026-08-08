import { Bot, session } from 'grammy';
import type { SessionData } from './types.js';
import { rejectNonOwnerCallback } from './middleware/owner.js';
import { handleStart } from './handlers/start.js';
import { sendMainMenu } from './handlers/menu.js';
import { startAddFlow, handleAddStep, handleSkipAppPassword } from './handlers/add.js';
import { handleViewAll, handleGetCode, handleDeleteConfirm, handleDeleteYes } from './handlers/view.js';
import { handleEditList, handleEditAccount, handleEditField, handleEditValueInput } from './handlers/edit.js';
import { handleVerifyList, handleVerifyAccount } from './handlers/verify.js';

export type MyContext = import('grammy').Context & {
  session: SessionData;
};

export function createBot(): Bot<MyContext> {
  const token = process.env.BOT_TOKEN;
  if (!token) throw new Error('BOT_TOKEN environment variable is not set');

  const bot = new Bot<MyContext>(token);

  // Session middleware
  bot.use(
    session({
      initial: (): SessionData => ({}),
    }),
  );

  // ── Commands ─────────────────────────────────────────────────────────────
  bot.command('start', handleStart);
  bot.command('menu',  (ctx) => sendMainMenu(ctx));

  // ── Callback queries ─────────────────────────────────────────────────────
  bot.callbackQuery('main_menu',       async (ctx) => { await ctx.answerCallbackQuery(); await sendMainMenu(ctx); });
  bot.callbackQuery('add_account',     async (ctx) => { await ctx.answerCallbackQuery(); await startAddFlow(ctx); });
  bot.callbackQuery('view_all',        async (ctx) => { if (await rejectNonOwnerCallback(ctx)) return; await ctx.answerCallbackQuery(); await handleViewAll(ctx); });
  bot.callbackQuery('edit_list',       async (ctx) => { if (await rejectNonOwnerCallback(ctx)) return; await ctx.answerCallbackQuery(); await handleEditList(ctx); });
  bot.callbackQuery('verify_list',     async (ctx) => { if (await rejectNonOwnerCallback(ctx)) return; await ctx.answerCallbackQuery(); await handleVerifyList(ctx); });
  bot.callbackQuery('skip_app_password', handleSkipAppPassword);
  bot.callbackQuery('cancel',          async (ctx) => { await ctx.answerCallbackQuery(); await sendMainMenu(ctx, '❌ تم الإلغاء'); });

  // Dynamic callbacks
  bot.callbackQuery(/^get_code:(.+)$/,    async (ctx) => { if (await rejectNonOwnerCallback(ctx)) return; await handleGetCode(ctx, ctx.match[1]); });
  bot.callbackQuery(/^edit:(.+)$/,        async (ctx) => { if (await rejectNonOwnerCallback(ctx)) return; await handleEditAccount(ctx, ctx.match[1]); });
  bot.callbackQuery(/^edit_field:(.+):(.+)$/, async (ctx) => { if (await rejectNonOwnerCallback(ctx)) return; await handleEditField(ctx, ctx.match[1], ctx.match[2]); });
  bot.callbackQuery(/^delete:(.+)$/,      async (ctx) => { if (await rejectNonOwnerCallback(ctx)) return; await handleDeleteConfirm(ctx, ctx.match[1]); });
  bot.callbackQuery(/^delete_yes:(.+)$/,  async (ctx) => { if (await rejectNonOwnerCallback(ctx)) return; await handleDeleteYes(ctx, ctx.match[1]); });
  bot.callbackQuery(/^verify:(.+)$/,      async (ctx) => { if (await rejectNonOwnerCallback(ctx)) return; await handleVerifyAccount(ctx, ctx.match[1]); });

  // ── Text messages (multi-step flows) ─────────────────────────────────────
  bot.on('message:text', async (ctx) => {
    const step = ctx.session.step;

    if (!step) {
      await sendMainMenu(ctx);
      return;
    }

    if (
      step === 'add_email' ||
      step === 'add_password' ||
      step === 'add_totp' ||
      step === 'add_appPassword'
    ) {
      await handleAddStep(ctx);
      return;
    }

    if (step === 'edit_entering_value') {
      await handleEditValueInput(ctx);
      return;
    }
  });

  // ── Error handling ────────────────────────────────────────────────────────
  bot.catch((err) => {
    console.error('Bot error:', err);
  });

  return bot;
}
