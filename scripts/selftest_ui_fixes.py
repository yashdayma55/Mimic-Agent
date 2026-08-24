"""Self-test: screenshot URLs, vision labels via finish_recording, approval bodies."""

from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
import threading
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

import ui_backend
from safety_gate import is_irreversible_step
from workflow_folder import workflow_dir

SHOT_WF = "batch_a_selftest"
VISION_SRC = "notepad_new_workflow"
VISION_COPY = "_ui_fix_selftest"
TOLLGATE_WF = "_ui_fix_tollgate"


def _print_banner(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def test_screenshots():
    _print_banner("1. Screenshots on cards (get_steps join)")
    result = ui_backend.get_steps(SHOT_WF)
    steps = result["steps"]
    with_shot = [s for s in steps if s.get("screenshot_url")]
    without = [s for s in steps if not s.get("screenshot_url")]
    print(f"workflow: {SHOT_WF}")
    print(f"cards: {len(steps)}  with screenshot: {len(with_shot)}  without: {len(without)}")
    for s in steps:
        flag = "SHOT" if s.get("screenshot_url") else "----"
        desc = (s.get("description") or "")[:60]
        print(f"  {s['index']}. [{s.get('kind')}] {flag}  {s.get('screenshot_url')}  {desc}")
    assert with_shot, "expected at least one resolved screenshot"
    sample = with_shot[0]["screenshot_url"]
    print(f"sample URL: {sample}")
    return sample


def test_http_image(sample_url):
    _print_banner("1b. GET screenshot URL -> image bytes")
    import review_server

    httpd = review_server.ReviewHTTPServer(("127.0.0.1", 0), review_server.ReviewHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        url = f"http://127.0.0.1:{port}{sample_url}"
        print(f"GET {url}")
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type")
            status = resp.status
        print(f"status={status}  content-type={ctype}  bytes={len(body)}")
        print(f"png_magic={body[:8]!r}")
        assert status == 200
        assert body[:8] == b"\x89PNG\r\n\x1a\n", "expected PNG bytes"
        print("image bytes: OK")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_vision():
    _print_banner("2. Vision labels via finish_recording")
    src = inspect.getsource(ui_backend.finish_recording)
    print("finish_recording calls transcribe:", "transcribe(" in src)
    assert "transcribe(" in src

    src_dir = workflow_dir(VISION_SRC)
    dst = workflow_dir(VISION_COPY)
    if not os.path.isdir(src_dir):
        raise SystemExit(f"missing source workflow {VISION_SRC}")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src_dir, dst)

    plan_path = os.path.join(dst, "plan.json")
    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    unlabeled = [
        s for s in plan
        if isinstance(s, dict) and "unlabeled" in (s.get("instruction") or "").lower()
    ]
    print(f"copied {VISION_SRC} -> {VISION_COPY}")
    print(f"unlabeled plan steps: {len(unlabeled)}")
    before = (unlabeled[0].get("instruction") if unlabeled else plan[0].get("instruction"))
    print(f"BEFORE (plan): {before}")

    ui_backend.finish_recording(VISION_COPY)
    after_steps = ui_backend.get_steps(VISION_COPY)["steps"]
    after = after_steps[0]["description"] if after_steps else ""
    print(f"AFTER  (card): {after}")
    for i, s in enumerate(after_steps):
        print(f"  {i}. {s.get('description')}")
    assert after, "expected a card description after finish_recording"
    assert "Click something (unlabeled" not in after
    print("vision path ran (description is no longer the unlabeled fallback)")


def test_approval_bodies_and_tollgate():
    _print_banner("3. Approval toggle request bodies + irreversible tollgate")
    off = {"name": SHOT_WF, "inputs": {}, "require_approval": False}
    on = {"name": SHOT_WF, "inputs": {}, "require_approval": True}
    print("checkbox OFF (default):")
    print("  POST /api/run  " + json.dumps(off))
    print("checkbox ON:")
    print("  POST /api/run  " + json.dumps(on))

    ui_backend.seed_steps(TOLLGATE_WF, [{
        "kind": "native",
        "description": "Click Send",
        "instruction": "Click Send",
        "action": {"action": "click"},
        "target_name": "Send",
        "target_type": "Button",
    }])
    step = ui_backend.get_steps(TOLLGATE_WF)["steps"][0]
    print("is_irreversible_step:", is_irreversible_step(step))
    assert is_irreversible_step(step)

    r = ui_backend.run_workflow(TOLLGATE_WF, inputs={}, require_approval=False)
    rid = r["run_id"]
    st = None
    for _ in range(40):
        st = ui_backend.run_status(rid)
        if st.get("awaiting") == "tollgate":
            break
        time.sleep(0.05)
    print("with require_approval=False, awaiting:", st.get("awaiting"))
    print("prompt_text starts:", (st.get("prompt_text") or "")[:80])
    assert st.get("awaiting") == "tollgate", "tollgate must still fire when approval is off"
    ui_backend.answer_run(rid, "no")
    for _ in range(40):
        st = ui_backend.run_status(rid)
        if not st.get("running"):
            break
        time.sleep(0.05)
    print("after declining tollgate: running=", st.get("running"), "error=", st.get("error"))
    assert st.get("running") is False


def main():
    sample = test_screenshots()
    test_http_image(sample)
    test_vision()
    test_approval_bodies_and_tollgate()
    print("\nALL THREE FIXES SELF-TESTED")


if __name__ == "__main__":
    try:
        main()
    finally:
        for n in (VISION_COPY, TOLLGATE_WF):
            wd = workflow_dir(n)
            if os.path.isdir(wd):
                shutil.rmtree(wd, ignore_errors=True)
