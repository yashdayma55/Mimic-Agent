"""PART 1: click_count + anchors data model."""

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
    validate_click_count,
)

NAME = "_chain_part1"


def _ok(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label)


def main():
    print("=" * 70)
    print("PART 1 chain data model self-test")
    print("=" * 70)
    path = teaching_path(NAME)
    wd = os.path.dirname(path)
    if os.path.isdir(wd):
        shutil.rmtree(wd)

    a0 = {"primary": {"name": "Extensions", "control_type": "Button"}, "agreement": "single"}
    a1 = {"primary": {"name": "Apollo.io", "control_type": "Button"}, "agreement": "single"}
    wf = TaughtWorkflow(
        name=NAME,
        steps=[
            TaughtStep(
                id="s1",
                order=0,
                user_description="click Extensions then Apollo.io",
                click_count=2,
                anchors=[a0, a1],
                action={"action": "chain", "clicks": [{"action": "click"}, {"action": "click"}]},
            )
        ],
    )
    save_taught(wf)
    loaded = load_taught(NAME)
    step = loaded.steps[0]
    _ok("click_count survived", step.click_count == 2)
    _ok("two anchors in order", len(step.anchors) == 2)
    _ok("first anchor name", step.anchors[0]["primary"]["name"] == "Extensions")
    _ok("second anchor name", step.anchors[1]["primary"]["name"] == "Apollo.io")
    _ok("anchor alias is anchors[0]", step.anchor == step.anchors[0])
    _ok("anchor alias name", step.anchor["primary"]["name"] == "Extensions")

    try:
        validate_click_count(3)
        _ok("click_count=3 rejected", False)
    except TeachingError as e:
        print(f"  [PASS] click_count=3 rejected ({e})")

    print("PART 1 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
