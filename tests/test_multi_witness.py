"""Multi-witness Show me: agreement, conflict-as-question, runtime log."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def _sentences(text: str) -> int:
    return len([p for p in re.split(r"[.!?]+", (text or "").strip()) if p.strip()])


import re as _re
re = _re


def part1():
    print("=== PART 1 agreement ===")
    from show_capture import score_agreement

    overlap = {
        "a11y": {"saw": True, "rect": [0, 0, 100, 40], "account": "a Button named 'Apollo.io' inside /Pane/Menu."},
        "dom": {"saw": True, "rect": [10, 4, 90, 36], "account": "page element button 'Apollo.io'."},
        "vision": {"saw": True, "rect": [8, 2, 95, 38], "account": "a small coloured icon in a dropdown."},
    }
    agr, note = score_agreement(overlap)
    _pass("overlapping rects agree", agr == "agree", agr)
    far = {
        "a11y": {"saw": True, "rect": [0, 0, 50, 20], "account": "a Button named 'Apollo.io' inside /Pane/Menu."},
        "dom": {"saw": False, "account": "not page content — nothing here."},
        "vision": {"saw": True, "rect": [240, 0, 290, 20], "account": "a small coloured icon, third item in a dropdown."},
    }
    agr, note = score_agreement(far)
    _pass("240px apart is conflict", agr == "conflict", agr)
    _pass("conflict_note populated", bool(note), note)
    only = {
        "a11y": {"saw": False, "account": "the accessibility tree saw nothing here."},
        "dom": {"saw": False, "account": "not page content — nothing here."},
        "vision": {"saw": True, "rect": [10, 10, 40, 40], "account": "a small coloured icon in a dropdown."},
    }
    agr, _ = score_agreement(only)
    _pass("only vision is single", agr == "single", agr)
    for pack in (overlap, far, only):
        for k, w in pack.items():
            n = _sentences(w["account"])
            _pass(f"{k} account <= 2 sentences", n <= 2, f"{n}: {w['account']}")


def part2():
    print("=== PART 2 cascade (no witness voting) ===")
    from teach_loop import (
        add_step,
        apply_show_witnesses,
        set_context,
    )
    from teaching import TaughtWorkflow, get_step

    name = "_wit_conflict"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "apollo")
    s = add_step(wf, "click Apollo")
    resolution = {
        "source": "a11y",
        "reason": "structural pipeline saw the element",
        "witnesses": {
            "a11y": {
                "saw": True,
                "name": "Apollo.io",
                "control_type": "Button",
                "account": "a Button named 'Apollo.io' inside /Pane/Menu.",
            },
            "dom": {"saw": False, "account": "saw nothing"},
            "vision": {
                "saw": True,
                "account": "a small coloured icon, third item in a dropdown.",
            },
        },
        "primary": {"name": "Apollo.io", "control_type": "Button", "pipeline": "a11y"},
        "confirmation": {"confirmed_by_vision": True},
        "resolution_line": "resolved by a11y · confirmed by vision",
    }
    from teaching import save_taught
    save_taught(wf)
    apply_show_witnesses(wf, s.id, {"resolution": resolution, "witnesses": resolution})
    step = get_step(wf, s.id)
    qs = [q for q in step.qa_history if q.get("kind") == "witness_conflict"]
    _pass("no witness conflict question", len(qs) == 0, str(len(qs)))
    _pass("primary is a11y", (step.anchors[0].get("primary") or {}).get("pipeline") == "a11y")
    _pass("resolution line set", "resolved by a11y" in (step.anchors[0].get("resolution_line") or ""))
    _pass("conflict never unresolved", step.anchors[0].get("conflict_unresolved") is False)


def part3():
    print("=== PART 3 runtime fallthrough log ===")
    import anchor_repair as _ar
    from plan_schema import node_from_dict
    from ui_runner import find_window, resolve_element

    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    time.sleep(0.4)
    folder = os.path.join("workflows", "_wit_resolve")
    os.makedirs(os.path.join(folder, "repairs"), exist_ok=True)
    path = os.path.join(folder, "probe.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("witness")
    subprocess.Popen(["notepad.exe", path])
    win = None
    for _ in range(25):
        win, _title = find_window("Notepad")
        if win is not None:
            break
        time.sleep(0.2)
    _pass("notepad open", win is not None)
    el = resolve_element(win, "Text editor", "Document")
    _pass("editor found", el is not None)
    r = el.rectangle()
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    from PIL import ImageGrab

    shot = os.path.join(folder, "repairs", "halt_live.png")
    ImageGrab.grab().save(shot)
    node = node_from_dict({
        "id": "n2",
        "action": "click",
        "elem_name": "Text editor",
        "elem_type": "Document",
        "window_title": "Notepad",
    })
    repaired = _ar.apply_repair_click(node, shot, cx, cy, folder)
    data = repaired.to_dict()
    data["elem_name"] = "ZZZ_BROKEN"
    data["extra"]["anchor"]["repaired_name"] = None
    data["extra"]["anchor"]["primary_selector"] = "ZZZ_BROKEN"
    data["extra"]["anchor"]["primary"] = {"name": "ZZZ_BROKEN", "pipeline": "a11y"}
    data["extra"]["anchor"]["primary_reason"] = "all witnesses agreed"
    broken = node_from_dict(data)
    found, layer = _ar.resolve_with_anchor(broken, "Notepad")
    log = _ar.LAST_RESOLVE_LOG
    print("  layer", layer)
    print("  log", log)
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    _pass("fell through, still found", found is not None, layer)
    _pass("layer is not primary", layer != "primary", layer)
    _pass("log names the layer", layer in log, log)
    _pass("primary_reason in log", "all witnesses agreed" in log, log)


def main():
    print("=" * 70)
    print("MULTI-WITNESS CAPTURE")
    print("=" * 70)
    part1()
    part2()
    part3()
    print("ALL MULTI-WITNESS CHECKS PASSED")


if __name__ == "__main__":
    main()
