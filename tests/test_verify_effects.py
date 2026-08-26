"""Phase 1 ground truth: success is a file on disk, not a quiet return."""

from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

MARKER = "MIMIC_GROUND_TRUTH_1234"


def _kill_notepad():
    subprocess.run(
        ["taskkill", "/F", "/IM", "notepad.exe"],
        capture_output=True,
        text=True,
    )
    time.sleep(0.6)


def _notepad_plan(path: str) -> list:
    return [
        {
            "kind": "native",
            "action": "click",
            "elem_name": "Text editor",
            "elem_type": "Document",
            "window_title": "Notepad",
            "instruction": "click the Notepad text area",
        },
        {
            "kind": "native",
            "action": "type",
            "text": MARKER,
            "type_mode": "replace",
            "window_title": "Notepad",
            "instruction": f"type {MARKER}",
        },
        {
            "kind": "native",
            "action": "hotkey",
            "keys": "ctrl+s",
            "window_title": "Notepad",
            "expect": "save",
            "verify_file": path,
            "verify_contains": MARKER,
            "instruction": "save the file",
        },
    ]


def test_notepad_open_writes_file():
    import os_input
    from ui_runner import find_window, run_verified_plan

    os_input.reset_calls()
    _kill_notepad()
    path = os.path.join(ROOT, "workflows", "_verify_effects_open.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("")
    subprocess.Popen(["notepad.exe", path])
    title = None
    for _ in range(25):
        win, title = find_window("Notepad")
        if win is not None:
            break
        time.sleep(0.2)
    assert title, "Notepad did not open"
    try:
        out = run_verified_plan(_notepad_plan(path))
        print("OPEN run ok=", out["ok"], "reason=", out["reason"])
        for r in out["results"]:
            for line in r.log_lines():
                print(line)
        time.sleep(0.4)
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
        print("OPEN file contents:", repr(body))
        assert out["ok"], out["reason"]
        assert MARKER.lower() in body.lower(), f"disk missing marker: {body!r}"
        print("PASS open: disk contains", MARKER)
    finally:
        _kill_notepad()


def test_notepad_closed_fails_and_creates_no_file():
    import os_input
    from ui_runner import run_verified_plan

    os_input.reset_calls()
    _kill_notepad()
    path = os.path.join(ROOT, "workflows", "_verify_effects_closed.txt")
    if os.path.isfile(path):
        os.remove(path)
    before_calls = os_input.call_count()
    out = run_verified_plan(_notepad_plan(path))
    print("CLOSED run ok=", out["ok"], "reason=", out["reason"])
    for r in out["results"]:
        for line in r.log_lines():
            print(line)
    created = os.path.isfile(path)
    print("CLOSED file exists=", created, "os_input calls=", os_input.call_count() - before_calls)
    assert out["ok"] is False, "closed run must not report success"
    assert "not found" in (out["reason"] or "").lower()
    assert "notepad" in (out["reason"] or "").lower()
    if created:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
        assert MARKER not in body
        os.remove(path)
    else:
        print("PASS closed: no file created")
    print("PASS closed: ok=False and no marker on disk")


def main():
    print("=" * 70)
    print("PHASE 1 ground-truth tests")
    print("=" * 70)
    test_notepad_open_writes_file()
    print()
    test_notepad_closed_fails_and_creates_no_file()
    print()
    print("PHASE 1 BOTH CHECKS PASSED")


if __name__ == "__main__":
    main()
