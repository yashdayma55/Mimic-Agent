"""Show-me capture: a11y + DOM + 64x64 crop at a point. No execution."""

from __future__ import annotations

import os
import re
import time
from concurrent import futures
from datetime import datetime, timezone

from teaching import TaughtWorkflow, get_step, save_taught
from workflow_folder import workflow_dir

try:
    from PIL import Image, ImageGrab
except Exception:
    Image = None
    ImageGrab = None


def _element_at(x: int, y: int) -> dict:
    info = {
        "name": None,
        "control_type": None,
        "automation_id": None,
        "rect": None,
        "parent_path": None,
    }
    try:
        from pywinauto import Desktop

        el = Desktop(backend="uia").from_point(int(x), int(y))
        ei = el.element_info
        info["name"] = (ei.name or "").strip() or None
        info["control_type"] = ei.control_type
        info["automation_id"] = getattr(ei, "automation_id", None) or None
        r = el.rectangle()
        info["rect"] = [int(r.left), int(r.top), int(r.right), int(r.bottom)]
        try:
            parent = el.parent()
            info["parent_path"] = f"{parent.element_info.name or ''}/{ei.control_type}"
        except Exception:
            info["parent_path"] = info["control_type"]
    except Exception:
        pass
    return info


_element_at = _element_at


def _browser_at(x: int, y: int) -> dict:
    try:
        from anchor_repair import element_at_point

        return element_at_point(x, y) or {}
    except Exception:
        return {}


def _virtual_screen() -> tuple[int, int, int, int]:
    try:
        import ctypes

        u = ctypes.windll.user32
        return (
            int(u.GetSystemMetrics(76)),
            int(u.GetSystemMetrics(77)),
            int(u.GetSystemMetrics(78)),
            int(u.GetSystemMetrics(79)),
        )
    except Exception:
        return (0, 0, 1920, 1080)


def _list_monitors() -> list[tuple[int, int, int, int]]:
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        MonitorEnumProc = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_ulonglong, ctypes.c_ulonglong,
            ctypes.POINTER(RECT), ctypes.c_ulonglong,
        )
        found: list[tuple[int, int, int, int]] = []

        def _cb(hmon, hdc, lprc, lparam):
            r = lprc.contents
            found.append((int(r.left), int(r.top), int(r.right), int(r.bottom)))
            return 1

        cb = MonitorEnumProc(_cb)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, cb, 0)
        return found
    except Exception:
        return []


def _monitor_at(x: int, y: int) -> tuple[int, int, int, int]:
    for m in _list_monitors():
        if m[0] <= x < m[2] and m[1] <= y < m[3]:
            return m
    vx, vy, vw, vh = _virtual_screen()
    return (vx, vy, vx + vw, vy + vh)


def _to_image_box(box, origin, img_size):
    ox, oy = origin
    l, t, r, b = box
    return _clamp_box((l - ox, t - oy, r - ox, b - oy), img_size)


def cursor_point() -> tuple[int, int]:
    try:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return int(pt.x), int(pt.y)
    except Exception:
        return _fallback_point()


def _window_rect_at(x: int, y: int) -> tuple[int, int, int, int] | None:
    try:
        from pywinauto import Desktop

        el = Desktop(backend="uia").from_point(int(x), int(y))
        win = el.top_level_parent()
        r = win.rectangle()
        return int(r.left), int(r.top), int(r.right), int(r.bottom)
    except Exception:
        return None


def _grab_screen():
    if ImageGrab is None:
        raise RuntimeError("Pillow ImageGrab unavailable")
    try:
        img = ImageGrab.grab(all_screens=True)
        vx, vy, _vw, _vh = _virtual_screen()
        return img, (vx, vy)
    except TypeError:
        return ImageGrab.grab(), (0, 0)


def _clamp_box(box, size):
    l, t, r, b = [int(v) for v in box]
    w, h = size
    l = max(0, min(l, w - 1))
    t = max(0, min(t, h - 1))
    r = max(l + 1, min(r, w))
    b = max(t + 1, min(b, h))
    return (l, t, r, b)


_AGREE_PX = 48
_WITNESS_TIMEOUT = 4.0
_CURSOR_VISION_TIMEOUT = 12.0
_A11Y_OVERLAY_NOISE = frozenset({"close side panel", "close", "dismiss"})


def _run_timed(fn, *args, timeout: float = _WITNESS_TIMEOUT):
    with futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn, *args)
        try:
            return fut.result(timeout=timeout), False
        except futures.TimeoutError:
            return None, True
        except Exception:
            return None, False


def _combined_vlm_witness(
    ctx_path: str | None,
    x: int,
    y: int,
    a11y_raw: dict,
    a11y_account: str,
) -> dict:
    """One VLM call: screenshot + structured a11y account together."""
    rect = a11y_raw.get("rect") or [x - 32, y - 32, x + 32, y + 32]
    out = {
        "saw": False,
        "described": None,
        "rect": rect,
        "confidence": "low",
        "account": "vision did not run.",
        "model_judgement": "unknown",
        "agrees_with_a11y": None,
    }
    img_path = ctx_path
    if not img_path or not os.path.isfile(img_path):
        return out
    try:
        key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_key.txt")
        key = open(key_path, encoding="utf-8").read().strip() if os.path.isfile(key_path) else ""
        if not key:
            return out
        from vision_api import ask_vision_with_prompt

        name = a11y_raw.get("name") or "unnamed"
        ctype = a11y_raw.get("control_type") or "element"
        path = a11y_raw.get("parent_path") or "the window"
        rect_s = f"[{rect[0]},{rect[1]},{rect[2]},{rect[3]}]" if len(rect) >= 4 else "unknown"
        prompt = (
            "Here is the screen at the moment of the click. "
            f"The accessibility tree reports: a {ctype} named {name!r}, "
            f"inside {path}, rect {rect_s}. "
            f"The user clicked at ({x}, {y}). "
            "Describe what was clicked and whether the two accounts agree. "
            'Respond ONLY with JSON: {"account": "short description", '
            '"agrees_with_a11y": true/false, "judgement": "agree"|"disagree"|"unknown"}'
        )
        with open(img_path, "rb") as f:
            raw = f.read()
        vis = ask_vision_with_prompt(raw, key, prompt)
        account = (vis.get("account") or vis.get("what_you_see") or "").strip()
        judgement = (vis.get("judgement") or "unknown").lower()
        agrees = vis.get("agrees_with_a11y")
        if account:
            out["saw"] = True
            out["described"] = account
            out["account"] = _clip_account(account)
            out["confidence"] = "medium"
            out["model_judgement"] = judgement if judgement in ("agree", "disagree", "unknown") else "unknown"
            out["agrees_with_a11y"] = bool(agrees) if agrees is not None else None
    except Exception:
        pass
    return out


def _sentence_count(text: str) -> int:
    return len([p for p in re.split(r"[.!?]+", (text or "").strip()) if p.strip()])


def _clip_account(text: str) -> str:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if p.strip()]
    return " ".join(parts[:2]) if parts else "saw nothing."


def _rect_center(rect) -> tuple[float, float] | None:
    if not rect or len(rect) < 4:
        return None
    l, t, r, b = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    return ((l + r) / 2.0, (t + b) / 2.0)


def _rects_agree(a, b, tol: int = _AGREE_PX) -> bool:
    if not a or not b or len(a) < 4 or len(b) < 4:
        return False
    ax1, ay1, ax2, ay2 = [float(v) for v in a[:4]]
    bx1, by1, bx2, by2 = [float(v) for v in b[:4]]
    overlap = ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1
    if overlap:
        return True
    ac = _rect_center(a)
    bc = _rect_center(b)
    if ac is None or bc is None:
        return False
    dx, dy = ac[0] - bc[0], ac[1] - bc[1]
    return (dx * dx + dy * dy) ** 0.5 <= tol


def _a11y_account(info: dict) -> str:
    if not (info.get("name") or info.get("control_type") or info.get("rect")):
        return "the accessibility tree saw nothing here."
    name = info.get("name") or "unnamed"
    ctype = info.get("control_type") or "element"
    path = info.get("parent_path") or "the window"
    return _clip_account(f"a {ctype} named {name!r} inside {path}.")


def _dom_account(info: dict) -> str:
    if not info or not (info.get("selector") or info.get("text") or info.get("role") or info.get("name")):
        return "not page content — nothing here."
    bits = []
    if info.get("role") or info.get("control_type"):
        bits.append(str(info.get("role") or info.get("control_type")))
    if info.get("text") or info.get("name"):
        bits.append(repr(info.get("text") or info.get("name")))
    if info.get("selector"):
        bits.append(f"({info['selector']})")
    return _clip_account("page element " + " ".join(bits) + ".")


def _vision_account(info: dict) -> str:
    if not info.get("saw"):
        return info.get("account") or "vision saw nothing usable here."
    desc = (info.get("described") or "").strip()
    if desc:
        return _clip_account(desc if desc.endswith((".", "!", "?")) else desc + ".")
    return "a visual patch around the pointer."


def _a11y_confidence(info: dict) -> str:
    if info.get("name") and info.get("rect"):
        return "high"
    if info.get("name") or info.get("control_type"):
        return "medium"
    return "low"


_NON_INTERACTIVE = frozenset({
    "Text", "Label", "Static", "StaticText", "text", "label", "static",
})
_INTERACTIVE = frozenset({
    "Button", "MenuItem", "Hyperlink", "ListItem", "TabItem",
    "button", "menuitem", "hyperlink", "listitem", "tabitem",
})


def _normalize_ctype(ct: str | None) -> str:
    if not ct:
        return ""
    s = str(ct).strip()
    if s.endswith("Control"):
        s = s[:-7]
    return s


def _is_non_interactive(ct: str | None) -> bool:
    return _normalize_ctype(ct) in _NON_INTERACTIVE


def _is_interactive(ct: str | None) -> bool:
    return _normalize_ctype(ct) in _INTERACTIVE
_CONFIRM_CROP_W = 200
_CONFIRM_CROP_H = 120
_CURSOR_CROP_HALF = 56


def _point_in_rect(x: int, y: int, rect, margin: int = 4) -> bool:
    if not rect or len(rect) < 4:
        return False
    l, t, r, b = [int(v) for v in rect[:4]]
    return (l - margin) <= x <= (r + margin) and (t - margin) <= y <= (b + margin)


def _labels_overlap(a: str, b: str) -> bool:
    a = re.sub(r"[^a-z0-9]+", " ", (a or "").lower()).strip()
    b = re.sub(r"[^a-z0-9]+", " ", (b or "").lower()).strip()
    if not a or not b:
        return True
    if a in b or b in a:
        return True
    return bool(set(a.split()) & set(b.split()))


def infer_click_count_from_description(description: str) -> int | None:
    """Return 2 when the step text clearly describes a two-click chain."""
    d = (description or "").lower()
    if re.search(r"\b(first click|second click|then click|two click|2[\s-]?click)\b", d):
        return 2
    if " as a first click " in d and ("second click" in d or "then click" in d):
        return 2
    return None


def _click_confirm_label(step_description: str, sub_index: int = 0, primary: dict | None = None) -> str:
    name = (primary or {}).get("name")
    if name:
        return str(name)
    d = (step_description or "").lower()
    if sub_index <= 0:
        if "extensions" in d:
            return "the Extensions toolbar button (puzzle-piece icon)"
        return "the first click target"
    if "apollo" in d:
        return "the yellow Apollo.io icon in the Extensions dropdown"
    return "the second click target"


def _click_hints(step_description: str, sub_index: int = 0) -> list[str]:
    hints: list[str] = []
    d = (step_description or "").lower()
    if sub_index <= 0:
        hints.extend(["extensions", "puzzle"])
        for word in ("extensions", "puzzle"):
            if word in d and word not in hints:
                hints.append(word)
    else:
        hints.extend(["apollo", "yellow"])
        for word in ("apollo", "apollo.io", "yellow"):
            if word in d and word not in hints:
                hints.append(word)
    return hints


def _hint_score(label: str, hints: list[str]) -> int:
    lab = (label or "").lower()
    if not lab:
        return 0
    score = 0
    for h in hints:
        if re.search(r"\b" + re.escape(h.lower()) + r"\b", lab):
            score += 2
    return score


def _a11y_is_overlay_noise(name: str | None, step_description: str, sub_index: int = 0) -> bool:
    n = (name or "").lower().strip()
    if not n:
        return False
    if n in _A11Y_OVERLAY_NOISE or "close side panel" in n:
        hints = _click_hints(step_description, sub_index)
        return bool(hints)
    return False


def _snapshot_structural_at_click(x: int, y: int) -> tuple[dict, dict]:
    """Capture a11y/DOM at click instant — must run before UI finishes changing."""
    return _element_at(x, y), _browser_at(x, y)


def _crop_cursor_target(img, origin, x: int, y: int, dest: str, half: int = _CURSOR_CROP_HALF) -> tuple[str, tuple[int, int]]:
    """Tight crop centered on click; yellow crosshair marks exact cursor point."""
    ox, oy = int(origin[0]), int(origin[1])
    box = (x - half, y - half, x + half, y + half)
    ibox = _to_image_box(box, origin, img.size)
    crop = img.crop(ibox).copy()
    local_x = int(x - ox - ibox[0])
    local_y = int(y - oy - ibox[1])
    local_x = max(0, min(local_x, crop.size[0] - 1))
    local_y = max(0, min(local_y, crop.size[1] - 1))
    if Image is not None:
        from PIL import ImageDraw
        draw = ImageDraw.Draw(crop)
        s = 7
        draw.line([(local_x - s, local_y), (local_x + s, local_y)], fill=(255, 220, 0), width=2)
        draw.line([(local_x, local_y - s), (local_x, local_y + s)], fill=(255, 220, 0), width=2)
    path = _save_image(crop, dest, max_w=max(half * 2, 112))
    return path, (local_x, local_y)


def _cursor_target_witness(
    click_frame_path: str | None,
    origin,
    x: int,
    y: int,
    *,
    cursor_crop_path: str | None = None,
    step_description: str = "",
    sub_index: int = 0,
) -> dict:
    """Vision on cursor-centered crop from click-instant frame."""
    out = {
        "saw": False,
        "name": None,
        "control_type": None,
        "account": "cursor witness did not run.",
        "confidence": "low",
        "rect": [x - 20, y - 20, x + 20, y + 20],
        "cursor_point": [x, y],
    }
    if not click_frame_path or not os.path.isfile(click_frame_path) or Image is None:
        return out
    try:
        img = Image.open(click_frame_path)
        origin = tuple(origin) if origin else (0, 0)
        if cursor_crop_path:
            _crop_cursor_target(img, origin, x, y, cursor_crop_path)
            send_path = cursor_crop_path
        else:
            import tempfile
            send_path = os.path.join(tempfile.gettempdir(), f"mimic_cursor_{x}_{y}.png")
            _crop_cursor_target(img, origin, x, y, send_path)
        key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_key.txt")
        key = open(key_path, encoding="utf-8").read().strip() if os.path.isfile(key_path) else ""
        if not key:
            return out
        from vision_api import ask_vision_with_prompt

        hint = _click_confirm_label(step_description, sub_index).strip()
        prompt = (
            "The yellow crosshair marks the EXACT pixel the user clicked. "
            f"Expected target for this click: {hint!r}. "
            "What single UI element is under the crosshair (not nearby elements)? "
            'JSON only: {"name": "short label", "control_type": "Button|Icon|Text|MenuItem|...", '
            '"description": "one short sentence", "confidence": "high"|"medium"|"low"}'
        )
        with open(send_path, "rb") as f:
            raw = f.read()
        vis = ask_vision_with_prompt(raw, key, prompt)
        name = (vis.get("name") or "").strip() or None
        ctype = (vis.get("control_type") or "").strip() or None
        desc = (vis.get("description") or vis.get("what_you_see") or "").strip()
        conf = str(vis.get("confidence") or "low").lower()
        if conf not in ("high", "medium", "low"):
            conf = "low"
        if name or desc:
            out["saw"] = True
            out["name"] = name
            out["control_type"] = ctype
            out["account"] = _clip_account(desc or f"{ctype or 'element'} {name!r} under the cursor.")
            out["confidence"] = conf
            out["cursor_crop_path"] = send_path
    except Exception:
        pass
    return out


def _nothing_account() -> str:
    return "saw nothing"


def _structural_element_a11y(raw: dict) -> bool:
    if not raw:
        return False
    return bool(raw.get("name") or (raw.get("control_type") and raw.get("rect")))


def _structural_element_dom(raw: dict) -> bool:
    if not raw:
        return False
    return bool(
        raw.get("selector") or raw.get("text") or raw.get("role")
        or raw.get("name") or raw.get("parent_path")
    )


def _witness_pack(a11y_raw, dom_raw, vision_w, *, a11y_to=False, dom_to=False, vision_to=False, cursor_w=None):
    from app_ui_guard import is_own_ui_name

    if is_own_ui_name((a11y_raw or {}).get("name")):
        a11y_raw = {**(a11y_raw or {}), "name": None}
    a11y_saw = _structural_element_a11y(a11y_raw or {})
    a11y = {
        "saw": a11y_saw,
        "name": (a11y_raw or {}).get("name"),
        "control_type": (a11y_raw or {}).get("control_type"),
        "automation_id": (a11y_raw or {}).get("automation_id"),
        "rect": (a11y_raw or {}).get("rect"),
        "parent_path": (a11y_raw or {}).get("parent_path"),
        "confidence": _a11y_confidence(a11y_raw or {}) if a11y_saw else "low",
        "account": _a11y_account(a11y_raw or {}) if a11y_saw else _nothing_account(),
        "timed_out": a11y_to,
    }
    dom_raw = dom_raw or {}
    dom_saw = _structural_element_dom(dom_raw)
    dom = {
        "saw": dom_saw,
        "selector": dom_raw.get("selector") or dom_raw.get("css"),
        "text": dom_raw.get("text") or dom_raw.get("name"),
        "role": dom_raw.get("role") or dom_raw.get("role_name") or dom_raw.get("control_type"),
        "confidence": "medium" if dom_saw else "low",
        "account": _dom_account(dom_raw) if dom_saw else _nothing_account(),
        "rect": dom_raw.get("rect"),
        "timed_out": dom_to,
    }
    vision_w = dict(vision_w or {})
    if not vision_w.get("saw"):
        vision_w["account"] = vision_w.get("account") or _nothing_account()
    vision_w["timed_out"] = vision_to
    cursor_w = dict(cursor_w or {})
    if not cursor_w.get("saw"):
        cursor_w["account"] = cursor_w.get("account") or "cursor saw nothing here."
    return {"a11y": a11y, "dom": dom, "vision": vision_w, "cursor": cursor_w}


def _capture_screen_meta(ctx_path: str | None) -> dict:
    vx, vy, vw, vh = _virtual_screen()
    return {
        "origin_x": vx,
        "origin_y": vy,
        "width": vw,
        "height": vh,
        "full_image_path": ctx_path or "",
    }


def _vision_locate_tile(intent: str, ctx_path: str | None, x: int, y: int, crop_path: str | None) -> dict:
    """Tile scan + refine when structural pipelines are blind (Apollo path, unchanged)."""
    out = {"locate_path": "point_vision", "refined": False, "saw": False}
    if ctx_path and os.path.isfile(ctx_path):
        try:
            from email_workflow_automation.apollo import find_target_by_tile_scan, split_into_tiles

            tile_dir = os.path.join(os.path.dirname(ctx_path), "_tiles")
            os.makedirs(tile_dir, exist_ok=True)
            tiles = split_into_tiles(ctx_path, save_dir=tile_dir)
            meta = _capture_screen_meta(ctx_path)
            scan = find_target_by_tile_scan(
                intent or "the UI element the user clicked",
                tiles,
                full_image_path=ctx_path,
                meta=meta,
            )
            if scan.get("found") and scan.get("screen_x") is not None:
                sx, sy = int(scan["screen_x"]), int(scan["screen_y"])
                verify = scan.get("verify") or {}
                refined = verify.get("refined_screen")
                out = {
                    "locate_path": "tile_scan",
                    "refined": bool(refined),
                    "saw": True,
                    "screen_x": sx,
                    "screen_y": sy,
                    "tile_index": scan.get("tile_index"),
                    "rect": [sx - 20, sy - 12, sx + 20, sy + 12],
                    "account": _clip_account(
                        str(verify.get("what_you_see") or scan.get("why") or "located by tile scan")
                    ),
                    "confidence": scan.get("confidence") or "medium",
                }
                return out
        except Exception:
            pass
    vw = _vision_witness(x, y, crop_path)
    out.update({
        "saw": bool(vw.get("saw")),
        "rect": vw.get("rect"),
        "account": vw.get("account") or _nothing_account(),
        "confidence": vw.get("confidence") or "low",
    })
    return out


def _log_vision_confirm(diag: dict) -> None:
    try:
        import json

        print("[vision_confirm] " + json.dumps(diag, default=str), flush=True)
    except Exception:
        pass


def _save_click_frame(img, dest: str) -> str:
    """Save full-resolution click instant frame (no downscale)."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    img.save(dest)
    return dest


def _crop_confirm_region(
    rect,
    dest: str,
    width: int = _CONFIRM_CROP_W,
    height: int = _CONFIRM_CROP_H,
    *,
    point: tuple[int, int] | list | None = None,
    monitor: int | None = None,
    click_frame_path: str | None = None,
    frame_origin: tuple[int, int] | list | None = None,
    click_grab_offset_ms: int | None = None,
) -> tuple[str, dict]:
    """Crop a confirm patch from the click-instant frame when available."""
    t0 = time.perf_counter()
    grab_timing = "at_click_time"
    if click_frame_path and os.path.isfile(click_frame_path):
        if Image is None:
            raise RuntimeError("Pillow unavailable")
        img = Image.open(click_frame_path)
        origin = tuple(frame_origin) if frame_origin else (0, 0)
    else:
        img, origin = _grab_screen()
        grab_timing = "at_confirm_time"
    vx, vy, vw, vh = _virtual_screen()
    if point and len(point) >= 2:
        cx, cy = int(point[0]), int(point[1])
        center_source = "click_point"
    else:
        center = _rect_center(rect)
        cx, cy = (int(center[0]), int(center[1])) if center else (0, 0)
        center_source = "rect_center" if center else "fallback_0_0"
    half_w, half_h = width // 2, height // 2
    box = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
    ibox = _to_image_box(box, origin, img.size)
    crop = img.crop(ibox)
    path = _save_image(crop, dest, max_w=width)
    mon_idx = monitor
    if mon_idx is None and point and len(point) >= 2:
        mon_idx, _ = _monitor_index(int(point[0]), int(point[1]))
    diag = {
        "rect_input": rect,
        "point": list(point) if point and len(point) >= 2 else None,
        "center_source": center_source,
        "crop_center_screen": [cx, cy],
        "crop_box_screen": [int(v) for v in box],
        "crop_box_image": [int(v) for v in ibox],
        "image_size": [int(img.size[0]), int(img.size[1])],
        "screen_origin": [int(origin[0]), int(origin[1])],
        "virtual_screen": [vx, vy, vw, vh],
        "monitor_index": mon_idx,
        "crop_dimensions": [int(crop.size[0]), int(crop.size[1])],
        "crop_path": path,
        "crop_bytes": os.path.getsize(path) if os.path.isfile(path) else 0,
        "grab_elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "grab_timing": grab_timing,
        "click_frame_path": click_frame_path,
        "click_grab_offset_ms": click_grab_offset_ms,
    }
    return path, diag


def _ask_confirm_vision(crop_path: str, target_label: str) -> dict:
    out = {"shows_target": None, "what_you_see": "", "confidence": "low"}
    if not crop_path or not os.path.isfile(crop_path):
        return out
    try:
        key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_key.txt")
        key = open(key_path, encoding="utf-8").read().strip() if os.path.isfile(key_path) else ""
        if not key:
            return out
        from vision_api import ask_vision_with_prompt

        label = (target_label or "the click target").strip()
        prompt = (
            f'Does this crop show {label!r}? Answer JSON only: '
            '{"shows_target": true|false, "what_you_see": "...", "confidence": "high"|"medium"|"low"}'
        )
        with open(crop_path, "rb") as f:
            raw = f.read()
        vis = ask_vision_with_prompt(raw, key, prompt)
        shows = vis.get("shows_target")
        if shows is None and vis.get("found") is not None:
            shows = bool(vis.get("found"))
        out["shows_target"] = bool(shows) if shows is not None else None
        out["what_you_see"] = (vis.get("what_you_see") or vis.get("account") or "").strip()
        out["confidence"] = vis.get("confidence") or "low"
    except Exception:
        pass
    return out


def confirm_target_with_vision(
    resolution: dict,
    step_description: str,
    *,
    wf_name: str | None = None,
    stem: str = "confirm",
) -> dict:
    """Vision confirms appearance after structural or tile resolution."""
    import shutil

    rect = resolution.get("rect")
    point = resolution.get("point")
    monitor = resolution.get("monitor")
    source = resolution.get("source")
    primary = resolution.get("primary") or {}
    sub_index = int(resolution.get("sub_index") or 0)
    witnesses = resolution.get("witnesses") or {}
    cursor_w = witnesses.get("cursor") or {}
    click_frame_path = resolution.get("click_frame_abs") or resolution.get("click_frame_path")
    if click_frame_path and not os.path.isabs(str(click_frame_path)) and wf_name:
        click_frame_path = os.path.join(workflow_dir(wf_name), click_frame_path)
    frame_origin = resolution.get("click_frame_origin")
    click_grab_offset_ms = resolution.get("click_grab_offset_ms")
    cursor_crop_rel = resolution.get("cursor_crop_path")
    cursor_crop_abs = None
    if cursor_crop_rel and wf_name:
        cursor_crop_abs = os.path.join(workflow_dir(wf_name), cursor_crop_rel)
    elif wf_name and stem:
        guess = os.path.join(workflow_dir(wf_name), "anchors", f"{stem}_cursor.png")
        if os.path.isfile(guess):
            cursor_crop_abs = guess

    target_label = _click_confirm_label(step_description, sub_index, primary)

    if (
        source == "cursor"
        and cursor_w.get("saw")
        and cursor_w.get("confidence") in ("high", "medium")
        and cursor_crop_abs
        and os.path.isfile(cursor_crop_abs)
    ):
        return {
            "confirmed_by_vision": True,
            "confirmed_by_cursor": True,
            "what_you_see": cursor_w.get("account"),
            "confidence": cursor_w.get("confidence"),
            "confirm_crop": cursor_crop_abs,
            "diagnostics": {
                "grab_timing": "cursor_crop",
                "confirm_source": "cursor_witness",
                "cursor_crop_path": cursor_crop_abs,
                "target_label": target_label,
            },
        }

    if (not rect or len(rect) < 4) and not (point and len(point) >= 2):
        return {"unconfirmed": True, "confirmed_by_vision": False}
    timeout = _WITNESS_TIMEOUT
    if wf_name:
        rel = os.path.join("anchors", f"{stem}_confirm.png")
        crop_path = os.path.join(workflow_dir(wf_name), rel)
    else:
        import tempfile
        crop_path = os.path.join(tempfile.gettempdir(), f"mimic_{stem}_confirm.png")

    if cursor_crop_abs and os.path.isfile(cursor_crop_abs):
        os.makedirs(os.path.dirname(crop_path), exist_ok=True)
        shutil.copy2(cursor_crop_abs, crop_path)
        crop_diag = {
            "grab_timing": "cursor_crop",
            "confirm_source": "cursor_crop_copy",
            "cursor_crop_path": cursor_crop_abs,
            "crop_path": crop_path,
            "target_label": target_label,
        }
    else:
        crop_kw = dict(
            point=point,
            monitor=monitor,
            click_frame_path=click_frame_path,
            frame_origin=frame_origin,
            click_grab_offset_ms=click_grab_offset_ms,
        )
        crop_path, crop_diag = _crop_confirm_region(rect, crop_path, **crop_kw)
        crop_diag["target_label"] = target_label
    t0 = time.perf_counter()
    try:
        vis, timed_out = _run_timed(_ask_confirm_vision, crop_path, target_label, timeout=timeout)
    except Exception:
        vis, timed_out = None, True
    vision_elapsed_ms = int((time.perf_counter() - t0) * 1000)
    diagnostics = {
        **crop_diag,
        "vision_elapsed_ms": vision_elapsed_ms,
        "vision_timeout_ms": int(timeout * 1000),
        "vision_timed_out": timed_out,
        "vision_response": vis,
        "confirm_stem": stem,
    }
    _log_vision_confirm(diagnostics)
    if timed_out or vis is None:
        if source == "cursor" and cursor_w.get("saw"):
            return {
                "confirmed_by_vision": True,
                "confirmed_by_cursor": True,
                "what_you_see": cursor_w.get("account"),
                "confidence": cursor_w.get("confidence") or "medium",
                "confirm_crop": crop_path,
                "diagnostics": diagnostics,
            }
        return {
            "unconfirmed": True,
            "confirmed_by_vision": False,
            "confirm_crop": crop_path,
            "diagnostics": diagnostics,
        }
    if vis.get("shows_target") is True:
        return {
            "confirmed_by_vision": True,
            "what_you_see": vis.get("what_you_see"),
            "confidence": vis.get("confidence"),
            "confirm_crop": crop_path,
            "diagnostics": diagnostics,
        }
    if vis.get("shows_target") is False:
        if source == "cursor" and cursor_w.get("saw") and cursor_w.get("confidence") in ("high", "medium"):
            return {
                "confirmed_by_vision": True,
                "confirmed_by_cursor": True,
                "what_you_see": cursor_w.get("account"),
                "confidence": cursor_w.get("confidence"),
                "confirm_crop": crop_path,
                "diagnostics": diagnostics,
            }
        name = primary.get("name") or "unnamed"
        ctype = primary.get("control_type") or "element"
        saw = vis.get("what_you_see") or "something else"
        src_word = "cursor" if source == "cursor" else "tree"
        question = (
            f"The {src_word} pointed at a {ctype} named {name!r}, but the picture there shows {saw}. "
            "Should I use this, or would you like to show me again?"
        )
        return {
            "vision_mismatch": True,
            "question": question,
            "what_you_see": saw,
            "confirm_crop": crop_path,
            "diagnostics": diagnostics,
        }
    return {
        "unconfirmed": True,
        "confirmed_by_vision": False,
        "confirm_crop": crop_path,
        "diagnostics": diagnostics,
    }


def _element_ancestors(x: int, y: int, max_levels: int = 3) -> list[dict]:
    chain: list[dict] = []
    try:
        from pywinauto import Desktop

        el = Desktop(backend="uia").from_point(int(x), int(y))
        cur = el
        for _ in range(max_levels):
            ei = cur.element_info
            chain.append({
                "name": (ei.name or "").strip() or None,
                "control_type": ei.control_type,
            })
            try:
                cur = cur.parent()
            except Exception:
                break
    except Exception:
        pass
    return chain


def check_parent_target(x: int, y: int, primary: dict) -> dict | None:
    """Text/Label clicked but interactive ancestor exists within 2 levels."""
    ctype = (primary or {}).get("control_type") or ""
    if not _is_non_interactive(ctype):
        return None
    chain = _element_ancestors(x, y, max_levels=4)
    if not chain:
        return None
    clicked_name = primary.get("name") or chain[0].get("name") or "unnamed"
    clicked_type = _normalize_ctype(ctype) or _normalize_ctype(chain[0].get("control_type")) or "Text"
    for anc in chain[1:3]:
        act_raw = anc.get("control_type") or ""
        if _is_interactive(act_raw):
            act = _normalize_ctype(act_raw) or act_raw
            return {
                "clicked_name": clicked_name,
                "clicked_type": clicked_type,
                "ancestor_type": act,
                "ancestor_name": anc.get("name"),
                "question": (
                    f"You clicked the text {clicked_name!r}, which sits inside a {act}. "
                    f"Should I click the {act}?"
                ),
            }
    return None


def _resolution_line(source: str, confirmation: dict) -> str:
    src = source or "unknown"
    if confirmation.get("confirmed_by_cursor"):
        return f"resolved by {src} · confirmed by cursor crop"
    if confirmation.get("vision_mismatch"):
        return f"resolved by {src} · vision disagrees (needs you)"
    if confirmation.get("confirmed_by_vision"):
        return f"resolved by {src} · confirmed by vision"
    if confirmation.get("unconfirmed"):
        return f"resolved by {src} · unconfirmed (vision timed out)"
    return f"resolved by {src}"


def resolve_target(
    point,
    monitor=None,
    *,
    step_description: str = "",
    ctx_path: str | None = None,
    crop_path: str | None = None,
    wf_name: str | None = None,
    confirm_stem: str | None = None,
    click_frame_path: str | None = None,
    click_frame_abs: str | None = None,
    click_frame_origin: tuple[int, int] | list | None = None,
    click_grab_offset_ms: int | None = None,
    click_a11y_raw: dict | None = None,
    click_dom_raw: dict | None = None,
    sub_index: int = 0,
) -> dict:
    """Role-based cascade: structural authority, vision locates when blind, vision confirms."""
    from app_ui_guard import (
        is_own_ui_name,
        is_own_window,
        vision_is_own_ui,
        window_title_at_point,
        _log_skip,
    )

    x, y = int(point[0]), int(point[1])
    win_title = window_title_at_point(x, y)
    if is_own_window(win_title):
        _log_skip(f"skipped own UI capture at ({x},{y}) window={win_title!r}")
        empty = {"saw": False, "account": _nothing_account(), "confidence": "low"}
        return {
            "point": [x, y],
            "witnesses": {"a11y": dict(empty), "dom": dict(empty), "vision": dict(empty)},
            "skipped_own_ui": True,
            "source": None,
            "reason": "own UI skipped",
            "resolution_line": "own UI skipped",
        }

    if monitor is None:
        idx, monitor = _monitor_index(x, y)
    elif isinstance(monitor, int):
        mons = _list_monitors()
        idx = monitor
        monitor = mons[idx] if 0 <= idx < len(mons) else _monitor_at(x, y)
    else:
        idx, _mon = _monitor_index(x, y)
        monitor = tuple(monitor)

    a11y_raw, a11y_to = ({}, False)
    dom_raw, dom_to = ({}, False)
    if click_a11y_raw is not None:
        a11y_raw = dict(click_a11y_raw)
    else:
        a11y_raw, a11y_to = _run_timed(_element_at, x, y)
    if click_dom_raw is not None:
        dom_raw = dict(click_dom_raw)
    else:
        dom_raw, dom_to = _run_timed(_browser_at, x, y)
    if a11y_to:
        a11y_raw = {}
    if dom_to:
        dom_raw = {}

    vision_observe, v_to = _run_timed(
        _combined_vlm_witness, ctx_path or crop_path, x, y, a11y_raw or {}, ""
    )
    if v_to or vision_observe is None:
        vision_observe = _vision_witness(x, y, crop_path)
        vision_observe["timed_out"] = v_to
    if vision_is_own_ui(vision_observe.get("account") or vision_observe.get("described")):
        _log_skip("skipped own UI vision witness")
        vision_observe = {**vision_observe, "saw": False, "account": _nothing_account()}

    frame_abs = click_frame_abs or click_frame_path
    if frame_abs and not os.path.isabs(str(frame_abs)) and wf_name:
        frame_abs = os.path.join(workflow_dir(wf_name), frame_abs)
    cursor_crop_abs = None
    if wf_name and confirm_stem:
        cursor_crop_abs = os.path.join(
            workflow_dir(wf_name), "anchors", f"{confirm_stem}_cursor.png",
        )
    cursor_w = None
    cursor_to = False
    try:
        cursor_w = _cursor_target_witness(
            frame_abs if frame_abs and os.path.isfile(str(frame_abs)) else None,
            click_frame_origin or (0, 0),
            x,
            y,
            cursor_crop_path=cursor_crop_abs,
            step_description=step_description,
            sub_index=sub_index,
        )
    except Exception:
        cursor_w = {"saw": False, "account": "cursor witness error."}
    if not cursor_w:
        cursor_w = {"saw": False, "account": "cursor witness did not run."}

    witnesses = _witness_pack(
        a11y_raw, dom_raw, vision_observe,
        a11y_to=a11y_to, dom_to=dom_to, vision_to=v_to, cursor_w=cursor_w,
    )

    source = None
    reason = None
    primary: dict = {}
    rect = None

    hints = _click_hints(step_description, sub_index)
    a11y_name = (a11y_raw or {}).get("name")
    cursor_name = (cursor_w or {}).get("name")
    a11y_noise = _a11y_is_overlay_noise(a11y_name, step_description, sub_index)
    a11y_hint = _hint_score(a11y_name, hints)
    cursor_hint = _hint_score(cursor_name, hints) + _hint_score((cursor_w or {}).get("account"), hints)

    a11y_at_point = (
        _structural_element_a11y(a11y_raw or {})
        and _point_in_rect(x, y, (a11y_raw or {}).get("rect"))
        and not a11y_noise
    )
    cursor_reliable = bool(
        (cursor_w or {}).get("saw")
        and (cursor_w or {}).get("confidence") in ("high", "medium")
        and (cursor_w or {}).get("name")
    )
    cursor_disagrees = cursor_reliable and (
        not a11y_at_point
        or not _labels_overlap(a11y_name, cursor_name)
        or cursor_hint > a11y_hint
    )
    cursor_wrong_sub = (
        sub_index <= 0
        and cursor_name
        and "apollo" in (cursor_name or "").lower()
        and "extensions" not in (cursor_name or "").lower()
        and a11y_hint > 0
    ) or (
        sub_index > 0
        and cursor_name
        and "extensions" in (cursor_name or "").lower()
        and "apollo" not in (cursor_name or "").lower()
        and a11y_hint > 0
    )
    cursor_beats_a11y = cursor_reliable and not cursor_wrong_sub and (
        cursor_disagrees or a11y_noise or cursor_hint > a11y_hint
    )

    if cursor_beats_a11y:
        source = "cursor"
        reason = (
            "cursor-centered vision matches the step better than accessibility tree"
            if cursor_hint > a11y_hint or a11y_noise
            else "cursor-centered vision at the click point"
            if not a11y_at_point
            else "cursor-centered vision disagrees with accessibility tree at click point"
        )
        primary = {
            "name": cursor_w.get("name"),
            "control_type": cursor_w.get("control_type") or "element",
            "pipeline": "cursor",
            "kind": "cursor",
        }
        rect = [x - 24, y - 24, x + 24, y + 24]
    elif a11y_at_point:
        source = "a11y"
        reason = "structural pipeline saw the element at the click point"
        primary = {
            "name": a11y_raw.get("name"),
            "control_type": a11y_raw.get("control_type"),
            "automation_id": a11y_raw.get("automation_id"),
            "pipeline": "a11y",
            "kind": "a11y",
        }
        rect = a11y_raw.get("rect")
    elif cursor_reliable:
        source = "cursor"
        reason = "cursor-centered vision at the click point"
        primary = {
            "name": cursor_w.get("name"),
            "control_type": cursor_w.get("control_type") or "element",
            "pipeline": "cursor",
            "kind": "cursor",
        }
        rect = [x - 24, y - 24, x + 24, y + 24]
    elif _structural_element_dom(dom_raw or {}):
        source = "dom"
        reason = "structural pipeline saw the element"
        primary = {
            "name": dom_raw.get("name") or dom_raw.get("text"),
            "control_type": dom_raw.get("role") or dom_raw.get("control_type"),
            "pipeline": "dom",
            "kind": "dom",
        }
        rect = dom_raw.get("rect") or [x - 16, y - 16, x + 16, y + 16]
    else:
        locate = _vision_locate_tile(step_description, ctx_path, x, y, crop_path)
        source = "vision"
        reason = "no structural pipeline could see this element"
        primary = {
            "name": None,
            "control_type": "vision",
            "pipeline": "vision",
            "kind": "vision",
            "locate_path": locate.get("locate_path"),
            "refined": locate.get("refined"),
        }
        rect = locate.get("rect") or [x - 32, y - 32, x + 32, y + 32]
        if locate.get("saw"):
            witnesses["vision"]["saw"] = True
            witnesses["vision"]["account"] = locate.get("account") or witnesses["vision"]["account"]
            witnesses["vision"]["locate_path"] = locate.get("locate_path")
            witnesses["vision"]["tile_index"] = locate.get("tile_index")

    resolution = {
        "point": [x, y],
        "monitor": idx,
        "witnesses": witnesses,
        "source": source,
        "reason": reason,
        "primary": primary,
        "rect": rect,
        "click_frame_path": click_frame_path,
        "click_frame_abs": click_frame_abs,
        "click_frame_origin": list(click_frame_origin) if click_frame_origin else None,
        "click_grab_offset_ms": click_grab_offset_ms,
        "sub_index": sub_index,
        "cursor_crop_path": (
            os.path.join("anchors", f"{confirm_stem}_cursor.png")
            if wf_name and confirm_stem and cursor_crop_abs and os.path.isfile(cursor_crop_abs)
            else None
        ),
    }

    stem = confirm_stem or (wf_name or "step").replace(os.sep, "_")[:20]
    confirmation = confirm_target_with_vision(
        resolution, step_description, wf_name=wf_name, stem=stem,
    )
    resolution["confirmation"] = confirmation
    resolution["resolution_line"] = _resolution_line(source, confirmation)

    if source in ("a11y", "dom", "cursor"):
        parent = check_parent_target(x, y, primary)
        if parent:
            resolution["parent_target"] = parent

    return resolution


def score_agreement(witnesses: dict) -> tuple[str, str]:
    """Geometric agreement. Does not pick a winner."""
    seeing = []
    for key in ("a11y", "dom", "vision"):
        w = (witnesses or {}).get(key) or {}
        if w.get("saw"):
            seeing.append((key, w))
    if len(seeing) <= 1:
        return "single", ""
    with_rect = [(k, w) for k, w in seeing if w.get("rect") and len(w.get("rect") or []) >= 4]
    if len(with_rect) < 2:
        return "partial", "more than one witness saw something, but not enough rects to compare."
    pairs_agree = []
    notes = []
    for i, (ka, wa) in enumerate(with_rect):
        for kb, wb in with_rect[i + 1 :]:
            ok = _rects_agree(wa["rect"], wb.get("rect"))
            pairs_agree.append(ok)
            if not ok:
                ca, cb = _rect_center(wa["rect"]), _rect_center(wb["rect"])
                dist = 0
                if ca and cb:
                    dist = int(((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5)
                notes.append(f"{ka} points at rect {wa['rect']}; {kb} points at rect {wb['rect']}, {dist}px apart")
    if all(pairs_agree):
        if len(with_rect) < len(seeing):
            return "partial", "rects that exist agree, but not every witness has a rect."
        return "agree", ""
    if any(pairs_agree) and not all(pairs_agree):
        return "partial", "; ".join(notes)
    return "conflict", "; ".join(notes) or "witness rects do not overlap."


def _monitor_index(x: int, y: int) -> tuple[int, tuple[int, int, int, int]]:
    mons = _list_monitors()
    for i, m in enumerate(mons):
        if m[0] <= x < m[2] and m[1] <= y < m[3]:
            return i, m
    return 0, _monitor_at(x, y)


def _vision_witness(x: int, y: int, crop_path: str | None) -> dict:
    rect = [x - 32, y - 32, x + 32, y + 32]
    out = {
        "saw": False,
        "described": None,
        "rect": rect,
        "crop_path": crop_path,
        "confidence": "low",
        "account": "vision did not run.",
    }
    if crop_path and os.path.isfile(crop_path):
        try:
            key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_key.txt")
            key = open(key_path, encoding="utf-8").read().strip() if os.path.isfile(key_path) else ""
            if key:
                from vision_api import ask_vision_api

                with open(crop_path, "rb") as f:
                    raw = f.read()
                vis = ask_vision_api(raw, key)
                desc = (vis.get("what_you_see") or vis.get("described") or "").strip()
                if vis.get("found") or desc:
                    out["saw"] = True
                    out["described"] = desc or None
                    out["confidence"] = vis.get("confidence") if vis.get("confidence") in ("high", "medium", "low") else "medium"
                    out["account"] = _vision_account(out)
                    return out
            out["account"] = "vision saw nothing usable here."
        except Exception:
            out["account"] = "vision saw nothing usable here."
    return out


def multi_witness_capture(point, monitor=None, crop_path: str | None = None,
                          ctx_path: str | None = None, step_description: str = "",
                          wf_name: str | None = None, confirm_stem: str | None = None,
                          click_frame_path: str | None = None,
                          click_frame_abs: str | None = None,
                          click_frame_origin: tuple[int, int] | list | None = None,
                          click_grab_offset_ms: int | None = None,
                          click_a11y_raw: dict | None = None,
                          click_dom_raw: dict | None = None,
                          sub_index: int = 0) -> dict:
    """Gather witnesses and resolve target via role-based cascade."""
    res = resolve_target(
        point, monitor,
        step_description=step_description,
        ctx_path=ctx_path,
        crop_path=crop_path,
        wf_name=wf_name,
        confirm_stem=confirm_stem,
        click_frame_path=click_frame_path,
        click_frame_abs=click_frame_abs,
        click_frame_origin=click_frame_origin,
        click_grab_offset_ms=click_grab_offset_ms,
        click_a11y_raw=click_a11y_raw,
        click_dom_raw=click_dom_raw,
        sub_index=sub_index,
    )
    return {
        "point": res.get("point"),
        "monitor": res.get("monitor"),
        "witnesses": res.get("witnesses"),
        "resolution": res,
        "source": res.get("source"),
        "reason": res.get("reason"),
        "resolution_line": res.get("resolution_line"),
        "confirmation": res.get("confirmation"),
        "skipped_own_ui": res.get("skipped_own_ui"),
    }


def _save_image(img, dest: str, max_w: int = 1400) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    w, h = img.size
    if w > max_w:
        img = img.resize((max_w, max(1, int(h * max_w / w))))
    img.save(dest)
    return dest


def _crop_64(x: int, y: int, dest: str, img=None, origin=None) -> str:
    if img is None or origin is None:
        img, origin = _grab_screen()
    half = 32
    box = _to_image_box((x - half, y - half, x + half, y + half), origin, img.size)
    crop = img.crop(box).resize((64, 64))
    return _save_image(crop, dest, max_w=64)


def _save_context(x: int, y: int, dest: str, element_rect=None, focus: bool = False,
                  img=None, origin=None) -> str:
    """Crop the focused monitor (or window). Tight element crop only when focus=True."""
    if img is None or origin is None:
        img, origin = _grab_screen()
    if focus and element_rect and len(element_rect) == 4:
        l, t, r, b = [int(v) for v in element_rect]
        pad = 24
        box = (l - pad, t - pad, r + pad, b + pad)
    else:
        win = _window_rect_at(x, y)
        mon = _monitor_at(x, y)
        if win is not None:
            # Keep the window, but never wider than the monitor the cursor is on.
            wl, wt, wr, wb = win
            ml, mt, mr, mb = mon
            box = (max(wl, ml), max(wt, mt), min(wr, mr), min(wb, mb))
            if box[2] <= box[0] or box[3] <= box[1]:
                box = mon
        else:
            box = mon
    crop = img.crop(_to_image_box(box, origin, img.size))
    return _save_image(crop, dest)


def _capture_at(
    x: int,
    y: int,
    dest_stem: str,
    wf_name: str,
    focus: bool = False,
    *,
    pre_frame_img=None,
    pre_frame_origin=None,
    pre_click_grab_offset_ms: int | None = None,
    pre_a11y_raw: dict | None = None,
    pre_dom_raw: dict | None = None,
    pre_structural_state: dict | None = None,
) -> dict:
    click_rel = os.path.join("anchors", f"{dest_stem}_click_frame.png")
    ctx_rel = os.path.join("anchors", f"{dest_stem}_window.png")
    crop_rel = os.path.join("anchors", f"{dest_stem}.png")
    cursor_rel = os.path.join("anchors", f"{dest_stem}_cursor.png")
    click_abs = os.path.join(workflow_dir(wf_name), click_rel)
    ctx_abs = os.path.join(workflow_dir(wf_name), ctx_rel)
    crop_abs = os.path.join(workflow_dir(wf_name), crop_rel)
    cursor_abs = os.path.join(workflow_dir(wf_name), cursor_rel)

    if pre_frame_img is not None and pre_frame_origin is not None:
        img = pre_frame_img
        origin = pre_frame_origin
        click_grab_offset_ms = int(pre_click_grab_offset_ms or 0)
    else:
        t_click = time.perf_counter()
        img, origin = _grab_screen()
        click_grab_offset_ms = int((time.perf_counter() - t_click) * 1000)
    _save_click_frame(img, click_abs)

    _crop_64(x, y, crop_abs, img=img, origin=origin)
    _crop_cursor_target(img, origin, x, y, cursor_abs)

    a11y = dict(pre_a11y_raw) if pre_a11y_raw is not None else _element_at(x, y)
    dom = dict(pre_dom_raw) if pre_dom_raw is not None else _browser_at(x, y)
    _save_context(x, y, ctx_abs, element_rect=a11y.get("rect"), focus=bool(focus), img=img, origin=origin)
    primary = {}
    if a11y.get("name") or a11y.get("control_type"):
        primary = {
            "name": a11y.get("name"),
            "control_type": a11y.get("control_type"),
            "automation_id": a11y.get("automation_id"),
            "kind": "a11y",
        }
    elif dom:
        primary = {"kind": "dom", **{k: dom[k] for k in list(dom)[:6]}}
    from success_signals import snapshot_structural_state

    structural_state = pre_structural_state
    if structural_state is None:
        structural_state = snapshot_structural_state(x, y, a11y, dom)
    return {
        "primary": primary,
        "parent_path": a11y.get("parent_path"),
        "crop_path": crop_rel,
        "context_path": ctx_rel,
        "click_frame_path": click_rel,
        "cursor_crop_path": cursor_rel,
        "preview_path": ctx_rel if not focus else crop_rel,
        "focused": bool(focus),
        "screen_rect": a11y.get("rect") or [x - 32, y - 32, x + 32, y + 32],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "crop_abs": crop_abs,
        "click_frame_abs": click_abs,
        "click_frame_origin": [int(origin[0]), int(origin[1])],
        "click_grab_offset_ms": click_grab_offset_ms,
        "click_a11y_raw": a11y,
        "click_dom_raw": dom,
        "structural_state": structural_state,
    }


def capture_start(
    wf: TaughtWorkflow,
    point: tuple[int, int] | None = None,
    countdown: float = 0,
    focus: bool = False,
) -> dict:
    """Snapshot of the screen every run begins on. Not a step."""
    if countdown:
        time.sleep(float(countdown))
    if point is None:
        point = cursor_point()
    x, y = int(point[0]), int(point[1])
    anchor = _capture_at(x, y, "start", wf.name, focus=bool(focus))
    crop_abs = anchor.pop("crop_abs")
    name = (anchor.get("primary") or {}).get("name") or "unnamed"
    ctype = (anchor.get("primary") or {}).get("control_type") or "screen"
    summary = f"Every run starts here — a {ctype} named {name!r}."
    start = dict(wf.start_screen or {})
    start.update({"anchor": anchor, "shown": True, "capture_summary": summary})
    wf.start_screen = start
    save_taught(wf)
    return {"ok": True, "summary": summary, "anchor": anchor, "start_screen": start, "crop_abs": crop_abs}


def capture_show(
    wf: TaughtWorkflow,
    step_id: str,
    point: tuple[int, int] | None = None,
    countdown: float = 0,
    focus: bool = False,
    sub_index: int = 0,
    *,
    pre_frame_img=None,
    pre_frame_origin=None,
    pre_click_grab_offset_ms: int | None = None,
    pre_a11y_raw: dict | None = None,
    pre_dom_raw: dict | None = None,
    pre_structural_state: dict | None = None,
) -> dict:
    """Capture at `point` or the focused element's centre. Tests pass countdown=0.

    Default picture is the whole window. Pass focus=True to crop onto the
    element — only when the user asked to zoom in.
    sub_index: which sub-click anchor (0 or 1) for 2-click chains.
    """
    step = get_step(wf, step_id)
    if countdown:
        time.sleep(float(countdown))
    if point is None:
        point = _fallback_point()
    x, y = int(point[0]), int(point[1])
    stem = step.id if sub_index <= 0 else f"{step.id}_c{sub_index + 1}"
    packed = _capture_at(
        x, y, stem, wf.name, focus=bool(focus),
        pre_frame_img=pre_frame_img,
        pre_frame_origin=pre_frame_origin,
        pre_click_grab_offset_ms=pre_click_grab_offset_ms,
        pre_a11y_raw=pre_a11y_raw,
        pre_dom_raw=pre_dom_raw,
        pre_structural_state=pre_structural_state,
    )
    crop_abs = packed.pop("crop_abs")
    click_frame_abs = packed.get("click_frame_abs")
    click_grab_offset_ms = packed.get("click_grab_offset_ms")
    click_frame_origin = packed.get("click_frame_origin")
    click_a11y_raw = packed.get("click_a11y_raw")
    click_dom_raw = packed.get("click_dom_raw")
    ctx_abs = os.path.join(workflow_dir(wf.name), packed.get("context_path") or "")
    anchor = packed
    idx, mon = _monitor_index(x, y)
    mw = multi_witness_capture(
        (x, y), monitor=mon, crop_path=crop_abs, ctx_path=ctx_abs,
        step_description=step.user_description or "",
        wf_name=wf.name,
        confirm_stem=stem,
        click_frame_path=packed.get("click_frame_path"),
        click_frame_abs=click_frame_abs,
        click_frame_origin=click_frame_origin,
        click_grab_offset_ms=click_grab_offset_ms,
        click_a11y_raw=click_a11y_raw,
        click_dom_raw=click_dom_raw,
        sub_index=sub_index,
    )
    resolution = mw.get("resolution") or {}
    mw["monitor"] = idx
    anchor["witnesses"] = mw["witnesses"]
    anchor["resolution"] = resolution
    anchor["resolution_source"] = resolution.get("source")
    anchor["resolution_reason"] = resolution.get("reason")
    anchor["resolution_line"] = resolution.get("resolution_line")
    conf = resolution.get("confirmation") or {}
    anchor["confirmed_by_vision"] = conf.get("confirmed_by_vision")
    anchor["vision_unconfirmed"] = conf.get("unconfirmed")
    anchor["vision_mismatch_pending"] = conf.get("vision_mismatch")
    if conf.get("diagnostics"):
        anchor["vision_confirm_diag"] = conf.get("diagnostics")
    if resolution.get("primary"):
        anchor["primary"] = resolution["primary"]
    if resolution.get("rect"):
        anchor["screen_rect"] = resolution["rect"]
    anchor["point"] = [x, y]
    anchor["monitor"] = idx
    anchor["sub_index"] = sub_index
    if packed.get("structural_state"):
        anchor["structural_state"] = packed["structural_state"]
    from teaching import sync_step_anchors

    sync_step_anchors(step)
    while len(step.anchors) <= sub_index:
        step.anchors.append(None)
    step.anchors[sub_index] = anchor
    sync_step_anchors(step)
    save_taught(wf)
    primary = anchor.get("primary") or {}
    name = primary.get("name") or "unnamed"
    ctype = primary.get("control_type") or "element"
    summary = f"I saw a {ctype} named {name!r} — is that the one?"
    save_taught(wf)
    return {
        "ok": True,
        "summary": summary,
        "anchor": anchor,
        "sub_index": sub_index,
        "crop_abs": crop_abs,
        "witnesses": mw,
        "resolution": resolution,
        "confirm_question": (conf.get("question") if conf.get("vision_mismatch") else summary),
    }


def _fallback_point() -> tuple[int, int]:
    try:
        from ui_runner import find_window, resolve_element

        win, _ = find_window("Notepad")
        if win is not None:
            el = resolve_element(win, "Text editor", "Document")
            if el is not None:
                r = el.rectangle()
                return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    except Exception:
        pass
    return (400, 400)


def _left_button_down() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
    except Exception:
        return False


def _freeze_click_at(x: int, y: int) -> dict:
    """Grab frame + structural snapshot at click instant (may run in a worker thread)."""
    t0 = time.perf_counter()
    img, origin = _grab_screen()
    offset_ms = int((time.perf_counter() - t0) * 1000)
    a11y_raw, dom_raw = _snapshot_structural_at_click(x, y)
    from success_signals import snapshot_click_moment

    return {
        "point": [x, y],
        "frame_img": img,
        "frame_origin": origin,
        "click_grab_offset_ms": offset_ms,
        "a11y_raw": a11y_raw,
        "dom_raw": dom_raw,
        "structural_state": snapshot_click_moment(x, y, a11y_raw, dom_raw),
    }


def listen_clicks(
    count: int,
    timeout: float = 25.0,
    min_gap: float = 0.3,
    *,
    with_frames: bool = False,
    on_click=None,
    async_freeze: bool = False,
) -> list:
    """Wait for `count` distinct left-clicks.

    When with_frames=True, on each mousedown: grab screen + snapshot a11y/DOM
    at that instant. async_freeze=True runs freeze work in threads so click 2
    is not missed while click 1 is still being captured.
    """
    events: list = []
    pressed = False
    deadline = time.time() + max(1.0, float(timeout))
    last_click = 0.0
    target = count if on_click is None else count
    poll_s = 0.01

    def _emit(ev: dict) -> None:
        if on_click is not None:
            on_click(ev)
        events.append(ev)

    if with_frames and async_freeze:
        inflight: list = []
        with futures.ThreadPoolExecutor(max_workers=max(2, count)) as pool:
            while len(inflight) < target and time.time() < deadline:
                down = _left_button_down()
                if down and not pressed:
                    now = time.time()
                    if now - last_click >= min_gap:
                        pt = cursor_point()
                        x, y = int(pt[0]), int(pt[1])
                        inflight.append(pool.submit(_freeze_click_at, x, y))
                        last_click = now
                pressed = down
                time.sleep(poll_s)
            for fut in inflight:
                try:
                    ev = fut.result(timeout=max(2.0, timeout))
                    _emit(ev)
                except Exception:
                    pass
        return events

    while len(events) < target and time.time() < deadline:
        down = _left_button_down()
        if down and not pressed:
            now = time.time()
            if now - last_click >= min_gap:
                pt = cursor_point()
                x, y = int(pt[0]), int(pt[1])
                if with_frames or on_click is not None:
                    _emit(_freeze_click_at(x, y))
                else:
                    events.append((x, y))
                last_click = now
        pressed = down
        time.sleep(poll_s)
    return events


def capture_chain_session(
    wf: TaughtWorkflow,
    step_id: str,
    click_count: int = 2,
    countdown: float = 1.6,
    window_sec: float = 25.0,
) -> dict:
    """One session: user performs N clicks; each gets multi-witness capture."""
    step = get_step(wf, step_id)
    click_count = max(1, min(int(click_count), 2))
    if countdown:
        time.sleep(float(countdown))
    pending: list[dict] = []

    def _on_click(ev: dict) -> None:
        pending.append(ev)

    listen_clicks(
        click_count, timeout=window_sec, with_frames=True, on_click=_on_click,
        min_gap=0.02, async_freeze=True,
    )
    heard = len(pending)
    captures: list = []
    for i, ev in enumerate(pending[:click_count]):
        try:
            captures.append(capture_show(
                wf, step_id,
                point=tuple(ev["point"]),
                countdown=0,
                sub_index=i,
                pre_frame_img=ev.get("frame_img"),
                pre_frame_origin=ev.get("frame_origin"),
                pre_click_grab_offset_ms=ev.get("click_grab_offset_ms"),
                pre_a11y_raw=ev.get("a11y_raw"),
                pre_dom_raw=ev.get("dom_raw"),
                pre_structural_state=ev.get("structural_state"),
            ))
        except Exception:
            pass
    points = []
    for cap in captures:
        anc = cap.get("anchor") or {}
        pt = anc.get("point")
        if not pt:
            res = cap.get("resolution") or {}
            pt = res.get("point")
        if pt:
            points.append(pt)
    got = len(points)
    heard = len(pending)
    ignored = 0
    if len(points) > click_count:
        ignored = len(points) - click_count
    note = ""
    if ignored:
        note = f"You declared {click_count} clicks — ignoring {ignored} extra click(s)."
        step.chain_capture = dict(step.chain_capture or {})
        step.chain_capture["ignored_extra"] = ignored
        step.chain_capture["last_note"] = note
        save_taught(wf)
    return {
        "ok": True,
        "mode": "batch",
        "points": [list(p) for p in points[:click_count]],
        "captures": captures,
        "click_count": click_count,
        "got": got,
        "heard": heard,
        "ignored": ignored,
        "note": note,
    }
