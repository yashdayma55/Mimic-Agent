"""Case authoring via float bar — click count, grab, finish, Case N labels."""

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
        "a11y_elements": [{"name": "Sign in", "control_type": "Button"}],
        "at": "2026-01-01T00:00:00Z",
    }


def test_capture_start_click_count_and_label():
    from case_authoring import float_authoring_state, grab_user_case_screen, start_user_case_capture
    from teach_loop import add_step, set_context
    from teaching import get_step, load_taught, save_taught, TaughtWorkflow
    from workflow_folder import workflow_dir

    name = "_cases_float"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "test")
    step = add_step(wf, "open inbox")
    save_taught(wf)

    out = start_user_case_capture(load_taught(name), step.id, click_count=2)
    _pass("case label", out.get("case_label") == "Case 1")
    _pass("click count", out.get("click_count") == 2)
    state = float_authoring_state(get_step(load_taught(name), step.id))
    _pass("float phase", state and state["phase"] == "awaiting_capture")
    grab = grab_user_case_screen(
        load_taught(name), step.id, structural=_struct("Paywall"), synthetic_bytes=b"png",
    )
    _pass("grab ok", grab.get("ok"))
    _pass("needs resolution", grab.get("needs_resolution"))
    loaded = get_step(load_taught(name), step.id)
    _pass("step click count applied", loaded.click_count == 2)
    _pass("authoring phase", (loaded.case_authoring or {}).get("phase") == "needs_resolution")


def test_describe_two_click_and_card_title():
    from case_authoring import start_user_case_describe
    from step_cases import add_step_case, case_card_title, case_row_display
    from teach_loop import add_step, set_context
    from teaching import CASE_ORIGIN_USER_CAPTURED, StepCase, TaughtWorkflow, load_taught, save_taught

    wf = TaughtWorkflow(name="_cases_float2")
    set_context(wf, "test")
    step = add_step(wf, "step")
    save_taught(wf)
    out = start_user_case_describe(
        load_taught(wf.name), step.id, "A popup blocks the list", click_count=2,
    )
    _pass("describe label", out.get("case_label") == "Case 1")
    _pass("describe 2-click", out.get("click_count") == 2)

    step = load_taught(wf.name).steps[0]
    add_step_case(
        step,
        StepCase(
            id="c1",
            created_from=CASE_ORIGIN_USER_CAPTURED,
            trigger={"foreground_title": "Paywall"},
            evidence={"frame": "cases/c1.png"},
            resolution={"action": "click", "elem_name": "Close"},
            success_check={"text": "list visible"},
        ),
    )
    _pass("card title", case_card_title(step.cases[0]) == "Case 1")
    row = case_row_display(step.cases[0])
    _pass("row title", row.get("title") == "Case 1")


def test_float_widget_case_paths():
    from float_widget import CASE_FINISH_PATH, CASE_GRAB_PATH, FloatingTeacher

    calls = []

    def _post(path, body):
        calls.append(path)
        if path.endswith("case-grab-screen"):
            return {"ok": True, "needs_resolution": True, "case_label": "Case 1", "click_count": 1}
        return {"ok": True, "case": {"id": "c1"}}

    w = FloatingTeacher(
        workflow="wf", step_id="s1", case_phase="awaiting_capture", case_label="Case 1",
    )
    w._post = _post  # type: ignore[method-assign]
    w._root = None
    w._status = type("S", (), {"config": lambda *a, **k: None})()
    w.on_grab_screen()
    _pass("grab path", any(CASE_GRAB_PATH in p for p in calls))
    w.case_phase = "needs_resolution"
    w.on_finish_case()
    _pass("finish path", any(CASE_FINISH_PATH in p for p in calls))


def main():
    print("=" * 70)
    print("Case float authoring self-test")
    print("=" * 70)
    test_capture_start_click_count_and_label()
    test_describe_two_click_and_card_title()
    test_float_widget_case_paths()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
