from __future__ import annotations

from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy, maybe_dry_run


class PasteIn(BaseModel):
    into_send_field: bool = False


class PasteOut(BaseModel):
    ok: bool
    dry_run: bool = False
    detail: str = ""


SPEC = ActionSpec(
    name="paste",
    input_schema=PasteIn,
    output_schema=PasteOut,
    is_irreversible=False,  # elevated when into_send_field
    default_retry=RetryPolicy(max_attempts=1),
)


def run(inp: PasteIn) -> PasteOut:
    if inp.into_send_field:
        class _Tmp:
            name = "paste"
            is_irreversible = True

        sim = maybe_dry_run(_Tmp(), "paste into send field")  # type: ignore[arg-type]
        if sim is not None:
            return PasteOut(**sim)

    try:
        import pyautogui

        pyautogui.hotkey("ctrl", "v")
        return PasteOut(ok=True, detail="sent Ctrl+V")
    except Exception as e:
        return PasteOut(ok=False, detail=str(e))


SPEC.handler = run  # type: ignore[assignment]
