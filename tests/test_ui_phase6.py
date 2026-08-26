"""Phase 6: endpoints, validator gate, terminal run state."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

import ui_backend
from review_server import ReviewHTTPServer, ReviewHandler


def _req(method, url, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode(errors="replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"raw": raw[:80]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"raw": raw}
        return e.code, payload


def main():
    print("=" * 70)
    print("PHASE 6 UI/API tests")
    print("=" * 70)
    httpd = ReviewHTTPServer(("127.0.0.1", 0), ReviewHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"

    st, body = _req("GET", base + "/")
    print("GET /", st, type(body))
    assert st == 200

    st, body = _req("POST", base + "/api/plan/validate", {
        "plan": {"nodes": [{"id": "n1", "action": "shell", "value": "x"}]}
    })
    print("validate shell", st, body.get("ok"), body.get("executed"), body.get("os_input_calls"))
    assert body.get("ok") is False
    assert body.get("executed") is False
    assert body.get("os_input_calls") == 0

    st, body = _req("POST", base + "/api/run", {
        "plan": {"nodes": [{"id": "n1", "action": "shell", "value": "x"}]}
    })
    print("run invalid plan", st, body.get("executed"))
    assert st == 400
    assert body.get("executed") is False

    st, body = _req("POST", base + "/api/plan/propose", {"text": "open notepad"})
    print("propose", st, body.get("ok"), bool(body.get("plan")))
    assert st == 200 and body.get("plan")

    NAME = "_phase6_term"
    ui_backend.seed_steps(NAME, [{
        "kind": "reason",
        "description": "noop",
        "goal": "do nothing harmful",
    }])
    ui_backend.save_workflow(NAME)
    r = ui_backend.run_workflow(NAME, inputs={}, require_approval=False)
    rid = r["run_id"]
    stt = None
    for _ in range(40):
        stt = ui_backend.run_status(rid)
        if not stt.get("running"):
            break
        time.sleep(0.05)
    print("terminal state", stt.get("state"), "running", stt.get("running"))
    assert stt.get("state") in ("finished", "failed", "stopped")
    assert stt.get("running") is False
    print("PASS terminal state", stt.get("state"))
    httpd.shutdown()
    print("PHASE 6 ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
