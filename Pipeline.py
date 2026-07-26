import queue, threading, time, os
from pynput import mouse, keyboard
from pywinauto import Desktop
import mss, mss.tools

os.makedirs("captures", exist_ok=True)
q = queue.Queue()
mouse_listener = None

# ---------- PRODUCER: callbacks do almost nothing ----------
def on_click(x, y, button, pressed):
    if pressed:
        q.put({"kind": "click", "ts": time.time(), "x": x, "y": y})   # instant

def on_press(key):
    q.put({"kind": "key", "ts": time.time(), "key": str(key)})
    if key == keyboard.Key.esc:
        mouse_listener.stop()
        return False

# ---------- CONSUMER: writer thread owns the slow resources ----------
def writer():
    desktop = Desktop(backend="uia")   # created INSIDE the thread
    sct = mss.MSS()                    # created INSIDE the thread (thread-affinity!)
    while True:
        e = q.get()
        if e is None:
            break
        if e["kind"] == "click":
            try:
                info = desktop.from_point(e["x"], e["y"]).element_info
                name, ctype = info.name, info.control_type
            except Exception:
                name, ctype = "", ""
            path = f"captures/click_{e['ts']:.3f}.png"
            img = sct.grab(sct.monitors[1])
            mss.tools.to_png(img.rgb, img.size, output=path)
            print(f"{e['ts']:.3f} CLICK ({e['x']},{e['y']}) -> '{name}' {ctype}")
        else:
            print(f"{e['ts']:.3f} KEY {e['key']}")
        q.task_done()

t = threading.Thread(target=writer, daemon=True)
t.start()

mouse_listener = mouse.Listener(on_click=on_click)
keyboard_listener = keyboard.Listener(on_press=on_press)
mouse_listener.start()
keyboard_listener.start()
print("recording press esc to stop")
keyboard_listener.join()

q.put(None)     # poison pill AFTER listeners stop
t.join()
print("stopped cleanly")