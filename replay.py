import json

def load_plan(path="plan.json"):
    """Load the distilled plan into a list of steps."""
    with open(path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    return plan

plan = load_plan()

print(f"Loaded {len(plan)} steps\n")

# show the first few steps so we can see what we're working with
for step in plan[:5]:
    # TODO: print each step's number and instruction, e.g.:
    #   "  3. Click the 'Submit' button"
    # each step is a dict with keys like "step", "instruction", "action", "elem_name"
    print(step)