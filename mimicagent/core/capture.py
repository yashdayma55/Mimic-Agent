"""Screen capture, SoM marking, window focus, and sensitive-region redaction.

Copied (not imported) from the outreach pipeline's vision patterns.
Redaction is ON by default. Does not import email_workflow_automation.
"""

from __future__ import annotations

import concurrent.futures
import ctypes
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from pywinauto import Desktop

from mimicagent.core import config

# --- DPI awareness (same idea as set_of_mark: tree coords == pixels) ---
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

SENSITIVE_HINTS = re.compile(
    r"(password|passcode|ssn|social security|credit card|card number|cvv|cvc|"
    r"pin\b|secret|api[_ ]?key|token|routing|account number)",
    re.I,
)

# Clickable filter — copied from set_of_mark.collect_clickable_elements
# (the walk email_workflow_automation.apollo actually calls).
_CLICKABLE = {
    "Button",
    "Hyperlink",
    "MenuItem",
    "TabItem",
    "ListItem",
    "Edit",
    "ComboBox",
    "CheckBox",
    "RadioButton",
    "Text",
    "TreeItem",
    "Image",
    "Custom",
    "Group",
    "Pane",
}


def _ensure_capture_dir() -> Path:
    d = Path(config.CAPTURE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def grab_full_screen() -> tuple[Image.Image, int, int, float]:
    """Screenshot the virtual desktop. Returns (img, ox, oy, scale)."""
    user = ctypes.windll.user32
    left = int(user.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
    top = int(user.GetSystemMetrics(77))  # SM_YVIRTUALSCREEN
    width = int(user.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
    height = int(user.GetSystemMetrics(79))  # SM_CYVIRTUALSCREEN
    if width <= 0 or height <= 0:
        # Fallback: primary monitor
        from PIL import ImageGrab

        img = ImageGrab.grab()
        return img, 0, 0, 1.0

    from PIL import ImageGrab

    bbox = (left, top, left + width, top + height)
    try:
        img = ImageGrab.grab(bbox=bbox, all_screens=True)
    except TypeError:
        img = ImageGrab.grab(bbox=bbox)
    return img, left, top, 1.0


def find_sensitive_rects(elements: list[dict]) -> list[tuple[int, int, int, int]]:
    rects = []
    for el in elements:
        name = el.get("name") or ""
        if SENSITIVE_HINTS.search(name):
            rect = el.get("rect")
            if rect:
                rects.append(tuple(rect))  # type: ignore[arg-type]
    return rects


def redact_image(
    img: Image.Image,
    elements: list[dict],
    offset_x: int = 0,
    offset_y: int = 0,
) -> Image.Image:
    """Black out sensitive regions. Returns a copy; original untouched."""
    safe = img.copy()
    draw = ImageDraw.Draw(safe)
    rects = find_sensitive_rects(elements)
    for L, T, R, B in rects:
        L -= offset_x
        R -= offset_x
        T -= offset_y
        B -= offset_y
        draw.rectangle([L, T, R, B], fill=(0, 0, 0))
    if rects:
        print(f"  [mimic-capture] redacted {len(rects)} sensitive region(s)")
    return safe


# Name-based overlay only. Do NOT treat generic mid-size Pane/Group as overlays —
# LinkedIn pages have dozens of those and they distorted ranking (overlays=50).
_OVERLAY_NAME = re.compile(
    r"\b(dialog|compose|new message|popup|popover|dropdown|apollo|"
    r"extensions|tooltip|modal)\b",
    re.I,
)


def _screen_size() -> tuple[int, int]:
    user32 = ctypes.windll.user32
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def _point_in_rect(x: int, y: int, rect: tuple[int, int, int, int]) -> bool:
    L, T, R, B = rect
    return L <= x <= R and T <= y <= B


def _is_overlay_container(
    control_type: str, name: str, rect: tuple[int, int, int, int], sw: int, sh: int
) -> bool:
    ct = (control_type or "")
    if ct in ("Dialog", "Menu"):
        return True
    if _OVERLAY_NAME.search(name or ""):
        return True
    return False


def rank_som_candidates(
    clickable: list[dict],
    overlays: list[dict],
    sw: int,
    sh: int,
) -> list[dict]:
    """Prefer overlay/dialog content over background page, then later tree order.

    Sort key: in topmost overlay, in any overlay, later DFS index, closer to center.
    """
    topmost = overlays[-1] if overlays else None
    cx0, cy0 = sw / 2.0, sh / 2.0

    def sort_key(el: dict) -> tuple:
        ecx, ecy = int(el["cx"]), int(el["cy"])
        in_top = 0
        in_any = 0
        if topmost and _point_in_rect(ecx, ecy, topmost["rect"]):
            in_top = 1
        for ov in overlays:
            if _point_in_rect(ecx, ecy, ov["rect"]):
                in_any = 1
                break
        dist = (ecx - cx0) ** 2 + (ecy - cy0) ** 2
        dfs = int(el.get("_dfs_index") or 0)
        return (-in_top, -in_any, -dfs, dist)

    return sorted(clickable, key=sort_key)


def _find_named_subtree(root, hint: str):
    """Last (topmost) descendant whose name contains *hint*."""
    needle = hint.strip().lower()
    if not needle:
        return None
    last = None
    try:
        for el in root.descendants():
            try:
                name = el.window_text() or ""
                if needle in name.lower():
                    last = el
            except Exception:
                continue
    except Exception:
        return None
    return last


def collect_clickable_elements(
    window_title=None,
    max_elems=None,
    subtree_root: str | None = None,
):
    """Walk the accessibility tree; rank overlays first, then cap.

    Collects every clickable node, then keeps the top *max_elems* by overlay /
    z-order / center proximity so Apollo panels and Gmail compose are not
    truncated away in favor of background page content.
    """
    max_elems = max_elems or config.SOM_MAX_ELEMS
    CLICKABLE = {"Button", "Hyperlink", "MenuItem", "TabItem", "ListItem",
                 "Edit", "ComboBox", "CheckBox", "RadioButton", "Text",
                 "TreeItem", "Image", "Custom", "Group", "Pane"}
    elements: list[dict] = []
    overlays: list[dict] = []
    try:
        if window_title:
            root = Desktop(backend="uia").window(title_re=f".*{window_title}.*")
        else:
            from pywinauto import Application
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            root = Desktop(backend="uia").window(handle=hwnd)
        root.wait("exists", timeout=3)
        window_root = root
        if subtree_root:
            scoped = _find_named_subtree(root, subtree_root)
            if scoped is None:
                print(
                    f"  [mimic-capture] WARNING: subtree_root={subtree_root!r} not found; "
                    "failing open to full window"
                )
            else:
                try:
                    n_desc = len(scoped.descendants())
                except Exception:
                    n_desc = 0
                if n_desc == 0:
                    print(
                        f"  [mimic-capture] WARNING: subtree_root={subtree_root!r} "
                        f"name={(scoped.window_text() or '')!r} is a leaf "
                        f"(0 descendants); failing open to full window"
                    )
                else:
                    print(
                        f"  [mimic-capture] scoped SoM to subtree_root={subtree_root!r} "
                        f"name={(scoped.window_text() or '')!r} descendants={n_desc}"
                    )
                    root = scoped
        descendants = root.descendants()
    except Exception as e:
        print(f"   could not walk tree: {e}")
        return elements

    sw, sh = _screen_size()
    dfs = 0
    for el in descendants:
        dfs += 1
        try:
            ct = el.element_info.control_type
            r = el.rectangle()
            w, h = r.right - r.left, r.bottom - r.top
            name = el.window_text() or ""
            rec = {
                "name": name,
                "control_type": ct,
                "rect": (r.left, r.top, r.right, r.bottom),
                "cx": (r.left + r.right) // 2,
                "cy": (r.top + r.bottom) // 2,
                "_dfs_index": dfs,
            }
            if w >= 8 and h >= 8 and _is_overlay_container(ct, name, rec["rect"], sw, sh):
                overlays.append(rec)
            if ct not in CLICKABLE:
                continue
            if w < 8 or h < 8 or w > 1800 or h > 1000:
                continue
            elements.append(rec)
        except Exception:
            continue

    pre = len(elements)
    if subtree_root and pre == 0:
        print(
            f"  [mimic-capture] WARNING: subtree_root={subtree_root!r} produced "
            "0 clickable elements; failing open to full unscoped tree"
        )
        return collect_clickable_elements(
            window_title=window_title, max_elems=max_elems, subtree_root=None
        )
    ranked = rank_som_candidates(elements, overlays, sw, sh)
    kept = ranked[:max_elems]
    out = []
    for i, el in enumerate(kept, 1):
        item = {
            "id": i,
            "name": el.get("name") or "",
            "control_type": el.get("control_type"),
            "rect": el["rect"],
            "cx": el["cx"],
            "cy": el["cy"],
            "automation_id": "",
        }
        item["control_type"] = item.get("control_type")
        out.append(item)
    print(
        f"  [mimic-capture] SoM a11y elements: pre-filter={pre} "
        f"post-filter={len(out)} cap={max_elems} overlays={len(overlays)} "
        f"window_title={window_title!r} subtree_root={subtree_root!r}"
    )
    return out


def foreground_window_title() -> str:
    try:
        import win32gui

        return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
    except Exception:
        return ""


UIA_INSPECT_TIMEOUT_S = 4.0
_CHROME_PROCS = frozenset({"chrome.exe", "msedge.exe"})
_UIA_INSPECT_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="uia-inspect"
)


def _empty_a11y_info() -> dict:
    return {
        "total_descendants": 0,
        "clickable": 0,
        "has_document": False,
        "web_like": 0,
    }


def _exe_for_title_hint(window_title: str) -> str | None:
    """Process name of a visible window matching *window_title*. No UIA walk."""
    try:
        import win32gui
        import win32process
        import psutil
    except Exception:
        return None
    hint = (window_title or "").strip().lower()
    if not hint:
        return None
    found: list[tuple[float, str]] = []

    def enum_cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            title = win32gui.GetWindowText(hwnd) or ""
            t = title.lower()
            if hint not in t:
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            pname = psutil.Process(pid).name().lower()
            score = len(hint) / max(len(t), 1)
            found.append((score, pname))
        except Exception:
            pass

    win32gui.EnumWindows(enum_cb, None)
    if not found:
        return None
    found.sort(key=lambda x: x[0], reverse=True)
    return found[0][1]


def _is_chromium_window(window_title: str) -> bool:
    t = (window_title or "").lower()
    if "google chrome" in t or "microsoft edge" in t:
        return True
    pname = _exe_for_title_hint(window_title)
    return bool(pname and pname in _CHROME_PROCS)


def inspect_window_a11y(window_title: str) -> dict:
    """Count UIA nodes; skip Chrome (vision path) and bound any native UIA walk.

    Chrome FindAll on a LinkedIn profile can hang indefinitely; pywinauto's
    wait('exists', timeout=3) does not bound that enumeration.
    """
    empty = _empty_a11y_info()
    if _is_chromium_window(window_title):
        print(
            "[capture] target is chrome.exe — skipping UIA inspection "
            "(vision path does not need it)"
        )
        return empty

    try:
        fut = _UIA_INSPECT_POOL.submit(_inspect_window_a11y_uia, window_title)
        return fut.result(timeout=UIA_INSPECT_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        print(
            f"[capture] UIA inspect timed out after {UIA_INSPECT_TIMEOUT_S}s "
            f"(window={window_title!r}) — skipping a11y inspection"
        )
        return empty
    except Exception as e:
        print(f"[capture] UIA inspect failed for {window_title!r}: {e}")
        return empty


def _inspect_window_a11y_uia(window_title: str) -> dict:
    """Inner UIA walk — must not be called on the harness thread for Chrome."""
    from pywinauto import Desktop

    info = _empty_a11y_info()
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pythoncom = None  # type: ignore
    try:
        try:
            root = Desktop(backend="uia").window(title_re=f".*{window_title}.*")
            root.wait("exists", timeout=3)
            descendants = root.descendants()
        except Exception as e:
            print(f"  [a11y] could not inspect window {window_title!r}: {e}")
            print(
                "  [a11y] NOTE: if this is Chrome, relaunch with "
                "--force-renderer-accessibility so page content is in the UIA tree."
            )
            return info

        type_counts: dict[str, int] = {}
        web_like = 0
        has_document = False
        samples: list[str] = []
        for el in descendants:
            try:
                ct = str(el.element_info.control_type or "")
                type_counts[ct] = type_counts.get(ct, 0) + 1
                if ct in ("Document", "WebView"):
                    has_document = True
                name = (el.window_text() or "").strip()
                if ct in ("Document", "Edit", "Hyperlink", "Text") and name:
                    web_like += 1
                    if len(samples) < 6:
                        samples.append(f"{ct}:{name[:48]}")
            except Exception:
                continue

        clickable = collect_clickable_elements(window_title=window_title)
        info["total_descendants"] = len(descendants)
        info["clickable"] = len(clickable)
        info["has_document"] = has_document
        info["web_like"] = web_like

        top_types = sorted(type_counts.items(), key=lambda x: -x[1])[:10]
        print(
            f"  [a11y] window matching {window_title!r}: "
            f"{info['total_descendants']} UIA descendants, "
            f"{info['clickable']} clickable (SoM filter)"
        )
        print(f"  [a11y] control_type top: {top_types}")
        print(f"  [a11y] Document/WebView present: {has_document}; web-like named nodes: {web_like}")
        if samples:
            print(f"  [a11y] sample names: {samples}")
        if not has_document or info["total_descendants"] < 80:
            print(
                "  [a11y] NOTE: web page content may NOT be in the UIA tree. "
                "Relaunch Chrome with --force-renderer-accessibility "
                "(or chrome://accessibility -> enable for this tab) if LinkedIn/"
                "Gmail/Apollo should resolve at layer 2 instead of vision."
            )
        else:
            print("  [a11y] renderer accessibility looks ON (Document/web nodes present).")
        return info
    finally:
        if pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass



def draw_marks(
    img: Image.Image,
    elements: list[dict],
    offset_x: int = 0,
    offset_y: int = 0,
    scale: float = 1.0,
) -> Image.Image:
    annotated = img.copy()
    draw = ImageDraw.Draw(annotated)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    for el in elements:
        L, T, R, B = el["rect"]
        L = int((L - offset_x) * scale)
        R = int((R - offset_x) * scale)
        T = int((T - offset_y) * scale)
        B = int((B - offset_y) * scale)
        draw.rectangle([L, T, R, B], outline=(255, 0, 0), width=2)
        label = str(el["id"])
        draw.rectangle([L, T, L + 18, T + 18], fill=(255, 0, 0))
        draw.text((L + 3, T + 1), label, fill=(255, 255, 255), font=font)
    return annotated


def focus_app(proc_names: list[str], title_hint: str | None = None) -> str:
    """Bring a matching window to the foreground by process name + optional title.

    Returns the title of the window that was focused, or '' on failure.
    Callers must use this returned title — do not re-query the foreground
    window (SetForegroundWindow can succeed while GetForegroundWindow is empty).
    """
    try:
        import win32con
        import win32gui
        import win32process
    except Exception as e:
        print(f"  [mimic-capture] focus_app needs pywin32: {e}")
        return ""

    try:
        import psutil
    except Exception:
        psutil = None  # type: ignore

    targets = [pn.lower() for pn in proc_names]
    candidates: list[tuple[int, str]] = []

    def enum_cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil is None:
                return
            pname = psutil.Process(pid).name().lower()
            title = win32gui.GetWindowText(hwnd)
            if pname in targets and title.strip():
                candidates.append((hwnd, title))
        except Exception:
            pass

    win32gui.EnumWindows(enum_cb, None)
    if not candidates:
        print(f"  [mimic-capture] focus_app: no window for {proc_names}")
        return ""

    chosen_hwnd = candidates[0][0]
    chosen_title = candidates[0][1]
    reason = "most recently active match"

    if title_hint:
        hint = title_hint.strip().lower()
        if hint:
            scored = []
            for hwnd, title in candidates:
                t = title.lower()
                if hint in t:
                    score = len(hint) / max(len(t), 1)
                    scored.append((score, hwnd, title))
            if not scored:
                titles = [t for _, t in candidates[:8]]
                print(
                    f"  [mimic-capture] focus_app: no window title contains "
                    f"{title_hint!r}. candidates={titles}"
                )
                return ""
            scored.sort(key=lambda x: x[0], reverse=True)
            _, chosen_hwnd, chosen_title = scored[0]
            reason = f"title hint {title_hint!r}"

    try:
        win32gui.ShowWindow(chosen_hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(chosen_hwnd)
        print(
            f"  [mimic-capture] focused [{targets}] "
            f"{chosen_title!r} ({reason})"
        )
        return chosen_title
    except Exception as e:
        print(f"  [mimic-capture] SetForegroundWindow failed: {e}")
        return ""


def capture_raw_no_focus(save_path: str | None = None) -> tuple[str, dict[str, Any]]:
    """Raw fullscreen grab WITHOUT focus_app / SetForegroundWindow."""
    print("  [mimic-capture] NO-FOCUS raw capture")
    img, ox, oy, scale = grab_full_screen()
    if save_path is None:
        save_path = str(_ensure_capture_dir() / "nofocus_raw.png")
    img.save(save_path)
    meta = {
        "width": img.size[0],
        "height": img.size[1],
        "ox": ox,
        "oy": oy,
        "scale": scale,
    }
    print(f"  [mimic-capture] raw -> {save_path} {meta['width']}x{meta['height']}")
    return save_path, meta


def capture_som_marked(
    save_path: str | None = None,
    *,
    no_focus: bool = True,
    redact: bool = True,
    title_hint: str | None = None,
    proc_names: list[str] | None = None,
    subtree_root: str | None = None,
) -> tuple[list[dict], str, dict[str, Any]]:
    """Fullscreen grab + SoM numbered marks. Redaction on by default."""
    if not no_focus and proc_names:
        focus_app(proc_names, title_hint=title_hint)

    if no_focus:
        print("  [mimic-capture] NO-FOCUS SoM capture")
    else:
        print("  [mimic-capture] SoM capture (may have focused window)")

    elements = collect_clickable_elements(
        window_title=title_hint,
        max_elems=config.SOM_MAX_ELEMS,
        subtree_root=subtree_root,
    )
    img, ox, oy, scale = grab_full_screen()
    for el in elements:
        el["sx"] = int((el["cx"] - ox) * scale)
        el["sy"] = int((el["cy"] - oy) * scale)
    if not elements:
        print("  [mimic-capture] WARNING: SoM element count is 0")
    else:
        print(f"  [mimic-capture] SoM capture element count={len(elements)} (non-zero OK)")

    if redact:
        try:
            img = redact_image(img, elements, ox, oy)
        except Exception as e:
            print(f"  [mimic-capture] redact skipped: {e}")

    if save_path is None:
        save_path = str(_ensure_capture_dir() / "som_marked.png")
    annotated = draw_marks(img, elements, ox, oy, scale)
    annotated.save(save_path)
    meta = {
        "width": annotated.size[0],
        "height": annotated.size[1],
        "ox": ox,
        "oy": oy,
        "scale": scale,
        "raw_path": save_path,
    }
    print(
        f"  [mimic-capture] SoM -> {save_path} "
        f"({len(elements)} elements, img {annotated.size[0]}x{annotated.size[1]})"
    )
    return elements, save_path, meta


def image_xy_to_screen(ix: float, iy: float, meta: dict) -> tuple[int, int]:
    scale = float(meta.get("scale") or 1.0) or 1.0
    ox = int(meta.get("ox") or 0)
    oy = int(meta.get("oy") or 0)
    return int(ix / scale + ox), int(iy / scale + oy)


def screen_to_image_xy(screen_x: int, screen_y: int, meta: dict) -> tuple[int, int]:
    scale = float(meta.get("scale") or 1.0) or 1.0
    ox = int(meta.get("ox") or 0)
    oy = int(meta.get("oy") or 0)
    return int((screen_x - ox) * scale), int((screen_y - oy) * scale)


def crop_around_screen_point(
    full_image_path: str,
    meta: dict,
    screen_x: int,
    screen_y: int,
    *,
    crop_w: int = 200,
    crop_h: int = 120,
    save_path: str | None = None,
) -> str:
    """Crop around a screen point; save and return path."""
    ix, iy = screen_to_image_xy(screen_x, screen_y, meta)
    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)
    left = max(0, ix - crop_w // 2)
    top = max(0, iy - crop_h // 2)
    if left + crop_w > w:
        left = max(0, w - crop_w)
    if top + crop_h > h:
        top = max(0, h - crop_h)
    right = min(w, left + crop_w)
    bottom = min(h, top + crop_h)
    if save_path is None:
        save_path = str(_ensure_capture_dir() / "verify_crop.png")
    with Image.open(full_image_path) as img:
        img.crop((left, top, right, bottom)).save(save_path)
    return save_path
