"""
Stage B step 3: ACT - route the model's chosen action to real tools.

Maps the closed-vocabulary action to code that already exists:
  click      -> pyautogui / playwright click the chosen numbered element
  type       -> keyboard send the text
  press      -> keyboard send a key
  scroll     -> pyautogui scroll
  navigate   -> Playwright page.goto(url) via CDP
  switch_tab -> bring matching Chrome tab to front
  copy/paste -> Ctrl+C / Ctrl+V
  wait       -> short sleep (capped)
  hotkey     -> send_keys chord
The reasoning already chose; acting just routes it to the crew.
"""

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import time
import pyautogui
from pywinauto.keyboard import send_keys


def _coerce_id(val):
    """The model sometimes returns the id as a string; make it an int."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _escape_for_send_keys(text):
    """Wrap pywinauto special chars in {} so they type literally."""
    special = set("^+%~(){}[]")
    out = []
    for ch in text:
        if ch in special:
            out.append("{" + ch + "}")
        else:
            out.append(ch)
    return "".join(out)


def _refocus_target(target_procs, title_hint=None):
    """Re-focus the target app window right before acting, because the terminal
    approval prompt steals focus back to PowerShell. Beats that focus-steal.
    Prefer title_hint when multiple windows match (e.g. several Chrome windows)."""
    if not target_procs:
        return
    try:
        from prereq_reasoner import focus_app
        focus_app(target_procs, title_hint=title_hint)
    except Exception:
        pass


def _looks_like_omnibox_element(el):
    """True if an accessibility/DOM element looks like Chrome's address bar."""
    name = (el.get("name") or "").lower()
    return (
        "omnibox" in name
        or "address and search" in name
        or (name.startswith("address") and "bar" in name)
    )


def _is_address_bar_focused():
    """Detect Chrome omnibox focus via UI Automation."""
    try:
        from agent_loop import _is_address_bar_focused as _check
        return _check()
    except Exception:
        pass
    try:
        from pywinauto.uia_defines import IUIA
        el = IUIA().iuia.GetFocusedElement()
        name = (el.CurrentName or "").lower()
        aid = (el.CurrentAutomationId or "").lower()
        if any(k in name for k in ("address and search", "address bar", "omnibox")):
            return True
        if "omnibox" in aid:
            return True
    except Exception:
        pass
    return False


def _refocus_page_body(elements):
    """Move focus out of the omnibox into the page body / main editable."""
    # Prefer CDP: blur + click viewport center (never opens a new Chrome)
    browser_els = [e for e in elements if e.get("browser")]
    if browser_els:
        try:
            page = _get_browser_page()
            if page is not None:
                try:
                    page.evaluate(
                        "() => { try { document.activeElement && document.activeElement.blur(); } catch (e) {} }"
                    )
                except Exception:
                    pass
                try:
                    vp = page.viewport_size or {"width": 800, "height": 600}
                    page.mouse.click(max(10, vp["width"] // 2), max(10, vp["height"] // 2))
                except Exception:
                    page.mouse.click(400, 300)
                print("  [type] re-focused page body via CDP (left address bar)")
                time.sleep(0.25)
                return True
        except Exception as e:
            print(f"  [type] CDP body re-focus failed: {e}")

    # Native fallback: click largest Document/Edit that is not the omnibox
    editables = [
        e for e in elements
        if e.get("control_type") in ("Document", "Edit")
        and not _looks_like_omnibox_element(e)
    ]
    if editables:
        def _area(el):
            L, T, R, B = el["rect"]
            return max(0, R - L) * max(0, B - T)
        target = max(editables, key=_area)
        try:
            pyautogui.click(target["cx"], target["cy"])
            print(f"  [type] re-focused page body via click on "
                  f"{target.get('control_type')} '{target.get('name', '')[:40]}'")
            time.sleep(0.25)
            return True
        except Exception as e:
            print(f"  [type] body re-focus click failed: {e}")
    return False


def _get_browser_page():
    """Return the active CDP page, or None if Playwright/Chrome unavailable."""
    try:
        from browser_locator import connect_browser, _active_page
        if connect_browser():
            return _active_page()
    except Exception:
        pass
    return None


def do_action(action, elements, target_procs=None, title_hint=None):
    """Perform one action dict. Returns (ok, message).
    title_hint helps focus the correct window when several match."""
    kind = action.get("action")

    _refocus_target(target_procs, title_hint=title_hint)

    if kind == "click":
        eid = _coerce_id(action.get("id"))
        match = next((e for e in elements if e["id"] == eid), None)
        if not match:
            return False, f"no element with id {action.get('id')}"
        # Browser DOM elements: prefer Playwright viewport click (more reliable
        # than screen-coord pyautogui). No live element handle is stored on the
        # dict — page.mouse.click(vx, vy) reuses the existing CDP connection.
        # TODO: if needed, store a CSS/XPath handle for element.click() instead.
        if match.get("browser"):
            try:
                from browser_locator import connect_browser, _active_page
                if not connect_browser():
                    return False, "browser click failed: could not connect"
                page = _active_page()
                if page is None:
                    return False, "browser click failed: no active page"
                vx = match.get("vx", match["cx"])
                vy = match.get("vy", match["cy"])
                page.mouse.click(vx, vy)
                return True, (
                    f"clicked box {eid} (browser): "
                    f"{match['control_type']} '{match['name']}'"
                )
            except Exception as e:
                return False, f"browser click failed: {e}"
        try:
            pyautogui.click(match["cx"], match["cy"])
            return True, f"clicked box {eid}: {match['control_type']} '{match['name']}'"
        except Exception as e:
            return False, f"click failed: {e}"

    if kind == "type":
        text_missing = "text" not in action
        text = "" if text_missing else (action.get("text") or "")
        # Never let free-text type land in the address bar / omnibox.
        # navigate (page.goto) is the only path allowed to use the address bar.
        if _is_address_bar_focused():
            print("  [type] address bar focused — moving focus to page body first")
            _refocus_page_body(elements)
        # Click into the main editable text area first (Document/Edit), so keys
        # land in the body rather than after a tab/title click left focus elsewhere.
        # Skip omnibox-named elements.
        editables = [
            e for e in elements
            if e.get("control_type") in ("Document", "Edit")
            and not _looks_like_omnibox_element(e)
        ]
        if editables:
            def _area(el):
                L, T, R, B = el["rect"]
                return max(0, R - L) * max(0, B - T)
            target = max(editables, key=_area)
            if target.get("browser"):
                try:
                    page = _get_browser_page()
                    if page is not None:
                        vx = target.get("vx", target["cx"])
                        vy = target.get("vy", target["cy"])
                        page.mouse.click(vx, vy)
                        time.sleep(0.2)
                    else:
                        pyautogui.click(target["cx"], target["cy"])
                        time.sleep(0.2)
                except Exception:
                    pyautogui.click(target["cx"], target["cy"])
                    time.sleep(0.2)
            else:
                pyautogui.click(target["cx"], target["cy"])
                time.sleep(0.2)
        elif any(e.get("browser") for e in elements):
            # Browser page with no obvious Edit — still leave omnibox if needed
            if _is_address_bar_focused():
                _refocus_page_body(elements)
        # type_mode controls where the text lands (fixes typing-mid-text bug):
        #   'replace' (default) -> Ctrl+A then type, so the field's contents are replaced
        #   'append'            -> Ctrl+End then type, so text is added at the end
        #   'as-is'             -> type wherever the cursor is
        mode = action.get("type_mode", "replace")
        if mode == "replace":
            send_keys("^a")
        elif mode == "append":
            send_keys("^{END}")
        send_keys(_escape_for_send_keys(text), with_spaces=True)
        if text_missing:
            return True, f'typed "" (mode={mode}; text missing)'
        return True, f'typed "{text}" (mode={mode})'

    if kind == "press":
        if not action.get("key"):
            return False, "press with no key"
        key = str(action.get("key")).lower()
        keymap = {"enter": "{ENTER}", "tab": "{TAB}", "esc": "{ESC}",
                  "escape": "{ESC}", "space": "{SPACE}", "backspace": "{BACKSPACE}"}
        send_keys(keymap.get(key, key))
        return True, f"pressed {key}"

    if kind == "scroll":
        direction = action.get("direction") or "down"
        amount = -400 if direction == "down" else 400
        pyautogui.scroll(amount)
        return True, f"scrolled {direction}"

    if kind == "navigate":
        url = (action.get("url") or "").strip()
        if not url:
            return False, "navigate with no url"
        page = _get_browser_page()
        if page is None:
            return False, "no browser to navigate"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return True, f"navigated to {url}"
        except Exception as e:
            return False, f"navigate failed: {e}"

    if kind == "switch_tab":
        match_text = (action.get("match") or "").strip()
        if not match_text:
            return False, "switch_tab with no match"
        try:
            from browser_locator import connect_browser
            import browser_locator as bl
            if not connect_browser() or bl._browser is None:
                return False, "no browser to switch tabs"
            needle = match_text.lower()
            for ctx in bl._browser.contexts:
                for page in ctx.pages:
                    try:
                        title = (page.title() or "")
                        url = (page.url or "")
                        if needle in title.lower() or needle in url.lower():
                            page.bring_to_front()
                            return True, f"switched to tab '{title or url}'"
                    except Exception:
                        continue
            return False, f"no tab matches '{match_text}'"
        except Exception as e:
            return False, f"switch_tab failed: {e}"

    if kind == "copy":
        send_keys("^c")
        return True, "copied (Ctrl+C)"

    if kind == "paste":
        send_keys("^v")
        return True, "pasted (Ctrl+V)"

    if kind == "wait":
        try:
            seconds = float(action.get("seconds", 1))
        except (TypeError, ValueError):
            return False, "wait with invalid seconds"
        if seconds < 0:
            return False, "wait with negative seconds"
        capped = min(seconds, 10.0)
        time.sleep(capped)
        return True, f"waited {capped}s"

    if kind == "hotkey":
        keys = action.get("keys")
        if not keys:
            return False, "hotkey with no keys"
        try:
            send_keys(str(keys))
            return True, f"hotkey {keys}"
        except Exception as e:
            return False, f"hotkey failed: {e}"

    if kind == "done":
        return True, "done"

    if kind == "stuck":
        return False, f"stuck: {action.get('why')}"

    return False, f"unknown action {kind}"


if __name__ == "__main__":
    import sys
    from agent_loop import perceive
    from agent_reason import reason_next_action

    goal = sys.argv[1] if len(sys.argv) > 1 else "open the View menu"
    print(f"=== Stage B step 3: full PERCEIVE -> REASON -> ACT for '{goal}' ===")
    elements, path, _page_info = perceive()
    print(f"perceived {len(elements)} elements")
    action = reason_next_action(goal, elements, path)
    print(f"proposed: {action}")

    confirm = input("perform this action? (y/n): ").strip().lower()
    if confirm == "y":
        ok, msg = do_action(action, elements)
        print(f"{'OK' if ok else 'FAIL'}: {msg}")
    else:
        print("cancelled.")
