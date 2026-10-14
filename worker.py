#!/usr/bin/env python3
"""
TodoBot Notification Worker
===========================
Ultra-lightweight notification dispatcher designed for 1GB RAM Ubuntu VPS.
Runs via Linux Cron (e.g., `* * * * * python3 /path/to/worker.py`).
- 0 MB Idle RAM footprint (spins up, checks events, exits in < 1 second).
- Zero external pip dependencies (Standard library: sqlite3, urllib, json, re, os).
- Strictly Read-Only on Markdown memory files; tracks state in SQLite.
- Sends responsive HTML emails via Resend API.
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
            resend_id TEXT,
            error_message TEXT
        )
    """)
    conn.commit()
    conn.close()


def is_already_sent(event_id, db_path=DB_PATH):
    """Check if event_id has already been sent successfully."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM notification_history WHERE id = ? AND status = 'sent'", (event_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def record_notification(event_id, title, source_file, event_type, notify_at, status, resend_id=None, error_msg=None, db_path=DB_PATH):
    """Record or update notification status in SQLite."""
    now_str = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO notification_history
        (id, title, source_file, event_type, notify_at, sent_at, status, resend_id, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (event_id, title, source_file, event_type, notify_at, now_str, status, resend_id, error_msg))
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
        r'^(?:\s*-\s*\[([ xX])\]\s*)?(.*?)\s*<!--\s*id:\s*([a-zA-Z0-9_-]+)\s*\|\s*notify:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})(?:\s*\|\s*type:\s*([a-zA-Z0-9_-]+))?(?:\s*\|\s*priority:\s*([a-zA-Z0-9_-]+))?\s*-->',
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
                    "source_file": filename
                })
        except Exception as e:
            print(f"[-] Lỗi khi đọc file {filename}: {e}", file=sys.stderr)

    return events


def build_email_html(event, now_str):
    """Generate modern, responsive HTML email template."""
    type_badge_color = {
        "debt": "#dc2626",      # Red
        "task": "#2563eb",      # Blue
        "finance": "#059669",   # Green
        "roadmap": "#7c3aed",   # Purple
        "general": "#4b5563"    # Gray
    }.get(event["type"].lower(), "#2563eb")

    type_label = {
        "debt": "ĐẾN HẠN TRẢ NỢ / TÀI CHÍNH",
        "task": "NHẮC VIỆC CẦN LÀM",
        "finance": "THÔNG BÁO TÀI CHÍNH",
        "roadmap": "CỘT MỐC ROADMAP",
        "general": "THÔNG BÁO TỪ TRỢ LÝ"
    }.get(event["type"].lower(), event["type"].upper())

    return f"""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{event['title']}</title>
    </head>
    <body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1f2937;">
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f3f4f6; padding: 24px 12px;">
            <tr>
                <td align="center">
                    <table width="100%" max-width="600" border="0" cellspacing="0" cellpadding="0" style="max-width: 600px; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
                        <!-- Header Bar -->
                        <tr>
                            <td style="background-color: #111827; padding: 20px 24px; text-align: left;">
                                <span style="display: inline-block; padding: 4px 10px; background-color: {type_badge_color}; color: #ffffff; font-size: 11px; font-weight: 700; border-radius: 4px; letter-spacing: 0.5px; text-transform: uppercase;">
                                    {type_label}
                                </span>
                                <h1 style="color: #ffffff; font-size: 18px; margin: 12px 0 0 0; font-weight: 600;">
                                    TodoBot Executive Assistant
                                </h1>
                            </td>
                        </tr>

                        <!-- Body Content -->
                        <tr>
                            <td style="padding: 24px;">
                                <h2 style="font-size: 20px; font-weight: 700; color: #111827; margin: 0 0 16px 0; line-height: 1.4;">
                                    {event['title']}
                                </h2>

                                <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #f9fafb; border-radius: 8px; padding: 16px; margin-bottom: 20px;">
                                    <tr>
                                        <td style="font-size: 13px; color: #6b7280; padding: 4px 0;">⏰ Thời gian hẹn:</td>
                                        <td style="font-size: 14px; font-weight: 600; color: #111827; text-align: right; padding: 4px 0;">{event['notify_at']} (GMT+7)</td>
                                    </tr>
                                    <tr>
                                        <td style="font-size: 13px; color: #6b7280; padding: 4px 0;">📁 Phân hệ lưu trữ:</td>
                                        <td style="font-size: 14px; font-weight: 600; color: #111827; text-align: right; padding: 4px 0;"><code>memory/{event['source_file']}</code></td>
                                    </tr>
                                    <tr>
                                        <td style="font-size: 13px; color: #6b7280; padding: 4px 0;">🏷️ Mã sự kiện:</td>
                                        <td style="font-size: 14px; font-weight: 600; color: #4b5563; text-align: right; padding: 4px 0;">{event['id']}</td>
                                    </tr>
                                </table>

                                <p style="font-size: 14px; line-height: 1.6; color: #4b5563; margin: 0 0 20px 0;">
                                    Sự kiện trên đã đến hạn cần xử lý theo lịch đã định. Bạn có thể mở Antigravity Remote Control trên điện thoại để cập nhật tiến độ công việc hoặc báo cáo số dư tài chính.
                                </p>

                                <div style="text-align: center; margin: 28px 0 12px 0;">
                                    <span style="display: inline-block; padding: 10px 20px; background-color: #111827; color: #ffffff; text-decoration: none; font-size: 13px; font-weight: 600; border-radius: 6px;">
                                        ⚡ Tự động dispatch bởi TodoBot
                                    </span>
                                </div>
                            </td>
                        </tr>

                        <!-- Footer -->
                        <tr>
                            <td style="background-color: #f9fafb; border-top: 1px solid #e5e7eb; padding: 16px 24px; text-align: center; font-size: 12px; color: #9ca3af;">
                                Email gửi tự động lúc {now_str} (GMT+7) qua Resend API.<br>
                                Chạy trên VPS Ubuntu Server 24.04 LTS (Zero Idle RAM Architecture).
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """


def send_resend_email(api_key, from_email, to_email, subject, html_content):
    """Send email via Resend REST API using Python standard library."""
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "TodoBot-Assistant/1.0"
    }

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_content
    }

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=15) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            return True, res_json.get("id"), None
    except error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        return False, None, f"HTTP {e.code}: {err_msg}"
    except Exception as e:
        return False, None, str(e)


def run_worker(dry_run=False, send_test=False):
    """Main worker cycle."""
    config = load_env()
    init_db()

    api_key = config.get("RESEND_API_KEY", "")
    from_email = config.get("RESEND_FROM", "onboarding@resend.dev")
    to_email = config.get("NOTIFY_TO_EMAIL", "")

    now_local = datetime.now(LOCAL_TZ)
    now_str = now_local.strftime("%Y-%m-%d %H:%M")
    print(f"[{now_local.strftime('%Y-%m-%d %H:%M:%S')}] [TodoBot Worker] Bắt đầu quét sự kiện...")

    # Test Email mode
    if send_test:
        if not api_key or not to_email:
            print("[-] Lỗi: Cần cấu hình RESEND_API_KEY và NOTIFY_TO_EMAIL trong file .env trước khi test email.")
            return

        print(f"[i] Đang gửi email kiểm tra tới {to_email} từ {from_email}...")
        test_event = {
            "id": "test-connection-01",
            "title": "Kiểm tra kết nối Resend API thành công!",
            "notify_at": now_str,
            "type": "general",
            "priority": "high",
            "source_file": "tasks.md"
        }
        html = build_email_html(test_event, now_str)
        success, res_id, err = send_resend_email(api_key, from_email, to_email, "[TodoBot] ✅ Kiểm tra kết nối Email thành công", html)
        if success:
            print(f"[+] Thành công! Email ID: {res_id}")
        else:
            print(f"[-] Gửi email thất bại: {err}")
        return

    # Normal polling mode
    events = parse_memory_files()
    print(f"[i] Đã tìm thấy {len(events)} thẻ sự kiện trong thư mục memory/")

    dispatched_count = 0
    for ev in events:
        try:
            event_dt = datetime.strptime(ev["notify_at"], "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
        except ValueError:
            print(f"[-] Định dạng thời gian sai ở thẻ {ev['id']}: {ev['notify_at']}")
            continue

        # Check if event has arrived
        if event_dt <= now_local:
            if is_already_sent(ev["id"]):
                continue

            print(f"[!] Phát hiện sự kiện đến hạn: [{ev['id']}] {ev['title']} (Hẹn: {ev['notify_at']})")

            if dry_run:
                print(f"    -> [DRY-RUN] Bỏ qua gửi email thực tế cho {ev['id']}")
                dispatched_count += 1
                continue

            if not api_key or not to_email:
                print(f"    [-] Chưa cấu hình RESEND_API_KEY hoặc NOTIFY_TO_EMAIL trong .env, ghi nhận pending.")
                continue

            subject = f"[{ev['type'].upper()}] {ev['title']}"
            html = build_email_html(ev, now_str)

            success, resend_id, err_msg = send_resend_email(api_key, from_email, to_email, subject, html)
            if success:
                print(f"    [+] Đã gửi email thành công qua Resend (ID: {resend_id})")
                record_notification(ev["id"], ev["title"], ev["source_file"], ev["type"], ev["notify_at"], "sent", resend_id=resend_id)
                dispatched_count += 1
            else:
                print(f"    [-] Lỗi gửi email: {err_msg}")
                record_notification(ev["id"], ev["title"], ev["source_file"], ev["type"], ev["notify_at"], "failed", error_msg=err_msg)

    print(f"[{now_local.strftime('%Y-%m-%d %H:%M:%S')}] Hoàn tất. Đã xử lý {dispatched_count} thông báo.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TodoBot Notification Worker")
    parser.add_argument("--dry-run", action="store_true", help="Quét và kiểm tra sự kiện mà không gửi email thực")
    parser.add_argument("--test-email", action="store_true", help="Gửi email thử nghiệm để kiểm tra cấu hình Resend")
    args = parser.parse_args()

    run_worker(dry_run=args.dry_run, send_test=args.test_email)
