"""Durable, re-groundable UI element descriptors (three parallel layers)."""

from __future__ import annotations

from pydantic import BaseModel, model_validator


class A11yRef(BaseModel):
    automation_id: str | None = None
    name: str | None = None
    control_type: str | None = None
    path_from_anchor: str | None = None  # e.g. "Toolbar/Button[3]"
    anchor_name: str | None = None  # stable parent container
    # When Name is not a stable literal (LinkedIn heading, Apollo email line):
    name_contains: str | None = None
    name_regex: str | None = None
    nth_of_type: int | None = None  # 1-based among type (+ name filters) hits
    # Scope search to descendants of this named container (Apollo panel, compose dialog).
    subtree_root: str | None = None


class VisualRef(BaseModel):
    crop_path: str | None = None  # cropped image of the element
    neighbor_crop_path: str | None = None  # element + surrounding context
    rel_bbox: tuple[float, float, float, float] | None = None  # normalized 0-1


class SemanticRef(BaseModel):
    description: str  # "Extensions puzzle-piece icon in Chrome toolbar"
    app: str  # "chrome.exe"
    window_title_hint: str | None = None


class ElementRef(BaseModel):
    a11y: A11yRef | None = None
    visual: VisualRef | None = None
    semantic: SemanticRef

    @model_validator(mode="after")
    def _require_grounding_layer(self) -> ElementRef:
        if self.a11y is None and self.visual is None:
            raise ValueError(
                "ElementRef requires at least one of a11y or visual "
                "(semantic alone is not enough to re-ground)"
            )
        return self
