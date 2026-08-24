from __future__ import annotations

from pydantic import BaseModel

from mimicagent.core.actions.base import ActionSpec, RetryPolicy


class HumanApproveIn(BaseModel):
    prompt: str
    require_exact_yes: bool = True


class HumanApproveOut(BaseModel):
    ok: bool
    approved: bool = False
    detail: str = ""


SPEC = ActionSpec(
    name="human_approve",
    input_schema=HumanApproveIn,
    output_schema=HumanApproveOut,
    is_irreversible=True,
    default_retry=RetryPolicy(max_attempts=1),
)


def run(inp: HumanApproveIn) -> HumanApproveOut:
    print("==============================================")
    print(" HUMAN APPROVAL REQUIRED")
    print(f" {inp.prompt}")
    if inp.require_exact_yes:
        print(" Type 'yes' to approve, anything else to deny: ", end="", flush=True)
    else:
        print(" Approve? (y/n): ", end="", flush=True)
    try:
        answer = input().strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return HumanApproveOut(ok=True, approved=False, detail="interrupted")

    if inp.require_exact_yes:
        approved = answer == "yes"
    else:
        approved = answer.lower() in ("y", "yes")
    return HumanApproveOut(
        ok=True,
        approved=approved,
        detail="approved" if approved else "denied",
    )


SPEC.handler = run  # type: ignore[assignment]
