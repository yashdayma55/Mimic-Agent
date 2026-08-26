"""Watch-me, memory notes, web tool gate, and monitor cropping."""

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


def test_monitor_crop_uses_cursor_screen():
    from show_capture import _monitor_at, _to_image_box

    box = _to_image_box((-100, 10, 100, 200), origin=(-1920, 0), img_size=(3840, 1080))
    _pass("negative virtual origin maps into the image", box[0] >= 0 and box[2] <= 3840, str(box))
    mon = _monitor_at(50, 50)
    _pass("monitor_at returns 4 ints", len(mon) == 4, str(mon))
    _pass("monitor has area", mon[2] > mon[0] and mon[3] > mon[1], str(mon))


def test_memory_on_approved_step():
    from teach_loop import add_step, approve_step, set_context, update_step
    from teaching import TaughtWorkflow, get_step, load_taught

    name = "_mem_note"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "notes")
    s = add_step(wf, "click the Save button")
    approve_step(wf, s.id, skip_rehearsal=True)
    update_step(wf, s.id, memory_note="write cold emails short and specific", web_allowed=True)
    loaded = get_step(load_taught(name), s.id)
    _pass("approved step kept", loaded.status == "approved")
    _pass("memory saved", "short and specific" in (loaded.memory_note or ""), loaded.memory_note)
    _pass("web allowed", loaded.web_allowed is True)


def test_watch_writes_learned():
    from observe import watch_step
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow, get_step, load_taught

    name = "_watch_me"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "watch")
    s = add_step(wf, "click the text editor")
    out = watch_step(name, s.id, seconds=0, interval=0.2)
    _pass("watch ok", out.get("ok") is True, str(out)[:120])
    learned = out.get("learned") or {}
    _pass("summary present", bool(learned.get("summary")), str(learned)[:160])
    loaded = get_step(load_taught(name), s.id)
    _pass("learned stored on step", bool(loaded.learned and loaded.learned.get("summary")))


def test_web_tool_rejects_private():
    from step_tools import tools_for_step, web_get
    from teaching import TaughtStep

    out = web_get("http://127.0.0.1/secret")
    _pass("localhost blocked", out.get("ok") is False, str(out))
    out2 = web_get("not-a-url")
    _pass("non-http blocked", out2.get("ok") is False, str(out2))
    s = TaughtStep(id="s1", order=0, user_description="x", web_allowed=True)
    tools = tools_for_step(s)
    _pass("web tool listed when allowed", any(t["name"] == "web_get" for t in tools), str(tools))
    s2 = TaughtStep(id="s2", order=1, user_description="y", web_allowed=False)
    _pass("no web tool when off", tools_for_step(s2) == [])


def main():
    print("=== observe / memory / web / monitor ===")
    test_monitor_crop_uses_cursor_screen()
    test_memory_on_approved_step()
    test_watch_writes_learned()
    test_web_tool_rejects_private()
    print("ALL OBSERVE/MEMORY CHECKS PASSED")


if __name__ == "__main__":
    main()
