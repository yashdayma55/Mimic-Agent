"""
Unified harness step schema.

Every workflow step — recorded, trained, or hand-written — normalizes to
HarnessStep before the harness routes it to browser / native / reason engines.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional

# the three engines a step can route to
STEP_KINDS = ("browser", "native", "reason")


@dataclass
class HarnessStep:
    kind: str                      # one of STEP_KINDS
    description: str               # human-readable, editable transcript line
    # for kind="reason": a plain-language sub-goal for the agent loop
    goal: Optional[str] = None
    # for kind="browser"/"native": the concrete action + target hint
    action: Optional[dict] = None  # closed-vocab action dict, e.g. {"action":"click",...}
    target_name: Optional[str] = None      # element name/label to locate
    target_type: Optional[str] = None      # control_type hint
    # parameterization: values that change per run, referenced as {placeholder}
    inputs: list = field(default_factory=list)   # e.g. ["recipient_email"]

    def validate(self):
        assert self.kind in STEP_KINDS, f"bad kind {self.kind}"
        # a reason step needs a goal; a browser/native step needs an action or target
        if self.kind == "reason":
            assert self.goal, "reason step needs a goal"
        else:
            assert self.action or self.target_name, "concrete step needs action/target"
        return True


def step_to_dict(s: HarnessStep) -> dict:
    return asdict(s)


def step_from_dict(d: dict) -> HarnessStep:
    return HarnessStep(**{k: v for k, v in d.items() if k in HarnessStep.__dataclass_fields__})


if __name__ == "__main__":
    samples = [
        HarnessStep(
            kind="browser",
            description="click the Search button on the page",
            action={"action": "click", "id": 3, "why": "open search"},
            target_name="Search",
            target_type="Button",
        ),
        HarnessStep(
            kind="native",
            description="type hello into Notepad",
            action={"action": "type", "text": "hello", "type_mode": "replace"},
            target_name="Text Editor",
            target_type="Document",
        ),
        HarnessStep(
            kind="reason",
            description="figure out how to dismiss the cookie banner",
            goal="dismiss any cookie or consent banner if present",
        ),
    ]
    print("=== harness_schema: one of each STEP_KIND ===")
    for s in samples:
        s.validate()
        print(f"\n[{s.kind}] {s.description}")
        print(" ", step_to_dict(s))
    print("\nSTEP_KINDS =", STEP_KINDS)
    print("ok")
