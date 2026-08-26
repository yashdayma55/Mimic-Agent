"""Compile approved taught steps into the existing plan format. Executor unchanged."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from plan_schema import Plan, PlanNode
from plan_validator import validate_plan
from teaching import TaughtStep, TaughtWorkflow, TeachingError, get_step, save_taught


def _bind(text, inputs: dict | None):
    if not text or not inputs:
        return text
    out = str(text)
    for k, v in inputs.items():
        key = str(k).strip("{}")
        out = out.replace("{" + key + "}", str(v))
        out = out.replace(str(k), str(v))
    return out


def step_to_node(step: TaughtStep, inputs: dict | None = None) -> PlanNode:
    action = step.action or {}
    kind = action.get("action") or "wait"
    value = action.get("value") or action.get("text") or action.get("keys")
    value = _bind(value, inputs)
    extra = {}
    if action.get("keys"):
        extra["keys"] = _bind(action.get("keys"), inputs)
    if action.get("type_mode"):
        extra["type_mode"] = action.get("type_mode")
    if action.get("verify_file"):
        extra["verify_file"] = _bind(action.get("verify_file"), inputs)
        extra["verify_contains"] = action.get("verify_contains")
    extra["from_taught"] = step.id
    if step.anchor:
        extra["anchor"] = step.anchor
    if getattr(step, "memory_note", ""):
        extra["memory_note"] = step.memory_note
    extra["web_allowed"] = bool(getattr(step, "web_allowed", False))
    return PlanNode(
        id=step.id,
        action=kind,
        target_desc=action.get("target_desc"),
        target_ref=action.get("elem_name"),
        value=value,
        produces=list(step.produces or []),
        consumes=list(step.consumes or []),
        window_title=action.get("window_title"),
        elem_name=action.get("elem_name"),
        elem_type=action.get("elem_type"),
        extra=extra,
    )


def compile_taught(wf: TaughtWorkflow, inputs: dict | None = None) -> dict:
    unapproved = [s.id for s in wf.steps if s.status != "approved"]
    if unapproved:
        raise TeachingError(f"cannot compile unapproved steps: {unapproved}")
    nodes = [step_to_node(s, inputs) for s in sorted(wf.steps, key=lambda x: x.order)]
    plan = Plan(nodes=nodes, source=f"taught:{wf.name}")
    viol = validate_plan(plan)
    extra_plan = {}
    if wf.start_screen:
        extra_plan["start_screen"] = {
            "summary": (wf.start_screen or {}).get("summary"),
            "parameters": (wf.start_screen or {}).get("parameters") or [],
            "shown": bool((wf.start_screen or {}).get("shown")),
        }
        if plan.nodes:
            plan.nodes[0].extra["start_screen"] = extra_plan["start_screen"]
    if viol:
        return {"ok": False, "plan": plan.to_dict(), "violations": viol, **extra_plan}
    return {"ok": True, "plan": plan.to_dict(), "violations": [], **extra_plan}


def rehearse_taught_step(wf: TaughtWorkflow, step_id: str, test_values: dict | None = None) -> dict:
    import os_input
    from ui_runner import run_verified_plan

    step = get_step(wf, step_id)
    if not step.action:
        from teach_loop import resolve_action

        step.action = resolve_action(step)
        if not step.action:
            raise TeachingError("cannot rehearse until the action is resolved")
    inputs = dict(test_values or {})
    node = step_to_node(step, inputs)
    viol = validate_plan(Plan(nodes=[node], source="rehearse"))
    if viol:
        result = {
            "ok": False,
            "reason": viol[0]["message"],
            "observed": None,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        step.rehearsal = result
        save_taught(wf)
        return result
    before = os_input.call_count()
    out = run_verified_plan([node.to_runner_step()], halt_on_fail=True)
    ok = bool(out.get("ok"))
    reason = out.get("reason") or (out.get("results") or [{}])[-1].reason if out.get("results") else "rehearse"
    if out.get("results"):
        reason = out["results"][-1].reason
    result = {
        "ok": ok,
        "reason": reason,
        "observed": out.get("results")[-1].value_after if out.get("results") else None,
        "os_input_calls": os_input.call_count() - before,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    step.rehearsal = result
    save_taught(wf)
    return result


def prepare_state(wf: TaughtWorkflow, step_id: str, mode: str, test_values: dict | None = None) -> dict:
    """Reach the state before this step. manual = user sets up; replay = run 1..n-1."""
    step = get_step(wf, step_id)
    mode = (mode or "manual").strip().lower()
    if mode == "manual":
        return {
            "ok": True,
            "mode": "manual",
            "message": "Set the screen to where it should be before this step, then click Ready.",
            "ran": [],
        }
    if mode != "replay":
        raise TeachingError(f"unknown prepare mode {mode!r}")
    prior = [s for s in sorted(wf.steps, key=lambda x: x.order) if s.order < step.order]
    for s in prior:
        if s.status != "approved":
            return {
                "ok": False,
                "mode": "replay",
                "failed_step": s.id,
                "reason": f"cannot replay {s.id}: status is {s.status}, not approved",
            }
    if not prior:
        return {"ok": True, "mode": "replay", "ran": []}
    import os_input
    from ui_runner import run_verified_plan

    nodes = [step_to_node(s, test_values) for s in prior]
    viol = validate_plan(Plan(nodes=nodes, source="prepare"))
    if viol:
        return {"ok": False, "mode": "replay", "failed_step": prior[0].id, "reason": viol[0]["message"]}
    before = os_input.call_count()
    out = run_verified_plan([n.to_runner_step() for n in nodes], halt_on_fail=True)
    if not out.get("ok"):
        idx = out.get("halted_index") or 0
        failed = prior[min(idx, len(prior) - 1)]
        return {
            "ok": False,
            "mode": "replay",
            "failed_step": failed.id,
            "reason": out.get("reason") or (out.get("results") or [None])[-1].reason if out.get("results") else "replay failed",
            "os_input_calls": os_input.call_count() - before,
        }
    return {
        "ok": True,
        "mode": "replay",
        "ran": [s.id for s in prior],
        "os_input_calls": os_input.call_count() - before,
    }


def demo_taught_step(wf: TaughtWorkflow, step_id: str, test_values: dict | None = None, mode: str = "manual") -> dict:
    """Run only this step. Status becomes demonstrated only on observed success."""
    import os_input
    from ui_runner import run_verified_plan
    from teach_loop import resolve_action

    step = get_step(wf, step_id)
    if not step.action:
        step.action = resolve_action(step)
    if not step.action:
        raise TeachingError("cannot demo until the action is resolved")
    node = step_to_node(step, test_values)
    viol = validate_plan(Plan(nodes=[node], source="demo"))
    if viol:
        result = {
            "ok": False,
            "reason": viol[0]["message"],
            "observed": None,
            "os_input_calls": 0,
            "mode": mode,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        step.demo = result
        save_taught(wf)
        return result
    before = os_input.call_count()
    out = run_verified_plan([node.to_runner_step()], halt_on_fail=True)
    ok = bool(out.get("ok"))
    reason = out.get("reason")
    if out.get("results"):
        reason = out["results"][-1].reason
        observed = out["results"][-1].value_after
    else:
        observed = None
    result = {
        "ok": ok,
        "reason": reason,
        "observed": observed,
        "os_input_calls": os_input.call_count() - before,
        "mode": mode,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    step.demo = result
    if ok:
        step.status = "demonstrated"
    save_taught(wf)
    return result


def run_taught(wf: TaughtWorkflow, inputs: dict | None = None) -> dict:
    compiled = compile_taught(wf, inputs)
    if not compiled.get("ok"):
        return {"ok": False, "executed": False, "violations": compiled["violations"]}
    from plan_engine import execute_validated_plan

    return execute_validated_plan(compiled["plan"])
