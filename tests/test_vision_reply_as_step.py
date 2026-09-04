"""Vision reply + Add this as a step (memory / produces)."""

from __future__ import annotations

import os
import shutil
import sys
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _pass(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        raise AssertionError(label + " " + str(detail))


def _fresh(name: str):
    from teach_loop import add_step, set_context
    from teaching import TaughtWorkflow, save_taught
    from workflow_folder import workflow_dir

    wd = workflow_dir(name)
    if os.path.isdir(wd):
        shutil.rmtree(wd)
    wf = TaughtWorkflow(name=name)
    set_context(wf, "linkedin apollo")
    step = add_step(wf, "read email")
    save_taught(wf)
    return step.id


def test_extract_email_facts():
    from vision_chat import extract_facts_from_text

    facts = extract_facts_from_text(
        "The address is jane.doe@acme.io and also jane.doe@acme.io again."
    )
    _pass("primary email", facts["primary_email"] == "jane.doe@acme.io")
    _pass("deduped", facts["emails"] == ["jane.doe@acme.io"])


def test_reply_reuses_frame():
    from teaching import get_step, load_taught
    from vision_chat import ask_vision, reply_vision

    name = "_vision_reply"
    sid = _fresh(name)
    ask = ask_vision(
        load_taught(name),
        sid,
        "what email is visible?",
        synthetic_bytes=b"png-frame-1",
        synthetic_answer="I see contact@example.com in the Emails section.",
    )
    _pass("ask ok", ask.get("ok"))
    frame1 = (ask.get("entry") or {}).get("frame_path")
    _pass("has frame", bool(frame1), frame1)
    _pass(
        "ask facts email",
        ((ask.get("entry") or {}).get("facts") or {}).get("primary_email")
        == "contact@example.com",
    )

    reply = reply_vision(
        load_taught(name),
        sid,
        "Is that email dynamic per profile?",
        synthetic_answer="Yes — contact@example.com is an example; it changes each person.",
    )
    _pass("reply ok", reply.get("ok"))
    entry = reply.get("entry") or {}
    _pass("reply kind", entry.get("kind") == "reply")
    _pass("same frame", entry.get("frame_path") == frame1, entry.get("frame_path"))
    step = get_step(load_taught(name), sid)
    _pass("two turns", len(step.vision_chat or []) == 2)


def test_apply_as_step_fills_memory():
    from teaching import get_step, load_taught
    from vision_chat import apply_vision_chat_to_step, ask_vision, reply_vision

    name = "_vision_as_step"
    sid = _fresh(name)
    ask_vision(
        load_taught(name),
        sid,
        "What email address is shown?",
        synthetic_bytes=b"png",
        synthetic_answer="The Emails field shows alex@company.com.",
    )
    reply_vision(
        load_taught(name),
        sid,
        "Confirm the exact address.",
        synthetic_answer="Exact address: alex@company.com",
    )

    with patch("teach_loop.explain_understanding", return_value={"ok": True}):
        out = apply_vision_chat_to_step(
            load_taught(name),
            sid,
            remember_prompt="Remember the visible email as {recipient_email}; it changes each profile.",
        )
    _pass("apply ok", out.get("ok"))
    filled = out.get("filled") or {}
    _pass("method prompt", filled.get("method") == "prompt")
    _pass("produces email", "{recipient_email}" in (filled.get("produces") or []))
    _pass("parameters email", "{recipient_email}" in (filled.get("parameters") or []))
    _pass("memory has email", "alex@company.com" in (filled.get("memory_note") or ""))
    _pass("memory dynamic", "dynamic" in (filled.get("memory_note") or "").lower())
    _pass("desc filled", bool(filled.get("user_description")))
    _pass("instruction filled", bool(filled.get("prompt_instruction")))

    step = get_step(load_taught(name), sid)
    _pass("varies note", "{recipient_email}" in (step.varies_note or ""))
    learned = (step.learned or {}).get("vision_extract") or {}
    _pass("sample stored", learned.get("sample_email") == "alex@company.com")
    _pass("dynamic flag", learned.get("dynamic") is True)


if __name__ == "__main__":
    print("test_vision_reply_as_step")
    test_extract_email_facts()
    test_reply_reuses_frame()
    test_apply_as_step_fills_memory()
    print("all passed")
