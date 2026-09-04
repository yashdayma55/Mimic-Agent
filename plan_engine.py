"""Execute a plan only after the validator passes. LLM never calls this directly."""

from __future__ import annotations

from plan_schema import Plan, plan_from_dict
from plan_validator import validate_plan
from ui_runner import execute_step, run_verified_plan


def execute_validated_plan(plan, *, halt_on_fail: bool = True) -> dict:
    plan = plan_from_dict(plan)
    violations = validate_plan(plan)
    if violations:
        return {
            "ok": False,
            "executed": False,
            "violations": list(violations),
            "reason": violations[0]["message"],
            "results": [],
        }
    steps = [n.to_runner_step() for n in plan.nodes if n.action not in ("done", "stuck")]
    for n, s in zip(
        [n for n in plan.nodes if n.action not in ("done", "stuck")],
        steps,
    ):
        if n.action == "prompt":
            s.setdefault("produces", list(n.produces or []))
    out = run_verified_plan(steps, halt_on_fail=halt_on_fail)
    out["executed"] = True
    out["violations"] = []
    return out
