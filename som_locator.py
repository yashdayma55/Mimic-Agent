"""
Stage A: Set-of-Mark as the Tier-5 locator.

When tiers 1-4 (accessibility tree) fail to find an element, this runs:
  1. mark the active window's clickable elements with numbers
  2. ask the strong model which number matches the step's intent
  3. return that element's EXACT center for the engine to click

This replaces the old "guess if something is clickable here" vision fallback with
"pick the right numbered element", which is far more accurate (no coordinate drift).

Returns a dict compatible with the engine's vision path:
  {found: bool, x, y, what_you_see, confidence, som_id}
"""

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # process DPI-aware up front
except Exception:
    pass

from set_of_mark import build_marked_screenshot
from som_pick import pick_element_by_intent


def locate_with_som(step, verbose=True):
    """Tier-5 Set-of-Mark locate. Uses the step's intent to pick the right
    on-screen element by number, and returns its exact center."""
    intent = (step.get("elem_name") or step.get("instruction")
              or "the target element")
    if verbose:
        print(f"      Tier 5 (set-of-mark): marking screen, intent = '{intent}'")

    try:
        elements, path = build_marked_screenshot(save_path="marked.png")
    except Exception as e:
        if verbose:
            print(f"      Tier 5 SoM: could not mark screen ({e})")
        return {"found": False}

    if not elements:
        if verbose:
            print("      Tier 5 SoM: no clickable elements found")
        return {"found": False}

    chosen_id, reason = pick_element_by_intent(path, elements, intent)
    if not chosen_id:
        if verbose:
            print(f"      Tier 5 SoM: model picked nothing ({reason})")
        return {"found": False}

    match = next((e for e in elements if e["id"] == chosen_id), None)
    if not match:
        return {"found": False}

    if verbose:
        print(f"      Tier 5 SoM: chose box {chosen_id} = "
              f"{match['control_type']} '{match['name']}' -> ({match['cx']},{match['cy']})")

    return {
        "found": True,
        "x": match["cx"],
        "y": match["cy"],
        "what_you_see": f"{match['control_type']} '{match['name']}'",
        "confidence": "high",
        "som_id": chosen_id,
        "reason": reason,
    }


if __name__ == "__main__":
    # standalone test: pretend a step needs an element tiers 1-4 couldn't find
    test_step = {"elem_name": "View", "instruction": "open the View menu"}
    print("Testing Set-of-Mark Tier-5 locator...")
    print(locate_with_som(test_step))