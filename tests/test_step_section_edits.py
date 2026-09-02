"""Section edits: Q&A patches, anchor witness text, reflection — no LLM."""

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


def test_qa_and_reflection_edits():
    from teach_loop import add_step, set_context, update_step
    from teaching import TaughtWorkflow, get_step, load_taught

    name = "_sec_edits"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "edit sections")
    s = add_step(wf, "click save")
    s.qa_history = [{"q": "Which button?", "a": "old answer"}]
    from teaching import save_taught

    save_taught(wf)
    update_step(
        wf,
        s.id,
        qa_updates=[{"q": "Which button?", "a": "the blue Save icon"}],
        reflection={"what_i_did": "clicked save", "what_i_observed": "dialog closed"},
    )
    loaded = get_step(load_taught(name), s.id)
    rec = next(r for r in loaded.qa_history if r.get("q") == "Which button?")
    _pass("qa answer updated", rec.get("a") == "the blue Save icon", rec)
    _pass("qa user_edited", rec.get("user_edited") is True)
    _pass("reflection saved", loaded.reflection.get("what_i_did") == "clicked save")
    _pass("reflection user_edited", loaded.reflection.get("user_edited") is True)


def test_anchor_witness_edits():
    from teach_loop import add_step, set_context, update_step
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught

    name = "_anc_wit"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "anchors")
    s = add_step(wf, "click target")
    s.anchors = [{
        "primary": {"name": "Save"},
        "parent_path": "toolbar",
        "witnesses": {"a11y": {"account": "old a11y"}, "dom": {}, "vision": {}},
    }]
    save_taught(wf)
    update_step(
        wf,
        s.id,
        anchor_edits=[{
            "sub_index": 0,
            "name": "Save button",
            "wit_a11y": "user says it is the floppy disk icon",
        }],
    )
    loaded = get_step(load_taught(name), s.id)
    anc = loaded.anchors[0]
    _pass("name updated", anc["primary"]["name"] == "Save button")
    _pass("witness updated", "floppy" in (anc["witnesses"]["a11y"]["account"] or ""))
    _pass("witness user_edited", anc["witnesses"]["a11y"].get("user_edited") is True)


def main():
    print("=== step section edits ===")
    test_qa_and_reflection_edits()
    test_anchor_witness_edits()
    print("ALL SECTION EDIT CHECKS PASSED")


if __name__ == "__main__":
    main()
