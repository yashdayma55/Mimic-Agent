"""PART 4: understanding + UI-facing chain description."""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from teach_loop import add_step, explain_understanding, resolve_action, set_context, simulate_chain_capture
from teaching import TaughtWorkflow, get_step, load_taught

NAME = "_chain_part4"


def _ok(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label)


def main():
    print("=" * 70)
    print("PART 4 chain understanding self-test")
    print("=" * 70)
    wd = os.path.join("workflows", NAME)
    if os.path.isdir(wd):
        shutil.rmtree(wd)

    wf = TaughtWorkflow(name=NAME)
    set_context(wf, "apollo extension")
    step = add_step(wf, "click Extensions then click Apollo.io")
    step.click_count = 2
    wf = load_taught(NAME)
    simulate_chain_capture(
        wf, step.id,
        points=[(10, 10), (20, 20)],
        names=["Extensions", "Apollo.io"],
    )
    wf = load_taught(NAME)
    step = get_step(wf, step.id)
    step.qa_history.append({
        "q": "How will I know this step succeeded?",
        "a": "the Apollo panel appearing",
        "source": "chat",
    })
    from teaching import save_taught

    save_taught(wf)
    u = explain_understanding(wf, step.id)
    _ok("action is chain", u.get("action") == "chain")
    _ok("names both targets", "Extensions" in (u.get("plain_summary") or "") and "Apollo.io" in (u.get("plain_summary") or ""))
    _ok("success check included", "Apollo panel" in (u.get("plain_summary") or ""))
    _ok("chain_clicks listed", u.get("chain_clicks") == ["Extensions", "Apollo.io"])
    act = resolve_action(get_step(load_taught(NAME), step.id))
    _ok("resolved action is chain", act.get("action") == "chain")
    _ok("two clicks in action", len(act.get("clicks") or []) == 2)
    print("  summary:", u.get("plain_summary"))
    print("PART 4 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
