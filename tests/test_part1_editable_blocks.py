"""PART 1 — every block editable/removable; approval reset rules."""

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


def _full_understanding():
    return {
        "target": "Save button",
        "action": "click",
        "varies_each_run": [],
        "constants": ["nothing changes"],
        "uses_from_earlier": [],
        "success_check": "file is saved",
        "assumptions": ["Notepad is open"],
        "plain_summary": "Click Save.",
    }


def test_material_edit_resets_approval():
    from teach_loop import add_step, approve_step, set_context, update_step
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught

    name = "_p1_material"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "save file")
    s = add_step(wf, "click the Save button")
    s.understanding = _full_understanding()
    s.demo = {"ok": True, "reason": "saved"}
    s.reflection = {"what_i_did": "clicked", "what_i_observed": "saved"}
    save_taught(wf)
    approve_step(wf, s.id, skip_rehearsal=True)
    loaded = get_step(load_taught(name), s.id)
    _pass("starts approved", loaded.status == "approved")

    update_step(wf, s.id, description="click the blue Save icon instead", re_explain=True)
    loaded = get_step(load_taught(name), s.id)
    _pass("status questioning after target edit", loaded.status == "questioning", loaded.status)
    _pass("demo cleared", loaded.demo is None)
    _pass("reflection cleared", loaded.reflection is None)
    _pass("edit notice set", "approving again" in (loaded.edit_notice or "").lower())
    open_clarify = [
        q for q in loaded.qa_history
        if (q.get("kind") or "") == "clarify" and not (q.get("a") or "").strip()
    ]
    _pass("at most one re-ask", len(open_clarify) <= 1, len(open_clarify))
    _pass("understanding refreshed", bool(loaded.understanding))


def test_cosmetic_edit_keeps_approval():
    from teach_loop import add_step, approve_step, set_context, update_step
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught

    name = "_p1_cosmetic"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "notes")
    s = add_step(wf, "click save")
    s.understanding = _full_understanding()
    save_taught(wf)
    approve_step(wf, s.id, skip_rehearsal=True)
    update_step(wf, s.id, memory_note="write short friendly emails", re_explain=True)
    loaded = get_step(load_taught(name), s.id)
    _pass("status still approved", loaded.status == "approved", loaded.status)
    _pass("notes saved", "friendly" in (loaded.memory_note or ""))
    _pass("stale edit notice cleared", not loaded.edit_notice)


def test_stale_edit_notice_cleared_on_reapprove():
    from teach_loop import add_step, approve_step, set_context, update_step
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught

    name = "_p1_notice"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "save file")
    s = add_step(wf, "click the Save button")
    s.understanding = _full_understanding()
    s.demo = {"ok": True, "reason": "saved"}
    save_taught(wf)
    approve_step(wf, s.id, skip_rehearsal=True)
    update_step(wf, s.id, description="click the blue Save icon instead", re_explain=True)
    loaded = get_step(load_taught(name), s.id)
    _pass("notice after material edit", bool(loaded.edit_notice))
    wf2 = load_taught(name)
    step2 = get_step(wf2, s.id)
    step2.understanding = _full_understanding()
    step2.demo = {"ok": True, "reason": "saved"}
    step2.status = "demonstrated"
    save_taught(wf2)
    approve_step(wf2, s.id)
    loaded = get_step(load_taught(name), s.id)
    _pass("approved again", loaded.status == "approved")
    _pass("notice cleared after approve", not loaded.edit_notice)


def test_remove_photo_keeps_approval():
    from teach_loop import add_step, approve_step, set_context, update_step
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught, teaching_path
    from workflow_folder import workflow_dir

    name = "_p1_photo"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "shots")
    s = add_step(wf, "click save")
    s.understanding = _full_understanding()
    rel = os.path.join("anchors", f"{s.id}_user_1.png")
    abs_path = os.path.join(workflow_dir(name), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(b"png")
    s.photos = [{"path": rel, "filename": "x.png"}]
    save_taught(wf)
    approve_step(wf, s.id, skip_rehearsal=True)
    update_step(wf, s.id, drop_photo=rel)
    loaded = get_step(load_taught(name), s.id)
    _pass("photo removed from list", not loaded.photos)
    _pass("photo removed from disk", not os.path.isfile(abs_path))
    _pass("still approved", loaded.status == "approved")


def test_redemo_keeps_approval():
    from unittest.mock import patch

    from teach_compile import demo_taught_step
    from teach_loop import add_step, approve_step, set_context
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught

    name = "_p1_redemo"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "save file")
    s = add_step(wf, "click the Save button")
    s.understanding = _full_understanding()
    s.action = {"action": "click", "elem_name": "Save", "elem_type": "Button", "window_title": "Notepad"}
    save_taught(wf)
    approve_step(wf, s.id, skip_rehearsal=True)
    with patch("ui_runner.run_verified_plan", return_value={"ok": True, "results": []}):
        with patch("success_signals.verify_success_check", return_value={"ok": True, "reason": "saved", "cost": "free"}):
            demo_taught_step(wf, s.id, mode="manual")
    loaded = get_step(load_taught(name), s.id)
    _pass("still approved after redemo", loaded.status == "approved")
    _pass("demo result stored", bool(loaded.demo and loaded.demo.get("ok")))


def main():
    print("=" * 70)
    print("PART 1 editable blocks self-test")
    print("=" * 70)
    test_material_edit_resets_approval()
    test_cosmetic_edit_keeps_approval()
    test_stale_edit_notice_cleared_on_reapprove()
    test_redemo_keeps_approval()
    test_remove_photo_keeps_approval()
    print("PART 1 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
