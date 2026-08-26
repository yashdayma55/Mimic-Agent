"""Phase 2: invalid plans never reach OS input."""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

import os_input
from plan_engine import execute_validated_plan
from plan_schema import plan_from_dict
from plan_validator import patch_node, validate_plan


def _valid():
    return {
        "nodes": [
            {"id": "n1", "action": "wait", "value": "0.1", "produces": ["ready"]},
            {
                "id": "n2",
                "action": "type",
                "value": "hello",
                "consumes": ["ready"],
                "produces": ["email"],
                "window_title": "Notepad",
            },
            {"id": "n3", "action": "wait", "value": "0.1", "consumes": ["email"]},
        ]
    }


def test_valid_passes_validator():
    v = validate_plan(_valid())
    print("valid violations:", v)
    assert v == []


def test_unknown_action_rejected_no_input():
    os_input.reset_calls()
    plan = {"nodes": [{"id": "n1", "action": "shell", "value": "rm -rf /"}]}
    out = execute_validated_plan(plan)
    print("unknown_action:", out["reason"], "calls=", os_input.call_count())
    assert out["ok"] is False
    assert out["executed"] is False
    assert os_input.call_count() == 0
    assert any(x["code"] == "unknown_action" for x in out["violations"])


def test_unsatisfied_consume():
    os_input.reset_calls()
    plan = {
        "nodes": [
            {"id": "n1", "action": "wait", "value": "0"},
            {"id": "n2", "action": "wait", "value": "0"},
            {"id": "n3", "action": "type", "value": "x", "consumes": ["email"]},
        ]
    }
    out = execute_validated_plan(plan)
    print("consume:", out["reason"], "calls=", os_input.call_count())
    assert out["executed"] is False
    assert os_input.call_count() == 0
    assert "email" in out["reason"]


def test_cycle_rejected():
    os_input.reset_calls()
    plan = {
        "nodes": [
            {"id": "n1", "action": "wait", "value": "0", "next": "n2"},
            {"id": "n2", "action": "wait", "value": "0", "next": "n1"},
        ]
    }
    out = execute_validated_plan(plan)
    print("cycle:", out["reason"], "calls=", os_input.call_count())
    assert out["executed"] is False
    assert os_input.call_count() == 0
    assert any(x["code"] == "cycle" for x in out["violations"])


def test_patch_breaks_downstream():
    os_input.reset_calls()
    plan = plan_from_dict(_valid())
    patched, viol = patch_node(plan, "n2", {"produces": []})
    print("patch violations:", viol)
    assert any("n3" in (v.get("message") or "") or v.get("node_id") == "n3" for v in viol)
    out = execute_validated_plan(patched)
    assert out["executed"] is False
    assert os_input.call_count() == 0
    print("PASS patch named downstream break and sent no OS input")


def main():
    print("=" * 70)
    print("PHASE 2 validator tests")
    print("=" * 70)
    test_valid_passes_validator()
    test_unknown_action_rejected_no_input()
    test_unsatisfied_consume()
    test_cycle_rejected()
    test_patch_breaks_downstream()
    print("PHASE 2 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
