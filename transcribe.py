"""
Transcribe a recorded workflow (plan.json / library step list) into an
editable harness transcript.

Produces:
  transcript.txt  — human-readable, editable lines + INPUTS header
  transcript.json — HarnessStep dicts + declared input placeholders

Does NOT execute anything. Kind is a first guess; the harness router still
confirms against the live screen at run time.
"""

import os
import re
import json

from harness_schema import HarnessStep, step_to_dict, STEP_KINDS


# Words that hint a typed value should be parameterized
_VARIABLE_FIELD_HINTS = (
    "email", "e-mail", "recipient", "to", "cc", "bcc",
    "password", "passcode", "username", "user name", "login",
    "phone", "mobile", "ssn", "card", "cvv", "otp", "code",
    "first name", "last name", "full name", "address", "company",
)


def load_recorded_steps(source):
    """Load a recorded step LIST from a path, library name, or already-a-list."""
    if isinstance(source, list):
        return source

    if not isinstance(source, str):
        raise TypeError(f"source must be list/str, got {type(source)}")

    # File path?
    if source.endswith(".json") and os.path.isfile(source):
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        raise ValueError(f"{source} is not a recorded step list")

    # Library recorded workflow name
    try:
        from library import load_workflow
        steps = load_workflow(source)
        if isinstance(steps, list):
            return steps
    except Exception:
        pass

    # Bare path without existing check above
    if os.path.isfile(source):
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data

    raise FileNotFoundError(
        f"could not load recorded steps from {source!r} "
        f"(tried path and library name)"
    )


def _slug(name):
    """Turn a field label into a {placeholder} stem."""
    stem = (name or "value").strip().lower()
    stem = re.sub(r"[^\w\s-]", "", stem)
    stem = re.sub(r"[\s-]+", "_", stem).strip("_")
    return stem or "value"


def _secret_placeholder(text):
    """'[SECRET: Password]' -> 'password'."""
    m = re.match(r"\[SECRET:\s*(.+?)\]", (text or "").strip(), re.I)
    if m:
        return _slug(m.group(1))
    return None


def _looks_browser(step):
    """First-guess: does this recorded step belong in the browser engine?"""
    wt = (step.get("window_title") or "").lower()
    name = (step.get("elem_name") or "").lower()
    instr = (step.get("instruction") or "").lower()
    etype = (step.get("elem_type") or "")
    url = (step.get("url") or "").lower()
    blob = " ".join([wt, name, instr, url])

    if url.startswith("http://") or url.startswith("https://"):
        return True
    if "http://" in blob or "https://" in blob:
        return True
    if any(b in blob for b in ("chrome", "google chrome", "msedge", "firefox", "chromium")):
        return True
    if "switch to tab" in instr:
        return True
    if etype == "Hyperlink":
        return True
    if etype == "TabItem" and ("|" in name or "http" in name or "www." in name):
        return True
    for site in ("linkedin", "gmail", "google", "github", "youtube",
                 "claude", "chatgpt", "jobright", "notion"):
        if site in blob:
            return True
    return False


def _infer_kind(step):
    return "browser" if _looks_browser(step) else "native"


def _field_suggests_variable(field_name):
    n = (field_name or "").lower()
    for h in _VARIABLE_FIELD_HINTS:
        # word-boundary match so "to" does not hit "Text editor"
        if re.search(r"\b" + re.escape(h) + r"\b", n):
            return True
    return False


def _email_like(text):
    return bool(re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", text or ""))


def _build_action_and_inputs(step, inputs_found):
    """Map a recorded step to a closed-vocab action + collect placeholders.

    Returns (action_dict|None, placeholder_names_used_in_this_step, description).
    """
    act = (step.get("action") or "").lower()
    name = (step.get("elem_name") or "").strip()
    etype = (step.get("elem_type") or "").strip()
    instruction = (step.get("instruction") or "").strip()
    used = []

    if act == "type":
        text = step.get("text") or ""
        ph = _secret_placeholder(text)
        if ph:
            used.append(ph)
            inputs_found.add(ph)
            desc = f'Type {{{ph}}} into the "{name}" field' if name else (
                instruction or f"Type {{{ph}}}"
            )
            return (
                {"action": "type", "text": "{" + ph + "}", "type_mode": "replace",
                 "why": f"fill {name or ph}"},
                used,
                desc,
            )
        # Non-secret but field looks variable, or value looks like an email
        if name and (_field_suggests_variable(name) or _email_like(text)):
            ph = _slug(name)
            if ph in ("to", "cc", "bcc"):
                ph = "recipient_email" if ph == "to" else f"{ph}_email"
            if "email" in (name or "").lower() and "email" not in ph:
                ph = "email" if ph in ("email", "e_mail") else ph
            if _email_like(text) and "email" not in ph:
                ph = "recipient_email"
            used.append(ph)
            inputs_found.add(ph)
            desc = f'Type {{{ph}}} into the "{name}" field'
            return (
                {"action": "type", "text": "{" + ph + "}", "type_mode": "replace",
                 "why": f"fill {name}"},
                used,
                desc,
            )
        desc = instruction or f'Type "{text}"'
        return (
            {"action": "type", "text": text, "type_mode": "replace",
             "why": "type recorded text"},
            used,
            desc,
        )

    if act == "click":
        desc = instruction or (
            f'Click "{name}"' if name else f"Click something ({etype or 'unknown'})"
        )
        # No stable id from a recording — harness will locate via target_name
        # or re-reason. Keep a click action without id.
        action = {"action": "click", "why": desc[:80]}
        return action, used, desc

    if act in ("press", "key", "keypress"):
        key = step.get("key") or step.get("text") or "enter"
        desc = instruction or f"Press {key}"
        return {"action": "press", "key": str(key).lower(), "why": desc[:80]}, used, desc

    if act == "scroll":
        direction = step.get("direction") or "down"
        desc = instruction or f"Scroll {direction}"
        return {"action": "scroll", "direction": direction, "why": desc[:80]}, used, desc

    if act == "navigate":
        url = step.get("url") or ""
        desc = instruction or f"Navigate to {url}"
        return {"action": "navigate", "url": url, "why": desc[:80]}, used, desc

    if act in ("hotkey", "shortcut"):
        keys = step.get("keys") or step.get("text") or ""
        desc = instruction or f"Hotkey {keys}"
        return {"action": "hotkey", "keys": keys, "why": desc[:80]}, used, desc

    # Unknown recorded action — fall back to a reason step description
    desc = instruction or f"Perform recorded action {act!r}"
    return None, used, desc


def recorded_to_harness_step(step, inputs_found):
    """Convert one recorded step dict into a HarnessStep (first-guess kind)."""
    kind = _infer_kind(step)
    action, used, desc = _build_action_and_inputs(step, inputs_found)
    name = (step.get("elem_name") or "").strip() or None
    etype = (step.get("elem_type") or "").strip() or None

    if action is None:
        # Ambiguous — express as a reason sub-goal so the agent loop can handle it
        return HarnessStep(
            kind="reason",
            description=desc,
            goal=desc,
            inputs=list(used),
        )

    return HarnessStep(
        kind=kind,
        description=desc,
        action=action,
        target_name=name,
        target_type=etype,
        inputs=list(used),
    )


def transcribe(source, out_txt="transcript.txt", out_json="transcript.json"):
    """Convert a recorded workflow into transcript.txt + transcript.json.

    Returns (harness_steps, inputs_list).
    """
    recorded = load_recorded_steps(source)
    inputs_found = set()
    harness_steps = []
    for raw in recorded:
        if not isinstance(raw, dict):
            continue
        hs = recorded_to_harness_step(raw, inputs_found)
        # Soft-validate; reason/browser/native must pass
        try:
            hs.validate()
        except AssertionError:
            # Ensure concrete steps have at least a target or action
            if hs.kind != "reason" and not hs.action and not hs.target_name:
                hs.kind = "reason"
                hs.goal = hs.description or "continue the workflow"
                hs.validate()
            else:
                raise
        harness_steps.append(hs)

    inputs_list = sorted(inputs_found)

    # --- transcript.txt (editable) ---
    lines = [
        "# MimicAgent editable transcript",
        "# Edit freely. kind must be one of: " + ", ".join(STEP_KINDS),
        "# The harness router still confirms kind against the live screen at run time.",
        "#",
        "# INPUTS (fill these when running):",
    ]
    if inputs_list:
        for name in inputs_list:
            lines.append(f"#   - {{{name}}}")
    else:
        lines.append("#   (none detected)")
    lines.append("#")
    lines.append("")

    for i, hs in enumerate(harness_steps, 1):
        lines.append(f"{i}. [{hs.kind}] {hs.description}")

    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # --- transcript.json (structured) ---
    payload = {
        "source": source if isinstance(source, str) else "(list)",
        "inputs": inputs_list,
        "steps": [step_to_dict(hs) for hs in harness_steps],
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"[transcribe] {len(harness_steps)} steps -> {out_txt}, {out_json}")
    if inputs_list:
        print(f"[transcribe] INPUTS: {', '.join('{'+n+'}' for n in inputs_list)}")
    else:
        print("[transcribe] INPUTS: (none)")
    return harness_steps, inputs_list


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "notepad_greeting"
    out_txt = sys.argv[2] if len(sys.argv) > 2 else "transcript.txt"
    out_json = sys.argv[3] if len(sys.argv) > 3 else "transcript.json"
    print(f"=== transcribe {src!r} ===")
    steps, inputs = transcribe(src, out_txt=out_txt, out_json=out_json)
    print("\n--- transcript.txt preview ---")
    with open(out_txt, "r", encoding="utf-8") as f:
        print(f.read())
