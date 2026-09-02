"""PART 1 — StepCase data model and validation rules."""

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


def _sample_case(case_id: str = "c1", *, created_from: str = "halt", success_check=None):
    from teaching import StepCase

    return StepCase(
        id=case_id,
        created_from=created_from,
        trigger={"foreground_title": "Sign in — Apollo"},
        evidence={"frame": f"cases/{case_id}.png", "window_title": "Sign in — Apollo", "at": "2026-01-01T00:00:00Z"},
        resolution={"action": "click", "elem_name": "Sign in", "elem_type": "Button"},
        success_check=success_check
        if success_check is not None
        else {"check": {"type": "foreground_title", "expected": "Apollo — Home"}},
    )


def test_reject_user_origin():
    from step_cases import add_step_case, validate_step_case
    from teach_loop import add_step, set_context
    from teaching import TeachingError, TaughtWorkflow

    wf = TaughtWorkflow(name="_cases_p1_user")
    set_context(wf, "test")
    step = add_step(wf, "open email panel")
    bad = _sample_case(created_from="user")
    try:
        validate_step_case(bad)
        _pass("reject user origin", False, "no error raised")
    except TeachingError as e:
        _pass("reject user origin", "halt" in str(e).lower() or "created_from" in str(e).lower(), str(e))
    try:
        add_step_case(step, bad)
        _pass("add user origin case", False, "no error raised")
    except TeachingError:
        _pass("add user origin case rejected", True)


def test_reject_missing_success_check():
    from step_cases import validate_step_case
    from teaching import TeachingError

    for bad_sc in ({}, {"note": "no check"}):
        case = _sample_case(success_check=bad_sc)
        try:
            validate_step_case(case)
            _pass("reject missing success_check", False, repr(bad_sc))
        except TeachingError as e:
            _pass("reject missing success_check", "success_check" in str(e).lower(), str(e))
    try:
        validate_step_case(
            {
                "id": "c9",
                "created_from": "halt",
                "trigger": {"foreground_title": "x"},
                "evidence": {"frame": "cases/c9.png"},
                "resolution": {"action": "click"},
            }
        )
        _pass("reject dict without success_check", False)
    except TeachingError as e:
        _pass("reject dict without success_check", "success_check" in str(e).lower(), str(e))


def test_reject_fourth_case():
    from step_cases import add_step_case
    from teach_loop import add_step, set_context
    from teaching import TeachingError, TaughtWorkflow

    name = "_cases_p1_max"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "test")
    step = add_step(wf, "reveal email")
    for i in range(1, 4):
        add_step_case(step, _sample_case(f"c{i}"))
    _pass("three cases stored", len(step.cases) == 3)
    try:
        add_step_case(step, _sample_case("c4"))
        _pass("reject fourth case", False, "no error raised")
    except TeachingError as e:
        _pass("reject fourth case", "3" in str(e), str(e))


def test_valid_case_roundtrip():
    from step_cases import add_step_case
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught

    name = "_cases_p1_round"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "test")
    step = add_step(wf, "click sign in")
    add_step_case(step, _sample_case("c1"))
    save_taught(wf)
    loaded = get_step(load_taught(name), step.id)
    _pass("one case saved", len(loaded.cases) == 1)
    c = loaded.cases[0]
    _pass("origin halt", c.created_from == "halt")
    _pass("evidence frame", c.evidence.get("frame") == "cases/c1.png")
    _pass("success_check present", bool(c.success_check.get("check")))


def main():
    print("=" * 70)
    print("PART 1 step cases data model self-test")
    print("=" * 70)
    test_reject_user_origin()
    test_reject_missing_success_check()
    test_reject_fourth_case()
    test_valid_case_roundtrip()
    print("PART 1 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
