import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import notifications
import worker


class NotificationStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.directory.name) / "notifications.sqlite")
        worker.init_db(self.db_path)

    def tearDown(self):
        self.directory.cleanup()

    def test_enqueue_is_idempotent_and_available_in_inbox(self):
        event = {
            "id": "task-1",
            "title": "Review plan",
            "source_file": "tasks.md",
            "type": "task",
            "notify_at": "2026-11-13 10:00",
        }
        self.assertTrue(notifications.enqueue_notification(event, self.db_path))
        self.assertFalse(notifications.enqueue_notification(event, self.db_path))
        inbox = notifications.list_inbox(self.db_path)
        self.assertEqual(len(inbox), 1)
        self.assertIn("Review plan", inbox[0]["body"])
        self.assertTrue(worker.is_already_sent("task-1", self.db_path))

    def test_push_subscription_and_device_token_lifecycle(self):
        subscription = {
            "endpoint": "https://push.example/subscription-1",
            "keys": {"p256dh": "public", "auth": "secret"},
        }
        subscription_id = notifications.register_push_subscription(subscription, self.db_path, "phone")
        self.assertEqual(subscription_id, 1)
        notifications.register_push_subscription(subscription, self.db_path, "phone-updated")
        self.assertEqual(len(notifications.list_push_subscriptions(self.db_path)), 1)
        self.assertTrue(notifications.remove_push_subscription(subscription["endpoint"], self.db_path))
        self.assertEqual(notifications.list_push_subscriptions(self.db_path), [])

        notifications.register_device_token("device-token", self.db_path, "phone", "native")
        notifications.register_device_token("device-token", self.db_path, "phone-2", "native")
        self.assertEqual(len(notifications.list_device_tokens(self.db_path)), 1)
        self.assertTrue(notifications.remove_device_token("device-token", self.db_path))
        self.assertEqual(notifications.list_device_tokens(self.db_path), [])

    def test_optional_push_hooks_are_called_without_imports(self):
        subscription = {
            "endpoint": "https://push.example/subscription-1",
            "keys": {"p256dh": "public", "auth": "secret"},
        }
        webpush = Mock()
        ok, error = notifications.deliver_web_push(
            subscription, {"id": "n1"}, "private", {"sub": "mailto:test@example.com"}, webpush
        )
        self.assertTrue(ok)
        self.assertIsNone(error)
        webpush.assert_called_once()

        sender = Mock(return_value="message-id")
        ok, result = notifications.deliver_fcm("token", {"id": "n1"}, sender)
        self.assertTrue(ok)
        self.assertEqual(result, "message-id")
        sender.assert_called_once()

    def test_delivery_fanout_updates_inbox_and_failure_status(self):
        event = {"id": "task-1", "title": "Review", "source_file": "tasks.md", "type": "task", "notify_at": "2026-11-13 10:00"}
        notifications.enqueue_notification(event, self.db_path)
        notifications.register_push_subscription(
            {"endpoint": "https://push.example/sub", "keys": {"p256dh": "p", "auth": "a"}}, self.db_path
        )
        notifications.register_device_token("token", self.db_path)
        result = notifications.deliver_notification(
            notifications.list_inbox(self.db_path)[0], self.db_path,
            vapid_private_key="private", vapid_claims={"sub": "mailto:test@example.com"},
            webpush_impl=Mock(), fcm_sender=Mock(side_effect=RuntimeError("offline")),
        )
        self.assertEqual(result, {"delivered": 1, "failed": 1})
        self.assertIsNotNone(notifications.list_inbox(self.db_path)[0]["delivered_at"])
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute("SELECT status FROM notification_history WHERE id = 'task-1'").fetchone()[0], "failed")
        finally:
            connection.close()

    def test_dry_run_keeps_activation_and_inbox_unchanged(self):
        with patch.object(worker, "DB_PATH", self.db_path), patch.object(worker, "MEMORY_DIR", self.directory.name):
            Path(self.directory.name, "tasks.md").write_text(
                "- [ ] Due <!-- id: due-1 | notify: 2026-11-13 16:00 | type: task -->\n", encoding="utf-8"
            )
            worker.run_worker(dry_run=True)
        self.assertEqual(notifications.list_inbox(self.db_path), [])
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertIsNone(connection.execute("SELECT value FROM worker_state WHERE key = 'activated_at'").fetchone())
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
