"""Shared CDP helpers — reuses browser_locator from the core engine."""

from __future__ import annotations

import json
import re
import time
import urllib.request

_LINKEDIN_PROFILE_RE = re.compile(
    r"https?://(?:[\w-]+\.)?linkedin\.com/in/[\w%-]+/?",
    re.I,
)

_CONNECT_TIMEOUT_SEC = 20
_CONNECT_RETRIES = 3
_CONNECT_BACKOFF_SEC = 1.5
_CDP = "http://127.0.0.1:9222"
_LAST_CONNECT_ERR = ""
_WORKING_CDP_URL = _CDP


def cdp_debug_info(port: int = 9222) -> tuple[bool, str]:
    """Check CDP HTTP endpoint before Playwright attach. Returns (ok, detail)."""
    global _WORKING_CDP_URL
    last = ""
    for host in ("127.0.0.1", "localhost"):
        url = f"http://{host}:{port}/json/version"
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if getattr(resp, "status", 200) != 200:
                    last = f"debug Chrome HTTP {resp.status} on {host}:{port}"
                    continue
                data = json.loads(resp.read().decode("utf-8"))
                browser = data.get("Browser") or data.get("browser") or "Chrome"
                _WORKING_CDP_URL = f"http://{host}:{port}"
                ws = data.get("webSocketDebuggerUrl") or "ws ok"
                return True, f"{browser} on {host}:{port} ({ws[:48]}...)"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    return False, f"debug Chrome not on {port} ({last})"


def _debug_port_open(port: int = 9222) -> bool:
    ok, _detail = cdp_debug_info(port)
    return ok


def _cdp_json(path: str, timeout: float = 1.0):
    try:
        with urllib.request.urlopen(f"{_WORKING_CDP_URL}{path}", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        try:
            with urllib.request.urlopen(f"{_CDP}{path}", timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None


def list_cdp_targets():
    """Lightweight tab list via CDP HTTP — no Playwright."""
    data = _cdp_json("/json/list")
    return data if isinstance(data, list) else []


def _pick_best_target(targets: list) -> dict | None:
    """Prefer visible LinkedIn /in/ profile tab."""
    if not targets:
        return None

    def score(t):
        url = (t.get("url") or "").lower()
        s = 0
        if "linkedin.com/in/" in url:
            s += 10
        elif "linkedin.com" in url:
            s += 3
        if t.get("type") == "page":
            s += 1
        return s

    ranked = sorted(targets, key=score, reverse=True)
    return ranked[0] if ranked else None


def last_connect_error() -> str:
    return _LAST_CONNECT_ERR


def _browser_alive() -> bool:
    """True if the existing Playwright CDP session still answers."""
    try:
        from browser_locator import _browser
        if _browser is None:
            return False
        _ = _browser.contexts
        return True
    except Exception:
        return False


def connect(
    timeout: float = _CONNECT_TIMEOUT_SEC,
    *,
    retries: int = _CONNECT_RETRIES,
    backoff: float = _CONNECT_BACKOFF_SEC,
    verbose: bool = True,
) -> bool:
    """Attach Playwright to debug Chrome with pre-check + retries.

    Runs connect_over_cdp on THIS thread (Playwright sync is not thread-safe).
    The old thread-join timeout aborted Playwright's own CDP wait and produced
    the vague '[apollo] Playwright attach failed' skip.
    """
    global _LAST_CONNECT_ERR

    try:
        from browser_locator import disconnect_browser
        if _browser_alive():
            return True
        from browser_locator import _browser as _b
        if _b is not None:
            if verbose:
                print("   [browser_util] stale Playwright connection; reconnecting")
            try:
                disconnect_browser()
            except Exception:
                pass
    except Exception:
        pass

    ok, detail = cdp_debug_info()
    if not ok:
        _LAST_CONNECT_ERR = detail
        if verbose:
            try:
                from email_workflow_automation import config as ewa_config

                if not ewa_config.REQUIRE_DEBUG_CHROME:
                    print(
                        f"   [browser_util] CDP not available ({detail}) — "
                        "ok for vision-first outreach (REQUIRE_DEBUG_CHROME=False)"
                    )
                    return False
            except Exception:
                pass
            print(f"   [browser_util] {detail}")
            print("   [browser_util] start debug Chrome (--remote-debugging-port=9222)")
        return False
    if verbose:
        print(f"   [browser_util] CDP endpoint ok: {detail}")

    last_err = "unknown"
    timeout_ms = int(max(3.0, float(timeout)) * 1000)
    for attempt in range(1, max(1, retries) + 1):
        if verbose and attempt > 1:
            print(f"   [browser_util] Playwright attach retry {attempt}/{retries}...")
        try:
            from browser_locator import connect_browser, disconnect_browser
            if attempt > 1:
                try:
                    disconnect_browser()
                except Exception:
                    pass
            attached = bool(connect_browser(_WORKING_CDP_URL, timeout_ms=timeout_ms))
            if attached and _browser_alive():
                _LAST_CONNECT_ERR = ""
                if verbose:
                    print(f"   [browser_util] Playwright attached (attempt {attempt})")
                return True
            from browser_locator import last_browser_error
            detail = last_browser_error()
            if not attached:
                last_err = detail or "connect_browser returned False"
            else:
                last_err = "attached but browser session is dead"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            try:
                from browser_locator import disconnect_browser
                disconnect_browser()
            except Exception:
                pass

        if verbose:
            print(f"   [browser_util] attach attempt {attempt} failed: {last_err}")
        if attempt < retries:
            time.sleep(backoff * attempt)

    _LAST_CONNECT_ERR = last_err
    if verbose:
        print(
            f"   [browser_util] Playwright attach failed after {retries} "
            f"attempt(s): {last_err}"
        )
    return False


def all_pages() -> list:
    """Every Playwright page in the attached Chrome (tabs + extension views)."""
    if not connect():
        return []
    from browser_locator import _browser
    pages = []
    if _browser is None:
        return pages
    for ctx in _browser.contexts:
        try:
            pages.extend(ctx.pages)
        except Exception:
            continue
    return pages


def active_page_info() -> dict:
    """Return active page title/url after attach, or error detail."""
    if not connect():
        err = last_connect_error() or "Playwright attach failed"
        return {"ok": False, "note": err}
    from browser_locator import _active_page
    try:
        page = _active_page()
    except Exception as e:
        return {"ok": False, "note": f"active page probe failed: {type(e).__name__}: {e}"}
    if page is None:
        return {"ok": False, "note": "no active page after attach"}
    return {
        "ok": True,
        "title": page_title(page),
        "url": page_url(page),
    }


def active_page():
    from browser_locator import _active_page
    if not connect():
        return None
    return _active_page()


def active_target_http() -> dict | None:
    """Best LinkedIn-ish tab via CDP HTTP when Playwright attach is slow."""
    targets = [t for t in list_cdp_targets() if t.get("type") == "page"]
    return _pick_best_target(targets)


def page_url(page) -> str:
    try:
        return (page.url or "").strip()
    except Exception:
        return ""


def page_title(page) -> str:
    try:
        if isinstance(page, dict):
            return (page.get("title") or "").strip()
        return (page.title() or "").strip()
    except Exception:
        return ""


def is_linkedin_profile_url(url: str) -> bool:
    return bool(_LINKEDIN_PROFILE_RE.search(url or ""))


def normalize_linkedin_profile_url(url: str) -> str:
    m = _LINKEDIN_PROFILE_RE.search(url or "")
    if not m:
        return (url or "").strip()
    return m.group(0).rstrip("/")


def require_linkedin_profile(page):
    """Return (ok, url, note). Accepts Playwright page or HTTP target dict."""
    if isinstance(page, dict):
        url = (page.get("url") or "").strip()
    else:
        url = page_url(page)
    if not url:
        return False, "", "no active page URL"
    if "linkedin.com" not in url.lower():
        return False, url, f"not on LinkedIn (url={url!r})"
    if not is_linkedin_profile_url(url):
        return False, url, f"not a /in/ profile page (url={url!r})"
    return True, normalize_linkedin_profile_url(url), ""


def switch_to_tab(match: str) -> bool:
    """Bring a Chrome tab whose title or URL contains *match* to the foreground."""
    if not connect():
        return False
    from browser_locator import _browser
    if _browser is None:
        return False
    needle = match.lower()
    for ctx in _browser.contexts:
        for p in ctx.pages:
            try:
                t = (p.title() or "").lower()
                u = (p.url or "").lower()
                if needle in t or needle in u:
                    p.bring_to_front()
                    return True
            except Exception:
                continue
    return False


def open_new_tab(url: str) -> bool:
    """Open *url* in a new Chrome tab (Ctrl+T then navigate)."""
    if not connect():
        return False
    from browser_locator import _browser
    if _browser is None:
        return False
    try:
        ctx = _browser.contexts[0]
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return True
    except Exception as e:
        print(f"  [browser_util] open_new_tab failed: {e}")
        return False


def find_tab(match: str):
    """Return a Playwright page matching *match*, or None."""
    if not connect():
        return None
    from browser_locator import _browser
    if _browser is None:
        return None
    needle = match.lower()
    for ctx in _browser.contexts:
        for p in ctx.pages:
            try:
                t = (p.title() or "").lower()
                u = (p.url or "").lower()
                if needle in t or needle in u:
                    return p
            except Exception:
                continue
    return None


if __name__ == "__main__":
    print("=" * 60)
    print("SELF-TEST TASK 1 — Playwright CDP attach")
    print("Requires debug Chrome on port 9222")
    print("=" * 60)

    ok, detail = cdp_debug_info()
    print(f"CDP endpoint: ok={ok}")
    print(f"  {detail}")
    if not ok:
        print("\nSTOP — start debug Chrome first, then re-run.")
    else:
        info = active_page_info()
        print(f"\nPlaywright attach: ok={info.get('ok')}")
        if info.get("ok"):
            print("  Playwright attached and can see the active page:")
            print(f"  title: {info.get('title')!r}")
            print(f"  url:   {info.get('url')!r}")
            print("\nTask 1 PASS if title/url look right.")
        else:
            print(f"  exact error: {info.get('note')}")
            print("\nSTOP — attach failed; see error above.")
    print("STOP after Task 1.")

