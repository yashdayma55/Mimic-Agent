"""
Test the API-powered vision -> click path end to end.
Reads your key from my_key.txt (gitignored), focuses Notepad FIRST, then sends
its File-menu region to the vision API (auto-detected provider), and clicks if confirmed.

Open Notepad before running.
"""

import time
import pyautogui
from pywinauto import Desktop
from vision_locator import grab_screen_region
from vision_api import ask_vision_api, detect_provider

CX, CY = 388, 428     # real center of Notepad 'File' menu


def load_key():
    with open("my_key.txt", "r", encoding="utf-8") as f:
        return f.read().strip()


def focus_notepad():
    """Bring Notepad to the front so the screenshot + click hit it."""
    try:
        for win in Desktop(backend="uia").windows():
            if "Notepad" in win.window_text():
                win.set_focus()
                return True
    except Exception:
        pass
    return False


print("=== API-powered vision -> click test ===\n")

key = load_key()
provider = detect_provider(key)
print(f"detected provider: {provider}")
if provider == "unknown":
    print("!! key format not recognized")
    raise SystemExit

# FOCUS NOTEPAD FIRST, then wait a moment, THEN screenshot
print("bringing Notepad to the front...")
if not focus_notepad():
    print("!! Notepad not found - open Notepad and try again")
    raise SystemExit
time.sleep(1.5)      # let it come forward before we screenshot

print(f"grabbing screen region around ({CX},{CY})...")
img = grab_screen_region(CX, CY)

print("sending to the vision API...")
t0 = time.time()
res = ask_vision_api(img, key)
dt = time.time() - t0
print(f"got response in {dt:.1f}s: {res}\n")

if res.get("found"):
    print(f">>> API vision confirmed: '{res.get('what_you_see')}' via {res.get('provider')}")
    focus_notepad()
    print(">>> WATCH NOTEPAD - clicking in 2 seconds...")
    time.sleep(2)
    pyautogui.click(CX, CY)
    print(">>> CLICKED via API vision! Notepad File menu should be OPEN. (Esc to close)")
else:
    print(f">>> API vision did not confirm: {res.get('what_you_see')}")
    print("    (if it saw the wrong thing, make sure Notepad is open and not minimized)")