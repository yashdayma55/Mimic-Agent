"""
Harness runner: orchestrate existing engines per step.

Routes each HarnessStep to browser (DOM+selector), native (perceive+act),
or reason (bounded agent loop). Does NOT reimplement perception or action —
only decides kind, dispatches, approves, and records a transcript.
"""

import time

from harness_schema import HarnessStep, step_from_dict
from harness_router import decide_kind
from agent_loop import perceive
from agent_reason import reason_next_action
from agent_act import do_action

try:
    from browser_detect import is_browser_frontmost
except Exception:
    def is_browser_frontmost():
        return False

try:
    import prereq_reasoner
    prepare_for = prereq_reasoner.prepare_for
except Exception:
    prepare_for = None

# Reuse the exact approval / correction gate from agent_run (no behavior change)
from agent_run import (
    _approve_or_correct,
    _apply_clarification,
    _print_proposal,
    _history_line,
    _quiet_browser_teardown,
)
from safety_gate import require_irreversible_confirmation, harness_step_check


def _fill_inputs(text, inputs):
    """Replace {placeholder} in a string using the inputs dict."""
    if not text or not inputs:
        return text
    out = text
    for k, v in inputs.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _fill_action(action, inputs):
    """Deep-ish fill of {placeholders} inside common action string fields."""
    if not action or not inputs:
        return action
    out = dict(action)
    for key in ("text", "url", "match", "keys", "why"):
        if key in out and isinstance(out[key], str):
            out[key] = _fill_inputs(out[key], inputs)
    return out


def _as_step(step):
    """Accept HarnessStep or a dict; return HarnessStep."""
    if isinstance(step, HarnessStep):
        return step
    if isinstance(step, dict):
        return step_from_dict(step)
    raise TypeError(f"expected HarnessStep or dict, got {type(step)}")


def _run_reason_substep(subgoal, require_approval, max_steps, prefer_browser=False):
    """Bounded agent loop toward one sub-goal. Reuses perceive/reason/act."""
    history = []
    auto_approve = not require_approval

    for step_num in range(1, max_steps + 1):
        print(f"  [reason] sub-step {step_num}/{max_steps} toward: {subgoal!r}")
        elements, path, page_info = perceive(prefer_browser=prefer_browser)
        action = reason_next_action(subgoal, elements, path, history)
        _print_proposal(action)

        if action.get("action") == "done":
            print("  [reason] sub-goal reached.")
            return "done"
        if action.get("action") == "stuck":
            print(f"  [reason] stuck: {action.get('why')}")
            return "stuck"

        cstatus, action = _apply_clarification(
            subgoal, elements, path, history, action
        )
        if cstatus == "cancel":
            return "stopped"
        if cstatus == "skip":
            history.append("note: user skipped an ambiguous step")
            time.sleep(0.2)
            continue

        status, action = _approve_or_correct(
            subgoal, elements, path, history, action, auto_approve
        )
        if status == "stopped":
            return "stopped"
        if status == "done":
            return "done"
        if status == "stuck":
            return "stuck"

        title_hint = None
        target_procs = None
        if prefer_browser or (page_info and page_info.get("mode") == "browser"):
            target_procs = ["chrome.exe"]
        ok, msg = do_action(
            action, elements, target_procs=target_procs, title_hint=title_hint
        )
        print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        history.append(_history_line(action, ok, msg))

        if not ok:
            print("  [reason] auto-retry: re-perceive + different action...")
            history.append("note: previous action failed; try a DIFFERENT next action")
            elements, path, page_info = perceive(prefer_browser=prefer_browser)
            retry = reason_next_action(subgoal, elements, path, history)
            _print_proposal(retry, label="retry proposes")
            if retry.get("action") in ("done", "stuck"):
                return retry.get("action")
            cstatus, retry = _apply_clarification(
                subgoal, elements, path, history, retry
            )
            if cstatus == "cancel":
                return "stopped"
            if cstatus == "skip":
                history.append("note: user skipped an ambiguous retry step")
                time.sleep(0.2)
                continue
            status, retry = _approve_or_correct(
                subgoal, elements, path, history, retry, auto_approve
            )
            if status != "ok":
                return status
            if not require_irreversible_confirmation(
                harness_step_check(None, action=retry, description=subgoal)
            ):
                return "stopped_irreversible"
            ok2, msg2 = do_action(
                retry, elements, target_procs=target_procs, title_hint=title_hint
            )
            print(f"  {'OK' if ok2 else 'FAIL'}: {msg2}")
            history.append(_history_line(retry, ok2, msg2))
            if not ok2:
                print("  [reason] retry failed — stopping sub-goal.")
                return "failed"

        time.sleep(0.6)

    print(f"  [reason] reached {max_steps}-step ceiling for sub-goal.")
    return "ceiling"


def run_harness(steps, inputs=None, require_approval=True, max_reason_steps=6,
                start_index=0):
    """Run a list of HarnessStep (or dicts) through the router + engines.

    Returns a transcript: list of per-step result records.
    """
    inputs = inputs or {}
    transcript = []
    auto_approve = not require_approval
    steps = [_as_step(s) for s in steps]

    # 1. Prerequisites once from the whole workflow's intent
    goal_blob = " ; ".join(getattr(s, "description", "") or "" for s in steps)
    if prepare_for:
        try:
            prepare_for(goal=goal_blob or None)
        except Exception as e:
            print(f"  [harness] prereq warning: {e}")

    start_index = max(0, min(int(start_index or 0), len(steps)))
    if start_index > 0:
        print(f"[harness] starting at step {start_index + 1} "
              f"(skipping first {start_index})")

    try:
        for i in range(start_index, len(steps)):
            step = steps[i]
            step_num = i + 1
            try:
                step.validate()
            except AssertionError as e:
                print(f"--- step {step_num}/{len(steps)} INVALID: {e} ---")
                transcript.append({
                    "step": step_num, "kind": getattr(step, "kind", "?"),
                    "ok": False, "msg": f"invalid step: {e}",
                })
                break

            # Live context for routing (actual screen, not record-time guess)
            try:
                browser_fm = bool(is_browser_frontmost())
            except Exception:
                browser_fm = False
            ctx = {
                "browser_frontmost": browser_fm,
                "goal_hint": getattr(step, "description", "") or "",
            }
            kind = decide_kind(step, ctx)
            desc = _fill_inputs(getattr(step, "description", "") or "", inputs)
            print(f"--- step {step_num}/{len(steps)} [{kind}] {desc} ---")

            if kind == "reason":
                subgoal = _fill_inputs(step.goal or desc, inputs)
                # Prefer browser perception if Chrome is already frontmost
                prefer_browser = browser_fm
                outcome = _run_reason_substep(
                    subgoal, require_approval, max_reason_steps,
                    prefer_browser=prefer_browser,
                )
                transcript.append({
                    "step": step_num, "kind": kind, "goal": subgoal, "outcome": outcome,
                })
                if outcome in ("stopped", "stuck", "failed", "stopped_irreversible"):
                    print(f"  [harness] stopping after reason outcome={outcome}")
                    break
                continue

            # Concrete browser / native step
            prefer_browser = (kind == "browser")
            elements, img, page_info = perceive(prefer_browser=prefer_browser)
            action = dict(step.action) if step.action else None
            action = _fill_action(action, inputs)

            # If only a target hint is given, ask the reasoner to pick the action
            if action is None:
                hint = desc
                if step.target_name:
                    hint = (
                        f"{desc} — target element named "
                        f"'{step.target_name}'"
                        + (f" ({step.target_type})" if step.target_type else "")
                    )
                action = reason_next_action(hint, elements, img)
                _print_proposal(action, label="reasoned")
            else:
                _print_proposal(action, label="planned")

            if action.get("action") == "stuck":
                print(f"  stuck: {action.get('why')}")
                transcript.append({
                    "step": step_num, "kind": kind, "action": action,
                    "ok": False, "msg": action.get("why"),
                })
                break

            cstatus, action = _apply_clarification(
                desc, elements, img, [], action
            )
            if cstatus == "cancel":
                transcript.append({
                    "step": step_num, "kind": kind, "action": action,
                    "ok": False, "msg": "cancelled at clarification",
                })
                break
            if cstatus == "skip":
                transcript.append({
                    "step": step_num, "kind": kind, "action": action,
                    "ok": True, "msg": "skipped at clarification",
                })
                continue

            if require_approval:
                status, action = _approve_or_correct(
                    desc, elements, img, [], action, auto_approve
                )
                if status == "stopped":
                    transcript.append({
                        "step": step_num, "kind": kind, "action": action,
                        "ok": False, "msg": "stopped by human",
                    })
                    break
                if status in ("done", "stuck"):
                    transcript.append({
                        "step": step_num, "kind": kind, "action": action,
                        "ok": status == "done", "msg": status,
                    })
                    if status == "stuck":
                        break
                    continue

            if not require_irreversible_confirmation(
                harness_step_check(step, action=action, description=desc)
            ):
                transcript.append({
                    "step": step_num, "kind": kind, "action": action,
                    "ok": False, "msg": "stopped at irreversible step",
                })
                break

            target_procs = ["chrome.exe"] if prefer_browser else None
            ok, msg = do_action(action, elements, target_procs=target_procs)
            print(f"  {'OK' if ok else 'FAIL'}: {msg}")

            if not ok:
                # One auto-retry: re-perceive + reason a different action
                print("  [harness] auto-retry: re-perceive + re-reason...")
                elements, img, page_info = perceive(prefer_browser=prefer_browser)
                retry = reason_next_action(
                    desc + " (previous action failed; try a DIFFERENT next action)",
                    elements, img,
                    [_history_line(action, False, msg)],
                )
                _print_proposal(retry, label="retry proposes")
                cstatus, retry = _apply_clarification(
                    desc, elements, img, [], retry
                )
                if cstatus == "cancel":
                    transcript.append({
                        "step": step_num, "kind": kind, "action": retry,
                        "ok": False, "msg": "cancelled at clarification",
                    })
                    break
                if cstatus == "skip":
                    transcript.append({
                        "step": step_num, "kind": kind, "action": retry,
                        "ok": True, "msg": "skipped retry at clarification",
                    })
                    continue
                if require_approval:
                    status, retry = _approve_or_correct(
                        desc, elements, img, [], retry, auto_approve
                    )
                    if status != "ok":
                        transcript.append({
                            "step": step_num, "kind": kind, "action": retry,
                            "ok": False, "msg": f"retry {status}",
                        })
                        break
                if not require_irreversible_confirmation(
                    harness_step_check(step, action=retry, description=desc)
                ):
                    transcript.append({
                        "step": step_num, "kind": kind, "action": retry,
                        "ok": False, "msg": "stopped at irreversible step",
                    })
                    break
                ok2, msg2 = do_action(retry, elements, target_procs=target_procs)
                print(f"  {'OK' if ok2 else 'FAIL'}: {msg2}")
                transcript.append({
                    "step": step_num, "kind": kind, "action": retry,
                    "ok": ok2, "msg": msg2, "retried": True,
                })
                if not ok2:
                    print("  [harness] retry failed — stopping.")
                    break
            else:
                transcript.append({
                    "step": step_num, "kind": kind, "action": action,
                    "ok": ok, "msg": msg,
                })

            time.sleep(0.6)

    finally:
        _quiet_browser_teardown()

    print(f"\n[harness] finished — {len(transcript)} step record(s)")
    return transcript


if __name__ == "__main__":
    # Tiny dry-run of routing + schema only (no live act unless you approve)
    print("=== harness.py smoke: build a 2-step workflow ===")
    demo = [
        HarnessStep(
            kind="browser",
            description="navigate to google.com",
            action={"action": "navigate", "url": "https://www.google.com",
                    "why": "open google"},
        ),
        HarnessStep(
            kind="reason",
            description="search for python",
            goal="search google for python",
        ),
    ]
    for s in demo:
        s.validate()
        print(f"  [{s.kind}] {s.description}")
    print("\nTo run live: run_harness(demo, require_approval=True)")
    print("ok")
