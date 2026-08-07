"""
Stage B steps 4+5: the full goal-driven loop, safely bounded.

  perceive -> reason -> APPROVE -> act -> observe -> check goal -> repeat

Safety (from the study notes):
  - human approves EVERY action before it happens
  - a hard STEP CEILING stops a confused agent from flailing forever
  - 'done' ends cleanly; 'stuck' or rejection or the ceiling -> stop safely
  - one action at a time, re-perceiving each turn (never a plan swallowed whole)
"""

import sys
from agent_loop import perceive
from agent_reason import reason_next_action
from agent_act import do_action


def check_goal_reached(goal, elements, image_path, history):
    """OBSERVE + CHECK: after acting, ask the model if the goal is now reached.
    Returns True/False."""
    # reuse the reasoner: if it says 'done', the goal is reached
    action = reason_next_action(goal, elements, image_path, history)
    return action.get("action") == "done", action


def run_goal(goal, max_steps=8, auto_approve=False):
    """Run the goal-driven loop. Human approves each action unless auto_approve."""
    print(f"\n=== GOAL: {goal} ===")
    print(f"(max {max_steps} steps, human approves each action)\n")
    history = []

    for step_num in range(1, max_steps + 1):
        print(f"--- step {step_num}/{max_steps} ---")

        # PERCEIVE
        elements, path = perceive()

        # REASON: single next action
        action = reason_next_action(goal, elements, path, history)
        print(f"  proposes: {action.get('action')} "
              f"{action.get('id', action.get('text', action.get('key','')))} "
              f"- {action.get('why','')}")

        # terminal states from the model
        if action.get("action") == "done":
            print("\n  the agent believes the GOAL IS REACHED.")
            return "done"
        if action.get("action") == "stuck":
            print(f"\n  the agent is STUCK: {action.get('why')}. stopping.")
            return "stuck"

        # APPROVE (human gate)
        if not auto_approve:
            ans = input("  approve this action? (y = do it / s = stop): ").strip().lower()
            if ans != "y":
                print("  stopped by human.")
                return "stopped"

        # ACT
        ok, msg = do_action(action, elements)
        print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        history.append(msg if ok else f"tried but failed: {msg}")

        if not ok:
            # a failed action -> pause, don't cascade
            cont = input("  that action failed. continue anyway? (y/n): ").strip().lower()
            if cont != "y":
                return "failed"

        # small settle so the screen updates before the next perceive
        import time
        time.sleep(1.0)

    print(f"\n  reached the {max_steps}-step ceiling without finishing. stopping safely.")
    return "ceiling"


if __name__ == "__main__":
    goal = sys.argv[1] if len(sys.argv) > 1 else "open the View menu and then close it"
    result = run_goal(goal, max_steps=6)
    print(f"\n=== loop ended: {result} ===")