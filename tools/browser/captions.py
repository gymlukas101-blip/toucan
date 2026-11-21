"""Sample a player's captions on a separate Playwright connection."""

import threading
import time
from pathlib import Path

from playwright.sync_api import Error, sync_playwright

from tools.browser.attach import attach
from tools.browser.driver import visible_frames


def _target(page):
    session = page.context.new_cdp_session(page)
    try:
        return session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
    finally:
        session.detach()


def _read(page):
    for frame in visible_frames(page):
        sample = frame.evaluate("""() => {
            const video = document.querySelector('video');
            const caption = document.querySelector(
                '.vjs-text-track-display, .caption-text, .captions-text, ' +
                '.caption-window, .closed-caption, [data-testid="caption"], ' +
                '[class*="captionText"], [class*="subtitle-text"]');
            if (!video || !caption) return null;
            return {text: caption.innerText || '', time: video.currentTime};
        }""")
        if sample is not None:
            return sample
    return None


class CaptionSampler:
    def __init__(self, target, path, log, interval=0.8):
        self.target = target
        self.path = Path(path)
        self.log = log
        self.interval = interval
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, name="caption-sampler", daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(20):
            raise RuntimeError("Caption sampler did not start.")
        if self.error:
            raise RuntimeError("Caption sampler could not attach.") from self.error

    def stop(self):
        self.stop_event.set()
        self.thread.join()
        if self.error:
            raise RuntimeError("Caption sampler failed.") from self.error

    def _run(self):
        try:
            with sync_playwright() as pw:
                browser = attach(pw)
                page = next(p for p in browser.contexts[0].pages if _target(p) == self.target)
                self._sample(page)
        except Exception as error:
            self.error = error
            self.ready.set()

    def _sample(self, page):
        previous = None
        missing_since = None
        reported = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", buffering=1) as output:
            self.ready.set()
            while not self.stop_event.is_set():
                try:
                    sample = _read(page)
                except Error:
                    sample = None
                if sample is None:
                    missing_since = missing_since or time.monotonic()
                    if not reported and time.monotonic() - missing_since > 15:
                        self.log.write("captions-missing", "player", "miss", 0, tier="T1")
                        reported = True
                else:
                    missing_since = None
                    text = " ".join(sample["text"].split())
                    if text and text != previous:
                        seconds = int(sample["time"])
                        output.write(f"[{seconds // 60:02d}:{seconds % 60:02d}] {text}\n")
                        output.flush()
                        previous = text
                self.stop_event.wait(self.interval)
