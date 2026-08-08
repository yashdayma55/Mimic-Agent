"""
Browser tier for the locator. Holds one Playwright connection to the
running debug Chrome and finds/acts on elements inside web pages.

The engine calls:
  connect_browser()        once, to attach to Chrome
  find_in_browser(step)    to locate an element for a step
  is_browser_step(step)    to decide desktop vs browser routing
  disconnect_browser()     at the end
"""

from playwright.sync_api import sync_playwright
import atexit

_pw = None          # the playwright context manager
_browser = None     # the connected browser
_atexit_registered = False


def connect_browser(cdp_url="http://localhost:9222"):
    """Attach to the running debug Chrome. Call once at start of a browser run."""
    global _pw, _browser, _atexit_registered
    if _browser is not None:
        return True
    try:
        _pw = sync_playwright().start()
        _browser = _pw.chromium.connect_over_cdp(cdp_url)
        print("   [browser] connected to Chrome")
        if not _atexit_registered:
            atexit.register(disconnect_browser)
            _atexit_registered = True
        return True
    except Exception as e:
        print(f"   [browser] could NOT connect ({e}) - is debug Chrome running?")
        # clean up a half-started playwright driver so exit doesn't EPIPE
        disconnect_browser()
        return False


def disconnect_browser():
    """Close CDP connection + stop Playwright; swallow errors (no EPIPE traceback)."""
    global _pw, _browser
    had = _browser is not None or _pw is not None
    try:
        if _browser is not None:
            try:
                _browser.close()
            except Exception:
                pass
        if _pw is not None:
            try:
                _pw.stop()
            except Exception:
                pass
        if had:
            print("   [browser] closed")
    except Exception:
        pass
    finally:
        _browser = None
        _pw = None


def _active_page():
    """Return the frontmost / best-guess page across all tabs.

    Prefers the tab matching the foreground Chrome window title; otherwise the
    most recently opened real (non-chrome://) page; otherwise the newest blank.
    """
    if not _browser or not _browser.contexts:
        return None
    pages = []
    for ctx in _browser.contexts:
        try:
            pages.extend(ctx.pages)
        except Exception:
            continue
    if not pages:
        return None

    def _url_of(pg):
        try:
            return (pg.url or "").strip()
        except Exception:
            return ""

    def _title_of(pg):
        try:
            return (pg.title() or "").strip()
        except Exception:
            return ""

    def _is_blank(url):
        u = (url or "").lower()
        return (
            not u
            or u == "about:blank"
            or u.startswith("chrome://")
            or u.startswith("edge://")
        )

    # 1) Match the OS-frontmost Chrome window title to a tab
    try:
        import win32gui
        fg_title = win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
        tab_title = fg_title
        for suffix in (" - Google Chrome", " - Chrome", " - Chromium", " - Microsoft Edge"):
            if tab_title.endswith(suffix):
                tab_title = tab_title[: -len(suffix)]
                break
        needle = tab_title.strip().lower()
        if needle:
            # Exact title match first
            for pg in pages:
                if _title_of(pg).lower() == needle:
                    return pg
            # Fuzzy contains (e.g. truncated titles)
            for pg in pages:
                pt = _title_of(pg).lower()
                if pt and (needle in pt or pt in needle):
                    return pg
            # New Tab with blank URL
            if needle in ("new tab", "新标签页", "nouvel onglet"):
                blanks = [pg for pg in pages if _is_blank(_url_of(pg))]
                if blanks:
                    return blanks[-1]
    except Exception:
        pass

    # 2) Prefer real pages (last in list ≈ most recently created/navigated)
    real = [pg for pg in pages if not _is_blank(_url_of(pg))]
    if real:
        return real[-1]

    # 3) Only blanks / chrome:// — pick the newest
    return pages[-1]


def is_browser_step(step):
    """Heuristic: is this step a browser/web action?
    We treat a step as browser if its window title looks like a browser tab
    (contains a site name / ' - Google Chrome') or the elem_type is web-ish.
    For now: if the recorded window title looks like a Chrome tab."""
    wt = (step.get("window_title") or "").lower()
    if "chrome" in wt or "- google chrome" in wt:
        return True
    # tab-switch steps in the plan name a tab explicitly
    name = (step.get("elem_name") or "").lower()
    if "|" in name and ("ai" in name or "claude" in name or "linkedin" in name):
        return True
    return False


def find_in_browser(step, verbose=True):
    """Find an element in the active page by name across roles, then text.
    Returns a Playwright locator (has .click(), .fill()) or None."""
    page = _active_page()
    if page is None:
        if verbose:
            print("   [browser] no active page")
        return None, None

    name = step.get("elem_name", "")
    if not name:
        return None, None

    # browser sub-tiers: role+name, then plain text
    for role in ["button", "link", "textbox", "combobox", "checkbox", "tab"]:
        try:
            el = page.get_by_role(role, name=name)
            if el.count() > 0:
                if verbose:
                    print(f"   [browser] found '{name}' as {role}")
                return el.first, page
        except Exception:
            continue
    try:
        el = page.get_by_text(name, exact=False)
        if el.count() > 0:
            if verbose:
                print(f"   [browser] found '{name}' by text")
            return el.first, page
    except Exception:
        pass

    if verbose:
        print(f"   [browser] NOT FOUND: '{name}'")
    return None, page


if __name__ == "__main__":
    # quick self-test: connect and try to find something on the open page
    if connect_browser():
        test = {"elem_name": "Gmail", "action": "click"}
        el, page = find_in_browser(test)
        if el:
            print("self-test: found an element, browser tier works")
        else:
            print("self-test: element not found (open a page with that element)")
        disconnect_browser()