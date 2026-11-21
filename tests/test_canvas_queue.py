"""DOM navigation, queue persistence and completion proofs. Uses only local fixtures."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tasks.queue import exclusive, load, mark, next_pending, resume, save
from tools.browser.driver import Outcome, RunLog
from tools.canvas.dom import CanvasDOM, ORIGIN, course_cards, requirement
from tools.canvas.plan import plan
from tools.canvas.resolve import Ambiguous, resolve
from tools.canvas.runner import CourseItem, _player_identity, dry_run, live_players, perform_item, run_power_settings, walk_queue


COURSE = dict(id="42", title="U Got This! | Fall", url=ORIGIN + "/courses/42", aliases=[], codes=[])
MKT = dict(id="43", title="Marketing Foundations", url=ORIGIN + "/courses/43", aliases=[], codes=["MKT 300"])


def row(n, title, *, state="pending", label="Score at least 100.0", cid="42", group="Program"):
    url = f"{ORIGIN}/courses/{cid}/modules/items/{n}" if state not in {"locked", "instruction"} else ""
    return dict(key=f"{cid}:{n}", title=title, url=url, label=label, requirement=requirement(label),
                state=state, group=group, kind="Assignment")


def listing():
    return [row(1, "Instructions", state="instruction", label=""),
            row(2, "Module 1", state="complete", label="Submitted"),
            row(3, "Module 3: Test"),
            row(4, "Module 4: Resources and Help", state="locked"),
            row(5, "Post-Survey", state="locked", label="Submit"),
            row(6, "Glossary", label="", group="Optional: Extra")]


class Listing:
    def __init__(self, folder, rows):
        self.rows = rows
        self.log = RunLog(folder / "run.jsonl")
        self.lookups = []

    def courses(self):
        return [COURSE]

    def modules(self, cid, *, refresh=False):
        self.lookups.append((cid, refresh))
        return copy.deepcopy(self.rows)


class CanvasQueueTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.folder = Path(scratch.name)
        self.dom = Listing(self.folder, listing())
        self.path = self.folder / "queue.json"

    def test_requirement_vocabulary(self):
        cases = {"Submitted": "must_submit", "Score at least 9": "min_score", "Scored at least 9.5": "min_score",
                 "Must view": "mark_done", "Mark as done": "mark_done", "Submit": "unknown", "": "unknown"}
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(requirement(label), expected)
        self.assertEqual(requirement("", True), "mark_done")

    def test_course_resolution_and_close_scores(self):
        self.assertEqual(resolve("42", [MKT, COURSE]), COURSE)
        self.assertEqual(resolve("U Got This", [MKT, COURSE]), COURSE)
        self.assertEqual(resolve("mkt300", [MKT, COURSE]), MKT)
        self.assertEqual(resolve("Market", [MKT, COURSE]), MKT)
        self.assertEqual(resolve("MKT 3", [MKT, COURSE]), MKT)
        with self.assertRaises(Ambiguous):
            resolve("MKT 300", [dict(COURSE, title="MKT 300 Advanced"), MKT])
        self.assertEqual(resolve("Marketing", [dict(COURSE, codes=["Marketing"]), MKT])["id"], "42")
        self.assertEqual(resolve("Foundations", [MKT, COURSE]), MKT)
        similar = dict(COURSE, id="44", title="U Got This! | Spring")
        with self.assertRaises(Ambiguous) as caught:
            resolve("U Got This", [similar, COURSE])
        self.assertEqual(len(caught.exception.options), 2)
        with self.assertRaises(ValueError):
            resolve("Unlisted", [MKT, COURSE])
        with self.assertRaises(Ambiguous):
            resolve("advanced people policies", [dict(MKT, title="Campus People Policies"),
                                                 dict(COURSE, title="Department People")])

    def test_card_aliases_and_course_links(self):
        def card(title, href):
            return dict(title=title, href=href, alias="", code="")
        raw = dict(cards=[card("U Got This!", COURSE["url"]), card("UHS", COURSE["url"] + "/"),
                          card("Foreign", "https://elsewhere.invalid/courses/42"),
                          card("Assignment", COURSE["url"] + "/assignments/9")])
        cards = course_cards(raw)
        self.assertEqual(len(cards), 1)
        self.assertEqual(resolve("UHS", cards)["id"], "42")

    def test_course_all_and_multi_respect_list_order(self):
        queue, report = plan("complete every module in U Got This", [COURSE], self.dom.modules)
        self.assertEqual(queue["type"], "course_all")
        self.assertEqual([i["key"] for i in queue["items"]], ["42:3"])
        self.assertEqual(len(report["locked"]), 2)
        self.dom.rows[3] = row(4, "Module 4: Resources and Help")
        queue, _ = plan("do Module 4 and Module 3", [COURSE], self.dom.modules, "42")
        self.assertEqual(queue["type"], "multi")
        self.assertEqual([i["key"] for i in queue["items"]], ["42:3", "42:4"])
        single, _ = plan("complete Module 4: Resources and Help", [COURSE], self.dom.modules, "42")
        self.assertEqual(single["type"], "single")
        self.assertEqual(len(self.dom.lookups), 3)
        with self.assertRaises(ValueError):
            plan("complete Module 30", [COURSE], self.dom.modules, "42")

    def test_multi_course_reads_each_listing_once(self):
        observed = []
        def read(cid):
            observed.append(cid)
            return listing() if cid == "42" else [row(8, "Lecture", cid="43")]
        queue, report = plan("do Module 3 in U Got This and Lecture in MKT 300", [COURSE, MKT], read)
        self.assertEqual(observed, ["42", "43"])
        self.assertEqual([i["key"] for i in queue["items"]], ["42:3", "43:8"])
        self.assertEqual(report["rows_seen"], 7)

    def test_visible_prerequisite_overrides_reversed_list_and_request(self):
        for instruction in ("Complete Module 4 after Module 3.", "Before Module 4, finish Module 3.",
                            "After Module 3, complete Module 4."):
            with self.subTest(instruction=instruction):
                self.dom.rows = [row(1, instruction, state="instruction", label=""),
                                 row(4, "Module 4: Resources"), row(3, "Module 3: Test")]
                queue, _ = plan("do Module 4 and Module 3", [COURSE], self.dom.modules, "42")
                self.assertEqual([i["key"] for i in queue["items"]], ["42:3", "42:4"])

    def test_dry_run_persists_without_execution_or_overwrite(self):
        queue, report = dry_run(self.dom, "do Module 3 and Module 4", "U Got This", self.folder)
        self.assertEqual(self.dom.lookups, [("42", True)])
        self.assertEqual(load(self.path), queue)
        self.assertEqual(len(report["order"]), 1)
        self.assertEqual(report["deferred"], [{"title": "Module 4: Resources and Help", "reason": "locked"}])
        with self.assertRaises(ValueError):
            dry_run(self.dom, "complete Module 3", "42", self.folder)
        self.assertEqual(load(self.path), queue)

    def test_atomic_state_resume_and_exclusive_claim(self):
        queue, _ = dry_run(self.dom, "complete Module 3", "42", self.folder)
        item = next_pending(queue)
        item.update(status="running", player="target")
        self.assertEqual(next_pending(resume(queue, {"target"}))["status"], "running")
        self.assertEqual(next_pending(resume(queue, {"other"}))["status"], "pending")
        with exclusive(self.path):
            with self.assertRaises(RuntimeError):
                with exclusive(self.path):
                    pass
            mark(queue, item["key"], "skipped", "Owner skipped item.")
            save(self.path, queue)
        self.assertIsNone(next_pending(load(self.path)))

    def test_resume_binds_item_even_when_two_urls_share_path(self):
        queue, _ = dry_run(self.dom, "complete Module 3", "42", self.folder)
        item = next_pending(queue)
        page = Mock(url="https://player.invalid/course?item=3#slide-2")
        item.update(status="running", player="target", player_identity=_player_identity(page.url))
        browser = Mock(contexts=[Mock(pages=[page])])
        with patch("tools.canvas.runner.target_id", return_value="target"):
            self.assertEqual(live_players(browser, queue), {"target": page})
            page.url = "https://player.invalid/course?item=4"
            self.assertEqual(live_players(browser, queue), {})

    def test_worker_stops_on_other_canvas_item_or_player_return(self):
        item = listing()[2]
        page = Mock(url=item["url"])
        page.is_closed.return_value = False
        worker = CourseItem(item, lambda: None)
        self.assertTrue(worker.at_item(page))
        page.url = ORIGIN + "/courses/42/modules/items/5"
        self.assertFalse(worker.at_item(page))
        worker.player = page
        self.assertFalse(worker.at_item(page))
        page.url = "https://fixture.invalid/session#slide"
        self.assertTrue(worker.at_item(page))

    def test_power_settings_restore_when_the_run_stops(self):
        with patch("tools.canvas.runner.subprocess.run") as command:
            with self.assertRaises(ValueError):
                with run_power_settings():
                    raise ValueError("fixture stop")
            self.assertEqual([c.args[0][-2:] for c in command.call_args_list],
                             [["monitor-timeout-ac", "0"], ["standby-timeout-ac", "0"],
                              ["monitor-timeout-ac", "15"], ["standby-timeout-ac", "30"]])

    def test_walk_verifies_completion_then_discovers_unlocked_next(self):
        dry_run(self.dom, "do Module 3 and Module 4", "42", self.folder)
        dispatched = []
        def work(item, player, persist):
            self.assertEqual(load(self.path)["items"][-1]["status"], "running")
            dispatched.append(item["key"])
            if item["key"] == "42:3":
                self.dom.rows[2] = row(3, "Module 3: Test", state="complete", label="Scored at least 100.0")
                self.dom.rows[3] = row(4, "Module 4: Resources and Help")
            return Outcome("ok", detail="Worker saw the end screen.")
        queue, deferred = walk_queue(self.path, self.dom, lambda q: {}, work)
        self.assertEqual(dispatched, ["42:3", "42:4"])
        self.assertEqual([i["status"] for i in queue["items"]], ["done", "blocked"])
        self.assertEqual(deferred, [])
        self.assertEqual(load(self.path), queue)
        self.assertIsNone(next_pending(queue))

    def test_walk_continues_after_failure_without_relaunching_live_player(self):
        self.dom.rows[3] = row(4, "Module 4: Resources and Help")
        queue, _ = dry_run(self.dom, "do Module 3 and Module 4", "42", self.folder)
        queue["items"][0].update(status="running", player="real-target")
        save(self.path, queue)
        calls = []
        def work(item, page, persist):
            calls.append(page)
            if item["key"] == "42:3":
                raise RuntimeError("Fixture player stalled")
            self.dom.rows[3] = row(4, "Module 4: Resources and Help", state="complete", label="Submitted")
            return Outcome("ok")
        result, _ = walk_queue(self.path, self.dom, lambda q: {"real-target": "same-open-page"}, work)
        self.assertEqual(calls, ["same-open-page", None])
        self.assertEqual([r["status"] for r in result["items"]], ["blocked", "done"])


class BackgroundSnapshotTests(unittest.TestCase):
    def test_rendered_cards_rows_and_icons_in_a_disposable_tab(self):
        from playwright.sync_api import sync_playwright
        from tools.browser.attach import attach

        origin = "https://toucan-dom-fixture.test"
        courses = """<main><div class="ic-DashboardCard">
          <a href="/courses/42"><span class="ic-DashboardCard__header-title">Marketing</span>
          <span class="ic-DashboardCard__header-subtitle">MKT 300</span></a></div></main>
          <nav aria-label="Courses"><a href="/courses/43">Second course</a><a href="https://other.invalid/courses/11">Foreign</a></nav>
          <nav><a href="/courses/99">Home</a></nav>"""
        def item(n, title, req="", icon="", css="", klass=""):
            return f"""<li id="context_module_item_{n}" class="context_module_item {klass}">
                <span class=item_name><a class="ig-title title" style="{css}" href="/courses/42/modules/items/{n}">{title}</a></span>
                <div class=requirement-description><span hidden>Submitted</span>{req}</div>
                <div class=module-item-status-icon>{icon}</div></li>"""
        body = "".join([item(1, "Scored", "Scored at least 1.0"),
                        item(2, "Due", "Score at least 1.0"),
                        item(3, "Checked", icon='<i class="icon-check" style="display:inline-block;width:12px;height:12px"></i>'),
                        item(4, "Locked", icon='<i title="Locked" class="icon-lock">lock</i>'),
                        item(5, "Disabled link", css="color:gray"), item(6, "Instruction", klass="context_module_sub_header"),
                        item(7, "Still needs a score", "Score at least 2", icon='<i class="icon-check">✓</i>'),
                        item(8, "Unknown", "Submit"), item(9, "View lesson", "Must view"), item(10, "Confirm", "Mark as done")])
        modules = ('<main><section class="context_module"><h2>Program</h2><ul>' + body
                   + '</ul></section><button>Complete All Items</button><button>Next</button>'
                   + '<button>Play</button><button>Load a different item in a new window</button></main>')
        with tempfile.TemporaryDirectory() as folder, sync_playwright() as pw:
            browser = attach(pw)
            ctx = browser.contexts[0]
            session = browser.new_browser_cdp_session()
            try:
                with ctx.expect_page() as opened:
                    session.send("Target.createTarget", {"url": "about:blank", "background": True})
            finally:
                session.detach()
            page = opened.value
            try:
                paths = []
                def serve(route):
                    paths.append(route.request.url)
                    html = courses if route.request.url.endswith("/courses") else modules
                    route.fulfill(content_type="text/html", body=html)
                page.route(origin + "/**", serve)
                page.add_init_script("window.fixtureClicks=0; addEventListener('click',()=>window.fixtureClicks++)")
                dom = CanvasDOM(browser, Path(folder))
                with patch("tools.canvas.dom.ORIGIN", origin):
                    page.goto(origin + "/courses")
                    cards = dom.courses()
                    self.assertEqual([r["id"] for r in cards], ["42", "43"])
                    self.assertEqual(cards[0]["codes"], ["MKT 300"])
                    self.assertEqual(page.evaluate("window.fixtureClicks"), 0)
                    page.goto(origin + "/courses/42/modules")
                    rows = dom.modules("42")
                self.assertEqual([r["state"] for r in rows], ["complete", "pending", "complete", "locked", "locked",
                                                               "instruction", "pending", "pending", "pending", "pending"])
                self.assertEqual([r["requirement"] for r in rows[-3:]], ["unknown", "mark_done", "mark_done"])
                self.assertEqual(rows[2]["requirement"], "mark_done")
                allowed = CourseItem(rows[1], lambda: None).controls(Mock(page=page))
                self.assertEqual([(s["action"], s["locator"]["name"]) for s in allowed], [("play", "Play")])
                self.assertEqual(page.evaluate("window.fixtureClicks"), 0)
                self.assertEqual(paths, [origin + "/courses", origin + "/courses/42/modules"])
                self.assertEqual(len(dom.snapshots), 2)
                self.assertTrue(all(p.is_file() for p in dom.snapshots))
                # Exercise the real page worker without activating a control. A worker's
                # "done" decision must return to the separate completion check.
                item = dict(rows[1], url=page.url)
                def pixels(driver, path):
                    path.write_bytes(b"local fixture viewport")
                    return Outcome("ok")
                finish = dict(action="done", target="", text="", basis="domain")
                with patch("tools.browser.driver.Driver.shot", pixels), patch("tools.browser.run.decide", return_value=finish):
                    completed = perform_item(browser, dom, item, None, lambda: None)
                self.assertTrue(completed.ok, completed)
                self.assertIn("Canvas verification is pending", completed.detail)
                self.assertEqual(page.evaluate("window.fixtureClicks"), 0)
            finally:
                page.close()


if __name__ == "__main__":
    unittest.main()
