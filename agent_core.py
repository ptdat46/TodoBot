"""Provider adapter and memory-only tool-calling agent."""
import json
from urllib import error, request

MAX_TOOL_ITERATIONS = 8

TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "list_memory_files", "description": "List Markdown files in the personal memory directory.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "read_memory_file", "description": "Read a personal memory Markdown file.", "parameters": {"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}}},
    {"type": "function", "function": {"name": "edit_memory_file", "description": "Replace one exact section in a personal memory Markdown file.", "parameters": {"type": "object", "properties": {"filename": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["filename", "old_text", "new_text"]}}},
    {"type": "function", "function": {"name": "write_memory_file", "description": "Write a personal memory Markdown file.", "parameters": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}}},
]


def build_chat_completions_url(base_url):
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


class AgentCore:
    def __init__(self, settings, memory_store, conversation_store):
        self.settings = settings
        self.memory = memory_store
        self.conversations = conversation_store

    def _provider(self, messages):
        base = self.settings.get("AI_BASE_URL") or self.settings.get("OPENAI_BASE_URL", "")
        key = self.settings.get("AI_API_KEY") or self.settings.get("OPENAI_API_KEY", "")
        if not base or not key:
            return {"choices": [{"message": {"role": "assistant", "content": "AI provider chưa được cấu hình."}}]}
        payload = {"model": self.settings.get("AI_MODEL", "gpt-6-luna"), "messages": messages, "tools": TOOLS_SCHEMA, "tool_choice": "auto"}
        req = request.Request(build_chat_completions_url(base), data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
        try:
            with request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode())
        except error.HTTPError as exc:
            raise RuntimeError("AI provider request failed (HTTP %s)" % exc.code)
        except Exception as exc:
            raise RuntimeError("AI provider request failed: %s" % type(exc).__name__)

    def stream(self, conversation_id, text):
        history = self.conversations.messages(conversation_id, 20)
        messages = [{"role": "system", "content": "You are a private assistant. You may only use memory tools; never use shell, workspace, or arbitrary filesystem tools.\n" + self.memory.prompt_context()}]
        messages.extend({"role": item["role"], "content": item["content"]} for item in history)
        messages.append({"role": "user", "content": text})
        for _ in range(MAX_TOOL_ITERATIONS):
            response = self._provider(messages)
            message = (response.get("choices") or [{}])[0].get("message") or {}
            calls = message.get("tool_calls") or []
            if not calls:
                reply = (message.get("content") or "").strip() or "Đã xử lý yêu cầu."
                self.conversations.add_message(conversation_id, "user", text)
                self.conversations.add_message(conversation_id, "assistant", reply)
                yield {"type": "delta", "content": reply}
                yield {"type": "done"}
                return
            messages.append(message)
            for call in calls:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                result = self.memory.tool(fn.get("name", ""), args)
                messages.append({"role": "tool", "tool_call_id": call.get("id", "call"), "name": fn.get("name", ""), "content": json.dumps(result, ensure_ascii=False)})
        raise RuntimeError("agent tool iteration limit reached")
