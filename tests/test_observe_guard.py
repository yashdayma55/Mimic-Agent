"""Watch me must not read MimicAgent's own UI or placeholder text."""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app_ui_guard import is_own_window, memory_note_for_summary


def _ok(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def test_own_window_detection():
    _ok("MimicAgent title", is_own_window("MimicAgent"))
    _ok("localhost review", is_own_window("MimicAgent - 127.0.0.1:8765"))
    _ok("float widget", is_own_window("Show me"))
    _ok("notepad ok", not is_own_window("Untitled - Notepad"))


def test_empty_notes_not_placeholder():
    line = memory_note_for_summary("")
    _ok("empty notes phrase", "no notes" in line.lower(), line)
    ph = memory_note_for_summary("e.g. write the cold email in this voice: short, specific, no fluff")
    _ok("placeholder treated as empty", "no notes" in ph.lower(), ph)
    real = memory_note_for_summary("Use a friendly tone")
    _ok("real note kept", "friendly" in real, real)


def test_watch_skips_dashboard():
    import observe as obs
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow, get_step, load_taught

    name = "_observe_guard"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)

    own_samples = [
        {
            "ts": "t",
            "point": [100, 100],
            "monitor": [0, 0, 1280, 720],
            "window": "MimicAgent",
            "name": "e.g. write the cold email in this voice: short, specific, no fluff",
            "control_type": "Edit",
            "rect": [0, 0, 10, 10],
        },
        {
            "ts": "t2",
            "point": [200, 200],
            "monitor": [0, 0, 1280, 720],
            "window": "MimicAgent - 127.0.0.1:8765",
            "name": "What does this step do?",
            "control_type": "Edit",
            "rect": [0, 0, 10, 10],
        },
    ]

    idx = [0]

    def fake_sample():
        i = min(idx[0], len(own_samples) - 1)
        idx[0] += 1
        return dict(own_samples[i])

    wf = TaughtWorkflow(name=name)
    set_context(wf, "guard test")
    step = add_step(wf, "click Extensions")
    step.memory_note = ""

    orig_sample = obs._sample_once
    orig_fg = obs._foreground_title
    obs._sample_once = fake_sample
    obs._foreground_title = lambda: "MimicAgent"
    try:
        out = obs.watch_step(name, step.id, seconds=0, interval=0.1)
    finally:
        obs._sample_once = orig_sample
        obs._foreground_title = orig_fg

    learned = out.get("learned") or {}
    summary = (learned.get("summary") or "").lower()
    vision = (learned.get("vision") or "").lower()
    blob = summary + " " + vision
    _ok("no MimicAgent in summary", "mimicagent" not in blob and "127.0.0.1" not in blob, blob[:200])
    _ok("no placeholder in summary", "cold email" not in blob and "what does this step" not in blob, blob[:200])
    _ok("no vision on own UI", "vision saw" not in summary, summary)
    _ok("no notes phrase", "no notes" in summary, summary)
    _ok("skip log populated", len(out.get("skip_log") or []) > 0, out.get("skip_log"))
    _ok("target unset", learned.get("target") in (None, ""), learned.get("target"))
    loaded = get_step(load_taught(name), step.id)
    _ok("stored on step", bool(loaded.learned))


def test_edit_learned_reexplains():
    from teach_loop import add_step, set_context, update_step
    from teaching import TaughtWorkflow, get_step, load_taught

    name = "_learned_edit"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "edit")
    step = add_step(wf, "click Save")
    wf = load_taught(name)
    step = get_step(wf, step.id)
    step.learned = {"summary": "auto summary", "vision": "auto vision"}
    from teaching import save_taught

    save_taught(wf)
    update_step(
        load_taught(name), step.id,
        learned={"summary": "User fixed: click the blue Save button", "vision": ""},
        re_explain=True,
    )
    loaded = get_step(load_taught(name), step.id)
    _ok("user edited flag", loaded.learned.get("user_edited") is True)
    _ok("summary saved", "blue Save" in (loaded.learned.get("summary") or ""))
    _ok("understanding refreshed", bool(loaded.understanding))


def main():
    print("=" * 70)
    print("OBSERVE GUARD self-test")
    print("=" * 70)
    test_own_window_detection()
    test_empty_notes_not_placeholder()
    test_watch_skips_dashboard()
    test_edit_learned_reexplains()
    print("ALL OBSERVE GUARD CHECKS PASSED")


if __name__ == "__main__":
    main()
