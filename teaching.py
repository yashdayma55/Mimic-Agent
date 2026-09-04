"""Per-step teaching data model. Knowledge, not execution.

Persists to workflows/<name>/teaching.json.
Approved steps are never silently overwritten.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional

from workflow_folder import safe_name, workflow_dir

STATUSES = ("draft", "questioning", "understood", "demonstrated", "approved")
MAX_CASES_PER_STEP = 3
CASE_ORIGIN_HALT = "halt"
CASE_ORIGIN_USER_CAPTURED = "user_captured"
CASE_ORIGIN_USER_DESCRIBED = "user_described"
CASE_ORIGINS = (CASE_ORIGIN_HALT, CASE_ORIGIN_USER_CAPTURED, CASE_ORIGIN_USER_DESCRIBED)


class TeachingError(ValueError):
    pass


@dataclass
class StepCase:
    id: str
    created_from: str
    trigger: dict
    resolution: dict
    success_check: dict
    evidence: dict = field(default_factory=dict)
    origin_note: str | None = None
    halted_at_step: str | None = None
    times_matched: int = 0
    last_matched: str | None = None
    sub_step: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TaughtStep:
    id: str
    order: int
    user_description: str
    varies_note: str = ""
    parameters: list = field(default_factory=list)
    produces: list = field(default_factory=list)
    consumes: list = field(default_factory=list)
    qa_history: list = field(default_factory=list)
    click_count: int = 1
    anchors: list = field(default_factory=list)
    anchor: dict | None = None
    action: dict | None = None
    status: str = "draft"
    rehearsal: dict | None = None
    understanding: dict | None = None
    demo: dict | None = None
    reflection: dict | None = None
    memory_note: str = ""
    web_allowed: bool = False
    learned: dict | None = None
    photos: list = field(default_factory=list)
    is_start: bool = False
    loop_role: str | None = None
    chain_capture: dict | None = None
    last_capture: dict | None = None
    edit_notice: str | None = None
    after_frame: dict | None = None
    before_state: dict | None = None
    success_candidates: list = field(default_factory=list)
    expected_start_frame: str | None = None
    cases: list = field(default_factory=list)
    case_halt: dict | None = None
    case_authoring: dict | None = None
    vision_chat: list = field(default_factory=list)
    method: str = "anchor"
    prompt_instruction: str = ""

    def to_dict(self) -> dict:
        sync_step_anchors(self)
        d = asdict(self)
        if self.anchors:
            d["anchor"] = self.anchors[0]
        if self.cases:
            d["cases"] = [
                c.to_dict() if isinstance(c, StepCase) else c for c in self.cases
            ]
        return d


@dataclass
class TaughtWorkflow:
    name: str
    context: str = ""
    start_screen: dict | None = None
    steps: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "context": self.context,
            "start_screen": self.start_screen,
            "steps": [s.to_dict() if isinstance(s, TaughtStep) else s for s in self.steps],
        }


_STEP_FIELDS = {f.name for f in fields(TaughtStep)}


def validate_click_count(n) -> int:
    try:
        v = int(n)
    except (TypeError, ValueError):
        raise TeachingError(f"click_count must be 1 or 2, got {n!r}")
    if v not in (1, 2):
        raise TeachingError(f"click_count must be 1 or 2, got {v}")
    return v


def sync_step_anchors(step: TaughtStep) -> None:
    """Keep anchor (alias) and anchors list in sync."""
    if step.anchors:
        step.anchor = step.anchors[0]
    elif step.anchor:
        step.anchors = [step.anchor]
    else:
        step.anchors = []
        step.anchor = None


def validate_chain_action(step: TaughtStep) -> None:
    """A chain action must have exactly click_count click entries."""
    action = step.action or {}
    if (action.get("action") or "") != "chain":
        return
    clicks = action.get("clicks") or []
    cc = validate_click_count(getattr(step, "click_count", 1) or 1)
    if len(clicks) != cc:
        raise TeachingError(
            f"chain must have exactly {cc} click(s), got {len(clicks)}"
        )


def step_from_dict(d: dict) -> TaughtStep:
    payload = {k: v for k, v in (d or {}).items() if k in _STEP_FIELDS}
    if "status" in payload and payload["status"] not in STATUSES:
        payload["status"] = "draft"
    if payload.get("click_count") is not None:
        payload["click_count"] = validate_click_count(payload["click_count"])
    if not payload.get("anchors") and payload.get("anchor"):
        payload["anchors"] = [payload["anchor"]]
    if payload.get("cases"):
        from step_cases import step_case_from_dict

        payload["cases"] = [
            step_case_from_dict(c) if isinstance(c, dict) else c for c in payload["cases"]
        ]
    step = TaughtStep(**payload)
    sync_step_anchors(step)
    return step


def workflow_from_dict(d: dict) -> TaughtWorkflow:
    steps = [step_from_dict(s) for s in (d or {}).get("steps") or []]
    return TaughtWorkflow(
        name=(d or {}).get("name") or "",
        context=(d or {}).get("context") or "",
        start_screen=(d or {}).get("start_screen"),
        steps=steps,
    )


def teaching_path(name: str) -> str:
    return os.path.join(workflow_dir(name), "teaching.json")


def _steps_equal(a: TaughtStep, b: TaughtStep) -> bool:
    return a.to_dict() == b.to_dict()


def save_taught(wf: TaughtWorkflow) -> str:
    stem = safe_name(wf.name)
    wf.name = stem
    path = teaching_path(stem)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.isfile(path):
        existing = load_taught(stem)
        approved = {s.id: s for s in existing.steps if s.status == "approved"}
        merged: list[TaughtStep] = []
        for s in wf.steps:
            disk = approved.get(s.id)
            if disk is None:
                merged.append(s)
                continue
            if s.status != "approved":
                if disk.status == "approved":
                    # Demotion or any edit that leaves step non-approved replaces approved disk row.
                    merged.append(s)
                    continue
                if s.rehearsal:
                    disk.rehearsal = s.rehearsal
                if s.demo:
                    disk.demo = s.demo
                if s.reflection:
                    disk.reflection = s.reflection
                if getattr(s, "learned", None):
                    disk.learned = s.learned
                if getattr(s, "photos", None):
                    disk.photos = s.photos
                if getattr(s, "memory_note", "") != getattr(disk, "memory_note", ""):
                    disk.memory_note = s.memory_note
                disk.web_allowed = bool(getattr(s, "web_allowed", False))
                merged.append(disk)
                continue
            if not _steps_equal(disk, s):
                # Explicit save of an approved step (notes, memory, learned).
                merged.append(s)
                continue
            merged.append(s)
        wf.steps = merged
    with open(path, "w", encoding="utf-8") as f:
        json.dump(wf.to_dict(), f, indent=2)
    return path


def get_step(wf: TaughtWorkflow, step_id: str) -> TaughtStep:
    for s in wf.steps:
        if s.id == step_id:
            return s
    raise TeachingError(f"no step {step_id!r}")


def next_step_id(wf: TaughtWorkflow) -> str:
    n = len(wf.steps)
    return f"s{n + 1}"


def load_taught(name: str) -> TaughtWorkflow:
    path = teaching_path(name)
    if not os.path.isfile(path):
        return TaughtWorkflow(name=safe_name(name))
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    wf = workflow_from_dict(data)
    wf.name = safe_name(name)
    return wf
