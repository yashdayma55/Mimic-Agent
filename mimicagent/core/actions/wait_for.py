from __future__ import annotations

import time
from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy


class WaitForIn(BaseModel):
    seconds: float = 1.0
    # Optional: wait until an automation_id appears
    automation_id: str | None = None
    timeout_sec: float = 10.0


class WaitForOut(BaseModel):
    ok: bool
    detail: str = ""


SPEC = ActionSpec(
    name="wait_for",
    input_schema=WaitForIn,
    output_schema=WaitForOut,
    is_irreversible=False,
    default_retry=RetryPolicy(max_attempts=1),
)


def run(inp: WaitForIn) -> WaitForOut:
    if not inp.automation_id:
        time.sleep(max(0.0, inp.seconds))
        return WaitForOut(ok=True, detail=f"slept {inp.seconds}s")

    import uiautomation as auto

    deadline = time.time() + inp.timeout_sec
    while time.time() < deadline:
        try:
            ctrl = auto.Control(searchDepth=10, AutomationId=inp.automation_id)
            if ctrl.Exists(0, 0):
                return WaitForOut(ok=True, detail=f"found {inp.automation_id!r}")
        except Exception:
            pass
        time.sleep(0.3)
    return WaitForOut(ok=False, detail=f"timeout waiting for {inp.automation_id!r}")


SPEC.handler = run  # type: ignore[assignment]
