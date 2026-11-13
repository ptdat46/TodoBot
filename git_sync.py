#!/usr/bin/env python3
"""Safely commit and push approved TodoBot source and documentation changes.

This helper deliberately stages individual paths from a small allowlist. Runtime
state, credentials, uploads, logs, and personal memory are never candidates for
synchronisation.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Sequence


REPO_ROOT = Path(__file__).resolve().parent
CO_AUTHOR_TRAILER = "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
COMMIT_SUBJECT = "chore: sync approved TodoBot changes"

# Root files are explicit so a newly created secret/config file cannot be staged
# merely because it happens to have a familiar extension.
ALLOWED_ROOT_FILES = {
    ".gitignore",
    ".env.example",
    "AGENTS.md",
    "README.md",
    "setup.sh",
    "git_sync.py",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "Makefile",
}
ALLOWED_ROOT_EXTENSIONS = {".py", ".md", ".sh", ".txt", ".toml"}
ALLOWED_DIRECTORIES = {"android", "web", ".github", "tests"}
EXCLUDED_COMPONENTS = {"memory", "data", "uploads", "logs", ".git", ".venv", "venv"}
SECRET_FILENAMES = {
    ".env",
    "google-services.json",
    "local.properties",
}
SECRET_SUFFIXES = {".key", ".pem", ".p12", ".jks", ".keystore"}


def _normalise(path: str) -> str:
    normalised = path.replace("\\", "/")
    while normalised.startswith("./"):
        normalised = normalised[2:]
    return normalised


def is_allowed_path(path: str) -> bool:
    """Return whether *path* is an approved source/documentation path."""
    normalised = _normalise(path)
    if not normalised or normalised.startswith("../") or "/../" in normalised:
        return False

    parts = PurePosixPath(normalised).parts
    if any(part in EXCLUDED_COMPONENTS for part in parts):
        return False
    basename = parts[-1].lower()
    if basename in SECRET_FILENAMES or any(basename.endswith(suffix) for suffix in SECRET_SUFFIXES):
        return False
    if basename.startswith(".env") and basename != ".env.example":
        return False

    if len(parts) == 1:
        return normalised in ALLOWED_ROOT_FILES or Path(normalised).suffix.lower() in ALLOWED_ROOT_EXTENSIONS
    return parts[0] in ALLOWED_DIRECTORIES


def parse_status_paths(status_output: str) -> List[str]:
    """Extract changed paths from porcelain status output."""
    paths: List[str] = []
    for line in status_output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        # Porcelain uses ``old -> new`` for renames/copies.  The destination is
        # the path that must be staged; the old path is also checked separately.
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        paths.append(path)
    return paths


def run_git(args: Sequence[str], repo: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=check,
    )


def changed_paths(repo: Path) -> List[str]:
    result = run_git(["status", "--porcelain=v1", "--untracked-files=all"], repo)
    return parse_status_paths(result.stdout)


def _has_staged_changes(repo: Path) -> bool:
    result = run_git(["diff", "--cached", "--quiet"], repo, check=False)
    return result.returncode != 0


def _staged_paths(repo: Path) -> List[str]:
    result = run_git(["diff", "--cached", "--name-only", "--diff-filter=ACDMRTUXB"], repo)
    return [line for line in result.stdout.splitlines() if line]


def sync_repository(
    repo: Path = REPO_ROOT,
    *,
    remote: str = "origin",
    branch: str | None = None,
    dry_run: bool = False,
) -> int:
    """Commit approved changes, fast-forward from the remote, then push.

    Disallowed changes are reported but left untouched.  The return value is
    zero when there is nothing to do or the sync succeeds.
    """
    repo = Path(repo).resolve()
    paths = changed_paths(repo)
    allowed = sorted({path for path in paths if is_allowed_path(path)})
    skipped = sorted({path for path in paths if not is_allowed_path(path)})

    if skipped:
        print("Skipped disallowed paths: " + ", ".join(skipped), file=sys.stderr)
    pre_staged = _staged_paths(repo)
    disallowed_staged = sorted(path for path in pre_staged if not is_allowed_path(path))
    if disallowed_staged:
        print("Refusing to sync disallowed staged paths: " + ", ".join(disallowed_staged), file=sys.stderr)
        return 1
    if not allowed:
        print("No approved changes to sync.")
        return 0

    print("Approved paths: " + ", ".join(allowed))
    if dry_run:
        return 0

    for path in allowed:
        if (repo / path).exists():
            run_git(["add", "--", path], repo)
        else:
            run_git(["add", "-u", "--", path], repo, check=False)

    if not _has_staged_changes(repo):
        print("No staged changes to sync.")
        return 0

    run_git(["commit", "-m", COMMIT_SUBJECT, "-m", CO_AUTHOR_TRAILER], repo)

    if branch is None:
        branch_result = run_git(["branch", "--show-current"], repo)
        branch = branch_result.stdout.strip()
    if not branch:
        raise RuntimeError("Unable to determine the current branch")

    remote_result = run_git(["remote", "get-url", remote], repo, check=False)
    if remote_result.returncode != 0:
        print(f"Remote '{remote}' is not configured; commit created locally.")
        return 0

    run_git(["pull", "--ff-only", remote, branch], repo)
    run_git(["push", remote, branch], repo)
    print(f"Synced {branch} to {remote}.")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return sync_repository(args.repo, remote=args.remote, branch=args.branch, dry_run=args.dry_run)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "git command failed").strip()
        print(f"Git sync failed: {detail}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Git sync failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
