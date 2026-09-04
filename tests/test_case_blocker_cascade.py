"""When extract finds no email, switch into the taught case then continue."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def test_live_description_matches_captured_halt_case():
    from case_match import decide_step_cases
    from teaching import CASE_ORIGIN_USER_CAPTURED, StepCase, TaughtStep

    case = StepCase(
        id="c1",
        created_from=CASE_ORIGIN_USER_CAPTURED,
        trigger={
            "description": "Blocked extract — no email visible yet",
            "halt_signature": True,
        },
        evidence={"frame": "cases/c1.png"},
        resolution={"action": "click", "elem_name": "Access email"},
        success_check={"text": "stale title change — should not be used for match"},
        sub_step={"status": "approved", "continue_with_parent": True},
    )
    step = TaughtStep(id="s2", order=1, user_description="extract email", cases=[case])
    vision = MagicMock(return_value={"matches": True, "confidence": "high"})
    decision = decide_step_cases(
        step,
        {"foreground_title": "Joel Jang | LinkedIn - Google Chrome", "a11y_elements": []},
        "_wf_block",
        vision_fn=vision,
        live_screen=b"live-png",
    )
    _pass("switches to case", decision["action"] == "case" and decision["case_id"] == "c1")
    _pass("used live screen", vision.call_count == 1)
    _pass("asked with when-applies", "no email" in (vision.call_args[0][1] or "").lower())
    _pass("did not use success_check text", "stale title" not in (vision.call_args[0][1] or "").lower())


def test_single_case_picked_after_no_email_failure():
    from case_match import looks_like_blocker_extract_failure, pick_case_after_blocker_failure
    from teaching import CASE_ORIGIN_USER_CAPTURED, StepCase, TaughtStep

    case = StepCase(
        id="c1",
        created_from=CASE_ORIGIN_USER_CAPTURED,
        trigger={"description": "Access email is showing", "halt_signature": True},
        resolution={"action": "click", "elem_name": "Access email"},
        success_check={"text": "email visible"},
        sub_step={"status": "approved", "continue_with_parent": True},
    )
    step = TaughtStep(id="s2", order=1, user_description="extract", cases=[case])
    _pass(
        "detects blocker fail",
        looks_like_blocker_extract_failure(
            "vision did not find a visible email on screen",
            "Access email button but no actual email",
        ),
    )
    pick = pick_case_after_blocker_failure(
        step,
        {"foreground_title": "x"},
        "_wf_block",
        reason="vision did not find a visible email on screen",
        observed="Access email visible",
        vision_fn=MagicMock(return_value={"matches": False, "confidence": "low"}),
        live_screen=b"x",
    )
    _pass("picks only case", pick and pick["case"].id == "c1")
    _pass("single_case how", pick.get("how") == "single_case")


def test_draft_case_not_runnable():
    from case_match import case_is_runnable, decide_step_cases
    from teaching import StepCase, TaughtStep

    case = StepCase(
        id="c1",
        created_from="user_captured",
        trigger={"description": "no email", "halt_signature": True},
        resolution={"action": "click", "elem_name": "Access email"},
        success_check={"text": "ok"},
        sub_step={"status": "draft"},
    )
    _pass("draft not runnable", case_is_runnable(case) is False)
    step = TaughtStep(id="s1", order=0, user_description="x", cases=[case])
    vision = MagicMock(return_value={"matches": True, "confidence": "high"})
    decision = decide_step_cases(
        step, {"foreground_title": "x"}, "_wf", vision_fn=vision, live_screen=b"x",
    )
    _pass("skips draft", decision["action"] == "normal")
    _pass("no vision on draft", vision.call_count == 0)


def test_demo_cascades_after_extract_fail():
    from teach_compile import demo_taught_step
    from teaching import CASE_ORIGIN_USER_CAPTURED, StepCase, TaughtStep, TaughtWorkflow, save_taught
    from workflow_folder import workflow_dir
    import shutil

    name = "_case_blocker_demo"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    case = StepCase(
        id="c1",
        created_from=CASE_ORIGIN_USER_CAPTURED,
        trigger={"description": "Blocked extract — no email visible yet", "halt_signature": True},
        resolution={"action": "click", "elem_name": "Access email", "point": [10, 20]},
        success_check={"text": "email visible"},
        sub_step={
            "status": "approved",
            "method": "anchor",
            "continue_with_parent": True,
            "action": {"action": "click", "elem_name": "Access email", "point": [10, 20]},
            "anchors": [],
        },
    )
    step = TaughtStep(
        id="s1",
        order=0,
        user_description="extract email",
        method="prompt",
        prompt_instruction="Read the visible email",
        produces=["{recipient_email}"],
        action={"action": "prompt", "value": "Read the visible email"},
        cases=[case],
        status="understood",
    )
    wf = TaughtWorkflow(name=name, steps=[step])
    save_taught(wf)

    class _R:
        def __init__(self, ok, reason, value_after=None):
            self.ok = ok
            self.reason = reason
            self.value_after = value_after

    calls = {"n": 0}

    def fake_run(runners, halt_on_fail=True):
        calls["n"] += 1
        n = calls["n"]
        # 1) normal extract fail  2) case click ok  3) parent extract ok
        if n == 1:
            return {
                "ok": False,
                "reason": "vision did not find a visible email on screen",
                "results": [_R(False, "vision did not find a visible email on screen", "Access email only")],
            }
        if n == 2:
            return {"ok": True, "reason": "clicked", "results": [_R(True, "clicked", "Access email")]}
        return {
            "ok": True,
            "reason": "vision extracted {recipient_email}=a@b.com",
            "results": [_R(True, "vision extracted {recipient_email}=a@b.com", "a@b.com")],
        }

    with patch("teach_compile.focus_step_target", return_value=None), \
         patch("success_signals.snapshot_structural_state", return_value={"foreground_title": "x"}), \
         patch("ui_runner.run_verified_plan", side_effect=fake_run), \
         patch("case_match.default_vision_match", return_value={"matches": False, "confidence": "low"}), \
         patch("case_match._capture_live_screen", return_value=b"live"):
        out = demo_taught_step(wf, "s1", mode="manual")
    _pass("demo ok after cascade", out.get("ok") is True, out)
    _pass("used case", out.get("case_id") == "c1")
    _pass("cascaded", out.get("cascaded") is True)
    _pass("three runner waves", calls["n"] == 3, calls["n"])


def main():
    print("=" * 70)
    print("case blocker cascade self-test")
    print("=" * 70)
    test_live_description_matches_captured_halt_case()
    test_single_case_picked_after_no_email_failure()
    test_draft_case_not_runnable()
    test_demo_cascades_after_extract_fail()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
