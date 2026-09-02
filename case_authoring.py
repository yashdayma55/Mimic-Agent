"""User-created step cases — capture or describe, then teach resolution."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from typing import Any

from case_halt_loop import (
    _copy_case_frame,
    build_trigger_from_halt,
    derive_case_success_check,
    resolution_from_anchor,
)
from step_cases import add_step_case, default_origin_note, list_step_cases, next_case_id
from success_signals import snapshot_structural_state
from teaching import (
    CASE_ORIGIN_USER_CAPTURED,
    CASE_ORIGIN_USER_DESCRIBED,
    MAX_CASES_PER_STEP,
    StepCase,
    TaughtStep,
    TaughtWorkflow,
    TeachingError,
    get_step,
    save_taught,
    sync_step_anchors,
)
from workflow_folder import workflow_dir

CAPTURE_PROMPT = (
    "Set your screen to the situation you want me to recognise, then press Capture. "
    "I'll grab what's on screen now."
)
DESCRIBE_PROMPT = (
    "Describe the situation in a sentence. I'll only be able to recognise it by looking, "
    "so this is less reliable than capturing it."
)
RESOLUTION_PROMPT = "Teach the resolution with Show me or Watch me, then I'll derive success from what changes."


def _ensure_case_room(step: TaughtStep) -> None:
    if len(list_step_cases(step)) >= MAX_CASES_PER_STEP:
        raise TeachingError(
            f"this step already has {MAX_CASES_PER_STEP} cases — remove one before adding another"
        )


def _snapshot_backup(step: TaughtStep) -> dict:
    return {
        "saved_anchors": [dict(a) for a in (step.anchors or []) if a],
        "saved_action": dict(step.action) if step.action else None,
    }


def _restore_step_capture_state(step: TaughtStep, auth: dict) -> None:
    step.anchors = [dict(a) for a in (auth.get("saved_anchors") or [])]
    sync_step_anchors(step)
    saved_action = auth.get("saved_action")
    step.action = dict(saved_action) if saved_action else step.action


def _get_authoring(step: TaughtStep) -> dict:
    auth = step.case_authoring
    if not auth:
        raise TeachingError("no case authoring in progress for this step")
    return auth


def build_trigger_from_structural(structural: dict) -> dict:
    st = dict(structural or {})
    return build_trigger_from_halt({
        "evidence": {
            "window_title": st.get("foreground_title") or st.get("window_title"),
            "a11y_elements": list(st.get("a11y_elements") or []),
            "browser_url": st.get("browser_url"),
        }
    })


def _save_capture_frame(
    wf_name: str,
    step_id: str,
    *,
    synthetic_bytes: bytes | None = None,
) -> str:
    rel = f"cases/user_{step_id}.png"
    abs_path = os.path.join(workflow_dir(wf_name), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    if synthetic_bytes is not None:
        with open(abs_path, "wb") as f:
            f.write(synthetic_bytes)
    else:
        from anchor_repair import capture_halt_screenshot

        tmp = capture_halt_screenshot(workflow_dir(wf_name), f"user_{step_id}")
        shutil.copyfile(tmp, abs_path)
    return rel


def authoring_status(step: TaughtStep) -> dict | None:
    auth = step.case_authoring
    if not auth:
        return None
    return {
        "mode": auth.get("mode"),
        "phase": auth.get("phase"),
        "created_from": auth.get("created_from"),
        "needs_resolution": auth.get("phase") == "needs_resolution",
        "capture_prompt": CAPTURE_PROMPT if auth.get("mode") == "capture" else None,
        "describe_prompt": DESCRIBE_PROMPT if auth.get("mode") == "describe" else None,
        "resolution_prompt": RESOLUTION_PROMPT if auth.get("phase") == "needs_resolution" else None,
        "description": (auth.get("trigger") or {}).get("description"),
    }


def start_user_case_capture(wf: TaughtWorkflow, step_id: str) -> dict:
    step = get_step(wf, step_id)
    if step.case_halt and not step.case_halt.get("resolution"):
        raise TeachingError("finish or cancel the halt before adding a case")
    if step.case_authoring:
        raise TeachingError("case authoring already in progress on this step")
    _ensure_case_room(step)
    step.case_authoring = {
        "mode": "capture",
        "phase": "awaiting_capture",
        "created_from": CASE_ORIGIN_USER_CAPTURED,
        **_snapshot_backup(step),
    }
    save_taught(wf)
    return {
        "ok": True,
        "message": CAPTURE_PROMPT,
        "countdown_sec": 3,
        "authoring": authoring_status(step),
    }


def capture_user_case_frame(
    wf: TaughtWorkflow,
    step_id: str,
    *,
    structural: dict | None = None,
    synthetic_bytes: bytes | None = None,
) -> dict:
    step = get_step(wf, step_id)
    auth = _get_authoring(step)
    if auth.get("mode") != "capture":
        raise TeachingError("case authoring is not in capture mode")
    if auth.get("phase") != "awaiting_capture":
        raise TeachingError("capture already completed for this case")
    st = dict(structural or snapshot_structural_state())
    frame = _save_capture_frame(wf.name, step_id, synthetic_bytes=synthetic_bytes)
    evidence = {
        "frame": frame,
        "window_title": st.get("foreground_title"),
        "a11y_elements": list(st.get("a11y_elements") or []),
        "browser_url": st.get("browser_url"),
        "at": st.get("at") or datetime.now(timezone.utc).isoformat(),
    }
    trigger = build_trigger_from_structural(st)
    auth.update(
        {
            "phase": "needs_resolution",
            "evidence": evidence,
            "trigger": trigger,
            "before_resolution": st,
        }
    )
    step.case_authoring = auth
    save_taught(wf)
    return {
        "ok": True,
        "evidence": evidence,
        "trigger": trigger,
        "needs_resolution": True,
        "message": RESOLUTION_PROMPT,
        "authoring": authoring_status(step),
    }


def start_user_case_describe(wf: TaughtWorkflow, step_id: str, description: str) -> dict:
    step = get_step(wf, step_id)
    desc = (description or "").strip()
    if not desc:
        raise TeachingError("describe the situation in a sentence")
    if step.case_halt and not step.case_halt.get("resolution"):
        raise TeachingError("finish or cancel the halt before adding a case")
    if step.case_authoring:
        raise TeachingError("case authoring already in progress on this step")
    _ensure_case_room(step)
    before = snapshot_structural_state()
    step.case_authoring = {
        "mode": "describe",
        "phase": "needs_resolution",
        "created_from": CASE_ORIGIN_USER_DESCRIBED,
        "trigger": {"description": desc},
        "evidence": {},
        "before_resolution": before,
        **_snapshot_backup(step),
    }
    save_taught(wf)
    return {
        "ok": True,
        "message": RESOLUTION_PROMPT,
        "reliability_warning": "recognised by description only — less reliable",
        "needs_resolution": True,
        "authoring": authoring_status(step),
    }


def cancel_user_case_authoring(wf: TaughtWorkflow, step_id: str) -> dict:
    step = get_step(wf, step_id)
    auth = step.case_authoring
    if not auth:
        return {"ok": True, "cancelled": False}
    frame = (auth.get("evidence") or {}).get("frame")
    step.case_authoring = None
    _restore_step_capture_state(step, auth)
    save_taught(wf)
    if frame:
        abs_path = os.path.join(workflow_dir(wf.name), frame)
        if os.path.isfile(abs_path) and frame.startswith(f"cases/user_{step_id}"):
            try:
                os.remove(abs_path)
            except OSError:
                pass
    return {"ok": True, "cancelled": True, "step": step.to_dict()}


def _build_user_case(step: TaughtStep, auth: dict, wf_name: str) -> StepCase:
    origin = auth.get("created_from")
    resolution = auth.get("resolution")
    if not resolution:
        raise TeachingError("cannot save a case without a taught resolution")
    case_id = next_case_id(step)
    evidence = dict(auth.get("evidence") or {})
    if origin == CASE_ORIGIN_USER_CAPTURED:
        evidence["frame"] = _copy_case_frame(wf_name, evidence.get("frame") or "", case_id)
    trigger = dict(auth.get("trigger") or {})
    halt_like = {
        "before_resolution": auth.get("before_resolution") or {},
        "after_resolution": auth.get("after_resolution") or {},
        "resolution": resolution,
    }
    return StepCase(
        id=case_id,
        created_from=origin,
        trigger=trigger,
        evidence=evidence,
        resolution=dict(resolution),
        success_check=derive_case_success_check(step, halt_like, wf_name),
        origin_note=default_origin_note(origin),
    )


def complete_user_case_resolution(
    wf: TaughtWorkflow,
    step_id: str,
    resolution: dict,
    *,
    after_structural: dict | None = None,
) -> dict:
    step = get_step(wf, step_id)
    auth = _get_authoring(step)
    if auth.get("phase") != "needs_resolution":
        raise TeachingError("case authoring is not waiting for a resolution")
    auth["resolution"] = dict(resolution)
    auth["after_resolution"] = dict(after_structural or snapshot_structural_state())
    case = _build_user_case(step, auth, wf.name)
    add_step_case(step, case)
    _restore_step_capture_state(step, auth)
    step.case_authoring = None
    save_taught(wf)
    return {"ok": True, "case": case.to_dict(), "step": step.to_dict()}


def try_complete_user_case_from_show(
    wf: TaughtWorkflow,
    step_id: str,
    capture_result: dict,
) -> dict | None:
    step = get_step(wf, step_id)
    auth = step.case_authoring
    if not auth or auth.get("phase") != "needs_resolution":
        return None
    if not capture_result.get("ok", True):
        return None
    anchor = None
    anchors = capture_result.get("anchors") or step.anchors or []
    if anchors:
        anchor = anchors[-1]
    if not anchor:
        return None
    resolution = resolution_from_anchor(
        anchor,
        window_title=(auth.get("evidence") or {}).get("window_title"),
    )
    after = snapshot_structural_state()
    return complete_user_case_resolution(wf, step_id, resolution, after_structural=after)


def try_complete_user_case_from_watch(
    wf: TaughtWorkflow,
    step_id: str,
    watch_result: dict,
) -> dict | None:
    step = get_step(wf, step_id)
    auth = step.case_authoring
    if not auth or auth.get("phase") != "needs_resolution":
        return None
    action = (watch_result or {}).get("action") or (watch_result or {}).get("resolution")
    if not action:
        learned = (watch_result or {}).get("learned") or {}
        summary = (learned.get("summary") or "").strip()
        if not summary:
            return None
        action = {"action": "click", "elem_name": summary[:80]}
    if not isinstance(action, dict) or not action.get("action"):
        return None
    after = snapshot_structural_state()
    return complete_user_case_resolution(wf, step_id, action, after_structural=after)
