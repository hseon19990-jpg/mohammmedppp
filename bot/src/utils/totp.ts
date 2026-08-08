import { authenticator } from 'otplib';

export function generateTOTP(secret: string): string {
  try {
    return authenticator.generate(secret.replace(/\s+/g, '').toUpperCase());
  } catch {
    throw new Error('مفتاح المصادقة الثنائية غير صالح');
  }
}

export function getRemainingSeconds(): number {
  return 30 - (Math.floor(Date.now() / 1000) % 30);
}

export function validateTOTPSecret(secret: string): boolean {
  try {
    const clean = secret.replace(/\s+/g, '').toUpperCase();
    authenticator.generate(clean);
    return true;
  } catch {
    return false;
  }
}
