"""
Visual workflow review — DATA/LOGIC layer (the brain).

Pure Python: load steps + screenshots, save edits, run via harness.
Knows NOTHING about HTML/HTTP. The web/desktop face calls these functions only.
"""

import json
import os

from harness_schema import HarnessStep, step_from_dict, step_to_dict, STEP_KINDS
from harness import run_harness

DEFAULT_TRANSCRIPT_JSON = "transcript.json"
DEFAULT_TRANSCRIPT_TXT = "transcript.txt"
DEFAULT_PLAN_JSON = "plan.json"
CAPTURES_DIR = "captures"


def _basename(path):
    if not path:
        return None
    return os.path.basename(path.replace("\\", "/"))


def _screenshot_url_for_path(path):
    """Map a local PNG path to a URL the web layer can serve, or None."""
    if not path or not os.path.isfile(path):
        return None
    name = _basename(path)
    if not name:
        return None
    # Prefer captures/ files served as /screenshots/<name>
    norm = path.replace("\\", "/")
    if "/captures/" in norm or norm.startswith("captures/") or \
            os.path.dirname(os.path.abspath(path)) == os.path.abspath(CAPTURES_DIR):
        return f"/screenshots/{name}"
    # Still expose by basename if the file exists under captures with that name
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


def _resolve_step_screenshot(plan_step, harness_step_dict=None):
    """Find a saved click PNG for this step (plan field, then DB lookup)."""
    # 1) plan.json carries screenshot from distill
    if isinstance(plan_step, dict):
        shot = plan_step.get("screenshot")
        if shot and os.path.isfile(shot):
            return shot
        # 2) reuse transcribe's resolver (plan fields x/y + recording.db)
        try:
            from transcribe import _resolve_screenshot
            path = _resolve_screenshot(plan_step)
            if path:
                return path
        except Exception:
            pass
    # 3) harness step may not have coords; nothing else to try
    return None


def load_workflow_for_review(transcript_path=DEFAULT_TRANSCRIPT_JSON,
                             plan_path=DEFAULT_PLAN_JSON):
    """Return step dicts for the UI.

    Each item:
      {index, kind, description, screenshot_url, editable_note}
    """
    if not os.path.isfile(transcript_path):
        raise FileNotFoundError(
            f"no transcript at {transcript_path!r} — transcribe a recording first"
        )

    with open(transcript_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        step_dicts = payload
        inputs = []
    elif isinstance(payload, dict) and isinstance(payload.get("steps"), list):
        step_dicts = payload["steps"]
        inputs = list(payload.get("inputs") or [])
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
        desc = (sd.get("description") or "").strip()
        plan_step = plan_steps[i] if i < len(plan_steps) else None
        shot_path = _resolve_step_screenshot(plan_step, sd)
        out.append({
            "index": i + 1,  # 1-based for humans
            "kind": kind,
            "description": desc,
            "screenshot_url": _screenshot_url_for_path(shot_path),
            "screenshot_path": shot_path,  # brain-only helper; UI may ignore
            "editable_note": "",
            "deleted": False,
        })

    # Stash inputs on the list object? Return plain list; callers that need
    # inputs can read transcript.json. Attach as attribute for optional use.
    return out


def save_workflow_edits(steps, transcript_json=DEFAULT_TRANSCRIPT_JSON,
                        transcript_txt=DEFAULT_TRANSCRIPT_TXT):
    """Write edited UI steps back to transcript.json / transcript.txt.

    Dropped/deleted steps are removed. editable_note is appended onto
    description so run_harness / the reasoner sees the annotation.
    """
    if not isinstance(steps, list):
        raise TypeError("steps must be a list")

    # Preserve declared inputs from existing transcript if present
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

        # Start from original harness step if we can align by prior index
        base = {}
        if 0 <= idx0 < len(old_steps) and isinstance(old_steps[idx0], dict):
            base = dict(old_steps[idx0])

        kind = (item.get("kind") or base.get("kind") or "reason").strip().lower()
        if kind not in STEP_KINDS:
            kind = "reason"
        desc = (item.get("description") or base.get("description") or "").strip()
        note = (item.get("editable_note") or "").strip()
        if note:
            # Append so the agent sees the annotation at run time
            if note not in desc:
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
        else:
            # Ensure concrete steps remain valid
            if not hs.action and not hs.target_name:
                hs.kind = "reason"
                hs.goal = desc or "continue the workflow"
        try:
            hs.validate()
        except AssertionError:
            hs.kind = "reason"
            hs.goal = desc or "continue the workflow"
            hs.validate()
        kept.append(step_to_dict(hs))

    payload = {
        "source": source,
        "inputs": inputs,
        "steps": kept,
    }
    with open(transcript_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    # Matching transcript.txt (same format as transcribe / load_edited_transcript)
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

    return {"ok": True, "steps": len(kept), "path": transcript_json}


def run_reviewed_workflow(require_approval=True,
                          transcript_json=DEFAULT_TRANSCRIPT_JSON):
    """Load the saved transcript and call run_harness (reuse). Keep safety floor."""
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

    # Empty input values; harness fills {placeholders} with "" unless caller
    # supplies them later. Review UI v1 does not collect inputs.
    inputs = {k: "" for k in inputs_declared}
    print(f"[review] running harness ({len(steps)} steps, "
          f"approval={'on' if require_approval else 'off'})...")
    transcript = run_harness(
        steps, inputs=inputs, require_approval=require_approval
    )
    return transcript


if __name__ == "__main__":
    print("=== review_backend: load_workflow_for_review ===")
    try:
        steps = load_workflow_for_review()
    except Exception as e:
        print(f"FAILED: {e}")
        raise SystemExit(1)

    with_shot = sum(1 for s in steps if s.get("screenshot_url"))
    print(f"steps: {len(steps)}  with screenshot: {with_shot}  "
          f"without: {len(steps) - with_shot}\n")
    for s in steps[:15]:
        flag = "SHOT" if s.get("screenshot_url") else "----"
        desc = (s.get("description") or "")[:70]
        print(f"  {s['index']:3}. [{s['kind']:7}] {flag}  {desc}")
    if len(steps) > 15:
        print(f"  ... and {len(steps) - 15} more")
    print("\nok")
