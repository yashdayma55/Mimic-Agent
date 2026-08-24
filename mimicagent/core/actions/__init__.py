"""Typed action registry for MimicAgent Phase 0."""

from __future__ import annotations

from mimicagent.core.actions.base import ActionSpec
from mimicagent.core.actions import (
    click,
    copy,
    extract,
    focus_app,
    human_approve,
    llm_generate,
    navigate,
    paste,
    read_text,
    scroll_to,
    type_text,
    wait_for,
)

REGISTRY: dict[str, ActionSpec] = {
    focus_app.SPEC.name: focus_app.SPEC,
    navigate.SPEC.name: navigate.SPEC,
    click.SPEC.name: click.SPEC,
    type_text.SPEC.name: type_text.SPEC,
    read_text.SPEC.name: read_text.SPEC,
    wait_for.SPEC.name: wait_for.SPEC,
    extract.SPEC.name: extract.SPEC,
    copy.SPEC.name: copy.SPEC,
    paste.SPEC.name: paste.SPEC,
    scroll_to.SPEC.name: scroll_to.SPEC,
    llm_generate.SPEC.name: llm_generate.SPEC,
    human_approve.SPEC.name: human_approve.SPEC,
}


def get_action(name: str) -> ActionSpec:
    key = (name or "").strip()
    if key not in REGISTRY:
        known = ", ".join(sorted(REGISTRY))
        raise KeyError(f"Unknown action {name!r}. Known: {known}")
    return REGISTRY[key]


__all__ = ["REGISTRY", "get_action", "ActionSpec"]
