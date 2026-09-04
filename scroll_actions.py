"""Scroll to a destination — never store pixel deltas."""

from __future__ import annotations

import time
from typing import Any


def scroll_intent_from_teaching(
    *,
    to_find: str,
    within: str | None = None,
    interacted_element: str | None = None,
) -> dict:
    target = (to_find or interacted_element or "").strip()
    if not target:
        raise ValueError("scroll intent needs a destination")
    return {
        "action": "scroll",
        "to_find": target,
        "within": (within or "").strip() or None,
        "max_steps": 8,
    }


def _target_visible(to_find: str, elements: list[dict]) -> bool:
    needle = (to_find or "").strip().lower()
    if not needle:
        return False
    for el in elements or []:
        name = (el.get("name") or "").strip().lower()
        if needle in name or name in needle:
            return True
    return False


def execute_scroll(step: dict) -> dict:
    """Scroll in increments until to_find is visible or max_steps reached."""
    from ui_runner import StepResult, infer_window_title, find_window, resolve_element, _rect, _center
    from hover_actions import snapshot_a11y_elements

    to_find = (step.get("to_find") or step.get("value") or "").strip()
    within = (step.get("within") or "").strip() or None
    max_steps = int(step.get("max_steps") or 8)
    if not to_find:
        return StepResult(ok=False, reason="scroll requires to_find destination").__dict__

    wanted = within or infer_window_title(step, None)
    scroll_xy = None
    if wanted:
        win, _ = find_window(wanted)
        if win is not None:
            el = resolve_element(win, within or "", None) if within else None
            if el is not None:
                rect = _rect(el)
                if rect:
                    scroll_xy = _center(rect)
    if scroll_xy is None:
        scroll_xy = (800, 400)

    import os_input

    for i in range(max_steps):
        if _target_visible(to_find, snapshot_a11y_elements()):
            return StepResult(
                ok=True,
                reason=f"scroll stopped at step {i + 1}: '{to_find}' visible",
            ).__dict__
        os_input.scroll_at(scroll_xy[0], scroll_xy[1], -3)
        time.sleep(0.25)

    return StepResult(
        ok=False,
        reason=f"scrolled {max_steps} times, {to_find!r} never appeared",
    ).__dict__
