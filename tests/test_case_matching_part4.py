"""PART 4 — origin-aware case matching."""

from __future__ import annotations

import os
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


def _struct(title: str) -> dict:
    return {
        "foreground_title": title,
        "window_titles": [title],
        "a11y_elements": [],
        "at": "2026-01-01T00:00:00Z",
    }


def test_described_medium_confidence_halts():
    from case_match import decide_step_cases
    from teaching import CASE_ORIGIN_USER_DESCRIBED, StepCase, TaughtStep

    case = StepCase(
        id="c1",
        created_from=CASE_ORIGIN_USER_DESCRIBED,
        trigger={"description": "A sign-in panel blocks the email list"},
        evidence={},
        resolution={"action": "click", "elem_name": "Sign in"},
        success_check={"text": "inbox visible"},
    )
    step = TaughtStep(id="s1", order=0, user_description="open inbox", cases=[case])
    vision = MagicMock(return_value={"matches": True, "confidence": "medium"})
    decision = decide_step_cases(
        step,
        _struct("Unknown"),
        "_wf_p4",
        vision_fn=vision,
        live_screen=b"live-screen",
    )
    _pass("medium described halts", decision["action"] == "halt_ambiguous")
    _pass("uncertain reason", decision.get("reason") == "uncertain_vision")
    _pass("vision consulted", vision.call_count == 1)


def test_prefer_captured_over_described():
    from case_match import decide_step_cases
    from teaching import (
        CASE_ORIGIN_USER_CAPTURED,
        CASE_ORIGIN_USER_DESCRIBED,
        StepCase,
        TaughtStep,
    )

    captured = StepCase(
        id="c_cap",
        created_from=CASE_ORIGIN_USER_CAPTURED,
        trigger={"foreground_title": "Sign in — Apollo"},
        evidence={"frame": "cases/c1.png"},
        resolution={"action": "click", "elem_name": "Sign in"},
        success_check={"text": "Apollo home"},
    )
    described = StepCase(
        id="c_desc",
        created_from=CASE_ORIGIN_USER_DESCRIBED,
        trigger={"description": "sign-in panel visible"},
        evidence={},
        resolution={"action": "click", "elem_name": "Sign in"},
        success_check={"text": "Apollo home"},
    )
    step = TaughtStep(
        id="s1",
        order=0,
        user_description="reveal email",
        cases=[captured, described],
    )
    vision = MagicMock(return_value={"matches": True, "confidence": "high"})
    decision = decide_step_cases(
        step,
        _struct("Sign in — Apollo"),
        "_wf_p4",
        vision_fn=vision,
        live_screen=b"live-screen",
    )
    _pass("chooses captured", decision["action"] == "case" and decision["case_id"] == "c_cap")
    _pass("log mentions origin", "user_captured" in decision["log"])
    _pass("log mentions tier", "Tier 1" in decision["log"])


def test_described_high_confidence_fires():
    from case_match import decide_step_cases
    from teaching import CASE_ORIGIN_USER_DESCRIBED, StepCase, TaughtStep

    case = StepCase(
        id="c1",
        created_from=CASE_ORIGIN_USER_DESCRIBED,
        trigger={"description": "paywall dialog"},
        evidence={},
        resolution={"action": "click", "elem_name": "Close"},
        success_check={"text": "content visible"},
    )
    step = TaughtStep(id="s1", order=0, user_description="read article", cases=[case])
    vision = MagicMock(return_value={"matches": True, "confidence": "high"})
    decision = decide_step_cases(
        step,
        _struct("Article — Browser"),
        "_wf_p4",
        vision_fn=vision,
        live_screen=b"live-screen",
    )
    _pass("high described fires", decision["action"] == "case" and decision["case_id"] == "c1")
    _pass("described in log", "user_described" in decision["log"])


def main():
    print("=" * 70)
    print("PART 4 origin-aware matching self-test")
    print("=" * 70)
    test_described_medium_confidence_halts()
    test_prefer_captured_over_described()
    test_described_high_confidence_fires()
    print("PART 4 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
