"""
MimicAgent Phase 7 - the workflow library.

Grows the Phase 6 workflows/ folder into a real library: list, load, save,
rename, delete named workflows. Each workflow is a named JSON file of steps,
stored as a plain file (no database) so it stays offline, inspectable, and
easy to back up.

  list_workflows()              -> ['apply_job', 'notepad_greeting', ...]
  load_workflow(name)           -> [steps] or None
  save_workflow(name, steps)    -> saves (and won't silently overwrite)
  delete_workflow(name)         -> removes it
  rename_workflow(old, new)     -> renames
  workflow_info(name)           -> a small summary dict
"""

import os
import re
import json
import glob
import shutil

WORKFLOWS_DIR = "workflows"


def _ensure_dir():
    os.makedirs(WORKFLOWS_DIR, exist_ok=True)


def _safe_name(name):
    """Turn a user-typed name into a safe filename stem.
    'Apply to Job!' -> 'apply_to_job'"""
    stem = name.strip().lower()
    stem = re.sub(r"[^\w\s-]", "", stem)     # drop punctuation
    stem = re.sub(r"[\s-]+", "_", stem)      # spaces/dashes -> underscore
    return stem or "workflow"


def _path(name):
    return os.path.join(WORKFLOWS_DIR, f"{_safe_name(name)}.json")


def list_workflows():
    """All saved workflow names, sorted."""
    _ensure_dir()
    names = []
    for p in glob.glob(os.path.join(WORKFLOWS_DIR, "*.json")):
        names.append(os.path.splitext(os.path.basename(p))[0])
    return sorted(names)


def load_workflow(name):
    """Load a workflow's steps by name, or None if it doesn't exist."""
    p = _path(name)
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_workflow(name, steps, overwrite=False):
    """Save steps under a name. Refuses to overwrite unless overwrite=True.
    Returns the stored name (the safe stem). Raises FileExistsError if it
    exists and overwrite is False, so a recording never silently clobbers."""
    _ensure_dir()
    p = _path(name)
    if os.path.isfile(p) and not overwrite:
        raise FileExistsError(f"'{_safe_name(name)}' already exists. "
                              f"Use overwrite=True or pick a new name.")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(steps, f, indent=2)
    return _safe_name(name)


def delete_workflow(name):
    """Delete a workflow. Returns True if something was removed."""
    p = _path(name)
    if os.path.isfile(p):
        os.remove(p)
        return True
    return False


def rename_workflow(old, new):
    """Rename a workflow. Returns True on success."""
    src, dst = _path(old), _path(new)
    if not os.path.isfile(src):
        return False
    if os.path.isfile(dst):
        raise FileExistsError(f"'{_safe_name(new)}' already exists.")
    shutil.move(src, dst)
    return True


def workflow_info(name):
    """A small summary of a workflow for showing in a list."""
    steps = load_workflow(name)
    if steps is None:
        return None
    clicks = sum(1 for s in steps if s.get("action") == "click")
    types  = sum(1 for s in steps if s.get("action") == "type")
    return {"name": _safe_name(name), "steps": len(steps),
            "clicks": clicks, "types": types}


if __name__ == "__main__":
    # demo the library on whatever exists, plus a save/list/delete round-trip
    print("current workflows:")
    for n in list_workflows():
        info = workflow_info(n)
        print(f"   {info['name']:22} {info['steps']} steps "
              f"({info['clicks']} clicks, {info['types']} types)")

    print("\n--- save/list/delete round-trip ---")
    demo_steps = [
        {"step": 1, "instruction": "Click into Notepad", "action": "click",
         "elem_name": "Text editor", "elem_type": "Document"},
        {"step": 2, "instruction": "Type hello", "action": "type",
         "elem_name": "Text editor", "text": "library test"},
    ]
    saved = save_workflow("Library Test!", demo_steps, overwrite=True)
    print(f"saved as: {saved}")
    print("now in library:", list_workflows())
    delete_workflow("Library Test!")
    print("after delete:", list_workflows())