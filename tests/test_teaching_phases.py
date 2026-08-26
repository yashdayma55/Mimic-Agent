"""Phases 2-8 of per-step teaching. Effects checked on disk/process, not by silence."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

MARKER = "MARKER_9cats"

def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def _kill_notepad():
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    time.sleep(0.5)


def phase2():
    print("\n=== PHASE 2 teach loop ===")
    from teach_loop import add_step, approve_step, answer_chat, set_context, start_training
    from teaching import TaughtWorkflow, load_taught

    name = "_teach_p2"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name, context="use Apollo")
    set_context(wf, "use Apollo")
    step = add_step(wf, "click the Apollo dropdown")
    qs = start_training(wf, step.id)
    print("  questions:", qs)
    _pass("1-3 questions", 1 <= len(qs) <= 3, str(qs))
    blob = " ".join(qs).lower()
    _pass(
        "about target/variance/success",
        any(w in blob for w in ("target", "change", "same", "succeed", "success")),
        blob,
    )
    answer_chat(wf, step.id, qs[0], "the Apollo.io button")
    approve_step(wf, step.id, skip_rehearsal=True)
    loaded = load_taught(name)
    st = loaded.steps[0]
    _pass("status approved", st.status == "approved")
    _pass("single click action", (st.action or {}).get("action") == "click", str(st.action))


def phase3():
    print("\n=== PHASE 3 show-me capture ===")
    from show_capture import capture_show
    from teach_loop import add_step
    from teaching import TaughtWorkflow
    from PIL import Image

    name = "_teach_p3"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    _kill_notepad()
    subprocess.Popen(["notepad.exe"])
    time.sleep(1.0)
    wf = TaughtWorkflow(name=name)
    step = add_step(wf, "click the text editor")
    out = capture_show(wf, step.id, countdown=0)
    crop = out["crop_abs"]
    _pass("crop file exists", os.path.isfile(crop), crop)
    img = Image.open(crop)
    _pass("crop is 64x64", img.size == (64, 64), str(img.size))
    anc = out["anchor"]
    _pass("primary populated", bool(anc.get("primary")), str(anc.get("primary")))
    _pass("parent_path populated", bool(anc.get("parent_path")), str(anc.get("parent_path")))
    _pass("crop_path populated", bool(anc.get("crop_path")))
    _kill_notepad()


def phase4():
    print("\n=== PHASE 4 rehearse ===")
    import os_input
    from teach_loop import add_step, approve_step, rehearse_step, set_context
    from teaching import TaughtWorkflow

    name = "_teach_p4"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    path = os.path.join("workflows", name, "p4.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("")
    _kill_notepad()
    time.sleep(0.8)
    subprocess.Popen(["notepad.exe", path])
    time.sleep(1.2)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "notepad")
    s = add_step(wf, f'type "{MARKER}" into Notepad')
    approve_step(wf, s.id, skip_rehearsal=True)
    os_input.reset_calls()
    r = rehearse_step(wf, s.id)
    print("  open rehearse", r)
    _pass("rehearse ok", r.get("ok") is True, r.get("reason"))
    time.sleep(0.3)
    from ui_runner import find_window, resolve_element
    from ui_runner import _read_value

    win, _ = find_window("Notepad")
    el = resolve_element(win, "Text editor", "Document") if win else None
    val = _read_value(el) if el else ""
    _pass("marker in notepad", MARKER.lower() in (val or "").lower(), repr(val)[:80])
    _kill_notepad()
    os_input.reset_calls()
    r2 = rehearse_step(wf, s.id)
    print("  closed rehearse", r2)
    _pass("closed ok=False", r2.get("ok") is False, r2.get("reason"))
    _pass("missing window reason", "not found" in (r2.get("reason") or "").lower(), r2.get("reason"))
    _pass("zero os_input on closed", r2.get("os_input_calls", os_input.call_count()) == 0, str(r2))


def phase5():
    print("\n=== PHASE 5 memory graph ===")
    from memory_graph import producer_of, validate_dependencies, why
    from teaching import TaughtStep, TaughtWorkflow

    wf = TaughtWorkflow(
        name="_mem",
        steps=[
            TaughtStep(id="a", order=0, user_description="copy email", produces=["{recipient_email}"], status="approved"),
            TaughtStep(id="b", order=1, user_description="click to", status="approved"),
            TaughtStep(id="c", order=2, user_description="qa step", qa_history=[{"q": "what?", "a": "that"}], status="approved"),
            TaughtStep(id="d", order=3, user_description="paste email", consumes=["{recipient_email}"], status="approved"),
        ],
    )
    prod = producer_of(wf, "{recipient_email}")
    _pass("producer_of is step a", prod is not None and prod.id == "a")
    wf.steps = [s for s in wf.steps if s.id != "a"]
    viol = validate_dependencies(wf)
    print("  violations", viol)
    _pass("delete producer flags consumer d", any(v.get("step_id") == "d" for v in viol), str(viol))
    w = why(wf, "c")
    _pass("why returns qa", w.get("qa") == [{"q": "what?", "a": "that"}])


def phase6():
    print("\n=== PHASE 6 floating widget ===")
    from float_widget import FloatingTeacher

    hits = []
    w = FloatingTeacher(workflow="x", step_id="s1", capture_fn=lambda body: hits.append(body))
    w.launch(block=False)
    _pass("topmost", w.topmost is True, str(w.topmost))
    w.on_show()
    _pass("show calls capture once", len(hits) == 1, str(w.calls))
    w.close()


def phase7():
    print("\n=== PHASE 7 compile+run ground truth ===")
    from teach_compile import compile_taught, run_taught
    from teach_loop import add_step, approve_step, set_context
    from teaching import TaughtWorkflow, TeachingError

    name = "_teach_p7"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    os.makedirs(wd, exist_ok=True)
    f1 = os.path.join(wd, "gt_1.txt")
    f2 = os.path.join(wd, "gt_2.txt")
    for p in (f1, f2):
        with open(p, "w", encoding="utf-8") as f:
            f.write("")
    wf = TaughtWorkflow(name=name)
    set_context(wf, "notepad notes")
    s1 = add_step(wf, "open the file", "{filename}")
    s2 = add_step(wf, "click the text editor in Notepad")
    s3 = add_step(wf, f'type "{MARKER}" into Notepad')
    s4 = add_step(wf, "save with ctrl+s", "{filename}")
    for s in (s1, s2, s3, s4):
        approve_step(wf, s.id, skip_rehearsal=True)
    try:
        dirty = TaughtWorkflow(name=name, steps=wf.steps + [])
        from teaching import TaughtStep
        dirty.steps.append(TaughtStep(id="sx", order=9, user_description="draft", status="draft"))
        compile_taught(dirty)
        _pass("unapproved cannot compile", False)
    except TeachingError:
        print("  [PASS] unapproved cannot compile")
    _kill_notepad()
    out = run_taught(wf, {"filename": f1})
    print("  run1", out.get("ok"), out.get("reason"))
    time.sleep(0.4)
    body1 = open(f1, encoding="utf-8").read()
    _pass("gt_1 contains marker", MARKER.lower() in body1.lower(), repr(body1)[:80])
    _kill_notepad()
    run_taught(wf, {"filename": f2})
    time.sleep(0.4)
    body2 = open(f2, encoding="utf-8").read()
    _pass("gt_2 contains marker", MARKER.lower() in body2.lower(), repr(body2)[:80])
    _pass("two distinct files", os.path.isfile(f1) and os.path.isfile(f2))
    _kill_notepad()


def phase8():
    print("\n=== PHASE 8 endpoints ===")
    import json
    import threading
    import urllib.error
    import urllib.request
    from review_server import ReviewHTTPServer, ReviewHandler
    import ui_backend

    httpd = ReviewHTTPServer(("127.0.0.1", 0), ReviewHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"

    def req(method, url, body=None):
        data = None if body is None else json.dumps(body).encode()
        req_ = urllib.request.Request(url, data=data, method=method)
        if data:
            req_.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req_, timeout=15) as resp:
                raw = resp.read().decode()
                try:
                    return resp.status, json.loads(raw)
                except Exception:
                    return resp.status, {"raw": raw[:80]}
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw}

    st, body = req("POST", base + "/api/plan/propose", {"text": "open notepad"})
    _pass("propose disabled", st == 410 and body.get("ok") is False, str(st))
    st, body = req("POST", base + "/api/teach/context", {"name": "_teach_p8", "text": "demo"})
    _pass("teach context 200", st == 200 and body.get("ok"), str(body)[:80])
    st, body = req("POST", base + "/api/teach/step", {"name": "_teach_p8", "description": "click the Apollo dropdown"})
    _pass("teach step 200", st == 200 and body.get("ok"), str(body)[:80])
    sid = body["step"]["id"]
    st, body = req("POST", base + "/api/teach/train", {"name": "_teach_p8", "step_id": sid})
    _pass("train 200 questions", st == 200 and 1 <= len(body.get("questions") or []) <= 3)
    st, body = req("POST", base + "/api/teach/approve", {"name": "_teach_p8", "step_id": sid, "skip_rehearsal": True})
    _pass("approve 200", st == 200 and body.get("ok"), str(body)[:80])
    st, body = req("POST", base + "/api/teach/compile", {"name": "_teach_p8"})
    _pass("compile approved 200", st == 200 and body.get("ok") is not False, str(body)[:120])
    httpd.shutdown()


def main():
    print("=" * 70)
    print("TEACHING PHASES 2-8")
    print("=" * 70)
    phase2()
    phase3()
    phase4()
    phase5()
    phase6()
    phase7()
    phase8()
    print("\nALL TEACHING PHASES PASSED")


if __name__ == "__main__":
    main()
