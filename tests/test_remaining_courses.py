import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import sync_playwright
from tasks.queue import mark

from tools.browser.attach import attach
from tools.browser.driver import Driver, Outcome, RunLog
from tools.browser.player import OriginChanged, PlayerWatch, player_identity
from tools.canvas.profile import ProfileAnswers, resolve_personal
from tools.canvas.quiz import attempt, run_quiz
from tools.canvas.runner import CourseItem, open_assignment_player
from tools.canvas.tabs import cleanup


class PlayerChanges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pw = sync_playwright().start()
        cls.addClassCleanup(pw.stop)
        cls.browser = attach(pw)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.ctx = self.browser.contexts[0]
        self.page = self.ctx.new_page()
        self.addCleanup(self.page.close)
        self.log = RunLog(self.root / "run.jsonl")
        self.driver = Driver(self.page, self.log)
        self.item = dict(title="Test",key="42:1",url="https://canvas.wisc.edu/courses/42/assignments/1")
        self.worker = CourseItem(self.item, Mock(), self.page)
        self.profile = self.worker.profile = ProfileAnswers(self.root/"answers.md")

    def test_item_bound_origins_persist_but_unrelated_top_does_not(self):
        urls = [f"https://{part}.toucan-test.invalid" for part in ("launch","content","other")]
        for url in urls:
            self.page.route(url+"/**",lambda r:r.fulfill(content_type="text/html",body="<button>Continue</button>"))
        self.page.goto(urls[0]+"/start?signature=private")
        self.page.set_content(f'<iframe src="{urls[1]}/lesson"></iframe>')
        self.page.frame_locator("iframe").get_by_role("button").wait_for()
        self.worker.remember_popup(self.page)
        self.assertEqual(set(self.item["allowed_origins"]),{"https://canvas.wisc.edu",*urls[:2]})
        watch = PlayerWatch(self.item["player_identity"], self.worker.origins)
        with self.assertRaises(OriginChanged), watch.observe(self.page):
            embedded = next(f for f in self.page.frames if f.parent_frame == self.page.main_frame)
            watch.track_media(embedded)
            self.page.goto(urls[1]+"/activity")
            watch.check(self.page)
            self.assertFalse(watch.ended.is_set())
            self.assertTrue(self.worker.at_item(self.page))
            self.page.goto(urls[2]+"/unrelated")
            watch.check(self.page)
        self.assertNotIn(urls[2],self.item["allowed_origins"])

    def test_named_hotspot_retries_through_pointer_input(self):
        self.page.set_content("""<div role=button onpointerdown="this.dataset.pressed='1'"
            onclick="if(this.dataset.pressed) this.textContent='Opened'">Marker</div>""")
        step, = self.worker.controls(self.driver)
        self.assertTrue(self.driver.click(step["locator"]).ok)
        self.assertEqual(self.page.get_by_role("button").inner_text(),"Opened")
        events=[json.loads(s) for s in self.log.path.read_text().splitlines()]
        self.assertEqual([r["rule_used"] for r in events if r["action"]=="coordinate-retry"],["L13"])

    def test_duplicate_names_select_the_second_control(self):
        self.page.set_content('<button onclick="this.textContent=1">Go to next slide</button>'
                              '<button onclick="this.textContent=2">Go to next slide</button>')
        one, two=self.worker.controls(self.driver)
        self.assertIn("slide #1",one["target"])
        self.assertIn("slide #2",two["target"])
        self.assertTrue(self.driver.click(two["locator"]).ok)
        self.assertEqual(self.page.locator("button").all_inner_texts(),["Go to next slide","2"])

    def test_transparent_storyline_button_uses_its_visible_interactive_artwork(self):
        self.page.set_content("""
            <button id="acc-lesson1" class="acc-shadow-el" style="opacity:0">Start activity</button>
            <button id="acc-old" class="acc-shadow-el" style="opacity:0">Old activity</button>
            <div class="slide-object shown cursor-hover" data-model-id="lesson1"
                 onpointerup="document.querySelector('output').textContent='Opened'">
                <span class="slide-object shown" data-model-id="lesson1" data-acc-text="Start activity">Start activity</span>
            </div><output></output>""")
        steps = self.worker.controls(self.driver)
        self.assertNotIn("Old activity", [s["control_text"] for s in steps])
        start, = [s for s in steps if s["control_text"] == "Start activity"]
        self.assertTrue(self.driver.click(start["locator"]).ok)
        self.assertEqual(self.page.locator("output").inner_text(), "Opened")

    def test_select_all_six_applicable_options_then_continue(self):
        for include_distractor in (False, True):
            opts=[f"Useful {i}" for i in range(6)] + (["Distractor"] if include_distractor else [])
            self.page.set_content("<fieldset><legend>Choose all acceptable techniques</legend>" +
                "".join(f'<label><input type=checkbox>{label}</label>' for label in opts) +
                "</fieldset><button>Continue</button>")
            self.worker.selection_plans.clear()
            def choose(*args, select_labels, **kw):
                return {"labels":[label for label in select_labels if label.startswith("Useful")]}
            with patch("tools.browser.run.decide",side_effect=choose) as decision:
                for _ in range(6):
                    step,=self.worker.select_all_steps(self.driver,self.worker.controls(self.driver),self.driver.read().value,self.root,"")
                    self.assertTrue(self.driver.click(step["locator"]).ok)
                last,=self.worker.select_all_steps(self.driver,self.worker.controls(self.driver),self.driver.read().value,self.root,"")
                self.assertEqual(last["control_text"],"Continue")
                self.assertEqual(self.page.locator("input:checked").count(),6)
                self.assertEqual(decision.call_count,1)

    def test_native_quiz_answers_three_questions_submits_and_reads_score(self):
        base="https://canvas.wisc.edu/courses/42/quizzes/31"
        self.page.route(base+"/take",lambda r:r.fulfill(content_type="text/html",body="""
            <form action="/courses/42/quizzes/31" method="post">
            <fieldset><legend>Do you own a canoe?</legend><label><input required type=radio name=a value=yes>Yes</label>
            <label><input type=radio name=a value=no>No</label></fieldset>
            <fieldset><legend>How many canoes do you own?</legend><select required name=b><option value="">Choose</option>
            <option>3</option><option>0</option></select></fieldset>
            <fieldset><legend>Describe your overall experience</legend><textarea name=c hidden></textarea>
            <iframe title="Answer editor" srcdoc="<body contenteditable=true
              oninput='parent.document.querySelector(&quot;textarea&quot;).value=this.innerText'></body>"></iframe></fieldset>
            <button type=submit>Submit Quiz</button></form>"""))
        posted=[]
        def receipt(r):
            posted.append(r.request.post_data)
            r.fulfill(content_type="text/html",body="<main>Score for this quiz: 3 out of 3. Some questions not yet graded.</main>")
        self.page.route(base,receipt)
        self.page.goto(base+"/take")
        worker=CourseItem(self.item,Mock(),entry_url=self.page.url)
        self.assertTrue(attempt(self.page.url,"42"))
        self.assertFalse(attempt(self.page.url,"12"))
        capture = self.root / "capture"
        capture.mkdir()
        with patch.object(self.driver,"shot",return_value=Outcome("ok")):
            result=run_quiz(self.driver,worker,capture,"",profile=self.profile)
        self.assertTrue(result.ok,result)
        self.assertEqual(len(posted),1)
        self.assertIn("a=no&b=0&c=",posted[0])
        self.assertEqual(self.item["quiz"]["score"],"3 out of 3")
        self.assertTrue(self.item["quiz"]["grading_pending"])
        self.assertEqual(len(self.profile.read()[1]),3)


class PrivateProfileAndCleanup(unittest.TestCase):
    def test_preexisting_vendor_window_must_match_the_assignment_opener_and_title(self):
        assignment="https://canvas.wisc.edu/courses/42/assignments/3"
        item=dict(title="Example", url=assignment)
        def window(url,title,opener):
            page=Mock(url=url)
            page.is_closed.return_value=False
            page.opener.return_value=Mock(url=opener)
            page.title.return_value=title
            return page
        right=window("https://vendor.invalid/course","Course: Example","https://canvas.wisc.edu/courses/42/assignments/3")
        wrong=window("https://vendor.invalid/course","Course: Example","https://canvas.wisc.edu/courses/42/assignments/4")
        other=window("https://vendor.invalid/else","Different course",assignment)
        browser=Mock(contexts=[Mock(pages=[wrong,other,right])])
        self.assertIs(open_assignment_player(browser,item),right)
        browser.contexts[0].pages.append(window("https://vendor.invalid/other","Course: Example",assignment))
        self.assertIsNone(open_assignment_player(browser,item))

    def test_personal_precedence_and_reuse(self):
        with tempfile.TemporaryDirectory() as scratch:
            profile=ProfileAnswers(Path(scratch)/"profile.md")
            profile.record("Question?",["A"])
            self.assertEqual(profile.choose("question?",["B","A"]),["A"])
            self.assertEqual(resolve_personal("How many?",["10","4","1"],{}),["1"])
            self.assertEqual(resolve_personal("Rate your satisfaction",["Strongly disagree","Disagree","Neutral","Agree","Strongly agree"],{}),["Agree"])
            self.assertEqual(resolve_personal("I feel connected",["Strongly disagree","Disagree","Neutral","Agree","Strongly agree"],{}),["Neutral"])

    def test_terminal_cleanup_and_mid_item_protection(self):
        pages=[]
        def tab(name,url):
            p=Mock(url=url); p.name=name; p.is_closed.return_value=False
            p.close.side_effect=lambda **_:pages.remove(p)
            pages.append(p)
            return p
        canvas=tab("canvas","https://canvas.wisc.edu/courses/42")
        blank=tab("blank","about:blank")
        dead=tab("dead","https://vendor.invalid/ended?sign=private")
        old=tab("old","https://vendor.invalid/player?id=same")
        new=tab("new",old.url)
        active=tab("active","https://vendor.invalid/active")
        ctx,log=Mock(pages=pages),Mock()
        def item(tab,status="done"):
            return dict(key=tab.name,player=tab.name,player_identity=player_identity(tab.url),status=status,canvas_verified=True)
        queue={"items":[item(dead,"running"),item(old,"running"),item(active,"running")]}
        for p in (dead,old):
            mark(queue,p.name,"done","Verified on Canvas.")
        self.assertNotIn("player",queue["items"][0])
        self.assertEqual(queue["items"][0]["ended_player"],"dead")
        with patch("tools.canvas.tabs.target_id",side_effect=lambda p:p.name),patch("tools.canvas.tabs.first_navigation",side_effect=lambda p:1 if p is old else 2):
            cleanup(ctx,queue,{"dead","old"},log)
            self.assertEqual(len(pages),6)
            queue["items"][2]["status"]="pending"
            cleanup(ctx,queue,{"dead","old"},log,driven=active)
            self.assertEqual(pages,[canvas,new,active])
            events=[c.args[1] for c in log.write.call_args_list if c.args[0]=="tab-closed"]
            self.assertCountEqual(events,["","vendor.invalid","vendor.invalid"])
            self.assertEqual(log.write.call_args.kwargs["tabs_after"],3)
