#!/usr/bin/env python3
"""
TodoBot Notification Worker
===========================
Ultra-lightweight notification dispatcher designed for 1GB RAM Ubuntu VPS.
Runs via Linux Cron (e.g., `* * * * * python3 /path/to/worker.py`).
- 0 MB Idle RAM footprint (spins up, checks events, exits in < 1 second).
- Zero external pip dependencies (Standard library: sqlite3, urllib, json, re, os).
- Strictly Read-Only on Markdown memory files; tracks state in SQLite.
- Sends Telegram messages via the Bot API.
"""

import os
import sys
import re
import glob
import json
import sqlite3
import argparse
from datetime import datetime, timezone, timedelta
from urllib import request, error

# Ensure UTF-8 output across Windows and Linux
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Path definitions
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MEMORY_DIR = os.path.join(BASE_DIR, "memory")
DB_PATH = os.path.join(DATA_DIR, "bot.sqlite")
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Default Timezone: GMT+7 (Vietnam)
TZ_OFFSET_HOURS = 7
LOCAL_TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))


def load_env(env_file=ENV_PATH):
    """Load environment variables from .env without external libraries."""
    config = {}
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    config[key] = val
                    os.environ[key] = val
    return config


def init_db(db_path=DB_PATH):
    """Initialize SQLite database for tracking sent notifications."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notification_history (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_file TEXT NOT NULL,
            event_type TEXT DEFAULT 'general',
            notify_at TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            status TEXT NOT NULL,
            telegram_message_id TEXT,
            error_message TEXT
        )
    """)
    cursor.execute("PRAGMA table_info(notification_history)")
    if "telegram_message_id" not in {row[1] for row in cursor.fetchall()}:
        cursor.execute("ALTER TABLE notification_history ADD COLUMN telegram_message_id TEXT")
    cursor.execute("CREATE TABLE IF NOT EXISTS worker_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    conn.close()


def get_activated_at(now_local, db_path=DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO worker_state (key, value) VALUES ('activated_at', ?)",
                     (now_local.replace(second=0, microsecond=0).isoformat(),))
        value = conn.execute("SELECT value FROM worker_state WHERE key = 'activated_at'").fetchone()[0]
    return datetime.fromisoformat(value)


def due_occurrence(event, now_local, activated_at):
    event_dt = datetime.strptime(event["notify_at"], "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
    if event.get("repeat") == "weekly":
        days_since = (now_local.weekday() - event_dt.weekday()) % 7
        occurrence_dt = (now_local - timedelta(days=days_since)).replace(
            hour=event_dt.hour, minute=event_dt.minute, second=0, microsecond=0)
        if occurrence_dt > now_local:
            occurrence_dt -= timedelta(days=7)
        event_id = f"{event['id']}-{occurrence_dt:%Y%m%d}"
        if occurrence_dt < event_dt:
            return None
    else:
        occurrence_dt = event_dt
        event_id = event["id"]
    if occurrence_dt < activated_at or not timedelta(0) <= now_local - occurrence_dt <= timedelta(hours=24):
        return None
    return event_id, occurrence_dt


def is_already_sent(event_id, db_path=DB_PATH):
    """Check if event_id has already been sent successfully."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM notification_history WHERE id = ? AND status = 'sent'", (event_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def record_notification(event_id, title, source_file, event_type, notify_at, status, telegram_message_id=None, error_msg=None, db_path=DB_PATH):
    """Record or update notification status in SQLite."""
    now_str = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO notification_history
        (id, title, source_file, event_type, notify_at, sent_at, status, telegram_message_id, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (event_id, title, source_file, event_type, notify_at, now_str, status, telegram_message_id, error_msg))
    conn.commit()
    conn.close()


def parse_memory_files(memory_dir=MEMORY_DIR):
    """
    Scan all .md files in memory/ for notification comment tags.
    Format: <!-- id: <id> | notify: YYYY-MM-DD HH:MM | type: <type> -->
    """
    events = []
    # Matches:
    # Optional checkbox: - [ ] or - [x]
    # Task title
    # HTML comment with id, notify date/time, optional type, optional priority
    tag_pattern = re.compile(
        r'^(?:\s*-\s*\[([ xX])\]\s*)?(.*?)\s*<!--\s*id:\s*([a-zA-Z0-9_-]+)\s*\|\s*notify:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})(?:\s*\|\s*type:\s*([a-zA-Z0-9_-]+))?(?:\s*\|\s*priority:\s*([a-zA-Z0-9_-]+))?(?:\s*\|\s*repeat:\s*(weekly))?\s*-->',
        re.MULTILINE
    )

    md_files = glob.glob(os.path.join(memory_dir, "*.md"))
    for file_path in md_files:
        filename = os.path.basename(file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            for match in tag_pattern.finditer(content):
                checkbox = match.group(1) # ' ' or 'x'/'X' or None
                raw_title = match.group(2).strip()
                event_id = match.group(3).strip()
                notify_str = match.group(4).strip()
                event_type = match.group(5).strip() if match.group(5) else "task"
                priority = match.group(6).strip() if match.group(6) else "normal"

                # If task is already marked completed [x], skip notification
                if checkbox and checkbox.lower() == 'x':
                    continue

                events.append({
                    "id": event_id,
                    "title": raw_title or f"Nhắc nhở: {event_id}",
                    "notify_at": notify_str,
                    "type": event_type,
                    "priority": priority,
                    "repeat": match.group(7),
                    "source_file": filename
                })
        except Exception as e:
            print(f"[-] Lỗi khi đọc file {filename}: {e}", file=sys.stderr)

    return events


def build_telegram_message(event):
    """Format a reminder as a plain-text Telegram message."""
    return (f"[{event['type'].upper()}] {event['title']}\n"
            f"Thời gian: {event['notify_at']} (GMT+7)\n"
            f"Mã: {event['id']}")


def send_telegram_message(bot_token, chat_id, text):
    """Send a message through the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with request.urlopen(req, timeout=15) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            if not res_json.get("ok"):
                return False, None, res_json.get("description", "Telegram API error")
            return True, str(res_json["result"]["message_id"]), None
    except error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        return False, None, f"HTTP {e.code}: {err_msg}"
    except Exception as e:
        return False, None, str(e)


def run_worker(dry_run=False, send_test=False):
    """Main worker cycle."""
    config = load_env()
    init_db()

    bot_token = config.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = config.get("TELEGRAM_CHAT_ID", "")

    now_local = datetime.now(LOCAL_TZ)
    now_str = now_local.strftime("%Y-%m-%d %H:%M")
    print(f"[{now_local.strftime('%Y-%m-%d %H:%M:%S')}] [TodoBot Worker] Bắt đầu quét sự kiện...")

    if send_test:
        if not bot_token or not chat_id:
            print("[-] Lỗi: Cần cấu hình TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID trong file .env.")
            return

        print(f"[i] Đang gửi tin nhắn kiểm tra tới chat {chat_id}...")
        test_event = {
            "id": "test-connection-01",
            "title": "Kiểm tra kết nối Telegram",
            "notify_at": now_str,
            "type": "general",
            "priority": "high",
            "source_file": "tasks.md"
        }
        success, message_id, err = send_telegram_message(bot_token, chat_id, build_telegram_message(test_event))
        if success:
            print(f"[+] Thành công! Telegram message ID: {message_id}")
        else:
            print(f"[-] Gửi tin nhắn thất bại: {err}")
        return

    activated_at = (now_local.replace(second=0, microsecond=0)
                    if dry_run else get_activated_at(now_local))
    # Normal polling mode
    events = parse_memory_files()
    print(f"[i] Đã tìm thấy {len(events)} thẻ sự kiện trong thư mục memory/")

    dispatched_count = 0
    for ev in events:
        try:
            occurrence = due_occurrence(ev, now_local, activated_at)
        except ValueError:
            print(f"[-] Định dạng thời gian sai ở thẻ {ev['id']}: {ev['notify_at']}")
            continue

        if occurrence:
            event_id, occurrence_dt = occurrence
            if is_already_sent(event_id):
                continue

            print(f"[!] Phát hiện sự kiện đến hạn: [{event_id}] {ev['title']} (Hẹn: {occurrence_dt:%Y-%m-%d %H:%M})")
            reminder = {**ev, "id": event_id, "notify_at": occurrence_dt.strftime("%Y-%m-%d %H:%M")}

            if dry_run:
                print(f"    -> [DRY-RUN] Bỏ qua gửi Telegram thực tế cho {ev['id']}")
                dispatched_count += 1
                continue

            if not bot_token or not chat_id:
                print("    [-] Chưa cấu hình TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID trong .env.")
                continue

            success, message_id, err_msg = send_telegram_message(bot_token, chat_id, build_telegram_message(reminder))
            if success:
                print(f"    [+] Đã gửi Telegram thành công (ID: {message_id})")
                record_notification(event_id, ev["title"], ev["source_file"], ev["type"], reminder["notify_at"], "sent", telegram_message_id=message_id)
                dispatched_count += 1
            else:
                print(f"    [-] Lỗi gửi Telegram: {err_msg}")
                record_notification(event_id, ev["title"], ev["source_file"], ev["type"], reminder["notify_at"], "failed", error_msg=err_msg)

    print(f"[{now_local.strftime('%Y-%m-%d %H:%M:%S')}] Hoàn tất. Đã xử lý {dispatched_count} thông báo.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TodoBot Notification Worker")
    parser.add_argument("--dry-run", action="store_true", help="Quét sự kiện mà không gửi Telegram")
    parser.add_argument("--test-telegram", action="store_true", help="Gửi tin nhắn thử để kiểm tra cấu hình Telegram")
    args = parser.parse_args()

    run_worker(dry_run=args.dry_run, send_test=args.test_telegram)
