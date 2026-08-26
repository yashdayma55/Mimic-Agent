"""Constant vs parameter questions — asked once at compile time, then persisted."""

from __future__ import annotations

import json
import os
import re

from plan_schema import Plan, PlanNode, plan_from_dict

_AMBIGUOUS_ACTIONS = {
    "type",
    "open_path",
    "open_url",
    "launch_app",
    "move_file",
    "copy_file",
    "navigate",
}
_PLACEHOLDER = re.compile(r"^\{[A-Za-z_][A-Za-z0-9_]*\}$")


def _is_literal(value) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    if _PLACEHOLDER.match(text):
        return False
    return True


def find_ambiguous_nodes(plan, decisions: dict | None = None) -> list[dict]:
    plan = plan_from_dict(plan)
    decisions = decisions or {}
    questions = []
    for node in plan.nodes:
        if (node.action or "") not in _AMBIGUOUS_ACTIONS:
            continue
        if not _is_literal(node.value):
            continue
        if str(node.id) in decisions:
            continue
        likely = bool((node.extra or {}).get("likely_parameter"))
        item = {
            "node_id": node.id,
            "action": node.action,
            "value": node.value,
            "likely_parameter": likely,
            "prompt": (
                f"You typed {node.value!r}. Should I always use exactly this, "
                "or ask you for it each run?"
            ),
        }
        questions.append(item)
    marked = [q for q in questions if q.get("likely_parameter")]
    if marked:
        return marked
    return questions


def apply_answers(plan, answers: dict, *, name_for: dict | None = None) -> Plan:
    """answers[node_id] is 'constant' or 'parameter'."""
    plan = plan_from_dict(plan)
    name_for = name_for or {}
    new_nodes = []
    for node in plan.nodes:
        ans = (answers or {}).get(str(node.id))
        if not ans:
            new_nodes.append(node)
            continue
        data = node.to_dict()
        if ans == "parameter":
            stem = name_for.get(str(node.id)) or _slug(node.value) or "value"
            data["value"] = "{" + stem + "}"
            extra = dict(node.extra or {})
            extra["binding"] = "parameter"
            extra["param_name"] = stem
            extra["original_literal"] = node.value
            data["extra"] = extra
        else:
            extra = dict(node.extra or {})
            extra["binding"] = "constant"
            data["extra"] = extra
        from plan_schema import node_from_dict

        new_nodes.append(node_from_dict(data))
    return Plan(nodes=new_nodes, source=plan.source)


def _slug(value) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "")).strip("_").lower()
    if text.endswith("txt"):
        text = "filename"
    return (text[:24] or "value")


def decisions_path(workflow_dir: str) -> str:
    return os.path.join(workflow_dir, "parameter_decisions.json")


def load_decisions(workflow_dir: str) -> dict:
    path = decisions_path(workflow_dir)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def save_decisions(workflow_dir: str, decisions: dict) -> None:
    os.makedirs(workflow_dir, exist_ok=True)
    path = decisions_path(workflow_dir)
    existing = load_decisions(workflow_dir)
    existing.update(decisions or {})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)


def compile_parameters(plan, workflow_dir: str, answers: dict | None = None,
                       name_for: dict | None = None) -> tuple[Plan, list[dict]]:
    """Apply persisted + new answers. Returns (plan, remaining questions)."""
    plan = plan_from_dict(plan)
    decisions = load_decisions(workflow_dir)
    if answers:
        decisions.update(answers)
        save_decisions(workflow_dir, decisions)
        plan = apply_answers(plan, answers, name_for=name_for)
    remaining = find_ambiguous_nodes(plan, decisions)
    return plan, remaining


def bind_parameters(plan, inputs: dict | None) -> Plan:
    """Late-bind {name} from inputs. Does not mutate the stored plan."""
    plan = plan_from_dict(plan)
    inputs = inputs or {}
    new_nodes = []
    for node in plan.nodes:
        data = node.to_dict()
        val = node.value
        if isinstance(val, str) and inputs:
            bound = val
            for key, inp in inputs.items():
                bound = bound.replace("{" + str(key) + "}", str(inp))
            data["value"] = bound
            if bound != val:
                extra = dict(node.extra or {})
                extra["bound"] = True
                data["extra"] = extra
        from plan_schema import node_from_dict

        new_nodes.append(node_from_dict(data))
    return Plan(nodes=new_nodes, source=plan.source)
