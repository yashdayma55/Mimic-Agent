"""Ask vision — read-only debugging chat on the step card."""

from __future__ import annotations

import io
import os
import re
import time
from datetime import datetime, timezone

from teaching import TaughtWorkflow, TeachingError, get_step, save_taught
from workflow_folder import workflow_dir

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _load_api_key() -> str:
    for path in ("my_key.txt", os.path.join(os.path.dirname(__file__), "my_key.txt")):
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                key = (f.read() or "").strip()
                if key:
                    return key
    raise TeachingError("no vision API key in my_key.txt")


def extract_facts_from_text(text: str) -> dict:
    """Pull structured facts (emails, etc.) from vision answers or user notes."""
    emails = _EMAIL_RE.findall(text or "")
    seen: set[str] = set()
    uniq: list[str] = []
    for e in emails:
        low = e.lower()
        if low not in seen:
            seen.add(low)
            uniq.append(e)
    return {
        "emails": uniq,
        "primary_email": uniq[0] if uniq else None,
    }


def normalize_memory_param(remember: str, *, default_email: bool = False) -> str | None:
    """Pick a {param} from the remember text; normalize email aliases."""
    names = _PARAM_RE.findall(remember or "")
    if names:
        low = names[0].lower()
        if low in ("recipient_email", "email", "emails", "work_email"):
            return "{recipient_email}"
        return "{" + low + "}"
    if default_email:
        return "{recipient_email}"
    return None


def _canonicalize_remember_text(remember: str) -> str:
    def _repl(m: re.Match) -> str:
        low = m.group(1).lower()
        if low in ("recipient_email", "email", "emails", "work_email"):
            return "{recipient_email}"
        return "{" + low + "}"

    return _PARAM_RE.sub(_repl, remember or "")


def _chat_transcript(history: list[dict], limit: int = 8) -> str:
    lines: list[str] = []
    for entry in (history or [])[-limit:]:
        q = (entry.get("q") or "").strip()
        a = (entry.get("a") or "").strip()
        if q:
            lines.append(f"User: {q}")
        if a:
            lines.append(f"Vision: {a}")
    return "\n".join(lines)


def _window_area(win) -> int:
    from ui_runner import _rect

    rect = _rect(win)
    if not rect:
        return 0
    return max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])


def _iter_non_own_windows():
    from app_ui_guard import is_own_window
    from ui_runner import _all_windows, _proc_name, _title

    for w in _all_windows():
        try:
            if not w.is_visible():
                continue
            title = _title(w)
            if not title or is_own_window(title):
                continue
            yield w, title, (_proc_name(w) or "")
        except Exception:
            continue


def pick_target_window(wf: TaughtWorkflow, step_id: str):
    """Best non-MimicAgent window for a vision grab. Never returns our own UI."""
    from teach_compile import window_hint_from_step
    from ui_runner import find_any_window

    step = get_step(wf, step_id)
    hints: list[str] = []
    hint = window_hint_from_step(step)
    if hint:
        hints.append(hint)
    blob = " ".join([
        step.user_description or "",
        wf.context or "",
    ]).lower()
    if any(w in blob for w in ("linkedin", "apollo", "email")):
        hints.extend(["LinkedIn", "Google Chrome"])
    hints.extend(["LinkedIn", "Google Chrome", "Microsoft Edge"])
    seen: set[str] = set()
    for h in hints:
        if not h or h.lower() in seen:
            continue
        seen.add(h.lower())
        win, title = find_any_window(h)
        if win is not None:
            return win, title

    browsers = []
    others = []
    for win, title, proc in _iter_non_own_windows():
        if proc in ("chrome.exe", "msedge.exe", "firefox.exe"):
            browsers.append((win, title, _window_area(win)))
        else:
            others.append((win, title, _window_area(win)))
    browsers.sort(key=lambda x: x[2], reverse=True)
    others.sort(key=lambda x: x[2], reverse=True)
    if browsers:
        return browsers[0][0], browsers[0][1]
    if others:
        return others[0][0], others[0][1]
    return None, None


def focus_target_for_vision(wf: TaughtWorkflow, step_id: str) -> dict:
    """Bring the target app forward. Does not fail the ask if focus is imperfect."""
    from ui_runner import focus_window

    win, title = pick_target_window(wf, step_id)
    if win is None:
        return {"ok": False, "title": None, "reason": "no target window found"}
    focused = focus_window(win)
    time.sleep(0.2)
    return {"ok": bool(focused), "title": title, "reason": f"focused {title!r}"}


def _crop_box_for_window(win) -> tuple[int, int, int, int] | None:
    from show_capture import _monitor_at
    from ui_runner import _center, _rect

    rect = _rect(win)
    if not rect:
        return None
    cx, cy = _center(rect)
    return _monitor_at(int(cx), int(cy))


def capture_vision_frame(
    wf_name: str,
    step_id: str,
    *,
    wf: TaughtWorkflow | None = None,
    synthetic_bytes: bytes | None = None,
) -> tuple[str, bytes]:
    """Grab the target app's monitor. Never refuses because MimicAgent is in front."""
    folder = os.path.join(workflow_dir(wf_name), "vision_chat")
    os.makedirs(folder, exist_ok=True)
    n = len(os.listdir(folder)) + 1
    rel = f"vision_chat/{step_id}_{n}.png"
    abs_path = os.path.join(workflow_dir(wf_name), rel)

    if synthetic_bytes is not None:
        with open(abs_path, "wb") as f:
            f.write(synthetic_bytes)
        return rel, synthetic_bytes

    from show_capture import _grab_screen, _save_image, _to_image_box, _virtual_screen

    box = None
    if wf is not None:
        focus_target_for_vision(wf, step_id)
        win, _title = pick_target_window(wf, step_id)
        if win is not None:
            box = _crop_box_for_window(win)
    if box is None:
        vx, vy, vw, vh = _virtual_screen()
        box = (vx, vy, vx + vw, vy + vh)

    img, origin = _grab_screen()
    crop = img.crop(_to_image_box(box, origin, img.size))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    raw = buf.getvalue()
    _save_image(crop, abs_path)
    return rel, raw


def _read_frame_bytes(wf_name: str, frame_path: str) -> bytes:
    abs_path = os.path.join(workflow_dir(wf_name), (frame_path or "").replace("/", os.sep))
    if not os.path.isfile(abs_path):
        raise TeachingError("no frame available for a reply — ask vision first")
    with open(abs_path, "rb") as f:
        return f.read()


def _append_chat_entry(step, entry: dict) -> dict:
    facts = extract_facts_from_text(
        " ".join([(entry.get("q") or ""), (entry.get("a") or "")])
    )
    entry = dict(entry)
    entry["facts"] = facts
    history = list(step.vision_chat or [])
    history.append(entry)
    step.vision_chat = history
    return entry


def ask_vision(
    wf: TaughtWorkflow,
    step_id: str,
    question: str,
    *,
    synthetic_bytes: bytes | None = None,
    synthetic_answer: str | None = None,
) -> dict:
    """Capture frame + ask vision. Never executes step actions."""
    q = (question or "").strip()
    if not q:
        raise TeachingError("type a question for vision")
    step = get_step(wf, step_id)
    rel, raw = capture_vision_frame(
        wf.name, step_id, wf=wf, synthetic_bytes=synthetic_bytes,
    )
    if synthetic_answer is not None:
        answer = synthetic_answer
    else:
        from vision_api import ask_vision_freeform

        answer = ask_vision_freeform(raw, _load_api_key(), q)
    entry = _append_chat_entry(step, {
        "q": q,
        "a": answer,
        "frame_path": rel,
        "kind": "ask",
        "at": datetime.now(timezone.utc).isoformat(),
    })
    save_taught(wf)
    return {"ok": True, "entry": entry, "step": step.to_dict()}


def reply_vision(
    wf: TaughtWorkflow,
    step_id: str,
    reply: str,
    *,
    synthetic_answer: str | None = None,
    regrab: bool = False,
    synthetic_bytes: bytes | None = None,
) -> dict:
    """Follow-up prompt to vision, using the same frame by default."""
    text = (reply or "").strip()
    if not text:
        raise TeachingError("type a follow-up for vision")
    step = get_step(wf, step_id)
    history = list(step.vision_chat or [])
    if not history:
        raise TeachingError("ask vision first, then reply")

    last = history[-1]
    if regrab or synthetic_bytes is not None:
        rel, raw = capture_vision_frame(
            wf.name, step_id, wf=wf, synthetic_bytes=synthetic_bytes,
        )
    else:
        rel = last.get("frame_path") or ""
        raw = _read_frame_bytes(wf.name, rel)

    transcript = _chat_transcript(history)
    prompt = (
        "You already answered about this screenshot. Continue the conversation.\n\n"
        f"{transcript}\n\n"
        f"User follow-up: {text}\n\n"
        "Answer clearly. If the user asks for a specific value (email, name, button), "
        "quote it exactly. If they ask what to remember for automation, say what is "
        "dynamic vs fixed."
    )
    if synthetic_answer is not None:
        answer = synthetic_answer
    else:
        from vision_api import ask_vision_freeform

        answer = ask_vision_freeform(raw, _load_api_key(), prompt)
    entry = _append_chat_entry(step, {
        "q": text,
        "a": answer,
        "frame_path": rel,
        "kind": "reply",
        "at": datetime.now(timezone.utc).isoformat(),
    })
    save_taught(wf)
    return {"ok": True, "entry": entry, "step": step.to_dict()}


def apply_vision_chat_to_step(
    wf: TaughtWorkflow,
    step_id: str,
    remember_prompt: str = "",
) -> dict:
    """Fill the step card from vision chat + the user's remember instructions."""
    from teach_loop import explain_understanding

    step = get_step(wf, step_id)
    history = list(step.vision_chat or [])
    if not history:
        raise TeachingError("ask vision first before adding it as a step")

    remember = (remember_prompt or "").strip()
    blob = "\n".join(
        [(e.get("q") or "") + "\n" + (e.get("a") or "") for e in history]
        + [remember]
    )
    facts = extract_facts_from_text(blob)
    email = facts.get("primary_email")
    param = normalize_memory_param(remember, default_email=bool(email))

    first_q = (history[0].get("q") or "").strip()
    remember_looks_like_memory = bool(
        remember
        and re.search(r"remember|chang(e|es|ing)|per (person|profile|run)|next step", remember, re.I)
    )
    if email:
        desc = "Read the visible email address from the Apollo panel"
    elif remember and not remember_looks_like_memory:
        desc = remember.split("\n")[0].strip()
    else:
        desc = first_q or "Extract information visible on screen with vision"

    if email or param == "{recipient_email}":
        param = param or "{recipient_email}"
        step.produces = list(dict.fromkeys(list(step.produces or []) + [param]))
        if param not in (step.parameters or []):
            step.parameters = list(step.parameters or []) + [param]
        step.varies_note = (step.varies_note or "").strip() or (
            f"{param} — changes each profile / run"
        )
        mem_bits = []
        if remember:
            mem_bits.append(_canonicalize_remember_text(remember))
        mem_bits.append(
            f"Remember the visible email as {param}; it is dynamic and changes each run."
        )
        if email:
            mem_bits.append(f"Example from this teaching: {email}.")
        step.memory_note = " ".join(mem_bits)
        action_verb = "prompt"
        target = "the visible email address on screen"
        success = f"the email address is known as {param}"
        instruction = (
            "Look at the Apollo / contact sidebar Emails section and read the visible "
            "email address. Do not invent one."
        )
    else:
        mem_bits = []
        if remember:
            mem_bits.append(remember)
        mem_bits.append("Remember what vision found on this screen for this step.")
        step.memory_note = " ".join(mem_bits)
        action_verb = "prompt"
        target = "what vision can see on screen"
        success = "vision confirms the expected information is visible"
        instruction = first_q or remember or "Describe what you see that this step needs."
        param = None

    step.user_description = desc[:240]
    step.method = "prompt"
    step.prompt_instruction = instruction
    step.action = {
        "action": action_verb,
        "value": instruction,
        "target_desc": target[:80],
    }

    learned = dict(step.learned or {})
    learned["vision_extract"] = {
        "facts": facts,
        "remember_prompt": remember,
        "sample_email": email,
        "dynamic": bool(email),
        "at": datetime.now(timezone.utc).isoformat(),
        "chat_turns": len(history),
    }
    if email:
        learned["summary"] = f"Vision extracted email {email} (dynamic each run)."
    step.learned = learned

    understanding = dict(step.understanding or {})
    understanding.update({
        "target": target,
        "action": action_verb,
        "success_check": success,
        "success_source": "user",
        "varies_each_run": list(step.parameters or []),
        "plain_summary": (
            f"I will use vision to {desc}. "
            + (f"I produce {param} which changes each run. " if email else "")
            + f"I remember: {step.memory_note[:180]}"
        ),
        "assumptions": [
            "the target app is already on the expected screen",
            "vision reads what is visible; values may change each profile",
        ],
    })
    step.understanding = understanding
    save_taught(wf)
    try:
        explain_understanding(wf, step_id)
    except Exception:
        pass
    step = get_step(wf, step_id)
    return {
        "ok": True,
        "facts": facts,
        "filled": {
            "user_description": step.user_description,
            "memory_note": step.memory_note,
            "produces": list(step.produces or []),
            "parameters": list(step.parameters or []),
            "method": step.method,
            "prompt_instruction": step.prompt_instruction,
        },
        "step": step.to_dict(),
    }


def execute_vision_prompt_step(
    wf: TaughtWorkflow | None,
    step,
    *,
    synthetic_bytes: bytes | None = None,
    synthetic_answer: str | None = None,
    workflow_name: str | None = None,
    step_id: str | None = None,
) -> dict:
    """Run a prompt/vision step: read the screen and return produced values.

    Used by Demo / runner. Does not click — observational extract only.
    """
    from ui_runner import StepResult

    name = workflow_name or (wf.name if wf is not None else "")
    sid = step_id or getattr(step, "id", None) or "s1"
    if isinstance(step, dict):
        instruction = (
            step.get("prompt_instruction")
            or step.get("value")
            or step.get("instruction")
            or ""
        ).strip()
        produces = list(step.get("produces") or [])
        memory = (step.get("memory_note") or "").strip()
    else:
        instruction = (
            getattr(step, "prompt_instruction", "")
            or ((getattr(step, "action", None) or {}) or {}).get("value")
            or getattr(step, "user_description", "")
            or ""
        ).strip()
        produces = list(getattr(step, "produces", None) or [])
        memory = (getattr(step, "memory_note", "") or "").strip()

    if not instruction:
        instruction = "Read the visible email address from the Apollo Emails section."

    clickish = bool(
        re.match(
            r"^\s*(click|press|tap|open|select|hit|hover|double[-\s]?click)\b",
            instruction,
            re.I,
        )
    )
    wants_email = False
    if not clickish:
        wants_email = any("email" in str(p).lower() for p in produces) or bool(
            re.search(r"email|recipient", instruction + " " + memory, re.I)
        )

    # Case / action prompts: click the named control via Set-of-Mark.
    if clickish and not wants_email:
        err = None
        match = None
        try:
            from som_click import ground_and_click

            match = ground_and_click(instruction, require_confirm=False)
        except Exception as e:
            err = str(e)
        result = StepResult(ok=False, reason="")
        if match:
            result.ok = True
            result.reason = f"prompt clicked {match.get('name') or 'target'}"
            result.value_after = match.get("name") or instruction[:120]
            if match.get("cx") is not None and match.get("cy") is not None:
                result.click_xy = (int(match["cx"]), int(match["cy"]))
        else:
            result.reason = err or "prompt click did not find a target"
            result.value_after = instruction[:120]
        return {
            "ok": result.ok,
            "result": result,
            "answer": result.reason,
            "facts": {},
            "produced": {},
            "frame_path": None,
        }

    if wf is not None:
        rel, raw = capture_vision_frame(name, sid, wf=wf, synthetic_bytes=synthetic_bytes)
    else:
        rel, raw = capture_vision_frame(name, sid, synthetic_bytes=synthetic_bytes)

    ask = instruction
    if wants_email:
        ask = (
            f"{instruction}\n\n"
            "Reply with the exact visible email address if present. "
            "If none is visible, say so clearly."
        )
    if synthetic_answer is not None:
        answer = synthetic_answer
    else:
        from vision_api import ask_vision_freeform

        answer = ask_vision_freeform(raw, _load_api_key(), ask)

    facts = extract_facts_from_text(answer)
    email = facts.get("primary_email")
    produced: dict = {}
    param = "{recipient_email}"
    for p in produces:
        if "email" in str(p).lower():
            param = str(p)
            break
    if email and wants_email:
        produced[param] = email

    result = StepResult(ok=False, reason="")
    if wants_email:
        if email:
            result.ok = True
            result.reason = f"vision extracted {param}={email}"
            result.value_after = email
        else:
            result.ok = False
            result.reason = "vision did not find a visible email on screen"
            result.value_after = (answer or "")[:240]
    else:
        result.ok = True
        result.reason = "vision prompt answered"
        result.value_after = (answer or "")[:240]

    return {
        "ok": result.ok,
        "result": result,
        "answer": answer,
        "facts": facts,
        "produced": produced,
        "frame_path": rel,
    }


def remove_vision_chat_entry(wf: TaughtWorkflow, step_id: str, index: int) -> dict:
    step = get_step(wf, step_id)
    history = list(step.vision_chat or [])
    if index < 0 or index >= len(history):
        raise TeachingError("vision chat entry not found")
    removed = history.pop(index)
    step.vision_chat = history
    save_taught(wf)
    frame = (removed or {}).get("frame_path")
    still_used = any((e.get("frame_path") or "") == frame for e in step.vision_chat or [])
    if frame and not still_used:
        abs_path = os.path.join(workflow_dir(wf.name), frame.replace("/", os.sep))
        if os.path.isfile(abs_path):
            try:
                os.remove(abs_path)
            except OSError:
                pass
    return {"ok": True, "removed": removed, "step": step.to_dict()}
