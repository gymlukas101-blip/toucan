"""Observe the lifetime of the active player without navigating its tab."""

import hashlib
import re
import time
from contextlib import contextmanager
from threading import Event, local

from playwright.sync_api import Error


def target_id(page):
    session = page.context.new_cdp_session(page)
    try:
        return session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
    finally:
        session.detach()


def player_identity(url):
    # Slide fragments change in place. Keep signed document URLs out of the queue and log.
    return hashlib.sha256(url.partition("#")[0].encode("utf-8")).hexdigest()


def player_ended(page, identity, error=None):
    if error and re.search(r"\bframe (?:has been |was |is |got )?detached\b|\bdetached frame\b", str(error), re.I):
        return True
    return page is not None and (page.is_closed() or bool(identity) and player_identity(page.url) != identity)


class PlayerEnded(Exception):
    pass


class LessonChanged(Exception):
    """A lesson inside a still-bound course changed during an observation or input."""


def wait_for_player(page, timeout=25):
    """Follow launch redirects until a learner-facing document has rendered."""
    deadline = time.monotonic() + timeout
    previous = ""
    while time.monotonic() < deadline:
        if page.is_closed():
            raise PlayerEnded("The launch window closed before the player appeared.")
        address = page.url
        if address.startswith(("https://", "http://")):
            try:
                if re.fullmatch(r"thank you[!.]?", page.locator("body").inner_text(timeout=800).strip(), re.I):
                    raise PlayerEnded("The launch returned an exit receipt before showing the player.")
                ready = any(frame.locator("body").evaluate("""body => {
                    const visible = e => e.checkVisibility() && e.getClientRects().length;
                    const text = body.innerText.trim();
                    const learnerControl = [...body.querySelectorAll('button,[role=button],a[href]')]
                        .some(e => visible(e) && /^(?:start|resume|play|continue|begin|next|take quiz)\\b/i
                            .test((e.getAttribute('aria-label') || e.innerText || '').trim()));
                    const readable = body.querySelector('main,article,[role=main]');
                    const question = [...body.querySelectorAll('textarea,input[type=radio],input[type=checkbox]')]
                        .some(visible);
                    return !!body.querySelector('video,audio') || learnerControl || question
                        || (readable && text.length > 20 && !/please wait|redirecting|launching/i.test(text))
                        || (text.length > 160 && !/please wait|redirecting|launching/i.test(text));
                }""", timeout=800) for frame in page.frames if frame.locator("body").count())
                if ready and previous == address:
                    return
                previous = address if ready else ""
            except Error:
                previous = ""
        time.sleep(0.25)
    raise RuntimeError("The launch did not reach a stable learner-facing player document.")


class PlayerWatch:
    def __init__(self, identity):
        self.identity = identity
        self.ended = Event()
        self.reason = ""
        self._connection = local()

    def track_media(self, frame):
        # A player can redirect its content iframe while keeping the popup URL.
        # Frame handles belong to the Playwright connection on the calling thread.
        course = self._connection.course
        ancestor = frame
        while course and ancestor:
            if ancestor == course:
                frame = course
                break
            ancestor = ancestor.parent_frame
        while frame and frame.parent_frame:
            if frame.url.startswith(("https://", "http://")):
                self._connection.documents.setdefault(frame, player_identity(frame.url))
            frame = frame.parent_frame

    def finish(self, reason):
        if not self.ended.is_set():
            self.reason = reason
            self.ended.set()

    def check(self, page, error=None):
        if not self.ended.is_set():
            if page.is_closed():
                self.finish("The player window closed.")
            elif player_identity(page.url) != self.identity:
                self.finish("The player window navigated to another document.")
            elif error and player_ended(None, None, error):
                course = getattr(self._connection, "course", None)
                if course and not course.is_detached() and self._connection.documents[course] == player_identity(course.url):
                    raise LessonChanged("An embedded activity changed inside the open course.")
                self.finish("The browser reported a detached player frame during an action.")
        if self.ended.is_set():
            raise PlayerEnded(self.reason or "The player document ended. Verify completion on Canvas.")

    @contextmanager
    def observe(self, page):
        # Each Playwright connection registers its own listeners. Only the event
        # and document identity cross threads, including during a model decision.
        documents = self._connection.documents = {page.main_frame: self.identity}
        # In a Rustici course the Storyline video and the following quiz are
        # activities in the same outer content document. Bind that document.
        self._connection.course = next((f for f in page.frames if f.name == "scormdriver_content"
                                        and f.url.startswith(("https://", "http://"))), None)
        if self._connection.course:
            self.track_media(self._connection.course)

        def ended(_):
            self.finish("The player window closed.")

        def detached(frame):
            if frame in documents:
                self.finish("A bound player frame detached.")

        def navigated(frame):
            if frame in documents and player_identity(frame.url) != documents[frame]:
                self.finish("The player window navigated away." if frame == page.main_frame else
                            "An embedded player document navigated away.")

        handlers = (("framedetached", detached), ("framenavigated", navigated), ("close", ended))
        for event, handler in handlers:
            page.on(event, handler)
        try:
            self.check(page)
            yield
            self.check(page)
        except Error as error:
            self.check(page, error)
            raise
        finally:
            for event, handler in handlers:
                page.remove_listener(event, handler)
            del self._connection.documents
            del self._connection.course
