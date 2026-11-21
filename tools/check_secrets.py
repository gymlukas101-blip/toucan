#!/usr/bin/env python3
"""Check tracked text for credentials. --fix redacts jwt= values in the worktree."""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


JWT_PARAM = re.compile(rb"(?<!\w)(jwt=)[A-Za-z0-9._%~-]{20,}", re.IGNORECASE)
PATTERNS = {
    "jwt": re.compile(
        rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
        rb"|(?i:" + JWT_PARAM.pattern + rb")"
    ),
    "bearer": re.compile(rb"\bBearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
    "openai": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"),
    "canvas_token": re.compile(rb"\b\d{4,7}~[A-Za-z0-9]{20,}"),
}


def paths_to_check(root: Path, staged: bool) -> list[str]:
    if staged:
        # A rename contributes a delete and an add, so each raw record names one path.
        records = subprocess.check_output(
            ["git", "diff", "--cached", "--raw", "-z", "--no-renames", "--diff-filter=AMT", "--"],
            cwd=root,
        ).split(b"\0")
        return [
            os.fsdecode(name)
            for header, name in zip(records[::2], records[1::2])
            if header.split()[1] != b"160000"  # Submodule entries point to commits.
        ]

    result = subprocess.run(
        ["git", "grep", "-I", "-l", "-z", "-e", ".", "--"], cwd=root, stdout=subprocess.PIPE
    )
    if result.returncode == 1:  # No nonempty tracked text files.
        return []
    result.check_returncode()
    return [os.fsdecode(name) for name in result.stdout.split(b"\0") if name]


def redact_file(path: Path, data: bytes) -> bytes:
    redacted = JWT_PARAM.sub(rb"\1<redacted>", data)
    if redacted == data:
        return data
    stream = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(redacted)
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return redacted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fix", action="store_true", help="redact JWT parameters without staging changes")
    mode.add_argument("--staged", action="store_true", help="check changed files in the Git index")
    args = parser.parse_args()

    top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"]).rstrip(b"\r\n")
    root = Path(os.fsdecode(top)).resolve()
    hits = 0
    for relative in paths_to_check(root, args.staged):
        path = root / relative
        if args.staged:
            data = subprocess.check_output(["git", "cat-file", "blob", f":0:{relative}"], cwd=root)
        else:
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
                continue
            data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue
        if args.fix:
            data = redact_file(path, data)
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(data):
                line = data.count(b"\n", 0, match.start()) + 1
                print(f"{relative}:{line}: {label}")
                hits += 1
    if hits:
        print(f"{hits} secret(s). Redact the affected files and stage the clean versions.")
        return 1
    print("no secrets in staged changes" if args.staged else "no secrets in tracked files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
