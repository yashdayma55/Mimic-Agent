"""
Non-interactive entry point to run a plan programmatically.
Reuses the Phase 4 locator/action logic.

Phase 7 robustness fix: when a step FAILS (element not found), the engine does
NOT blindly continue - that is what caused a single failure to cascade and break
every following step. Instead it PAUSES and asks the human what to do:
   retry / skip / stop / correct
so a failure is contained instead of poisoning the rest of the run.

IMPORTANT for MCP: under MCP stdio, stdout is the protocol channel, so logging
goes to stderr and interactive prompts are not used (pass on_fail='skip').

  run_plan(steps, require_approval=True, on_fail='ask') -> (ran, skipped)
"""

import sys
from pywinauto import Desktop
from pywinauto.keyboard import send_keys
from locator import locate
from safety_gate import require_irreversible_confirmation


_last_window_title = {"title": ""}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def _refocus(title):
    if not title:
        return
    try:
        Desktop(backend="uia").window(title=title).set_focus()
    except Exception:
        pass


def _do_step(step):
    """Perform one step. Returns 'done' or 'failed'."""
    if step.get("_skip"):
        return "done"          # deliberately skipped, not a failure

    action = step.get("action")

    if action == "type":
        text = step.get("text", "")
        if text.startswith("[SECRET"):
            log(f"   (skipping secret field {text})")
            return "done"
        _refocus(_last_window_title["title"])
        send_keys(text, with_spaces=True)
        log(f'   typed "{text}"')
        return "done"

    # click
    got = locate(step)
    if got[0] == "BROWSER":
        got[2]["element"].click()
        log(f"   clicked '{step.get('elem_name')}' (browser)")
        return "done"
    elif got[0] == "VISION":
        res = got[2]
        if not res.get("found"):
            log(f"   vision could not confirm '{step.get('elem_name','?')}'")
            return "failed"
        import pyautogui
        pyautogui.click(res["x"], res["y"])
        log(f"   clicked at ({res['x']},{res['y']}) (vision)")
        return "done"
    elif got[0]:
        el, tier = got[0], got[1]
        try:
            el.set_focus()
        except Exception:
            pass
        el.click_input()
        log(f"   clicked '{step.get('elem_name','?')}' (tier {tier})")
        try:
            _last_window_title["title"] = el.top_level_parent().window_text()
        except Exception:
            pass
        return "done"
    else:
        log(f"   could not find '{step.get('elem_name','?')}'")
        return "failed"


def _handle_failure(step, on_fail):
    """A step failed. Decide what to do. Returns one of:
    'retry', 'skip', 'stop'. In 'ask' mode, prompt the human."""
    instr = step.get("instruction", step.get("action", "step"))

    if on_fail == "skip":
        return "skip"          # non-interactive: just skip and keep going
    if on_fail == "stop":
        return "stop"

    # on_fail == 'ask' : pause and ask the human, so a failure doesn't cascade
    log(f"\n   !!! step FAILED: {instr}")
    print(f"\n   step failed: {instr}", flush=True)
    ans = input("   what now? (retry / skip / stop): ").strip().lower()
    if ans.startswith("r"):
        return "retry"
    if ans.startswith("st"):
        return "stop"
    return "skip"


def run_plan(steps, require_approval=True, on_fail="ask", start_index=0):
    """Run steps. Returns (ran, skipped).
    on_fail controls what happens when a step can't find its element:
      'ask'  -> pause and ask the human (default, interactive)
      'skip' -> skip the failed step and continue (non-interactive/MCP)
      'stop' -> stop the whole run at the first failure
    The key robustness idea: a failure is handled explicitly, not ignored,
    so it cannot silently cascade into every following step."""
    ran, skipped = [], []
    start_index = max(0, min(int(start_index or 0), len(steps)))
    if start_index > 0:
        log(f"[plan] starting at step {start_index + 1} (skipping first {start_index})")
    i = start_index
    while i < len(steps):
        step = steps[i]
        instr = step.get("instruction", step.get("action", "step"))
        log(f"\n[step {i+1}/{len(steps)}] {instr}")

        if require_approval:
            ans = input(f"   approve this step? (y/n/stop): ").strip().lower()
            if ans == "stop":
                log("   stopping the run.")
                break
            if ans != "y":
                log("   skipped (not approved)")
                skipped.append(instr)
                i += 1
                continue

        if not require_irreversible_confirmation(step):
            log("   stopping at irreversible step.")
            break

        outcome = _do_step(step)

        if outcome == "done":
            ran.append(instr)
            i += 1
        else:
            # a failure - handle it, do NOT just march on
            decision = _handle_failure(step, on_fail)
            if decision == "retry":
                log("   retrying...")
                continue            # same i, try the step again
            elif decision == "stop":
                log("   stopping the run so the failure doesn't cascade.")
                skipped.append(instr)
                break
            else:  # skip
                log("   skipping this step (you chose to continue).")
                skipped.append(instr)
                i += 1

    return ran, skipped


if __name__ == "__main__":
    from workflow_runner import load_workflow
    steps = load_workflow("notepad_greeting")
    if steps:
        log("running notepad_greeting (open Notepad first)...")
        ran, skipped = run_plan(steps, require_approval=True, on_fail="ask")
        log(f"\ndone: {ran}\nskipped: {skipped}")
    else:
        log("no seed workflow found")