import nodemailer from 'nodemailer';

interface SmtpConfig {
  host: string;
  port: number;
  secure: boolean;
}

function getSmtpConfig(email: string): SmtpConfig | null {
  const domain = email.split('@')[1]?.toLowerCase();

  const configs: Record<string, SmtpConfig> = {
    'gmail.com':        { host: 'smtp.gmail.com',         port: 587, secure: false },
    'googlemail.com':   { host: 'smtp.gmail.com',         port: 587, secure: false },
    'outlook.com':      { host: 'smtp.office365.com',     port: 587, secure: false },
    'hotmail.com':      { host: 'smtp.office365.com',     port: 587, secure: false },
    'live.com':         { host: 'smtp.office365.com',     port: 587, secure: false },
    'msn.com':          { host: 'smtp.office365.com',     port: 587, secure: false },
    'yahoo.com':        { host: 'smtp.mail.yahoo.com',    port: 587, secure: false },
    'yahoo.co.uk':      { host: 'smtp.mail.yahoo.com',    port: 587, secure: false },
    'icloud.com':       { host: 'smtp.mail.me.com',       port: 587, secure: false },
    'me.com':           { host: 'smtp.mail.me.com',       port: 587, secure: false },
    'mac.com':          { host: 'smtp.mail.me.com',       port: 587, secure: false },
    'protonmail.com':   { host: 'smtp.protonmail.ch',     port: 587, secure: false },
    'proton.me':        { host: 'smtp.protonmail.ch',     port: 587, secure: false },
    'zoho.com':         { host: 'smtp.zoho.com',          port: 587, secure: false },
    'aol.com':          { host: 'smtp.aol.com',           port: 587, secure: false },
  };

  return configs[domain] ?? null;
}

export async function verifyEmailCredentials(
  email: string,
  appPassword: string,
): Promise<{ success: boolean; message: string }> {
  const smtpConfig = getSmtpConfig(email);

  if (!smtpConfig) {
    return {
      success: false,
      message: `⚠️ نوع البريد غير مدعوم للتحقق التلقائي\n(${email.split('@')[1]})`,
    };
  }

  const transporter = nodemailer.createTransport({
    ...smtpConfig,
    auth: { user: email, pass: appPassword },
    connectionTimeout: 10_000,
    greetingTimeout: 5_000,
    socketTimeout: 10_000,
  });

  try {
    await transporter.verify();
    return { success: true, message: '✅ بيانات الاعتماد صحيحة' };
  } catch (err: any) {
    const msg: string = err?.message ?? String(err);
    if (msg.includes('Invalid login') || msg.includes('Username and Password not accepted')) {
      return { success: false, message: '❌ إيميل أو كلمة مرور التطبيق غير صحيحة' };
    }
    return { success: false, message: `❌ فشل الاتصال: ${msg}` };
  }
}
