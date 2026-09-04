"""Case sub-step teaching + LinkedIn title not used as trigger."""

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


def test_linkedin_title_not_trigger():
    from case_authoring import sanitize_case_trigger

    tr = sanitize_case_trigger(
        {
            "foreground_title": "Person: lucindo | LinkedIn - Google Chrome",
            "a11y_present": [
                {"name": "Home", "control_type": "Button"},
                {"name": "Access email", "control_type": "Button"},
            ],
        },
        situation_note="No address yet — Access email is showing",
    )
    _pass("dropped profile title", "foreground_title" not in tr, tr)
    names = [e.get("name") for e in (tr.get("a11y_present") or [])]
    _pass("kept access email", "Access email" in names, names)
    _pass("kept situation", "Access email" in (tr.get("description") or ""))


def test_substep_saved_on_case():
    from case_authoring import (
        complete_user_case_resolution,
        set_case_sub_description,
        start_user_case_describe,
    )
    from teach_loop import add_step, set_context
    from teaching import get_step, load_taught, save_taught, TaughtWorkflow
    from workflow_folder import workflow_dir

    name = "_case_substep"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "apollo")
    step = add_step(wf, "read email")
    step.method = "prompt"
    save_taught(wf)

    start_user_case_describe(
        load_taught(name),
        step.id,
        "Emails shows Access email instead of an address",
    )
    set_case_sub_description(
        load_taught(name), step.id, "Click Access email",
    )
    loaded = get_step(load_taught(name), step.id)
    loaded.anchors = [{
        "primary": {"name": "Access email", "control_type": "Button"},
        "point": [100, 200],
    }]
    save_taught(load_taught(name))
    wf2 = load_taught(name)
    s2 = get_step(wf2, step.id)
    s2.anchors = [{
        "primary": {"name": "Access email", "control_type": "Button"},
        "point": [100, 200],
    }]
    save_taught(wf2)

    out = complete_user_case_resolution(
        load_taught(name),
        step.id,
        {"action": "click", "elem_name": "Access email", "point": [100, 200]},
    )
    _pass("saved", out.get("ok"))
    case = out.get("case") or {}
    _pass("sub desc", (case.get("sub_step") or {}).get("user_description") == "Click Access email")
    parent = get_step(load_taught(name), step.id)
    _pass("parent still prompt", parent.method == "prompt")
    _pass("parent not click", (parent.action or {}).get("action") != "click" or parent.method == "prompt")
    _pass("authoring cleared", not parent.case_authoring)
    _pass("one case", len(parent.cases or []) == 1)


if __name__ == "__main__":
    print("test_case_substep")
    test_linkedin_title_not_trigger()
    test_substep_saved_on_case()
    print("all passed")
