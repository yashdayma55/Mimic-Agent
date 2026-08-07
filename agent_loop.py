"""
MimicAgent Stage B - the goal-driven ReAct loop.

Give a plain-language goal, and the agent loops:
  PERCEIVE -> REASON -> (approve) -> ACT -> OBSERVE -> check goal -> repeat

We build this beat by beat. Step 1 = PERCEIVE: turn the current screen into a
numbered list of elements + a marked screenshot the model can reason over.
Reuses the Stage A Set-of-Mark machinery.
"""

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from set_of_mark import collect_clickable_elements, grab_full_screen, draw_marks

try:
    from som_redact import redact_image
except Exception:
    redact_image = None


def perceive(save_path="agent_view.png"):
    """PERCEIVE: capture the screen as a numbered element list + marked image,
    REDACTING sensitive fields (passwords, cards, etc.) before the image is saved
    or sent to the model - same safety as Stage A. Returns (elements, image_path)."""
    elements = collect_clickable_elements()
    img, ox, oy, scale = grab_full_screen()
    for el in elements:
        el["sx"] = int((el["cx"] - ox) * scale)
        el["sy"] = int((el["cy"] - oy) * scale)
    # 1. black out sensitive regions BEFORE marking/sending
    if redact_image:
        img = redact_image(img, elements, ox, oy)
    # 2. then draw the numbered marks
    annotated = draw_marks(img, elements, ox, oy, scale)
    annotated.save(save_path)
    return elements, save_path


def describe_perception(elements):
    """Build the compact text menu of numbered elements for the model prompt."""
    return "\n".join(
        f"{el['id']}: {el['control_type']} '{el['name']}'" for el in elements
    )


if __name__ == "__main__":
    print("Stage B step 1: PERCEIVE the screen")
    elements, path = perceive()
    print(f"\nperceived {len(elements)} elements -> {path}\n")
    print(describe_perception(elements)[:1200])
    print("\n(this numbered menu + the screenshot is what the model reasons over)")