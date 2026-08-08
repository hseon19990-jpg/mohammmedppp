"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.handleStart = handleStart;
const menu_js_1 = require("./menu.js");
async function handleStart(ctx) {
    await (0, menu_js_1.sendMainMenu)(ctx, '👋 مرحباً بك في بوت إدارة الحسابات الآمن\n\n🔒 جميع بياناتك مشفرة بـ AES-256-GCM');
}
