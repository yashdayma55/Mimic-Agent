"""
Stage B + C: the full goal-driven loop, with prerequisite setup first.

  ensure prerequisites (Stage C) -> then the loop:
  perceive -> reason -> APPROVE -> act -> observe -> check goal -> repeat

Safety: human approves every action, hard step ceiling, clean stops.
"""

import sys
import time
from agent_loop import perceive
from agent_reason import reason_next_action
from agent_act import do_action

try:
    from prereq_reasoner import prepare_for
except Exception:
    prepare_for = None


def run_goal(goal, max_steps=8, auto_approve=False, skip_prereqs=False):
    """Run the goal-driven loop, ensuring prerequisites first."""
    print(f"\n=== GOAL: {goal} ===")

    # ---- Stage C: reason about + prepare the environment before acting ----
    target_procs = None
    if prepare_for and not skip_prereqs:
        results = prepare_for(goal=goal)
        for cap, ready in results:
            if not ready:
                print(f"  WARNING: could not ensure '{cap}' is ready.")
                ans = input("  continue anyway? (y/n): ").strip().lower()
                if ans != "y":
                    return "prereq_failed"
        # remember the target app's process names to refocus before each action
        try:
            from prereq_reasoner import CAPABILITIES
            for cap, ready in results:
                if ready and CAPABILITIES.get(cap, {}).get("procs"):
                    target_procs = CAPABILITIES[cap]["procs"]
                    break
        except Exception:
            pass

    print(f"\n[loop] max {max_steps} steps, human approves each action\n")
    history = []

    for step_num in range(1, max_steps + 1):
        print(f"--- step {step_num}/{max_steps} ---")

        elements, path = perceive()
        action = reason_next_action(goal, elements, path, history)
        print(f"  proposes: {action.get('action')} "
              f"{action.get('id', action.get('text', action.get('key','')))} "
              f"- {action.get('why','')}")

        if action.get("action") == "done":
            print("\n  the agent believes the GOAL IS REACHED.")
            return "done"
        if action.get("action") == "stuck":
            print(f"\n  the agent is STUCK: {action.get('why')}. stopping.")
            return "stuck"

        if not auto_approve:
            ans = input("  approve this action? (y = do it / s = stop): ").strip().lower()
            if ans != "y":
                print("  stopped by human.")
                return "stopped"

        ok, msg = do_action(action, elements, target_procs=target_procs)
        print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        history.append(msg if ok else f"tried but failed: {msg}")

        if not ok:
            cont = input("  that action failed. continue anyway? (y/n): ").strip().lower()
            if cont != "y":
                return "failed"

        time.sleep(1.0)

    print(f"\n  reached the {max_steps}-step ceiling without finishing. stopping safely.")
    return "ceiling"


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else "open the View menu and then press escape to close it"
    result = run_goal(goal, max_steps=6)
    print(f"\n=== loop ended: {result} ===")