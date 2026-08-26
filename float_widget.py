"""Always-on-top Show-me button. Lives on the left of the screen, over every app."""

from __future__ import annotations

import threading
import time

_active = None
_lock = threading.Lock()


def cursor_point() -> tuple[int, int]:
    from show_capture import cursor_point as _pt

    return _pt()


class FloatingTeacher:
    def __init__(self, api_url: str = "http://127.0.0.1:8765", workflow: str = "",
                 step_id: str = "", capture_fn=None, rehearse_fn=None, countdown: float = 0):
        self.api_url = api_url.rstrip("/")
        self.workflow = workflow
        self.step_id = step_id
        self.capture_fn = capture_fn
        self.rehearse_fn = rehearse_fn
        self.countdown = float(countdown or 0)
        self.calls = []
        self._root = None
        self._status = None
        self._secs = None
        self.topmost = False

    def set_target(self, workflow: str, step_id: str) -> None:
        self.workflow = workflow
        self.step_id = step_id
        if self._status is not None:
            try:
                self._status.config(text=f"step {self.step_id or '-'}")
            except Exception:
                pass

    def _post(self, path: str, body: dict) -> None:
        self.calls.append(path)
        if path.endswith("/show") and self.capture_fn:
            self.capture_fn(body)
            return
        if path.endswith("/rehearse") and self.rehearse_fn:
            self.rehearse_fn(body)
            return
        try:
            import json
            import urllib.request

            req = urllib.request.Request(
                self.api_url + path,
                data=json.dumps(body).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=8)
        except Exception:
            pass

    def on_show(self) -> None:
        if self._root is not None:
            try:
                self._root.withdraw()
                self._root.update()
            except Exception:
                pass
        if self.countdown:
            time.sleep(self.countdown)
        else:
            time.sleep(0.12)
        x, y = cursor_point()
        self._post("/api/teach/show", {
            "name": self.workflow,
            "step_id": self.step_id,
            "point": [x, y],
        })
        if self._root is not None:
            try:
                self._root.deiconify()
                self._root.attributes("-topmost", True)
                if self._status is not None:
                    self._status.config(text="captured")
            except Exception:
                pass

    def on_watch(self) -> None:
        try:
            seconds = float(self._secs.get()) if self._secs is not None else 15
        except Exception:
            seconds = 15
        seconds = max(5.0, min(seconds, 60.0))
        if self._status is not None:
            try:
                self._status.config(text=f"watching {int(seconds)}s")
                if self._root is not None:
                    self._root.update()
            except Exception:
                pass
        from observe import watch_step

        try:
            out = watch_step(self.workflow, self.step_id, seconds=seconds, interval=1.0)
            summary = ((out.get("learned") or {}).get("summary") or "learned")[:48]
            if self._status is not None:
                self._status.config(text="learned")
        except Exception as e:
            if self._status is not None:
                self._status.config(text="watch failed")
            summary = str(e)
            out = {"ok": False, "error": str(e)}
        self.calls.append("/api/teach/observe")
        return out

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
        root.title("Show me")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        self.topmost = bool(root.attributes("-topmost"))
        root.geometry("100x268+8+180")
        root.configure(bg="#141A22")
        tk.Label(
            root, text="SHOW ME", fg="#9BB4C9", bg="#141A22",
            font=("Segoe UI", 7, "bold"),
        ).pack(pady=(10, 2))
        self._status = tk.Label(
            root, text=f"step {self.step_id or '-'}", fg="#7C8894", bg="#141A22",
            font=("Segoe UI", 7),
        )
        self._status.pack()
        btn = tk.Button(
            root, text="Show me", command=self.on_show,
            bg="#0F6E5C", fg="#fff", activebackground="#0C5B4C", activeforeground="#fff",
            relief="flat", font=("Segoe UI", 9, "bold"), height=2,
        )
        btn.pack(fill="x", padx=8, pady=8)
        tk.Label(
            root, text="watch (sec)", fg="#7C8894", bg="#141A22",
            font=("Segoe UI", 7),
        ).pack()
        self._secs = tk.Spinbox(
            root, from_=5, to=60, increment=5, width=6,
            font=("Segoe UI", 9), justify="center",
        )
        self._secs.delete(0, "end")
        self._secs.insert(0, "15")
        self._secs.pack(pady=2)
        tk.Button(
            root, text="Watch me", command=self.on_watch,
            bg="#2C5578", fg="#fff", activebackground="#244660", activeforeground="#fff",
            relief="flat", font=("Segoe UI", 8, "bold"),
        ).pack(fill="x", padx=8, pady=6)
        tk.Button(
            root, text="Close", command=self.on_stop,
            bg="#141A22", fg="#9BB4C9", relief="flat", font=("Segoe UI", 8),
        ).pack(fill="x", padx=8)
        if block:
            root.mainloop()
        else:
            root.update_idletasks()
            root.update()

    def close(self) -> None:
        self.on_stop()


def arm_show(workflow: str, step_id: str, api_url: str = "http://127.0.0.1:8765") -> dict:
    """Put the left-edge Show-me button on screen. One instance at a time."""
    global _active
    with _lock:
        if _active is not None:
            try:
                _active.set_target(workflow, step_id)
                return {"ok": True, "ready": True}
            except Exception:
                _active = None
        widget = FloatingTeacher(
            api_url=api_url, workflow=workflow, step_id=step_id, countdown=1.6,
        )
        _active = widget

    def _run():
        try:
            widget.launch(block=True)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "ready": True}
