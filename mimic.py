"""
MimicAgent front door — pick a saved workflow or give the agent a goal.
"""

from menu import choose_and_run
from agent_run import run_goal
from trained_workflows import save_trained, list_trained, load_trained
from auto_runner import run_trained


def _read_goal():
    """Read a single-line goal; empty cancels back to the menu."""
    print("Describe any small bounded task in plain language.")
    print("  e.g. go to example.com, search for X, click the first result")
    return input("Enter your goal: ").strip()


def _chat_loop():
    """Repeated one-line goals via the same run_goal; quit/exit returns to menu."""
    print("Describe tasks in plain language. Type 'quit' to return to the menu.")
    empty_streak = 0
    while True:
        try:
            line = input("task> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.lower() in ("quit", "exit"):
            break
        if not line:
            empty_streak += 1
            if empty_streak >= 2:
                break
            print("(empty — type a task, or press Enter again to leave chat)")
            continue
        empty_streak = 0
        result = run_goal(line)
        print(f"\n=== loop ended: {result} ===")
        print("done - what next?")


def _train_workflow():
    """Human-in-the-loop training run -> save as a named trained workflow."""
    goal = _read_goal()
    if not goal:
        print("no goal entered.")
        return
    print("\n[train] approving each step (y / s / correction). "
          "Verified actions will be saved as hints.")
    outcome, trace = run_goal(goal, record_trace=True)
    print(f"\n=== training run ended: {outcome} ===")
    if outcome != "done":
        print("  training only saves on a successful 'done' run. not saving.")
        return
    if not trace:
        print("  no verified steps were recorded. not saving.")
        return
    print(f"  recorded {len(trace)} verified step(s).")
    name = input("  name this trained workflow: ").strip()
    if not name:
        print("  no name — not saving.")
        return
    try:
        stem = save_trained(name, goal, trace)
        print(f"  saved as '{stem}' ({len(trace)} hints).")
    except FileExistsError as e:
        ans = input(f"  {e}\n  overwrite? (y/n): ").strip().lower()
        if ans == "y":
            stem = save_trained(name, goal, trace, overwrite=True)
            print(f"  overwritten '{stem}'.")
        else:
            print("  not saved.")


def _run_trained_workflow():
    """Pick a trained workflow and auto-run it (hint-guided, no per-step y/n)."""
    names = list_trained()
    if not names:
        print("  no trained workflows yet. use option 4 to train one.")
        return
    print("\nTrained workflows:")
    for i, n in enumerate(names, 1):
        wf = load_trained(n)
        n_hints = len((wf or {}).get("trace") or [])
        g = ((wf or {}).get("goal") or "")[:60]
        print(f"  {i}. {n}  ({n_hints} hints)  {g}")
    raw = input("\npick number (or name): ").strip()
    if not raw:
        print("cancelled.")
        return
    chosen = None
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(names):
            chosen = names[idx - 1]
    else:
        # accept with or without trained_ prefix
        if raw in names:
            chosen = raw
        elif ("trained_" + raw) in names:
            chosen = "trained_" + raw
        else:
            # try load_trained fuzzy
            if load_trained(raw):
                chosen = raw
    if not chosen:
        print("invalid pick.")
        return
    print("\n  Auto mode: no per-step approval.")
    print("  Press Ctrl+Alt+P (or create a PAUSE file) to pause / edit goal / stop.\n")
    result = run_trained(chosen)
    print(f"\n=== auto run ended: {result} ===")


def main():
    while True:
        print("\n=== MimicAgent ===")
        print("  1. Run a saved workflow")
        print("  2. Give the agent a goal")
        print("  3. Chat with the agent")
        print("  4. Train a workflow")
        print("  5. Run a trained workflow (auto)")
        print("  0. Quit")
        choice = input("\nchoice: ").strip()

        if choice == "1":
            choose_and_run()
        elif choice == "2":
            goal = _read_goal()
            if goal:
                result = run_goal(goal)
                print(f"\n=== loop ended: {result} ===")
            else:
                print("no goal entered.")
        elif choice == "3":
            _chat_loop()
        elif choice == "4":
            _train_workflow()
        elif choice == "5":
            _run_trained_workflow()
        elif choice == "0":
            print("bye.")
            break
        else:
            print("invalid choice.")


if __name__ == "__main__":
    main()
