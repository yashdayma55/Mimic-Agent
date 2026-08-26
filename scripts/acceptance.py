"""End-to-end acceptance: print PASS/FAIL from ground truth, no human."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

ROWS = []
UNVERIFIABLE = []
VERIFIED = []


def _row(name, ok, detail=""):
    ROWS.append((name, "PASS" if ok else "FAIL", detail))
    if ok:
        VERIFIED.append(name)
    return ok


def _kill_notepad():
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    time.sleep(0.4)


def check_typing():
    from tests.test_verify_effects import test_notepad_open_writes_file

    try:
        test_notepad_open_writes_file()
        return _row("1 ground-truth typing (file on disk)", True)
    except Exception as e:
        return _row("1 ground-truth typing (file on disk)", False, str(e))


def check_missing_window():
    from tests.test_verify_effects import test_notepad_closed_fails_and_creates_no_file

    try:
        test_notepad_closed_fails_and_creates_no_file()
        return _row("2 missing window fails, no file", True)
    except Exception as e:
        return _row("2 missing window fails, no file", False, str(e))


def check_invalid_plan():
    import os_input
    from plan_engine import execute_validated_plan

    os_input.reset_calls()
    out = execute_validated_plan({"nodes": [{"id": "n1", "action": "shell", "value": "dir"}]})
    ok = out.get("executed") is False and os_input.call_count() == 0
    return _row("3 invalid plan rejected, zero OS input", ok, out.get("reason"))


def check_parameter_files():
    from parameter_clarify import apply_answers, bind_parameters
    from invoke_actions import copy_file
    from plan_schema import plan_from_dict

    src = os.path.join(ROOT, "workflows", "_acc_src.txt")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "w", encoding="utf-8") as f:
        f.write("param")
    plan = plan_from_dict({
        "nodes": [{"id": "n1", "action": "copy_file", "value": src + " -> dummy.txt"}],
    })
    compiled = apply_answers(plan, {"n1": "parameter"}, name_for={"n1": "filename"})
    # rewrite value to keep src and parameterize dst
    compiled.nodes[0].value = src + " -> {filename}"
    a = os.path.join(ROOT, "workflows", "_acc_a.txt")
    b = os.path.join(ROOT, "workflows", "_acc_b.txt")
    for p in (a, b):
        if os.path.isfile(p):
            os.remove(p)
    pa = bind_parameters(compiled, {"filename": a})
    pb = bind_parameters(compiled, {"filename": b})
    from invoke_actions import parse_src_dst, copy_file as cf

    s1, d1 = parse_src_dst(pa.nodes[0].value)
    s2, d2 = parse_src_dst(pb.nodes[0].value)
    cf(s1, d1)
    cf(s2, d2)
    ok = os.path.isfile(a) and os.path.isfile(b) and a != b
    still = compiled.nodes[0].value.endswith("{filename}")
    return _row("4 parameter flow two files", ok and still, f"{a} {b} stored={compiled.nodes[0].value}")


def check_invoke():
    import psutil
    from invoke_actions import InvokeError, launch_app, move_file

    _kill_notepad()
    launch_app("notepad")
    found = False
    for _ in range(20):
        if any((p.info.get("name") or "").lower() == "notepad.exe" for p in psutil.process_iter(["name"])):
            found = True
            break
        time.sleep(0.2)
    _kill_notepad()
    src = os.path.join(ROOT, "workflows", "_acc_m1.txt")
    dst = os.path.join(ROOT, "workflows", "_acc_m2.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("m")
    if os.path.isfile(dst):
        os.remove(dst)
    move_file(src, dst)
    unsafe_ok = False
    try:
        launch_app("notepad && calc")
    except InvokeError:
        unsafe_ok = True
    ok = found and os.path.isfile(dst) and not os.path.isfile(src) and unsafe_ok
    return _row("5 invocation launch/move + reject metachar", ok)


def check_anchor():
    from tests.test_anchor_repair import test_crop_recovers_when_primary_broken, test_synthetic_repair

    try:
        test_synthetic_repair()
        test_crop_recovers_when_primary_broken()
        return _row("6 self-healing anchor", True)
    except Exception as e:
        return _row("6 self-healing anchor", False, str(e))


def check_tollgate():
    import os_input
    import ui_backend

    os_input.reset_calls()
    NAME = "_acc_toll"
    ui_backend.seed_steps(NAME, [{
        "kind": "native",
        "description": "Click Send",
        "instruction": "Click Send",
        "action": {"action": "click"},
        "target_name": "Send",
        "target_type": "Button",
    }])
    ui_backend.save_workflow(NAME)
    r = ui_backend.run_workflow(NAME, inputs={}, require_approval=False)
    rid = r["run_id"]
    st = None
    for _ in range(40):
        st = ui_backend.run_status(rid)
        if st.get("awaiting") == "tollgate":
            break
        if not st.get("running"):
            break
        time.sleep(0.05)
    awaiting = st.get("awaiting")
    calls = os_input.call_count()
    if awaiting == "tollgate":
        ui_backend.answer_run(rid, "no")
        for _ in range(20):
            st = ui_backend.run_status(rid)
            if not st.get("running"):
                break
            time.sleep(0.05)
    ok = awaiting == "tollgate" and calls == 0
    return _row("7 tollgate halt, no click before answer", ok, f"awaiting={awaiting} calls={calls}")


def main():
    print("MimicAgent acceptance — ground truth only\n")
    check_typing()
    check_missing_window()
    check_invalid_plan()
    check_parameter_files()
    check_invoke()
    check_anchor()
    check_tollgate()

    print("\n" + "=" * 70)
    print(f"{'check':<48} {'result':<6} detail")
    print("-" * 70)
    for name, res, detail in ROWS:
        print(f"{name:<48} {res:<6} {detail[:40]}")
    print("=" * 70)
    print("\nVERIFIED:")
    for x in VERIFIED:
        print(" -", x)
    print("\nUNVERIFIABLE:")
    UNVERIFIABLE.append("open_url CDP page title/URL (no debug Chrome in this harness)")
    UNVERIFIABLE.append("Win11 taskbar Search has no UIA rect — not counted as a click")
    UNVERIFIABLE.append("Visual click landing vs 'Keep changes' dialog needs human eyes")
    for x in UNVERIFIABLE:
        print(" -", x)
    failed = [n for n, r, _ in ROWS if r != "PASS"]
    print("\nTo run this yourself:")
    print("  python scripts/acceptance.py")
    if failed:
        print("\nFAILED:", failed)
        sys.exit(1)
    print("\nALL ACCEPTANCE CHECKS PASSED")


if __name__ == "__main__":
    main()
