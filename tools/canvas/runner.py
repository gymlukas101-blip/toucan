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
from tools.browser.driver import Driver, Outcome, controls_on, keyed_frames, public_url
from tools.browser.launch import apply_running_popup_preference, launch
from tools.browser.player import (OriginChanged, PlayerEnded, exit_document, player_ended,
                                  player_identity as _player_identity, target_id, web_origin)
from tools.browser.run import BLOCKED, choose_page, hard_block, run_loop
from tools.canvas.completion import SUBMIT, mid_positive, quiz_evidence, quiz_summary
from tools.canvas.dom import CanvasDOM, ORIGIN
from tools.canvas.plan import course_order, plan, queue_item, whole_course_row
from tools.canvas.resolve import normalized
from tools.canvas.profile import MULTIPLE, ProfileAnswers, personal_question
from tools.canvas.quiz import attempt
from tools.collect_lessons import records


class ComputerHandoff(Exception):
    pass


def survey_item(item):
    return item.get("requirement") == "must_submit" and (
        item.get("survey") or bool(re.search(r"\bsurvey\b", item.get("title", ""), re.I)))


def handoff(log, url=None):
    log.write("handoff", "player", "handoff", 0, driver="computer", tier="T1", reason="stall", rule_used="L10", url=url)
    print("HANDOFF: switch to @Computer", flush=True)
    raise ComputerHandoff()


def dry_run(dom, request, course, run_dir):
    started = datetime.now(timezone.utc)
    queue, report = plan(request, dom.courses(), lambda cid: dom.modules(cid, refresh=True), course)
    report["started_at"] = started.isoformat()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    decisions = []
    if dom.log.path.exists():
        for event in records(dom.log.path):
            if event.get("action") == "decide" and report["started_at"] <= event["t"] <= report["finished_at"]:
                decisions.append(event["ms"])
    report["model_decisions"] = len(decisions)
    report["mean_decision_ms"] = sum(decisions) / len(decisions) if decisions else None
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
                 f"Read {report['courses_seen']} courses and {report['rows_seen']} course rows from the visible DOM.",
                 f"Model decisions {len(decisions)}. Mean decision latency "
                 + (f"{report['mean_decision_ms']:.1f} ms." if decisions else "N/A."),
                 "", "## Planned order", ""]
        lines.extend(f"{i}. {name}" for i, name in enumerate(report["order"], 1))
        if not report["order"]:
            lines.append("All requested items are complete.")
        lines.extend(["", "## Requested items", ""])
        for listing in report["listings"]:
            for row in listing["rows"]:
                if row["key"] in queue["requested_keys"]:
                    lines.append(f"- {row['title']} — {row['state']}. {row['label'] or 'No label'} "
                                 f"(`{row['requirement']}`).")
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
        course_rows = course_order(dom.modules(cid, refresh=True), queue.get("explicit_order", ()))
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
            if row["url"] in {"", item["url"]}:
                item["canvas_verified"] = True
        if item["status"] in TERMINAL:
            continue
        verifying_end = item.get("awaiting_verification")
        if row and row["url"] in {"", item["url"]} and (row["state"] == "complete" or item.get("quiz", {}).get("canvas_result")):
            mark(queue, item["key"], "done", "Canvas confirmed completion: " + (row["label"] or "checkmark"))
            if item.get("quiz", {}).get("canvas_result"):
                item["note"] = "Canvas posted the quiz score " + item["quiz"]["score"] + "."
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
    for item in queue["items"]:
        if item["status"] in TERMINAL and item.get("canvas_verified") and item.get("ended_player"):
            dom.ended_targets.add(item["ended_player"])
    return deferred


def walk_queue(path, dom, open_players, perform):
    with exclusive(path):
        queue = load(path)
        live = open_players(queue)
        resume(queue, live)
        save(path, queue)
        deferred = _reconcile(queue, dom)
        save(path, queue)
        if isinstance(dom, CanvasDOM):
            dom.cleanup_tabs(queue)
        while item := next_pending(queue):
            key = item["key"]
            if item.get("handoff") and live.get(item.get("player")) and not survey_item(item):
                # The popup can redirect while the initial Modules snapshot is in flight.
                live = open_players(queue)
                if item["player"] in live:
                    print("HANDOFF: switch to @Computer", flush=True)
                    raise ComputerHandoff()
                _item_ended(item, dom.log)
                save(path, queue)
                deferred = _reconcile(queue, dom)
                save(path, queue)
                if isinstance(dom, CanvasDOM):
                    dom.cleanup_tabs(queue)
                continue
            item.pop("handoff", None)
            started = time.monotonic()
            mark(queue, key, "running", "Running the listed item.")
            item.pop("canvas_verified", None)
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
            if outcome.result == "handoff" and survey_item(item):
                outcome = Outcome("blocked", detail="Survey stalled before Canvas confirmed submission.")
            if outcome.result == "handoff":
                if item.get("player") and item["player"] not in open_players(queue):
                    outcome = Outcome("item-ended")
                else:
                    item["handoff"] = True
                    if isinstance(outcome.value, str):
                        item["handoff_url"] = public_url(outcome.value)
                    save(path, queue)
                    handoff(dom.log, item.get("handoff_url"))
            if outcome.result == "item-ended":
                _item_ended(item, dom.log, outcome.detail)
                # A failed listing read can be retried without opening the old player.
                save(path, queue)
            # A player score, closed popup or model decision alone cannot complete a row.
            deferred = _reconcile(queue, dom)
            if item["status"] == "running":
                mark(queue, key, "blocked", "Canvas completion unconfirmed. " + outcome.detail)
            if item.get("canvas_verified") and item.get("ended_player"):
                dom.ended_targets.add(item["ended_player"])
            save(path, queue)
            if isinstance(dom, CanvasDOM):
                dom.cleanup_tabs(queue)
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
            if (_player_identity(page.url) == item["player_identity"] or
                    web_origin(page.url) in item.get("allowed_origins", ()) and not exit_document(page.url)):
                found[tid] = page
            else:
                detail = "The recorded player target left its document."
                if item.get("allowed_origins") and web_origin(page.url) not in item["allowed_origins"]:
                    detail = "The player navigated outside this item's recorded origins."
                    if log:
                        log.write("origin-check", item["title"], "aborted", 0, rule_used="L14", detail=detail)
                _item_ended(item, log, detail)
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
        entry = urlsplit(entry_url or item["url"])
        self.native_quiz = re.fullmatch(r"(/courses/\d+/quizzes/\d+)(?:/take)?/?", entry.path)
        self.native_origin = entry[:2]
        self.launch_popups = []
        self.origins = set(item.get("allowed_origins", ())) | {web_origin(item["url"])}
        self.selection_plans = {}
        self.profile = ProfileAnswers()

    def native_attempt(self, page):
        return self.native_quiz and attempt(page.url, _course_id(self.item)) and urlsplit(page.url).path.rstrip("/") == self.native_quiz[1] + "/take"

    def record_player_origins(self, page, *, launch=False):
        top = web_origin(page.url)
        if not top or (not launch and top not in self.origins):
            raise OriginChanged("The player navigated outside Canvas and this item's recorded origins.")
        observed = {top}
        for f in page.frames:
            if not f.parent_frame or f.is_detached():
                continue
            if page is not self.player and not launch:
                parent = f
                while parent.parent_frame and parent.parent_frame != page.main_frame:
                    parent = parent.parent_frame
                try:
                    if not parent.frame_element().evaluate("e=>!!e.closest('#content,main,[role=main]')"):
                        continue
                except Error:
                    if parent.is_detached():
                        continue
                    raise
            observed.add(web_origin(f.url))
        additions = (observed - {""}) - self.origins
        self.origins.update(additions)
        if additions or "allowed_origins" not in self.item:
            self.item["allowed_origins"] = sorted(self.origins)
            self.persist()
            if self.dom and additions:
                self.dom.log.write("record-origins", self.item["title"], "ok", 0, rule_used="L14",
                                   detail=", ".join(sorted(additions)))

    def _milestone(self, driver, key, value, action, detail):
        quiz = self.item.setdefault("quiz", {})
        if quiz.get(key) != value:
            quiz[key] = value
            self.persist()
            driver.log.write(action, self.item["title"], "ok", 0, tier="T1", source="dom", detail=detail)

    def observe_quiz(self, driver, frames):
        if self.item.get("requirement") == "must_submit" and any(
                c["role"] in {"radio", "checkbox"} for f in frames for c in f.get("controls", [])):
            self.item["survey"] = True
            self._milestone(driver, "reached", True, "quiz-reached", "Visible survey answer groups.")
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
        if step.get("record_personal"):
            self.profile.record(step["question"], [step["control_text"]])

    def record_submit(self, driver, *, dispatched):
        if dispatched:
            self._milestone(driver, "submitted", True, "quiz-submit-clicked", "Submit click dispatched.")
        else:
            self._milestone(driver, "attempted", True, "quiz-submit-attempt", "About to activate the quiz Submit control.")

    def completion_proof(self, driver, frames):
        if driver.page is self.player and not survey_item(self.item):
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
                if web_origin(page.url) not in self.origins or exit_document(page.url):
                    return False
            return parts.scheme in {"http", "https"} and not (
                parts.hostname == "canvas.wisc.edu" and re.match(r"/courses/\d+(?:/|$)", parts.path))
        address = urlsplit(page.url)
        if self.native_quiz and address[:2] == self.native_origin:
            return address.path.rstrip("/") in {self.native_quiz[1], self.native_quiz[1] + "/take"}
        return _player_identity(page.url) == self.entry_identity

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
        if self.dom:
            self.dom.driven_page = page
        self.record_player_origins(page, launch=True)
        self.item.pop("quiz", None)
        self.item.pop("last_slide", None)
        self.persist()

    def controls(self, driver):
        steps = []
        seen = set()
        player = driver.page is self.player
        for frame_key, frame in keyed_frames(driver.page):
            # Canvas also has global navigation and a Next link to other assignments.
            # Only item content buttons and answer choices enter this worker's menu.
            surface = frame.locator("body")
            if not player and frame == driver.page.main_frame:
                content = frame.locator("#content, main, [role=main]").first
                if content.count():
                    surface = content
            controls = controls_on(surface, course=True)
            canvas_chrome = not player and (frame == driver.page.main_frame or web_origin(frame.url) == ORIGIN)
            for control in controls:
                name, role = control["name"], control["role"]
                if control["disabled"] or role not in {"link", "button", "radio", "checkbox", "tab", "menuitem"}:
                    continue
                # A Storyline shadow button can literally be named "[object Object]".
                # Present the matching slide artwork while keeping the browser's actual locator.
                display = control["artwork"] if name == "[object Object]" and control["artwork"] else name
                prompt = control["prompt"]
                if re.search(r"captions?|accessibility|^(?:continue|next|previous|ok|submit|start course)$", display, re.I):
                    prompt = ""
                if (not name or BLOCKED.search(display) or BLOCKED.search(name)
                        or re.search(r"\b(?:complete all items|delete|withdraw)\b", display, re.I)
                        or (canvas_chrome and re.match(r"^(?:next|previous|back|modules?)\b", display, re.I)
                            and not (self.item.get("requirement") == "must_submit" and control["inForm"]
                                     and re.fullmatch(r"next(?: question| page)?", display, re.I)))):
                    continue
                named_launch = re.fullmatch(r"load .+ in a new window", name, re.I)
                if named_launch and normalized(self.item["title"]) not in normalized(name):
                    continue
                opens = not player and (bool(named_launch) or control["newWindow"]
                                        or name.casefold() == "launch course")
                group = control["group"]
                suffix = f" #{control['position']}" if control.get("position") else ""
                label = f"{display}{suffix} [{role}, {frame_key}" + (f", {group}]" if group else "]")
                if label in seen:
                    continue
                seen.add(label)
                steps.append({"action": "play" if name.casefold() in {"play", "resume playback"} else "click",
                              "target": label, "locator": {"name": name, "role": role, "frame": frame_key,
                                                          "position": control.get("position"), "peers": control.get("peerCount"),
                                                          "frame_url": public_url(frame.url),
                                                          "surface": "content" if not player
                                                          and frame == driver.page.main_frame else "body",
                                                          "control_id": control["id"], "group": group,
                                                          "course_launch": opens},
                              "control_text": display, "opens_popup": opens, "prompt": prompt,
                              "question": control["question"], "checked": control.get("checked", False),
                              "toggle": control.get("toggle", False),
                              "is_answer": bool(prompt) or role in {"radio", "checkbox"}})
        if (self.item.get("course_id") == "532394" and normalized(self.item["title"])=="decisions"
                and not self.item.get("intro_video_started")):
            play = [s for s in steps if s["control_text"].casefold()=="play video" and
                    urlsplit(s["locator"]["frame_url"]).hostname=="wisc.prevent.zone"]
            if len(play)==1:
                if driver.media_advancing():
                    self.item["intro_video_started"] = True
                    self.persist()
                else:
                    return [{**play[0], "selection_rule":"L11", "program_intro":True}]
        if self.item.get("requirement") == "must_submit":
            return self.survey_steps(steps)
        personal_groups = {}
        for step in steps:
            loc = step["locator"]
            if loc["role"] == "radio" and personal_question(step["question"]):
                personal_groups.setdefault((loc["frame"], loc["group"]), []).append(step)
        for choices in personal_groups.values():
            if not any(s["checked"] for s in choices):
                pick, = self.profile.choose(choices[0]["question"], [s["control_text"] for s in choices])
                chosen = next(s for s in choices if s["control_text"] == pick)
                return [{**chosen, "locator":{**chosen["locator"], "survey_choice":True},
                         "selection_rule":"L16", "record_personal":True}]
        if self.response_required or any(s["prompt"] and s["prompt"] != self.answered_prompt for s in steps):
            steps = [s for s in steps if not re.fullmatch(r"Next\s*\(Ctrl\+Alt\+Period\)", s["locator"]["name"], re.I)]
        return steps

    def survey_steps(self, steps):
        if self.item.get("quiz", {}).get("submitted"):
            return [s for s in steps if not s["is_answer"] and not SUBMIT.fullmatch(s["control_text"])]
        groups = {}
        for step in steps:
            loc = step["locator"]
            if loc["role"] in {"radio", "checkbox"}:
                loc["survey_choice"] = True
                groups.setdefault((loc["frame"], loc["group"]), []).append(step)
        for choices in groups.values():
            if MULTIPLE.search(choices[0]["question"]) and choices[0]["locator"]["role"] == "checkbox":
                return choices + [s for s in steps if not s["is_answer"]]
            if any(s["checked"] for s in choices):
                continue
            prompt = choices[0]["question"]
            if prompt and personal_question(prompt):
                labels = [s["control_text"] for s in choices]
                pick, = self.profile.choose(prompt, labels)
                matched = next(s for s in choices if s["control_text"] == pick)
                return [{**matched, "selection_rule":"L16", "record_personal":True}]
            if choices[0]["locator"]["role"] == "radio" and (preferred := mid_positive(choices)):
                preferred["survey_preference"] = True
                return [preferred]
            return choices
        # A select-all group can legitimately need more than one option. Keep
        # the remaining choices available alongside Submit until the form posts.
        return [s for s in steps if not s["checked"] and s["locator"]["role"] != "radio"]

    def select_all_steps(self, driver, steps, frames, capture, transcript):
        from tools.browser.run import decide
        groups = {}
        for step in steps:
            loc = step["locator"]
            if loc["role"] == "checkbox" or step.get("toggle"):
                groups.setdefault((loc["frame"], loc["group"]), []).append(step)
        for (frame_key, group), choices in groups.items():
            frame = next((f for f in frames if f["key"] == frame_key), {})
            prompt = choices[0]["question"] or choices[0]["prompt"] or next(
                (s for s in frame.get("text", "").splitlines() if MULTIPLE.search(s)), "")
            if not MULTIPLE.search(prompt):
                continue
            labels = [s["target"] for s in choices]
            key = (frame_key, group, prompt, tuple(labels))
            personal = personal_question(prompt)
            if key not in self.selection_plans:
                if personal:
                    exact = self.profile.choose(prompt, [s["control_text"] for s in choices])
                    picks = [s["target"] for s in choices if s["control_text"] in exact]
                else:
                    task = dict(goal=prompt + " Select all plausible answers.", actions=choices, done_text="")
                    picks = decide(task, capture, [frame], transcript, select_labels=labels)["labels"]
                self.selection_plans[key] = set(picks)
            selected = self.selection_plans[key]
            for step in choices:
                desired = step["target"] in selected
                if step["checked"] != desired:
                    loc = {**step["locator"], "survey_choice": desired, "expected_checked": desired}
                    return [{**step, "locator": loc, "selection_rule": "L16" if personal else "L11"}]
            if personal:
                self.profile.record(prompt, [s["control_text"] for s in choices if s["target"] in selected])
            steps = [s for s in steps if s not in choices]
        return steps


def perform_item(browser, dom, item, player, persist):
    page = player or choose_page(browser, item["url"], excluded_targets=dom.ended_targets)
    dom.driven_page = page
    popups = []
    def listener(opened):
        popups.append(opened)
    page.on("popup", listener)
    try:
        return _perform_item(page, dom, item, player, persist, popups)
    finally:
        page.remove_listener("popup", listener)
        dom.driven_page = None


def _perform_item(page, dom, item, player, persist, popups):
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
        if page.url.startswith(ORIGIN + "/"):
            headings = page.locator('main h1:visible, #content h1:visible, main h2:visible, #content h2:visible').all_inner_texts()
            titles = [re.sub(r"^(?:quiz|survey):\s*", "", s, flags=re.I) for s in [page.title(), *headings]]
            if normalized(item["title"]) not in {normalized(s) for s in titles}:
                return Outcome("aborted", detail="The displayed Canvas title did not match the queued item.")
            base = public_url(page.url).rstrip("/")
            if re.fullmatch(re.escape(ORIGIN) + r"/courses/\d+/quizzes/\d+", base):
                existing = [p for p in page.context.pages if not p.is_closed() and public_url(p.url).rstrip("/") == base+"/take"]
                if len(existing) == 1:
                    page = existing[0]
                    driver = Driver(page, dom.log)
                    dom.log.write("resume-native", item["title"], "ok", 0, rule_used="L11",
                                  detail="Resumed the open attempt for this exact Canvas quiz.")
    worker = CourseItem(item, persist, player, page.url, dom)
    if player is None and web_origin(page.url) != web_origin(item["url"]):
        # The just-opened, verified Canvas item URL itself launched the vendor in place.
        worker.remember_popup(page)
    worker.launch_popups = popups
    goal = (f"Complete only {item['title']}. Use the visible instructions and transcript. "
            "Enable captions, mute and use the player's fullscreen control when available. "
            "Read and answer non-video content in place. Finish required responses before Next.")
    if _course_id(item) == "532394" and normalized(item["title"]) == "decisions":
        goal += (" Play the Introduction video and verify its playhead advances. Follow the six numbered activities "
                 "from Initial Survey through Final Survey in their displayed order as they unlock.")
    if _course_id(item) == "378851":
        goal += (" Wait for the Harassment behaviors marker and open it when it appears. Complete the remaining "
                 "visible markers, then the assessment. Continue past already-played videos.")
    done_text = "A recorded completion indicator on this exact Canvas item."
    if item.get("requirement") == "min_score":
        goal += " Follow the completion quiz through answer selection, Submit and results."
        done_text = ("A posted quiz score on this item's results screen after Submit, "
                     "or a recorded completion indicator on this exact Canvas item.")
    elif item.get("requirement") == "must_submit":
        goal += (" For a survey, answer every displayed group. Prefer Agree or Satisfied, four out of five. "
                 "Read what each scale measures. Answer safety scenarios from the situation described. "
                 "Use the question for factual choices and select every applicable checkbox before Submit. "
                 "Verify submission on Canvas. Answer completion attestations affirmatively.")
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
        apply_running_popup_preference(browser)
        dom = CanvasDOM(browser, folder)
        # attach() has verified the process, loopback endpoint and dedicated profile.
        dom.cleanup_enabled = True
        if args.dry_run:
            _, report = dry_run(dom, args.dry_run, args.course, folder)
            print(json.dumps(report, ensure_ascii=True, indent=2))
            return 0
        try:
            queue, deferred = walk_queue(args.queue, dom, lambda q: live_players(browser, q, dom.log),
                                         lambda i, p, save_state: perform_item(browser, dom, i, p, save_state))
        except ComputerHandoff:
            return 3
        finally:
            dom.cleanup_tabs(load(args.queue))
        lines = ["", "## Execution outcomes", ""]
        for item in queue["items"]:
            evidence = " " + quiz_summary(item) if item["requirement"] == "min_score" else ""
            if item.get("last_slide"):
                evidence += " Last observed slide: " + item["last_slide"] + "."
            lines.append(f"- {item['title']} — {item['status']}. {item['note']}{evidence}")
        lines.extend(f"- {i.get('title', i.get('key'))} — {i['reason']}." for i in deferred)
        with (folder / "report.md").open("a", encoding="utf-8") as report_file:
            report_file.write("\n".join(lines) + "\n")
        from tools.collect_lessons import main as collect_lessons
        collect_lessons(dom.log.path)
        print(json.dumps({"items": queue["items"], "deferred": deferred}, ensure_ascii=True, indent=2))
        return 2 if deferred or any(i["status"] == "blocked" for i in queue["items"]) else 0
