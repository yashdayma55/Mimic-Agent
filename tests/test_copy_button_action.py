"""Copy-button steps should resolve to click, not abstract copy."""

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


def test_resolve_click_copy_button():
    from teach_loop import add_step, resolve_action, set_context, _closed_verb, _user_success_text
    from teaching import TaughtWorkflow

    wf = TaughtWorkflow(name="_copy_button_action")
    set_context(wf, "test")
    step = add_step(
        wf,
        "in this step you have to click on the copy button to copy the visible email",
    )
    act = resolve_action(step)
    _pass("action is click", act and act.get("action") == "click", act)
    _pass("closed verb click", _closed_verb(step.user_description) == "click")
    _pass(
        "partly answer normalized",
        _user_success_text(
            "partly: copy the visible email address from the Apollo sidebar"
        )
        == "copy the visible email address from the Apollo sidebar",
    )


def test_ui_runner_copy_delegates_to_click():
    from ui_runner import execute_step

    out = execute_step(
        {
            "id": "s1",
            "action": "copy",
            "elem_name": "Copy",
            "elem_type": "Button",
            "window_title": "LinkedIn",
            "anchor": {"point": [100, 200]},
        }
    )
    _pass("copy uses taught point", out.click_xy == (100, 200), out.reason)


def main():
    print("=" * 70)
    print("copy button action self-test")
    print("=" * 70)
    test_resolve_click_copy_button()
    test_ui_runner_copy_delegates_to_click()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
