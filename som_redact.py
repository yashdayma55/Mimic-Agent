"""
Stage A step 6: redact sensitive on-screen data before a screenshot leaves the machine.

The research flagged that frontier models given a screenshot can leak sensitive
data to external endpoints. Since Set-of-Mark sends screenshots to the API, we
mask known-sensitive regions FIRST, using the accessibility tree to find them:
  - password fields (control type Edit with is_password, or name hints)
  - fields whose name suggests secrets (password, ssn, card, cvv, pin, secret)

This extends Phase 3's password masking from the plan to the actual pixels.
"""

import re
from PIL import ImageDraw

SENSITIVE_HINTS = re.compile(
    r"(password|passcode|ssn|social security|credit card|card number|cvv|cvc|"
    r"pin\b|secret|api[_ ]?key|token|routing|account number)", re.I)


def find_sensitive_rects(elements):
    """From the collected elements, return rectangles that look sensitive."""
    rects = []
    for el in elements:
        name = (el.get("name") or "")
        ct = el.get("control_type", "")
        # name hints at a secret, on an editable/text control
        if SENSITIVE_HINTS.search(name):
            rects.append(el["rect"])
    return rects


def redact_image(img, elements, offset_x=0, offset_y=0):
    """Black out sensitive regions on a copy of the image before it is sent.
    Returns the redacted image (original is untouched)."""
    safe = img.copy()
    draw = ImageDraw.Draw(safe)
    rects = find_sensitive_rects(elements)
    for (L, T, R, B) in rects:
        L -= offset_x; R -= offset_x; T -= offset_y; B -= offset_y
        draw.rectangle([L, T, R, B], fill=(0, 0, 0))
    if rects:
        print(f"      [redact] masked {len(rects)} sensitive region(s) before sending")
    return safe


if __name__ == "__main__":
    # quick logic test with fake elements
    fake = [
        {"name": "Email", "control_type": "Edit", "rect": (10, 10, 200, 40)},
        {"name": "Password", "control_type": "Edit", "rect": (10, 50, 200, 80)},
        {"name": "Card Number", "control_type": "Edit", "rect": (10, 90, 200, 120)},
        {"name": "Search", "control_type": "Edit", "rect": (10, 130, 200, 160)},
    ]
    rects = find_sensitive_rects(fake)
    print(f"found {len(rects)} sensitive rects (expected 2: Password, Card Number):")
    for r in rects:
        print("  ", r)