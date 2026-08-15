# account_manager.py
import time
import hashlib
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
from collections import defaultdict

from utils import get_user, save_user
from config import BotConfig

logger = logging.getLogger(__name__)


class AccountManager:
    """
    إدارة الحسابات حسب الأقسام:
    1. email_only - إيميل + باسورد فقط
    2. totp_only - إيميل + باسورد + TOTP
    3. full - إيميل + باسورد + TOTP + App Pass
    4. ready_totp_only - TOTP فقط للاستخراج
    5. ready_totp_app - TOTP + App Pass للاستخراج
    """
    
    def __init__(self):
        self.sections = {
            "email_only": [],
            "totp_accounts": [],
            "full_accounts": [],
            "ready_totp_only": [],
            "ready_totp_app": [],
        }
        self.account_cache = {}
        self.section_stats = defaultdict(int)
    
    def get_section_for_account(self, has_totp: bool, has_app_pass: bool) -> str:
        """تحديد القسم المناسب للحساب"""
        if has_totp and has_app_pass:
            return "full_accounts"
        elif has_totp:
            return "totp_accounts"
        else:
            return "email_only"
    
    def add_account(
        self, 
        user_id: int, 
        email: str, 
        password: str, 
        totp_secret: str = None, 
        app_password: str = None,
        user_name: str = "",
        user_username: str = ""
    ) -> Tuple[bool, str]:
        """
        إضافة حساب جديد إلى القسم المناسب
        يعيد: (نجاح, رسالة)
        """
        from email_manager import EmailManager
        email_manager = EmailManager()
        
        can_add, message = email_manager.can_update_account(user_id, email)
        if not can_add:
            return False, message
        
        has_totp = bool(totp_secret)
        has_app_pass = bool(app_password)
        section = self.get_section_for_account(has_totp, has_app_pass)
        
        amount = self.calculate_price(has_totp, has_app_pass)
        
        account_data = {
            "email": email,
            "password": password,
            "totp_secret": totp_secret,
            "app_password": app_password,
            "amount": amount,
            "has_totp": has_totp,
            "has_app_pass": has_app_pass,
            "section": section,
            "user_name": user_name,
            "user_username": user_username,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "verification_status": "pending",
            "update_count": 0,
        }
        
        self.sections[section].append(account_data)
        self.section_stats[section] += 1
        
        success = email_manager.add_pending_account(user_id, account_data)
        if success:
            logger.info(f"Account {email} added to {section}")
            return True, "تم إضافة الحساب بنجاح"
        else:
            return False, "فشل في إضافة الحساب"
    
    def calculate_price(self, has_totp: bool, has_app_pass: bool) -> float:
        """حساب السعر حسب البيانات"""
        if has_totp and has_app_pass:
            return BotConfig.PRICES["full"]
        elif has_totp:
            return BotConfig.PRICES["totp_only"]
        else:
            return BotConfig.PRICES["email_only"]
    
    def move_to_ready(self, account: Dict, verification_result: Dict) -> str:
        """
        نقل الحساب إلى قسم الاستخراج بعد التحقق
        يعيد: القسم الذي تم النقل إليه
        """
        has_totp = account.get("has_totp", False)
        has_app_pass = account.get("has_app_pass", False)
        
        account["verified_at"] = datetime.now(timezone.utc).isoformat()
        account["verification_result"] = verification_result
        
        if has_totp and has_app_pass:
            self.sections["ready_totp_app"].append(account)
            self.section_stats["ready_totp_app"] += 1
            return "ready_totp_app"
        elif has_totp:
            self.sections["ready_totp_only"].append(account)
            self.section_stats["ready_totp_only"] += 1
            return "ready_totp_only"
        else:
            return "email_only"
    
    def get_ready_accounts(self, section: str, limit: int = 50) -> List[Dict]:
        """الحصول على الحسابات الجاهزة للاستخراج"""
        if section == "totp_only":
            return self.sections["ready_totp_only"][:limit]
        elif section == "totp_app":
            return self.sections["ready_totp_app"][:limit]
        elif section == "all":
            return (self.sections["ready_totp_only"] + self.sections["ready_totp_app"])[:limit]
        else:
            return []
    
    def mark_extracted(self, account: Dict) -> bool:
        """تحديد حساب كمستخرج"""
        for idx, acc in enumerate(self.sections["ready_totp_only"]):
            if acc.get("email") == account.get("email"):
                self.sections["ready_totp_only"][idx]["extracted"] = True
                return True
        
        for idx, acc in enumerate(self.sections["ready_totp_app"]):
            if acc.get("email") == account.get("email"):
                self.sections["ready_totp_app"][idx]["extracted"] = True
                return True
        
        return False
    
    def get_stats(self) -> Dict:
        """إحصائيات الأقسام"""
        return {
            "📦 إيميل + باسورد فقط": len(self.sections["email_only"]),
            "🔐 إيميل + باسورد + TOTP": len(self.sections["totp_accounts"]),
            "🔑 إيميل + باسورد + TOTP + App Pass": len(self.sections["full_accounts"]),
            "📂 TOTP فقط (جاهز للاستخراج)": len(self.sections["ready_totp_only"]),
            "📂 TOTP + App Pass (جاهز للاستخراج)": len(self.sections["ready_totp_app"]),
            "📊 الإجمالي": sum(len(section) for section in self.sections.values())
        }
    
    def get_account_by_email(self, email: str) -> Optional[Dict]:
        """البحث عن حساب بواسطة الإيميل"""
        for section_name, section in self.sections.items():
            for account in section:
                if account.get("email") == email:
                    return account
        return None
    
    def remove_account(self, email: str, section: Optional[str] = None) -> bool:
        """حذف حساب من قسم معين"""
        if section:
            sections_to_search = [section]
        else:
            sections_to_search = list(self.sections.keys())
        
        for section_name in sections_to_search:
            for idx, account in enumerate(self.sections[section_name]):
                if account.get("email") == email:
                    self.sections[section_name].pop(idx)
                    self.section_stats[section_name] -= 1
                    logger.info(f"Account {email} removed from {section_name}")
                    return True
        
        return False
    
    def clear_cache(self):
        """مسح الكاش"""
        self.account_cache.clear()
        logger.info("Account cache cleared")
