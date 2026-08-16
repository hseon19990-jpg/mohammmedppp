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
        
        proxy = self._get_optimal_proxy(email) if self.proxy_pool else None
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    proxy={"server": proxy} if proxy else None,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
                        '--disable-setuid-sandbox',  # Railway fix
                        '--disable-images',
                        '--disable-extensions',
                        '--disable-plugins',
                        '--disable-default-apps',
                        '--disable-sync',
                        '--disable-background-networking',
                        '--disable-client-side-phishing-detection',
                        '--disable-component-update',
                        '--disable-domain-reliability',
                        '--disable-features=IsolateOrigins,site-per-process',
                        '--disable-hang-monitor',
                        '--disable-ipc-flooding-protection',
                        '--disable-popup-blocking',
                        '--disable-prompt-on-repost',
                        '--disable-renderer-backgrounding',
                        '--blink-settings=imagesEnabled=false',
                        '--max_connections=6',
                    ]
                )
                
                context = await browser.new_context(
                    viewport={'width': 1280, 'height': 720},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    locale='en-US',
                    timezone_id='America/New_York',
                )
                
                page = await context.new_page()
                
                # محاكاة سلوك بشري
                await self._simulate_human_behavior(page)
                
                result = await self._attempt_login_advanced(page, email, password, totp_secret, app_password)
                
                await browser.close()
                
                if result["success"]:
                    return {
                        "status": "verified",
                        "message": "✅ الحساب سليم",
                        "details": result.get("details", {})
                    }
                else:
                    return {
                        "status": "failed",
                        "message": result.get("message", "فشل التحقق"),
                        "reason": result.get("reason", "unknown"),
                        "details": result.get("details", {})
                    }
                    
        except Exception as e:
            logger.error(f"Verification error for {email}: {e}")
            return {
                "status": "failed",
                "message": f"خطأ تقني: {str(e)}",
                "reason": "technical_error",
                "details": {"error": str(e)}
            }
    
    async def _simulate_human_behavior(self, page):
        """محاكاة سلوك بشري طبيعي"""
        await asyncio.sleep(random.uniform(1, 3))
        
        # حركات الماوس العشوائية
        for _ in range(random.randint(3, 6)):
            x = random.randint(100, 1000)
            y = random.randint(100, 600)
            await page.mouse.move(x, y, steps=random.randint(5, 15))
            await asyncio.sleep(random.uniform(0.1, 0.3))
        
        # التمرير العشوائي
        for _ in range(random.randint(2, 4)):
            await page.mouse.wheel(0, random.randint(100, 300))
            await asyncio.sleep(random.uniform(0.5, 1.5))
    
    def _get_optimal_proxy(self, email: str) -> Optional[str]:
        if not self.proxy_pool:
            return None
        
        domain = email.split('@')[1]
        regions = {
            'gmail.com': 'US',
            'yahoo.com': 'US', 
            'outlook.com': 'US',
            'hotmail.com': 'US',
            'protonmail.com': 'CH',
            'mail.ru': 'RU',
            'yandex.ru': 'RU',
            'gmx.com': 'DE',
            'web.de': 'DE',
            'libero.it': 'IT',
            'orange.fr': 'FR',
        }
        preferred_region = regions.get(domain, 'US')
        return self.proxy_pool.get_proxy(preferred_region=preferred_region) if self.proxy_pool else None
    
    async def _attempt_login_advanced(self, page, email: str, password: str, 
                                      totp_secret: str = None, app_password: str = None) -> dict:
        """
        محاولة تسجيل الدخول مع تشخيص دقيق للأسباب
        """
        try:
            # جولة 1: التحقق من تحميل الصفحة
            logger.info(f"Loading page for {email}")
            await page.goto('https://web.telegram.org/k/', wait_until='domcontentloaded')
            await asyncio.sleep(random.uniform(3, 5))
            
            # جولة 2: التحقق من وجود حقل الإيميل (كشف الحظر أو مشاكل الشبكة)
            try:
                await page.wait_for_selector('input[type="text"]', timeout=5000)
            except Exception as e:
                # التحقق من وجود رسائل الحظر من Telegram
                page_content = await page.content()
                if "Too many requests" in page_content or "429" in page_content:
                    return {"success": False, "message": "Telegram حظر الطلب بسبب كثرة المحاولات", "reason": "rate_limited", "details": {"content_snippet": page_content[:200]}}
                elif "blocked" in page_content.lower():
                    return {"success": False, "message": "تم حظر الـ IP من قبل Telegram", "reason": "ip_blocked", "details": {"content_snippet": page_content[:200]}}
                else:
                    return {"success": False, "message": "لم يتم العثور على حقل الإيميل", "reason": "element_not_found", "details": {"error": str(e)}}
            
            # جولة 3: إدخال الإيميل
            logger.info(f"Entering email: {email}")
            await page.fill('input[type="text"]', email)
            await page.click('button:has-text("Next")')
            await asyncio.sleep(random.uniform(3, 5))
            
            # جولة 4: التحقق من صحة الإيميل
            if await page.is_visible('div:has-text("Invalid")'):
                return {"success": False, "message": "الإيميل غير موجود أو غير صالح", "reason": "email_invalid", "details": {"email": email}}
            
            if await page.is_visible('div:has-text("not found")'):
                return {"success": False, "message": "الحساب غير موجود", "reason": "account_not_found", "details": {"email": email}}
            
            # جولة 5: إدخال كلمة المرور
            logger.info(f"Entering password for {email}")
            await page.fill('input[type="password"]', password)
            await page.click('button:has-text("Sign in")')
            await asyncio.sleep(random.uniform(5, 7))
            
            # جولة 6: التحقق من صحة كلمة المرور
            if await page.is_visible('div:has-text("Incorrect password")'):
                return {"success": False, "message": "كلمة المرور غير صحيحة", "reason": "password_incorrect", "details": {"email": email}}
            
            # جولة 7: التحقق من TOTP
            if await page.is_visible('input[placeholder*="Code"]'):
                if not totp_secret:
                    return {"success": False, "message": "الحساب يطلب رمز TOTP ولكن لم يتم إرساله", "reason": "totp_required", "details": {"email": email}}
                
                totp = pyotp.TOTP(totp_secret)
                code = totp.now()
                
                logger.info(f"Entering TOTP code for {email}")
                await page.fill('input[type="text"]', code)
                await page.click('button:has-text("Confirm")')
                await asyncio.sleep(random.uniform(3, 5))
                
                if await page.is_visible('div:has-text("Invalid code")'):
                    return {"success": False, "message": "رمز TOTP غير صحيح", "reason": "totp_invalid", "details": {"email": email, "code": code}}
            
            # جولة 8: التحقق من App Password
            if await page.is_visible('input[placeholder*="App Password"]'):
                if not app_password:
                    return {"success": False, "message": "الحساب يطلب كلمة مرور تطبيق ولكن لم يتم إرسالها", "reason": "app_password_required", "details": {"email": email}}
                
                logger.info(f"Entering App Password for {email}")
                await page.fill('input[type="password"]', app_password)
                await page.click('button:has-text("Confirm")')
                await asyncio.sleep(random.uniform(3, 5))
                
                if await page.is_visible('div:has-text("Invalid")'):
                    return {"success": False, "message": "كلمة مرور التطبيق غير صحيحة", "reason": "app_password_invalid", "details": {"email": email}}
            
            # جولة 9: التحقق من نجاح تسجيل الدخول
            try:
                await page.wait_for_selector('.chat-list', timeout=15000)
                
                # التحقق من حالة الحساب
                if await page.is_visible('div:has-text("banned")'):
                    return {"success": False, "message": "الحساب محظور من Telegram", "reason": "account_banned", "details": {"email": email}}
                
                if await page.is_visible('div:has-text("phone number")'):
                    return {"success": False, "message": "الحساب يطلب رقم هاتف للتحقق", "reason": "phone_required", "details": {"email": email}}
                
                # نجاح!
                logger.info(f"Successfully verified {email}")
                return {
                    "success": True,
                    "details": {
                        "email_verified": True,
                        "password_verified": True,
                        "totp_verified": bool(totp_secret),
                        "app_password_verified": bool(app_password),
                        "account_active": True
                    }
                }
                
            except Exception as e:
                # التحقق من وجود رسائل الحظر أو مشاكل أخرى
                page_content = await page.content()
                if "banned" in page_content.lower():
                    return {"success": False, "message": "الحساب محظور من Telegram", "reason": "account_banned", "details": {"content_snippet": page_content[:200]}}
                else:
                    return {"success": False, "message": "فشل تسجيل الدخول بسبب خطأ غير معروف", "reason": "login_failed", "details": {"error": str(e)}}
                
        except Exception as e:
            return {"success": False, "message": f"خطأ تقني: {str(e)}", "reason": "technical_error", "details": {"error": str(e)}}
