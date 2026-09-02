"""Per-step teaching loop. The LLM (or heuristic) sees only THIS step."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from plan_schema import CLOSED_ACTIONS
from teaching import (
    TaughtStep,
    TaughtWorkflow,
    TeachingError,
    get_step,
    next_step_id,
    save_taught,
    sync_step_anchors,
    validate_click_count,
    validate_chain_action,
)

_TARGET_HINT = re.compile(
    r"\b(the\s+['\"][^'\"]+['\"]|the\s+[\w.]+(?:\s+[\w.]+){0,4}|['\"][^'\"]+['\"])",
    re.I,
)

CAPTURE_FLOW_KINDS = frozenset({"chain_second", "capture_prompt"})
MAX_QUESTIONS_PER_ROUND = 3


def is_capture_flow_kind(kind: str | None) -> bool:
    return (kind or "") in CAPTURE_FLOW_KINDS


def _pending_questions(step: TaughtStep) -> list:
    return [
        q for q in (step.qa_history or [])
        if q.get("q") and not (q.get("a") or "").strip()
        and not is_capture_flow_kind(q.get("kind"))
    ]


def _can_ask_question(step: TaughtStep) -> bool:
    return len(_pending_questions(step)) < MAX_QUESTIONS_PER_ROUND


def _set_capture_prompt(step: TaughtStep, message: str, phase: str = "chain") -> None:
    cc = dict(step.chain_capture or {})
    cc["prompt"] = (message or "").strip()
    cc["phase"] = phase
    step.chain_capture = cc


def set_context(wf: TaughtWorkflow, text: str) -> TaughtWorkflow:
    wf.context = (text or "").strip()
    save_taught(wf)
    return wf


def _dynamic_params(text: str, context: str = "") -> list[str]:
    blob = f"{text} {context}".lower()
    params: list[str] = []
    if "linkedin" in blob or re.search(r"\bprofile\b", blob):
        params.append("{linkedin_profile}")
    if re.search(r"\b(person|recipient|contact|candidate|lead)\b", blob) and "{linkedin_profile}" not in params:
        params.append("{person}")
    if "filename" in blob or "file name" in blob:
        params.append("{filename}")
    if "email" in blob and "{recipient_email}" not in params and "linkedin" not in blob:
        params.append("{recipient_email}")
    # de-dupe, keep order
    seen = set()
    out = []
    for p in params:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def explain_start(wf: TaughtWorkflow, description: str = "", varies_note: str = "") -> dict:
    """Mark the starting screen and what changes each run. No OS input."""
    desc = (description or "").strip() or ((wf.start_screen or {}).get("description") or "")
    varies = (varies_note or "").strip() or ((wf.start_screen or {}).get("varies_note") or "")
    params = _dynamic_params(desc + " " + varies, wf.context)
    if not params and re.search(r"\b(changes|each run|different each|keeps changing)\b", varies, re.I):
        params = ["{input}"]
    if params:
        summary = (
            f"Every run begins on this screen. What changes each time: {', '.join(params)}. "
            "I will wait until this screen is showing before step 1."
        )
    else:
        summary = (
            "Every run begins on this screen. Nothing here was marked as changing each run."
        )
    start = dict(wf.start_screen or {})
    start.update({
        "description": desc,
        "varies_note": varies,
        "parameters": params,
        "summary": summary,
    })
    wf.start_screen = start
    save_taught(wf)
    return start


def _witness_rank(name: str, w: dict) -> int:
    if not w or not w.get("saw"):
        return -1
    conf = {"high": 3, "medium": 2, "low": 1}.get(w.get("confidence") or "low", 1)
    pipe = {"a11y": 3, "vision": 2, "dom": 1}.get(name, 0)
    return conf * 10 + pipe


def _pick_primary(witnesses: dict) -> str | None:
    best, score = None, -1
    for k in ("a11y", "vision", "dom"):
        sc = _witness_rank(k, (witnesses or {}).get(k) or {})
        if sc > score:
            best, score = k, sc
    return best if score >= 0 else None


def _filled_anchors(step: TaughtStep) -> list:
    return [a for a in (step.anchors or []) if a]


def _capture_complete(step: TaughtStep) -> bool:
    cc = validate_click_count(getattr(step, "click_count", 1) or 1)
    return len(_filled_anchors(step)) >= cc


def _ask_success_confirm(step: TaughtStep, signal: dict) -> None:
    from success_signals import format_success_confirmation

    question = format_success_confirmation(signal)
    for q in step.qa_history:
        if q.get("kind") == "success_confirm" and not (q.get("a") or "").strip():
            return
    step.qa_history.append({
        "q": question,
        "a": "",
        "source": "show",
        "kind": "success_confirm",
        "signal": signal,
        "choices": ["yes", "partly", "no"],
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    step.status = "questioning"


def _ask_success_fallback(step: TaughtStep) -> None:
    question = "I could not see anything change after this step. What should I look for?"
    for q in step.qa_history:
        if q.get("kind") == "success_fallback" and not (q.get("a") or "").strip():
            return
    step.qa_history.append({
        "q": question,
        "a": "",
        "source": "show",
        "kind": "success_fallback",
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    step.status = "questioning"


def _maybe_finalize_step_capture(wf: TaughtWorkflow, step_id: str) -> dict | None:
    step = get_step(wf, step_id)
    if not _capture_complete(step):
        return None
    from success_signals import finalize_step_capture

    out = finalize_step_capture(wf, step_id)
    top = out.get("top_signal")
    if top:
        _ask_success_confirm(step, top)
    elif out.get("ok"):
        _ask_success_fallback(step)
    save_taught(wf)
    return out


def handle_success_confirm(wf: TaughtWorkflow, step_id: str, answer: str) -> TaughtStep:
    from success_signals import success_check_text

    step = get_step(wf, step_id)
    signal = None
    for q in reversed(step.qa_history):
        if q.get("kind") == "success_confirm" and not (q.get("a") or "").strip():
            q["a"] = answer
            signal = q.get("signal") or {}
            break
    low = (answer or "").strip().lower()
    understanding = dict(step.understanding or {})
    if low.startswith("yes") or low == "yes":
        understanding["success_check"] = success_check_text(signal or {})
        understanding["success_source"] = "derived"
        understanding["success_evidence"] = {
            "check": (signal or {}).get("check"),
            "evidence": (signal or {}).get("evidence"),
            "kind": (signal or {}).get("kind"),
        }
    else:
        user_text = answer.strip()
        if low.startswith("partly"):
            user_text = answer.split(":", 1)[-1].strip() if ":" in answer else answer.strip()
        understanding["success_check"] = user_text or answer.strip()
        understanding["success_source"] = "user"
        understanding["success_candidates"] = list(step.success_candidates or [])
    step.understanding = understanding
    save_taught(wf)
    return step


def handle_success_fallback(wf: TaughtWorkflow, step_id: str, answer: str) -> TaughtStep:
    step = get_step(wf, step_id)
    for q in reversed(step.qa_history):
        if q.get("kind") == "success_fallback" and not (q.get("a") or "").strip():
            q["a"] = answer
            break
    understanding = dict(step.understanding or {})
    understanding["success_check"] = (answer or "").strip()
    understanding["success_source"] = "user"
    understanding["success_candidates"] = list(step.success_candidates or [])
    step.understanding = understanding
    save_taught(wf)
    return step


def _anchor_name(anchor: dict | None) -> str:
    primary = (anchor or {}).get("primary") or {}
    return primary.get("name") or "unnamed"


def _anchor_type(anchor: dict | None) -> str:
    primary = (anchor or {}).get("primary") or {}
    return primary.get("control_type") or "element"


def apply_show_witnesses(
    wf: TaughtWorkflow,
    step_id: str,
    capture_out: dict | None = None,
    sub_index: int = 0,
    skip_show_confirm: bool = False,
    skip_confirm: bool | None = None,
) -> dict:
    """After Show me: apply role-based resolution — no witness voting."""
    if skip_confirm is not None:
        skip_show_confirm = bool(skip_confirm)
    step = get_step(wf, step_id)
    sync_step_anchors(step)
    while len(step.anchors) <= sub_index:
        step.anchors.append(None)
    anchor = dict((step.anchors[sub_index] if sub_index < len(step.anchors) else None) or {})
    mw = (capture_out or {}).get("witnesses") or {}
    resolution = (
        (capture_out or {}).get("resolution")
        or mw.get("resolution")
        or anchor.get("resolution")
        or {}
    )
    witnesses = resolution.get("witnesses") or mw.get("witnesses") or anchor.get("witnesses") or {}
    anchor["witnesses"] = witnesses
    anchor["resolution"] = resolution
    anchor["resolution_source"] = resolution.get("source") or anchor.get("resolution_source")
    anchor["resolution_reason"] = resolution.get("reason") or anchor.get("resolution_reason")
    anchor["resolution_line"] = resolution.get("resolution_line") or anchor.get("resolution_line")
    anchor["sub_index"] = sub_index
    anchor["conflict_unresolved"] = False

    confirmation = resolution.get("confirmation") or {}
    anchor["confirmed_by_vision"] = confirmation.get("confirmed_by_vision")
    anchor["vision_unconfirmed"] = confirmation.get("unconfirmed")
    anchor["vision_mismatch_pending"] = confirmation.get("vision_mismatch")
    if confirmation.get("diagnostics"):
        anchor["vision_confirm_diag"] = confirmation.get("diagnostics")

    primary = resolution.get("primary") or anchor.get("primary")
    if primary:
        anchor["primary"] = primary
        anchor["primary_reason"] = resolution.get("reason") or anchor.get("primary_reason")

    if not resolution.get("parent_target") and resolution.get("source") in ("a11y", "dom", "cursor"):
        pt = resolution.get("point") or anchor.get("point")
        if pt and len(pt) >= 2:
            from show_capture import check_parent_target

            parent = check_parent_target(int(pt[0]), int(pt[1]), primary or {})
            if parent:
                resolution["parent_target"] = parent
                anchor["resolution"] = resolution

    step.anchors[sub_index] = anchor
    sync_step_anchors(step)

    if resolution.get("parent_target") and _can_ask_question(step):
        pt = resolution["parent_target"]
        _ask_parent_target(step, pt.get("question") or "", pt, sub_index=sub_index)
    if confirmation.get("vision_mismatch") and not confirmation.get("confirmed_by_cursor") and _can_ask_question(step):
        _ask_vision_mismatch(step, confirmation.get("question") or "", sub_index=sub_index)
    elif not skip_show_confirm and not confirmation.get("vision_mismatch") and _can_ask_question(step):
        confirm = (capture_out or {}).get("confirm_question") or _show_confirm_question(anchor)
        if confirm and not confirmation.get("confirmed_by_vision"):
            _ask_show_confirm(step, confirm, sub_index=sub_index)

    save_taught(wf)
    out = dict(capture_out or {})
    out["anchor"] = anchor
    out["resolution"] = resolution
    out["resolution_line"] = anchor.get("resolution_line")
    out["sub_index"] = sub_index
    if confirmation.get("vision_mismatch"):
        out["question"] = confirmation.get("question")
        out["kind"] = "vision_mismatch"
    elif resolution.get("parent_target"):
        out["question"] = resolution["parent_target"].get("question")
        out["kind"] = "parent_target"
    return out


def _ask_vision_mismatch(step: TaughtStep, question: str, sub_index: int = 0) -> None:
    question = (question or "").strip()
    if not question:
        return
    for q in step.qa_history:
        if q.get("kind") == "vision_mismatch" and not (q.get("a") or "").strip():
            return
    step.status = "questioning"
    step.qa_history.append({
        "q": question,
        "a": "",
        "source": "witness",
        "kind": "vision_mismatch",
        "sub_index": sub_index,
        "choices": ["use this target", "show me again"],
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def _ask_parent_target(step: TaughtStep, question: str, parent_info: dict, sub_index: int = 0) -> None:
    question = (question or "").strip()
    if not question:
        return
    for q in step.qa_history:
        if q.get("kind") == "parent_target" and not (q.get("a") or "").strip():
            return
    step.status = "questioning"
    step.qa_history.append({
        "q": question,
        "a": "",
        "source": "witness",
        "kind": "parent_target",
        "sub_index": sub_index,
        "parent_info": parent_info,
        "choices": ["yes, click the parent", "no, use what I clicked"],
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def _show_confirm_question(anchor: dict) -> str:
    primary = (anchor or {}).get("primary") or {}
    name = primary.get("name") or "unnamed"
    ctype = primary.get("control_type") or "element"
    return f"I saw a {ctype} named {name!r} — is that the one?"


def _ask_show_confirm(step: TaughtStep, question: str, sub_index: int = 0) -> None:
    question = (question or "").strip()
    if not question:
        return
    for q in step.qa_history:
        if (
            q.get("kind") == "show_confirm"
            and not (q.get("a") or "").strip()
            and q.get("q") == question
            and q.get("sub_index", 0) == sub_index
        ):
            return
    step.status = "questioning"
    step.qa_history.append({
        "q": question,
        "a": "",
        "source": "show",
        "kind": "show_confirm",
        "sub_index": sub_index,
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def _ask_chain_second(step: TaughtStep, first_anchor: dict) -> None:
    name = _anchor_name(first_anchor)
    ctype = _anchor_type(first_anchor)
    message = (
        f"Got it — that was a {ctype} named {name!r}. Now show me the second click."
    )
    _set_capture_prompt(step, message, phase="second_click")


def _ask_chain_summary(step: TaughtStep) -> None:
    filled = _filled_anchors(step)
    if len(filled) < 2:
        return
    n1, n2 = _anchor_name(filled[0]), _anchor_name(filled[1])
    question = f"So: click {n1}, then click {n2}. Is that the whole step?"
    for q in step.qa_history:
        if q.get("kind") == "chain_summary" and not (q.get("a") or "").strip():
            return
    step.qa_history.append({
        "q": question,
        "a": "",
        "source": "show",
        "kind": "chain_summary",
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def _ask_one_click_instead(step: TaughtStep) -> None:
    question = "You declared 2 clicks but only showed one — did you mean one click after all?"
    for q in step.qa_history:
        if q.get("kind") == "chain_one_click" and not (q.get("a") or "").strip():
            return
    step.status = "questioning"
    step.qa_history.append({
        "q": question,
        "a": "",
        "source": "show",
        "kind": "chain_one_click",
        "choices": ["yes, set click_count to 1", "no, let me show the second click"],
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def _chain_capture_prompt(step: TaughtStep) -> str | None:
    cc = int(getattr(step, "click_count", 1) or 1)
    if cc != 2:
        return None
    chain_cap = step.chain_capture or {}
    if chain_cap.get("prompt"):
        return chain_cap.get("prompt")
    filled = len(_filled_anchors(step))
    if filled == 0:
        return "Show me the first click."
    if filled == 1:
        return "Now show me the second click."
    return None


def handle_vision_mismatch(wf: TaughtWorkflow, step_id: str, answer: str) -> TaughtStep:
    step = get_step(wf, step_id)
    text = (answer or "").strip().lower()
    sub_index = 0
    for q in reversed(step.qa_history):
        if q.get("kind") == "vision_mismatch" and not (q.get("a") or "").strip():
            sub_index = int(q.get("sub_index") or 0)
            q["a"] = answer
            break
    sync_step_anchors(step)
    anchor = dict((step.anchors[sub_index] if sub_index < len(step.anchors) else None) or {})
    use_it = text.startswith("use") or text in ("yes", "y", "yeah")
    show_again = "show" in text or text in ("no", "n", "again")
    if use_it and not show_again:
        anchor["vision_mismatch_pending"] = False
        anchor["vision_mismatch_accepted"] = True
    else:
        anchor["vision_mismatch_pending"] = False
        anchor["primary"] = None
        anchor["vision_mismatch_rejected"] = True
    while len(step.anchors) <= sub_index:
        step.anchors.append(None)
    step.anchors[sub_index] = anchor
    sync_step_anchors(step)
    save_taught(wf)
    return step


def handle_parent_target(wf: TaughtWorkflow, step_id: str, answer: str) -> TaughtStep:
    step = get_step(wf, step_id)
    text = (answer or "").strip().lower()
    sub_index = 0
    parent_info = {}
    for q in reversed(step.qa_history):
        if q.get("kind") == "parent_target" and not (q.get("a") or "").strip():
            sub_index = int(q.get("sub_index") or 0)
            parent_info = dict(q.get("parent_info") or {})
            q["a"] = answer
            break
    sync_step_anchors(step)
    anchor = dict((step.anchors[sub_index] if sub_index < len(step.anchors) else None) or {})
    yes = text.startswith("yes") or "parent" in text
    if yes:
        primary = dict(anchor.get("primary") or {})
        primary["name"] = parent_info.get("ancestor_name") or primary.get("name")
        primary["control_type"] = parent_info.get("ancestor_type") or primary.get("control_type")
        primary["pipeline"] = primary.get("pipeline") or "a11y"
        anchor["primary"] = primary
        anchor["primary_reason"] = (
            f"user confirmed clicking {parent_info.get('ancestor_type')} "
            f"instead of {parent_info.get('clicked_type')} {parent_info.get('clicked_name')!r}"
        )
        anchor["parent_target_resolved"] = True
    else:
        anchor["primary_reason"] = anchor.get("primary_reason") or "user kept the clicked element"
    while len(step.anchors) <= sub_index:
        step.anchors.append(None)
    step.anchors[sub_index] = anchor
    sync_step_anchors(step)
    cc = int(getattr(step, "click_count", 1) or 1)
    if yes and cc == 2 and sub_index == 0:
        _ask_chain_second(step, anchor)
    save_taught(wf)
    return step


def handle_show_confirm(wf: TaughtWorkflow, step_id: str, answer: str) -> TaughtStep:
    step = get_step(wf, step_id)
    text = (answer or "").strip()
    low = text.lower()
    sub_index = 0
    for q in reversed(step.qa_history):
        if q.get("kind") == "show_confirm" and not (q.get("a") or "").strip():
            sub_index = int(q.get("sub_index") or 0)
            q["a"] = text
            break
    sync_step_anchors(step)
    anchor = dict((step.anchors[sub_index] if sub_index < len(step.anchors) else None) or step.anchor or {})
    yes = low in ("yes", "y", "yeah", "yep", "correct", "right") or low.startswith("yes") or "that's the one" in low or "thats the one" in low
    no = low in ("no", "n", "nope", "wrong") or low.startswith("no") or "not the" in low
    if yes:
        anchor["confirmed"] = True
    elif no:
        anchor["confirmed"] = False
    else:
        anchor["confirmed_note"] = text
        if text and len(text) > 3:
            step.user_description = step.user_description or text
    while len(step.anchors) <= sub_index:
        step.anchors.append(None)
    step.anchors[sub_index] = anchor
    sync_step_anchors(step)
    cc = int(getattr(step, "click_count", 1) or 1)
    if yes and cc == 2:
        if sub_index == 0:
            _ask_chain_second(step, anchor)
        elif sub_index == 1:
            _ask_chain_summary(step)
    save_taught(wf)
    return step


def choose_witness(wf: TaughtWorkflow, step_id: str, choice: str) -> TaughtStep:
    step = get_step(wf, step_id)
    sync_step_anchors(step)
    sub_index = 0
    for q in reversed(step.qa_history):
        if q.get("kind") == "witness_conflict" and not (q.get("a") or "").strip():
            sub_index = int(q.get("sub_index") or 0)
            break
    anchor = dict((step.anchors[sub_index] if sub_index < len(step.anchors) else None) or step.anchor or {})
    witnesses = anchor.get("witnesses") or {}
    choice = (choice or "").strip().lower()
    if choice.startswith("neither") or choice == "again":
        anchor["conflict_unresolved"] = False
        anchor["primary"] = None
        while len(step.anchors) <= sub_index:
            step.anchors.append(None)
        step.anchors[sub_index] = anchor
        sync_step_anchors(step)
        for q in reversed(step.qa_history):
            if q.get("kind") == "witness_conflict" and not (q.get("a") or "").strip():
                q["a"] = "neither — show again"
                break
        save_taught(wf)
        return step
    key = choice
    for k in ("a11y", "vision", "dom"):
        if k in choice or choice in k:
            key = k
            break
    if key not in witnesses:
        raise TeachingError(f"unknown witness {choice!r}")
    chosen = dict(witnesses[key])
    chosen["pipeline"] = key
    others = [{**dict(witnesses[k]), "pipeline": k} for k in ("a11y", "vision", "dom") if k != key]
    saw_others = [w["pipeline"] for w in others if w.get("saw")]
    reason = f"user chose {key} over {', '.join(saw_others) or 'the others'} (they disagreed)"
    anchor["primary"] = chosen
    anchor["fallbacks"] = others
    anchor["primary_reason"] = reason
    anchor["conflict_unresolved"] = False
    if not anchor.get("parent_path"):
        anchor["parent_path"] = chosen.get("parent_path")
    while len(step.anchors) <= sub_index:
        step.anchors.append(None)
    step.anchors[sub_index] = anchor
    sync_step_anchors(step)
    for q in reversed(step.qa_history):
        if q.get("kind") == "witness_conflict" and not (q.get("a") or "").strip():
            q["a"] = key
            break
    confirm = _show_confirm_question(anchor)
    _ask_show_confirm(step, confirm, sub_index=sub_index)
    save_taught(wf)
    return step


def process_chain_batch(wf: TaughtWorkflow, step_id: str, session: dict) -> dict:
    """Apply witnesses for every capture in a batch chain session."""
    captures = session.get("captures") or []
    results = []
    finalize_out = None
    for i, cap in enumerate(captures):
        results.append(
            apply_show_witnesses(wf, step_id, cap, sub_index=i, skip_show_confirm=True)
        )
    step = get_step(wf, step_id)
    cc = int(getattr(step, "click_count", 1) or 1)
    got = len(_filled_anchors(step))
    if got >= cc:
        _ask_chain_summary(step)
        cc_state = dict(step.chain_capture or {})
        cc_state.pop("prompt", None)
        cc_state["phase"] = "complete"
        step.chain_capture = cc_state or None
        finalize_out = _maybe_finalize_step_capture(wf, step_id)
    elif got == 1 and cc == 2:
        heard = int(session.get("heard") or session.get("got") or 0)
        step.chain_capture = dict(step.chain_capture or {})
        step.chain_capture["phase"] = "incomplete"
        if heard >= 2:
            step.chain_capture["prompt"] = (
                "Both clicks were heard but only one anchor saved — press Show me (2) again."
            )
        else:
            _ask_one_click_instead(step)
            step.chain_capture["prompt"] = (
                "Only 1 of 2 clicks heard. After countdown, click Extensions then yellow Apollo "
                "within ~25 seconds — or press No, show second below to capture just click 2."
            )
    save_taught(wf)
    out = dict(session)
    out["witness_results"] = results
    out["anchors"] = step.anchors
    if got >= cc:
        n1, n2 = _anchor_name(step.anchors[0]), _anchor_name(step.anchors[1])
        out["summary"] = f"Captured both clicks: {n1}, then {n2}."
    if got >= cc and finalize_out:
        out["after_frame"] = finalize_out.get("after_frame")
        out["success_candidates"] = finalize_out.get("success_candidates")
    return out


def _record_capture(wf: TaughtWorkflow, step_id: str, mode: str, result: dict) -> dict:
    from capture_result import outcome_from_show, outcome_from_watch, set_capture_result

    if step_id in ("__start__", "start", "start_screen"):
        return result
    step = get_step(wf, step_id)
    if mode == "watch":
        outcome, msg = outcome_from_watch(result)
    else:
        outcome, msg = outcome_from_show(result)
    set_capture_result(step, mode=mode, outcome=outcome, message=msg, detail={"ok": result.get("ok")})
    result["outcome"] = outcome
    result["capture_message"] = msg
    result["last_capture"] = step.last_capture
    save_taught(wf)
    return result


def _record_show_capture(wf: TaughtWorkflow, step_id: str, result: dict) -> dict:
    out = _record_capture(wf, step_id, "show", result)
    try:
        from case_halt_loop import try_complete_halt_from_show

        halt_done = try_complete_halt_from_show(wf, step_id, out)
        if halt_done:
            out["case_halt_resolution"] = halt_done
    except Exception:
        pass
    return out


def answer_show(wf: TaughtWorkflow, step_id: str, **capture_kwargs) -> dict:
    if step_id in ("__start__", "start", "start_screen"):
        from show_capture import capture_start

        return capture_start(wf, **{k: v for k, v in capture_kwargs.items() if k in ("point", "countdown", "focus")})
    from show_capture import capture_chain_session, capture_show

    step = get_step(wf, step_id)
    cc = validate_click_count(getattr(step, "click_count", 1) or 1)
    inferred = None
    try:
        from show_capture import infer_click_count_from_description
        inferred = infer_click_count_from_description(step.user_description or "")
    except Exception:
        pass
    click_count_hint = None
    if inferred == 2 and cc == 1:
        click_count_hint = (
            "This step describes 2 clicks (Extensions, then Apollo). "
            "Set click count to 2 and press Show me (2) to capture both."
        )
        step.chain_capture = dict(step.chain_capture or {})
        step.chain_capture["prompt"] = click_count_hint
        step.chain_capture["phase"] = "click_count_hint"
        save_taught(wf)
    point = capture_kwargs.get("point")
    sequential = bool(capture_kwargs.get("sequential"))
    batch = capture_kwargs.get("batch")
    if batch is None:
        batch = cc == 2 and not sequential and point is None

    if cc == 2 and batch:
        window_sec = float(capture_kwargs.get("window_sec") or 25.0)
        countdown = float(capture_kwargs["countdown"]) if "countdown" in capture_kwargs else 1.6
        step.anchors = []
        sync_step_anchors(step)
        save_taught(wf)
        session = capture_chain_session(
            wf, step_id, click_count=cc, countdown=countdown, window_sec=window_sec,
        )
        result = process_chain_batch(wf, step_id, session)
        heard = int(session.get("heard") or 0)
        got = int(session.get("got") or 0)
        result["heard"] = heard
        result["got"] = got
        if heard < cc:
            result["incomplete"] = True
            result["chain_prompt"] = (
                f"Only {heard} of {cc} clicks heard — after countdown, click Extensions then Apollo "
                f"within ~{int(window_sec)} seconds."
            )
        elif len(_filled_anchors(get_step(wf, step_id))) < cc:
            result["incomplete"] = True
            result["chain_prompt"] = (
                f"Only {len(_filled_anchors(get_step(wf, step_id)))} of {cc} anchors saved — try Show me (2) again."
            )
        return _record_show_capture(wf, step_id, result)

    filled = _filled_anchors(step)
    if cc == 2 and len(filled) >= 2:
        note = "You declared 2 clicks — ignoring this extra click."
        step.chain_capture = dict(step.chain_capture or {})
        step.chain_capture["ignored_extra"] = int(step.chain_capture.get("ignored_extra") or 0) + 1
        step.chain_capture["last_note"] = note
        save_taught(wf)
        return _record_show_capture(wf, step_id, {
            "ok": True, "ignored": True, "note": note, "anchors": step.anchors,
        })
    sub_index = len(filled) if cc == 2 else 0
    if cc == 2 and sub_index == 0 and sequential:
        step.chain_capture = {"phase": "first", "prompt": "Show me the first click."}
        save_taught(wf)
    cap_kw = {
        k: v for k, v in capture_kwargs.items()
        if k in ("point", "countdown", "focus")
    }
    out = capture_show(wf, step_id, sub_index=sub_index, **cap_kw)
    result = apply_show_witnesses(wf, step_id, out, sub_index=sub_index)
    if _capture_complete(get_step(wf, step_id)):
        fin = _maybe_finalize_step_capture(wf, step_id)
        if fin:
            result["after_frame"] = fin.get("after_frame")
            result["success_candidates"] = fin.get("success_candidates")
    if click_count_hint:
        result["click_count_hint"] = click_count_hint
        result["chain_prompt"] = click_count_hint
    if cc == 2 and sequential:
        prompt = _chain_capture_prompt(get_step(wf, step_id))
        if prompt:
            result["chain_prompt"] = prompt
    result = _record_show_capture(wf, step_id, result)
    return result


def add_step(wf: TaughtWorkflow, description: str, varies_note: str = "") -> TaughtStep:
    step = TaughtStep(
        id=next_step_id(wf),
        order=len(wf.steps),
        user_description=(description or "").strip(),
        varies_note=(varies_note or "").strip(),
        status="draft",
        is_start=len(wf.steps) == 0,
    )
    if varies_note:
        params = re.findall(r"\{[A-Za-z_][\w]*\}", varies_note)
        step.parameters = params
    wf.steps.append(step)
    save_taught(wf)
    return step


def _drop_learned_shot(step: TaughtStep, wf: TaughtWorkflow) -> None:
    from workflow_folder import workflow_dir

    cur = dict(step.learned or {})
    shot = cur.pop("shot", None)
    step.learned = cur or None
    if shot:
        abs_path = os.path.join(workflow_dir(wf.name), shot)
        if os.path.isfile(abs_path):
            try:
                os.remove(abs_path)
            except Exception:
                pass
    if step.anchor and step.anchor.get("context_path") == shot:
        step.anchor.pop("context_path", None)
        step.anchor.pop("preview_path", None)
    sync_step_anchors(step)


_APPROVAL_STATUSES = ("understood", "demonstrated", "approved")
_RESET_MSG = "You changed the target, so this step needs approving again."


def _drop_photo_file(wf: TaughtWorkflow, rel: str) -> None:
    from workflow_folder import workflow_dir

    if not rel:
        return
    abs_path = os.path.join(workflow_dir(wf.name), rel)
    if os.path.isfile(abs_path):
        try:
            os.remove(abs_path)
        except Exception:
            pass


def _drop_anchor_at(step: TaughtStep, idx: int) -> bool:
    """Remove one anchor. Returns True if click_count / chain was reduced."""
    sync_step_anchors(step)
    anchors = [a for a in (step.anchors or []) if a]
    if idx < 0 or idx >= len(anchors):
        return False
    anchors.pop(idx)
    step.anchors = anchors
    reduced = False
    if int(getattr(step, "click_count", 1) or 1) == 2 and len(anchors) <= 1:
        step.click_count = 1
        reduced = True
    sync_step_anchors(step)
    return reduced


def _maybe_reset_approval(step: TaughtStep, *, material: bool) -> None:
    if not material:
        return
    if step.status not in _APPROVAL_STATUSES:
        return
    step.status = "questioning"
    step.demo = None
    step.reflection = None
    step.edit_notice = _RESET_MSG


def _clear_edit_notice(step: TaughtStep) -> None:
    step.edit_notice = None


def update_step(wf: TaughtWorkflow, step_id: str, description=None, varies_note=None,
                 memory_note=None, web_allowed=None, clear=None, understanding=None,
                 drop_photo=None, click_count=None, learned=None, drop_learned_shot=None,
                 qa_updates=None, anchor_edits=None, reflection=None,
                 drop_qa=None, drop_anchor_index=None, drop_sub_click: bool = False,
                 re_explain: bool = False) -> TaughtStep:
    """Edit or strip any part of a step at any time."""
    step = get_step(wf, step_id)
    material = False
    prior_desc = step.user_description
    prior_varies = step.varies_note
    prior_cc = int(getattr(step, "click_count", 1) or 1)
    if click_count is not None:
        old = prior_cc
        new_cc = validate_click_count(click_count)
        if new_cc != old:
            material = True
        step.click_count = new_cc
        if step.click_count == 1 and step.anchors:
            if len(_filled_anchors(step)) > 1:
                material = True
            step.anchors = step.anchors[:1]
        sync_step_anchors(step)
        if step.click_count == 2 and old == 1 and len(_filled_anchors(step)) == 1:
            _ask_chain_second(step, step.anchors[0])
    if description is not None:
        new_desc = (description or "").strip()
        if new_desc != (prior_desc or "").strip():
            material = True
        step.user_description = new_desc
    if varies_note is not None:
        new_var = (varies_note or "").strip()
        if new_var != (prior_varies or "").strip():
            material = True
        step.varies_note = new_var
        params = re.findall(r"\{[A-Za-z_][\w]*\}", step.varies_note)
        step.parameters = params
        from success_signals import normalize_profile_aware_success

        normalize_profile_aware_success(step)
    if memory_note is not None:
        step.memory_note = (memory_note or "").strip()
    if web_allowed is not None:
        step.web_allowed = bool(web_allowed)
    if understanding and isinstance(understanding, dict):
        cur = dict(step.understanding or {})
        for k, v in understanding.items():
            if cur.get(k) != v:
                if k in ("target", "action", "success_check", "plain_summary", "assumptions"):
                    material = True
            cur[k] = v
        if any(k in understanding for k in ("target", "action", "success_check", "plain_summary", "assumptions")):
            cur["user_edited"] = True
        step.understanding = cur
    if learned and isinstance(learned, dict):
        cur = dict(step.learned or {})
        for k, v in learned.items():
            if k == "summary" and v is not None:
                cur["summary"] = str(v).strip()
            elif k == "vision" and v is not None:
                cur["vision"] = str(v).strip()
            elif v is not None:
                cur[k] = v
        if learned.get("summary") is not None or learned.get("vision") is not None:
            cur["user_edited"] = True
            material = True
        step.learned = cur
    if drop_learned_shot:
        _drop_learned_shot(step, wf)
    if qa_updates and isinstance(qa_updates, list):
        for upd in qa_updates:
            qtext = (upd or {}).get("q") or ""
            ans = (upd or {}).get("a")
            if ans is None:
                continue
            for rec in step.qa_history:
                if (rec.get("q") or "") == qtext:
                    rec["a"] = str(ans).strip()
                    rec["user_edited"] = True
                    break
            else:
                if qtext:
                    step.qa_history.append({
                        "q": qtext,
                        "a": str(ans).strip(),
                        "source": "user",
                        "user_edited": True,
                    })
    if drop_qa:
        drop_set = {str(q) for q in (drop_qa if isinstance(drop_qa, list) else [drop_qa])}
        step.qa_history = [r for r in step.qa_history if (r.get("q") or "") not in drop_set]
    if anchor_edits and isinstance(anchor_edits, list):
        sync_step_anchors(step)
        for ed in anchor_edits:
            if not isinstance(ed, dict):
                continue
            idx = int(ed.get("sub_index") or 0)
            while len(step.anchors) <= idx:
                step.anchors.append(None)
            anc = dict(step.anchors[idx] or {})
            if ed.get("name") is not None:
                material = True
                primary = dict(anc.get("primary") or {})
                primary["name"] = str(ed.get("name") or "").strip() or None
                primary["user_edited"] = True
                anc["primary"] = primary
            if ed.get("parent_path") is not None:
                material = True
                anc["parent_path"] = str(ed.get("parent_path") or "").strip() or None
            if ed.get("user_note") is not None:
                anc["user_note"] = str(ed.get("user_note") or "").strip()
            for wit_key, pipe in (("wit_a11y", "a11y"), ("wit_dom", "dom"), ("wit_vision", "vision")):
                if ed.get(wit_key) is not None:
                    witnesses = dict(anc.get("witnesses") or {})
                    w = dict(witnesses.get(pipe) or {})
                    w["account"] = str(ed.get(wit_key) or "").strip()
                    w["user_edited"] = True
                    witnesses[pipe] = w
                    anc["witnesses"] = witnesses
            step.anchors[idx] = anc
        sync_step_anchors(step)
    if drop_sub_click:
        if _drop_anchor_at(step, 1):
            material = True
    if drop_anchor_index is not None:
        if _drop_anchor_at(step, int(drop_anchor_index)):
            material = True
    if reflection and isinstance(reflection, dict):
        cur = dict(step.reflection or {})
        for k, v in reflection.items():
            if v is not None:
                cur[k] = v
        cur["user_edited"] = True
        step.reflection = cur
    for key in (clear or []):
        if key == "learned":
            step.learned = None
        elif key == "learned_shot":
            _drop_learned_shot(step, wf)
        elif key == "anchor":
            material = True
            step.anchor = None
            step.anchors = []
        elif key == "understanding":
            material = True
            step.understanding = None
        elif key == "reflection":
            step.reflection = None
        elif key == "qa":
            step.qa_history = []
        elif key == "photos":
            for p in list(step.photos or []):
                _drop_photo_file(wf, p.get("path") or p)
            step.photos = []
        elif key == "notes":
            step.memory_note = ""
        elif key == "varies":
            material = True
            step.varies_note = ""
            step.parameters = []
    if drop_photo:
        _drop_photo_file(wf, drop_photo)
        step.photos = [p for p in (step.photos or []) if (p.get("path") or p) != drop_photo]
    sync_step_anchors(step)
    _maybe_reset_approval(step, material=material)
    if step.status == "approved" and not material:
        _clear_edit_notice(step)
    save_taught(wf)
    if re_explain:
        explain_understanding(wf, step_id, re_ask_only=True)
    return step


def delete_step(wf: TaughtWorkflow, step_id: str) -> TaughtWorkflow:
    wf.steps = [s for s in wf.steps if s.id != step_id]
    for i, s in enumerate(wf.steps):
        s.order = i
        s.is_start = i == 0
    save_taught(wf)
    return wf


def remove_case(wf: TaughtWorkflow, step_id: str, case_id: str) -> TaughtStep:
    """Delete a learned case and its evidence file. Does not disturb approval."""
    from step_cases import remove_step_case_with_evidence

    step = get_step(wf, step_id)
    remove_step_case_with_evidence(step, case_id, wf.name)
    save_taught(wf)
    return step


def attach_photo(wf: TaughtWorkflow, step_id: str, image_bytes: bytes, filename: str = "shot.png") -> dict:
    from workflow_folder import workflow_dir

    step = get_step(wf, step_id)
    folder = os.path.join(workflow_dir(wf.name), "anchors")
    os.makedirs(folder, exist_ok=True)
    n = len(step.photos or []) + 1
    ext = "png"
    low = (filename or "").lower()
    if low.endswith(".jpg") or low.endswith(".jpeg"):
        ext = "jpg"
    rel = os.path.join("anchors", f"{step.id}_user_{n}.{ext}")
    abs_path = os.path.join(workflow_dir(wf.name), rel)
    with open(abs_path, "wb") as f:
        f.write(image_bytes)
    rec = {"path": rel, "filename": filename, "ts": datetime.now(timezone.utc).isoformat()}
    step.photos = list(step.photos or []) + [rec]
    save_taught(wf)
    return {"ok": True, "photo": rec, "step": step.to_dict()}


def _approved_summaries(wf: TaughtWorkflow, before_id: str) -> list[str]:
    out = []
    for s in wf.steps:
        if s.id == before_id:
            break
        if s.status == "approved":
            act = (s.action or {}).get("action") or "?"
            out.append(f"{s.id} ({act}): {s.user_description[:80]}")
    return out


def _already_has_target(step: TaughtStep) -> bool:
    desc = step.user_description or ""
    return bool(_TARGET_HINT.search(desc)) or bool(
        re.search(r"\b(click|type|press|paste|copy|save|open|launch|hotkey|enter|write|select|navigate)\b.+\S", desc, re.I)
    )


def start_training(wf: TaughtWorkflow, step_id: str) -> list[str]:
    """Ask at most 3 short questions about THIS step only."""
    step = get_step(wf, step_id)
    questions: list[str] = []
    if not _already_has_target(step):
        questions.append("What exactly should I target?")
    if not step.varies_note and not step.parameters:
        questions.append("Does anything here change each run, or is it always the same?")
    questions = questions[:3]
    step.status = "questioning"
    existing = {(q.get("q") or "").strip() for q in step.qa_history}
    for q in questions:
        if q not in existing:
            step.qa_history.append({
                "q": q,
                "a": "",
                "source": "chat",
                "kind": "train",
                "ts": datetime.now(timezone.utc).isoformat(),
            })
    save_taught(wf)
    return questions


def answer_chat(wf: TaughtWorkflow, step_id: str, question: str, answer: str) -> TaughtStep:
    step = get_step(wf, step_id)
    pending_w = None
    pending_show = None
    pending_vision = None
    pending_parent = None
    pending_chain_one = None
    pending_chain_summary = None
    pending_success = None
    pending_success_fb = None
    pending_case_remember = None
    pending_case_attach = None
    pending_case_disambiguate = None
    for q in reversed(step.qa_history):
        if q.get("kind") == "vision_mismatch" and not (q.get("a") or "").strip():
            pending_vision = q
            break
        if q.get("kind") == "parent_target" and not (q.get("a") or "").strip():
            pending_parent = q
            break
        if q.get("kind") == "witness_conflict" and not (q.get("a") or "").strip():
            pending_w = q
            break
        if q.get("kind") == "show_confirm" and not (q.get("a") or "").strip() and pending_show is None:
            pending_show = q
        if q.get("kind") == "chain_one_click" and not (q.get("a") or "").strip() and pending_chain_one is None:
            pending_chain_one = q
        if q.get("kind") == "chain_summary" and not (q.get("a") or "").strip() and pending_chain_summary is None:
            pending_chain_summary = q
        if q.get("kind") == "success_confirm" and not (q.get("a") or "").strip() and pending_success is None:
            pending_success = q
        if q.get("kind") == "success_fallback" and not (q.get("a") or "").strip() and pending_success_fb is None:
            pending_success_fb = q
        if q.get("kind") == "case_remember" and not (q.get("a") or "").strip() and pending_case_remember is None:
            pending_case_remember = q
        if q.get("kind") == "case_attach_step" and not (q.get("a") or "").strip() and pending_case_attach is None:
            pending_case_attach = q
        if q.get("kind") == "case_disambiguate" and not (q.get("a") or "").strip() and pending_case_disambiguate is None:
            pending_case_disambiguate = q
    if pending_case_attach:
        from case_halt_loop import answer_attach_case_step

        pending_case_attach["a"] = answer
        answer_attach_case_step(wf, step_id, answer)
        return get_step(wf, step_id)
    if pending_case_disambiguate:
        from case_match import handle_case_disambiguation

        pending_case_disambiguate["a"] = answer
        halt = step.case_halt or {}
        handle_case_disambiguation(
            wf, step_id, answer, structural=halt.get("before_resolution"),
        )
        return get_step(wf, step_id)
    if pending_case_remember:
        from case_halt_loop import answer_remember_case

        answer_remember_case(wf, step_id, answer)
        return get_step(wf, step_id)
    if pending_success:
        return handle_success_confirm(wf, step_id, answer)
    if pending_success_fb:
        return handle_success_fallback(wf, step_id, answer)
    if pending_vision:
        return handle_vision_mismatch(wf, step_id, answer)
    if pending_parent:
        return handle_parent_target(wf, step_id, answer)
    if pending_w:
        return choose_witness(wf, step_id, answer)
    if pending_show and (
        not question or question == pending_show.get("q") or "is that the one" in (question or "").lower()
    ):
        return handle_show_confirm(wf, step_id, answer)
    if pending_chain_one and (
        not question or question == pending_chain_one.get("q")
    ):
        pending_chain_one["a"] = answer
        low = (answer or "").lower()
        if "yes" in low or "one click" in low:
            step.click_count = 1
            step.anchors = step.anchors[:1] if step.anchors else []
            sync_step_anchors(step)
        elif "no" in low or "second" in low:
            step.chain_capture = dict(step.chain_capture or {})
            step.chain_capture["phase"] = "second"
            step.chain_capture["prompt"] = "Now show me the second click (yellow Apollo icon)."
        save_taught(wf)
        return step
    if pending_chain_summary and (
        not question or question == pending_chain_summary.get("q")
    ):
        pending_chain_summary["a"] = answer
        save_taught(wf)
        return step
    step.qa_history.append({
        "q": question,
        "a": answer,
        "source": "chat",
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    low = (answer or "").lower()
    if any(w in low for w in ("varies", "changes", "each run", "parameter", "different")):
        brace = re.findall(r"\{[A-Za-z_][\w]*\}", answer or "")
        if brace:
            for b in brace:
                if b not in step.parameters:
                    step.parameters.append(b)
        elif "filename" in low and "{filename}" not in step.parameters:
            step.parameters.append("{filename}")
    save_taught(wf)
    return step


def followup(wf: TaughtWorkflow, step_id: str) -> list[str]:
    step = get_step(wf, step_id)
    if step.anchor:
        name = ((step.anchor.get("primary") or {}).get("name")) or "that element"
        return [f"I saw {name!r} — is that the one?"]
    return start_training(wf, step_id)


def _target_phrase(step: TaughtStep) -> str:
    desc = step.user_description or ""
    m = re.search(r"\bthe\s+(.+)$", desc, re.I)
    if m:
        return "the " + m.group(1).strip()
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", desc)
    if quoted:
        return quoted[0]
    return desc


def _closed_verb(text: str) -> str | None:
    """Map a natural-language description to a CLOSED_ACTIONS verb, or None."""
    blob = (text or "").lower()
    if re.search(r"\bpaste\b|\bctrl\s*\+\s*v\b", blob):
        return "paste"
    if re.search(r"\bcopy\b|\bctrl\s*\+\s*c\b", blob) and not re.search(r"\bcopy[_ ]?file\b", blob):
        return "copy"
    if re.search(r"\b(go\s*to|navigate|goto)\b", blob):
        return "navigate"
    if re.search(r"\b(launch|open)\b.*\b(notepad|chrome|edge|firefox|app)\b", blob) or re.search(
        r"\blaunch\b", blob
    ):
        return "launch_app"
    if re.search(r"https?://", blob) or re.search(r"\bopen[_ ]?url\b", blob):
        return "open_url"
    if re.search(r"\b(open[_ ]?path|open the file|open file)\b", blob) or (
        re.search(r"\bopen\b", blob) and "filename" in blob
    ):
        return "open_path"
    if re.search(r"\bsave\b|\bctrl\s*\+\s*s\b|\bhotkey\b", blob):
        return "hotkey"
    if re.search(r"\bpress\b", blob) and re.search(
        r"\b(enter|tab|esc|escape|ctrl|alt|shift|key)\b", blob
    ):
        return "press"
    if re.search(r"\b(type|enter|write)\b", blob):
        return "type"
    if re.search(r"\b(click|select)\b", blob):
        return "click"
    if re.search(r"\bpress\b", blob):
        return "click"
    if re.search(r"\bopen\b", blob):
        return "open_path"
    return None


def _step_blob(step: TaughtStep) -> str:
    return " ".join(
        [
            step.user_description or "",
            step.varies_note or "",
            " ".join(q.get("a") or "" for q in step.qa_history),
        ]
    )


def _plain_summary(verb, target, uses, success, assumptions) -> str:
    bits = []
    if verb and target:
        bits.append(f"I will {verb} {target}.")
    elif target:
        bits.append(f"The target is {target}, but I do not yet know the action.")
    elif verb:
        bits.append(f"I will {verb}.")
    else:
        bits.append("I do not yet know the action for this step.")
    if uses:
        bits.append(
            "This uses " + ", ".join(u["param"] for u in uses) + " from earlier steps."
        )
    else:
        bits.append("It does not use a value from an earlier step.")
    if success:
        bits.append(f"I will treat success as: {success}.")
    if assumptions:
        bits.append(f"I am assuming: {assumptions[0]}.")
    elif not success:
        bits.append("I still need a success check.")
    return " ".join(bits)


_REQUIRED_FILLED = ("target", "action", "success_check", "plain_summary")


def _chain_clicks_from_anchors(step: TaughtStep) -> list:
    clicks = []
    for anc in _filled_anchors(step)[:2]:
        primary = (anc or {}).get("primary") or {}
        clicks.append({
            "action": "click",
            "elem_name": primary.get("name"),
            "elem_type": primary.get("control_type"),
            "target_desc": primary.get("name") or "target",
            "window_title": "Notepad" if "notepad" in (step.user_description or "").lower() else None,
        })
    return clicks


def resolve_action(step: TaughtStep) -> dict | None:
    cc = int(getattr(step, "click_count", 1) or 1)
    if cc == 2 and len(_filled_anchors(step)) >= 2:
        clicks = _chain_clicks_from_anchors(step)
        if len(clicks) == 2:
            return {"action": "chain", "clicks": clicks, "click_count": 2}
    if step.action and step.action.get("action"):
        return step.action
    blob = " ".join(
        [
            step.user_description or "",
            step.varies_note or "",
            " ".join(q.get("a") or "" for q in step.qa_history),
        ]
    ).lower()
    target = _target_phrase(step)
    if re.search(r"\b(launch|open)\b.*\bnotepad\b", blob) or blob.strip() in ("open notepad", "launch notepad"):
        return {"action": "launch_app", "value": "notepad"}
    if re.search(r"\b(open|open_path|open the file)\b", blob) and (
        "{filename}" in (step.varies_note or "") + str(step.parameters) or "filename" in blob
    ):
        return {"action": "open_path", "value": "{filename}", "window_title": "Notepad"}
    if re.search(r"\b(ctrl\+s|hotkey|save)\b", blob) and "click" not in blob.split("save")[0][-12:]:
        if "save" in blob or "ctrl+s" in blob:
            act = {"action": "hotkey", "value": "ctrl+s", "keys": "ctrl+s", "window_title": "Notepad"}
            if any("filename" in str(p) for p in (step.parameters or [])) or "filename" in (step.varies_note or ""):
                act["verify_file"] = "{filename}"
            return act
    if re.search(r"\bpaste\b|\bctrl\s*\+\s*v\b", blob):
        return {"action": "paste", "target_desc": target}
    if re.search(r"\bcopy\b|\bctrl\s*\+\s*c\b", blob) and not re.search(r"\bcopy[_ ]?file\b", blob):
        return {"action": "copy", "target_desc": target}
    if re.search(r"\btype\b", blob):
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", step.user_description or "")
        value = quoted[0] if quoted else None
        for q in step.qa_history:
            more = re.findall(r"['\"]([^'\"]+)['\"]", q.get("a") or "")
            if more:
                value = more[-1]
        if not value and step.parameters:
            value = step.parameters[0]
        if not value:
            m = re.search(r"\btype\s+(\S+)", step.user_description or "", re.I)
            if m:
                value = m.group(1)
        if not value:
            return None
        return {
            "action": "type",
            "value": value,
            "text": value,
            "type_mode": "replace",
            "elem_name": "Text editor",
            "elem_type": "Document",
            "window_title": "Notepad",
            "target_desc": "the text editing area",
        }
    if re.search(r"\bclick\b", blob):
        action = {
            "action": "click",
            "target_desc": target,
            "window_title": "Notepad" if "notepad" in blob or "editor" in blob or "apollo" not in blob else None,
        }
        if step.anchor and (step.anchor.get("primary") or {}).get("name"):
            action["elem_name"] = step.anchor["primary"]["name"]
            action["elem_type"] = (step.anchor.get("primary") or {}).get("control_type")
        if "editor" in blob or "text" in blob:
            action["elem_name"] = "Text editor"
            action["elem_type"] = "Document"
            action["window_title"] = "Notepad"
        return action
    if re.search(r"\bhotkey\b|\bpress\b", blob):
        return {"action": "hotkey", "value": "ctrl+s", "keys": "ctrl+s"}
    verb = _closed_verb(blob)
    if verb in CLOSED_ACTIONS:
        return {"action": verb, "target_desc": target}
    return None


_UNDERSTANDING_KEYS = (
    "target",
    "action",
    "varies_each_run",
    "constants",
    "uses_from_earlier",
    "success_check",
    "assumptions",
    "plain_summary",
)


def _qa_blob(step: TaughtStep) -> str:
    return " ".join((q.get("a") or "") + " " + (q.get("q") or "") for q in (step.qa_history or []))


def _sentence_count(text: str) -> int:
    parts = [p for p in re.split(r"[.!?]+", (text or "").strip()) if p.strip()]
    return len(parts)


def explain_understanding(wf: TaughtWorkflow, step_id: str, re_ask_only: bool = False) -> dict:
    """Write back, in structured form, what this step is believed to do. No OS input."""
    from app_ui_guard import is_placeholder_text
    from memory_graph import producer_of

    step = get_step(wf, step_id)
    action = resolve_action(step) or {}
    blob = _step_blob(step)
    verb = action.get("action")
    cc = int(getattr(step, "click_count", 1) or 1)
    if cc == 2 and (action.get("clicks") or len(_filled_anchors(step)) >= 2):
        verb = "chain"
    if verb not in CLOSED_ACTIONS:
        verb = _closed_verb(blob)
    if verb not in CLOSED_ACTIONS:
        verb = None
    target = action.get("target_desc") or action.get("elem_name") or _target_phrase(step)
    if verb == "chain":
        filled = _filled_anchors(step)
        names = [_anchor_name(a) for a in filled[:2]]
        if len(names) == 2:
            target = f"{names[0]}, then {names[1]}"
        elif names:
            target = names[0]
    target = (target or "").strip() or None
    varies = list(step.parameters or [])
    if step.varies_note and not varies:
        varies = re.findall(r"\{[A-Za-z_][\w]*\}", step.varies_note)
    constants = []
    if not varies:
        constants.append("nothing in this step was marked as changing each run")
    if target:
        constants.append(f"the target is described as {target!r}")

    uses = []
    consumed = list(step.consumes or [])
    blob_l = blob.lower()
    if "email" in blob_l or "paste" in blob_l or "recipient" in blob_l:
        for earlier in wf.steps:
            if earlier.id == step.id:
                break
            for p in earlier.produces or []:
                if "email" in str(p).lower() and p not in consumed:
                    consumed.append(p)
    for p in consumed:
        prod = producer_of(wf, p)
        if prod:
            uses.append({"param": p, "from_step": prod.id})
            if p not in (step.consumes or []):
                step.consumes = list(step.consumes or []) + [p]
        else:
            uses.append({"param": p, "from_step": None})

    assumptions = []
    extra_q = None
    if verb is None:
        extra_q = "Which action is this: click, type, paste, or something else?"
        assumptions.append("I could not map this description to a closed-vocabulary action")
    if uses:
        assumptions.append("the earlier step that produces the consumed value has already run")
    if "notepad" in blob_l or (action.get("window_title") or "") == "Notepad":
        assumptions.append("Notepad is already open on the expected document")
    if "apollo" in blob_l or "linkedin" in blob_l:
        assumptions.append("the browser is already on the expected page")
    if (step.memory_note or "").strip() and not is_placeholder_text(step.memory_note):
        assumptions.append("follow the user's memory note for this step")
    elif not (step.memory_note or "").strip():
        assumptions.append("there are no notes for this step")
    learned = step.learned or {}
    if learned.get("user_edited") and (learned.get("summary") or learned.get("vision")):
        bits = []
        if learned.get("summary"):
            bits.append(learned["summary"][:240])
        if learned.get("vision"):
            bits.append("Vision: " + learned["vision"][:180])
        assumptions.append("user-edited watch summary: " + " ".join(bits))
    elif learned.get("summary") and not learned.get("user_edited"):
        assumptions.append("from Watch me: " + learned["summary"][:200])
    if learned.get("assumptions"):
        for a in learned["assumptions"]:
            if a not in assumptions:
                assumptions.append(a)
    if step.web_allowed:
        assumptions.append("I may look up a public page if this step needs a fact from the web")

    from step_cases import cases_assumption_text, list_step_cases

    case_note = cases_assumption_text(list_step_cases(step))
    if case_note:
        assumptions.append(case_note)

    from success_signals import expected_start_note

    start_note = expected_start_note(wf, step)
    if start_note:
        prev_idx = next((i for i, s in enumerate(wf.steps) if s.id == step.id), -1)
        if prev_idx > 0:
            prev = wf.steps[prev_idx - 1]
            after_title = ((prev.after_frame or {}).get("window_title") or "").strip()
            if after_title:
                assumptions.append(f"assuming {after_title!r} from step {prev.id} is open")
            else:
                assumptions.append(start_note.replace("This step should begin where", "assuming the screen from"))

    understanding_prior = dict(step.understanding or {})
    success = understanding_prior.get("success_check")
    success_source = understanding_prior.get("success_source")
    if not success:
        for q in step.qa_history:
            if q.get("kind") == "success_confirm" and (q.get("a") or "").strip().lower().startswith("yes"):
                from success_signals import success_check_text
                success = success_check_text(q.get("signal") or {})
                success_source = "derived"
                break
            if q.get("kind") == "success_fallback" and (q.get("a") or "").strip():
                success = q["a"].strip()
                success_source = "user"
                break
    if not success:
        for q in step.qa_history:
            if "succeed" in (q.get("q") or "").lower() and (q.get("a") or "").strip():
                success = q["a"].strip()
                success_source = "user"
                break
    extra_q = None
    pending_success = any(
        q.get("kind") in ("success_confirm", "success_fallback") and not (q.get("a") or "").strip()
        for q in step.qa_history
    )
    if verb is None:
        extra_q = "Which action is this: click, type, paste, or something else?"
        assumptions.append("I could not map this description to a closed-vocabulary action")
        success = None
    elif not success and not pending_success:
        if step.success_candidates:
            pass  # confirmation pending or will be asked after capture
        elif verb:
            assumptions.append("success check will be derived from before/after capture when available")
            success = f"the action {verb} on {target} has an observable effect"
        else:
            success = None
    elif pending_success and not success:
        assumptions.append("waiting for confirmation of the derived success signal")
    if not assumptions:
        assumptions.append("the screen is already in the state this step expects")

    summary = _plain_summary(verb, target, uses, success, assumptions)
    if verb == "chain" and len(_filled_anchors(step)) >= 2:
        n1, n2 = _anchor_name(step.anchors[0]), _anchor_name(step.anchors[1])
        summary = f"I will click {n1} and then click {n2}."
        if success:
            summary += f" I treat success as {success}."
        elif assumptions:
            summary += f" I am assuming: {assumptions[0]}."
    understanding = {
        "target": target,
        "action": verb,
        "varies_each_run": varies,
        "constants": constants,
        "uses_from_earlier": uses,
        "success_check": success,
        "assumptions": assumptions,
        "plain_summary": summary,
    }
    if success_source:
        understanding["success_source"] = success_source
    if understanding_prior.get("success_evidence"):
        understanding["success_evidence"] = understanding_prior["success_evidence"]
    from success_signals import normalize_profile_aware_success

    if normalize_profile_aware_success(step):
        understanding["success_check"] = step.understanding.get("success_check")
        understanding["success_evidence"] = step.understanding.get("success_evidence")
        if step.understanding.get("plain_summary"):
            understanding["plain_summary"] = step.understanding.get("plain_summary")
    if step.success_candidates and not understanding_prior.get("success_candidates"):
        understanding["success_candidates"] = list(step.success_candidates)
    if verb == "chain":
        understanding["chain_clicks"] = [_anchor_name(a) for a in _filled_anchors(step)[:2]]
    prior = dict(step.understanding or {})
    if prior.get("user_edited"):
        for k in ("target", "action", "success_check", "plain_summary", "assumptions"):
            if prior.get(k) not in (None, ""):
                understanding[k] = prior[k]
        understanding["user_edited"] = True
    if extra_q:
        if re_ask_only:
            # At most one clarifying question per edit; never disturb approved cosmetic edits.
            if step.status == "approved":
                extra_q = None
            else:
                open_clarify = [
                    q for q in step.qa_history
                    if (q.get("kind") or "") == "clarify" and not (q.get("a") or "").strip()
                ]
                if len(open_clarify) > 1:
                    step.qa_history = [
                        q for q in step.qa_history
                        if not (
                            (q.get("kind") or "") == "clarify"
                            and not (q.get("a") or "").strip()
                            and q is not open_clarify[0]
                        )
                    ]
        if extra_q:
            understanding["clarifying_question"] = extra_q
            understanding["followup_question"] = extra_q
            if step.status != "approved":
                step.status = "questioning"
            already = any(
                (q.get("q") or "") == extra_q and not (q.get("a") or "").strip()
                for q in step.qa_history
            )
            if not already:
                step.qa_history.append({
                    "q": extra_q, "a": "", "source": "chat", "kind": "clarify",
                })
    elif re_ask_only and step.status == "approved":
        understanding.pop("clarifying_question", None)
        understanding.pop("followup_question", None)
    step.understanding = understanding
    if not extra_q and step.status in ("draft", "questioning"):
        step.status = "questioning"
    save_taught(wf)
    return understanding


def approve_understanding(wf: TaughtWorkflow, step_id: str) -> TaughtStep:
    step = get_step(wf, step_id)
    sync_step_anchors(step)
    for anc in (step.anchors or []):
        if (anc or {}).get("vision_mismatch_pending"):
            raise TeachingError(
                "vision does not match the tree target — answer the confirmation on the card first"
            )
    cc = int(getattr(step, "click_count", 1) or 1)
    filled = _filled_anchors(step)
    if cc == 2:
        if len(filled) < 2:
            _ask_one_click_instead(step)
            save_taught(wf)
            raise TeachingError("second click not captured — did you mean one click?")
        from chain_exec import chain_irreversible_error

        err = chain_irreversible_error(step)
        if err:
            raise TeachingError(err)
    if not step.understanding:
        raise TeachingError("no written understanding to approve")
    for k in _UNDERSTANDING_KEYS:
        if k not in step.understanding:
            raise TeachingError(f"understanding missing {k}")
    missing = [k for k in _REQUIRED_FILLED if step.understanding.get(k) in (None, "")]
    if missing:
        raise TeachingError(f"cannot approve understanding: {', '.join(missing)} unset")
    if not step.understanding.get("assumptions"):
        raise TeachingError("cannot approve understanding: assumptions is unset")
    step.status = "understood"
    if not step.action:
        step.action = resolve_action(step)
    _clear_edit_notice(step)
    save_taught(wf)
    return step


def reject_understanding(wf: TaughtWorkflow, step_id: str, correction: str) -> TaughtStep:
    step = get_step(wf, step_id)
    step.qa_history.append({
        "q": "Your understanding was not quite right. What should change?",
        "a": correction,
        "source": "chat",
    })
    step.understanding = None
    step.status = "questioning"
    save_taught(wf)
    return step


def approve_behaviour(wf: TaughtWorkflow, step_id: str) -> TaughtStep:
    step = get_step(wf, step_id)
    if step.status != "demonstrated":
        raise TeachingError("a successful demo is required before approving behaviour")
    if not (step.demo or {}).get("ok"):
        raise TeachingError("demo did not succeed")
    action = resolve_action(step)
    if action is None:
        raise TeachingError("cannot resolve to exactly one action")
    step.action = action
    step.status = "approved"
    _clear_edit_notice(step)
    save_taught(wf)
    return step


def approve_step(wf: TaughtWorkflow, step_id: str, skip_rehearsal: bool = False) -> TaughtStep:
    step = get_step(wf, step_id)
    if skip_rehearsal:
        if not step.understanding:
            explain_understanding(wf, step_id)
        step.status = "demonstrated"
        step.demo = {
            "ok": True,
            "reason": "demo skipped by caller",
            "mode": "skip",
            "os_input_calls": 0,
        }
        save_taught(wf)
        return approve_behaviour(wf, step_id)
    if step.status not in ("demonstrated", "approved"):
        raise TeachingError("demo this step (or skip rehearsal) before approving behaviour")
    action = resolve_action(step)
    if action is None:
        step.status = "questioning"
        step.qa_history.append({
            "q": "Which closed action is this — click, type, hotkey, or launch_app?",
            "a": "",
            "source": "chat",
        })
        save_taught(wf)
        raise TeachingError("cannot resolve to exactly one action; asked one more question")
    step.action = action
    step.status = "approved"
    _clear_edit_notice(step)
    save_taught(wf)
    return step


def rehearse_step(wf: TaughtWorkflow, step_id: str, test_values: dict | None = None) -> dict:
    from teach_compile import rehearse_taught_step

    return rehearse_taught_step(wf, step_id, test_values=test_values)


def prepare_state(wf: TaughtWorkflow, step_id: str, mode: str, test_values: dict | None = None) -> dict:
    from teach_compile import prepare_state as _prep

    return _prep(wf, step_id, mode, test_values=test_values)


def demo_step(wf: TaughtWorkflow, step_id: str, test_values: dict | None = None, mode: str = "manual") -> dict:
    from teach_compile import demo_taught_step

    return demo_taught_step(wf, step_id, test_values=test_values, mode=mode)


def simulate_chain_capture(
    wf: TaughtWorkflow,
    step_id: str,
    points: list,
    names: list | None = None,
) -> dict:
    """No-human helper: simulate guided 2-click capture with synthetic witnesses."""
    step = get_step(wf, step_id)
    step.click_count = 2
    names = names or [f"Target{i + 1}" for i in range(len(points))]
    notes = []
    for i, pt in enumerate(points):
        if i >= 2:
            note = "You declared 2 clicks — ignoring this extra click."
            notes.append(note)
            step.chain_capture = dict(step.chain_capture or {})
            step.chain_capture["ignored_extra"] = int(step.chain_capture.get("ignored_extra") or 0) + 1
            step.chain_capture["last_note"] = note
            continue
        primary = {"name": names[i], "control_type": "Button", "pipeline": "a11y"}
        anchor = {
            "primary": primary,
            "witnesses": {
                "a11y": {"saw": True, "account": f"a Button named {names[i]!r}.", "confidence": "high"},
                "dom": {"saw": False, "account": "not page content."},
                "vision": {"saw": False, "account": "nothing here."},
            },
            "agreement": "single",
            "point": list(pt),
            "sub_index": i,
            "confirmed": True,
        }
        while len(step.anchors) <= i:
            step.anchors.append(None)
        step.anchors[i] = anchor
        sync_step_anchors(step)
        if i == 0:
            _ask_chain_second(step, anchor)
        if i == 1:
            _ask_chain_summary(step)
    save_taught(wf)
    out = {"ok": True, "anchors": list(step.anchors), "notes": notes}
    if len(points) > 2:
        out["ignored"] = True
        out["note"] = notes[-1] if notes else ""
    return out


def check_chain_incomplete(wf: TaughtWorkflow, step_id: str) -> dict | None:
    """If declared 2 but only 1 anchor, surface the one-click question."""
    step = get_step(wf, step_id)
    cc = int(getattr(step, "click_count", 1) or 1)
    if cc == 2 and len(_filled_anchors(step)) == 1:
        _ask_one_click_instead(step)
        save_taught(wf)
        return {"incomplete": True, "question": "You declared 2 clicks but only showed one — did you mean one click after all?"}
    return None


def reflect_on_demo(wf: TaughtWorkflow, step_id: str) -> dict:
    step = get_step(wf, step_id)
    if not step.demo:
        raise TeachingError("no demo to reflect on")
    understood = step.understanding or {}
    wanted = (understood.get("success_check") or "").lower()
    observed = str((step.demo or {}).get("observed") or step.demo.get("reason") or "")
    obs_l = observed.lower()
    differences = []
    if wanted and "dialog" in wanted and "dialog" not in obs_l:
        differences.append(f"success check expected a dialog; observed {observed!r}")
    if wanted and "dialog" not in wanted and "dialog" in obs_l:
        differences.append("a dialog appeared that the success check did not mention")
    if wanted and observed and wanted not in obs_l and not any(
        tok in obs_l for tok in wanted.split() if len(tok) > 4
    ):
        differences.append(f"observed {observed!r} does not match success check {wanted!r}")
    matches = not differences
    reflection = {
        "what_i_did": (step.action or {}).get("action") or "unknown",
        "what_i_observed": observed,
        "matches_understanding": matches,
        "differences": differences,
        "confidence_note": "matched" if matches else "demo and written understanding disagree",
    }
    step.reflection = reflection
    save_taught(wf)
    return reflection
