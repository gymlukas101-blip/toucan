"""Read stable rule IDs from LESSONS.md and maintain the active rule limit."""

import re
from pathlib import Path


LESSONS = Path(__file__).resolve().parents[1] / "LESSONS.md"
LIMIT = 15
RULE = re.compile(r"^- \[(L[0-9]{2,})\] ([^\n]+(?:\n[ \t]+[^\n]+)*)(?:\n|$)", re.MULTILINE)


def lesson_rules(text, *, enforce_limit=True):
    rules = {}
    for match in RULE.finditer(text):
        key, entry = match.groups()
        if key in rules:
            raise ValueError(f"Duplicate lesson ID {key}.")
        rules[key] = " ".join(entry.split())
    if len(rules) != sum(line.startswith("- ") for line in text.splitlines()):
        raise ValueError("Each active lesson must start with '- [Lnn] '.")
    if enforce_limit and len(rules) > LIMIT:
        raise ValueError(f"Found {len(rules)} active lessons. The limit is {LIMIT}.")
    return rules


def prune_lessons(path, uses):
    text = path.read_text(encoding="utf-8")
    rules = lesson_rules(text, enforce_limit=False)
    excess = len(rules) - LIMIT
    if excess <= 0:
        return []
    ranked = sorted(rules, key=lambda key: uses.get(key, 0))
    cutoff = uses.get(ranked[excess - 1], 0)
    if uses.get(ranked[excess], 0) == cutoff:
        tied = [key for key in ranked if uses.get(key, 0) == cutoff]
        raise ValueError(f"Retirement cutoff tied at {cutoff} recorded uses: {', '.join(tied)}. "
                         "Use run evidence to resolve the tie in LESSONS.md.")
    retired = ranked[:excess]
    kept = RULE.sub(lambda m: "" if m[1] in retired else m[0], text)
    path.write_text(kept, encoding="utf-8")
    return retired
