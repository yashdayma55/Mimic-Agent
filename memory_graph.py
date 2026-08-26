"""Memory graph: what we know, not what we run. Separate from replay_engine."""

from __future__ import annotations

from dataclasses import dataclass, field

from teaching import TaughtStep, TaughtWorkflow, get_step


@dataclass
class MemoryNode:
    step_id: str
    description: str
    parameters: list = field(default_factory=list)
    produces: list = field(default_factory=list)
    consumes: list = field(default_factory=list)
    qa: list = field(default_factory=list)
    anchor: dict | None = None


@dataclass
class MemoryEdge:
    kind: str  # next | depends_on
    src: str
    dst: str
    param: str | None = None


@dataclass
class MemoryGraph:
    nodes: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)


def build_graph(wf: TaughtWorkflow) -> MemoryGraph:
    g = MemoryGraph()
    ordered = sorted(wf.steps, key=lambda s: s.order)
    for s in ordered:
        g.nodes[s.id] = MemoryNode(
            step_id=s.id,
            description=s.user_description,
            parameters=list(s.parameters or []),
            produces=list(s.produces or []),
            consumes=list(s.consumes or []),
            qa=list(s.qa_history),
            anchor=s.anchor,
        )
    for a, b in zip(ordered, ordered[1:]):
        g.edges.append(MemoryEdge(kind="next", src=a.id, dst=b.id))
    for s in ordered:
        for param in s.consumes or []:
            prod = producer_of(wf, param)
            if prod and prod.id != s.id:
                g.edges.append(MemoryEdge(kind="depends_on", src=prod.id, dst=s.id, param=param))
    return g


def why(wf: TaughtWorkflow, step_id: str) -> dict:
    step = get_step(wf, step_id)
    return {
        "step_id": step.id,
        "description": step.user_description,
        "qa": list(step.qa_history),
        "anchor": step.anchor,
        "action": step.action,
    }


def producer_of(wf: TaughtWorkflow, param: str) -> TaughtStep | None:
    needle = param if str(param).startswith("{") else "{" + str(param).strip("{}") + "}"
    for s in wf.steps:
        if needle in (s.produces or []) or param in (s.produces or []):
            return s
    return None


def find_similar(wf: TaughtWorkflow, description: str) -> list[TaughtStep]:
    words = set(re_words(description))
    scored = []
    for s in wf.steps:
        sw = set(re_words(s.user_description))
        if not words or not sw:
            continue
        overlap = len(words & sw) / max(len(words), 1)
        if overlap >= 0.4:
            scored.append((overlap, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored]


def re_words(text: str) -> list[str]:
    import re

    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2]


def validate_dependencies(wf: TaughtWorkflow) -> list[dict]:
    ids = {s.id for s in wf.steps}
    produced = {}
    for s in wf.steps:
        for p in s.produces or []:
            produced[p] = s.id
    violations = []
    for s in wf.steps:
        for p in s.consumes or []:
            prod = producer_of(wf, p)
            if prod is None or prod.id not in ids:
                violations.append({
                    "step_id": s.id,
                    "param": p,
                    "message": f"step {s.id} consumes {p} but no producer exists",
                })
    return violations
