"""PART 2 — vision confirms before committing."""

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


def test_vision_mismatch_blocks_approval():
    from teach_loop import add_step, apply_show_witnesses, approve_understanding, set_context
    from teaching import TeachingError, TaughtWorkflow, get_step, save_taught

    wf = TaughtWorkflow(name="_auth_p2a")
    set_context(wf, "ext")
    s = add_step(wf, "click Extensions")
    s.understanding = {
        "target": "Extensions", "action": "click", "varies_each_run": [],
        "constants": [], "uses_from_earlier": [], "success_check": "ok",
        "assumptions": ["none"], "plain_summary": "click Extensions",
    }
    save_taught(wf)
    res = {
        "source": "a11y",
        "reason": "structural pipeline saw the element",
        "witnesses": {"a11y": {"saw": True}, "dom": {"saw": False, "account": "saw nothing"},
                      "vision": {"saw": False, "account": "saw nothing"}},
        "primary": {"name": "Extensions", "control_type": "Text", "pipeline": "a11y"},
        "confirmation": {
            "vision_mismatch": True,
            "question": "The tree pointed at Text 'Extensions', but the picture shows a blank area. Should I use this?",
        },
        "resolution_line": "resolved by a11y · vision disagrees (needs you)",
    }
    apply_show_witnesses(wf, s.id, {"resolution": res})
    step = get_step(wf, s.id)
    _pass("vision mismatch pending", step.anchors[0].get("vision_mismatch_pending") is True)
    qs = [q for q in step.qa_history if q.get("kind") == "vision_mismatch"]
    _pass("confirmation question raised", len(qs) == 1, qs[0]["q"][:80] if qs else "")
    try:
        approve_understanding(wf, s.id)
        _pass("approve blocked", False)
    except TeachingError as e:
        _pass("approve blocked on mismatch", "vision" in str(e).lower(), str(e))


def test_vision_timeout_unconfirmed_not_blocking():
    from teach_loop import add_step, apply_show_witnesses, approve_understanding, set_context
    from teaching import TaughtWorkflow, get_step, save_taught

    wf = TaughtWorkflow(name="_auth_p2b")
    set_context(wf, "x")
    s = add_step(wf, "click save")
    s.understanding = {
        "target": "Save", "action": "click", "varies_each_run": [],
        "constants": [], "uses_from_earlier": [], "success_check": "saved",
        "assumptions": ["none"], "plain_summary": "click save",
    }
    save_taught(wf)
    res = {
        "source": "a11y",
        "reason": "structural pipeline saw the element",
        "witnesses": {"a11y": {"saw": True}, "dom": {"saw": False, "account": "saw nothing"},
                      "vision": {"saw": False, "account": "saw nothing"}},
        "primary": {"name": "Save", "control_type": "Button", "pipeline": "a11y"},
        "confirmation": {"unconfirmed": True, "confirmed_by_vision": False},
        "resolution_line": "resolved by a11y · unconfirmed (vision timed out)",
    }
    apply_show_witnesses(wf, s.id, {"resolution": res})
    step = get_step(wf, s.id)
    _pass("unconfirmed flagged", step.anchors[0].get("vision_unconfirmed") is True)
    _pass("not mismatch pending", not step.anchors[0].get("vision_mismatch_pending"))
    approve_understanding(wf, s.id)
    _pass("approve ok when unconfirmed", get_step(wf, s.id).status == "understood")


def main():
    print("=" * 70)
    print("PART 2 vision confirmation self-test")
    print("=" * 70)
    test_vision_mismatch_blocks_approval()
    test_vision_timeout_unconfirmed_not_blocking()
    print("PART 2 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
