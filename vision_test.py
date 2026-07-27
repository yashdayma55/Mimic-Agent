import ollama
import time

# the screenshot we'll ask about — use a real filename from your captures/ folder
IMAGE_PATH = r"D:\python_files\Mimic Agent\captures\click_1785120433.083.png"

print("Asking the model... (this runs on CPU, give it a minute or two)")
start = time.time()

response = ollama.chat(
    model="qwen3-vl:2b",
    messages=[
        {
            "role": "user",
            "content": "What is on this screen? Answer in 2 sentences.",
            "images": [IMAGE_PATH],
        }
    ],
    think=False,
)

elapsed = time.time() - start
print(f"\n--- Answer (took {elapsed:.1f} seconds) ---")
print(response["message"]["content"])