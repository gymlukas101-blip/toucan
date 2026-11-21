#!/usr/bin/env python3
"""Validate the format, source and date of Markdown memory facts."""

import os
import re
import subprocess
from collections.abc import Iterator
from datetime import date
from pathlib import Path


ALLOWED = {"transcript", "screenshot", "domain", "inferred"}
LINE = re.compile(
    r"^\s*-\s+.+<!--\s*id=(\S+)\s+src=(\S+)\s+date=(\d{4}-\d{2}-\d{2})\s*-->$"
)


def memory_files(root: Path) -> Iterator[tuple[Path, str]]:
    """Git supplies GIT_INDEX_FILE during a commit; standalone runs check working files."""
    if "GIT_INDEX_FILE" not in os.environ:
        for path in sorted((root / "memory").rglob("*.md")):
            yield path, path.read_text(encoding="utf-8-sig")
        return

    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--", ":(glob)memory/**/*.md"], cwd=root
    )
    for name in names.split(b"\0"):
        if name:
            relative = os.fsdecode(name)
            blob = subprocess.check_output(["git", "cat-file", "blob", f":0:{relative}"], cwd=root)
            yield root / relative, blob.decode("utf-8-sig")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    memory = root / "memory"
    bad = 0
    for path, contents in memory_files(root):
        for number, line in enumerate(contents.splitlines(), 1):
            if not line.lstrip().startswith("- "):
                continue
            match = LINE.fullmatch(line.rstrip())
            if not match:
                error = "malformed fact line"
            elif match[2] not in ALLOWED:
                error = f"bad src {match[2]}"
            elif match[2] == "inferred" and path != memory / "inferences.md":
                error = "inferred facts belong in memory/inferences.md"
            else:
                try:
                    date.fromisoformat(match[3])
                except ValueError:
                    error = f"invalid date {match[3]}"
                else:
                    continue
            print(f"{path.relative_to(root).as_posix()}:{number}: {error}")
            bad += 1
    print("memory ok" if not bad else f"{bad} problem(s)")
    return int(bool(bad))


if __name__ == "__main__":
    raise SystemExit(main())
