"""Shared action types: ActionSpec, RetryPolicy, dry-run helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel


@dataclass
class RetryPolicy:
    max_attempts: int = 2
    backoff_sec: float = 0.4


@dataclass
class ActionSpec:
    name: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    is_irreversible: bool
    default_retry: RetryPolicy
    handler: Callable[[BaseModel], BaseModel] | None = field(default=None, repr=False)


def maybe_dry_run(spec: ActionSpec, detail: str) -> dict[str, Any] | None:
    """If DRY_RUN and irreversible, log and return a simulated-success payload."""
    from mimicagent.core import config

    if not spec.is_irreversible:
        return None
    if config.DRY_RUN or not config.LIVE_MODE:
        print(f"  [dry-run] would execute irreversible action {spec.name!r}: {detail}")
        return {"ok": True, "dry_run": True, "detail": detail}
    return None
