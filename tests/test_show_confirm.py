"""Show-me 'is that the one?' stays unanswered until the user replies."""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def main():
    from teach_loop import add_step, apply_show_witnesses, answer_chat, set_context
    from teaching import TaughtWorkflow, get_step, save_taught

    name = "_q_confirm"
    wd = os.path.join("workflows", name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "x")
    s = add_step(wf, "click Extensions")
    s.anchor = {
        "primary": {"name": "Extensions", "control_type": "Button"},
        "witnesses": {
            "a11y": {
                "saw": True,
                "name": "Extensions",
                "control_type": "Button",
                "rect": [0, 0, 40, 20],
                "account": "a Button named Extensions.",
                "confidence": "high",
            },
            "dom": {"saw": False, "account": "nothing here."},
            "vision": {"saw": False, "account": "nothing here."},
        },
        "agreement": "single",
    }
    save_taught(wf)
    q = "I saw a Button named 'Extensions' — is that the one?"
    apply_show_witnesses(wf, s.id, {
        "witnesses": {"witnesses": s.anchor["witnesses"], "agreement": "single"},
        "confirm_question": q,
    })
    s = get_step(wf, s.id)
    openq = [x for x in s.qa_history if not (x.get("a") or "").strip()]
    print("open questions:", [(x.get("kind"), x.get("q")) for x in openq])
    assert any(x.get("kind") == "show_confirm" for x in openq)
    answer_chat(wf, s.id, openq[0]["q"], "yes")
    s = get_step(wf, s.id)
    assert s.anchor.get("confirmed") is True
    print("SHOW CONFIRM OK")


if __name__ == "__main__":
    main()
