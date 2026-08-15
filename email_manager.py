import re
import hashlib
from typing import Dict, Optional, Tuple
from datetime import datetime, timezone
import logging
from config import BotConfig
from data_structure import PENDING_ACCOUNT_TEMPLATE
from utils import get_user, save_user

logger = logging.getLogger(__name__)

class EmailManager:
    """إدارة الإيميلات: منع التكرار، التعديل، التتبع"""
    
    def __init__(self):
        self.email_cache = {}
        self.max_update_count = BotConfig.MAX_UPDATE_COUNT
        
    def normalize_email(self, email: str) -> str:
        """توحيد صيغة الإيميل"""
        return email.strip().lower()
    
    def is_email_used(self, user_id: int, email: str) -> Tuple[bool, Optional[Dict]]:
        """التحقق من استخدام الإيميل مسبقاً"""
        user_data = get_user(user_id)
        normalized_email = self.normalize_email(email)
        
        pending = user_data.get("pending_accounts", {})
        if normalized_email in pending:
            return True, pending[normalized_email]
        
        for acc in user_data.get("approved_accounts", []):
            if self.normalize_email(acc.get("email", "")) == normalized_email:
                return True, acc
        
        for acc in user_data.get("rejected_accounts", []):
            if self.normalize_email(acc.get("email", "")) == normalized_email:
                return True, acc
        
        return False, None
    
    def can_update_account(self, user_id: int, email: str) -> Tuple[bool, str]:
        """التحقق من إمكانية تعديل الحساب"""
        is_used, account = self.is_email_used(user_id, email)
        
        if not is_used:
            return True, "يمكن إضافة الإيميل"
        
        if account and account.get("verification_status") == "pending":
            update_count = account.get("update_count", 0)
            if update_count >= self.max_update_count:
                return False, f"تم تعديل هذا الإيميل {self.max_update_count} مرات، لا يمكن التعديل مرة أخرى"
            return True, "يمكن تعديل البيانات"
        
        return False, "تم الانتهاء من معالجة هذا الإيميل"
    
    def update_account_data(self, user_id: int, email: str, new_data: Dict) -> bool:
        """تحديث بيانات الحساب"""
        user_data = get_user(user_id)
        normalized_email = self.normalize_email(email)
        
        pending = user_data.get("pending_accounts", {})
        
        if normalized_email in pending:
            account = pending[normalized_email]
            
            allowed_fields = ["password", "totp_secret", "app_password"]
            for field in allowed_fields:
                if field in new_data:
                    account[field] = new_data[field]
            
            account["updated_at"] = datetime.now(timezone.utc).isoformat()
            account["update_count"] = account.get("update_count", 0) + 1
            account["has_totp"] = bool(account.get("totp_secret"))
            account["has_app_pass"] = bool(account.get("app_password"))
            account["amount"] = self.calculate_price(account)
            account["section"] = self.determine_section(account)
            
            user_data["pending_accounts"][normalized_email] = account
            save_user(user_id, user_data)
            
            logger.info(f"Updated account {email} for user {user_id}")
            return True
        
        return False
    
    def calculate_price(self, account: Dict) -> float:
        has_totp = bool(account.get("totp_secret"))
        has_app_pass = bool(account.get("app_password"))
        
        if has_totp and has_app_pass:
            return BotConfig.PRICES["full"]
        elif has_totp:
            return BotConfig.PRICES["totp_only"]
        else:
            return BotConfig.PRICES["email_only"]
    
    def determine_section(self, account: Dict) -> str:
        has_totp = bool(account.get("totp_secret"))
        has_app_pass = bool(account.get("app_password"))
        
        if has_totp and has_app_pass:
            return "full"
        elif has_totp:
            return "totp_only"
        else:
            return "email_only"
    
    def add_pending_account(self, user_id: int, account_data: Dict) -> bool:
        user_data = get_user(user_id)
        normalized_email = self.normalize_email(account_data["email"])
        
        if normalized_email in user_data.get("pending_accounts", {}):
            return False
        
        pending = user_data.get("pending_accounts", {})
        pending[normalized_email] = {**PENDING_ACCOUNT_TEMPLATE, **account_data}
        user_data["pending_accounts"] = pending
        
        if "used_emails" not in user_data:
            user_data["used_emails"] = []
        user_data["used_emails"].append(normalized_email)
        
        user_data["pending_balance"] = float(user_data.get("pending_balance", 0.0)) + account_data["amount"]
        
        save_user(user_id, user_data)
        return True
    
    def get_pending_account(self, user_id: int, email: str) -> Optional[Dict]:
        user_data = get_user(user_id)
        normalized_email = self.normalize_email(email)
        return user_data.get("pending_accounts", {}).get(normalized_email)
