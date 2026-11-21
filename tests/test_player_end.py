import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import Error, sync_playwright

from tasks.queue import load, save
from tools.browser.attach import attach
from tools.browser.captions import CaptionSampler
from tools.browser.driver import Driver, Outcome, RunLog
from tools.browser.player import PlayerEnded, PlayerWatch, player_identity
from tools.browser.run import choose_page
from tools.canvas.dom import CanvasDOM
from tools.canvas.runner import live_players, perform_item, target_id, walk_queue


class EndOfItemTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.folder = Path(scratch.name)
        self.path = self.folder / "queue.json"
        self.log = RunLog(self.folder / "run.jsonl")
        self.dom = Mock(log=self.log, run_dir=self.folder)
        self.page = Mock(url="https://player.test/course?item=3")
        self.page.is_closed.return_value = False
        self.item = dict(key="42:3", title="Module 3", requirement="min_score", status="running", note="",
                         url="https://canvas.wisc.edu/courses/42/modules/items/3", player="target",
                         player_identity=player_identity(self.page.url))
        save(self.path, dict(type="single", items=[self.item]))
        self.row = dict(self.item, group="Program", kind="Assignment", state="pending", label="Score at least 100.0")

    def records(self):
        return [json.loads(line) for line in self.log.path.read_text(encoding="utf-8").splitlines()]

    def exercise_end(self, *, redirect, canvas_complete):
        with patch("tools.browser.captions.CaptionSampler") as sampler, \
             patch("tools.browser.run.hard_block", return_value=""), \
             patch("tools.browser.run.dispatch") as dispatch:
            def listing(course, *, refresh):
                self.assertEqual((course, refresh), ("42", True))
                if self.dom.modules.call_count == 2:
                    sampler.return_value.stop.assert_called_once_with()
                    self.assertEqual(self.records()[-1]["result"], "item-ended")
                    self.assertTrue(load(self.path)["items"][0]["awaiting_verification"])
                    if canvas_complete:
                        return [dict(self.row, state="complete", label="Scored at least 100.0")]
                return [self.row]

            def shot(_driver, path):
                path.write_bytes(b"fixture viewport")
                return Outcome("ok")

            def frames(_page):
                if redirect:
                    return []
                raise Error("Frame.frame_element: Frame has been detached.")

            def decision(*_args, **_kwargs):
                self.page.url = "https://receipt.test/thank-you"
                return dict(action="wait", target="", text="", basis="domain", rule_used=None)

            self.dom.modules.side_effect = listing
            with patch.object(Driver, "shot", shot), patch("tools.browser.driver.visible_frames", side_effect=frames) as read, \
                 patch("tools.canvas.runner.CourseItem.controls", return_value=[]), \
                 patch("tools.browser.run.decide", side_effect=decision) as decide:
                queue, _ = walk_queue(self.path, self.dom, lambda q: {"target": self.page},
                                      lambda i, p, persist: perform_item(None, self.dom, i, p, persist))
            read.assert_called_once_with(self.page)
            self.assertEqual(decide.call_count, int(redirect))
            dispatch.assert_not_called()
            self.assertEqual(self.dom.modules.call_count, 2)
            events = self.records()
            self.assertEqual([e["action"] for e in events if e["result"] == "item-ended"], ["item-ended"])
            self.assertEqual(next(e for e in events if e["result"] == "item-ended")["rule_used"], "L14")
            self.assertFalse({"miss", "handoff", "stall"} & {e["result"] for e in events})
            self.assertEqual(events[-1]["action"], "verify-completion")
            self.assertEqual(queue["items"][0]["status"], "done" if canvas_complete else "blocked")
            self.assertEqual(self.dom.ended_targets, {"target"})

    def test_detached_frame_stops_sampler_and_verifies_without_miss_or_handoff(self):
        self.exercise_end(redirect=False, canvas_complete=True)

    def test_redirect_during_decision_cannot_dispatch_or_grant_unconfirmed_credit(self):
        self.exercise_end(redirect=True, canvas_complete=False)

    def test_resume_retries_canvas_after_redirect_and_interrupted_verification(self):
        self.page.url = "https://player.test/receipt"
        browser = Mock(contexts=[Mock(pages=[self.page])])
        perform = Mock()
        self.dom.modules.side_effect = [Error("Listing unavailable"),
                                        [dict(self.row, state="complete", label="Scored at least 100.0")]]
        with patch("tools.canvas.runner.target_id", return_value="target"):
            with self.assertRaisesRegex(Error, "Listing unavailable"):
                walk_queue(self.path, self.dom, lambda q: live_players(browser, q, self.log), perform)
        self.assertTrue(load(self.path)["items"][0]["awaiting_verification"])
        queue, _ = walk_queue(self.path, self.dom, lambda q: {}, perform)
        perform.assert_not_called()
        self.assertEqual(queue["items"][0]["status"], "done")
        self.assertEqual([e["result"] for e in self.records()], ["item-ended", "ok"])

    def test_redirect_during_initial_canvas_check_cancels_stored_handoff(self):
        self.item["handoff"] = True
        save(self.path, dict(type="single", items=[self.item]))
        browser = Mock(contexts=[Mock(pages=[self.page])])
        def listing(_course, *, refresh):
            self.page.url = "https://receipt.test/thank-you"
            return [self.row]
        self.dom.modules.side_effect = listing
        perform = Mock()
        with patch("tools.canvas.runner.target_id", return_value="target"):
            queue, _ = walk_queue(self.path, self.dom, lambda q: live_players(browser, q, self.log), perform)
        perform.assert_not_called()
        self.assertEqual(queue["items"][0]["status"], "blocked")
        self.assertEqual([e["result"] for e in self.records()], ["item-ended", "blocked"])

    def test_detached_element_in_live_document_retains_normal_retry(self):
        driver = Driver(self.page, self.log)
        driver.player_watch = PlayerWatch(player_identity(self.page.url))
        operation = Mock(side_effect=[Error("Element is not attached to the DOM"), "recovered"])
        with driver.player_watch.observe(self.page):
            self.page.url += "#slide-2"
            self.assertTrue(driver._action("read", "element", operation).ok)
        self.assertEqual(operation.call_count, 2)
        self.assertNotIn("rule_used", self.records()[0])

    def test_verify_uses_its_own_canvas_tab_even_if_player_redirected_to_modules(self):
        url = "https://canvas.wisc.edu/courses/42/modules"
        self.page.url = url
        browser = Mock(contexts=[Mock(pages=[self.page])])
        dom = CanvasDOM(browser, self.folder)
        listing = Mock(url=url)
        listing.is_closed.return_value = False
        with patch.object(dom, "_open_listing", return_value=listing) as open_listing, \
             patch.object(dom, "_snapshot", return_value=dict(sections=[], rows=[], empty=True)) as snapshot:
            dom.modules("42", refresh=True)
            dom.modules("42", refresh=True)
        open_listing.assert_called_once_with("/courses/42/modules")
        self.assertEqual([(c.args[0], c.kwargs["refresh"]) for c in snapshot.call_args_list],
                         [(listing, False), (listing, True)])
        self.assertEqual(self.page.method_calls, [])

    def test_next_item_skips_ended_tab_that_redirected_to_its_assignment(self):
        url = "https://canvas.wisc.edu/courses/42/assignments/4"
        ended, assignment = Mock(url=url), Mock(url=url)
        browser = Mock(contexts=[Mock(pages=[ended, assignment])])
        with patch("tools.browser.run.target_id", side_effect=["expired", "canvas"]):
            self.assertIs(choose_page(browser, url, excluded_targets={"expired"}), assignment)


class BrowserPlayerEventTests(unittest.TestCase):
    def test_real_nested_media_navigation_and_frame_detach(self):
        origin = "https://toucan-player-fixture.test"
        with tempfile.TemporaryDirectory() as scratch, sync_playwright() as pw:
            browser = attach(pw)
            context = browser.contexts[0]
            session = browser.new_browser_cdp_session()
            try:
                with context.expect_page() as opened:
                    session.send("Target.createTarget", {"url": "about:blank", "background": True})
            finally:
                session.detach()
            page = opened.value
            try:
                def serve(route):
                    body = ('<iframe src="/media"></iframe>' if route.request.url.endswith("/launch")
                            else '<video></video><div class="caption-text">Opening line</div>')
                    route.fulfill(content_type="text/html", body=body)
                page.route(origin + "/**", serve)
                for event in ("redirect", "detach"):
                    with self.subTest(event=event):
                        page.goto(origin + "/launch")
                        frame = page.frame(url=origin + "/media")
                        watch = PlayerWatch(player_identity(page.url))
                        log = RunLog(Path(scratch) / event / "run.jsonl")
                        transcript = log.path.with_name("transcript.md")
                        sampler = CaptionSampler(target_id(page), transcript, log, interval=0.1, watch=watch)
                        sampler.start()
                        try:
                            deadline = time.monotonic() + 5
                            while (not transcript.exists() or not transcript.stat().st_size) and time.monotonic() < deadline:
                                time.sleep(0.05)
                            self.assertEqual(transcript.read_text(encoding="utf-8"), "[00:00] Opening line\n")
                            frame.evaluate("location.hash='slide-2'")
                            watch.check(page)
                            if event == "redirect":
                                frame.goto(origin + "/receipt")
                            else:
                                page.locator("iframe").evaluate("el => el.remove()")
                            self.assertTrue(watch.ended.wait(5), "Caption connection missed the end event")
                            sampler.thread.join(5)
                            self.assertFalse(sampler.thread.is_alive())
                            with self.assertRaises(PlayerEnded):
                                watch.check(page)
                            self.assertFalse(log.path.exists(), "Unexpected sampler failure record")
                        finally:
                            sampler.stop()
            finally:
                page.close()


if __name__ == "__main__":
    unittest.main()
