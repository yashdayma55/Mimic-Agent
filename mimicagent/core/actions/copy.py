from __future__ import annotations

from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy


class CopyIn(BaseModel):
    text: str | None = None  # if set, put on clipboard; else Ctrl+C


class CopyOut(BaseModel):
    ok: bool
    detail: str = ""


SPEC = ActionSpec(
    name="copy",
    input_schema=CopyIn,
    output_schema=CopyOut,
    is_irreversible=False,
    default_retry=RetryPolicy(max_attempts=1),
)


def run(inp: CopyIn) -> CopyOut:
    try:
        if inp.text is not None:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(inp.text)
            root.update()
            root.destroy()
            return CopyOut(ok=True, detail=f"clipboard set ({len(inp.text)} chars)")
        import pyautogui

        pyautogui.hotkey("ctrl", "c")
        return CopyOut(ok=True, detail="sent Ctrl+C")
    except Exception as e:
        return CopyOut(ok=False, detail=str(e))


SPEC.handler = run  # type: ignore[assignment]
