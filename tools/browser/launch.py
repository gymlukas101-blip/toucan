"""Start or reuse the Chrome instance belonging to Toucan."""

import argparse
import http.client
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import psutil


HOST = "127.0.0.1"
PORT = 9222
ENDPOINT = f"http://{HOST}:{PORT}"
PROFILE = Path(os.environ["LOCALAPPDATA"]) / "Toucan" / "chrome-profile"


class BrowserUnavailable(RuntimeError):
    pass


def find_chrome() -> Path:
    import winreg

    for name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if base := os.environ.get(name):
            candidate = Path(base) / "Google/Chrome/Application/chrome.exe"
            if candidate.is_file():
                return candidate
    key = r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key, access=winreg.KEY_READ | view) as handle:
                    candidate = Path(os.path.expandvars(winreg.QueryValueEx(handle, "")[0]).strip('"'))
            except OSError:
                continue
            if candidate.is_file():
                return candidate
    raise BrowserUnavailable("Chrome was not found in Program Files, LocalAppData or Windows App Paths.")


def version(port: int = PORT, timeout: float = 0.7) -> dict | None:
    """Probe exactly localhost. Never follow redirects or use an HTTP proxy."""
    connection = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        try:
            connection.connect()
        except (ConnectionRefusedError, TimeoutError):
            if any(row.laddr.port == port and row.status == psutil.CONN_LISTEN
                   for row in psutil.net_connections(kind="tcp")):
                raise BrowserUnavailable(f"Port {port} is occupied but did not accept the probe.")
            return None
        connection.request("GET", "/json/version")
        response = connection.getresponse()
        if response.status != 200:
            raise BrowserUnavailable(f"Port {port} returned HTTP {response.status} for /json/version.")
        data = json.loads(response.read(32769))
        address = urlsplit(data["webSocketDebuggerUrl"])
        if (address.scheme, address.hostname, address.port) != ("ws", HOST, port):
            raise BrowserUnavailable(f"Port {port} did not advertise its own loopback debugger.")
        if not str(data.get("Browser", "")).startswith("Chrome/"):
            raise BrowserUnavailable(f"Port {port} did not identify itself as Chrome.")
        return data
    except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError) as exc:
        raise BrowserUnavailable(f"Port {port} did not return a valid Chrome version response.") from exc
    finally:
        connection.close()


def verify_owner(port: int = PORT) -> int:
    listeners = [row for row in psutil.net_connections(kind="tcp") if
                 row.status == psutil.CONN_LISTEN and row.laddr.port == port]
    if not listeners or any(row.laddr.ip not in (HOST, "::1") for row in listeners):
        raise BrowserUnavailable(f"Expected a loopback-only Chrome listener on {port}.")
    pids = {row.pid for row in listeners}
    if len(pids) != 1 or None in pids:
        raise BrowserUnavailable(f"Cannot verify the owner of port {port}.")
    pid = pids.pop()
    try:
        process = psutil.Process(pid)
        args = process.cmdline()
        profiles = [Path(arg.split("=", 1)[1].strip('"')).resolve()
                    for arg in args if arg.startswith("--user-data-dir=")]
        if process.name().lower() == "chrome.exe" and profiles == [PROFILE.resolve()]:
            return pid
    except (psutil.Error, OSError) as exc:
        raise BrowserUnavailable(f"Could not inspect the browser listening on {port}.") from exc
    raise BrowserUnavailable(f"Port {port} belongs to a different browser or profile. Toucan cannot attach.")


def wait_ready(port: int = PORT, timeout: float = 20) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if version(port, min(0.7, max(0.01, deadline - time.monotonic()))) is not None:
            return verify_owner(port)
        time.sleep(min(0.15, max(0, deadline - time.monotonic())))
    raise BrowserUnavailable(f"Toucan Chrome did not answer at {HOST}:{port} within {timeout:g}s.")


def allow_course_popups(profile: Path) -> None:
    """Configure the dedicated profile before Chrome takes ownership of its preferences."""
    directory = profile / "Default"
    directory.mkdir(parents=True, exist_ok=True)
    prefs = directory / "Preferences"
    if prefs.resolve().parent != profile.resolve() / "Default" or prefs.is_symlink():
        raise BrowserUnavailable("Chrome preferences must belong to the dedicated profile.")
    values = json.loads(prefs.read_text(encoding="utf-8")) if prefs.exists() else {}
    settings = values.setdefault("profile", {}).setdefault("default_content_setting_values", {})
    if settings.get("popups") == 1:
        return
    settings["popups"] = 1
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as tmp:
        path = Path(tmp.name)
    try:
        path.write_text(json.dumps(values, separators=(",", ":")), encoding="utf-8")
        os.replace(path, prefs)
    finally:
        path.unlink(missing_ok=True)


def apply_running_popup_preference(browser):
    """Update Chrome's live setting when reusing a profile started by an older runner."""
    prefs = PROFILE / "Default" / "Preferences"
    if prefs.is_file():
        settings = json.loads(prefs.read_text(encoding="utf-8")).get("profile", {}).get("default_content_setting_values", {})
        if settings.get("popups") == 1:
            return
    url = "chrome://settings/content/popups"
    session = browser.new_browser_cdp_session()
    try:
        with browser.contexts[0].expect_page(timeout=10000) as created:
            # Settings defers rendering its radio group in an inactive tab.
            session.send("Target.createTarget", {"url": url, "newWindow": True, "background": True})
        page = created.value
        try:
            page.wait_for_url(url, timeout=10000)
            allow = page.get_by_role("radio", name="Sites can send pop-ups and use redirects", exact=True)
            allow.wait_for(timeout=10000)
            if not allow.is_checked():
                allow.evaluate("e => e.click()")
            page.get_by_role("radio", name="Sites can send pop-ups and use redirects",
                             exact=True, checked=True).wait_for(timeout=5000)
        finally:
            page.close()
    finally:
        session.detach()


def launch() -> int:
    if version() is not None:
        return verify_owner()
    chrome = find_chrome()
    PROFILE.mkdir(parents=True, exist_ok=True)
    if PROFILE.is_symlink() or PROFILE.resolve() != PROFILE.absolute():
        raise BrowserUnavailable("The dedicated Chrome profile must resolve to its own directory.")
    allow_course_popups(PROFILE)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 4  # SW_SHOWNOACTIVATE
    subprocess.Popen([
        str(chrome), f"--remote-debugging-port={PORT}", f"--remote-debugging-address={HOST}",
        f"--user-data-dir={PROFILE}", "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding", "--disable-popup-blocking",
        "--no-first-run", "--no-default-browser-check", "about:blank",
    ], startupinfo=startup, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
       stderr=subprocess.DEVNULL, close_fds=True)
    return wait_ready()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        print(f"Toucan Chrome is ready at {ENDPOINT} (PID {launch()}).")
    except BrowserUnavailable as error:
        parser.exit(1, f"{error}\n")
