import type { MyContext } from '../bot.js';
import { sendMainMenu } from './menu.js';

export async function handleStart(ctx: MyContext): Promise<void> {
  await sendMainMenu(
    ctx,
    '👋 مرحباً بك في بوت إدارة الحسابات الآمن\n\n🔒 جميع بياناتك مشفرة بـ AES-256-GCM',
  );
}
