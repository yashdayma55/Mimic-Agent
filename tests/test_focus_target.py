"""Focus-target helper for demo prep."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label)


def test_target_window_hint_from_after_frame():
    from teach_compile import target_window_hint
    from teaching import TaughtStep

    step = TaughtStep(id="s1", order=0, user_description="click extensions")
    step.after_frame = {"window_title": "Oliane Piana | LinkedIn - Google Chrome"}
    _pass("uses after_frame title", target_window_hint(step) == "Oliane Piana | LinkedIn - Google Chrome")


def test_find_any_window_skips_own_ui():
    from ui_runner import find_any_window

    own = MagicMock()
    own.is_visible.return_value = True
    chrome = MagicMock()
    chrome.is_visible.return_value = True

    with patch("ui_runner._all_windows", return_value=[own, chrome]):
        with patch("ui_runner._title", side_effect=[
            "MimicAgent and 8 more pages - Personal - Microsoft Edge",
            "Oliane Piana | LinkedIn - Google Chrome",
        ]):
            with patch("ui_runner._proc_name", return_value="chrome.exe"):
                with patch("app_ui_guard.is_own_window", side_effect=lambda t: "mimicagent" in (t or "").lower()):
                    win, title = find_any_window("Oliane Piana | LinkedIn - Google Chrome")
    _pass("skips mimic UI", title == "Oliane Piana | LinkedIn - Google Chrome", title)


def main():
    print("=" * 60)
    print("Focus target self-test")
    print("=" * 60)
    test_target_window_hint_from_after_frame()
    test_find_any_window_skips_own_ui()
    print("ALL FOCUS TARGET CHECKS PASSED")


if __name__ == "__main__":
    main()
