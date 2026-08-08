"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.rejectNonOwnerCallback = rejectNonOwnerCallback;
exports.isOwner = isOwner;
exports.ownerOnly = ownerOnly;
function getOwnerId() {
    const rawOwnerId = process.env.OWNER_TELEGRAM_ID?.trim();
    if (!rawOwnerId)
        return null;
    const ownerId = Number(rawOwnerId);
    return Number.isSafeInteger(ownerId) ? ownerId : null;
}
function isOwner(ctx) {
    const userId = ctx.from?.id;
    const ownerId = getOwnerId();
    return userId !== undefined && ownerId !== null && userId === ownerId;
}
async function ownerOnly(ctx, next) {
    if (!isOwner(ctx)) {
        await ctx.reply('🚫 هذا الإجراء متاح للمالك فقط.');
        return;
    }
    await next();
}
async function rejectNonOwnerCallback(ctx) {
    if (isOwner(ctx))
        return false;
    await ctx.answerCallbackQuery('🚫 هذا الإجراء متاح للمالك فقط.');
    return true;
}
