"""Durable, ordered queues for Toucan course work."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit


TYPES = {"single", "course_all", "multi"}
TERMINAL = {"done", "skipped", "blocked"}
STATES = TERMINAL | {"pending", "running"}
REQUIREMENTS = {"must_submit", "min_score", "mark_done", "unknown"}


def validate(queue):
    if not isinstance(queue, dict) or queue.get("type") not in TYPES or not isinstance(queue.get("items"), list):
        raise ValueError("Expected a single, course_all or multi queue with an items list.")
    if "explicit_order" in queue:
        order = queue["explicit_order"]
        if (not isinstance(order, list) or not all(isinstance(key, str) and key for key in order)
                or len(set(order)) != len(order)):
            raise ValueError("An explicit sequence needs a list of distinct item keys.")
    seen = set()
    for row in queue["items"]:
        if not isinstance(row, dict):
            raise ValueError("Expected a queue item object.")
        for field in ("key", "title", "url", "requirement", "status", "note"):
            if not isinstance(row.get(field), str) or (field != "note" and not row[field]):
                raise ValueError(f"Invalid queue item field: {field}")
        if row["key"] in seen:
            raise ValueError(f"Duplicate queue key: {row['key']}")
        seen.add(row["key"])
        if row["status"] not in STATES or row["requirement"] not in REQUIREMENTS:
            raise ValueError(f"Invalid queue state or requirement for {row['title']}")
        url = urlsplit(row["url"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError(f"Expected an HTTP(S) item link for {row['title']}")
    return queue


def next_pending(queue):
    return next((item for item in queue["items"] if item["status"] not in TERMINAL), None)


def mark(queue, key, status, note=""):
    if status not in STATES or not isinstance(note, str):
        raise ValueError("Invalid queue status or note.")
    for item in queue["items"]:
        if item["key"] == key:
            item.update(status=status, note=note)
            if status != "running":
                if status in TERMINAL and item.get("canvas_verified") and item.get("player"):
                    item["ended_player"] = item["player"]
                    if item.get("player_identity"):
                        item["ended_identity"] = item["player_identity"]
                item.pop("player", None)
                item.pop("player_identity", None)
                item.pop("awaiting_verification", None)
                item.pop("handoff", None)
            return item
    raise KeyError(key)


def resume(queue, open_players=()):
    """Resume the bound player or its outstanding Canvas verification."""
    live = set(open_players)
    for item in queue["items"]:
        if item["status"] == "running" and not item.get("awaiting_verification") and item.get("player") not in live:
            mark(queue, item["key"], "pending", "Resumed after interruption.")
    return queue


def load(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def save(path, queue):
    validate(queue)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            json.dump(queue, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def exclusive(path):
    """The OS releases the adjacent lock when a runner exits, including after a crash."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+b") as stream:
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(f"Another process is using queue {path.name}.") from error
        yield
