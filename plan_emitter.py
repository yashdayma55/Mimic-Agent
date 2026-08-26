"""Turn a natural-language instruction into a multi-node plan.

The LLM (if present) may only emit this JSON. Execution is never called from here.
A deterministic decomposer is the fallback so tests do not depend on an API.
"""

from __future__ import annotations

import json
import os
import re

from plan_schema import CLOSED_ACTIONS, plan_from_dict

# ---------------------------------------------------------------------------
# Prompt shown to the planner LLM. One node per atomic action; target_desc is
# a UI element, never the user's sentence.
# ---------------------------------------------------------------------------
PLANNER_PROMPT = """
You emit a JSON plan for a desktop agent. Output ONLY JSON:
{"nodes":[...], "source":"chat"}

RULES
1. One node per atomic action. A sentence with several verbs
   (open X, type Y, then save as Z) becomes several nodes, in order.
2. Field contract:
   - action: one of {actions}
   - target_desc: a SHORT UI-element description (a few words). NEVER the user's
     sentence. NEVER instruction text. Examples: "the text editing area",
     "the Save button", "the search box".
   - value: literal to type, app to launch, URL, filename, path.
   - keys: for hotkey nodes only (e.g. "ctrl+s").
3. Trailing intent is NOT a step. A clause like "I'll use a different filename
   each time" is metadata: set extra.likely_parameter=true on the filename
   node. Do not append that clause to target_desc.

EXAMPLE A
Input: Open Notepad, type "meeting notes for today", then save it as notes.txt
in D:\\python_files\\Mimic Agent\\testout — I'll use a different filename each time.
Output:
{
  "nodes": [
    {"id":"n1","action":"launch_app","value":"notepad"},
    {"id":"n2","action":"click","target_desc":"the text editing area"},
    {"id":"n3","action":"type","value":"meeting notes for today",
      "target_desc":"the text editing area"},
    {"id":"n4","action":"hotkey","value":"ctrl+s","keys":"ctrl+s"},
    {"id":"n5","action":"type","value":"notes.txt",
      "target_desc":"the filename field","extra":{"likely_parameter":true}}
  ],
  "source":"chat"
}

EXAMPLE B
Input: go to google.com, search for python, and click the first result
Output:
{
  "nodes": [
    {"id":"n1","action":"navigate","value":"https://google.com"},
    {"id":"n2","action":"click","target_desc":"the search box"},
    {"id":"n3","action":"type","value":"python","target_desc":"the search box"},
    {"id":"n4","action":"click","target_desc":"the first result"}
  ],
  "source":"chat"
}

Instruction:
""".strip().replace("{actions}", ", ".join(CLOSED_ACTIONS))

_VERB_PATTERNS = (
    ("open", r"\b(open|launch|start)\b"),
    ("type", r"\b(type|enter|write)\b"),
    ("save", r"\bsave\b"),
    ("click", r"\bclick\b"),
    ("navigate", r"\b(navigate|go to|goto|visit)\b"),
    ("search", r"\bsearch(?:\s+for)?\b"),
    ("copy", r"\bcopy\b"),
    ("paste", r"\bpaste\b"),
    ("select", r"\bselect\b"),
)

_INTENT_RE = re.compile(
    r"[\s,\-–—]*I['’]ll use a different (\w+)(?:\s+each time)?\s*[.]?\s*$",
    re.I,
)


def count_action_verbs(instruction: str) -> set[str]:
    text = instruction or ""
    found = set()
    for name, pat in _VERB_PATTERNS:
        if re.search(pat, text, re.I):
            found.add(name)
    return found


def _strip_intent(text: str) -> tuple[str, str | None]:
    m = _INTENT_RE.search(text or "")
    if not m:
        return (text or "").strip(), None
    kind = m.group(1).lower()
    core = text[: m.start()].strip().rstrip(",—–- ").strip()
    return core, kind


def _quoted_strings(text: str) -> list[str]:
    return re.findall(r'"([^"]+)"', text or "")


def _looks_filename(text: str) -> bool:
    t = (text or "").strip().strip(".,;")
    return bool(re.search(r"\.\w{1,5}$", t)) and " " not in t


def _next_id(n: int) -> str:
    return f"n{n}"


def _decompose(instruction: str) -> dict:
    original = (instruction or "").strip()
    core, intent_kind = _strip_intent(original)
    nodes: list[dict] = []
    n = 1
    lower = core.lower()

    # --- launch / open app ---
    app = None
    m_app = re.search(r"\b(?:open|launch|start)\s+([A-Za-z][A-Za-z0-9 ._-]*)", core, re.I)
    if m_app:
        raw = m_app.group(1).strip().rstrip(",.")
        # stop at next verb
        raw = re.split(r"\b(type|enter|write|save|click|then)\b", raw, maxsplit=1, flags=re.I)[0].strip()
        app = raw.lower()
        if app:
            nodes.append({
                "id": _next_id(n),
                "action": "launch_app",
                "value": app.split()[0],
            })
            n += 1

    # --- navigate ---
    m_url = re.search(
        r"\b(?:go to|navigate to|visit)\s+(\S+)",
        core,
        re.I,
    )
    if m_url:
        url = m_url.group(1).strip().rstrip(",.")
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        nodes.append({"id": _next_id(n), "action": "navigate", "value": url})
        n += 1

    # --- type quoted body text (not filenames) ---
    body_typed = False
    for q in _quoted_strings(core):
        if _looks_filename(q):
            continue
        if not body_typed:
            nodes.append({
                "id": _next_id(n),
                "action": "click",
                "target_desc": "the text editing area" if app else "the search box",
            })
            n += 1
            nodes.append({
                "id": _next_id(n),
                "action": "type",
                "value": q,
                "target_desc": "the text editing area" if app else "the search box",
            })
            n += 1
            body_typed = True

    # --- search for X (unquoted) ---
    m_search = re.search(r"\bsearch(?:\s+for)?\s+([A-Za-z0-9_+.-]+)", core, re.I)
    if m_search and not body_typed:
        term = m_search.group(1).strip().rstrip(",.")
        nodes.append({
            "id": _next_id(n),
            "action": "click",
            "target_desc": "the search box",
        })
        n += 1
        nodes.append({
            "id": _next_id(n),
            "action": "type",
            "value": term,
            "target_desc": "the search box",
        })
        n += 1
        body_typed = True

    # --- save as filename ---
    if re.search(r"\bsave\b", core, re.I):
        nodes.append({
            "id": _next_id(n),
            "action": "hotkey",
            "value": "ctrl+s",
            "keys": "ctrl+s",
        })
        n += 1
        fname = None
        m_as = re.search(r"\bsave(?:\s+it)?\s+as\s+([^\s,]+)", core, re.I)
        if m_as:
            fname = m_as.group(1).strip().strip(".,;")
        extra = {}
        m_in = re.search(r"\bin\s+([A-Za-z]:\\[^—–-]+?)(?:\s*$|\s+[—–-])", core)
        if m_in:
            extra["save_dir"] = m_in.group(1).strip()
        if fname:
            if intent_kind in ("filename", "file", "name"):
                extra["likely_parameter"] = True
            nodes.append({
                "id": _next_id(n),
                "action": "type",
                "value": fname,
                "target_desc": "the filename field",
                "extra": extra,
            })
            n += 1

    # --- click first/the result ---
    m_click = re.search(r"\bclick\s+(the\s+)?(.+?)(?:\s*$|\s+and\b)", core, re.I)
    if m_click and "search" not in (m_click.group(0) or "").lower():
        desc = m_click.group(2).strip().rstrip(".,")
        if len(desc) > 80:
            desc = desc[:60]
        # avoid duplicating "open notepad" as click
        if desc.lower() not in (app or "", "notepad"):
            if not any(
                nd.get("action") == "click" and nd.get("target_desc") == desc
                for nd in nodes
            ):
                # skip if this click is just "the search box" already added
                if "search box" not in desc.lower() or not any(
                    nd.get("target_desc") == "the search box" for nd in nodes
                ):
                    nodes.append({
                        "id": _next_id(n),
                        "action": "click",
                        "target_desc": desc if len(desc) < 80 else "the first result",
                    })
                    n += 1

    # generic click "first result"
    if re.search(r"\b(first result|first link)\b", core, re.I):
        if not any(
            "first result" in (nd.get("target_desc") or "") for nd in nodes
        ):
            nodes.append({
                "id": _next_id(n),
                "action": "click",
                "target_desc": "the first result",
            })

    if not nodes:
        nodes.append({
            "id": "n1",
            "action": "wait",
            "value": "0",
            "target_desc": "unknown",
        })

    return {"nodes": nodes, "source": "chat", "instruction": original}


def _try_llm(instruction: str) -> dict | None:
    key_path = os.path.join(os.path.dirname(__file__), "my_key.txt")
    if not os.path.isfile(key_path):
        return None
    try:
        with open(key_path, encoding="utf-8") as f:
            key = f.read().strip()
        if not key:
            return None
        import json as _json
        import urllib.request

        body = _json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 800,
            "system": PLANNER_PROMPT,
            "messages": [{"role": "user", "content": instruction}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
        )
        # Opt-in only: skip network in tests unless MIMIC_PLANNER_LLM=1
        if os.environ.get("MIMIC_PLANNER_LLM") != "1":
            return None
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
        text = payload["content"][0]["text"]
        start, end = text.find("{"), text.rfind("}")
        if start < 0:
            return None
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def emit_plan(instruction: str) -> dict:
    """Return a decomposed plan dict. Never sends OS input."""
    fallback = _decompose(instruction)
    llm = _try_llm(instruction)
    if llm:
        from plan_validator import validate_plan

        if not validate_plan(llm, instruction=instruction):
            plan = plan_from_dict(llm)
            return plan.to_dict() if hasattr(plan, "to_dict") else llm
    return fallback
