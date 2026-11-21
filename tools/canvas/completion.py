"""Read quiz milestones from rendered player text."""

import re


VALUE = r"(?:\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*(?:/|out of)\s*\d+(?:\.\d+)?)"
SCORE = re.compile(rf"^(?:your (?:quiz )?score(?: is| was)?|you scored|quiz score|score earned)"
                   rf"[ \t]*[:\-]?[ \t]*\n?({VALUE})[.!]?$", re.I | re.M)
RESULT = re.compile(rf"^({VALUE})[ \t]*(?:\n|[ \t]+)[ \t]*(?:passed|failed)[.!]?$", re.I | re.M)
RESULT_SCORE = re.compile(rf"^score[ \t]*:[ \t]*\n?({VALUE})[.!]?$", re.I | re.M)
QUESTION = re.compile(r"^(?:(?:yes[, .]+)?i have (?:successfully )?completed this module|"
                      r"have you completed (?:and understood )?this module)[.!?]?$", re.I | re.M)
QUIZ_TITLE = re.compile(r"^(?:(?:module \d+[: -]+)?completion (?:quiz|question)|module \d+ completion)[.!]?$", re.I | re.M)
SUBMIT = re.compile(r"^(?:submit(?: (?:quiz|answer|interaction|assessment|response))?|finish quiz)"
                    r"(?:\s*\([^)]*\))?$", re.I)


def quiz_evidence(frames):
    reached, score = False, ""
    for frame in frames:
        text = "\n".join(" ".join(line.split()) for line in frame["text"].splitlines() if line.strip())
        quiz_control = any(re.fullmatch(r"(?:start|begin|take|resume) quiz|submit(?: quiz| answer)?",
                                        control["name"].strip(), re.I) for control in frame.get("controls", ()))
        question = QUESTION.search(text)
        reached = reached or bool(question or QUIZ_TITLE.search(text) and quiz_control)
        if quiz_control and (question or QUIZ_TITLE.search(text)):
            continue
        match = SCORE.search(text)
        if not match and re.search(r"^quiz results[.!]?$", text, re.I | re.M):
            match = RESULT_SCORE.search(text) or RESULT.search(text)
        if match:
            score = match[1]
    return reached or bool(score), score


def quiz_summary(item):
    quiz = item.get("quiz", {})
    return (f"Completion quiz {'observed' if quiz.get('reached') else 'unobserved'}. "
            f"Submit {'clicked' if quiz.get('submitted') else 'attempted' if quiz.get('attempted') else 'unobserved'}. "
            f"Player score {quiz.get('score') or 'unobserved'}.")
