"""Timed watch: a11y + focused-monitor screenshots stay awake, then we write what we learned."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from show_capture import (
    _element_at,
    _monitor_at,
    _save_context,
    cursor_point,
)
from teaching import get_step, load_taught, save_taught
from workflow_folder import workflow_dir


def _foreground_title() -> str:
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
        return (buf.value or "").strip()
    except Exception:
        return ""


def _sample_once() -> dict:
    x, y = cursor_point()
    a11y = _element_at(x, y)
    mon = _monitor_at(x, y)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "point": [x, y],
        "monitor": list(mon) if mon else None,
        "window": _foreground_title(),
        "name": a11y.get("name"),
        "control_type": a11y.get("control_type"),
        "rect": a11y.get("rect"),
    }


def _summarize(samples: list, memory: str = "") -> dict:
    names = []
    windows = []
    types = []
    for s in samples:
        n = (s.get("name") or "").strip()
        if n and n not in names:
            names.append(n)
        w = (s.get("window") or "").strip()
        if w and w not in windows:
            windows.append(w)
        t = (s.get("control_type") or "").strip()
        if t and t not in types:
            types.append(t)
    if names:
        target = names[-1]
    elif windows:
        target = windows[-1]
    else:
        target = "the screen the user was showing"
    bits = [
        f"I watched for {len(samples)} sample{'s' if len(samples) != 1 else ''}.",
    ]
    if windows:
        bits.append("The front window was " + ", then ".join(repr(w) for w in windows[:4]) + ".")
    if names:
        bits.append("You lingered on: " + ", ".join(repr(n) for n in names[:6]) + ".")
    bits.append(f"I think this step is about {target!r}.")
    if memory:
        bits.append("I will also follow your note: " + memory.strip()[:180])
    summary = " ".join(bits)
    return {
        "summary": summary,
        "target": target,
        "windows": windows[:6],
        "names": names[:8],
        "control_types": types[:6],
        "samples": len(samples),
    }


def _maybe_vision(image_path: str) -> str:
    if not image_path or not os.path.isfile(image_path):
        return ""
    try:
        key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "my_key.txt")
        if not os.path.isfile(key_path):
            return ""
        key = open(key_path, encoding="utf-8").read().strip()
        if not key:
            return ""
        from vision_api import ask_vision_api

        with open(image_path, "rb") as f:
            raw = f.read()
        out = ask_vision_api(raw, key)
        return (out.get("what_you_see") or "") if isinstance(out, dict) else ""
    except Exception:
        return ""


def watch_step(name: str, step_id: str, seconds: float = 15, interval: float = 1.0) -> dict:
    """Keep a11y + the focused monitor awake for `seconds`, then store what we learned."""
    wf = load_taught(name)
    step = get_step(wf, step_id)
    seconds = max(0.0, min(float(seconds), 60.0))
    interval = max(0.2, float(interval))
    deadline = time.time() + seconds
    samples = []
    last_shot = None
    while True:
        sample = _sample_once()
        samples.append(sample)
        x, y = sample["point"]
        dest = os.path.join(workflow_dir(wf.name), "anchors", f"{step.id}_watch.png")
        try:
            last_shot = _save_context(x, y, dest, element_rect=sample.get("rect"), focus=False)
        except Exception:
            last_shot = last_shot
        if time.time() >= deadline:
            break
        remain = deadline - time.time()
        time.sleep(min(interval, max(0.0, remain)))
        if seconds == 0:
            break
    learned = _summarize(samples, memory=getattr(step, "memory_note", "") or "")
    vision = _maybe_vision(last_shot)
    if vision:
        learned["vision"] = vision
        learned["summary"] = learned["summary"] + " Vision saw: " + vision[:180]
    learned["ts"] = datetime.now(timezone.utc).isoformat()
    learned["seconds"] = seconds
    step.learned = learned
    if last_shot:
        rel = os.path.join("anchors", f"{step.id}_watch.png")
        learned["shot"] = rel
        if not step.anchor:
            step.anchor = {}
        step.anchor["context_path"] = step.anchor.get("context_path") or rel
        step.anchor["preview_path"] = rel
    step.qa_history.append({
        "q": "Watch me",
        "a": learned["summary"],
        "source": "observe",
        "ts": learned["ts"],
    })
    save_taught(wf)
    return {"ok": True, "learned": learned, "step": step.to_dict()}
