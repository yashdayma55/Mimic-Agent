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


# ---- the shared state that flows through every node ----
# Everything here must be serializable (str/int/bool/list/dict) - no live objects.
class ReplayState(TypedDict):
    plan: list
    step_index: int
    done: bool
    approved: bool
    last_window_title: str
    found: bool
    missing_choice: str
    target_rect: list          # [L, T, R, B] of the element to highlight


def _refocus_last_target(state):
    """Re-find the last target window by its title and bring it to the front."""
    title = state.get("last_window_title")
    if not title:
        return
    try:
        win = Desktop(backend="uia").window(title=title)
        win.set_focus()
        print("   (refocused target window)")
    except Exception:
        pass


# ---- NODE: find the element for the current step ----
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
        state["target_rect"] = [res["x"] - 50, res["y"] - 20, res["x"] + 50, res["y"] + 20]
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


# ---- NODE: element missing - STOP and ask the human what to do ----
def missing_node(state):
    step = state["plan"][state["step_index"]]
    answer = interrupt({
        "problem": f"Could not find '{step.get('elem_name','?')}' for step {state['step_index']+1}",
        "question": "retry / skip / stop?"
    })
    print(f"   human chose: {answer}")
    state["missing_choice"] = answer
    return state


# ---- NODE: pause and ask the human to approve the action ----
def approve_node(state):
    step = state["plan"][state["step_index"]]
    answer = interrupt({"about_to_do": step["instruction"], "question": "approve?"})
    print(f"   human said: {answer}")
    state["approved"] = (answer == "approve")
    return state


# ---- NODE: perform the action (click or type), respecting the decision ----
def act_node(state):
    if not state["approved"]:
        print("   >>> SKIPPED (rejected)")
        return state
    step = state["plan"][state["step_index"]]

    # a correction may have marked this step to be skipped
    if step.get("_skip"):
        print("   >>> SKIPPED (corrected to skip)")
        return state

    if step["action"] == "type":
        text = step["text"]
        if text.startswith("[SECRET"):
            print(f"   >>> would type a secret ({text}) - skipping in test")
            return state
        # browser step -> fill via playwright
        got = locate(step) if step.get("elem_name") else (None, None)
        if got and got[0] == "BROWSER":
            try:
                got[2]["element"].fill(text)
                print(f'   >>> FILLED "{text}" (browser)')
                return state
            except Exception:
                pass
        # desktop typing
        _refocus_last_target(state)
        send_keys(text, with_spaces=True)
        print(f'   >>> TYPED "{text}"')
        return state

    # ---- click ----
    got = locate(step)
    if got[0] == "BROWSER":
        got[2]["element"].click()
        print(f"   >>> CLICKED '{step.get('elem_name')}' via BROWSER (playwright)")
    elif got[0] == "VISION":
        import pyautogui
        res = got[2]
        pyautogui.click(res["x"], res["y"])
        print(f"   >>> CLICKED at ({res['x']},{res['y']}) via VISION (tier 5)")
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


# ---- NODE: advance to the next step ----
def advance_node(state):
    state["step_index"] += 1
    if state["step_index"] >= len(state["plan"]):
        state["done"] = True
    return state


# ---- routing functions (conditional edges) ----
def after_find(state):
    return "approve" if state["found"] else "missing"

def after_missing(state):
    choice = state["missing_choice"]
    if choice == "retry":
        return "find"
    elif choice == "skip":
        return "advance"
    else:
        return END

def more_steps(state):
    return "find" if not state["done"] else END


# ---- build the graph ----
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


# ---- a tiny SAFE test plan: click into Notepad, then type (try correcting step 2!) ----
test_plan = [
    {"step": 1, "instruction": "Click into Notepad text area", "action": "click",
     "elem_name": "Text editor", "elem_type": "Document"},
    {"step": 2, "instruction": "Type a greeting", "action": "type", "text": "original text"},
]

config = {"configurable": {"thread_id": "test-run-1"}}
state = {"plan": test_plan, "step_index": 0, "done": False,
         "approved": False, "last_window_title": "",
         "found": False, "missing_choice": "", "target_rect": []}

print("=== Starting replay (Phase 5: type a sentence to CORRECT a step) ===")
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
        # missing-element interrupt (terminal for the 3-way choice)
        print(f"\n!!! {info['problem']}")
        choice = input("    retry / skip / stop: ").strip().lower()
        if choice not in ("retry", "skip", "stop"):
            choice = "stop"
        result = app.invoke(Command(resume=choice), config=config)
    else:
        # approve-action interrupt: show overlay box, then approve / reject / CORRECT
        print(f"\n>>> About to: {info['about_to_do']}")
        rect =[]
        answer = None
        if rect:
            try:
                from overlay import approve_with_overlay
                answer = approve_with_overlay(tuple(rect), info["about_to_do"])
            except Exception as e:
                print(f"    (overlay failed: {e})")
                answer = None

        # if the overlay didn't return a clear approve/reject, fall to the terminal,
        # which ALSO accepts a typed natural-language correction
        if answer not in ("approve", "reject"):
            raw = input("    [Enter]=approve | type a correction | 'skip'=reject: ").strip()
            if raw == "":
                answer = "approve"
            elif raw.lower() in ("skip", "reject", "no"):
                answer = "reject"
            else:
                # a natural-language correction
                idx = snapshot.values["step_index"]
                step = snapshot.values["plan"][idx]
                edit, echo = handle_correction(step, raw)
                print(f"    {echo}")
                if edit is None:
                    continue                       # not understood - loop asks again
                if edit["edit"] == "skip":
                    answer = "reject"
                else:
                    app.update_state(config, {"plan": snapshot.values["plan"]})
                    answer = "approve"

        print(f"    decision: {answer}")
        result = app.invoke(Command(resume=answer), config=config)

print("\n=== Done ===")