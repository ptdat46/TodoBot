"""Channel-neutral notification storage and optional push delivery hooks.

The module keeps reminders available in a local SQLite inbox. Optional push
providers can consume the same notification payload without making the worker
dependent on third-party packages.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


_SCHEMA = """
CREATE TABLE IF NOT EXISTS notification_history (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_file TEXT NOT NULL,
    event_type TEXT NOT NULL,
    notify_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    delivery_id TEXT,
    error_message TEXT
);
CREATE TABLE IF NOT EXISTS notification_inbox (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    source_file TEXT NOT NULL,
    event_type TEXT NOT NULL,
    notify_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    device_id TEXT,
    user_agent TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS device_tokens (
    token TEXT PRIMARY KEY,
    device_id TEXT,
    platform TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _connect(db_path: str) -> sqlite3.Connection:
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(db_path, factory=_ClosingConnection)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: str) -> None:
    """Create notification storage tables, preserving existing records."""
    with _connect(db_path) as connection:
        existing = {row[1] for row in connection.execute("PRAGMA table_info(notification_history)")}
        if existing and "created_at" not in existing:
            # Migrate the original schema, whose dispatch-specific columns and
            # required sent_at field are not part of the channel-neutral model.
            connection.execute("ALTER TABLE notification_history RENAME TO notification_history_legacy")
            connection.executescript(_SCHEMA)
            legacy_columns = {row[1] for row in connection.execute("PRAGMA table_info(notification_history_legacy)")}
            created_column = "sent_at" if "sent_at" in legacy_columns else "NULL"
            delivery_column = "resend_id" if "resend_id" in legacy_columns else "NULL"
            error_column = "error_message" if "error_message" in legacy_columns else "NULL"
            connection.execute(
                "INSERT OR IGNORE INTO notification_history "
                "(id, title, source_file, event_type, notify_at, created_at, status, delivery_id, error_message) "
                f"SELECT id, title, source_file, COALESCE(event_type, 'general'), notify_at, {created_column}, status, {delivery_column}, {error_column} "
                "FROM notification_history_legacy"
            )
            connection.execute("DROP TABLE notification_history_legacy")
        else:
            connection.executescript(_SCHEMA)


def _close_result(connection):
    try:
        connection.commit()
    finally:
        connection.close()


def is_already_sent(event_id: str, db_path: str) -> bool:
    """Return whether an occurrence is already accepted by the inbox."""
    init_db(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM notification_history WHERE id = ? AND status IN ('queued', 'sent', 'delivered')",
            (event_id,),
        ).fetchone()
    return row is not None


def format_notification(event: Dict[str, Any]) -> str:
    """Build the plain text body shared by all delivery channels."""
    return "[{type}] {title}\nTime: {notify_at}\nID: {id}".format(
        type=str(event.get("type", "general")).upper(),
        title=event.get("title", event.get("id", "Notification")),
        notify_at=event.get("notify_at", ""),
        id=event.get("id", ""),
    )


def enqueue_notification(event: Dict[str, Any], db_path: str) -> bool:
    """Persist one notification in the inbox; return False for duplicates."""
    init_db(db_path)
    now = _utc_now()
    body = str(event.get("body") or format_notification(event))
    values = (
        event["id"],
        str(event.get("title", event["id"])),
        body,
        str(event.get("source_file", "")),
        str(event.get("type", "general")),
        str(event.get("notify_at", "")),
        now,
    )
    with _connect(db_path) as connection:
        inserted = connection.execute(
            "INSERT OR IGNORE INTO notification_inbox "
            "(id, title, body, source_file, event_type, notify_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            values,
        ).rowcount > 0
        if inserted:
            connection.execute(
                "INSERT OR REPLACE INTO notification_history "
                "(id, title, source_file, event_type, notify_at, created_at, status, delivery_id, error_message) "
                "VALUES (?, ?, ?, ?, ?, ?, 'queued', NULL, NULL)",
                (event["id"], values[1], values[3], values[4], values[5], now),
            )
    return inserted


def record_notification(
    event_id: str,
    title: str,
    source_file: str,
    event_type: str,
    notify_at: str,
    status: str,
    delivery_id: Optional[str] = None,
    error_message: Optional[str] = None,
    db_path: str = "data/bot.sqlite",
) -> None:
    """Record a channel-neutral delivery result."""
    init_db(db_path)
    now = _utc_now()
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO notification_history "
            "(id, title, source_file, event_type, notify_at, created_at, status, delivery_id, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, title, source_file, event_type, notify_at, now, status, delivery_id, error_message),
        )


def list_inbox(db_path: str, unread_only: bool = False) -> List[Dict[str, Any]]:
    init_db(db_path)
    query = "SELECT * FROM notification_inbox"
    if unread_only:
        query += " WHERE read_at IS NULL"
    query += " ORDER BY created_at, id"
    with _connect(db_path) as connection:
        return [dict(row) for row in connection.execute(query)]


def mark_read(notification_id: str, db_path: str) -> bool:
    init_db(db_path)
    with _connect(db_path) as connection:
        result = connection.execute(
            "UPDATE notification_inbox SET read_at = ? WHERE id = ? AND read_at IS NULL",
            (_utc_now(), notification_id),
        )
    return result.rowcount > 0


def register_push_subscription(
    subscription: Dict[str, Any], db_path: str, device_id: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> int:
    """Upsert a browser push subscription and return its local id."""
    endpoint = str(subscription.get("endpoint", "")).strip()
    keys = subscription.get("keys") or {}
    p256dh = str(subscription.get("p256dh") or keys.get("p256dh") or "").strip()
    auth = str(subscription.get("auth") or keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        raise ValueError("A push subscription requires endpoint, p256dh, and auth")
    init_db(db_path)
    now = _utc_now()
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT INTO push_subscriptions "
            "(endpoint, p256dh, auth, device_id, user_agent, created_at, updated_at, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, "
            "device_id=excluded.device_id, user_agent=excluded.user_agent, updated_at=excluded.updated_at, active=1",
            (endpoint, p256dh, auth, device_id, user_agent, now, now),
        )
        return int(connection.execute("SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()[0])


def remove_push_subscription(endpoint: str, db_path: str) -> bool:
    init_db(db_path)
    with _connect(db_path) as connection:
        result = connection.execute("UPDATE push_subscriptions SET active = 0, updated_at = ? WHERE endpoint = ?", (_utc_now(), endpoint))
    return result.rowcount > 0


def list_push_subscriptions(db_path: str, active_only: bool = True) -> List[Dict[str, Any]]:
    init_db(db_path)
    query = "SELECT * FROM push_subscriptions"
    if active_only:
        query += " WHERE active = 1"
    with _connect(db_path) as connection:
        return [dict(row) for row in connection.execute(query)]


def register_device_token(token: str, db_path: str, device_id: Optional[str] = None, platform: Optional[str] = None) -> None:
    """Upsert a native-device token without requiring an SDK."""
    token = str(token).strip()
    if not token:
        raise ValueError("A device token is required")
    init_db(db_path)
    now = _utc_now()
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT INTO device_tokens (token, device_id, platform, created_at, updated_at, active) "
            "VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT(token) DO UPDATE SET device_id=excluded.device_id, "
            "platform=excluded.platform, updated_at=excluded.updated_at, active=1",
            (token, device_id, platform, now, now),
        )


def remove_device_token(token: str, db_path: str) -> bool:
    init_db(db_path)
    with _connect(db_path) as connection:
        result = connection.execute("UPDATE device_tokens SET active = 0, updated_at = ? WHERE token = ?", (_utc_now(), token))
    return result.rowcount > 0


def list_device_tokens(db_path: str, active_only: bool = True) -> List[Dict[str, Any]]:
    init_db(db_path)
    query = "SELECT * FROM device_tokens"
    if active_only:
        query += " WHERE active = 1"
    with _connect(db_path) as connection:
        return [dict(row) for row in connection.execute(query)]


def deliver_web_push(
    subscription: Dict[str, Any], payload: Dict[str, Any], vapid_private_key: Optional[str] = None,
    vapid_claims: Optional[Dict[str, str]] = None, webpush_impl: Optional[Callable[..., Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """Deliver a browser push payload when the optional package is installed."""
    if webpush_impl is None:
        try:
            from pywebpush import webpush as webpush_impl  # type: ignore
        except ImportError:
            return False, "optional dependency pywebpush is not installed"
    if not vapid_private_key or not vapid_claims:
        return False, "VAPID credentials are required"
    try:
        webpush_impl(
            subscription_info=subscription,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=vapid_private_key,
            vapid_claims=vapid_claims,
        )
        return True, None
    except Exception as exc:
        return False, str(exc)


def deliver_fcm(
    token: str, payload: Dict[str, Any], sender: Optional[Callable[..., Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """Deliver a native push payload through an optional Firebase sender hook."""
    if sender is None:
        try:
            from firebase_admin import messaging  # type: ignore
            sender = messaging.send
        except ImportError:
            return False, "optional dependency firebase-admin is not installed"
    try:
        result = sender({"token": token, "data": {"notification": json.dumps(payload, ensure_ascii=False)}})
        return True, None if result is None else str(result)
    except Exception as exc:
        return False, str(exc)


def deliver_notification(
    notification: Dict[str, Any], db_path: str, vapid_private_key: Optional[str] = None,
    vapid_claims: Optional[Dict[str, str]] = None, webpush_impl: Optional[Callable[..., Any]] = None,
    fcm_sender: Optional[Callable[..., Any]] = None,
) -> Dict[str, int]:
    """Fan out an inbox item to registered push endpoints and record outcomes."""
    payload = {"id": notification["id"], "title": notification["title"], "body": notification["body"]}
    delivered = failed = 0
    errors: List[str] = []
    for subscription in list_push_subscriptions(db_path):
        ok, error = deliver_web_push(subscription, payload, vapid_private_key, vapid_claims, webpush_impl)
        if ok:
            delivered += 1
        else:
            failed += 1
            if error:
                errors.append(error)
    for device in list_device_tokens(db_path):
        ok, result = deliver_fcm(device["token"], payload, fcm_sender)
        if ok:
            delivered += 1
        else:
            failed += 1
            if result:
                errors.append(result)
    if delivered:
        with _connect(db_path) as connection:
            connection.execute("UPDATE notification_inbox SET delivered_at = ? WHERE id = ?", (_utc_now(), notification["id"]))
    if failed:
        init_db(db_path)
        with _connect(db_path) as connection:
            connection.execute(
                "UPDATE notification_history SET status = 'failed', error_message = ? WHERE id = ?",
                ("; ".join(errors), notification["id"]),
            )
    elif delivered:
        record_notification(notification["id"], notification["title"], notification["source_file"], notification["event_type"], notification["notify_at"], "delivered")
    return {"delivered": delivered, "failed": failed}
