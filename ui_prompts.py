"""Thread-local human prompts for UI-started runs.

CLI (no bridge): uses input().
UI runs: set_ui_bridge(...) so ask_human parks the run and waits on a Queue.
"""

from __future__ import annotations

import threading
from queue import Queue

_tls = threading.local()


def set_ui_bridge(status: dict, answer_queue: Queue) -> None:
    _tls.bridge = (status, answer_queue)


def clear_ui_bridge() -> None:
    _tls.bridge = None


def ui_bridge_active() -> bool:
    return getattr(_tls, "bridge", None) is not None


def ask_human(kind: str, prompt_text: str) -> str:
    """kind is approval | clarification | tollgate."""
    bridge = getattr(_tls, "bridge", None)
    if not bridge:
        print(prompt_text, end="" if prompt_text.endswith(" ") else "\n", flush=True)
        try:
            return input().strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    status, answer_queue = bridge
    status["awaiting"] = kind
    status["prompt_text"] = prompt_text
    status.setdefault("log", [])
    status["log"].append(f"[prompt:{kind}] {prompt_text}")
    answer = answer_queue.get()
    status["awaiting"] = "none"
    status["prompt_text"] = ""
    status["log"].append(f"[answer:{kind}] {answer!r}")
    return str(answer).strip() if answer is not None else ""
