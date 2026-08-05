from pywinauto import Desktop
from pywinauto.keyboard import send_keys
from pynput import keyboard as kb
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3
from typing import TypedDict
from locator import locate
from correction import handle_correction
from memory import open_memory, remember, recall


# ---- global hotkey approval fallback: Enter=approve, Esc=reject ----
def wait_for_hotkey():
    decision = {"answer": None}
    def on_press(key):
        if key == kb.Key.enter:
            decision["answer"] = "approve"
            return False
        elif key == kb.Key.esc:
            decision["answer"] = "reject"
            return False
    with kb.Listener(on_press=on_press) as listener:
        listener.join()
    return decision["answer"]


# ---- correction memory: one shared store open for the whole run ----
mem = open_memory("corrections.db")


class ReplayState(TypedDict):
    plan: list
    step_index: int
    done: bool
    approved: bool
    last_window_title: str
    found: bool
    missing_choice: str
    target_rect: list


def _refocus_last_target(state):
    title = state.get("last_window_title")
    if not title:
        return
    try:
        Desktop(backend="uia").window(title=title).set_focus()
        print("   (refocused target window)")
    except Exception:
        pass


def find_node(state):
    step = state["plan"][state["step_index"]]
    print(f"\n[FIND] step {state['step_index']+1}: {step['instruction']}")
    state["target_rect"] = []
    if step["action"] == "type":
        print("   (type step - no element to find)")
        state["found"] = True
        return state
    got = locate(step)
    state["found"] = got[0] is not None
    if got[0] == "BROWSER":
        print("   found via browser (playwright)")
    elif got[0] == "VISION":
        print("   found via vision (tier 5)")
        res = got[2]
        state["target_rect"] = [res["x"]-50, res["y"]-20, res["x"]+50, res["y"]+20]
    elif got[0]:
        print(f"   found via tier {got[1]}")
        try:
            r = got[0].rectangle()
            state["target_rect"] = [r.left, r.top, r.right, r.bottom]
        except Exception:
            pass
    else:
        print("   NOT FOUND by any tier")
    return state


def missing_node(state):
    step = state["plan"][state["step_index"]]
    answer = interrupt({
        "problem": f"Could not find '{step.get('elem_name','?')}' for step {state['step_index']+1}",
        "question": "retry / skip / stop?"})
    print(f"   human chose: {answer}")
    state["missing_choice"] = answer
    return state


def approve_node(state):
    step = state["plan"][state["step_index"]]
    answer = interrupt({"about_to_do": step["instruction"], "question": "approve?"})
    print(f"   human said: {answer}")
    state["approved"] = (answer == "approve")
    return state


def act_node(state):
    if not state["approved"]:
        print("   >>> SKIPPED (rejected)")
        return state
    step = state["plan"][state["step_index"]]

    if step.get("_skip"):
        print("   >>> SKIPPED (corrected to skip)")
        return state

    if step["action"] == "type":
        text = step["text"]
        if text.startswith("[SECRET"):
            print(f"   >>> would type a secret ({text}) - skipping in test")
            return state
        got = locate(step) if step.get("elem_name") else (None, None)
        if got and got[0] == "BROWSER":
            try:
                got[2]["element"].fill(text)
                print(f'   >>> FILLED "{text}" (browser)')
                return state
            except Exception:
                pass
        _refocus_last_target(state)
        send_keys(text, with_spaces=True)
        print(f'   >>> TYPED "{text}"')
        return state

    got = locate(step)
    if got[0] == "BROWSER":
        got[2]["element"].click()
        print(f"   >>> CLICKED '{step.get('elem_name')}' via BROWSER")
    elif got[0] == "VISION":
        import pyautogui
        res = got[2]
        pyautogui.click(res["x"], res["y"])
        print(f"   >>> CLICKED at ({res['x']},{res['y']}) via VISION")
    elif got[0]:
        el, tier = got[0], got[1]
        try:
            el.set_focus()
        except Exception:
            pass
        el.click_input()
        print(f"   >>> CLICKED {step.get('elem_name','?')} (via tier {tier})")
        try:
            state["last_window_title"] = el.top_level_parent().window_text()
        except Exception:
            pass
    else:
        print(f"   >>> could not find {step.get('elem_name','?')}")
    return state


def advance_node(state):
    state["step_index"] += 1
    if state["step_index"] >= len(state["plan"]):
        state["done"] = True
    return state


def after_find(state):
    return "approve" if state["found"] else "missing"

def after_missing(state):
    c = state["missing_choice"]
    return "find" if c == "retry" else "advance" if c == "skip" else END

def more_steps(state):
    return "find" if not state["done"] else END


graph = StateGraph(ReplayState)
graph.add_node("find", find_node)
graph.add_node("missing", missing_node)
graph.add_node("approve", approve_node)
graph.add_node("act", act_node)
graph.add_node("advance", advance_node)
graph.set_entry_point("find")
graph.add_conditional_edges("find", after_find)
graph.add_conditional_edges("missing", after_missing)
graph.add_edge("approve", "act")
graph.add_edge("act", "advance")
graph.add_conditional_edges("advance", more_steps)

conn = sqlite3.connect("replay_checkpoints.db", check_same_thread=False)
checkpointer = SqliteSaver(conn)
app = graph.compile(checkpointer=checkpointer)


test_plan = [
    {"step": 1, "instruction": "Click into Notepad text area", "action": "click",
     "elem_name": "Text editor", "elem_type": "Document"},
    {"step": 2, "instruction": "Type a greeting", "action": "type",
     "elem_name": "Email", "text": "original text"},
]

config = {"configurable": {"thread_id": "test-run-1"}}
state = {"plan": test_plan, "step_index": 0, "done": False,
         "approved": False, "last_window_title": "",
         "found": False, "missing_choice": "", "target_rect": []}

print("=== Starting replay (Phase 5: correction + memory) ===")
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

        # ---- MEMORY: before asking, check for a remembered correction ----
        idx = snapshot.values["step_index"]
        step = snapshot.values["plan"][idx]
        past, remembered_edit = recall(mem, step)
        if remembered_edit:
            print(f"    [memory] last time on a step like this you chose: {remembered_edit['edit']}")
            print(f"    [memory] (press 'm' to apply that remembered correction)")

        raw = input("    [Enter]=approve | type a correction | 'm'=remembered | 'skip'=reject: ").strip()

        if raw == "":
            result = app.invoke(Command(resume="approve"), config=config)

        elif raw.lower() in ("skip", "reject", "no"):
            result = app.invoke(Command(resume="reject"), config=config)

        elif raw.lower() == "m" and remembered_edit:
            # apply the remembered correction
            from correction import apply_edit
            echo = apply_edit(step, remembered_edit)
            print(f"    {echo}  (from memory)")
            if remembered_edit["edit"] == "skip":
                result = app.invoke(Command(resume="reject"), config=config)
            else:
                app.update_state(config, {"plan": snapshot.values["plan"]})
                result = app.invoke(Command(resume="approve"), config=config)

        else:
            # a fresh natural-language correction
            edit, echo = handle_correction(step, raw)
            print(f"    {echo}")
            if edit is None:
                continue
            # ---- MEMORY: remember this correction for the future ----
            remember(mem, step, edit)
            if edit["edit"] == "skip":
                result = app.invoke(Command(resume="reject"), config=config)
            else:
                app.update_state(config, {"plan": snapshot.values["plan"]})
                result = app.invoke(Command(resume="approve"), config=config)

print("\n=== Done ===")
mem.close()