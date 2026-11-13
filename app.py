"""Private one-account web runtime for TodoBot (Python standard library)."""
import argparse
import hashlib
import hmac
import http.cookies
import json
import mimetypes
import os
import secrets
import sqlite3
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import config
import notifications
from agent_core import AgentCore
from memory_store import MemoryStore


SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, csrf_token TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, filename TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, extracted_text TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS error_events (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT, error_type TEXT, created_at TEXT NOT NULL);
"""


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class ConversationStore:
    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        with sqlite3.connect(db_path, factory=_ClosingConnection) as db:
            db.executescript(SCHEMA)

    def connect(self):
        db = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    def create(self, title="New conversation"):
        ident = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO conversations VALUES (?, ?, ?, ?)", (ident, title[:200], now(), now()))
        return {"id": ident, "title": title[:200]}

    def list(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,title,created_at,updated_at FROM conversations ORDER BY updated_at DESC")]

    def get(self, ident):
        with self.connect() as db:
            row = db.execute("SELECT id,title,created_at,updated_at FROM conversations WHERE id=?", (ident,)).fetchone()
            return dict(row) if row else None

    def delete(self, ident):
        with self.connect() as db:
            db.execute("DELETE FROM messages WHERE conversation_id=?", (ident,))
            return db.execute("DELETE FROM conversations WHERE id=?", (ident,)).rowcount > 0

    def add_message(self, ident, role, content):
        with self.connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)", (ident, role, content, now()))
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), ident))

    def messages(self, ident, limit=100):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT role,content,created_at FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (ident, limit))][::-1]

    def export(self, ident):
        conversation = self.get(ident)
        if not conversation:
            return None
        lines = ["# " + conversation["title"], ""]
        for message in self.messages(ident):
            lines.extend(["## " + message["role"].title(), "", message["content"], ""])
        return "\n".join(lines)


def verify_password(password, stored, development=False):
    if not stored:
        return False
    if "$" not in stored:
        return development and hmac.compare_digest(password, stored)
    salt_hex, digest_hex = stored.split("$", 1)
    try:
        derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(derived, digest_hex)
    except (ValueError, TypeError):
        return False


def signed_cookie(token, secret):
    signature = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    return token + "." + signature


def unsign_cookie(value, secret):
    try:
        token, signature = value.rsplit(".", 1)
        expected = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        return token if hmac.compare_digest(signature, expected) else None
    except ValueError:
        return None


class AppHandler(BaseHTTPRequestHandler):
    server_version = "TodoBotWeb/1.0"

    def rate_allowed(self):
        now_value = time.time()
        address = self.client_address[0]
        entries = self.server.rate_limits.setdefault(address, [])
        entries[:] = [stamp for stamp in entries if now_value - stamp < 60]
        limit = int(self.settings.get("RATE_LIMIT", 60))
        if len(entries) >= limit:
            self.send_json(429, {"error": "rate limit exceeded"})
            return False
        entries.append(now_value)
        return True

    def log_message(self, fmt, *args):
        # Keep request logs free of cookies, bodies, and provider credentials.
        if self.path.startswith("/api/"):
            return
        super().log_message(fmt, *args)

    @property
    def settings(self):
        return self.server.settings

    @property
    def store(self):
        return self.server.store

    def send_json(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > int(self.settings.get("MAX_BODY_BYTES", 6 * 1024 * 1024)):
            raise ValueError("request body too large")
        return self.rfile.read(length)

    def json_body(self):
        return json.loads(self.read_body().decode("utf-8"))

    def session(self):
        cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        signed = cookie.get("todobot_session")
        if not signed:
            return None
        token = unsign_cookie(signed.value, self.settings.get("SESSION_SECRET", ""))
        if not token:
            return None
        with self.store.connect() as db:
            row = db.execute("SELECT token_hash,csrf_token,expires_at FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return dict(row) if row and row["expires_at"] > time.time() else None

    def require_auth(self):
        session = self.session()
        if not session:
            self.send_json(401, {"error": "authentication required"})
            return None
        return session

    def require_csrf(self, session):
        if not session or not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf_token"]):
            self.send_json(403, {"error": "csrf token required"})
            return False
        return True

    def do_GET(self):
        if not self.rate_allowed():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/session":
                session = self.session()
                self.send_json(200, {"authenticated": bool(session), "csrf_token": session["csrf_token"] if session else None})
                return
            if path.startswith("/api/"):
                session = self.require_auth()
                if not session:
                    return
                if path == "/api/conversations":
                    self.send_json(200, {"conversations": self.store.list()}); return
                if path == "/api/memory":
                    self.send_json(200, {"files": [{"filename": name, "size_bytes": os.path.getsize(os.path.join(self.server.memory.directory, name))} for name in self.server.memory.list_files()]}); return
                if path.startswith("/api/memory/"):
                    name = unquote(path.rsplit("/", 1)[1])
                    self.send_json(200, {"filename": name, "content": self.server.memory.read(name)}); return
                if path.startswith("/api/conversations/"):
                    ident = path.split("/")[3]
                    if path.endswith("/export"):
                        ident = path.split("/")[3]
                        result = self.store.export(ident)
                        if result is None: self.send_json(404, {"error": "not found"})
                        else:
                            body = result.encode(); self.send_response(200); self.send_header("Content-Type", "text/markdown; charset=utf-8"); self.send_header("Content-Disposition", "attachment; filename=conversation.md"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                        return
                    conversation = self.store.get(ident)
                    self.send_json(200 if conversation else 404, {**conversation, "messages": self.store.messages(ident)} if conversation else {"error": "not found"}); return
                if path == "/api/logs":
                    with self.store.connect() as db:
                        rows = [dict(row) for row in db.execute("SELECT id,path,error_type,created_at FROM error_events ORDER BY id DESC LIMIT 100")]
                    self.send_json(200, {"events": rows}); return
                if path == "/api/notifications":
                    self.send_json(200, {"notifications": notifications.list_inbox(self.settings["DB_PATH"], unread_only=False)}); return
                if path == "/api/push/config":
                    self.send_json(200, {"enabled": bool(self.settings.get("VAPID_PUBLIC_KEY")), "public_key": self.settings.get("VAPID_PUBLIC_KEY")}); return
                if path == "/api/push/subscriptions":
                    with self.store.connect() as db:
                        rows = [json.loads(row[0]) for row in db.execute("SELECT subscription_json FROM push_subscriptions ORDER BY created_at DESC")]
                    self.send_json(200, {"subscriptions": rows}); return
                self.send_json(404, {"error": "not found"}); return
            self.serve_static(path)
        except Exception as exc:
            self.record_error(type(exc).__name__)
            self.send_json(400, {"error": "request failed"})

    def do_POST(self):
        if not self.rate_allowed():
            return
        path = urlparse(self.path).path
        try:
            if path == "/api/login":
                data = self.json_body()
                if not verify_password(str(data.get("password", "")), self.settings.get("APP_PASSWORD_HASH", ""), self.settings.get("APP_ENV") == "development"):
                    self.send_json(401, {"error": "invalid credentials"}); return
                token = secrets.token_urlsafe(32); csrf = secrets.token_urlsafe(24)
                with self.store.connect() as db:
                    db.execute(
                        "INSERT INTO sessions(token_hash,csrf_token,created_at,expires_at) VALUES(?,?,?,?)",
                        (hashlib.sha256(token.encode()).hexdigest(), csrf, time.time(), time.time() + 86400),
                    )
                self.send_json(200, {"authenticated": True, "csrf_token": csrf}, {"Set-Cookie": "todobot_session=%s; HttpOnly; SameSite=Strict; Path=/" % signed_cookie(token, self.settings.get("SESSION_SECRET", ""))}); return
            session = self.require_auth()
            if not session or not self.require_csrf(session): return
            if path == "/api/notifications/read":
                data = self.json_body()
                self.send_json(200, {"read": notifications.mark_read(str(data.get("id", "")), self.settings["DB_PATH"])})
                return
            if path == "/api/logout":
                cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", "")); signed = cookie.get("todobot_session"); token = unsign_cookie(signed.value, self.settings.get("SESSION_SECRET", "")) if signed else None
                if token:
                    with self.store.connect() as db: db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
                self.send_json(200, {"authenticated": False}, {"Set-Cookie": "todobot_session=; Max-Age=0; Path=/"}); return
            if path == "/api/conversations":
                data = self.json_body(); self.send_json(201, self.store.create(str(data.get("title") or "New conversation"))); return
            if path == "/api/chat":
                data = self.json_body(); ident = str(data.get("conversation_id") or "")
                if not self.store.get(ident): ident = self.store.create()["id"]
                message = str(data.get("message") or "").strip()
                attachment_ids = [str(item) for item in (data.get("attachment_ids") or [])]
                if attachment_ids:
                    with self.store.connect() as db:
                        rows = db.execute("SELECT filename,extracted_text FROM uploads WHERE id IN (%s)" % ",".join("?" for _ in attachment_ids), attachment_ids).fetchall()
                    attachments = "\n\n".join("[File: %s]\n%s" % (row[0], row[1] or "") for row in rows)
                    message = "\n\n".join(part for part in (message, attachments) if part)
                self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.send_header("Cache-Control", "no-store"); self.end_headers()
                try:
                    for event in self.server.agent.stream(ident, message):
                        self.wfile.write((json.dumps({**event, "conversation_id": ident}, ensure_ascii=False) + "\n").encode()); self.wfile.flush()
                except Exception:
                    self.wfile.write(b'{"type":"error","error":"chat failed"}\n'); self.wfile.flush()
                return
            if path == "/api/uploads":
                data = self.read_body(); filename = self.headers.get("X-Filename", "upload.txt")
                if len(data) > int(self.settings.get("MAX_UPLOAD_BYTES", 5 * 1024 * 1024)): raise ValueError("upload too large")
                if os.path.basename(filename) != filename or not filename or len(filename) > 200: raise ValueError("invalid filename")
                mime = self.headers.get_content_type() or mimetypes.guess_type(filename)[0] or "application/octet-stream"
                allowed_extensions = (".txt", ".md", ".csv", ".json", ".log", ".xml", ".yaml", ".yml", ".py", ".js", ".ts", ".pdf", ".docx", ".xlsx", ".pptx", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp3", ".wav", ".m4a", ".mp4", ".mov", ".webm")
                if not filename.lower().endswith(allowed_extensions): raise ValueError("unsupported file type")
                ident = uuid.uuid4().hex
                upload_dir = os.path.abspath(self.settings.get("UPLOAD_DIR", os.path.join(os.path.dirname(self.settings["DB_PATH"]), "uploads")))
                os.makedirs(upload_dir, exist_ok=True)
                with open(os.path.join(upload_dir, ident + ".bin"), "wb") as handle: handle.write(data)
                text = data.decode("utf-8", errors="replace") if mime.startswith("text/") or filename.lower().endswith((".txt", ".md", ".csv", ".json", ".log", ".xml", ".yaml", ".yml", ".py", ".js", ".ts")) else ""
                with self.store.connect() as db: db.execute("INSERT INTO uploads VALUES(?,?,?,?,?,?)", (ident, filename, mime, len(data), text, now()))
                self.send_json(201, {"id": ident, "filename": filename, "mime_type": mime, "size_bytes": len(data), "text": text}); return
            if path == "/api/push/subscriptions":
                subscription = self.json_body()
                ident = notifications.register_push_subscription(subscription, self.settings["DB_PATH"], user_agent=self.headers.get("User-Agent"))
                self.send_json(201, {"id": ident}); return
            if path == "/api/devices":
                data = self.json_body()
                notifications.register_device_token(data.get("token", ""), self.settings["DB_PATH"], data.get("device_id"), data.get("platform", "android"))
                self.send_json(201, {"registered": True}); return
            self.send_json(404, {"error": "not found"})
        except Exception as exc:
            self.record_error(type(exc).__name__); self.send_json(400, {"error": "request failed"})

    def do_DELETE(self):
        if not self.rate_allowed():
            return
        path = urlparse(self.path).path
        session = self.require_auth()
        if not session or not self.require_csrf(session): return
        if path.startswith("/api/conversations/"):
            ident = path.split("/")[3]
            self.send_json(200 if self.store.delete(ident) else 404, {"deleted": True}); return
        self.send_json(404, {"error": "not found"})

    def record_error(self, error_type):
        try:
            with self.store.connect() as db: db.execute("INSERT INTO error_events(path,error_type,created_at) VALUES(?,?,?)", (urlparse(self.path).path, error_type, now()))
        except Exception: pass

    def serve_static(self, path):
        relative = path.lstrip("/") or "index.html"
        if relative == "web" or relative.startswith("web/"):
            relative = relative[5:]
        candidate = os.path.abspath(os.path.join(self.settings.get("WEB_DIR", "web"), relative))
        root = os.path.abspath(self.settings.get("WEB_DIR", "web"))
        if os.path.commonpath((candidate, root)) != root or not os.path.isfile(candidate): self.send_json(404, {"error": "not found"}); return
        with open(candidate, "rb") as handle: body = handle.read()
        self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(candidate)[0] or "application/octet-stream"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


def create_server(host=None, port=None, settings=None):
    values = config.load_env(); values.update(settings or {})
    values.setdefault("SESSION_SECRET", values.get("APP_PASSWORD_HASH") or secrets.token_urlsafe(32))
    server = ThreadingHTTPServer((host or values.get("APP_HOST", "127.0.0.1"), int(port or values.get("APP_PORT", "8080"))), AppHandler)
    server.daemon_threads = True
    server.block_on_close = False
    server.settings = values
    server.rate_limits = {}
    server.store = ConversationStore(values["DB_PATH"])
    notifications.init_db(values["DB_PATH"])
    server.memory = MemoryStore(values["MEMORY_DIR"])
    server.agent = AgentCore(values, server.memory, server.store)
    return server


def main():
    parser = argparse.ArgumentParser(description="TodoBot private web server")
    parser.add_argument("--host", default=None); parser.add_argument("--port", type=int, default=None); parser.add_argument("--set-password", action="store_true")
    args = parser.parse_args()
    if args.set_password:
        import getpass
        password = getpass.getpass("New application password: ")
        print(config.password_hash(password)); return
    server = create_server(args.host, args.port)
    print("TodoBot web server listening on http://%s:%s" % server.server_address)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__": main()
