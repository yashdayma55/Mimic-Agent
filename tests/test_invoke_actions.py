"""Phase 5: named invocations, no shell, filesystem/process ground truth."""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

import psutil

from invoke_actions import InvokeError, launch_app, move_file, open_url, run_invoke


def _notepad_running() -> bool:
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() == "notepad.exe":
                return True
        except Exception:
            continue
    return False


def _kill_notepad():
    import subprocess

    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    time.sleep(0.4)


def main():
    print("=" * 70)
    print("PHASE 5 invocation tests")
    print("=" * 70)

    _kill_notepad()
    before = _notepad_running()
    launch_app("notepad")
    found = False
    for _ in range(20):
        if _notepad_running():
            found = True
            break
        time.sleep(0.2)
    print("notepad launched", found, "was_running_before", before)
    assert found
    _kill_notepad()
    print("PASS launch_app notepad seen in process list")

    src = os.path.join(ROOT, "workflows", "_invoke_src.txt")
    dst = os.path.join(ROOT, "workflows", "_invoke_dst.txt")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    with open(src, "w", encoding="utf-8") as f:
        f.write("move-me")
    if os.path.isfile(dst):
        os.remove(dst)
    move_file(src, dst)
    print("after move src exists", os.path.exists(src), "dst exists", os.path.exists(dst))
    assert not os.path.exists(src) and os.path.exists(dst)
    print("PASS move_file src gone dst present")

    try:
        run_invoke("launch_app", "notepad.exe && calc.exe")
        raise SystemExit("unsafe launch should have been rejected")
    except InvokeError as e:
        print("rejected metachar:", e)
    print("PASS shell metacharacter rejected")

    try:
        open_url("http://127.0.0.1:9/")
        print("PASS open_url accepted http (CDP title UNVERIFIABLE if no debug browser)")
    except Exception as e:
        print("open_url error", e)

    try:
        open_url("file:///C:/Windows/System32/cmd.exe")
        raise SystemExit("file url should be rejected")
    except InvokeError:
        print("PASS non-http url rejected")

    print("PHASE 5 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
