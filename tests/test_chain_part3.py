"""PART 3: chain execution — verify once at end, per-click evidence, failure by index."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _ok(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label)


def _kill_notepad():
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True, text=True)
    time.sleep(0.6)


def _open_notepad():
    subprocess.Popen(["notepad.exe"])
    from ui_runner import find_window

    for _ in range(25):
        win, title = find_window("Notepad")
        if win is not None:
            return title
        time.sleep(0.2)
    return None


def _chain_step(anchors, broken_second: bool = False):
    if broken_second:
        anchors = [anchors[0], {
            "primary": {"name": "ZZZ_MISSING_MENU", "control_type": "MenuItem"},
            "crop_path": "anchors/missing.png",
        }]
    return {
        "kind": "native",
        "action": "chain",
        "window_title": "Notepad",
        "clicks": [
            {"action": "click", "elem_name": "Text editor", "elem_type": "Document", "window_title": "Notepad"},
            {"action": "click", "elem_name": "View", "elem_type": "MenuItem", "window_title": "Notepad"},
        ],
        "anchors": anchors,
        "click_count": 2,
    }


def test_chain_success():
    import os_input
    from ui_runner import run_verified_plan

    os_input.reset_calls()
    _kill_notepad()
    title = _open_notepad()
    assert title, "Notepad did not open"
    anchors = [
        {"primary": {"name": "Text editor", "control_type": "Document"}},
        {"primary": {"name": "View", "control_type": "MenuItem"}},
    ]
    before = os_input.call_count()
    out = run_verified_plan([_chain_step(anchors)])
  # verify once at end
    _ok("chain run ok", out.get("ok") is True, out.get("reason"))
    lines = []
    for r in out.get("results") or []:
        lines.extend(r.log_lines())
    evidence_lines = [ln for ln in lines if "chain click" in ln]
    _ok("per-click evidence for both", len(evidence_lines) >= 2, evidence_lines)
    _ok("os input used", os_input.call_count() > before)
    print("  evidence:", evidence_lines)


def test_chain_failure_names_index():
    import os_input
    from ui_runner import run_verified_plan

    os_input.reset_calls()
    _kill_notepad()
    title = _open_notepad()
    assert title, "Notepad did not open"
    anchors = [
        {"primary": {"name": "Text editor", "control_type": "Document"}},
        {"primary": {"name": "View", "control_type": "MenuItem"}},
    ]
    out = run_verified_plan([_chain_step(anchors, broken_second=True)])
    reason = out.get("reason") or ""
    _ok("chain failed", out.get("ok") is False, reason)
    _ok("failure names click 2 of 2", "click 2 of 2" in reason.lower(), reason)
    print("  reason:", reason)


def test_irreversible_first_rejected():
    import os_input
    from plan_schema import Plan, PlanNode
    from plan_validator import validate_plan
    from teach_compile import demo_taught_step
    from teach_loop import add_step, explain_understanding, set_context
    from teaching import TaughtWorkflow, load_taught

    name = "_chain_irrev"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "send")
    step = add_step(wf, "click Send then click OK")
    step.click_count = 2
    step.anchors = [
        {"primary": {"name": "Send", "control_type": "Button"}, "agreement": "single"},
        {"primary": {"name": "OK", "control_type": "Button"}, "agreement": "single"},
    ]
    step.action = {
        "action": "chain",
        "click_count": 2,
        "clicks": [
            {"action": "click", "elem_name": "Send"},
            {"action": "click", "elem_name": "OK"},
        ],
    }
    from teaching import save_taught

    save_taught(wf)
    node = PlanNode(
        id="s1",
        action="chain",
        extra={
            "clicks": step.action["clicks"],
            "anchors": step.anchors,
            "click_count": 2,
        },
    )
    viol = validate_plan(Plan(nodes=[node]))
    _ok("validation rejects irreversible first", any("irreversible" in v["message"].lower() for v in viol), viol)
    os_input.reset_calls()
    before = os_input.call_count()
    wf = load_taught(name)
    step = wf.steps[0]
    step.status = "understood"
    step.understanding = explain_understanding(wf, step.id)
    from teach_compile import step_to_node

    runner = step_to_node(step).to_runner_step()
    from ui_runner import run_verified_plan

    out = run_verified_plan([runner])
    _ok("irreversible chain not executed", os_input.call_count() == before, os_input.call_count())


def test_chain_long_user_description_validates():
    from plan_schema import Plan
    from plan_validator import validate_plan
    from teach_compile import step_to_node
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow

    wf = TaughtWorkflow(name="_chain_long_desc")
    set_context(wf, "linkedin")
    long_desc = (
        "In this step you will click on the extensions tab as a first click and then "
        "second click on the apollo icon (The one with yellow coloured )"
    )
    step = add_step(wf, long_desc)
    step.click_count = 2
    step.anchors = [
        {"primary": {"name": "the Extensions toolbar button (puzzle-piece icon)", "control_type": "Button"}},
        {"primary": {"name": "Apollo.io: Free B2B Phone Number & Email Finder", "control_type": "Button"}},
    ]
    step.action = {
        "action": "chain",
        "click_count": 2,
        "clicks": [
            {
                "action": "click",
                "elem_name": "the Extensions toolbar button (puzzle-piece icon)",
                "elem_type": "Button",
                "target_desc": "the Extensions toolbar button (puzzle-piece icon)",
            },
            {
                "action": "click",
                "elem_name": "Apollo.io: Free B2B Phone Number & Email Finder",
                "elem_type": "Button",
                "target_desc": "Apollo.io: Free B2B Phone Number & Email Finder",
            },
        ],
    }
    node = step_to_node(step)
    viol = validate_plan(Plan(nodes=[node], source="demo"))
    _ok("long user_description does not block chain demo", not viol, viol)
    _ok("chain target_desc within limit", len(node.target_desc or "") <= 80, node.target_desc)


def test_chain_crop_xy_clicks():
    import os_input
    from chain_exec import execute_chain_step
    from unittest.mock import patch

    os_input.reset_calls()
    anchors = [
        {"point": [100, 200], "crop_path": "anchors/a1.png"},
        {"point": [300, 400], "crop_path": "anchors/a2.png"},
    ]
    step = {
        "id": "s1",
        "action": "chain",
        "window_title": "Oliane Piana | LinkedIn - Google Chrome",
        "clicks": [
            {"action": "click", "elem_name": "Extensions"},
            {"action": "click", "elem_name": "Apollo"},
        ],
        "anchors": anchors,
        "click_count": 2,
    }
    with patch("chain_exec._focus_target_window", return_value=(object(), "Oliane Piana | LinkedIn - Google Chrome")):
        with patch("anchor_repair.resolve_with_anchor", side_effect=[((150, 160), "crop_xy"), ((310, 420), "crop_xy")]):
            with patch("ui_runner.foreground_title", side_effect=["Oliane Piana | LinkedIn - Google Chrome", "Extensions", "Oliane Piana | LinkedIn - Google Chrome"]):
                with patch("ui_runner.focused_wrapper", return_value=None):
                    out = execute_chain_step(step)
    _ok("crop_xy chain ok", "chain completed" in (out.reason or "").lower(), out.reason)
    _ok("crop_xy chain clicked twice", os_input.call_count() == 2, os_input.call_count())


def main():
    print("=" * 70)
    print("PART 3 chain execution self-test")
    print("=" * 70)
    try:
        test_chain_success()
        print()
        test_chain_failure_names_index()
        print()
        test_irreversible_first_rejected()
        print()
        test_chain_long_user_description_validates()
        print()
        test_chain_crop_xy_clicks()
        print()
        print("PART 3 ALL CHECKS PASSED")
    finally:
        _kill_notepad()


if __name__ == "__main__":
    main()
