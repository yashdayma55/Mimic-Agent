"""
MimicAgent Phase 4 end-to-end demo.
A clean multi-step run showing the full engine: locate -> overlay -> approve -> act,
repeated across several steps, with checkpointing.

SETUP: open a fresh Notepad before running.
"""

from replay_engine import app, wait_for_hotkey
from langgraph.types import Command

# a multi-step demo plan (all desktop / Notepad - reliable)
demo_plan = [
    {"step": 1, "instruction": "Click into the Notepad text area", "action": "click",
     "elem_name": "Text editor", "elem_type": "Document"},
    {"step": 2, "instruction": "Type a greeting", "action": "type",
     "text": "MimicAgent demo: "},
    {"step": 3, "instruction": "Type the first line", "action": "type",
     "text": "I was recorded once, "},
    {"step": 4, "instruction": "Type the second line", "action": "type",
     "text": "and now I replay myself."},
]

config = {"configurable": {"thread_id": "demo-run-1"}}
state = {"plan": demo_plan, "step_index": 0, "done": False,
         "approved": False, "last_window_title": "",
         "found": False, "missing_choice": "", "target_rect": []}

print("=" * 55)
print("  MimicAgent - end to end replay demo")
print("  Each step: it finds the target, shows a red box,")
print("  waits for your ENTER (approve) or ESC (reject),")
print("  then acts. Watch Notepad.")
print("=" * 55)

result = app.invoke(state, config=config)

while True:
    snapshot = app.get_state(config)
    if not snapshot.next:
        break
    interrupts = snapshot.tasks[0].interrupts if snapshot.tasks else []
    if not interrupts:
        break

    info = interrupts[0].value

    if "problem" in info:
        print(f"\n!!! {info['problem']}")
        choice = input("    retry / skip / stop: ").strip().lower()
        if choice not in ("retry", "skip", "stop"):
            choice = "stop"
        result = app.invoke(Command(resume=choice), config=config)
    else:
        print(f"\n>>> About to: {info['about_to_do']}")
        rect = snapshot.values.get("target_rect") or []
        if rect:
            try:
                from overlay import approve_with_overlay
                answer = approve_with_overlay(tuple(rect), info["about_to_do"])
            except Exception as e:
                print(f"    (overlay failed: {e})")
                print("    Press ENTER to approve, ESC to reject...")
                answer = wait_for_hotkey()
        else:
            print("    Press ENTER to approve, ESC to reject...")
            answer = wait_for_hotkey()
        print(f"    decision: {answer}")
        result = app.invoke(Command(resume=answer), config=config)

print("\n" + "=" * 55)
print("  DEMO COMPLETE - check Notepad for the full message")
print("=" * 55)