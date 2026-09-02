"""Halt → resolve → remember loop for learned step cases."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from typing import Any

from step_cases import add_step_case, default_origin_note, list_step_cases, next_case_id
from teaching import MAX_CASES_PER_STEP
from success_signals import success_check_text, snapshot_structural_state
from teaching import (
    CASE_ORIGIN_HALT,
    StepCase,
    TaughtStep,
    TaughtWorkflow,
    TeachingError,
    get_step,
    save_taught,
    sync_step_anchors,
)
from workflow_folder import workflow_dir

REMEMBER_KIND = "case_remember"
REMEMBER_QUESTION = (
    "Should I remember this as a case for this step, so I do not stop here again?"
)
DISAMBIGUATE_KIND = "case_disambiguate"
ATTACH_KIND = "case_attach_step"


def describe_observed(structural: dict | None) -> str:
    st = structural or {}
    title = (st.get("foreground_title") or "").strip() or "an unknown window"
    elems = [e for e in (st.get("a11y_elements") or []) if (e.get("name") or "").strip()]
    if elems:
        names = ", ".join(repr(e["name"]) for e in elems[:3])
        return f"{title} (notably: {names})"
    return title


def expected_description(step: TaughtStep) -> str:
    und = step.understanding or {}
    return (
        (und.get("target") or "").strip()
        or (und.get("success_check") or "").strip()
        or (step.user_description or "").strip()
        or "the usual screen for this step"
    )


def format_halt_message(expected: str, observed: str) -> str:
    exp = (expected or "").strip() or "the usual screen for this step"
    obs = (observed or "").strip() or "something unexpected"
    return f"I expected {exp}. I see {obs} instead."


def _cases_dir(wf_name: str) -> str:
    path = os.path.join(workflow_dir(wf_name), "cases")
    os.makedirs(path, exist_ok=True)
    return path


def _save_halt_frame(wf_name: str, step_id: str, *, synthetic_bytes: bytes | None = None) -> str:
    rel = f"cases/halt_{step_id}.png"
    abs_path = os.path.join(workflow_dir(wf_name), rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    if synthetic_bytes is not None:
        with open(abs_path, "wb") as f:
            f.write(synthetic_bytes)
    else:
        from anchor_repair import capture_halt_screenshot

        tmp = capture_halt_screenshot(workflow_dir(wf_name), f"case_{step_id}")
        shutil.copyfile(tmp, abs_path)
    return rel


def _build_evidence(wf_name: str, step_id: str, structural: dict, *, synthetic_bytes: bytes | None = None) -> dict:
    frame = _save_halt_frame(wf_name, step_id, synthetic_bytes=synthetic_bytes)
    return {
        "frame": frame,
        "window_title": structural.get("foreground_title"),
        "a11y_elements": list(structural.get("a11y_elements") or []),
        "browser_url": structural.get("browser_url"),
        "at": structural.get("at") or datetime.now(timezone.utc).isoformat(),
    }


def record_step_halt(
    wf: TaughtWorkflow,
    step_id: str,
    *,
    reason: str,
    expected: str | None = None,
    observed: str | None = None,
    structural: dict | None = None,
    synthetic_frame: bytes | None = None,
) -> dict:
    """Stop. Capture one halt frame and structural evidence. No retry."""
    step = get_step(wf, step_id)
    if step.case_halt and not step.case_halt.get("resolution"):
        raise TeachingError("a halt is already in progress for this step")
    st = dict(structural or snapshot_structural_state())
    obs = (observed or "").strip() or describe_observed(st)
    exp = (expected or "").strip() or expected_description(step)
    evidence = _build_evidence(wf.name, step_id, st, synthetic_bytes=synthetic_frame)
    halt = {
        "reason": (reason or "").strip() or "step halted",
        "expected": exp,
        "observed": obs,
        "evidence": evidence,
        "resolution": None,
        "before_resolution": st,
        "after_resolution": None,
        "remember_asked": False,
        "saved_anchors": [dict(a) for a in (step.anchors or []) if a],
        "saved_action": dict(step.action) if step.action else None,
    }
    step.case_halt = halt
    save_taught(wf)
    return {
        "halt": halt,
        "message": format_halt_message(exp, obs),
        "needs_resolution": True,
    }


def resolution_from_anchor(anchor: dict, *, window_title: str | None = None) -> dict:
    primary = dict(anchor.get("primary") or {})
    return {
        "action": "click",
        "elem_name": primary.get("name") or anchor.get("repaired_name"),
        "elem_type": primary.get("control_type") or anchor.get("repaired_type"),
        "window_title": window_title or anchor.get("window_title"),
        "point": anchor.get("point"),
    }


def _restore_step_capture_state(step: TaughtStep, halt: dict) -> None:
    step.anchors = [dict(a) for a in (halt.get("saved_anchors") or [])]
    sync_step_anchors(step)
    saved_action = halt.get("saved_action")
    step.action = dict(saved_action) if saved_action else step.action


def _derive_after_resolution(halt: dict, wf_name: str, step_id: str) -> dict:
    after = snapshot_structural_state()
    halt["after_resolution"] = after
    halt["after_observed"] = describe_observed(after)
    return after


def build_trigger_from_halt(halt: dict) -> dict:
    ev = dict(halt.get("evidence") or {})
    trigger: dict[str, Any] = {}
    title = (ev.get("window_title") or "").strip()
    if title:
        trigger["foreground_title"] = title
    elems = [e for e in (ev.get("a11y_elements") or []) if (e.get("name") or "").strip()]
    if elems:
        trigger["a11y_present"] = elems[:5]
    url = (ev.get("browser_url") or "").strip()
    if url:
        trigger["browser_url"] = url
    if not trigger:
        trigger["halt_signature"] = True
    return trigger


def derive_case_success_check(step: TaughtStep, halt: dict, wf_name: str) -> dict:
    """Derive this case's own success check — structural first, same as step capture."""
    from success_signals import _tier1_signals

    before = halt.get("before_resolution") or {}
    after = halt.get("after_resolution") or {}
    signals = _tier1_signals(before, after)
    if signals:
        sig = signals[0]
        check = dict(sig.get("check") or {})
        if check.get("type"):
            return {
                "check": check,
                "text": success_check_text(sig),
                "detail": sig.get("detail"),
            }
    observed = halt.get("after_observed") or halt.get("observed") or "resolution succeeded"
    return {
        "check": {"type": "user_text", "text": observed},
        "text": observed,
    }


def _copy_case_frame(wf_name: str, src_rel: str, case_id: str) -> str:
    dst_rel = f"cases/{case_id}.png"
    src = os.path.join(workflow_dir(wf_name), src_rel)
    dst = os.path.join(workflow_dir(wf_name), dst_rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isfile(src):
        shutil.copyfile(src, dst)
    elif not os.path.isfile(dst):
        with open(dst, "wb") as f:
            f.write(b"png")
    return dst_rel


def build_case_from_halt(
    step: TaughtStep,
    halt: dict,
    wf_name: str,
    *,
    target_step: TaughtStep | None = None,
    halted_at_step: str | None = None,
) -> StepCase:
    resolution = halt.get("resolution")
    if not resolution:
        raise TeachingError("cannot remember a case without a taught resolution")
    host = target_step or step
    case_id = next_case_id(host)
    evidence = dict(halt.get("evidence") or {})
    evidence["frame"] = _copy_case_frame(wf_name, evidence.get("frame") or "", case_id)
    return StepCase(
        id=case_id,
        created_from=CASE_ORIGIN_HALT,
        trigger=build_trigger_from_halt(halt),
        evidence=evidence,
        resolution=dict(resolution),
        success_check=derive_case_success_check(step, halt, wf_name),
        origin_note=default_origin_note(CASE_ORIGIN_HALT),
        halted_at_step=halted_at_step,
    )


def _ask_remember_case(step: TaughtStep, halt: dict) -> None:
    if halt.get("remember_asked"):
        return
    for q in step.qa_history or []:
        if q.get("kind") == REMEMBER_KIND and not (q.get("a") or "").strip():
            halt["remember_asked"] = True
            return
    step.qa_history.append(
        {
            "kind": REMEMBER_KIND,
            "q": REMEMBER_QUESTION,
            "a": "",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    halt["remember_asked"] = True


def pending_remember_question(step: TaughtStep) -> dict | None:
    for q in reversed(step.qa_history or []):
        if q.get("kind") == REMEMBER_KIND and not (q.get("a") or "").strip():
            return q
    return None


def _step_display_label(wf: TaughtWorkflow, step_id: str) -> tuple[int, str]:
    for i, s in enumerate(sorted(wf.steps, key=lambda x: x.order), start=1):
        if s.id == step_id:
            return i, (s.user_description or "Untitled step").strip()
    raise TeachingError(f"no step {step_id!r}")


def attach_step_question(wf: TaughtWorkflow, halting_step_id: str) -> str:
    num, desc = _step_display_label(wf, halting_step_id)
    return f"Attach this case to step {num} ({desc}) — the step that stopped?"


def workflow_steps_for_attach(wf: TaughtWorkflow) -> list[dict]:
    rows: list[dict] = []
    for i, s in enumerate(sorted(wf.steps, key=lambda x: x.order), start=1):
        desc = (s.user_description or "Untitled step").strip()
        rows.append(
            {
                "id": s.id,
                "number": i,
                "description": desc,
                "case_count": len(list_step_cases(s)),
                "full": f"step {i} ({desc})",
            }
        )
    return rows


def _ensure_case_room(step: TaughtStep, wf: TaughtWorkflow) -> None:
    if len(list_step_cases(step)) >= MAX_CASES_PER_STEP:
        num, desc = _step_display_label(wf, step.id)
        raise TeachingError(
            f"step {num} ({desc}) already has {MAX_CASES_PER_STEP} cases — "
            "pick another step or remove one first"
        )


def _ask_attach_step(step: TaughtStep, halt: dict, wf: TaughtWorkflow, halting_step_id: str) -> str:
    if halt.get("attach_asked"):
        for q in step.qa_history or []:
            if q.get("kind") == ATTACH_KIND and not (q.get("a") or "").strip():
                return q.get("q") or ""
    question = attach_step_question(wf, halting_step_id)
    step.qa_history.append(
        {
            "kind": ATTACH_KIND,
            "q": question,
            "a": "",
            "halting_step_id": halting_step_id,
            "default_step_id": halting_step_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    halt["attach_asked"] = True
    halt["halting_step_id"] = halting_step_id
    return question


def pending_attach_question(step: TaughtStep) -> dict | None:
    for q in reversed(step.qa_history or []):
        if q.get("kind") == ATTACH_KIND and not (q.get("a") or "").strip():
            return q
    return None


def complete_halt_resolution(
    wf: TaughtWorkflow,
    step_id: str,
    resolution: dict,
    *,
    after_structural: dict | None = None,
) -> dict:
    """Store the taught fix, derive case success check, then ask to remember once."""
    step = get_step(wf, step_id)
    halt = step.case_halt
    if not halt:
        raise TeachingError("no halt in progress for this step")
    if halt.get("resolution"):
        raise TeachingError("halt resolution already recorded")
    halt["resolution"] = dict(resolution)
    if after_structural is not None:
        halt["after_resolution"] = dict(after_structural)
        halt["after_observed"] = describe_observed(after_structural)
    else:
        _derive_after_resolution(halt, wf.name, step_id)
    _restore_step_capture_state(step, halt)
    _ask_remember_case(step, halt)
    step.case_halt = halt
    save_taught(wf)
    return {
        "resolution": halt["resolution"],
        "remember_question": REMEMBER_QUESTION,
        "remember_kind": REMEMBER_KIND,
    }


def try_complete_halt_from_show(wf: TaughtWorkflow, step_id: str, capture_result: dict) -> dict | None:
    """After Show me during a halt: build resolution without keeping capture on the step."""
    step = get_step(wf, step_id)
    halt = step.case_halt
    if not halt or halt.get("resolution"):
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
        window_title=(halt.get("evidence") or {}).get("window_title"),
    )
    after = snapshot_structural_state()
    return complete_halt_resolution(wf, step_id, resolution, after_structural=after)


def answer_remember_case(wf: TaughtWorkflow, step_id: str, answer: str) -> dict:
    """Yes → ask which step to attach. No → discard halt."""
    step = get_step(wf, step_id)
    halt = step.case_halt
    if not halt:
        raise TeachingError("no halt awaiting remember answer")
    if not halt.get("resolution"):
        raise TeachingError("cannot remember a case before the halt is resolved")
    q = pending_remember_question(step)
    if not q:
        raise TeachingError("remember question was not asked")
    low = (answer or "").strip().lower()
    yes = low in ("yes", "y", "remember", "yes, remember it", "yes remember it")
    no = low in ("no", "n", "one-off", "one off", "no, one-off", "no one-off")
    if not yes and not no:
        raise TeachingError("answer must be yes (remember) or no (one-off)")
    q["a"] = answer.strip()
    if no:
        step.case_halt = None
        save_taught(wf)
        return {"remembered": False, "case": None, "step": step.to_dict()}
    halt["remember_confirmed"] = True
    attach_q = _ask_attach_step(step, halt, wf, step_id)
    step.case_halt = halt
    save_taught(wf)
    return {
        "remembered": None,
        "pending_attach": True,
        "attach_question": attach_q,
        "attach_kind": ATTACH_KIND,
        "halting_step_id": step_id,
        "default_step_id": step_id,
        "steps": workflow_steps_for_attach(wf),
        "step": step.to_dict(),
    }


def answer_attach_case_step(
    wf: TaughtWorkflow,
    halting_step_id: str,
    answer: str,
    *,
    target_step_id: str | None = None,
) -> dict:
    """Confirm or choose which step owns the remembered halt case."""
    halting = get_step(wf, halting_step_id)
    halt = halting.case_halt
    if not halt or not halt.get("remember_confirmed"):
        raise TeachingError("no remembered halt awaiting step attachment")
    if not halt.get("resolution"):
        raise TeachingError("cannot attach a case before the halt is resolved")
    q = pending_attach_question(halting)
    if not q:
        raise TeachingError("attach-step question was not asked")

    default_id = halt.get("halting_step_id") or halting_step_id
    chosen = (target_step_id or "").strip()
    low = (answer or "").strip().lower()
    if not chosen:
        if low in ("yes", "y", "yes, step", "confirm") or low.startswith("yes,"):
            chosen = default_id
        elif low.startswith("step "):
            token = low.replace("step ", "").strip().split()[0]
            if token.isdigit():
                num = int(token)
                for row in workflow_steps_for_attach(wf):
                    if row["number"] == num:
                        chosen = row["id"]
                        break
            else:
                chosen = token
        else:
            chosen = (answer or "").strip()

    if not chosen:
        raise TeachingError("choose a step to attach this case to")
    if chosen.isdigit():
        num = int(chosen)
        for row in workflow_steps_for_attach(wf):
            if row["number"] == num:
                chosen = row["id"]
                break

    target = get_step(wf, chosen)
    _ensure_case_room(target, wf)

    halted_at = None if target.id == halting_step_id else halting_step_id
    frame_before = (halt.get("evidence") or {}).get("frame")
    case = build_case_from_halt(
        halting,
        halt,
        wf.name,
        target_step=target,
        halted_at_step=halted_at,
    )
    add_step_case(target, case)
    q["a"] = answer.strip() or f"attach to {target.id}"
    if target_step_id:
        q["target_step_id"] = target_step_id
    halting.case_halt = None
    save_taught(wf)
    return {
        "remembered": True,
        "case": case.to_dict(),
        "attached_to": target.id,
        "halted_at_step": halted_at,
        "halt_frame": frame_before,
        "evidence_frame": case.evidence.get("frame"),
        "step": halting.to_dict(),
        "target_step": target.to_dict(),
    }


def record_case_ambiguity_halt(
    wf: TaughtWorkflow,
    step_id: str,
    candidates: list[dict],
    *,
    structural: dict | None = None,
    log: str = "",
) -> dict:
    """Halt when case matching is ambiguous — ask the user which situation this is."""
    step = get_step(wf, step_id)
    ids = [c.get("case_id") for c in candidates if c.get("case_id")]
    question = (
        "More than one case could apply, or matching was uncertain. "
        f"Which situation is this? Reply with a case id ({', '.join(ids)}) or 'normal' to run the step as usual."
    )
    halt = record_step_halt(
        wf,
        step_id,
        reason=log or "ambiguous case match",
        observed=describe_observed(structural),
        structural=structural,
    )
    step = get_step(wf, step_id)
    halt_state = dict(step.case_halt or {})
    halt_state["ambiguity"] = {
        "candidates": [{"case_id": c.get("case_id"), "log": c.get("log")} for c in candidates],
        "log": log,
    }
    step.case_halt = halt_state
    for q in step.qa_history or []:
        if q.get("kind") == DISAMBIGUATE_KIND and not (q.get("a") or "").strip():
            save_taught(wf)
            return {**halt, "disambiguate_question": q.get("q")}
    step.qa_history.append(
        {
            "kind": DISAMBIGUATE_KIND,
            "q": question,
            "a": "",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    save_taught(wf)
    return {**halt, "disambiguate_question": question}


def maybe_record_demo_halt(wf: TaughtWorkflow, step: TaughtStep, demo_result: dict) -> dict | None:
    """Record a halt when demo fails. Does not retry."""
    if demo_result.get("ok"):
        return None
    if step.case_halt and not step.case_halt.get("resolution"):
        return {
            "halt": step.case_halt,
            "message": format_halt_message(
                step.case_halt.get("expected") or "",
                step.case_halt.get("observed") or "",
            ),
            "needs_resolution": True,
        }
    reason = (demo_result.get("reason") or "demo failed").strip()
    observed = (demo_result.get("observed") or reason).strip()
    before = demo_result.get("success_verify") or {}
    if isinstance(before, dict) and before.get("actual"):
        observed = str(before.get("actual"))
    return record_step_halt(
        wf,
        step.id,
        reason=reason,
        observed=observed,
        structural=demo_result.get("before_demo"),
    )


def halt_status(step: TaughtStep) -> dict | None:
    halt = step.case_halt
    if not halt:
        return None
    out = {
        "message": format_halt_message(halt.get("expected") or "", halt.get("observed") or ""),
        "needs_resolution": not bool(halt.get("resolution")),
        "remember_pending": pending_remember_question(step) is not None,
    }
    if halt.get("resolution") and not out["remember_pending"]:
        out["awaiting_remember"] = False
    return out
