"""Case authoring must not pollute empty step content."""

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


def _empty_step_workflow():
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow, load_taught, save_taught
    from workflow_folder import workflow_dir

    name = "_cases_step_isolation"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "test")
    step = add_step(wf, "")
    save_taught(wf)
    return name, step.id


def _sample_anchor(name: str = "Sign in") -> dict:
    return {
        "primary": {"name": name, "control_type": "Button", "pipeline": "a11y"},
        "confirmed": True,
        "resolution": {"action": "click", "elem_name": name, "source": "a11y"},
    }


def test_describe_then_resolve_keeps_step_empty():
    from case_authoring import start_user_case_describe, try_complete_user_case_from_show
    from step_cases import case_row_display, case_situation_text
    from teaching import CASE_ORIGIN_USER_DESCRIBED, get_step, load_taught, save_taught

    name, step_id = _empty_step_workflow()
    desc = (
        "A sign-in panel blocks the email list. Apollo shows a modal asking me to sign in again "
        "before I can see new messages."
    )
    start_user_case_describe(load_taught(name), step_id, desc)

    wf = load_taught(name)
    step = get_step(wf, step_id)
    step.anchors = [_sample_anchor()]
    sync = __import__("teaching").sync_step_anchors
    sync(step)
    save_taught(wf)

    out = try_complete_user_case_from_show(
        load_taught(name),
        step_id,
        {"ok": True, "anchors": step.anchors},
    )
    _pass("case completed", out and out.get("ok"))

    loaded = get_step(load_taught(name), step_id)
    _pass("step description empty", not (loaded.user_description or "").strip())
    _pass("step has no anchors", not any(loaded.anchors or []))
    _pass("step has no understanding", not loaded.understanding)
    _pass("step qa empty", not loaded.qa_history)
    _pass("step status draft", loaded.status == "draft")
    _pass("one case saved", len(loaded.cases or []) == 1)

    case = loaded.cases[0]
    _pass("case origin", case.created_from == CASE_ORIGIN_USER_DESCRIBED)
    _pass("case keeps full description", case.trigger.get("description") == desc)
    _pass("display situation", case_situation_text(case) == desc)
    row = case_row_display(case)
    _pass("row situation", row.get("situation") == desc)
    _pass("row resolution", "click Sign in" in (row.get("resolution") or ""))


def test_show_during_authoring_skips_step_questions():
    from case_authoring import start_user_case_describe
    from teach_loop import apply_show_witnesses
    from teaching import get_step, load_taught, save_taught

    name, step_id = _empty_step_workflow()
    start_user_case_describe(
        load_taught(name), step_id, "Paywall appears before the inbox loads",
    )

    apply_show_witnesses(
        load_taught(name),
        step_id,
        {
            "ok": True,
            "resolution": {
                "action": "click",
                "elem_name": "Close",
                "source": "a11y",
                "primary": {"name": "Close", "control_type": "Button"},
            },
            "witnesses": {"a11y": {"saw": True, "account": "Close button"}},
        },
    )

    loaded = get_step(load_taught(name), step_id)
    _pass("no qa during case authoring", not loaded.qa_history)
    _pass("step description still empty", not (loaded.user_description or "").strip())
    _pass("authoring still active", bool(loaded.case_authoring))


def test_cancel_restores_step():
    from case_authoring import cancel_user_case_authoring, start_user_case_describe
    from teaching import get_step, load_taught, save_taught

    name, step_id = _empty_step_workflow()
    wf = load_taught(name)
    step = get_step(wf, step_id)
    step.user_description = "real step text"
    step.qa_history = [{"q": "test", "a": ""}]
    save_taught(wf)

    start_user_case_describe(load_taught(name), step_id, "temporary case note")
    mid = get_step(load_taught(name), step_id)
    mid.anchors = [_sample_anchor("Dismiss")]
    mid.status = "questioning"
    save_taught(load_taught(name))

    cancel_user_case_authoring(load_taught(name), step_id)
    loaded = get_step(load_taught(name), step_id)
    _pass("restored description", loaded.user_description == "real step text")
    _pass("restored qa", len(loaded.qa_history) == 1)
    _pass("restored status", loaded.status == "draft")
    _pass("no anchors after cancel", not any(loaded.anchors or []))
    _pass("authoring cleared", not loaded.case_authoring)


def main():
    print("=" * 70)
    print("Case / step isolation self-test")
    print("=" * 70)
    test_describe_then_resolve_keeps_step_empty()
    test_show_during_authoring_skips_step_questions()
    test_cancel_restores_step()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
