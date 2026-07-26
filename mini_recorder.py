"""
mini_recorder.py  —  Week 1 capstone: the Phase 1 Recorder.

Merges all five exercises:
  Ex1 pynput listeners        -> capture clicks & keys system-wide
  Ex2 pywinauto from_point    -> identify WHICH element was clicked
  Ex3 mss screenshot          -> capture the screen at click time
  Ex4 SQLite (WAL)            -> persist everything durably
  Ex5 queue + writer thread   -> keep the mouse smooth (producer/consumer)

Architecture (say it out loud):
  listeners catch events -> queue passes them safely ->
  writer thread saves events + screenshots + UI-element info into SQLite.
"""

import queue
import threading
import time
import os
import sqlite3
from pynput import mouse, keyboard
from pywinauto import Desktop
import mss
import mss.tools

os.makedirs("captures", exist_ok=True)

q = queue.Queue()
mouse_listener = None  # declared early: on_press references it before its real assignment


# ---------------- PRODUCERS: callbacks do almost nothing ----------------
# They run inside the OS input pipeline, so they must return instantly.
# All they do is drop a tiny dict into the queue.

def on_click(x, y, button, pressed):
    if pressed:  # keep only the press, not the release
        q.put({
            "kind": "click",
            "ts": time.time(),
            "x": x,
            "y": y,
            "button": str(button),
        })

def on_press(key):
    q.put({
        "kind": "key",
        "ts": time.time(),
        "key": str(key),
    })
    if key == keyboard.Key.esc:
        mouse_listener.stop()  # stop the OTHER listener too
        return False           # stop THIS listener


# ---------------- CONSUMER: the writer thread does all slow work ----------------
# UIA lookup (~100ms), screenshot + PNG encode (~150ms), and DB insert all
# happen HERE, off the input pipeline — so the user's mouse never lags.

def writer():
    # Heavy resources are created INSIDE this thread (thread-affinity):
    #   - mss is not thread-safe
    #   - a sqlite3 connection belongs to the thread that made it
    desktop = Desktop(backend="uia")
    sct = mss.MSS()

    conn = sqlite3.connect("recording.db")
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
        e = q.get()          # sleeps until an event arrives
        if e is None:        # poison pill -> shut down
            break

        if e["kind"] == "click":
            # WHICH element? (Ex2)
            try:
                info = desktop.from_point(e["x"], e["y"]).element_info
                name, ctype = info.name, str(info.control_type)
            except Exception:
                name, ctype = "", ""   # empty-tree case: screenshot covers us

            # WHAT was on screen? (Ex3)
            path = f"captures/click_{e['ts']:.3f}.png"
            try:
                img = sct.grab(sct.monitors[1])
                mss.tools.to_png(img.rgb, img.size, output=path)
            except Exception:
                path = None

            # PERSIST (Ex4)
            conn.execute(INSERT, (e["ts"], "click", e["x"], e["y"],
                                  e["button"], None, name, ctype, path))
            conn.commit()
            print(f"{e['ts']:.3f}  CLICK ({e['x']},{e['y']}) {e['button']} "
                  f"-> '{name}' {ctype}  [saved]")

        else:  # key
            conn.execute(INSERT, (e["ts"], "key", None, None,
                                  None, e["key"], None, None, None))
            conn.commit()
            print(f"{e['ts']:.3f}  KEY {e['key']}  [saved]")

        q.task_done()

    conn.close()
    print(f"(writer thread closed the database)")


# ---------------- WIRING IT ALL TOGETHER ----------------

t = threading.Thread(target=writer, daemon=True)
t.start()

mouse_listener = mouse.Listener(on_click=on_click)
keyboard_listener = keyboard.Listener(on_press=on_press)
mouse_listener.start()
keyboard_listener.start()

print("recording... press Esc to stop")
keyboard_listener.join()   # main parks here until Esc

q.put(None)   # poison pill AFTER listeners have stopped
t.join()      # wait for the writer to drain & close cleanly
print("stopped cleanly — open recording.db in DB Browser to see your workflow")