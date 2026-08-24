from __future__ import annotations

from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy


class ScrollToIn(BaseModel):
    x: int
    y: int
    clicks: int = -3  # negative = down


class ScrollToOut(BaseModel):
    ok: bool
    detail: str = ""


SPEC = ActionSpec(
    name="scroll_to",
    input_schema=ScrollToIn,
    output_schema=ScrollToOut,
    is_irreversible=False,
    default_retry=RetryPolicy(max_attempts=1),
)


def run(inp: ScrollToIn) -> ScrollToOut:
    try:
        import pyautogui

        pyautogui.moveTo(inp.x, inp.y)
        pyautogui.scroll(inp.clicks)
        return ScrollToOut(ok=True, detail=f"scrolled {inp.clicks} at ({inp.x},{inp.y})")
    except Exception as e:
        return ScrollToOut(ok=False, detail=str(e))


SPEC.handler = run  # type: ignore[assignment]
