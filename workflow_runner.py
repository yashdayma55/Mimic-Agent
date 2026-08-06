"""
Workflow library + runner for MimicAgent.

The seed of the Phase 7 workflow library: each workflow is a named JSON file in
workflows/. This module lists them and runs one by name, programmatically (no
interactive test block), so it can be called from the MCP server or anywhere.

  list_workflow_names()             -> ['notepad_greeting', ...]
  run_workflow_by_name(name, data)  -> a short summary string
"""

import os
import json
import glob

WORKFLOWS_DIR = "workflows"


def list_workflow_names():
    """Return the names of all saved workflows (files in workflows/)."""
    if not os.path.isdir(WORKFLOWS_DIR):
        return []
    names = []
    for path in glob.glob(os.path.join(WORKFLOWS_DIR, "*.json")):
        names.append(os.path.splitext(os.path.basename(path))[0])
    return sorted(names)


def load_workflow(name):
    """Load a workflow's steps by name. Returns a list of steps, or None."""
    path = os.path.join(WORKFLOWS_DIR, f"{name}.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _apply_data(steps, data):
    """Fill in {placeholders} in step text from the data dict.
    e.g. text 'apply as {name}' + data {'name':'Yash'} -> 'apply as Yash'."""
    if not data:
        return steps
    for step in steps:
        if step.get("action") == "type" and "text" in step:
            try:
                step["text"] = step["text"].format(**data)
            except (KeyError, IndexError):
                pass       # leave text as-is if a placeholder has no value
    return steps


def run_workflow_by_name(name, data=None, require_approval=True):
    """Load a named workflow and run it through the replay engine.
    Returns a short human-readable summary for the calling agent.

    NOTE: importing the engine here (not at top) avoids side effects until needed.
    """
    steps = load_workflow(name)
    if steps is None:
        return f"No workflow named '{name}'. Available: {list_workflow_names()}"

    steps = _apply_data(steps, data or {})

    # run through the engine programmatically
    try:
        from workflow_engine import run_plan   # a non-interactive engine entry point
        ran, skipped = run_plan(steps, require_approval=require_approval)
    except Exception as e:
        return f"Workflow '{name}' failed to run: {e}"

    if skipped:
        return (f"Workflow '{name}': {len(ran)} steps done, {len(skipped)} skipped "
                f"({', '.join(skipped)}).")
    return f"Workflow '{name}' completed all {len(ran)} steps."


if __name__ == "__main__":
    print("workflows found:", list_workflow_names())
    for n in list_workflow_names():
        steps = load_workflow(n)
        print(f"  {n}: {len(steps)} steps")