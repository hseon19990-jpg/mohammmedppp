# advanced_monitor.py
import json
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AdvancedMonitor:
    """
    نظام مراقبة متقدم لتتبع:
    1. استهلاك البروكسي
    2. معدلات النجاح
    3. أداء الأقسام
    4. توفير التكلفة
    """
    
    def __init__(self):
        self.stats = {
            "total_emails": 0,
            "by_section": defaultdict(int),
            "verification": {
                "attempted": 0,
                "successful": 0,
                "failed": 0,
                "by_type": defaultdict(int),
                "by_reason": defaultdict(int),
                "average_time": 0.0,
                "total_time": 0.0
            },
            "proxy_usage": {
                "total": 0,
                "daily_limit": 3000,
                "by_region": defaultdict(int),
                "saved": 0,
                "failed_proxies": 0
            },
            "cost_savings": {
                "total_saved": 0.0,
                "estimated_cost_without_optimization": 0.0,
                "actual_cost": 0.0,
                "proxy_cost_per_use": 0.015
            },
            "errors": {
                "total": 0,
                "by_type": defaultdict(int),
                "last_error": None,
                "last_error_time": None
            },
            "users": {
                "total": 0,
                "active": 0,
                "banned": 0
            },
            "start_time": datetime.now().isoformat(),
            "last_reset": datetime.now().isoformat()
        }
        self.hourly_stats = defaultdict(lambda: defaultdict(int))
        self.daily_stats = defaultdict(lambda: defaultdict(int))
        self.history = []
        self.max_history = 1000
        
    def record_submission(self, section: str, used_proxy: bool = False):
        """تسجيل إرسال إيميل جديد"""
        self.stats["total_emails"] += 1
        self.stats["by_section"][section] += 1
        
        if not used_proxy:
            self.stats["proxy_usage"]["saved"] += 1
        
        hour = datetime.now().hour
        self.hourly_stats[hour]["total"] += 1
        self.hourly_stats[hour][section] += 1
        
        day = datetime.now().date().isoformat()
        self.daily_stats[day]["total"] += 1
        self.daily_stats[day][section] += 1
    
    def record_verification(
        self, 
        success: bool, 
        verification_type: str, 
        duration: float = 0.0,
        reason: str = None
    ):
        """تسجيل نتيجة التحقق"""
        self.stats["verification"]["attempted"] += 1
        self.stats["verification"]["by_type"][verification_type] += 1
        self.stats["verification"]["total_time"] += duration
        
        avg_time = self.stats["verification"]["total_time"] / max(self.stats["verification"]["attempted"], 1)
        self.stats["verification"]["average_time"] = avg_time
        
        if success:
            self.stats["verification"]["successful"] += 1
        else:
            self.stats["verification"]["failed"] += 1
            if reason:
                self.stats["verification"]["by_reason"][reason] += 1
        
        self.stats["proxy_usage"]["total"] += 1
        
        self._update_cost_estimates()
        
        hour = datetime.now().hour
        self.hourly_stats[hour]["verification"] += 1
        self.hourly_stats[hour][f"verification_{'success' if success else 'failed'}"] += 1
        
        self._add_history({
            "timestamp": datetime.now().isoformat(),
            "type": "verification",
            "success": success,
            "verification_type": verification_type,
            "duration": duration,
            "reason": reason
        })
    
    def record_proxy_usage(self, proxy: str, success: bool, region: str = None):
        """تسجيل استخدام بروكسي"""
        self.stats["proxy_usage"]["total"] += 1
        if region:
            self.stats["proxy_usage"]["by_region"][region] += 1
        
        if not success:
            self.stats["proxy_usage"]["failed_proxies"] += 1
        
        self._add_history({
            "timestamp": datetime.now().isoformat(),
            "type": "proxy",
            "proxy": proxy,
            "success": success,
            "region": region
        })
    
    def record_error(self, error_type: str, error_message: str):
        """تسجيل خطأ"""
        self.stats["errors"]["total"] += 1
        self.stats["errors"]["by_type"][error_type] += 1
        self.stats["errors"]["last_error"] = error_message
        self.stats["errors"]["last_error_time"] = datetime.now().isoformat()
        
        self._add_history({
            "timestamp": datetime.now().isoformat(),
            "type": "error",
            "error_type": error_type,
            "error_message": error_message
        })
    
    def record_user_stats(self, total: int, active: int, banned: int):
        """تسجيل إحصائيات المستخدمين"""
        self.stats["users"]["total"] = total
        self.stats["users"]["active"] = active
        self.stats["users"]["banned"] = banned
    
    def _update_cost_estimates(self):
        """تحديث تقديرات التكلفة"""
        proxy_cost = self.stats["cost_savings"]["proxy_cost_per_use"]
        total_uses = self.stats["proxy_usage"]["total"]
        saved_uses = self.stats["proxy_usage"]["saved"]
        
        self.stats["cost_savings"]["estimated_cost_without_optimization"] = (total_uses + saved_uses) * proxy_cost
        self.stats["cost_savings"]["actual_cost"] = total_uses * proxy_cost
        self.stats["cost_savings"]["total_saved"] = saved_uses * proxy_cost
    
    def _add_history(self, entry: Dict):
        """إضافة إدخال إلى التاريخ"""
        self.history.append(entry)
        if len(self.history) > self.max_history:
            self.history.pop(0)
    
    def reset_daily_stats(self):
        """إعادة تعيين الإحصائيات اليومية"""
        self.stats["total_emails"] = 0
        self.stats["by_section"] = defaultdict(int)
        self.stats["verification"] = {
            "attempted": 0,
            "successful": 0,
            "failed": 0,
            "by_type": defaultdict(int),
            "by_reason": defaultdict(int),
            "average_time": 0.0,
            "total_time": 0.0
        }
        self.stats["proxy_usage"]["total"] = 0
        self.stats["proxy_usage"]["saved"] = 0
        self.stats["proxy_usage"]["failed_proxies"] = 0
        self.stats["errors"]["total"] = 0
        self.stats["errors"]["by_type"] = defaultdict(int)
        self.stats["last_reset"] = datetime.now().isoformat()
        self.hourly_stats.clear()
        self.daily_stats.clear()
        
        logger.info("Daily statistics reset")
    
    def get_success_rate(self) -> float:
        """نسبة النجاح"""
        attempted = self.stats["verification"]["attempted"]
        if attempted == 0:
            return 0.0
        return self.stats["verification"]["successful"] / attempted * 100
    
    def get_detailed_report(self) -> str:
        """توليد تقرير مفصل"""
        now = datetime.now()
        start = datetime.fromisoformat(self.stats["start_time"])
        elapsed = now - start
        
        success_rate = self.get_success_rate()
        proxy_saved_percent = (self.stats["proxy_usage"]["saved"] / max(self.stats["total_emails"], 1)) * 100
        
        report = f"""
📊 *تقرير الأداء المفصل*
═══════════════════════════════
🕐 الوقت: {now.strftime('%Y-%m-%d %H:%M:%S')}
⏱️ وقت التشغيل: {elapsed.days} يوم, {elapsed.seconds // 3600} ساعة

📧 *إجمالي الإيميلات:* {self.stats['total_emails']}

📂 *توزيع حسب الأقسام:*
   📦 إيميل + باسورد فقط: {self.stats['by_section'].get('email_only', 0)}
   🔐 إيميل + باسورد + TOTP: {self.stats['by_section'].get('totp_accounts', 0)}
   🔑 إيميل + باسورد + TOTP + App Pass: {self.stats['by_section'].get('full_accounts', 0)}

🔍 *التحقق التلقائي:*
   ✅ ناجح: {self.stats['verification']['successful']}
   ❌ فاشل: {self.stats['verification']['failed']}
   📊 نسبة النجاح: {success_rate:.1f}%
   ⏱️ متوسط الوقت: {self.stats['verification']['average_time']:.2f} ثانية

📊 *أسباب الفشل:*
   {self._format_failure_reasons()}

🌐 *استهلاك البروكسي:*
   🟢 المستخدم: {self.stats['proxy_usage']['total']}
   🟡 المتبقي: {self.stats['proxy_usage']['daily_limit'] - self.stats['proxy_usage']['total']}
   💰 تم توفيره: {self.stats['proxy_usage']['saved']} عملية ({proxy_saved_percent:.1f}%)

💰 *توفير التكلفة:*
   💵 بدون تحسين: ${self.stats['cost_savings']['estimated_cost_without_optimization']:.2f}
   💵 مع التحسين: ${self.stats['cost_savings']['actual_cost']:.2f}
   🎉 التوفير: ${self.stats['cost_savings']['total_saved']:.2f}

👥 *المستخدمين:*
   👤 الإجمالي: {self.stats['users']['total']}
   🟢 نشط: {self.stats['users']['active']}
   🔴 محظور: {self.stats['users']['banned']}

📈 *التوزيع حسب الساعة (اليوم):*
{self._format_hourly_stats()}
        """
        return report
    
    def _format_failure_reasons(self) -> str:
        """تنسيق أسباب الفشل"""
        reasons = self.stats["verification"]["by_reason"]
        if not reasons:
            return "   لا توجد أسباب فشل مسجلة"
        
        total_failed = self.stats["verification"]["failed"]
        lines = []
        for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:5]:
            percent = (count / max(total_failed, 1)) * 100
            lines.append(f"   • {reason}: {count} ({percent:.1f}%)")
        
        return "\n".join(lines)
    
    def _format_hourly_stats(self) -> str:
        """تنسيق الإحصائيات الساعية"""
        lines = []
        for hour in sorted(self.hourly_stats.keys())[-12:]:  # آخر 12 ساعة
            data = self.hourly_stats[hour]
            total = data.get('total', 0)
            bar = "█" * min(total // 5, 50)
            lines.append(f"  {hour:02d}:00  {bar} {total}")
        return "\n".join(lines) if lines else "  لا توجد بيانات بعد"
