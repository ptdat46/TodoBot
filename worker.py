#!/usr/bin/env python3
"""Notification worker that scans memory files and fills the local inbox."""

import argparse
import glob
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import notifications

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MEMORY_DIR = os.path.join(BASE_DIR, "memory")
DB_PATH = os.path.join(DATA_DIR, "bot.sqlite")
ENV_PATH = os.path.join(BASE_DIR, ".env")
TZ_OFFSET_HOURS = 7
LOCAL_TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))


def load_env(env_file=ENV_PATH):
    """Load simple KEY=VALUE settings without external dependencies."""
    config = {}
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip().strip("'\"")
    return config


def init_db(db_path=DB_PATH):
    """Initialize inbox/history storage and worker state."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    notifications.init_db(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS worker_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.commit()
    finally:
        connection.close()


def get_activated_at(now_local, db_path=DB_PATH):
    init_db(db_path)
    value = now_local.replace(second=0, microsecond=0).isoformat()
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT OR IGNORE INTO worker_state (key, value) VALUES ('activated_at', ?)",
            (value,),
        )
        connection.commit()
        return datetime.fromisoformat(
            connection.execute("SELECT value FROM worker_state WHERE key = 'activated_at'").fetchone()[0]
        )
    finally:
        connection.close()


def due_occurrence(event, now_local, activated_at):
    """Return the due occurrence id/time, retaining the existing catch-up rules."""
    event_dt = datetime.strptime(event["notify_at"], "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL_TZ)
    if event.get("repeat") == "weekly":
        days_since = (now_local.weekday() - event_dt.weekday()) % 7
        occurrence_dt = (now_local - timedelta(days=days_since)).replace(
            hour=event_dt.hour, minute=event_dt.minute, second=0, microsecond=0
        )
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
    """Compatibility wrapper for the channel-neutral history store."""
    return notifications.is_already_sent(event_id, db_path)


def record_notification(
    event_id, title, source_file, event_type, notify_at, status,
    delivery_id=None, error_msg=None, db_path=DB_PATH, **legacy_kwargs,
):
    """Record a channel-neutral delivery result."""
    delivery_id = legacy_kwargs.get("message_id", delivery_id)
    notifications.record_notification(
        event_id, title, source_file, event_type, notify_at, status,
        delivery_id=delivery_id, error_message=error_msg, db_path=db_path,
    )


def parse_memory_files(memory_dir=MEMORY_DIR):
    """Scan Markdown memory files for notification tags."""
    events = []
    tag_pattern = re.compile(
        r'^(?:\s*-\s*\[([ xX])\]\s*)?(.*?)\s*<!--\s*id:\s*([a-zA-Z0-9_-]+)\s*\|\s*notify:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})(?:\s*\|\s*type:\s*([a-zA-Z0-9_-]+))?(?:\s*\|\s*priority:\s*([a-zA-Z0-9_-]+))?(?:\s*\|\s*repeat:\s*(weekly))?\s*-->',
        re.MULTILINE,
    )
    for file_path in glob.glob(os.path.join(memory_dir, "*.md")):
        filename = os.path.basename(file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as stream:
                content = stream.read()
            for match in tag_pattern.finditer(content):
                checkbox = match.group(1)
                if checkbox and checkbox.lower() == "x":
                    continue
                events.append({
                    "id": match.group(3).strip(),
                    "title": match.group(2).strip() or f"Reminder: {match.group(3).strip()}",
                    "notify_at": match.group(4).strip(),
                    "type": match.group(5).strip() if match.group(5) else "task",
                    "priority": match.group(6).strip() if match.group(6) else "normal",
                    "repeat": match.group(7),
                    "source_file": filename,
                })
        except (OSError, UnicodeError) as exc:
            print(f"[-] Could not read {filename}: {exc}", file=sys.stderr)
    return events


def _push_settings(config):
    claims = {}
    subject = config.get("PUSH_VAPID_SUBJECT")
    if subject:
        claims["sub"] = subject
    return config.get("PUSH_VAPID_PRIVATE_KEY"), claims or None


def run_worker(dry_run=False):
    """Scan due events and persist them; dry-run never initializes activation state."""
    config = load_env()
    init_db(DB_PATH)
    now_local = datetime.now(LOCAL_TZ)
    print(f"[{now_local:%Y-%m-%d %H:%M:%S}] [TodoBot Worker] Scanning events...")
    activated_at = now_local.replace(second=0, microsecond=0) if dry_run else get_activated_at(now_local)
    events = parse_memory_files(MEMORY_DIR)
    print(f"[i] Found {len(events)} event tags in memory/")
    processed = 0
    vapid_key, vapid_claims = _push_settings(config)
    for event in events:
        try:
            occurrence = due_occurrence(event, now_local, activated_at)
        except ValueError:
            print(f"[-] Invalid time for {event['id']}: {event['notify_at']}")
            continue
        if not occurrence:
            continue
        event_id, occurrence_dt = occurrence
        if is_already_sent(event_id, DB_PATH):
            continue
        reminder: Dict[str, Any] = {
            **event,
            "id": event_id,
            "notify_at": occurrence_dt.strftime("%Y-%m-%d %H:%M"),
            "body": notifications.format_notification({**event, "id": event_id, "notify_at": occurrence_dt.strftime("%Y-%m-%d %H:%M")}),
        }
        print(f"[!] Due event: [{event_id}] {event['title']} ({reminder['notify_at']})")
        if dry_run:
            print(f"    -> [DRY-RUN] Inbox unchanged for {event_id}")
            processed += 1
            continue
        if notifications.enqueue_notification(reminder, DB_PATH):
            inbox_item = next(item for item in notifications.list_inbox(DB_PATH) if item["id"] == event_id)
            result = notifications.deliver_notification(inbox_item, DB_PATH, vapid_key, vapid_claims)
            processed += 1
            print(f"    -> Queued; delivered={result['delivered']} failed={result['failed']}")
    print(f"[{now_local:%Y-%m-%d %H:%M:%S}] Complete. Processed {processed} notifications.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TodoBot notification worker")
    parser.add_argument("--dry-run", action="store_true", help="Scan due events without changing the inbox")
    args = parser.parse_args()
    run_worker(dry_run=args.dry_run)
