"""
Stage A: Set-of-Mark as the Tier-5 locator - FULL integrated version.

When tiers 1-4 (accessibility tree) fail, this does three things in order:
  1. RECALL: check the adaptation cache (sqlite-vec). If we've solved this same
     situation before, reuse the remembered element - free, offline, no API call.
  2. SAFE PICK: otherwise, mark the screen, REDACT sensitive fields, and ask the
     strong model which numbered element matches the intent.
  3. REMEMBER: cache the fresh pick so next time step 1 handles it for free.

Returns an engine-compatible dict: {found, x, y, what_you_see, confidence, som_id}.
"""

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from som_safe_pick import safe_pick_element_by_intent
from som_memory import open_adapt_memory, remember_adaptation, recall_adaptation


# open the adaptation memory once (module-level, reused across calls)
try:
    _adapt_db = open_adapt_memory("adaptations.db")
except Exception as e:
    print(f"      [SoM] could not open adaptation memory: {e}")
    _adapt_db = None


def _find_by_name_in_tree(name):
    """Try to re-find a remembered element by its name via the fast tree tiers.
    Returns (x, y) or None. This is why caching a NAME is powerful: once we know
    the real name, the cheap tiers can find it directly next time."""
    try:
        from set_of_mark import collect_clickable_elements
        for el in collect_clickable_elements():
            label = f"{el['control_type']} '{el['name']}'"
            if label == name or (el['name'] and el['name'] in name):
                return el["cx"], el["cy"]
    except Exception:
        pass
    return None


def locate_with_som(step, verbose=True):
    """Tier-5 Set-of-Mark locate with recall -> safe pick -> remember."""
    intent = (step.get("elem_name") or step.get("instruction")
              or "the target element")

    # ---- 1. RECALL: have we adapted this exact situation before? ----
    if _adapt_db is not None:
        remembered = recall_adaptation(_adapt_db, step)
        if remembered:
            name = remembered.get("name", "")
            if verbose:
                print(f"      Tier 5 (SoM): recalled past adaptation -> {name}")
            coords = _find_by_name_in_tree(name)
            if coords:
                if verbose:
                    print(f"      Tier 5 (SoM): reused remembered element (free/offline)")
                return {"found": True, "x": coords[0], "y": coords[1],
                        "what_you_see": name, "confidence": "high",
                        "som_id": remembered.get("som_id"), "from_cache": True}
            # remembered name no longer on screen -> fall through to a fresh pick

    # ---- 2. SAFE PICK: redact + ask the model which numbered element ----
    if verbose:
        print(f"      Tier 5 (SoM): marking + redacting, intent = '{intent}'")
    match, reason = safe_pick_element_by_intent(intent)
    if not match:
        if verbose:
            print(f"      Tier 5 (SoM): no element chosen ({reason})")
        return {"found": False}

    if verbose:
        print(f"      Tier 5 (SoM): chose {match['control_type']} "
              f"'{match['name']}' -> ({match['cx']},{match['cy']})")

    result = {"found": True, "x": match["cx"], "y": match["cy"],
              "what_you_see": f"{match['control_type']} '{match['name']}'",
              "confidence": "high", "som_id": match["id"], "reason": reason}

    # ---- 3. REMEMBER: cache this fresh adaptation for next time ----
    if _adapt_db is not None:
        try:
            remember_adaptation(_adapt_db, step, result)
        except Exception as e:
            if verbose:
                print(f"      Tier 5 (SoM): could not cache adaptation ({e})")

    return result


if __name__ == "__main__":
    test_step = {"elem_name": "the settings gear icon", "instruction": "open settings"}
    print("Testing integrated Set-of-Mark Tier-5 (recall -> safe pick -> remember)...")
    print("\n--- first call (should use API, then cache) ---")
    print(locate_with_som(test_step))
    print("\n--- second call (should RECALL from cache, no API) ---")
    print(locate_with_som(test_step))