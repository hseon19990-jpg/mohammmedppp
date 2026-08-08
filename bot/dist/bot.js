"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.createBot = createBot;
const grammy_1 = require("grammy");
const owner_js_1 = require("./middleware/owner.js");
const start_js_1 = require("./handlers/start.js");
const menu_js_1 = require("./handlers/menu.js");
const add_js_1 = require("./handlers/add.js");
const view_js_1 = require("./handlers/view.js");
const edit_js_1 = require("./handlers/edit.js");
const verify_js_1 = require("./handlers/verify.js");
function createBot() {
    const token = process.env.BOT_TOKEN;
    if (!token)
        throw new Error('BOT_TOKEN environment variable is not set');
    const bot = new grammy_1.Bot(token);
    // Session middleware
    bot.use((0, grammy_1.session)({
        initial: () => ({}),
    }));
    // ── Commands ─────────────────────────────────────────────────────────────
    bot.command('start', start_js_1.handleStart);
    bot.command('menu', (ctx) => (0, menu_js_1.sendMainMenu)(ctx));
    // ── Callback queries ─────────────────────────────────────────────────────
    bot.callbackQuery('main_menu', async (ctx) => { await ctx.answerCallbackQuery(); await (0, menu_js_1.sendMainMenu)(ctx); });
    bot.callbackQuery('add_account', async (ctx) => { await ctx.answerCallbackQuery(); await (0, add_js_1.startAddFlow)(ctx); });
    bot.callbackQuery('view_all', async (ctx) => { if (await (0, owner_js_1.rejectNonOwnerCallback)(ctx))
        return; await ctx.answerCallbackQuery(); await (0, view_js_1.handleViewAll)(ctx); });
    bot.callbackQuery('edit_list', async (ctx) => { if (await (0, owner_js_1.rejectNonOwnerCallback)(ctx))
        return; await ctx.answerCallbackQuery(); await (0, edit_js_1.handleEditList)(ctx); });
    bot.callbackQuery('verify_list', async (ctx) => { if (await (0, owner_js_1.rejectNonOwnerCallback)(ctx))
        return; await ctx.answerCallbackQuery(); await (0, verify_js_1.handleVerifyList)(ctx); });
    bot.callbackQuery('skip_app_password', add_js_1.handleSkipAppPassword);
    bot.callbackQuery('cancel', async (ctx) => { await ctx.answerCallbackQuery(); await (0, menu_js_1.sendMainMenu)(ctx, '❌ تم الإلغاء'); });
    // Dynamic callbacks
    bot.callbackQuery(/^get_code:(.+)$/, async (ctx) => { if (await (0, owner_js_1.rejectNonOwnerCallback)(ctx))
        return; await (0, view_js_1.handleGetCode)(ctx, ctx.match[1]); });
    bot.callbackQuery(/^edit:(.+)$/, async (ctx) => { if (await (0, owner_js_1.rejectNonOwnerCallback)(ctx))
        return; await (0, edit_js_1.handleEditAccount)(ctx, ctx.match[1]); });
    bot.callbackQuery(/^edit_field:(.+):(.+)$/, async (ctx) => { if (await (0, owner_js_1.rejectNonOwnerCallback)(ctx))
        return; await (0, edit_js_1.handleEditField)(ctx, ctx.match[1], ctx.match[2]); });
    bot.callbackQuery(/^delete:(.+)$/, async (ctx) => { if (await (0, owner_js_1.rejectNonOwnerCallback)(ctx))
        return; await (0, view_js_1.handleDeleteConfirm)(ctx, ctx.match[1]); });
    bot.callbackQuery(/^delete_yes:(.+)$/, async (ctx) => { if (await (0, owner_js_1.rejectNonOwnerCallback)(ctx))
        return; await (0, view_js_1.handleDeleteYes)(ctx, ctx.match[1]); });
    bot.callbackQuery(/^verify:(.+)$/, async (ctx) => { if (await (0, owner_js_1.rejectNonOwnerCallback)(ctx))
        return; await (0, verify_js_1.handleVerifyAccount)(ctx, ctx.match[1]); });
    // ── Text messages (multi-step flows) ─────────────────────────────────────
    bot.on('message:text', async (ctx) => {
        const step = ctx.session.step;
        if (!step) {
            await (0, menu_js_1.sendMainMenu)(ctx);
            return;
        }
        if (step === 'add_email' ||
            step === 'add_password' ||
            step === 'add_totp' ||
            step === 'add_appPassword') {
            await (0, add_js_1.handleAddStep)(ctx);
            return;
        }
        if (step === 'edit_entering_value') {
            await (0, edit_js_1.handleEditValueInput)(ctx);
            return;
        }
    });
    // ── Error handling ────────────────────────────────────────────────────────
    bot.catch((err) => {
        console.error('Bot error:', err);
    });
    return bot;
}
