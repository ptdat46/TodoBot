#!/usr/bin/env python3
"""
Unit Test & Dry-Run Verification for TodoBot Worker
===================================================
Tests markdown parsing, regex matching, SQLite tracking, and dry-run dispatch.
"""

import os
import sys
import unittest
import tempfile
import sqlite3
import json
from unittest.mock import patch
from urllib.error import HTTPError
from io import BytesIO
from datetime import datetime, timezone, timedelta

# Import functions from worker
import worker

class TestTodoBotWorker(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.memory_dir = os.path.join(self.test_dir, "memory")
        self.data_dir = os.path.join(self.test_dir, "data")
        os.makedirs(self.memory_dir, exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "test.sqlite")
        worker.init_db(self.db_path)

    def test_parse_tags(self):
        sample_md = """# Tasks Test
- [ ] Task khẩn cấp cần làm <!-- id: task-001 | notify: 2026-10-20 10:00 | type: task | priority: high -->
- [x] Task đã xong không thông báo <!-- id: task-done | notify: 2026-10-15 08:00 | type: task -->
- [ ] Trả nợ ngân hàng <!-- id: debt-002 | notify: 2026-10-22 09:30 | type: debt -->
"""
        test_file = os.path.join(self.memory_dir, "tasks.md")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(sample_md)

        events = worker.parse_memory_files(self.memory_dir)
        self.assertEqual(len(events), 2)
        
        ev1 = next(e for e in events if e["id"] == "task-001")
        self.assertEqual(ev1["type"], "task")
        self.assertEqual(ev1["priority"], "high")
        self.assertEqual(ev1["notify_at"], "2026-10-20 10:00")

        ev2 = next(e for e in events if e["id"] == "debt-002")
        self.assertEqual(ev2["type"], "debt")
        self.assertEqual(ev2["notify_at"], "2026-10-22 09:30")

    def test_sqlite_idempotency(self):
        self.assertFalse(worker.is_already_sent("event-test-1", self.db_path))
        
        worker.record_notification(
            event_id="event-test-1",
            title="Test Event",
            source_file="tasks.md",
            event_type="task",
            notify_at="2026-10-15 10:00",
            status="sent",
            telegram_message_id="12345",
            db_path=self.db_path
        )

        self.assertTrue(worker.is_already_sent("event-test-1", self.db_path))
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT telegram_message_id FROM notification_history WHERE id = ?", ("event-test-1",)).fetchone()[0], "12345")

    def test_existing_database_migrates(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DROP TABLE notification_history")
            conn.execute("CREATE TABLE notification_history (id TEXT PRIMARY KEY, title TEXT NOT NULL, source_file TEXT NOT NULL, event_type TEXT, notify_at TEXT NOT NULL, sent_at TEXT NOT NULL, status TEXT NOT NULL, resend_id TEXT, error_message TEXT)")
        worker.init_db(self.db_path)
        worker.init_db(self.db_path)
        worker.record_notification("old-db", "Reminder", "tasks.md", "task", "2026-11-03 10:00", "sent", telegram_message_id="42", db_path=self.db_path)
        self.assertTrue(worker.is_already_sent("old-db", self.db_path))

    def test_activation_is_persisted(self):
        activated_at = datetime(2026, 11, 3, 9, 0, 10, tzinfo=worker.LOCAL_TZ)
        activation_minute = activated_at.replace(second=0)
        self.assertEqual(worker.get_activated_at(activated_at, self.db_path), activation_minute)
        worker.init_db(self.db_path)
        self.assertEqual(worker.get_activated_at(activated_at + timedelta(days=1), self.db_path), activation_minute)
        event = {"id": "task-first-minute", "notify_at": "2026-11-03 09:00"}
        self.assertIsNotNone(worker.due_occurrence(event, activated_at, activation_minute))

    def test_one_time_reminders_respect_activation_and_catch_up(self):
        activated_at = datetime(2026, 11, 3, 9, 0, tzinfo=worker.LOCAL_TZ)
        event = {"id": "task-1", "notify_at": "2026-11-03 08:59"}
        self.assertIsNone(worker.due_occurrence(event, activated_at, activated_at))
        event["notify_at"] = "2026-11-03 10:00"
        now_local = datetime(2026, 11, 4, 10, 0, tzinfo=worker.LOCAL_TZ)
        self.assertEqual(worker.due_occurrence(event, now_local, activated_at)[0], "task-1")
        self.assertIsNone(worker.due_occurrence(event, now_local + timedelta(minutes=1), activated_at))

    def test_weekly_reminder_has_one_id_per_week(self):
        event = {"id": "backup", "notify_at": "2026-10-18 20:00", "repeat": "weekly"}
        activated_at = datetime(2026, 11, 3, 9, 0, tzinfo=worker.LOCAL_TZ)
        sunday = datetime(2026, 11, 8, 20, 0, tzinfo=worker.LOCAL_TZ)
        self.assertIsNone(worker.due_occurrence(event, sunday - timedelta(minutes=1), activated_at))
        event_id, scheduled_at = worker.due_occurrence(event, sunday, activated_at)
        self.assertEqual(event_id, "backup-20261108")
        self.assertEqual(scheduled_at, sunday)
        self.assertEqual(worker.due_occurrence(event, sunday + timedelta(hours=24), activated_at)[0], event_id)
        self.assertIsNone(worker.due_occurrence(event, sunday + timedelta(hours=24, minutes=1), activated_at))
        self.assertEqual(worker.due_occurrence(event, sunday + timedelta(days=7), activated_at)[0], "backup-20261115")

    def test_parse_weekly_tag(self):
        test_file = os.path.join(self.memory_dir, "tasks.md")
        with open(test_file, "w", encoding="utf-8") as file:
            file.write("- [ ] Backup <!-- id: backup | notify: 2026-10-18 20:00 | priority: normal | repeat: weekly -->\n")
        events = worker.parse_memory_files(self.memory_dir)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["repeat"], "weekly")

    def test_dry_run_does_not_activate_worker(self):
        with patch.object(worker, "DB_PATH", self.db_path), patch.object(worker, "parse_memory_files", return_value=[]), patch.object(worker, "load_env", return_value={}):
            worker.run_worker(dry_run=True)
        with sqlite3.connect(self.db_path) as conn:
            self.assertIsNone(conn.execute("SELECT value FROM worker_state WHERE key = 'activated_at'").fetchone())

    def test_send_telegram_message(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return b'{"ok":true,"result":{"message_id":42}}'

        with patch.object(worker.request, "urlopen", return_value=Response()) as urlopen:
            self.assertEqual(worker.send_telegram_message("token", "123", "Nhắc việc"), (True, "42", None))
            req = urlopen.call_args.args[0]
            self.assertEqual(req.full_url, "https://api.telegram.org/bottoken/sendMessage")
            self.assertEqual(json.loads(req.data), {"chat_id": "123", "text": "Nhắc việc"})

        http_error = HTTPError("https://api.telegram.org", 403, "Forbidden", {}, BytesIO(b'{"ok":false,"description":"Forbidden: bot was blocked by the user"}'))
        with patch.object(worker.request, "urlopen", side_effect=http_error):
            success, message_id, error_msg = worker.send_telegram_message("token", "123", "Nhắc việc")
            self.assertFalse(success)
            self.assertIsNone(message_id)
            self.assertIn("403", error_msg)


if __name__ == "__main__":
    unittest.main()
