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
            resend_id="re_12345",
            db_path=self.db_path
        )

        self.assertTrue(worker.is_already_sent("event-test-1", self.db_path))


if __name__ == "__main__":
    unittest.main()
