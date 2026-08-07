"""
MimicAgent Stage A - Set-of-Mark grounding.

Instead of asking a vision model to GUESS a coordinate (which drifts), we:
  1. collect the clickable elements on screen (from the accessibility tree)
  2. draw a numbered box on each one  <- THIS FILE, step 1
  3. ask a strong model which NUMBER matches the intent  (next step)
  4. map the number back to that box's exact center and click

Step 1 proves the numbering is correct: we enumerate on-screen elements, draw
labelled boxes, and save an annotated image so we can eyeball it.
"""

import io
import mss
from PIL import Image, ImageDraw, ImageFont
from pywinauto import Desktop


def collect_clickable_elements(window_title=None, max_elems=60):
    """Walk the accessibility tree and return a list of clickable elements as
    dicts: {id, name, control_type, rect=(L,T,R,B), cx, cy}.
    We keep interactive control types and skip huge/!visible containers."""
    CLICKABLE = {"Button", "Hyperlink", "MenuItem", "TabItem", "ListItem",
                 "Edit", "ComboBox", "CheckBox", "RadioButton", "Text",
                 "TreeItem", "Image", "Custom", "Group", "Pane"}
    elements = []
    try:
        if window_title:
            root = Desktop(backend="uia").window(title_re=f".*{window_title}.*")
        else:
            # no title given: use the currently ACTIVE (foreground) window
            from pywinauto import Application
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            root = Desktop(backend="uia").window(handle=hwnd)
        root.wait("exists", timeout=3)
        descendants = root.descendants()
    except Exception as e:
        print(f"   could not walk tree: {e}")
        return elements

    idx = 1
    for el in descendants:
        try:
            ct = el.element_info.control_type
            if ct not in CLICKABLE:
                continue
            r = el.rectangle()
            w, h = r.right - r.left, r.bottom - r.top
            # skip zero-size and giant full-screen containers
            if w < 8 or h < 8 or w > 1800 or h > 1000:
                continue
            elements.append({
                "id": idx,
                "name": (el.window_text() or "")[:40],
                "control_type": ct,
                "rect": (r.left, r.top, r.right, r.bottom),
                "cx": (r.left + r.right) // 2,
                "cy": (r.top + r.bottom) // 2,
            })
            idx += 1
            if idx > max_elems:
                break
        except Exception:
            continue
    return elements


def grab_full_screen():
    """Screenshot the whole (primary) screen as a PIL image + its offset."""
    with mss.mss() as sct:
        mon = sct.monitors[1]           # primary monitor
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)
    return img, mon["left"], mon["top"]


def draw_marks(img, elements, offset_x=0, offset_y=0):
    """Draw a numbered box on each element. Returns the annotated image."""
    annotated = img.copy()
    draw = ImageDraw.Draw(annotated)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    for el in elements:
        L, T, R, B = el["rect"]
        L -= offset_x; R -= offset_x; T -= offset_y; B -= offset_y
        # the box
        draw.rectangle([L, T, R, B], outline=(255, 0, 0), width=2)
        # the number label with a filled background so it's readable
        label = str(el["id"])
        tw, th = 18, 18
        draw.rectangle([L, T, L + tw, T + th], fill=(255, 0, 0))
        draw.text((L + 3, T + 1), label, fill=(255, 255, 255), font=font)
    return annotated


def build_marked_screenshot(window_title=None, save_path="marked.png"):
    """The full step-1 pipeline: collect elements, screenshot, draw marks, save.
    Returns (elements, saved_path)."""
    elements = collect_clickable_elements(window_title)
    img, ox, oy = grab_full_screen()
    annotated = draw_marks(img, elements, ox, oy)
    annotated.save(save_path)
    return elements, save_path


if __name__ == "__main__":
    print("Set-of-Mark step 1: numbering on-screen clickable elements")
    # optionally focus a specific window by title substring; None = whole desktop
    els, path = build_marked_screenshot(window_title=None, save_path="marked.png")
    print(f"\nfound {len(els)} clickable elements. annotated image saved to: {path}\n")
    for el in els[:25]:
        print(f"  [{el['id']:2}] {el['control_type']:10} '{el['name']}' "
              f"center=({el['cx']},{el['cy']})")
    if len(els) > 25:
        print(f"  ... and {len(els)-25} more")
    print(f"\nOpen {path} and check: does each number sit on a real clickable thing?")