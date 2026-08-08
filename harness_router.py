"""
Harness router: decide which engine runs a step.

Deterministic and cheap — no model calls in v1.
The harness fills live_context each step so routing reflects the ACTUAL
current screen (browser frontmost vs native app), not a record-time guess.
"""

from harness_schema import STEP_KINDS

# Optional: browser frontmost probe (native path still works if missing)
try:
    from browser_detect import is_browser_frontmost
except Exception:
    def is_browser_frontmost():
        return False


def decide_kind(step, live_context) -> str:
    """Return 'browser' | 'native' | 'reason' for this step.

    live_context: a small dict the harness passes, e.g.
      {"browser_frontmost": bool, "page_url": str|None, "goal_hint": str|None}
    """
    # 1. explicit wins: if the step already declares a kind, trust it
    kind = getattr(step, "kind", None)
    if kind in STEP_KINDS:
        return kind

    # Also accept a plain dict step
    if isinstance(step, dict):
        kind = step.get("kind")
        if kind in STEP_KINDS:
            return kind
        goal = step.get("goal")
        action = step.get("action")
        if goal and not action:
            return "reason"
    else:
        # 2. a reason step is anything expressed only as a goal with no concrete target
        goal = getattr(step, "goal", None)
        action = getattr(step, "action", None)
        if goal and not action:
            return "reason"

    # 3. otherwise decide browser vs native by the LIVE foreground context
    ctx = live_context or {}
    if ctx.get("browser_frontmost"):
        return "browser"
    # If caller omitted the flag, probe once (still deterministic, no model)
    if "browser_frontmost" not in ctx:
        try:
            if is_browser_frontmost():
                return "browser"
        except Exception:
            pass
    return "native"

    # TODO(model-tiebreaker): when kind is ambiguous (e.g. step has both a vague
    # description and a weak target, or browser_frontmost is unclear), ask a
    # small model to pick among STEP_KINDS given step.description + live_context.
    # Keep decide_kind free of network calls until that hook is deliberately enabled.


if __name__ == "__main__":
    from harness_schema import HarnessStep

    browser_step = HarnessStep(
        kind="browser",
        description="click Search",
        action={"action": "click", "id": 1},
        target_name="Search",
    )
    native_step = HarnessStep(
        kind="native",
        description="type in Notepad",
        action={"action": "type", "text": "hi"},
        target_name="Text Editor",
    )
    reason_step = HarnessStep(
        kind="reason",
        description="handle a popup",
        goal="dismiss any modal dialog",
    )
    # undeclared kind: live context decides
    undeclared = HarnessStep(
        kind="",  # will fail validate; used only to test routing without explicit kind
        description="click something",
        action={"action": "click", "id": 2},
        target_name="OK",
    )
    # Bypass validate: clear kind via object trick
    undeclared.kind = "???"  # not in STEP_KINDS -> fall through to live context

    goal_only = HarnessStep(
        kind="???",
        description="do the next bit",
        goal="find and click the Submit button",
    )

    print("=== harness_router: decide_kind ===")
    cases = [
        (browser_step, {"browser_frontmost": False}, "browser"),   # explicit wins
        (native_step, {"browser_frontmost": True}, "native"),      # explicit wins
        (reason_step, {"browser_frontmost": True}, "reason"),      # explicit wins
        (undeclared, {"browser_frontmost": True}, "browser"),      # live context
        (undeclared, {"browser_frontmost": False}, "native"),      # live context
        (goal_only, {"browser_frontmost": False}, "reason"),       # goal, no action
    ]
    all_ok = True
    for step, ctx, expected in cases:
        got = decide_kind(step, ctx)
        ok = got == expected
        all_ok = all_ok and ok
        mark = "OK" if ok else "FAIL"
        print(f"  [{mark}] kind={step.kind!r} ctx={ctx} -> {got!r} (want {expected!r})")
    print("ok" if all_ok else "SOME FAILURES")
