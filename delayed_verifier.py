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
    """التحقق من الحساب بعد 24 ساعة (مع دعم البروكسي وبدونه)"""
    
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
                        '--disable-setuid-sandbox',  # <-- الحل الحاسم لمشكلة Railway
                        '--disable-images',
                    ]
                )
                
                context = await browser.new_context(
                    viewport={'width': 1280, 'height': 720},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                
                page = await context.new_page()
                
                result = await self._attempt_login(page, email, password, totp_secret, app_password)
                
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
                        "message": result.get("error", "فشل التحقق"),
                        "reason": result.get("reason", "unknown")
                    }
                    
        except Exception as e:
            logger.error(f"Verification error for {email}: {e}")
            return {
                "status": "failed",
                "message": f"خطأ في التحقق: {str(e)}",
                "reason": "technical_error"
            }
    
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
    
    async def _attempt_login(self, page, email: str, password: str, 
                             totp_secret: str = None, app_password: str = None) -> dict:
        try:
            await page.goto('https://web.telegram.org/k/', wait_until='domcontentloaded')
            await asyncio.sleep(random.uniform(2, 4))
            
            await page.fill('input[type="text"]', email)
            await page.click('button:has-text("Next")')
            await asyncio.sleep(random.uniform(2, 3))
            
            if await page.is_visible('div:has-text("Invalid")'):
                return {"success": False, "error": "الإيميل غير موجود", "reason": "email_invalid"}
            
            if await page.is_visible('div:has-text("not found")'):
                return {"success": False, "error": "الحساب غير موجود", "reason": "account_not_found"}
            
            await page.fill('input[type="password"]', password)
            await page.click('button:has-text("Sign in")')
            await asyncio.sleep(random.uniform(3, 5))
            
            if await page.is_visible('div:has-text("Incorrect password")'):
                return {"success": False, "error": "كلمة المرور غير صحيحة", "reason": "password_incorrect"}
            
            if await page.is_visible('input[placeholder*="Code"]'):
                if not totp_secret:
                    return {"success": False, "error": "مطلوب TOTP", "reason": "totp_required"}
                
                totp = pyotp.TOTP(totp_secret)
                code = totp.now()
                
                await page.fill('input[type="text"]', code)
                await page.click('button:has-text("Confirm")')
                await asyncio.sleep(random.uniform(2, 3))
                
                if await page.is_visible('div:has-text("Invalid code")'):
                    return {"success": False, "error": "TOTP غير صحيح", "reason": "totp_invalid"}
            
            if await page.is_visible('input[placeholder*="App Password"]'):
                if not app_password:
                    return {"success": False, "error": "مطلوب App Password", "reason": "app_password_required"}
                
                await page.fill('input[type="password"]', app_password)
                await page.click('button:has-text("Confirm")')
                await asyncio.sleep(random.uniform(2, 3))
                
                if await page.is_visible('div:has-text("Invalid")'):
                    return {"success": False, "error": "App Password غير صحيحة", "reason": "app_password_invalid"}
            
            try:
                await page.wait_for_selector('.chat-list', timeout=10000)
                
                if await page.is_visible('div:has-text("banned")'):
                    return {"success": False, "error": "الحساب محظور", "reason": "account_banned"}
                
                if await page.is_visible('div:has-text("phone number")'):
                    return {"success": False, "error": "يطلب رقم هاتف", "reason": "phone_required"}
                
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
                
            except:
                return {"success": False, "error": "فشل تسجيل الدخول", "reason": "login_failed"}
                
        except Exception as e:
            return {"success": False, "error": str(e), "reason": "technical_error"}
