# smart_verifier.py
import asyncio
import random
import hashlib
import json
import logging
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import aiohttp
from playwright.async_api import async_playwright
import pyotp

from delayed_verifier import DelayedVerifier

logger = logging.getLogger(__name__)


class SmartVerifier:
    """
    نظام التحقق الذكي مع:
    1. تقليل استهلاك البروكسي
    2. تغيير البصمة لكل عملية
    3. IP سكني مشابه لمنطقة الإيميل
    4. محاكاة سلوك بشري
    5. تجنب الإعلانات والموارد غير الضرورية
    """
    
    def __init__(self, proxy_pool):
        self.proxy_pool = proxy_pool
        self.browser_cache = {}
        self.verification_cache = {}
        self.delayed_verifier = DelayedVerifier(proxy_pool)
        
    async def verify_account(
        self, 
        email: str, 
        password: str, 
        totp_secret: str = None, 
        app_password: str = None,
        use_proxy: bool = True
    ) -> Dict:
        """
        التحقق الذكي من الحساب
        """
        cache_key = hashlib.md5(f"{email}{password}{totp_secret}{app_password}".encode()).hexdigest()
        if cache_key in self.verification_cache:
            cache_result = self.verification_cache[cache_key]
            if (datetime.now() - cache_result.get("cached_at", datetime.min)).seconds < 300:
                return cache_result.get("result", {"success": False, "error": "cached"})
        
        verification_type = self._determine_verification_type(totp_secret, app_password)
        
        proxy = None
        if use_proxy:
            proxy = await self._get_optimal_proxy(email)
            if not proxy:
                logger.warning(f"No proxy available for {email}, using direct connection")
        
        result = await self._perform_verification(
            email=email,
            password=password,
            totp_secret=totp_secret,
            app_password=app_password,
            proxy=proxy,
            verification_type=verification_type
        )
        
        self.verification_cache[cache_key] = {
            "result": result,
            "cached_at": datetime.now()
        }
        
        return result
    
    def _determine_verification_type(self, totp_secret: str, app_password: str) -> str:
        """تحديد نوع التحقق المطلوب"""
        if totp_secret and app_password:
            return "full"
        elif totp_secret:
            return "totp_only"
        else:
            return "basic"
    
    async def _get_optimal_proxy(self, email: str) -> Optional[str]:
        """اختيار البروكسي الأمثل حسب منطقة الإيميل"""
        domain = email.split('@')[1]
        region = self._get_domain_region(domain)
        return self.proxy_pool.get_proxy(preferred_region=region, avoid_recent=True)
    
    def _get_domain_region(self, domain: str) -> str:
        """تحديد منطقة الدومين"""
        domain_regions = {
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
        return domain_regions.get(domain, 'US')
    
    async def _perform_verification(
        self,
        email: str,
        password: str,
        totp_secret: str,
        app_password: str,
        proxy: str,
        verification_type: str
    ) -> Dict:
        
        fingerprint = self._generate_fingerprint()
        
        try:
            async with async_playwright() as p:
                launch_options = {
                    "headless": True,
                    "args": [
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                        '--no-sandbox',
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
                        '--disable-setuid-sandbox',
                        '--disable-web-resources',
                        '--blink-settings=imagesEnabled=false',
                        '--max_connections=6',
                    ]
                }
                
                if proxy:
                    launch_options["proxy"] = {"server": proxy}
                
                browser = await p.chromium.launch(**launch_options)
                
                context = await browser.new_context(
                    viewport=fingerprint['viewport'],
                    user_agent=fingerprint['user_agent'],
                    locale=fingerprint['locale'],
                    timezone_id=fingerprint['timezone'],
                    extra_http_headers=fingerprint['headers'],
                    java_script_enabled=True,
                    bypass_csp=True,
                    ignore_https_errors=True,
                )
                
                page = await context.new_page()
                
                await self._simulate_human_behavior(page)
                
                if verification_type == "full":
                    result = await self._verify_full(page, email, password, totp_secret, app_password)
                elif verification_type == "totp_only":
                    result = await self._verify_totp(page, email, password, totp_secret)
                else:
                    result = await self._verify_basic(page, email, password)
                
                await context.close()
                await browser.close()
                
                if proxy and result.get("success", False):
                    self.proxy_pool.mark_success(proxy)
                
                return result
                
        except Exception as e:
            logger.error(f"Verification error for {email}: {e}")
            if proxy:
                self.proxy_pool.mark_failed(proxy)
            return {
                "success": False,
                "error": str(e),
                "verification_type": verification_type
            }
    
    def _generate_fingerprint(self) -> Dict:
        """توليد بصمة فريدة لكل عملية"""
        return {
            "viewport": random.choice([
                {"width": 1920, "height": 1080},
                {"width": 1366, "height": 768},
                {"width": 1536, "height": 864},
                {"width": 1440, "height": 900},
                {"width": 1280, "height": 720},
            ]),
            "user_agent": random.choice([
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ]),
            "locale": random.choice(['en-US', 'en-GB', 'ar-SA', 'fr-FR', 'de-DE']),
            "timezone": random.choice(['America/New_York', 'Europe/London', 'Asia/Dubai', 'Europe/Paris', 'America/Los_Angeles']),
            "headers": {
                'Accept-Language': random.choice(['en-US,en;q=0.9', 'ar-SA,ar;q=0.9', 'fr-FR,fr;q=0.9']),
                'Accept-Encoding': 'gzip, deflate, br',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Cache-Control': 'max-age=0',
            }
        }
    
    async def _simulate_human_behavior(self, page):
        """محاكاة سلوك بشري طبيعي"""
        await asyncio.sleep(random.uniform(1, 3))
        
        for _ in range(random.randint(3, 6)):
            x = random.randint(100, 1000)
            y = random.randint(100, 600)
            await page.mouse.move(x, y, steps=random.randint(5, 15))
            await asyncio.sleep(random.uniform(0.1, 0.3))
        
        for _ in range(random.randint(2, 4)):
            await page.mouse.wheel(0, random.randint(100, 300))
            await asyncio.sleep(random.uniform(0.5, 1.5))
    
    async def _verify_basic(self, page, email: str, password: str) -> Dict:
        """تحقق أساسي (أقل استهلاك)"""
        try:
            await page.goto('https://web.telegram.org/k/', wait_until='domcontentloaded')
            await asyncio.sleep(random.uniform(1, 2))
            
            await page.fill('input[type="text"]', email)
            await asyncio.sleep(random.uniform(0.5, 1))
            
            error = await page.is_visible('div:has-text("Invalid")')
            if error:
                return {"success": False, "error": "الإيميل غير صحيح", "reason": "email_invalid"}
            
            return {"success": False, "error": "يتطلب التحقق اليدوي", "requires_manual": True}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _verify_totp(self, page, email: str, password: str, totp_secret: str) -> Dict:
        """تحقق مع TOTP (استهلاك متوسط)"""
        try:
            await page.goto('https://web.telegram.org/k/', wait_until='domcontentloaded')
            await asyncio.sleep(random.uniform(1, 2))
            
            await page.fill('input[type="text"]', email)
            await page.click('button:has-text("Next")')
            await asyncio.sleep(random.uniform(1, 2))
            
            await page.fill('input[type="password"]', password)
            await page.click('button:has-text("Sign in")')
            await asyncio.sleep(random.uniform(2, 3))
            
            totp = pyotp.TOTP(totp_secret)
            code = totp.now()
            
            await page.fill('input[type="text"]', code)
            await page.click('button:has-text("Confirm")')
            await asyncio.sleep(random.uniform(2, 3))
            
            if await page.is_visible('.chat-list'):
                return {
                    "success": True,
                    "type": "totp_only",
                    "verified_at": datetime.now().isoformat(),
                    "details": {
                        "email_verified": True,
                        "password_verified": True,
                        "totp_verified": True
                    }
                }
            else:
                return {
                    "success": False,
                    "error": "فشل التحقق من TOTP",
                    "reason": "totp_invalid"
                }
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _verify_full(self, page, email: str, password: str, totp_secret: str, app_password: str) -> Dict:
        """تحقق كامل (أكثر استهلاك)"""
        try:
            result = await self._verify_totp(page, email, password, totp_secret)
            
            if result.get('success', False):
                if await page.is_visible('input[placeholder*="App Password"]'):
                    await page.fill('input[type="password"]', app_password)
                    await page.click('button:has-text("Confirm")')
                    await asyncio.sleep(random.uniform(2, 3))
                    
                    if await page.is_visible('div:has-text("Invalid")'):
                        return {
                            "success": False,
                            "error": "كلمة مرور التطبيق غير صحيحة",
                            "reason": "app_password_invalid"
                        }
                
                if await page.is_visible('.chat-list'):
                    return {
                        "success": True,
                        "type": "full",
                        "verified_at": datetime.now().isoformat(),
                        "details": {
                            "email_verified": True,
                            "password_verified": True,
                            "totp_verified": True,
                            "app_password_verified": True
                        }
                    }
            
            return result
            
        except Exception as e:
            return {"success": False, "error": str(e)}
