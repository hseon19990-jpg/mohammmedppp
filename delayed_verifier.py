# delayed_verifier.py
import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
import logging
from playwright.async_api import async_playwright
import pyotp

logger = logging.getLogger(__name__)

class DelayedVerifier:
    """التحقق من الحساب مع تشخيص دقيق لأسباب الفشل"""
    
    def __init__(self, proxy_pool=None):
        self.proxy_pool = proxy_pool
        
    async def verify_after_24h(self, user_id: int, account_data: dict) -> dict:
        email = account_data["email"]
        password = account_data["password"]
        totp_secret = account_data.get("totp_secret")
        app_password = account_data.get("app_password")
        
        logger.info(f"Starting verification for {email}")
        
        # ====== تعطيل البروكسي تماماً ======
        proxy = None  # إجبارياً بدون بروكسي
        
        error_details = {}
        step = "initialization"
        
        try:
            async with async_playwright() as p:
                step = "launch_browser"
                launch_options = {
                    "headless": True,
                    "args": [
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--disable-images',
                        '--disable-extensions',
                        '--single-process',
                        '--max_connections=3',
                        '--disable-accelerated-2d-canvas',
                        '--disable-accelerated-jpeg-decoding',
                        '--disable-accelerated-mjpeg-decode',
                        '--disable-accelerated-video-decode'
                    ]
                }
                
                try:
                    browser = await p.chromium.launch(**launch_options)
                except Exception as e:
                    return {
                        "status": "failed",
                        "message": "فشل تشغيل المتصفح",
                        "reason": "browser_launch_failed",
                        "details": {
                            "step": step,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "solution": "تأكد من تثبيت Playwright: playwright install chromium"
                        }
                    }
                
                step = "create_context"
                try:
                    context = await browser.new_context(
                        viewport={'width': 1280, 'height': 720},
                        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        locale='en-US',
                        timezone_id='America/New_York'
                    )
                    page = await context.new_page()
                except Exception as e:
                    await browser.close()
                    return {
                        "status": "failed",
                        "message": "فشل إنشاء سياق المتصفح",
                        "reason": "context_creation_failed",
                        "details": {
                            "step": step,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "solution": "حاول تحديث Playwright: pip install --upgrade playwright"
                        }
                    }
                
                step = "goto_page"
                try:
                    await page.goto('https://web.telegram.org/k/', wait_until='domcontentloaded', timeout=30000)
                except Exception as e:
                    await browser.close()
                    return {
                        "status": "failed",
                        "message": "فشل تحميل صفحة Telegram",
                        "reason": "page_load_failed",
                        "details": {
                            "step": step,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "solution": "السيرفر قد يكون محظوراً من Telegram. جرب إضافة بروكسي لاحقاً."
                        }
                    }
                
                # محاكاة سلوك بشري
                await self._simulate_human_behavior(page)
                
                # بدء تسجيل الدخول
                step = "enter_email"
                result = await self._attempt_login_advanced(
                    page, email, password, totp_secret, app_password
                )
                
                await browser.close()
                return result
                
        except Exception as e:
            return {
                "status": "failed",
                "message": f"خطأ تقني غير متوقع",
                "reason": "unexpected_error",
                "details": {
                    "step": step,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "solution": "أعد تشغيل البوت. إذا تكرر، راجع السجلات."
                }
            }
    
    async def _simulate_human_behavior(self, page):
        await asyncio.sleep(random.uniform(1, 3))
        for _ in range(random.randint(3, 6)):
            x = random.randint(100, 1000)
            y = random.randint(100, 600)
            await page.mouse.move(x, y, steps=random.randint(5, 15))
            await asyncio.sleep(random.uniform(0.1, 0.3))
        for _ in range(random.randint(2, 4)):
            await page.mouse.wheel(0, random.randint(100, 300))
            await asyncio.sleep(random.uniform(0.5, 1.5))
    
    async def _attempt_login_advanced(self, page, email: str, password: str, 
                                      totp_secret: str = None, app_password: str = None) -> dict:
        try:
            # إدخال الإيميل
            logger.info(f"Entering email: {email}")
            await page.fill('input[type="text"]', email)
            await page.click('button:has-text("Next")')
            await asyncio.sleep(random.uniform(3, 5))
            
            # التحقق من صحة الإيميل
            if await page.is_visible('div:has-text("Invalid")'):
                return {"success": False, "message": "الإيميل غير موجود", "reason": "email_invalid"}
            if await page.is_visible('div:has-text("not found")'):
                return {"success": False, "message": "الحساب غير موجود", "reason": "account_not_found"}
            
            # إدخال كلمة المرور
            logger.info(f"Entering password for {email}")
            await page.fill('input[type="password"]', password)
            await page.click('button:has-text("Sign in")')
            await asyncio.sleep(random.uniform(5, 7))
            
            # التحقق من صحة كلمة المرور
            if await page.is_visible('div:has-text("Incorrect password")'):
                return {"success": False, "message": "كلمة المرور غير صحيحة", "reason": "password_incorrect"}
            
            # التحقق من TOTP
            if await page.is_visible('input[placeholder*="Code"]'):
                if not totp_secret:
                    return {"success": False, "message": "مطلوب TOTP ولكن لم يتم إرساله", "reason": "totp_required"}
                totp = pyotp.TOTP(totp_secret)
                code = totp.now()
                logger.info(f"Entering TOTP code for {email}")
                await page.fill('input[type="text"]', code)
                await page.click('button:has-text("Confirm")')
                await asyncio.sleep(random.uniform(3, 5))
                if await page.is_visible('div:has-text("Invalid code")'):
                    return {"success": False, "message": "رمز TOTP غير صحيح", "reason": "totp_invalid"}
            
            # التحقق من App Password
            if await page.is_visible('input[placeholder*="App Password"]'):
                if not app_password:
                    return {"success": False, "message": "مطلوب App Password ولكن لم يتم إرساله", "reason": "app_password_required"}
                logger.info(f"Entering App Password for {email}")
                await page.fill('input[type="password"]', app_password)
                await page.click('button:has-text("Confirm")')
                await asyncio.sleep(random.uniform(3, 5))
                if await page.is_visible('div:has-text("Invalid")'):
                    return {"success": False, "message": "كلمة مرور التطبيق غير صحيحة", "reason": "app_password_invalid"}
            
            # التحقق من نجاح تسجيل الدخول
            try:
                await page.wait_for_selector('.chat-list', timeout=15000)
                if await page.is_visible('div:has-text("banned")'):
                    return {"success": False, "message": "الحساب محظور", "reason": "account_banned"}
                if await page.is_visible('div:has-text("phone number")'):
                    return {"success": False, "message": "يطلب رقم هاتف", "reason": "phone_required"}
                
                logger.info(f"Successfully verified {email}")
                return {
                    "success": True,
                    "details": {
                        "email_verified": True,
                        "password_verified": True,
                        "totp_verified": bool(totp_secret),
                        "app_password_verified": bool(app_password)
                    }
                }
            except Exception as e:
                page_content = await page.content()
                if "banned" in page_content.lower():
                    return {"success": False, "message": "الحساب محظور", "reason": "account_banned"}
                return {"success": False, "message": "فشل تسجيل الدخول", "reason": "login_failed"}
                
        except Exception as e:
            return {"success": False, "message": f"خطأ: {str(e)}", "reason": "technical_error"}
