"""
Browser DOM perception via CDP (Playwright).

Connection method (reuses the project's existing browser tier — do NOT duplicate):
  browser_locator.connect_browser() attaches with
  sync_playwright().chromium.connect_over_cdp("http://localhost:9222")
  and _active_page() picks the frontmost/non-chrome:// tab.
  Same debug Chrome as prereq_reasoner browser_debug (--remote-debugging-port=9222).

Returns element dicts shaped like the accessibility-tree set-of-mark list so the
agent loop can treat them as a drop-in: id, name, control_type, rect, cx, cy.

Screenshot is best-effort only: DOM element extraction is the source of truth.
A slow/failed screenshot must NOT abort browser perception.
"""

import os
from PIL import Image

try:
    from browser_locator import connect_browser, disconnect_browser, _active_page
except Exception:
    connect_browser = None
    disconnect_browser = None
    _active_page = None

try:
    from set_of_mark import draw_marks
except Exception:
    draw_marks = None

# CSS selector for interactable page elements
_INTERACTABLE = (
    "a, button, input, select, textarea, "
    "[role='button'], [role='link'], [role='textbox'], [role='combobox'], "
    "[role='checkbox'], [role='menuitem'], [role='tab'], "
    "[onclick], [contenteditable='true']"
)

_TAG_TO_CONTROL = {
    "a": "Hyperlink",
    "button": "Button",
    "input": "Edit",
    "select": "ComboBox",
    "textarea": "Edit",
}

# Tiny blank PNG reused when screenshot is skipped (downstream needs a path)
_BLANK_PNG_PATH = "browser_view_blank.png"
_SCREENSHOT_TIMEOUT_MS = 5000


def _control_type(tag, role):
    if role:
        role_map = {
            "button": "Button", "link": "Hyperlink", "textbox": "Edit",
            "combobox": "ComboBox", "checkbox": "CheckBox",
            "menuitem": "MenuItem", "tab": "TabItem",
        }
        if role.lower() in role_map:
            return role_map[role.lower()]
    return _TAG_TO_CONTROL.get((tag or "").lower(), (tag or "Element").capitalize())


def _blank_fallback_png(path=None):
    """Ensure a tiny blank PNG exists; return its path for image-required callers."""
    path = path or _BLANK_PNG_PATH
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 8:
            Image.new("RGB", (4, 4), (255, 255, 255)).save(path, format="PNG")
    except Exception:
        try:
            Image.new("RGB", (4, 4), (255, 255, 255)).save(path, format="PNG")
        except Exception:
            pass
    return path


def _viewport_screen_offset(page):
    """Map viewport (0,0) to screen pixels using the browser window chrome size.

    Playwright bounding_box() is viewport-relative; pyautogui needs screen coords.
    Approximate content origin as:
      ox = screenX + (outerWidth - innerWidth) // 2
      oy = screenY + (outerHeight - innerHeight)
    TODO: calibrate this offset in testing if clicks land consistently off
    (DPI, multi-monitor, or Chrome UI chrome can skew it).
    """
    try:
        m = page.evaluate(
            """() => ({
                screenX: window.screenX,
                screenY: window.screenY,
                outerW: window.outerWidth,
                outerH: window.outerHeight,
                innerW: window.innerWidth,
                innerH: window.innerHeight
            })"""
        )
        border_x = max(0, int(m["outerW"] - m["innerW"]) // 2)
        chrome_y = max(0, int(m["outerH"] - m["innerH"]))
        return int(m["screenX"]) + border_x, int(m["screenY"]) + chrome_y, True
    except Exception:
        # TODO: calibrate browser content-area screen offset in testing
        return 0, 0, False


def _collect_dom_elements(page, max_elems=80):
    """Extract visible interactable DOM nodes; tag each with data-mimic-id.

    Numbering matches the element id shown to the model (1..N). Each raw item
    includes mimic_id so we can build selector '[data-mimic-id=\"N\"]'.
    """
    raw = page.evaluate(
        """([sel, maxElems]) => {
            // Clear prior tags from a previous perceive
            for (const old of document.querySelectorAll('[data-mimic-id]')) {
                old.removeAttribute('data-mimic-id');
            }
            const out = [];
            const seen = new Set();
            for (const el of document.querySelectorAll(sel)) {
                const r = el.getBoundingClientRect();
                if (r.width < 2 || r.height < 2) continue;
                if (r.bottom < 0 || r.right < 0) continue;
                if (r.top > window.innerHeight || r.left > window.innerWidth) continue;
                const style = window.getComputedStyle(el);
                if (style.visibility === 'hidden' || style.display === 'none') continue;
                if (parseFloat(style.opacity || '1') === 0) continue;
                const label = (
                    el.getAttribute('aria-label')
                    || el.getAttribute('placeholder')
                    || el.getAttribute('name')
                    || el.getAttribute('title')
                    || (el.tagName === 'INPUT' ? (el.getAttribute('value') || '') : '')
                    || (el.innerText || el.textContent || '')
                ).replace(/\\s+/g, ' ').trim().slice(0, 60);
                const key = [el.tagName, Math.round(r.x), Math.round(r.y),
                             Math.round(r.width), Math.round(r.height), label].join('|');
                if (seen.has(key)) continue;
                seen.add(key);
                const mimicId = out.length + 1;
                el.setAttribute('data-mimic-id', String(mimicId));
                out.push({
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    name: label,
                    x: r.x, y: r.y, w: r.width, h: r.height,
                    mimic_id: mimicId
                });
                if (out.length >= maxElems) break;
            }
            return out;
        }""",
        [_INTERACTABLE, max_elems],
    )
    return raw[:max_elems]


def get_active_page():
    """Return the current Playwright page (CDP), or None if unavailable."""
    try:
        if connect_browser is None or _active_page is None:
            return None
        if not connect_browser():
            return None
        return _active_page()
    except Exception:
        return None



def _fast_screenshot(page, save_path):
    """Best-effort viewport screenshot: short timeout, no font/animation waits.

    Returns True if save_path was written successfully.
    """
    # Bypass Playwright's internal "wait for fonts to load" (common 30s hang)
    os.environ.setdefault("PW_TEST_SCREENSHOT_NO_FONTS_READY", "1")
    try:
        page.screenshot(
            path=save_path,
            type="png",
            full_page=False,
            timeout=_SCREENSHOT_TIMEOUT_MS,
            animations="disabled",
            caret="initial",
        )
        return os.path.isfile(save_path) and os.path.getsize(save_path) > 0
    except TypeError:
        # Older Playwright may not accept animations/caret — retry minimal args
        try:
            page.screenshot(
                path=save_path,
                type="png",
                full_page=False,
                timeout=_SCREENSHOT_TIMEOUT_MS,
            )
            return os.path.isfile(save_path) and os.path.getsize(save_path) > 0
        except Exception as e:
            print(f"   [browser] screenshot skipped ({e})")
            return False
    except Exception as e:
        print(f"   [browser] screenshot skipped ({e})")
        return False


def _page_url_title(page):
    """Best-effort url/title from a Playwright page."""
    url, title = "", ""
    try:
        url = page.url or ""
    except Exception:
        pass
    try:
        title = page.title() or ""
    except Exception:
        pass
    return url, title


def _is_blank_tab_url(url):
    u = (url or "").lower().strip()
    return (
        not u
        or u == "about:blank"
        or u.startswith("chrome://")
        or u.startswith("edge://")
    )


def perceive_browser(save_path="browser_view.png"):
    """Perceive the active Chrome tab via CDP/DOM.

    DOM interactable elements are extracted FIRST (required). Screenshot is
    best-effort: if it times out or fails, we still return the element list
    with a blank placeholder image so callers do not fall back to the native
    accessibility tree.

    Blank / New Tab / chrome:// pages may yield zero interactables — that is OK.
    Returns (elements, image_path, page_info). page_info always includes
    mode/url/title/blank_tab so the loop can keep browser mode and still run
    navigate (page.goto) with an empty element list.

    Each element: {id, name, control_type, rect, cx, cy, vx, vy, browser=True,
    selector='[data-mimic-id=\"N\"]', ...}.
    """
    if connect_browser is None or _active_page is None:
        raise RuntimeError("browser_locator / Playwright not available")

    if not connect_browser():
        raise RuntimeError("could not connect to Chrome on CDP port 9222")

    page = _active_page()
    if page is None:
        raise RuntimeError("no active Chrome page")

    url, title = _page_url_title(page)
    blank_tab = _is_blank_tab_url(url)

    # ---- 1. DOM elements first (this is what drives perception) ----
    ox, oy, offset_ok = _viewport_screen_offset(page)
    try:
        raw = _collect_dom_elements(page)
    except Exception as e:
        print(f"[browser] DOM query failed ({e}) — continuing with empty list")
        raw = []

    elements = []
    for i, item in enumerate(raw, start=1):
        x, y, w, h = item["x"], item["y"], item["w"], item["h"]
        L, T, R, B = int(x), int(y), int(x + w), int(y + h)
        vx = (L + R) // 2
        vy = (T + B) // 2
        mimic_id = int(item.get("mimic_id") or i)
        elements.append({
            "id": mimic_id,
            "name": item.get("name") or "",
            "control_type": _control_type(item.get("tag"), item.get("role")),
            # viewport rect for draw_marks on the page screenshot (offset 0)
            "rect": (L, T, R, B),
            "sx": vx,
            "sy": vy,
            # screen center for pyautogui (offset applied; see TODO in _viewport_screen_offset)
            "cx": vx + ox,
            "cy": vy + oy,
            "vx": vx,
            "vy": vy,
            "browser": True,
            "offset_ok": offset_ok,
            # Stable Playwright selector (data-mimic-id tagged during extraction)
            "selector": f'[data-mimic-id="{mimic_id}"]',
        })

    # Zero interactables on a blank/New Tab is expected — keep browser mode
    if not elements and blank_tab:
        print(f"[browser] blank/New Tab (url={url!r}) — DOM empty; "
              f"navigate (page.goto) still works")
    elif not elements:
        # Treat empty DOM like a soft blank so the loop does not drop to native
        blank_tab = True
        print(f"[browser] empty DOM (url={url!r}) — keeping browser mode; "
              f"prefer navigate if you need a URL")

    page_info = {
        "mode": "browser",
        "url": url,
        "title": title,
        "blank_tab": blank_tab or _is_blank_tab_url(url),
        "address_bar_focused": False,
    }

    # ---- 2. Screenshot is optional / best-effort ----
    def _finish(img_path):
        return elements, img_path, page_info

    got_shot = _fast_screenshot(page, save_path)
    if got_shot:
        if draw_marks and elements:
            try:
                img = Image.open(save_path).convert("RGB")
                annotated = draw_marks(img, elements, offset_x=0, offset_y=0, scale=1.0)
                annotated.save(save_path)
            except Exception as e:
                print(f"   [browser] mark overlay skipped ({e})")
        return _finish(save_path)

    # Screenshot failed/timed out — still return DOM elements (do NOT raise)
    print("[browser] screenshot skipped (slow), using DOM elements only")
    blank = _blank_fallback_png()
    try:
        Image.new("RGB", (4, 4), (255, 255, 255)).save(save_path, format="PNG")
        return _finish(save_path)
    except Exception:
        return _finish(blank)


if __name__ == "__main__":
    print("=== browser_perceive: DOM perception via CDP (port 9222) ===")
    print("Reuse: browser_locator.connect_browser -> connect_over_cdp(localhost:9222)")
    try:
        elements, path, page_info = perceive_browser()
        print(f"found {len(elements)} page elements -> {path}")
        print(f"page_info: {page_info}\n")
        for el in elements[:20]:
            print(f"  {el['id']}: {el['control_type']} '{el['name']}' "
                  f"rect={el['rect']} screen=({el['cx']},{el['cy']})")
        if len(elements) > 20:
            print(f"  ... and {len(elements) - 20} more")
    except Exception as e:
        print(f"FAILED: {e}")
        print("Is debug Chrome running? (browser_debug / --remote-debugging-port=9222)")
    finally:
        # Always tear down Playwright cleanly so exit does not throw EPIPE
        try:
            if disconnect_browser:
                disconnect_browser()
        except Exception:
            print("   [browser] closed")
