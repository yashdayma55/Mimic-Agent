"""Two-stage approval: understanding then behaviour. Ground-truth where possible."""

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

MARKER = "MARKER_TWOSTAGE"


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def _kill_notepad():
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
    time.sleep(0.4)


def _sentences(text: str) -> int:
    import re
    return len([p for p in re.split(r"[.!?]+", (text or "").strip()) if p.strip()])


def phase_a():
    print("\n=== PHASE A explain_understanding ===")
    from teach_loop import add_step, explain_understanding, set_context
    from teaching import TaughtWorkflow

    name = "_gate_a"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "copy an email then paste it")
    s1 = add_step(wf, "copy the person's email")
    s1.produces = ["{recipient_email}"]
    s1.status = "approved"
    from teaching import save_taught
    save_taught(wf)
    s2 = add_step(wf, "paste the email into the recipient box")
    s2.qa_history.append({
        "q": "How will I know this step succeeded?",
        "a": "the recipient box shows the pasted email",
        "source": "chat",
    })
    from teaching import save_taught as _s
    _s(wf)
    u = explain_understanding(wf, s2.id)
    print("  understanding:")
    for k, v in u.items():
        print(f"    {k}: {v!r}")
    keys = (
        "target", "action", "varies_each_run", "constants",
        "uses_from_earlier", "success_check", "assumptions", "plain_summary",
    )
    for k in keys:
        _pass(f"has {k}", k in u)
    uses = u.get("uses_from_earlier") or []
    _pass("uses_from_earlier names s1", any(x.get("from_step") == s1.id for x in uses), str(uses))
    n = _sentences(u.get("plain_summary") or "")
    _pass("plain_summary 2-4 sentences", 2 <= n <= 4, f"{n} sentences")
    _pass("assumptions non-empty", bool(u.get("assumptions")))
    _pass("action is paste", u.get("action") == "paste", repr(u.get("action")))
    _pass("plain_summary has no placeholder", "unknown" not in (u.get("plain_summary") or "").lower())


def phase_a_gaps():
    print("\n=== PHASE A gaps: paste vs vague ===")
    from teach_loop import add_step, approve_understanding, explain_understanding, set_context
    from teaching import TaughtWorkflow, TeachingError

    name = "_gate_a_gap"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "paste an email")
    s = add_step(wf, "paste the email into the recipient box")
    u = explain_understanding(wf, s.id)
    print("  paste understanding action:", u.get("action"), "summary:", u.get("plain_summary"))
    _pass("action is paste", u.get("action") == "paste", repr(u.get("action")))

    s2 = add_step(wf, "handle the popup")
    u2 = explain_understanding(wf, s2.id)
    print("  vague understanding:")
    for k, v in u2.items():
        print(f"    {k}: {v!r}")
    _pass("action is None", u2.get("action") is None, repr(u2.get("action")))
    gap = " ".join(u2.get("assumptions") or []).lower()
    _pass("assumptions mention the gap", "action" in gap or "map" in gap or "closed" in gap, gap)
    q = u2.get("clarifying_question") or u2.get("followup_question")
    _pass("exactly one clarifying question", isinstance(q, str) and bool(q.strip()), repr(q))
    summary = (u2.get("plain_summary") or "").lower()
    _pass("plain_summary has no placeholder", "unknown" not in summary and " none" not in summary)
    try:
        approve_understanding(wf, s2.id)
        _pass("approve_understanding blocked", False)
    except TeachingError as e:
        print(f"  [PASS] approve_understanding raises ({e})")


def phase_b():
    print("\n=== PHASE B split approval ===")
    from teach_compile import compile_taught
    from teach_loop import (
        add_step,
        approve_behaviour,
        approve_understanding,
        explain_understanding,
        reject_understanding,
        set_context,
    )
    from teaching import TaughtWorkflow, TeachingError

    name = "_gate_b"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "click a thing")
    s = add_step(wf, "click the Apollo dropdown")
    explain_understanding(wf, s.id)
    try:
        approve_behaviour(wf, s.id)
        _pass("cannot approve behaviour yet", False)
    except TeachingError:
        print("  [PASS] approve_behaviour blocked before demo")
    approve_understanding(wf, s.id)
    from teaching import get_step, load_taught
    _pass("status understood", get_step(load_taught(name), s.id).status == "understood")
    try:
        compile_taught(wf)
        _pass("compile understood refused", False)
    except TeachingError as e:
        print(f"  [PASS] compile refused ({e})")
    reject_understanding(wf, s.id, "it is the extensions menu item, not a page button")
    step = get_step(load_taught(name), s.id)
    _pass("back to questioning", step.status == "questioning")
    _pass(
        "correction in qa_log",
        any("extensions menu" in (q.get("a") or "") for q in step.qa_history),
    )


def phase_c():
    print("\n=== PHASE C demo replay vs manual ===")
    import os_input
    from teach_loop import (
        add_step,
        approve_step,
        demo_step,
        prepare_state,
        set_context,
    )
    from teaching import TaughtWorkflow, load_taught

    name = "_gate_c"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    os.makedirs(wd, exist_ok=True)
    path = os.path.join(wd, "demo.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("")
    wf = TaughtWorkflow(name=name)
    set_context(wf, "notepad")
    s1 = add_step(wf, "open the file", "{filename}")
    s2 = add_step(wf, "click the text editor in Notepad")
    s3 = add_step(wf, f'type "{MARKER}" into Notepad')
    approve_step(wf, s1.id, skip_rehearsal=True)
    approve_step(wf, s2.id, skip_rehearsal=True)
    # s3 not approved
    from teach_loop import explain_understanding, approve_understanding, resolve_action
    explain_understanding(wf, s3.id)
    approve_understanding(wf, s3.id)
    get_step = __import__("teaching").get_step
    s3o = get_step(wf, s3.id)
    s3o.action = resolve_action(s3o)

    _kill_notepad()
    prep = prepare_state(wf, s3.id, "replay", test_values={"filename": path})
    print("  replay prepare", prep)
    _pass("replay ran s1 and s2", prep.get("ok") is True and set(prep.get("ran") or []) == {s1.id, s2.id}, str(prep))
    os_input.reset_calls()
    d = demo_step(wf, s3.id, test_values={"filename": path}, mode="replay")
    print("  demo", d)
    _pass("demo ok", d.get("ok") is True, d.get("reason"))
    import os_input as oi
    oi.hotkey("ctrl+s")
    time.sleep(0.4)
    body = open(path, encoding="utf-8").read()
    _pass("marker on disk after save", MARKER.lower() in body.lower(), repr(body)[:80])

    _kill_notepad()
    os_input.reset_calls()
    d2 = demo_step(wf, s3.id, mode="manual")
    print("  manual closed", d2)
    _pass("manual closed ok=False", d2.get("ok") is False, d2.get("reason"))
    _pass("missing window", "not found" in (d2.get("reason") or "").lower(), d2.get("reason"))
    _pass("zero os_input", d2.get("os_input_calls") == 0, str(d2))

    wf2 = load_taught(name)
    s2b = get_step(wf2, s2.id)
    s2b.action = dict(s2b.action or {})
    s2b.action["elem_name"] = "ZZZ_NO_SUCH"
    _kill_notepad()
    with open(path, "w", encoding="utf-8") as f:
        f.write("")
    broken = prepare_state(wf2, s3.id, "replay", test_values={"filename": path})
    print("  broken replay", broken)
    _pass("reports step 2", broken.get("failed_step") == s2.id, str(broken))
    _pass("did not claim success", broken.get("ok") is False)
    _kill_notepad()


def phase_d():
    print("\n=== PHASE D reflection mismatch ===")
    from teach_loop import add_step, explain_understanding, reflect_on_demo, set_context
    from teaching import TaughtWorkflow, save_taught

    name = "_gate_d"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "demo")
    s = add_step(wf, "click the OK button")
    s.qa_history.append({"q": "How will I know this step succeeded?", "a": "a dialog appears", "source": "chat"})
    save_taught(wf)
    explain_understanding(wf, s.id)
    s = __import__("teaching").get_step(wf, s.id)
    s.demo = {"ok": True, "reason": "focus changed", "observed": "focus changed to Edit", "mode": "manual"}
    r = reflect_on_demo(wf, s.id)
    print("  reflection", r)
    _pass("matches_understanding false", r.get("matches_understanding") is False)
    _pass("differences non-empty", bool(r.get("differences")), str(r.get("differences")))


def phase_e():
    print("\n=== PHASE E endpoints ===")
    import json
    import threading
    import urllib.error
    import urllib.request
    from review_server import ReviewHTTPServer, ReviewHandler

    httpd = ReviewHTTPServer(("127.0.0.1", 0), ReviewHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"

    def req(method, url, body=None):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(url, data=data, method=method)
        if data:
            r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    name = "_gate_e"
    req("POST", base + "/api/teach/context", {"name": name, "text": "x"})
    st, body = req("POST", base + "/api/teach/step", {"name": name, "description": "click the Apollo dropdown"})
    sid = body["step"]["id"]
    st, body = req("POST", base + "/api/teach/explain", {"name": name, "step_id": sid})
    _pass("explain 200", st == 200 and "understanding" in body, str(body)[:80])
    st, body = req("POST", base + "/api/teach/approve-understanding", {"name": name, "step_id": sid})
    _pass("approve understanding 200", st == 200, str(body)[:80])
    st, body = req("POST", base + "/api/teach/approve-behaviour", {"name": name, "step_id": sid})
    _pass("approve behaviour blocked", st == 400 and body.get("ok") is False, str(body))
    st, body = req("POST", base + "/api/teach/compile", {"name": name})
    _pass("compile refused understood", st == 400 or body.get("ok") is False, str(body)[:100])
    httpd.shutdown()


def main():
    print("=" * 70)
    print("TWO-STAGE APPROVAL A-E")
    print("=" * 70)
    phase_a()
    phase_a_gaps()
    phase_b()
    phase_c()
    phase_d()
    phase_e()
    print("\nALL TWO-STAGE CHECKS PASSED")


if __name__ == "__main__":
    main()
