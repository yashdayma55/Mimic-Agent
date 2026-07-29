import ollama
import time
from PIL import Image
import io

IMAGE_PATH = r"D:\python_files\Mimic Agent\captures\click_1785120433.083.png"

# pretend this is the click location from your database (pick a real spot on that screenshot)
CLICK_X = 900
CLICK_Y = 500

def crop_around_click(path, cx, cy, box=500):
    """Crop a box x box region centered on the click point, return PNG bytes."""
    img = Image.open(path)
    half = box // 2
    # TODO: compute the crop edges. The box is centered on (cx, cy):
    left   = cx - half
    top    = cy - half
    right  = cx + half
    bottom = cy + half
    cropped = img.crop((left, top, right, bottom))
    print(f"cropped to: {cropped.width}x{cropped.height}")
    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    return buffer.getvalue()

print("Asking the model about the cropped region...")
start = time.time()

response = ollama.chat(
    model="qwen3-vl:2b",
    messages=[
        {
            "role": "user",
            "content": "What UI element is in the center of this image? Answer in one short sentence.",
            "images": [crop_around_click(IMAGE_PATH, CLICK_X, CLICK_Y)],
        }
    ],
    think=False,
    keep_alive=-1,
)

elapsed = time.time() - start
print(f"\n--- Answer (took {elapsed:.1f} seconds) ---")
print(response["message"]["content"])