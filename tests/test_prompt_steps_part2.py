"""PART 2 — prompt-driven steps fallback."""

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


def test_prompt_method_save_and_compile():
    from prompt_steps import METHOD_PROMPT, PROMPT_RELIABILITY_NOTE, prompt_card_note, save_prompt_method
    from teach_compile import compile_taught, step_to_node
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught
    from workflow_folder import workflow_dir

    name = "_prompt_p2"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "test")
    step = add_step(wf, "hover email then click copy")
    step.status = "approved"
    save_taught(wf)

    instr = "hover over the email address, then click the copy icon that appears next to it"
    out = save_prompt_method(load_taught(name), step.id, instr)
    _pass("save ok", out.get("ok"))
    loaded = get_step(load_taught(name), step.id)
    _pass("method prompt", loaded.method == METHOD_PROMPT)
    _pass("instruction stored", loaded.prompt_instruction == instr)
    note = prompt_card_note(loaded)
    _pass("reliability note", note and "less deterministic" in note.lower())
    _pass("card exposes note", PROMPT_RELIABILITY_NOTE in (note or ""))

    node = step_to_node(loaded)
    _pass("compiled action prompt", node.action == "prompt")

    try:
        compile_taught(load_taught(name))
        _pass("compile without success", False, "should have failed")
    except Exception as e:
        _pass("compile rejects no success", "success check" in str(e).lower())

    wf2 = load_taught(name)
    step2 = get_step(wf2, step.id)
    step2.understanding = {"success_check": "email copied to clipboard"}
    save_taught(wf2)
    compiled = compile_taught(load_taught(name))
    _pass("compile with success", compiled.get("ok"))


def main():
    print("=" * 70)
    print("PART 2 prompt steps self-test")
    print("=" * 70)
    test_prompt_method_save_and_compile()
    print("PART 2 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
