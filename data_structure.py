from typing import Dict, List, Optional
from datetime import datetime

USER_DATA_TEMPLATE = {
    "user_id": 0,
    
    # ===== الرصيد =====
    "balance": 0.0,
    "pending_balance": 0.0,
    "hold_balance": 0.0,
    
    # ===== الحسابات المعلقة =====
    "pending_accounts": {},
    
    # ===== الحسابات المقبولة =====
    "approved_accounts": [],
    
    # ===== الحسابات المرفوضة =====
    "rejected_accounts": [],
    
    # ===== قائمة الإيميلات المستخدمة =====
    "used_emails": [],
    
    # ===== إحصائيات =====
    "stats": {
        "total_submitted": 0,
        "verified_success": 0,
        "verified_fail": 0,
        "updates_made": 0
    },
    
    # ===== الإحالة =====
    "referral_code": "",
    "referred_by": None,
    "referral_earnings": 0.0,
    "total_referrals": 0
}

PENDING_ACCOUNT_TEMPLATE = {
    "email": "",
    "password": "",
    "totp_secret": None,
    "app_password": None,
    "amount": 0.0,
    "has_totp": False,
    "has_app_pass": False,
    "section": "email_only",
    "submitted_at": "",
    "release_at": "",
    "updated_at": None,
    "update_count": 0,
    "verification_status": "pending",
    "verification_attempts": 0,
    "user_name": "",
    "user_username": ""
}

APPROVED_ACCOUNT_TEMPLATE = {
    "email": "",
    "password": "",
    "totp_secret": None,
    "app_password": None,
    "amount": 0.0,
    "has_totp": False,
    "has_app_pass": False,
    "section": "email_only",
    "verified_at": "",
    "user_name": "",
    "user_username": "",
    "totp_code": "",
    "extracted": False,
}

REJECTED_ACCOUNT_TEMPLATE = {
    "email": "",
    "password": "",
    "totp_secret": None,
    "app_password": None,
    "amount": 0.0,
    "has_totp": False,
    "has_app_pass": False,
    "rejected_at": "",
    "reject_reason": "",
    "reject_message": "",
    "user_name": "",
    "user_username": ""
}
