"""Single place OS mouse/keyboard is sent. Tests spy on CALLS.

Nothing here is considered success. Callers must verify a side effect.
"""

from __future__ import annotations

import time

CALLS: list[dict] = []


def reset_calls() -> None:
    CALLS.clear()


def call_count() -> int:
    return len(CALLS)


def _record(kind: str, **kwargs) -> None:
    CALLS.append({"kind": kind, **kwargs})


def click(x: int, y: int, button: str = "left") -> None:
    _record("click", x=int(x), y=int(y), button=button)
    import pyautogui

    pyautogui.FAILSAFE = False
    pyautogui.click(int(x), int(y), button=button)


def type_text(text: str, replace: bool = False) -> None:
    _record("type", text=text, replace=bool(replace))
    from pywinauto.keyboard import send_keys

    special = set("^+%~(){}[]")
    escaped = "".join("{" + ch + "}" if ch in special else ch for ch in text)
    if replace:
        send_keys("^a")
        time.sleep(0.05)
    send_keys(escaped, with_spaces=True)


def hotkey(combo: str) -> None:
    """combo like 'ctrl+s' or '^s'."""
    _record("hotkey", combo=combo)
    from pywinauto.keyboard import send_keys

    raw = (combo or "").strip().lower()
    mapping = {
        "ctrl+s": "^s",
        "ctrl+a": "^a",
        "ctrl+c": "^c",
        "ctrl+v": "^v",
        "ctrl+n": "^n",
        "alt+f4": "%{F4}",
        "enter": "{ENTER}",
        "esc": "{ESC}",
        "tab": "{TAB}",
    }
    keys = mapping.get(raw, combo)
    send_keys(keys)


def move_to(x: int, y: int) -> None:
    _record("move", x=int(x), y=int(y))
    import pyautogui

    pyautogui.FAILSAFE = False
    pyautogui.moveTo(int(x), int(y))


def scroll_at(x: int, y: int, clicks: int) -> None:
    _record("scroll", x=int(x), y=int(y), clicks=int(clicks))
    import pyautogui

    pyautogui.FAILSAFE = False
    pyautogui.scroll(int(clicks), x=int(x), y=int(y))
