import unittest
from unittest.mock import Mock

from tools.canvas.completion import quiz_evidence
from tools.canvas.runner import CourseItem


class QuizCompletionTests(unittest.TestCase):
    def test_required_response_stays_bound_to_its_slide(self):
        item = dict(title="Module 3", url="https://canvas.wisc.edu/courses/42/assignments/3")
        driver = Mock()
        worker = CourseItem(item, Mock(), driver.page)
        prompt = [dict(text="slide: Reflection\nYou must complete the question before continuing.")]
        worker.observe_quiz(driver, prompt)
        worker.observe_quiz(driver, prompt)
        self.assertTrue(worker.response_required)
        self.assertEqual(item["last_slide"], "Reflection")
        self.assertEqual([c.args[0] for c in driver.log.write.call_args_list], ["slide", "response-required"])
        worker.observe_quiz(driver, [dict(text="slide: Feedback")])
        self.assertFalse(worker.response_required)

    def test_receipt_thresholds_and_another_modules_score_cannot_finish_the_item(self):
        item = dict(key="42:3", title="Module 3", course_id="42", url="https://canvas.wisc.edu/courses/42/modules/items/3")
        dom = Mock()
        dom.modules.return_value = [dict(item, state="pending", label="Score at least 100.0"),
                                    dict(item, key="42:2", state="complete", label="Scored at least 100.0")]
        driver = Mock()
        worker = CourseItem(item, Mock(), driver.page, dom=dom)
        for text in ("Thank you!", "Video complete\n100%", "Passing score: 80%", "Score at least 100.0",
                     "Quiz Results\nPassing score\n80%", "Answer required\nOK"):
            with self.subTest(text=text):
                self.assertEqual(worker.completion_proof(driver, [dict(text=text)]), "")
        self.assertFalse(quiz_evidence([dict(text="Have you ever had thoughts like this?")])[0])
        self.assertFalse(quiz_evidence([dict(text="Course navigation\nCompletion Quiz\n33% COMPLETE")])[0])
        self.assertTrue(quiz_evidence([dict(text="Module 3: Completion Quiz", controls=[dict(name="Start Quiz")])])[0])
        intro = dict(text="Module 3 Completion\nSTART QUIZ\nQuiz Results\nYour score: 0%\nFailed",
                     controls=[dict(name="Start Quiz")])
        self.assertEqual(quiz_evidence([intro]), (True, ""))
        self.assertEqual(worker.completion_proof(driver, [intro]), "")
        self.assertTrue(quiz_evidence([dict(text="Have you completed and understood this module?")])[0])
        self.assertEqual(quiz_evidence([dict(text="Quiz Results\nYour score 100%\nPassing\n80%")]), (True, "100%"))

    def test_quiz_submit_and_score_are_distinct_observations(self):
        item = dict(title="Module 3", url="https://canvas.wisc.edu/courses/42/assignments/3")
        driver, persist = Mock(), Mock()
        worker = CourseItem(item, persist, driver.page)
        submit = dict(locator=dict(name="Submit (Ctrl+Alt+S)"))
        self.assertFalse(worker.is_quiz_submit(submit))
        worker.observe_quiz(driver, [dict(text="Have you completed this module?")])
        self.assertTrue(worker.is_quiz_submit(submit))
        self.assertFalse(worker.is_quiz_submit(dict(locator=dict(name="Next"))))
        score = [dict(text="Quiz Results\n100%\nPassed\n80% passing")]
        self.assertEqual(worker.completion_proof(driver, score), "")
        worker.record_submit(driver, dispatched=False)
        self.assertEqual(worker.completion_proof(driver, score), "")
        worker.record_submit(driver, dispatched=True)
        worker.observe_quiz(driver, score)
        worker.observe_quiz(driver, score)
        self.assertIn("100%", worker.completion_proof(driver, score))
        self.assertEqual(worker.completion_proof(driver, [dict(text="Thank you!")]), "")
        self.assertEqual([c.args[0] for c in driver.log.write.call_args_list],
                         ["quiz-reached", "quiz-submit-attempt", "quiz-submit-clicked", "quiz-score"])
