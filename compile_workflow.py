"""Compile edited UI cards into the existing harness / replay-engine plan.

Does not execute. Placeholders stay as {name} until bind_inputs() at run time.
"""

from __future__ import annotations

import re

from harness_schema import STEP_KINDS, HarnessStep, step_from_dict, step_to_dict

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][\w]*)\}")


def placeholders_in(text: str | None) -> set[str]:
    if not text:
        return set()
    return set(_PLACEHOLDER_RE.findall(str(text)))


def _action_blobs(action) -> str:
    if not isinstance(action, dict):
        return ""
    parts = []
    for v in action.values():
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            parts.append(_action_blobs(v))
    return " ".join(parts)


def step_references(card: dict) -> set[str]:
    refs = set()
    for key in ("description", "instruction", "goal", "target_name"):
        refs |= placeholders_in(card.get(key))
    refs |= placeholders_in(_action_blobs(card.get("action")))
    for name in card.get("inputs") or []:
        if isinstance(name, str) and name.strip():
            refs.add(name.strip().strip("{}"))
    return refs


def step_produces(card: dict) -> set[str]:
    out = set()
    for name in card.get("outputs") or []:
        if isinstance(name, str) and name.strip():
            out.add(name.strip().strip("{}"))
    return out


def live_cards(cards: list) -> list[dict]:
    return [
        c for c in (cards or [])
        if isinstance(c, dict) and not c.get("deleted")
    ]


def check_dependencies(cards: list) -> list[dict]:
    """If a deleted step produced a value a later live step still references, flag it."""
    cards = [c for c in (cards or []) if isinstance(c, dict)]
    violations = []
    produced_live: set[str] = set()
    deleted_outputs: dict[str, int] = {}  # name -> deleted index

    for card in cards:
        idx = card.get("index", "?")
        produced = step_produces(card)
        if card.get("deleted"):
            for name in produced:
                deleted_outputs.setdefault(name, idx)
            continue
        refs = step_references(card)
        for name in refs:
            if name in produced_live:
                continue
            if name in deleted_outputs:
                violations.append({
                    "placeholder": name,
                    "used_by_index": idx,
                    "deleted_source_index": deleted_outputs[name],
                    "message": (
                        f"Step {idx} uses {{{name}}} but the step that produced it "
                        f"(index {deleted_outputs[name]}) is deleted. "
                        f"Map {{{name}}} to a new source before saving."
                    ),
                })
        produced_live |= produced
    return violations


def validate_cards(cards: list) -> list[str]:
    problems = []
    for card in live_cards(cards):
        idx = card.get("index", "?")
        kind = (card.get("kind") or "").strip().lower()
        if kind not in STEP_KINDS:
            problems.append(f"step {idx}: missing/invalid kind {kind!r}")
            continue
        if kind == "reason":
            if not (card.get("goal") or card.get("description") or card.get("instruction")):
                problems.append(f"step {idx}: reason step needs a goal or description")
        else:
            action = card.get("action")
            has_action = isinstance(action, dict) and bool(action)
            if not (has_action or card.get("target_name")):
                problems.append(
                    f"step {idx}: {kind} step needs an action or target"
                )
    return problems


def _action_string(action) -> str | None:
    if not isinstance(action, dict):
        return None
    return action.get("action") or action.get("kind") or None


def compile_workflow(cards: list) -> dict:
    """Map edited cards to harness steps + replay-engine plan. No late binding."""
    problems = validate_cards(cards)
    if problems:
        return {"ok": False, "problems": problems, "plan": [], "harness_steps": []}

    plan = []
    harness_steps = []
    for n, card in enumerate(live_cards(cards), 1):
        kind = (card.get("kind") or "reason").strip().lower()
        desc = (card.get("description") or "").strip()
        instruction = (card.get("instruction") or desc).strip()
        goal = (card.get("goal") or instruction or desc).strip() or None
        action = card.get("action") if isinstance(card.get("action"), dict) else None
        target_name = card.get("target_name")
        target_type = card.get("target_type")
        inputs = list(card.get("inputs") or [])

        hs = HarnessStep(
            kind=kind,
            description=desc or instruction or f"step {n}",
            goal=goal if kind == "reason" else None,
            action=dict(action) if action else None,
            target_name=target_name,
            target_type=target_type,
            inputs=inputs,
        )
        try:
            hs.validate()
        except AssertionError as e:
            problems.append(f"step {card.get('index', n)}: {e}")
            continue

        harness_steps.append(step_to_dict(hs))
        replay = {
            "step": n,
            "kind": kind,
            "instruction": instruction or desc,
            "action": _action_string(action) or ("reason" if kind == "reason" else None),
            "elem_name": target_name,
            "elem_type": target_type,
            "text": (action or {}).get("text") if action else None,
            "type_mode": (action or {}).get("type_mode") if action else None,
            "window_title": card.get("window_title") or card.get("target_window"),
            "goal": goal,
            "index": card.get("index"),
        }
        plan.append(replay)

    if problems:
        return {"ok": False, "problems": problems, "plan": [], "harness_steps": []}
    return {
        "ok": True,
        "problems": [],
        "plan": plan,
        "harness_steps": harness_steps,
    }


def bind_inputs(obj, inputs: dict):
    """Late-bind {placeholders} from inputs. Does not mutate compile output if copied."""
    inputs = inputs or {}
    if isinstance(obj, str):
        out = obj
        for k, v in inputs.items():
            out = out.replace("{" + str(k) + "}", str(v))
        return out
    if isinstance(obj, list):
        return [bind_inputs(x, inputs) for x in obj]
    if isinstance(obj, dict):
        return {k: bind_inputs(v, inputs) for k, v in obj.items()}
    return obj


def load_harness_steps(harness_step_dicts: list) -> list:
    """Prove engine schema can load compiled steps."""
    steps = []
    for d in harness_step_dicts:
        hs = step_from_dict(d)
        hs.validate()
        steps.append(hs)
    return steps
