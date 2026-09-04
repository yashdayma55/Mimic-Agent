"""Hover — detect revealing dwells and execute hover precursor actions."""

from __future__ import annotations

import time
from typing import Any

DWELL_MS = 500
DWELL_RADIUS_PX = 8
REPERCEIVE_MS = 400
DEFAULT_HOVER_DWELL_MS = 600

_STATE_CHANGING = frozenset({"click", "type", "paste", "copy"})


def _a11y_key(el: dict) -> tuple:
    return ((el.get("name") or "").strip().lower(), (el.get("control_type") or "").strip().lower())


def diff_new_a11y(before: list[dict], after: list[dict]) -> list[dict]:
    seen = {_a11y_key(e) for e in (before or [])}
    out = []
    for el in after or []:
        if not (el.get("name") or "").strip():
            continue
        if _a11y_key(el) not in seen:
            out.append(dict(el))
    return out


def snapshot_a11y_elements() -> list[dict]:
    from success_signals import _collect_a11y_elements

    return list(_collect_a11y_elements() or [])


def analyze_dwell(x: int, y: int) -> dict | None:
    """Return revealing hover dict or None if nothing new appeared."""
    before = snapshot_a11y_elements()
    time.sleep(REPERCEIVE_MS / 1000.0)
    after = snapshot_a11y_elements()
    revealed = diff_new_a11y(before, after)
    if not revealed:
        return None
    return {
        "action": "hover",
        "point": [int(x), int(y)],
        "revealed": revealed,
        "before_count": len(before),
        "after_count": len(after),
    }


def execute_hover(step: dict, *, dwell_ms: int = DEFAULT_HOVER_DWELL_MS) -> dict:
    """Move to target, dwell, re-perceive. ok=False if nothing revealed."""
    from chain_exec import _anchor_click_point
    from ui_runner import StepResult, resolve_element, infer_window_title, find_window

    wanted = infer_window_title(step, None)
    anchor = (step.get("extra") or {}).get("anchor") or step.get("anchor")
    anchors = step.get("anchors") or (step.get("extra") or {}).get("anchors") or []
    if not anchor and anchors:
        anchor = anchors[0]
    pt = _anchor_click_point(anchor) if anchor else None
    if not pt:
        point = step.get("point") or (anchor or {}).get("point")
        if isinstance(point, (list, tuple)) and len(point) == 2:
            pt = int(point[0]), int(point[1])
    name = (step.get("elem_name") or "").strip()
    etype = (step.get("elem_type") or "").strip()
    win = None
    if wanted:
        win, _ = find_window(wanted)
    if pt is None and win is not None and name:
        el = resolve_element(win, name, etype)
        if el is not None:
            from ui_runner import _rect, _center

            rect = _rect(el)
            if rect:
                pt = _center(rect)
    if pt is None:
        return StepResult(ok=False, reason="hover has no target point or element").__dict__

    import os_input

    os_input.move_to(pt[0], pt[1])
    time.sleep(max(0.05, dwell_ms / 1000.0))
    before = snapshot_a11y_elements()
    time.sleep(REPERCEIVE_MS / 1000.0)
    after = snapshot_a11y_elements()
    revealed = diff_new_a11y(before, after)
    if not revealed:
        return StepResult(
            ok=False,
            reason="hover revealed nothing",
            click_xy=pt,
            element_name=name or None,
        ).__dict__
    return StepResult(
        ok=True,
        reason=f"hover revealed {len(revealed)} element(s)",
        click_xy=pt,
        value_after=str(revealed[:5]),
    ).__dict__


def propose_hover_click_chain(hover: dict, click_event: dict) -> dict | None:
    """Compose hover→click when dwell revealed elements then user clicked."""
    if not hover or not hover.get("revealed"):
        return None
    click_pt = click_event.get("point") or click_event.get("coords")
    if not click_pt:
        return None
    hover_pt = hover.get("point")
    if hover_pt and click_pt:
        dx = abs(int(click_pt[0]) - int(hover_pt[0]))
        dy = abs(int(click_pt[1]) - int(hover_pt[1]))
        if dx > 400 or dy > 400:
            return None
    revealed = hover.get("revealed") or []
    primary = revealed[0] if revealed else {}
    click_anchor = dict(click_event.get("anchor") or {})
    if not click_anchor.get("primary"):
        click_anchor["primary"] = {
            "name": primary.get("name") or "revealed control",
            "control_type": primary.get("control_type") or "Button",
            "pipeline": "hover_reveal",
        }
    click_anchor["point"] = list(click_pt)
    click_anchor["hover_reveal"] = revealed
    return {
        "action": "chain",
        "chain_kind": "interaction",
        "parts": [
            {"action": "hover", "point": hover_pt, "target_desc": "hover target"},
            {
                "action": "click",
                "elem_name": primary.get("name"),
                "elem_type": primary.get("control_type"),
                "point": list(click_pt),
            },
        ],
        "clicks": [
            {"action": "hover", "target_desc": "hover to reveal"},
            {
                "action": "click",
                "elem_name": primary.get("name"),
                "elem_type": primary.get("control_type"),
            },
        ],
        "anchors": [dict(hover), click_anchor],
        "click_count": 2,
        "prompt": (
            "You hovered to reveal controls, then clicked. Save as hover → click?"
        ),
    }
