"""PART 4 — scroll destination intents and interaction chains."""

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


def test_scroll_intent_no_pixel_delta():
    from scroll_actions import scroll_intent_from_teaching

    intent = scroll_intent_from_teaching(
        to_find="the Emails section",
        within="Apollo Side Panel",
    )
    _pass("to_find stored", intent.get("to_find") == "the Emails section")
    _pass("within stored", intent.get("within") == "Apollo Side Panel")
    _pass("no delta key", "delta" not in intent and "pixels" not in intent)


def test_scroll_stops_when_visible():
    import os_input
    from scroll_actions import execute_scroll

    os_input.reset_calls()
    calls = {"n": 0}

    def _visible():
        calls["n"] += 1
        return calls["n"] >= 2

    with patch("scroll_actions._target_visible", side_effect=lambda t, e: _visible()):
        with patch("hover_actions.snapshot_a11y_elements", return_value=[]):
            out = execute_scroll({"to_find": "Emails section", "max_steps": 8})
    _pass("scroll stopped early", out.get("ok"))
    _pass("few scroll calls", os_input.call_count() <= 2, os_input.call_count())


def test_scroll_fails_at_cap():
    import os_input
    from scroll_actions import execute_scroll

    os_input.reset_calls()
    with patch("scroll_actions._target_visible", return_value=False):
        with patch("hover_actions.snapshot_a11y_elements", return_value=[]):
            out = execute_scroll({"to_find": "Missing", "max_steps": 3})
    _pass("scroll cap fail", not out.get("ok"))
    _pass("cap message", "never appeared" in (out.get("reason") or "").lower())
    _pass("three increments", os_input.call_count() == 3)


def test_scroll_hover_click_validates():
    from interaction_chain import compose_scroll_hover_click, validate_interaction_chain

    scroll = {"action": "scroll", "to_find": "email address", "within": "Apollo Side Panel"}
    hover = {"action": "hover", "point": [400, 300], "revealed": [{"name": "Copy", "control_type": "Button"}]}
    click = {"point": [410, 305], "anchor": {}}
    chain = compose_scroll_hover_click(scroll, hover, click)
    _pass("3-part chain", chain and len(chain.get("parts") or []) == 3)
    err = validate_interaction_chain(chain.get("parts") or [])
    _pass("chain validates", err is None)


def test_two_clicks_rejected():
    from interaction_chain import validate_interaction_chain

    bad = [
        {"action": "hover", "point": [1, 2]},
        {"action": "click"},
        {"action": "click"},
    ]
    err = validate_interaction_chain(bad)
    _pass("two clicks rejected", err and "state-changing" in err.lower())


def main():
    print("=" * 70)
    print("PART 4 scroll self-test")
    print("=" * 70)
    test_scroll_intent_no_pixel_delta()
    test_scroll_stops_when_visible()
    test_scroll_fails_at_cap()
    test_scroll_hover_click_validates()
    test_two_clicks_rejected()
    print("PART 4 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
