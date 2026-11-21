"""Exercise parallel use and capture recovery against disposable local surfaces."""

import contextlib
import ctypes
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

if sys.platform != "win32":
    raise unittest.SkipTest("The parallel-use proof requires Windows.")

import win32api
import win32con
import win32gui
import win32process
from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.browser.attach import attach
from tools.browser.driver import Driver, Outcome, RunLog, cursor_position
from tools.browser.launch import launch, verify_owner
from tools.browser.run import run, run_loop, verify_after_play


VIDEO = Path(__file__).with_name("fixtures") / "loop.webm"


def wait_for(predicate, timeout=7):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := predicate():
            return value
        time.sleep(.05)
    raise AssertionError("Timed out waiting for " + (predicate.__doc__ or predicate.__name__))


def focus_test_browser(page):
    """Give the test Chrome real Windows focus once, so covering it causes a real blur."""
    title = "Toucan blur proof " + uuid.uuid4().hex
    page.evaluate("(title) => document.title = title", title)
    page.bring_to_front()
    pid = verify_owner()
    def target_window():
        matches = []
        def visit(hwnd, _):
            if (win32process.GetWindowThreadProcessId(hwnd)[1] == pid and win32gui.IsWindowVisible(hwnd)
                    and title in win32gui.GetWindowText(hwnd)):
                matches.append(hwnd)
        win32gui.EnumWindows(visit, None)
        return matches[0] if len(matches) == 1 else None
    target = wait_for(target_window)
    if win32gui.GetForegroundWindow() == target:
        return
    # Windows grants the foreground process permission to activate a window. Use a
    # temporary window owned by the fixture so no native click touches Chrome.
    wc = win32gui.WNDCLASS()
    wc.hInstance = win32api.GetModuleHandle(None)
    wc.lpszClassName = "ToucanProof" + uuid.uuid4().hex
    wc.lpfnWndProc = win32gui.DefWindowProc
    wc.hbrBackground = win32con.COLOR_WINDOW + 1
    win32gui.RegisterClass(wc)
    panel = win32gui.CreateWindowEx(win32con.WS_EX_TOPMOST, wc.lpszClassName, "Toucan focus proof",
                                    win32con.WS_POPUP | win32con.WS_VISIBLE | win32con.WS_BORDER,
                                    200, 200, 240, 80, 0, 0, wc.hInstance, None)
    previous = cursor_position()
    point = win32gui.ClientToScreen(panel, (50, 40))
    try:
        win32gui.ShowWindow(panel, win32con.SW_SHOW)
        win32gui.UpdateWindow(panel)
        win32api.SetCursorPos(point)
        def panel_ready():
            win32gui.PumpWaitingMessages()
            return win32gui.WindowFromPoint(point) == panel
        wait_for(panel_ready)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
        def panel_active():
            win32gui.PumpWaitingMessages()
            return win32gui.GetForegroundWindow() == panel
        wait_for(panel_active)
        win32gui.SetForegroundWindow(target)
        wait_for(lambda: win32gui.GetForegroundWindow() == target)
    finally:
        win32gui.DestroyWindow(panel)
        win32gui.UnregisterClass(wc.lpszClassName, wc.hInstance)
        if cursor_position() == point:
            win32api.SetCursorPos(previous)


class Notepad:
    """Reuse one normal-sized, named proof document throughout a suite."""

    def __init__(self, file, log):
        self.file = file.resolve()
        self.log = log
        self.hwnd = None
        self.started = False
        self.positioned = False
        self.parked = None

    def scratch_point(self):
        """Find a point currently owned by the named document, including during a resize."""
        if self.file.stem not in win32gui.GetWindowText(self.hwnd):
            return None
        _, _, width, height = win32gui.GetClientRect(self.hwnd)
        for x, y in ((.5, .5), (.2, .4), (.8, .4), (.25, .75), (.75, .75)):
            point = win32gui.ClientToScreen(self.hwnd, (int(width * x), int(height * y)))
            if win32gui.GetAncestor(win32gui.WindowFromPoint(point), 2) == self.hwnd:
                return point
        return None

    def __enter__(self):
        self.cursor = cursor_position()
        self.parked = None
        def scratch_window():
            """the test document's Notepad window"""
            matches = []
            def visit(hwnd, _):
                if (win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "Notepad"
                        and self.file.stem in win32gui.GetWindowText(hwnd)):
                    matches.append(hwnd)
            win32gui.EnumWindows(visit, None)
            return matches[0] if len(matches) == 1 else None
        self.hwnd = scratch_window()
        started_at = time.monotonic()
        check = """
        $file = $env:TOUCAN_EDITOR_PATH
        if (-not [IO.Path]::IsPathFullyQualified($file) -or -not (Test-Path -LiteralPath $file -PathType Leaf)) { exit 2 }
        if ($env:TOUCAN_EDITOR_START -eq 'yes') {
            Start-Process -FilePath (Join-Path $env:SystemRoot 'System32/notepad.exe') -WindowStyle Normal -ArgumentList ('"' + $file + '"')
        }
        """
        launch_editor = not self.hwnd and not self.started
        result = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", check],
                                env={**os.environ, "TOUCAN_EDITOR_PATH": str(self.file),
                                     "TOUCAN_EDITOR_START": "yes" if launch_editor else "no"},
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        if result.returncode == 2:
            self.log.write("open-file", self.file.name, "path-missing", (time.monotonic()-started_at)*1000, driver="computer")
            raise unittest.SkipTest("Missing editor fixture. Continuing the suite.")
        result.check_returncode()
        self.started = True
        if not self.hwnd:
            if not launch_editor:
                self.log.write("reuse-notepad", self.file.name, "miss", (time.monotonic()-started_at)*1000, driver="computer")
                raise unittest.SkipTest("The existing proof tab is unavailable. Continuing the suite.")
            self.hwnd = wait_for(scratch_window, 12)
            self.log.write("launch-notepad", self.file.name, "ok", (time.monotonic()-started_at)*1000, driver="computer")
        else:
            self.log.write("reuse-notepad", self.file.name, "ok", (time.monotonic()-started_at)*1000, driver="computer")
        if not self.positioned:
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
            screen_w, screen_h = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
            w, h = min(680, screen_w//2), min(440, screen_h//2)
            win32gui.SetWindowPos(self.hwnd, 0, screen_w-w-16, 16, w, h,
                                  win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
            self.positioned = True
        if not self.active():
            try:
                win32gui.SetForegroundWindow(self.hwnd)
            except win32gui.error:
                pass  # Foreground permission can belong to another process.
        if win32gui.GetForegroundWindow() != self.hwnd:
            # Simulate the owner selecting this scratch editor during fixture setup.
            flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
            win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
            try:
                self.parked = wait_for(self.scratch_point)
                win32api.SetCursorPos(self.parked)
                if (cursor_position() != self.parked or self.file.stem not in win32gui.GetWindowText(self.hwnd)
                        or win32gui.GetAncestor(win32gui.WindowFromPoint(self.parked), 2) != self.hwnd):
                    raise AssertionError("Scratch Notepad moved before fixture activation.")
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
            finally:
                win32gui.SetWindowPos(self.hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        wait_for(self.active)
        return self

    def active(self):
        return (win32gui.GetForegroundWindow() == self.hwnd and
                self.file.stem in win32gui.GetWindowText(self.hwnd))

    def require_active(self):
        if not self.active():
            raise AssertionError("Scratch Notepad lost focus. Keyboard input stopped.")

    def park_cursor(self):
        self.require_active()
        self.parked = wait_for(self.scratch_point)
        win32api.SetCursorPos(self.parked)
        hit = win32gui.WindowFromPoint(self.parked)
        assert win32gui.GetAncestor(hit, 2) == self.hwnd
        return self.parked

    def char(self, character):
        self.require_active()
        # Send one genuine Unicode keyboard input pair to the foreground application.
        class Keyboard(ctypes.Structure):
            _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]
        class Mouse(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("data", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]
        class Payload(ctypes.Union):
            _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]
        class Input(ctypes.Structure):
            _fields_ = [("kind", wintypes.DWORD), ("data", Payload)]
        events = (Input * 2)()
        for event, flags in zip(events, (4, 6)):
            event.kind = 1
            event.data.keyboard = Keyboard(0, ord(character), flags, 0, 0)
        if ctypes.windll.user32.SendInput(2, events, ctypes.sizeof(Input)) != 2:
            raise ctypes.WinError()

    def shortcut(self, letter):
        self.require_active()
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        try:
            win32api.keybd_event(ord(letter), 0, 0, 0)
            win32api.keybd_event(ord(letter), 0, win32con.KEYEVENTF_KEYUP, 0)
        finally:
            win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)

    def __exit__(self, *_):
        if self.parked and cursor_position() == self.parked:
            win32api.SetCursorPos(self.cursor)


class Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = None
        if self.path == "/loop.webm":
            payload = VIDEO.read_bytes()
        elif self.path == "/counter":
            body = """<button onclick="count.textContent=Number(count.textContent)+1">Increment</button>
                      <output id="count">0</output>"""
        elif self.path == "/nested":
            body = '<button onclick="this.textContent=\'Frame clicked\'">Inside frame</button>'
        elif self.path == "/blank-until-reload":
            body = """<body><script>
                if (performance.getEntriesByType('navigation')[0].type === 'reload')
                    document.body.append('Restored viewport');
                </script>"""
        elif self.path in {"/blank", "/courses/1/assignments/2"}:
            body = ""
        elif self.path == "/embedded-course":
            body = f'<iframe style="border:0" src="{self.server.other}/runtime"></iframe>'
        elif self.path == "/runtime":
            body = "<script>window.API_1484_11 = {};</script>"
        elif self.path == "/black-video":
            body = """<style>html {background: black} video {filter: brightness(0)}</style>
                <video muted loop playsinline src="/loop.webm"></video>"""
        elif self.path in {"/frames", "/media"}:
            path = "/nested" if self.path == "/frames" else "/player"
            body = f'<iframe title="Fixture player" allow="autoplay" src="{self.server.other}{path}"></iframe>' + """
                <script>window.addEventListener('blur', () => setTimeout(() => {
                    if (!document.hasFocus())
                        document.querySelector('iframe').contentWindow.postMessage('fixture-blur', '*');
                }, 0));</script>"""
        elif self.path == "/player":
            body = """<video width="80" height="48" muted loop playsinline src="/loop.webm"></video>
                <button onclick="document.querySelector('video').play()">Play</button>
                <output id="blurs">0</output><script>
                function pauseWhenCovered() {
                    if (!document.hasFocus()) {
                        document.querySelector('video').pause(); blurs.textContent = Number(blurs.textContent)+1;
                    }
                }
                addEventListener('blur', () => setTimeout(pauseWhenCovered, 0));
                addEventListener('message', e => {
                    if (e.source === parent && e.data === 'fixture-blur') pauseWhenCovered();
                });</script>"""
        else:
            self.send_error(404)
            return
        if payload is None:
            payload = ("<!doctype html><title>Toucan proof</title>" + body).encode()
            kind = "text/html; charset=utf-8"
        else:
            kind = "video/webm"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


class NativeEditorHygiene(unittest.TestCase):
    def test_missing_file_logs_and_continues_without_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            log = RunLog(Path(folder) / "run.jsonl")
            editor = Notepad(Path(folder) / "missing proof file.txt", log)
            with self.assertRaises(unittest.SkipTest):
                editor.__enter__()
            self.assertFalse(editor.started)
            self.assertEqual(json.loads(log.path.read_text(encoding="utf-8"))["result"], "path-missing")


@unittest.skipUnless(sys.platform == "win32", "Requires an interactive Windows desktop")
class ParallelBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        launch()
        native_dir = Path(os.environ["LOCALAPPDATA"]) / "Toucan/fixtures/native"
        native_dir.mkdir(parents=True, exist_ok=True)
        # Windows Notepad restores its tabs after restart. Keep its proof file at a stable path.
        proof = native_dir / "toucan-foreground-proof.txt"
        try:
            proof.touch(exist_ok=False)
        except FileExistsError:
            pass
        cls.editor = Notepad(proof, RunLog(native_dir / "native.jsonl"))
        cls.pw = sync_playwright().start()
        cls.addClassCleanup(cls.pw.stop)
        cls.browser = attach(cls.pw)
        servers = [ThreadingHTTPServer(("127.0.0.1", 0), Pages) for _ in range(2)]
        cls.root = f"http://127.0.0.1:{servers[0].server_port}"
        cls.other = f"http://127.0.0.1:{servers[1].server_port}"
        for server, other in zip(servers, (cls.other, cls.root)):
            server.other = other
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cls.addClassCleanup(server.server_close)
            cls.addClassCleanup(server.shutdown)

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="toucan-proof-")
        self.addCleanup(scratch.cleanup)
        self.folder = Path(scratch.name)
        self.page = self.browser.contexts[0].new_page()
        self.addCleanup(self.page.close)
        self.log = self.folder / "run.jsonl"
        self.driver = Driver(self.page, RunLog(self.log))

    def records(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_capture_recovers_blank(self):
        self.assertTrue(self.driver.goto(self.root + "/blank-until-reload").ok)
        session = self.page.context.new_cdp_session(self.page)
        self.addCleanup(session.detach)
        session.send("Emulation.setFocusEmulationEnabled", {"enabled": False})
        cover = self.browser.contexts[0].new_page()
        self.addCleanup(cover.close)
        self.page.wait_for_function("document.hidden", timeout=5000)
        image = self.folder / "viewport.png"
        result = self.driver.shot(image)
        self.assertTrue(result.ok, result)
        self.assertTrue(image.read_bytes().startswith(b"\x89PNG"))
        self.assertTrue(self.page.evaluate("document.hidden"))
        self.assertFalse(cover.evaluate("document.hidden"))
        self.assertEqual(self.page.locator("body").inner_text(), "Restored viewport")
        cover.close()
        fresh = self.folder / "viewport.jpg"
        self.assertTrue(self.driver.shot(fresh).ok)
        self.assertTrue(fresh.read_bytes().startswith(b"\xff\xd8"))
        self.assertEqual([r["target"] for r in self.records() if r["action"] == "capture_reload"],
                         ["blank viewport"])

    def test_capture_bounds_persistent_blank(self):
        self.assertTrue(self.driver.goto(self.root + "/blank").ok)
        image = self.folder / "viewport.png"
        for _ in range(2):
            self.assertEqual(self.driver.shot(image).result, "miss")
            self.assertFalse(image.exists())
        self.assertEqual(sum(r["action"] == "capture_reload" for r in self.records()), 1)

    def test_capture_handles_discard_signal_before_screenshot(self):
        self.assertTrue(self.driver.goto(self.root + "/counter").ok)
        # Simulate the flag on one document. The reload and capture use real CDP.
        self.page.evaluate("Object.defineProperty(document, 'wasDiscarded', {value: true})")
        calls = []
        screenshot = self.page.screenshot
        def record_navigation(**options):
            calls.append(self.page.evaluate("performance.getEntriesByType('navigation')[0].type"))
            return screenshot(**options)
        with patch.object(self.page, "screenshot", side_effect=record_navigation):
            result = self.driver.shot(self.folder / "viewport.png")
        self.assertTrue(result.ok, result)
        self.assertEqual(calls, ["reload"])
        self.assertEqual([r["target"] for r in self.records() if r["action"] == "capture_reload"],
                         ["Chrome reported a restored discard"])

    def test_capture_timeout_has_its_own_reason(self):
        self.assertTrue(self.driver.goto(self.root + "/counter").ok)
        screenshot = self.page.screenshot
        attempts = []
        def timeout_once(**options):
            attempts.append(options)
            if len(attempts) == 1:
                raise PlaywrightTimeout("Fixture timeout")
            return screenshot(**options)
        with patch.object(self.page, "screenshot", side_effect=timeout_once):
            result = self.driver.shot(self.folder / "viewport.png")
        self.assertTrue(result.ok, result)
        self.assertEqual([r["target"] for r in self.records() if r["action"] == "capture_retry"],
                         ["screenshot timed out"])
        self.assertFalse(any(r["action"] == "capture_reload" for r in self.records()))

    def test_timeout_after_discard_retries_only_the_restored_document(self):
        self.assertTrue(self.driver.goto(self.root + "/counter").ok)
        self.page.evaluate("Object.defineProperty(document, 'wasDiscarded', {value: true})")
        capture = self.driver._viewport
        requests = []
        def delayed_paint(kind, *, background_recovery=False):
            requests.append(background_recovery)
            if len(requests) == 1:
                return b"", "screenshot timed out"
            return capture(kind, background_recovery=background_recovery)
        with patch.object(self.driver, "_viewport", side_effect=delayed_paint):
            result = self.driver.shot(self.folder / "restored.png")
        self.assertTrue(result.ok, result)
        self.assertEqual(requests, [False, True])
        self.assertEqual(sum(r["action"] == "capture_reload" for r in self.records()), 1)

    def test_capture_preserves_course_frame_and_popup(self):
        self.assertTrue(self.driver.goto(self.root + "/embedded-course").ok)
        self.page.frame_locator("iframe").locator("body").wait_for(state="attached")
        self.assertEqual(self.driver.shot(self.folder / "course.png").result, "miss")
        self.assertEqual(self.page.evaluate("performance.getEntriesByType('navigation')[0].type"), "navigate")
        self.assertTrue(self.driver.goto(self.root + "/courses/1/assignments/2").ok)
        with self.page.expect_popup() as opened:
            self.page.evaluate("() => { window.open('/blank') }")
        popup = opened.value
        self.addCleanup(popup.close)
        self.driver.use_popup(popup)
        popup.wait_for_load_state()
        self.assertEqual(self.driver.shot(self.folder / "popup.png").result, "miss")
        self.assertEqual(popup.evaluate("performance.getEntriesByType('navigation')[0].type"), "navigate")
        self.assertEqual(sum(r["action"] == "capture_preserved" for r in self.records()), 2)

    def test_blank_playing_video_keeps_its_playhead(self):
        self.assertTrue(self.driver.goto(self.root + "/black-video").ok)
        self.page.locator("video").evaluate("v => v.play()")
        self.assertTrue(self.driver.assert_progress(5).ok)
        self.assertEqual(self.driver.shot(self.folder / "video.png").result, "miss")
        self.assertTrue(self.driver.assert_progress(5).ok)
        self.assertEqual(self.page.evaluate("performance.getEntriesByType('navigation')[0].type"), "navigate")
        self.assertFalse(any(r["action"] == "capture_reload" for r in self.records()))

    def test_capture_failure_does_not_replay_media_or_handoff(self):
        self.assertTrue(self.driver.goto(self.root + "/counter").ok)
        task = dict(url=self.page.url, actions=[], native_fallback="Notepad")
        with patch.object(self.driver, "shot", return_value=Outcome("miss", detail="blank viewport")), \
                patch("tools.browser.run.time.sleep"), patch("tools.browser.run.decide") as decide:
            result = run_loop(task, self.driver, self.folder / "transcript.md", self.folder)
        decide.assert_not_called()
        self.assertEqual(result.result, "miss")
        self.assertIn("Three unavailable viewport captures", result.detail)
        self.assertFalse(any(r["action"] in {"click", "assert_progress", "handoff"} for r in self.records()))

    def test_background_click(self):
        self.assertTrue(self.driver.goto(self.root + "/counter").ok)
        with self.editor as editor:
            self.assertNotEqual(win32gui.GetWindowPlacement(editor.hwnd)[1], win32con.SW_SHOWMAXIMIZED)
            point = editor.park_cursor()
            self.assertTrue(self.driver.click("Increment").ok)
            self.assertEqual(self.page.locator("#count").inner_text(), "1")
            self.assertEqual(cursor_position(), point)
            editor.require_active()
        print("Background click fired with the same OS cursor coordinates and foreground window.")

    def test_occupancy(self):
        self.assertTrue(self.driver.goto(self.root + "/counter").ok)
        letters = "toucan" * 7
        with self.editor as editor:
            editor.park_cursor()
            editor.shortcut("A")
            start = time.monotonic()
            failures = []
            def type_into_notepad():
                try:
                    for i, character in enumerate(letters):
                        time.sleep(max(0, start + .46 * i - time.monotonic()))
                        editor.char(character)
                except BaseException as error:
                    failures.append(error)
            worker = threading.Thread(target=type_into_notepad)
            worker.start()
            try:
                for i in range(10):
                    time.sleep(max(0, start + 2 * i - time.monotonic()))
                    editor.require_active()
                    self.assertTrue(self.driver.click("Increment").ok)
                    editor.require_active()
                time.sleep(max(0, start + 20 - time.monotonic()))
            finally:
                worker.join()
            self.assertFalse(failures, failures)
            self.assertEqual(self.page.locator("#count").inner_text(), "10")
            editor.shortcut("S")
            wait_for(lambda: editor.file.read_text(encoding="utf-8-sig") == letters)
        print(f"20 seconds: 10/10 CDP clicks and {len(letters)}/{len(letters)} foreground Notepad characters.")

    def test_cross_origin_frames(self):
        self.assertTrue(self.driver.goto(self.root + "/frames").ok)
        self.page.frame_locator("iframe").get_by_role("button", name="Inside frame").wait_for()
        self.assertEqual({f["url"] for f in self.driver.frames().value},
                         {self.root + "/frames", self.other + "/nested"})
        clicked = self.driver.click({"role": "button", "name": "Inside frame"})
        self.assertTrue(clicked.ok, clicked)
        self.assertTrue(any("Frame clicked" in f["text"] for f in self.driver.read().value))
        print("Clicked the button in the second HTTP origin and read the changed frame text.")

    def test_closed_port_fallback(self):
        with socket.socket() as reserved, io.StringIO() as warning:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
            task = dict(url=self.root + "/counter", goal="Read the fixture.", actions=[], done_text="Increment")
            with contextlib.redirect_stderr(warning):
                result = run({**task, "native_fallback": "Notepad"}, self.folder,
                             port=port, attach_timeout=.3, start_browser=False)
            self.assertEqual(result.result, "escalate")
            self.assertIn("no longer background-safe", warning.getvalue())
            self.assertTrue(any(r["driver"] == "computer" and r["action"] == "handoff" for r in self.records()))
            result = run(task, self.folder / "browser-only", port=port, attach_timeout=.3, start_browser=False)
            self.assertEqual(result.result, "stall")
        print("Closed port reported a native handoff and foreground warning; browser-only task stopped.")

    def test_blur_pause(self):
        self.assertTrue(self.driver.goto(self.root + "/media").ok)
        focus_test_browser(self.page)
        self.page.frame_locator("iframe").get_by_role("button", name="Play").wait_for()
        frame = self.page.frame(url=self.other + "/player")
        self.assertIsNotNone(frame)
        self.assertEqual(self.page.main_frame.locator("video").count(), 0)
        session = self.page.context.new_cdp_session(self.page)
        try:
            session.send("Emulation.setFocusEmulationEnabled", {"enabled": False})
            frame.get_by_role("button", name="Play").wait_for()
            clicked = self.driver.click("Play")
            self.assertTrue(clicked.ok, clicked)
            self.assertTrue(self.driver.assert_progress(5).ok)
            with self.editor as editor:
                editor.park_cursor()
                frame.wait_for_function("Number(document.querySelector('#blurs').textContent) > 0 && document.querySelector('video').paused", timeout=5000)
                recovered = verify_after_play(self.driver, "Play", 5)
                self.assertTrue(recovered.ok, recovered)
                editor.require_active()
                events = [(r["action"], r["result"]) for r in self.records()]
                self.assertEqual(events[-3:], [("assert_progress", "stall"), ("click", "ok"), ("assert_progress", "ok")])
        finally:
            session.detach()
        print("Real window blur paused the looping iframe video. Five-second stall detected; replay advanced.")

if __name__ == "__main__":
    unittest.main(verbosity=2)
