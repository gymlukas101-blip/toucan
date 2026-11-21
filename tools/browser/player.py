"""Observe the lifetime of the active player without navigating its tab."""

import hashlib
import re
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


class PlayerWatch:
    def __init__(self, identity):
        self.identity = identity
        self.ended = Event()
        self._connection = local()

    def track_media(self, frame):
        # A player can redirect its content iframe while keeping the popup URL.
        # Frame handles belong to the Playwright connection on the calling thread.
        while frame and frame.parent_frame:
            self._connection.documents.setdefault(frame, player_identity(frame.url))
            frame = frame.parent_frame

    def check(self, page, error=None):
        if not self.ended.is_set() and player_ended(page, self.identity, error):
            self.ended.set()
        if self.ended.is_set():
            raise PlayerEnded("The player document ended. Verify completion on Canvas.")

    @contextmanager
    def observe(self, page):
        # Each Playwright connection registers its own listeners. Only the event
        # and document identity cross threads, including during a model decision.
        documents = self._connection.documents = {page.main_frame: self.identity}

        def ended(_):
            self.ended.set()

        def navigated(frame):
            if frame in documents and player_identity(frame.url) != documents[frame]:
                self.ended.set()

        handlers = (("framedetached", ended), ("framenavigated", navigated), ("close", ended))
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
