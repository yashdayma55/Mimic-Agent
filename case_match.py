"""Conservative runtime matching for learned step cases."""

from __future__ import annotations

import os
from typing import Any, Callable

from step_cases import get_step_case, list_step_cases, record_case_match
from teaching import (
    CASE_ORIGIN_HALT,
    CASE_ORIGIN_USER_CAPTURED,
    CASE_ORIGIN_USER_DESCRIBED,
    StepCase,
    TaughtStep,
    TaughtWorkflow,
    TeachingError,
)

VisionFn = Callable[[bytes, str], dict]


def _elem_key(el: dict) -> tuple[str, str]:
    return (
        (el.get("name") or "").strip().lower(),
        (el.get("control_type") or "").strip().lower(),
    )


def _a11y_elem_present(structural: dict, wanted: dict) -> bool:
    want_name = (wanted.get("name") or "").strip().lower()
    want_type = (wanted.get("control_type") or "").strip().lower()
    for el in structural.get("a11y_elements") or []:
        name = (el.get("name") or "").strip().lower()
        ctype = (el.get("control_type") or "").strip().lower()
        if want_name and name != want_name:
            continue
        if want_type and ctype != want_type:
            continue
        if want_name or want_type:
            return True
    return False


def has_structural_trigger(trigger: dict | None) -> bool:
    tr = trigger or {}
    if (tr.get("foreground_title") or "").strip():
        return True
    if tr.get("a11y_present"):
        return True
    if (tr.get("browser_url") or "").strip():
        return True
    return False


def _origin_label(case: StepCase) -> str:
    return (case.created_from or CASE_ORIGIN_HALT).strip()


def _origin_has_evidence(case: StepCase) -> bool:
    return _origin_label(case) in (CASE_ORIGIN_HALT, CASE_ORIGIN_USER_CAPTURED)


def _format_match_log(case: StepCase, match: dict) -> str:
    origin = _origin_label(case)
    tier = match.get("tier")
    reason = (match.get("reason") or "").strip()
    if tier == 2:
        detail = f"vision, confidence {match.get('confidence')}"
    elif tier == "described":
        detail = reason or "description match"
    else:
        detail = reason or "structural trigger"
    tier_label = f"Tier {tier}" if tier != "described" else "description"
    return f"case {case.id} matched ({origin} · {tier_label}: {detail})"


def _attach_log(case: StepCase, match: dict) -> dict:
    out = dict(match)
    out["log"] = _format_match_log(case, out)
    return out


def _case_description(case: StepCase) -> str:
    sc = case.success_check or {}
    text = (sc.get("text") or sc.get("detail") or "").strip()
    if text:
        return text
    ev = case.evidence or {}
    title = (ev.get("window_title") or "").strip()
    if title:
        return title
    return f"case {case.id}"


def _tier1_case_match(case: StepCase, structural: dict) -> dict | None:
    trigger = case.trigger or {}
    if not has_structural_trigger(trigger):
        return None
    actual_title = (structural.get("foreground_title") or "").strip()
    expected_title = (trigger.get("foreground_title") or "").strip()
    if expected_title:
        from ui_runner import _titles_match

        if not _titles_match(expected_title, actual_title):
            return None
    expected_url = (trigger.get("browser_url") or "").strip()
    if expected_url:
        actual_url = (structural.get("browser_url") or "").strip()
        if expected_url != actual_url:
            return None
    for el in trigger.get("a11y_present") or []:
        if not _a11y_elem_present(structural, el):
            return None
    parts: list[str] = []
    if expected_title:
        parts.append(f"window title {expected_title!r}")
    for el in trigger.get("a11y_present") or []:
        name = el.get("name")
        if name:
            parts.append(f"a11y element {name!r}")
    if expected_url:
        parts.append(f"URL {expected_url!r}")
    reason = ", ".join(parts) or "structural trigger"
    return _attach_log(case, {
        "case_id": case.id,
        "case": case,
        "tier": 1,
        "confidence": "high",
        "reason": reason,
    })


def _read_case_frame(wf_name: str, case: StepCase) -> bytes | None:
    rel = (case.evidence or {}).get("frame") or ""
    if not rel:
        return None
    from workflow_folder import workflow_dir

    abs_path = rel if os.path.isabs(rel) else os.path.join(workflow_dir(wf_name), rel)
    if not os.path.isfile(abs_path):
        return None
    with open(abs_path, "rb") as f:
        return f.read()


def default_vision_match(image_bytes: bytes, description: str) -> dict:
    key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_key.txt")
    key = open(key_path, encoding="utf-8").read().strip() if os.path.isfile(key_path) else ""
    if not key:
        return {"matches": False, "confidence": "low"}
    from vision_api import ask_vision_with_prompt

    prompt = (
        f"Does this show {description}? "
        'Answer JSON only: {"matches": true|false, "confidence": "high|medium|low"}'
    )
    out = ask_vision_with_prompt(image_bytes, key, prompt)
    matches = out.get("matches")
    if matches is None and out.get("found") is not None:
        matches = bool(out.get("found"))
    conf = (out.get("confidence") or "low").strip().lower()
    if conf not in ("high", "medium", "low"):
        conf = "low"
    return {"matches": bool(matches), "confidence": conf}


def _tier2_case_match(
    case: StepCase,
    structural: dict,
    wf_name: str,
    vision_fn: VisionFn | None,
) -> dict | None:
    if has_structural_trigger(case.trigger):
        return None
    if vision_fn is None:
        return None
    image = _read_case_frame(wf_name, case)
    if not image:
        return None
    description = _case_description(case)
    vis = vision_fn(image, description)
    if not vis.get("matches"):
        return None
    conf = (vis.get("confidence") or "low").strip().lower()
    if conf not in ("high", "medium", "low"):
        conf = "low"
    return _attach_log(case, {
        "case_id": case.id,
        "case": case,
        "tier": 2,
        "confidence": conf,
        "reason": f"vision match for {description!r}",
    })


def _described_case_match(
    case: StepCase,
    live_screen: bytes | None,
    vision_fn: VisionFn | None,
) -> dict | None:
    if _origin_label(case) != CASE_ORIGIN_USER_DESCRIBED:
        return None
    description = ((case.trigger or {}).get("description") or "").strip()
    if not description:
        return None
    if vision_fn is None or not live_screen:
        return None
    vis = vision_fn(live_screen, description)
    if not vis.get("matches"):
        return None
    conf = (vis.get("confidence") or "low").strip().lower()
    if conf not in ("high", "medium", "low"):
        conf = "low"
    return _attach_log(case, {
        "case_id": case.id,
        "case": case,
        "tier": "described",
        "confidence": conf,
        "reason": f"description {description!r}",
    })


def _capture_live_screen(wf_name: str) -> bytes | None:
    try:
        from anchor_repair import capture_halt_screenshot
        from workflow_folder import workflow_dir

        tmp = capture_halt_screenshot(workflow_dir(wf_name), "_case_match")
        with open(tmp, "rb") as f:
            return f.read()
    except Exception:
        return None


def _choose_high_match(high: list[dict]) -> dict | None:
    if not high:
        return None
    if len(high) == 1:
        return high[0]
    evidenced = [c for c in high if _origin_has_evidence(c["case"])]
    if len(evidenced) == 1:
        return evidenced[0]
    return None


def evaluate_case_match(
    case: StepCase,
    structural: dict,
    wf_name: str,
    *,
    vision_fn: VisionFn | None = None,
    live_screen: bytes | None = None,
) -> dict | None:
    """Return a match record or None. Origin-aware: structural for halt/captured, vision for described."""
    origin = _origin_label(case)
    if origin == CASE_ORIGIN_USER_DESCRIBED:
        return _described_case_match(case, live_screen, vision_fn)
    tier1 = _tier1_case_match(case, structural)
    if tier1:
        return tier1
    return _tier2_case_match(case, structural, wf_name, vision_fn)


def decide_step_cases(
    step: TaughtStep,
    structural: dict,
    wf_name: str,
    *,
    vision_fn: VisionFn | None = None,
    live_screen: bytes | None = None,
) -> dict:
    """Decide whether to run a case, the normal path, or halt on ambiguity."""
    cases = list_step_cases(step)
    if not cases:
        return {"action": "normal", "log": "no cases on step", "candidates": []}

    screen = live_screen
    if screen is None and any(_origin_label(c) == CASE_ORIGIN_USER_DESCRIBED for c in cases):
        screen = _capture_live_screen(wf_name)

    candidates: list[dict] = []
    for case in cases:
        match = evaluate_case_match(
            case, structural, wf_name, vision_fn=vision_fn, live_screen=screen,
        )
        if match:
            candidates.append(match)

    high = [c for c in candidates if c.get("confidence") == "high"]
    chosen = _choose_high_match(high)
    if chosen:
        return {
            "action": "case",
            "case_id": chosen["case_id"],
            "case": chosen["case"],
            "tier": chosen["tier"],
            "log": chosen["log"],
            "candidates": candidates,
        }
    if len(high) > 1:
        ids = ", ".join(c["case_id"] for c in high)
        return {
            "action": "halt_ambiguous",
            "log": f"ambiguous: multiple Tier-1/high matches ({ids}) — halting",
            "candidates": candidates,
            "reason": "multiple_cases",
        }
    uncertain = [c for c in candidates if c.get("confidence") in ("medium", "low")]
    if uncertain:
        ids = ", ".join(c["case_id"] for c in uncertain)
        return {
            "action": "halt_ambiguous",
            "log": f"ambiguous: uncertain vision match ({ids}) — halting",
            "candidates": candidates,
            "reason": "uncertain_vision",
        }
    return {
        "action": "normal",
        "log": "no case matched — normal path",
        "candidates": [],
    }


def resolution_to_runner_step(step_id: str, resolution: dict) -> dict:
    action = (resolution.get("action") or "click").strip()
    keys = resolution.get("value") or resolution.get("keys") or ""
    return {
        "kind": "native",
        "action": action,
        "elem_name": resolution.get("elem_name"),
        "elem_type": resolution.get("elem_type"),
        "window_title": resolution.get("window_title"),
        "text": resolution.get("text") or (keys if action == "type" else ""),
        "keys": keys if action in ("hotkey", "press") else "",
        "instruction": f"case resolution for {step_id}",
        "extra": {
            "from_case": True,
            "point": resolution.get("point"),
        },
    }


def verify_case_success(
    case: StepCase,
    wf_name: str,
    *,
    before_demo: dict | None = None,
    after_demo: dict | None = None,
    os_input_calls: int = 0,
) -> dict:
    from success_signals import verify_success_check

    class _Shim:
        pass

    shim = _Shim()
    sc = case.success_check or {}
    shim.understanding = {
        "success_check": sc.get("text") or sc.get("detail") or "",
        "success_evidence": {"check": sc.get("check") or {}},
    }
    return verify_success_check(
        shim,
        wf_name,
        before_demo=before_demo,
        after_demo=after_demo,
        os_input_calls=os_input_calls,
    )


def tighten_case_trigger(case: StepCase, structural: dict) -> None:
    """Add structural evidence from a disambiguation answer."""
    trigger = dict(case.trigger or {})
    title = (structural.get("foreground_title") or "").strip()
    if title and not (trigger.get("foreground_title") or "").strip():
        trigger["foreground_title"] = title
    url = (structural.get("browser_url") or "").strip()
    if url and not (trigger.get("browser_url") or "").strip():
        trigger["browser_url"] = url
    present = list(trigger.get("a11y_present") or [])
    known = {_elem_key(e) for e in present}
    for el in structural.get("a11y_elements") or []:
        if not (el.get("name") or "").strip():
            continue
        key = _elem_key(el)
        if key not in known:
            present.append({"name": el.get("name"), "control_type": el.get("control_type")})
            known.add(key)
    if present:
        trigger["a11y_present"] = present[:5]
    trigger.pop("halt_signature", None)
    case.trigger = trigger


def plan_step_execution(
    wf: TaughtWorkflow,
    step: TaughtStep,
    test_values: dict | None,
    *,
    before_demo: dict | None = None,
    vision_fn: VisionFn | None = None,
    live_screen: bytes | None = None,
) -> dict:
    """Choose case vs normal execution before running a taught step."""
    from success_signals import snapshot_structural_state
    from teach_compile import step_to_node

    structural = before_demo or snapshot_structural_state()
    screen = live_screen
    if screen is None and any(
        _origin_label(c) == CASE_ORIGIN_USER_DESCRIBED for c in list_step_cases(step)
    ):
        screen = _capture_live_screen(wf.name)
    decision = decide_step_cases(
        step, structural, wf.name, vision_fn=vision_fn, live_screen=screen,
    )
    if decision["action"] == "case":
        case = decision["case"]
        return {
            "action": "case",
            "case": case,
            "runner_step": resolution_to_runner_step(step.id, case.resolution),
            "log": decision["log"],
            "decision": decision,
            "structural": structural,
        }
    if decision["action"] == "halt_ambiguous":
        return {
            "action": "halt_ambiguous",
            "log": decision["log"],
            "candidates": decision.get("candidates") or [],
            "reason": decision.get("reason"),
            "decision": decision,
            "structural": structural,
        }
    node = step_to_node(step, test_values)
    return {
        "action": "normal",
        "runner_step": node.to_runner_step(),
        "log": decision.get("log") or "normal path",
        "decision": decision,
        "structural": structural,
    }


def handle_case_disambiguation(
    wf: TaughtWorkflow,
    step_id: str,
    answer: str,
    *,
    structural: dict | None = None,
) -> dict:
    """Record which case applies and tighten its trigger with new evidence."""
    from success_signals import snapshot_structural_state
    from teaching import get_step, save_taught

    step = get_step(wf, step_id)
    case_id = (answer or "").strip().lower()
    if case_id in ("normal", "none", "none of these", "run normally"):
        if step.case_halt:
            step.case_halt = None
        save_taught(wf)
        return {"ok": True, "action": "normal", "step": step.to_dict()}
    try:
        case = get_step_case(step, case_id)
    except TeachingError as e:
        raise TeachingError(f"disambiguation answer must name a case id or 'normal': {e}") from e
    st = structural or snapshot_structural_state()
    tighten_case_trigger(case, st)
    record_case_match(case)
    step.case_halt = None
    save_taught(wf)
    return {
        "ok": True,
        "action": "case",
        "case_id": case.id,
        "log": f"user chose case {case.id}; trigger tightened",
        "step": step.to_dict(),
    }
