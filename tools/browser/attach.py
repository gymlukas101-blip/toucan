"""Connect Playwright to the verified Toucan profile and leave Chrome running."""

from time import monotonic

from playwright.sync_api import Browser, Error, Playwright

if __package__:
    from .launch import HOST, PORT, BrowserUnavailable, wait_ready
else:
    from launch import HOST, PORT, BrowserUnavailable, wait_ready


def attach(playwright: Playwright, *, port: int = PORT, timeout: float = 20) -> Browser:
    deadline = monotonic() + timeout
    wait_ready(port, timeout)
    try:
        return playwright.chromium.connect_over_cdp(
            f"http://{HOST}:{port}", timeout=max(1, (deadline - monotonic()) * 1000),
        )
    except Error as exc:
        raise BrowserUnavailable(f"Cannot attach Playwright to Toucan Chrome on port {port}.") from exc
