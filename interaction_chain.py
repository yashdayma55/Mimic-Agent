"""Interaction chains: scroll / hover precursors + one state-changing action last."""

from __future__ import annotations

from hover_actions import _STATE_CHANGING
from plan_schema import CLOSED_ACTIONS

MAX_INTERACTION_PARTS = 3
PRECURSOR_ACTIONS = frozenset({"scroll", "hover"})


def validate_interaction_chain(parts: list[dict]) -> str | None:
    if not parts:
        return "interaction chain is empty"
    if len(parts) > MAX_INTERACTION_PARTS:
        return f"interaction chain allows at most {MAX_INTERACTION_PARTS} parts"
    state_changers = [p for p in parts if (p.get("action") or "") in _STATE_CHANGING]
    if len(state_changers) > 1:
        return "chain may contain at most one state-changing action"
    if state_changers and (parts[-1].get("action") or "") not in _STATE_CHANGING:
        return "state-changing action must be last in the chain"
    for p in parts:
        act = (p.get("action") or "").strip()
        if act not in CLOSED_ACTIONS:
            return f"unknown action {act!r}"
        if act == "scroll" and not (p.get("to_find") or "").strip():
            return "scroll part requires to_find"
        if "delta" in p or "pixels" in p:
            return "scroll intents must not store pixel deltas"
    return None


def parts_from_action(action: dict) -> list[dict]:
    if not action:
        return []
    if action.get("parts"):
        return list(action.get("parts") or [])
    if action.get("action") == "chain":
        clicks = action.get("clicks") or []
        return [{"action": "click", **c} for c in clicks]
    return [dict(action)]


def execute_interaction_chain(step: dict, last_window: str | None = None):
    from ui_runner import StepResult, execute_step

    extra = step.get("extra") or {}
    parts = step.get("parts") or extra.get("parts") or []
    if not parts:
        from chain_exec import execute_chain_step

        return execute_chain_step(step, last_window)
    err = validate_interaction_chain(parts)
    if err:
        return StepResult(ok=False, reason=err)
    anchors = step.get("anchors") or extra.get("anchors") or []
    result = StepResult(ok=False, reason="")
    for i, part in enumerate(parts):
        act = (part.get("action") or "").strip()
        mini = {
            **step,
            "action": act,
            "elem_name": part.get("elem_name"),
            "elem_type": part.get("elem_type"),
            "to_find": part.get("to_find"),
            "within": part.get("within"),
            "max_steps": part.get("max_steps") or 8,
            "point": part.get("point"),
            "anchor": anchors[i] if i < len(anchors) else part.get("anchor"),
        }
        if act == "hover":
            from hover_actions import execute_hover

            out = execute_hover(mini)
        elif act == "scroll":
            from scroll_actions import execute_scroll

            out = execute_scroll(mini)
        elif act == "click":
            mini["action"] = "click"
            out = execute_step(mini, last_window).__dict__
        else:
            return StepResult(ok=False, reason=f"unsupported chain part {act!r}")
        result.lines.append(f"  part {i + 1}/{len(parts)}: {out.get('reason')}")
        if not out.get("ok"):
            result.reason = out.get("reason") or f"failed at part {i + 1}"
            return result
    result.ok = True
    result.reason = f"interaction chain completed ({len(parts)} parts)"
    return result


def execute_legacy_chain(step: dict, last_window: str | None = None):
    from chain_exec import execute_chain_step

    return execute_chain_step(step, last_window)


def compose_scroll_hover_click(
    scroll: dict,
    hover: dict,
    click_event: dict,
) -> dict | None:
    parts = []
    anchors = []
    if scroll and scroll.get("to_find"):
        parts.append(dict(scroll))
        anchors.append({"scroll_intent": scroll})
    if hover and hover.get("revealed"):
        parts.append({"action": "hover", "point": hover.get("point")})
        anchors.append(dict(hover))
    click_pt = click_event.get("point")
    if not click_pt:
        return None
    revealed = (hover or {}).get("revealed") or []
    primary = revealed[0] if revealed else {}
    click_part = {
        "action": "click",
        "elem_name": primary.get("name"),
        "elem_type": primary.get("control_type"),
        "point": list(click_pt),
    }
    parts.append(click_part)
    click_anchor = dict(click_event.get("anchor") or {})
    click_anchor["point"] = list(click_pt)
    anchors.append(click_anchor)
    err = validate_interaction_chain(parts)
    if err:
        return None
    return {
        "action": "chain",
        "chain_kind": "interaction",
        "parts": parts,
        "anchors": anchors,
        "click_count": len(parts),
        "prompt": "Save as: scroll → hover → click?",
    }
