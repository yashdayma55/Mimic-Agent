"""Pipeline safety config. Edit LIVE_MODE only when deliberately going live."""

import os
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent

# Golden rule: keep False until you intentionally send to real founders.
LIVE_MODE = False

# Outreach Apollo flow is vision-based (tile scan + screen clicks) and does NOT
# need CDP. Keep False so we never kill/relaunch the user's normal Chrome into
# a fresh debug session (which breaks Apollo sign-in / extension state).
# Optional: if debug Chrome happens to already be on 9222, CDP helpers may be
# used as a nicety — we still never launch or relaunch it.
REQUIRE_DEBUG_CHROME = False

# Your own inbox for dry runs — never a real founder during testing.
# Set via env EWA_SAFE_TEST_RECIPIENT or gitignored .safe_recipient file.
_SAFE_FILE = _PKG_DIR / ".safe_recipient"


def _load_safe_recipient() -> str:
    env_val = (os.environ.get("EWA_SAFE_TEST_RECIPIENT") or "").strip()
    if env_val:
        return env_val
    try:
        if _SAFE_FILE.is_file():
            return _SAFE_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


SAFE_TEST_RECIPIENT = _load_safe_recipient()

# Seconds between people in a batch (jitter added in run_list).
BATCH_DELAY_SEC = 8

# Sub-goal step ceiling when using the reason loop for browser substeps.
MAX_REASON_STEPS = 10

# Default outreach template placeholders (filled by draft.py).
DEFAULT_SUBJECT = "Quick intro — {person}"
DEFAULT_SENDER_NAME = os.environ.get("EWA_SENDER_NAME", "").strip() or "Mimic Agent user"
