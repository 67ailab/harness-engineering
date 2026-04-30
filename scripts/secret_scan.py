from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "openai": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "github_pat": re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"),
    "anthropic": re.compile(r"sk-ant-[A-Za-z0-9\-]{20,}"),
    "google_api": re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),
    "slack": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
}

EXCLUDE_DIRS = {".git", ".runs", "runs", "__pycache__"}


def tracked_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    hits: list[str] = []
    for path in tracked_files():
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in PATTERNS.items():
            if pattern.search(text):
                hits.append(f"{path}: matched {name}")
    if hits:
        print("Potential secrets found:")
        for hit in hits:
            print(hit)
        return 1
    print("No obvious secrets found in tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
