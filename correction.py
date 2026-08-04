"""
MimicAgent Phase 5 - natural-language correction.

Pipeline: get_human_decision -> interpret (local OR api) -> validate -> apply + echo.
The interpreter is swappable (like Phase 4 vision): local Ollama or a hosted API,
reusing my_key.txt and the provider detection from vision_api.py.
"""

import json
import ollama
import requests
from vision_api import detect_provider


ALLOWED_EDITS = {"change_text", "skip", "retarget", "insert_before", "unknown"}

# which interpreter to use: "local" (ollama, private/offline) or "api" (fast/reliable)
INTERPRET_PROVIDER = "api"


# =====================================================================
# 1. THE THIRD EXIT
# =====================================================================
def get_human_decision():
    raw = input("  [Enter]=approve | type a correction | 'skip'=reject : ").strip()
    if raw == "":
        return ("approve", None)
    if raw.lower() in ("skip", "reject", "no", "esc"):
        return ("reject", None)
    return ("correct", raw)


# =====================================================================
# 2. INTERPRET (shared prompt, two backends)
# =====================================================================
INTERPRET_PROMPT = """You convert a user's plain-language correction about a UI automation step into ONE structured edit. Output JSON only.

Edit types:
- "change_text": user wants DIFFERENT TEXT typed. Signals: "type X instead", "use X", "it should say X", "change it to X".
- "retarget": the TARGET ELEMENT is wrong/named differently. Signals: "the field is called X", "click X instead", "wrong button, it's X".
- "skip": user wants to skip. Signals: "skip", "don't do this", "leave it".
- "unknown": the correction is unrelated or makes no sense.

Examples:
Step: {{"action":"type","elem_name":"Email","text":"a@b.com"}}
Correction: "type my gmail instead: yash@gmail.com"
{{"edit":"change_text","new_text":"yash@gmail.com"}}

Step: {{"action":"type","elem_name":"Email","text":"a@b.com"}}
Correction: "the field is actually called Username not Email"
{{"edit":"retarget","new_name":"Username"}}

Step: {{"action":"click","elem_name":"Submit"}}
Correction: "skip this one"
{{"edit":"skip"}}

Step: {{"action":"type","elem_name":"Email","text":"a@b.com"}}
Correction: "make me a sandwich"
{{"edit":"unknown"}}

Now do this one. Output JSON only:
Step: {step_json}
Correction: "{sentence}"
"""


def _load_key():
    try:
        with open("my_key.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _parse_edit(raw):
    try:
        return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    except Exception:
        return {"edit": "unknown", "_error": "could not parse"}


def interpret_local(step, sentence, model="qwen3:1.7b"):
    prompt = INTERPRET_PROMPT.format(step_json=json.dumps(step), sentence=sentence)
    try:
        resp = ollama.chat(model=model, think=False, keep_alive="30m",
                           messages=[{"role": "user", "content": prompt}])
        return _parse_edit(resp["message"]["content"])
    except Exception as e:
        return {"edit": "unknown", "_error": str(e)}


def interpret_api(step, sentence):
    key = _load_key()
    provider = detect_provider(key)
    prompt = INTERPRET_PROMPT.format(step_json=json.dumps(step), sentence=sentence)
    try:
        if provider == "claude":
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-sonnet-4-5", "max_tokens": 200,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30)
            r.raise_for_status()
            raw = r.json()["content"][0]["text"]
        elif provider == "openai":
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini", "max_tokens": 200,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30)
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
        elif provider == "gemini":
            url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                   f"gemini-1.5-flash:generateContent?key={key}")
            r = requests.post(url, headers={"Content-Type": "application/json"},
                              json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
            r.raise_for_status()
            raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        else:
            return {"edit": "unknown", "_error": "no valid API key"}
        return _parse_edit(raw)
    except Exception as e:
        return {"edit": "unknown", "_error": str(e)}


def interpret_correction(step, sentence):
    """Route to whichever interpreter the switch selects."""
    if INTERPRET_PROVIDER == "api":
        return interpret_api(step, sentence)
    return interpret_local(step, sentence)


# =====================================================================
# 3. VALIDATE
# =====================================================================
def validate_edit(edit):
    kind = edit.get("edit")
    if kind not in ALLOWED_EDITS:
        return False, f"unknown edit type: {kind}"
    if kind == "change_text" and not edit.get("new_text"):
        return False, "change_text needs new_text"
    if kind == "retarget" and not edit.get("new_name"):
        return False, "retarget needs new_name"
    if kind == "insert_before" and not edit.get("action"):
        return False, "insert_before needs an action"
    if kind == "unknown":
        return False, "could not understand the correction"
    return True, "ok"


# =====================================================================
# 4. APPLY + ECHO
# =====================================================================
def apply_edit(step, edit):
    kind = edit["edit"]
    if kind == "change_text":
        step["text"] = edit["new_text"]
        step["secret"] = False
        return f"ok - I'll type \"{edit['new_text']}\" instead"
    if kind == "skip":
        step["_skip"] = True
        return "ok - I'll skip this step"
    if kind == "retarget":
        step["elem_name"] = edit["new_name"]
        return f"ok - I'll look for \"{edit['new_name']}\" instead"
    if kind == "insert_before":
        return f"ok - first I'll {edit['action']} \"{edit.get('elem_name') or edit.get('text')}\""
    return "sorry, I did not understand that - could you rephrase?"


def handle_correction(step, sentence):
    """interpret -> validate -> (apply + echo) or ask again. Returns (edit, echo)."""
    edit = interpret_correction(step, sentence)
    ok, reason = validate_edit(edit)
    if not ok:
        return None, f"hmm, {reason}. Please rephrase your correction."
    echo = apply_edit(step, edit)
    return edit, echo


# =====================================================================
# standalone test
# =====================================================================
if __name__ == "__main__":
    print(f"(interpreter: {INTERPRET_PROVIDER})")
    step = {"action": "type", "elem_name": "Email", "text": "old@example.com"}
    tests = [
        "type my gmail instead: yash@gmail.com",
        "skip this step",
        "the field is actually called Username not Email",
        "make me a sandwich",
    ]
    for sentence in tests:
        s = dict(step)
        edit, echo = handle_correction(s, sentence)
        print(f"\nCORRECTION: {sentence}")
        print(f"  EDIT: {edit}")
        print(f"  ECHO: {echo}")
        if edit and edit.get("edit") not in ("skip",):
            print(f"  STEP NOW: {s}")