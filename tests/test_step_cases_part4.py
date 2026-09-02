"""PART 4 — visible, inspectable, deletable cases."""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def _sample_case(case_id: str = "c1", *, times_matched: int = 0, last_matched: str | None = None):
    from teaching import StepCase

    return StepCase(
        id=case_id,
        created_from="halt",
        trigger={"foreground_title": "Sign in — Apollo", "a11y_present": [{"name": "Sign in to continue"}]},
        evidence={"frame": f"cases/{case_id}.png", "window_title": "Sign in — Apollo"},
        resolution={"action": "click", "elem_name": "Sign in"},
        success_check={"check": {"type": "foreground_title", "expected": "Apollo — Home"}, "text": "Apollo home open"},
        times_matched=times_matched,
        last_matched=last_matched,
    )


def test_case_row_display():
    from step_cases import case_row_display

    last = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    row = case_row_display(_sample_case(times_matched=4, last_matched=last))
    _pass("label present", bool(row.get("label")))
    _pass("trigger mono", "title:Sign in" in row.get("trigger_mono", ""))
    _pass("success check", row.get("success_check") == "Apollo home open")
    _pass("frame path", row.get("frame") == "cases/c1.png")
    _pass("stats mention matched", "matched 4" in row.get("stats", ""))
    _pass("stats mention days", "2 days ago" in row.get("stats", ""))
    never = case_row_display(_sample_case(times_matched=0))
    _pass("never matched flag", never.get("never_matched") is True)
    _pass("never matched text", "never matched" in never.get("stats", ""))


def test_explain_understanding_mentions_cases():
    from step_cases import add_step_case
    from teach_loop import add_step, explain_understanding, set_context
    from teaching import TaughtWorkflow, save_taught

    name = "_cases_p4_explain"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "reveal email")
    step = add_step(wf, "click access email")
    add_step_case(step, _sample_case())
    save_taught(wf)
    u = explain_understanding(wf, step.id)
    assumptions = u.get("assumptions") or []
    joined = " ".join(assumptions).lower()
    _pass("mentions case", "case" in joined or "sign in" in joined)
    _pass("mentions normally", "normally" in joined)


def test_remove_case_keeps_approval_and_deletes_file():
    from step_cases import add_step_case
    from teach_loop import add_step, approve_step, remove_case, set_context
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught
    from workflow_folder import workflow_dir

    name = "_cases_p4_remove"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "test")
    step = add_step(wf, "click access email")
    step.understanding = {"target": "email", "success_check": "visible"}
    add_step_case(step, _sample_case("c1"))
    add_step_case(step, _sample_case("c2"))
    frame = os.path.join(wd, "cases", "c1.png")
    os.makedirs(os.path.dirname(frame), exist_ok=True)
    with open(frame, "wb") as f:
        f.write(b"png")
    save_taught(wf)
    approve_step(wf, step.id, skip_rehearsal=True)
    loaded = get_step(load_taught(name), step.id)
    _pass("starts approved", loaded.status == "approved")
    _pass("two cases", len(loaded.cases) == 2)

    remove_case(wf, step.id, "c1")
    loaded = get_step(load_taught(name), step.id)
    _pass("one case left", len(loaded.cases) == 1)
    _pass("still approved", loaded.status == "approved")
    _pass("description unchanged", loaded.user_description == "click access email")
    _pass("evidence file deleted", not os.path.isfile(frame))
    _pass("other case kept", loaded.cases[0].id == "c2")


def main():
    print("=" * 70)
    print("PART 4 cases UI + removal self-test")
    print("=" * 70)
    test_case_row_display()
    test_explain_understanding_mentions_cases()
    test_remove_case_keeps_approval_and_deletes_file()
    print("PART 4 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
