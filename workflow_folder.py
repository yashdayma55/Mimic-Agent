"""
Per-workflow folder layout under workflows/<name>/.

Each named recording gets its own isolated db, captures, plan, and transcript.
The data/logic layer — no web code.
"""

import os
import re
import shutil
from datetime import datetime

WORKFLOWS_ROOT = "workflows"


def safe_name(name):
    """Turn a user-typed name into a safe folder stem."""
    stem = (name or "").strip().lower()
    stem = re.sub(r"[^\w\s-]", "", stem)
    stem = re.sub(r"[\s-]+", "_", stem).strip("_")
    return stem or "workflow"


def workflow_dir(name):
    """Absolute path to workflows/<name>/."""
    return os.path.abspath(os.path.join(WORKFLOWS_ROOT, safe_name(name)))


def captures_dir(name):
    return os.path.join(workflow_dir(name), "captures")


def recording_db(name):
    return os.path.join(workflow_dir(name), "recording.db")


def plan_json(name):
    return os.path.join(workflow_dir(name), "plan.json")


def plan_txt(name):
    return os.path.join(workflow_dir(name), "plan.txt")


def transcript_json(name):
    return os.path.join(workflow_dir(name), "transcript.json")


def transcript_txt(name):
    return os.path.join(workflow_dir(name), "transcript.txt")


def resolve_paths(name):
    """Return all standard paths for a named workflow folder."""
    wd = workflow_dir(name)
    return {
        "name": safe_name(name),
        "workflow_dir": wd,
        "captures_dir": os.path.join(wd, "captures"),
        "recording_db": os.path.join(wd, "recording.db"),
        "plan_json": os.path.join(wd, "plan.json"),
        "plan_txt": os.path.join(wd, "plan.txt"),
        "transcript_json": os.path.join(wd, "transcript.json"),
        "transcript_txt": os.path.join(wd, "transcript.txt"),
    }


def infer_workflow_dir(source):
    """Derive the workflow folder from a plan path or workflow name.

    Returns absolute path to workflows/<name>/, or None if unknown.
    """
    if not source:
        return None
    if isinstance(source, str):
        if os.path.isfile(source):
            parent = os.path.abspath(os.path.dirname(source))
            if (
                os.path.isdir(os.path.join(parent, "captures"))
                or os.path.isfile(os.path.join(parent, "recording.db"))
                or os.path.basename(source) in ("plan.json", "transcript.json")
            ):
                return parent
        if workflow_exists(source):
            return workflow_dir(source)
    return None


def normalize_screenshot_path(stored, workflow_dir):
    """Resolve a stored screenshot reference within ONE workflow folder only.

    Returns absolute path if the file exists under workflow_dir, else None.
    Never searches top-level captures/ or other workflows.
    """
    if not stored or not workflow_dir:
        return None
    workflow_dir = os.path.abspath(workflow_dir)
    stored = str(stored).replace("\\", "/").lstrip("./")
    candidates = [
        os.path.join(workflow_dir, stored),
        os.path.join(workflow_dir, "captures", os.path.basename(stored)),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


def workflow_exists(name):
    """True if the workflow folder already exists."""
    return os.path.isdir(workflow_dir(name))


def create_workflow_folder(name, overwrite=False):
    """Create workflows/<name>/ and captures/. Raises if exists and not overwrite."""
    paths = resolve_paths(name)
    wd = paths["workflow_dir"]
    if os.path.isdir(wd):
        if not overwrite:
            raise FileExistsError(
                f"workflow folder already exists: {wd}\n"
                f"Choose another name or confirm overwrite."
            )
    os.makedirs(paths["captures_dir"], exist_ok=True)
    return paths


def list_workflow_folders():
    """List named workflow folders (subdirs of workflows/, not flat .json files)."""
    root = WORKFLOWS_ROOT
    if not os.path.isdir(root):
        return []
    out = []
    for entry in sorted(os.listdir(root)):
        full = os.path.join(root, entry)
        if os.path.isdir(full):
            out.append(entry)
    return out


def most_recent_workflow():
    """Return the name of the most recently modified workflow folder, or None."""
    names = list_workflow_folders()
    if not names:
        return None
    best = None
    best_mtime = -1.0
    for n in names:
        wd = workflow_dir(n)
        try:
            m = os.path.getmtime(wd)
        except OSError:
            continue
        if m > best_mtime:
            best_mtime = m
            best = n
    return best


def clear_recording_db(name):
    """Delete all events in this workflow's recording.db (fresh record session)."""
    db = recording_db(name)
    if not os.path.isfile(db):
        return
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM events")
    conn.commit()
    conn.close()


def prepare_for_record(name, overwrite=False):
    """Create folder (with overwrite prompt handled by caller) and return paths."""
    return create_workflow_folder(name, overwrite=overwrite)


if __name__ == "__main__":
    print("=== workflow_folder self-test ===")
    test_name = "selftest_wf"
    test_paths = resolve_paths(test_name)
    print("resolved paths (before create):")
    for k, v in test_paths.items():
        print(f"  {k}: {v}")

    if workflow_exists(test_name):
        shutil.rmtree(test_paths["workflow_dir"])
    created = create_workflow_folder(test_name)
    assert os.path.isdir(created["workflow_dir"]), "workflow dir missing"
    assert os.path.isdir(created["captures_dir"]), "captures dir missing"
    assert created["recording_db"] != "recording.db"
    assert "selftest_wf" in created["captures_dir"]
    assert not created["captures_dir"].startswith("captures")
    print("\ncreated:")
    for k, v in created.items():
        print(f"  {k}: {v}")
    print("\nOK")
