"""Phase 3: literal vs parameter asked once, late-bound, persisted."""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from parameter_clarify import (
    apply_answers,
    bind_parameters,
    compile_parameters,
    find_ambiguous_nodes,
    load_decisions,
)
from plan_schema import plan_from_dict

DIR = os.path.join(ROOT, "workflows", "_param_selftest")


def _plan():
    return {
        "nodes": [
            {"id": "n1", "action": "wait", "value": "0.1"},
            {"id": "n2", "action": "type", "value": "notes1.txt", "window_title": "Notepad"},
        ]
    }


def main():
    print("=" * 70)
    print("PHASE 3 parameter clarification tests")
    print("=" * 70)
    if os.path.isdir(DIR):
        shutil.rmtree(DIR)

    plan = plan_from_dict(_plan())
    qs = find_ambiguous_nodes(plan)
    print("questions:", qs)
    assert len(qs) == 1, qs
    assert qs[0]["value"] == "notes1.txt"
    print("PASS exactly one ambiguity question")

    compiled, remaining = compile_parameters(
        plan, DIR, answers={"n2": "parameter"}, name_for={"n2": "filename"}
    )
    print("compiled n2.value=", compiled.nodes[1].value, "remaining=", remaining)
    assert compiled.nodes[1].value == "{filename}"
    assert remaining == []
    assert load_decisions(DIR).get("n2") == "parameter"

    again, remaining2 = compile_parameters(compiled, DIR)
    print("reload remaining=", remaining2)
    assert remaining2 == []
    print("PASS question not asked again after save/reload")

    run_a = bind_parameters(compiled, {"filename": "alpha.txt"})
    run_b = bind_parameters(compiled, {"filename": "beta.txt"})
    print("bound A", run_a.nodes[1].value, "B", run_b.nodes[1].value)
    assert run_a.nodes[1].value == "alpha.txt"
    assert run_b.nodes[1].value == "beta.txt"
    assert compiled.nodes[1].value == "{filename}"
    print("PASS two binds differ and stored plan still holds {filename}")
    print("PHASE 3 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
