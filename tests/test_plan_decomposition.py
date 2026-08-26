"""Ground-truth tests for multi-step plan emission. No OS input required."""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

import os_input
from parameter_clarify import find_ambiguous_nodes
from plan_emitter import emit_plan
from plan_engine import execute_validated_plan
from plan_validator import validate_plan
from ui_backend import propose_plan

NOTEPAD_SENTENCE = (
    'Open Notepad, type "meeting notes for today", then save it as notes.txt in '
    r"D:\python_files\Mimic Agent\testout — I'll use a different filename each time."
)

BROWSER_SENTENCE = "go to google.com, search for python, and click the first result"


def _ok(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label} {detail}")
    if not cond:
        raise AssertionError(label)


def test_notepad_decomposition():
    print("\n1. Notepad sentence -> decomposed plan")
    plan = emit_plan(NOTEPAD_SENTENCE)
    nodes = plan.get("nodes") or []
    print(json.dumps(plan, indent=2))
    _ok(">= 4 nodes", len(nodes) >= 4, f"got {len(nodes)}")
    actions = [n.get("action") for n in nodes]
    values = [n.get("value") for n in nodes]
    _ok(
        "launch_app notepad",
        any(n.get("action") == "launch_app" and str(n.get("value") or "").lower() == "notepad" for n in nodes),
        str(values),
    )
    _ok(
        'type value == "meeting notes for today"',
        any(n.get("action") == "type" and n.get("value") == "meeting notes for today" for n in nodes),
    )
    _ok(
        "hotkey ctrl+s",
        any(
            n.get("action") == "hotkey"
            and "ctrl+s" in str(n.get("value") or n.get("keys") or "").lower()
            for n in nodes
        ),
    )
    _ok(
        "type node for filename notes.txt",
        any(n.get("action") == "type" and str(n.get("value") or "").endswith("notes.txt") for n in nodes),
    )
    for n in nodes:
        desc = n.get("target_desc") or ""
        _ok(
            "target_desc short / not instruction",
            len(desc) <= 80 and "different filename each time" not in desc.lower(),
            repr(desc),
        )
    chat = propose_plan(NOTEPAD_SENTENCE)
    _ok("propose_plan disabled", chat.get("ok") is False and "one-shot" in str(chat.get("error") or "").lower())
    return plan


def test_ambiguity_filename_only(plan):
    print("\n2. Ambiguity detector flags exactly the filename node")
    qs = find_ambiguous_nodes(plan)
    print("  questions:", qs)
    _ok("exactly one question", len(qs) == 1, f"got {len(qs)}")
    val = str(qs[0].get("value") or "")
    _ok("question is about notes.txt", "notes.txt" in val, val)
    _ok(
        "not about body text",
        "meeting notes" not in val.lower(),
        val,
    )


def test_degenerate_rejected():
    print("\n3. Degenerate single-node plan rejected, zero OS calls")
    degenerate = {
        "nodes": [
            {
                "id": "n1",
                "action": "launch_app",
                "value": "notepad",
                "target_desc": NOTEPAD_SENTENCE,
            }
        ],
        "source": "chat",
    }
    os_input.reset_calls()
    viol = validate_plan(degenerate, instruction=NOTEPAD_SENTENCE)
    print("  violations:", viol)
    _ok("rejected", bool(viol))
    blob = " ".join(v.get("message") or "" for v in viol).lower()
    _ok(
        "decomposition message",
        "decompos" in blob or "not decomposed" in blob or "target_desc" in blob,
        blob,
    )
    _ok("validator sent no OS input", os_input.call_count() == 0, str(os_input.call_count()))
    out = execute_validated_plan(degenerate)
    _ok("execute_validated_plan did not run", out.get("executed") is False)
    _ok("still zero OS input", os_input.call_count() == 0, str(os_input.call_count()))


def test_browser_sentence():
    print("\n4. Browser sentence decomposes")
    plan = emit_plan(BROWSER_SENTENCE)
    nodes = plan.get("nodes") or []
    print(json.dumps(plan, indent=2))
    actions = [n.get("action") for n in nodes]
    _ok(">= 3 nodes", len(nodes) >= 3, f"got {len(nodes)} {actions}")
    _ok("has navigate", "navigate" in actions, str(actions))
    _ok("has type", "type" in actions, str(actions))
    _ok("has click", "click" in actions, str(actions))


def main():
    print("=" * 70)
    print("PLAN DECOMPOSITION self-test (no OS input required for 3–4)")
    print("=" * 70)
    plan = test_notepad_decomposition()
    test_ambiguity_filename_only(plan)
    test_degenerate_rejected()
    test_browser_sentence()
    print("\n" + "=" * 70)
    print("FULL NOTEPAD PLAN")
    print("=" * 70)
    print(json.dumps(plan, indent=2))
    print("\nALL DECOMPOSITION CHECKS PASSED")


if __name__ == "__main__":
    main()
