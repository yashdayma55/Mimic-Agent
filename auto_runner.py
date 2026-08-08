"""
Auto-run a trained workflow with hint-guided re-reasoning (Option C).

For each step: perceive live screen, take the next saved hint, and if the
hint still matches the screen, perform it without asking; otherwise
re-reason that step live. Pause hotkey (Ctrl+Alt+P) or a PAUSE sentinel file
lets the user resume / edit goal / stop.
"""

import os
import time

from agent_loop import perceive
from agent_reason import reason_next_action
from agent_act import do_action
from agent_run import (
    _history_line,
    _print_proposal,
    _ensure_target_focus,
    _quiet_browser_teardown,
    _append_run_log,
)
from trained_workflows import load_trained

try:
    from config import MAX_STEPS
except Exception:
    MAX_STEPS = 8

try:
    from prereq_reasoner import prepare_for, CAPABILITIES
except Exception:
    prepare_for = None
    CAPABILITIES = {}

PAUSE_SENTINEL = "PAUSE"

# Shared pause flag — set by hotkey listener or sentinel file
_paused = False
_stop_requested = False
_listener = None


def _set_paused():
    global _paused
    _paused = True
    print("\n  [pause] hotkey received — will pause at next step boundary")


def _start_pause_listener():
    """Background Ctrl+Alt+P listener via pynput; never crashes the run."""
    global _listener
    try:
        from pynput import keyboard

        hotkeys = keyboard.GlobalHotKeys({
            "<ctrl>+<alt>+p": _set_paused,
        })
        hotkeys.start()
        _listener = hotkeys
        print("  [pause] Ctrl+Alt+P armed (or create a file named PAUSE)")
        return True
    except Exception as e:
        print(f"  [pause] hotkey unavailable ({e}); create a file named PAUSE to pause")
        _listener = None
        return False


def _stop_pause_listener():
    global _listener
    if _listener is not None:
        try:
            _listener.stop()
        except Exception:
            pass
        _listener = None


def _sentinel_pause_requested():
    """True if a PAUSE file exists in the working directory."""
    try:
        return os.path.isfile(PAUSE_SENTINEL)
    except Exception:
        return False


def _clear_sentinel():
    try:
        if os.path.isfile(PAUSE_SENTINEL):
            os.remove(PAUSE_SENTINEL)
    except Exception:
        pass


def _handle_pause_menu(goal):
    """Blocking pause menu. Returns (goal, should_stop)."""
    global _paused, _stop_requested
    _clear_sentinel()
    print("\n=== PAUSED ===")
    print("  [r]esume")
    print("  [e]dit goal / looping prompt")
    print("  [s]top")
    while True:
        try:
            choice = input("pause> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            _paused = False
            _stop_requested = True
            return goal, True
        if choice in ("r", "resume"):
            _paused = False
            print("  resuming...")
            return goal, False
        if choice in ("s", "stop"):
            _paused = False
            _stop_requested = True
            print("  stop requested.")
            return goal, True
        if choice in ("e", "edit"):
            try:
                new_goal = input("  new goal: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                continue
            if new_goal:
                goal = new_goal
                print(f"  goal updated -> {goal!r}")
            else:
                print("  (empty — goal unchanged)")
            continue
        print("  type r, e, or s")


def _fuzzy_elem_match(sig, elements):
    """Find an element whose name+control_type fuzzy-contains the hint signature.
    Returns the element dict, or None."""
    if not sig:
        return None
    name = (sig.get("name") or "").strip().lower()
    ctype = (sig.get("control_type") or "").strip().lower()
    if not name and not ctype:
        return None
    for el in elements:
        en = (el.get("name") or "").strip().lower()
        et = (el.get("control_type") or "").strip().lower()
        type_ok = (not ctype) or (ctype == et)
        if not type_ok:
            continue
        if not name:
            return el
        # fuzzy contains either way
        if name in en or en in name:
            return el
    return None


def _hint_applies(hint, elements):
    """Does this hint still match the live screen?

    Returns:
      (True, matched_el_or_None)  — perform the hint action
      (False, None)               — re-reason instead
    """
    action = hint.get("action") or {}
    kind = action.get("action")
    sig = hint.get("signature") or {}

    # Actions that don't need a screen element — apply directly from the hint
    if kind in ("type", "press", "scroll", "navigate", "wait", "hotkey",
                "copy", "paste", "switch_tab"):
        # If a signature was recorded (unusual), still require a match when present
        name = (sig.get("name") or "").strip()
        ctype = (sig.get("control_type") or "").strip()
        if name or ctype:
            matched = _fuzzy_elem_match(sig, elements)
            return (matched is not None, matched)
        return True, None

    if kind == "click":
        matched = _fuzzy_elem_match(sig, elements)
        if matched is None:
            return False, None
        return True, matched

    # done/stuck/unknown in a saved hint — treat as no match, re-reason
    return False, None


def _action_from_hint(hint, matched_el):
    """Build the live action dict from a saved hint (+ optional remapped id)."""
    action = dict(hint.get("action") or {})
    if matched_el is not None and action.get("action") == "click":
        action["id"] = matched_el["id"]
    return action


def run_trained(name, max_steps=None, skip_prereqs=False):
    """Auto-run a trained workflow by name. No per-step approval.

    Follows saved hints when the live screen still matches; otherwise
    re-reasons that step. Honors pause (Ctrl+Alt+P / PAUSE file).
    """
    global _paused, _stop_requested
    _paused = False
    _stop_requested = False

    wf = load_trained(name)
    if wf is None:
        print(f"  no trained workflow named '{name}'")
        return "not_found"

    goal = wf.get("goal") or ""
    trace = wf.get("trace") or []
    if max_steps is None:
        # Allow a bit of headroom past the trained length for re-reasons
        max_steps = max(MAX_STEPS, len(trace) + 4)

    print(f"\n=== AUTO RUN trained: {wf.get('name', name)} ===")
    print(f"  goal: {goal}")
    print(f"  hints: {len(trace)} | max steps: {max_steps}")
    print("  Press Ctrl+Alt+P (or create a PAUSE file) to pause/edit/stop.\n")

    _start_pause_listener()

    try:
        return _run_trained_body(goal, trace, max_steps, skip_prereqs, name)
    finally:
        _stop_pause_listener()
        _clear_sentinel()
        _quiet_browser_teardown()


def _run_trained_body(goal, trace, max_steps, skip_prereqs, run_name):
    global _paused, _stop_requested

    target_procs = None
    if prepare_for and not skip_prereqs:
        results = prepare_for(goal=goal)
        for cap, ready in results:
            if not ready:
                print(f"  WARNING: could not ensure '{cap}' is ready.")
                ans = input("  continue anyway? (y/n): ").strip().lower()
                if ans != "y":
                    _append_run_log(goal, "prereq_failed", 0)
                    return "prereq_failed"
        try:
            for cap, ready in results:
                if ready and CAPABILITIES.get(cap, {}).get("procs"):
                    target_procs = CAPABILITIES[cap]["procs"]
                    break
        except Exception:
            pass

    history = []
    hint_idx = 0
    steps_taken = 0
    consecutive_fails = 0
    hints_exhausted = False

    for step_num in range(1, max_steps + 1):
        steps_taken = step_num

        # Pause gate (hotkey or PAUSE file)
        if _paused or _sentinel_pause_requested():
            _paused = True
            goal, should_stop = _handle_pause_menu(goal)
            if should_stop or _stop_requested:
                print("  stopped by human (pause menu).")
                _append_run_log(goal, "stopped", steps_taken)
                return "stopped"

        print(f"--- auto step {step_num}/{max_steps} "
              f"(hint {min(hint_idx + 1, len(trace))}/{len(trace)}) ---")

        elements, path, page_info = perceive()

        # After all hints consumed: re-reason; stop when model says done
        if hint_idx >= len(trace):
            hints_exhausted = True
            print("  [auto] all hints used — asking model if goal is done...")
            action = reason_next_action(goal, elements, path, history)
            _print_proposal(action, label="post-hint proposes")
            if action.get("action") == "done":
                print("\n  the agent believes the GOAL IS REACHED.")
                _append_run_log(goal, "done", steps_taken)
                return "done"
            if action.get("action") == "stuck":
                print(f"\n  the agent is STUCK: {action.get('why')}. stopping.")
                _append_run_log(goal, "stuck", steps_taken)
                return "stuck"
            # Continue with re-reasoned action (no more hints)
            source = "re-reason (hints exhausted)"
        else:
            hint = trace[hint_idx]
            applies, matched = _hint_applies(hint, elements)
            if applies:
                action = _action_from_hint(hint, matched)
                source = "hint match"
                print(f"  [auto] HINT MATCH — performing "
                      f"{action.get('action')} without asking")
                _print_proposal(action, label="hint")
            else:
                print("  [auto] HINT MISS — screen changed; re-reasoning this step...")
                action = reason_next_action(goal, elements, path, history)
                source = "re-reason (hint miss)"
                _print_proposal(action)

        if action.get("action") == "done":
            print("\n  the agent believes the GOAL IS REACHED.")
            _append_run_log(goal, "done", steps_taken)
            return "done"
        if action.get("action") == "stuck":
            print(f"\n  the agent is STUCK: {action.get('why')}. stopping.")
            _append_run_log(goal, "stuck", steps_taken)
            return "stuck"

        title_hint = _ensure_target_focus(goal, target_procs, page_info)
        ok, msg = do_action(
            action, elements, target_procs=target_procs, title_hint=title_hint
        )
        print(f"  {'OK' if ok else 'FAIL'} ({source}): {msg}")
        history.append(_history_line(action, ok, msg))

        if ok:
            consecutive_fails = 0
            # Advance hint pointer only when we consumed a hint slot this step
            if not hints_exhausted and hint_idx < len(trace):
                # Advance whether match or re-reason — one hint per step in order
                hint_idx += 1
        else:
            consecutive_fails += 1
            print(f"  [auto] failure {consecutive_fails}/2 in a row")
            if consecutive_fails >= 2:
                print("  stopping: action failed twice in a row.")
                _append_run_log(goal, "failed", steps_taken)
                return "failed"
            # One automatic re-perceive + re-reason retry (counts toward the 2)
            print("  [auto] retrying with re-perceive + re-reason...")
            history.append("note: previous action failed; try a DIFFERENT next action")
            elements, path, page_info = perceive()
            retry = reason_next_action(goal, elements, path, history)
            _print_proposal(retry, label="retry proposes")
            if retry.get("action") == "done":
                print("\n  the agent believes the GOAL IS REACHED.")
                _append_run_log(goal, "done", steps_taken)
                return "done"
            if retry.get("action") == "stuck":
                print(f"\n  the agent is STUCK: {retry.get('why')}. stopping.")
                _append_run_log(goal, "stuck", steps_taken)
                return "stuck"
            title_hint = _ensure_target_focus(goal, target_procs, page_info)
            ok2, msg2 = do_action(
                retry, elements, target_procs=target_procs, title_hint=title_hint
            )
            print(f"  {'OK' if ok2 else 'FAIL'} (retry): {msg2}")
            history.append(_history_line(retry, ok2, msg2))
            if ok2:
                consecutive_fails = 0
                if not hints_exhausted and hint_idx < len(trace):
                    hint_idx += 1
            else:
                consecutive_fails += 1
                print("  stopping: action failed twice in a row.")
                _append_run_log(goal, "failed", steps_taken)
                return "failed"

        time.sleep(1.0)

    print(f"\n  reached the {max_steps}-step ceiling without finishing. stopping safely.")
    _append_run_log(goal, "ceiling", steps_taken)
    return "ceiling"


if __name__ == "__main__":
    import sys
    n = sys.argv[1] if len(sys.argv) > 1 else ""
    if not n:
        from trained_workflows import list_trained
        print("usage: python auto_runner.py <trained_name>")
        print("trained:", list_trained())
    else:
        print("=== ended:", run_trained(n), "===")
