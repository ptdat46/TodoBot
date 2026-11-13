import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import git_sync


class GitSyncTests(unittest.TestCase):
    def test_allowlist_excludes_runtime_and_secret_paths(self):
        self.assertTrue(git_sync.is_allowed_path("README.md"))
        self.assertTrue(git_sync.is_allowed_path("android/app/src/main/AndroidManifest.xml"))
        self.assertTrue(git_sync.is_allowed_path("web/index.html"))
        for path in ("memory/tasks.md", ".env", "data/bot.sqlite", "uploads/a.txt", "logs/app.log", "android/app/google-services.json"):
            self.assertFalse(git_sync.is_allowed_path(path))

    def test_parse_status_paths_handles_untracked_and_rename(self):
        output = " M README.md\n?? android/app/build.gradle\nR  old.md -> README.md\n"
        self.assertEqual(
            git_sync.parse_status_paths(output),
            ["README.md", "android/app/build.gradle", "README.md"],
        )

    def test_sync_stages_only_allowlisted_paths_and_adds_trailer(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "README.md").write_text("updated\n", encoding="utf-8")
            (repo / "memory").mkdir()
            (repo / "memory" / "tasks.md").write_text("private\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md", "memory/tasks.md"], cwd=repo, check=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial"], cwd=repo, check=True)
            (repo / "README.md").write_text("updated again\n", encoding="utf-8")
            (repo / "memory" / "tasks.md").write_text("private again\n", encoding="utf-8")

            with patch.object(git_sync, "run_git", wraps=git_sync.run_git) as run:
                with patch.dict(os.environ, {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}):
                    self.assertEqual(git_sync.sync_repository(repo), 0)

            commit = subprocess.check_output(["git", "log", "-1", "--pretty=%B"], cwd=repo, text=True)
            self.assertIn(git_sync.CO_AUTHOR_TRAILER, commit)
            self.assertEqual((repo / "memory" / "tasks.md").read_text(encoding="utf-8"), "private again\n")
            staged_commands = [call.args[0] for call in run.call_args_list if call.args and call.args[0][:2] == ["add", "--"]]
            self.assertEqual(staged_commands, [["add", "--", "README.md"]])


if __name__ == "__main__":
    unittest.main()
