"""Build an ordered queue from course listings. Planning never opens an item."""

import re
from collections import Counter

from tasks.queue import validate
from tools.canvas.resolve import Ambiguous, normalized, resolve


ALL = re.compile(r"^(?:every|all)(?: the)? (?:modules?|items?)$", re.I)
AND = re.compile(r"\s+and\s+", re.I)
THEN = re.compile(r",?\s+(?:and\s+)?then\s+", re.I)


def _one_item(name, rows):
    needle = normalized(name)
    available = [row for row in rows if row["state"] != "instruction"]
    matches = [row for row in available if normalized(row["title"]) == needle]
    if not matches:
        matches = [row for row in available if normalized(row["title"]).startswith(needle + " ")]
    if not matches and needle.startswith("the "):
        return _one_item(needle[4:], rows)
    if len(matches) > 1:
        raise Ambiguous(name, matches)
    return matches[0] if matches else None


def whole_course_row(row):
    return row["state"] != "instruction" and not re.match(r"^optional\b", row["group"], re.I)


def _select(phrase, rows):
    if ALL.fullmatch(phrase):
        return [r for r in rows if whole_course_row(r)]
    # Match an entire title before interpreting an "and" inside it as a separator.
    if match := _one_item(phrase, rows):
        return [match]
    if join := THEN.search(phrase):
        rest = re.sub(r"^(?:do|complete|finish)\s+", "", phrase[join.end():], flags=re.I)
        return [*_select(phrase[:join.start()], rows), *_select(rest, rows)]
    for conjunction in AND.finditer(phrase):
        if first := _one_item(phrase[:conjunction.start()], rows):
            rest = re.sub(r"^(?:do|complete|finish)\s+", "", phrase[conjunction.end():], flags=re.I)
            return [first, *_select(rest, rows)]
    raise ValueError(f"No visible item matches {phrase!r}.")


def _scope(phrase, default, courses):
    named = re.fullmatch(r"(.+)\s+in\s+(.+)", phrase, flags=re.I)
    if named:
        try:
            return named[1].strip(), resolve(named[2], courses)
        except Ambiguous:
            raise
        except ValueError:
            if default is None and len(courses) != 1:
                raise
    if default is not None:
        return phrase, resolve(default, courses)
    if len(courses) == 1:
        return phrase, courses[0]
    raise Ambiguous("course for " + phrase, courses)


def _scopes(phrase, default, courses):
    # A trailing course applies to both items. Independently qualified items can
    # span courses, each of which is read once and retains its own listed order.
    for join in AND.finditer(phrase):
        left, right = phrase[:join.start()], phrase[join.end():]
        if re.search(r"\s+in\s+", left, re.I) and re.search(r"\s+in\s+", right, re.I):
            return [_scope(left, default, courses), *_scopes(right, default, courses)]
    return [_scope(phrase, default, courses)]


def queue_item(row, course_id):
    return {**{field: row[field] for field in ("key", "title", "url", "requirement")},
            "course_id": course_id, "status": "pending", "note": ""}


def course_order(rows, explicit_order=()):
    """Apply the requested sequence, then honor visible prerequisites."""
    if explicit_order:
        ranks = {key: n for n, key in enumerate(explicit_order)}
        selected = iter(sorted((r for r in rows if r["key"] in ranks), key=lambda r: ranks[r["key"]]))
        rows = [next(selected) if r["key"] in ranks else r for r in rows]
    aliases, numbers = {}, {}
    for row in rows:
        if row["state"] == "instruction":
            continue
        title = normalized(row["title"])
        aliases.setdefault(title, []).append(row["key"])
        short = re.match(r"^(module (\d+)|pre survey|post survey)\b", title)
        if short and short[1] != title:
            aliases.setdefault(short[1], []).append(row["key"])
        if short and short[2]:
            numbers[int(short[2])] = short[1]
    if not aliases:
        return rows
    names = sorted(aliases, key=len, reverse=True)
    token = re.compile(r"\b(?:" + "|".join(map(re.escape, names)) + r")\b")
    predecessors = {}
    for instruction in (r["title"] for r in rows if r["state"] == "instruction"):
        for sentence in re.split(r"[.!?\n]", instruction.casefold()):
            if not re.search(r"\b(complete|take|start|finish|first|before|after|then|order)\b", sentence):
                continue
            def expand(match):
                low, high = int(match[1]), int(match[2])
                direction = sorted(numbers, reverse=low > high)
                return " then ".join(numbers[n] for n in direction if min(low, high) <= n <= max(low, high))
            sentence = re.sub(r"\bmodules?\s+(\d+)\s*(?:[-–—]|to|through)\s*(\d+)", expand, sentence)
            sentence = normalized(sentence)
            matches = list(token.finditer(sentence))
            for left, right in zip(matches, matches[1:]):
                a, b = aliases[left[0]], aliases[right[0]]
                if len(a) != 1 or len(b) != 1:
                    raise ValueError("A visible ordering instruction names more than one matching item.")
                before, after = a[0], b[0]
                if (left is matches[0] and sentence.startswith("before ")
                        or re.search(r"\bafter\b", sentence[left.end():right.start()])):
                    before, after = after, before
                if before != after:
                    predecessors.setdefault(before, set())
                    predecessors.setdefault(after, set()).add(before)
    if not predecessors:
        return rows
    remaining = [r for r in rows if r["key"] in predecessors]
    ordered, finished = [], set()
    while remaining:
        ready = next((r for r in remaining if predecessors[r["key"]] <= finished), None)
        if ready is None:
            raise ValueError("The course has conflicting visible order instructions.")
        remaining.remove(ready)
        ordered.append(ready)
        finished.add(ready["key"])
    sequence = iter(ordered)
    return [next(sequence) if r["key"] in predecessors else r for r in rows]


def plan(request, courses, read_modules, course=None):
    phrase = re.sub(r"^(?:do|complete|finish)\s+", "", request.strip().rstrip("."), flags=re.I)
    if not phrase:
        raise ValueError("Supply the items to plan.")
    scopes = _scopes(phrase, course, courses)
    listings, chosen, explicit_order = {}, set(), []
    for selection, resolved in scopes:
        selection = re.sub(r"^(?:do|complete|finish)\s+", "", selection, flags=re.I)
        cid = resolved["id"]
        if cid not in listings:
            listings[cid] = {"course": resolved, "rows": read_modules(cid)}
        selected = [r["key"] for r in _select(selection, listings[cid]["rows"])]
        chosen.update(selected)
        if len(selected) > 1 and THEN.search(selection):
            explicit_order.extend(selected)
    kind = "multi" if len(chosen) > 1 or len(scopes) > 1 else "single"
    if len(scopes) == 1 and ALL.fullmatch(scopes[0][0]):
        kind = "course_all"
    queue = {"type": kind, "request": request, "course_query": course,
             "courses": [v["course"] for v in listings.values()], "items": [], "requested_keys": []}
    if explicit_order:
        queue["explicit_order"] = list(dict.fromkeys(explicit_order))
    locked, deferred, order = [], [], []
    for listing in listings.values():
        for row in course_order(listing["rows"], explicit_order):
            if row["state"] == "locked":
                locked.append(row["title"])
            if row["key"] not in chosen:
                continue
            # Pin the request to stable DOM identities. Only a visible, unlocked link
            # can turn an identity into an executable item, including on resume.
            queue["requested_keys"].append(row["key"])
            if row["state"] in {"pending", "locked", "unavailable"}:
                order.append(row["title"])
            if row["state"] in {"locked", "unavailable"}:
                deferred.append({"title": row["title"], "reason": row["state"]})
            if row["state"] != "pending":
                continue
            queue["items"].append(queue_item(row, listing["course"]["id"]))
    rows = [row for listing in listings.values() for row in listing["rows"]]
    report = {"source": "dom", "courses_seen": len(courses), "listings": list(listings.values()),
              "rows_seen": len(rows), "states": dict(Counter(r["state"] for r in rows)),
              "locked": locked, "deferred": deferred, "order": order}
    return validate(queue), report
