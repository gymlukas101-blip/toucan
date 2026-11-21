"""Page-scoped input, observations and an append-only run log."""

import ctypes
import json
import math
import re
import tempfile
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from PIL import Image
from playwright.sync_api import Error, Frame, Locator, Page, TimeoutError as PlaywrightTimeout

from tools.check_secrets import PATTERNS


def public_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.split("@")[-1], parts.path, "", ""))


@dataclass
class Outcome:
    result: str
    value: Any = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.result in {"ok", "domain"}


class RunLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, action, target, result, ms, *, driver="cdp", tier="T0", basis="domain", detail="",
              source=None, elements=None, reason=None):
        record = dict(t=datetime.now(timezone.utc).isoformat(), action=action, target=target,
                      result=result, tier=tier, basis=basis, ms=round(ms), driver=driver)
        if detail:
            record["detail"] = detail
        if source is not None:
            record["source"] = source
        if elements is not None:
            record["elements"] = elements
        if reason is not None:
            record["reason"] = reason
        data = json.dumps(record, ensure_ascii=True).encode("utf-8")
        for pattern in PATTERNS.values():
            data = pattern.sub(b"<redacted>", data)
        with self.path.open("ab") as log:
            log.write(data + b"\n")


class ActionFailure(Exception):
    def __init__(self, result: str, detail: str):
        super().__init__(detail)
        self.result = result


def cursor_position() -> tuple[int, int]:
    point = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError()
    return point.x, point.y


def guarded_click(click: Callable[[], None]):
    before = cursor_position()
    try:
        click()
    finally:
        if cursor_position() != before:
            raise ActionFailure("aborted", "Cursor position changed during dispatch; inspect before replaying.")


def visible_frames(page: Page) -> list[Frame]:
    visible = []
    for frame in page.frames:
        parent = frame
        while parent.parent_frame:
            if not parent.frame_element().is_visible():
                break
            parent = parent.parent_frame
        else:
            visible.append(frame)
    return visible


def _visible_matches(locators: list[Locator]) -> list[Locator]:
    return [match for locator in locators for match in locator.all() if match.is_visible()]


def _player_address(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path.lower()
    return (host == "insops.net" or host.endswith(".insops.net") or "scorm" in host or "scorm" in path
            or "/launch_complete" in path or "/external_tools/retrieve" in path
            or any(k.lower() in {"jwt", "sig", "signature", "token", "launch_token"}
                   for k, _ in parse_qsl(parts.query)))


class Driver:
    def __init__(self, page: Page, log: RunLog):
        self.page = page
        self.log = log
        self.basis = "domain"
        self._capture_retry = None
        self._seen_discard = None
        self._course_popup = None
        self._next_viewport = 0.0

    def _action(self, name: str, target: str, operation, *, tier="T0") -> Outcome:
        start = time.monotonic()
        for attempt in range(2):
            try:
                outcome = Outcome("ok", operation())
                break
            except ActionFailure as error:
                outcome = Outcome(error.result, False, str(error))
                break
            except Error as error:
                stale = any(word in str(error).lower() for word in
                            ("detached", "not attached", "execution context was destroyed"))
                if stale and attempt == 0:
                    continue
                outcome = Outcome("miss", False, "Detached target after retry." if stale else
                                  "The page action did not complete.")
                break
            except (OSError, ValueError) as error:
                outcome = Outcome("miss", False, str(error))
                break
        self.log.write(name, target, outcome.result, (time.monotonic() - start) * 1000,
                       tier=tier, basis=self.basis, detail=outcome.detail)
        return outcome

    def frames(self) -> Outcome:
        def describe():
            return [dict(name=f.name, url=public_url(f.url), main=f == self.page.main_frame)
                    for f in visible_frames(self.page)]
        return self._action("frames", "page", describe, tier="T1")

    def read(self) -> Outcome:
        def observe():
            controls = """els => els.filter(e => e.checkVisibility() && e.getClientRects().length).map(e => ({
                role: e.getAttribute('role') || (e.tagName === 'A' ? 'link' : 'button'),
                name: (e.getAttribute('aria-labelledby') || '').split(/ +/).filter(Boolean)
                        .map(id => document.getElementById(id)?.innerText || '').join(' ').trim()
                    || e.getAttribute('aria-label') || e.innerText?.trim() || e.value || e.title || ''
            })).filter(c => c.name)"""
            selector = "button,a[href],[role=button],[role=link],input[type=submit],input[type=button]"
            return [dict(name=f.name, url=public_url(f.url), main=f == self.page.main_frame,
                         text=f.locator("body").inner_text(timeout=2000),
                         controls=f.locator(selector).evaluate_all(controls))
                    for f in visible_frames(self.page) if f.locator("body").count()]
        return self._action("read", "visible frame text", observe, tier="T1")

    def goto(self, url: str) -> Outcome:
        def navigate():
            part = urlsplit(url)
            if part.scheme not in {"http", "https"} or not part.hostname or part.username or part.password:
                raise ActionFailure("aborted", "Supply an HTTP(S) URL without embedded credentials.")
            response = self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
            if response and response.status >= 400:
                raise ActionFailure("aborted", f"The page returned HTTP {response.status}.")
            return public_url(self.page.url)
        return self._action("goto", public_url(url), navigate)

    def settle(self) -> Outcome:
        def wait():
            try:
                self.page.wait_for_load_state("networkidle", timeout=850)
                return "network idle"
            except PlaywrightTimeout:
                pass
            previous = None
            stable_samples = 0
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                state = [(f.url, f.evaluate("() => [document.body?.innerText, document.body?.scrollHeight]"))
                         for f in visible_frames(self.page)]
                stable_samples = stable_samples + 1 if state == previous else 0
                if stable_samples >= 2:
                    return "stable rendered text and layout"
                previous = state
                time.sleep(0.15)
            raise ActionFailure("stall", "Page did not settle.")
        return self._action("settle", "page", wait)

    def auth_required(self) -> bool:
        for f in visible_frames(self.page):
            host = (urlsplit(f.url).hostname or "").lower()
            if (host == "login.wisc.edu" or host.endswith(".duosecurity.com")
                    or f.locator('input[type=password]:visible,input[autocomplete=one-time-code]:visible,'
                                 'input[autocomplete=username]:visible').count()):
                return True
        return False

    def _check_input(self):
        if self.auth_required():
            raise ActionFailure("aborted", "Manual sign-in required.")

    def _click(self, target):
        self._check_input()
        spec = {"name": target} if isinstance(target, str) else target
        frames = visible_frames(self.page)
        if "frame" in spec:
            frames = [f for f in frames if f.name == spec["frame"] or public_url(f.url) == spec["frame"]]
            if len(frames) != 1:
                raise ActionFailure("miss", "The named frame is missing or ambiguous.")
        name = spec.get("name") or spec.get("label") or spec.get("text")
        if name:
            roles = [spec["role"]] if spec.get("role") else ["button", "link", "radio", "checkbox",
                                                               "tab", "menuitem", "textbox", "option"]
            searches = [
                lambda: [f.get_by_role(role, name=name, exact=True) for f in frames for role in roles],
                lambda: [f.get_by_label(spec.get("label", name), exact=True) for f in frames],
                lambda: [f.get_by_text(spec.get("text", name), exact=True) for f in frames],
            ]
            for search in searches:
                found = _visible_matches(search())
                if len(found) > 1:
                    raise ActionFailure("ambiguous", "More than one visible control matches the target.")
                if found:
                    guarded_click(lambda: found[0].click(timeout=2000))
                    return "named control"
        if "point" in spec:
            x, y = spec["point"]
            if not all(isinstance(v, (float, int)) and math.isfinite(v) and 0 <= v < 1 for v in (x, y)):
                raise ValueError("Viewport coordinates must be fractions in [0, 1).")
            width, height = self.page.evaluate("() => [innerWidth, innerHeight]")
            guarded_click(lambda: self.page.mouse.click(x * width, y * height))
            return "viewport point"
        raise ActionFailure("miss", "No visible control matched.")

    def click(self, target) -> Outcome:
        label = target if isinstance(target, str) else str(target.get("name") or target.get("label") or
                                                            target.get("text") or "viewport point")
        return self._action("click", label, lambda: self._click(target))

    def type(self, text: str) -> Outcome:
        def enter():
            self._check_input()
            editable = "input:focus:not([type=password]),textarea:focus,[contenteditable=true]:focus"
            if not any(f.locator(editable).count() for f in visible_frames(self.page)):
                raise ActionFailure("miss", "Focus an editable control first.")
            self.page.keyboard.insert_text(text)
        return self._action("type", "focused editor", enter)

    def press(self, key: str) -> Outcome:
        def send():
            self._check_input()
            self.page.keyboard.press(key)
        return self._action("press", key, send)

    def scroll(self, dy: int) -> Outcome:
        def wheel():
            self._check_input()
            self.page.mouse.wheel(0, dy)
        return self._action("scroll", str(dy), wheel)

    def use_popup(self, page: Page):
        if (self.page is self._course_popup
                or re.search(r"/courses/\d+/assignments/\d+(?:/|$)", urlsplit(self.page.url).path)):
            self._course_popup = page
        self.page = page

    def _viewport(self, kind: str) -> tuple[bytes, str]:
        time.sleep(max(0, self._next_viewport - time.monotonic()))
        self._next_viewport = time.monotonic() + 10
        try:
            data = self.page.screenshot(type=kind, full_page=False, timeout=5000)
        except PlaywrightTimeout:
            return b"", "screenshot timed out"
        with Image.open(BytesIO(data)) as image:
            pixels = image if image.mode in {"RGB", "RGBA"} else image.convert("RGBA")
            if (all(low == high for low, high in pixels.getextrema())
                    or pixels.mode == "RGBA" and pixels.getchannel("A").getbbox() is None):
                return b"", "blank viewport"
        return data, ""

    def _capture_state(self, kind: str) -> tuple[bytes, str]:
        try:
            # Chrome sets this on the document it restored after a discard.
            restored = self.page.locator(":root").evaluate(
                "() => document.wasDiscarded ? performance.timeOrigin : null", timeout=1500)
        except PlaywrightTimeout:
            restored = None
        discard = (self.page, restored)
        if restored is not None and discard != self._seen_discard:
            self._seen_discard = discard
            return b"", "Chrome reported a restored discard"
        return self._viewport(kind)

    def _reload_for_capture(self, url: str, reason: str) -> bool:
        page = self.page
        session = page.context.new_cdp_session(page)
        started = time.monotonic()
        try:
            loader = session.send("Page.getFrameTree")["frameTree"]["frame"]["loaderId"]
            if page.url != url:
                raise ActionFailure("miss", "The page navigated during capture.")
            protected = page is self._course_popup or any(_player_address(f.url) for f in page.frames)
            for frame in page.frames:
                if protected:
                    break
                # Query each origin in its own frame. A black media frame can be genuine.
                protected = frame.locator(":root").evaluate("""root =>
                    !!(window.API || window.API_1484_11 || root.querySelector('video,audio'))""", timeout=1500)
            if protected:
                self.log.write("capture_preserved", reason, "ok", (time.monotonic() - started) * 1000,
                               detail="Kept the signed launch, course popup or media player in place.")
                return False
            with page.expect_event("framenavigated", predicate=lambda f: f == page.main_frame, timeout=8000):
                # Chrome rejects this reload if navigation replaced the inspected document.
                session.send("Page.reload", {"loaderId": loader})
            page.wait_for_load_state("domcontentloaded", timeout=8000)
            self.log.write("capture_reload", reason, "ok", (time.monotonic() - started) * 1000)
            return True
        except Error as error:
            raise ActionFailure("miss", f"{reason}. Could not safely reload the inspected document.") from error
        finally:
            session.detach()

    def shot(self, path: Path) -> Outcome:
        path = Path(path)
        def capture():
            kind = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg"}.get(path.suffix.lower())
            if kind is None:
                raise ValueError("Use a PNG or JPEG screenshot path.")
            key = (self.page, self.page.url)
            started = time.monotonic()
            data, reason = self._capture_state(kind)
            if reason == "screenshot timed out":
                self.log.write("capture_retry", reason, "miss", (time.monotonic() - started) * 1000)
                data, reason = self._capture_state(kind)
                if reason == "screenshot timed out":
                    raise ActionFailure("miss", "Viewport capture timed out twice.")
            if reason:
                if self._capture_retry == key:
                    raise ActionFailure("miss", f"{reason}. Capture recovery was already attempted.")
                self._capture_retry = key
                reloaded = self._reload_for_capture(key[1], reason)
                if not reloaded and reason == "blank viewport":
                    raise ActionFailure("miss", f"{reason}. Kept the player in place.")
                data, reason = self._viewport(kind)
                if reason:
                    raise ActionFailure("miss", f"{reason} on the recovery capture.")
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
            temporary = Path(stream.name)
            try:
                with stream:
                    stream.write(data)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            self._capture_retry = None
            return str(path)
        return self._action("shot", path.name, capture)

    def mute(self) -> Outcome:
        def silence():
            self._check_input()
            session = self.page.context.new_cdp_session(self.page)
            try:
                session.send("Page.setMuted", {"muted": True})
                return "Page.setMuted"
            except Error as error:
                if not any(s in str(error).lower() for s in ("wasn't found", "not found", "unknown method")):
                    raise
            finally:
                session.detach()
            frames = visible_frames(self.page)
            if _visible_matches([f.get_by_role("button", name=re.compile(r"^unmute\b", re.I)) for f in frames]):
                return "already muted"
            self._click({"role": "button", "name": re.compile(r"^mute\b", re.I)})
            return "player mute control"
        return self._action("mute", "page audio", silence)

    def assert_progress(self, seconds: float = 5) -> Outcome:
        def check():
            previous = {}
            deadline = time.monotonic() + seconds
            while True:
                for f in visible_frames(self.page):
                    samples = f.evaluate("""() => Array.from(document.querySelectorAll('video,audio'), (m,i) =>
                        [i, m.currentSrc, m.currentTime, m.duration || 0, m.loop, m.paused])""")
                    for index, source, value, duration, looping, paused in samples:
                        key = (f, index, source)
                        old = previous.get(key)
                        if old is not None and not paused and (value > old + .025 or
                                (looping and duration and old > duration - .5 and value < old)):
                            return True
                        previous[key] = value
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ActionFailure("stall", "No media playhead advanced in the visible frames.")
                time.sleep(min(.16, remaining))
        return self._action("assert_progress", "frame media playhead", check, tier="T1")
