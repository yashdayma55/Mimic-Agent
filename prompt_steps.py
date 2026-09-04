"""Prompt-driven steps — fallback when anchoring fails."""

from __future__ import annotations

from datetime import datetime, timezone

from plan_schema import CLOSED_ACTIONS
from teaching import TaughtStep, TaughtWorkflow, TeachingError, get_step, save_taught

PROMPT_RELIABILITY_NOTE = (
    "This step re-locates its target every run from your instruction — "
    "less deterministic than an anchor. Use only when Show me cannot capture the target."
)

METHOD_ANCHOR = "anchor"
METHOD_PROMPT = "prompt"


def prompt_card_note(step: TaughtStep) -> str | None:
    if (step.method or METHOD_ANCHOR) != METHOD_PROMPT:
        return None
    return PROMPT_RELIABILITY_NOTE


def set_step_method(step: TaughtStep, method: str) -> None:
    m = (method or METHOD_ANCHOR).strip().lower()
    if m not in (METHOD_ANCHOR, METHOD_PROMPT):
        raise TeachingError(f"method must be anchor or prompt, got {method!r}")
    step.method = m
    if m == METHOD_ANCHOR:
        step.prompt_instruction = ""


def save_prompt_method(
    wf: TaughtWorkflow,
    step_id: str,
    instruction: str,
) -> dict:
    text = (instruction or "").strip()
    if not text:
        raise TeachingError("write an instruction for this prompt step")
    step = get_step(wf, step_id)
    step.method = METHOD_PROMPT
    step.prompt_instruction = text
    step.action = {"action": "prompt", "value": text, "target_desc": text[:80]}
    save_taught(wf)
    return {
        "ok": True,
        "method": METHOD_PROMPT,
        "reliability_note": PROMPT_RELIABILITY_NOTE,
        "step": step.to_dict(),
    }


def try_prompt_instruction(
    wf: TaughtWorkflow,
    step_id: str,
    instruction: str | None = None,
) -> dict:
    """Execute instruction once via closed vocabulary — observational trial."""
    step = get_step(wf, step_id)
    text = (instruction or step.prompt_instruction or step.user_description or "").strip()
    if not text:
        raise TeachingError("no instruction to try")
    from teach_loop import _closed_verb, resolve_action

    blob = text.lower()
    verb = _closed_verb(blob) or "click"
    if verb not in CLOSED_ACTIONS:
        verb = "click"
    trial_action = resolve_action(step) or {"action": verb, "target_desc": text}
    if trial_action.get("action") == "chain":
        trial_action = {"action": "click", "target_desc": text}
    observed = f"trial would run {trial_action.get('action')} — {text[:120]}"
    reflection = {
        "what_i_did": f"prompt trial: {text[:200]}",
        "what_i_observed": observed,
        "matches_understanding": None,
        "differences": [],
        "confidence_note": "prompt trial — not a full demo",
        "trial_action": trial_action,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    step.reflection = reflection
    save_taught(wf)
    return {"ok": True, "reflection": reflection, "trial_action": trial_action, "step": step.to_dict()}


def compile_prompt_step_ok(step: TaughtStep) -> bool:
    if (step.method or METHOD_ANCHOR) != METHOD_PROMPT:
        return True
    sc = (step.understanding or {}).get("success_check") or ""
    if not str(sc).strip():
        return False
    return bool((step.prompt_instruction or "").strip())
