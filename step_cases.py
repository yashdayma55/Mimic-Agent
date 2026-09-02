"""Learned step cases — max 3 per step, halt or user-authored."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
from typing import Any

from plan_schema import CLOSED_ACTIONS
from teaching import (
    CASE_ORIGIN_HALT,
    CASE_ORIGIN_USER_CAPTURED,
    CASE_ORIGIN_USER_DESCRIBED,
    CASE_ORIGINS,
    MAX_CASES_PER_STEP,
    StepCase,
    TaughtStep,
    TeachingError,
)

_CASE_FIELDS = {f.name for f in fields(StepCase)}


def _require_dict(value: Any, label: str) -> dict:
    if not isinstance(value, dict) or not value:
        raise TeachingError(f"case {label} must be a non-empty dict")
    return value


def _success_check_present(success_check: Any) -> dict:
    sc = _require_dict(success_check, "success_check")
    check = sc.get("check")
    text = (sc.get("text") or sc.get("detail") or "").strip()
    if isinstance(check, dict) and check.get("type"):
        return sc
    if text:
        return sc
    raise TeachingError("case success_check must include a structured check or text")


def _validate_resolution(resolution: Any) -> dict:
    res = _require_dict(resolution, "resolution")
    action = (res.get("action") or "").strip()
    if not action or action not in CLOSED_ACTIONS:
        raise TeachingError(
            f"case resolution action {action!r} is not in the closed vocabulary"
        )
    return res


def default_origin_note(created_from: str, *, at: datetime | None = None) -> str:
    origin = (created_from or "").strip()
    if origin == CASE_ORIGIN_HALT:
        dt = at or datetime.now(timezone.utc)
        return f"from a halt on {dt.strftime('%d %b')}"
    if origin == CASE_ORIGIN_USER_CAPTURED:
        return "you captured this"
    if origin == CASE_ORIGIN_USER_DESCRIBED:
        return "you described this"
    return "you added this"


def case_origin_badge(created_from: str) -> str:
    origin = (created_from or "").strip()
    if origin == CASE_ORIGIN_HALT:
        return "from a halt"
    if origin == CASE_ORIGIN_USER_CAPTURED:
        return "you captured this"
    if origin == CASE_ORIGIN_USER_DESCRIBED:
        return "you described this"
    return "case"


def _validate_origin(created_from: str) -> str:
    origin = (created_from or "").strip()
    if origin not in CASE_ORIGINS:
        raise TeachingError(
            f"case created_from must be one of {CASE_ORIGINS!r}, got {created_from!r}"
        )
    return origin


def _validate_evidence_for_origin(created_from: str, evidence: Any) -> dict:
    ev = dict(evidence or {})
    frame = (ev.get("frame") or "").strip()
    if created_from in (CASE_ORIGIN_HALT, CASE_ORIGIN_USER_CAPTURED):
        if not frame:
            raise TeachingError(
                f"case created_from {created_from!r} requires evidence.frame"
            )
        return ev
    if created_from == CASE_ORIGIN_USER_DESCRIBED:
        if frame:
            raise TeachingError("user_described case must not include evidence.frame")
        return ev
    return ev


def _validate_trigger_for_origin(created_from: str, trigger: Any) -> dict:
    tr = _require_dict(trigger, "trigger")
    if created_from == CASE_ORIGIN_USER_DESCRIBED:
        desc = (tr.get("description") or "").strip()
        if not desc:
            raise TeachingError("user_described case trigger must include description")
    return tr


def validate_step_case(case: StepCase | dict) -> StepCase:
    """Reject invalid cases before they are stored."""
    if isinstance(case, dict):
        if "success_check" not in case:
            raise TeachingError("case success_check must be a non-empty dict")
        case = step_case_from_dict(case)
    if not isinstance(case, StepCase):
        raise TeachingError("case must be a StepCase or dict")
    if not (case.id or "").strip():
        raise TeachingError("case id is required")
    origin = _validate_origin(case.created_from)
    case.created_from = origin
    case.trigger = _validate_trigger_for_origin(origin, case.trigger)
    case.evidence = _validate_evidence_for_origin(origin, case.evidence)
    _validate_resolution(case.resolution)
    _success_check_present(case.success_check)
    if not (case.origin_note or "").strip():
        case.origin_note = default_origin_note(origin)
    return case


def step_case_from_dict(d: dict) -> StepCase:
    payload = {k: v for k, v in (d or {}).items() if k in _CASE_FIELDS}
    if "evidence" not in payload:
        payload["evidence"] = {}
    return StepCase(**payload)


def next_case_id(step: TaughtStep) -> str:
    used = {c.id for c in _cases(step)}
    for n in range(1, MAX_CASES_PER_STEP + 1):
        cid = f"c{n}"
        if cid not in used:
            return cid
    raise TeachingError(f"step already has {MAX_CASES_PER_STEP} cases")


def _cases(step: TaughtStep) -> list[StepCase]:
    out: list[StepCase] = []
    for c in getattr(step, "cases", None) or []:
        if isinstance(c, StepCase):
            out.append(c)
        elif isinstance(c, dict):
            out.append(step_case_from_dict(c))
    return out


def add_step_case(step: TaughtStep, case: StepCase | dict) -> StepCase:
    """Append a validated case; refuse a 4th."""
    existing = _cases(step)
    if len(existing) >= MAX_CASES_PER_STEP:
        raise TeachingError(f"step already has {MAX_CASES_PER_STEP} cases")
    validated = validate_step_case(case)
    if any(c.id == validated.id for c in existing):
        raise TeachingError(f"case id {validated.id!r} already exists on this step")
    step.cases = existing + [validated]
    return validated


def remove_step_case(step: TaughtStep, case_id: str) -> StepCase | None:
    cases = _cases(step)
    kept: list[StepCase] = []
    removed: StepCase | None = None
    for c in cases:
        if c.id == case_id:
            removed = c
        else:
            kept.append(c)
    if removed is None:
        raise TeachingError(f"no case {case_id!r} on this step")
    step.cases = kept
    return removed


def remove_step_case_with_evidence(step: TaughtStep, case_id: str, wf_name: str) -> StepCase:
    """Remove a case and delete its evidence frame from disk."""
    import os

    from workflow_folder import workflow_dir

    removed = remove_step_case(step, case_id)
    rel = (removed.evidence or {}).get("frame") or ""
    if rel:
        abs_path = rel if os.path.isabs(rel) else os.path.join(workflow_dir(wf_name), rel)
        if os.path.isfile(abs_path):
            try:
                os.remove(abs_path)
            except OSError:
                pass
    return removed


def case_short_label(case: StepCase) -> str:
    trigger = case.trigger or {}
    desc = (trigger.get("description") or "").strip()
    if desc:
        return desc[:100]
    title = (trigger.get("foreground_title") or "").strip()
    if title:
        return f"a {title} screen appears"
    elems = trigger.get("a11y_present") or []
    if elems and (elems[0].get("name") or "").strip():
        return f"{elems[0]['name']!r} is on screen"
    sc = case.success_check or {}
    text = (sc.get("text") or sc.get("detail") or "").strip()
    if text:
        return text[:100]
    return f"case {case.id}"


def format_trigger_mono(trigger: dict | None) -> str:
    tr = trigger or {}
    parts: list[str] = []
    desc = (tr.get("description") or "").strip()
    if desc:
        parts.append(f"desc:{desc}")
    title = (tr.get("foreground_title") or "").strip()
    if title:
        parts.append(f"title:{title}")
    for el in tr.get("a11y_present") or []:
        name = (el.get("name") or "").strip()
        if name:
            ctype = (el.get("control_type") or "").strip()
            parts.append(f"a11y:{name}" + (f"/{ctype}" if ctype else ""))
    url = (tr.get("browser_url") or "").strip()
    if url:
        parts.append(f"url:{url}")
    if tr.get("halt_signature"):
        parts.append("vision-only")
    return " · ".join(parts) if parts else "—"


def format_case_stats(case: StepCase) -> str:
    n = int(case.times_matched or 0)
    if n <= 0:
        return "never matched yet"
    last = (case.last_matched or "").strip()
    if not last:
        return f"matched {n} time{'s' if n != 1 else ''}"
    try:
        then = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        days = max(0, int((datetime.now(timezone.utc) - then).total_seconds() // 86400))
        if days == 0:
            when = "today"
        elif days == 1:
            when = "1 day ago"
        else:
            when = f"{days} days ago"
    except Exception:
        when = "recently"
    return f"matched {n} time{'s' if n != 1 else ''} · last {when}"


def cases_assumption_text(cases: list[StepCase]) -> str | None:
    if not cases:
        return None
    if len(cases) == 1:
        return (
            "normally the usual path applies; I also know one case where "
            + case_short_label(cases[0])
        )
    return (
        f"normally the usual path applies; I also know {len(cases)} alternate cases for this step"
    )


def case_row_display(case: StepCase) -> dict:
    sc = case.success_check or {}
    success = (sc.get("text") or sc.get("detail") or "").strip()
    check = sc.get("check") or {}
    if not success and check.get("type"):
        success = str(check.get("expected") or check.get("text") or check.get("type"))
    return {
        "id": case.id,
        "label": case_short_label(case),
        "trigger_mono": format_trigger_mono(case.trigger),
        "success_check": success or "—",
        "stats": format_case_stats(case),
        "never_matched": int(case.times_matched or 0) <= 0,
        "frame": (case.evidence or {}).get("frame"),
        "created_from": case.created_from,
        "origin_badge": case_origin_badge(case.created_from),
        "origin_note": case.origin_note or default_origin_note(case.created_from),
        "halted_at_step": case.halted_at_step,
        "description_only": case.created_from == CASE_ORIGIN_USER_DESCRIBED,
        "reliability_warning": (
            "recognised by description only — less reliable"
            if case.created_from == CASE_ORIGIN_USER_DESCRIBED
            else None
        ),
    }


def list_step_cases(step: TaughtStep) -> list[StepCase]:
    return _cases(step)


def get_step_case(step: TaughtStep, case_id: str) -> StepCase:
    for c in _cases(step):
        if c.id == case_id:
            return c
    raise TeachingError(f"no case {case_id!r} on this step")


def record_case_match(case: StepCase) -> None:
    case.times_matched = int(case.times_matched or 0) + 1
    case.last_matched = datetime.now(timezone.utc).isoformat()

