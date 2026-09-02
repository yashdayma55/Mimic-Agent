"""Derive step success checks from before/after frozen frames — semantic, not pixel-equal."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from teaching import TaughtWorkflow, get_step, save_taught
from workflow_folder import workflow_dir


def _title(w) -> str:
    try:
        return (w.window_text() or "").strip()
    except Exception:
        return ""


def _top_level_window_titles() -> list[str]:
    try:
        from ui_runner import _all_windows
        from app_ui_guard import is_own_window

        titles: list[str] = []
        for w in _all_windows():
            try:
                if not w.is_visible():
                    continue
                t = _title(w)
                if t and not is_own_window(t):
                    titles.append(t)
            except Exception:
                continue
        return sorted(set(titles))
    except Exception:
        return []


def _foreground_for_success_check(expected: str | None = None) -> str:
    """Prefer a real app window over MimicAgent / review UI stealing focus."""
    from app_ui_guard import is_own_window
    from ui_runner import _titles_match, foreground_title

    actual = foreground_title()
    if actual and not is_own_window(actual):
        return actual

    candidates = _top_level_window_titles()
    if expected:
        for title in candidates:
            if _titles_match(expected, title):
                return title
        exp_low = expected.lower()
        if "linkedin" in exp_low:
            for title in candidates:
                tl = title.lower()
                if "linkedin" in tl and ("chrome" in tl or "google chrome" in tl):
                    return title
    for title in candidates:
        if "linkedin" in title.lower() and "chrome" in title.lower():
            return title
    return actual


def _collect_a11y_elements(max_elems: int = 80) -> list[dict]:
    out: list[dict] = []
    try:
        from pywinauto import Desktop
        from app_ui_guard import is_own_window

        fg = Desktop(backend="uia").get_active()
        title = _title(fg)
        if is_own_window(title):
            return out
        for el in fg.descendants()[:max_elems]:
            try:
                name = (el.window_text() or "").strip() or None
                ctype = el.element_info.control_type
                if name or ctype:
                    out.append({"name": name, "control_type": ctype})
            except Exception:
                continue
    except Exception:
        pass
    return out


def snapshot_click_moment(
    x: int,
    y: int,
    a11y_raw: dict | None = None,
    dom_raw: dict | None = None,
) -> dict:
    """Fast snapshot at mousedown — must not block the click listener."""
    from ui_runner import foreground_title

    a11y = dict(a11y_raw or {})
    dom = dict(dom_raw or {})
    title = foreground_title()
    elems = []
    if a11y.get("name") or a11y.get("control_type"):
        elems.append({"name": a11y.get("name"), "control_type": a11y.get("control_type")})
    return {
        "foreground_title": title,
        "window_titles": [title] if title else [],
        "a11y_elements": elems,
        "browser_url": dom.get("url") or dom.get("href"),
        "point": [int(x), int(y)],
        "at": datetime.now(timezone.utc).isoformat(),
    }


def snapshot_structural_state(
    x: int | None = None,
    y: int | None = None,
    a11y_raw: dict | None = None,
    dom_raw: dict | None = None,
) -> dict:
    """Cheap structural snapshot — no vision."""
    from ui_runner import foreground_title

    dom = dict(dom_raw or {})
    url = dom.get("url") or dom.get("href")
    if not url and x is not None and y is not None and not dom:
        try:
            from show_capture import _browser_at

            dom = _browser_at(int(x), int(y)) or {}
            url = dom.get("url") or dom.get("href")
        except Exception:
            pass
    return {
        "foreground_title": foreground_title(),
        "window_titles": _top_level_window_titles(),
        "a11y_elements": _collect_a11y_elements(),
        "browser_url": url,
        "point": [int(x), int(y)] if x is not None and y is not None else None,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def wait_for_settle(max_wait: float = 1.2, stable_ms: int = 400) -> int:
    """Wait until foreground title is stable or max_wait elapses. Returns settle_ms."""
    from ui_runner import foreground_title

    t0 = time.perf_counter()
    last_title = foreground_title()
    last_change = t0
    while (time.perf_counter() - t0) < max_wait:
        time.sleep(0.05)
        title = foreground_title()
        if title != last_title:
            last_title = title
            last_change = time.perf_counter()
        elif int((time.perf_counter() - last_change) * 1000) >= stable_ms:
            break
    return int((time.perf_counter() - t0) * 1000)


def capture_after_frame(wf: TaughtWorkflow, step_id: str) -> dict | None:
    """Grab after-frame once UI settles. Excludes MimicAgent own UI."""
    from app_ui_guard import is_own_window
    from show_capture import _grab_screen, _monitor_index, _save_click_frame
    from ui_runner import foreground_title

    step = get_step(wf, step_id)
    settle_ms = wait_for_settle()
    title = foreground_title()
    if is_own_window(title):
        return None
    img, origin = _grab_screen()
    ox, oy = int(origin[0]), int(origin[1])
    pt = step.anchors[0].get("point") if step.anchors and step.anchors[0] else [400, 400]
    idx, _mon = _monitor_index(int(pt[0]), int(pt[1]))
    rel = os.path.join("anchors", f"{step.id}_after.png")
    abs_path = os.path.join(workflow_dir(wf.name), rel)
    _save_click_frame(img, abs_path)
    structural = snapshot_structural_state()
    return {
        "path": rel,
        "abs_path": abs_path,
        "monitor": idx,
        "origin": [ox, oy],
        "at": datetime.now(timezone.utc).isoformat(),
        "window_title": structural.get("foreground_title") or title,
        "settle_ms": settle_ms,
        "structural_state": structural,
    }


def _abs_frame_path(wf_name: str, rel: str | None) -> str | None:
    if not rel:
        return None
    if os.path.isabs(rel):
        return rel
    return os.path.join(workflow_dir(wf_name), rel)


def _before_frame_path(step, wf_name: str) -> str | None:
    anchors = [a for a in (step.anchors or []) if a]
    if not anchors:
        return None
    first = anchors[0]
    rel = first.get("click_frame_path")
    if not rel:
        stem = step.id
        rel = os.path.join("anchors", f"{stem}_click_frame.png")
    return _abs_frame_path(wf_name, rel)


def _elem_key(el: dict) -> tuple:
    return ((el.get("name") or "").strip().lower(), (el.get("control_type") or "").strip().lower())


def _tier1_signals(before: dict, after: dict) -> list[dict]:
    signals: list[dict] = []
    b_title = (before or {}).get("foreground_title") or ""
    a_title = (after or {}).get("foreground_title") or ""
    if b_title and a_title and b_title != a_title:
        signals.append({
            "kind": "window_title_changed",
            "detail": f"foreground title changed from {b_title!r} to {a_title!r}",
            "evidence": {"before": b_title, "after": a_title},
            "cost": "free",
            "confidence": "high",
            "check": {"type": "foreground_title", "expected": a_title, "before": b_title},
        })
    b_wins = set((before or {}).get("window_titles") or [])
    a_wins = set((after or {}).get("window_titles") or [])
    appeared = sorted(a_wins - b_wins)
    closed = sorted(b_wins - a_wins)
    for t in appeared:
        signals.append({
            "kind": "window_appeared",
            "detail": f"a new top-level window appeared: {t!r}",
            "evidence": {"title": t},
            "cost": "free",
            "confidence": "high",
            "check": {"type": "window_appeared", "expected": t},
        })
    for t in closed:
        signals.append({
            "kind": "window_closed",
            "detail": f"a top-level window closed: {t!r}",
            "evidence": {"title": t},
            "cost": "free",
            "confidence": "medium",
            "check": {"type": "window_closed", "expected": t},
        })
    b_elems = {_elem_key(e) for e in (before or {}).get("a11y_elements") or [] if _elem_key(e) != ("", "")}
    for el in (after or {}).get("a11y_elements") or []:
        key = _elem_key(el)
        if key == ("", ""):
            continue
        if key not in b_elems:
            name, ctype = el.get("name"), el.get("control_type")
            signals.append({
                "kind": "a11y_element_appeared",
                "detail": f"a {ctype or 'element'} named {name!r} entered the tree",
                "evidence": {"name": name, "control_type": ctype},
                "cost": "free",
                "confidence": "medium",
                "check": {"type": "a11y_present", "name": name, "control_type": ctype},
            })
    b_url = (before or {}).get("browser_url") or ""
    a_url = (after or {}).get("browser_url") or ""
    if b_url and a_url and b_url != a_url:
        signals.append({
            "kind": "browser_url_changed",
            "detail": f"browser URL changed",
            "evidence": {"before": b_url, "after": a_url},
            "cost": "free",
            "confidence": "high",
            "check": {"type": "browser_url", "expected": a_url, "before": b_url},
        })
    return signals


def _largest_changed_region(before_path: str, after_path: str, block: int = 32) -> list[int] | None:
    try:
        from PIL import Image

        if not os.path.isfile(before_path) or not os.path.isfile(after_path):
            return None
        b = Image.open(before_path).convert("RGB")
        a = Image.open(after_path).convert("RGB")
        w = min(b.size[0], a.size[0])
        h = min(b.size[1], a.size[1])
        if w < block or h < block:
            return None
        b = b.resize((w, h))
        a = a.resize((w, h))
        best = None
        best_score = 0
        for y in range(0, h - block, block // 2):
            for x in range(0, w - block, block // 2):
                box = (x, y, x + block, y + block)
                bp = b.crop(box)
                ap = a.crop(box)
                diff = sum(
                    abs(bp.getpixel((i, j))[k] - ap.getpixel((i, j))[k])
                    for i in range(block) for j in range(block) for k in range(3)
                )
                if diff > best_score:
                    best_score = diff
                    best = box
        if best is None or best_score < block * block * 30:
            return None
        return [int(best[0]), int(best[1]), int(best[2]), int(best[3])]
    except Exception:
        return None


def _vision_panel_appeared(crop_path: str) -> dict:
    out = {"appeared": None, "what": ""}
    if not crop_path or not os.path.isfile(crop_path):
        return out
    try:
        key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_key.txt")
        key = open(key_path, encoding="utf-8").read().strip() if os.path.isfile(key_path) else ""
        if not key:
            return out
        from vision_api import ask_vision_with_prompt

        prompt = (
            "Did a new panel or dialog appear in this region? "
            'Answer JSON only: {"appeared": true|false, "what": "short label"}'
        )
        with open(crop_path, "rb") as f:
            raw = f.read()
        vis = ask_vision_with_prompt(raw, key, prompt)
        appeared = vis.get("appeared")
        if appeared is None and vis.get("found") is not None:
            appeared = bool(vis.get("found"))
        out["appeared"] = bool(appeared) if appeared is not None else None
        out["what"] = (vis.get("what") or vis.get("what_you_see") or "").strip()
    except Exception:
        pass
    return out


def _tier2_signal(
    before_path: str | None,
    after_path: str | None,
    wf_name: str,
    step_id: str,
) -> dict | None:
    if not before_path or not after_path:
        return None
    region = _largest_changed_region(before_path, after_path)
    if not region:
        return None
    try:
        from PIL import Image
        from show_capture import _save_image

        img = Image.open(after_path)
        crop = img.crop((region[0], region[1], region[2], region[3]))
        crop_rel = os.path.join("anchors", f"{step_id}_success_diff.png")
        crop_abs = os.path.join(workflow_dir(wf_name), crop_rel)
        _save_image(crop, crop_abs, max_w=400)
    except Exception:
        return None
    vis = _vision_panel_appeared(crop_abs)
    if not vis.get("appeared"):
        return None
    label = vis.get("what") or "a new panel"
    return {
        "kind": "vision_panel_appeared",
        "detail": f"a new panel or dialog appeared ({label})",
        "evidence": {"region": region, "crop_path": crop_rel, "vision": vis},
        "cost": "vision",
        "confidence": "medium",
        "check": {"type": "vision_panel", "crop_path": crop_rel, "region": region, "label": label},
    }


def _tier3_user_signal(step) -> dict | None:
    for q in step.qa_history or []:
        ql = (q.get("q") or "").lower()
        if "succeed" in ql and (q.get("a") or "").strip():
            text = q["a"].strip()
            return {
                "kind": "user_stated",
                "detail": text,
                "evidence": {"text": text},
                "cost": "free",
                "confidence": "low",
                "check": {"type": "user_text", "text": text},
            }
    return None


def derive_success_signals(step, wf_name: str | None = None) -> list[dict]:
    """Compare before/after structural state and frames. Cheapest signals first."""
    wf_name = wf_name or getattr(step, "_wf_name", "") or ""
    before = getattr(step, "before_state", None) or {}
    after_frame = getattr(step, "after_frame", None) or {}
    after = (after_frame or {}).get("structural_state") or {}
    before_path = _before_frame_path(step, wf_name) if wf_name else None
    after_path = _abs_frame_path(wf_name, (after_frame or {}).get("path")) if wf_name else None

    signals = _tier1_signals(before, after)
    if not signals and before_path and after_path:
        t2 = _tier2_signal(before_path, after_path, wf_name, step.id)
        if t2:
            signals.append(t2)
    user_sig = _tier3_user_signal(step)
    if user_sig:
        signals.append(user_sig)
    return signals


def format_success_confirmation(signal: dict) -> str:
    kind = signal.get("kind") or ""
    if kind == "window_title_changed":
        ev = signal.get("evidence") or {}
        return (
            f"After your clicks I saw the window title change to {ev.get('after')!r} "
            f"(was {ev.get('before')!r}). Is that what success looks like?"
        )
    if kind == "window_appeared":
        t = (signal.get("evidence") or {}).get("title")
        return f"After your clicks I saw a new window appear: {t!r}. Is that what success looks like?"
    if kind == "a11y_element_appeared":
        ev = signal.get("evidence") or {}
        return (
            f"After your clicks I saw a new {ev.get('control_type') or 'element'} "
            f"named {ev.get('name')!r} appear. Is that what success looks like?"
        )
    if kind == "browser_url_changed":
        ev = signal.get("evidence") or {}
        return f"After your clicks the browser URL changed to {ev.get('after')!r}. Is that what success looks like?"
    if kind == "vision_panel_appeared":
        return f"After your clicks I saw {signal.get('detail')}. Is that what success looks like?"
    return f"After your clicks I observed: {signal.get('detail')}. Is that what success looks like?"


def success_check_text(signal: dict) -> str:
    return (signal.get("detail") or "").strip() or "an observable UI change occurred"


def is_linkedin_chrome_title(title: str | None) -> bool:
    t = (title or "").strip().lower()
    if not t:
        return False
    return "linkedin" in t and ("chrome" in t or "google chrome" in t)


def step_profile_varies(step) -> bool:
    und = getattr(step, "understanding", None) or {}
    blob = " ".join(
        [
            getattr(step, "varies_note", "") or "",
            " ".join(getattr(step, "parameters", None) or []),
            " ".join(und.get("varies_each_run") or []),
        ]
    ).lower()
    return "linkedin_profile" in blob or "{linkedin_profile}" in blob


def profile_aware_success_text(before: str | None = None) -> str:
    b = (before or "Extensions").strip() or "Extensions"
    return f"foreground title changed from {b!r} to a LinkedIn profile in Google Chrome"


def normalize_profile_aware_success(step) -> bool:
    """Relax a person-specific LinkedIn title success check when the profile varies."""
    if not step_profile_varies(step):
        return False
    und = dict(step.understanding or {})
    ev = dict(und.get("success_evidence") or {})
    check = dict(ev.get("check") or {})
    if check.get("type") != "foreground_title":
        return False
    expected = (check.get("expected") or "").strip()
    if not is_linkedin_chrome_title(expected):
        return False
    before = (check.get("before") or "Extensions").strip() or "Extensions"
    check["pattern"] = "linkedin_chrome_profile"
    ev["check"] = check
    und["success_evidence"] = ev
    und["success_check"] = profile_aware_success_text(before)
    plain = (und.get("plain_summary") or "").strip()
    if plain and expected in plain:
        und["plain_summary"] = plain.replace(
            f"foreground title changed from 'Extensions' to {expected!r}",
            und["success_check"],
        ).replace(
            f"I treat success as foreground title changed from 'Extensions' to {expected!r}.",
            f"I treat success as {und['success_check']}.",
        )
    step.understanding = und
    return True


def _foreground_title_matches(step, expected: str, actual: str) -> bool:
    from ui_runner import _titles_match

    if _titles_match(expected, actual):
        return True
    check = ((step.understanding or {}).get("success_evidence") or {}).get("check") or {}
    pattern = (check.get("pattern") or "").strip()
    if pattern == "linkedin_chrome_profile" or (
        step_profile_varies(step) and is_linkedin_chrome_title(expected)
    ):
        return is_linkedin_chrome_title(actual)
    return False


def link_expected_start_frame(wf: TaughtWorkflow, step) -> None:
    """Step N after-frame becomes step N+1 expected start."""
    idx = None
    for i, s in enumerate(wf.steps):
        if s.id == step.id:
            idx = i
            break
    if idx is None or idx + 1 >= len(wf.steps):
        return
    nxt = wf.steps[idx + 1]
    path = (step.after_frame or {}).get("path")
    if path:
        nxt.expected_start_frame = path


def expected_start_note(wf: TaughtWorkflow, step) -> str | None:
    """One-line note for step card when it should begin where a prior step ended."""
    if not getattr(step, "expected_start_frame", None):
        return None
    prev = None
    for i, s in enumerate(wf.steps):
        if s.id == step.id and i > 0:
            prev = wf.steps[i - 1]
            break
    if not prev:
        return None
    desc = (prev.user_description or prev.id)[:80]
    after_title = ((prev.after_frame or {}).get("window_title") or "").strip()
    if after_title:
        return f"This step should begin where step {prev.id} ended — {after_title!r} open."
    return f"This step should begin where step {prev.id} ended — {desc}."


def finalize_step_capture(wf: TaughtWorkflow, step_id: str) -> dict:
    """After all sub-clicks: capture after-frame and derive success signal candidates."""
    step = get_step(wf, step_id)
    anchors = [a for a in (step.anchors or []) if a]
    if not anchors:
        return {"ok": False, "reason": "no anchors"}
    first = anchors[0]
    step.before_state = getattr(step, "before_state", None) or first.get("structural_state")
    if not step.before_state:
        pt = first.get("point") or [400, 400]
        step.before_state = snapshot_structural_state(int(pt[0]), int(pt[1]))
    step.after_frame = capture_after_frame(wf, step_id)
    step.success_candidates = derive_success_signals(step, wf.name)
    link_expected_start_frame(wf, step)
    save_taught(wf)
    top = step.success_candidates[0] if step.success_candidates else None
    if top and top.get("kind") == "user_stated":
        top = step.success_candidates[1] if len(step.success_candidates) > 1 else None
    return {
        "ok": True,
        "after_frame": step.after_frame,
        "success_candidates": step.success_candidates,
        "top_signal": top,
    }


def verify_success_check(
    step,
    wf_name: str | None = None,
    before_demo: dict | None = None,
    after_demo: dict | None = None,
    os_input_calls: int = 0,
) -> dict:
    """Judge demo/run success against stored success_check. Tier 1 first, no pixels."""
    understanding = step.understanding or {}
    check = understanding.get("success_check") or ""
    evidence = understanding.get("success_evidence") or {}
    stored = evidence.get("check") or {}
    if not check and not stored:
        return {"ok": None, "reason": "no success check recorded", "cost": "free"}

    ctype = stored.get("type")
    if ctype == "foreground_title":
        expected = stored.get("expected") or ""
        if before_demo and after_demo:
            pre_title = (before_demo or {}).get("foreground_title") or ""
            post_title = (after_demo or {}).get("foreground_title") or ""
            pre_elems = {
                ((e.get("name") or "").strip().lower(), (e.get("control_type") or "").strip().lower())
                for e in (before_demo or {}).get("a11y_elements") or []
            }
            post_elems = {
                ((e.get("name") or "").strip().lower(), (e.get("control_type") or "").strip().lower())
                for e in (after_demo or {}).get("a11y_elements") or []
            }
            if (
                pre_title == post_title
                and pre_elems == post_elems
                and not int(os_input_calls or 0)
            ):
                return {
                    "ok": False,
                    "reason": (
                        "no observable UI change during the demo — "
                        "use Focus here on Chrome, then run the step again"
                    ),
                    "expected": expected,
                    "actual": post_title,
                    "cost": "free",
                }
        actual = _foreground_for_success_check(expected)
        ok = bool(expected) and _foreground_title_matches(step, expected, actual)
        if ok:
            if step_profile_varies(step) and is_linkedin_chrome_title(actual):
                return {
                    "ok": True,
                    "reason": f"LinkedIn profile open in Chrome ({actual!r})",
                    "cost": "free",
                }
            return {"ok": True, "reason": f"foreground title is {actual!r}", "cost": "free"}
        from app_ui_guard import is_own_window
        from ui_runner import foreground_title

        raw_fg = foreground_title()
        hint = ""
        if is_own_window(raw_fg):
            hint = " The review UI was in front — click Chrome before demo, or keep the teaching page in a separate window."
        if step_profile_varies(step) and is_linkedin_chrome_title(expected):
            want = "a LinkedIn profile in Google Chrome"
        else:
            want = expected
        return {
            "ok": False,
            "reason": f"expected the window title to become {want!r}; it is still {actual!r}.{hint}",
            "expected": expected,
            "actual": actual,
            "cost": "free",
        }
    if ctype == "window_appeared":
        expected = stored.get("expected") or ""
        actual = _foreground_for_success_check(expected)
        titles = set(_top_level_window_titles())
        ok = expected in titles or _titles_match(expected, actual)
        if ok:
            return {"ok": True, "reason": f"window {expected!r} is present", "cost": "free"}
        return {
            "ok": False,
            "reason": f"expected window {expected!r} to appear; foreground is {actual!r}.",
            "cost": "free",
        }
    if ctype == "a11y_present":
        name = stored.get("name")
        ctype_w = stored.get("control_type")
        present = any(
            (e.get("name") or "") == (name or "")
            and (not ctype_w or (e.get("control_type") or "") == ctype_w)
            for e in _collect_a11y_elements()
        )
        if present:
            return {"ok": True, "reason": f"{ctype_w or 'element'} {name!r} is present", "cost": "free"}
        return {"ok": False, "reason": f"expected {name!r} in the tree; it is not present.", "cost": "free"}
    if ctype == "browser_url":
        pt = (step.anchors[0] or {}).get("point") if step.anchors else None
        dom = snapshot_structural_state(pt[0], pt[1]) if pt else snapshot_structural_state()
        actual = dom.get("browser_url") or ""
        expected = stored.get("expected") or ""
        ok = actual == expected
        if ok:
            return {"ok": True, "reason": f"URL is {actual!r}", "cost": "free"}
        return {
            "ok": False,
            "reason": f"expected URL {expected!r}; got {actual!r}.",
            "cost": "free",
        }
    if ctype == "vision_panel":
        crop_rel = stored.get("crop_path")
        wf_name = wf_name or getattr(step, "_wf_name", "")
        crop_abs = _abs_frame_path(wf_name, crop_rel) if wf_name else crop_rel
        if not crop_abs or not os.path.isfile(crop_abs):
            return {"ok": None, "reason": "vision check crop missing", "cost": "vision"}
        vis = _vision_panel_appeared(crop_abs)
        if vis.get("appeared"):
            return {"ok": True, "reason": vis.get("what") or "panel appeared", "cost": "vision"}
        return {"ok": False, "reason": "expected a new panel; vision did not see one.", "cost": "vision"}

    before = getattr(step, "before_state", None) or {}
    current = snapshot_structural_state()
    for sig in _tier1_signals(before, current):
        if sig.get("detail") == check or check in (sig.get("detail") or ""):
            return {"ok": True, "reason": sig.get("detail"), "cost": "free"}
    return {"ok": None, "reason": f"could not verify: {check}", "cost": "free"}
