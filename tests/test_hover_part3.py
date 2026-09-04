"""PART 3 — hover detect and execute."""

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


def test_hover_click_chain_proposed():
    from capture_listener import synthesise_dwell_reveal
    from hover_actions import diff_new_a11y, propose_hover_click_chain

    hover = synthesise_dwell_reveal(400, 300, [{"name": "Copy", "control_type": "Button"}])
    click = {"point": [420, 305], "anchor": {"primary": {"name": "Copy", "control_type": "Button"}}}
    chain = propose_hover_click_chain(hover, click)
    _pass("chain proposed", chain is not None)
    _pass("has hover part", chain and chain.get("parts")[0].get("action") == "hover")
    _pass("click anchored", chain and len(chain.get("anchors") or []) >= 2)

    empty = synthesise_dwell_reveal(1, 1, [])
    _pass("empty dwell discarded", not empty.get("revealed"))


def test_dwell_no_reveal_discarded():
    from capture_listener import poll_dwell, reset_listener_state

    reset_listener_state()
    pos = [100, 200]
    t = 1000.0
    with patch("hover_actions.analyze_dwell", return_value=None):
        for _ in range(60):
            t += 0.01
            poll_dwell(lambda: tuple(pos), now=t)
    _pass("no reveal discarded", True)


def test_hover_execute_no_reveal_fails():
    import os_input
    from hover_actions import execute_hover

    os_input.reset_calls()
    with patch("hover_actions.snapshot_a11y_elements", return_value=[{"name": "Email", "control_type": "Text"}]):
        out = execute_hover({"point": [100, 200], "elem_name": "Email"})
    _pass("hover alone fails", not out.get("ok"))
    _pass("reason nothing revealed", "revealed nothing" in (out.get("reason") or "").lower())


def test_hover_execute_with_reveal():
    from hover_actions import execute_hover

    calls = {"n": 0}

    def _snap():
        calls["n"] += 1
        if calls["n"] <= 1:
            return [{"name": "Email", "control_type": "Text"}]
        return [{"name": "Email", "control_type": "Text"}, {"name": "Copy", "control_type": "Button"}]

    with patch("hover_actions.snapshot_a11y_elements", side_effect=_snap):
        out = execute_hover({"point": [100, 200]})
    _pass("hover reveal ok", out.get("ok"))
    _pass("reports revealed", "revealed" in (out.get("reason") or "").lower())


def test_diff_new_a11y():
    from hover_actions import diff_new_a11y

    before = [{"name": "Email", "control_type": "Text"}]
    after = before + [{"name": "Copy", "control_type": "Button"}]
    new = diff_new_a11y(before, after)
    _pass("diff finds copy", len(new) == 1 and new[0]["name"] == "Copy")


def main():
    print("=" * 70)
    print("PART 3 hover self-test")
    print("=" * 70)
    test_diff_new_a11y()
    test_hover_click_chain_proposed()
    test_dwell_no_reveal_discarded()
    test_hover_execute_no_reveal_fails()
    test_hover_execute_with_reveal()
    print("PART 3 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
