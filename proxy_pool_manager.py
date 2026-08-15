# proxy_pool_manager.py
import random
import time
import asyncio
import logging
from typing import Optional, Dict, List
from collections import defaultdict
import aiohttp

logger = logging.getLogger(__name__)


class SmartProxyPool:
    """
    إدارة ذكية للبروكسيات مع تتبع الاستخدام وتوزيع الأحمال
    """
    
    def __init__(self, daily_limit: int = 3000):
        self.proxies: List[str] = []
        self.usage_tracker: Dict[str, int] = defaultdict(int)
        self.failed_proxies: set = set()
        self.last_refresh: float = 0
        self.daily_limit: int = daily_limit
        self.used_today: int = 0
        self.proxy_regions: Dict[str, str] = {}
        self.blacklisted_proxies: set = set()
        self.proxy_scores: Dict[str, int] = defaultdict(int)
        
    async def refresh_proxies(self, api_url: Optional[str] = None):
        """
        تحديث قائمة البروكسيات من المزود
        """
        if not api_url:
            # استخدام قائمة بروكسيات افتراضية (للاختبار)
            self.proxies = [
                "socks5://proxy1.example.com:1080",
                "socks5://proxy2.example.com:1080",
                "socks5://proxy3.example.com:1080",
                "socks5://proxy4.example.com:1080",
                "socks5://proxy5.example.com:1080",
            ]
            for proxy in self.proxies:
                self.proxy_regions[proxy] = random.choice(["US", "UK", "DE", "FR", "CA"])
            self.last_refresh = time.time()
            logger.info(f"Loaded {len(self.proxies)} proxies (fallback)")
            return
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.proxies = data.get("proxies", [])
                        
                        for proxy in self.proxies:
                            region = data.get("regions", {}).get(proxy, random.choice(["US", "UK", "DE"]))
                            self.proxy_regions[proxy] = region
                            self.proxy_scores[proxy] = 100
                        
                        self.last_refresh = time.time()
                        logger.info(f"Loaded {len(self.proxies)} proxies from API")
                    else:
                        logger.warning(f"Failed to load proxies: {response.status}")
        except Exception as e:
            logger.error(f"Error loading proxies: {e}")
    
    def get_proxy(self, preferred_region: Optional[str] = None, avoid_recent: bool = True) -> Optional[str]:
        """
        اختيار بروكسي ذكي:
        1. يفضل البروكسيات من نفس المنطقة
        2. يتجنب البروكسيات المستخدمة مؤخراً
        3. يختار البروكسيات ذات الدرجة الأعلى
        """
        if self.used_today >= self.daily_limit:
            logger.warning("Daily proxy limit reached")
            return None
        
        available = [
            p for p in self.proxies
            if p not in self.failed_proxies
            and p not in self.blacklisted_proxies
            and self.proxy_scores.get(p, 100) > 50
        ]
        
        if not available:
            logger.warning("No available proxies")
            return None
        
        if preferred_region:
            region_filtered = [
                p for p in available
                if self.proxy_regions.get(p) == preferred_region
            ]
            if region_filtered:
                available = region_filtered
        
        if avoid_recent:
            avg_usage = sum(self.usage_tracker.values()) / max(len(self.usage_tracker), 1)
            available = [
                p for p in available
                if self.usage_tracker.get(p, 0) <= avg_usage * 1.5
            ]
        
        if not available:
            available = [p for p in self.proxies if p not in self.failed_proxies]
        
        if available:
            weights = []
            for proxy in available:
                score = self.proxy_scores.get(proxy, 100)
                weight = max(1, score / 10)
                weights.append(weight)
            
            # اختيار بروكسي حسب الوزن
            proxy = random.choices(available, weights=weights, k=1)[0]
            
            self.usage_tracker[proxy] += 1
            self.used_today += 1
            self.proxy_scores[proxy] = max(50, self.proxy_scores.get(proxy, 100) - 1)
            
            return proxy
        
        return None
    
    def mark_failed(self, proxy: str):
        """تسجيل بروكسي فاشل"""
        self.failed_proxies.add(proxy)
        self.proxy_scores[proxy] = max(0, self.proxy_scores.get(proxy, 100) - 20)
        logger.warning(f"Proxy {proxy} marked as failed")
    
    def mark_success(self, proxy: str):
        """تسجيل بروكسي ناجح"""
        if proxy in self.failed_proxies:
            self.failed_proxies.remove(proxy)
        self.proxy_scores[proxy] = min(100, self.proxy_scores.get(proxy, 100) + 2)
    
    def blacklist_proxy(self, proxy: str, reason: str = ""):
        """إضافة بروكسي إلى القائمة السوداء"""
        self.blacklisted_proxies.add(proxy)
        logger.warning(f"Proxy {proxy} blacklisted: {reason}")
    
    def get_stats(self) -> dict:
        """إحصائيات استخدام البروكسي"""
        return {
            "total_proxies": len(self.proxies),
            "available_proxies": len([p for p in self.proxies if p not in self.failed_proxies and p not in self.blacklisted_proxies]),
            "used_today": self.used_today,
            "daily_limit": self.daily_limit,
            "failed_proxies": len(self.failed_proxies),
            "blacklisted": len(self.blacklisted_proxies),
            "remaining": self.daily_limit - self.used_today,
            "proxy_scores": dict(list(self.proxy_scores.items())[:10])
        }
    
    def reset_daily_counter(self):
        """إعادة تعيين العداد اليومي"""
        self.used_today = 0
        self.usage_tracker.clear()
        logger.info("Daily proxy counter reset")
