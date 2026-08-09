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


def _url_of(page):
    try:
        return (page.url or "").strip()
    except Exception:
        return ""


def _title_of(page):
    try:
        return (page.title() or "").strip()
    except Exception:
        return ""


def _is_blank_url(url):
    """True for empty / about:blank / chrome|edge internal pages (incl. New Tab)."""
    u = (url or "").lower().strip()
    return (
        not u
        or u == "about:blank"
        or u.startswith("chrome://newtab")
        or u.startswith("chrome://new-tab-page")
        or u.startswith("chrome://")
        or u.startswith("edge://")
    )


def _is_page_visible(page):
    """Foreground tab in its window: document.visibilityState == 'visible'."""
    try:
        return page.evaluate("() => document.visibilityState") == "visible"
    except Exception:
        try:
            return page.evaluate("document.visibilityState") == "visible"
        except Exception:
            return False


def _pick_active_page(pages):
    """Choose the best page to perceive/act on.

    Ordering (after excluding blanks when any real URL exists):
      visible & real-url  >  visible  >  real-url  >  anything
    Among ties, prefer an OS-title match, else the last page in the list
    (≈ most recently created/activated).
    """
    if not pages:
        return None

    real = [pg for pg in pages if not _is_blank_url(_url_of(pg))]
    # Prefer real content tabs whenever any exist; only use blanks if that is all we have
    pool = real if real else list(pages)

    meta = []
    for pg in pool:
        url = _url_of(pg)
        blank = _is_blank_url(url)
        visible = _is_page_visible(pg)
        if visible and not blank:
            rank = 0
        elif visible:
            rank = 1
        elif not blank:
            rank = 2
        else:
            rank = 3
        meta.append((rank, pg, url))

    best_rank = min(m[0] for m in meta)
    candidates = [m[1] for m in meta if m[0] == best_rank]

    # Optional tie-break: match the OS-frontmost Chrome window title
    chosen = None
    try:
        import win32gui
        fg_title = win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
        tab_title = fg_title
        for suffix in (" - Google Chrome", " - Chrome", " - Chromium", " - Microsoft Edge"):
            if tab_title.endswith(suffix):
                tab_title = tab_title[: -len(suffix)]
                break
        needle = tab_title.strip().lower()
        # Never use a "New Tab" title to force a blank when real pages exist
        if needle and needle not in ("new tab", "新标签页", "nouvel onglet"):
            for pg in candidates:
                if _title_of(pg).lower() == needle:
                    chosen = pg
                    break
            if chosen is None:
                for pg in candidates:
                    pt = _title_of(pg).lower()
                    if pt and (needle in pt or pt in needle):
                        chosen = pg
                        break
    except Exception:
        pass

    if chosen is None:
        chosen = candidates[-1]

    url = _url_of(chosen)
    title = _title_of(chosen) or "(no title)"
    print(f"[browser] active page: {title!r} ({url or 'about:blank'})")
    return chosen


def _active_page():
    """Return the frontmost / best-guess page across all tabs.

    Prefers a visible real (non-blank) tab via document.visibilityState;
    skips New Tab / chrome:// when any real page exists.
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
    return _pick_active_page(pages)


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