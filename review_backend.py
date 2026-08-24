"""
Visual workflow review — DATA/LOGIC layer (the brain).

Pure Python: load steps + screenshots, save edits, run via harness.
Knows NOTHING about HTML/HTTP. The web/desktop face calls these functions only.
"""

import json
import os
import re

from harness_schema import HarnessStep, step_from_dict, step_to_dict, STEP_KINDS
from harness import run_harness

DEFAULT_TRANSCRIPT_JSON = "transcript.json"
DEFAULT_TRANSCRIPT_TXT = "transcript.txt"
DEFAULT_PLAN_JSON = "plan.json"
CAPTURES_DIR = "captures"
_NOTE_SUFFIX_RE = re.compile(r"\s+— note:\s*(.+)$", re.DOTALL)


def _basename(path):
    if not path:
        return None
    return os.path.basename(path.replace("\\", "/"))


def get_review_context(workflow_name=None):
    """Resolve paths for a named workflow folder, or legacy top-level files."""
    from workflow_folder import resolve_paths, most_recent_workflow, workflow_exists

    if workflow_name is None:
        workflow_name = most_recent_workflow()
    if workflow_name and workflow_exists(workflow_name):
        paths = resolve_paths(workflow_name)
        return {
            "name": paths["name"],
            "workflow_dir": paths["workflow_dir"],
            "transcript_json": paths["transcript_json"],
            "transcript_txt": paths["transcript_txt"],
            "plan_json": paths["plan_json"],
            "captures_dir": paths["captures_dir"],
        }
    return {
        "name": None,
        "workflow_dir": None,
        "transcript_json": DEFAULT_TRANSCRIPT_JSON,
        "transcript_txt": DEFAULT_TRANSCRIPT_TXT,
        "plan_json": DEFAULT_PLAN_JSON,
        "captures_dir": CAPTURES_DIR,
    }


def _screenshot_url_for_path(path, ctx=None):
    """Map a local PNG path to a URL the web layer can serve, or None."""
    if not path or not os.path.isfile(path):
        return None
    name = _basename(path)
    if not name:
        return None
    if ctx and ctx.get("workflow_dir"):
        wd = os.path.abspath(ctx["workflow_dir"])
        abs_path = os.path.abspath(path)
        cap = os.path.abspath(ctx.get("captures_dir") or CAPTURES_DIR)
        if abs_path.startswith(wd + os.sep) or abs_path.startswith(cap + os.sep):
            return f"/screenshots/{name}"
        return None
    norm = path.replace("\\", "/")
    if "/captures/" in norm or norm.startswith("captures/"):
        return f"/screenshots/{name}"
    under = os.path.join(CAPTURES_DIR, name)
    if os.path.isfile(under):
        return f"/screenshots/{name}"
    return None


def _load_plan_steps(plan_path):
    if not plan_path or not os.path.isfile(plan_path):
        return []
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _split_note_from_description(desc):
    """Split stored 'desc — note: xyz' back into description + editable_note."""
    desc = (desc or "").strip()
    m = _NOTE_SUFFIX_RE.search(desc)
    if not m:
        return desc, ""
    base = desc[: m.start()].strip()
    note = m.group(1).strip()
    return base, note


def _resolve_step_screenshot(plan_step, harness_step_dict=None, workflow_dir=None):
    """Find a saved click PNG for this step (plan field, then workflow db)."""
    if isinstance(plan_step, dict):
        shot = plan_step.get("screenshot")
        if shot and workflow_dir:
            from workflow_folder import normalize_screenshot_path
            path = normalize_screenshot_path(shot, workflow_dir)
            if path:
                return path
        elif shot and os.path.isfile(shot):
            return shot
        try:
            from transcribe import _resolve_screenshot
            path = _resolve_screenshot(plan_step, workflow_dir=workflow_dir)
            if path:
                return path
        except Exception:
            pass
    return None


def load_workflow_for_review(workflow_name=None,
                             transcript_path=None,
                             plan_path=None):
    """Return {workflow, steps} for the UI.

    Each step:
      {index, kind, description, screenshot_url, editable_note, deleted}
    """
    ctx = get_review_context(workflow_name)
    transcript_path = transcript_path or ctx["transcript_json"]
    plan_path = plan_path or ctx["plan_json"]
    workflow_dir = ctx.get("workflow_dir")

    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(
            f"no transcript at {transcript_path!r} — transcribe a recording first"
        )

    with open(transcript_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        step_dicts = payload
    elif isinstance(payload, dict) and isinstance(payload.get("steps"), list):
        step_dicts = payload["steps"]
    else:
        raise ValueError(f"{transcript_path} is not a harness transcript payload")

    plan_steps = _load_plan_steps(plan_path)
    out = []
    for i, sd in enumerate(step_dicts):
        if not isinstance(sd, dict):
            continue
        kind = (sd.get("kind") or "reason").strip().lower()
        if kind not in STEP_KINDS:
            kind = "reason"
        desc_raw = (sd.get("description") or "").strip()
        desc, note = _split_note_from_description(desc_raw)
        plan_step = plan_steps[i] if i < len(plan_steps) else None
        shot_path = _resolve_step_screenshot(
            plan_step, sd, workflow_dir=workflow_dir
        )
        out.append({
            "index": i + 1,
            "kind": kind,
            "description": desc,
            "screenshot_url": _screenshot_url_for_path(shot_path, ctx),
            "editable_note": note,
            "deleted": False,
        })

    return {
        "workflow": ctx.get("name"),
        "workflow_dir": workflow_dir,
        "captures_dir": ctx.get("captures_dir"),
        "steps": out,
    }


def save_workflow_edits(steps, workflow_name=None,
                        transcript_json=None, transcript_txt=None):
    """Write edited UI steps back to transcript.json / transcript.txt."""
    if not isinstance(steps, list):
        raise TypeError("steps must be a list")

    ctx = get_review_context(workflow_name)
    transcript_json = transcript_json or ctx["transcript_json"]
    transcript_txt = transcript_txt or ctx["transcript_txt"]

    inputs = []
    source = "(review)"
    if os.path.isfile(transcript_json):
        try:
            with open(transcript_json, "r", encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict):
                inputs = list(old.get("inputs") or [])
                source = old.get("source") or source
                old_steps = old.get("steps") or []
            else:
                old_steps = old if isinstance(old, list) else []
        except Exception:
            old_steps = []
    else:
        old_steps = []

    kept = []
    for item in steps:
        if not isinstance(item, dict):
            continue
        if item.get("deleted"):
            continue
        idx = item.get("index")
        try:
            idx0 = int(idx) - 1
        except (TypeError, ValueError):
            idx0 = len(kept)

        base = {}
        if 0 <= idx0 < len(old_steps) and isinstance(old_steps[idx0], dict):
            base = dict(old_steps[idx0])

        kind = (item.get("kind") or base.get("kind") or "reason").strip().lower()
        if kind not in STEP_KINDS:
            kind = "reason"
        desc = (item.get("description") or base.get("description") or "").strip()
        base_desc, _ = _split_note_from_description(desc)
        desc = base_desc
        note = (item.get("editable_note") or "").strip()
        if note and note not in desc:
            desc = f"{desc} — note: {note}" if desc else f"note: {note}"

        hs = HarnessStep(
            kind=kind,
            description=desc,
            goal=base.get("goal") if kind == "reason" else None,
            action=base.get("action"),
            target_name=base.get("target_name"),
            target_type=base.get("target_type"),
            inputs=list(base.get("inputs") or []),
        )
        if kind == "reason":
            hs.goal = desc or hs.goal or "continue the workflow"
        elif not hs.action and not hs.target_name:
            hs.kind = "reason"
            hs.goal = desc or "continue the workflow"
        try:
            hs.validate()
        except AssertionError:
            hs.kind = "reason"
            hs.goal = desc or "continue the workflow"
            hs.validate()
        kept.append(step_to_dict(hs))

    payload = {"source": source, "inputs": inputs, "steps": kept}
    with open(transcript_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    lines = [
        "# MimicAgent editable transcript",
        "# Edit freely. kind must be one of: " + ", ".join(STEP_KINDS),
        "# The harness router still confirms kind against the live screen at run time.",
        "#",
        "# INPUTS (fill these when running):",
    ]
    if inputs:
        for name in inputs:
            lines.append(f"#   - {{{name}}}")
    else:
        lines.append("#   (none detected)")
    lines.append("#")
    lines.append("")
    for i, sd in enumerate(kept, 1):
        lines.append(f"{i}. [{sd.get('kind')}] {sd.get('description')}")
    with open(transcript_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return {
        "ok": True,
        "steps": len(kept),
        "path": transcript_json,
        "workflow": ctx.get("name"),
    }


def save_workflow_step(step, workflow_name=None):
    """Save a single edited step; return refreshed step dict (incl. screenshot)."""
    if not isinstance(step, dict):
        raise TypeError("step must be a dict")
    idx = step.get("index")
    if idx is None:
        raise ValueError("step.index required")

    bundle = load_workflow_for_review(workflow_name=workflow_name)
    all_steps = bundle["steps"]
    merged = []
    found = False
    for s in all_steps:
        if s.get("index") == idx:
            merged.append({**s, **step, "index": idx})
            found = True
        else:
            merged.append(s)
    if not found:
        merged.append(step)

    save_workflow_edits(merged, workflow_name=workflow_name)
    refreshed = load_workflow_for_review(workflow_name=workflow_name)
    updated = next(
        (x for x in refreshed["steps"] if x.get("index") == idx), None
    )
    return {"ok": True, "step": updated, "workflow": refreshed.get("workflow")}


def run_reviewed_workflow(require_approval=True, workflow_name=None,
                          transcript_json=None, start_index=0):
    """Load the saved transcript and call run_harness (reuse). Keep safety floor."""
    ctx = get_review_context(workflow_name)
    transcript_json = transcript_json or ctx["transcript_json"]

    if not os.path.isfile(transcript_json):
        raise FileNotFoundError(f"no transcript at {transcript_json!r}")

    with open(transcript_json, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, dict):
        raw_steps = payload.get("steps") or []
        inputs_declared = list(payload.get("inputs") or [])
    elif isinstance(payload, list):
        raw_steps = payload
        inputs_declared = []
    else:
        raise ValueError("bad transcript payload")

    steps = []
    for sd in raw_steps:
        if not isinstance(sd, dict):
            continue
        hs = step_from_dict(sd)
        try:
            hs.validate()
        except AssertionError:
            if hs.kind != "reason":
                hs.kind = "reason"
                hs.goal = hs.description or "continue the workflow"
                hs.validate()
            else:
                raise
        steps.append(hs)

    if not steps:
        raise ValueError("transcript has no steps to run")

    inputs = {k: "" for k in inputs_declared}
    wf = ctx.get("name") or "(legacy)"
    print(f"[review] running harness for {wf!r} ({len(steps)} steps, "
          f"approval={'on' if require_approval else 'off'}, "
          f"start_index={start_index})...")
    transcript = run_harness(
        steps, inputs=inputs, require_approval=require_approval,
        start_index=start_index,
    )
    return transcript


if __name__ == "__main__":
    import sys
    wf = sys.argv[1] if len(sys.argv) > 1 else None
    print("=== review_backend: load_workflow_for_review ===")
    try:
        bundle = load_workflow_for_review(workflow_name=wf)
        steps = bundle["steps"]
    except Exception as e:
        print(f"FAILED: {e}")
        raise SystemExit(1)

    print(f"workflow: {bundle.get('workflow')!r}")
    with_shot = sum(1 for s in steps if s.get("screenshot_url"))
    print(f"steps: {len(steps)}  with screenshot: {with_shot}  "
          f"without: {len(steps) - with_shot}\n")
    for s in steps[:8]:
        flag = "SHOT" if s.get("screenshot_url") else "----"
        desc = (s.get("description") or "")[:60]
        print(f"  {s['index']:3}. [{s['kind']:7}] {flag}  {s.get('screenshot_url')}  {desc}")
    if len(steps) > 8:
        print(f"  ... and {len(steps) - 8} more")
    print("\nok")
