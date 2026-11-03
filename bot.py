#!/usr/bin/env python3
"""
TodoBot Telegram AI Agent
=========================
Ultra-lightweight Telegram AI Assistant for 1GB RAM Ubuntu VPS.
- Zero external pip dependencies (Standard library only).
- OpenAI-compatible Chat Completions API with Function Calling (Tools).
- Full read/write/edit access to `memory/*.md` adhering to `AGENTS.md`.
- Long-polling Telegram listener + short-term conversation memory in SQLite.
"""

import os
import sys
import json
import time
import sqlite3
import argparse
from datetime import datetime
from urllib import request, error

# Ensure project directory is on sys.path (supports embedded Python on Windows)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import worker

# Ensure UTF-8 output across Windows and Linux
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = worker.BASE_DIR
MEMORY_DIR = worker.MEMORY_DIR
DB_PATH = worker.DB_PATH
AGENTS_MD_PATH = os.path.join(BASE_DIR, "AGENTS.md")

DEFAULT_MODEL = "gpt-6-luna"
MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ITERATIONS = 8


def init_chat_db(db_path=DB_PATH):
    """Ensure SQLite tables for Telegram offset and conversation history exist."""
    worker.init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


def get_state(key, default=None, db_path=DB_PATH):
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT value FROM worker_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default


def set_state(key, value, db_path=DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO worker_state (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        conn.commit()


def add_chat_message(chat_id, role, content, db_path=DB_PATH):
    now_str = datetime.now(worker.LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO chat_history (chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (str(chat_id), role, content, now_str),
        )
        # Keep only the latest messages per chat to keep context & DB small
        conn.execute("""
            DELETE FROM chat_history
            WHERE chat_id = ? AND id NOT IN (
                SELECT id FROM chat_history
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
            )
        """, (str(chat_id), str(chat_id), MAX_HISTORY_MESSAGES))
        conn.commit()


def get_chat_history(chat_id, limit=MAX_HISTORY_MESSAGES, db_path=DB_PATH):
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("""
            SELECT role, content FROM (
                SELECT id, role, content FROM chat_history
                WHERE chat_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
        """, (str(chat_id), limit)).fetchall()
    return [{"role": r[0], "content": r[1]} for r in rows]


def clear_chat_history(chat_id, db_path=DB_PATH):
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM chat_history WHERE chat_id = ?", (str(chat_id),))
        conn.commit()


def safe_memory_path(filename, memory_dir=MEMORY_DIR):
    """Resolve and validate that filename stays strictly inside memory/ and ends with .md."""
    clean_name = os.path.basename(filename.strip())
    if not clean_name.endswith(".md"):
        clean_name += ".md"
    os.makedirs(memory_dir, exist_ok=True)
    full_path = os.path.abspath(os.path.join(memory_dir, clean_name))
    mem_abs = os.path.abspath(memory_dir)
    if os.path.commonpath([full_path, mem_abs]) != mem_abs:
        raise ValueError("Truy cập bị từ chối: Chỉ được phép thao tác file trong thư mục memory/.")
    return clean_name, full_path


# ==============================================================================
# Agent Tools Implementation
# ==============================================================================

def tool_list_memory_files(memory_dir=MEMORY_DIR):
    os.makedirs(memory_dir, exist_ok=True)
    files = sorted(f for f in os.listdir(memory_dir) if f.endswith(".md"))
    result = []
    for f in files:
        p = os.path.join(memory_dir, f)
        size = os.path.getsize(p) if os.path.exists(p) else 0
        result.append({"filename": f, "size_bytes": size})
    return json.dumps({"files": result}, ensure_ascii=False)


def tool_read_memory_file(filename, memory_dir=MEMORY_DIR):
    try:
        clean_name, full_path = safe_memory_path(filename, memory_dir)
        if not os.path.exists(full_path):
            return json.dumps({"error": f"File '{clean_name}' không tồn tại trong memory/."}, ensure_ascii=False)
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        return json.dumps({"filename": clean_name, "content": content}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def tool_write_memory_file(filename, content, memory_dir=MEMORY_DIR):
    try:
        clean_name, full_path = safe_memory_path(filename, memory_dir)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return json.dumps({
            "status": "success",
            "filename": clean_name,
            "message": f"Đã lưu thành công file memory/{clean_name} ({len(content)} ký tự)."
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def tool_edit_memory_file(filename, old_text, new_text, memory_dir=MEMORY_DIR):
    try:
        clean_name, full_path = safe_memory_path(filename, memory_dir)
        if not os.path.exists(full_path):
            return json.dumps({"error": f"File '{clean_name}' không tồn tại."}, ensure_ascii=False)
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        if old_text not in content:
            return json.dumps({
                "error": f"Không tìm thấy đoạn văn bản old_text chính xác trong '{clean_name}'. Hãy đọc lại file hoặc dùng write_memory_file."
            }, ensure_ascii=False)
        updated = content.replace(old_text, new_text, 1)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(updated)
        return json.dumps({
            "status": "success",
            "filename": clean_name,
            "message": f"Đã cập nhật thành công file memory/{clean_name}."
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "list_memory_files",
            "description": "Liệt kê tất cả các file Markdown (.md) hiện có trong thư mục memory/.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_memory_file",
            "description": "Đọc toàn bộ nội dung một file Markdown trong thư mục memory/ (ví dụ: tasks.md, finances.md, debts.md, roadmap.md, profile.md).",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Tên file trong thư mục memory/ (ví dụ: 'tasks.md' hoặc 'finances.md')."
                    }
                },
                "required": ["filename"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_memory_file",
            "description": "Thay thế một đoạn nội dung cũ (old_text) bằng nội dung mới (new_text) trong một file thuộc thư mục memory/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Tên file trong thư mục memory/ (ví dụ: 'tasks.md')."
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Đoạn văn bản gốc cần thay thế (phải khớp chính xác)."
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Đoạn văn bản mới sẽ thay thế vào."
                    }
                },
                "required": ["filename", "old_text", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_memory_file",
            "description": "Ghi mới hoặc cập nhật toàn bộ nội dung cho một file Markdown trong thư mục memory/. Dùng khi tạo file mới (như habits.md) hoặc cập nhật lại toàn bộ cấu trúc bảng.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Tên file trong thư mục memory/ (ví dụ: 'finances.md', 'habits.md')."
                    },
                    "content": {
                        "type": "string",
                        "description": "Toàn bộ nội dung Markdown hoàn chỉnh của file."
                    }
                },
                "required": ["filename", "content"]
            }
        }
    }
]


def execute_tool_call(name, arguments_str, memory_dir=MEMORY_DIR):
    try:
        args = json.loads(arguments_str) if arguments_str else {}
    except json.JSONDecodeError:
        return json.dumps({"error": f"JSON arguments không hợp lệ: {arguments_str}"}, ensure_ascii=False)

    if name == "list_memory_files":
        return tool_list_memory_files(memory_dir=memory_dir)
    if name == "read_memory_file":
        return tool_read_memory_file(args.get("filename", ""), memory_dir=memory_dir)
    if name == "edit_memory_file":
        return tool_edit_memory_file(
            args.get("filename", ""),
            args.get("old_text", ""),
            args.get("new_text", ""),
            memory_dir=memory_dir,
        )
    if name == "write_memory_file":
        return tool_write_memory_file(
            args.get("filename", ""),
            args.get("content", ""),
            memory_dir=memory_dir,
        )
    return json.dumps({"error": f"Tool không tồn tại: {name}"}, ensure_ascii=False)


# ==============================================================================
# System Prompt & OpenAI-Compatible Client
# ==============================================================================

def build_system_prompt(memory_dir=MEMORY_DIR, agents_md_path=AGENTS_MD_PATH):
    """Build system prompt combining AGENTS.md, current Vietnam time, and memory overview."""
    now_local = datetime.now(worker.LOCAL_TZ)
    weekdays_vi = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    weekday_name = weekdays_vi[now_local.weekday()]

    agents_rules = ""
    if os.path.exists(agents_md_path):
        with open(agents_md_path, "r", encoding="utf-8") as f:
            agents_rules = f.read()

    profile_content = ""
    profile_path = os.path.join(memory_dir, "profile.md")
    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            profile_content = f.read()

    os.makedirs(memory_dir, exist_ok=True)
    existing_files = sorted(f for f in os.listdir(memory_dir) if f.endswith(".md"))

    return (
        f"{agents_rules}\n\n"
        f"---\n"
        f"## 🕒 THÔNG TIN THỜI GIAN & NGỮ CẢNH HIỆN TẠI\n"
        f"- **Thời gian hiện tại (GMT+7)**: `{now_local.strftime('%Y-%m-%d %H:%M:%S')}` ({weekday_name})\n"
        f"- **Các file đang có trong `memory/`**: {', '.join(existing_files) if existing_files else '(Trống)'}\n"
        f"- **Kênh giao tiếp**: Bạn đang trả lời trực tiếp qua Telegram Bot. Hãy trình bày ngắn gọn, rõ ràng, dễ nhìn trên màn hình điện thoại.\n"
        f"- **Quy tắc sử dụng công cụ (Tools)**:\n"
        f"  1. Trước khi thêm/sửa dữ liệu (công việc, tài chính, nợ, lộ trình) hoặc trả lời câu hỏi cần tra cứu, BẮT BUỘC gọi `read_memory_file` để xem nội dung hiện tại.\n"
        f"  2. Sau khi tính toán xong, gọi `edit_memory_file` hoặc `write_memory_file` để lưu thay đổi vào file Markdown tương ứng.\n"
        f"  3. Luôn giữ nguyên định dạng thẻ thông báo ẩn `<!-- id: ... | notify: YYYY-MM-DD HH:MM | type: ... -->` khi tạo lịch nhắc.\n\n"
        f"### Hồ sơ người dùng (`memory/profile.md`):\n"
        f"{profile_content}\n"
    )


def build_chat_completions_url(base_url):
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def call_openai_compatible(base_url, api_key, model, messages, tools=None, timeout=60):
    """Send a Chat Completions request to an OpenAI-compatible endpoint."""
    url = build_chat_completions_url(base_url)
    payload = {
        "model": model,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "TodoBot-Executive-Assistant/1.0",
    }
    req = request.Request(url, data=data, headers=headers, method="POST")

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AI API HTTP {e.code}: {err_body}") from e
    except Exception as e:
        raise RuntimeError(f"Lỗi kết nối AI API: {e}") from e


def run_agent_turn(user_text, chat_id, config, memory_dir=MEMORY_DIR, db_path=DB_PATH):
    """Process a user message through the tool-calling loop and return the assistant reply."""
    base_url = config.get("AI_BASE_URL") or config.get("OPENAI_BASE_URL", "")
    api_key = config.get("AI_API_KEY") or config.get("OPENAI_API_KEY", "")
    model = config.get("AI_MODEL") or DEFAULT_MODEL

    if not base_url or not api_key or "your-openai-compatible-endpoint" in base_url or api_key == "your_api_key_here":
        return "⚠️ Chưa cấu hình `AI_BASE_URL` và `AI_API_KEY` thực tế trong file `.env`."

    system_prompt = build_system_prompt(memory_dir=memory_dir)
    history = get_chat_history(chat_id, db_path=db_path)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    final_reply = None

    for _ in range(MAX_TOOL_ITERATIONS):
        response = call_openai_compatible(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            tools=TOOLS_SCHEMA,
        )

        choices = response.get("choices") or []
        if not choices:
            raise RuntimeError("AI API trả về phản hồi rỗng (không có choices).")

        msg = choices[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            final_reply = (msg.get("content") or "").strip()
            break

        # Append assistant message requesting tool calls
        assistant_msg = {
            "role": "assistant",
            "content": msg.get("content"),
            "tool_calls": tool_calls,
        }
        messages.append(assistant_msg)

        # Execute each tool call and feed results back
        for tc in tool_calls:
            tc_id = tc.get("id", "call_0")
            func = tc.get("function") or {}
            fn_name = func.get("name", "")
            fn_args = func.get("arguments", "{}")

            print(f"    [Tool] Đang gọi {fn_name}({fn_args})")
            tool_output = execute_tool_call(fn_name, fn_args, memory_dir=memory_dir)

            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "name": fn_name,
                "content": tool_output,
            })

    if not final_reply:
        final_reply = "✅ Đã thực hiện xong yêu cầu của anh."

    add_chat_message(chat_id, "user", user_text, db_path=db_path)
    add_chat_message(chat_id, "assistant", final_reply, db_path=db_path)
    return final_reply


# ==============================================================================
# Telegram Polling & Command Handling
# ==============================================================================

def fetch_telegram_updates(bot_token, offset=None, timeout=25):
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates?timeout={timeout}"
    if offset is not None:
        url += f"&offset={offset}"

    req = request.Request(url, method="GET")
    with request.urlopen(req, timeout=timeout + 10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram getUpdates error"))
        return data.get("result", [])


def send_typing_action(bot_token, chat_id):
    url = f"https://api.telegram.org/bot{bot_token}/sendChatAction"
    payload = json.dumps({"chat_id": chat_id, "action": "typing"}).encode("utf-8")
    req = request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass


def send_long_telegram_message(bot_token, chat_id, text):
    """Split and send messages safely within Telegram's 4096-char limit."""
    max_len = 4000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or ["(Trống)"]
    for chunk in chunks:
        worker.send_telegram_message(bot_token, chat_id, chunk)


def handle_incoming_message(message, config, memory_dir=MEMORY_DIR, db_path=DB_PATH):
    bot_token = config.get("TELEGRAM_BOT_TOKEN", "")
    allowed_chat_id = str(config.get("TELEGRAM_CHAT_ID", "")).strip()

    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", "")).strip()
    text = (message.get("text") or "").strip()

    if not text or not chat_id:
        return

    # Security check: Only allow configured TELEGRAM_CHAT_ID
    if allowed_chat_id and chat_id != allowed_chat_id:
        print(f"[!] Từ chối tin nhắn từ chat_id lạ: {chat_id}")
        return

    print(f"[+] Nhận tin nhắn từ {chat_id}: {text}")

    # Built-in quick commands
    if text.lower() in ("/start", "/help"):
        welcome = (
            "🤖 Xin chào! Em là Trợ Lý Điều Hành Cá Nhân TodoBot.\n\n"
            "Anh có thể nhắn trực tiếp bằng ngôn ngữ tự nhiên:\n"
            "• Nhắc việc: 'Nhắc anh 15h chiều mai họp với đối tác'\n"
            "• Chi tiêu: 'Hôm nay đổ xăng 80k, ăn trưa 45k'\n"
            "• Quản lý nợ: 'Vừa trả 2 triệu thẻ tín dụng ngân hàng A'\n"
            "• Tra cứu: 'Tổng kết chi tiêu và các việc cần làm tuần này'\n\n"
            "Lệnh nhanh:\n"
            "• /clear - Xóa lịch sử hội thoại ngắn hạn (dữ liệu trong memory/ vẫn giữ nguyên)"
        )
        send_long_telegram_message(bot_token, chat_id, welcome)
        return

    if text.lower() == "/clear":
        clear_chat_history(chat_id, db_path=db_path)
        send_long_telegram_message(bot_token, chat_id, "🧹 Đã làm mới bộ nhớ hội thoại ngắn hạn. Dữ liệu trong `memory/` vẫn được giữ nguyên.")
        return

    send_typing_action(bot_token, chat_id)

    try:
        reply = run_agent_turn(text, chat_id, config, memory_dir=memory_dir, db_path=db_path)
    except Exception as e:
        print(f"[-] Lỗi xử lý AI Agent: {e}", file=sys.stderr)
        reply = f"⚠️ Đã xảy ra lỗi khi gọi AI Agent:\n{e}"

    send_long_telegram_message(bot_token, chat_id, reply)


def process_updates_once(config, timeout=25, memory_dir=MEMORY_DIR, db_path=DB_PATH):
    """Fetch and process pending Telegram updates once."""
    bot_token = config.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token or bot_token == "your_bot_token_here":
        raise RuntimeError("Chưa cấu hình TELEGRAM_BOT_TOKEN hợp lệ trong .env")

    saved_offset = get_state("telegram_update_offset", db_path=db_path)
    offset = int(saved_offset) if saved_offset is not None else None

    updates = fetch_telegram_updates(bot_token, offset=offset, timeout=timeout)
    for upd in updates:
        update_id = upd.get("update_id")
        msg = upd.get("message") or upd.get("edited_message")
        if msg:
            handle_incoming_message(msg, config, memory_dir=memory_dir, db_path=db_path)
        if update_id is not None:
            offset = int(update_id) + 1
            set_state("telegram_update_offset", offset, db_path=db_path)

    return len(updates)


def run_polling_loop(once=False):
    config = worker.load_env()
    init_chat_db()

    model = config.get("AI_MODEL") or DEFAULT_MODEL
    base_url = config.get("AI_BASE_URL") or config.get("OPENAI_BASE_URL", "(chưa đặt)")
    print("==========================================================")
    print("🤖 TodoBot Telegram AI Agent đang khởi động...")
    print(f"   • Model   : {model}")
    print(f"   • Base URL: {base_url}")
    print(f"   • Chat ID : {config.get('TELEGRAM_CHAT_ID', '(chưa đặt)')}")
    print("==========================================================")

    if once:
        count = process_updates_once(config, timeout=0)
        print(f"[+] Đã xử lý {count} bản cập nhật.")
        return

    while True:
        try:
            config = worker.load_env()
            process_updates_once(config, timeout=25)
        except KeyboardInterrupt:
            print("\n[i] Đã dừng TodoBot Telegram AI Agent.")
            break
        except Exception as e:
            print(f"[-] Lỗi vòng lặp polling: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TodoBot Telegram AI Agent")
    parser.add_argument("--once", action="store_true", help="Quét và xử lý tin nhắn chờ 1 lần rồi thoát")
    parser.add_argument("--prompt", type=str, help="Chạy thử trực tiếp 1 câu lệnh với Agent trên terminal")
    args = parser.parse_args()

    if args.prompt:
        cfg = worker.load_env()
        init_chat_db()
        ans = run_agent_turn(args.prompt, cfg.get("TELEGRAM_CHAT_ID", "cli-test"), cfg)
        print("\n--- PHẢN HỒI CỦA AGENT ---\n")
        print(ans)
    else:
        run_polling_loop(once=args.once)
