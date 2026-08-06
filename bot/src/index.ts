import 'dotenv/config';
import { createBot } from './bot.js';

const bot = createBot();

console.log('🤖 Starting bot...');

bot.start({
  onStart: (info) => {
    console.log(`✅ Bot @${info.username} is running`);
  },
});

// Graceful shutdown
process.once('SIGINT',  () => bot.stop());
process.once('SIGTERM', () => bot.stop());
