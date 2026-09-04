"""Compile approved taught steps into the existing plan format. Executor unchanged."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from plan_schema import Plan, PlanNode
from plan_validator import _MAX_TARGET_DESC, validate_plan
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


def _chain_clicks_from_step(step: TaughtStep) -> list:
    clicks = []
    for anc in (step.anchors or []):
        if not anc:
            continue
        primary = (anc or {}).get("primary") or {}
        clicks.append({
            "action": "click",
            "elem_name": primary.get("name"),
            "elem_type": primary.get("control_type"),
            "target_desc": primary.get("name") or "target",
        })
        if len(clicks) >= 2:
            break
    return clicks


def _chain_target_desc(clicks: list) -> str:
    labels = []
    for click in clicks[:2]:
        labels.append((click.get("elem_name") or click.get("target_desc") or "target").strip())
    desc = ", then ".join(labels) if len(labels) > 1 else (labels[0] if labels else "chain")
    if len(desc) > _MAX_TARGET_DESC:
        desc = desc[: _MAX_TARGET_DESC - 1].rstrip() + "…"
    return desc


def step_to_node(step: TaughtStep, inputs: dict | None = None) -> PlanNode:
    from prompt_steps import METHOD_PROMPT

    if (getattr(step, "method", "anchor") or "anchor") == METHOD_PROMPT:
        text = (step.prompt_instruction or step.user_description or "").strip()
        return PlanNode(
            id=step.id,
            action="prompt",
            target_desc=text[:80] if text else None,
            value=text,
            produces=list(step.produces or []),
            consumes=list(step.consumes or []),
            extra={
                "from_taught": step.id,
                "method": METHOD_PROMPT,
                "prompt_instruction": text,
                "memory_note": getattr(step, "memory_note", "") or "",
                "produces": list(step.produces or []),
            },
        )
    action = step.action or {}
    kind = action.get("action") or "wait"
    if kind == "chain":
        parts = action.get("parts") or []
        clicks = action.get("clicks") or _chain_clicks_from_step(step)
        extra = {
            "clicks": clicks,
            "anchors": list(step.anchors or []),
            "click_count": int(action.get("click_count") or getattr(step, "click_count", 1) or 1),
            "from_taught": step.id,
        }
        if parts:
            extra["parts"] = parts
            extra["chain_kind"] = action.get("chain_kind") or "interaction"
        if getattr(step, "memory_note", ""):
            extra["memory_note"] = step.memory_note
        extra["web_allowed"] = bool(getattr(step, "web_allowed", False))
        first = (step.anchors or [{}])[0] if step.anchors else {}
        primary = (first or {}).get("primary") or {}
        win_hint = target_window_hint(step)
        if win_hint:
            for click in clicks:
                click["window_title"] = win_hint
        extra["target_window_hint"] = win_hint
        return PlanNode(
            id=step.id,
            action="chain",
            target_desc=_chain_target_desc(clicks),
            elem_name=primary.get("name"),
            elem_type=primary.get("control_type"),
            window_title=win_hint,
            produces=list(step.produces or []),
            consumes=list(step.consumes or []),
            extra=extra,
        )
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
    if getattr(step, "anchors", None):
        extra["anchors"] = step.anchors
    if getattr(step, "click_count", 1):
        extra["click_count"] = int(step.click_count or 1)
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
    from prompt_steps import compile_prompt_step_ok

    unapproved = [s.id for s in wf.steps if s.status != "approved"]
    if unapproved:
        raise TeachingError(f"cannot compile unapproved steps: {unapproved}")
    for s in wf.steps:
        if not compile_prompt_step_ok(s):
            raise TeachingError(
                f"step {s.id} uses prompt method but has no success check — add one before compile"
            )
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


def target_window_hint(step: TaughtStep) -> str | None:
    after = getattr(step, "after_frame", None) or {}
    title = (after.get("window_title") or "").strip()
    if title and title.lower() not in ("extensions",):
        return title
    und = step.understanding or {}
    check = (und.get("success_evidence") or {}).get("check") or {}
    title = (check.get("expected") or "").strip()
    if title:
        return title
    for anc in step.anchors or []:
        ss = (anc or {}).get("structural_state") or {}
        ft = (ss.get("foreground_title") or "").strip()
        if ft and ft.lower() not in ("extensions",):
            return ft
    blob = (step.user_description or "").lower()
    if "linkedin" in blob:
        return "LinkedIn"
    if "notepad" in blob:
        return "Notepad"
    return None


def window_hint_from_step(step: TaughtStep) -> str | None:
    anchors = [a for a in (step.anchors or []) if a]
    if anchors:
        pt = anchors[0].get("point")
        if isinstance(pt, (list, tuple)) and len(pt) == 2:
            from app_ui_guard import is_own_window, window_title_at_point

            title = window_title_at_point(int(pt[0]), int(pt[1]))
            if title and not is_own_window(title) and title.lower() not in ("extensions",):
                return title
    return target_window_hint(step)


def focus_step_target(wf: TaughtWorkflow, step_id: str) -> dict:
    step = get_step(wf, step_id)
    hint = window_hint_from_step(step)
    if not hint:
        return {"ok": False, "reason": "no target window recorded for this step — set up the screen manually"}
    from ui_runner import focus_window_by_hint

    return focus_window_by_hint(hint)


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
    focus_result = None
    before_demo = None
    after_demo = None
    if (mode or "manual").strip().lower() == "manual":
        from success_signals import snapshot_structural_state

        before_demo = snapshot_structural_state()
        focus_result = focus_step_target(wf, step_id)
    from case_match import default_vision_match, plan_step_execution
    from step_cases import list_step_cases

    vision_fn = default_vision_match if list_step_cases(step) else None
    exec_plan = plan_step_execution(
        wf, step, test_values, before_demo=before_demo, vision_fn=vision_fn,
    )
    if exec_plan["action"] == "halt_ambiguous":
        from case_halt_loop import record_case_ambiguity_halt

        halt_info = record_case_ambiguity_halt(
            wf,
            step_id,
            exec_plan.get("candidates") or [],
            structural=exec_plan.get("structural"),
            log=exec_plan.get("log") or "",
        )
        result = {
            "ok": False,
            "reason": "ambiguous case match — halting for user",
            "observed": None,
            "os_input_calls": 0,
            "mode": mode,
            "case_decision": exec_plan.get("log"),
            "case_halt": halt_info,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if focus_result is not None:
            result["focus"] = focus_result
        step.demo = result
        save_taught(wf)
        return result
    before = os_input.call_count()
    matched_case = exec_plan.get("case") if exec_plan["action"] == "case" else None
    if matched_case is not None:
        from step_cases import record_case_match

        record_case_match(matched_case)
        save_taught(wf)
    runner_step = dict(exec_plan["runner_step"] or {})
    runner_step["workflow_name"] = wf.name
    if getattr(step, "produces", None):
        runner_step["produces"] = list(step.produces or [])
    if getattr(step, "prompt_instruction", None):
        runner_step["prompt_instruction"] = step.prompt_instruction
    if getattr(step, "memory_note", None):
        runner_step["memory_note"] = step.memory_note
    plan_runners = list(exec_plan.get("runner_steps") or [runner_step])
    for rs in plan_runners:
        rs.setdefault("workflow_name", wf.name)
        if getattr(step, "produces", None) and rs is plan_runners[-1] and exec_plan.get("action") != "case":
            rs["produces"] = list(step.produces or [])
    out = run_verified_plan(plan_runners, halt_on_fail=True)
    if (
        out.get("ok")
        and exec_plan.get("action") == "case"
        and exec_plan.get("continue_parent")
        and exec_plan.get("parent_runner_step")
    ):
        parent_runner = dict(exec_plan["parent_runner_step"])
        parent_runner["workflow_name"] = wf.name
        if getattr(step, "produces", None):
            parent_runner["produces"] = list(step.produces or [])
        if getattr(step, "prompt_instruction", None):
            parent_runner["prompt_instruction"] = step.prompt_instruction
        parent_out = run_verified_plan([parent_runner], halt_on_fail=True)
        if parent_out.get("ok"):
            out = dict(parent_out)
            out["case_ok"] = True
            out["cascaded"] = True
            out["reason"] = parent_out.get("reason") or "case then parent ok"
        else:
            out = dict(parent_out)
            out["case_ok"] = True
            out["cascaded"] = True
            out["ok"] = False
            out["reason"] = (
                "case resolved, but parent step failed: "
                + str(parent_out.get("reason") or "")
            )

    # Normal extract failed with "no email" — switch into an approved case, then retry.
    if (
        not out.get("ok")
        and matched_case is None
        and list_step_cases(step)
    ):
        fail_reason = None
        fail_observed = None
        if out.get("results"):
            fail_reason = out["results"][-1].reason
            fail_observed = out["results"][-1].value_after
        fail_reason = fail_reason or out.get("reason")
        from case_match import pick_case_after_blocker_failure

        pick = pick_case_after_blocker_failure(
            step,
            exec_plan.get("structural") or before_demo or {},
            wf.name,
            reason=str(fail_reason or ""),
            observed=str(fail_observed or ""),
            vision_fn=vision_fn,
        )
        if pick and pick.get("case"):
            from case_steps import case_continue_with_parent, case_to_runner_steps
            from step_cases import record_case_match

            matched_case = pick["case"]
            record_case_match(matched_case)
            case_runners = case_to_runner_steps(matched_case, step.id)
            for rs in case_runners:
                rs["workflow_name"] = wf.name
            case_out = run_verified_plan(case_runners, halt_on_fail=True)
            exec_plan = dict(exec_plan)
            exec_plan["action"] = "case"
            exec_plan["case"] = matched_case
            exec_plan["log"] = pick.get("log") or exec_plan.get("log")
            exec_plan["continue_parent"] = case_continue_with_parent(matched_case)
            if case_out.get("ok") and exec_plan["continue_parent"]:
                parent_runner = dict(exec_plan.get("runner_step") or {})
                # Prefer a fresh parent runner from the original normal plan
                parent_node = step_to_node(step, test_values)
                parent_runner = parent_node.to_runner_step()
                parent_runner["workflow_name"] = wf.name
                if getattr(step, "produces", None):
                    parent_runner["produces"] = list(step.produces or [])
                if getattr(step, "prompt_instruction", None):
                    parent_runner["prompt_instruction"] = step.prompt_instruction
                parent_out = run_verified_plan([parent_runner], halt_on_fail=True)
                if parent_out.get("ok"):
                    out = dict(parent_out)
                    out["case_ok"] = True
                    out["cascaded"] = True
                    out["blocker_cascade"] = True
                    out["reason"] = parent_out.get("reason") or "case then parent ok"
                else:
                    out = dict(parent_out)
                    out["case_ok"] = True
                    out["cascaded"] = True
                    out["blocker_cascade"] = True
                    out["ok"] = False
                    out["reason"] = (
                        "case resolved, but parent step failed: "
                        + str(parent_out.get("reason") or "")
                    )
            elif case_out.get("ok"):
                out = dict(case_out)
                out["cascaded"] = False
                out["blocker_cascade"] = True
            else:
                out = dict(case_out)
                out["ok"] = False
                out["blocker_cascade"] = True
                out["reason"] = (
                    "blocker case failed: " + str(case_out.get("reason") or fail_reason or "")
                )
            save_taught(wf)

    if before_demo is not None:
        from success_signals import snapshot_structural_state

        after_demo = snapshot_structural_state()
    ok = bool(out.get("ok"))
    reason = out.get("reason")
    if out.get("results"):
        reason = out["results"][-1].reason
        observed = out["results"][-1].value_after
    else:
        observed = None
    from success_signals import verify_success_check

    method = (getattr(step, "method", "anchor") or "anchor").strip().lower()
    # After case→parent cascade, trust the parent outcome (email extract), not a stale case title check.
    if matched_case is not None and out.get("cascaded") and method == "prompt" and ok and observed:
        v = {"ok": True, "reason": reason or f"prompt produced {observed!r}", "cost": "vision"}
    elif matched_case is not None and out.get("cascaded") and ok:
        v = verify_success_check(
            step,
            wf.name,
            before_demo=before_demo,
            after_demo=after_demo,
            os_input_calls=os_input.call_count() - before,
        )
        if v.get("ok") is None:
            v = {"ok": True, "reason": reason or "case then parent ok", "cost": "cascade"}
    elif matched_case is not None:
        from case_match import verify_case_success

        v = verify_case_success(
            matched_case,
            wf.name,
            before_demo=before_demo,
            after_demo=after_demo,
            os_input_calls=os_input.call_count() - before,
        )
    elif method == "prompt" and ok and observed:
        # Vision extract / prompt steps don't change UI chrome — runner result is enough.
        v = {"ok": True, "reason": reason or f"prompt produced {observed!r}", "cost": "vision"}
    else:
        v = verify_success_check(
            step,
            wf.name,
            before_demo=before_demo,
            after_demo=after_demo,
            os_input_calls=os_input.call_count() - before,
        )
    calls = os_input.call_count() - before
    cc = int(getattr(step, "click_count", 1) or 1)
    action = (step.action or {}).get("action") or ""
    if ok and action == "chain" and calls < cc:
        ok = False
        reason = f"demo reported success but sent {calls} of {cc} required click(s)"
    if v.get("ok") is False:
        ok = False
        reason = v.get("reason") or reason
        observed = v.get("actual") or observed
    elif v.get("ok") is True:
        observed = v.get("reason") or observed
    result = {
        "ok": ok,
        "reason": reason,
        "observed": observed,
        "success_verify": v,
        "os_input_calls": os_input.call_count() - before,
        "mode": mode,
        "ts": datetime.now(timezone.utc).isoformat(),
        "case_decision": exec_plan.get("log"),
    }
    if matched_case is not None:
        result["case_id"] = matched_case.id
        result["cascaded"] = bool(out.get("cascaded"))
        try:
            from case_steps import mark_case_demo

            mark_case_demo(matched_case, {
                "ok": ok,
                "reason": reason,
                "observed": observed,
                "cascaded": bool(out.get("cascaded")),
            })
        except Exception:
            pass
    if focus_result is not None:
        result["focus"] = focus_result
    if mode == "manual" and getattr(step, "expected_start_frame", None):
        result["expected_start_frame"] = step.expected_start_frame
    if method == "prompt" and ok and out.get("results"):
        emailish = out["results"][-1].value_after
        if emailish and "@" in str(emailish):
            param = "{recipient_email}"
            for p in step.produces or []:
                if "email" in str(p).lower():
                    param = str(p)
                    break
            result["produced"] = {param: emailish}
            result["observed"] = emailish
    step.demo = result
    if ok:
        if step.status != "approved":
            step.status = "demonstrated"
        step.edit_notice = None
        if step.case_halt and not (step.case_halt or {}).get("resolution"):
            step.case_halt = None
    else:
        try:
            from case_halt_loop import maybe_record_demo_halt

            halt_info = maybe_record_demo_halt(wf, step, result)
            if halt_info:
                result["case_halt"] = halt_info
        except Exception:
            pass
    save_taught(wf)
    return result


def run_taught(wf: TaughtWorkflow, inputs: dict | None = None) -> dict:
    compiled = compile_taught(wf, inputs)
    if not compiled.get("ok"):
        return {"ok": False, "executed": False, "violations": compiled["violations"]}
    from plan_engine import execute_validated_plan

    return execute_validated_plan(compiled["plan"])
