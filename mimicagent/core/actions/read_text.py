from __future__ import annotations

from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy


class ReadTextIn(BaseModel):
    automation_id: str | None = None
    name: str | None = None


class ReadTextOut(BaseModel):
    ok: bool
    text: str = ""
    detail: str = ""


SPEC = ActionSpec(
    name="read_text",
    input_schema=ReadTextIn,
    output_schema=ReadTextOut,
    is_irreversible=False,
    default_retry=RetryPolicy(max_attempts=2),
)


def run(inp: ReadTextIn) -> ReadTextOut:
    import uiautomation as auto

    try:
        ctrl = None
        if inp.automation_id:
            ctrl = auto.Control(searchDepth=8, AutomationId=inp.automation_id)
        elif inp.name:
            ctrl = auto.Control(searchDepth=8, Name=inp.name)
        if ctrl is None or not ctrl.Exists(0, 0):
            return ReadTextOut(ok=False, detail="control not found")
        text = ctrl.Name or getattr(ctrl, "ValuePattern", lambda: None)() or ""
        try:
            vp = ctrl.GetValuePattern()
            if vp:
                text = vp.Value or text
        except Exception:
            pass
        return ReadTextOut(ok=True, text=str(text), detail="read")
    except Exception as e:
        return ReadTextOut(ok=False, detail=str(e))


SPEC.handler = run  # type: ignore[assignment]
