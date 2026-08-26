"""Starting screen: every run begins here; dynamic bits are marked."""

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


def main():
    print("=== starting screen ===")
    from teach_loop import add_step, explain_start, set_context
    from teaching import TaughtWorkflow, load_taught

    name = "_start_li"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "automated email pipeline from LinkedIn")
    start = explain_start(
        wf,
        description="This is a LinkedIn profile page",
        varies_note="the person's profile keeps changing each run",
    )
    print("  start", start)
    _pass("summary mentions every run", "every run" in (start.get("summary") or "").lower())
    params = start.get("parameters") or []
    _pass("marks {linkedin_profile}", "{linkedin_profile}" in params, str(params))
    loaded = load_taught(name)
    _pass("start_screen persisted", bool(loaded.start_screen))
    _pass("same param on disk", "{linkedin_profile}" in (loaded.start_screen.get("parameters") or []))
    add_step(wf, "click the More button")
    from teach_loop import explain_start as _again

    start2 = _again(wf, description="LinkedIn profile", varies_note="profile changes")
    _pass("still has param after a step", "{linkedin_profile}" in (start2.get("parameters") or []))
    print("ALL START-SCREEN CHECKS PASSED")


if __name__ == "__main__":
    main()
