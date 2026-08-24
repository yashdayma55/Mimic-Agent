"""Batch A self-tests: session backend + compile. No web, no real recorder."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

import ui_backend
from compile_workflow import bind_inputs, compile_workflow, load_harness_steps
from harness_schema import step_from_dict
from workflow_folder import resolve_paths, workflow_dir

NAME = "_batch_a_selftest"


def _seed():
    cards = [
        {
            "kind": "native",
            "description": "click the Search box",
            "instruction": "click Search",
            "action": {"action": "click"},
            "target_name": "Search",
            "target_type": "Edit",
            "outputs": ["query"],
        },
        {
            "kind": "browser",
            "description": "type {x} into the field",
            "instruction": "type {x}",
            "action": {"action": "type", "text": "{x}"},
            "target_name": "q",
            "target_type": "Edit",
            "inputs": ["x"],
        },
        {
            "kind": "reason",
            "description": "decide whether the results look relevant",
            "instruction": "judge the results",
            "goal": "check that search results mention {x}",
            "inputs": ["x"],
        },
    ]
    return ui_backend.seed_steps(NAME, cards)


def main():
    wd = workflow_dir(NAME)
    if os.path.isdir(wd):
        shutil.rmtree(wd)

    print("=" * 70)
    print("1. import ui_backend")
    print("=" * 70)
    print("import ok")

    print("\n" + "=" * 70)
    print("2. list / seed / update / insert / delete / save")
    print("=" * 70)
    print("list_workflows():", ui_backend.list_workflows()[:5], "...")
    seeded = _seed()
    print("seeded:", json.dumps(seeded, indent=2)[:800])
    print("\n--- update_step description+instruction ---")
    before = ui_backend.get_steps(NAME)["steps"][0]
    print("before:", {"description": before["description"], "instruction": before["instruction"]})
    updated = ui_backend.update_step(
        NAME, 0,
        {"description": "click the main Search box", "instruction": "click the Search edit"},
    )
    after = updated["step"]
    print("after:", {"description": after["description"], "instruction": after["instruction"]})

    print("\n--- insert_step after index 1 ---")
    inserted = ui_backend.insert_step(NAME, 1, "reason about the typed value", kind="reason")
    print("kinds:", [(s["index"], s["kind"], s["description"][:40]) for s in inserted["steps"]])

    print("\n--- mark one step deleted (the inserted reason, index 2) ---")
    deleted = ui_backend.update_step(NAME, 2, {"deleted": True})
    print("deleted flags:", [(s["index"], s["deleted"], s["kind"]) for s in deleted["steps"]])

    print("\n--- save_workflow ---")
    saved = ui_backend.save_workflow(NAME)
    print("save:", saved)
    paths = resolve_paths(NAME)
    with open(paths["transcript_json"], "r", encoding="utf-8") as f:
        payload = json.load(f)
    loaded = [step_from_dict(sd) for sd in payload["steps"]]
    for hs in loaded:
        hs.validate()
    print("transcript.json harness_schema.step_from_dict: OK", len(loaded), "steps")

    print("\n" + "=" * 70)
    print("3. compile_workflow mixed list with {placeholder}")
    print("=" * 70)
    cards = ui_backend.get_steps(NAME)["steps"]
    compiled = compile_workflow(cards)
    print(json.dumps(compiled, indent=2)[:2000])
    blob = json.dumps(compiled)
    assert "{x}" in blob, "placeholder must survive compile"
    print("placeholder {x} still present at compile time: YES")

    print("\n" + "=" * 70)
    print("4. late binding")
    print("=" * 70)
    plan = compiled["plan"]
    a = bind_inputs(plan, {"x": "A"})
    b = bind_inputs(plan, {"x": "B"})
    print("fill A texts:", [s.get("text") for s in a])
    print("fill B texts:", [s.get("text") for s in b])
    print("compile plan unchanged texts:", [s.get("text") for s in plan])

    print("\n" + "=" * 70)
    print("5. dependency violation")
    print("=" * 70)
    ui_backend.seed_steps(NAME, [
        {
            "kind": "native",
            "description": "copy the email",
            "instruction": "copy email",
            "action": {"action": "copy"},
            "target_name": "Email",
            "outputs": ["addr"],
        },
        {
            "kind": "browser",
            "description": "paste {addr} into To",
            "instruction": "type {addr}",
            "action": {"action": "type", "text": "{addr}"},
            "target_name": "To",
            "inputs": ["addr"],
        },
    ])
    ui_backend.update_step(NAME, 0, {"deleted": True})
    refused = ui_backend.save_workflow(NAME)
    print("save_workflow:", json.dumps(refused, indent=2))
    assert refused.get("ok") is False
    print("REFUSED as required")

    print("\n" + "=" * 70)
    print("6. thread-flag safety")
    print("=" * 70)
    ui_backend.seed_steps(NAME, [{
        "kind": "reason",
        "description": "no-op",
        "goal": "do nothing harmful",
    }])
    ui_backend.save_workflow(NAME)
    r = ui_backend.run_workflow(NAME, inputs={}, require_approval=False)
    rid = r["run_id"]
    for _ in range(50):
        st = ui_backend.run_status(rid)
        if not st["running"]:
            break
        time.sleep(0.05)
    print("after finish:", {k: st[k] for k in ("running", "error", "log_tail")})
    assert st["running"] is False

    ui_backend.seed_steps(NAME, [{
        "kind": "native",
        "description": "crash",
        "action": {"action": "__test_crash__"},
        "target_name": "X",
    }])
    ui_backend.save_workflow(NAME)
    r2 = ui_backend.run_workflow(NAME, inputs={}, require_approval=False)
    rid2 = r2["run_id"]
    for _ in range(50):
        st2 = ui_backend.run_status(rid2)
        if not st2["running"]:
            break
        time.sleep(0.05)
    print("after crash:", {k: st2[k] for k in ("running", "error", "log_tail")})
    assert st2["running"] is False
    print("finally cleared running after exception: YES")

    print("\n" + "=" * 70)
    print("7. queue prompts")
    print("=" * 70)
    ui_backend.seed_steps(NAME, [{
        "kind": "reason",
        "description": "needs a human yes",
        "goal": "wait for approval then continue",
    }])
    ui_backend.save_workflow(NAME)
    r3 = ui_backend.run_workflow(NAME, inputs={}, require_approval=True)
    rid3 = r3["run_id"]
    st3 = None
    for _ in range(40):
        st3 = ui_backend.run_status(rid3)
        if st3.get("awaiting") == "approval":
            break
        time.sleep(0.05)
    print("while waiting:", {k: st3[k] for k in ("running", "awaiting", "prompt_text")})
    assert st3["awaiting"] == "approval"
    ans = ui_backend.answer_run(rid3, "y")
    print("answer_run:", ans)
    for _ in range(40):
        st3 = ui_backend.run_status(rid3)
        if not st3["running"]:
            break
        time.sleep(0.05)
    print("after answer:", {k: st3[k] for k in ("running", "awaiting", "error", "log_tail")})
    assert st3["running"] is False
    assert st3.get("error") in (None, "stopped") or "done" in str(st3.get("log_tail"))

    print("\nALL BATCH A SELF-TESTS PASSED")


if __name__ == "__main__":
    main()
