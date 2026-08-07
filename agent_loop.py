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

from set_of_mark import build_marked_screenshot


def perceive(save_path="agent_view.png"):
    """PERCEIVE: capture the screen as a numbered element list + marked image.
    Returns (elements, image_path). This is what the model 'sees' each turn."""
    elements, path = build_marked_screenshot(save_path=save_path)
    return elements, path


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