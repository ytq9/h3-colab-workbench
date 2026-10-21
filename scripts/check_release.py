#!/usr/bin/env python3
"""Conservative check of tracked release files; reports paths, never secrets."""
from pathlib import Path
from fnmatch import fnmatch
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".mp4", ".mov", ".safetensors", ".ckpt", ".pth", ".db", ".log", ".pid", ".whl"}
FORBIDDEN_PARTS = {"private", "jobs", "runtime", ".venv", "work", "outputs", "__pycache__",
                   "local-notes", ".codex", ".claude", ".agents", ".cursor"}
FORBIDDEN_NAMES = {"token.json", "previous-token.json", "identity.json", "oauth-client.json", "sessions.json",
                   "config.local.json", "CONTEXT.md", "AGENTS.md", "AGENTS.override.md", "CLAUDE.md",
                   "GEMINI.md", "SKILL.md", "MEMORY.md", ".cursorrules"}
FORBIDDEN_PATTERNS = ("docs/incident-*.md", "docs/verification*.json", "docs/verification.md",
                      ".github/copilot-instructions.md")
SECRET_PATTERNS = [
    rb"gh[pousr]_[A-Za-z0-9]{30,}",
    rb"github_pat_[A-Za-z0-9_]{30,}",
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"ya29\.[A-Za-z0-9_-]{30,}",
    rb"/Users/[a-zA-Z0-9_-]+/",
]


def main():
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    failures = []
    count = 0
    for value in filter(None, paths):
        path = Path(value)
        if (path.suffix in FORBIDDEN_SUFFIXES or FORBIDDEN_PARTS.intersection(path.parts)
                or path.name in FORBIDDEN_NAMES or path.name.startswith(".env")
                or any(fnmatch(value, pattern) for pattern in FORBIDDEN_PATTERNS)):
            failures.append((value, "runtime/private artifact"))
            continue
        data = (ROOT / path).read_bytes()
        count += 1
        if len(data) > 1_000_000 or b"\0" in data:
            failures.append((value, "unexpected binary/large file"))
        if any(re.search(pattern, data) for pattern in SECRET_PATTERNS):
            failures.append((value, "credential/local-user-path pattern"))
    for path, reason in failures:
        print(f"FAIL {path}: {reason}")
    if not failures:
        print(f"PASS: {count} tracked text files; no prohibited release artifacts found.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
