from __future__ import annotations

import json
import urllib.request
from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy
from mimicagent.core import config


class LlmGenerateIn(BaseModel):
    prompt: str
    max_tokens: int = 400


class LlmGenerateOut(BaseModel):
    ok: bool
    text: str = ""
    detail: str = ""


SPEC = ActionSpec(
    name="llm_generate",
    input_schema=LlmGenerateIn,
    output_schema=LlmGenerateOut,
    is_irreversible=False,
    default_retry=RetryPolicy(max_attempts=1),
)


def _load_api_key() -> str:
    try:
        with open("my_key.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def run(inp: LlmGenerateIn) -> LlmGenerateOut:
    key = _load_api_key()
    if not key or not key.startswith("sk-ant"):
        return LlmGenerateOut(ok=False, detail="no Claude API key (my_key.txt)")
    payload = {
        "model": config.VISION_MODEL,
        "max_tokens": inp.max_tokens,
        "messages": [{"role": "user", "content": inp.prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = data["content"][0]["text"]
        return LlmGenerateOut(ok=True, text=text, detail="ok")
    except Exception as e:
        return LlmGenerateOut(ok=False, detail=str(e))


SPEC.handler = run  # type: ignore[assignment]
