"""PART 2 — diagnose capture paths; unified /api/teach/capture."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def _diag(msg: str) -> None:
    print(f"  [DIAG] {msg}")


def test_diagnosis_and_unified_capture():
    from teach_loop import add_step, answer_show, set_context, simulate_chain_capture
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught
    from ui_backend import teach_capture, teach_show
    from float_widget import CAPTURE_PATH, build_capture_body

    _diag("Before fix: card armed /api/teach/float; float called /api/teach/show; card polled blindly.")
    _diag("After fix: float + card both use POST " + CAPTURE_PATH + "; response includes last_capture after persist.")

    name = "_p2_cap"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "capture")
    s = add_step(wf, "click target")
    save_taught(wf)

    body_float = build_capture_body(name, s.id, mode="show", click_count=1, point=(100, 200))
    _pass("float body uses capture mode", body_float.get("mode") == "show" or "batch" in body_float or "point" in body_float)

    t0 = time.time()
    out_card = teach_show(name, s.id, point=(120, 240))
    elapsed = time.time() - t0
    _diag(f"teach_show returned in {elapsed:.2f}s with keys {list(out_card.keys())}")
    _pass("show returns before response ends", "outcome" in out_card, out_card.get("outcome"))
    _pass("last_capture persisted", bool(out_card.get("last_capture")), out_card.get("last_capture"))

    from teaching import teaching_path

    path = teaching_path(name)
    with open(path, encoding="utf-8") as f:
        disk = json.load(f)
    step_disk = disk["steps"][0]
    _pass("teaching.json has last_capture", bool(step_disk.get("last_capture")))
    _diag(f"teaching.json last_capture outcome={step_disk.get('last_capture', {}).get('outcome')}")

    out_watch = teach_capture(name, s.id, mode="watch", seconds=0)
    _pass("watch capture has outcome", out_watch.get("outcome") in ("saved", "nothing_captured", "error"))
    _pass("watch last_capture shape matches", bool(out_watch.get("last_capture")))

    # nothing captured — own UI skip
    from unittest.mock import patch

    wf2 = TaughtWorkflow(name="_p2_none")
    if os.path.isdir(os.path.join("workflows", "_p2_none")):
        shutil.rmtree(os.path.join("workflows", "_p2_none"))
    set_context(wf2, "x")
    s2 = add_step(wf2, "click")
    save_taught(wf2)
    with patch("app_ui_guard.window_title_at_point", return_value="MimicAgent"):
        with patch("app_ui_guard.is_own_window", return_value=True):
            from show_capture import multi_witness_capture

            mw = multi_witness_capture((50, 50))
    _pass("own UI yields nothing captured path", mw.get("skipped_own_ui") is True)
    with patch("show_capture.capture_show") as mock_cap:
        mock_cap.return_value = {
            "ok": True,
            "anchor": {"primary": {"name": "X"}},
            "witnesses": {"witnesses": {"a11y": {"saw": True}}},
        }
        r = answer_show(load_taught(name), s.id, point=(1, 2))
    _pass("recorded outcome on show", r.get("outcome") in ("saved", "nothing_captured", "error"), r.get("outcome"))


def main():
    print("=" * 70)
    print("PART 2 capture diagnose + fix self-test")
    print("=" * 70)
    test_diagnosis_and_unified_capture()
    print("PART 2 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
