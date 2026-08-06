"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.generateTOTP = generateTOTP;
exports.getRemainingSeconds = getRemainingSeconds;
exports.validateTOTPSecret = validateTOTPSecret;
const otplib_1 = require("otplib");
function generateTOTP(secret) {
    try {
        return otplib_1.authenticator.generate(secret.replace(/\s+/g, '').toUpperCase());
    }
    catch {
        throw new Error('مفتاح المصادقة الثنائية غير صالح');
    }
}
function getRemainingSeconds() {
    return 30 - (Math.floor(Date.now() / 1000) % 30);
}
function validateTOTPSecret(secret) {
    try {
        const clean = secret.replace(/\s+/g, '').toUpperCase();
        otplib_1.authenticator.generate(clean);
        return true;
    }
    catch {
        return false;
    }
}
