"""Resolve a spoken course from the names, codes and IDs on the course list."""

import re
import unicodedata


def normalized(value):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", str(value)).casefold()))


class Ambiguous(ValueError):
    def __init__(self, query, options):
        self.options = options
        labels = [c["title"] + (f" ({c['id']})" if "id" in c else "") for c in options]
        super().__init__(f"Confirm which match you mean for {query!r}: " + " | ".join(labels))


def score_name(query, name):
    if query == name:
        return 100
    if name.startswith(query):
        return 70
    words = set(query.split()) - {"a", "an", "and", "in", "of", "the", "to"}
    return len(words.intersection(name.split()))


def resolve(query, courses):
    needle = normalized(query)
    if not needle:
        raise ValueError("Supply a course name, code or ID.")
    scored = []
    for course in courses:
        if needle == str(course["id"]):
            return course
        names = [course["title"], *course.get("aliases", [])]
        score = max(score_name(needle, normalized(name)) for name in names)
        codes = list(course.get("codes", []))
        for name in names:
            codes.extend(re.findall(r"\b[A-Za-z]{2,}(?:[ -][A-Za-z]{2,})?[ -]?\d{2,4}[A-Za-z]?\b", name))
            codes.extend(re.findall(r"\b[A-Za-z]{2,}[ -]?\d{2,4}[A-Za-z]?\b", name))
        compact_query = needle.replace(" ", "")
        for code in codes:
            compact_code = normalized(code).replace(" ", "")
            if compact_code == compact_query:
                score = max(score, 90)
            elif compact_code.startswith(compact_query):
                score = max(score, 70)
        if score:
            scored.append((score, course))
    if not scored:
        raise ValueError(f"No visible course matches {query!r}.")
    scored.sort(key=lambda match: match[0], reverse=True)
    top = [course for value, course in scored if scored[0][0] - value <= 1]
    if len(top) > 1:
        raise Ambiguous(query, top)
    return top[0]
