import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tasks.queue import load, save
from tools.browser.captions import CaptionSampler
from tools.browser.driver import Driver, Outcome, RunLog
from tools.browser.run import run_loop
from tools.canvas.runner import ComputerHandoff, canvas_command, walk_queue


class CaptionAndHandoffTests(unittest.TestCase):
    def test_handoff_records_the_current_public_driver_url(self):
        with tempfile.TemporaryDirectory() as folder:
            log = RunLog(Path(folder) / "run.jsonl")
            driver = Driver(Mock(url="https://player.example/course?example=private#slide"), log)
            self.assertEqual(driver.handoff_url(), "https://player.example/course")
            record = json.loads(log.path.read_text())
            self.assertEqual(record["url"], "https://player.example/course")
            self.assertNotIn("private", json.dumps(record))

    def test_sampler_writes_while_decision_is_blocked_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "transcript.md"
            log = RunLog(Path(folder) / "run.jsonl")
            sampler = CaptionSampler("fixture", path, log, interval=0.2)
            readings = iter([{"text": "one", "time": 0}, {"text": "one", "time": 1},
                             {"text": "two", "time": 2}, {"text": "three", "time": 3}])
            def read(_page, _watch):
                return next(readings, {"text": "three", "time": 3})
            with patch("tools.browser.captions._read", side_effect=read):
                thread = threading.Thread(target=sampler._sample, args=(object(),))
                thread.start()
                self.assertTrue(sampler.ready.wait(1))
                time.sleep(2)
                sampler.stop_event.set()
                thread.join()
            self.assertEqual(path.read_text(encoding="utf-8").splitlines(),
                             ["[00:00] one", "[00:02] two", "[00:03] three"])

    def test_handoff_keeps_running_and_resume_does_not_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "queue.json"
            log = RunLog(Path(folder) / "run.jsonl")
            item = dict(key="42:3", title="Module 3", url="https://canvas.wisc.edu/courses/42/modules/items/3",
                        requirement="min_score", status="pending", note="")
            queue = dict(type="single", items=[item], courses=[dict(id="42")], requested_keys=["42:3"])
            save(path, queue)
            dom = Mock(log=log)
            def perform(current, _player, persist):
                current.update(player="target", player_identity="identity")
                persist()
                return Outcome("handoff", detail="stall")
            with patch("tools.canvas.runner._reconcile", return_value=[]):
                with self.assertRaises(ComputerHandoff):
                    walk_queue(path, dom, lambda q: {"target": object()} if q["items"][0].get("player") else {}, perform)
                self.assertEqual(load(path)["items"][0]["status"], "running")
                worker = Mock()
                with self.assertRaises(ComputerHandoff):
                    walk_queue(path, dom, lambda _q: {"target": object()}, worker)
                worker.assert_not_called()
            record = json.loads((Path(folder) / "run.jsonl").read_text(encoding="utf-8"))
            self.assertEqual((record["result"], record["reason"], record["driver"]),
                             ("handoff", "stall", "computer"))
            args = SimpleNamespace(queue=path, run_dir=Path(folder), dry_run=None, course=None)
            with patch("tools.canvas.runner.run_power_settings"), patch("tools.canvas.runner.sync_playwright"), \
                 patch("tools.canvas.runner.launch"), patch("tools.canvas.runner.attach"), \
                 patch("tools.canvas.runner.CanvasDOM"), \
                 patch("tools.canvas.runner.walk_queue", side_effect=ComputerHandoff):
                self.assertEqual(canvas_command(args), 3)
        driver = Mock(unsafe=True)
        driver.assert_progress.side_effect = [Outcome("stall"), Outcome("stall")]
        worker = Mock()
        worker.player = None
        def check_progress(_task, checked, *_args, **_kwargs):
            self.assertEqual(checked.assert_progress(5).result, "stall")
            checked.assert_progress(5)
        with patch("tools.browser.run._run_loop", side_effect=check_progress):
            result = run_loop({}, driver, Path("unused"), Path("unused"), course_item=worker)
        self.assertEqual(result.result, "handoff")
        driver.handoff_url.assert_called_once_with()
        worker.item = {"title": "Post-Survey", "requirement": "must_submit"}
        driver.assert_progress.side_effect = [Outcome("stall"), Outcome("stall")]
        with patch("tools.browser.run._run_loop", side_effect=check_progress):
            result = run_loop({}, driver, Path("unused"), Path("unused"), course_item=worker)
        self.assertEqual(result.result, "blocked")
        driver.handoff_url.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
