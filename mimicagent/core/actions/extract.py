from __future__ import annotations

import re
from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy


class ExtractIn(BaseModel):
    text: str
    pattern: str  # regex with optional named group `value`


class ExtractOut(BaseModel):
    ok: bool
    value: str = ""
    detail: str = ""


SPEC = ActionSpec(
    name="extract",
    input_schema=ExtractIn,
    output_schema=ExtractOut,
    is_irreversible=False,
    default_retry=RetryPolicy(max_attempts=1),
)


def run(inp: ExtractIn) -> ExtractOut:
    try:
        m = re.search(inp.pattern, inp.text, re.I | re.M)
        if not m:
            return ExtractOut(ok=False, detail="no match")
        if "value" in m.groupdict():
            return ExtractOut(ok=True, value=m.group("value") or "", detail="named group")
        return ExtractOut(ok=True, value=m.group(0), detail="full match")
    except re.error as e:
        return ExtractOut(ok=False, detail=f"bad pattern: {e}")


SPEC.handler = run  # type: ignore[assignment]
