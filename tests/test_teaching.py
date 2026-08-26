"""Phase 1: TaughtStep / TaughtWorkflow persist and protect approved steps."""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from teaching import (
    TaughtStep,
    TaughtWorkflow,
    TeachingError,
    load_taught,
    save_taught,
    teaching_path,
)

NAME = "_teach_phase1"


def _ok(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label)


def main():
    print("=" * 70)
    print("PHASE 1 teaching model self-test")
    print("=" * 70)
    path = teaching_path(NAME)
    wd = os.path.dirname(path)
    if os.path.isdir(wd):
        shutil.rmtree(wd)

    wf = TaughtWorkflow(
        name=NAME,
        context="teach a tiny notepad flow",
        steps=[
            TaughtStep(id="s1", order=0, user_description="open notepad", status="approved",
                       action={"action": "launch_app", "value": "notepad"}),
            TaughtStep(id="s2", order=1, user_description="click the editor", status="draft"),
            TaughtStep(id="s3", order=2, user_description="type hello", status="draft",
                       varies_note="the text changes", parameters=["{body}"]),
        ],
    )
    save_taught(wf)
    loaded = load_taught(NAME)
    _ok("round-trip equality", loaded.to_dict() == wf.to_dict())
    _ok("approved step survived", loaded.steps[0].status == "approved")
    _ok("approved action survived", loaded.steps[0].action == {"action": "launch_app", "value": "notepad"})

    loaded.steps[1].user_description = "click the text editing area"
    save_taught(loaded)
    again = load_taught(NAME)
    _ok("draft mutation persisted", again.steps[1].user_description == "click the text editing area")
    _ok("approved still original", again.steps[0].user_description == "open notepad")

    clobber = load_taught(NAME)
    clobber.steps[0].user_description = "silently clobber"
    clobber.steps[0].status = "draft"
    save_taught(clobber)
    protected = load_taught(NAME)
    _ok(
        "approved not overwritten by draft save",
        protected.steps[0].status == "approved"
        and protected.steps[0].user_description == "open notepad",
    )

    mutate_approved = load_taught(NAME)
    mutate_approved.steps[0].user_description = "still approved but changed"
    try:
        save_taught(mutate_approved)
        _ok("mutating approved raises", False)
    except TeachingError as e:
        print(f"  [PASS] mutating approved raises ({e})")

    final = load_taught(NAME)
    _ok("approved text unchanged after refused save", final.steps[0].user_description == "open notepad")
    _ok("three steps still present", len(final.steps) == 3)
    print("PHASE 1 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
