import json
import os
import shutil
import html
from pathlib import Path
from typing import Dict, Any
import logging
from config import BotConfig

logger = logging.getLogger(__name__)

# ====== إدارة البيانات ======

def load_json(path: Path) -> dict:
    backup_path = path.with_name(f"{path.name}.bak")
    
    for candidate in (path, backup_path):
        if not candidate.exists():
            continue
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, json.JSONDecodeError):
            logger.exception(f"Could not read JSON data from {candidate}")
    
    return {}

def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    backup_path = path.with_name(f"{path.name}.bak")
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    
    with temporary_path.open("w", encoding="utf-8") as temporary_file:
        temporary_file.write(payload)
        temporary_file.flush()
        os.fsync(temporary_file.fileno())
    
    if path.exists():
        shutil.copy2(path, backup_path)
    os.replace(temporary_path, path)

def get_user(user_id: int) -> dict:
    from data_structure import USER_DATA_TEMPLATE
    users = load_json(BotConfig.USERS_DB)
    return users.get(str(user_id), USER_DATA_TEMPLATE.copy())

def save_user(user_id: int, user_data: dict):
    users = load_json(BotConfig.USERS_DB)
    users[str(user_id)] = user_data
    save_json(BotConfig.USERS_DB, users)

# ====== دوال مساعدة ======

def format_app_password(password: str) -> str:
    cleaned = password.replace(" ", "").upper()
    if len(cleaned) != 16:
        return password
    return f"{cleaned[0:4]} {cleaned[4:8]} {cleaned[8:12]} {cleaned[12:16]}"

def format_totp_secret(secret: str) -> str:
    cleaned = secret.replace(" ", "").upper()
    if len(cleaned) != 32:
        return secret
    return " ".join([cleaned[i:i+4] for i in range(0, 32, 4)])

def escape_html(text: str) -> str:
    return html.escape(str(text))

def calculate_price(has_totp: bool, has_app_pass: bool) -> float:
    from config import BotConfig
    
    if has_totp and has_app_pass:
        return BotConfig.PRICES["full"]
    elif has_totp:
        return BotConfig.PRICES["totp_only"]
    else:
        return BotConfig.PRICES["email_only"]
