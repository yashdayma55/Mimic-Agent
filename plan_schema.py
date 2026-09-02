"""Typed executable plan. The LLM may emit this JSON; it cannot invoke actions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

CLOSED_ACTIONS = (
    "click",
    "chain",
    "type",
    "press",
    "scroll",
    "navigate",
    "switch_tab",
    "copy",
    "paste",
    "wait",
    "hotkey",
    "done",
    "stuck",
    "launch_app",
    "open_url",
    "open_path",
    "move_file",
    "copy_file",
    "prompt",
)

REQUIRED_PARAMS = {
    "click": (),
    "chain": (),
    "type": ("value",),
    "press": ("value",),
    "scroll": (),
    "navigate": ("value",),
    "switch_tab": ("value",),
    "copy": (),
    "paste": (),
    "wait": (),
    "hotkey": ("value",),
    "done": (),
    "stuck": (),
    "launch_app": ("value",),
    "open_url": ("value",),
    "open_path": ("value",),
    "move_file": ("value",),  # "src -> dst" or dict in extra
    "copy_file": ("value",),
    "prompt": ("value",),
}


@dataclass
class PlanNode:
    id: str
    action: str
    target_desc: Optional[str] = None
    target_ref: Optional[str] = None
    value: Optional[str] = None
    produces: list = field(default_factory=list)
    consumes: list = field(default_factory=list)
    irreversible: bool = False
    max_retries: int = 0
    window_title: Optional[str] = None
    elem_name: Optional[str] = None
    elem_type: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_runner_step(self) -> dict:
        keys = self.value or ""
        base = {
            "kind": "native",
            "action": self.action,
            "text": keys if self.action == "type" else "",
            "keys": keys if self.action in ("hotkey", "press") else "",
            "elem_name": self.elem_name or self.target_ref,
            "elem_type": self.elem_type,
            "window_title": self.window_title,
            "instruction": self.target_desc or "",
            "value": self.value,
            "id": self.id,
            "irreversible": self.irreversible,
            **(self.extra or {}),
        }
        if self.action == "chain":
            extra = self.extra or {}
            base["clicks"] = extra.get("clicks") or []
            base["anchors"] = extra.get("anchors") or []
            base["click_count"] = extra.get("click_count") or len(base["clicks"])
        return base


@dataclass
class Plan:
    nodes: list
    source: str = "user"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "nodes": [n.to_dict() if isinstance(n, PlanNode) else n for n in self.nodes],
        }


def node_from_dict(d: dict) -> PlanNode:
    known = set(PlanNode.__dataclass_fields__)
    payload = {k: v for k, v in (d or {}).items() if k in known}
    extra = {k: v for k, v in (d or {}).items() if k not in known}
    node = PlanNode(**payload)
    node.extra.update(extra)
    return node


def plan_from_dict(d: Any) -> Plan:
    if isinstance(d, Plan):
        return d
    if isinstance(d, list):
        return Plan(nodes=[node_from_dict(x) for x in d])
    nodes = (d or {}).get("nodes") or []
    return Plan(
        nodes=[node_from_dict(n) for n in nodes],
        source=(d or {}).get("source") or "user",
    )
