"""Per-step teaching loop. The LLM (or heuristic) sees only THIS step."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from plan_schema import CLOSED_ACTIONS
from teaching import (
    TaughtStep,
    TaughtWorkflow,
    TeachingError,
    get_step,
    next_step_id,
    save_taught,
)

_TARGET_HINT = re.compile(
    r"\b(the\s+['\"][^'\"]+['\"]|the\s+[\w.]+(?:\s+[\w.]+){0,4}|['\"][^'\"]+['\"])",
    re.I,
)


def set_context(wf: TaughtWorkflow, text: str) -> TaughtWorkflow:
    wf.context = (text or "").strip()
    save_taught(wf)
    return wf


def _dynamic_params(text: str, context: str = "") -> list[str]:
    blob = f"{text} {context}".lower()
    params: list[str] = []
    if "linkedin" in blob or re.search(r"\bprofile\b", blob):
        params.append("{linkedin_profile}")
    if re.search(r"\b(person|recipient|contact|candidate|lead)\b", blob) and "{linkedin_profile}" not in params:
        params.append("{person}")
    if "filename" in blob or "file name" in blob:
        params.append("{filename}")
    if "email" in blob and "{recipient_email}" not in params and "linkedin" not in blob:
        params.append("{recipient_email}")
    # de-dupe, keep order
    seen = set()
    out = []
    for p in params:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def explain_start(wf: TaughtWorkflow, description: str = "", varies_note: str = "") -> dict:
    """Mark the starting screen and what changes each run. No OS input."""
    desc = (description or "").strip() or ((wf.start_screen or {}).get("description") or "")
    varies = (varies_note or "").strip() or ((wf.start_screen or {}).get("varies_note") or "")
    params = _dynamic_params(desc + " " + varies, wf.context)
    if not params and re.search(r"\b(changes|each run|different each|keeps changing)\b", varies, re.I):
        params = ["{input}"]
    if params:
        summary = (
            f"Every run begins on this screen. What changes each time: {', '.join(params)}. "
            "I will wait until this screen is showing before step 1."
        )
    else:
        summary = (
            "Every run begins on this screen. Nothing here was marked as changing each run."
        )
    start = dict(wf.start_screen or {})
    start.update({
        "description": desc,
        "varies_note": varies,
        "parameters": params,
        "summary": summary,
    })
    wf.start_screen = start
    save_taught(wf)
    return start


def _witness_rank(name: str, w: dict) -> int:
    if not w or not w.get("saw"):
        return -1
    conf = {"high": 3, "medium": 2, "low": 1}.get(w.get("confidence") or "low", 1)
    pipe = {"a11y": 3, "vision": 2, "dom": 1}.get(name, 0)
    return conf * 10 + pipe


def _pick_primary(witnesses: dict) -> str | None:
    best, score = None, -1
    for k in ("a11y", "vision", "dom"):
        sc = _witness_rank(k, (witnesses or {}).get(k) or {})
        if sc > score:
            best, score = k, sc
    return best if score >= 0 else None


def apply_show_witnesses(wf: TaughtWorkflow, step_id: str, capture_out: dict | None = None) -> dict:
    """After Show me: agree/single pick a primary; conflict becomes a question."""
    step = get_step(wf, step_id)
    anchor = dict(step.anchor or {})
    mw = (capture_out or {}).get("witnesses") or {}
    witnesses = mw.get("witnesses") or anchor.get("witnesses") or {}
    agreement = mw.get("agreement") or anchor.get("agreement") or "single"
    note = mw.get("conflict_note") or anchor.get("conflict_note") or ""
    anchor["witnesses"] = witnesses
    anchor["agreement"] = agreement
    anchor["conflict_note"] = note
    seeing = [k for k, w in witnesses.items() if (w or {}).get("saw")]
    if agreement in ("agree", "single"):
        pipe = _pick_primary(witnesses)
        if pipe:
            chosen = dict(witnesses[pipe])
            chosen["pipeline"] = pipe
            anchor["primary"] = chosen
            anchor["fallbacks"] = [
                {**dict(witnesses[k]), "pipeline": k}
                for k in ("a11y", "vision", "dom")
                if k != pipe
            ]
            anchor["primary_reason"] = (
                "all witnesses agreed" if agreement == "agree" else "only witness"
            )
        anchor["conflict_unresolved"] = False
        step.anchor = anchor
        confirm = (capture_out or {}).get("confirm_question") or _show_confirm_question(anchor)
        _ask_show_confirm(step, confirm)
        save_taught(wf)
        out = dict(capture_out or {})
        out["anchor"] = anchor
        out["agreement"] = agreement
        out["question"] = confirm
        return out
    # conflict or partial — do not pick a winner
    accounts = []
    for k in ("a11y", "vision", "dom"):
        w = witnesses.get(k) or {}
        if w.get("saw"):
            accounts.append((k, w.get("account") or f"{k} saw something."))
    if not accounts:
        accounts = [(k, (witnesses.get(k) or {}).get("account") or f"{k} saw nothing.") for k in ("a11y", "vision", "dom")]
    lines = [f"The {k} pipeline says: {acc}" for k, acc in accounts]
    question = " ".join(lines) + " Which one do you mean?"
    choices = [k for k, _ in accounts] + ["neither — let me show you again"]
    step.status = "questioning"
    step.qa_history.append({
        "q": question,
        "a": "",
        "source": "witness",
        "kind": "witness_conflict",
        "choices": choices,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    anchor["conflict_unresolved"] = True
    anchor["primary"] = None
    step.anchor = anchor
    save_taught(wf)
    out = dict(capture_out or {})
    out["ok"] = True
    out["anchor"] = anchor
    out["agreement"] = agreement
    out["question"] = question
    out["choices"] = choices
    return out


def _show_confirm_question(anchor: dict) -> str:
    primary = (anchor or {}).get("primary") or {}
    name = primary.get("name") or "unnamed"
    ctype = primary.get("control_type") or "element"
    return f"I saw a {ctype} named {name!r} — is that the one?"


def _ask_show_confirm(step: TaughtStep, question: str) -> None:
    question = (question or "").strip()
    if not question:
        return
    for q in step.qa_history:
        if q.get("kind") == "show_confirm" and not (q.get("a") or "").strip() and q.get("q") == question:
            return
    step.status = "questioning"
    step.qa_history.append({
        "q": question,
        "a": "",
        "source": "show",
        "kind": "show_confirm",
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def handle_show_confirm(wf: TaughtWorkflow, step_id: str, answer: str) -> TaughtStep:
    step = get_step(wf, step_id)
    text = (answer or "").strip()
    low = text.lower()
    for q in reversed(step.qa_history):
        if q.get("kind") == "show_confirm" and not (q.get("a") or "").strip():
            q["a"] = text
            break
    anchor = dict(step.anchor or {})
    yes = low in ("yes", "y", "yeah", "yep", "correct", "right") or low.startswith("yes") or "that's the one" in low or "thats the one" in low
    no = low in ("no", "n", "nope", "wrong") or low.startswith("no") or "not the" in low
    if yes:
        anchor["confirmed"] = True
    elif no:
        anchor["confirmed"] = False
    else:
        anchor["confirmed_note"] = text
        if text and len(text) > 3:
            step.user_description = step.user_description or text
    step.anchor = anchor
    save_taught(wf)
    return step


def choose_witness(wf: TaughtWorkflow, step_id: str, choice: str) -> TaughtStep:
    step = get_step(wf, step_id)
    anchor = dict(step.anchor or {})
    witnesses = anchor.get("witnesses") or {}
    choice = (choice or "").strip().lower()
    if choice.startswith("neither") or choice == "again":
        anchor["conflict_unresolved"] = False
        anchor["primary"] = None
        step.anchor = anchor
        for q in reversed(step.qa_history):
            if q.get("kind") == "witness_conflict" and not (q.get("a") or "").strip():
                q["a"] = "neither — show again"
                break
        save_taught(wf)
        return step
    key = choice
    for k in ("a11y", "vision", "dom"):
        if k in choice or choice in k:
            key = k
            break
    if key not in witnesses:
        raise TeachingError(f"unknown witness {choice!r}")
    chosen = dict(witnesses[key])
    chosen["pipeline"] = key
    others = [{**dict(witnesses[k]), "pipeline": k} for k in ("a11y", "vision", "dom") if k != key]
    saw_others = [w["pipeline"] for w in others if w.get("saw")]
    reason = f"user chose {key} over {', '.join(saw_others) or 'the others'} (they disagreed)"
    anchor["primary"] = chosen
    anchor["fallbacks"] = others
    anchor["primary_reason"] = reason
    anchor["conflict_unresolved"] = False
    # keep name/type on primary for existing UI
    if not anchor.get("parent_path"):
        anchor["parent_path"] = chosen.get("parent_path")
    step.anchor = anchor
    for q in reversed(step.qa_history):
        if q.get("kind") == "witness_conflict" and not (q.get("a") or "").strip():
            q["a"] = key
            break
    save_taught(wf)
    return step


def answer_show(wf: TaughtWorkflow, step_id: str, **capture_kwargs) -> dict:
    if step_id in ("__start__", "start", "start_screen"):
        from show_capture import capture_start

        return capture_start(wf, **{k: v for k, v in capture_kwargs.items() if k in ("point", "countdown", "focus")})
    from show_capture import capture_show

    out = capture_show(wf, step_id, **capture_kwargs)
    return apply_show_witnesses(wf, step_id, out)


def add_step(wf: TaughtWorkflow, description: str, varies_note: str = "") -> TaughtStep:
    step = TaughtStep(
        id=next_step_id(wf),
        order=len(wf.steps),
        user_description=(description or "").strip(),
        varies_note=(varies_note or "").strip(),
        status="draft",
        is_start=len(wf.steps) == 0,
    )
    if varies_note:
        params = re.findall(r"\{[A-Za-z_][\w]*\}", varies_note)
        step.parameters = params
    wf.steps.append(step)
    save_taught(wf)
    return step


def update_step(wf: TaughtWorkflow, step_id: str, description=None, varies_note=None,
                 memory_note=None, web_allowed=None, clear=None, understanding=None,
                 drop_photo=None) -> TaughtStep:
    """Edit or strip any part of a step at any time."""
    step = get_step(wf, step_id)
    if description is not None:
        step.user_description = (description or "").strip()
    if varies_note is not None:
        step.varies_note = (varies_note or "").strip()
        params = re.findall(r"\{[A-Za-z_][\w]*\}", step.varies_note)
        step.parameters = params
    if memory_note is not None:
        step.memory_note = (memory_note or "").strip()
    if web_allowed is not None:
        step.web_allowed = bool(web_allowed)
    if understanding and isinstance(understanding, dict):
        cur = dict(step.understanding or {})
        for k, v in understanding.items():
            cur[k] = v
        step.understanding = cur
    for key in (clear or []):
        if key == "learned":
            step.learned = None
        elif key == "anchor":
            step.anchor = None
        elif key == "understanding":
            step.understanding = None
        elif key == "reflection":
            step.reflection = None
        elif key == "qa":
            step.qa_history = []
        elif key == "photos":
            step.photos = []
        elif key == "notes":
            step.memory_note = ""
    if drop_photo:
        step.photos = [p for p in (step.photos or []) if (p.get("path") or p) != drop_photo]
    save_taught(wf)
    return step


def delete_step(wf: TaughtWorkflow, step_id: str) -> TaughtWorkflow:
    wf.steps = [s for s in wf.steps if s.id != step_id]
    for i, s in enumerate(wf.steps):
        s.order = i
        s.is_start = i == 0
    save_taught(wf)
    return wf


def attach_photo(wf: TaughtWorkflow, step_id: str, image_bytes: bytes, filename: str = "shot.png") -> dict:
    from workflow_folder import workflow_dir

    step = get_step(wf, step_id)
    folder = os.path.join(workflow_dir(wf.name), "anchors")
    os.makedirs(folder, exist_ok=True)
    n = len(step.photos or []) + 1
    ext = "png"
    low = (filename or "").lower()
    if low.endswith(".jpg") or low.endswith(".jpeg"):
        ext = "jpg"
    rel = os.path.join("anchors", f"{step.id}_user_{n}.{ext}")
    abs_path = os.path.join(workflow_dir(wf.name), rel)
    with open(abs_path, "wb") as f:
        f.write(image_bytes)
    rec = {"path": rel, "filename": filename, "ts": datetime.now(timezone.utc).isoformat()}
    step.photos = list(step.photos or []) + [rec]
    save_taught(wf)
    return {"ok": True, "photo": rec, "step": step.to_dict()}


def _approved_summaries(wf: TaughtWorkflow, before_id: str) -> list[str]:
    out = []
    for s in wf.steps:
        if s.id == before_id:
            break
        if s.status == "approved":
            act = (s.action or {}).get("action") or "?"
            out.append(f"{s.id} ({act}): {s.user_description[:80]}")
    return out


def _already_has_target(step: TaughtStep) -> bool:
    desc = step.user_description or ""
    return bool(_TARGET_HINT.search(desc)) or bool(
        re.search(r"\b(click|type|press|paste|copy|save|open|launch|hotkey|enter|write|select|navigate)\b.+\S", desc, re.I)
    )


def start_training(wf: TaughtWorkflow, step_id: str) -> list[str]:
    """Ask at most 3 short questions about THIS step only."""
    step = get_step(wf, step_id)
    questions: list[str] = []
    if not _already_has_target(step):
        questions.append("What exactly should I target?")
    if not step.varies_note and not step.parameters:
        questions.append("Does anything here change each run, or is it always the same?")
    questions.append("How will I know this step succeeded?")
    questions = questions[:3]
    step.status = "questioning"
    existing = {(q.get("q") or "").strip() for q in step.qa_history}
    for q in questions:
        if q not in existing:
            step.qa_history.append({
                "q": q,
                "a": "",
                "source": "chat",
                "kind": "train",
                "ts": datetime.now(timezone.utc).isoformat(),
            })
    save_taught(wf)
    return questions


def answer_chat(wf: TaughtWorkflow, step_id: str, question: str, answer: str) -> TaughtStep:
    step = get_step(wf, step_id)
    pending_w = None
    pending_show = None
    for q in reversed(step.qa_history):
        if q.get("kind") == "witness_conflict" and not (q.get("a") or "").strip():
            pending_w = q
            break
        if q.get("kind") == "show_confirm" and not (q.get("a") or "").strip() and pending_show is None:
            pending_show = q
    if pending_w:
        return choose_witness(wf, step_id, answer)
    if pending_show and (
        not question or question == pending_show.get("q") or "is that the one" in (question or "").lower()
    ):
        return handle_show_confirm(wf, step_id, answer)
    step.qa_history.append({
        "q": question,
        "a": answer,
        "source": "chat",
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    low = (answer or "").lower()
    if any(w in low for w in ("varies", "changes", "each run", "parameter", "different")):
        brace = re.findall(r"\{[A-Za-z_][\w]*\}", answer or "")
        if brace:
            for b in brace:
                if b not in step.parameters:
                    step.parameters.append(b)
        elif "filename" in low and "{filename}" not in step.parameters:
            step.parameters.append("{filename}")
    save_taught(wf)
    return step


def followup(wf: TaughtWorkflow, step_id: str) -> list[str]:
    step = get_step(wf, step_id)
    if step.anchor:
        name = ((step.anchor.get("primary") or {}).get("name")) or "that element"
        return [f"I saw {name!r} — is that the one?"]
    return start_training(wf, step_id)


def _target_phrase(step: TaughtStep) -> str:
    desc = step.user_description or ""
    m = re.search(r"\bthe\s+(.+)$", desc, re.I)
    if m:
        return "the " + m.group(1).strip()
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", desc)
    if quoted:
        return quoted[0]
    return desc


def _closed_verb(text: str) -> str | None:
    """Map a natural-language description to a CLOSED_ACTIONS verb, or None."""
    blob = (text or "").lower()
    if re.search(r"\bpaste\b|\bctrl\s*\+\s*v\b", blob):
        return "paste"
    if re.search(r"\bcopy\b|\bctrl\s*\+\s*c\b", blob) and not re.search(r"\bcopy[_ ]?file\b", blob):
        return "copy"
    if re.search(r"\b(go\s*to|navigate|goto)\b", blob):
        return "navigate"
    if re.search(r"\b(launch|open)\b.*\b(notepad|chrome|edge|firefox|app)\b", blob) or re.search(
        r"\blaunch\b", blob
    ):
        return "launch_app"
    if re.search(r"https?://", blob) or re.search(r"\bopen[_ ]?url\b", blob):
        return "open_url"
    if re.search(r"\b(open[_ ]?path|open the file|open file)\b", blob) or (
        re.search(r"\bopen\b", blob) and "filename" in blob
    ):
        return "open_path"
    if re.search(r"\bsave\b|\bctrl\s*\+\s*s\b|\bhotkey\b", blob):
        return "hotkey"
    if re.search(r"\bpress\b", blob) and re.search(
        r"\b(enter|tab|esc|escape|ctrl|alt|shift|key)\b", blob
    ):
        return "press"
    if re.search(r"\b(type|enter|write)\b", blob):
        return "type"
    if re.search(r"\b(click|select)\b", blob):
        return "click"
    if re.search(r"\bpress\b", blob):
        return "click"
    if re.search(r"\bopen\b", blob):
        return "open_path"
    return None


def _step_blob(step: TaughtStep) -> str:
    return " ".join(
        [
            step.user_description or "",
            step.varies_note or "",
            " ".join(q.get("a") or "" for q in step.qa_history),
        ]
    )


def _plain_summary(verb, target, uses, success, assumptions) -> str:
    bits = []
    if verb and target:
        bits.append(f"I will {verb} {target}.")
    elif target:
        bits.append(f"The target is {target}, but I do not yet know the action.")
    elif verb:
        bits.append(f"I will {verb}.")
    else:
        bits.append("I do not yet know the action for this step.")
    if uses:
        bits.append(
            "This uses " + ", ".join(u["param"] for u in uses) + " from earlier steps."
        )
    else:
        bits.append("It does not use a value from an earlier step.")
    if success:
        bits.append(f"I will treat success as: {success}.")
    if assumptions:
        bits.append(f"I am assuming: {assumptions[0]}.")
    elif not success:
        bits.append("I still need a success check.")
    return " ".join(bits)


_REQUIRED_FILLED = ("target", "action", "success_check", "plain_summary")


def resolve_action(step: TaughtStep) -> dict | None:
    if step.action and step.action.get("action"):
        return step.action
    blob = " ".join(
        [
            step.user_description or "",
            step.varies_note or "",
            " ".join(q.get("a") or "" for q in step.qa_history),
        ]
    ).lower()
    target = _target_phrase(step)
    if re.search(r"\b(launch|open)\b.*\bnotepad\b", blob) or blob.strip() in ("open notepad", "launch notepad"):
        return {"action": "launch_app", "value": "notepad"}
    if re.search(r"\b(open|open_path|open the file)\b", blob) and (
        "{filename}" in (step.varies_note or "") + str(step.parameters) or "filename" in blob
    ):
        return {"action": "open_path", "value": "{filename}", "window_title": "Notepad"}
    if re.search(r"\b(ctrl\+s|hotkey|save)\b", blob) and "click" not in blob.split("save")[0][-12:]:
        if "save" in blob or "ctrl+s" in blob:
            act = {"action": "hotkey", "value": "ctrl+s", "keys": "ctrl+s", "window_title": "Notepad"}
            if any("filename" in str(p) for p in (step.parameters or [])) or "filename" in (step.varies_note or ""):
                act["verify_file"] = "{filename}"
            return act
    if re.search(r"\bpaste\b|\bctrl\s*\+\s*v\b", blob):
        return {"action": "paste", "target_desc": target}
    if re.search(r"\bcopy\b|\bctrl\s*\+\s*c\b", blob) and not re.search(r"\bcopy[_ ]?file\b", blob):
        return {"action": "copy", "target_desc": target}
    if re.search(r"\btype\b", blob):
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", step.user_description or "")
        value = quoted[0] if quoted else None
        for q in step.qa_history:
            more = re.findall(r"['\"]([^'\"]+)['\"]", q.get("a") or "")
            if more:
                value = more[-1]
        if not value and step.parameters:
            value = step.parameters[0]
        if not value:
            m = re.search(r"\btype\s+(\S+)", step.user_description or "", re.I)
            if m:
                value = m.group(1)
        if not value:
            return None
        return {
            "action": "type",
            "value": value,
            "text": value,
            "type_mode": "replace",
            "elem_name": "Text editor",
            "elem_type": "Document",
            "window_title": "Notepad",
            "target_desc": "the text editing area",
        }
    if re.search(r"\bclick\b", blob):
        action = {
            "action": "click",
            "target_desc": target,
            "window_title": "Notepad" if "notepad" in blob or "editor" in blob or "apollo" not in blob else None,
        }
        if step.anchor and (step.anchor.get("primary") or {}).get("name"):
            action["elem_name"] = step.anchor["primary"]["name"]
            action["elem_type"] = (step.anchor.get("primary") or {}).get("control_type")
        if "editor" in blob or "text" in blob:
            action["elem_name"] = "Text editor"
            action["elem_type"] = "Document"
            action["window_title"] = "Notepad"
        return action
    if re.search(r"\bhotkey\b|\bpress\b", blob):
        return {"action": "hotkey", "value": "ctrl+s", "keys": "ctrl+s"}
    verb = _closed_verb(blob)
    if verb in CLOSED_ACTIONS:
        return {"action": verb, "target_desc": target}
    return None


_UNDERSTANDING_KEYS = (
    "target",
    "action",
    "varies_each_run",
    "constants",
    "uses_from_earlier",
    "success_check",
    "assumptions",
    "plain_summary",
)


def _qa_blob(step: TaughtStep) -> str:
    return " ".join((q.get("a") or "") + " " + (q.get("q") or "") for q in (step.qa_history or []))


def _sentence_count(text: str) -> int:
    parts = [p for p in re.split(r"[.!?]+", (text or "").strip()) if p.strip()]
    return len(parts)


def explain_understanding(wf: TaughtWorkflow, step_id: str) -> dict:
    """Write back, in structured form, what this step is believed to do. No OS input."""
    from memory_graph import producer_of

    step = get_step(wf, step_id)
    action = resolve_action(step) or {}
    blob = _step_blob(step)
    verb = action.get("action")
    if verb not in CLOSED_ACTIONS:
        verb = _closed_verb(blob)
    if verb not in CLOSED_ACTIONS:
        verb = None
    target = action.get("target_desc") or action.get("elem_name") or _target_phrase(step)
    target = (target or "").strip() or None
    varies = list(step.parameters or [])
    if step.varies_note and not varies:
        varies = re.findall(r"\{[A-Za-z_][\w]*\}", step.varies_note)
    constants = []
    if not varies:
        constants.append("nothing in this step was marked as changing each run")
    if target:
        constants.append(f"the target is described as {target!r}")

    uses = []
    consumed = list(step.consumes or [])
    blob_l = blob.lower()
    if "email" in blob_l or "paste" in blob_l or "recipient" in blob_l:
        for earlier in wf.steps:
            if earlier.id == step.id:
                break
            for p in earlier.produces or []:
                if "email" in str(p).lower() and p not in consumed:
                    consumed.append(p)
    for p in consumed:
        prod = producer_of(wf, p)
        if prod:
            uses.append({"param": p, "from_step": prod.id})
            if p not in (step.consumes or []):
                step.consumes = list(step.consumes or []) + [p]
        else:
            uses.append({"param": p, "from_step": None})

    assumptions = []
    extra_q = None
    if verb is None:
        extra_q = "Which action is this: click, type, paste, or something else?"
        assumptions.append("I could not map this description to a closed-vocabulary action")
    if uses:
        assumptions.append("the earlier step that produces the consumed value has already run")
    if "notepad" in blob_l or (action.get("window_title") or "") == "Notepad":
        assumptions.append("Notepad is already open on the expected document")
    if "apollo" in blob_l or "linkedin" in blob_l:
        assumptions.append("the browser is already on the expected page")
    if (step.memory_note or "").strip():
        assumptions.append("follow the user's memory note for this step")
    if step.web_allowed:
        assumptions.append("I may look up a public page if this step needs a fact from the web")

    success = None
    for q in step.qa_history:
        if "succeed" in (q.get("q") or "").lower() and (q.get("a") or "").strip():
            success = q["a"].strip()
            break
    if not success:
        if extra_q:
            assumptions.append("success check is unknown until the action is known")
            success = None
        elif verb:
            extra_q = "How will I know this step succeeded?"
            assumptions.append("success check was not given — I am inferring it from the description")
            success = f"the action {verb} on {target} has an observable effect"
        else:
            extra_q = "Which action is this: click, type, paste, or something else?"
            assumptions.append("I could not map this description to a closed-vocabulary action")
            success = None
    if not assumptions:
        assumptions.append("the screen is already in the state this step expects")

    summary = _plain_summary(verb, target, uses, success, assumptions)
    understanding = {
        "target": target,
        "action": verb,
        "varies_each_run": varies,
        "constants": constants,
        "uses_from_earlier": uses,
        "success_check": success,
        "assumptions": assumptions,
        "plain_summary": summary,
    }
    if extra_q:
        understanding["clarifying_question"] = extra_q
        understanding["followup_question"] = extra_q
        step.status = "questioning"
        already = any((q.get("q") or "") == extra_q and not (q.get("a") or "").strip() for q in step.qa_history)
        if not already:
            step.qa_history.append({"q": extra_q, "a": "", "source": "chat", "kind": "clarify"})
    step.understanding = understanding
    if not extra_q and step.status in ("draft", "questioning"):
        step.status = "questioning"
    save_taught(wf)
    return understanding


def approve_understanding(wf: TaughtWorkflow, step_id: str) -> TaughtStep:
    step = get_step(wf, step_id)
    if (step.anchor or {}).get("conflict_unresolved"):
        raise TeachingError("unresolved witness conflict — pick which account you mean")
    if not step.understanding:
        raise TeachingError("no written understanding to approve")
    for k in _UNDERSTANDING_KEYS:
        if k not in step.understanding:
            raise TeachingError(f"understanding missing {k}")
    missing = [k for k in _REQUIRED_FILLED if step.understanding.get(k) in (None, "")]
    if missing:
        raise TeachingError(f"cannot approve understanding: {', '.join(missing)} unset")
    if not step.understanding.get("assumptions"):
        raise TeachingError("cannot approve understanding: assumptions is unset")
    step.status = "understood"
    if not step.action:
        step.action = resolve_action(step)
    save_taught(wf)
    return step


def reject_understanding(wf: TaughtWorkflow, step_id: str, correction: str) -> TaughtStep:
    step = get_step(wf, step_id)
    step.qa_history.append({
        "q": "Your understanding was not quite right. What should change?",
        "a": correction,
        "source": "chat",
    })
    step.understanding = None
    step.status = "questioning"
    save_taught(wf)
    return step


def approve_behaviour(wf: TaughtWorkflow, step_id: str) -> TaughtStep:
    step = get_step(wf, step_id)
    if step.status != "demonstrated":
        raise TeachingError("a successful demo is required before approving behaviour")
    if not (step.demo or {}).get("ok"):
        raise TeachingError("demo did not succeed")
    action = resolve_action(step)
    if action is None:
        raise TeachingError("cannot resolve to exactly one action")
    step.action = action
    step.status = "approved"
    save_taught(wf)
    return step


def approve_step(wf: TaughtWorkflow, step_id: str, skip_rehearsal: bool = False) -> TaughtStep:
    step = get_step(wf, step_id)
    if skip_rehearsal:
        if not step.understanding:
            explain_understanding(wf, step_id)
        step.status = "demonstrated"
        step.demo = {
            "ok": True,
            "reason": "demo skipped by caller",
            "mode": "skip",
            "os_input_calls": 0,
        }
        save_taught(wf)
        return approve_behaviour(wf, step_id)
    if step.status not in ("demonstrated", "approved"):
        raise TeachingError("demo this step (or skip rehearsal) before approving behaviour")
    action = resolve_action(step)
    if action is None:
        step.status = "questioning"
        step.qa_history.append({
            "q": "Which closed action is this — click, type, hotkey, or launch_app?",
            "a": "",
            "source": "chat",
        })
        save_taught(wf)
        raise TeachingError("cannot resolve to exactly one action; asked one more question")
    step.action = action
    step.status = "approved"
    save_taught(wf)
    return step


def rehearse_step(wf: TaughtWorkflow, step_id: str, test_values: dict | None = None) -> dict:
    from teach_compile import rehearse_taught_step

    return rehearse_taught_step(wf, step_id, test_values=test_values)


def prepare_state(wf: TaughtWorkflow, step_id: str, mode: str, test_values: dict | None = None) -> dict:
    from teach_compile import prepare_state as _prep

    return _prep(wf, step_id, mode, test_values=test_values)


def demo_step(wf: TaughtWorkflow, step_id: str, test_values: dict | None = None, mode: str = "manual") -> dict:
    from teach_compile import demo_taught_step

    return demo_taught_step(wf, step_id, test_values=test_values, mode=mode)


def reflect_on_demo(wf: TaughtWorkflow, step_id: str) -> dict:
    step = get_step(wf, step_id)
    if not step.demo:
        raise TeachingError("no demo to reflect on")
    understood = step.understanding or {}
    wanted = (understood.get("success_check") or "").lower()
    observed = str((step.demo or {}).get("observed") or step.demo.get("reason") or "")
    obs_l = observed.lower()
    differences = []
    if wanted and "dialog" in wanted and "dialog" not in obs_l:
        differences.append(f"success check expected a dialog; observed {observed!r}")
    if wanted and "dialog" not in wanted and "dialog" in obs_l:
        differences.append("a dialog appeared that the success check did not mention")
    if wanted and observed and wanted not in obs_l and not any(
        tok in obs_l for tok in wanted.split() if len(tok) > 4
    ):
        differences.append(f"observed {observed!r} does not match success check {wanted!r}")
    matches = not differences
    reflection = {
        "what_i_did": (step.action or {}).get("action") or "unknown",
        "what_i_observed": observed,
        "matches_understanding": matches,
        "differences": differences,
        "confidence_note": "matched" if matches else "demo and written understanding disagree",
    }
    step.reflection = reflection
    save_taught(wf)
    return reflection
