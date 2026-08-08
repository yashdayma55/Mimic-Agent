"""
MimicAgent Phase 7 - a simple workflow picker.

Lists the saved RECORDED workflows like a menu, lets you choose one by number,
and runs it through the engine with per-step approval. Trained workflows are
not listed here — use mimic.py option 5 for those.
"""

from library import list_workflows, load_workflow, workflow_info
from workflow_engine import run_plan


def choose_and_run():
    names = list_workflows()
    if not names:
        print("No recorded workflows yet. Record one first.")
        print("(Trained workflows are under menu option 5.)")
        return

    print("\n=== MimicAgent workflows (recorded) ===")
    for i, name in enumerate(names, 1):
        info = workflow_info(name)
        if not info or info.get("kind") == "trained":
            # Defensive: list_workflows should already exclude trained
            print(f"  {i}. {name}  (unavailable)")
            continue
        print(f"  {i}. {info['name']}  ({info['steps']} steps)")
    print("  0. cancel")

    try:
        choice = int(input("\npick a workflow to run: ").strip())
    except ValueError:
        print("not a number, cancelled.")
        return

    if choice == 0 or choice > len(names):
        print("cancelled.")
        return

    name = names[choice - 1]
    steps = load_workflow(name)
    if not isinstance(steps, list):
        print(f"'{name}' is not a recorded step list "
              f"(is it a trained workflow? use option 5).")
        return
    print(f"\nrunning '{name}' ({len(steps)} steps). approve each step.\n")
    ran, skipped = run_plan(steps, require_approval=True)

    print(f"\n=== '{name}' finished: {len(ran)} done, {len(skipped)} skipped ===")
    if skipped:
        print("   skipped:", skipped)


if __name__ == "__main__":
    choose_and_run()
