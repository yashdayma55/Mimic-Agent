"""
Tier 5 vision fallback for the self-healing locator.

When the accessibility tree (tiers 1-4) can't find an element, we fall back to
actually LOOKING at the screen with a vision model. This module:
  1. grabs a fresh screenshot of the live screen
  2. crops around the element's recorded coordinates
  3. asks a vision model to confirm the element is there
  4. routes to either LOCAL (Ollama) or an API provider, based on config

The provider is swappable so users on weak CPUs can plug in a fast API key,
while the private/offline default stays local Ollama.
"""

import io
import json
import mss
from PIL import Image
import ollama


# ---- CONFIG: which vision backend to use ----
# "local"  -> Ollama qwen3-vl:2b (private, offline, slower on CPU)
# "api"    -> a third-party vision API (fast, needs a key in my_key.txt)
VISION_PROVIDER = "api"


def _load_key():
    """Read the API key from my_key.txt (gitignored). Returns '' if missing."""
    try:
        with open("my_key.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def grab_screen_region(cx, cy, box=400):
    """Screenshot the live screen and crop a box around (cx, cy). Returns PNG bytes."""
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[0])          # full virtual screen (all monitors)
        img = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)

    half = box // 2
    left   = max(0, cx - half)
    top    = max(0, cy - half)
    right  = min(img.width, cx + half)
    bottom = min(img.height, cy + half)
    crop = img.crop((left, top, right, bottom))

    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


# ---- PROVIDER 1: local Ollama vision (Phase 2 approach) ----
def _ask_local(image_bytes, target_desc):
    prompt = """Look at the CENTER of this cropped screenshot. Is there a clickable UI element there (a button, menu, link, icon, or field)?
    Respond ONLY with JSON: {"found": true/false, "what_you_see": "what the element is", "confidence": "high/medium/low"}
    Set found=true if there is any clickable element near the center."""
    resp = ollama.chat(
        model="qwen3-vl:2b",
        messages=[{"role": "user", "content": prompt, "images": [image_bytes]}],
        think=False,
        keep_alive="30m",
    )
    return resp["message"]["content"]


# ---- PROVIDER 2: third-party API (opt-in fast lane) - NOW WIRED to vision_api.py ----
def _ask_api(image_bytes, target_desc):
    """Call the real hosted vision API via vision_api.ask_vision_api (proven working).
    Loads the key from my_key.txt. Returns a JSON string (to match _ask_local)."""
    key = _load_key()
    if not key:
        return '{"found": false, "what_you_see": "no API key in my_key.txt", "confidence": "low"}'
    try:
        from vision_api import ask_vision_api
        result = ask_vision_api(image_bytes, key)   # returns a dict {found, what_you_see, confidence}
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"found": False, "what_you_see": f"api error: {e}", "confidence": "low"})


def _parse(raw):
    """Pull the JSON object out of the model text (tolerant of stray text)."""
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        return json.loads(raw[start:end])
    except Exception:
        return {"found": False, "what_you_see": "could not parse", "confidence": "low"}


def locate_with_vision(step, verbose=True):
    """Tier 5: look at the screen and try to confirm the element.
    Returns a dict: {found, what_you_see, confidence, x, y} or found=False."""
    cx = step.get("x", 0)
    cy = step.get("y", 0)
    target = step.get("elem_name") or step.get("instruction", "the target element")

    if verbose:
        print(f"      Tier 5 (vision, provider={VISION_PROVIDER}): looking near ({cx},{cy}) for '{target}'")

    try:
        image_bytes = grab_screen_region(cx, cy)
    except Exception as e:
        if verbose:
            print(f"      Tier 5: could not grab screen ({e})")
        return {"found": False}

    if VISION_PROVIDER == "api":
        raw = _ask_api(image_bytes, target)
    else:
        raw = _ask_local(image_bytes, target)

    result = _parse(raw)
    result["x"] = cx          # the recorded coords are our best click point
    result["y"] = cy
    if verbose:
        print(f"      Tier 5 result: {result}")
    return result


# ---- standalone test ----
if __name__ == "__main__":
    test_step = {"elem_name": "Text editor", "instruction": "the notepad text area",
                 "x": 900, "y": 500}
    print("Testing Tier 5 vision locator...")
    print(locate_with_vision(test_step))