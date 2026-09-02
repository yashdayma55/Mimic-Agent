"""PART 2 — halt → resolve → remember loop (no OS input)."""

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


def _struct(title: str, *, names: list[str] | None = None) -> dict:
    elems = [{"name": n, "control_type": "Button"} for n in (names or [])]
    return {
        "foreground_title": title,
        "window_titles": [title],
        "a11y_elements": elems,
        "browser_url": None,
        "at": "2026-01-01T00:00:00Z",
    }


def _resolution() -> dict:
    return {
        "action": "click",
        "elem_name": "Sign in",
        "elem_type": "Button",
        "window_title": "Sign in — Apollo",
    }


def test_halt_remember_loop():
    from case_halt_loop import (
        REMEMBER_KIND,
        REMEMBER_QUESTION,
        answer_attach_case_step,
        answer_remember_case,
        complete_halt_resolution,
        pending_remember_question,
        record_step_halt,
    )
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught

    name = "_cases_p2_loop"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "reveal email on linkedin")
    step = add_step(wf, "click access email")
    step.understanding = {
        "target": "an email address under Emails",
        "success_check": "email address is visible",
    }
    save_taught(wf)

    halt = record_step_halt(
        wf,
        step.id,
        reason="success check not met",
        expected="an email address under Emails",
        observed="a 'Sign in to continue' panel",
        structural=_struct("Sign in — Apollo", names=["Sign in to continue"]),
        synthetic_frame=b"png",
    )
    _pass("halt message", "expected" in halt["message"].lower() and "sign in" in halt["message"].lower())
    _pass("needs resolution", halt.get("needs_resolution") is True)

    complete_halt_resolution(
        wf,
        step.id,
        _resolution(),
        after_structural=_struct("Apollo — Home", names=["Emails"]),
    )
    loaded = get_step(load_taught(name), step.id)
    remember_qs = [
        q for q in (loaded.qa_history or [])
        if q.get("kind") == REMEMBER_KIND
    ]
    _pass("remember asked once", len(remember_qs) == 1, len(remember_qs))
    _pass("remember question text", remember_qs[0].get("q") == REMEMBER_QUESTION)
    _pass("resolution stored", bool(loaded.case_halt and loaded.case_halt.get("resolution")))
    _pass("pending remember", pending_remember_question(loaded) is not None)

    out_no = answer_remember_case(wf, step.id, "no, one-off")
    loaded = get_step(load_taught(name), step.id)
    _pass("no answer not remembered", out_no.get("remembered") is False)
    _pass("no stores nothing", not loaded.cases)
    _pass("halt cleared on no", loaded.case_halt is None)

    record_step_halt(
        wf,
        step.id,
        reason="unexpected screen",
        structural=_struct("Sign in — Apollo", names=["Sign in to continue"]),
        synthetic_frame=b"png2",
    )
    complete_halt_resolution(
        wf,
        step.id,
        _resolution(),
        after_structural=_struct("Apollo — Home", names=["Emails"]),
    )
    answer_remember_case(wf, step.id, "yes, remember it")
    out_yes = answer_attach_case_step(wf, step.id, "yes")
    loaded = get_step(load_taught(name), step.id)
    _pass("yes remembered", out_yes.get("remembered") is True)
    _pass("one case stored", len(loaded.cases) == 1)
    case = loaded.cases[0]
    _pass("case evidence frame", bool(case.evidence.get("frame")))
    _pass("case resolution", case.resolution.get("elem_name") == "Sign in")
    _pass("case success_check", bool(case.success_check.get("check") or case.success_check.get("text")))
    _pass("case from halt", case.created_from == "halt")
    _pass("halt cleared on yes", loaded.case_halt is None)


def main():
    print("=" * 70)
    print("PART 2 case halt loop self-test")
    print("=" * 70)
    test_halt_remember_loop()
    print("PART 2 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
