"""Always-on-top capture bar: Show me ⇄ Watch me for the active step."""

from __future__ import annotations

import threading
import time

_active = None
_lock = threading.Lock()
CAPTURE_PATH = "/api/teach/capture"
FOCUS_PATH = "/api/teach/focus-target"
CASE_GRAB_PATH = "/api/teach/case-grab-screen"
CASE_FINISH_PATH = "/api/teach/case-finish"
VISION_ASK_PATH = "/api/teach/vision-ask"


def cursor_point() -> tuple[int, int]:
    from show_capture import cursor_point as _pt

    return _pt()


def capture_endpoint_for_mode(mode: str) -> str:
    return CAPTURE_PATH


def build_capture_body(
    workflow: str,
    step_id: str,
    *,
    mode: str = "show",
    click_count: int = 1,
    countdown: float = 1.6,
    watch_seconds: float = 15,
    point: tuple[int, int] | None = None,
) -> dict:
    body: dict = {"name": workflow, "step_id": step_id, "mode": mode}
    if mode == "watch":
        body["seconds"] = max(5.0, min(60.0, float(watch_seconds)))
        return body
    if int(click_count or 1) == 2:
        body["batch"] = True
        body["countdown"] = float(countdown or 1.6)
        body["window_sec"] = 25.0
    else:
        if countdown:
            body["countdown"] = float(countdown)
        if point is not None:
            body["point"] = [int(point[0]), int(point[1])]
    return body


class FloatingTeacher:
    def __init__(self, api_url: str = "http://127.0.0.1:8765", workflow: str = "",
                 step_id: str = "", click_count: int = 1, capture_fn=None,
                 rehearse_fn=None, observe_fn=None, countdown: float = 0,
                 mode: str = "show", watch_seconds: float = 15,
                 case_phase: str | None = None, case_label: str = "",
                 vision_question: str = "", case_id: str = ""):
        self.api_url = api_url.rstrip("/")
        self.workflow = workflow
        self.step_id = step_id
        self.click_count = int(click_count or 1)
        self.mode = "vision" if mode == "vision" else ("watch" if mode == "watch" else "show")
        self.watch_seconds = float(watch_seconds or 15)
        self.capture_fn = capture_fn
        self.rehearse_fn = rehearse_fn
        self.observe_fn = observe_fn
        self.countdown = float(countdown or 0)
        self.case_phase = case_phase
        self.case_label = case_label or ""
        self.vision_question = vision_question or ""
        self.case_id = case_id or ""
        self.calls: list = []
        self.last_outcome = ""
        self._root = None
        self._status = None
        self._step_lbl = None
        self._mode_btn = None
        self._secs = None
        self._run_btn = None
        self._focus_btn = None
        self._finish_btn = None
        self._clicks = None
        self.topmost = False
        self._drag_from = None

    def set_case_authoring(
        self,
        phase: str | None,
        case_label: str = "",
        click_count: int | None = None,
    ) -> None:
        self.case_phase = phase
        self.case_label = case_label or self.case_label
        if click_count is not None:
            self.click_count = int(click_count or 1)
        self._refresh_labels()

    def set_target(self, workflow: str, step_id: str, click_count: int = 1,
                   mode: str | None = None, watch_seconds: float | None = None,
                   case_phase: str | None = None, case_label: str | None = None,
                   vision_question: str | None = None, case_id: str | None = None) -> None:
        self.workflow = workflow
        self.step_id = step_id
        self.click_count = int(click_count or 1)
        if mode is not None:
            self.mode = "vision" if mode == "vision" else ("watch" if mode == "watch" else "show")
        if watch_seconds is not None:
            self.watch_seconds = float(watch_seconds)
        if case_phase is not None:
            self.case_phase = case_phase
        if case_label is not None:
            self.case_label = case_label
        if vision_question is not None:
            self.vision_question = vision_question
        if case_id is not None:
            self.case_id = case_id or ""
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        if self._step_lbl is not None:
            try:
                if self.case_phase:
                    lbl = self.case_label or "Case"
                    self._step_lbl.config(text=lbl)
                else:
                    self._step_lbl.config(text=f"step {self.step_id or '-'}")
            except Exception:
                pass
        if self._mode_btn is not None:
            try:
                if self.case_phase == "awaiting_capture":
                    self._mode_btn.config(text="situation")
                elif self.mode == "vision":
                    self._mode_btn.config(text="ask vision")
                else:
                    other = "Watch me" if self.mode == "show" else "Show me"
                    self._mode_btn.config(text=f"⇄ {other}")
            except Exception:
                pass
        if self._run_btn is not None:
            try:
                if self.case_phase == "awaiting_capture":
                    self._run_btn.config(text="Grab screen")
                elif self.mode == "vision":
                    self._run_btn.config(text="Ask vision")
                elif self.mode == "watch":
                    self._run_btn.config(text="Watch me")
                else:
                    txt = "Show me (2)" if self.click_count == 2 else "Show me"
                    self._run_btn.config(text=txt)
            except Exception:
                pass
        if self._focus_btn is not None:
            try:
                self._focus_btn.config(text="Focus here")
            except Exception:
                pass
        if self._finish_btn is not None:
            try:
                show_finish = self.case_phase == "needs_resolution"
                if show_finish:
                    self._finish_btn.pack(fill="x", padx=8, pady=(2, 4))
                else:
                    self._finish_btn.pack_forget()
            except Exception:
                pass
        if self._clicks is not None:
            try:
                if self.case_phase:
                    self._clicks.pack(pady=2)
                    self._clicks.delete(0, "end")
                    self._clicks.insert(0, str(int(self.click_count or 1)))
                else:
                    self._clicks.pack_forget()
            except Exception:
                pass
        if self._status is not None and not self.last_outcome:
            try:
                if self.case_phase == "awaiting_capture":
                    hint = "set screen, then grab"
                elif self.case_phase == "needs_resolution":
                    hint = "teach resolution"
                    if self.click_count == 2:
                        hint += " · 2 clicks"
                elif self.mode == "vision":
                    hint = "Focus here, then Ask vision"
                else:
                    hint = "show" if self.mode == "show" else "watch"
                    if self.mode == "show" and self.click_count == 2:
                        hint += " · 2 clicks"
                self._status.config(text=hint)
            except Exception:
                pass

    def toggle_mode(self) -> None:
        if self.case_phase == "awaiting_capture" or self.mode == "vision":
            return
        self.mode = "watch" if self.mode == "show" else "show"
        self._refresh_labels()

    def _current_click_count(self) -> int:
        if self._clicks is not None:
            try:
                return max(1, min(2, int(self._clicks.get() or self.click_count or 1)))
            except Exception:
                pass
        return int(self.click_count or 1)

    def _post(self, path: str, body: dict) -> dict | None:
        self.calls.append(path)
        if path.endswith("/capture") and self.capture_fn:
            return self.capture_fn(body) or {}
        if path.endswith("/show") and self.capture_fn:
            self.capture_fn(body)
            return {}
        if path.endswith("/rehearse") and self.rehearse_fn:
            self.rehearse_fn(body)
            return {}
        if path.endswith("/observe") and self.observe_fn:
            return self.observe_fn(body)
        try:
            import json
            import urllib.request

            req = urllib.request.Request(
                self.api_url + path,
                data=json.dumps(body).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except Exception:
            return None

    def _apply_outcome(self, out: dict | None) -> None:
        if not out:
            self.last_outcome = "error"
        else:
            done = out.get("case_authoring_complete") or {}
            if done.get("case"):
                self.last_outcome = f"saved {done['case'].get('id', 'case')}"
            elif out.get("case"):
                self.last_outcome = f"saved {out['case'].get('id', 'case')}"
            else:
                lc = out.get("last_capture") or {}
                self.last_outcome = (
                    lc.get("message") or out.get("capture_message")
                    or out.get("message") or out.get("outcome") or ""
                )
                if not self.last_outcome and out.get("ok"):
                    self.last_outcome = "saved"
        if self._status is not None:
            try:
                self._status.config(text=self.last_outcome or "done")
            except Exception:
                pass

    def _apply_focus_result(self, out: dict | None) -> None:
        if not out:
            self.last_outcome = "could not focus target"
        else:
            self.last_outcome = out.get("reason") or ("focused " + (out.get("title") or "target"))
        if self._status is not None:
            try:
                self._status.config(text=self.last_outcome)
            except Exception:
                pass

    def on_run(self) -> None:
        if self.case_phase == "awaiting_capture":
            self.on_grab_screen()
            return
        if self.mode == "vision":
            self.on_ask_vision()
            return
        if self._root is not None:
            try:
                self._root.withdraw()
                self._root.update()
            except Exception:
                pass
        cc = self._current_click_count()
        self.click_count = cc
        body = build_capture_body(
            self.workflow,
            self.step_id,
            mode=self.mode,
            click_count=cc,
            countdown=self.countdown or 1.6,
            watch_seconds=float(self._secs.get()) if self._secs is not None else self.watch_seconds,
        )
        body["click_count"] = cc
        if self.mode == "show" and cc == 1:
            if self.countdown:
                time.sleep(self.countdown)
            else:
                time.sleep(0.12)
            x, y = cursor_point()
            body["point"] = [x, y]
        if self._status is not None:
            try:
                self._status.config(text="capturing…")
                if self._root is not None:
                    self._root.update()
            except Exception:
                pass
        out = self._post(CAPTURE_PATH, body)
        if self._root is not None:
            try:
                self._root.deiconify()
                self._root.attributes("-topmost", True)
            except Exception:
                pass
        self._apply_outcome(out)
        if self.case_phase == "needs_resolution" and out and out.get("case_authoring_complete"):
            self.case_phase = None
            self.case_label = ""
            self._refresh_labels()

    def on_grab_screen(self) -> None:
        if self._root is not None:
            try:
                self._root.withdraw()
                self._root.update()
            except Exception:
                pass
        for left in (3, 2, 1):
            if self._status is not None:
                try:
                    self._status.config(text=f"grab in {left}…")
                    if self._root is not None:
                        self._root.update()
                except Exception:
                    pass
            time.sleep(1)
        if self._status is not None:
            try:
                self._status.config(text="grabbing…")
                if self._root is not None:
                    self._root.update()
            except Exception:
                pass
        out = self._post(
            CASE_GRAB_PATH,
            {"name": self.workflow, "step_id": self.step_id},
        )
        if self._root is not None:
            try:
                self._root.deiconify()
                self._root.attributes("-topmost", True)
            except Exception:
                pass
        if out and out.get("needs_resolution"):
            self.case_phase = "needs_resolution"
            self.case_label = out.get("case_label") or self.case_label
            cc = out.get("click_count")
            if cc is not None:
                self.click_count = int(cc)
        self._apply_outcome(out)
        self._refresh_labels()

    def on_finish_case(self) -> None:
        cc = self._current_click_count()
        out = self._post(
            CASE_FINISH_PATH,
            {
                "name": self.workflow,
                "step_id": self.step_id,
                "click_count": cc,
            },
        )
        self._apply_outcome(out)
        if out and out.get("case"):
            self.case_phase = None
            self.case_label = ""
            self._refresh_labels()

    def on_show(self) -> None:
        """Backward-compatible alias."""
        prev = self.mode
        self.mode = "show"
        self.on_run()
        self.mode = prev

    def on_watch(self) -> None:
        prev = self.mode
        self.mode = "watch"
        self.on_run()
        self.mode = prev

    def on_focus(self) -> None:
        if self._status is not None:
            try:
                self._status.config(text="focusing…")
                if self._root is not None:
                    self._root.update()
            except Exception:
                pass
        body = {"name": self.workflow, "step_id": self.step_id}
        if self.case_id:
            body["case_id"] = self.case_id
        out = self._post(FOCUS_PATH, body)
        if self._root is not None:
            try:
                self._root.attributes("-topmost", True)
            except Exception:
                pass
        self._apply_focus_result(out)
        if self.mode == "vision" and (self.vision_question or "").strip():
            self.on_ask_vision()

    def on_ask_vision(self) -> None:
        q = (self.vision_question or "").strip()
        if not q:
            self.last_outcome = "type a question on the step card first"
            if self._status is not None:
                try:
                    self._status.config(text=self.last_outcome)
                except Exception:
                    pass
            return
        if self._root is not None:
            try:
                self._root.withdraw()
                self._root.update()
            except Exception:
                pass
        self._post(FOCUS_PATH, {"name": self.workflow, "step_id": self.step_id})
        time.sleep(0.2)
        if self._status is not None:
            try:
                self._status.config(text="asking vision…")
                if self._root is not None:
                    self._root.update()
            except Exception:
                pass
        out = self._post(
            VISION_ASK_PATH,
            {"name": self.workflow, "step_id": self.step_id, "question": q},
        )
        if self._root is not None:
            try:
                self._root.deiconify()
                self._root.attributes("-topmost", True)
            except Exception:
                pass
        if out and out.get("ok"):
            ans = ((out.get("entry") or {}).get("a") or "answered")[:80]
            self.last_outcome = ans
        else:
            self.last_outcome = (out or {}).get("error") or "vision ask failed"
        if self._status is not None:
            try:
                self._status.config(text=self.last_outcome)
            except Exception:
                pass

    def _drag_start(self, event) -> None:
        self._drag_from = (int(event.x_root), int(event.y_root))

    def _drag_move(self, event) -> None:
        if self._root is None or not self._drag_from:
            return
        x0, y0 = self._drag_from
        dx = int(event.x_root) - x0
        dy = int(event.y_root) - y0
        nx = self._root.winfo_x() + dx
        ny = self._root.winfo_y() + dy
        self._root.geometry(f"+{max(0, nx)}+{max(0, ny)}")
        self._drag_from = (int(event.x_root), int(event.y_root))

    def on_stop(self) -> None:
        self.calls.append("stop")
        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None
        global _active
        with _lock:
            if _active is self:
                _active = None

    def launch(self, *, block: bool = False) -> None:
        import tkinter as tk

        root = tk.Tk()
        self._root = root
        root.title("MimicAgent")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        self.topmost = bool(root.attributes("-topmost"))
        root.geometry("128x400+8+120")
        root.configure(bg="#141A22")
        root.bind("<ButtonPress-1>", self._drag_start)
        root.bind("<B1-Motion>", self._drag_move)
        tk.Label(
            root, text="MIMIC  :: drag", fg="#9BB4C9", bg="#141A22",
            font=("Segoe UI", 7, "bold"),
        ).pack(pady=(8, 0))
        self._step_lbl = tk.Label(
            root, text=f"step {self.step_id or '-'}", fg="#EEF1F4", bg="#141A22",
            font=("Segoe UI", 8, "bold"),
        )
        self._step_lbl.pack(padx=4, pady=(2, 4))
        self._mode_btn = tk.Button(
            root, text="⇄ Watch me", command=self.toggle_mode,
            bg="#141A22", fg="#9BB4C9", relief="flat", font=("Segoe UI", 7),
        )
        self._mode_btn.pack()
        self._status = tk.Label(
            root, text="", fg="#7C8894", bg="#141A22",
            font=("Segoe UI", 7), wraplength=108, justify="center",
        )
        self._status.pack(padx=4, pady=4)
        self._run_btn = tk.Button(
            root, text="Show me", command=self.on_run,
            bg="#0F6E5C", fg="#fff", activebackground="#0C5B4C", activeforeground="#fff",
            relief="flat", font=("Segoe UI", 9, "bold"), height=2,
        )
        self._run_btn.pack(fill="x", padx=8, pady=(4, 2))
        self._focus_btn = tk.Button(
            root, text="Focus here", command=self.on_focus,
            bg="#1F2A36", fg="#EEF1F4", activebackground="#263446", activeforeground="#fff",
            relief="flat", font=("Segoe UI", 8, "bold"), height=1,
        )
        self._focus_btn.pack(fill="x", padx=8, pady=(2, 4))
        tk.Label(root, text="clicks", fg="#7C8894", bg="#141A22", font=("Segoe UI", 7)).pack()
        self._clicks = tk.Spinbox(
            root, from_=1, to=2, increment=1, width=6,
            font=("Segoe UI", 9), justify="center",
        )
        self._clicks.delete(0, "end")
        self._clicks.insert(0, str(int(self.click_count or 1)))
        self._clicks.pack(pady=2)
        self._finish_btn = tk.Button(
            root, text="Finish case", command=self.on_finish_case,
            bg="#5A3D12", fg="#fff", activebackground="#6E4B18", activeforeground="#fff",
            relief="flat", font=("Segoe UI", 8, "bold"), height=1,
        )
        tk.Label(root, text="watch (sec)", fg="#7C8894", bg="#141A22", font=("Segoe UI", 7)).pack()
        self._secs = tk.Spinbox(
            root, from_=5, to=60, increment=5, width=6,
            font=("Segoe UI", 9), justify="center",
        )
        self._secs.delete(0, "end")
        self._secs.insert(0, str(int(self.watch_seconds)))
        self._secs.pack(pady=2)
        tk.Button(
            root, text="Close", command=self.on_stop,
            bg="#141A22", fg="#9BB4C9", relief="flat", font=("Segoe UI", 8),
        ).pack(fill="x", padx=8, pady=(6, 8))
        self._refresh_labels()
        if block:
            root.mainloop()
        else:
            root.update_idletasks()
            root.update()

    def close(self) -> None:
        self.on_stop()


def arm_show(workflow: str, step_id: str, api_url: str = "http://127.0.0.1:8765",
             click_count: int = 1, mode: str = "show", watch_seconds: float = 15,
             case_phase: str | None = None, case_label: str = "",
             vision_question: str = "", case_id: str = "") -> dict:
    """Put the left-edge capture bar on screen. One instance at a time."""
    global _active
    cc = int(click_count or 1)
    if mode == "vision":
        m = "vision"
    elif mode == "watch":
        m = "watch"
    else:
        m = "show"
    with _lock:
        if _active is not None:
            try:
                _active.set_target(
                    workflow, step_id, click_count=cc, mode=m, watch_seconds=watch_seconds,
                    case_phase=case_phase, case_label=case_label,
                    vision_question=vision_question, case_id=case_id,
                )
                return {
                    "ok": True, "ready": True, "click_count": cc, "mode": _active.mode,
                    "case_phase": _active.case_phase,
                }
            except Exception:
                _active = None
        widget = FloatingTeacher(
            api_url=api_url, workflow=workflow, step_id=step_id,
            click_count=cc, countdown=1.6, mode=m, watch_seconds=watch_seconds,
            case_phase=case_phase, case_label=case_label,
            vision_question=vision_question, case_id=case_id,
        )
        _active = widget

    def _run():
        try:
            widget.launch(block=True)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
    return {
        "ok": True, "ready": True, "click_count": cc, "mode": m,
        "case_phase": case_phase,
    }


def arm_case_authoring(
    workflow: str,
    step_id: str,
    *,
    phase: str,
    case_label: str,
    click_count: int = 1,
    api_url: str = "http://127.0.0.1:8765",
) -> dict:
    return arm_show(
        workflow, step_id, api_url=api_url, click_count=click_count,
        case_phase=phase, case_label=case_label,
    )
