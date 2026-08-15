# verification_engine.py
import asyncio
import random
import logging
from typing import Dict, Optional, Tuple
from datetime import datetime
import re

import pyotp
from playwright.async_api import async_playwright, TimeoutError

logger = logging.getLogger(__name__)


class VerificationEngine:
    """
    محرك التحقق الأساسي - يقوم بتسجيل الدخول الفعلي إلى Telegram Web
    """
    
    def __init__(self, proxy=None, headless=True):
        self.proxy = proxy
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()
    
    async def cleanup(self):
        """تنظيف الموارد"""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
    
    async def initialize_browser(self):
        """تهيئة المتصفح"""
        try:
            async with async_playwright() as p:
                launch_options = {
                    "headless": self.headless,
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
                
                if self.proxy:
                    launch_options["proxy"] = {"server": self.proxy}
                
                self.browser = await p.chromium.launch(**launch_options)
                
                self.context = await self.browser.new_context(
                    viewport={'width': 1280, 'height': 720},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    locale='en-US',
                    timezone_id='America/New_York',
                )
                
                self.page = await self.context.new_page()
                
                await self.page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                    window.chrome = { runtime: {} };
                """)
                
                return True
                
        except Exception as e:
            logger.error(f"Failed to initialize browser: {e}")
            return False
    
    async def verify_account(
        self, 
        email: str, 
        password: str, 
        totp_secret: Optional[str] = None,
        app_password: Optional[str] = None
    ) -> Dict:
        """التحقق من الحساب عن طريق تسجيل الدخول الفعلي"""
        result = {
            "success": False,
            "verified": False,
            "reason": "",
            "details": {},
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            if not await self.initialize_browser():
                result["reason"] = "فشل تهيئة المتصفح"
                return result
            
            await self.page.goto('https://web.telegram.org/k/', wait_until='domcontentloaded')
            await asyncio.sleep(random.uniform(1, 3))
            
            logger.info(f"Entering email: {email}")
            await self._enter_email(email)
            
            email_valid = await self._check_email_validity()
            if not email_valid:
                result["reason"] = "الإيميل غير صحيح أو غير مسجل"
                result["details"]["email"] = False
                return result
            result["details"]["email"] = True
            
            logger.info("Entering password")
            await self._enter_password(password)
            
            password_valid = await self._check_password_validity()
            if not password_valid:
                result["reason"] = "كلمة المرور غير صحيحة"
                result["details"]["password"] = False
                return result
            result["details"]["password"] = True
            
            if totp_secret:
                logger.info("Verifying TOTP")
                totp_valid = await self._verify_totp(totp_secret)
                if not totp_valid:
                    result["reason"] = "رمز TOTP غير صحيح"
                    result["details"]["totp"] = False
                    return result
                result["details"]["totp"] = True
            
            if app_password:
                logger.info("Verifying App Password")
                app_pass_valid = await self._verify_app_password(app_password)
                if not app_pass_valid:
                    result["reason"] = "كلمة مرور التطبيق غير صحيحة"
                    result["details"]["app_password"] = False
                    return result
                result["details"]["app_password"] = True
            
            login_success = await self._check_login_success()
            if not login_success:
                result["reason"] = "فشل تسجيل الدخول - قد يكون الحساب محظوراً"
                return result
            
            result["success"] = True
            result["verified"] = True
            result["reason"] = "تم التحقق بنجاح"
            result["details"]["account_active"] = True
            
            logger.info(f"Account {email} verified successfully")
            return result
            
        except TimeoutError as e:
            logger.error(f"Timeout during verification: {e}")
            result["reason"] = "انتهت المهلة أثناء التحقق"
            return result
        except Exception as e:
            logger.error(f"Verification error: {e}")
            result["reason"] = f"خطأ: {str(e)}"
            return result
        finally:
            await self.cleanup()
    
    async def _enter_email(self, email: str):
        """إدخال الإيميل"""
        try:
            selectors = [
                'input[type="text"]',
                'input[name="phone"]',
                'input[name="email"]',
                'input[placeholder*="Phone"]',
                'input[placeholder*="Email"]',
                '#login-phone-input',
            ]
            
            for selector in selectors:
                try:
                    element = await self.page.wait_for_selector(selector, timeout=3000)
                    if element:
                        await element.fill(email)
                        await asyncio.sleep(random.uniform(0.5, 1))
                        
                        next_buttons = [
                            'button[type="submit"]',
                            'button:has-text("Next")',
                            'button:has-text("Continue")',
                            '.btn-primary',
                        ]
                        
                        for btn_selector in next_buttons:
                            try:
                                button = await self.page.wait_for_selector(btn_selector, timeout=2000)
                                if button:
                                    await button.click()
                                    await asyncio.sleep(random.uniform(1, 2))
                                    return
                            except:
                                continue
                        return
                except:
                    continue
        except Exception as e:
            logger.error(f"Error entering email: {e}")
    
    async def _check_email_validity(self) -> bool:
        """التحقق من صحة الإيميل"""
        try:
            error_selectors = [
                '.error-message',
                '.alert-danger',
                '[role="alert"]',
                'div:has-text("Invalid")',
                'div:has-text("not found")',
                'div:has-text("does not exist")',
            ]
            
            for selector in error_selectors:
                try:
                    error = await self.page.wait_for_selector(selector, timeout=2000)
                    if error:
                        return False
                except:
                    continue
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking email validity: {e}")
            return False
    
    async def _enter_password(self, password: str):
        """إدخال كلمة المرور"""
        try:
            selectors = [
                'input[type="password"]',
                'input[name="password"]',
                '#login-password-input',
            ]
            
            for selector in selectors:
                try:
                    element = await self.page.wait_for_selector(selector, timeout=3000)
                    if element:
                        await element.fill(password)
                        await asyncio.sleep(random.uniform(0.5, 1))
                        
                        login_buttons = [
                            'button[type="submit"]',
                            'button:has-text("Login")',
                            'button:has-text("Sign in")',
                            '.btn-primary',
                        ]
                        
                        for btn_selector in login_buttons:
                            try:
                                button = await self.page.wait_for_selector(btn_selector, timeout=2000)
                                if button:
                                    await button.click()
                                    await asyncio.sleep(random.uniform(2, 3))
                                    return
                            except:
                                continue
                        return
                except:
                    continue
        except Exception as e:
            logger.error(f"Error entering password: {e}")
    
    async def _check_password_validity(self) -> bool:
        """التحقق من صحة كلمة المرور"""
        try:
            error_selectors = [
                '.error-message',
                '.alert-danger',
                '[role="alert"]',
                'div:has-text("Incorrect password")',
                'div:has-text("Invalid password")',
                'div:has-text("wrong password")',
            ]
            
            for selector in error_selectors:
                try:
                    error = await self.page.wait_for_selector(selector, timeout=2000)
                    if error:
                        return False
                except:
                    continue
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking password validity: {e}")
            return False
    
    async def _verify_totp(self, totp_secret: str) -> bool:
        """التحقق من TOTP"""
        try:
            totp = pyotp.TOTP(totp_secret)
            code = totp.now()
            
            selectors = [
                'input[type="text"]',
                'input[name="code"]',
                'input[placeholder*="Code"]',
                'input[placeholder*="6-digit"]',
                '#login-code-input',
            ]
            
            for selector in selectors:
                try:
                    element = await self.page.wait_for_selector(selector, timeout=3000)
                    if element:
                        await element.fill(code)
                        await asyncio.sleep(random.uniform(0.5, 1))
                        
                        confirm_buttons = [
                            'button[type="submit"]',
                            'button:has-text("Confirm")',
                            'button:has-text("Verify")',
                            '.btn-primary',
                        ]
                        
                        for btn_selector in confirm_buttons:
                            try:
                                button = await self.page.wait_for_selector(btn_selector, timeout=2000)
                                if button:
                                    await button.click()
                                    await asyncio.sleep(random.uniform(2, 3))
                                    return True
                            except:
                                continue
                        return True
                except:
                    continue
            
            return False
            
        except Exception as e:
            logger.error(f"Error verifying TOTP: {e}")
            return False
    
    async def _verify_app_password(self, app_password: str) -> bool:
        """التحقق من App Password"""
        try:
            selectors = [
                'input[type="password"]',
                'input[name="app_password"]',
                'input[placeholder*="App Password"]',
                'input[placeholder*="16-digit"]',
            ]
            
            for selector in selectors:
                try:
                    element = await self.page.wait_for_selector(selector, timeout=3000)
                    if element:
                        await element.fill(app_password)
                        await asyncio.sleep(random.uniform(0.5, 1))
                        
                        confirm_buttons = [
                            'button[type="submit"]',
                            'button:has-text("Confirm")',
                            'button:has-text("Verify")',
                            '.btn-primary',
                        ]
                        
                        for btn_selector in confirm_buttons:
                            try:
                                button = await self.page.wait_for_selector(btn_selector, timeout=2000)
                                if button:
                                    await button.click()
                                    await asyncio.sleep(random.uniform(2, 3))
                                    return True
                            except:
                                continue
                        return True
                except:
                    continue
            
            return False
            
        except Exception as e:
            logger.error(f"Error verifying App Password: {e}")
            return False
    
    async def _check_login_success(self) -> bool:
        """التحقق من نجاح تسجيل الدخول"""
        try:
            success_selectors = [
                '.chat-list',
                '.sidebar',
                '.main-content',
                '.messages-container',
                '.chat-list-wrapper',
            ]
            
            for selector in success_selectors:
                try:
                    element = await self.page.wait_for_selector(selector, timeout=10000)
                    if element:
                        return True
                except:
                    continue
            
            ban_selectors = [
                'div:has-text("banned")',
                'div:has-text("suspended")',
                'div:has-text("blocked")',
            ]
            
            for selector in ban_selectors:
                try:
                    element = await self.page.wait_for_selector(selector, timeout=2000)
                    if element:
                        return False
                except:
                    continue
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking login success: {e}")
            return False
