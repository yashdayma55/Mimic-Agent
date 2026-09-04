"""Expandable case mini-steps + continue-parent cascade."""

from __future__ import annotations

import os
import shutil
import sys
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def test_expandable_case_prompt_then_continue():
    from case_steps import (
        begin_expandable_case,
        case_continue_with_parent,
        patch_case_draft,
        save_expandable_case,
    )
    from teach_loop import add_step, set_context
    from teaching import get_step, load_taught, save_taught, TaughtWorkflow
    from workflow_folder import workflow_dir

    name = "_case_expand"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "apollo")
    step = add_step(wf, "extract email")
    step.method = "prompt"
    step.prompt_instruction = "Read visible email"
    step.produces = ["{recipient_email}"]
    save_taught(wf)

    begin_expandable_case(
        load_taught(name),
        step.id,
        when_applies="Access email button is showing",
        what_to_do="Click Access email",
        continue_prompt="Then extract {recipient_email}",
    )
    patch_case_draft(
        load_taught(name),
        step.id,
        method="prompt",
        prompt_instruction="Click the Access email button in Apollo Emails",
    )
    out = save_expandable_case(load_taught(name), step.id)
    _pass("saved", out.get("ok"))
    case = out.get("case") or {}
    sub = case.get("sub_step") or {}
    _pass("method prompt", sub.get("method") == "prompt")
    _pass("continue prompt", "recipient_email" in (sub.get("continue_prompt") or ""))
    parent = get_step(load_taught(name), step.id)
    _pass("parent still prompt", parent.method == "prompt")
    _pass("one case", len(parent.cases or []) == 1)
    from step_cases import step_case_from_dict

    cobj = parent.cases[0]
    if isinstance(cobj, dict):
        cobj = step_case_from_dict(cobj)
    _pass("continue parent", case_continue_with_parent(cobj) is True)


def test_plan_cascades_parent():
    from case_match import plan_step_execution
    from case_steps import begin_expandable_case, patch_case_draft, save_expandable_case
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught
    from workflow_folder import workflow_dir

    name = "_case_cascade"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "apollo")
    step = add_step(wf, "extract email")
    step.method = "prompt"
    step.prompt_instruction = "Read email"
    step.action = {"action": "prompt", "value": "Read email"}
    save_taught(wf)
    begin_expandable_case(
        load_taught(name), step.id,
        when_applies="Access email visible",
        what_to_do="Click Access email",
    )
    patch_case_draft(
        load_taught(name), step.id,
        method="prompt",
        prompt_instruction="Click Access email",
    )
    save_expandable_case(load_taught(name), step.id)
    wf2 = load_taught(name)
    step2 = get_step(wf2, step.id)
    # Force case match via decide mock
    with patch("case_match.decide_step_cases", return_value={
        "action": "case",
        "case": step2.cases[0],
        "log": "forced case",
        "candidates": [],
    }):
        plan = plan_step_execution(wf2, step2, None, before_demo={"foreground_title": "x"})
    _pass("action case", plan.get("action") == "case")
    _pass("continue parent", plan.get("continue_parent") is True)
    _pass("has parent runner", bool(plan.get("parent_runner_step")))
    _pass("case runner prompt", (plan.get("runner_step") or {}).get("action") == "prompt")


if __name__ == "__main__":
    print("test_case_expandable")
    test_expandable_case_prompt_then_continue()
    test_plan_cascades_parent()
    print("all passed")
