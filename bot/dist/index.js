"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
// Railway injects env vars directly — no dotenv needed in production
const bot_js_1 = require("./bot.js");
const bot = (0, bot_js_1.createBot)();
console.log('🤖 Starting bot...');
bot.start({
    onStart: (info) => {
        console.log(`✅ Bot @${info.username} is running`);
    },
});
// Graceful shutdown
process.once('SIGINT', () => bot.stop());
process.once('SIGTERM', () => bot.stop());
