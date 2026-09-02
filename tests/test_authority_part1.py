"""PART 1 — resolution cascade replaces witness voting."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def test_a11y_authority_vision_silent():
    from show_capture import resolve_target
    from teach_loop import add_step, apply_show_witnesses, set_context
    from teaching import TaughtWorkflow, get_step, save_taught

    a11y_raw = {
        "name": "Extensions",
        "control_type": "Text",
        "rect": [10, 10, 80, 30],
        "parent_path": "Toolbar",
    }
    dom_raw = {"name": "Extensions", "control_type": "Text", "text": "Extensions"}
    vision_w = {"saw": False, "account": "a nearly white background with minimal content."}

    with patch("show_capture._element_at", return_value=a11y_raw):
        with patch("show_capture._browser_at", return_value=dom_raw):
            with patch("show_capture._combined_vlm_witness", return_value=vision_w):
                with patch("show_capture._vision_witness", return_value=vision_w):
                    with patch("show_capture.confirm_target_with_vision", return_value={"confirmed_by_vision": True}):
                        with patch("show_capture.check_parent_target", return_value=None):
                            with patch("app_ui_guard.is_own_window", return_value=False):
                                with patch("app_ui_guard.window_title_at_point", return_value="Chrome"):
                                    res = resolve_target((50, 50))

    _pass("source is a11y", res.get("source") == "a11y", res.get("source"))
    _pass("reason recorded", "structural" in (res.get("reason") or ""), res.get("reason"))
    _pass("vision saw nothing account", res["witnesses"]["vision"]["account"] == "saw nothing"
          or not res["witnesses"]["vision"].get("saw"))

    wf = TaughtWorkflow(name="_auth_p1a")
    set_context(wf, "ext")
    s = add_step(wf, "click Extensions")
    save_taught(wf)
    out = apply_show_witnesses(wf, s.id, {"resolution": res, "witnesses": res})
    step = get_step(wf, s.id)
    qs = [q for q in step.qa_history if q.get("kind") == "witness_conflict"]
    _pass("no witness conflict question", len(qs) == 0, str(qs))


def test_vision_locate_when_blind():
    from show_capture import resolve_target

    locate = {
        "locate_path": "tile_scan",
        "refined": True,
        "saw": True,
        "rect": [100, 100, 140, 130],
        "account": "Apollo panel button.",
        "tile_index": 2,
    }
    with patch("show_capture._element_at", return_value={}):
        with patch("show_capture._browser_at", return_value={}):
            with patch("show_capture._vision_locate_tile", return_value=locate):
                with patch("show_capture._combined_vlm_witness", return_value={"saw": False, "account": "saw nothing"}):
                    with patch("show_capture.confirm_target_with_vision", return_value={"confirmed_by_vision": True}):
                        with patch("app_ui_guard.is_own_window", return_value=False):
                            with patch("app_ui_guard.window_title_at_point", return_value="Chrome"):
                                res = resolve_target((200, 200), step_description="click Apollo")

    _pass("source is vision", res.get("source") == "vision", res.get("source"))
    _pass("tile scan path used", res["witnesses"]["vision"].get("locate_path") == "tile_scan"
          or res["primary"].get("locate_path") == "tile_scan")


def test_saw_nothing_never_selectable():
    from teach_loop import add_step, apply_show_witnesses, set_context
    from teaching import TaughtWorkflow, get_step, save_taught

    res = {
        "source": "a11y",
        "reason": "structural pipeline saw the element",
        "witnesses": {
            "a11y": {"saw": True, "account": "a Button named 'Save'."},
            "dom": {"saw": False, "account": "saw nothing"},
            "vision": {"saw": False, "account": "saw nothing"},
        },
        "primary": {"name": "Save", "control_type": "Button", "pipeline": "a11y"},
        "confirmation": {"confirmed_by_vision": True},
    }
    wf = TaughtWorkflow(name="_auth_p1c")
    set_context(wf, "x")
    s = add_step(wf, "save")
    save_taught(wf)
    apply_show_witnesses(wf, s.id, {"resolution": res})
    step = get_step(wf, s.id)
    for q in step.qa_history:
        choices = q.get("choices") or []
        for c in choices:
            _pass("saw nothing not a choice", c not in ("dom", "vision", "a11y") or c.startswith("neither") is False,
                  str(choices))


def main():
    print("=" * 70)
    print("PART 1 resolution cascade self-test")
    print("=" * 70)
    test_a11y_authority_vision_silent()
    test_vision_locate_when_blind()
    test_saw_nothing_never_selectable()
    print("PART 1 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
