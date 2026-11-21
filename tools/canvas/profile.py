"""Apply the owner's standing choices and retain private answers across courses."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from tasks.queue import exclusive


PROFILE = Path(__file__).resolve().parents[2] / "memory/profile-answers.md"
MULTIPLE = re.compile(r"\b(?:select|choose|check|mark)\s+all\b", re.I)
TOPICS = {
    "education": r"class standing|year in (?:school|college)|class year|undergraduate|student classification",
    "caffeine": r"caffein|coffee|energy drinks?",
    "drinks per sitting": r"(?:how many|number of|typical number).{0,50}drinks?|drinks? per",
    "alcohol": r"\balcohol\b|\bdrink(?:ing)?\b|\bdrunk\b",
    "residence": r"where (?:do|will) you live|living arrangement|residen(?:ce|tial)|housing situation",
    "activities": r"\bclubs?\b|fraternit|sororit|campus activit|athletic team|campus sports",
}


def clean(s):
    return " ".join(str(s).split()).casefold()


def topic_for(question):
    return next((name for name, pattern in TOPICS.items() if re.search(pattern, question, re.I)), "")


def personal_question(question):
    return (bool(re.search(r"\b(?:your|yourself|you|my|myself|i)\b", question, re.I))
            and not re.search(r"\b(?:scenario|suppose|imagine|should|correct|most effective|best (?:way|response)|"
                              r"if (?:a|your|someone)|how (?:could|would) you help|ways? you can help|"
                              r"(?:can|could) you (?:use|identify|do to help))\b", question, re.I))


def resolve_personal(question, options, facts, previous=(), *, free_text=False, nonbranching=False):
    """Return labels from this question only. Numeric order is independent of display order."""
    labels = {clean(s): s for s in options}
    if previous and (free_text or all(clean(s) in labels for s in previous)):
        return list(previous) if free_text else [labels[clean(s)] for s in previous]
    topic = topic_for(question)
    value = facts.get(topic, "")
    def match(pattern):
        return next((s for key, s in labels.items() if re.search(pattern, key)), None)
    if free_text:
        if re.search(r"how .*?alcohol culture.*impacted", question, re.I):
            impact = next((v for k,v in facts.items() if k=="alcohol culture impact"), "")
            if clean(impact) == "no":
                subject = "sense of belonging" if re.search("belong",question,re.I) else "overall experience"
                return [f"It has not affected my {subject}."]
        if value:
            return [value]
        if re.search(r"belong|experience|satisf", question, re.I):
            return ["Neutral overall."] if re.search(r"belong|feel", question, re.I) else ["Generally satisfied."]
        return ["None to add."]
    if not labels:
        raise ValueError("This personal question has no rendered answer choices.")
    if value and clean(value) in labels:
        return [labels[clean(value)]]
    if value and (related := match(re.escape(clean(value)))):
        return [related]
    # Match topic facts across differently worded forms, without applying
    # substance-use defaults to objective questions about the subject.
    aliases = ((r"residence hall", r"residence hall|dorm|on[- ]campus housing"),
               (r"first.year", r"first.year|freshm[ae]n"),
               (r"rarely", r"rarely|seldom|occasionally"),
               (r"not involved|not (?:a )?member", r"chosen not to join|not (?:a |currently )?(?:member|involved)|none of"))
    for fact, option in aliases:
        if re.search(fact, clean(value)) and (found := match(option)):
            return [found]
    if len(labels) >= 3 and (match(r"\bagree\b|\bsatisfied\b") and match(r"disagree|dissatisfied")
                             or match(r"neutral|neither (?:agree|satisfied)")):
        evaluation = re.search(r"satisf|evaluat|course|program|training|quality|useful|material|overall experience", question, re.I)
        chosen = match(r"^(?:4\s*[-:.)]?\s*)?(?:agree|satisfied|somewhat satisfied)$") if evaluation else None
        return [chosen or match(r"neutral|neither|undecided") or options[(len(options)-1)//2]]
    sensitive = re.search(r"recovery|survivor|diagnos|sexual orientation|gender identity|race|ethnicity", question, re.I)
    if sensitive and nonbranching and not value and (decline := match(r"prefer not|rather not")):
        return [decline]
    for pattern in (r"^(?:no|never|none|zero|0)(?:$|[ ,.:(-])", r"have never|do not (?:drink|use)|don't (?:drink|use)|"
                    r"have not (?:drank|drunk|used|consumed)|not applicable|none of (?:these|the above)|not (?:a |currently )?(?:member|involved)|not to join"):
        choice = match(pattern)
        if choice:
            return [choice]
    amounts = [(float(m[1]), v) for k, v in labels.items() if (m := re.match(r"^(?:less than |fewer than |under )?(\d+(?:\.\d+)?)", k))]
    if amounts:
        if len(amounts) == 5 and re.search(r"agree|satisf|scale|rate", question, re.I):
            return [sorted(amounts)[3 if re.search(r"satisf|evaluat|quality", question, re.I) else 2][1]]
        return [min(amounts, key=lambda pair: pair[0])[1]]
    return [match(r"rarely|seldom|once|low|very unlikely") or
            next((s for s in options if not re.search(r"prefer not|other \(specify|all of the above", s, re.I)), options[0])]


class ProfileAnswers:
    def __init__(self, path=None):
        self.path = Path(path) if path else PROFILE

    def read(self):
        facts, answers = {}, {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.startswith("- "):
                    payload = line[2:].rsplit(" <!-- id=", 1)[0]
                    if payload.startswith("Answer "):
                        question, selected = json.loads(payload[7:])
                        answers[clean(question)] = selected
                    elif ": " in payload:
                        name, value = payload.split(": ", 1)
                        facts[clean(name)] = value
        return facts, answers

    def choose(self, question, options, **kwargs):
        facts, answers = self.read()
        for other, values in answers.items():
            if "has the alcohol culture impacted" in other and values:
                facts["alcohol culture impact"] = values[0]
        topic = topic_for(question)
        if topic and topic not in facts:
            for other, values in reversed(answers.items()):
                if topic_for(other) == topic and len(values) == 1:
                    facts[topic] = values[0]
                    break
        return resolve_personal(question, options, facts, answers.get(clean(question), ()), **kwargs)

    def record(self, question, selected):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive(self.path):
            if self.read()[1].get(clean(question)) == selected:
                return
            payload = json.dumps([" ".join(question.split()), selected], ensure_ascii=False)
            key = hashlib.sha256(payload.encode()).hexdigest()[:12]
            with self.path.open("a", encoding="utf-8") as f:
                f.write(f"- Answer {payload} <!-- id={key} src=user date={date.today().isoformat()} -->\n")
