"""
Set-of-Mark step 3: intent -> numbered pick -> EXACT CLICK.
The complete Set-of-Mark grounding loop, as a standalone tool.

Run:  python som_click.py "click the File menu"
It will mark the screen, ask Claude which box, show you the pick, and (after a
safety confirmation) click that box's exact center.
"""

import sys
import ctypes
from set_of_mark import build_marked_screenshot
from som_pick import pick_element_by_intent


def ground_and_click(intent, require_confirm=True):
    """Full loop: mark -> pick -> click the chosen element's exact center.
    Returns the chosen element dict, or None."""
    # make sure clicks use the same physical coordinate space as the tree
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    elements, path = build_marked_screenshot(save_path="marked.png")
    print(f"marked {len(elements)} elements")

    chosen_id, reason = pick_element_by_intent(path, elements, intent)
    if not chosen_id:
        print(f"no element chosen: {reason}")
        return None

    match = next((e for e in elements if e["id"] == chosen_id), None)
    if not match:
        print(f"chosen id {chosen_id} not in element list")
        return None

    print(f"\nchose box {chosen_id}: {match['control_type']} '{match['name']}'")
    print(f"reason: {reason}")
    print(f"will click screen center ({match['cx']},{match['cy']})")

    if require_confirm:
        ans = input("\nclick it? (y/n): ").strip().lower()
        if ans != "y":
            print("cancelled.")
            return match

    import pyautogui
    pyautogui.click(match["cx"], match["cy"])
    print(f">>> clicked ({match['cx']},{match['cy']})")
    return match


if __name__ == "__main__":
    intent = sys.argv[1] if len(sys.argv) > 1 else "click the File menu"
    print(f"=== Set-of-Mark ground + click: '{intent}' ===")
    ground_and_click(intent, require_confirm=True)