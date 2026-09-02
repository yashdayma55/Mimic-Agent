"""PART 3 — float widget mode toggle shares capture path with card."""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def test_float_toggle_and_endpoints():
    from float_widget import (
        CAPTURE_PATH,
        FOCUS_PATH,
        FloatingTeacher,
        build_capture_body,
        capture_endpoint_for_mode,
    )

    recorded = []

    def _cap(body):
        recorded.append(body)
        return {
            "ok": True,
            "outcome": "saved",
            "capture_message": "saved 1 anchor(s)",
            "last_capture": {"outcome": "saved", "message": "saved 1 anchor(s)", "mode": body.get("mode")},
        }

    w = FloatingTeacher(workflow="wf1", step_id="s3", click_count=2, capture_fn=_cap)
    _pass("show mode default", w.mode == "show")
    w.toggle_mode()
    _pass("toggle to watch", w.mode == "watch")
    w.set_target("wf1", "s9", click_count=1, mode="show")
    _pass("active step updated", w.step_id == "s9")
    _pass("endpoint unified", capture_endpoint_for_mode("show") == CAPTURE_PATH)
    _pass("endpoint unified watch", capture_endpoint_for_mode("watch") == CAPTURE_PATH)

    show_body = build_capture_body("wf1", "s9", mode="show", click_count=2)
    _pass("2-click show body batch", show_body.get("batch") is True)
    watch_body = build_capture_body("wf1", "s9", mode="watch", watch_seconds=25)
    _pass("watch body seconds", watch_body.get("seconds") == 25)

    w.mode = "watch"
    w.on_run()
    _pass("watch calls capture path", recorded and recorded[-1].get("mode") == "watch")
    w.mode = "show"
    w.click_count = 1
    w.on_run()
    _pass("show calls capture path", recorded[-1].get("mode") == "show" or "point" in recorded[-1])
    _pass("outcome updated", w.last_outcome == "saved 1 anchor(s)", w.last_outcome)
    w._post = lambda path, body: {"ok": path == FOCUS_PATH, "title": "LinkedIn", "reason": "focused 'LinkedIn'"}  # type: ignore[method-assign]
    w.on_focus()
    _pass("focus action updates status", "focused" in w.last_outcome.lower(), w.last_outcome)


def main():
    print("=" * 70)
    print("PART 3 float toggle self-test")
    print("=" * 70)
    test_float_toggle_and_endpoints()
    print("PART 3 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
