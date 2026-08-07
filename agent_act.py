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

import pyautogui
from pywinauto.keyboard import send_keys


def _coerce_id(val):
    """The model sometimes returns the id as a string; make it an int."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def do_action(action, elements):
    """Perform one action dict. Returns (ok, message)."""
    kind = action.get("action")

    if kind == "click":
        eid = _coerce_id(action.get("id"))
        match = next((e for e in elements if e["id"] == eid), None)
        if not match:
            return False, f"no element with id {action.get('id')}"
        pyautogui.click(match["cx"], match["cy"])
        return True, f"clicked box {eid}: {match['control_type']} '{match['name']}'"

    if kind == "type":
        text = action.get("text", "")
        send_keys(text, with_spaces=True)
        return True, f'typed "{text}"'

    if kind == "press":
        key = action.get("key", "").lower()
        keymap = {"enter": "{ENTER}", "tab": "{TAB}", "esc": "{ESC}",
                  "escape": "{ESC}", "space": "{SPACE}", "backspace": "{BACKSPACE}"}
        send_keys(keymap.get(key, key))
        return True, f"pressed {key}"

    if kind == "scroll":
        amount = -400 if action.get("direction") == "down" else 400
        pyautogui.scroll(amount)
        return True, f"scrolled {action.get('direction')}"

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