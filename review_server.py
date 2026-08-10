"""
Visual workflow review — DISPLAY adapter (HTTP face).

Thin stdlib server over review_backend.py. No business logic here.
Bind: 127.0.0.1:8765 (local only).
"""

import json
import os
import mimetypes
import threading
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

import review_backend

HOST = "127.0.0.1"
PORT = 8765
UI_FILE = "review_ui.html"
CAPTURES_DIR = "captures"

_run_lock = threading.Lock()
_run_busy = False


def _json_bytes(obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "MimicReview/1.0"

    def log_message(self, fmt, *args):
        print(f"  [review-http] {self.address_string()} {fmt % args}")

    def _send(self, status, body, content_type, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/" or path == "/index.html":
            self._serve_ui()
            return

        if path == "/api/steps":
            try:
                steps = review_backend.load_workflow_for_review()
                # Drop brain-only fields the UI does not need
                public = []
                for s in steps:
                    public.append({
                        "index": s.get("index"),
                        "kind": s.get("kind"),
                        "description": s.get("description"),
                        "screenshot_url": s.get("screenshot_url"),
                        "editable_note": s.get("editable_note") or "",
                        "deleted": bool(s.get("deleted")),
                    })
                status, body, ctype = _json_bytes({"ok": True, "steps": public})
                self._send(status, body, ctype)
            except Exception as e:
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=500
                )
                self._send(status, body, ctype)
            return

        if path.startswith("/screenshots/"):
            name = path[len("/screenshots/"):]
            self._serve_screenshot(name)
            return

        status, body, ctype = _json_bytes(
            {"ok": False, "error": f"not found: {path}"}, status=404
        )
        self._send(status, body, ctype)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/save":
            try:
                data = self._read_json()
                steps = data.get("steps") if isinstance(data, dict) else data
                result = review_backend.save_workflow_edits(steps)
                status, body, ctype = _json_bytes({"ok": True, **result})
                self._send(status, body, ctype)
            except Exception as e:
                traceback.print_exc()
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=500
                )
                self._send(status, body, ctype)
            return

        if path == "/api/run":
            global _run_busy
            try:
                data = self._read_json()
            except Exception:
                data = {}
            require_approval = True
            if isinstance(data, dict) and "require_approval" in data:
                require_approval = bool(data.get("require_approval"))

            with _run_lock:
                if _run_busy:
                    status, body, ctype = _json_bytes(
                        {"ok": False, "error": "a run is already in progress",
                         "started": False},
                        status=409,
                    )
                    self._send(status, body, ctype)
                    return
                _run_busy = True

            def _worker():
                global _run_busy
                try:
                    print("\n[review] === harness run started "
                          "(approvals in this terminal) ===\n")
                    result = review_backend.run_reviewed_workflow(
                        require_approval=require_approval
                    )
                    n = len(result) if result is not None else 0
                    print(f"\n[review] === harness run finished "
                          f"({n} transcript records) ===\n")
                except Exception:
                    print("\n[review] === harness run FAILED ===")
                    traceback.print_exc()
                finally:
                    with _run_lock:
                        _run_busy = False

            threading.Thread(target=_worker, daemon=True).start()
            status, body, ctype = _json_bytes(
                {"ok": True, "started": True,
                 "message": "running — watch the terminal for approvals"}
            )
            self._send(status, body, ctype)
            return

        status, body, ctype = _json_bytes(
            {"ok": False, "error": f"not found: {path}"}, status=404
        )
        self._send(status, body, ctype)

    def _serve_ui(self):
        if os.path.isfile(UI_FILE):
            with open(UI_FILE, "rb") as f:
                body = f.read()
            self._send(200, body, "text/html; charset=utf-8")
            return
        # Task 2 placeholder until Task 3 adds review_ui.html
        html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>MimicAgent Review</title></head>
<body style="font-family:sans-serif;max-width:40rem;margin:2rem auto;padding:0 1rem">
  <h1>MimicAgent Visual Review</h1>
  <p>API is up. The full UI page (<code>review_ui.html</code>) is not present yet.</p>
  <p>Try <a href="/api/steps"><code>GET /api/steps</code></a>.</p>
</body></html>
"""
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_screenshot(self, name):
        # Prevent path traversal — only a bare filename under captures/
        name = os.path.basename(unquote(name or ""))
        if not name or name in (".", ".."):
            status, body, ctype = _json_bytes(
                {"ok": False, "error": "bad name"}, status=400
            )
            self._send(status, body, ctype)
            return
        path = os.path.join(CAPTURES_DIR, name)
        if not os.path.isfile(path):
            status, body, ctype = _json_bytes(
                {"ok": False, "error": "screenshot not found"}, status=404
            )
            self._send(status, body, ctype)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            body = f.read()
        self._send(200, body, ctype)


def main():
    server = ThreadingHTTPServer((HOST, PORT), ReviewHandler)
    url = f"http://{HOST}:{PORT}/"
    print(f"MimicAgent review server listening on {url}", flush=True)
    print("  GET  /              -> review UI", flush=True)
    print("  GET  /api/steps     -> load_workflow_for_review()", flush=True)
    print("  GET  /screenshots/  -> capture images", flush=True)
    print("  POST /api/save      -> save_workflow_edits()", flush=True)
    print("  POST /api/run       -> run_reviewed_workflow() (background)", flush=True)
    print("Ctrl+C to stop.\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
