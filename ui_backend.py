"""Unified record → edit → compile → run cycle. Pure Python (no HTML)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import uuid
from queue import Queue

from compile_workflow import (
    bind_inputs,
    check_dependencies,
    compile_workflow,
)
from harness_schema import STEP_KINDS
from ui_prompts import ask_human, clear_ui_bridge, set_ui_bridge
from workflow_folder import (
    create_workflow_folder,
    list_workflow_folders,
    resolve_paths,
    safe_name,
    workflow_dir,
    workflow_exists,
)

_record_procs: dict[str, subprocess.Popen] = {}
_runs: dict[str, dict] = {}
_lock = threading.Lock()


def _cards_path(name: str) -> str:
    return os.path.join(resolve_paths(name)["workflow_dir"], "cards.json")


def _normalize_card(card: dict, index: int) -> dict:
    kind = (card.get("kind") or "reason").strip().lower()
    if kind not in STEP_KINDS:
        kind = "reason"
    action = card.get("action") if isinstance(card.get("action"), dict) else None
    return {
        "index": int(index),
        "kind": kind,
        "description": (card.get("description") or "").strip(),
        "instruction": (card.get("instruction") or "").strip(),
        "screenshot_url": card.get("screenshot_url"),
        "inputs": list(card.get("inputs") or []),
        "outputs": list(card.get("outputs") or []),
        "deleted": bool(card.get("deleted")),
        "action": action,
        "target_name": card.get("target_name"),
        "target_type": card.get("target_type"),
        "window_title": card.get("window_title") or card.get("target_window"),
        "goal": card.get("goal"),
    }


def _load_cards(name: str) -> list[dict]:
    path = _cards_path(name)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("steps") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    return [_normalize_card(c, i) for i, c in enumerate(raw) if isinstance(c, dict)]


def _save_cards(name: str, cards: list[dict]) -> None:
    paths = resolve_paths(name)
    os.makedirs(paths["workflow_dir"], exist_ok=True)
    normalized = [_normalize_card(c, i) for i, c in enumerate(cards)]
    with open(_cards_path(name), "w", encoding="utf-8") as f:
        json.dump({"steps": normalized}, f, indent=2)


def _load_plan_steps(name: str) -> list:
    path = resolve_paths(name).get("plan_json")
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _screenshot_url(name: str, abs_path: str | None) -> str | None:
    if not abs_path or not os.path.isfile(abs_path):
        return None
    fname = os.path.basename(str(abs_path).replace("\\", "/"))
    if not fname:
        return None
    return f"/screenshots/{fname}?name={safe_name(name)}"


def _existing_screenshot_url(name: str, card: dict) -> str | None:
    """Keep a stored URL if the file still exists under this workflow's captures/."""
    raw = card.get("screenshot_url")
    if not raw or not str(raw).startswith("/screenshots/"):
        return None
    fname = os.path.basename(str(raw).split("?")[0])
    cap = os.path.join(resolve_paths(name)["captures_dir"], fname)
    if os.path.isfile(cap):
        return f"/screenshots/{fname}?name={safe_name(name)}"
    return None


def _resolve_card_screenshot(name: str, card: dict, index: int,
                            plan_steps: list | None = None) -> str | None:
    """Join this card to a PNG the same way retro-label does (plan field, then db)."""
    kept = _existing_screenshot_url(name, card)
    if kept:
        return kept

    paths = resolve_paths(name)
    wd = paths["workflow_dir"]
    from transcribe import _resolve_screenshot
    from workflow_folder import normalize_screenshot_path

    stored = card.get("screenshot")
    if stored:
        path = normalize_screenshot_path(stored, wd)
        url = _screenshot_url(name, path)
        if url:
            return url

    # Inserted reason-only cards must not steal a neighboring click's shot.
    action = card.get("action") if isinstance(card.get("action"), dict) else None
    is_click = bool(action and str(action.get("action") or "").lower() == "click")
    if not is_click and not card.get("target_name"):
        return None

    if plan_steps is None:
        plan_steps = _load_plan_steps(name)
    if index < 0 or index >= len(plan_steps):
        return None
    plan_step = plan_steps[index]
    if not isinstance(plan_step, dict):
        return None
    path = _resolve_screenshot(plan_step, workflow_dir=wd)
    return _screenshot_url(name, path)


def _attach_screenshots(name: str, cards: list) -> tuple[list[dict], bool]:
    plan_steps = _load_plan_steps(name)
    changed = False
    out = []
    for i, card in enumerate(cards):
        url = _resolve_card_screenshot(name, card, i, plan_steps)
        c = dict(card)
        if url != c.get("screenshot_url"):
            changed = True
        c["screenshot_url"] = url
        out.append(_normalize_card(c, i))
    return out, changed


def _event_count(name: str) -> int:
    paths = resolve_paths(name)
    db = os.path.join(paths["workflow_dir"], "recording.db")
    if not os.path.isfile(db):
        db = paths["recording_db"]
    if not os.path.isfile(db):
        return 0
    try:
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        conn.close()
        return int(n)
    except Exception:
        return 0


def list_workflows() -> list:
    out = []
    for n in list_workflow_folders():
        paths = resolve_paths(n)
        steps_n, approved, status = 0, 0, "idle"
        try:
            from teaching import load_taught

            tw = load_taught(n)
            steps_n = len(tw.steps)
            approved = sum(1 for s in tw.steps if s.status == "approved")
            if steps_n and approved == steps_n:
                status = "ready"
            elif steps_n or (tw.context or "").strip():
                status = "draft"
        except Exception:
            pass
        out.append({
            "name": n,
            "workflow_dir": paths["workflow_dir"],
            "has_transcript": os.path.isfile(paths["transcript_json"]),
            "has_cards": os.path.isfile(_cards_path(n)),
            "steps": steps_n,
            "approved": approved,
            "status": status,
        })
    return out


def start_recording(name: str) -> dict:
    stem = safe_name(name)
    paths = create_workflow_folder(stem, overwrite=True)
    os.makedirs(paths["captures_dir"], exist_ok=True)
    rec_script = os.path.abspath("mini_recorder.py")
    proc = subprocess.Popen(
        [sys.executable, rec_script, paths["workflow_dir"]],
        cwd=os.path.abspath("."),
    )
    with _lock:
        _record_procs[stem] = proc
    return {
        "ok": True,
        "name": stem,
        "workflow_dir": paths["workflow_dir"],
        "pid": proc.pid,
        "hint": "Press Esc in the recorder to stop.",
    }


def recording_status(name: str) -> dict:
    stem = safe_name(name)
    proc = _record_procs.get(stem)
    running = bool(proc is not None and proc.poll() is None)
    return {
        "running": running,
        "events_so_far": _event_count(stem),
        "name": stem,
    }


def finish_recording(name: str) -> dict:
    stem = safe_name(name)
    proc = _record_procs.get(stem)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    with _lock:
        _record_procs.pop(stem, None)

    paths = resolve_paths(stem)
    db = os.path.join(paths["workflow_dir"], "recording.db")
    if os.path.isfile(db):
        from distill import distill_recording
        from transcribe import transcribe

        distill_recording(
            db_path=db,
            plan_txt=paths["plan_txt"],
            plan_json=paths["plan_json"],
        )
        transcribe(
            paths["plan_json"],
            out_txt=paths["transcript_txt"],
            out_json=paths["transcript_json"],
            workflow_dir=paths["workflow_dir"],
        )
        cards = _cards_from_transcript(paths["transcript_json"])
        cards, _ = _attach_screenshots(stem, cards)
        _save_cards(stem, cards)
    return get_steps(stem)


def _cards_from_transcript(transcript_json: str) -> list[dict]:
    with open(transcript_json, "r", encoding="utf-8") as f:
        payload = json.load(f)
    raw = payload.get("steps") if isinstance(payload, dict) else payload
    cards = []
    for i, sd in enumerate(raw or []):
        if not isinstance(sd, dict):
            continue
        kind = (sd.get("kind") or "reason").strip().lower()
        desc = (sd.get("description") or "").strip()
        action = sd.get("action") if isinstance(sd.get("action"), dict) else None
        cards.append(_normalize_card({
            "kind": kind,
            "description": desc,
            "instruction": desc,
            "action": action,
            "target_name": sd.get("target_name"),
            "target_type": sd.get("target_type"),
            "goal": sd.get("goal"),
            "inputs": list(sd.get("inputs") or []),
        }, i))
    return cards


def get_steps(name: str) -> dict:
    stem = safe_name(name)
    if not workflow_exists(stem) and not os.path.isdir(workflow_dir(stem)):
        return {"name": stem, "steps": []}
    cards, changed = _attach_screenshots(stem, _load_cards(stem))
    if changed:
        _save_cards(stem, cards)
    return {"name": stem, "steps": cards}


def seed_steps(name: str, cards: list[dict]) -> dict:
    """Self-test helper: write cards without launching the recorder."""
    stem = safe_name(name)
    create_workflow_folder(stem, overwrite=True)
    _save_cards(stem, cards)
    return get_steps(stem)


def update_step(name: str, index: int, patch: dict) -> dict:
    cards = _load_cards(name)
    if index < 0 or index >= len(cards):
        raise IndexError(f"no step at index {index}")
    allowed = {
        "description", "instruction", "kind", "deleted", "action",
        "target_name", "target_type", "goal", "inputs", "outputs",
        "window_title", "target_window",
    }
    for k, v in (patch or {}).items():
        if k in allowed:
            cards[index][k] = v
    cards = [_normalize_card(c, i) for i, c in enumerate(cards)]
    _save_cards(name, cards)
    return {"ok": True, "step": cards[index], "steps": cards}


def insert_step(name: str, after_index: int, description: str, kind: str = "reason") -> dict:
    cards = _load_cards(name)
    new = _normalize_card({
        "kind": kind or "reason",
        "description": description,
        "instruction": description,
        "goal": description if (kind or "reason") == "reason" else None,
    }, 0)
    insert_at = max(0, min(int(after_index) + 1, len(cards)))
    cards.insert(insert_at, new)
    cards = [_normalize_card(c, i) for i, c in enumerate(cards)]
    _save_cards(name, cards)
    return {"ok": True, "steps": cards}


def save_workflow(name: str, steps: list | None = None) -> dict:
    stem = safe_name(name)
    if steps is not None:
        _save_cards(stem, steps)
    cards = _load_cards(stem)
    violations = check_dependencies(cards)
    if violations:
        return {
            "ok": False,
            "error": "dependency_violation",
            "violations": violations,
        }
    problems = validate_cards_import(cards)
    if problems:
        return {"ok": False, "error": "invalid_plan", "problems": problems}

    compiled = compile_workflow(cards)
    if not compiled.get("ok"):
        return {
            "ok": False,
            "error": "compile_failed",
            "problems": compiled.get("problems") or [],
        }

    paths = resolve_paths(stem)
    os.makedirs(paths["workflow_dir"], exist_ok=True)
    payload = {
        "source": "ui",
        "inputs": sorted({
            inp
            for card in cards if not card.get("deleted")
            for inp in (card.get("inputs") or [])
        }),
        "steps": compiled["harness_steps"],
        "plan": compiled["plan"],
    }
    with open(paths["transcript_json"], "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return {
        "ok": True,
        "name": stem,
        "path": paths["transcript_json"],
        "steps": len(compiled["harness_steps"]),
    }


def validate_cards_import(cards):
    from compile_workflow import validate_cards
    return validate_cards(cards)


def compile_workflow_for(name: str) -> dict:
    return compile_workflow(_load_cards(name))


def _append_log(status: dict, line: str) -> None:
    status.setdefault("log", [])
    status["log"].append(line)
    print(f"  [run {status.get('run_id')}] {line}")


def _execute_plan(status: dict, plan: list, inputs: dict, require_approval: bool) -> None:
    from ui_runner import execute_step

    filled = bind_inputs(plan, inputs)
    status["total"] = len(filled)
    last_window = None
    skip_launch_types = False
    for i, step in enumerate(filled):
        status["step_index"] = i
        kind = step.get("kind")
        inst = step.get("instruction") or ""
        _append_log(status, f"step {i + 1}/{len(filled)} [{kind}] {inst}")
        action = (step.get("action") or "").strip()
        if skip_launch_types and action in ("type", "type_text") and not step.get("elem_name"):
            _append_log(status, "skipping launch type; target app already open")
            continue
        skip_launch_types = False
        if action == "__test_crash__":
            raise RuntimeError("injected test crash")
        if require_approval:
            prompt = f"Approve step {i + 1}: {inst} ?"
            ans = ask_human("approval", prompt)
            if str(ans).strip().lower() not in ("y", "yes", "approve", "ok"):
                _append_log(status, "stopped by human")
                status["error"] = "stopped"
                return
        from safety_gate import require_irreversible_confirmation
        if not require_irreversible_confirmation(step):
            _append_log(status, "stopped at irreversible tollgate")
            status["error"] = "stopped"
            return
        result = execute_step(step, last_window=last_window)
        for line in result.log_lines():
            _append_log(status, line)
        if not result.ok:
            try:
                from halt_repair import capture_halt_screenshot
                from workflow_folder import resolve_paths

                status["halt_screenshot"] = capture_halt_screenshot(
                    resolve_paths(status.get("name") or "")["workflow_dir"],
                    f"step{i}",
                )
                _append_log(status, f"halt screenshot {status['halt_screenshot']}")
            except Exception as e:
                _append_log(status, f"halt screenshot skipped ({e})")
            from ui_runner import find_window, needed_app_windows

            remaining = filled[i + 1 :]
            needed = needed_app_windows(remaining + [step], last_window)
            missing = [w for w in needed if find_window(w)[0] is None]
            present = [(w, find_window(w)[1]) for w in needed if find_window(w)[0] is not None]
            elem = (step.get("elem_name") or "").strip().lower()
            if missing:
                msg = f"target window {missing[0]!r} not found — is the app open?"
                status["error"] = msg
                _append_log(status, f"ok=False — stopping. {msg}")
                return
            if present and elem in ("search", "start"):
                last_window = present[0][1]
                skip_launch_types = True
                _append_log(
                    status,
                    f"skipping unresolved launch click {step.get('elem_name')!r}; "
                    f"{present[0][0]!r} already open",
                )
                continue
            status["error"] = result.reason
            _append_log(status, f"ok=False — stopping. {result.reason}")
            return
        if result.window_found:
            last_window = result.window_found
    status["step_index"] = len(filled)
    _append_log(status, "done")


def run_workflow(name: str, inputs: dict | None = None, require_approval: bool = False) -> dict:
    stem = safe_name(name)
    cards = _load_cards(stem)
    compiled = compile_workflow(cards)
    if not compiled.get("ok"):
        return {"ok": False, "error": "compile_failed", "problems": compiled.get("problems")}
    violations = check_dependencies(cards)
    if violations:
        return {"ok": False, "error": "dependency_violation", "violations": violations}

    run_id = uuid.uuid4().hex[:12]
    answer_q: Queue = Queue()
    status = {
        "run_id": run_id,
        "name": stem,
        "running": True,
        "step_index": 0,
        "total": len(compiled["plan"]),
        "log": [],
        "awaiting": "none",
        "prompt_text": "",
        "error": None,
        "answer_queue": answer_q,
    }
    with _lock:
        _runs[run_id] = status

    def _thread():
        set_ui_bridge(status, answer_q)
        try:
            _execute_plan(status, compiled["plan"], inputs or {}, require_approval)
        except Exception as e:
            status["error"] = str(e)
            _append_log(status, f"error: {e}")
        finally:
            clear_ui_bridge()
            status["running"] = False
            status["awaiting"] = "none"

    threading.Thread(target=_thread, daemon=True).start()
    return {"ok": True, "run_id": run_id}


def run_status(run_id: str) -> dict:
    with _lock:
        st = _runs.get(run_id)
    if not st:
        return {"ok": False, "error": "unknown run_id", "running": False}
    log = list(st.get("log") or [])
    running = bool(st.get("running"))
    error = st.get("error")
    if running:
        state = "running"
    elif error in ("stopped",):
        state = "stopped"
    elif error:
        state = "failed"
    else:
        state = "finished"
    return {
        "ok": True,
        "running": running,
        "state": state,
        "step_index": st.get("step_index"),
        "total": st.get("total"),
        "log_tail": log[-40:],
        "awaiting": st.get("awaiting") or "none",
        "prompt_text": st.get("prompt_text") or "",
        "error": error,
        "halt_screenshot": st.get("halt_screenshot"),
        "outcome": None if running else ("ok" if not error else error),
    }


def answer_run(run_id: str, answer: str) -> dict:
    with _lock:
        st = _runs.get(run_id)
    if not st:
        return {"ok": False, "error": "unknown run_id"}
    st["answer_queue"].put(answer)
    return {"ok": True, "run_id": run_id}


def bind_compiled_plan(plan: list, inputs: dict) -> list:
    return bind_inputs(plan, inputs)


def workflow_inputs(name: str) -> list:
    from compile_workflow import live_cards, placeholders_in, step_references

    names = set()
    for c in live_cards(_load_cards(name)):
        names |= step_references(c)
        names |= placeholders_in(c.get("description"))
        names |= placeholders_in(c.get("instruction"))
    return sorted(names)


def cards_to_plan(name: str) -> dict:
    from plan_schema import CLOSED_ACTIONS
    from plan_validator import validate_plan

    nodes = []
    for c in _load_cards(name):
        if c.get("deleted"):
            continue
        act = ""
        if isinstance(c.get("action"), dict):
            act = c["action"].get("action") or ""
        if act not in CLOSED_ACTIONS:
            act = "click" if c.get("target_name") else "wait"
        nodes.append({
            "id": f"s{c.get('index', len(nodes))}",
            "action": act,
            "target_desc": c.get("description") or c.get("instruction"),
            "target_ref": c.get("target_name"),
            "elem_name": c.get("target_name"),
            "elem_type": c.get("target_type"),
            "window_title": c.get("window_title"),
            "value": (c.get("action") or {}).get("text") if isinstance(c.get("action"), dict) else None,
        })
    plan = {"nodes": nodes, "source": f"workflow:{safe_name(name)}"}
    return {"ok": True, "plan": plan, "violations": validate_plan(plan)}


def validate_plan_payload(plan) -> dict:
    from plan_validator import validate_plan
    from os_input import call_count, reset_calls

    before = call_count()
    viol = validate_plan(plan)
    assert call_count() == before
    return {
        "ok": not viol,
        "violations": viol,
        "executed": False,
        "os_input_calls": call_count() - before,
    }


def propose_plan(text: str, name: str | None = None) -> dict:
    """Removed: one-shot planning from a single sentence."""
    return {
        "ok": False,
        "error": "one-shot planning removed; teach one step at a time",
        "plan": None,
        "violations": [{"code": "removed", "message": "one-shot planning removed"}],
    }


def teach_get(name: str) -> dict:
    from teaching import load_taught

    wf = load_taught(name)
    return {"ok": True, **wf.to_dict()}


def teach_set_context(name: str, text: str) -> dict:
    from teach_loop import set_context
    from teaching import load_taught

    wf = load_taught(name)
    wf.name = name
    set_context(wf, text)
    return {"ok": True, **wf.to_dict()}


def teach_update_step(name: str, step_id: str, description=None, varies_note=None,
                     memory_note=None, web_allowed=None, clear=None,
                     understanding=None, drop_photo=None) -> dict:
    from teach_loop import update_step
    from teaching import TeachingError, load_taught

    wf = load_taught(name)
    wf.name = name
    try:
        step = update_step(
            wf, step_id, description=description, varies_note=varies_note,
            memory_note=memory_note, web_allowed=web_allowed, clear=clear,
            understanding=understanding, drop_photo=drop_photo,
        )
    except TeachingError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "step": step.to_dict(), "workflow": wf.to_dict()}


def teach_delete_step(name: str, step_id: str) -> dict:
    from teach_loop import delete_step
    from teaching import load_taught

    wf = load_taught(name)
    wf.name = name
    delete_step(wf, step_id)
    return {"ok": True, "workflow": wf.to_dict()}


def teach_add_photo(name: str, step_id: str, image_b64: str, filename: str = "shot.png") -> dict:
    import base64

    from teach_loop import attach_photo
    from teaching import load_taught

    raw = image_b64 or ""
    if "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        blob = base64.b64decode(raw)
    except Exception:
        return {"ok": False, "error": "could not read image"}
    if len(blob) < 32:
        return {"ok": False, "error": "empty image"}
    wf = load_taught(name)
    wf.name = name
    return attach_photo(wf, step_id, blob, filename=filename)


def teach_observe(name: str, step_id: str, seconds: float = 15) -> dict:
    from observe import watch_step

    return watch_step(name, step_id, seconds=seconds)


def teach_add_step(name: str, description: str, varies_note: str = "") -> dict:
    from teach_loop import add_step
    from teaching import load_taught

    wf = load_taught(name)
    wf.name = name
    step = add_step(wf, description, varies_note)
    return {"ok": True, "step": step.to_dict(), "workflow": wf.to_dict()}


def teach_start(name: str, step_id: str) -> dict:
    from teach_loop import start_training
    from teaching import load_taught

    wf = load_taught(name)
    qs = start_training(wf, step_id)
    return {"ok": True, "questions": qs, "workflow": wf.to_dict()}


def teach_answer(name: str, step_id: str, question: str, answer: str) -> dict:
    from teach_loop import answer_chat
    from teaching import load_taught

    wf = load_taught(name)
    step = answer_chat(wf, step_id, question, answer)
    return {"ok": True, "step": step.to_dict()}


def teach_show(name: str, step_id: str, point=None, focus: bool = False) -> dict:
    from teach_loop import answer_show
    from teaching import load_taught

    wf = load_taught(name)
    kwargs = {"countdown": 0, "focus": bool(focus)}
    if point and len(point) == 2:
        kwargs["point"] = (int(point[0]), int(point[1]))
    return answer_show(wf, step_id, **kwargs)


def teach_arm_show(name: str, step_id: str) -> dict:
    from float_widget import arm_show

    return arm_show(workflow=name, step_id=step_id, api_url="http://127.0.0.1:8765")


def teach_choose_witness(name: str, step_id: str, choice: str) -> dict:
    from teach_loop import choose_witness
    from teaching import TeachingError, load_taught

    wf = load_taught(name)
    try:
        step = choose_witness(wf, step_id, choice)
    except TeachingError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "step": step.to_dict()}


def teach_explain_start(name: str, description: str = "", varies_note: str = "") -> dict:
    from teach_loop import explain_start
    from teaching import load_taught

    wf = load_taught(name)
    wf.name = name
    start = explain_start(wf, description=description, varies_note=varies_note)
    return {"ok": True, "start_screen": start, "workflow": wf.to_dict()}


def teach_rehearse(name: str, step_id: str, test_values: dict | None = None) -> dict:
    from teach_loop import rehearse_step
    from teaching import load_taught

    wf = load_taught(name)
    return rehearse_step(wf, step_id, test_values=test_values)


def teach_explain(name: str, step_id: str) -> dict:
    from teach_loop import explain_understanding
    from teaching import load_taught

    wf = load_taught(name)
    understanding = explain_understanding(wf, step_id)
    return {"ok": True, "understanding": understanding}


def teach_approve_understanding(name: str, step_id: str) -> dict:
    from teach_loop import approve_understanding
    from teaching import TeachingError, load_taught

    wf = load_taught(name)
    try:
        step = approve_understanding(wf, step_id)
    except TeachingError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "step": step.to_dict()}


def teach_reject_understanding(name: str, step_id: str, correction: str) -> dict:
    from teach_loop import reject_understanding
    from teaching import load_taught

    wf = load_taught(name)
    step = reject_understanding(wf, step_id, correction)
    return {"ok": True, "step": step.to_dict()}


def teach_prepare(name: str, step_id: str, mode: str, test_values: dict | None = None) -> dict:
    from teach_loop import prepare_state
    from teaching import load_taught

    wf = load_taught(name)
    return prepare_state(wf, step_id, mode, test_values=test_values)


def teach_demo(name: str, step_id: str, test_values: dict | None = None, mode: str = "manual") -> dict:
    from teach_loop import demo_step
    from teaching import load_taught

    wf = load_taught(name)
    return demo_step(wf, step_id, test_values=test_values, mode=mode)


def teach_reflect(name: str, step_id: str) -> dict:
    from teach_loop import reflect_on_demo
    from teaching import load_taught

    wf = load_taught(name)
    reflection = reflect_on_demo(wf, step_id)
    return {"ok": True, "reflection": reflection}


def teach_approve_behaviour(name: str, step_id: str) -> dict:
    from teach_loop import approve_behaviour
    from teaching import TeachingError, load_taught

    wf = load_taught(name)
    try:
        step = approve_behaviour(wf, step_id)
    except TeachingError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "step": step.to_dict()}


def teach_approve(name: str, step_id: str, skip_rehearsal: bool = False) -> dict:
    from teach_loop import approve_step
    from teaching import load_taught

    wf = load_taught(name)
    step = approve_step(wf, step_id, skip_rehearsal=skip_rehearsal)
    return {"ok": True, "step": step.to_dict()}


def teach_compile(name: str, inputs: dict | None = None) -> dict:
    from teach_compile import compile_taught
    from teaching import TeachingError, load_taught

    wf = load_taught(name)
    try:
        return compile_taught(wf, inputs)
    except TeachingError as e:
        return {"ok": False, "error": str(e)}


def teach_run(name: str, inputs: dict | None = None) -> dict:
    from teach_compile import run_taught
    from teaching import load_taught

    wf = load_taught(name)
    return run_taught(wf, inputs)


def apply_repair(name: str, node_id: str, x: int, y: int) -> dict:
    from halt_repair import apply_repair_click, capture_halt_screenshot
    from plan_schema import node_from_dict
    from workflow_folder import resolve_paths

    paths = resolve_paths(name)
    shot = capture_halt_screenshot(paths["workflow_dir"], str(node_id))
    node = node_from_dict({"id": str(node_id), "action": "click"})
    repaired = apply_repair_click(node, shot, int(x), int(y), paths["workflow_dir"])
    return {"ok": True, "node": repaired.to_dict(), "screenshot": shot}
