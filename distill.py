import sqlite3
import json
import os


def reconstruct_text(keys):
    """Turn a list of raw key events into the actual typed text."""
    text = ""
    for k in keys:
        if k == "Key.space":
            text += " "
        elif k == "Key.backspace":
            text = text[:-1]              # delete last char
        elif k == "Key.enter":
            text += "\n"
        elif k.startswith("Key.") or k.startswith("'\\x"):
            continue                     # ignore ctrl, arrows, control chars
        else:
            text += k.strip("'")         # normal char like 'h' -> h
    return text


def is_password_field(name):
    """Detect if an element name looks like a password field."""
    if not name:
        return False
    name_low = name.lower()
    return "password" in name_low or "passcode" in name_low


def group_events(rows):
    """Collapse consecutive keystrokes into 'type' steps, repeated clicks into one, and mask passwords."""
    steps = []
    key_buffer = []
    last_field = ""      # name of the most recent field we clicked into

    for e in rows:
        if e["kind"] == "key":
            key_buffer.append(e["key"])
        else:  # a click ends any run of typing
            if key_buffer:
                text = reconstruct_text(key_buffer)
                if text.strip():                      # only if something real was typed
                    if is_password_field(last_field):
                        # never store the real password - store a reference instead
                        steps.append({"action": "type", "text": f"[SECRET: {last_field}]", "secret": True})
                    else:
                        steps.append({"action": "type", "text": text})
                key_buffer = []

            new_click = {
                "action": "click",
                "elem_name": e["elem_name"],
                "elem_type": e["elem_type"],
                "x": e["x"], "y": e["y"],
            }
            # Carry the per-click capture so transcript vision can re-label
            shot = None
            try:
                shot = e["screenshot"]
            except (KeyError, IndexError, TypeError):
                try:
                    shot = e.get("screenshot")
                except Exception:
                    shot = None
            if shot:
                new_click["screenshot"] = shot
            last_field = e["elem_name"]      # remember what we clicked into

            # collapse repeated clicks on the SAME element
            if (steps and steps[-1]["action"] == "click"
                    and steps[-1]["elem_name"] == new_click["elem_name"]
                    and steps[-1]["elem_type"] == new_click["elem_type"]):
                continue          # same as previous click -> skip it

            steps.append(new_click)

    # flush any trailing typing
    if key_buffer:
        text = reconstruct_text(key_buffer)
        if text.strip():
            if is_password_field(last_field):
                steps.append({"action": "type", "text": f"[SECRET: {last_field}]", "secret": True})
            else:
                steps.append({"action": "type", "text": text})

    return steps


def label_step(step):
    """Turn a grouped step into a human-readable intent line."""
    if step["action"] == "type":
        text = step["text"].strip().replace("\n", " ")
        return f'Type "{text}"'

    # it's a click
    name = step["elem_name"].strip()
    etype = step["elem_type"]

    if not name:
        return f"Click something (unlabeled {etype}) - needs vision"   # the fallback case

    # choose a natural verb based on the element type
    if etype in ("Button", "MenuItem"):
        return f'Click the "{name}" {etype.lower()}'
    elif etype in ("ListItem", "DataItem"):
        return f'Select "{name}"'
    elif etype in ("Edit", "ComboBox"):
        return f'Click into the "{name}" field'
    elif etype in ("TabItem",):
        return f'Switch to tab "{name}"'
    elif etype in ("Hyperlink",):
        return f'Click the link "{name}"'
    else:
        return f'Click "{name}" ({etype})'


def distill_recording(db_path="recording.db", plan_txt="plan.txt", plan_json="plan.json"):
    """Group recording.db events into labeled steps; write plan.txt + plan.json.

    Returns the labeled step list (same shape as plan.json).
    """
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"no recording database at {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM events ORDER BY ts").fetchall()
    conn.close()

    steps = group_events(rows)
    labeled = []
    for i, s in enumerate(steps, 1):
        labeled.append({"step": i, "instruction": label_step(s), **s})

    with open(plan_txt, "w", encoding="utf-8") as f:
        f.write(f"MimicAgent Plan  ({len(steps)} steps, from {len(rows)} raw events)\n")
        f.write("=" * 50 + "\n\n")
        for item in labeled:
            f.write(f"{item['step']:3}. {item['instruction']}\n")

    with open(plan_json, "w", encoding="utf-8") as f:
        json.dump(labeled, f, indent=2)

    print(f"Raw events: {len(rows)}  ->  Grouped steps: {len(steps)}")
    print(f"Wrote {plan_txt} and {plan_json}\n")
    for item in labeled:
        try:
            print(f"{item['step']:3}. {item['instruction']}")
        except UnicodeEncodeError:
            print(f"{item['step']:3}. (instruction has non-printable chars)")
    return labeled


if __name__ == "__main__":
    distill_recording()
