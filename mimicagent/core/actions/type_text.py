from __future__ import annotations

from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy, maybe_dry_run


class TypeTextIn(BaseModel):
    text: str
    into_send_field: bool = False
    use_clipboard: bool = True


class TypeTextOut(BaseModel):
    ok: bool
    dry_run: bool = False
    detail: str = ""


# Typing into a send field (or any text that can fire a message) is irreversible.
SPEC = ActionSpec(
    name="type_text",
    input_schema=TypeTextIn,
    output_schema=TypeTextOut,
    is_irreversible=True,
    default_retry=RetryPolicy(max_attempts=1),
)


def run(inp: TypeTextIn) -> TypeTextOut:
    preview = (inp.text[:80] + "…") if len(inp.text) > 80 else inp.text
    sim = maybe_dry_run(SPEC, f"type_text into_send={inp.into_send_field} text={preview!r}")
    if sim is not None:
        return TypeTextOut(**sim)

    try:
        import pyautogui

        if inp.use_clipboard:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(inp.text)
            root.update()
            root.destroy()
            pyautogui.hotkey("ctrl", "v")
        else:
            pyautogui.write(inp.text, interval=0.02)
        return TypeTextOut(ok=True, detail=f"typed {len(inp.text)} chars")
    except Exception as e:
        return TypeTextOut(ok=False, detail=str(e))


SPEC.handler = run  # type: ignore[assignment]
