"""PART 3 — conservative runtime case matching."""

from __future__ import annotations

import os
import shutil
import sys
from unittest.mock import MagicMock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def _struct(title: str, *, names: list[str] | None = None, url: str | None = None) -> dict:
    elems = [{"name": n, "control_type": "Button"} for n in (names or [])]
    return {
        "foreground_title": title,
        "window_titles": [title],
        "a11y_elements": elems,
        "browser_url": url,
        "at": "2026-01-01T00:00:00Z",
    }


def _case(
    cid: str,
    trigger: dict,
    *,
    frame: str = "cases/c1.png",
    resolution: dict | None = None,
) -> "StepCase":
    from teaching import StepCase

    return StepCase(
        id=cid,
        created_from="halt",
        trigger=trigger,
        evidence={"frame": frame, "window_title": trigger.get("foreground_title")},
        resolution=resolution or {"action": "click", "elem_name": "Sign in", "elem_type": "Button"},
        success_check={"check": {"type": "foreground_title", "expected": "Apollo — Home"}, "text": "Apollo home"},
    )


def _step_with_cases(cases):
    from teaching import TaughtStep

    return TaughtStep(
        id="s1",
        order=0,
        user_description="reveal email",
        cases=cases,
        action={"action": "click", "elem_name": "Access email"},
    )


def test_tier1_single_match_no_vision():
    from case_match import decide_step_cases

    step = _step_with_cases([
        _case("c1", {"foreground_title": "Sign in — Apollo", "a11y_present": [{"name": "Sign in"}]}),
    ])
    vision = MagicMock(return_value={"matches": True, "confidence": "high"})
    decision = decide_step_cases(
        step,
        _struct("Sign in — Apollo", names=["Sign in"]),
        "_wf_p3",
        vision_fn=vision,
    )
    _pass("chooses case", decision["action"] == "case" and decision["case_id"] == "c1")
    _pass("tier 1 log", "Tier 1" in decision["log"])
    _pass("zero vision calls", vision.call_count == 0)


def test_two_cases_halt_ambiguous():
    from case_match import decide_step_cases

    trigger = {"foreground_title": "Sign in — Apollo"}
    step = _step_with_cases([_case("c1", trigger), _case("c2", trigger)])
    decision = decide_step_cases(step, _struct("Sign in — Apollo"), "_wf_p3")
    _pass("halts on two matches", decision["action"] == "halt_ambiguous")
    _pass("mentions multiple", "multiple" in decision["log"].lower() or "ambiguous" in decision["log"].lower())


def test_low_vision_confidence_halts():
    from case_match import decide_step_cases
    from workflow_folder import workflow_dir

    wf = "_wf_p3_vis"
    wd = workflow_dir(wf)
    os.makedirs(os.path.join(wd, "cases"), exist_ok=True)
    frame = os.path.join(wd, "cases", "c1.png")
    with open(frame, "wb") as f:
        f.write(b"png")
    step = _step_with_cases([
        _case("c1", {"halt_signature": True}, frame="cases/c1.png"),
    ])
    vision = MagicMock(return_value={"matches": True, "confidence": "low"})
    decision = decide_step_cases(step, _struct("Unknown screen"), wf, vision_fn=vision)
    _pass("vision consulted", vision.call_count == 1)
    _pass("low confidence halts", decision["action"] == "halt_ambiguous")
    _pass("uncertain reason", decision.get("reason") == "uncertain_vision")


def test_no_match_normal_path():
    from case_match import decide_step_cases, plan_step_execution
    from teaching import TaughtWorkflow

    step = _step_with_cases([
        _case("c1", {"foreground_title": "Sign in — Apollo"}),
    ])
    decision = decide_step_cases(step, _struct("LinkedIn — Google Chrome"), "_wf_p3")
    _pass("no match", decision["action"] == "normal")
    wf = TaughtWorkflow(name="_wf_p3_plan")
    step.action = {"action": "click", "elem_name": "Access email"}
    plan = plan_step_execution(wf, step, None, before_demo=_struct("LinkedIn — Google Chrome"))
    _pass("normal runner", plan["action"] == "normal")
    _pass("runner step present", bool(plan.get("runner_step")))


def main():
    print("=" * 70)
    print("PART 3 case matching self-test")
    print("=" * 70)
    test_tier1_single_match_no_vision()
    test_two_cases_halt_ambiguous()
    test_low_vision_confidence_halts()
    test_no_match_normal_path()
    print("PART 3 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
