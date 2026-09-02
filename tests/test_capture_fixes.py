"""Live-capture fixes: parent target, vision diagnostics, capture-flow separation."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def test_text_in_button_parent_check():
    from show_capture import check_parent_target

    chain = [
        {"name": "Extensions", "control_type": "Text"},
        {"name": "Extensions", "control_type": "Button"},
    ]
    with patch("show_capture._element_ancestors", return_value=chain):
        pt = check_parent_target(100, 100, {"name": "Extensions", "control_type": "Text"})
    _pass("Text in Button asks parent", pt is not None, pt)
    _pass("ancestor is Button", (pt or {}).get("ancestor_type") == "Button")


def test_textcontrol_normalized():
    from show_capture import check_parent_target

    chain = [
        {"name": "Extensions", "control_type": "TextControl"},
        {"name": "ext-btn", "control_type": "ButtonControl"},
    ]
    with patch("show_capture._element_ancestors", return_value=chain):
        pt = check_parent_target(50, 50, {"name": "Extensions", "control_type": "TextControl"})
    _pass("TextControl normalized", pt is not None)


def test_parent_fires_despite_vision_mismatch():
    from teach_loop import add_step, apply_show_witnesses, set_context
    from teaching import TaughtWorkflow, get_step, save_taught

    wf = TaughtWorkflow(name="_cap_fix_parent")
    set_context(wf, "ext")
    s = add_step(wf, "click Extensions label")
    save_taught(wf)
    parent = {
        "clicked_name": "Extensions",
        "clicked_type": "Text",
        "ancestor_type": "Button",
        "ancestor_name": "Extensions",
        "question": "You clicked the text 'Extensions', which sits inside a Button. Should I click the Button?",
    }
    res = {
        "source": "a11y",
        "point": [100, 100],
        "witnesses": {"a11y": {"saw": True}, "dom": {"saw": False}, "vision": {"saw": False}},
        "primary": {"name": "Extensions", "control_type": "Text", "pipeline": "a11y"},
        "confirmation": {"vision_mismatch": True, "question": "vision disagrees"},
        "parent_target": parent,
    }
    apply_show_witnesses(wf, s.id, {"resolution": res}, skip_show_confirm=True)
    step = get_step(wf, s.id)
    kinds = [q.get("kind") for q in step.qa_history if not (q.get("a") or "").strip()]
    _pass("parent asked with vision mismatch", "parent_target" in kinds, kinds)
    _pass("vision mismatch also asked", "vision_mismatch" in kinds, kinds)


def test_capture_prompt_not_in_qa():
    from teach_loop import add_step, handle_show_confirm, set_context, _set_capture_prompt
    from teaching import TaughtWorkflow, get_step, save_taught

    wf = TaughtWorkflow(name="_cap_fix_flow")
    set_context(wf, "x")
    s = add_step(wf, "two clicks")
    s.click_count = 2
    s.anchors = [{"primary": {"name": "A", "control_type": "Button"}, "confirmed": True}]
    save_taught(wf)
    handle_show_confirm(wf, s.id, "yes")
    step = get_step(wf, s.id)
    chain_second = [q for q in step.qa_history if q.get("kind") == "chain_second"]
    _pass("no chain_second in qa_history", len(chain_second) == 0)
    _pass("prompt in chain_capture", "second click" in (step.chain_capture or {}).get("prompt", "").lower())


def test_confirm_stem_per_sub_click():
    from show_capture import confirm_target_with_vision

    res = {"rect": [90, 90, 110, 110], "point": [100, 100], "monitor": 0, "primary": {"name": "B"}}
    with patch("show_capture._crop_confirm_region") as crop:
        crop.return_value = ("/tmp/fake.png", {"crop_center_screen": [100, 100]})
        with patch("show_capture._run_timed", return_value=({"shows_target": True}, False)):
            out = confirm_target_with_vision(res, "click B", wf_name="_wf", stem="s1_c2")
    _pass("diagnostics returned", "diagnostics" in out)
    crop.assert_called_once()
    dest = crop.call_args[0][1]
    _pass("stem in confirm path", "s1_c2_confirm" in dest, dest)


def test_vision_diagnostics_fields():
    import tempfile
    from PIL import Image
    from show_capture import _crop_confirm_region

    frame_path = os.path.join(tempfile.gettempdir(), "mimic_diag_click_frame.png")
    Image.new("RGB", (1920, 1080), (40, 40, 40)).save(frame_path)
    path, diag = _crop_confirm_region(
        [90, 90, 110, 110],
        os.path.join("workflows", "_t", "t.png"),
        point=[100, 100],
        monitor=0,
        click_frame_path=frame_path,
        frame_origin=(0, 0),
        click_grab_offset_ms=55,
    )
    for key in ("crop_box_screen", "crop_dimensions", "monitor_index", "grab_elapsed_ms", "center_source"):
        _pass(f"diag has {key}", key in diag, diag.get(key))
    _pass("uses click point", diag.get("center_source") == "click_point")
    _pass("grab_timing at_click_time", diag.get("grab_timing") == "at_click_time")
    _pass("offset logged", diag.get("click_grab_offset_ms") == 55)


def test_confirm_crop_from_click_frame_after_screen_change():
    """Confirm crop must come from frozen click frame, not a later screen state."""
    import tempfile
    from PIL import Image
    from show_capture import _crop_confirm_region, confirm_target_with_vision

    pre = Image.new("RGB", (400, 300), (30, 30, 30))
    for px in range(90, 111):
        for py in range(90, 111):
            pre.putpixel((px, py), (255, 0, 0))
    post = Image.new("RGB", (400, 300), (0, 0, 255))

    td = tempfile.gettempdir()
    frame_path = os.path.join(td, "mimic_pre_click_frame.png")
    confirm_path = os.path.join(td, "mimic_pre_confirm.png")
    pre.save(frame_path)

    path, diag = _crop_confirm_region(
        None, confirm_path, point=[100, 100],
        click_frame_path=frame_path, frame_origin=(0, 0), click_grab_offset_ms=38,
    )
    _pass("click frame timing", diag.get("grab_timing") == "at_click_time")
    _pass("click offset <100ms", (diag.get("click_grab_offset_ms") or 999) < 100)

    confirm = Image.open(path)
    cx, cy = confirm.size[0] // 2, confirm.size[1] // 2
    r, g, b = confirm.getpixel((cx, cy))
    _pass("confirm crop shows pre-click red", r > 200 and b < 80, (r, g, b))

    with patch("show_capture._grab_screen", return_value=(post, (0, 0))):
        path2, diag2 = _crop_confirm_region(
            None, confirm_path + "2.png", point=[100, 100],
        )
    confirm2 = Image.open(path2)
    r2, g2, b2 = confirm2.getpixel((confirm2.size[0] // 2, confirm2.size[1] // 2))
    _pass("live re-grab shows post-change blue", b2 > 200, (r2, g2, b2))

    def _fake_vision(crop_path, step_description):
        img = Image.open(crop_path)
        r, g, b = img.getpixel((img.size[0] // 2, img.size[1] // 2))
        if r > 200:
            return {"shows_target": True, "what_you_see": "red target under cursor", "confidence": "high"}
        return {"shows_target": False, "what_you_see": "blank blue area", "confidence": "high"}

    res = {
        "rect": [90, 90, 110, 110],
        "point": [100, 100],
        "monitor": 0,
        "click_frame_abs": frame_path,
        "click_frame_origin": [0, 0],
        "click_grab_offset_ms": 38,
        "primary": {"name": "Extensions", "control_type": "Text"},
    }
    with patch("show_capture._ask_confirm_vision", side_effect=_fake_vision):
        with patch("show_capture._run_timed", side_effect=lambda fn, *a, **k: (fn(*a), False)):
            out = confirm_target_with_vision(res, "click Extensions", stem="test_pre")
    _pass("vision sees click-time content", out.get("what_you_see") == "red target under cursor", out)
    _pass("vision confirms target", out.get("confirmed_by_vision") is True)
    _pass("diag uses click frame", (out.get("diagnostics") or {}).get("grab_timing") == "at_click_time")


def test_cursor_wins_when_a11y_disagrees():
    from show_capture import _labels_overlap, _point_in_rect

    _pass("labels disagree extensions vs close", not _labels_overlap("Close side panel", "Extensions"))
    _pass("point inside close rect", _point_in_rect(1628, 100, [1604, 69, 1656, 120]))


def test_a11y_overlay_noise():
    from show_capture import _a11y_is_overlay_noise, _click_hints

    hints = _click_hints("click extensions then apollo", 0)
    _pass("extensions hint", "extensions" in hints)
    _pass("close panel is noise", _a11y_is_overlay_noise("Close side panel", "click extensions", 0))


def test_batch_processes_immediately():
    from PIL import Image
    from unittest.mock import patch
    import show_capture as sc

    order = []

    def _fake_listen(count, timeout=25.0, min_gap=0.3, *, with_frames=False, on_click=None, **kwargs):
        frames = [Image.new("RGB", (100, 100), c) for c in ((255, 0, 0), (0, 0, 255))]
        for i, color in enumerate(frames):
            ev = {
                "point": [10 + i, 10 + i],
                "frame_img": color,
                "frame_origin": (0, 0),
                "click_grab_offset_ms": 5,
                "a11y_raw": {"name": f"E{i}", "control_type": "Button", "rect": [0, 0, 50, 50]},
                "dom_raw": {},
            }
            if on_click:
                on_click(ev)
            order.append(i)
        return []

    def _fake_capture_show(wf, step_id, **kw):
        order.append(f"cap-{kw.get('sub_index')}-{kw.get('pre_a11y_raw', {}).get('name')}")
        return {"ok": True, "anchor": {"point": kw.get("point")}, "resolution": {}}

    with patch.object(sc, "listen_clicks", _fake_listen):
        with patch.object(sc, "capture_show", _fake_capture_show):
            from teaching import TaughtWorkflow, save_taught
            from teach_loop import add_step, set_context
            wf = TaughtWorkflow(name="_batch_immediate")
            set_context(wf, "t")
            s = add_step(wf, "extensions then apollo")
            s.click_count = 2
            save_taught(wf)
            sc.capture_chain_session(wf, s.id, click_count=2, countdown=0, window_sec=1.0)
    _pass("both clicks queued before processing", order[:2] == [0, 1], order[:2])
    _pass("captures processed in order", "cap-0-E0" in order and "cap-1-E1" in order, order)
    _pass("capture after listen", order.index("cap-0-E0") > order.index(1), order)


def test_listen_not_blocked_by_full_snapshot():
    """Full structural snapshot in the listen loop would miss click 2."""
    from unittest.mock import patch
    import show_capture as sc

    calls = {"full": 0, "fast": 0}

    def _slow_full(*a, **k):
        calls["full"] += 1
        import time
        time.sleep(0.08)
        return {"foreground_title": "x", "window_titles": [], "a11y_elements": []}

    def _fast(*a, **k):
        calls["fast"] += 1
        return {"foreground_title": "x", "window_titles": ["x"], "a11y_elements": [], "point": [0, 0]}

    heard = []
    state = iter([True, True, False, True, False, False, False, False])

    with patch.object(sc, "_left_button_down", side_effect=lambda: next(state, False)):
        with patch.object(sc, "cursor_point", side_effect=[(10, 10), (20, 20)]):
            with patch.object(sc, "_grab_screen", return_value=(None, (0, 0))):
                with patch.object(sc, "_snapshot_structural_at_click", return_value=({}, {})):
                    with patch("success_signals.snapshot_structural_state", _slow_full):
                        with patch("success_signals.snapshot_click_moment", _fast):
                            sc.listen_clicks(
                                2, timeout=1.0, min_gap=0.0, with_frames=True,
                                on_click=lambda e: heard.append(e),
                            )
    _pass("fast snapshot used", calls["fast"] >= 2, calls)
    _pass("full snapshot not in listen", calls["full"] == 0, calls)
    _pass("two clicks heard", len(heard) == 2, len(heard))


def test_async_freeze_slow_first_grab():
    """Second click is heard while first screenshot is still being captured."""
    import time
    from unittest.mock import patch
    import show_capture as sc

    states = iter([True, False, True, False, False, False, False, False, False])
    heard = []

    def _slow_freeze(x, y):
        if int(x) == 10:
            time.sleep(0.15)
        return {"point": [x, y]}

    with patch.object(sc, "_left_button_down", side_effect=lambda: next(states, False)):
        with patch.object(sc, "cursor_point", side_effect=[(10, 10), (20, 20)]):
            with patch.object(sc, "_freeze_click_at", side_effect=_slow_freeze):
                sc.listen_clicks(
                    2, timeout=2.0, min_gap=0.0, with_frames=True,
                    on_click=lambda e: heard.append(e), async_freeze=True,
                )
    _pass("slow grab still hears both", len(heard) == 2, len(heard))


def test_batch_does_not_block_listen():
    """Slow capture_show must not run inside listen — second click would be missed."""
    from PIL import Image
    from unittest.mock import patch
    import show_capture as sc

    clicks_seen = []

    def _fake_listen(count, timeout=25.0, min_gap=0.3, *, with_frames=False, on_click=None, **kwargs):
        for i in range(count):
            ev = {
                "point": [i, i],
                "frame_img": Image.new("RGB", (10, 10)),
                "frame_origin": (0, 0),
                "click_grab_offset_ms": 1,
                "a11y_raw": {"name": f"C{i}"},
                "dom_raw": {},
            }
            if on_click:
                on_click(ev)
                clicks_seen.append(i)
        return []

    def _slow_capture_show(wf, step_id, **kw):
        import time
        time.sleep(0.05)
        return {"ok": True, "anchor": {"point": list(kw.get("point") or [])}, "resolution": {}}

    with patch.object(sc, "listen_clicks", _fake_listen):
        with patch.object(sc, "capture_show", _slow_capture_show):
            from teaching import TaughtWorkflow, save_taught
            from teach_loop import add_step, set_context
            wf = TaughtWorkflow(name="_batch_noblock")
            set_context(wf, "t")
            s = add_step(wf, "two clicks")
            s.click_count = 2
            save_taught(wf)
            session = sc.capture_chain_session(wf, s.id, click_count=2, countdown=0, window_sec=1.0)
    _pass("listen saw both clicks", clicks_seen == [0, 1], clicks_seen)
    _pass("session got both", session.get("got") == 2, session.get("got"))
    _pass("session heard both", session.get("heard") == 2, session.get("heard"))


def test_click1_hints_exclude_apollo():
    from show_capture import _click_hints, _hint_score
    desc = "click extensions then apollo yellow icon"
    hints0 = _click_hints(desc, 0)
    _pass("click1 no apollo hint", "apollo" not in hints0, hints0)
    _pass("apollo scores low on click1", _hint_score("Apollo extension icon", hints0) == 0)
    _pass("extensions scores on click1", _hint_score("Extensions", hints0) >= 2)
    hints1 = _click_hints(desc, 1)
    _pass("click2 has apollo", "apollo" in hints1)


def test_batch_uses_per_click_frames():
    from PIL import Image
    from unittest.mock import patch
    import show_capture as sc

    frames = [
        Image.new("RGB", (200, 200), (255, 0, 0)),
        Image.new("RGB", (200, 200), (0, 0, 255)),
    ]
    calls = []

    def _fake_listen(*a, **k):
        evs = [
            {"point": [50, 50], "frame_img": frames[0], "frame_origin": (0, 0), "click_grab_offset_ms": 8, "a11y_raw": {}, "dom_raw": {}},
            {"point": [150, 150], "frame_img": frames[1], "frame_origin": (0, 0), "click_grab_offset_ms": 9, "a11y_raw": {}, "dom_raw": {}},
        ]
        if k.get("on_click"):
            for ev in evs:
                k["on_click"](ev)
            return []
        if k.get("with_frames"):
            return evs
        return [(50, 50), (150, 150)]

    def _fake_capture_show(wf, step_id, **kw):
        calls.append(kw.get("pre_frame_img"))
        return {"ok": True, "anchor": {"point": list(kw.get("point") or [])}, "resolution": {}}

    with patch.object(sc, "listen_clicks", _fake_listen):
        with patch.object(sc, "capture_show", _fake_capture_show):
            from teaching import TaughtWorkflow, save_taught
            from teach_loop import add_step, set_context

            wf = TaughtWorkflow(name="_batch_frames")
            set_context(wf, "t")
            s = add_step(wf, "two clicks")
            s.click_count = 2
            save_taught(wf)
            sc.capture_chain_session(wf, s.id, click_count=2, countdown=0, window_sec=1.0)
    _pass("two captures", len(calls) == 2, len(calls))
    _pass("click1 red frame", calls[0] is frames[0])
    _pass("click2 blue frame", calls[1] is frames[1])


def test_resolve_cursor_over_a11y():
    from unittest.mock import patch
    from show_capture import resolve_target

    with patch("show_capture._element_at", return_value={
        "name": "Close side panel", "control_type": "Button",
        "rect": [1604, 69, 1656, 120], "parent_path": "/Button",
    }):
        with patch("show_capture._browser_at", return_value={}):
            with patch("show_capture._combined_vlm_witness", return_value={"saw": False, "account": "x"}):
                with patch("show_capture._cursor_target_witness", return_value={
                    "saw": True, "name": "Extensions", "control_type": "Button",
                    "confidence": "high", "account": "puzzle-piece Extensions icon",
                }):
                    with patch("show_capture.confirm_target_with_vision", return_value={"confirmed_by_vision": True}):
                        with patch("show_capture._run_timed", side_effect=lambda fn, *a, **k: (fn(), False)):
                            res = resolve_target(
                                [1628, 100], step_description="click Extensions",
                                click_frame_abs="fake.png",
                                click_frame_origin=[0, 0],
                                click_grab_offset_ms=40,
                                sub_index=0,
                            )
    _pass("source is cursor", res.get("source") == "cursor", res.get("source"))
    _pass("primary Extensions", (res.get("primary") or {}).get("name") == "Extensions")


def test_real_text_in_button_live():
    """Optional live UIA: Text inside Button via tkinter."""
    try:
        import tkinter as tk
        from show_capture import _element_ancestors, check_parent_target, _normalize_ctype
    except Exception as e:
        print(f"  [SKIP] real Text-in-Button live test ({e})")
        return
    root = tk.Tk()
    root.withdraw()
    btn = tk.Button(root, text="Extensions")
    btn.pack()
    root.update()
    root.update_idletasks()
    x = btn.winfo_rootx() + max(1, btn.winfo_width()) // 2
    y = btn.winfo_rooty() + max(1, btn.winfo_height()) // 2
    chain = _element_ancestors(x, y, max_levels=4)
    root.destroy()
    if not chain:
        print("  [SKIP] real Text-in-Button — no UIA chain")
        return
    top_ct = _normalize_ctype(chain[0].get("control_type"))
    if top_ct == "Text":
        pt = check_parent_target(x, y, {"name": "Extensions", "control_type": "Text"})
        _pass("live Text at point asks parent", pt is not None, chain)
    else:
        with patch("show_capture._element_ancestors", return_value=[
            {"name": "Extensions", "control_type": "Text"},
            {"name": "Extensions", "control_type": "Button"},
        ]):
            pt = check_parent_target(x, y, {"name": "Extensions", "control_type": "Text"})
        _pass("live fallback mock parent", pt is not None)
    print(f"  [INFO] live UIA chain: {chain[:3]}")


def main():
    print("=" * 70)
    print("Capture fixes self-test")
    print("=" * 70)
    test_text_in_button_parent_check()
    test_textcontrol_normalized()
    test_parent_fires_despite_vision_mismatch()
    test_capture_prompt_not_in_qa()
    test_confirm_stem_per_sub_click()
    test_vision_diagnostics_fields()
    test_confirm_crop_from_click_frame_after_screen_change()
    test_a11y_overlay_noise()
    test_batch_processes_immediately()
    test_listen_not_blocked_by_full_snapshot()
    test_async_freeze_slow_first_grab()
    test_batch_does_not_block_listen()
    test_click1_hints_exclude_apollo()
    test_cursor_wins_when_a11y_disagrees()
    test_batch_uses_per_click_frames()
    test_resolve_cursor_over_a11y()
    test_real_text_in_button_live()
    print("ALL CAPTURE FIX CHECKS PASSED")


if __name__ == "__main__":
    main()
