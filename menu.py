"""
MimicAgent Phase 7 - a simple workflow picker.

Lists the saved workflows like a menu, lets you choose one by number, and runs
it through the engine with per-step approval. This is the "feels like an app"
front door: pick a past workflow and run it, the way you open a past chat.
"""

from library import list_workflows, load_workflow, workflow_info
from workflow_engine import run_plan


def choose_and_run():
    names = list_workflows()
    if not names:
        print("No workflows yet. Record one first.")
        return

    print("\n=== MimicAgent workflows ===")
    for i, name in enumerate(names, 1):
        info = workflow_info(name)
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
    print(f"\nrunning '{name}' ({len(steps)} steps). approve each step.\n")
    ran, skipped = run_plan(steps, require_approval=True)

    print(f"\n=== '{name}' finished: {len(ran)} done, {len(skipped)} skipped ===")
    if skipped:
        print("   skipped:", skipped)


if __name__ == "__main__":
    choose_and_run()