"""PART 3 — parent target question for Text inside Button."""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def test_text_inside_button_asks_parent():
    from teach_loop import add_step, apply_show_witnesses, handle_parent_target, set_context
    from teaching import TaughtWorkflow, get_step, save_taught

    wf = TaughtWorkflow(name="_auth_p3a")
    set_context(wf, "ext")
    s = add_step(wf, "click Extensions label")
    save_taught(wf)
    parent = {
        "clicked_name": "Extensions",
        "clicked_type": "Text",
        "ancestor_type": "Button",
        "ancestor_name": "Extensions",
        "question": "You clicked the text 'Extensions', which sits inside a Button. Should I click the Button?",
    }
    res = {
        "source": "a11y",
        "reason": "structural pipeline saw the element",
        "witnesses": {"a11y": {"saw": True}, "dom": {"saw": False, "account": "saw nothing"},
                      "vision": {"saw": False, "account": "saw nothing"}},
        "primary": {"name": "Extensions", "control_type": "Text", "pipeline": "a11y"},
        "confirmation": {"confirmed_by_vision": True},
        "parent_target": parent,
    }
    apply_show_witnesses(wf, s.id, {"resolution": res})
    step = get_step(wf, s.id)
    qs = [q for q in step.qa_history if q.get("kind") == "parent_target"]
    _pass("parent question asked", len(qs) == 1, qs[0]["q"][:60] if qs else "")
    handle_parent_target(wf, s.id, "yes, click the parent")
    step = get_step(wf, s.id)
    anc = step.anchors[0]
    _pass("primary is Button", (anc.get("primary") or {}).get("control_type") == "Button")
    _pass("reason stored", "parent" in (anc.get("primary_reason") or "").lower() or "Button" in (anc.get("primary_reason") or ""))


def test_button_direct_no_parent_question():
    from show_capture import check_parent_target

    primary = {"name": "Save", "control_type": "Button"}
    pt = check_parent_target(50, 50, primary)
    _pass("Button direct no question", pt is None, str(pt))


def main():
    print("=" * 70)
    print("PART 3 parent target self-test")
    print("=" * 70)
    test_text_inside_button_asks_parent()
    test_button_direct_no_parent_question()
    print("PART 3 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
