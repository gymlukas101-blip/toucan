import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from tools.browser.driver import Outcome, RunLog
from tools.browser.observation import write_observation
from tools.browser.run import DECISION_SECONDS, decide, fast_button, read_transcript, run_loop


def status(action):
    return dict(action=action, target="", text="", basis="domain", rule_used=None)


class RunnerSpeedTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.folder = Path(scratch.name)
        self.transcript = self.folder / "transcript.md"
        self.button = dict(role="button", name="Continue")
        self.url = "https://fixture.test/lesson"
        self.frame = dict(name="lesson", url=self.url, text="Lesson", controls=[self.button])
        self.task = dict(url=self.url, goal="Continue to the end", done_text="Finished",
                         actions=[dict(action="click", target="Continue")])
        self.driver = Mock(player_watch=None, log=RunLog(self.folder / "run.jsonl"))
        self.driver.page.url = self.url
        self.driver.media_advancing.return_value = False
        self.driver.read.side_effect = lambda: Outcome("ok", [self.frame])
        self.driver.click.side_effect = self.finish
        def shot(path):
            path.write_bytes(b"fixture viewport")
            return Outcome("ok")
        self.driver.shot.side_effect = shot

    def finish(self, _target):
        self.frame = dict(self.frame, text="Finished", controls=[])
        return Outcome("ok")

    def drive(self, decide_fn):
        with patch("tools.browser.run.decide", side_effect=decide_fn) as self.model, \
             patch("tools.browser.run.time.sleep") as self.sleep, patch("tools.browser.run.hard_block", return_value=""):
            return run_loop(self.task, self.driver, self.transcript, self.folder)

    def events(self):
        return [json.loads(s) for s in self.driver.log.path.read_text(encoding="utf-8").splitlines()]

    def test_single_button_uses_no_model_call_to_click(self):
        def only_at_end(_task, _capture, frames, *_args, **_options):
            self.assertEqual(frames[0]["text"], "Finished")
            return status("done")
        self.assertTrue(self.drive(only_at_end).ok)
        self.driver.click.assert_called_once_with("Continue")
        self.model.assert_called_once()
        fast, = [e for e in self.events() if e["action"] == "fast-path"]
        self.assertEqual((fast["tier"], fast["rule_used"]), ("T0", "L15"))

    def test_only_a_unique_non_answer_button_in_the_menu_qualifies(self):
        menu = self.task["actions"]
        for extra in (dict(role="radio", name="A"), dict(role="checkbox", name="B"),
                      dict(role="button", name="Other"), dict(role="button", name="")):
            with self.subTest(extra=extra):
                self.assertIsNone(fast_button([dict(self.frame, controls=[self.button, extra])], menu))
        self.assertIsNone(fast_button([self.frame], [dict(menu[0], is_answer=True)]))
        self.assertIsNone(fast_button([self.frame], [dict(menu[0], target="Elsewhere")]))
        self.assertIsNone(fast_button([self.frame], [dict(menu[0], locator=dict(name="Continue", frame="other"))]))

    def test_timeout_reobserves_before_fast_click(self):
        self.frame["controls"].append(dict(role="button", name="Other"))
        def timeout_then_finish(_task, _capture, frames, *_args, **_options):
            if frames[0]["text"] == "Finished":
                return status("done")
            self.assertEqual(len(frames[0]["controls"]), 2)
            self.frame = dict(self.frame, controls=[self.button])
            raise subprocess.TimeoutExpired("codex", DECISION_SECONDS)
        self.assertTrue(self.drive(timeout_then_finish).ok)
        self.driver.click.assert_called_once_with("Continue")
        self.assertEqual([(e["action"], e["result"]) for e in self.events()],
                         [("decide", "timeout"), ("fast-path", "ok"), ("decide", "ok")])

    def test_timeout_without_fast_path_stalls_without_clicking(self):
        self.frame["controls"] = [dict(role="radio", name="A"), dict(role="radio", name="B"), self.button]
        result = self.drive(subprocess.TimeoutExpired("codex", DECISION_SECONDS))
        self.assertEqual(result.result, "stall")
        self.driver.click.assert_not_called()
        self.assertEqual(sum(e["result"] == "stall" for e in self.events()), 3)

    def test_transcript_change_does_not_rearm_clicked_frame(self):
        def append_caption(_target):
            self.transcript.write_text("[00:01] New caption", encoding="utf-8")
            return Outcome("ok")
        self.driver.click.side_effect = append_caption
        self.assertEqual(self.drive(lambda *a, **kw: status("wait")).result, "stall")
        self.driver.click.assert_called_once_with("Continue")

    def test_advancing_media_schedules_a_45_second_poll(self):
        self.frame["controls"] = []
        def progress():
            self.finish(None)
            return True
        self.driver.media_advancing.side_effect = progress
        self.assertTrue(self.drive([status("wait"), status("done")]).ok)
        self.assertAlmostEqual(self.sleep.call_args_list[-1].args[0], 45, delta=.25)

    def test_model_payload_shrinks_and_preserves_the_last_120_complete_lines(self):
        png = self.folder / "viewport.png"
        Image.effect_noise((1920, 1080), 80).convert("RGB").save(png)
        original = png.read_bytes()
        captions = [f"[00:00] {i} " + "é" * 500 for i in range(185)]
        self.transcript.write_text("\n".join(captions), encoding="utf-8-sig")
        tail = "\n".join(captions[-120:])
        self.assertEqual(read_transcript(self.transcript), tail)
        self.assertEqual(write_observation(self.folder, [self.frame], "\n".join(captions)), tail)
        evidence = json.loads((self.folder / "observation.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["transcript"], tail)
        jpg = self.folder / "viewport.jpg"
        with Image.open(jpg) as image:
            self.assertEqual((image.format, image.size), ("JPEG", (1024, 576)))
        self.assertLess(jpg.stat().st_size, len(original) // 2)
        self.assertEqual(png.read_bytes(), original)

    def test_codex_process_receives_remaining_45_second_budget(self):
        Image.new("RGB", (32, 24)).save(self.folder / "viewport.png")
        self.assertEqual(DECISION_SECONDS, 45)
        run_process = subprocess.run
        def slow_child(_args, **options):
            return run_process([sys.executable, "-c", "import time; time.sleep(8)"], **options)
        with patch("tools.browser.run.shutil.which", return_value="codex"), \
             patch("tools.browser.run.DECISION_SECONDS", .3), \
             patch("tools.browser.run.subprocess.run", side_effect=slow_child) as launch:
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                decide(self.task, self.folder, [self.frame], "")
            self.assertLess(time.monotonic() - started, 4)
        self.assertGreater(launch.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(launch.call_args.kwargs["timeout"], .3)
