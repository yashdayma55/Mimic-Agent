"""User-created step cases — capture or describe, then teach resolution."""

from __future__ import annotations

import copy
import os
import re
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
    "Describe when this case applies — what you see on screen and why it is different. "
    "I'll only be able to recognise it by looking, so capturing the screen is more reliable."
)
RESOLUTION_PROMPT = (
    "Teach this case as its own sub-step (Show me the Access email / blocker click), "
    "then Save as case. The main extract step stays as it is."
)

_STEP_SNAPSHOT_FIELDS = (
    "user_description",
    "varies_note",
    "qa_history",
    "click_count",
    "anchors",
    "anchor",
    "action",
    "status",
    "understanding",
    "learned",
    "last_capture",
    "chain_capture",
    "after_frame",
    "before_state",
    "success_candidates",
    "demo",
    "rehearsal",
    "reflection",
    "method",
    "prompt_instruction",
)


def case_authoring_active(step: TaughtStep) -> bool:
    return bool(step.case_authoring)


def case_authoring_resolving(step: TaughtStep) -> bool:
    auth = step.case_authoring
    if not auth:
        return False
    if auth.get("mode") in ("expandable", "reteach"):
        return True
    return bool(auth.get("phase") == "needs_resolution")


def _ensure_case_room(step: TaughtStep) -> None:
    if len(list_step_cases(step)) >= MAX_CASES_PER_STEP:
        raise TeachingError(
            f"this step already has {MAX_CASES_PER_STEP} cases — remove one before adding another"
        )


def _snapshot_backup(step: TaughtStep) -> dict:
    snap: dict[str, Any] = {}
    for name in _STEP_SNAPSHOT_FIELDS:
        val = getattr(step, name, None)
        if isinstance(val, (list, dict)):
            snap[name] = copy.deepcopy(val)
        else:
            snap[name] = val
    return {"step_snapshot": snap}


def _restore_step_capture_state(step: TaughtStep, auth: dict) -> None:
    snap = auth.get("step_snapshot")
    if snap:
        for name in _STEP_SNAPSHOT_FIELDS:
            if name not in snap:
                continue
            val = snap[name]
            if isinstance(val, (list, dict)):
                setattr(step, name, copy.deepcopy(val))
            else:
                setattr(step, name, val)
    else:
        step.anchors = [dict(a) for a in (auth.get("saved_anchors") or [])]
        saved_action = auth.get("saved_action")
        step.action = dict(saved_action) if saved_action else step.action
        step.click_count = int(auth.get("saved_click_count") or 1)
    sync_step_anchors(step)


def pending_case_label(step: TaughtStep) -> str:
    return f"Case {next_case_id(step).lstrip('c') or '1'}"


def _authoring_click_count(auth: dict) -> int:
    try:
        return max(1, min(2, int(auth.get("click_count") or 1)))
    except (TypeError, ValueError):
        return 1


def _apply_authoring_click_count(step: TaughtStep, auth: dict) -> None:
    from teaching import validate_click_count

    step.click_count = validate_click_count(_authoring_click_count(auth))


def _get_authoring(step: TaughtStep) -> dict:
    auth = step.case_authoring
    if not auth:
        raise TeachingError("no case authoring in progress for this step")
    return auth


_BLOCKER_NAME = re.compile(
    r"access\s*e-?mail|request\s*e-?mail|unlock|sign\s*in|verify|reveal\s*e-?mail|"
    r"log\s*in|continue with",
    re.I,
)


def _is_profile_specific_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    low = t.lower()
    if low.startswith("person:"):
        return True
    try:
        from success_signals import is_linkedin_chrome_title

        if is_linkedin_chrome_title(t):
            return True
    except Exception:
        pass
    return False


def sanitize_case_trigger(
    trigger: dict,
    *,
    structural: dict | None = None,
    situation_note: str = "",
) -> dict:
    """Keep situation/blocker cues; drop per-profile LinkedIn titles."""
    tr = dict(trigger or {})
    title = (tr.get("foreground_title") or "").strip()
    if _is_profile_specific_title(title):
        tr.pop("foreground_title", None)
    elems = list(tr.get("a11y_present") or [])
    if not elems and structural:
        elems = [
            e for e in (structural.get("a11y_elements") or [])
            if (e.get("name") or "").strip()
        ]
        if elems:
            tr["a11y_present"] = elems[:5]
    blockers = [e for e in elems if _BLOCKER_NAME.search((e.get("name") or ""))]
    if blockers:
        tr["a11y_present"] = blockers[:5]
    note = (situation_note or tr.get("description") or "").strip()
    if note:
        tr["description"] = note
    if not tr.get("foreground_title") and not tr.get("a11y_present") and not tr.get("browser_url"):
        if not tr.get("description"):
            tr["description"] = "Blocked extract — no email visible yet"
        tr["halt_signature"] = True
    return tr


def build_trigger_from_structural(structural: dict) -> dict:
    st = dict(structural or {})
    raw = build_trigger_from_halt({
        "evidence": {
            "window_title": st.get("foreground_title") or st.get("window_title"),
            "a11y_elements": list(st.get("a11y_elements") or []),
            "browser_url": st.get("browser_url"),
        }
    })
    return sanitize_case_trigger(raw, structural=st)


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
        "click_count": _authoring_click_count(auth),
        "case_label": auth.get("case_label") or pending_case_label(step),
        "needs_resolution": auth.get("phase") == "needs_resolution",
        "capture_prompt": CAPTURE_PROMPT if auth.get("mode") == "capture" else None,
        "describe_prompt": DESCRIBE_PROMPT if auth.get("mode") == "describe" else None,
        "resolution_prompt": RESOLUTION_PROMPT if auth.get("phase") == "needs_resolution" else None,
        "description": (auth.get("trigger") or {}).get("description"),
        "sub_description": (auth.get("sub_description") or "").strip(),
        "draft_ready": bool(auth.get("draft_resolution")),
        "draft_summary": (auth.get("draft_summary") or "").strip(),
    }


def start_user_case_capture(
    wf: TaughtWorkflow,
    step_id: str,
    *,
    click_count: int = 1,
    situation: str = "",
) -> dict:
    from teaching import validate_click_count

    step = get_step(wf, step_id)
    if step.case_halt and not step.case_halt.get("resolution"):
        raise TeachingError("finish or cancel the halt before adding a case")
    if step.case_authoring:
        raise TeachingError("case authoring already in progress on this step")
    _ensure_case_room(step)
    cc = validate_click_count(click_count)
    label = pending_case_label(step)
    step.case_authoring = {
        "mode": "capture",
        "phase": "awaiting_capture",
        "created_from": CASE_ORIGIN_USER_CAPTURED,
        "click_count": cc,
        "case_label": label,
        "situation_note": (situation or "").strip(),
        **_snapshot_backup(step),
    }
    save_taught(wf)
    return {
        "ok": True,
        "message": CAPTURE_PROMPT,
        "countdown_sec": 3,
        "case_label": label,
        "click_count": cc,
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
    trigger = sanitize_case_trigger(
        build_trigger_from_structural(st),
        structural=st,
        situation_note=(auth.get("situation_note") or "").strip(),
    )
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
    _apply_authoring_click_count(step, auth)
    save_taught(wf)
    return {
        "ok": True,
        "evidence": evidence,
        "trigger": trigger,
        "needs_resolution": True,
        "message": RESOLUTION_PROMPT,
        "case_label": auth.get("case_label"),
        "click_count": _authoring_click_count(auth),
        "authoring": authoring_status(step),
    }


def grab_user_case_screen(
    wf: TaughtWorkflow,
    step_id: str,
    *,
    structural: dict | None = None,
    synthetic_bytes: bytes | None = None,
) -> dict:
    """Capture the situation frame from the float bar (after countdown)."""
    return capture_user_case_frame(
        wf, step_id, structural=structural, synthetic_bytes=synthetic_bytes,
    )


def start_user_case_describe(
    wf: TaughtWorkflow,
    step_id: str,
    description: str,
    *,
    click_count: int = 1,
) -> dict:
    from teaching import validate_click_count

    step = get_step(wf, step_id)
    desc = (description or "").strip()
    if not desc:
        raise TeachingError("describe when this case applies")
    if step.case_halt and not step.case_halt.get("resolution"):
        raise TeachingError("finish or cancel the halt before adding a case")
    if step.case_authoring:
        raise TeachingError("case authoring already in progress on this step")
    _ensure_case_room(step)
    cc = validate_click_count(click_count)
    label = pending_case_label(step)
    before = snapshot_structural_state()
    step.case_authoring = {
        "mode": "describe",
        "phase": "needs_resolution",
        "created_from": CASE_ORIGIN_USER_DESCRIBED,
        "click_count": cc,
        "case_label": label,
        "trigger": {"description": desc},
        "evidence": {},
        "before_resolution": before,
        **_snapshot_backup(step),
    }
    _apply_authoring_click_count(step, step.case_authoring)
    save_taught(wf)
    return {
        "ok": True,
        "message": RESOLUTION_PROMPT,
        "reliability_warning": "recognised by description only — less reliable",
        "needs_resolution": True,
        "case_label": label,
        "click_count": cc,
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


def set_case_sub_description(wf: TaughtWorkflow, step_id: str, description: str) -> dict:
    """Name what this case's sub-step does, independently of the parent extract."""
    step = get_step(wf, step_id)
    auth = _get_authoring(step)
    text = (description or "").strip()
    auth["sub_description"] = text
    step.case_authoring = auth
    save_taught(wf)
    return {"ok": True, "sub_description": text, "authoring": authoring_status(step)}


def _draft_summary_from_resolution(resolution: dict, sub_description: str = "") -> str:
    action = (resolution.get("action") or "click").strip()
    name = (
        (sub_description or "").strip()
        or (resolution.get("elem_name") or "").strip()
        or (resolution.get("target_desc") or "").strip()
        or "taught target"
    )
    return f"{action} {name}".strip()


def _store_resolution_draft(step: TaughtStep, auth: dict, resolution: dict) -> None:
    auth["draft_resolution"] = dict(resolution)
    auth["draft_anchors"] = copy.deepcopy(list(step.anchors or []))
    auth["draft_summary"] = _draft_summary_from_resolution(
        resolution, auth.get("sub_description") or "",
    )
    step.case_authoring = auth


def _resolution_from_case_capture(
    step: TaughtStep,
    auth: dict,
    capture_result: dict | None = None,
) -> dict | None:
    _apply_authoring_click_count(step, auth)
    cc = _authoring_click_count(auth)
    if cc == 2:
        from teach_loop import _filled_anchors, resolve_action

        anchors = (capture_result or {}).get("anchors") or step.anchors or []
        if len(_filled_anchors(step)) >= 2 or len([a for a in anchors if a]) >= 2:
            resolution = resolve_action(step)
            if resolution and resolution.get("action"):
                return dict(resolution)
    anchor = None
    anchors = (capture_result or {}).get("anchors") or step.anchors or []
    if anchors:
        anchor = anchors[-1]
    if not anchor or not isinstance(anchor, dict):
        return None
    if not (anchor.get("primary") or anchor.get("point") or anchor.get("repaired_name")):
        cap_res = (capture_result or {}).get("resolution") or {}
        if cap_res.get("action"):
            return dict(cap_res)
        return None
    res = resolution_from_anchor(
        anchor,
        window_title=(auth.get("evidence") or {}).get("window_title"),
    )
    sub = (auth.get("sub_description") or "").strip()
    if sub:
        res["target_desc"] = sub
        if not res.get("elem_name"):
            res["elem_name"] = sub[:80]
    return res


def finish_user_case_from_capture(wf: TaughtWorkflow, step_id: str) -> dict:
    step = get_step(wf, step_id)
    auth = _get_authoring(step)
    if auth.get("phase") != "needs_resolution":
        raise TeachingError("case authoring is not waiting for a resolution")
    resolution = _resolution_from_case_capture(step, auth) or auth.get("draft_resolution")
    if not resolution:
        raise TeachingError("teach this case's sub-step with Show me first, then Save as case")
    after = snapshot_structural_state()
    return complete_user_case_resolution(wf, step_id, resolution, after_structural=after)


def float_authoring_state(step: TaughtStep) -> dict | None:
    auth = step.case_authoring
    if not auth:
        return None
    st = authoring_status(step) or {}
    phase = auth.get("phase")
    if auth.get("mode") in ("expandable", "reteach"):
        phase = "needs_resolution"
    return {
        "phase": phase,
        "mode": auth.get("mode"),
        "case_label": st.get("case_label") or pending_case_label(step),
        "click_count": st.get("click_count") or auth.get("click_count") or 1,
    }


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
    if auth.get("mode") in ("expandable", "reteach"):
        return None
    if not capture_result.get("ok", True):
        return None
    if capture_result.get("incomplete"):
        return None
    resolution = _resolution_from_case_capture(step, auth, capture_result)
    if not resolution:
        return None
    _store_resolution_draft(step, auth, resolution)
    save_taught(wf)
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
    if auth.get("mode") in ("expandable", "reteach"):
        return None
    resolution = _resolution_from_case_capture(step, auth, watch_result)
    if resolution:
        after = snapshot_structural_state()
        return complete_user_case_resolution(wf, step_id, resolution, after_structural=after)
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


def _build_user_case(step: TaughtStep, auth: dict, wf_name: str) -> StepCase:
    origin = auth.get("created_from")
    resolution = auth.get("resolution")
    if not resolution:
        raise TeachingError("cannot save a case without a taught resolution")
    case_id = next_case_id(step)
    evidence = dict(auth.get("evidence") or {})
    if origin == CASE_ORIGIN_USER_CAPTURED:
        evidence["frame"] = _copy_case_frame(wf_name, evidence.get("frame") or "", case_id)
    trigger = sanitize_case_trigger(
        dict(auth.get("trigger") or {}),
        structural=auth.get("before_resolution") or {},
        situation_note=(auth.get("sub_description") or auth.get("situation_note") or "").strip()
        or ((auth.get("trigger") or {}).get("description") or ""),
    )
    sub_desc = (auth.get("sub_description") or resolution.get("target_desc") or resolution.get("elem_name") or "").strip()
    sub_step = {
        "user_description": sub_desc,
        "click_count": _authoring_click_count(auth),
        "anchors": copy.deepcopy(auth.get("draft_anchors") or list(step.anchors or [])),
        "action": dict(resolution),
    }
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
        sub_step=sub_step,
    )
