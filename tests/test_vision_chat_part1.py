"""PART 1 — Ask vision chat on step card."""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def test_vision_ask_store_and_remove():
    import os_input
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow, get_step, load_taught, save_taught
    from vision_chat import ask_vision, remove_vision_chat_entry
    from workflow_folder import workflow_dir

    name = "_vision_chat_p1"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "test")
    step = add_step(wf, "copy email")
    save_taught(wf)

    os_input.reset_calls()
    before = os_input.call_count()
    out = ask_vision(
        load_taught(name),
        step.id,
        "what do you see near the email?",
        synthetic_bytes=b"png-bytes",
        synthetic_answer="A copy icon beside an email address in a sidebar panel.",
    )
    _pass("ask ok", out.get("ok"))
    entry = out.get("entry") or {}
    _pass("answer stored", "copy icon" in (entry.get("a") or "").lower())
    _pass("frame path stored", bool(entry.get("frame_path")), entry.get("frame_path"))
    frame_abs = os.path.join(wd, (entry.get("frame_path") or "").replace("/", os.sep))
    _pass("frame file exists", os.path.isfile(frame_abs), frame_abs)
    _pass("no os input", os_input.call_count() == before)

    loaded = get_step(load_taught(name), step.id)
    _pass("history length", len(loaded.vision_chat or []) == 1)

    rm = remove_vision_chat_entry(load_taught(name), step.id, 0)
    _pass("remove ok", rm.get("ok"))
    loaded2 = get_step(load_taught(name), step.id)
    _pass("history cleared", not loaded2.vision_chat)
    _pass("frame deleted", not os.path.isfile(frame_abs))


def test_capture_does_not_refuse_own_ui():
    from unittest.mock import MagicMock, patch
    from vision_chat import capture_vision_frame
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow, load_taught, save_taught
    from workflow_folder import workflow_dir
    import os
    import shutil

    name = "_vision_chat_focus"
    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "linkedin apollo")
    step = add_step(wf, "copy the visible email")
    save_taught(wf)

    fake_img = MagicMock()
    fake_img.crop.return_value = fake_img
    fake_img.save = lambda buf, format=None: None
    with patch("app_ui_guard.is_own_window", return_value=True), patch(
        "ui_runner.foreground_title", return_value="MimicAgent"
    ), patch("vision_chat.pick_target_window", return_value=(object(), "LinkedIn")), patch(
        "vision_chat.focus_target_for_vision", return_value={"ok": True, "title": "LinkedIn"}
    ), patch("vision_chat._crop_box_for_window", return_value=(0, 0, 100, 100)), patch(
        "show_capture._grab_screen", return_value=(fake_img, (0, 0))
    ), patch("show_capture._to_image_box", return_value=(0, 0, 100, 100)), patch(
        "show_capture._save_image", return_value=""
    ):
        rel, raw = capture_vision_frame(name, step.id, wf=load_taught(name), synthetic_bytes=None)
    _pass("did not refuse own ui", bool(rel))


def test_vision_mode_float_asks_after_focus():
    from float_widget import FOCUS_PATH, VISION_ASK_PATH, FloatingTeacher

    calls = []

    def _post(path, body):
        calls.append(path)
        if path.endswith("focus-target"):
            return {"ok": True, "title": "LinkedIn"}
        return {"ok": True, "entry": {"a": "I see a copy icon next to an email."}}

    w = FloatingTeacher(
        workflow="wf", step_id="s1", mode="vision",
        vision_question="what do you see near the email?",
    )
    w._post = _post  # type: ignore[method-assign]
    w._root = None
    w._status = type("S", (), {"config": lambda *a, **k: None})()
    w.on_ask_vision()
    _pass("focused first", any(FOCUS_PATH in p for p in calls))
    _pass("then asked vision", any(VISION_ASK_PATH in p for p in calls))
    _pass("answer on bar", "copy icon" in (w.last_outcome or "").lower())


def main():
    print("=" * 70)
    print("PART 1 vision chat self-test")
    print("=" * 70)
    test_vision_ask_store_and_remove()
    test_capture_does_not_refuse_own_ui()
    test_vision_mode_float_asks_after_focus()
    print("PART 1 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
