from pynput import mouse, keyboard
import time
from pywinauto import Desktop          # top of file
import os 
import mss,mss.tools 
desktop = Desktop(backend="uia")       # create ONCE, globally
mouse_listener=None
#keyboard_listener=None
os.makedirs("captures",exist_ok=True)
sct=mss.mss()

def on_click(x,y,button,pressed):
    if pressed:
        ts = time.time()
        print(f"{ts:.3f}  CLICK ({x}, {y}) {button}")
       
        try:
            info = desktop.from_point(x, y).element_info
            print(f"    -> Name='{info.name}' Type={info.control_type}")
        except Exception as e:
            print(f"    -> lookup failed: {e}")
        img = sct.grab(sct.monitors[1])
        path = f"captures/click_{ts:.3f}.png"
        mss.tools.to_png(img.rgb, img.size, output=path)
        print(f"    -> saved {path}")
        
def on_press(key):
    try:
        print(f"{time.time():.3f}  KEY {key.char}")
    except AttributeError:
        print(f"{time.time():.3f} KEY {key}")
    if key== keyboard.Key.esc:
        mouse_listener.stop()
        return False
mouse_listener = mouse.Listener(on_click=on_click)
keyboard_listener = keyboard.Listener(on_press=on_press)
mouse_listener.start()
keyboard_listener.start()
print("recording press esc to stop")
keyboard_listener.join()
print("stopped cleanly")


        