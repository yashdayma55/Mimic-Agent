"""PART 2 — halt cases: confirm which step they belong to."""

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


def _struct(title: str) -> dict:
    return {
        "foreground_title": title,
        "window_titles": [title],
        "a11y_elements": [{"name": "Sign in to continue", "control_type": "Button"}],
        "at": "2026-01-01T00:00:00Z",
    }


def _resolution():
    return {"action": "click", "elem_name": "Sign in", "elem_type": "Button"}


def _workflow_with_steps():
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow

    name = "_cases_attach_p2"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "email workflow")
    s1 = add_step(wf, "open linkedin profile")
    s2 = add_step(wf, "copy the revealed email")
    s3 = add_step(wf, "open mailbox")
    s4 = add_step(wf, "paste into compose")
    return wf, name, s1, s2, s3, s4


def test_default_attach_is_halting_step():
    from case_halt_loop import (
        attach_step_question,
        complete_halt_resolution,
        record_step_halt,
        workflow_steps_for_attach,
    )
    from workflow_folder import workflow_dir

    wf, name, s1, s2, s3, s4 = _workflow_with_steps()
    frame_path = os.path.join(workflow_dir(name), "cases", "halt_s4.png")
    os.makedirs(os.path.dirname(frame_path), exist_ok=True)
    with open(frame_path, "wb") as f:
        f.write(b"png")

    record_step_halt(
        wf,
        s4.id,
        reason="unexpected screen",
        structural=_struct("Sign in — Apollo"),
        synthetic_frame=b"png",
    )
    complete_halt_resolution(wf, s4.id, _resolution(), after_structural=_struct("Apollo — Home"))
    q = attach_step_question(wf, s4.id)
    _pass("default question mentions step 4", "step 4" in q.lower() and "paste" in q.lower(), q)
    rows = workflow_steps_for_attach(wf)
    _pass("four steps listed", len(rows) == 4)
    default_row = next(r for r in rows if r["id"] == s4.id)
    _pass("default is step 4", default_row["number"] == 4)


def test_attach_to_different_step_keeps_halt_frame():
    from case_halt_loop import (
        answer_attach_case_step,
        answer_remember_case,
        complete_halt_resolution,
        record_step_halt,
    )
    from teaching import TeachingError, get_step, load_taught, save_taught
    from workflow_folder import workflow_dir

    wf, name, s1, s2, s3, s4 = _workflow_with_steps()
    os.makedirs(os.path.join(workflow_dir(name), "cases"), exist_ok=True)

    record_step_halt(
        wf,
        s4.id,
        reason="blocked",
        structural=_struct("Sign in — Apollo"),
        synthetic_frame=b"halt-frame",
    )
    complete_halt_resolution(wf, s4.id, _resolution(), after_structural=_struct("Apollo — Home"))
    remember = answer_remember_case(wf, s4.id, "yes, remember it")
    _pass("pending attach", remember.get("pending_attach") is True)
    _pass("default step s4", remember.get("default_step_id") == s4.id)

    out = answer_attach_case_step(wf, s4.id, "attach", target_step_id=s2.id)
    loaded_s2 = get_step(load_taught(name), s2.id)
    loaded_s4 = get_step(load_taught(name), s4.id)
    _pass("case on step 2", len(loaded_s2.cases) == 1)
    _pass("not on step 4", not loaded_s4.cases)
    case = loaded_s2.cases[0]
    _pass("halted_at_step s4", case.halted_at_step == s4.id)
    _pass("halt frame copied", case.evidence.get("frame") == "cases/c1.png")
    _pass("frame file exists", os.path.isfile(os.path.join(workflow_dir(name), "cases", "c1.png")))
    _pass("halt cleared", loaded_s4.case_halt is None)
    _pass("response frame", out.get("halt_frame") == f"cases/halt_{s4.id}.png")


def test_refuse_attach_when_step_full():
    from case_halt_loop import (
        answer_attach_case_step,
        answer_remember_case,
        complete_halt_resolution,
        record_step_halt,
    )
    from step_cases import add_step_case
    from teaching import StepCase, TeachingError, get_step

    wf, name, s1, s2, s3, s4 = _workflow_with_steps()
    for i in range(1, 4):
        add_step_case(
            s2,
            StepCase(
                id=f"c{i}",
                created_from="halt",
                trigger={"foreground_title": f"T{i}"},
                evidence={"frame": f"cases/x{i}.png"},
                resolution=_resolution(),
                success_check={"text": "ok", "check": {"type": "user_text", "text": "ok"}},
            ),
        )
    record_step_halt(wf, s4.id, reason="x", structural=_struct("Sign in"), synthetic_frame=b"x")
    complete_halt_resolution(wf, s4.id, _resolution(), after_structural=_struct("Apollo"))
    answer_remember_case(wf, s4.id, "yes")
    try:
        answer_attach_case_step(wf, s4.id, "attach", target_step_id=s2.id)
        _pass("refuse full step", False)
    except TeachingError as e:
        _pass("refuse full step", "3 cases" in str(e).lower(), str(e))
    _pass("halt still pending", get_step(wf, s4.id).case_halt is not None)


def main():
    print("=" * 70)
    print("PART 2 halt case attach-step self-test")
    print("=" * 70)
    test_default_attach_is_halting_step()
    test_attach_to_different_step_keeps_halt_frame()
    test_refuse_attach_when_step_full()
    print("PART 2 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
