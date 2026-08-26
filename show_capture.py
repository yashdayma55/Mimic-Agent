"""Show-me capture: a11y + DOM + 64x64 crop at a point. No execution."""

from __future__ import annotations

import os
import re
import time
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
        from browser_locator import element_at_point

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


def multi_witness_capture(point, monitor=None, crop_path: str | None = None) -> dict:
    """Run a11y, DOM, and vision on the same moment. Do not pick a winner."""
    x, y = int(point[0]), int(point[1])
    if monitor is None:
        idx, monitor = _monitor_index(x, y)
    elif isinstance(monitor, int):
        mons = _list_monitors()
        idx = monitor
        monitor = mons[idx] if 0 <= idx < len(mons) else _monitor_at(x, y)
    else:
        idx, _mon = _monitor_index(x, y)
        monitor = tuple(monitor)
    a11y_raw = _element_at(x, y)
    a11y_saw = bool(a11y_raw.get("name") or a11y_raw.get("control_type") or a11y_raw.get("rect"))
    a11y = {
        "saw": a11y_saw,
        "name": a11y_raw.get("name"),
        "control_type": a11y_raw.get("control_type"),
        "automation_id": a11y_raw.get("automation_id"),
        "rect": a11y_raw.get("rect"),
        "parent_path": a11y_raw.get("parent_path"),
        "confidence": _a11y_confidence(a11y_raw) if a11y_saw else "low",
        "account": _a11y_account(a11y_raw),
    }
    dom_raw = _browser_at(x, y) or {}
    dom_saw = bool(dom_raw.get("selector") or dom_raw.get("text") or dom_raw.get("role") or dom_raw.get("name"))
    dom = {
        "saw": dom_saw,
        "selector": dom_raw.get("selector") or dom_raw.get("css"),
        "text": dom_raw.get("text") or dom_raw.get("name"),
        "role": dom_raw.get("role") or dom_raw.get("role_name"),
        "confidence": "medium" if dom_saw else "low",
        "account": _dom_account(dom_raw),
        "rect": dom_raw.get("rect"),
    }
    vision = _vision_witness(x, y, crop_path)
    witnesses = {"a11y": a11y, "dom": dom, "vision": vision}
    agreement, note = score_agreement(witnesses)
    return {
        "point": [x, y],
        "monitor": idx,
        "witnesses": witnesses,
        "agreement": agreement,
        "conflict_note": note,
    }


def _save_image(img, dest: str, max_w: int = 1400) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    w, h = img.size
    if w > max_w:
        img = img.resize((max_w, max(1, int(h * max_w / w))))
    img.save(dest)
    return dest


def _crop_64(x: int, y: int, dest: str) -> str:
    img, origin = _grab_screen()
    half = 32
    box = _to_image_box((x - half, y - half, x + half, y + half), origin, img.size)
    crop = img.crop(box).resize((64, 64))
    return _save_image(crop, dest, max_w=64)


def _save_context(x: int, y: int, dest: str, element_rect=None, focus: bool = False) -> str:
    """Crop the focused monitor (or window). Tight element crop only when focus=True."""
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


def _capture_at(x: int, y: int, dest_stem: str, wf_name: str, focus: bool = False) -> dict:
    a11y = _element_at(x, y)
    dom = _browser_at(x, y)
    crop_rel = os.path.join("anchors", f"{dest_stem}.png")
    ctx_rel = os.path.join("anchors", f"{dest_stem}_window.png")
    crop_abs = os.path.join(workflow_dir(wf_name), crop_rel)
    ctx_abs = os.path.join(workflow_dir(wf_name), ctx_rel)
    _crop_64(x, y, crop_abs)
    _save_context(x, y, ctx_abs, element_rect=a11y.get("rect"), focus=bool(focus))
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
    return {
        "primary": primary,
        "parent_path": a11y.get("parent_path"),
        "crop_path": crop_rel,
        "context_path": ctx_rel,
        "preview_path": ctx_rel if not focus else crop_rel,
        "focused": bool(focus),
        "screen_rect": a11y.get("rect") or [x - 32, y - 32, x + 32, y + 32],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "crop_abs": crop_abs,
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
) -> dict:
    """Capture at `point` or the focused element's centre. Tests pass countdown=0.

    Default picture is the whole window. Pass focus=True to crop onto the
    element — only when the user asked to zoom in.
    """
    step = get_step(wf, step_id)
    if countdown:
        time.sleep(float(countdown))
    if point is None:
        point = _fallback_point()
    x, y = int(point[0]), int(point[1])
    packed = _capture_at(x, y, step.id, wf.name, focus=bool(focus))
    crop_abs = packed.pop("crop_abs")
    anchor = packed
    idx, mon = _monitor_index(x, y)
    mw = multi_witness_capture((x, y), monitor=mon, crop_path=crop_abs)
    mw["monitor"] = idx
    anchor["witnesses"] = mw["witnesses"]
    anchor["agreement"] = mw["agreement"]
    anchor["conflict_note"] = mw["conflict_note"]
    anchor["point"] = [x, y]
    anchor["monitor"] = idx
    step.anchor = anchor
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
        "crop_abs": crop_abs,
        "witnesses": mw,
        "confirm_question": summary,
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
