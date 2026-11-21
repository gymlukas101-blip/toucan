import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import Error, sync_playwright

from tools.browser.attach import attach
from tools.browser.driver import Driver, RunLog
from tools.browser.player import LessonChanged, PlayerEnded, PlayerWatch, player_identity, wait_for_player
from tools.browser.run import fast_button
from tools.canvas.runner import CourseItem


class CourseTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pw = sync_playwright().start()
        cls.addClassCleanup(pw.stop)
        cls.browser = attach(pw)

    def setUp(self):
        session = self.browser.new_browser_cdp_session()
        try:
            with self.browser.contexts[0].expect_page() as created:
                session.send("Target.createTarget", {"url": "about:blank", "background": True})
        finally:
            session.detach()
        self.page = created.value
        self.addCleanup(self.page.close)
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.driver = Driver(self.page, RunLog(Path(scratch.name) / "run.jsonl"))
        item = dict(title="Module 3", url="https://canvas.wisc.edu/courses/42/assignments/3")
        self.worker = CourseItem(item, lambda: None, self.page)

    def test_launch_redirect_video_to_quiz_and_final_exit(self):
        origin = "https://toucan-course-transition.test"
        documents = {
            "/launch": "<body>Opening your course<script>setTimeout(()=>location.replace('/player'),500)</script>",
            "/player": '<iframe name="scormdriver_content" src="/course"></iframe>',
            "/course": '<h1>Module 3</h1><iframe name="activity" src="/video"></iframe>',
            "/video": "<video></video><button>Start Course</button>",
            "/end": "<body>Thank you!</body>",
        }
        page = self.page
        page.route(origin + "/**", lambda r: r.fulfill(content_type="text/html",
                                                       body=documents[r.request.url.removeprefix(origin)]))
        page.goto(origin + "/launch")
        wait_for_player(page, timeout=5)
        self.assertEqual(page.url, origin + "/player")
        watch = PlayerWatch(player_identity(page.url))
        with self.assertRaises(PlayerEnded), watch.observe(page):
            watch.track_media(page.frame(name="activity"))
            course = page.frame(name="scormdriver_content")
            course.locator("body").evaluate("el => el.innerHTML='<h1>Completion Quiz</h1><button>Submit</button>'")
            watch.check(page)
            with self.assertRaises(LessonChanged):
                watch.check(page, Error("Frame has been detached"))
            self.assertTrue(course.get_by_role("button", name="Submit").is_visible())
            page.goto(origin + "/end")
            watch.check(page)
        with self.assertRaisesRegex(PlayerEnded, "exit receipt"):
            wait_for_player(page, timeout=3)

    def test_storyline_accessible_control_clicks_its_matching_artwork(self):
        self.page.set_content("""<style>button,.slide-object {position:absolute;left:20px;top:140vh;width:120px;height:50px}
            button {pointer-events:none}</style>
            <button class="acc-shadow-el" id="acc-choice" aria-label="Choice"></button>
            <div class="slide-object" data-model-id="choice" onclick="this.dataset.clicked='yes'">Choice</div>""")
        control = dict(role="button", name="Choice")
        shape = self.page.locator(".slide-object")
        self.assertTrue(self.driver.click(control).ok)
        self.assertEqual(shape.get_attribute("data-clicked"), "yes")
        shape.evaluate("el => {el.dataset.modelId='other';delete el.dataset.clicked}")
        self.assertFalse(self.driver.click(control).ok)
        self.assertIsNone(shape.get_attribute("data-clicked"))

    def test_question_requires_a_response_before_the_generic_next_button(self):
        self.page.set_content("""<p class="slide-object shown" data-acc-text="Have you had thoughts like this?"
            data-model-id="question">Have you had thoughts like this?</p>
            <div class="slide-object shown" data-acc-text="Yes" data-model-id="yes">Yes</div>
            <div class="slide-object shown" data-acc-text="No" data-model-id="no">No</div>
            <button class="acc-shadow-el" id="acc-yes">Yes</button>
            <button class="acc-shadow-el" id="acc-no">No</button>
            <button>Next (Ctrl+Alt+Period)</button>""")
        choices = self.worker.controls(self.driver)
        self.assertEqual([s["control_text"] for s in choices], ["Yes", "No"])
        self.assertTrue(all(s["is_answer"] for s in choices))
        self.worker.answered(choices[1])
        self.assertIn("Next (Ctrl+Alt+Period)", [s["control_text"] for s in self.worker.controls(self.driver)])

    def test_unlabelled_storyline_button_uses_the_text_on_its_artwork(self):
        self.page.set_content("""<div class="slide-object shown" data-acc-text="CONTINUE" data-model-id="c">CONTINUE</div>
            <button class="acc-shadow-el" id="acc-c">[object Object]</button>""")
        step, = self.worker.controls(self.driver)
        self.assertEqual(step["control_text"], "CONTINUE")
        self.assertTrue(step["target"].startswith("CONTINUE ["))
        self.assertEqual(step["locator"]["name"], "[object Object]")

    def test_only_the_active_quiz_card_contributes_score_and_controls(self):
        self.page.set_content("""<div class="quiz__card quiz__card--active"><h1>Module 3 Completion</h1>
            <button>START QUIZ</button></div><div class="quiz__card" inert>
            <h1>Quiz Results</h1><p>Your score: 0%</p><button>TAKE AGAIN</button></div>""")
        frame, = self.driver.read().value
        self.assertIn("Module 3 Completion", frame["text"])
        self.assertNotIn("Your score", frame["text"])
        self.assertEqual([c["name"] for c in frame["controls"]], ["START QUIZ"])
        self.assertEqual([s["control_text"] for s in self.worker.controls(self.driver)], ["START QUIZ"])

    def test_quiz_answer_registers_through_its_associated_label(self):
        self.page.set_content("""<style>input{pointer-events:none;opacity:0} label{display:block;width:300px}</style>
            <label><input type="radio" name="q"><span>I have completed this module.</span></label>
            <label><input type="radio" name="q"><span>I have not completed this module.</span></label>
            <button>Submit</button>""")
        frames = self.driver.read().value
        self.assertEqual([c["role"] for c in frames[0]["controls"]], ["radio", "radio", "button"])
        self.assertIsNone(fast_button(frames, [dict(action="click", target="Submit")]))
        answer = dict(role="radio", name="I have completed this module.")
        self.assertTrue(self.driver.click(answer).ok)
        self.assertTrue(self.page.get_by_role("radio", name=answer["name"], exact=True).is_checked())
        self.assertFalse(self.page.get_by_role("radio", name="I have not completed this module.", exact=True).is_checked())

    def test_rise_start_course_link_is_in_the_player_menu(self):
        self.page.set_content("""<a class="overview__button" href="#/lessons/example">START COURSE</a>
            <a href="https://example.org">University Health Services</a>""")
        steps = self.worker.controls(self.driver)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["locator"]["role"], "link")
        self.assertEqual(steps[0]["locator"]["name"], "START COURSE")

    def test_course_controls_filter_prose_and_navigation(self):
        self.page.set_content("""<nav><a class="btn" href="#home">Home</a></nav>
            <p><a href="#reference">Reference</a> and <a href="#citation">Citation</a></p>
            <button>Continue</button><a class="cta" href="#exit">Exit course</a>""")
        self.assertEqual([s["control_text"] for s in self.worker.controls(self.driver)], ["Continue"])
        self.page.set_content('<a href="#go">Begin</a>')
        self.assertEqual([s["control_text"] for s in self.worker.controls(self.driver)], ["Begin"])
        self.page.set_content('<a href="#go">Begin</a><div tabindex="0">Next card</div>')
        self.assertEqual([s["control_text"] for s in self.worker.controls(self.driver)], ["Next card"])

    def test_clicks_open_shadow_controls_and_handler_divs(self):
        self.page.set_content("""<div id=host></div><div onclick="this.textContent='Done'">Reveal</div>
            <script>host.attachShadow({mode:'open'}).innerHTML =
                '<button onclick="this.textContent=123">Play clip</button>'</script>""")
        steps = {s["control_text"]: s for s in self.worker.controls(self.driver)}
        self.assertEqual(set(steps), {"Play clip", "Reveal"})
        for name in steps:
            result = self.driver.click(steps[name]["locator"])
            self.assertTrue(result.ok, result)
        self.assertEqual(self.page.get_by_role("button").inner_text(), "123")
        self.assertTrue(self.page.get_by_text("Done", exact=True).is_visible())

    def test_survey_answers_each_group_then_records_submit(self):
        self.worker.item["requirement"] = "must_submit"
        levels = ["Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"]
        self.page.set_content("".join("<fieldset><legend>" + group + "</legend>" + "".join(
            f'<label><input type=radio name="{group}">{option}</label>' for option in levels) + "</fieldset>"
            for group in ("Materials", "Pacing")) +
            "<fieldset><legend>Topics covered</legend><label><input type=checkbox>Practice</label></fieldset>"
            "<button>Submit Survey</button>")
        self.worker.observe_quiz(self.driver, self.driver.read().value)
        for group in ("Materials", "Pacing"):
            choice, = self.worker.controls(self.driver)
            self.assertEqual((choice["control_text"], choice["question"]), ("Agree", group))
            self.assertTrue(self.driver.click(choice["locator"]).ok)
        choice, = self.worker.controls(self.driver)
        self.assertEqual(choice["control_text"], "Practice")
        self.assertTrue(self.driver.click(choice["locator"]).ok)
        submit, = self.worker.controls(self.driver)
        self.assertTrue(self.worker.is_quiz_submit(submit))
        self.assertEqual(self.page.locator("input:checked").count(), 3)
        self.worker.record_submit(self.driver, dispatched=True)
        self.assertEqual(self.worker.controls(self.driver), [])

    def test_duplicate_frame_names_dispatch_to_the_frame_that_advertised_the_control(self):
        self.page.set_content('<iframe name="ScormContent" srcdoc="outer"></iframe>')
        outer = self.page.frame(name="ScormContent")
        outer.set_content("""<button>Outer</button><iframe name="ScormContent"
            srcdoc="<button onclick='this.textContent=123'>Launch Course</button>"></iframe>""")
        inner = outer.child_frames[0]
        inner.get_by_role("button", name="Launch Course").wait_for()
        steps = {s["control_text"]: s for s in self.worker.controls(self.driver)}
        keys = [steps[n]["locator"]["frame"] for n in ("Outer", "Launch Course")]
        self.assertEqual(keys, ["ScormContent#1", "ScormContent#2"])
        result = self.driver.click(steps["Launch Course"]["locator"])
        self.assertTrue(result.ok, result)
        self.assertEqual(inner.get_by_role("button").inner_text(), "123")
