# fingerprint_generator.py
import random
import hashlib
import json
import platform
from typing import Dict, List, Optional
from datetime import datetime


class FingerprintGenerator:
    """
    توليد بصمات رقمية فريدة لكل عملية لتجنب الكشف
    """
    
    def __init__(self):
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7; rv:109.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/121.0",
        ]
        
        self.viewports = [
            {"width": 1920, "height": 1080},
            {"width": 1366, "height": 768},
            {"width": 1536, "height": 864},
            {"width": 1440, "height": 900},
            {"width": 1280, "height": 720},
            {"width": 1600, "height": 900},
            {"width": 2560, "height": 1440},
        ]
        
        self.locales = [
            'en-US', 'en-GB', 'en-CA', 'en-AU',
            'ar-SA', 'ar-EG', 'ar-AE',
            'fr-FR', 'fr-CA', 'de-DE',
            'es-ES', 'es-MX', 'pt-BR',
            'ru-RU', 'it-IT', 'nl-NL'
        ]
        
        self.timezones = [
            'America/New_York', 'America/Los_Angeles',
            'Europe/London', 'Europe/Paris', 'Europe/Berlin',
            'Asia/Dubai', 'Asia/Riyadh', 'Asia/Kolkata',
            'Australia/Sydney', 'Asia/Tokyo',
            'America/Sao_Paulo', 'Africa/Johannesburg'
        ]
        
        self.languages = [
            ['en-US', 'en'],
            ['en-GB', 'en'],
            ['ar-SA', 'ar'],
            ['fr-FR', 'fr'],
            ['de-DE', 'de'],
            ['es-ES', 'es'],
            ['pt-BR', 'pt'],
            ['ru-RU', 'ru'],
            ['it-IT', 'it'],
        ]
    
    def generate_fingerprint(self) -> Dict:
        """توليد بصمة فريدة"""
        user_agent = random.choice(self.user_agents)
        viewport = random.choice(self.viewports)
        locale = random.choice(self.locales)
        timezone = random.choice(self.timezones)
        languages = random.choice(self.languages)
        
        os_type = self._detect_os(user_agent)
        
        fingerprint = {
            "user_agent": user_agent,
            "viewport": viewport,
            "locale": locale,
            "timezone": timezone,
            "languages": languages,
            "os_type": os_type,
            "headers": {
                'Accept-Language': f"{languages[0]},{languages[1]};q=0.9",
                'Accept-Encoding': random.choice(['gzip, deflate, br', 'gzip, deflate', 'br, gzip']),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Cache-Control': 'max-age=0',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
            },
            "screen": {
                "width": viewport["width"],
                "height": viewport["height"],
                "color_depth": random.choice([24, 30, 32]),
                "pixel_ratio": random.choice([1, 1.25, 1.5, 2])
            },
            "platform": {
                "name": os_type,
                "version": self._get_os_version(os_type)
            },
            "browser": {
                "name": self._detect_browser(user_agent),
                "version": self._get_browser_version(user_agent)
            },
            "plugins": self._generate_plugins(os_type),
            "fonts": self._generate_fonts(os_type),
            "webgl": {
                "vendor": random.choice(["Google Inc.", "Intel", "NVIDIA Corporation", "AMD"]),
                "renderer": random.choice([
                    "ANGLE (Google, Vulkan, ...)",
                    "ANGLE (Intel, Direct3D, ...)",
                    "ANGLE (NVIDIA, Direct3D, ...)"
                ])
            },
            "canvas": self._generate_canvas_hash(),
            "webgl_hash": self._generate_webgl_hash(),
            "audio_hash": self._generate_audio_hash(),
            "fingerprint_hash": self._generate_fingerprint_hash()
        }
        
        return fingerprint
    
    def _detect_os(self, user_agent: str) -> str:
        """اكتشاف نظام التشغيل من User-Agent"""
        if 'Windows' in user_agent:
            return 'Windows'
        elif 'Macintosh' in user_agent or 'Mac OS' in user_agent:
            return 'macOS'
        elif 'Linux' in user_agent or 'Ubuntu' in user_agent:
            return 'Linux'
        elif 'Android' in user_agent:
            return 'Android'
        elif 'iPhone' in user_agent or 'iPad' in user_agent:
            return 'iOS'
        else:
            return 'Unknown'
    
    def _get_os_version(self, os_type: str) -> str:
        """الحصول على إصدار نظام التشغيل"""
        versions = {
            'Windows': random.choice(['10.0', '11.0', '10.0.19045', '10.0.22621']),
            'macOS': random.choice(['10.15.7', '11.7', '12.6', '13.5', '14.1']),
            'Linux': random.choice(['5.15', '6.2', '6.5', '5.19']),
            'Android': random.choice(['13', '14', '12', '11']),
            'iOS': random.choice(['16.0', '17.0', '17.1', '16.5']),
        }
        return versions.get(os_type, 'Unknown')
    
    def _detect_browser(self, user_agent: str) -> str:
        """اكتشاف المتصفح من User-Agent"""
        if 'Edg' in user_agent:
            return 'Edge'
        elif 'Firefox' in user_agent:
            return 'Firefox'
        elif 'Chrome' in user_agent and 'Safari' in user_agent:
            return 'Chrome'
        elif 'Safari' in user_agent and 'Chrome' not in user_agent:
            return 'Safari'
        elif 'Opera' in user_agent:
            return 'Opera'
        else:
            return 'Unknown'
    
    def _get_browser_version(self, user_agent: str) -> str:
        """الحصول على إصدار المتصفح"""
        import re
        pattern = r'/(\d+\.\d+\.\d+\.\d+)'
        match = re.search(pattern, user_agent)
        if match:
            return match.group(1)
        return 'Unknown'
    
    def _generate_plugins(self, os_type: str) -> List[str]:
        """توليد قائمة إضافات المتصفح"""
        base_plugins = [
            'Chrome PDF Plugin', 'Chrome PDF Viewer', 'Native Client'
        ]
        
        if os_type == 'Windows':
            base_plugins.append('Windows Media Player Plug-in')
        elif os_type == 'macOS':
            base_plugins.append('QuickTime Plug-in')
        
        extra_plugins = [
            'Widevine Content Decryption Module',
            'Google Cast',
            'WebRTC Audio/Video',
            'Flash Player',
            'Java (TM) Platform SE',
        ]
        
        return random.sample(base_plugins + extra_plugins, random.randint(3, 6))
    
    def _generate_fonts(self, os_type: str) -> List[str]:
        """توليد قائمة الخطوط"""
        common_fonts = [
            'Arial', 'Helvetica', 'Times New Roman', 'Times',
            'Courier New', 'Courier', 'Verdana', 'Georgia',
            'Palatino', 'Garamond', 'Bookman', 'Comic Sans MS',
            'Trebuchet MS', 'Arial Black', 'Impact'
        ]
        
        os_fonts = {
            'Windows': ['Calibri', 'Candara', 'Consolas', 'Constantia', 'Corbel'],
            'macOS': ['SF Pro', 'SF Mono', 'Helvetica Neue', 'Menlo', 'Monaco'],
            'Linux': ['Ubuntu', 'DejaVu Sans', 'DejaVu Serif', 'DejaVu Mono'],
        }
        
        all_fonts = common_fonts + os_fonts.get(os_type, [])
        return random.sample(all_fonts, random.randint(10, 20))
    
    def _generate_canvas_hash(self) -> str:
        """توليد Canvas Hash عشوائي"""
        return hashlib.md5(str(random.random()).encode()).hexdigest()[:16]
    
    def _generate_webgl_hash(self) -> str:
        """توليد WebGL Hash عشوائي"""
        return hashlib.md5(str(random.random()).encode()).hexdigest()[:16]
    
    def _generate_audio_hash(self) -> str:
        """توليد Audio Hash عشوائي"""
        return hashlib.md5(str(random.random()).encode()).hexdigest()[:16]
    
    def _generate_fingerprint_hash(self) -> str:
        """توليد بصمة شاملة"""
        data = f"{random.random()}{datetime.now().timestamp()}{random.randint(1, 1000000)}"
        return hashlib.sha256(data.encode()).hexdigest()[:32]
    
    def get_unique_fingerprint(self) -> Dict:
        """الحصول على بصمة فريدة مع ضمان عدم تكرارها"""
        return self.generate_fingerprint()
