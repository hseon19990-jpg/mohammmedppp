# delayed_monitor.py
import json
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class DelayedMonitor:
    """
    مراقبة نظام التحقق المتأخر (24 ساعة)
    """
    
    def __init__(self):
        self.stats = {
            'total_scheduled': 0,
            'verified': {
                'success': 0,
                'failed': 0,
                'banned': 0,
                'requires_phone': 0,
                'unknown': 0,
                'password_changed': 0,
                'totp_changed': 0
            },
            'money': {
                'saved': 0.0,
                'withdrawn': 0.0,
                'pending': 0.0
            },
            'daily_checks': 0,
            'daily_limit': 3000,
            'average_check_time': 0.0,
            'total_check_time': 0.0
        }
        self.daily_stats = defaultdict(lambda: defaultdict(int))
        self.account_history = []
        self.max_history = 500
    
    def record_scheduled(self, amount: float):
        """تسجيل جدولة تحقق جديدة"""
        self.stats['total_scheduled'] += 1
        self.stats['money']['pending'] += amount
        
        hour = datetime.now().hour
        self.daily_stats[hour]['scheduled'] += 1
        
        self._add_history({
            "timestamp": datetime.now().isoformat(),
            "type": "scheduled",
            "amount": amount
        })
    
    def record_result(self, result: Dict, amount: float, duration: float = 0.0):
        """تسجيل نتيجة التحقق"""
        self.stats['daily_checks'] += 1
        self.stats['total_check_time'] += duration
        self.stats['average_check_time'] = self.stats['total_check_time'] / max(self.stats['daily_checks'], 1)
        
        status = result.get('status', 'unknown')
        self.stats['verified'][status] += 1
        
        self.stats['money']['pending'] -= amount
        if result.get('success', False):
            self.stats['money']['saved'] += amount
        else:
            self.stats['money']['withdrawn'] += amount
        
        hour = datetime.now().hour
        self.daily_stats[hour][status] += 1
        self.daily_stats[hour]['total_checks'] += 1
        
        self._add_history({
            "timestamp": datetime.now().isoformat(),
            "type": "result",
            "status": status,
            "amount": amount,
            "duration": duration
        })
    
    def record_verification_error(self, email: str, error: str):
        """تسجيل خطأ في التحقق"""
        self.stats['verified']['failed'] += 1
        
        self._add_history({
            "timestamp": datetime.now().isoformat(),
            "type": "error",
            "email": email,
            "error": error
        })
    
    def _add_history(self, entry: Dict):
        """إضافة إدخال إلى التاريخ"""
        self.account_history.append(entry)
        if len(self.account_history) > self.max_history:
            self.account_history.pop(0)
    
    def reset_daily_stats(self):
        """إعادة تعيين الإحصائيات اليومية"""
        self.stats['daily_checks'] = 0
        self.stats['total_check_time'] = 0.0
        self.stats['average_check_time'] = 0.0
        self.daily_stats.clear()
        logger.info("Delayed monitor daily stats reset")
    
    def get_success_rate(self) -> float:
        """نسبة نجاح التحقق المتأخر"""
        total = sum(self.stats['verified'].values())
        if total == 0:
            return 0.0
        return self.stats['verified']['success'] / total * 100
    
    def get_report(self) -> str:
        """توليد تقرير التحقق المتأخر"""
        total_verified = sum(self.stats['verified'].values())
        success_rate = self.get_success_rate()
        pending_amount = self.stats['money']['pending']
        
        report = f"""
📊 *تقرير التحقق المتأخر (24 ساعة)*
═══════════════════════════════

📋 *الإحصائيات العامة:*
   📌 إجمالي المجدول: {self.stats['total_scheduled']}
   🔍 تم التحقق: {total_verified}
   📈 نسبة النجاح: {success_rate:.1f}%
   ⏱️ متوسط وقت التحقق: {self.stats['average_check_time']:.2f} ثانية

🔍 *نتائج التحقق:*
   ✅ سليم: {self.stats['verified']['success']}
   ❌ محظور: {self.stats['verified']['banned']}
   📱 يطلب رقم: {self.stats['verified']['requires_phone']}
   🔑 تغيرت كلمة المرور: {self.stats['verified']['password_changed']}
   🔐 تغير TOTP: {self.stats['verified']['totp_changed']}
   ❓ غير معروف: {self.stats['verified']['unknown']}

💰 *التأثير المالي:*
   💰 تم حفظه: ${self.stats['money']['saved']:.2f}
   💸 تم سحبه: ${self.stats['money']['withdrawn']:.2f}
   ⏳ معلق: ${pending_amount:.2f}
   📊 صافي الربح: ${self.stats['money']['saved'] - self.stats['money']['withdrawn']:.2f}

🌐 *التحقق اليومي:*
   🟢 المستخدم: {self.stats['daily_checks']}
   📊 المتبقي: {self.stats['daily_limit'] - self.stats['daily_checks']}
        """
        return report
