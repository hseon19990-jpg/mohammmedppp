"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.ownerOnly = ownerOnly;
const OWNER_ID = Number(process.env.OWNER_TELEGRAM_ID);
async function ownerOnly(ctx, next) {
    const userId = ctx.from?.id;
    if (!userId || userId !== OWNER_ID) {
        await ctx.reply('🚫 غير مصرح لك باستخدام هذا البوت.');
        return;
    }
    await next();
}
