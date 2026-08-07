"""
Set-of-Mark step 2 as a SEPARATE file (so we don't have to modify set_of_mark.py).
Imports the step-1 functions and adds: pick the numbered element matching an intent.

Run:  python som_pick.py "click the File menu"
"""

import sys
import json
import base64
import requests
from set_of_mark import build_marked_screenshot


def _load_key():
    try:
        with open("my_key.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def pick_element_by_intent(marked_png_path, elements, intent):
    """Send the numbered screenshot + intent to Claude; return (chosen_id, reason)."""
    key = _load_key()
    if not key:
        return None, "no API key in my_key.txt"
    menu = "\n".join(f"{el['id']}: {el['control_type']} '{el['name']}'" for el in elements)
    prompt = ("You are helping click the correct UI element. The screenshot has numbered "
              "red boxes on every clickable element. Numbered elements:\n\n" + menu +
              f"\n\nThe user's intent is: \"{intent}\"\n\n"
              "Which numbered box should be clicked? Consider the names AND the image. "
              'Respond with ONLY JSON: {"id": <number>, "reason": "<short>"} '
              'or {"id": null, "reason": "..."} if nothing matches.')
    try:
        with open(marked_png_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        if not key.startswith("sk-ant"):
            return None, "only Claude keys wired for now (sk-ant-...)"
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
        return obj.get("id"), obj.get("reason", "")
    except Exception as e:
        return None, f"error: {e}"


if __name__ == "__main__":
    intent = sys.argv[1] if len(sys.argv) > 1 else "click the File menu"
    print(f"\n=== Set-of-Mark pick test: intent = '{intent}' ===")
    elements, path = build_marked_screenshot(save_path="marked.png")
    print(f"marked {len(elements)} elements -> {path}")
    chosen_id, reason = pick_element_by_intent(path, elements, intent)
    print(f"\nmodel chose box: {chosen_id}")
    print(f"reason: {reason}")
    if chosen_id:
        match = next((e for e in elements if e["id"] == chosen_id), None)
        if match:
            print(f"that box is: {match['control_type']} '{match['name']}' "
                  f"at screen center ({match['cx']},{match['cy']})")
            print("(step 3 would click that exact center)")