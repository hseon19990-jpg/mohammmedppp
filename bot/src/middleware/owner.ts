import type { Context, NextFunction } from 'grammy';

function getOwnerId(): number | null {
  const rawOwnerId = process.env.OWNER_TELEGRAM_ID?.trim();
  if (!rawOwnerId) return null;

  const ownerId = Number(rawOwnerId);
  return Number.isSafeInteger(ownerId) ? ownerId : null;
}

export function isOwner(ctx: Context): boolean {
  const userId = ctx.from?.id;
  const ownerId = getOwnerId();
  return userId !== undefined && ownerId !== null && userId === ownerId;
}

export async function ownerOnly(ctx: Context, next: NextFunction): Promise<void> {
  if (!isOwner(ctx)) {
    await ctx.reply('🚫 هذا الإجراء متاح للمالك فقط.');
    return;
  }
  await next();
}

export async function rejectNonOwnerCallback(ctx: Context): Promise<boolean> {
  if (isOwner(ctx)) return false;
  await ctx.answerCallbackQuery('🚫 هذا الإجراء متاح للمالك فقط.');
  return true;
}
