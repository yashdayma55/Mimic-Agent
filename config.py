"""MimicAgent settings. Edit these to change behavior without touching code."""

# reasoning mode: "local" uses Ollama models (private/offline); "api" uses the
# hosted model via my_key.txt (stronger, needs a key). Affects vision + reasoning.
REASONING_MODE = "api"   # "local" or "api"

# local model sizes, selectable by hardware. Larger = smarter but slower on CPU.
LOCAL_VISION_MODEL = "qwen3-vl:2b"      # options: qwen3-vl:2b, etc.
LOCAL_REASON_MODEL = "qwen2.5:3b"       # options: 3b / 7b / 8b as hardware allows

# the hosted model id used when REASONING_MODE == "api"
API_MODEL = "claude-sonnet-4-5"

# safety / loop settings
MAX_STEPS = 8            # hard ceiling on the goal-driven loop
REQUIRE_APPROVAL = True  # human approves each action

# where the API key lives (gitignored)
KEY_FILE = "my_key.txt"
