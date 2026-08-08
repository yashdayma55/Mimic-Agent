"""
Stage B + C: the full goal-driven loop, with prerequisite setup first.

  ensure prerequisites (Stage C) -> then the loop:
  perceive -> reason -> APPROVE -> act -> observe -> check goal -> repeat

Safety: human approves every action, hard step ceiling, clean stops.
On a failed action: one automatic re-perceive + different action retry, then
ask the human if that also fails.
"""

import sys
import time
from datetime import datetime
from agent_loop import perceive
from agent_reason import reason_next_action
from agent_act import do_action

try:
    from config import MAX_STEPS, REQUIRE_APPROVAL
except Exception:
    MAX_STEPS = 8
    REQUIRE_APPROVAL = True

try:
    from prereq_reasoner import prepare_for
except Exception:
    prepare_for = None


def _append_run_log(goal, outcome, steps):
    """Append one line summarizing the run. Never raises."""
    try:
        with open("agent_runs.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} | {outcome} | steps={steps} | {goal}\n")
    except Exception:
        pass


def _action_detail(action):
    """Short detail string for logging/printing a proposed action."""
    for key in ("id", "text", "key", "url", "match", "keys", "seconds", "direction"):
        if action.get(key) not in (None, ""):
            return action.get(key)
    return ""


def _history_line(action, ok, msg):
    """One concise history line: action + ok/fail + result note."""
    kind = action.get("action", "?")
    detail = _action_detail(action)
    prefix = f"OK {kind}" if ok else f"FAIL {kind}"
    if detail != "" and detail is not None:
        return f"{prefix}({detail}): {msg}"
    return f"{prefix}: {msg}"


def _quiet_browser_teardown():
    """Best-effort Playwright close so process exit does not dump EPIPE."""
    try:
        from browser_locator import disconnect_browser
        disconnect_browser()
    except Exception:
        pass


# Cap how many times the user can correct one proposed step
_MAX_CORRECTIONS = 3


def _print_proposal(action, label="proposes"):
    print(f"  {label}: {action.get('action')} "
          f"{_action_detail(action)} "
          f"- {action.get('why','')}")


def _approve_or_correct(goal, elements, path, history, action, auto_approve):
    """Human gate: y = approve, s/stop = stop, else re-reason with correction.

    Returns (status, action) where status is one of:
      "ok" | "stopped" | "done" | "stuck"
    """
    if auto_approve:
        return "ok", action

    corrections = 0
    while True:
        if corrections >= _MAX_CORRECTIONS:
            ans = input("  approve? (y = do it / s = stop): ").strip().lower()
            if ans == "y":
                return "ok", action
            print("  stopped by human.")
            return "stopped", action

        raw = input(
            "  approve? (y = do it / s = stop / or type a correction): "
        ).strip()
        ans = raw.lower()
        if ans == "y":
            return "ok", action
        if ans in ("s", "stop"):
            print("  stopped by human.")
            return "stopped", action

        # Plain-language correction: do not execute the proposed action
        corrections += 1
        print(f"  correction noted — re-reasoning "
              f"({corrections}/{_MAX_CORRECTIONS})...")
        action = reason_next_action(
            goal, elements, path, history, correction=raw
        )
        _print_proposal(action)

        if action.get("action") == "done":
            return "done", action
        if action.get("action") == "stuck":
            return "stuck", action


def _title_hint_from_goal(goal):
    """Soft window-title hint when the goal names a known site/app."""
    if not goal:
        return None
    g = goal.lower()
    for token in ("linkedin", "gmail", "github", "youtube", "notion",
                  "chatgpt", "claude", "google", "notepad"):
        if token in g:
            return token
    return None


_BROWSER_GOAL_MARKERS = (
    "http://", "https://", ".com", ".org", ".net", ".io",
    "google", "gmail", "linkedin", "youtube", "github",
    "website", "web page", "webpage", "browser",
    "search the web", "search google", "search on",
    "navigate to", "go to www", "open www",
)


def _detect_browser_task(goal, prereq_caps=None):
    """True if this goal should target the browser (DOM), not the frontmost app."""
    caps = prereq_caps or []
    if any(c in ("browser", "browser_debug") for c in caps):
        return True
    g = (goal or "").lower()
    if any(m in g for m in _BROWSER_GOAL_MARKERS):
        return True
    # bare 'search for X' is ambiguous — only count if paired with a site word above
    return False


def _focus_chrome_before_perceive(goal):
    """Bring debug Chrome to the foreground. Returns True on success."""
    try:
        from prereq_reasoner import focus_app
    except Exception as e:
        print(f"  [focus] cannot import focus_app: {e}")
        return False
    hint = _title_hint_from_goal(goal)
    print(f"  [focus] bringing Chrome frontmost before perceive (hint={hint!r})...")
    ok = focus_app(["chrome.exe"], title_hint=hint)
    if not ok:
        print("  WARNING: could not focus Chrome — refusing to act on the wrong window.")
        return False
    time.sleep(0.3)
    return True


def _title_hint_from_page(page_info, goal):
    """Prefer the live browser tab title so we focus the debug Chrome showing it."""
    if page_info and page_info.get("mode") == "browser":
        title = (page_info.get("title") or "").strip()
        for suffix in (" - Google Chrome", " - Chrome", " - Chromium"):
            if title.endswith(suffix):
                title = title[: -len(suffix)].strip()
                break
        if title:
            return title
        url = (page_info.get("url") or "").strip()
        if url and not url.startswith("chrome://") and url not in ("about:blank",):
            # host as weak hint, e.g. www.linkedin.com
            try:
                from urllib.parse import urlparse
                host = urlparse(url).hostname or ""
                if host.startswith("www."):
                    host = host[4:]
                if host:
                    return host.split(".")[0]
            except Exception:
                pass
    return _title_hint_from_goal(goal)


def _ensure_target_focus(goal, target_procs, page_info):
    """Before acting: bring the intended app (debug Chrome on 9222) to the front.
    Never launches a new Chrome — only focuses an existing matching window."""
    hint = _title_hint_from_page(page_info, goal)
    procs = target_procs
    if page_info and page_info.get("mode") == "browser":
        procs = ["chrome.exe"]
    if not procs:
        return hint
    try:
        from prereq_reasoner import focus_app
        print(f"  [focus] ensuring target window before act (hint={hint!r})...")
        focus_app(procs, title_hint=hint)
    except Exception as e:
        print(f"  [focus] re-focus skipped: {e}")
    return hint


def _trace_signature(action, elements):
    """Short signature of the target element for a verified step hint."""
    sig = {"name": "", "control_type": "", "position": None}
    eid = action.get("id")
    if eid is None:
        return sig
    try:
        eid = int(eid)
    except (TypeError, ValueError):
        return sig
    match = next((e for e in elements if e.get("id") == eid), None)
    if not match:
        return sig
    pos = None
    if match.get("cx") is not None and match.get("cy") is not None:
        # rough position only — not used as a hard locator
        pos = {"cx": int(match["cx"]), "cy": int(match["cy"])}
    return {
        "name": match.get("name") or "",
        "control_type": match.get("control_type") or "",
        "position": pos,
    }


def _make_trace_entry(action, elements, page_info):
    """One verified-step hint: action + element signature + perception mode."""
    mode = "native"
    if page_info and page_info.get("mode"):
        mode = page_info["mode"]
    elif any(e.get("browser") for e in (elements or [])):
        mode = "browser"
    return {
        "action": dict(action),
        "signature": _trace_signature(action, elements),
        "mode": mode,
    }


def run_goal(goal, max_steps=None, auto_approve=None, skip_prereqs=False,
             record_trace=False):
    """Run the goal-driven loop, ensuring prerequisites first.

    If record_trace=True, every APPROVED and successfully executed action is
    appended to a trace list. Returns (outcome, trace) in that case.
    Default (record_trace=False) still returns just the outcome string.
    """
    if max_steps is None:
        max_steps = MAX_STEPS
    if auto_approve is None:
        auto_approve = not REQUIRE_APPROVAL

    try:
        return _run_goal_body(
            goal, max_steps, auto_approve, skip_prereqs, record_trace
        )
    finally:
        # Close CDP/Playwright after each goal so exit does not throw EPIPE
        _quiet_browser_teardown()


def _run_goal_body(goal, max_steps, auto_approve, skip_prereqs, record_trace=False):
    print(f"\n=== GOAL: {goal} ===")
    if record_trace:
        print("[train] recording verified action sequence")

    # ---- Stage C: reason about + prepare the environment before acting ----
    target_procs = None
    prereq_caps = []
    results = []
    if prepare_for and not skip_prereqs:
        results = prepare_for(goal=goal)
        for cap, ready in results:
            prereq_caps.append(cap)
            if not ready:
                print(f"  WARNING: could not ensure '{cap}' is ready.")
                ans = input("  continue anyway? (y/n): ").strip().lower()
                if ans != "y":
                    _append_run_log(goal, "prereq_failed", 0)
                    return ("prereq_failed", []) if record_trace else "prereq_failed"
        # remember the target app's process names to refocus before each action
        try:
            from prereq_reasoner import CAPABILITIES
            for cap, ready in results:
                if ready and CAPABILITIES.get(cap, {}).get("procs"):
                    target_procs = CAPABILITIES[cap]["procs"]
                    break
        except Exception:
            pass

    is_browser_task = _detect_browser_task(goal, prereq_caps)
    if is_browser_task:
        target_procs = ["chrome.exe"]
        print("[browser-task] will force Chrome frontmost before each perceive "
              "(prefer_browser=True; no native-tree fallback)")

    print(f"\n[loop] max {max_steps} steps, human approves each action\n")
    history = []
    trace = []
    steps_taken = 0

    def _finish(outcome):
        _append_run_log(goal, outcome, steps_taken)
        if record_trace:
            return outcome, trace
        return outcome

    def _perceive_step():
        """Focus Chrome when needed, then perceive (DOM for browser tasks)."""
        if is_browser_task:
            if not _focus_chrome_before_perceive(goal):
                return None
        return perceive(prefer_browser=is_browser_task)

    for step_num in range(1, max_steps + 1):
        steps_taken = step_num
        print(f"--- step {step_num}/{max_steps} ---")

        perceived = _perceive_step()
        if perceived is None:
            return _finish("focus_failed")
        elements, path, page_info = perceived
        action = reason_next_action(goal, elements, path, history)
        _print_proposal(action)

        if action.get("action") == "done":
            print("\n  the agent believes the GOAL IS REACHED.")
            return _finish("done")
        if action.get("action") == "stuck":
            print(f"\n  the agent is STUCK: {action.get('why')}. stopping.")
            return _finish("stuck")

        status, action = _approve_or_correct(
            goal, elements, path, history, action, auto_approve
        )
        if status == "stopped":
            return _finish("stopped")
        if status == "done":
            print("\n  the agent believes the GOAL IS REACHED.")
            return _finish("done")
        if status == "stuck":
            print(f"\n  the agent is STUCK: {action.get('why')}. stopping.")
            return _finish("stuck")

        title_hint = _ensure_target_focus(goal, target_procs, page_info)
        ok, msg = do_action(
            action, elements, target_procs=target_procs, title_hint=title_hint
        )
        print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        history.append(_history_line(action, ok, msg))

        if ok and record_trace:
            trace.append(_make_trace_entry(action, elements, page_info))
            print(f"  [train] recorded step {len(trace)}")

        if not ok:
            # One automatic retry: re-perceive + different action, before asking human
            print("  auto-retry: re-perceiving and asking for a different action...")
            history.append("note: previous action failed; try a DIFFERENT next action")
            perceived = _perceive_step()
            if perceived is None:
                return _finish("focus_failed")
            elements, path, page_info = perceived
            retry = reason_next_action(goal, elements, path, history)
            _print_proposal(retry, label="retry proposes")

            if retry.get("action") == "done":
                print("\n  the agent believes the GOAL IS REACHED.")
                return _finish("done")
            if retry.get("action") == "stuck":
                print(f"\n  the agent is STUCK: {retry.get('why')}. stopping.")
                return _finish("stuck")

            status, retry = _approve_or_correct(
                goal, elements, path, history, retry, auto_approve
            )
            if status == "stopped":
                return _finish("stopped")
            if status == "done":
                print("\n  the agent believes the GOAL IS REACHED.")
                return _finish("done")
            if status == "stuck":
                print(f"\n  the agent is STUCK: {retry.get('why')}. stopping.")
                return _finish("stuck")

            title_hint = _ensure_target_focus(goal, target_procs, page_info)
            ok2, msg2 = do_action(
                retry, elements, target_procs=target_procs, title_hint=title_hint
            )
            print(f"  {'OK' if ok2 else 'FAIL'}: {msg2}")
            history.append(_history_line(retry, ok2, msg2))

            if ok2 and record_trace:
                trace.append(_make_trace_entry(retry, elements, page_info))
                print(f"  [train] recorded step {len(trace)}")

            if not ok2:
                cont = input("  retry also failed. continue anyway? (y/n): ").strip().lower()
                if cont != "y":
                    return _finish("failed")

        time.sleep(1.0)

    print(f"\n  reached the {max_steps}-step ceiling without finishing. stopping safely.")
    return _finish("ceiling")


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else "open the View menu and then press escape to close it"
    result = run_goal(goal, max_steps=6)
    print(f"\n=== loop ended: {result} ===")
