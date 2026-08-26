"""Phase 4: halt screenshot, repair click, composite anchor."""

from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image

from anchor_repair import apply_repair_click, crop_around, resolve_with_anchor
from plan_schema import node_from_dict
from ui_runner import find_window, resolve_element

DIR = os.path.join(ROOT, "workflows", "_anchor_selftest")


def test_synthetic_repair():
    os.makedirs(os.path.join(DIR, "repairs"), exist_ok=True)
    shot = os.path.join(DIR, "repairs", "halt_n1.png")
    Image.new("RGB", (200, 200), (40, 80, 120)).save(shot)
    node = node_from_dict({"id": "n1", "action": "click", "elem_name": "Broken"})
    repaired = apply_repair_click(node, shot, 32, 32, DIR)
    anchor = (repaired.extra or {}).get("anchor") or {}
    print("anchor", anchor)
    assert os.path.isfile(anchor["crop_path"])
    crop = Image.open(anchor["crop_path"])
    print("crop size", crop.size)
    assert crop.size[0] <= 64 and crop.size[1] <= 64
    assert "primary_selector" in anchor and "parent_path" in anchor and "crop_path" in anchor
    print("PASS synthetic repair crop + three anchor fields")


def test_crop_recovers_when_primary_broken():
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    time.sleep(0.4)
    path = os.path.join(DIR, "anchor_probe.txt")
    os.makedirs(DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("anchor")
    subprocess.Popen(["notepad.exe", path])
    win = None
    for _ in range(20):
        win, title = find_window("Notepad")
        if win is not None:
            break
        time.sleep(0.2)
    assert win is not None
    el = resolve_element(win, "Text editor", "Document")
    assert el is not None
    r = el.rectangle()
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    from PIL import ImageGrab

    shot = os.path.join(DIR, "repairs", "halt_live.png")
    os.makedirs(os.path.dirname(shot), exist_ok=True)
    ImageGrab.grab().save(shot)
    node = node_from_dict({
        "id": "n2",
        "action": "click",
        "elem_name": "Text editor",
        "elem_type": "Document",
        "window_title": "Notepad",
    })
    repaired = apply_repair_click(node, shot, cx, cy, DIR)
    # break primary
    data = repaired.to_dict()
    data["elem_name"] = "ZZZ_BROKEN"
    data["extra"]["anchor"]["repaired_name"] = None
    data["extra"]["anchor"]["primary_selector"] = "ZZZ_BROKEN"
    broken = node_from_dict(data)
    found, layer = resolve_with_anchor(broken, "Notepad")
    print("resolve layer", layer, "found", bool(found))
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    assert found is not None, "template/path did not recover broken primary"
    print("PASS crop/path recovered broken primary via", layer)


def main():
    print("=" * 70)
    print("PHASE 4 halt/repair tests")
    print("=" * 70)
    test_synthetic_repair()
    test_crop_recovers_when_primary_broken()
    print("PHASE 4 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
