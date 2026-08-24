from __future__ import annotations

import subprocess
from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy


class NavigateIn(BaseModel):
    url: str
    browser_exe: str | None = None


class NavigateOut(BaseModel):
    ok: bool
    detail: str = ""


SPEC = ActionSpec(
    name="navigate",
    input_schema=NavigateIn,
    output_schema=NavigateOut,
    is_irreversible=False,
    default_retry=RetryPolicy(max_attempts=1),
)


def run(inp: NavigateIn) -> NavigateOut:
    import os

    exe = inp.browser_exe
    if not exe:
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        exe = next((p for p in candidates if os.path.exists(p)), None)
    if not exe:
        return NavigateOut(ok=False, detail="chrome.exe not found")
    try:
        subprocess.Popen([exe, inp.url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return NavigateOut(ok=True, detail=f"launched {exe} -> {inp.url}")
    except Exception as e:
        return NavigateOut(ok=False, detail=str(e))


SPEC.handler = run  # type: ignore[assignment]
