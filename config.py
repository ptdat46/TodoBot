"""Configuration helpers for the private TodoBot web application."""
import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {
    "APP_HOST": "127.0.0.1",
    "APP_PORT": "8080",
    "APP_ENV": "production",
    "DB_PATH": os.path.join(BASE_DIR, "data", "bot.sqlite"),
    "MEMORY_DIR": os.path.join(BASE_DIR, "memory"),
    "WEB_DIR": os.path.join(BASE_DIR, "web"),
    "UPLOAD_DIR": os.path.join(BASE_DIR, "uploads"),
    "AI_MODEL": "gpt-6-luna",
    "MAX_UPLOAD_BYTES": str(5 * 1024 * 1024),
    "MAX_BODY_BYTES": str(6 * 1024 * 1024),
    "RATE_LIMIT": "60",
}


def load_env(path=None):
    values = dict(DEFAULTS)
    path = path or os.path.join(BASE_DIR, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip("'\"")
    values.update({key: value for key, value in os.environ.items() if key in set(DEFAULTS) | {
        "APP_PASSWORD_HASH", "SESSION_SECRET", "AI_BASE_URL", "AI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_KEY"
    }})
    return values


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    import hashlib
    return salt.hex() + "$" + hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
