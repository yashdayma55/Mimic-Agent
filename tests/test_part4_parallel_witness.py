"""PART 4 — parallel witnesses, combined VLM, own-UI guard."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def test_own_ui_excluded():
    from app_ui_guard import SKIP_LOG, clear_skip_log, memory_note_for_summary
    from show_capture import multi_witness_capture

    clear_skip_log()
    with patch("app_ui_guard.window_title_at_point", return_value="MimicAgent"):
        with patch("app_ui_guard.is_own_window", return_value=True):
            mw = multi_witness_capture((100, 100))
    _pass("skipped own UI flag", mw.get("skipped_own_ui") is True)
    vis = (mw.get("witnesses") or {}).get("vision", {})
    _pass("vision not dashboard", "MimicAgent" not in (vis.get("account") or ""))
    _pass("skip log", any("own UI" in x for x in SKIP_LOG), SKIP_LOG)


def test_parallel_witnesses_and_model_judgement():
    from show_capture import multi_witness_capture

    fake_a11y = {
        "name": "Save",
        "control_type": "Button",
        "rect": [10, 10, 50, 50],
        "parent_path": "Toolbar",
    }
    fake_vlm = {
        "account": "a Save button in the toolbar.",
        "agrees_with_a11y": True,
        "judgement": "agree",
        "saw": True,
        "described": "Save button",
        "confidence": "medium",
        "model_judgement": "agree",
    }

    with patch("app_ui_guard.window_title_at_point", return_value="Notepad"):
        with patch("app_ui_guard.is_own_window", return_value=False):
            with patch("show_capture._element_at", return_value=fake_a11y):
                with patch("show_capture._browser_at", return_value={"name": "Save", "control_type": "button"}):
                    with patch("show_capture._combined_vlm_witness", return_value=fake_vlm):
                        mw = multi_witness_capture((30, 30), crop_path=None, ctx_path=None)

    wits = mw.get("witnesses") or {}
    res = mw.get("resolution") or {}
    _pass("a11y present", "a11y" in wits)
    _pass("vision present", "vision" in wits)
    _pass("resolution source recorded", res.get("source") in ("a11y", "dom", "vision"))
    _pass("resolution line recorded", bool(res.get("resolution_line")))


def test_empty_notes_phrase():
    from app_ui_guard import memory_note_for_summary

    _pass(
        "empty notes phrase",
        "no notes" in memory_note_for_summary("").lower(),
        memory_note_for_summary(""),
    )
    _pass(
        "placeholder not content",
        "no notes" in memory_note_for_summary("e.g. write the cold email").lower(),
    )


def main():
    print("=" * 70)
    print("PART 4 parallel witnesses self-test")
    print("=" * 70)
    test_own_ui_excluded()
    test_parallel_witnesses_and_model_judgement()
    test_empty_notes_phrase()
    print("PART 4 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
