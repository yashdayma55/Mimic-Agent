"""
MimicAgent Phase 7 - the workflow library.

Grows the Phase 6 workflows/ folder into a real library: list, load, save,
rename, delete named workflows. Each workflow is a named JSON file of steps,
stored as a plain file (no database) so it stays offline, inspectable, and
easy to back up.

Recorded workflows = a JSON LIST of step dicts (this module).
Trained workflows  = a JSON DICT {name, goal, trace, ...} (trained_workflows.py),
                     stored as trained_*.json.
Harness workflows  = a JSON DICT {name, steps, inputs, ...} (harness_store.py),
                     stored as harness_*.json.
Option 1 only lists recorded; option 5 trained; option 7 harness — disjoint.

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


def _is_trained_stem(stem):
    """Filename stem reserved for trained workflows (option 5)."""
    return (stem or "").startswith("trained_")


def _is_harness_stem(stem):
    """Filename stem reserved for harness workflows (option 7)."""
    return (stem or "").startswith("harness_")


def _is_trained_payload(data):
    """True if JSON looks like a trained workflow {goal, trace, ...}."""
    return (
        isinstance(data, dict)
        and ("trace" in data or "goal" in data)
        and "trace" in data  # trained must have trace
        and not isinstance(data, list)
    )


def _is_harness_payload(data):
    """True if JSON looks like a harness workflow {steps, inputs, ...}."""
    return (
        isinstance(data, dict)
        and "steps" in data
        and isinstance(data.get("steps"), list)
        and "trace" not in data
    )


def _is_recorded_steps(data):
    """True if JSON is a plain recorded plan: list of step dicts."""
    return isinstance(data, list)


def _load_raw(name):
    """Load raw JSON for a stem, or None."""
    p = _path(name)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_workflows():
    """Recorded workflow names only (excludes trained_* and harness_*), sorted."""
    _ensure_dir()
    names = []
    for p in glob.glob(os.path.join(WORKFLOWS_DIR, "*.json")):
        stem = os.path.splitext(os.path.basename(p))[0]
        if _is_trained_stem(stem) or _is_harness_stem(stem):
            continue
        # Also exclude by content shape (trained/harness without prefix)
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if _is_trained_payload(data) or _is_harness_payload(data):
            continue
        if not _is_recorded_steps(data):
            continue
        names.append(stem)
    return sorted(names)


def load_workflow(name):
    """Load a recorded workflow's step list by name, or None.

    Returns None for missing files, trained/harness workflows, or non-list JSON
    so callers never treat a trained/harness dict as a step list.
    """
    stem = _safe_name(name)
    if _is_trained_stem(stem) or _is_harness_stem(stem):
        return None
    data = _load_raw(name)
    if data is None:
        return None
    if _is_trained_payload(data) or _is_harness_payload(data):
        return None
    if not _is_recorded_steps(data):
        return None
    return data


def save_workflow(name, steps, overwrite=False):
    """Save steps under a name. Refuses to overwrite unless overwrite=True.
    Returns the stored name (the safe stem). Raises FileExistsError if it
    exists and overwrite is False, so a recording never silently clobbers.
    Refuses names that collide with the trained_/harness_ prefixes."""
    _ensure_dir()
    stem = _safe_name(name)
    if _is_trained_stem(stem):
        raise ValueError(
            f"'{stem}' is reserved for trained workflows (use trained_workflows.save_trained)."
        )
    if _is_harness_stem(stem):
        raise ValueError(
            f"'{stem}' is reserved for harness workflows (use harness_store.save_harness)."
        )
    p = _path(name)
    if os.path.isfile(p) and not overwrite:
        raise FileExistsError(f"'{stem}' already exists. "
                              f"Use overwrite=True or pick a new name.")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(steps, f, indent=2)
    return stem


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
    if _is_trained_stem(_safe_name(new)):
        raise ValueError(f"'{_safe_name(new)}' is reserved for trained workflows.")
    if os.path.isfile(dst):
        raise FileExistsError(f"'{_safe_name(new)}' already exists.")
    shutil.move(src, dst)
    return True


def workflow_info(name):
    """A small summary of a workflow for showing in a list.

    Recorded (list of steps) -> {name, kind:'recorded', steps, clicks, types}.
    Trained (dict with goal/trace) -> {name, kind:'trained', steps} (no crash).
    Unknown / missing -> None.
    """
    data = _load_raw(name)
    if data is None:
        return None

    stem = _safe_name(name)

    if _is_trained_payload(data):
        trace = data.get("trace") if isinstance(data, dict) else None
        n = len(trace) if isinstance(trace, list) else 0
        return {
            "name": stem,
            "kind": "trained",
            "steps": n,
            "clicks": 0,
            "types": 0,
        }

    if not _is_recorded_steps(data):
        return None

    clicks = sum(
        1 for s in data
        if isinstance(s, dict) and s.get("action") == "click"
    )
    types = sum(
        1 for s in data
        if isinstance(s, dict) and s.get("action") == "type"
    )
    return {
        "name": stem,
        "kind": "recorded",
        "steps": len(data),
        "clicks": clicks,
        "types": types,
    }


if __name__ == "__main__":
    # demo the library on whatever exists, plus a save/list/delete round-trip
    print("current recorded workflows:")
    for n in list_workflows():
        info = workflow_info(n)
        if not info:
            continue
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
