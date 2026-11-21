#!/usr/bin/env python3
"""Digest run.jsonl into candidate lessons for human or agent review."""

import argparse
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

if __package__:
    from .check_secrets import PATTERNS
    from .lessons import LESSONS, lesson_rules, prune_lessons
else:
    from check_secrets import PATTERNS
    from lessons import LESSONS, lesson_rules, prune_lessons


WATCH = {"stall", "miss", "ambiguous", "aborted", "domain", "retake", "escalate", "fail"}


def first_example(record: dict) -> str:
    text = " ".join(str(record.get(field) or "") for field in ("t", "action", "target"))
    data = text.encode("utf-8", errors="replace")
    for pattern in PATTERNS.values():
        data = pattern.sub(b"<redacted>", data)
    return " ".join(data.decode("utf-8").split()).replace("|", r"\|")


def records(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def used_rule(record):
    key = record.get("rule_used")
    if isinstance(key, str) and re.fullmatch(r"L[0-9]{2,}", key):
        return key


def history_logs(path, extra_roots=()):
    paths = {Path(path).resolve()}
    roots = [LESSONS.parent, *extra_roots]
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "Toucan/runs")
    for root in roots:
        paths.update(p.resolve() for p in Path(root).rglob("run.jsonl"))
    return sorted(paths)


def main(path: str, *, prune=False, history=()) -> int:
    counts: Counter[str] = Counter()
    uses: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for record in records(path):
        if key := used_rule(record):
            uses[key] += 1
        event = record.get("result") or record.get("event")
        if event == "ok" and record.get("action") == "retake":
            event = "retake"
        if not isinstance(event, str) or event not in WATCH:
            continue
        counts[event] += 1
        if event not in examples:
            examples[event] = first_example(record)

    if uses:
        print("Rules used in this run: " + ", ".join(f"{key} x{count}" for key, count in uses.most_common()))
    if prune:
        totals = Counter(key for log in history_logs(path, history) for record in records(log)
                         if (key := used_rule(record)))
        for key in prune_lessons(LESSONS, totals):
            print(f"Retired {key} with {totals[key]} recorded uses.")

    if not counts:
        if not uses:
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
    parser.add_argument("--prune", action="store_true", help="delete the least-used rules above the limit")
    parser.add_argument("--history", action="append", default=[], type=Path, help="additional run directory to count")
    parser.add_argument("--check-staged", action="store_true", help="validate staged lesson IDs and the active cap")
    args = parser.parse_args()
    try:
        if args.check_staged:
            staged = subprocess.check_output(["git", "cat-file", "blob", ":0:LESSONS.md"], cwd=LESSONS.parent)
            lesson_rules(staged.decode("utf-8-sig").replace("\r\n", "\n"))
        else:
            raise SystemExit(main(args.path, prune=args.prune, history=args.history))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
