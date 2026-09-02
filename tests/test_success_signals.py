"""Self-tests for derived success checks (Parts 1–5)."""

from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _pass(label: str, cond: bool, detail=""):
    if cond:
        print(f"  [PASS] {label}", detail if detail else "")
    else:
        raise AssertionError(f"{label} {detail}")


def test_part1_after_frame():
    from PIL import Image
    from teaching import TaughtWorkflow, save_taught
    from teach_loop import add_step, set_context, _maybe_finalize_step_capture
    from success_signals import capture_after_frame

    wf = TaughtWorkflow(name="_succ_p1")
    set_context(wf, "test")
    s = add_step(wf, "click extensions")
    s.click_count = 1
    s.anchors = [{
        "point": [100, 100],
        "click_frame_path": "anchors/s1_click_frame.png",
        "structural_state": {"foreground_title": "LinkedIn", "window_titles": ["LinkedIn"]},
    }]
    save_taught(wf)
    td = tempfile.mkdtemp()
    wf_dir = os.path.join(td, "workflows", wf.name, "anchors")
    os.makedirs(wf_dir, exist_ok=True)
    img = Image.new("RGB", (200, 100), (10, 20, 30))
    click_path = os.path.join(wf_dir, "s1_click_frame.png")
    img.save(click_path)
    after_path = os.path.join(wf_dir, "s1_after.png")

    with patch("workflow_folder.workflow_dir", return_value=os.path.join(td, "workflows", wf.name)):
        with patch("success_signals.wait_for_settle", return_value=1240):
            with patch("success_signals.snapshot_structural_state", return_value={
                "foreground_title": "Apollo Sidebar", "window_titles": ["Apollo Sidebar"],
            }):
                with patch("show_capture._grab_screen", return_value=(img, (0, 0))):
                    with patch("app_ui_guard.is_own_window", return_value=False):
                        af = capture_after_frame(wf, s.id)
    _pass("after_frame path set", bool(af and af.get("path")))
    _pass("settle_ms recorded", af.get("settle_ms") == 1240, af)
    _pass("window_title stored", af.get("window_title") == "Apollo Sidebar")
    s.after_frame = af
    s.before_state = s.anchors[0]["structural_state"]
    with patch("workflow_folder.workflow_dir", return_value=os.path.join(td, "workflows", wf.name)):
        with patch("success_signals.capture_after_frame", return_value=af):
            out = _maybe_finalize_step_capture(wf, s.id)
    _pass("finalize ok", out and out.get("ok"))
    print("PART 1 ALL CHECKS PASSED")


def test_part2_derive_tier1_title():
    from success_signals import derive_success_signals
    from teaching import TaughtStep

    step = TaughtStep(id="s1", order=0, user_description="open apollo")
    step.before_state = {"foreground_title": "Oliane | LinkedIn", "window_titles": ["LinkedIn"]}
    step.after_frame = {
        "path": "anchors/s1_after.png",
        "structural_state": {
            "foreground_title": "LinkedIn Sidebar - Apollo",
            "window_titles": ["LinkedIn Sidebar - Apollo", "LinkedIn"],
        },
    }
    with patch("success_signals._tier2_signal", return_value=None) as t2:
        sigs = derive_success_signals(step, "_wf")
    _pass("tier1 title signal", sigs and sigs[0].get("kind") == "window_title_changed")
    _pass("cost free", sigs[0].get("cost") == "free")
    _pass("no tier2 call", t2.call_count == 0)


def test_part2_derive_tier1_element():
    from success_signals import derive_success_signals
    from teaching import TaughtStep

    step = TaughtStep(id="s1", order=0, user_description="x")
    step.before_state = {
        "foreground_title": "LinkedIn",
        "window_titles": ["LinkedIn"],
        "a11y_elements": [{"name": "Save", "control_type": "Button"}],
    }
    step.after_frame = {
        "path": "anchors/s1_after.png",
        "structural_state": {
            "foreground_title": "LinkedIn",
            "window_titles": ["LinkedIn"],
            "a11y_elements": [
                {"name": "Save", "control_type": "Button"},
                {"name": "Apollo panel", "control_type": "Pane"},
            ],
        },
    }
    sigs = derive_success_signals(step, "_wf")
    kinds = [s.get("kind") for s in sigs]
    _pass("element appeared", "a11y_element_appeared" in kinds, kinds)


def test_part2_derive_tier2_vision():
    from success_signals import derive_success_signals
    from teaching import TaughtStep

    step = TaughtStep(id="s1", order=0, user_description="x")
    step.before_state = {"foreground_title": "Same", "window_titles": ["Same"]}
    step.after_frame = {
        "path": "anchors/s1_after.png",
        "structural_state": {"foreground_title": "Same", "window_titles": ["Same"]},
    }
    fake_t2 = {
        "kind": "vision_panel_appeared",
        "detail": "a new panel or dialog appeared (Apollo)",
        "cost": "vision",
        "confidence": "medium",
        "check": {"type": "vision_panel"},
    }
    with patch("success_signals._before_frame_path", return_value="/b.png"):
        with patch("success_signals._abs_frame_path", return_value="/a.png"):
            with patch("success_signals._tier2_signal", return_value=fake_t2) as t2:
                sigs = derive_success_signals(step, "_wf")
    _pass("tier2 used when tier1 empty", sigs and sigs[0].get("cost") == "vision")
    _pass("exactly one tier2 call", t2.call_count == 1)


def test_part3_confirmation_not_open_question():
    from teaching import TaughtWorkflow, save_taught, get_step
    from teach_loop import start_training, handle_success_confirm, add_step, set_context, _ask_success_confirm

    wf = TaughtWorkflow(name="_succ_p3")
    set_context(wf, "t")
    s = add_step(wf, "click apollo")
    save_taught(wf)
    qs = start_training(wf, s.id)
    _pass("no open success question", not any("How will I know" in q for q in qs), qs)
    signal = {
        "kind": "window_title_changed",
        "detail": "foreground title changed from 'A' to 'B'",
        "evidence": {"before": "A", "after": "B"},
        "cost": "free",
        "check": {"type": "foreground_title", "expected": "B", "before": "A"},
    }
    _ask_success_confirm(s, signal)
    _pass("confirmation asked", any(q.get("kind") == "success_confirm" for q in s.qa_history))
    handle_success_confirm(wf, s.id, "yes")
    step = get_step(wf, s.id)
    u = step.understanding or {}
    _pass("source derived", u.get("success_source") == "derived")
    _pass("evidence stored", bool(u.get("success_evidence")))

    wf2 = TaughtWorkflow(name="_succ_p3b")
    set_context(wf2, "t")
    s_b = add_step(wf2, "click apollo")
    _ask_success_confirm(s_b, signal)
    handle_success_confirm(wf2, s_b.id, "no, it's something else: dialog closes")
    step2 = get_step(wf2, s_b.id)
    u2 = step2.understanding or {}
    _pass("user override", u2.get("success_source") == "user")
    _pass("candidates kept", "success_candidates" in u2 or step2.success_candidates is not None)
    print("PART 3 ALL CHECKS PASSED")


def test_part4_chain_frames():
    from teaching import TaughtWorkflow, save_taught
    from teach_loop import add_step, set_context, explain_understanding
    from success_signals import link_expected_start_frame, expected_start_note

    wf = TaughtWorkflow(name="_succ_p4")
    set_context(wf, "ctx")
    s1 = add_step(wf, "open apollo panel")
    s2 = add_step(wf, "click compose")
    s1.after_frame = {
        "path": "anchors/s1_after.png",
        "window_title": "LinkedIn Sidebar - Apollo",
    }
    link_expected_start_frame(wf, s1)
    save_taught(wf)
    _pass("expected_start_frame set", s2.expected_start_frame == "anchors/s1_after.png")
    note = expected_start_note(wf, s2)
    _pass("start note mentions prior", note and "s1" in note, note)
    u = explain_understanding(wf, s2.id)
    joined = " ".join(u.get("assumptions") or [])
    _pass("assumption mentions apollo", "Apollo" in joined or "s1" in joined, joined)
    print("PART 4 ALL CHECKS PASSED")


def test_part5_verify_structural():
    from success_signals import verify_success_check
    from teaching import TaughtStep

    step = TaughtStep(id="s1", order=0, user_description="x")
    step.understanding = {
        "success_check": "foreground title changed",
        "success_source": "derived",
        "success_evidence": {
            "check": {"type": "foreground_title", "expected": "LinkedIn Sidebar - Apollo", "before": "LinkedIn"},
        },
    }
    with patch("ui_runner.foreground_title", return_value="LinkedIn Sidebar - Apollo"):
        v = verify_success_check(step)
    _pass("success zero vision", v.get("ok") is True and v.get("cost") == "free", v)
    with patch("ui_runner.foreground_title", return_value="Oliane Piana | LinkedIn"):
        v2 = verify_success_check(step)
    _pass("failure names expected vs actual", "expected" in (v2.get("reason") or ""), v2.get("reason"))
    _pass("structural fail", v2.get("ok") is False)

    step3 = TaughtStep(id="s3", order=0, user_description="x")
    step3.understanding = {
        "success_check": "title",
        "success_evidence": {
            "check": {
                "type": "foreground_title",
                "expected": "Oliane Piana | LinkedIn - Google Chrome",
                "before": "Extensions",
            },
        },
    }
    same = {"foreground_title": "Oliane Piana | LinkedIn - Google Chrome", "a11y_elements": []}
    v4 = verify_success_check(step3, before_demo=same, after_demo=same)
    _pass("rejects no-op demo", v4.get("ok") is False, v4.get("reason"))

    with patch("ui_runner.foreground_title", return_value="MimicAgent and 8 more pages - Personal - Microsoft Edge"):
        chrome = TaughtStep(id="s2", order=0, user_description="x")
        chrome.understanding = {
            "success_check": "title",
            "success_evidence": {
                "check": {
                    "type": "foreground_title",
                    "expected": "Oliane Piana | LinkedIn - Google Chrome",
                    "before": "Extensions",
                },
            },
        }
        with patch("success_signals._top_level_window_titles", return_value=[
            "Oliane Piana | LinkedIn - Google Chrome",
            "Program Manager",
        ]):
            v3 = verify_success_check(chrome)
    _pass("ignores own UI foreground when target browser is open", v3.get("ok") is True, v3)

    profile_step = TaughtStep(id="s4", order=0, user_description="click extensions")
    profile_step.varies_note = "{linkedin_profile}"
    profile_step.parameters = ["{linkedin_profile}"]
    profile_step.understanding = {
        "success_check": "foreground title changed from 'Extensions' to 'Oliane Piana | LinkedIn - Google Chrome'",
        "varies_each_run": ["{linkedin_profile}"],
        "success_evidence": {
            "check": {
                "type": "foreground_title",
                "expected": "Oliane Piana | LinkedIn - Google Chrome",
                "before": "Extensions",
            },
        },
    }
    with patch("success_signals._foreground_for_success_check", return_value="Omid Ghiam | LinkedIn - Google Chrome"):
        v5 = verify_success_check(profile_step, os_input_calls=2)
    _pass("accepts any linkedin chrome profile when profile varies", v5.get("ok") is True, v5)
    print("PART 5 ALL CHECKS PASSED")


def test_part5_demo_manual_frame():
    from teaching import TaughtWorkflow, save_taught
    from teach_loop import add_step, set_context, resolve_action
    from teach_compile import demo_taught_step

    wf = TaughtWorkflow(name="_succ_p5b")
    set_context(wf, "t")
    s = add_step(wf, "click notepad")
    s.expected_start_frame = "anchors/s0_after.png"
    s.action = {"action": "click", "elem_name": "Text editor", "elem_type": "Document", "window_title": "Notepad"}
    s.understanding = {"success_check": "ok", "success_evidence": {"check": {"type": "foreground_title", "expected": "Notepad"}}}
    s.status = "understood"
    save_taught(wf)
    with patch("ui_runner.run_verified_plan", return_value={"ok": True, "results": []}):
        with patch("success_signals.verify_success_check", return_value={"ok": True, "reason": "ok", "cost": "free"}):
            d = demo_taught_step(wf, s.id, mode="manual")
    _pass("manual surfaces expected frame", d.get("expected_start_frame") == "anchors/s0_after.png")
    print("PART 5b ALL CHECKS PASSED")


def main():
    print("=" * 70)
    print("Derived success signals self-test")
    print("=" * 70)
    test_part1_after_frame()
    test_part2_derive_tier1_title()
    test_part2_derive_tier1_element()
    test_part2_derive_tier2_vision()
    test_part3_confirmation_not_open_question()
    test_part4_chain_frames()
    test_part5_verify_structural()
    test_part5_demo_manual_frame()
    print("=" * 70)
    print("ALL SUCCESS SIGNAL CHECKS PASSED")


if __name__ == "__main__":
    main()
