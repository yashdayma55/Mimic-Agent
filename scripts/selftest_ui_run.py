"""Run batch_a_selftest with Notepad closed, then open. Print per-step logs."""

from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

import ui_backend

NAME = "batch_a_selftest"


def _kill_notepad():
    subprocess.run(
        ["taskkill", "/F", "/IM", "notepad.exe"],
        capture_output=True,
        text=True,
    )
    time.sleep(0.8)


def _open_notepad():
    path = os.path.join(ROOT, "workflows", "_ui_run_probe.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("")
    subprocess.Popen(["notepad.exe", path], cwd=ROOT)
    for _ in range(20):
        from ui_runner import find_window

        win, title = find_window("Notepad")
        if win is not None:
            print(f"  notepad open: {title!r}")
            return
        time.sleep(0.25)
    raise RuntimeError("Notepad did not open")


def _wait(run_id: str, timeout: float = 90.0) -> dict:
    st = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = ui_backend.run_status(run_id)
        if not st.get("running"):
            return st
        time.sleep(0.25)
    return st


def _run(label: str) -> dict:
    print("\n" + "=" * 70)
    print(label)
    print("=" * 70)
    r = ui_backend.run_workflow(NAME, inputs={}, require_approval=False)
    print("run_id:", r.get("run_id"))
    st = _wait(r["run_id"])
    print("running:", st.get("running"), " error:", st.get("error"))
    print("--- log ---")
    for line in st.get("log_tail") or []:
        print(line)
    return st


def main():
    print("Task 1-3 self-test: batch_a_selftest against real UI")
    print("closing Notepad...")
    _kill_notepad()
    closed = _run("A. Notepad CLOSED")

    print("\nopening Notepad...")
    _open_notepad()
    time.sleep(0.5)
    opened = _run("B. Notepad OPEN")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("closed error:", closed.get("error"))
    print("open   error:", opened.get("error"))
    print("closed ended with ok=False (expected if Notepad missing):", bool(closed.get("error")))


if __name__ == "__main__":
    main()
