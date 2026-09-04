"""Cases as mini-steps — teachable like a normal step, then continue the parent."""

from __future__ import annotations

import copy
from typing import Any

from step_cases import (
    add_step_case,
    get_step_case,
    next_case_id,
    validate_step_case,
)
from teaching import (
    CASE_ORIGIN_USER_CAPTURED,
    CASE_ORIGIN_USER_DESCRIBED,
    StepCase,
    TaughtWorkflow,
    TeachingError,
    get_step,
    save_taught,
    sync_step_anchors,
    validate_click_count,
)

CASE_STATUSES = ("draft", "understood", "demonstrated", "approved")

ACCESS_EMAIL_PROMPT = "Click Access email in the Apollo Emails section"

_WRONG_BLOCKER_CLICK = (
    "wishlist", "add to cart", "add to list", "shopping", "cart",
)
_EMAIL_BLOCKER_HINTS = (
    "no email", "access email", "blocked extract", "not visible", "reveal email",
)


def empty_sub_step() -> dict:
    return {
        "user_description": "",
        "method": "anchor",
        "prompt_instruction": "",
        "click_count": 1,
        "anchors": [],
        "action": None,
        "understanding": None,
        "demo": None,
        "reflection": None,
        "memory_note": "",
        "status": "draft",
        "continue_with_parent": True,
        "continue_prompt": (
            "After this case succeeds, continue with the main step "
            "(e.g. extract the email)."
        ),
    }


def ensure_case_sub_step(case: StepCase) -> dict:
    sub = dict(case.sub_step or {})
    base = empty_sub_step()
    base.update({k: v for k, v in sub.items() if v is not None})
    if base.get("status") not in CASE_STATUSES:
        base["status"] = "draft"
    case.sub_step = base
    return base


def case_primary_target_name(case: StepCase) -> str:
    sub = ensure_case_sub_step(case)
    for anc in reversed(list(sub.get("anchors") or [])):
        name = ((anc or {}).get("primary") or {}).get("name") or ""
        if name.strip():
            return name.strip()
    res = case.resolution or {}
    return (
        (res.get("elem_name") or res.get("text") or (sub.get("action") or {}).get("elem_name") or "")
        .strip()
    )


def case_blocker_mismatch(case: StepCase) -> dict | None:
    """Detect Access-email blocker taught as cart/wishlist (or similar wrong click)."""
    sub = ensure_case_sub_step(case)
    if (sub.get("method") or "anchor").strip().lower() == "prompt":
        prompt = (sub.get("prompt_instruction") or "").lower()
        if "access email" in prompt or "reveal email" in prompt or "show email" in prompt:
            return None
    when = ((case.trigger or {}).get("description") or "").lower()
    what = (sub.get("user_description") or "").lower()
    target = case_primary_target_name(case).lower()
    blob = f"{when} {what}"
    looks_email_blocker = any(k in blob for k in _EMAIL_BLOCKER_HINTS)
    wrong = any(k in target for k in _WRONG_BLOCKER_CLICK) or any(
        k in what for k in _WRONG_BLOCKER_CLICK
    )
    # After switching to Access email description, still flag bad anchors if method is anchor.
    if (sub.get("method") or "anchor").strip().lower() == "prompt" and "access email" in what:
        return None
    if looks_email_blocker and wrong:
        return {
            "ok": False,
            "mismatch": True,
            "captured": case_primary_target_name(case) or (sub.get("user_description") or ""),
            "when": (case.trigger or {}).get("description") or "",
            "suggested_prompt": ACCESS_EMAIL_PROMPT,
            "message": (
                "This case is for a missing email / Access email screen, but the taught click "
                f"is {case_primary_target_name(case) or what!r}. "
                "Re-teach with Show me on Access email, or switch to the Access email prompt."
            ),
        }
    return None


def fix_case_access_email_prompt(
    wf: TaughtWorkflow,
    step_id: str,
    case_id: str,
    instruction: str = "",
) -> dict:
    """Repair a mismatched blocker case to a prompt that clicks Access email."""
    step = get_step(wf, step_id)
    case = get_step_case(step, case_id)
    sub = ensure_case_sub_step(case)
    text = (instruction or ACCESS_EMAIL_PROMPT).strip() or ACCESS_EMAIL_PROMPT
    when = ((case.trigger or {}).get("description") or "").strip()
    if not when or any(k in when.lower() for k in ("add to cart", "wishlist")):
        when = "Blocked extract — no email visible yet (Access email is showing)"
    tr = dict(case.trigger or {})
    tr["description"] = when
    tr["halt_signature"] = True
    case.trigger = tr
    sub["method"] = "prompt"
    sub["prompt_instruction"] = text
    sub["user_description"] = "Click Access email"
    sub["action"] = {"action": "prompt", "value": text, "target_desc": text[:80]}
    sub["demo"] = None
    # Keep old anchors for history but they are not used while method=prompt.
    case.resolution = dict(sub["action"])
    case.success_check = {
        "text": "Access email clicked — email address becomes available",
        "detail": "Access email clicked — email address becomes available",
        "check": {"type": "case_prompt", "expected": "access_email"},
    }
    case.sub_step = sub
    save_taught(wf)
    return {
        "ok": True,
        "fixed": True,
        "message": "Case switched to prompt: Click Access email",
        "case": case.to_dict(),
        "step": step.to_dict(),
    }


def case_continue_with_parent(case: StepCase) -> bool:
    sub = ensure_case_sub_step(case)
    return bool(sub.get("continue_with_parent", True))


def begin_expandable_case(
    wf: TaughtWorkflow,
    step_id: str,
    *,
    when_applies: str = "",
    what_to_do: str = "",
    continue_prompt: str = "",
    click_count: int = 1,
) -> dict:
    """Start drafting a case as an expandable mini-step on the parent card."""
    from case_authoring import _ensure_case_room, _snapshot_backup, pending_case_label

    step = get_step(wf, step_id)
    if step.case_halt and not step.case_halt.get("resolution"):
        raise TeachingError("finish or cancel the halt before adding a case")
    if step.case_authoring:
        raise TeachingError("case authoring already in progress on this step")
    _ensure_case_room(step)
    cc = validate_click_count(click_count)
    label = pending_case_label(step)
    when = (when_applies or "").strip()
    what = (what_to_do or "").strip()
    cont = (continue_prompt or "").strip() or empty_sub_step()["continue_prompt"]
    sub = empty_sub_step()
    sub.update({
        "user_description": what or "Handle the blocked / alternate screen",
        "click_count": cc,
        "continue_with_parent": True,
        "continue_prompt": cont,
        "status": "draft",
    })
    step.case_authoring = {
        "mode": "expandable",
        "phase": "editing",
        "created_from": CASE_ORIGIN_USER_DESCRIBED if when else CASE_ORIGIN_USER_CAPTURED,
        "click_count": cc,
        "case_label": label,
        "situation_note": when,
        "sub_description": what,
        "trigger": {"description": when} if when else {},
        "evidence": {},
        "sub_step": sub,
        "expanded": True,
        **_snapshot_backup(step),
    }
    save_taught(wf)
    return {
        "ok": True,
        "case_label": label,
        "expanded": True,
        "authoring": step.case_authoring,
        "step": step.to_dict(),
    }


def patch_case_draft(
    wf: TaughtWorkflow,
    step_id: str,
    *,
    when_applies: str | None = None,
    what_to_do: str | None = None,
    continue_prompt: str | None = None,
    continue_with_parent: bool | None = None,
    method: str | None = None,
    prompt_instruction: str | None = None,
    memory_note: str | None = None,
    click_count: int | None = None,
) -> dict:
    step = get_step(wf, step_id)
    auth = step.case_authoring
    if not auth:
        raise TeachingError("no case draft open — click Add a case first")
    sub = dict(auth.get("sub_step") or empty_sub_step())
    if when_applies is not None:
        text = when_applies.strip()
        auth["situation_note"] = text
        tr = dict(auth.get("trigger") or {})
        if text:
            tr["description"] = text
        else:
            tr.pop("description", None)
        auth["trigger"] = tr
    if what_to_do is not None:
        auth["sub_description"] = what_to_do.strip()
        sub["user_description"] = what_to_do.strip()
    if continue_prompt is not None:
        sub["continue_prompt"] = continue_prompt.strip()
    if continue_with_parent is not None:
        sub["continue_with_parent"] = bool(continue_with_parent)
    if method is not None:
        m = method.strip().lower()
        if m not in ("anchor", "prompt"):
            raise TeachingError("case method must be anchor or prompt")
        sub["method"] = m
    if prompt_instruction is not None:
        text = prompt_instruction.strip()
        sub["prompt_instruction"] = text
        if (sub.get("method") or "anchor") == "prompt" and text:
            sub["action"] = {
                "action": "prompt",
                "value": text,
                "target_desc": text[:80],
            }
    if memory_note is not None:
        sub["memory_note"] = memory_note.strip()
    if click_count is not None:
        cc = validate_click_count(click_count)
        sub["click_count"] = cc
        auth["click_count"] = cc
        step.click_count = cc
    auth["sub_step"] = sub
    step.case_authoring = auth
    save_taught(wf)
    return {"ok": True, "authoring": auth, "step": step.to_dict()}


def sync_draft_from_step_capture(wf: TaughtWorkflow, step_id: str) -> dict | None:
    """After Show me during expandable draft or reteach, copy anchors into the case."""
    step = get_step(wf, step_id)
    auth = step.case_authoring
    if not auth:
        return None
    mode = auth.get("mode")
    if mode == "reteach" and auth.get("case_id"):
        return sync_saved_case_from_capture(wf, step_id, auth["case_id"])
    if mode != "expandable":
        return None
    sub = dict(auth.get("sub_step") or empty_sub_step())
    sync_step_anchors(step)
    filled = [copy.deepcopy(a) for a in (step.anchors or []) if a]
    if not filled:
        return None
    sub["anchors"] = filled
    sub["method"] = "anchor"
    from teach_loop import resolve_action

    action = resolve_action(step)
    if action:
        sub["action"] = dict(action)
    else:
        primary = (filled[-1].get("primary") or {})
        sub["action"] = {
            "action": "click",
            "elem_name": primary.get("name"),
            "elem_type": primary.get("control_type"),
            "point": filled[-1].get("point"),
        }
    name = (sub.get("action") or {}).get("elem_name") or ""
    if name and not (sub.get("user_description") or "").strip():
        sub["user_description"] = f"Click {name}"
    elif name and (sub.get("user_description") or "").strip().lower() in (
        "add to cart",
        "handle the blocked / alternate screen",
    ):
        sub["user_description"] = f"Click {name}"
    auth["draft_resolution"] = dict(sub.get("action") or {})
    auth["draft_anchors"] = copy.deepcopy(filled)
    auth["draft_summary"] = (
        (sub.get("user_description") or "")
        or (sub.get("action") or {}).get("elem_name")
        or "taught click"
    )
    auth["sub_step"] = sub
    auth["phase"] = "editing"
    step.case_authoring = auth
    save_taught(wf)
    return {"ok": True, "draft_summary": auth.get("draft_summary"), "sub_step": sub}


def save_expandable_case(wf: TaughtWorkflow, step_id: str) -> dict:
    """Persist the expandable draft as a saved case (still editable)."""
    from case_authoring import _restore_step_capture_state, sanitize_case_trigger
    from step_cases import default_origin_note

    step = get_step(wf, step_id)
    auth = step.case_authoring
    if not auth or auth.get("mode") != "expandable":
        raise TeachingError("no expandable case draft to save")
    sub = dict(auth.get("sub_step") or empty_sub_step())
    action = sub.get("action") or auth.get("draft_resolution")
    if not action and (sub.get("method") or "") == "prompt":
        text = (sub.get("prompt_instruction") or "").strip()
        if text:
            action = {"action": "prompt", "value": text, "target_desc": text[:80]}
            sub["action"] = action
    if not action:
        raise TeachingError(
            "teach the case first — Show me a click, or set method to prompt with an instruction"
        )
    when = (
        auth.get("situation_note")
        or (auth.get("trigger") or {}).get("description")
        or ""
    ).strip()
    if not when:
        raise TeachingError("write when this case applies")
    trigger = sanitize_case_trigger(
        dict(auth.get("trigger") or {}),
        situation_note=when,
    )
    case_id = next_case_id(step)
    evidence = dict(auth.get("evidence") or {})
    origin = auth.get("created_from") or CASE_ORIGIN_USER_DESCRIBED
    if origin == CASE_ORIGIN_USER_CAPTURED and evidence.get("frame"):
        from case_halt_loop import _copy_case_frame

        evidence["frame"] = _copy_case_frame(wf.name, evidence.get("frame") or "", case_id)
    sub["status"] = sub.get("status") if sub.get("status") in CASE_STATUSES else "draft"
    sub["continue_with_parent"] = bool(sub.get("continue_with_parent", True))
    if not (sub.get("continue_prompt") or "").strip():
        sub["continue_prompt"] = empty_sub_step()["continue_prompt"]
    case = StepCase(
        id=case_id,
        created_from=origin,
        trigger=trigger,
        evidence=evidence,
        resolution=dict(action),
        success_check={
            "check": {"type": "user_text", "text": "case resolution succeeded"},
            "text": "case resolution succeeded",
        },
        origin_note=default_origin_note(origin),
        sub_step=sub,
    )
    validate_step_case(case)
    add_step_case(step, case)
    _restore_step_capture_state(step, auth)
    step.case_authoring = None
    save_taught(wf)
    return {"ok": True, "case": case.to_dict(), "step": step.to_dict()}


def patch_saved_case(
    wf: TaughtWorkflow,
    step_id: str,
    case_id: str,
    fields: dict,
) -> dict:
    step = get_step(wf, step_id)
    case = get_step_case(step, case_id)
    sub = ensure_case_sub_step(case)
    if "when_applies" in fields:
        when = (fields.get("when_applies") or "").strip()
        tr = dict(case.trigger or {})
        if when:
            tr["description"] = when
        if not tr.get("foreground_title") and not tr.get("a11y_present") and not tr.get("browser_url"):
            tr["halt_signature"] = True
        case.trigger = tr
    if "what_to_do" in fields:
        sub["user_description"] = (fields.get("what_to_do") or "").strip()
    if "continue_prompt" in fields:
        sub["continue_prompt"] = (fields.get("continue_prompt") or "").strip()
    if "continue_with_parent" in fields:
        sub["continue_with_parent"] = bool(fields.get("continue_with_parent"))
    if "method" in fields:
        m = (fields.get("method") or "anchor").strip().lower()
        if m not in ("anchor", "prompt"):
            raise TeachingError("case method must be anchor or prompt")
        sub["method"] = m
    if "prompt_instruction" in fields:
        text = (fields.get("prompt_instruction") or "").strip()
        sub["prompt_instruction"] = text
        if sub.get("method") == "prompt" and text:
            sub["action"] = {"action": "prompt", "value": text, "target_desc": text[:80]}
            case.resolution = dict(sub["action"])
    if "memory_note" in fields:
        sub["memory_note"] = (fields.get("memory_note") or "").strip()
    if "status" in fields:
        st = (fields.get("status") or "").strip()
        if st in CASE_STATUSES:
            sub["status"] = st
    case.sub_step = sub
    save_taught(wf)
    return {"ok": True, "case": case.to_dict(), "step": step.to_dict()}


def approve_case(wf: TaughtWorkflow, step_id: str, case_id: str) -> dict:
    step = get_step(wf, step_id)
    case = get_step_case(step, case_id)
    sub = ensure_case_sub_step(case)
    if not case.resolution and not sub.get("action"):
        raise TeachingError("case has no resolution to approve")
    sub["status"] = "approved"
    case.sub_step = sub
    save_taught(wf)
    return {"ok": True, "case": case.to_dict(), "step": step.to_dict()}


def case_to_runner_steps(case: StepCase, parent_step_id: str) -> list[dict]:
    from case_match import resolution_to_runner_step

    sub = ensure_case_sub_step(case)
    action = dict(sub.get("action") or case.resolution or {})
    method = (sub.get("method") or "anchor").strip().lower()
    if method == "prompt":
        text = (sub.get("prompt_instruction") or action.get("value") or "").strip()
        return [{
            "kind": "native",
            "action": "prompt",
            "value": text,
            "instruction": text,
            "prompt_instruction": text,
            "memory_note": sub.get("memory_note") or "",
            "id": f"{parent_step_id}:{case.id}",
            "from_case": case.id,
            "method": "prompt",
            "workflow_name": None,
        }]
    runner = resolution_to_runner_step(parent_step_id, action)
    anchors = list(sub.get("anchors") or [])
    if anchors:
        runner["anchors"] = anchors
        runner["extra"] = dict(runner.get("extra") or {})
        runner["extra"]["anchors"] = anchors
        runner["extra"]["point"] = (anchors[-1] or {}).get("point") or action.get("point")
    runner["from_case"] = case.id
    return [runner]


def sync_saved_case_from_capture(
    wf: TaughtWorkflow,
    step_id: str,
    case_id: str,
) -> dict:
    """Copy the latest Show me anchors from the parent step into a saved case."""
    from step_cases import get_step_case
    from teach_loop import resolve_action

    step = get_step(wf, step_id)
    case = get_step_case(step, case_id)
    sub = ensure_case_sub_step(case)
    sync_step_anchors(step)
    filled = [copy.deepcopy(a) for a in (step.anchors or []) if a]
    if not filled:
        raise TeachingError("no capture yet — Show me the target for this case first")
    sub["anchors"] = filled
    sub["method"] = "anchor"
    action = resolve_action(step)
    if action:
        sub["action"] = dict(action)
    else:
        primary = (filled[-1].get("primary") or {})
        sub["action"] = {
            "action": "click",
            "elem_name": primary.get("name"),
            "elem_type": primary.get("control_type"),
            "point": filled[-1].get("point"),
        }
    name = (sub["action"] or {}).get("elem_name") or ""
    if name and (
        not (sub.get("user_description") or "").strip()
        or (sub.get("user_description") or "").strip().lower() in ("add to cart", "handle the blocked / alternate screen")
    ):
        sub["user_description"] = f"Click {name}"
    case.resolution = dict(sub["action"])
    case.sub_step = sub
    # Restore parent step capture state if we have a snapshot from reteach
    reteach = step.case_authoring if (step.case_authoring or {}).get("mode") == "reteach" else None
    if reteach:
        from case_authoring import _restore_step_capture_state

        _restore_step_capture_state(step, reteach)
        step.case_authoring = None
    save_taught(wf)
    return {"ok": True, "case": case.to_dict(), "step": step.to_dict()}


def begin_case_reteach(wf: TaughtWorkflow, step_id: str, case_id: str) -> dict:
    """Arm Show me so the next capture updates this saved case."""
    from case_authoring import _snapshot_backup
    from step_cases import get_step_case

    step = get_step(wf, step_id)
    case = get_step_case(step, case_id)
    if step.case_authoring and step.case_authoring.get("mode") not in ("reteach",):
        raise TeachingError("finish or cancel the open case draft first")
    sub = ensure_case_sub_step(case)
    cc = validate_click_count(sub.get("click_count") or 1)
    step.case_authoring = {
        "mode": "reteach",
        "phase": "needs_resolution",
        "case_id": case_id,
        "click_count": cc,
        "case_label": f"Case {case_id}",
        "sub_step": sub,
        **_snapshot_backup(step),
    }
    step.click_count = cc
    step.anchors = []
    sync_step_anchors(step)
    save_taught(wf)
    return {
        "ok": True,
        "case_id": case_id,
        "click_count": cc,
        "message": "Show me the click for this case",
        "step": step.to_dict(),
    }


def try_case_prompt(wf: TaughtWorkflow, step_id: str, case_id: str, instruction: str = "") -> dict:
    """Trial-run a case prompt instruction (observational, like step prompt try)."""
    from step_cases import get_step_case

    step = get_step(wf, step_id)
    case = get_step_case(step, case_id)
    sub = ensure_case_sub_step(case)
    text = (instruction or sub.get("prompt_instruction") or sub.get("user_description") or "").strip()
    if not text:
        raise TeachingError("write a prompt instruction for this case first")
    sub["method"] = "prompt"
    sub["prompt_instruction"] = text
    sub["action"] = {"action": "prompt", "value": text, "target_desc": text[:80]}
    case.resolution = dict(sub["action"])
    case.sub_step = sub
    reflection = {
        "what_i_did": f"case prompt trial: {text[:200]}",
        "what_i_observed": f"would run prompt — {text[:120]}",
        "matches_understanding": None,
        "differences": [],
        "confidence_note": "case prompt trial — not a full demo",
    }
    sub["reflection"] = reflection
    case.sub_step = sub
    save_taught(wf)
    return {"ok": True, "reflection": reflection, "case": case.to_dict(), "step": step.to_dict()}


def mark_case_demo(case: StepCase, result: dict) -> None:
    sub = ensure_case_sub_step(case)
    sub["demo"] = dict(result)
    if result.get("ok") and sub.get("status") != "approved":
        sub["status"] = "demonstrated"
    case.sub_step = sub


def focus_case_target(wf: TaughtWorkflow, step_id: str, case_id: str) -> dict:
    """Bring the case's app window forward (LinkedIn / Apollo), same as Demo focus."""
    from teach_compile import focus_step_target, window_hint_from_step, target_window_hint
    from ui_runner import focus_window_by_hint

    step = get_step(wf, step_id)
    case = get_step_case(step, case_id)
    sub = ensure_case_sub_step(case)
    hints: list[str] = []
    for src in (
        (case.resolution or {}).get("window_title"),
        (sub.get("action") or {}).get("window_title"),
        (case.evidence or {}).get("window_title"),
    ):
        t = (src or "").strip()
        if t and t.lower() not in ("extensions",):
            hints.append(t)
    for anc in sub.get("anchors") or []:
        ss = (anc or {}).get("structural_state") or {}
        ft = (ss.get("foreground_title") or "").strip()
        if ft and ft.lower() not in ("extensions",):
            hints.append(ft)
    parent_hint = window_hint_from_step(step) or target_window_hint(step)
    if parent_hint:
        hints.append(parent_hint)
    hints.extend(["LinkedIn", "Google Chrome"])

    last = {"ok": False, "reason": "no target window for this case"}
    seen: set[str] = set()
    for hint in hints:
        key = hint.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        last = focus_window_by_hint(hint)
        if last.get("ok"):
            last["case_id"] = case_id
            return last
    # Last resort: parent step focus helper
    parent = focus_step_target(wf, step_id)
    if parent.get("ok"):
        parent["case_id"] = case_id
        return parent
    last["case_id"] = case_id
    return last


def demo_case(
    wf: TaughtWorkflow,
    step_id: str,
    case_id: str,
    *,
    continue_parent: bool | None = None,
) -> dict:
    """Run this case alone (like Demo on a step), optionally then continue the parent step."""
    import os_input
    from datetime import datetime, timezone

    from success_signals import snapshot_structural_state
    from teaching import save_taught
    from ui_runner import run_verified_plan

    step = get_step(wf, step_id)
    case = get_step_case(step, case_id)
    sub = ensure_case_sub_step(case)
    action = sub.get("action") or case.resolution
    method = (sub.get("method") or "anchor").strip().lower()
    if method == "prompt":
        text = (sub.get("prompt_instruction") or (action or {}).get("value") or "").strip()
        if not text:
            raise TeachingError("write a prompt instruction before demoing this case")
    elif not action:
        raise TeachingError("case has nothing to demo — Show me a click or set a prompt first")

    mismatch = case_blocker_mismatch(case)
    if mismatch and method != "prompt":
        raise TeachingError(
            mismatch["message"]
            + " Click “Fix: use Access email prompt” on the case card, then Demo again."
        )

    focus_result = focus_case_target(wf, step_id, case_id)
    before_demo = snapshot_structural_state()
    before_calls = os_input.call_count()
    runners = case_to_runner_steps(case, step.id)
    for rs in runners:
        rs["workflow_name"] = wf.name
    out = run_verified_plan(runners, halt_on_fail=True)
    cascaded = False
    do_cont = case_continue_with_parent(case) if continue_parent is None else bool(continue_parent)
    if out.get("ok") and do_cont:
        from teach_compile import step_to_node

        parent = step_to_node(step, None).to_runner_step()
        parent["workflow_name"] = wf.name
        if getattr(step, "produces", None):
            parent["produces"] = list(step.produces or [])
        if getattr(step, "prompt_instruction", None):
            parent["prompt_instruction"] = step.prompt_instruction
        parent_out = run_verified_plan([parent], halt_on_fail=True)
        cascaded = True
        if parent_out.get("ok"):
            out = dict(parent_out)
            out["case_ok"] = True
            out["cascaded"] = True
            out["reason"] = parent_out.get("reason") or "case then parent ok"
        else:
            out = dict(parent_out)
            out["case_ok"] = True
            out["cascaded"] = True
            out["ok"] = False
            out["reason"] = (
                "case ran, but parent step failed: "
                + str(parent_out.get("reason") or "")
            )

    snapshot_structural_state()  # settle UI after clicks
    ok = bool(out.get("ok"))
    reason = out.get("reason")
    observed = None
    if out.get("results"):
        reason = out["results"][-1].reason
        observed = out["results"][-1].value_after

    if ok:
        if cascaded:
            v = {
                "ok": True,
                "reason": reason or "case then parent ok",
                "cost": "demo",
            }
        else:
            v = {"ok": True, "reason": reason or "case runner ok", "cost": "demo"}
    else:
        v = {"ok": False, "reason": reason or "case demo failed", "cost": "demo"}

    result = {
        "ok": ok,
        "reason": reason,
        "observed": observed,
        "success_verify": v,
        "os_input_calls": os_input.call_count() - before_calls,
        "cascaded": cascaded,
        "continue_parent": do_cont,
        "case_id": case_id,
        "focus": focus_result,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if ok and observed and "@" in str(observed):
        result["produced"] = {"{recipient_email}": observed}
    mark_case_demo(case, result)
    if ok:
        from step_cases import record_case_match

        record_case_match(case)
    save_taught(wf)
    return {"ok": ok, "demo": result, "case": case.to_dict(), "step": step.to_dict()}
