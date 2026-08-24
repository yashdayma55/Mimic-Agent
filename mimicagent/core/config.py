"""Pipeline safety flags. Defaults are safe: no live side effects."""

# When False, irreversible actions must not execute for real.
LIVE_MODE = False

# When True, irreversible actions log intent and return simulated success.
DRY_RUN = True

# Default Claude model for vision / llm_generate (stdlib HTTP).
# Must match email_workflow_automation._call_vision_json production model.
VISION_MODEL = "claude-sonnet-4-5"

# Max SoM elements labeled on one capture.
SOM_MAX_ELEMS = 120

# Capture artifact directory (created on demand).
CAPTURE_DIR = "mimicagent_captures"
