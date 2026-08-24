from __future__ import annotations

from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy, maybe_dry_run
from mimicagent.core.capture import focus_app as _focus_app


class FocusAppIn(BaseModel):
    proc_names: list[str]
    title_hint: str | None = None


class FocusAppOut(BaseModel):
    ok: bool
    dry_run: bool = False
    detail: str = ""


SPEC = ActionSpec(
    name="focus_app",
    input_schema=FocusAppIn,
    output_schema=FocusAppOut,
    is_irreversible=False,
    default_retry=RetryPolicy(max_attempts=2),
)


def run(inp: FocusAppIn) -> FocusAppOut:
    title = _focus_app(inp.proc_names, title_hint=inp.title_hint)
    return FocusAppOut(
        ok=bool(title),
        detail=title or "no matching window",
    )


SPEC.handler = run  # type: ignore[assignment]
