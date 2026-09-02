"""PART 4 — resolution line and non-selectable witness display."""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def test_resolution_line_format():
    from show_capture import _resolution_line

    _pass(
        "a11y confirmed",
        _resolution_line("a11y", {"confirmed_by_vision": True}) == "resolved by a11y · confirmed by vision",
    )
    _pass(
        "vision blind tree",
        "vision" in _resolution_line("vision", {"confirmed_by_vision": True}),
    )
    _pass(
        "unconfirmed",
        "unconfirmed" in _resolution_line("a11y", {"unconfirmed": True}),
    )


def test_ui_html_has_resolution_and_no_conflict_buttons():
    path = os.path.join(ROOT, "review_ui.html")
    html = open(path, encoding="utf-8").read()
    _pass("resolve-line css", "resolve-line" in html)
    _pass("resolution_line in body", "resolution_line" in html)
    _pass("wit-line mono display", "wit-line" in html)
    _pass("no witness conflict buttons", 'data-act="witness"' not in html or html.count('witness_conflict') == 0)
    _pass("vision mismatch buttons", "visionyes" in html and "parentyes" in html)


def main():
    print("=" * 70)
    print("PART 4 card display self-test")
    print("=" * 70)
    test_resolution_line_format()
    test_ui_html_has_resolution_and_no_conflict_buttons()
    print("PART 4 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
