"""PART 2: guided 2-click capture (synthetic, no human)."""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from teach_loop import (
    add_step,
    answer_show,
    approve_understanding,
    check_chain_incomplete,
    set_context,
    simulate_chain_capture,
)
from teaching import TeachingError, TaughtWorkflow, get_step, load_taught

NAME = "_chain_part2"


def _ok(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label)


def test_batch_capture_one_session():
    import show_capture as sc
    from teach_loop import add_step, answer_show, set_context
    from teaching import TaughtWorkflow, get_step, load_taught

    name = "_chain_batch"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "batch")
    step = add_step(wf, "click A then click B")
    step.click_count = 2
    from teaching import save_taught

    save_taught(wf)
    orig = sc.listen_clicks
    from PIL import Image

    def _fake_clicks(*a, **k):
        frame = Image.new("RGB", (400, 300), (20, 20, 20))
        evs = [
            {"point": [100, 100], "frame_img": frame.copy(), "frame_origin": (0, 0), "click_grab_offset_ms": 12, "a11y_raw": {}, "dom_raw": {}},
            {"point": [200, 200], "frame_img": frame.copy(), "frame_origin": (0, 0), "click_grab_offset_ms": 11, "a11y_raw": {}, "dom_raw": {}},
        ]
        if k.get("on_click"):
            for ev in evs:
                k["on_click"](ev)
            return []
        if k.get("with_frames"):
            return evs
        return [(100, 100), (200, 200)]

    sc.listen_clicks = _fake_clicks
    try:
        out = answer_show(load_taught(name), step.id, batch=True, countdown=0)
    finally:
        sc.listen_clicks = orig
    _ok("batch mode", out.get("mode") == "batch", out.get("mode"))
    step = get_step(load_taught(name), step.id)
    _ok("both anchors from one session", len([a for a in step.anchors if a]) == 2)
    _ok("chain summary asked", any(q.get("kind") == "chain_summary" for q in step.qa_history))


def main():
    print("=" * 70)
    print("PART 2 guided chain capture self-test")
    print("=" * 70)
    wd = os.path.join("workflows", NAME)
    if os.path.isdir(wd):
        shutil.rmtree(wd)

    wf = TaughtWorkflow(name=NAME)
    set_context(wf, "extensions dropdown")
    step = add_step(wf, "click Extensions then click Apollo.io")
    step.click_count = 2
    wf = load_taught(NAME)

    out = simulate_chain_capture(
        wf, step.id,
        points=[(100, 100), (120, 140), (999, 999)],
        names=["Extensions", "Apollo.io"],
    )
    step = get_step(load_taught(NAME), step.id)
    _ok("two anchors stored", len([a for a in step.anchors if a]) == 2)
    _ok("first is Extensions", step.anchors[0]["primary"]["name"] == "Extensions")
    _ok("second is Apollo.io", step.anchors[1]["primary"]["name"] == "Apollo.io")
    _ok("third click ignored", out.get("ignored") is True)
    _ok("ignore note present", "ignoring" in (out.get("note") or "").lower())
    _ok("chain summary question asked", any(q.get("kind") == "chain_summary" for q in step.qa_history))

    wf2 = load_taught(NAME)
    step2 = add_step(wf2, "only one click step")
    step2.click_count = 2
  # simulate only first anchor
    simulate_chain_capture(wf2, step2.id, points=[(50, 50)], names=["OnlyOne"])
    step2 = get_step(load_taught(NAME), step2.id)
    while len(step2.anchors) < 2:
        step2.anchors.append(None)
    incomplete = check_chain_incomplete(load_taught(NAME), step2.id)
    _ok("incomplete chain detected", incomplete is not None)
    step2 = get_step(load_taught(NAME), step2.id)
    _ok("one-click question asked", any(q.get("kind") == "chain_one_click" for q in step2.qa_history))
    try:
        approve_understanding(load_taught(NAME), step2.id)
        _ok("approve with 1 of 2 raises", False)
    except TeachingError as e:
        print(f"  [PASS] approve with 1 of 2 raises ({e})")

    test_batch_capture_one_session()
    print()
    print("PART 2 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
