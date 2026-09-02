"""Deterministic gate: invalid plan → nothing executes."""

from __future__ import annotations

import re

from plan_schema import CLOSED_ACTIONS, REQUIRED_PARAMS, Plan, PlanNode, node_from_dict, plan_from_dict


class PlanViolation(dict):
    def __init__(self, code: str, message: str, node_id: str | None = None):
        super().__init__(code=code, message=message, node_id=node_id)


_MAX_TARGET_DESC = 80

_ACTION_VERBS = (
    r"\bopen\b",
    r"\blaunch\b",
    r"\btype\b",
    r"\benter\b",
    r"\bwrite\b",
    r"\bsave\b",
    r"\bclick\b",
    r"\bnavigate\b",
    r"\bgo to\b",
    r"\bsearch\b",
    r"\bcopy\b",
    r"\bpaste\b",
    r"\bselect\b",
)


def _instruction_verb_count(instruction: str | None) -> int:
    if not instruction:
        return 0
    n = 0
    for pat in _ACTION_VERBS:
        if re.search(pat, instruction, re.I):
            n += 1
    return n


def validate_plan(plan, instruction: str | None = None) -> list[PlanViolation]:
    plan = plan_from_dict(plan)
    nodes: list[PlanNode] = list(plan.nodes)
    violations: list[PlanViolation] = []
    seen: set[str] = set()
    produced: set[str] = set()
    instruction = instruction or getattr(plan, "instruction", None) or (plan.to_dict().get("instruction") if hasattr(plan, "to_dict") else None)
    if not instruction:
        instruction = None

    if instruction:
        verbs = _instruction_verb_count(instruction)
        if verbs >= 2 and len(nodes) < 2:
            violations.append(
                PlanViolation(
                    "not_decomposed",
                    "instruction was not decomposed into steps",
                )
            )

    for node in nodes:
        nid = str(node.id or "")
        if not nid:
            violations.append(PlanViolation("missing_id", "node is missing id"))
            continue
        if nid in seen:
            violations.append(PlanViolation("duplicate_id", f"duplicate id {nid!r}", nid))
        seen.add(nid)
        action = (node.action or "").strip()
        if not action or action not in CLOSED_ACTIONS:
            violations.append(
                PlanViolation(
                    "unknown_action",
                    f"action {action!r} is not in the closed vocabulary",
                    nid,
                )
            )
        desc = (node.target_desc or "").strip()
        if len(desc) > _MAX_TARGET_DESC:
            violations.append(
                PlanViolation(
                    "instruction_in_target_desc",
                    "instruction text placed in target_desc; decompose into steps",
                    nid,
                )
            )
        elif instruction and desc:
            blob = " ".join(instruction.lower().split())
            dlow = " ".join(desc.lower().split())
            if dlow in blob and len(dlow) > 40:
                violations.append(
                    PlanViolation(
                        "instruction_in_target_desc",
                        "instruction text placed in target_desc; decompose into steps",
                        nid,
                    )
                )
            if "different filename each time" in dlow:
                violations.append(
                    PlanViolation(
                        "instruction_in_target_desc",
                        "instruction text placed in target_desc; decompose into steps",
                        nid,
                    )
                )
        for req in REQUIRED_PARAMS.get(action, ()):
            has_val = bool(node.value or (node.extra or {}).get("value") or (node.extra or {}).get("keys"))
            if req == "value" and not has_val:
                violations.append(
                    PlanViolation("missing_param", f"action {action!r} requires {req}", nid)
                )
        if action == "chain":
            extra = node.extra or {}
            clicks = extra.get("clicks") or []
            cc = int(extra.get("click_count") or len(clicks) or 0)
            if cc not in (1, 2):
                violations.append(
                    PlanViolation("invalid_chain", f"click_count must be 1 or 2, got {cc}", nid)
                )
            elif len(clicks) != cc:
                violations.append(
                    PlanViolation(
                        "invalid_chain",
                        f"chain must have exactly {cc} click(s), got {len(clicks)}",
                        nid,
                    )
                )
            elif cc == 2 and clicks:
                from chain_exec import validate_chain_node

                err = validate_chain_node(node)
                if err:
                    violations.append(PlanViolation("invalid_chain", err, nid))
        for key in node.consumes or []:
            if key not in produced:
                violations.append(
                    PlanViolation(
                        "unsatisfied_consume",
                        f"node {nid} consumes {{{key}}} but no earlier node produces it",
                        nid,
                    )
                )
        for key in node.produces or []:
            produced.add(str(key))

    graph = {str(n.id): [] for n in nodes if n.id}
    ids = [str(n.id) for n in nodes if n.id]
    for n in nodes:
        nxt = (n.extra or {}).get("next")
        if isinstance(nxt, str):
            graph.setdefault(str(n.id), []).append(str(nxt))
        elif isinstance(nxt, list):
            graph.setdefault(str(n.id), []).extend(str(x) for x in nxt)
    for a, b in zip(ids, ids[1:]):
        graph.setdefault(a, []).append(b)

    visiting, done = set(), set()

    def dfs(u: str) -> bool:
        if u in visiting:
            return True
        if u in done:
            return False
        visiting.add(u)
        for v in graph.get(u, []):
            if v in graph and dfs(v):
                return True
        visiting.remove(u)
        done.add(u)
        return False

    for u in graph:
        if dfs(u):
            violations.append(PlanViolation("cycle", "plan graph contains a cycle", u))
            break
    return violations


def patch_node(plan, node_id: str, patch: dict) -> tuple[Plan, list[PlanViolation]]:
    plan = plan_from_dict(plan)
    found = False
    new_nodes = []
    for n in plan.nodes:
        if str(n.id) != str(node_id):
            new_nodes.append(n)
            continue
        found = True
        new_nodes.append(_apply(n, patch))
    if not found:
        return plan, [PlanViolation("unknown_node", f"no node {node_id!r}", node_id)]
    patched = Plan(nodes=new_nodes, source=plan.source)
    return patched, validate_plan(patched)


def _apply(node: PlanNode, patch: dict) -> PlanNode:
    data = node.to_dict()
    extra = dict(node.extra or {})
    for k, v in (patch or {}).items():
        if k in PlanNode.__dataclass_fields__ and k != "extra":
            data[k] = v
        else:
            extra[k] = v
    data["extra"] = extra
    return node_from_dict(data)
