"""
MimicAgent Stage B - the goal-driven ReAct loop.

Give a plain-language goal, and the agent loops:
  PERCEIVE -> REASON -> (approve) -> ACT -> OBSERVE -> check goal -> repeat

We build this beat by beat. Step 1 = PERCEIVE: turn the current screen into a
numbered list of elements + a marked screenshot the model can reason over.
Reuses the Stage A Set-of-Mark machinery.
"""

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from set_of_mark import collect_clickable_elements, grab_full_screen, draw_marks

try:
    from som_redact import redact_image
except Exception:
    redact_image = None

# Optional browser (DOM/CDP) perception — missing Playwright must not break native path
try:
    from browser_detect import is_browser_frontmost
    from browser_perceive import perceive_browser
    _browser_perceive_available = True
except Exception:
    is_browser_frontmost = None
    perceive_browser = None
    _browser_perceive_available = False


def _is_address_bar_focused():
    """True if Chrome's omnibox / Address and search bar currently has focus."""
    try:
        from pywinauto.uia_defines import IUIA
        el = IUIA().iuia.GetFocusedElement()
        name = (el.CurrentName or "").lower()
        aid = (el.CurrentAutomationId or "").lower()
        cls = (el.CurrentClassName or "").lower()
        if any(k in name for k in ("address and search", "address bar", "omnibox")):
            return True
        if "omnibox" in aid or "omnibox" in cls:
            return True
        # Walk a few parents — focus sometimes sits on an inner edit
        walker = IUIA().iuia.ControlViewWalker
        p = el
        for _ in range(4):
            try:
                p = walker.GetParentElement(p)
                pname = (p.CurrentName or "").lower()
                paid = (p.CurrentAutomationId or "").lower()
                if any(k in pname for k in ("address and search", "address bar", "omnibox")):
                    return True
                if "omnibox" in paid:
                    return True
            except Exception:
                break
    except Exception:
        pass
    return False


def _browser_page_info():
    """URL/title/blank-tab/omnibox notes for the active CDP page."""
    info = {
        "mode": "browser",
        "url": "",
        "title": "",
        "blank_tab": False,
        "address_bar_focused": False,
    }
    try:
        from browser_locator import connect_browser, _active_page
        if connect_browser():
            page = _active_page()
            if page is not None:
                try:
                    info["url"] = page.url or ""
                except Exception:
                    pass
                try:
                    info["title"] = page.title() or ""
                except Exception:
                    pass
    except Exception:
        pass
    u = (info["url"] or "").lower().strip()
    info["blank_tab"] = (
        u in ("", "about:blank")
        or u.startswith("chrome://newtab")
        or u.startswith("chrome://new-tab-page")
    )
    info["address_bar_focused"] = _is_address_bar_focused()
    return info


def _perceive_native(save_path):
    """Accessibility-tree perception with redaction (unchanged native path)."""
    elements = collect_clickable_elements()
    img, ox, oy, scale = grab_full_screen()
    for el in elements:
        el["sx"] = int((el["cx"] - ox) * scale)
        el["sy"] = int((el["cy"] - oy) * scale)
    # 1. black out sensitive regions BEFORE marking/sending
    if redact_image:
        img = redact_image(img, elements, ox, oy)
    # 2. then draw the numbered marks
    annotated = draw_marks(img, elements, ox, oy, scale)
    annotated.save(save_path)
    return elements, save_path


def perceive(save_path="agent_view.png", prefer_browser=False):
    """PERCEIVE: capture the screen as a numbered element list + marked image,
    REDACTING sensitive fields (passwords, cards, etc.) before the image is saved
    or sent to the model - same safety as Stage A.

    Returns (elements, image_path, page_info).
    page_info is a dict with mode ('browser'|'native') and, for browser:
    url, title, blank_tab, address_bar_focused.

    prefer_browser=True (browser goals): always use CDP/DOM perception. Never
    silently fall back to the native accessibility tree (which would be whatever
    app is frontmost, e.g. VS Code). Blank/New Tab may return zero elements with
    blank_tab=True — navigate still works.

    prefer_browser=False (native app goals): use browser DOM only when Chrome is
    already frontmost; otherwise use the accessibility tree.
    """
    use_browser = prefer_browser or (
        _browser_perceive_available and is_browser_frontmost and is_browser_frontmost()
    )

    if use_browser and _browser_perceive_available:
        try:
            result = perceive_browser(save_path=save_path)
            if len(result) == 3:
                elements, path, page_info = result
            else:
                elements, path = result
                page_info = _browser_page_info()
            try:
                page_info["address_bar_focused"] = _is_address_bar_focused()
            except Exception:
                pass
            print(f"[perceive] browser DOM | title={page_info.get('title', '')!r} "
                  f"| url={page_info.get('url', '')!r} "
                  f"| elements={len(elements)}")
            if page_info.get("blank_tab") or not elements:
                print("[perceive] NOTE: blank/New Tab or empty DOM — "
                      "navigate action still available (do not use address bar)")
            if page_info.get("address_bar_focused"):
                print("[perceive] NOTE: address bar / omnibox appears focused")
            return elements, path, page_info
        except Exception as e:
            if prefer_browser:
                # Browser task: do NOT drop into VS Code / native tree
                print(f"[perceive] browser DOM failed ({e}); "
                      f"NOT falling back to native (prefer_browser)")
                blank_path = save_path
                try:
                    from browser_perceive import _blank_fallback_png
                    blank_path = _blank_fallback_png(save_path)
                except Exception:
                    pass
                return [], blank_path, {
                    "mode": "browser",
                    "url": "",
                    "title": "",
                    "blank_tab": True,
                    "address_bar_focused": False,
                }
            print(f"   browser perceive failed ({e}); falling back to accessibility tree")

    if prefer_browser:
        # Browser perceive unavailable entirely — still refuse native fallback
        print("[perceive] prefer_browser set but browser perception unavailable; "
              "returning empty browser view (will not use native tree)")
        blank_path = save_path
        try:
            from browser_perceive import _blank_fallback_png
            blank_path = _blank_fallback_png(save_path)
        except Exception:
            pass
        return [], blank_path, {
            "mode": "browser",
            "url": "",
            "title": "",
            "blank_tab": True,
            "address_bar_focused": False,
        }

    print("[perceive] native tree")
    elements, path = _perceive_native(save_path)
    return elements, path, {"mode": "native", "url": "", "title": "",
                            "blank_tab": False, "address_bar_focused": False}


def describe_perception(elements):
    """Build the compact text menu of numbered elements for the model prompt."""
    return "\n".join(
        f"{el['id']}: {el['control_type']} '{el['name']}'" for el in elements
    )


if __name__ == "__main__":
    print("Stage B step 1: PERCEIVE the screen")
    elements, path, page_info = perceive()
    print(f"\nperceived {len(elements)} elements -> {path}")
    print(f"page_info: {page_info}\n")
    print(describe_perception(elements)[:1200])
    print("\n(this numbered menu + the screenshot is what the model reasons over)")
