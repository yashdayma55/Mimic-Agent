"""
Visual workflow review — DISPLAY adapter (HTTP face).

Thin stdlib server over review_backend.py. No business logic here.
Bind: 127.0.0.1:8765 (local only).

Usage:
  python review_server.py [workflow_name]
"""

import json
import os
import sys
import mimetypes
import threading
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote, parse_qs

import review_backend
import ui_backend

HOST = "127.0.0.1"
PORT = 8765
UI_FILE = "review_ui.html"

_run_lock = threading.Lock()
_run_thread = None  # only block if this thread is genuinely still alive


def _run_is_alive():
    """True only if a harness run thread is currently alive."""
    t = _run_thread
    return t is not None and t.is_alive()


def _json_bytes(obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def _public_step(s):
    return {
        "index": s.get("index"),
        "kind": s.get("kind"),
        "description": s.get("description"),
        "screenshot_url": s.get("screenshot_url"),
        "editable_note": s.get("editable_note") or "",
        "deleted": bool(s.get("deleted")),
    }


class ReviewHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass, workflow_name=None):
        super().__init__(server_address, RequestHandlerClass)
        self.workflow_name = workflow_name


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "MimicReview/1.0"

    def _workflow_name(self):
        return getattr(self.server, "workflow_name", None)

    def _captures_dir(self):
        ctx = review_backend.get_review_context(self._workflow_name())
        return ctx.get("captures_dir") or review_backend.CAPTURES_DIR

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

    def _query_workflow(self, parsed):
        qs = parse_qs(parsed.query or "")
        if "workflow" in qs and qs["workflow"]:
            return qs["workflow"][0]
        return self._workflow_name()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        wf = self._query_workflow(parsed)

        if path == "/" or path == "/index.html":
            self._serve_ui()
            return

        if path == "/api/workflows":
            try:
                status, body, ctype = _json_bytes({
                    "ok": True,
                    "workflows": ui_backend.list_workflows(),
                })
                self._send(status, body, ctype)
            except Exception as e:
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=500
                )
                self._send(status, body, ctype)
            return

        if path == "/api/record/status":
            try:
                name = (parse_qs(parsed.query or "").get("name") or [None])[0]
                if not name:
                    raise ValueError("missing name")
                status, body, ctype = _json_bytes({
                    "ok": True, **ui_backend.recording_status(name),
                })
                self._send(status, body, ctype)
            except Exception as e:
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=400
                )
                self._send(status, body, ctype)
            return

        if path == "/api/steps":
            qs = parse_qs(parsed.query or "")
            if qs.get("name"):
                try:
                    result = ui_backend.get_steps(qs["name"][0])
                    status, body, ctype = _json_bytes({"ok": True, **result})
                    self._send(status, body, ctype)
                except Exception as e:
                    status, body, ctype = _json_bytes(
                        {"ok": False, "error": str(e)}, status=500
                    )
                    self._send(status, body, ctype)
                return

        if path == "/api/teach":
            qs = parse_qs(parsed.query or "")
            name = (qs.get("name") or [None])[0]
            if not name:
                status, body, ctype = _json_bytes({"ok": False, "error": "missing name"}, status=400)
                self._send(status, body, ctype)
                return
            status, body, ctype = _json_bytes(ui_backend.teach_get(name))
            self._send(status, body, ctype)
            return

        if path == "/api/inputs":
            qs = parse_qs(parsed.query or "")
            name = (qs.get("name") or [None])[0]
            if not name:
                status, body, ctype = _json_bytes({"ok": False, "error": "missing name"}, status=400)
                self._send(status, body, ctype)
                return
            status, body, ctype = _json_bytes({
                "ok": True, "name": name, "inputs": ui_backend.workflow_inputs(name),
            })
            self._send(status, body, ctype)
            return

        if path == "/api/run/status":
            try:
                run_id = (parse_qs(parsed.query or "").get("run_id") or [None])[0]
                if not run_id:
                    raise ValueError("missing run_id")
                status, body, ctype = _json_bytes(ui_backend.run_status(run_id))
                self._send(status, body, ctype)
            except Exception as e:
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=400
                )
                self._send(status, body, ctype)
            return

        if path == "/api/status":
            status, body, ctype = _json_bytes({
                "ok": True,
                "running": _run_is_alive(),
                "workflow": self._workflow_name(),
            })
            self._send(status, body, ctype)
            return

        if path == "/api/steps":
            try:
                bundle = review_backend.load_workflow_for_review(workflow_name=wf)
                public = [_public_step(s) for s in bundle["steps"]]
                status, body, ctype = _json_bytes({
                    "ok": True,
                    "workflow": bundle.get("workflow"),
                    "steps": public,
                })
                self._send(status, body, ctype)
            except Exception as e:
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=500
                )
                self._send(status, body, ctype)
            return

        if path.startswith("/screenshots/"):
            name = path[len("/screenshots/"):]
            self._serve_screenshot(name, parsed.query)
            return

        status, body, ctype = _json_bytes(
            {"ok": False, "error": f"not found: {path}"}, status=404
        )
        self._send(status, body, ctype)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        wf = self._query_workflow(parsed)

        if path == "/api/record/start":
            try:
                data = self._read_json()
                name = (data or {}).get("name")
                if not name:
                    raise ValueError("missing name")
                status, body, ctype = _json_bytes(
                    {"ok": True, **ui_backend.start_recording(name)}
                )
                self._send(status, body, ctype)
            except Exception as e:
                traceback.print_exc()
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=400
                )
                self._send(status, body, ctype)
            return

        if path == "/api/record/finish":
            try:
                data = self._read_json()
                name = (data or {}).get("name")
                if not name:
                    raise ValueError("missing name")
                status, body, ctype = _json_bytes(
                    {"ok": True, **ui_backend.finish_recording(name)}
                )
                self._send(status, body, ctype)
            except Exception as e:
                traceback.print_exc()
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=400
                )
                self._send(status, body, ctype)
            return

        if path == "/api/step/update":
            try:
                data = self._read_json()
                name = data.get("name")
                index = int(data.get("index"))
                patch = data.get("patch") or {}
                status, body, ctype = _json_bytes(
                    ui_backend.update_step(name, index, patch)
                )
                self._send(status, body, ctype)
            except Exception as e:
                traceback.print_exc()
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=400
                )
                self._send(status, body, ctype)
            return

        if path == "/api/step/insert":
            try:
                data = self._read_json()
                name = data.get("name")
                after = int(data.get("after_index", -1))
                desc = data.get("description") or ""
                kind = data.get("kind") or "reason"
                status, body, ctype = _json_bytes(
                    ui_backend.insert_step(name, after, desc, kind=kind)
                )
                self._send(status, body, ctype)
            except Exception as e:
                traceback.print_exc()
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=400
                )
                self._send(status, body, ctype)
            return

        if path == "/api/workflow/save":
            try:
                data = self._read_json()
                name = data.get("name")
                steps = data.get("steps")
                status, body, ctype = _json_bytes(
                    ui_backend.save_workflow(name, steps)
                )
                self._send(status, body, ctype)
            except Exception as e:
                traceback.print_exc()
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=400
                )
                self._send(status, body, ctype)
            return

        if path == "/api/plan/validate":
            data = self._read_json()
            status, body, ctype = _json_bytes(ui_backend.validate_plan_payload(data.get("plan") or data))
            self._send(status, body, ctype)
            return

        if path == "/api/plan/propose":
            status, body, ctype = _json_bytes(
                {
                    "ok": False,
                    "error": "one-shot planning removed; teach one step at a time",
                },
                status=410,
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/context":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_set_context(data.get("name"), data.get("text") or "")
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/step":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_add_step(
                    data.get("name"), data.get("description") or "", data.get("varies_note") or "",
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/patch":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_update_step(
                    data.get("name"), data.get("step_id"),
                    description=data.get("description"),
                    varies_note=data.get("varies_note"),
                    memory_note=data.get("memory_note"),
                    web_allowed=data.get("web_allowed"),
                    clear=data.get("clear"),
                    understanding=data.get("understanding"),
                    drop_photo=data.get("drop_photo"),
                    click_count=data.get("click_count"),
                    learned=data.get("learned"),
                    drop_learned_shot=data.get("drop_learned_shot"),
                    re_explain=data.get("re_explain"),
                    qa_updates=data.get("qa_updates"),
                    anchor_edits=data.get("anchor_edits"),
                    reflection=data.get("reflection"),
                    drop_qa=data.get("drop_qa"),
                    drop_anchor_index=data.get("drop_anchor_index"),
                    drop_sub_click=bool(data.get("drop_sub_click")),
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/photo":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_add_photo(
                    data.get("name"), data.get("step_id"),
                    data.get("image") or "", data.get("filename") or "shot.png",
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/delete-step":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_delete_step(data.get("name"), data.get("step_id"))
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/remove-case":
            data = self._read_json()
            try:
                result = ui_backend.teach_remove_case(
                    data.get("name"), data.get("step_id"), data.get("case_id") or "",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/observe":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_observe(
                    data.get("name"), data.get("step_id"),
                    seconds=float(data.get("seconds") or 15),
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/train":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_start(data.get("name"), data.get("step_id"))
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/answer":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_answer(
                    data.get("name"), data.get("step_id"),
                    data.get("question") or "", data.get("answer") or "",
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/capture":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_capture(
                    data.get("name"), data.get("step_id"),
                    mode=data.get("mode") or "show",
                    point=data.get("point"),
                    batch=data.get("batch"),
                    countdown=data.get("countdown"),
                    window_sec=data.get("window_sec"),
                    seconds=float(data.get("seconds") or 15),
                    click_count=data.get("click_count"),
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/show":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_show(
                    data.get("name"), data.get("step_id"),
                    point=data.get("point"), focus=bool(data.get("focus")),
                    batch=data.get("batch"),
                    countdown=data.get("countdown"),
                    window_sec=data.get("window_sec"),
                    sequential=bool(data.get("sequential")),
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/start-screen":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_explain_start(
                    data.get("name"),
                    data.get("description") or "",
                    data.get("varies_note") or "",
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/witness":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_choose_witness(
                    data.get("name"), data.get("step_id"), data.get("choice") or "",
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/float":
            data = self._read_json()
            api_url = f"http://127.0.0.1:{PORT}"
            if data.get("case_mode"):
                try:
                    result = ui_backend.teach_arm_case_float(
                        data.get("name"), data.get("step_id"),
                        click_count=data.get("click_count"),
                        api_url=api_url,
                    )
                except Exception as e:
                    result = {"ok": False, "error": str(e)}
                st = 400 if not result.get("ok") else 200
                status, body, ctype = _json_bytes(result, status=st)
                self._send(status, body, ctype)
                return
            status, body, ctype = _json_bytes(
                ui_backend.teach_arm_show(
                    data.get("name"), data.get("step_id"),
                    click_count=data.get("click_count"),
                    api_url=api_url,
                    vision_mode=bool(data.get("vision_mode")),
                    question=data.get("question") or "",
                    case_id=data.get("case_id") or "",
                )
            )
            self._send(status, body, ctype)
            return

        if path in (
            "/api/teach/float-case",
            "/api/teach/float_bar",
            "/api/teach/float-bar",
        ):
            data = self._read_json()
            api_url = f"http://127.0.0.1:{PORT}"
            try:
                result = ui_backend.teach_arm_case_float(
                    data.get("name"), data.get("step_id"),
                    click_count=data.get("click_count"),
                    api_url=api_url,
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/rehearse":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_rehearse(
                    data.get("name"), data.get("step_id"), data.get("test_values"),
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/explain":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_explain(data.get("name"), data.get("step_id"))
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/approve-understanding":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_approve_understanding(data.get("name"), data.get("step_id"))
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/reject-understanding":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_reject_understanding(
                    data.get("name"), data.get("step_id"), data.get("correction") or "",
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/prepare":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_prepare(
                    data.get("name"), data.get("step_id"),
                    data.get("mode") or "manual", data.get("test_values"),
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/demo":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_demo(
                    data.get("name"), data.get("step_id"),
                    data.get("test_values"), data.get("mode") or "manual",
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/focus-target":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_focus_target(
                    data.get("name"),
                    data.get("step_id"),
                    case_id=data.get("case_id"),
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/reflect":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_reflect(data.get("name"), data.get("step_id"))
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-remember":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_remember(
                    data.get("name"), data.get("step_id"), data.get("answer") or "",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-attach":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_attach(
                    data.get("name"),
                    data.get("halting_step_id") or data.get("step_id"),
                    data.get("answer") or "",
                    data.get("target_step_id"),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-capture-start":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_capture_start(
                    data.get("name"), data.get("step_id"),
                    click_count=int(data.get("click_count") or 1),
                    situation=data.get("situation") or "",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-grab-screen":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_grab_screen(
                    data.get("name"), data.get("step_id"),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-begin":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_begin(
                    data.get("name"),
                    data.get("step_id"),
                    when_applies=data.get("when_applies") or data.get("situation") or "",
                    what_to_do=data.get("what_to_do") or "",
                    continue_prompt=data.get("continue_prompt") or "",
                    click_count=int(data.get("click_count") or 1),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-reteach":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_reteach(
                    data.get("name"), data.get("step_id"), data.get("case_id"),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-prompt-try":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_prompt_try(
                    data.get("name"),
                    data.get("step_id"),
                    data.get("case_id"),
                    data.get("instruction") or "",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-draft-patch":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_draft_patch(
                    data.get("name"), data.get("step_id"), data,
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-save-draft":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_save_draft(
                    data.get("name"), data.get("step_id"),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-patch":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_patch(
                    data.get("name"), data.get("step_id"), data.get("case_id"), data,
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-approve":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_approve(
                    data.get("name"), data.get("step_id"), data.get("case_id"),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-demo":
            data = self._read_json()
            try:
                cont = data.get("continue_parent")
                if cont is not None:
                    cont = bool(cont)
                result = ui_backend.teach_case_demo(
                    data.get("name"),
                    data.get("step_id"),
                    data.get("case_id"),
                    continue_parent=cont,
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-fix-access-email":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_fix_access_email(
                    data.get("name"),
                    data.get("step_id"),
                    data.get("case_id"),
                    instruction=data.get("instruction") or "",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-finish":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_finish(
                    data.get("name"), data.get("step_id"),
                    click_count=data.get("click_count"),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-substep":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_sub_description(
                    data.get("name"), data.get("step_id"), data.get("description") or "",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-capture-frame":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_capture_frame(
                    data.get("name"),
                    data.get("step_id"),
                    structural=data.get("structural"),
                    synthetic_b64=data.get("synthetic_b64"),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-describe":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_describe(
                    data.get("name"),
                    data.get("step_id"),
                    data.get("description") or "",
                    click_count=int(data.get("click_count") or 1),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-authoring-cancel":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_authoring_cancel(
                    data.get("name"), data.get("step_id"),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/case-halt-dismiss":
            data = self._read_json()
            try:
                result = ui_backend.teach_case_halt_dismiss(
                    data.get("name"), data.get("step_id"),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/vision-ask":
            data = self._read_json()
            try:
                result = ui_backend.teach_vision_ask(
                    data.get("name"), data.get("step_id"), data.get("question") or "",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/vision-reply":
            data = self._read_json()
            try:
                result = ui_backend.teach_vision_reply(
                    data.get("name"),
                    data.get("step_id"),
                    data.get("reply") or data.get("question") or "",
                    regrab=bool(data.get("regrab")),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/vision-as-step":
            data = self._read_json()
            try:
                result = ui_backend.teach_vision_as_step(
                    data.get("name"),
                    data.get("step_id"),
                    data.get("remember_prompt") or data.get("remember") or "",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/vision-remove":
            data = self._read_json()
            try:
                result = ui_backend.teach_vision_remove(
                    data.get("name"), data.get("step_id"), int(data.get("index", 0)),
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/prompt-try":
            data = self._read_json()
            try:
                result = ui_backend.teach_prompt_try(
                    data.get("name"), data.get("step_id"), data.get("instruction") or "",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/prompt-save":
            data = self._read_json()
            try:
                result = ui_backend.teach_prompt_save(
                    data.get("name"), data.get("step_id"), data.get("instruction") or "",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/set-method":
            data = self._read_json()
            try:
                result = ui_backend.teach_set_method(
                    data.get("name"), data.get("step_id"), data.get("method") or "anchor",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/approve-behaviour":
            data = self._read_json()
            try:
                result = ui_backend.teach_approve_behaviour(data.get("name"), data.get("step_id"))
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/approve":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.teach_approve(
                    data.get("name"), data.get("step_id"),
                    bool(data.get("skip_rehearsal")),
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/teach/compile":
            data = self._read_json()
            try:
                result = ui_backend.teach_compile(data.get("name"), data.get("inputs"))
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            st = 400 if not result.get("ok") else 200
            status, body, ctype = _json_bytes(result, status=st)
            self._send(status, body, ctype)
            return

        if path == "/api/teach/run":
            data = self._read_json()
            try:
                status, body, ctype = _json_bytes(
                    ui_backend.teach_run(data.get("name"), data.get("inputs"))
                )
            except Exception as e:
                status, body, ctype = _json_bytes({"ok": False, "error": str(e)}, status=400)
            self._send(status, body, ctype)
            return

        if path == "/api/repair/click":
            data = self._read_json()
            status, body, ctype = _json_bytes(
                ui_backend.apply_repair(
                    data.get("name"), data.get("node_id"), data.get("x"), data.get("y"),
                )
            )
            self._send(status, body, ctype)
            return

        if path == "/api/run/answer":
            try:
                data = self._read_json()
                run_id = data.get("run_id")
                answer = data.get("answer")
                status, body, ctype = _json_bytes(
                    ui_backend.answer_run(run_id, answer)
                )
                self._send(status, body, ctype)
            except Exception as e:
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=400
                )
                self._send(status, body, ctype)
            return

        if path == "/api/run":
            try:
                data = self._read_json()
            except Exception:
                data = {}
            self._posted_json = data
            # New UI cycle: body includes `name` -> ui_backend thread runner
            if isinstance(data, dict) and data.get("plan") and not data.get("name"):
                gate = ui_backend.validate_plan_payload(data.get("plan"))
                if not gate.get("ok"):
                    gate["executed"] = False
                    status, body, ctype = _json_bytes(gate, status=400)
                    self._send(status, body, ctype)
                    return
            if isinstance(data, dict) and data.get("name"):
                try:
                    if data.get("plan"):
                        gate = ui_backend.validate_plan_payload(data.get("plan"))
                        if not gate.get("ok"):
                            gate["executed"] = False
                            status, body, ctype = _json_bytes(gate, status=400)
                            self._send(status, body, ctype)
                            return
                    result = ui_backend.run_workflow(
                        data.get("name"),
                        inputs=data.get("inputs") or {},
                        require_approval=bool(data.get("require_approval", False)),
                    )
                    code = 200 if result.get("ok") else 400
                    status, body, ctype = _json_bytes(result, status=code)
                    self._send(status, body, ctype)
                except Exception as e:
                    traceback.print_exc()
                    status, body, ctype = _json_bytes(
                        {"ok": False, "error": str(e)}, status=500
                    )
                    self._send(status, body, ctype)
                return

        if path == "/api/save":
            try:
                data = self._read_json()
                steps = data.get("steps") if isinstance(data, dict) else data
                if isinstance(data, dict) and data.get("workflow"):
                    wf = data.get("workflow")
                result = review_backend.save_workflow_edits(
                    steps, workflow_name=wf
                )
                status, body, ctype = _json_bytes({"ok": True, **result})
                self._send(status, body, ctype)
            except Exception as e:
                traceback.print_exc()
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=500
                )
                self._send(status, body, ctype)
            return

        if path == "/api/save_step":
            try:
                data = self._read_json()
                step = data.get("step") if isinstance(data, dict) else None
                if isinstance(data, dict) and data.get("workflow"):
                    wf = data.get("workflow")
                if not step:
                    raise ValueError("missing step in body")
                result = review_backend.save_workflow_step(step, workflow_name=wf)
                pub = dict(result)
                if pub.get("step"):
                    pub["step"] = _public_step(pub["step"])
                status, body, ctype = _json_bytes(pub)
                self._send(status, body, ctype)
            except Exception as e:
                traceback.print_exc()
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": str(e)}, status=500
                )
                self._send(status, body, ctype)
            return

        if path == "/api/run":
            global _run_thread
            try:
                data = getattr(self, "_posted_json", None)
                if data is None:
                    data = self._read_json()
            except Exception:
                data = {}
            require_approval = True
            start_index = 0
            if isinstance(data, dict):
                if "require_approval" in data:
                    require_approval = bool(data.get("require_approval"))
                if "start_index" in data:
                    start_index = int(data.get("start_index") or 0)
                if data.get("workflow"):
                    wf = data.get("workflow")

            with _run_lock:
                # Only 409 if a run thread is genuinely still alive
                if _run_is_alive():
                    status, body, ctype = _json_bytes(
                        {"ok": False, "error": "a run is already in progress",
                         "started": False, "running": True},
                        status=409,
                    )
                    self._send(status, body, ctype)
                    return
                _run_thread = None  # clear stale dead-thread reference

            workflow_for_run = wf

            def _worker():
                try:
                    print("\n[review] === harness run started "
                          "(approvals in this terminal) ===\n")
                    result = review_backend.run_reviewed_workflow(
                        require_approval=require_approval,
                        workflow_name=workflow_for_run,
                        start_index=start_index,
                    )
                    n = len(result) if result is not None else 0
                    print(f"\n[review] === harness run finished "
                          f"({n} transcript records) ===\n")
                except BaseException:
                    # Catch KeyboardInterrupt/SystemExit too so we always log
                    print("\n[review] === harness run FAILED / interrupted ===")
                    traceback.print_exc()
                # Thread dying clears "running" via is_alive(); no sticky bool.

            with _run_lock:
                if _run_is_alive():
                    status, body, ctype = _json_bytes(
                        {"ok": False, "error": "a run is already in progress",
                         "started": False, "running": True},
                        status=409,
                    )
                    self._send(status, body, ctype)
                    return
                t = threading.Thread(target=_worker, daemon=True)
                _run_thread = t
                t.start()

            status, body, ctype = _json_bytes({
                "ok": True,
                "started": True,
                "running": True,
                "workflow": workflow_for_run,
                "message": "running — watch the terminal for approvals",
            })
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
        html = "<html><body><p>review_ui.html missing</p></body></html>"
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_screenshot(self, name, query=""):
        qs = parse_qs(query or "")
        wf = (qs.get("name") or [None])[0] or self._workflow_name()
        rel = (qs.get("rel") or [None])[0]
        name = os.path.basename(unquote(name or "").split("?")[0])
        if not rel and (not name or name in (".", "..")):
            status, body, ctype = _json_bytes(
                {"ok": False, "error": "bad name"}, status=400
            )
            self._send(status, body, ctype)
            return
        if rel and wf:
            from workflow_folder import workflow_dir

            root = os.path.abspath(workflow_dir(wf))
            full = os.path.abspath(os.path.join(root, rel.replace("\\", "/")))
            if not (full == root or full.startswith(root + os.sep)):
                status, body, ctype = _json_bytes(
                    {"ok": False, "error": "bad path"}, status=400
                )
                self._send(status, body, ctype)
                return
            path = full
        else:
            if wf:
                from workflow_folder import resolve_paths
                cap_dir = resolve_paths(wf)["captures_dir"]
            else:
                cap_dir = self._captures_dir()
            path = os.path.join(cap_dir, name)
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


def main(workflow_name=None):
    server = ReviewHTTPServer((HOST, PORT), ReviewHandler, workflow_name=workflow_name)
    ctx = review_backend.get_review_context(workflow_name)
    url = f"http://{HOST}:{PORT}/"
    if ctx.get("name"):
        url += f"?workflow={ctx['name']}"
    print(f"MimicAgent review server listening on {url}", flush=True)
    print(f"  workflow: {ctx.get('name')!r}", flush=True)
    print(f"  captures: {ctx.get('captures_dir')}", flush=True)
    print("  GET  /              -> review UI", flush=True)
    print("  GET  /api/status    -> {running: bool}", flush=True)
    print("  GET  /api/steps     -> load_workflow_for_review()", flush=True)
    print("  GET  /screenshots/  -> workflow capture images", flush=True)
    print("  POST /api/save      -> save all steps", flush=True)
    print("  POST /api/save_step -> save one step", flush=True)
    print("  POST /api/run       -> run_reviewed_workflow() (legacy) or ui_backend if body.name", flush=True)
    print("  GET  /api/workflows          POST /api/record/start   GET /api/record/status", flush=True)
    print("  POST /api/record/finish        GET  /api/steps?name=    POST /api/step/update", flush=True)
    print("  POST /api/step/insert          POST /api/workflow/save   GET /api/run/status", flush=True)
    print("  POST /api/run/answer           GET  /screenshots/?name=", flush=True)
    print("Ctrl+C to stop.\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    wf = sys.argv[1] if len(sys.argv) > 1 else None
    main(workflow_name=wf)
