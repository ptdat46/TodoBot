import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import app


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "bot.sqlite")
        self.memory = os.path.join(self.tmp.name, "memory")
        os.makedirs(self.memory)
        with open(os.path.join(self.memory, "tasks.md"), "w", encoding="utf-8") as f:
            f.write("# Tasks\n- [ ] Demo\n")
        self.server = app.create_server(
            "127.0.0.1", 0,
            settings={
                "APP_PASSWORD_HASH": "dev-password",
                "APP_ENV": "development",
                "DB_PATH": self.db,
                "MEMORY_DIR": self.memory,
                "SESSION_SECRET": "test-session-secret",
                "AI_BASE_URL": "",
                "AI_API_KEY": "",
            },
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        headers = headers or {}
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        self.conn.request(method, path, body=body, headers=headers)
        response = self.conn.getresponse()
        data = response.read()
        return response, data

    def login(self):
        response, data = self.request("POST", "/api/login", {"password": "dev-password"})
        self.assertEqual(response.status, 200)
        self.cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        self.csrf = json.loads(data)["csrf_token"]

    def test_login_session_and_csrf(self):
        response, data = self.request("GET", "/api/session")
        self.assertEqual(response.status, 200)
        self.assertFalse(json.loads(data)["authenticated"])
        self.login()
        response, data = self.request("GET", "/api/session", headers={"Cookie": self.cookie})
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(data)["authenticated"])
        response, _ = self.request("POST", "/api/logout", headers={"Cookie": self.cookie})
        self.assertEqual(response.status, 403)
        response, _ = self.request("POST", "/api/logout", headers={"Cookie": self.cookie, "X-CSRF-Token": self.csrf})
        self.assertEqual(response.status, 200)

    def test_conversation_and_export(self):
        self.login()
        headers = {"Cookie": self.cookie, "X-CSRF-Token": self.csrf}
        response, data = self.request("POST", "/api/conversations", {"title": "Planning"}, headers)
        self.assertEqual(response.status, 201)
        conversation_id = json.loads(data)["id"]
        response, data = self.request("GET", "/api/conversations", headers=headers)
        self.assertEqual(response.status, 200)
        self.assertIn("Planning", [item["title"] for item in json.loads(data)["conversations"]])
        response, data = self.request("GET", "/api/conversations/%s/export" % conversation_id, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertIn("# Planning", data.decode())

    def test_upload_limits_and_memory_access(self):
        self.login()
        headers = {"Cookie": self.cookie, "X-CSRF-Token": self.csrf, "X-Filename": "notes.txt", "Content-Type": "text/plain"}
        response, data = self.request("POST", "/api/uploads", b"hello upload", headers)
        self.assertEqual(response.status, 201)
        self.assertEqual(json.loads(data)["text"], "hello upload")
        response, _ = self.request("POST", "/api/uploads", b"x", {**headers, "X-Filename": "../.env"})
        self.assertEqual(response.status, 400)
        response, data = self.request("GET", "/api/memory", headers=headers)
        self.assertEqual(response.status, 200)
        self.assertIn("tasks.md", [item["filename"] for item in json.loads(data)["files"]])

    def test_chat_streams_ndjson_without_provider_secret_leak(self):
        self.login()
        headers = {"Cookie": self.cookie, "X-CSRF-Token": self.csrf}
        with patch.object(app.AgentCore, "stream", return_value=iter([{"type": "delta", "content": "Xin chào"}, {"type": "done"}])):
            response, data = self.request("POST", "/api/chat", {"message": "hello"}, headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/x-ndjson")
        events = [json.loads(line) for line in data.decode().splitlines()]
        self.assertEqual(events[0]["type"], "delta")
        self.assertEqual(events[-1]["type"], "done")


if __name__ == "__main__":
    unittest.main()
