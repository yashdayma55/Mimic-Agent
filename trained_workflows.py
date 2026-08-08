"""
Trained workflows: goal + verified step hints from a human-approved training run.

Stored as workflows/trained_<name>.json (DICT with goal/trace) so they do not
clash with raw recorded plans (LIST of steps) from library.py.
list_trained() and library.list_workflows() are disjoint.
"""

import os
import json
from datetime import datetime

from library import WORKFLOWS_DIR, _safe_name, _ensure_dir, _is_trained_payload


def _trained_stem(name):
    """Safe filename stem with trained_ prefix (idempotent)."""
    stem = _safe_name(name)
    if not stem.startswith("trained_"):
        stem = "trained_" + stem
    return stem


def _path(name):
    return os.path.join(WORKFLOWS_DIR, f"{_trained_stem(name)}.json")


def save_trained(name, goal, trace, overwrite=False):
    """Save a trained workflow {name, goal, trace, created}.

    Raises FileExistsError if it already exists and overwrite is False.
    Returns the stored stem name (always trained_*).
    """
    _ensure_dir()
    stem = _trained_stem(name)
    p = _path(name)
    if os.path.isfile(p) and not overwrite:
        raise FileExistsError(
            f"'{stem}' already exists. Pass overwrite=True or pick a new name."
        )
    payload = {
        "name": stem,
        "goal": goal,
        "trace": trace,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return stem


def load_trained(name):
    """Load {goal, trace, ...} by name, or None if missing / not a trained file."""
    p = _path(name)
    # Also allow callers to pass the full stem including trained_
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
    if not _is_trained_payload(data):
        return None
    if "trace" not in data or "goal" not in data:
        return None
    return data


def list_trained():
    """Names of trained workflows only (trained_*.json with goal/trace), sorted."""
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
        # Prefer prefix; also accept unprefixed dict-shaped files as trained
        path = os.path.join(WORKFLOWS_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not _is_trained_payload(data):
            continue
        if "trace" not in data or "goal" not in data:
            continue
        # Only list under trained_* names so option 5 stays clean;
        # if somehow unprefixed, still show it so it isn't orphaned.
        names.append(stem)
    return sorted(names)


if __name__ == "__main__":
    print("trained workflows:", list_trained())
