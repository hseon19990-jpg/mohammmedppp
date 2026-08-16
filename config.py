import os
from pathlib import Path

class BotConfig:
    """إعدادات البوت"""
    
    # ========== إعدادات البوت الأساسية ==========
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
    OWNER_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "0"))
    
    # ========== إعدادات التحقق ==========
    VERIFICATION_DELAY_HOURS = 24  # 24 ساعة
    VERIFICATION_DELAY_MIN = 180   # 3 دقائق
    VERIFICATION_DELAY_MAX = 600   # 10 دقائق
    
    # ========== إعدادات البروكسي ==========
    PROXY_DAILY_LIMIT = 3000
    PROXY_REFRESH_INTERVAL = 3600
    
    # ========== إعدادات الأمان ==========
    MAX_FAILED_ATTEMPTS = 3
    MAX_UPDATE_COUNT = 3
    SESSION_TIMEOUT = 3600
    
    # ========== إعدادات الأسعار ==========
    PRICES = {
        "email_only": 0.10,
        "totp_only": 0.15,
        "full": 0.20
    }
    
    # ========== إعدادات الأقسام ==========
    SECTIONS = {
        "email_only": "📦 إيميل + باسورد فقط",
        "totp_only": "🔐 إيميل + باسورد + TOTP",
        "full": "🔑 إيميل + باسورد + TOTP + App Pass"
    }
    
    # ========== إعدادات البيانات ==========
    configured_data_dir = os.environ.get("DATA_DIR", "").strip()
    railway_volume_dir = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    default_data_dir = Path("/app/data")
    if not os.access(default_data_dir.parent, os.W_OK):
        default_data_dir = Path(__file__).resolve().parent / "data"
    DATA_DIR = Path(
        configured_data_dir or railway_volume_dir or default_data_dir
    ).resolve()
    USERS_DB = DATA_DIR / "users.json"
    VIDEOS_DIR = DATA_DIR / "videos"
    
    # ========== إعدادات القنوات ==========
    PURCHASE_CHANNEL_1 = os.environ.get("PURCHASE_CHANNEL_1", "").strip()
    PURCHASE_CHANNEL_2 = os.environ.get("PURCHASE_CHANNEL_2", "").strip()
