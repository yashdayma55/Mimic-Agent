"""
Stage A: the SAFE Set-of-Mark pick - redacts sensitive regions before sending.

Full pipeline: mark the screen -> redact sensitive fields -> ask the model to
pick the numbered element. This is the version the locator should use, because
it guarantees no password/card/ssn pixels leave the machine.
"""

import json
import base64
import requests
from PIL import Image
from set_of_mark import collect_clickable_elements, grab_full_screen, draw_marks
from som_redact import redact_image


def _load_key():
    try:
        with open("my_key.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def build_safe_marked_screenshot(save_path="marked_safe.png"):
    """Mark the screen AND redact sensitive regions before saving.
    Returns (elements, saved_path)."""
    elements = collect_clickable_elements()
    img, ox, oy, scale = grab_full_screen()
    for el in elements:
        el["sx"] = int((el["cx"] - ox) * scale)
        el["sy"] = int((el["cy"] - oy) * scale)
    # 1. redact sensitive regions FIRST (so secrets never get marked or sent)
    img = redact_image(img, elements, ox, oy)
    # 2. then draw the numbered marks
    annotated = draw_marks(img, elements, ox, oy, scale)
    annotated.save(save_path)
    return elements, save_path


def safe_pick_element_by_intent(intent):
    """Full safe loop: mark + redact -> ask model to pick a number.
    Returns (chosen_element_dict or None, reason)."""
    key = _load_key()
    if not key:
        return None, "no API key in my_key.txt"

    elements, path = build_safe_marked_screenshot()
    if not elements:
        return None, "no clickable elements found"

    menu = "\n".join(f"{el['id']}: {el['control_type']} '{el['name']}'" for el in elements)
    prompt = ("You are helping click the correct UI element. The screenshot has numbered "
              "red boxes on every clickable element (some regions may be blacked out for "
              "privacy). Numbered elements:\n\n" + menu +
              f"\n\nThe user's intent is: \"{intent}\"\n\n"
              "Which numbered box should be clicked? Consider the names AND the image. "
              'Respond with ONLY JSON: {"id": <number>, "reason": "<short>"} '
              'or {"id": null, "reason": "..."}.')
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        if not key.startswith("sk-ant"):
            return None, "only Claude keys wired for now"
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-sonnet-4-5", "max_tokens": 300,
                  "messages": [{"role": "user", "content": [
                      {"type": "image", "source": {"type": "base64",
                       "media_type": "image/png", "data": b64}},
                      {"type": "text", "text": prompt}]}]},
            timeout=60)
        r.raise_for_status()
        raw = r.json()["content"][0]["text"]
        obj = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        cid = obj.get("id")
        if not cid:
            return None, obj.get("reason", "nothing matched")
        match = next((e for e in elements if e["id"] == cid), None)
        return match, obj.get("reason", "")
    except Exception as e:
        return None, f"error: {e}"


if __name__ == "__main__":
    import sys
    intent = sys.argv[1] if len(sys.argv) > 1 else "open the View menu"
    print(f"=== SAFE set-of-mark pick: '{intent}' ===")
    match, reason = safe_pick_element_by_intent(intent)
    if match:
        print(f"chose: {match['control_type']} '{match['name']}' at ({match['cx']},{match['cy']})")
        print(f"reason: {reason}")
    else:
        print(f"no pick: {reason}")