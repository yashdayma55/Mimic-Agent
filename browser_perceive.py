"""
Browser DOM perception via CDP (Playwright).

Connection method (reuses the project's existing browser tier — do NOT duplicate):
  browser_locator.connect_browser() attaches with
  sync_playwright().chromium.connect_over_cdp("http://localhost:9222")
  and _active_page() picks the frontmost/non-chrome:// tab.
  Same debug Chrome as prereq_reasoner browser_debug (--remote-debugging-port=9222).

Returns element dicts shaped like the accessibility-tree set-of-mark list so the
agent loop can treat them as a drop-in: id, name, control_type, rect, cx, cy.
"""

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
    """Extract visible interactable DOM nodes; coords are viewport-relative."""
    raw = page.evaluate(
        """(sel) => {
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
                out.push({
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    name: label,
                    x: r.x, y: r.y, w: r.width, h: r.height
                });
            }
            return out;
        }""",
        _INTERACTABLE,
    )
    return raw[:max_elems]


def perceive_browser(save_path="browser_view.png"):
    """Perceive the active Chrome tab via CDP/DOM.

    Returns (elements, image_path) drop-in compatible with agent_loop.perceive().
    Each element: {id, name, control_type, rect, cx, cy, browser=True, ...}.
    rect/sx/sy are viewport pixels (for marks on the page screenshot);
    cx/cy are screen pixels (for pyautogui), using the estimated window offset.
    """
    if connect_browser is None or _active_page is None:
        raise RuntimeError("browser_locator / Playwright not available")

    if not connect_browser():
        raise RuntimeError("could not connect to Chrome on CDP port 9222")

    page = _active_page()
    if page is None:
        raise RuntimeError("no active Chrome page")

    ox, oy, offset_ok = _viewport_screen_offset(page)
    raw = _collect_dom_elements(page)

    elements = []
    for i, item in enumerate(raw, start=1):
        x, y, w, h = item["x"], item["y"], item["w"], item["h"]
        L, T, R, B = int(x), int(y), int(x + w), int(y + h)
        vx = (L + R) // 2
        vy = (T + B) // 2
        elements.append({
            "id": i,
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
        })

    # Playwright page screenshot = accurate page pixels; mark with viewport rects
    page.screenshot(path=save_path, type="png")
    if draw_marks and elements:
        img = Image.open(save_path).convert("RGB")
        annotated = draw_marks(img, elements, offset_x=0, offset_y=0, scale=1.0)
        annotated.save(save_path)

    return elements, save_path


if __name__ == "__main__":
    print("=== browser_perceive: DOM perception via CDP (port 9222) ===")
    print("Reuse: browser_locator.connect_browser -> connect_over_cdp(localhost:9222)")
    try:
        elements, path = perceive_browser()
        print(f"found {len(elements)} page elements -> {path}\n")
        for el in elements[:20]:
            print(f"  {el['id']}: {el['control_type']} '{el['name']}' "
                  f"rect={el['rect']} screen=({el['cx']},{el['cy']})")
        if len(elements) > 20:
            print(f"  ... and {len(elements) - 20} more")
    except Exception as e:
        print(f"FAILED: {e}")
        print("Is debug Chrome running? (browser_debug / --remote-debugging-port=9222)")
    finally:
        if disconnect_browser:
            disconnect_browser()
