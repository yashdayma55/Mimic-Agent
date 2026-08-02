from pywinauto import Desktop

def locate(step, verbose=True):
    """5-tier self-healing locator. Returns (element, tier) or (None, None)."""
    name = step.get("elem_name", "")
    etype = step.get("elem_type", "")
    desktop = Desktop(backend="uia")

    def log(msg):
        if verbose:
            print(f"      {msg}")

    # ---- TIER 1: exact role + name ----
    if name:
        for win in desktop.windows():
            try:
                m = win.descendants(title=name, control_type=etype)
                if m:
                    log(f"Tier 1 hit (role+name): '{name}' [{etype}]")
                    return m[0], 1
            except Exception:
                continue

    # ---- TIER 2: automation id (stable developer id) ----
    auto_id = step.get("auto_id", "")
    if auto_id:
        for win in desktop.windows():
            try:
                m = win.descendants(auto_id=auto_id)
                if m:
                    log(f"Tier 2 hit (automation id): '{auto_id}'")
                    return m[0], 2
            except Exception:
                continue

    # ---- TIER 3: name only, any type ----
    if name:
        for win in desktop.windows():
            try:
                m = win.descendants(title=name)
                if m:
                    log(f"Tier 3 hit (name only): '{name}'")
                    return m[0], 3
            except Exception:
                continue

    # ---- TIER 4: partial / fuzzy name match ----
    if name:
        for win in desktop.windows():
            try:
                for el in win.descendants():
                    el_name = el.element_info.name or ""
                    if name.lower() in el_name.lower() and len(name) > 3:
                        log(f"Tier 4 hit (partial name): '{el_name}' contains '{name}'")
                        return el, 4
            except Exception:
                continue

    # ---- TIER 5: vision fallback (Phase 2) ----
    log("Tier 5 (vision) would run here - not wired yet")
    # TODO: crop screenshot at step['x'],step['y'], ask the model to confirm
    return None, None


if __name__ == "__main__":
    test_step = {"elem_name": "Text edito", "elem_type": "Document", "x": 500, "y": 400}
    el, tier = locate(test_step)
    if el:
        print(f"\nFOUND at tier {tier}: {el.rectangle()}")
    else:
        print("\nNOT FOUND by any tier")