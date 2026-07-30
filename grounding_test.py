import ollama
import time
import json
from PIL import Image
import io

IMAGE_PATH = r"D:\python_files\Mimic Agent\captures\click_1785120433.083.png"

# A known click location from the recording (roughly the "Write a message" box)
CLICK_X = 965
CLICK_Y = 598

def crop_around_click(path, cx, cy, box=400):
    """Crop a box x box region centered on the click point, return PNG bytes."""
    img = Image.open(path)
    half = box // 2
    left   = cx - half
    top    = cy - half
    right  = cx + half
    bottom = cy + half
    cropped = img.crop((left, top, right, bottom))
    print(f"cropped to: {cropped.width}x{cropped.height}")
    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    return buffer.getvalue()

# Ask the model to IDENTIFY the element in the crop, as JSON (in the prompt, not forced mode).
PROMPT = """Identify the main UI element in the center of this cropped screenshot.
Respond with ONLY a JSON object, no other text, using these keys:
{"element_type": "button/textbox/link/icon/text/image/other", "label": "visible text or purpose", "confidence": "high/medium/low"}"""

print("Asking the model to identify the element...")
start = time.time()

response = ollama.chat(
    model="qwen3-vl:2b",
    messages=[
        {
            "role": "user",
            "content": PROMPT,
            "images": [crop_around_click(IMAGE_PATH, CLICK_X, CLICK_Y)],
        }
    ],
    think=False,
    keep_alive=-1,
)

elapsed = time.time() - start
raw = response["message"]["content"]

print(f"\n--- Raw answer (took {elapsed:.1f}s) ---")
print(repr(raw))

# Parse the JSON, tolerating any extra text around it by grabbing from first { to last }
print("\n--- Parsed ---")
try:
    start_idx = raw.find("{")
    end_idx = raw.rfind("}") + 1
    data = json.loads(raw[start_idx:end_idx])
    print("element_type:", data.get("element_type"))
    print("label:       ", data.get("label"))
    print("confidence:  ", data.get("confidence"))
except Exception as e:
    print(f"Could not parse JSON: {e}")