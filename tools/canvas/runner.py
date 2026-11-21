"""Preview or walk DOM-derived queues using the existing page-scoped browser worker."""

import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error, sync_playwright

from tasks.queue import TERMINAL, exclusive, load, mark, next_pending, resume, save
from tools.browser.attach import attach
from tools.browser.driver import Driver, Outcome, public_url, visible_frames
from tools.browser.launch import launch
from tools.browser.player import PlayerEnded, player_ended, player_identity as _player_identity, target_id
from tools.browser.run import BLOCKED, choose_page, hard_block, run_loop
from tools.canvas.completion import SUBMIT, quiz_evidence, quiz_summary
from tools.canvas.dom import CanvasDOM, ORIGIN
from tools.canvas.plan import course_order, plan, queue_item, whole_course_row
from tools.canvas.resolve import normalized


class ComputerHandoff(Exception):
    pass


def handoff(log):
    log.write("handoff", "player", "handoff", 0, driver="computer", tier="T1", reason="stall", rule_used="L10")
    print("HANDOFF: switch to @Computer", flush=True)
    raise ComputerHandoff()


def dry_run(dom, request, course, run_dir):
    started = datetime.now(timezone.utc)
    queue, report = plan(request, dom.courses(), lambda cid: dom.modules(cid, refresh=True), course)
    report["started_at"] = started.isoformat()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    run_dir = Path(run_dir)
    path = run_dir / "queue.json"
    with exclusive(path):
        if path.exists():
            raise ValueError(f"A queue already exists in {run_dir}. Choose a new dry-run directory.")
        (run_dir / "dry-run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        names = ", ".join(c["title"] for c in queue["courses"])
        lines = ["# Canvas queue preview", "", f"Resolved {names}.",
                 f"Started {report['started_at']}. Finished {report['finished_at']}.",
                 "Dry run complete. No coursework, questions or quiz score in this run.",
                 f"Read {report['courses_seen']} courses and {report['rows_seen']} module rows from the visible DOM.",
                 "", "## Planned order", ""]
        lines.extend(f"{i}. {name}" for i, name in enumerate(report["order"], 1))
        if not report["order"]:
            lines.append("There are no runnable items.")
        lines.extend(["", "## Requested items", ""])
        for listing in report["listings"]:
            for row in listing["rows"]:
                if row["key"] in queue["requested_keys"]:
                    lines.append(f"- {row['title']} — {row['state']}. Dry run only.")
        if report["locked"]:
            lines.extend(["", "## Locked rows", "", *["- " + s for s in report["locked"]]])
        (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        save(path, queue)
    return queue, report


def _course_id(item):
    cid = item.get("course_id") or item["key"].partition(":")[0]
    if not str(cid).isdigit():
        match = re.match(r"/courses/(\d+)(?:/|$)", urlsplit(item["url"]).path)
        if not match:
            raise ValueError(f"Missing Canvas course for {item['title']}")
        cid = match[1]
    return str(cid)


def _item_ended(item, log, detail="The recorded player target left its document."):
    if not item.get("awaiting_verification"):
        item["awaiting_verification"] = True
        if item.get("player"):
            item["ended_player"] = item["player"]
        item.pop("handoff", None)
        if log is not None:
            log.write("item-ended", item["title"], "item-ended", 0, tier="T1", rule_used="L14", detail=detail)


def _reconcile(queue, dom):
    """Read each course once. Reapply the saved request so newly unlocked rows can join."""
    started = time.monotonic()
    dom.ended_targets = {i["ended_player"] for i in queue["items"] if i.get("ended_player")}
    ids = [c["id"] for c in queue.get("courses", [])] or list(dict.fromkeys(_course_id(i) for i in queue["items"]))
    requested = set(queue.get("requested_keys", []))
    entire_course = queue["type"] == "course_all" and queue.get("request")
    known = {i["key"] for i in queue["items"]}
    rows, deferred = [], []
    for cid in ids:
        course_rows = course_order(dom.modules(cid, refresh=True))
        rows.extend(course_rows)
        for row in course_rows:
            if entire_course and whole_course_row(row) and row["key"] not in requested:
                queue.setdefault("requested_keys", []).append(row["key"])
                requested.add(row["key"])
            if row["key"] not in requested:
                continue
            if row["state"] in {"locked", "unavailable"}:
                deferred.append({"title": row["title"], "reason": row["state"]})
            elif row["state"] == "pending" and row["key"] not in known:
                queue["items"].append(queue_item(row, cid))
                known.add(row["key"])
    by_key = {row["key"]: row for row in rows}
    deferred.extend({"key": key, "reason": "missing from the listing"} for key in sorted(requested - by_key.keys()))
    by_url = {row["url"]: row for row in rows if row["url"]}
    observed = {}
    order = {r["key"]: n for n, r in enumerate(rows)}
    for item in queue["items"]:
        row = by_key.get(item["key"]) or by_url.get(item["url"])
        if row:
            observed[item["key"]] = row
        if item["status"] in TERMINAL:
            continue
        verifying_end = item.get("awaiting_verification")
        if row and row["state"] == "complete" and row["url"] in {"", item["url"]}:
            mark(queue, item["key"], "done", "Canvas confirmed completion: " + (row["label"] or "checkmark"))
        elif verifying_end:
            evidence = "row missing" if not row else f"{row['state']} ({row['label'] or 'no status label'})"
            if row and row["url"] and row["url"] != item["url"]:
                evidence = "item link changed"
            mark(queue, item["key"], "blocked", f"The player ended. Canvas completion unconfirmed: {evidence}.")
        elif not row or row["state"] != "pending" or row["url"] != item["url"]:
            reason = row["state"] if row else "missing from the listing"
            mark(queue, item["key"], "blocked", f"Item unavailable or link changed ({reason}).")
        if verifying_end:
            result = "ok" if item["status"] == "done" else "blocked"
            dom.log.write("verify-completion", item["title"], result, (time.monotonic() - started) * 1000,
                          source="dom", tier="T1", detail=item["note"])
    queue["items"].sort(key=lambda i: order.get(observed.get(i["key"], {}).get("key"), len(rows)))
    return deferred


def walk_queue(path, dom, open_players, perform):
    with exclusive(path):
        queue = load(path)
        live = open_players(queue)
        resume(queue, live)
        save(path, queue)
        deferred = _reconcile(queue, dom)
        save(path, queue)
        while item := next_pending(queue):
            key = item["key"]
            if item.get("handoff") and live.get(item.get("player")):
                # The popup can redirect while the initial Modules snapshot is in flight.
                live = open_players(queue)
                if item["player"] in live:
                    print("HANDOFF: switch to @Computer", flush=True)
                    raise ComputerHandoff()
                _item_ended(item, dom.log)
                save(path, queue)
                deferred = _reconcile(queue, dom)
                save(path, queue)
                continue
            item.pop("handoff", None)
            started = time.monotonic()
            mark(queue, key, "running", "Running the listed item.")
            save(path, queue)
            page = live.get(item.get("player"))
            try:
                outcome = perform(item, page, lambda: save(path, queue))
            except PlayerEnded as end:
                outcome = Outcome("item-ended", detail=str(end))
            except Error as error:
                if item.get("player") and player_ended(page, item.get("player_identity"), error):
                    outcome = Outcome("item-ended")
                else:
                    outcome = Outcome("aborted", detail="The course page became unavailable during the item.")
            except (OSError, RuntimeError, ValueError) as error:
                outcome = Outcome("aborted", detail=str(error))
            if outcome.result == "handoff":
                if item.get("player") and item["player"] not in open_players(queue):
                    outcome = Outcome("item-ended")
                else:
                    item["handoff"] = True
                    save(path, queue)
                    handoff(dom.log)
            if outcome.result == "item-ended":
                _item_ended(item, dom.log, outcome.detail)
                # A failed listing read can be retried without opening the old player.
                save(path, queue)
            # A player score, closed popup or model decision alone cannot complete a row.
            deferred = _reconcile(queue, dom)
            if item["status"] == "running":
                mark(queue, key, "blocked", "Canvas completion unconfirmed. " + outcome.detail)
            save(path, queue)
            if outcome.result != "item-ended":
                dom.log.write("queue_item", item["title"], "ok" if item["status"] == "done" else "miss",
                              (time.monotonic() - started) * 1000, source="dom", detail=item["note"])
        return queue, deferred


def live_players(browser, queue, log=None):
    expected = {i["player"]: i for i in queue["items"] if i["status"] == "running" and i.get("player")
                and not i.get("awaiting_verification")}
    found = {}
    if expected:
        for page in browser.contexts[0].pages:
            tid = target_id(page)
            item = expected.get(tid)
            if not item or not item.get("player_identity"):
                continue
            if _player_identity(page.url) == item["player_identity"]:
                found[tid] = page
            else:
                _item_ended(item, log)
    return found


class CourseItem:
    """Bind the current row to its content controls and any player it actually opens."""

    def __init__(self, item, persist, player=None, entry_url=None, dom=None):
        self.item = item
        self.persist = persist
        self.player = player
        self.dom = dom
        self.response_required = False
        self.answered_prompt = ""
        self.entry_identity = _player_identity(entry_url or item["url"])

    def _milestone(self, driver, key, value, action, detail):
        quiz = self.item.setdefault("quiz", {})
        if quiz.get(key) != value:
            quiz[key] = value
            self.persist()
            driver.log.write(action, self.item["title"], "ok", 0, tier="T1", source="dom", detail=detail)

    def observe_quiz(self, driver, frames):
        if driver.page is self.player:
            for frame in frames:
                text = frame["text"]
                slide = re.search(r"^slide:[ \t]*([^\n]+)", text, re.I | re.M)
                if slide and (name := slide[1].strip()[:160]) != self.item.get("last_slide"):
                    self.response_required = False
                    self.answered_prompt = ""
                    self.item["last_slide"] = name
                    self.persist()
                    driver.log.write("slide", self.item["title"], "ok", 0, tier="T1", source="dom", detail=name)
                if (not self.response_required and re.search(r"you must (?:complete|answer|select|enter) "
                        r"[^.\n]{0,100}before (?:continuing|going on|moving on)|response (?:is )?required",
                        text, re.I)):
                    self.response_required = True
                    driver.log.write("response-required", self.item["title"], "ok", 0, tier="T1", source="dom",
                                     detail=self.item.get("last_slide", "Current activity"))
            reached, score = quiz_evidence(frames)
            if reached:
                self._milestone(driver, "reached", True, "quiz-reached", "Visible completion quiz or quiz results.")
            if score and self.item.get("quiz", {}).get("submitted"):
                self._milestone(driver, "score", score, "quiz-score", score)

    def is_quiz_submit(self, step):
        name = step.get("control_text") or step.get("locator", {}).get("name", "")
        return self.item.get("quiz", {}).get("reached") and SUBMIT.fullmatch(name)

    def answered(self, step):
        self.response_required = False
        self.answered_prompt = step.get("prompt", "")

    def record_submit(self, driver, *, dispatched):
        if dispatched:
            self._milestone(driver, "submitted", True, "quiz-submit-clicked", "Submit click dispatched.")
        else:
            self._milestone(driver, "attempted", True, "quiz-submit-attempt", "About to activate the quiz Submit control.")

    def completion_proof(self, driver, frames):
        if driver.page is self.player:
            _, score = quiz_evidence(frames)
            if score and self.item.get("quiz", {}).get("submitted"):
                return f"Quiz submitted. Visible player score {score}. Canvas verification is pending."
        # The Canvas row can confirm an auto-submission or one made before a resume.
        if self.dom:
            for row in self.dom.modules(_course_id(self.item), refresh=True):
                if (row["key"] == self.item["key"] and row["url"] in {"", self.item["url"]}
                        and row["state"] == "complete"):
                    return "Canvas confirmed completion: " + (row["label"] or "checkmark")
        return ""

    def at_item(self, page):
        if page.is_closed():
            return False
        if page is self.player:
            parts = urlsplit(page.url)
            if self.item.get("player_identity") and _player_identity(page.url) != self.item["player_identity"]:
                return False
            return parts.scheme in {"http", "https"} and not (
                parts.hostname == "canvas.wisc.edu" and re.match(r"/courses/\d+(?:/|$)", parts.path))
        return _player_identity(page.url) == self.entry_identity

    def media_advancing(self, driver):
        playing = any(f.evaluate("() => [...document.querySelectorAll('video,audio')].some(m => !m.paused && !m.ended)")
                      for f in visible_frames(driver.page))
        return playing and driver.assert_progress(5).ok

    def remember_popup(self, page):
        if urlsplit(page.url).scheme not in {"https", "http"}:
            raise ValueError("The course popup did not reach a web page.")
        identity = _player_identity(page.url)
        self.player = page
        try:
            target = target_id(page)
        except Error as error:
            if player_ended(page, identity, error):
                raise PlayerEnded("The player ended during launch. Verify completion on Canvas.") from error
            raise
        self.item.update(player=target, player_identity=identity)
        self.item.pop("quiz", None)
        self.item.pop("last_slide", None)
        self.persist()

    def controls(self, driver):
        steps = []
        seen = set()
        player = driver.page is self.player
        for frame in visible_frames(driver.page):
            # Canvas also has global navigation and a Next link to other assignments.
            # Only item content buttons and answer choices enter this worker's menu.
            surface = frame.locator("body")
            if not player and frame == driver.page.main_frame:
                content = frame.locator("#content, main, [role=main]").first
                if content.count():
                    surface = content
            controls = surface.evaluate("""el => {
                const shapes = [...el.querySelectorAll('.slide-object.shown[data-acc-text]')].filter(e =>
                    e.checkVisibility({opacityProperty:true}) && e.getClientRects().length);
                const question = shapes.map(e => e.dataset.accText.trim()).find(t => t.endsWith('?')) || '';
                const artwork = new Map(shapes.map(e => ['acc-' + e.dataset.modelId, e.dataset.accText.trim()]));
                return Array.from(el.querySelectorAll(
                'button,[role=button],[role=radio],[role=checkbox],input[type=submit],input[type=radio],input[type=checkbox]'
            )).filter(c => c.checkVisibility() && c.getClientRects().length && !c.disabled
                && !c.closest('[inert]') && !c.closest('.quiz__card:not(.quiz__card--active)')
                && c.getAttribute('aria-disabled') !== 'true').map(c => ({
                name: (c.getAttribute('aria-labelledby') || '').split(/ +/).filter(Boolean)
                            .map(id => document.getElementById(id)?.innerText || '').join(' ').trim()
                    || c.getAttribute('aria-label') || Array.from(c.labels || []).map(l => l.innerText).join(' ').trim()
                    || c.innerText?.trim() || c.title || c.value || '',
                role: c.getAttribute('role') || (['radio','checkbox'].includes(c.type) ? c.type : 'button'),
                artwork: artwork.get(c.id) || '',
                prompt: c.classList.contains('acc-shadow-el') && artwork.has(c.id) ? question : ''
            }))}""")
            for control in controls:
                name, role = control["name"], control["role"]
                # A Storyline shadow button can literally be named "[object Object]".
                # Present the matching slide artwork while keeping the browser's actual locator.
                display = control["artwork"] if name == "[object Object]" and control["artwork"] else name
                prompt = control["prompt"]
                if re.search(r"captions?|accessibility|^(?:continue|next|previous|ok|submit|start course)$", display, re.I):
                    prompt = ""
                if (not name or BLOCKED.search(display) or re.search(r"\b(?:complete all items|delete|withdraw)\b", display, re.I)
                        or (not player and re.match(r"^(?:next|previous|back|modules?)\b", display, re.I))):
                    continue
                opens = bool(re.fullmatch(r"load .+ in a new window", name, re.I))
                if opens and normalized(self.item["title"]) not in normalized(name):
                    continue
                frame_key = frame.name or public_url(frame.url)
                label = f"{display} [{role}, {frame_key}]"
                if label in seen:
                    continue
                seen.add(label)
                steps.append({"action": "play" if name.casefold() in {"play", "resume playback"} else "click",
                              "target": label, "locator": {"name": name, "role": role, "frame": frame_key,
                                                          "course_launch": opens},
                              "control_text": display, "opens_popup": opens, "prompt": prompt,
                              "is_answer": bool(prompt) or role in {"radio", "checkbox"}})
        if self.response_required or any(s["prompt"] and s["prompt"] != self.answered_prompt for s in steps):
            steps = [s for s in steps if not re.fullmatch(r"Next\s*\(Ctrl\+Alt\+Period\)", s["locator"]["name"], re.I)]
        return steps


def perform_item(browser, dom, item, player, persist):
    page = player or choose_page(browser, item["url"], excluded_targets=dom.ended_targets)
    driver = Driver(page, dom.log)
    if not player and page.url != item["url"]:
        opened = driver.goto(item["url"])
        if not opened.ok:
            return opened
    if not player:
        settled = driver.settle()
        if not settled.ok:
            return settled
        if reason := hard_block(driver):
            return Outcome("aborted", detail=reason)
        if urlsplit(page.url)[:2] != urlsplit(item["url"])[:2]:
            return Outcome("aborted", detail="The item link redirected to a different origin.")
        if page.url.startswith(ORIGIN + "/"):
            headings = page.locator('main h1:visible, #content h1:visible, main h2:visible, #content h2:visible').all_inner_texts()
            if normalized(item["title"]) not in {normalized(s) for s in [page.title(), *headings]}:
                return Outcome("aborted", detail="The displayed Canvas title did not match the queued item.")
    worker = CourseItem(item, persist, player, page.url, dom)
    goal = (f"Complete only {item['title']}. Use the visible instructions and transcript. "
            "Enable captions, mute and use the player's fullscreen control when available. "
            "Read and answer non-video content in place. Finish required responses before Next.")
    done_text = "A recorded completion indicator on this exact Canvas item."
    if item.get("requirement") == "min_score":
        goal += " Follow the completion quiz through answer selection, Submit and results."
        done_text = ("A posted quiz score on this item's results screen after Submit, "
                     "or a recorded completion indicator on this exact Canvas item.")
    task = dict(url=page.url, goal=goal + " A Thank you or exit screen carries no credit.", done_text=done_text, actions=[])
    directory = dom.run_dir / ("item-" + re.sub(r"[^a-zA-Z0-9_-]", "_", item["key"])[:100])
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as capture:
        return run_loop(task, driver, directory / "transcript.md", Path(capture), course_item=worker)


@contextmanager
def run_power_settings():
    def ac(setting, minutes):
        subprocess.run(["powercfg", "/change", setting, str(minutes)], check=True)
    try:
        ac("monitor-timeout-ac", 0)
        ac("standby-timeout-ac", 0)
        yield
    finally:
        try:
            ac("monitor-timeout-ac", 15)
        finally:
            ac("standby-timeout-ac", 30)


def canvas_command(args):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    default = args.queue.parent if args.queue else Path(os.environ["LOCALAPPDATA"]) / "Toucan/runs" / ("plan-" + stamp)
    folder = args.run_dir or default
    with run_power_settings(), sync_playwright() as pw:
        launch()
        browser = attach(pw)
        dom = CanvasDOM(browser, folder)
        if args.dry_run:
            _, report = dry_run(dom, args.dry_run, args.course, folder)
            print(json.dumps(report, ensure_ascii=True, indent=2))
            return 0
        try:
            queue, deferred = walk_queue(args.queue, dom, lambda q: live_players(browser, q, dom.log),
                                         lambda i, p, save_state: perform_item(browser, dom, i, p, save_state))
        except ComputerHandoff:
            return 3
        lines = ["", "## Execution outcomes", ""]
        for item in queue["items"]:
            evidence = " " + quiz_summary(item) if item["requirement"] == "min_score" else ""
            if item.get("last_slide"):
                evidence += " Last observed slide: " + item["last_slide"] + "."
            lines.append(f"- {item['title']} — {item['status']}. {item['note']}{evidence}")
        lines.extend(f"- {i.get('title', i.get('key'))} — {i['reason']}." for i in deferred)
        with (folder / "report.md").open("a", encoding="utf-8") as report_file:
            report_file.write("\n".join(lines) + "\n")
        print(json.dumps({"items": queue["items"], "deferred": deferred}, ensure_ascii=True, indent=2))
        return 2 if deferred or any(i["status"] == "blocked" for i in queue["items"]) else 0
