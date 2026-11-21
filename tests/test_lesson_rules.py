import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools import collect_lessons
from tools.lessons import lesson_rules


class LessonRuleTests(unittest.TestCase):
    def test_retirement_counts_successful_decisions_across_runs_and_breaks_ties_by_id(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            rules = root / "LESSONS.md"
            entries = [f"- [L{i:02d}] Rule {i}\n  Evidence {i}.\n" for i in range(1, 15)]
            rules.write_text("# Rules\n\n" + "".join(entries), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "limit is 12"):
                lesson_rules(rules.read_text(encoding="utf-8"))
            current = root / "run.jsonl"
            current.write_text('{"result":"ok","rule_used":"L01"}\n{"result":"miss"}\n', encoding="utf-8")
            history = root / "other"
            history.mkdir()
            (history / "run.jsonl").write_text("\n".join(
                json.dumps(dict(result="ok", rule_used=f"L{i:02d}")) for i in range(5, 15)), encoding="utf-8")
            output = io.StringIO()
            with patch.object(collect_lessons, "LESSONS", rules), patch.dict("os.environ", LOCALAPPDATA=scratch), \
                 redirect_stdout(output):
                self.assertEqual(collect_lessons.main(current, prune=True, history=[root]), 0)
            kept = rules.read_text(encoding="utf-8")
            self.assertEqual(set(lesson_rules(kept)), {"L01", "L04", *(f"L{i:02d}" for i in range(5, 15))})
            self.assertNotIn("  Evidence 2.", kept)
            self.assertNotIn("  Evidence 3.", kept)
            self.assertIn("Rules used in this run: L01 x1", output.getvalue())
            self.assertIn("Retired L02 with 0 recorded uses", output.getvalue())

    def test_active_lessons_require_distinct_stable_ids(self):
        with self.assertRaisesRegex(ValueError, "Duplicate lesson ID"):
            lesson_rules("- [L03] First\n- [L03] Second\n")
        with self.assertRaisesRegex(ValueError, "Each active lesson"):
            lesson_rules("- Anonymous lesson\n")
