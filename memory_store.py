"""Safe read/write access to the Markdown memory directory."""
import json
import os


class MemoryStore:
    def __init__(self, directory):
        self.directory = os.path.abspath(directory)
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, name):
        name = str(name).strip()
        if not name or os.path.basename(name) != name or name in (".", ".."):
            raise ValueError("memory path is outside memory directory")
        if not name.endswith(".md"):
            name += ".md"
        path = os.path.abspath(os.path.join(self.directory, name))
        if os.path.commonpath((path, self.directory)) != self.directory:
            raise ValueError("memory path is outside memory directory")
        return name, path

    def list_files(self):
        return sorted(name for name in os.listdir(self.directory) if name.endswith(".md") and os.path.isfile(os.path.join(self.directory, name)))

    def read(self, name):
        clean, path = self._path(name)
        if not os.path.isfile(path):
            raise FileNotFoundError(clean)
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def write(self, name, content):
        clean, path = self._path(name)
        if not isinstance(content, str):
            raise TypeError("content must be text")
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        return clean

    def tool(self, name, arguments):
        try:
            if name == "list_memory_files":
                return {"files": self.list_files()}
            if name == "read_memory_file":
                return {"filename": self._path(arguments.get("filename", ""))[0], "content": self.read(arguments.get("filename", ""))}
            if name == "write_memory_file":
                return {"filename": self.write(arguments.get("filename", ""), arguments.get("content", "")), "status": "success"}
            if name == "edit_memory_file":
                old = self.read(arguments.get("filename", ""))
                needle = arguments.get("old_text", "")
                if needle not in old:
                    return {"error": "old_text was not found"}
                self.write(arguments.get("filename", ""), old.replace(needle, arguments.get("new_text", ""), 1))
                return {"status": "success"}
            return {"error": "unknown memory tool"}
        except Exception as exc:
            return {"error": str(exc)}

    def prompt_context(self):
        profile = ""
        try:
            profile = self.read("profile.md")
        except FileNotFoundError:
            pass
        return "Memory files: %s\n\nProfile:\n%s" % (", ".join(self.list_files()), profile)
