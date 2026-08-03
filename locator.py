from pywinauto import Desktop


def locate(step, verbose=True):
    """5-tier self-healing locator + browser tier.

    Returns one of:
      ("BROWSER", 0, {element, page})  browser step  (a Playwright locator)
      (element, tier)                  tiers 1-4      (a pywinauto element)
      ("VISION", 5, result_dict)       tier 5         (vision hit - has x,y coords)
      (None, None)                     nothing found
    """
    name = step.get("elem_name", "")
    etype = step.get("elem_type", "")
    desktop = Desktop(backend="uia")

    def log(msg):
        if verbose:
            print(f"      {msg}")

    # ---- BROWSER TIER: if this is a web step, use Playwright ----
    try:
        from browser_locator import is_browser_step, find_in_browser
        if is_browser_step(step):
            el, page = find_in_browser(step, verbose=verbose)
            if el:
                log("Browser tier hit (playwright)")
                return "BROWSER", 0, {"element": el, "page": page}
    except Exception as e:
        log(f"browser tier skipped: {e}")

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

    # ---- TIER 4: partial name match (short interactive controls only) ----
    if name and len(name) > 3:
        SAFE_TYPES = ("Button", "MenuItem", "ListItem", "Edit", "ComboBox",
                      "CheckBox", "RadioButton", "TabItem", "Hyperlink", "TreeItem")
        for win in desktop.windows():
            try:
                for el in win.descendants():
                    if el.element_info.control_type not in SAFE_TYPES:
                        continue
                    el_name = el.element_info.name or ""
                    # the element name must be SHORT and close to our target,
                    # not a giant paragraph that merely contains the string
                    if not el_name:
                        continue
                    if name.lower() in el_name.lower() and len(el_name) < len(name) + 15:
                        log(f"Tier 4 hit (partial name): '{el_name}' [{el.element_info.control_type}]")
                        return el, 4
            except Exception:
                continue

    # ---- TIER 5: vision fallback (local Ollama or API) ----
    try:
        from vision_locator import locate_with_vision
        result = locate_with_vision(step, verbose=verbose)
        if result.get("found"):
            log(f"Tier 5 hit (vision): {result.get('what_you_see')}")
            return "VISION", 5, result       # coords-based result, engine clicks x,y
        log("Tier 5: vision could not confirm the element")
    except Exception as e:
        log(f"Tier 5 error: {e}")

    return None, None


if __name__ == "__main__":
    test_step = {"elem_name": "zzznonexistent", "elem_type": "Document", "x": 500, "y": 400}
    got = locate(test_step)
    if got[0] == "BROWSER":
        print(f"\nBROWSER-located: {got[2]}")
    elif got[0] == "VISION":
        print(f"\nVISION-located: {got[2]}")
    elif got[0]:
        print(f"\nFOUND at tier {got[1]}: {got[0].rectangle()}")
    else:
        print("\nNOT FOUND by any tier")