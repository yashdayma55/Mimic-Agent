"""
mini_recorder.py  —  Week 1 capstone: the Phase 1 Recorder.

Merges all five exercises:
  Ex1 pynput listeners        -> capture clicks & keys system-wide
  Ex2 pywinauto from_point    -> identify WHICH element was clicked
  Ex3 mss screenshot          -> capture the screen at click time
  Ex4 SQLite (WAL)            -> persist everything durably
  Ex5 queue + writer thread   -> keep the mouse smooth (producer/consumer)

Usage:
  python mini_recorder.py [workflow_dir]

  workflow_dir defaults to cwd. DB -> <dir>/recording.db,
  screenshots -> <dir>/captures/click_<ts>.png
"""

import queue
import threading
import time
import os
import sys
import sqlite3
from pynput import mouse, keyboard
from pywinauto import Desktop
import mss
import mss.tools

q = queue.Queue()
mouse_listener = None
keyboard_listener = None
_workflow_dir = "."


def run_recorder(workflow_dir="."):
    """Record clicks/keys into workflow_dir/recording.db and workflow_dir/captures/."""
    global mouse_listener, keyboard_listener, _workflow_dir
    _workflow_dir = os.path.abspath(workflow_dir or ".")
    captures = os.path.join(_workflow_dir, "captures")
    os.makedirs(captures, exist_ok=True)
    db_path = os.path.join(_workflow_dir, "recording.db")

    t = threading.Thread(
        target=_writer,
        args=(db_path, captures),
        daemon=True,
    )
    t.start()

    mouse_listener = mouse.Listener(on_click=_on_click)
    keyboard_listener = keyboard.Listener(on_press=_on_press)
    mouse_listener.start()
    keyboard_listener.start()

    print(f"recording into {_workflow_dir} ... press Esc to stop")
    keyboard_listener.join()

    q.put(None)
    t.join()
    print(f"stopped cleanly — db: {db_path}")


def _on_click(x, y, button, pressed):
    if pressed:
        q.put({
            "kind": "click",
            "ts": time.time(),
            "x": x,
            "y": y,
            "button": str(button),
        })


def _on_press(key):
    q.put({
        "kind": "key",
        "ts": time.time(),
        "key": str(key),
    })
    if key == keyboard.Key.esc:
        if mouse_listener:
            mouse_listener.stop()
        return False


def _writer(db_path, captures_dir):
    desktop = Desktop(backend="uia")
    sct = mss.MSS()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          REAL NOT NULL,
        kind        TEXT NOT NULL,
        x           INTEGER,
        y           INTEGER,
        button      TEXT,
        key         TEXT,
        elem_name   TEXT,
        elem_type   TEXT,
        screenshot  TEXT
    )""")
    conn.commit()

    INSERT = ("INSERT INTO events "
              "(ts, kind, x, y, button, key, elem_name, elem_type, screenshot) "
              "VALUES (?,?,?,?,?,?,?,?,?)")

    while True:
        e = q.get()
        if e is None:
            break

        if e["kind"] == "click":
            try:
                info = desktop.from_point(e["x"], e["y"]).element_info
                name, ctype = info.name, str(info.control_type)
            except Exception:
                name, ctype = "", ""

            fname = f"click_{e['ts']:.3f}.png"
            path = os.path.join(captures_dir, fname)
            rel_path = os.path.join("captures", fname)
            try:
                img = sct.grab(sct.monitors[1])
                mss.tools.to_png(img.rgb, img.size, output=path)
            except Exception:
                path = None
                rel_path = None

            conn.execute(INSERT, (e["ts"], "click", e["x"], e["y"],
                                  e["button"], None, name, ctype, rel_path))
            conn.commit()
            print(f"{e['ts']:.3f}  CLICK ({e['x']},{e['y']}) {e['button']} "
                  f"-> '{name}' {ctype}  [saved]")

        else:
            conn.execute(INSERT, (e["ts"], "key", None, None,
                                  None, e["key"], None, None, None))
            conn.commit()
            print(f"{e['ts']:.3f}  KEY {e['key']}  [saved]")

        q.task_done()

    conn.close()
    print("(writer thread closed the database)")


if __name__ == "__main__":
    wf_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    run_recorder(wf_dir)
