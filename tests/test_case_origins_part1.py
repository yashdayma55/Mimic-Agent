"""PART 1 — case origins: halt, user_captured, user_described."""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def _resolution():
    return {"action": "click", "elem_name": "Sign in", "elem_type": "Button"}


def _success_check():
    return {"check": {"type": "foreground_title", "expected": "Apollo — Home"}, "text": "Apollo home"}


def test_reject_user_captured_without_frame():
    from step_cases import validate_step_case
    from teaching import StepCase, TeachingError

    case = StepCase(
        id="c1",
        created_from="user_captured",
        trigger={"foreground_title": "Sign in"},
        evidence={},
        resolution=_resolution(),
        success_check=_success_check(),
    )
    try:
        validate_step_case(case)
        _pass("reject captured without frame", False)
    except TeachingError as e:
        _pass("reject captured without frame", "frame" in str(e).lower(), str(e))


def test_reject_user_described_without_description():
    from step_cases import validate_step_case
    from teaching import StepCase, TeachingError

    case = StepCase(
        id="c1",
        created_from="user_described",
        trigger={"foreground_title": "should not be enough alone"},
        evidence={},
        resolution=_resolution(),
        success_check=_success_check(),
    )
    try:
        validate_step_case(case)
        _pass("reject described without description", False)
    except TeachingError as e:
        _pass("reject described without description", "description" in str(e).lower(), str(e))


def test_three_origins_roundtrip():
    from step_cases import add_step_case
    from teach_loop import add_step, set_context
    from teaching import (
        CASE_ORIGIN_HALT,
        CASE_ORIGIN_USER_CAPTURED,
        CASE_ORIGIN_USER_DESCRIBED,
        StepCase,
        TaughtWorkflow,
        get_step,
        load_taught,
        save_taught,
    )

    name = "_cases_origins_p1"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "test")
    step = add_step(wf, "reveal email")

    add_step_case(
        step,
        StepCase(
            id="c1",
            created_from=CASE_ORIGIN_HALT,
            trigger={"foreground_title": "Sign in — Apollo"},
            evidence={"frame": "cases/c1.png"},
            resolution=_resolution(),
            success_check=_success_check(),
        ),
    )
    add_step_case(
        step,
        StepCase(
            id="c2",
            created_from=CASE_ORIGIN_USER_CAPTURED,
            trigger={"foreground_title": "Paywall"},
            evidence={"frame": "cases/c2.png"},
            resolution=_resolution(),
            success_check=_success_check(),
            origin_note="you added this",
        ),
    )
    add_step_case(
        step,
        StepCase(
            id="c3",
            created_from=CASE_ORIGIN_USER_DESCRIBED,
            trigger={"description": "A sign-in panel blocks the email list"},
            evidence={},
            resolution=_resolution(),
            success_check=_success_check(),
        ),
    )
    save_taught(wf)
    loaded = get_step(load_taught(name), step.id)
    _pass("three cases saved", len(loaded.cases) == 3)
    by_id = {c.id: c for c in loaded.cases}
    _pass("halt origin", by_id["c1"].created_from == CASE_ORIGIN_HALT)
    _pass("halt frame", by_id["c1"].evidence.get("frame") == "cases/c1.png")
    _pass("halt origin note", "halt" in (by_id["c1"].origin_note or "").lower())
    _pass("captured origin", by_id["c2"].created_from == CASE_ORIGIN_USER_CAPTURED)
    _pass("captured frame", by_id["c2"].evidence.get("frame") == "cases/c2.png")
    _pass("captured origin note", by_id["c2"].origin_note == "you added this")
    _pass("described origin", by_id["c3"].created_from == CASE_ORIGIN_USER_DESCRIBED)
    _pass("described no frame", not by_id["c3"].evidence.get("frame"))
    _pass("described trigger", "sign-in" in by_id["c3"].trigger.get("description", "").lower())
    _pass("described origin note", "described" in (by_id["c3"].origin_note or "").lower())


def test_reject_fourth_case_mixed_origins():
    from step_cases import add_step_case
    from teach_loop import add_step, set_context
    from teaching import StepCase, TeachingError, TaughtWorkflow

    wf = TaughtWorkflow(name="_cases_origins_max")
    set_context(wf, "test")
    step = add_step(wf, "step")
    for i, origin in enumerate(("halt", "user_captured", "user_described"), start=1):
        trigger = (
            {"description": f"situation {i}"}
            if origin == "user_described"
            else {"foreground_title": f"Screen {i}"}
        )
        evidence = {} if origin == "user_described" else {"frame": f"cases/c{i}.png"}
        add_step_case(
            step,
            StepCase(
                id=f"c{i}",
                created_from=origin,
                trigger=trigger,
                evidence=evidence,
                resolution=_resolution(),
                success_check=_success_check(),
            ),
        )
    try:
        add_step_case(
            step,
            StepCase(
                id="c4",
                created_from="halt",
                trigger={"foreground_title": "x"},
                evidence={"frame": "cases/c4.png"},
                resolution=_resolution(),
                success_check=_success_check(),
            ),
        )
        _pass("reject fourth", False)
    except TeachingError as e:
        _pass("reject fourth", "3" in str(e), str(e))


def main():
    print("=" * 70)
    print("PART 1 case origins self-test")
    print("=" * 70)
    test_reject_user_captured_without_frame()
    test_reject_user_described_without_description()
    test_three_origins_roundtrip()
    test_reject_fourth_case_mixed_origins()
    print("PART 1 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
