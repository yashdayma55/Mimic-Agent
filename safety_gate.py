"""
Irreversible-action tollgate — last-line safety before real Send/Submit/Delete/etc.

The tollgate is ALWAYS active, even when require_approval=False.
Only the exact word 'yes' proceeds; anything else stops the run.
"""

import re

# Closed list — conservative; over-flagging is acceptable.
IRREVERSIBLE_PATTERNS = (
    "send message",
    "send via",
    "schedule send",
    "confirm payment",
    "place order",
    "apply now",
    "send",
    "submit",
    "post",
    "publish",
    "delete",
    "remove",
    "pay",
)

# Goals like "fill compose … Do NOT click Send / submit" must NOT trip the tollgate.
# Affirmative "Click Send" / "Send outreach email" still match after scrubbing.
_NEGATED_IRREVERSIBLE = re.compile(
    r"(?:do\s+not|don't|dont|never|without|avoid)\s+"
    r"(?:(?:to|the|a|an|any)\s+)*"
    r"(?:click(?:ing)?\s+|press(?:ing)?\s+|hit(?:ting)?\s+)?"
    r"(?:send|submit|post|publish|delete|remove|pay)\b",
    re.I,
)


def _step_text_blob(step) -> str:
    """Collect searchable text from replay, harness, or agent action shapes."""
    parts = []

    if step is None:
        return ""

    # HarnessStep or similar dataclass
    for attr in ("description", "target_name", "goal", "instruction", "elem_name", "text"):
        val = getattr(step, attr, None)
        if val:
            parts.append(str(val))

    if isinstance(step, dict):
        for key in (
            "instruction", "elem_name", "text", "description",
            "target_name", "goal", "why",
        ):
            val = step.get(key)
            if val:
                parts.append(str(val))
        action = step.get("action")
        if isinstance(action, dict):
            for key in ("action", "why", "text", "match", "url", "keys"):
                val = action.get(key)
                if val not in (None, ""):
                    parts.append(str(val))
        elif isinstance(action, str) and action:
            parts.append(action)

    return " ".join(parts).lower()


def _step_description(step) -> str:
    """Human-readable line for the tollgate prompt."""
    if step is None:
        return "(unknown step)"
    for attr in ("description", "instruction"):
        val = getattr(step, attr, None)
        if val:
            return str(val)
    if isinstance(step, dict):
        for key in ("description", "instruction", "elem_name"):
            val = step.get(key)
            if val:
                return str(val)
        action = step.get("action")
        if isinstance(action, dict):
            why = action.get("why") or action.get("text")
            if why:
                return str(why)
            if action.get("action"):
                return str(action["action"])
    return "(unknown step)"


def _scrub_negated_irreversible(blob: str) -> str:
    """Remove 'do not … send/submit/…' spans so those verbs do not false-positive."""
    return _NEGATED_IRREVERSIBLE.sub(" ", blob or "")


def _matched_irreversible_verb(step) -> str:
    blob = _scrub_negated_irreversible(_step_text_blob(step))
    for pattern in IRREVERSIBLE_PATTERNS:
        if pattern in blob:
            return pattern
    return "perform this action"


def is_irreversible_step(step) -> bool:
    """True if step text matches a closed list of irreversible intents."""
    blob = _scrub_negated_irreversible(_step_text_blob(step))
    return any(p in blob for p in IRREVERSIBLE_PATTERNS)


def confirm_irreversible_step(step) -> bool:
    """Show tollgate; return True only on exact 'yes'."""
    desc = _step_description(step)
    verb = _matched_irreversible_verb(step)
    print("==============================================")
    print(" IRREVERSIBLE ACTION - THIS CANNOT BE UNDONE")
    print(f" About to: {desc}")
    print(f" This will {verb} for real.")
    prompt = (
        "==============================================\n"
        " IRREVERSIBLE ACTION - THIS CANNOT BE UNDONE\n"
        f" About to: {desc}\n"
        f" This will {verb} for real.\n"
        " Type 'yes' to proceed, anything else to STOP: "
    )
    try:
        from ui_prompts import ask_human
        answer = ask_human("tollgate", prompt)
    except Exception:
        print(prompt, end="", flush=True)
        answer = input().strip()
    return answer == "yes"


def require_irreversible_confirmation(step) -> bool:
    """Run tollgate when needed. True = safe to act; False = stop the run."""
    if not is_irreversible_step(step):
        return True
    if confirm_irreversible_step(step):
        return True
    print("\n  STOPPED at irreversible step — run halted for safety.")
    return False


def harness_step_check(step, action=None, description=None):
    """Build a dict harness/agent paths can pass to the tollgate."""
    blob = {
        "description": description or getattr(step, "description", "") or "",
        "target_name": getattr(step, "target_name", None),
        "goal": getattr(step, "goal", None),
    }
    if action:
        blob["action"] = action
    return blob
