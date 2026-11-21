"""Retire only the dedicated profile's idle, verified player targets."""

from urllib.parse import urlsplit

from playwright.sync_api import Error

from tasks.queue import TERMINAL
from tools.browser.player import player_identity, target_id


def first_navigation(page):
    session = page.context.new_cdp_session(page)
    try:
        # Chrome assigns navigation entry IDs consecutively across tabs. A later
        # reload in the older tab does not replace its first history entry.
        history = session.send("Page.getNavigationHistory")["entries"]
        return history[0]["id"] if history else None
    finally:
        session.detach()


def cleanup(context, queue, ended_targets, log, *, driven=None, canvas_pages=()):
    pages = list(context.pages)
    before = len(pages)
    if any(i["status"] == "running" for i in queue["items"]):
        log.write("tab-count", "profile", "ok", 0, tabs_before=before, tabs_after=before,
                  detail=f"Before {before}, after {before}. Queue mid-item.")
        return
    verified = [i for i in queue["items"] if i["status"] in TERMINAL and i.get("canvas_verified")]
    dead = set(ended_targets) & {i.get("ended_player") or i.get("player") for i in verified}
    known = {identity for i in verified if (identity := i.get("ended_identity") or i.get("player_identity"))}
    protected = {i.get("player") for i in queue["items"] if i["status"] not in TERMINAL}
    duplicates, close = {}, set()
    for page in pages:
        if page in canvas_pages or page.is_closed() or urlsplit(page.url).hostname == "canvas.wisc.edu":
            continue
        try:
            tid = target_id(page)
            if tid in protected:
                continue
            identity = player_identity(page.url)
            if identity in known:
                duplicates.setdefault(identity, []).append(page)
            if page is not driven and (page.url == "about:blank" or tid in dead):
                close.add(page)
        except Error:
            continue
    for copies in duplicates.values():
        if len(copies) > 1:
            try:
                ordered = [(first_navigation(p), p) for p in copies]
                if any(k is None for k, _ in ordered) or len({k for k, _ in ordered}) != len(copies):
                    close.difference_update(copies)
                    continue
                newest = max(ordered, key=lambda e: e[0])[1]
                close.update(p for p in copies if p is not newest and p is not driven)
                close.discard(newest)
            except Error:
                close.difference_update(copies)
    for page in close:
        try:
            # Re-check immediately before closing. A blank may have become a real player.
            address = page.url
            if (page is driven or page.is_closed() or urlsplit(address).hostname == "canvas.wisc.edu"
                    or not (address == "about:blank" or target_id(page) in dead or player_identity(address) in known)):
                continue
            page.close(run_before_unload=False)
            log.write("tab-closed", urlsplit(address).hostname or "", "ok", 0, rule_used="L14")
        except Error:
            continue
    after = len(context.pages)
    log.write("tab-count", "profile", "ok", 0, tabs_before=before, tabs_after=after, detail=f"Before {before}, after {after}.")
