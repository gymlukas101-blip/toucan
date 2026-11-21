"""Complete a Canvas classic quiz using its rendered form and posted results."""

import re
import subprocess
import time
from urllib.parse import urlsplit

from playwright.sync_api import Error

from tools.browser.driver import Outcome
from tools.canvas.profile import ProfileAnswers, clean, personal_question


ATTEMPT = re.compile(r"(/courses/(\d+)/quizzes/\d+)/take/?$")
POSTED = re.compile(r"\b(?:score for this (?:quiz|attempt|survey)|(?:quiz|attempt|survey) score|"
                    r"you scored)\s*:?\s*(\d+(?:\.\d+)?\s*(?:(?:/|out of)\s*\d+(?:\.\d+)?|%))", re.I)
FIELDS = 'input[type=radio],input[type=checkbox],select,textarea,input[type=text],[contenteditable=true],iframe'
BLOCKS = ".display_question:visible, .question_holder:visible > .question:visible"


def attempt(url, course_id):
    address = urlsplit(url)
    match = ATTEMPT.fullmatch(address.path)
    return match if address.hostname == "canvas.wisc.edu" and match and match[2] == str(course_id) else None


def rendered_fields(block):
    return block.locator(FIELDS).evaluate_all("""fields=>fields.flatMap((e,i)=>{
        const visible=n=>n?.checkVisibility() && n.getClientRects().length;
        const label=[...e.labels||[]].find(visible);
        if(e.disabled || !(visible(e)||label))return [];
        const type=e.tagName==='SELECT'?'select':e.matches('input[type=checkbox],input[type=radio]')?e.type:'text';
        const text=(label?.innerText || e.getAttribute('aria-label') ||
            e.closest('.answer')?.querySelector('.answer_text,.answer_label')?.innerText || '').trim();
        return [{i,type,name:e.name||'',text,checked:e.checked||false,
            options:type==='select'?[...e.options].filter(o=>!o.disabled && o.value!=='' && !/^\\s*\\[?choose\\b/i.test(o.text)).map(o=>o.label.trim()):[]}];
    })""")


def fill_block(block, question, choose, before_action, driver, *, nonbranching=False, answer_log=None):
    groups = {}
    for field in rendered_fields(block):
        group = (field["type"], field["name"] if field["type"] == "radio" else
                 field["i"] if field["type"] in {"text", "select"} else "checkboxes")
        groups.setdefault(group, []).append(field)
    if not groups:
        raise ValueError("A visible question had no supported answer field.")
    for (kind, _), fields in groups.items():
        first = fields[0]
        labels = first["options"] if kind == "select" else [f["text"] for f in fields] if kind != "text" else []
        q = question
        if len(groups) > 1 and first["text"]:
            q += " — " + first["text"]
        picks, personal = choose(q, labels, kind, nonbranching)
        if not picks or (kind != "checkbox" and len(picks) != 1) or (kind != "text" and any(p not in labels for p in picks)):
            raise ValueError("A question selection did not match its displayed labels.")
        locators = block.locator(FIELDS)
        if kind == "select":
            control = locators.nth(first["i"])
            before_action()
            control.select_option(label=picks[0])
            if control.locator("option:checked").inner_text().strip() != picks[0]:
                raise ValueError("The selected option did not register.")
        elif kind == "text":
            control = locators.nth(first["i"])
            if control.evaluate("e=>e.tagName==='IFRAME'"):
                control = control.content_frame.locator('body[contenteditable=true],[role=textbox]').first
            before_action()
            control.fill(picks[0])
            if control.evaluate("e=>(e.value ?? e.innerText).trim()") != picks[0]:
                raise ValueError("The written response did not register.")
        else:
            for f in fields:
                desired = f["text"] in picks
                control = locators.nth(f["i"])
                if control.is_checked() == desired or (not desired and kind == "radio"):
                    continue
                before_action()
                # Playwright checks by actual input position. A hidden native input can have a visible label.
                handle = control.element_handle()
                label = handle.evaluate_handle("e=>[...e.labels||[]].find(l=>l.checkVisibility())||e").as_element()
                try:
                    try:
                        label.evaluate("e=>e.click()")
                    except Error:
                        if control.is_checked() == desired:
                            continue
                    if control.is_checked() != desired:
                        driver.coordinate_click(label, "question option")
                    if control.is_checked() != desired:
                        raise ValueError("A displayed option did not register after its coordinate retry.")
                finally:
                    handle.dispose()
                    label.dispose()
        if personal:
            personal.record(q, picks)
        if answer_log is not None:
            with answer_log.open("a", encoding="utf-8") as report:
                report.write(f"- {q} — {', '.join(picks)} (domain)\n")
        driver.log.write("answer", question, "domain", 0, tier="T1", basis="domain",
                         rule_used="L16" if personal else "L11")


def run_quiz(driver, worker, capture, transcript, *, profile=None, decide_labels=None):
    from tools.browser.run import decide, hard_block

    page = driver.page
    path = urlsplit(page.url).path.rstrip("/")
    base = path.removesuffix("/take")
    origin = urlsplit(page.url)[:2]
    profile = profile or ProfileAnswers()
    blocks = page.locator(BLOCKS)
    if not blocks.count():
        blocks = page.locator("form fieldset:visible").filter(has_not=page.locator("fieldset"))
    def observe():
        if urlsplit(page.url)[:2] != origin or urlsplit(page.url).path.rstrip("/") != path:
            raise ValueError("The Canvas attempt changed before the current action.")
        if reason := hard_block(driver):
            raise RuntimeError(reason)
        shot = driver.shot(capture / "viewport.png")
        if not shot.ok and re.search(r"timed out|blank viewport",shot.detail,re.I) and page.evaluate("document.hidden"):
            if urlsplit(page.url)[:2] != origin or urlsplit(page.url).path.rstrip("/") != path:
                raise ValueError("The original Canvas attempt is no longer open.")
            page.bring_to_front()
            page.wait_for_function("document.visibilityState==='visible'",timeout=5000)
            driver.log.write("select-quiz-tab", worker.item["title"], "ok", 0, rule_used="L06",
                             detail="Selected the same Canvas attempt after background capture timed out.")
            shot = driver.shot(capture / "viewport.png")
        if not shot.ok:
            raise RuntimeError(shot.detail)
    def choose(question, labels, kind, nonbranching):
        if kind == "text" or personal_question(question):
            answers = profile.choose(question, labels, free_text=kind=="text", nonbranching=nonbranching)
            # A personal checkbox list can be resolved from the owner's profile. An exclusive
            # None answer stays exclusive even when the instructions say select all.
            return answers, profile
        if len(labels) == 1:
            return labels, None
        if decide_labels:
            return decide_labels(question, labels, kind), None
        frame = [{"text": question, "controls": [{"name": s} for s in labels]}]
        task = {"goal": question + (" Select all applicable answers." if kind=="checkbox" else " Select one."),
                "actions": [], "done_text": ""}
        for n in range(2):
            try:
                return decide(task, capture, frame, transcript, select_labels=labels)["labels"], None
            except (subprocess.TimeoutExpired, RuntimeError, ValueError) as error:
                driver.log.write("quiz-decision", question, "stall", 0, tier="T1", detail="Rechecking the visible answer group.")
                if n:
                    raise ValueError("The visible knowledge question could not be resolved after re-observation.") from error
                observe()
    index = 0
    worker._milestone(driver, "reached", True, "quiz-reached", "Canvas displayed the native quiz attempt.")
    while index < blocks.count():
        block = blocks.nth(index)
        title = block.locator(".question_text:visible,legend:visible,[data-question-text]:visible").first
        if not title.count():
            raise ValueError("A visible Canvas question has no readable prompt.")
        question = " ".join(title.inner_text().split())
        fields = rendered_fields(block)
        if not fields and block.evaluate("e=>e.matches('.text_only_question') || !!e.querySelector('.text_only_question')"):
            index += 1
            continue
        if fields and all(f["type"] in {"radio", "checkbox"} for f in fields):
            recorded = profile.read()[1].get(clean(question))
            checked = [f["text"] for f in fields if f["checked"]]
            if recorded and sorted(checked) == sorted(recorded):
                driver.log.write("resume-answer", question, "ok", 0, rule_used="L16",
                                 detail="The existing response matches the checked options.")
                index += 1
                continue
        block.evaluate("e=>e.scrollIntoView({behavior:'instant',block:'center'})")
        observe()
        observed_at = time.monotonic()
        first_action = True
        def before_answer():
            nonlocal first_action
            if not first_action or time.monotonic() - observed_at > 3:
                observe()
            first_action = False
        fill_block(block, question, choose, before_answer, driver, nonbranching=blocks.count() > 1,
                   answer_log=capture.parent / "answers.md")
        index += 1
    if not index:
        return Outcome("blocked", detail="The Canvas attempt displayed no question blocks.")
    submit = page.get_by_role("button", name=re.compile(r"^Submit(?: Quiz| Survey| Answers)?$", re.I))
    if submit.count() != 1:
        return Outcome("blocked", detail="The answered Canvas form did not expose a unique Submit button.")
    observe()
    worker.record_submit(driver, dispatched=False)
    def confirm(dialog):
        if dialog.type=="confirm" and re.search(r"submit|finish", dialog.message, re.I) and not re.search(r"unanswered",dialog.message,re.I):
            dialog.accept()
        else:
            dialog.dismiss()
    page.on("dialog", confirm)
    try:
        submit.evaluate("e=>e.click()")
        worker.record_submit(driver, dispatched=True)
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            address = urlsplit(page.url)
            if address[:2] != origin or not (address.path.rstrip("/") == base or address.path.startswith(base+"/")):
                break
            if address.path.rstrip("/") != path:
                body = page.locator("body").inner_text(timeout=3000)
                score = POSTED.search(body)
                if score:
                    worker._milestone(driver, "score", score[1], "quiz-score", "Posted Canvas score " + score[1])
                    if re.search(r"some questions (?:are )?not yet graded|awaiting (?:manual )?grading", body, re.I):
                        worker._milestone(driver, "grading_pending", True, "grading-pending",
                                          "Canvas says that some questions still need grading.")
                    worker._milestone(driver, "canvas_result", True, "verify-quiz", "Score on this quiz's Canvas results page.")
                    return Outcome("ok", detail="Canvas posted the quiz score " + score[1] + ".")
                if re.search(r"\b(?:submitted (?:on|at)|survey submitted|attempt submitted)\b", body, re.I):
                    return Outcome("ok", detail="Canvas displayed a submission receipt. Verify its listing.")
            page.wait_for_timeout(350)
        return Outcome("blocked", detail="Submit was activated. Canvas has not displayed a posted score or receipt.")
    finally:
        page.remove_listener("dialog", confirm)
