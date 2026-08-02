from pywinauto import Desktop
from pywinauto.keyboard import send_keys
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict


# ---- the shared state that flows through every node ----
# IMPORTANT: everything here must be simple, serializable data (str/int/list/dict).
# We store the window TITLE (a string), never the live window object.
class ReplayState(TypedDict):
    plan: list
    step_index: int
    done: bool
    approved: bool
    last_window_title: str


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


# ---- NODE 1: find the element for the current step ----
def find_node(state):
    step = state["plan"][state["step_index"]]
    print(f"\n[FIND] step {state['step_index']+1}: {step['instruction']}")
    if step["action"] == "type":
        print("   (type step - no element to find)")
        return state
    el = find_element(step["elem_name"], step["elem_type"])
    print(f"   found '{step['elem_name']}'" if el else "   NOT FOUND")
    return state


# ---- NODE 2: pause and ask the human to approve ----
def approve_node(state):
    step = state["plan"][state["step_index"]]
    answer = interrupt({"about_to_do": step["instruction"], "question": "approve?"})
    print(f"   human said: {answer}")
    state["approved"] = (answer == "approve")
    return state


# ---- NODE 3: perform the action (click or type), respecting the decision ----
def act_node(state):
    if not state["approved"]:
        print("   >>> SKIPPED (rejected)")
        return state

    step = state["plan"][state["step_index"]]

    if step["action"] == "type":
        _refocus_last_target(state)          # bring the target back to front first
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
            # store only the TITLE (a string) so the state stays serializable
            try:
                state["last_window_title"] = el.top_level_parent().window_text()
            except Exception:
                pass
        else:
            print(f"   >>> could not find {step['elem_name']}")

    return state


# ---- NODE 4: advance to the next step ----
def advance_node(state):
    state["step_index"] += 1
    if state["step_index"] >= len(state["plan"]):
        state["done"] = True
    return state


def more_steps(state):
    return "find" if not state["done"] else END


# ---- build the graph ----
graph = StateGraph(ReplayState)
graph.add_node("find", find_node)
graph.add_node("approve", approve_node)
graph.add_node("act", act_node)
graph.add_node("advance", advance_node)

graph.set_entry_point("find")
graph.add_edge("find", "approve")
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
         "approved": False, "last_window_title": ""}

print("=== Starting replay ===")
result = app.invoke(state, config=config)

while True:
    snapshot = app.get_state(config)
    if not snapshot.next:            # no next node = graph finished
        break
    interrupts = snapshot.tasks[0].interrupts if snapshot.tasks else []
    if interrupts:
        info = interrupts[0].value
        print(f"\n>>> About to: {info['about_to_do']}")
        choice = input("    Approve? (y/n): ").strip().lower()
        answer = "approve" if choice == "y" else "reject"
        result = app.invoke(Command(resume=answer), config=config)
    else:
        break

print("\n=== Done ===")