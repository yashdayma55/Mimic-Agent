import sqlite3

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
def group_events(rows):
    """Collapse consecutive keystrokes into 'type' steps and repeated clicks into one."""
    steps = []
    key_buffer = []

    for e in rows:
        if e["kind"] == "key":
            key_buffer.append(e["key"])
        else:  # click
            if key_buffer:
                text = reconstruct_text(key_buffer)
                if text.strip():
                    steps.append({"action": "type", "text": text})
                key_buffer = []

            new_click = {
                "action": "click",
                "elem_name": e["elem_name"],
                "elem_type": e["elem_type"],
                "x": e["x"], "y": e["y"],
            }

            # collapse repeated clicks on the SAME element
            if (steps and steps[-1]["action"] == "click"
                    and steps[-1]["elem_name"] == new_click["elem_name"]
                    and steps[-1]["elem_type"] == new_click["elem_type"]):
                continue          # same as previous click -> skip it

            steps.append(new_click)

    if key_buffer:
        text = reconstruct_text(key_buffer)
        if text.strip():
            steps.append({"action": "type", "text": text})

    return steps
# def group_events(rows):
#     """Collapse consecutive keystrokes into single 'type' steps."""
#     steps = []
#     key_buffer = []

#     for e in rows:
#         if e["kind"] == "key":
#             key_buffer.append(e["key"])
#         else:  # a click ends any run of typing
#             if key_buffer:
#                 text = reconstruct_text(key_buffer)
#                 if text.strip():                      # only if something real was typed
#                     steps.append({"action": "type", "text": text})
#                 key_buffer = []
#             steps.append({
#                 "action": "click",
#                 "elem_name": e["elem_name"],
#                 "elem_type": e["elem_type"],
#                 "x": e["x"], "y": e["y"],
#             })

#     # flush any trailing typing
#     if key_buffer:
#         text = reconstruct_text(key_buffer)
#         if text.strip():
#             steps.append({"action": "type", "text": text})

#     return steps


conn = sqlite3.connect("recording.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM events ORDER BY ts").fetchall()
conn.close()

steps = group_events(rows)

print(f"Raw events: {len(rows)}  ->  Grouped steps: {len(steps)}\n")
for i, s in enumerate(steps, 1):
    if s["action"] == "type":
        print(f"{i:3}. TYPE  \"{s['text']}\"")
    else:
        print(f"{i:3}. CLICK '{s['elem_name']}' [{s['elem_type']}]")