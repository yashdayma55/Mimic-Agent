"""Capture listener — dwells and scroll events during teaching."""

from __future__ import annotations

import math
import time
from typing import Callable

from hover_actions import DWELL_MS, DWELL_RADIUS_PX, analyze_dwell

_last_pos: tuple[int, int] | None = None
_dwell_start: float | None = None
_dwell_origin: tuple[int, int] | None = None
_scroll_events: list[dict] = []
_listening = False


def reset_listener_state() -> None:
    global _last_pos, _dwell_start, _dwell_origin, _scroll_events, _listening
    _last_pos = None
    _dwell_start = None
    _dwell_origin = None
    _scroll_events = []
    _listening = False


def _dist(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def poll_dwell(cursor_fn: Callable[[], tuple[int, int]], now: float | None = None) -> dict | None:
    """Call each poll tick; returns revealing hover when dwell completes."""
    global _last_pos, _dwell_start, _dwell_origin
    now = now if now is not None else time.time()
    pos = cursor_fn()
    if _last_pos is None:
        _last_pos = pos
        _dwell_start = now
        _dwell_origin = pos
        return None
    if _dist(pos, _dwell_origin or pos) <= DWELL_RADIUS_PX:
        if _dwell_start and (now - _dwell_start) * 1000 >= DWELL_MS:
            origin = _dwell_origin or pos
            _dwell_start = None
            _dwell_origin = None
            _last_pos = pos
            return analyze_dwell(origin[0], origin[1])
    else:
        _dwell_origin = pos
        _dwell_start = now
    _last_pos = pos
    return None


def record_scroll_event(*, within: str | None = None, to_find: str | None = None) -> None:
    _scroll_events.append({
        "at": time.time(),
        "within": within,
        "to_find_hint": to_find,
    })


def consume_scroll_events() -> list[dict]:
    global _scroll_events
    out = list(_scroll_events)
    _scroll_events = []
    return out


def synthesise_dwell_reveal(x: int, y: int, revealed: list[dict]) -> dict:
    """Test helper — fake a revealing dwell without waiting."""
    return {
        "action": "hover",
        "point": [int(x), int(y)],
        "revealed": list(revealed),
        "synthetic": True,
    }
