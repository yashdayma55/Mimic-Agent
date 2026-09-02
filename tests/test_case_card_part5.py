"""PART 5 — case card UI: origin badges, description warning, add-case routes."""

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


def _resolution():
    return {"action": "click", "elem_name": "Sign in", "elem_type": "Button"}


def _success_check():
    return {"check": {"type": "foreground_title", "expected": "Apollo — Home"}, "text": "Apollo home"}


def test_origin_badges():
    from step_cases import case_origin_badge, case_row_display
    from teaching import (
        CASE_ORIGIN_HALT,
        CASE_ORIGIN_USER_CAPTURED,
        CASE_ORIGIN_USER_DESCRIBED,
        StepCase,
    )

    _pass("halt badge", case_origin_badge(CASE_ORIGIN_HALT) == "from a halt")
    _pass("captured badge", case_origin_badge(CASE_ORIGIN_USER_CAPTURED) == "you captured this")
    _pass("described badge", case_origin_badge(CASE_ORIGIN_USER_DESCRIBED) == "you described this")

    for origin, badge in (
        (CASE_ORIGIN_HALT, "from a halt"),
        (CASE_ORIGIN_USER_CAPTURED, "you captured this"),
        (CASE_ORIGIN_USER_DESCRIBED, "you described this"),
    ):
        trigger = (
            {"description": "sign-in panel"}
            if origin == CASE_ORIGIN_USER_DESCRIBED
            else {"foreground_title": "Sign in — Apollo"}
        )
        evidence = {} if origin == CASE_ORIGIN_USER_DESCRIBED else {"frame": "cases/c1.png"}
        row = case_row_display(
            StepCase(
                id="c1",
                created_from=origin,
                trigger=trigger,
                evidence=evidence,
                resolution=_resolution(),
                success_check=_success_check(),
            )
        )
        _pass(f"{origin} row badge", row.get("origin_badge") == badge)


def test_reliability_warning_only_for_described():
    from step_cases import case_row_display
    from teaching import CASE_ORIGIN_HALT, CASE_ORIGIN_USER_DESCRIBED, StepCase

    halt_row = case_row_display(
        StepCase(
            id="c1",
            created_from=CASE_ORIGIN_HALT,
            trigger={"foreground_title": "Sign in"},
            evidence={"frame": "cases/c1.png"},
            resolution=_resolution(),
            success_check=_success_check(),
        )
    )
    _pass("halt no warning", not halt_row.get("reliability_warning"))
    _pass("halt not description_only", halt_row.get("description_only") is not True)

    described_row = case_row_display(
        StepCase(
            id="c2",
            created_from=CASE_ORIGIN_USER_DESCRIBED,
            trigger={"description": "A sign-in panel blocks the email list"},
            evidence={},
            resolution=_resolution(),
            success_check=_success_check(),
        )
    )
    _pass("described warning", "less reliable" in (described_row.get("reliability_warning") or ""))
    _pass("described flag", described_row.get("description_only") is True)


def test_add_case_routes_in_ui():
    ui_path = os.path.join(ROOT, "review_ui.html")
    with open(ui_path, encoding="utf-8") as f:
        html = f.read()
    _pass("add case button", 'data-act="caseadd"' in html)
    _pass("capture route", 'data-act="casecap"' in html)
    _pass("describe route", 'data-act="casedescopen"' in html)
    _pass("origin badge class", "case-origin-badge" in html)
    _pass("reliability class", "case-reliability" in html)
    _pass("halt badge text", "from a halt" in html)
    _pass("captured badge text", "you captured this" in html)
    _pass("described badge text", "you described this" in html)


def main():
    print("=" * 70)
    print("PART 5 case card self-test")
    print("=" * 70)
    test_origin_badges()
    test_reliability_warning_only_for_described()
    test_add_case_routes_in_ui()
    print("PART 5 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
