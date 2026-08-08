"""
Harness workflows: parameterized HarnessStep lists for run_harness.

Stored as workflows/harness_<name>.json:
  {name, steps (HarnessStep dicts), inputs (placeholder names), created}

Kept DISTINCT from:
  - recorded plans (plain step lists) -> library.py / option 1
  - trained hint traces (trained_*.json) -> trained_workflows.py / option 5
"""

import os
import json
from datetime import datetime

from library import WORKFLOWS_DIR, _safe_name, _ensure_dir
from harness_schema import HarnessStep, step_from_dict, step_to_dict


def _harness_stem(name):
    """Safe filename stem with harness_ prefix (idempotent)."""
    stem = _safe_name(name)
    if not stem.startswith("harness_"):
        stem = "harness_" + stem
    return stem


def _path(name):
    return os.path.join(WORKFLOWS_DIR, f"{_harness_stem(name)}.json")


def _is_harness_payload(data):
    return (
        isinstance(data, dict)
        and "steps" in data
        and isinstance(data.get("steps"), list)
        and "trace" not in data  # trained files have trace, not harness steps
    )


def save_harness(name, steps, inputs=None, overwrite=False):
    """Save a harness workflow. steps: list of HarnessStep or dicts.

    Raises FileExistsError if it exists and overwrite is False.
    Returns the stored stem name.
    """
    _ensure_dir()
    stem = _harness_stem(name)
    p = _path(name)
    if os.path.isfile(p) and not overwrite:
        raise FileExistsError(
            f"'{stem}' already exists. Pass overwrite=True or pick a new name."
        )

    step_dicts = []
    for s in steps:
        if isinstance(s, HarnessStep):
            step_dicts.append(step_to_dict(s))
        elif isinstance(s, dict):
            step_dicts.append(step_to_dict(step_from_dict(s)))
        else:
            raise TypeError(f"bad step type: {type(s)}")

    # Collect declared inputs from arg + per-step inputs + {placeholders} in text
    import re
    declared = list(inputs or [])
    seen = set(declared)
    for sd in step_dicts:
        for inp in sd.get("inputs") or []:
            if inp not in seen:
                declared.append(inp)
                seen.add(inp)
        for field in (sd.get("description"), sd.get("goal")):
            if not isinstance(field, str):
                continue
            for m in re.findall(r"\{([a-zA-Z_][\w]*)\}", field):
                if m not in seen:
                    declared.append(m)
                    seen.add(m)
        action = sd.get("action") or {}
        if isinstance(action, dict):
            for key in ("text", "url", "match", "why"):
                val = action.get(key)
                if isinstance(val, str):
                    for m in re.findall(r"\{([a-zA-Z_][\w]*)\}", val):
                        if m not in seen:
                            declared.append(m)
                            seen.add(m)

    payload = {
        "name": stem,
        "steps": step_dicts,
        "inputs": declared,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return stem


def load_harness(name):
    """Load {name, steps, inputs, created} or None."""
    p = _path(name)
    if not os.path.isfile(p):
        alt = os.path.join(WORKFLOWS_DIR, f"{_safe_name(name)}.json")
        if os.path.isfile(alt):
            p = alt
        else:
            return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not _is_harness_payload(data):
        return None
    # Normalize steps to HarnessStep-compatible dicts
    return data


def load_harness_steps(name):
    """Return list of HarnessStep for run_harness, or None."""
    data = load_harness(name)
    if not data:
        return None
    return [step_from_dict(s) for s in data.get("steps") or [] if isinstance(s, dict)]


def list_harness():
    """Names of harness workflows (harness_*.json), sorted."""
    _ensure_dir()
    names = []
    try:
        entries = os.listdir(WORKFLOWS_DIR)
    except Exception:
        return []
    for fname in entries:
        if not fname.endswith(".json"):
            continue
        stem = fname[:-5]
        if not stem.startswith("harness_"):
            continue
        path = os.path.join(WORKFLOWS_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if _is_harness_payload(data):
            names.append(stem)
    return sorted(names)


if __name__ == "__main__":
    print("harness workflows:", list_harness())
