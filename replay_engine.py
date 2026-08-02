from pywinauto import Desktop
from pywinauto.keyboard import send_keys
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict


# ---- the shared state that flows through every node ----
# Everything here must be serializable (str/int/bool/list/dict) - no live objects.
class ReplayState(TypedDict):
    plan: list
    step_index: int
    done: bool
    approved: bool
    last_window_title: str
    found: bool               # did we find the element this step?
    missing_choice: str       # what the human chose when an element was missing


# ---- the locator (Tier 1): find an element by accessibility name + type ----
def find_element(elem_name, elem_type):
    desktop = Desktop(backend="uia")
    for win in desktop.windows():
        try:
            matches = win.descendants(title=elem_name, control_type=elem_type)
            if matches:
                return matches[0]
        except Exception:
            continue
    return None


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
    if step["action"] == "type":
        print("   (type step - no element to find)")
        state["found"] = True          # type steps don't need an element
        return state
    el = find_element(step["elem_name"], step["elem_type"])
    state["found"] = el is not None
    print(f"   found '{step['elem_name']}'" if el else "   NOT FOUND")
    return state


# ---- NODE: element missing - STOP and ask the human what to do ----
def missing_node(state):
    step = state["plan"][state["step_index"]]
    answer = interrupt({
        "problem": f"Could not find '{step['elem_name']}' for step {state['step_index']+1}",
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

    if step["action"] == "type":
        _refocus_last_target(state)
        text = step["text"]
        if text.startswith("[SECRET"):
            print(f"   >>> would type a secret ({text}) - skipping in test")
        else:
            send_keys(text, with_spaces=True)
            print(f'   >>> TYPED "{text}"')

    else:  # click
        el = find_element(step["elem_name"], step["elem_type"])
        if el:
            try:
                el.set_focus()
            except Exception:
                pass
            el.click_input()
            print(f"   >>> CLICKED {step['elem_name']}")
            try:
                state["last_window_title"] = el.top_level_parent().window_text()
            except Exception:
                pass
        else:
            print(f"   >>> could not find {step['elem_name']}")

    return state


# ---- NODE: advance to the next step ----
def advance_node(state):
    state["step_index"] += 1
    if state["step_index"] >= len(state["plan"]):
        state["done"] = True
    return state


# ---- routing functions (the conditional edges) ----
def after_find(state):
    # if found -> approve the action; if not -> handle the missing element
    return "approve" if state["found"] else "missing"

def after_missing(state):
    choice = state["missing_choice"]
    if choice == "retry":
        return "find"          # look again (maybe you opened the app)
    elif choice == "skip":
        return "advance"       # skip this step
    else:
        return END             # stop the whole run

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
graph.add_conditional_edges("find", after_find)      # found? -> approve : missing
graph.add_conditional_edges("missing", after_missing)  # retry / skip / stop
graph.add_edge("approve", "act")
graph.add_edge("act", "advance")
graph.add_conditional_edges("advance", more_steps)

app = graph.compile(checkpointer=MemorySaver())


# ---- a tiny SAFE test plan: click into Notepad, then type ----
test_plan = [
    {"step": 1, "instruction": "Click into Notepad text area", "action": "click",
     "elem_name": "Text editor", "elem_type": "Document"},
    {"step": 2, "instruction": "Type hello", "action": "type", "text": "hello mimicagent"},
]

config = {"configurable": {"thread_id": "test-run-1"}}
state = {"plan": test_plan, "step_index": 0, "done": False,
         "approved": False, "last_window_title": "",
         "found": False, "missing_choice": ""}

print("=== Starting replay ===")
result = app.invoke(state, config=config)

# resume loop: handle whichever kind of interrupt the graph paused on
while True:
    snapshot = app.get_state(config)
    if not snapshot.next:                      # no next node = graph finished
        break
    interrupts = snapshot.tasks[0].interrupts if snapshot.tasks else []
    if not interrupts:
        break

    info = interrupts[0].value

    if "problem" in info:
        # a "missing element" interrupt
        print(f"\n!!! {info['problem']}")
        choice = input("    retry / skip / stop: ").strip().lower()
        if choice not in ("retry", "skip", "stop"):
            choice = "stop"
        result = app.invoke(Command(resume=choice), config=config)
    else:
        # an "approve action" interrupt
        print(f"\n>>> About to: {info['about_to_do']}")
        choice = input("    Approve? (y/n): ").strip().lower()
        answer = "approve" if choice == "y" else "reject"
        result = app.invoke(Command(resume=answer), config=config)

print("\n=== Done ===")