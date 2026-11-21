#!/usr/bin/env python3
"""Digest run.jsonl into candidate lessons for human or agent review."""

import argparse
import json
from collections import Counter
from pathlib import Path

if __package__:
    from .check_secrets import PATTERNS
else:
    from check_secrets import PATTERNS


WATCH = {"stall", "miss", "ambiguous", "aborted", "domain", "retake", "escalate", "fail"}


def first_example(record: dict) -> str:
    text = " ".join(str(record.get(field) or "") for field in ("t", "action", "target"))
    data = text.encode("utf-8", errors="replace")
    for pattern in PATTERNS.values():
        data = pattern.sub(b"<redacted>", data)
    return " ".join(data.decode("utf-8").split()).replace("|", r"\|")


def main(path: str) -> int:
    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    try:
        stream = Path(path).open(encoding="utf-8-sig")
    except OSError as error:
        print(f"Cannot read {path}: {error.strerror}")
        return 1
    with stream:
        for line in stream:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            event = record.get("result") or record.get("event")
            if event == "ok" and record.get("action") == "retake":
                event = "retake"
            if not isinstance(event, str) or event not in WATCH:
                continue
            counts[event] += 1
            if event not in examples:
                examples[event] = first_example(record)

    if not counts:
        print("nothing to report")
        return 0
    print(f"# Run digest: {Path(path).name}\n")
    print("| event | count | first example |")
    print("|---|---|---|")
    for event, count in counts.most_common():
        print(f"| {event} | {count} | {examples[event][:70]} |")
    print("\nCandidate lessons (2+ occurrences):")
    for event, count in counts.most_common():
        if count >= 2:
            print(f"- {event} x{count} — {examples[event][:90]}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="run.jsonl")
    raise SystemExit(main(parser.parse_args().path))
