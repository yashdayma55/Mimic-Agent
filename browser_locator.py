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

_pw = None          # the playwright context manager
_browser = None     # the connected browser


def connect_browser(cdp_url="http://localhost:9222"):
    """Attach to the running debug Chrome. Call once at start of a browser run."""
    global _pw, _browser
    if _browser is not None:
        return True
    try:
        _pw = sync_playwright().start()
        _browser = _pw.chromium.connect_over_cdp(cdp_url)
        print("   [browser] connected to Chrome")
        return True
    except Exception as e:
        print(f"   [browser] could NOT connect ({e}) - is debug Chrome running?")
        return False


def disconnect_browser():
    global _pw, _browser
    try:
        if _browser:
            _browser.close()
        if _pw:
            _pw.stop()
    except Exception:
        pass
    _browser = None
    _pw = None


def _active_page():
    """Return the frontmost / best-guess page across all tabs."""
    if not _browser or not _browser.contexts:
        return None
    ctx = _browser.contexts[0]
    if not ctx.pages:
        return None
    # prefer a page that is not a blank/new tab
    for pg in ctx.pages:
        try:
            if pg.url and not pg.url.startswith("chrome://"):
                return pg
        except Exception:
            continue
    return ctx.pages[0]


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