from pywinauto import Desktop
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict

# ---- the shared state that flows through every node ----
class ReplayState(TypedDict):
    plan: list
    step_index: int
    done: bool
    approved: bool  
    

# ---- the locator (Tier 1) from find_test.py ----
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

# ---- NODE 1: find the element for the current step ----
def find_node(state):
    step = state["plan"][state["step_index"]]
    print(f"\n[FIND] step {state['step_index']+1}: {step['instruction']}")
    el = find_element(step["elem_name"], step["elem_type"])
    if el:
        print(f"   found '{step['elem_name']}'")
    else:
        print(f"   NOT FOUND")
    return state

# ---- NODE 2: pause and ask the human to approve ----
def approve_node(state):
    step = state["plan"][state["step_index"]]
    # interrupt() PAUSES the graph and waits for a human answer
    answer = interrupt({"about_to_do": step["instruction"], "question": "approve?"})
    print(f"   human said: {answer}")
    state["approved"] = (answer == "approve")     # new remember the decision
    return state

# ---- NODE 3: perform the click ----
def act_node(state):
    if not state["approved"]:
        print("****skipped aka rejected******")#showing some respect for rejection by accepting it 
        return state
    step = state["plan"][state["step_index"]]
    el = find_element(step["elem_name"], step["elem_type"])
    if el:
        el.click_input()
        print(f"   >>> CLICKED {step['elem_name']}")
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

# ---- a tiny SAFE test plan (minimize buttons - harmless) ----
test_plan = [
    {"step": 1, "instruction": "Click Minimize", "action": "click",
     "elem_name": "Minimize", "elem_type": "Button"},
    {"step": 2, "instruction": "Click Minimize again", "action": "click",
     "elem_name": "Minimize", "elem_type": "Button"},
]

config = {"configurable": {"thread_id": "test-run-1"}}
state = {"plan": test_plan, "step_index": 0, "done": False, "approved": False}

print("=== Starting replay ===")
result = app.invoke(state, config=config)

# loop: keep resuming until the graph is finished
while True:
    # check if the graph is paused at an interrupt
    snapshot = app.get_state(config)
    if not snapshot.next:        # no next node = graph finished
        break

    # the graph is paused - ask the REAL human
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