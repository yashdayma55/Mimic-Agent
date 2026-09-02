"""PART 3 — user-created cases: capture and describe routes."""

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
        "browser_url": "https://app.example/signin",
        "at": "2026-01-01T00:00:00Z",
    }


def _workflow():
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow
    from workflow_folder import workflow_dir

    name = "_cases_author_p3"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "test")
    step = add_step(wf, "open inbox")
    return wf, name, step


def test_capture_route_frame_and_trigger():
    from case_authoring import capture_user_case_frame, start_user_case_capture
    from teaching import CASE_ORIGIN_USER_CAPTURED, get_step, load_taught, save_taught

    wf, name, step = _workflow()
    save_taught(wf)

    out = start_user_case_capture(load_taught(name), step.id)
    _pass("capture start ok", out.get("ok"))
    _pass("countdown hint", out.get("countdown_sec") == 3)

    cap = capture_user_case_frame(
        load_taught(name),
        step.id,
        structural=_struct("Sign in — Apollo"),
        synthetic_bytes=b"png-bytes",
    )
    _pass("capture frame ok", cap.get("ok"))
    evidence = cap.get("evidence") or {}
    _pass("frame saved", bool(evidence.get("frame")), evidence.get("frame"))
    from workflow_folder import workflow_dir

    frame_abs = os.path.join(workflow_dir(name), evidence.get("frame", "").replace("/", os.sep))
    _pass("frame file exists", os.path.isfile(frame_abs), frame_abs)

    trigger = cap.get("trigger") or {}
    _pass("structural title", trigger.get("foreground_title") == "Sign in — Apollo")
    _pass("structural a11y", len(trigger.get("a11y_present") or []) >= 1)
    _pass("structural url", "signin" in (trigger.get("browser_url") or ""))

    loaded = get_step(load_taught(name), step.id)
    auth = loaded.case_authoring or {}
    _pass("authoring needs resolution", auth.get("phase") == "needs_resolution")
    _pass("created_from pending", auth.get("created_from") == CASE_ORIGIN_USER_CAPTURED)


def test_describe_route_no_frame_warning_in_card():
    from case_authoring import start_user_case_describe
    from step_cases import case_row_display
    from teaching import CASE_ORIGIN_USER_DESCRIBED, StepCase, get_step, load_taught, save_taught

    wf, name, step = _workflow()
    save_taught(wf)

    desc = "A sign-in panel blocks the email list"
    out = start_user_case_describe(load_taught(name), step.id, desc)
    _pass("describe start ok", out.get("ok"))
    _pass("reliability warning in response", "less reliable" in (out.get("reliability_warning") or "").lower())

    loaded = get_step(load_taught(name), step.id)
    auth = loaded.case_authoring or {}
    _pass("no frame in evidence", not (auth.get("evidence") or {}).get("frame"))
    _pass("description in trigger", auth.get("trigger", {}).get("description") == desc)
    _pass("created_from pending", auth.get("created_from") == CASE_ORIGIN_USER_DESCRIBED)

    row = case_row_display(
        StepCase(
            id="c1",
            created_from=CASE_ORIGIN_USER_DESCRIBED,
            trigger={"description": desc},
            evidence={},
            resolution={"action": "click", "elem_name": "Sign in"},
            success_check={"text": "inbox visible"},
        )
    )
    _pass("card description_only", row.get("description_only") is True)
    _pass("card reliability warning", "less reliable" in (row.get("reliability_warning") or "").lower())


def main():
    print("=" * 70)
    print("PART 3 case authoring self-test")
    print("=" * 70)
    test_capture_route_frame_and_trigger()
    test_describe_route_no_frame_warning_in_card()
    print("PART 3 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
