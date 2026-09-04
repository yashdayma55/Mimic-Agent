"""Exclude MimicAgent's own UI from capture, observation, and intent inference."""

from __future__ import annotations

import re

# Titles / chrome belonging to this app — never sample for watch, show, or vision.
_OWN_WINDOW_PATTERNS = (
    r"mimic\s*agent",
    r"mimicagent",
    r"127\.0\.0\.1",
    r"localhost:\d+",
    r"^show me$",
    r"review\s*ui",
)

# Placeholder / hint text from review_ui.html — not user intent.
_UI_PLACEHOLDERS = (
    "e.g. write the cold email in this voice: short, specific, no fluff",
    "click the text editor in notepad",
    "nothing, or e.g. {filename}",
    "click on the extensions tab",
    "i keep a daily log. every day i open notepad",
    "a linkedin profile page",
    "the person / profile keeps changing",
    '{"filename":"c:\\\\tmp\\\\a.txt"}',
    "type your answer here",
)

# Dashboard labels read via a11y — not user content.
_UI_CHROME_LABELS = (
    "what does this step do?",
    "what changes each run?",
    "how many clicks is this step?",
    "your notes for this step",
    "add a screenshot",
    "watch me (seconds)",
    "what i learned watching you",
    "what i understood",
    "in plain words",
    "success when",
    "may look this up on the web when running",
    "mimicagent",
    "show me",
    "watch me",
    "send answers",
    "ask me what you need",
)

SKIP_LOG: list[str] = []


def clear_skip_log() -> None:
    SKIP_LOG.clear()


def _log_skip(msg: str) -> None:
    SKIP_LOG.append(msg)


def is_own_window(title: str | None) -> bool:
    t = (title or "").strip().lower()
    if not t:
        return False
    for pat in _OWN_WINDOW_PATTERNS:
        if re.search(pat, t, re.I):
            return True
    return False


def is_placeholder_text(text: str | None) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if low.startswith("e.g.") or low.startswith("eg."):
        return True
    for ph in _UI_PLACEHOLDERS:
        if low == ph or ph in low:
            return True
    for label in _UI_CHROME_LABELS:
        if low == label or low.startswith(label):
            return True
    return False


def is_own_ui_name(name: str | None) -> bool:
    if is_placeholder_text(name):
        return True
    low = (name or "").strip().lower()
    if is_own_window(low):
        return True
    return False


def window_title_at_point(x: int, y: int) -> str:
    try:
        from pywinauto import Desktop

        el = Desktop(backend="uia").from_point(int(x), int(y))
        win = el.top_level_parent()
        return (win.window_text() or "").strip()
    except Exception:
        return ""


def point_on_own_ui(x: int, y: int) -> bool:
    title = window_title_at_point(x, y)
    if is_own_window(title):
        _log_skip(f"skipped own UI window {title!r} at ({x},{y})")
        return True
    return False


def filter_sample(sample: dict) -> dict | None:
    """Return sample if it is from a real app; None if own UI or placeholder."""
    window = (sample.get("window") or "").strip()
    if is_own_window(window):
        _log_skip(f"skipped own UI foreground window {window!r}")
        return None
    x, y = sample.get("point") or [0, 0]
    if point_on_own_ui(int(x), int(y)):
        return None
    name = (sample.get("name") or "").strip()
    if name and is_own_ui_name(name):
        _log_skip(f"skipped own UI element name {name!r}")
        return None
    return sample


def vision_is_own_ui(account: str | None) -> bool:
    if not account:
        return False
    low = account.lower()
    if is_own_window(low):
        return True
    for label in _UI_CHROME_LABELS:
        if label in low:
            return True
    for ph in _UI_PLACEHOLDERS:
        if ph in low:
            return True
    if "what does this step do" in low:
        return True
    if "mimicagent" in low.replace(" ", ""):
        return True
    return False


def memory_note_for_summary(note: str | None) -> str:
    """Only real user notes — never placeholder."""
    text = (note or "").strip()
    if not text or is_placeholder_text(text):
        return "There are no notes for this step."
    return text
