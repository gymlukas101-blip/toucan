"""Run the five Windows proofs against Toucan Chrome and disposable local surfaces."""

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

if sys.platform != "win32":
    raise unittest.SkipTest("The parallel-use proof requires Windows.")

import win32api
import win32con
import win32gui
import win32process
from playwright.sync_api import sync_playwright

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.browser.attach import attach
from tools.browser.driver import Driver, RunLog, cursor_position
from tools.browser.launch import launch, verify_owner
from tools.browser.run import run, verify_after_play


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
    """Type only while the uniquely named, disposable Notepad document owns the foreground."""

    def __init__(self, folder):
        self.file = folder / ("toucan-proof-" + uuid.uuid4().hex + ".txt")
        self.file.write_bytes(b"")
        self.cursor = cursor_position()
        self.parked = None

    def __enter__(self):
        win32api.ShellExecute(0, "open", str(Path(os.environ["SYSTEMROOT"]) / "System32/notepad.exe"),
                              subprocess.list2cmdline([str(self.file)]), None, win32con.SW_SHOWNORMAL)
        def scratch_window():
            """the test document's Notepad window"""
            matches = []
            def visit(hwnd, _):
                if (win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "Notepad"
                        and self.file.stem in win32gui.GetWindowText(hwnd)):
                    matches.append(hwnd)
            win32gui.EnumWindows(visit, None)
            return matches[0] if len(matches) == 1 else None
        self.hwnd = wait_for(scratch_window, 12)
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWMAXIMIZED)
        if win32gui.GetForegroundWindow() != self.hwnd:
            # Simulate the owner selecting this scratch editor during fixture setup.
            flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
            win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
            try:
                x1, y1, x2, y2 = win32gui.GetWindowRect(self.hwnd)
                self.parked = ((x1 + x2) // 2, (y1 + y2) // 2)
                win32api.SetCursorPos(self.parked)
                wait_for(lambda: win32gui.GetAncestor(win32gui.WindowFromPoint(self.parked), 2) == self.hwnd)
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
        x1, y1, x2, y2 = win32gui.GetWindowRect(self.hwnd)
        self.parked = ((x1 + x2) // 2, (y1 + y2) // 2)
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
        if self.active():
            self.shortcut("S")
            time.sleep(.2)
            if self.active():
                self.shortcut("W")
                wait_for(lambda: not win32gui.IsWindow(self.hwnd) or
                         self.file.stem not in win32gui.GetWindowText(self.hwnd))
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


@unittest.skipUnless(sys.platform == "win32", "Requires an interactive Windows desktop")
class ParallelBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        launch()
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

    def test_background_click(self):
        self.assertTrue(self.driver.goto(self.root + "/counter").ok)
        with Notepad(self.folder) as editor:
            point = editor.park_cursor()
            self.assertTrue(self.driver.click("Increment").ok)
            self.assertEqual(self.page.locator("#count").inner_text(), "1")
            self.assertEqual(cursor_position(), point)
            editor.require_active()
        print("Background click fired with the same OS cursor coordinates and foreground window.")

    def test_occupancy(self):
        self.assertTrue(self.driver.goto(self.root + "/counter").ok)
        letters = "toucan" * 7
        with Notepad(self.folder) as editor:
            editor.park_cursor()
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
            with Notepad(self.folder) as editor:
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
