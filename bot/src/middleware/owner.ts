import type { Context, NextFunction } from 'grammy';

const OWNER_ID = Number(process.env.OWNER_TELEGRAM_ID);

export async function ownerOnly(ctx: Context, next: NextFunction): Promise<void> {
  const userId = ctx.from?.id;
  if (!userId || userId !== OWNER_ID) {
    await ctx.reply('🚫 غير مصرح لك باستخدام هذا البوت.');
    return;
  }
  await next();
}
