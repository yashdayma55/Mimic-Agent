"""
A non-interactive entry point to run a plan programmatically.
Reuses the Phase 4 locator and action logic, but instead of an interactive
LangGraph loop with terminal approval, it exposes a plain function that runs
a list of steps and returns (ran, skipped).

This is what the MCP server / workflow_runner calls. It keeps the interactive
replay_engine.py separate (that one is for a human at a terminal).

Approval: when require_approval is True, it asks on the terminal per step
(simple y/n). An MCP server running unattended would pass require_approval=False
only for user-trusted workflows.
"""

from pywinauto import Desktop
from pywinauto.keyboard import send_keys
from locator import locate


_last_window_title = {"title": ""}


def _refocus(title):
    if not title:
        return
    try:
        Desktop(backend="uia").window(title=title).set_focus()
    except Exception:
        pass


def _do_step(step):
    """Perform one step. Returns 'done' or 'skipped'."""
    if step.get("_skip"):
        return "skipped"

    action = step.get("action")

    if action == "type":
        text = step.get("text", "")
        if text.startswith("[SECRET"):
            print(f"   (skipping secret field {text})")
            return "skipped"
        _refocus(_last_window_title["title"])
        send_keys(text, with_spaces=True)
        print(f'   typed "{text}"')
        return "done"

    # click
    got = locate(step)
    if got[0] == "BROWSER":
        got[2]["element"].click()
        print(f"   clicked '{step.get('elem_name')}' (browser)")
        return "done"
    elif got[0] == "VISION":
        import pyautogui
        res = got[2]
        pyautogui.click(res["x"], res["y"])
        print(f"   clicked at ({res['x']},{res['y']}) (vision)")
        return "done"
    elif got[0]:
        el, tier = got[0], got[1]
        try:
            el.set_focus()
        except Exception:
            pass
        el.click_input()
        print(f"   clicked '{step.get('elem_name','?')}' (tier {tier})")
        try:
            _last_window_title["title"] = el.top_level_parent().window_text()
        except Exception:
            pass
        return "done"
    else:
        print(f"   could not find '{step.get('elem_name','?')}'")
        return "skipped"


def run_plan(steps, require_approval=True):
    """Run a list of steps. Returns (ran, skipped) lists of instructions."""
    ran, skipped = [], []
    for step in steps:
        instr = step.get("instruction", step.get("action", "step"))
        print(f"\n[step] {instr}")

        if require_approval:
            ans = input(f"   approve this step? (y/n): ").strip().lower()
            if ans != "y":
                print("   skipped (not approved)")
                skipped.append(instr)
                continue

        outcome = _do_step(step)
        (ran if outcome == "done" else skipped).append(instr)

    return ran, skipped


if __name__ == "__main__":
    # quick self-test on the seed workflow
    from workflow_runner import load_workflow
    steps = load_workflow("notepad_greeting")
    if steps:
        print("running notepad_greeting (open Notepad first)...")
        ran, skipped = run_plan(steps, require_approval=True)
        print(f"\ndone: {ran}\nskipped: {skipped}")
    else:
        print("no seed workflow found")