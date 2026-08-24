"""
Clarification prompt: when uncertainty is flagged, ask the user which
Set-of-Mark element id to use. Text-only; no GUI.
"""


def _element_by_id(elements, eid):
    for e in elements or []:
        if e.get("id") == eid:
            return e
    return None


def ask_user_to_disambiguate(goal, elements, image_path, candidate_ids, reason):
    """Show a targeted question referencing candidate SoM numbers; return a choice.

    Reuses the numbered screenshot already produced by perceive (image_path).

    Returns:
      int  — chosen element id
      "skip" — user skipped this step
      "cancel" — user cancelled the run
    """
    print()
    print("=" * 60)
    print("CLARIFICATION NEEDED")
    print("=" * 60)
    if image_path:
        print(f"  Marked screenshot: {image_path}")
    print(f"  I'm unsure which element to use for: {goal}")
    if reason:
        print(f"  Reason: {reason}")
    print("  Candidates:")
    shown = []
    for cid in candidate_ids or []:
        el = _element_by_id(elements, cid)
        if el:
            name = (el.get("name") or "").strip() or "(unnamed)"
            ctype = (el.get("control_type") or "?").strip()
            line = f"    [{cid}] {ctype} '{name}'"
        else:
            line = f"    [{cid}] (not in current element list)"
        print(line)
        shown.append(cid)
    if not shown:
        # Fall back: list a short menu of all elements so the user can still pick
        print("  (no candidate ids — listing perceived elements)")
        for el in (elements or [])[:40]:
            eid = el.get("id")
            name = (el.get("name") or "").strip() or "(unnamed)"
            ctype = (el.get("control_type") or "?").strip()
            print(f"    [{eid}] {ctype} '{name}'")
            shown.append(eid)

    valid_ids = {e.get("id") for e in (elements or []) if e.get("id") is not None}
    prompt = "  Type the number to use (or s to skip / c to cancel): "

    for attempt in range(3):
        try:
            from ui_prompts import ask_human, ui_bridge_active
            if ui_bridge_active():
                raw = ask_human("clarification", prompt).strip()
            else:
                raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "cancel"
        if not raw:
            print("  (empty — enter a number, s, or c)")
            continue
        low = raw.lower()
        if low in ("s", "skip"):
            return "skip"
        if low in ("c", "cancel", "q", "quit"):
            return "cancel"
        try:
            eid = int(raw)
        except ValueError:
            print(f"  not a number: {raw!r} — try again")
            continue
        if eid in valid_ids:
            print(f"  -> using element [{eid}]")
            return eid
        # Allow a candidate id even if somehow missing from elements
        if eid in shown:
            print(f"  -> using element [{eid}] (from candidates)")
            return eid
        print(f"  [{eid}] is not a known element id — try again "
              f"(valid examples: {sorted(valid_ids)[:8]}...)")

    print("  too many bad inputs — treating as skip")
    return "skip"


if __name__ == "__main__":
    fake_elements = [
        {"id": 14, "name": "Apply", "control_type": "Button"},
        {"id": 15, "name": "Apply", "control_type": "Button"},
        {"id": 22, "name": "Apply settings", "control_type": "Button"},
        {"id": 30, "name": "Cancel", "control_type": "Button"},
    ]
    print("=== clarify.py smoke (type 15 then Enter) ===")
    choice = ask_user_to_disambiguate(
        goal="apply the change",
        elements=fake_elements,
        image_path="browser_view.png",
        candidate_ids=[14, 15, 22],
        reason="three similar Apply buttons",
    )
    print(f"returned: {choice!r}")
