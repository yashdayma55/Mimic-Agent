"""Hit each Batch B Task 3 endpoint once. No browser UI."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import ui_backend
from review_server import HOST, ReviewHTTPServer, ReviewHandler

PORT = 8767
BASE = f"http://{HOST}:{PORT}"
NAME = "_task3_selftest"


def _req(method, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type") or ""
            if "json" in ctype:
                parsed = json.loads(raw.decode("utf-8"))
                return resp.status, _shape(parsed)
            return resp.status, f"{ctype} bytes={len(raw)}"
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw[:80]
        return e.code, _shape(parsed)


def _shape(obj, depth=0):
    if depth > 4:
        return "..."
    if isinstance(obj, dict):
        return {k: _shape(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        if not obj:
            return []
        return [_shape(obj[0], depth + 1), f"... len={len(obj)}"] if len(obj) > 1 else [_shape(obj[0], depth + 1)]
    if isinstance(obj, str):
        return f"str:{len(obj)}"
    return type(obj).__name__ if not isinstance(obj, (int, float, bool, type(None))) else obj


def main():
    ui_backend.seed_steps(NAME, [{
        "kind": "reason",
        "description": "task3 seed",
        "goal": "noop",
    }])
    cap = ui_backend.resolve_paths(NAME)["captures_dir"] if False else None
    from workflow_folder import resolve_paths
    cap = resolve_paths(NAME)["captures_dir"]
    os.makedirs(cap, exist_ok=True)
    png = os.path.join(cap, "dot.png")
    with open(png, "wb") as f:
        f.write(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00"
            b"\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
            b"\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x00\x05\x18\xd8N"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

    server = ReviewHTTPServer((HOST, PORT), ReviewHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)

    hits = [
        ("GET", "/api/workflows", None),
        ("GET", f"/api/record/status?name={NAME}", None),
        ("GET", f"/api/steps?name={NAME}", None),
        ("POST", "/api/step/update", {"name": NAME, "index": 0, "patch": {"description": "edited via http"}}),
        ("POST", "/api/step/insert", {"name": NAME, "after_index": 0, "description": "inserted via http", "kind": "reason"}),
        ("POST", "/api/workflow/save", {"name": NAME}),
        ("POST", "/api/run", {"name": NAME, "inputs": {}, "require_approval": False}),
    ]
    print("TASK 3 endpoint hits")
    for method, path, body in hits:
        status, shape = _req(method, path, body)
        print(f"  {status} {method:4} {path}  {shape}")

    # run status + answer (no wait if already done)
    st, shape = _req("POST", "/api/run", {"name": NAME, "inputs": {}, "require_approval": True})
    print(f"  {st} POST /api/run (approval)  {shape}")
    run_id = None
    # fetch run_id from a live call
    import json as _json
    req = urllib.request.Request(
        BASE + "/api/run",
        data=json.dumps({"name": NAME, "inputs": {}, "require_approval": True}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        run_id = json.loads(resp.read().decode())["run_id"]
    time.sleep(0.15)
    st, shape = _req("GET", f"/api/run/status?run_id={run_id}", None)
    print(f"  {st} GET  /api/run/status?run_id=  {shape}")
    st, shape = _req("POST", "/api/run/answer", {"run_id": run_id, "answer": "y"})
    print(f"  {st} POST /api/run/answer  {shape}")

    st, shape = _req("GET", f"/screenshots/dot.png?name={NAME}", None)
    print(f"  {st} GET  /screenshots/dot.png?name={NAME}  {shape}")

    # record start/finish (terminates recorder immediately)
    st, shape = _req("POST", "/api/record/start", {"name": NAME + "_rec"})
    print(f"  {st} POST /api/record/start  {shape}")
    st, shape = _req("GET", f"/api/record/status?name={NAME}_rec", None)
    print(f"  {st} GET  /api/record/status  {shape}")
    st, shape = _req("POST", "/api/record/finish", {"name": NAME + "_rec"})
    print(f"  {st} POST /api/record/finish  {shape}")

    server.shutdown()
    print("TASK 3 SELF-TEST DONE")


if __name__ == "__main__":
    main()
