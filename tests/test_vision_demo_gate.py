"""Prompt/vision steps: approve without anchors + demo extracts email."""

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


def test_prompt_approve_without_anchors():
    from teach_loop import add_step, approve_understanding, set_context
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught
    from vision_chat import apply_vision_chat_to_step, ask_vision
    from workflow_folder import workflow_dir

    name = "_vision_demo_gate"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "apollo")
    step = add_step(wf, "read email")
    save_taught(wf)

    ask_vision(
        load_taught(name),
        step.id,
        "what email?",
        synthetic_bytes=b"png",
        synthetic_answer="Visible email: other.person@acme.com",
    )
    with patch("teach_loop.explain_understanding", return_value={"ok": True}):
        apply_vision_chat_to_step(
            load_taught(name),
            step.id,
            remember_prompt="Remember {recipient_email}; changes per person.",
        )

    wf2 = load_taught(name)
    s2 = get_step(wf2, step.id)
    s2.understanding = {
        "target": "visible email",
        "action": "prompt",
        "success_check": "email known as {recipient_email}",
        "plain_summary": "read email with vision",
        "assumptions": ["Apollo panel open"],
        "varies_each_run": ["{recipient_email}"],
        "constants": [],
        "uses_from_earlier": [],
    }
    s2.status = "questioning"
    s2.method = "prompt"
    save_taught(wf2)

    approved = approve_understanding(load_taught(name), step.id)
    _pass("understood without anchors", approved.status == "understood")
    _pass("still prompt method", approved.method == "prompt")
    _pass("no anchors required", not (approved.anchors or []))


def test_vision_prompt_demo_extract():
    from teach_compile import demo_taught_step
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught
    from workflow_folder import workflow_dir

    name = "_vision_demo_run"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "apollo")
    step = add_step(wf, "read email")
    step.method = "prompt"
    step.prompt_instruction = "Read the visible email from Apollo Emails"
    step.produces = ["{recipient_email}"]
    step.parameters = ["{recipient_email}"]
    step.action = {"action": "prompt", "value": step.prompt_instruction}
    step.status = "understood"
    step.understanding = {
        "target": "email",
        "action": "prompt",
        "success_check": "email known as {recipient_email}",
        "plain_summary": "extract email",
        "assumptions": ["panel open"],
        "varies_each_run": ["{recipient_email}"],
        "constants": [],
        "uses_from_earlier": [],
    }
    save_taught(wf)

    with patch(
        "vision_chat.execute_vision_prompt_step",
        return_value={
            "ok": True,
            "result": __import__("ui_runner", fromlist=["StepResult"]).StepResult(
                ok=True,
                reason="vision extracted {recipient_email}=new.person@corp.io",
                value_after="new.person@corp.io",
            ),
            "answer": "new.person@corp.io",
            "facts": {"emails": ["new.person@corp.io"], "primary_email": "new.person@corp.io"},
            "produced": {"{recipient_email}": "new.person@corp.io"},
            "frame_path": "vision_chat/x.png",
        },
    ), patch(
        "teach_compile.focus_step_target",
        return_value={"ok": True, "title": "LinkedIn"},
    ), patch(
        "success_signals.snapshot_structural_state",
        return_value={"foreground_title": "LinkedIn"},
    ):
        out = demo_taught_step(load_taught(name), step.id, mode="manual")

    _pass("demo ok", out.get("ok"), out)
    _pass("produced email", (out.get("produced") or {}).get("{recipient_email}") == "new.person@corp.io")
    loaded = get_step(load_taught(name), step.id)
    _pass("status demonstrated", loaded.status == "demonstrated")


def test_reflect_accepts_other_person_email():
    from teach_loop import add_step, reflect_on_demo, set_context
    from teaching import TaughtWorkflow, save_taught
    from workflow_folder import workflow_dir

    name = "_vision_reflect_email"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "apollo")
    step = add_step(wf, "read email")
    step.method = "prompt"
    step.produces = ["{recipient_email}"]
    step.action = {"action": "prompt"}
    step.understanding = {
        "success_check": "the email address is known as {recipient_email}",
        "action": "prompt",
    }
    step.demo = {
        "ok": True,
        "reason": "vision extracted {recipient_email}=oliane@amazon.com",
        "observed": "oliane@amazon.com",
        "produced": {"{recipient_email}": "oliane@amazon.com"},
    }
    save_taught(wf)
    r = reflect_on_demo(wf, step.id)
    _pass("matches other person", r.get("matches_understanding") is True, r)


if __name__ == "__main__":
    print("test_vision_demo_gate")
    test_prompt_approve_without_anchors()
    test_vision_prompt_demo_extract()
    test_reflect_accepts_other_person_email()
    print("all passed")
