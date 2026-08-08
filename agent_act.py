"""
Stage B step 3: ACT - route the model's chosen action to real tools.

Maps the closed-vocabulary action to code that already exists:
  click  -> pyautogui click the chosen numbered element's exact center
  type   -> keyboard send the text
  press  -> keyboard send a key
  scroll -> pyautogui scroll
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


def _refocus_target(target_procs):
    """Re-focus the target app window right before acting, because the terminal
    approval prompt steals focus back to PowerShell. Beats that focus-steal."""
    if not target_procs:
        return
    try:
        from prereq_reasoner import focus_app
        focus_app(target_procs)
    except Exception:
        pass


def do_action(action, elements, target_procs=None):
    """Perform one action dict. Returns (ok, message)."""
    kind = action.get("action")

    _refocus_target(target_procs)

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
                if connect_browser():
                    page = _active_page()
                    if page is not None:
                        vx = match.get("vx", match["cx"])
                        vy = match.get("vy", match["cy"])
                        page.mouse.click(vx, vy)
                        return True, (
                            f"clicked box {eid} (browser): "
                            f"{match['control_type']} '{match['name']}'"
                        )
            except Exception:
                pass  # fall through to pyautogui at cx, cy
        pyautogui.click(match["cx"], match["cy"])
        return True, f"clicked box {eid}: {match['control_type']} '{match['name']}'"

    if kind == "type":
        text_missing = "text" not in action
        text = "" if text_missing else (action.get("text") or "")
        # Click into the main editable text area first (Document/Edit), so keys
        # land in the body rather than after a tab/title click left focus elsewhere.
        editables = [e for e in elements if e.get("control_type") in ("Document", "Edit")]
        if editables:
            def _area(el):
                L, T, R, B = el["rect"]
                return max(0, R - L) * max(0, B - T)
            target = max(editables, key=_area)
            pyautogui.click(target["cx"], target["cy"])
            time.sleep(0.2)
        # type_mode controls where the text lands (fixes typing-mid-text bug):
        #   'replace' (default) -> Ctrl+A then type, so the field's contents are replaced
        #   'append'            -> Ctrl+End then type, so text is added at the end
        #   'as-is'             -> type wherever the cursor is
        mode = action.get("type_mode", "replace")
        if mode == "replace":
            send_keys("^a")
        elif mode == "append":
            send_keys("^{END}")
        send_keys(text, with_spaces=True)
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
    elements, path = perceive()
    print(f"perceived {len(elements)} elements")
    action = reason_next_action(goal, elements, path)
    print(f"proposed: {action}")

    confirm = input("perform this action? (y/n): ").strip().lower()
    if confirm == "y":
        ok, msg = do_action(action, elements)
        print(f"{'OK' if ok else 'FAIL'}: {msg}")
    else:
        print("cancelled.")