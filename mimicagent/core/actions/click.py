from __future__ import annotations

from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy, maybe_dry_run


class ClickIn(BaseModel):
    x: int
    y: int
    button: str = "left"
    is_submit: bool = False  # form submit / Send → irreversible


class ClickOut(BaseModel):
    ok: bool
    dry_run: bool = False
    detail: str = ""


SPEC = ActionSpec(
    name="click",
    input_schema=ClickIn,
    output_schema=ClickOut,
    is_irreversible=False,  # elevated at runtime when is_submit=True
    default_retry=RetryPolicy(max_attempts=2),
)


def run(inp: ClickIn) -> ClickOut:
    if inp.is_submit:
        # Treat submit clicks as irreversible for dry-run gating.
        class _Tmp:
            name = "click"
            is_irreversible = True

        sim = maybe_dry_run(_Tmp(), f"click submit at ({inp.x},{inp.y})")  # type: ignore[arg-type]
        if sim is not None:
            return ClickOut(**sim)

    try:
        import pyautogui

        pyautogui.click(inp.x, inp.y, button=inp.button)
        return ClickOut(ok=True, detail=f"clicked ({inp.x},{inp.y})")
    except Exception as e:
        return ClickOut(ok=False, detail=str(e))


SPEC.handler = run  # type: ignore[assignment]
