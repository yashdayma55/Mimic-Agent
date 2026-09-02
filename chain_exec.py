"""Execute declared 2-click chain steps: click through, verify once at the end."""

from __future__ import annotations

import time

from plan_schema import PlanNode, node_from_dict
from safety_gate import is_irreversible_step


def _click_label(click_def: dict, anchor: dict | None) -> str:
    primary = (anchor or {}).get("primary") or {}
    return (
        click_def.get("elem_name")
        or primary.get("name")
        or click_def.get("target_desc")
        or "target"
    )


def _anchor_click_point(anchor: dict | None) -> tuple[int, int] | None:
    anchor = anchor or {}
    pt = anchor.get("point")
    if isinstance(pt, (list, tuple)) and len(pt) == 2:
        return int(pt[0]), int(pt[1])
    rect = anchor.get("screen_rect")
    if isinstance(rect, (list, tuple)) and len(rect) == 4:
        return int((rect[0] + rect[2]) / 2), int((rect[1] + rect[3]) / 2)
    return None


def _focus_target_window(wanted: str | None):
    from ui_runner import find_any_window, find_window, focus_window

    if not wanted:
        return None, None
    win, title = find_window(wanted)
    if win is None:
        win, title = find_any_window(wanted)
    if win is not None:
        focus_window(win)
    return win, title


def _perform_click(anchor: dict | None, el, layer: str, label: str) -> tuple[bool, tuple[int, int] | None, str]:
    from ui_runner import _click_wrapper

    taught = _anchor_click_point(anchor)
    if taught:
        try:
            import os_input

            os_input.click(taught[0], taught[1])
            return True, taught, f"taught_point {taught}"
        except Exception as e:
            return False, taught, f"taught_point click failed: {e}"
    if isinstance(el, tuple):
        xy = (int(el[0]), int(el[1]))
        try:
            import os_input

            os_input.click(xy[0], xy[1])
            return True, xy, f"crop_xy {xy}"
        except Exception as e:
            return False, xy, f"crop_xy click failed: {e}"
    if el is None:
        return False, None, f"{label!r} not found and no taught click point"
    return _click_wrapper(el)


def _layers_tried(layer: str) -> str:
    if layer in ("primary", "repaired_name"):
        return "primary"
    if layer == "parent_path":
        return "parent_path"
    if layer in ("crop", "crop_xy"):
        return "crop"
    return layer or "unknown"


def chain_irreversible_error(step) -> str | None:
    """Return error message if click 1 is irreversible; None if safe."""
    cc = int(getattr(step, "click_count", 1) or 1)
    if cc < 2:
        return None
    anchors = list(getattr(step, "anchors", None) or [])
    if not anchors:
        return None
    first = anchors[0] or {}
    primary = first.get("primary") or {}
    name = primary.get("name") or ""
    blob = f"{name} {getattr(step, 'user_description', '') or ''}"
    probe = {"elem_name": name, "description": blob, "instruction": blob}
    if is_irreversible_step(probe):
        return (
            "an irreversible action cannot be the first click in a chain — "
            "split this into two steps"
        )
    return None


def validate_chain_node(node: PlanNode) -> str | None:
    """Validate a compiled chain node before execution."""
    extra = node.extra or {}
    clicks = extra.get("clicks") or []
    anchors = extra.get("anchors") or []
    cc = int(extra.get("click_count") or len(clicks) or 1)
    if cc not in (1, 2):
        return f"click_count must be 1 or 2, got {cc}"
    if len(clicks) != cc:
        return f"chain must have exactly {cc} click(s), got {len(clicks)}"
    if cc == 2 and len(anchors) >= 1:
        first = anchors[0] or {}
        primary = first.get("primary") or {}
        name = primary.get("name") or node.elem_name or ""
        probe = {"elem_name": name, "description": name, "instruction": name}
        if is_irreversible_step(probe):
            return (
                "an irreversible action cannot be the first click in a chain — "
                "split this into two steps"
            )
    return None


def execute_chain_step(step: dict, last_window: str | None = None):
    """Perform click 1, settle, click 2; verify only after the final click."""
    from ui_runner import (
        StepResult,
        _el_name,
        foreground_title,
        infer_window_title,
    )

    extra = step.get("extra") or {}
    clicks = step.get("clicks") or extra.get("clicks") or []
    anchors = step.get("anchors") or extra.get("anchors") or []
    total = len(clicks)
    if total < 1:
        r = StepResult(ok=False, reason="chain has no clicks")
        return r

    wanted = infer_window_title(step, last_window)
    if not wanted:
        wanted = (step.get("window_title") or (extra or {}).get("target_window_hint") or "").strip() or None
    result = StepResult(ok=False, reason="", window_wanted=wanted)
    win = None
    found_title = None
    if wanted:
        win, found_title = _focus_target_window(wanted)
        result.window_found = found_title
        if win is None:
            result.reason = f"target window {wanted!r} not found — is the app open?"
            return result

    evidence: list[dict] = []

    for i, click_def in enumerate(clicks):
        idx = i + 1
        anchor = anchors[i] if i < len(anchors) else {}
        label = _click_label(click_def, anchor)
        mini = {
            "id": f"{step.get('id') or 'chain'}_c{idx}",
            "kind": "native",
            "action": "click",
            "elem_name": click_def.get("elem_name") or label,
            "elem_type": click_def.get("elem_type"),
            "window_title": click_def.get("window_title") or wanted,
            "extra": {"anchor": anchor},
        }
        node = node_from_dict(mini)
        from anchor_repair import resolve_with_anchor

        el, layer = resolve_with_anchor(node, wanted)
        layer_name = _layers_tried(layer)
        elem_name = label
        if el is None and _anchor_click_point(anchor) is None:
            result.reason = (
                f"failed at click {idx} of {total}: {label!r} not found "
                f"(primary and parent_path both missed)"
            )
            result.lines.append(f"  chain evidence={evidence}")
            return result
        if el is None:
            layer_name = "taught_point"
        clicked, xy, how = _perform_click(anchor, el, layer_name, label)
        if not clicked:
            result.reason = (
                f"failed at click {idx} of {total}: {label!r} could not be clicked ({how})"
            )
            result.lines.append(f"  chain evidence={evidence}")
            return result
        elem_name = label
        if not isinstance(el, tuple) and el is not None:
            elem_name = _el_name(el) or label
        evidence.append({
            "click_index": idx,
            "layer": layer_name,
            "element": elem_name,
            "coords": list(xy) if xy else None,
        })
        result.lines.append(
            f"  chain click {idx}/{total}: {elem_name!r} via {layer_name} at {xy}"
        )
        if idx < total:
            time.sleep(0.35)

    if len(evidence) < total or not all((e or {}).get("coords") for e in evidence):
        result.reason = "chain did not complete all clicks"
        result.lines.append(f"  chain evidence={evidence}")
        return result
    result.ok = True
    result.reason = f"chain completed ({total} click(s))"
    result.window_found = result.window_found or found_title or foreground_title()
    result.lines.append(f"  chain evidence={evidence}")
    result.value_after = str(evidence)
    return result
