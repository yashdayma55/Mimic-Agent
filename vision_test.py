import ollama
import time
from PIL import Image
import io

IMAGE_PATH = r"D:\python_files\Mimic Agent\captures\click_1785120433.083.png"

def load_and_resize(path, max_width=1280):
    """Open the image, shrink it so width <= max_width, return raw PNG bytes."""
    img = Image.open(path)
    if img.width<=max_width:#  only resize if it's wider than max_width
        ratio = max_width / img.width #   - figure out the scale ratio: max_width / img.width

        new_height = int(img.height * ratio)    #   - compute the new height using that ratio
        img = img.resize((max_width, new_height)) #   - use img.resize((new_width, new_height))
        
    

   
   
    buffer = io.BytesIO() # then convert the image to PNG bytes and return them:
    img.save(buffer, format="PNG")
    return buffer.getvalue()

print("Asking the model...")
start = time.time()

response = ollama.chat(
    model="qwen3-vl:2b",
    messages=[
        {
            "role": "user",
            "content": "What is on this screen? Answer in 2 sentences.",
            "images": [load_and_resize(IMAGE_PATH)],   # now passing bytes, not a path
        }
    ],
    think=False,
)

elapsed = time.time() - start
print(f"\n--- Answer (took {elapsed:.1f} seconds) ---")
print(response["message"]["content"])