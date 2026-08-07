"""
MimicAgent Phase 7 - save a fresh recording into the workflow library.

After you record a task and distill it into plan.json, this saves that plan
under a name you choose, so it joins the library and shows up in the menu.
This closes the loop: record many tasks, each saved by name, pick any to run.

Usage:
  python save_recording.py            # prompts for a name, saves plan.json
  python save_recording.py apply_job  # saves plan.json as 'apply_job'
"""

import sys
import json
import os
from library import save_workflow, list_workflows

PLAN_FILE = "plan.json"


def save_current_recording(name=None):
    if not os.path.isfile(PLAN_FILE):
        print(f"No {PLAN_FILE} found. Record and distill a task first.")
        return

    with open(PLAN_FILE, "r", encoding="utf-8") as f:
        steps = json.load(f)

    if not name:
        print(f"This recording has {len(steps)} steps.")
        name = input("save it under what name? ").strip()
        if not name:
            print("no name given, cancelled.")
            return

    # don't silently clobber an existing workflow
    try:
        saved = save_workflow(name, steps, overwrite=False)
    except FileExistsError:
        ans = input(f"'{name}' already exists. overwrite? (y/n): ").strip().lower()
        if ans != "y":
            print("cancelled.")
            return
        saved = save_workflow(name, steps, overwrite=True)

    print(f"\nsaved as '{saved}'. your library now has:")
    for n in list_workflows():
        print(f"   - {n}")


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else None
    save_current_recording(name)