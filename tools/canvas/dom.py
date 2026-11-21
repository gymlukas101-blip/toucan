"""Read Canvas course lists and modules from their rendered pages in Toucan Chrome."""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from tools.browser.driver import Driver, RunLog


ORIGIN = "https://canvas.wisc.edu"
SNAPSHOT = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8")
COURSE_LISTS = {"/", "/courses", "/dashboard"}


def requirement(label, check=False):
    label = " ".join(label.casefold().split())
    if label in {"✓", "✔", "☑"}:
        return "mark_done"
    if label in {"submit", "submitted"}:
        return "must_submit"
    if re.fullmatch(r"score(?:d)? at least\s+\d+(?:\.\d+)?(?:\s*(?:%|points?|pts))?", label):
        return "min_score"
    if label in {"must view", "mark as done"} or (not label and check):
        return "mark_done"
    return "unknown"


def parse_rows(snapshot, course_id):
    """Keep evidence of headers and locks even though they cannot be queued."""
    rows, keys = [], set()
    for raw in snapshot["rows"]:
        title, label, url = raw["title"], raw["label"], raw["href"]
        if not title:
            raise ValueError("A visible module row has no title. Check the saved snapshot.")
        identifier = raw["id"].removeprefix("context_module_item_")
        key = f"{course_id}:{identifier or url or title}"
        if key in keys:
            raise ValueError(f"Two visible rows have the same item key: {title}")
        keys.add(key)
        parts = urlsplit(url)
        if url and (parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password):
            raise ValueError(f"Unexpected link on module row: {title}")
        req = requirement(label, raw["check"])
        if "points" in raw and raw["check"]:
            req = "must_submit"
        if raw["header"]:
            state = "instruction"
        elif raw["locked"]:
            state = "locked"
        elif (re.match(r"^(?:scored|submitted)\b", label, re.I) or label in {"✓", "✔", "☑"}
              or raw["check"] and req != "min_score" and label.casefold() != "submit"):
            state = "complete"
        elif not url or (parts.hostname == "canvas.wisc.edu" and parts.path.startswith("/api/")):
            state = "unavailable"
        else:
            state = "pending"
        rows.append(dict(key=key, title=title, url=url, group=raw["group"], kind=raw["kind"],
                         label=label, requirement=req, state=state))
        if "points" in raw:
            rows[-1]["points"] = raw["points"]
    return rows


def course_cards(snapshot):
    by_id = {}
    for card in snapshot["cards"]:
        match = re.fullmatch(r"/courses/([0-9]+)/?", urlsplit(card["href"]).path)
        if not card["href"].startswith(ORIGIN + "/") or not match:
            continue
        course_id = match[1]
        course = by_id.setdefault(course_id, dict(id=course_id, title=card["title"],
                                                  url=card["href"], aliases=[], codes=[]))
        for name in (card["title"], card["alias"]):
            if name and name != course["title"] and name not in course["aliases"]:
                course["aliases"].append(name)
        if card["code"] and card["code"] not in course["codes"]:
            course["codes"].append(card["code"])
    return list(by_id.values())


class CanvasDOM:
    def __init__(self, browser, run_dir):
        self.browser = browser
        self.context = browser.contexts[0]
        self.run_dir = Path(run_dir)
        self.log = RunLog(self.run_dir / "run.jsonl")
        self.snapshots = []
        self._verification_pages = {}
        self._listing_kinds = {}
        self.ended_targets = set()

    def _page(self, path, *, courses=False):
        for page in self.context.pages:
            parts = urlsplit(page.url)
            if not page.url.startswith(ORIGIN + "/"):
                continue
            current = parts.path.rstrip("/") or "/"
            if current == path or (courses and current in COURSE_LISTS):
                return page, False
        if courses:
            for page in self.context.pages:
                if page.url.startswith(ORIGIN + "/") and page.locator(
                    ':is(#nav-tray-portal, [role=dialog], nav[aria-label*="course" i], '
                    '[role=navigation][aria-label*="course" i]) a[href*="/courses/"]:visible'
                ).count():
                    return page, False
        return self._open_listing(path), True

    def _open_listing(self, path):
        session = self.browser.new_browser_cdp_session()
        try:
            with self.context.expect_page(timeout=10000) as new_page:
                session.send("Target.createTarget", {"url": ORIGIN + path, "background": True})
            page = new_page.value
            if page.url == "about:blank":
                page.wait_for_url(lambda url: url != "about:blank", wait_until="domcontentloaded", timeout=15000)
            return page
        finally:
            session.detach()

    def _snapshot(self, page, kind, name, *, refresh=False):
        started = time.monotonic()
        driver = Driver(page, self.log)
        if refresh:
            if not re.fullmatch(re.escape(ORIGIN) + r"/courses/[0-9]+/(?:modules|assignments)/?", page.url):
                raise ValueError("Refresh requires a Canvas course listing.")
            before = driver.shot(self.run_dir / f"before-{kind}-refresh.png")
            if not before.ok:
                raise RuntimeError(before.detail)
            reopened = driver.goto(page.url)
            if not reopened.ok:
                raise RuntimeError(reopened.detail)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        if not driver.settle().ok:
            raise RuntimeError("Canvas did not settle before the DOM snapshot.")
        if driver.auth_required():
            raise RuntimeError("The Toucan profile needs manual Canvas sign-in.")
        if not page.url.startswith(ORIGIN + "/"):
            raise RuntimeError("The listing left Canvas.")
        if kind in {"modules", "assignments"}:
            path = urlsplit(page.url).path.rstrip("/")
            if path != f"/courses/{name}/{kind}":
                if kind != "modules" or path != f"/courses/{name}":
                    raise RuntimeError("Canvas did not display the requested course listing.")
                kind = "navigation"
        if kind == "assignments":
            page.wait_for_function("""() => document.querySelector(
                'li.assignment, .assignment-row, [data-testid="assignment-row"], .ig-header [aria-expanded]')
                || /no assignments|there aren.t any assignments/i.test(document.body.innerText)""", timeout=10000)
            # Past and overdue groups can arrive collapsed even when they contain work.
            toggles = page.locator(':is(#content, main) .ig-header [aria-expanded]:visible, '
                                  ':is(#content, main) :is(h2,h3) [aria-controls][aria-expanded]:visible')
            for toggle in toggles.all():
                if toggle.get_attribute("aria-expanded") == "false":
                    toggle.evaluate("e => e.click()")
                    if toggle.get_attribute("aria-expanded") == "false":
                        raise ValueError("An Assignments group stayed collapsed.")
            if not driver.settle().ok:
                raise RuntimeError("The Assignments listing did not settle.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.run_dir / f"{kind}-{name}-{stamp}.json"
        snapshot = page.locator("body").evaluate(SNAPSHOT, kind)
        snapshot["source"] = "dom"
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.snapshots.append(path)
        self.log.write("snapshot", snapshot["page"], "ok", (time.monotonic() - started) * 1000,
                       source="dom", elements=snapshot["elements"], tier="T1", detail=path.name)
        return snapshot

    def courses(self):
        page, _ = self._page("/courses", courses=True)
        cards = course_cards(self._snapshot(page, "courses", "listing"))
        if not cards and urlsplit(page.url).path.rstrip("/") != "/courses":
            page, _ = self._page("/courses")
            cards = course_cards(self._snapshot(page, "courses", "all"))
        if not cards:
            raise ValueError("No course cards or course links were visible on the listing.")
        return cards

    def modules(self, course_id, *, refresh=False):
        course_id = str(course_id)
        if not re.fullmatch(r"[0-9]+", course_id):
            raise ValueError("Canvas course ID must be numeric.")
        kind = self._listing_kinds.get(course_id, "modules")
        path = f"/courses/{course_id}/{kind}"
        if refresh:
            # The expired popup may itself have redirected to this exact Modules URL.
            # Verify in a listing tab we opened, and reuse it on later items.
            page = self._verification_pages.get(course_id)
            opened = page is None or page.is_closed() or page.url.rstrip("/") != ORIGIN + path
            if opened:
                page = self._verification_pages[course_id] = self._open_listing(path)
        else:
            page, opened = self._page(path)
        snapshot = self._snapshot(page, kind, course_id, refresh=refresh and not opened)
        if kind == "modules" and not snapshot.get("rows") and snapshot.get("assignmentsLink"):
            path = snapshot["assignmentsLink"]
            if path.rstrip("/") != f"/courses/{course_id}/assignments":
                raise ValueError("Assignments navigation left the selected course.")
            page = self._open_listing(path)
            snapshot = self._snapshot(page, "assignments", course_id)
            self._listing_kinds[course_id] = "assignments"
            if refresh:
                self._verification_pages[course_id] = page
        if "rows" not in snapshot:
            raise ValueError("Canvas displayed neither Modules nor an Assignments navigation link.")
        collapsed = [s["title"] for s in snapshot["sections"] if s["collapsed"]]
        if collapsed:
            raise ValueError("The visible list is incomplete. Collapsed sections: " + ", ".join(collapsed))
        if not snapshot["rows"] and not snapshot["empty"]:
            raise ValueError("The course listing contains no visible item rows. Check the saved snapshot.")
        return parse_rows(snapshot, course_id)
