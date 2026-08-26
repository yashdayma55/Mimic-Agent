"""Halt-and-repair: one screenshot, a click, a composite anchor."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from plan_schema import PlanNode

try:
    from PIL import Image, ImageGrab
except Exception:
    Image = None
    ImageGrab = None


@dataclass
class HaltState:
    node_id: str
    looking_for: str
    screenshot_path: str
    reason: str


def capture_halt_screenshot(workflow_dir: str, node_id: str) -> str:
    os.makedirs(os.path.join(workflow_dir, "repairs"), exist_ok=True)
    path = os.path.join(workflow_dir, "repairs", f"halt_{node_id}.png")
    if ImageGrab is None:
        raise RuntimeError("Pillow ImageGrab unavailable")
    img = ImageGrab.grab()
    img.save(path)
    return path


def crop_around(screenshot_path: str, x: int, y: int, size: int = 64) -> str:
    if Image is None:
        raise RuntimeError("Pillow unavailable")
    img = Image.open(screenshot_path)
    half = size // 2
    box = (max(0, x - half), max(0, y - half), x + half, y + half)
    crop = img.crop(box)
    base, ext = os.path.splitext(screenshot_path)
    out = base + "_crop" + (ext or ".png")
    crop.save(out)
    return out


def element_at_point(x: int, y: int) -> dict:
    info = {"name": None, "control_type": None, "parent_path": None}
    try:
        from pywinauto import Desktop

        el = Desktop(backend="uia").from_point(int(x), int(y))
        info["name"] = (el.element_info.name or "").strip() or None
        info["control_type"] = el.element_info.control_type
        try:
            parent = el.parent()
            info["parent_path"] = (parent.element_info.name or "") + "/" + (
                el.element_info.control_type or ""
            )
        except Exception:
            info["parent_path"] = info["control_type"]
    except Exception:
        pass
    return info


def apply_repair_click(node: PlanNode, screenshot_path: str, x: int, y: int,
                       workflow_dir: str) -> PlanNode:
    crop_path = crop_around(screenshot_path, x, y, 64)
    a11y = element_at_point(x, y)
    extra = dict(node.extra or {})
    extra["anchor"] = {
        "primary_selector": node.elem_name or node.target_ref or a11y.get("name"),
        "parent_path": a11y.get("parent_path"),
        "crop_path": crop_path,
        "repair_xy": [int(x), int(y)],
        "repaired_name": a11y.get("name"),
        "repaired_type": a11y.get("control_type"),
    }
    data = node.to_dict()
    extra_name = a11y.get("name")
    if extra_name and not data.get("elem_name"):
        data["elem_name"] = extra_name
    data["extra"] = extra
    from plan_schema import node_from_dict

    return node_from_dict(data)


def _template_match(crop_path: str):
    if not crop_path or not os.path.isfile(crop_path):
        return None
    try:
        import pyautogui

        box = pyautogui.locateOnScreen(crop_path, confidence=0.8)
        if not box:
            box = pyautogui.locateOnScreen(crop_path)
        if not box:
            return None
        return (int(box.left + box.width / 2), int(box.top + box.height / 2))
    except Exception:
        try:
            from PIL import ImageGrab

            if Image is None:
                return None
            hay = ImageGrab.grab()
            needle = Image.open(crop_path)
            # brute-force small images only
            if needle.size[0] * needle.size[1] > 80 * 80:
                return None
            hx, hy = hay.size
            nx, ny = needle.size
            needle_px = list(needle.convert("RGB").getdata())
            hay_rgb = hay.convert("RGB")
            step = 8
            for y in range(0, hy - ny, step):
                for x in range(0, hx - nx, step):
                    patch = list(hay_rgb.crop((x, y, x + nx, y + ny)).getdata())
                    if patch[:20] == needle_px[:20]:
                        return (x + nx // 2, y + ny // 2)
        except Exception:
            return None
    return None


LAST_RESOLVE_LOG = ""


def resolve_with_anchor(node: PlanNode, window_title: str | None = None):
    """primary → parent_path → crop template. Returns (element_or_None, layer).
    Teaching-time multi-witness is not re-run here."""
    global LAST_RESOLVE_LOG
    from ui_runner import find_window, resolve_element

    win = None
    if window_title or node.window_title:
        win, _ = find_window(window_title or node.window_title)
    anchor = (node.extra or {}).get("anchor") or {}
    reason = (anchor.get("primary_reason") or "").strip()
    reason_bit = f"; primary was chosen because {reason}" if reason else ""

    def _log(layer: str, failed_primary: bool = False) -> str:
        global LAST_RESOLVE_LOG
        if failed_primary and reason:
            line = f"resolved via {layer} (primary failed; primary was chosen because {reason})"
        elif failed_primary:
            line = f"resolved via {layer} (primary failed)"
        elif reason:
            line = f"resolved via {layer} (primary was chosen because {reason})"
        else:
            line = f"resolved via {layer}"
        LAST_RESOLVE_LOG = line
        return layer

    primary = anchor.get("primary") if isinstance(anchor.get("primary"), dict) else {}
    name = (
        primary.get("name")
        or anchor.get("primary_selector")
        or node.elem_name
        or node.target_ref
    )
    etype = primary.get("control_type") or node.elem_type
    el = resolve_element(win, name or "", etype)
    if el is not None:
        return el, _log("primary")
    repaired = anchor.get("repaired_name")
    if repaired:
        el = resolve_element(win, repaired, anchor.get("repaired_type") or etype)
        if el is not None:
            return el, _log("repaired_name", True)
    parent_path = anchor.get("parent_path") or primary.get("parent_path")
    if parent_path and win is not None:
        try:
            parts = parent_path.split("/")
            hits = win.descendants(control_type=parts[-1]) if len(parts) > 1 else []
            if hits:
                return hits[0], _log("parent_path", True)
        except Exception:
            pass
    crop = anchor.get("crop_path") or (primary.get("crop_path") if isinstance(primary, dict) else None)
    xy = _template_match(crop)
    if xy:
        try:
            from pywinauto import Desktop

            return Desktop(backend="uia").from_point(*xy), _log("crop", True)
        except Exception:
            return xy, _log("crop_xy", True)
    LAST_RESOLVE_LOG = f"resolved via miss{reason_bit}"
    return None, "miss"
