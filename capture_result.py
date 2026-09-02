"""Unified capture outcomes for Show me / Watch me (card + float widget)."""

from __future__ import annotations

from datetime import datetime, timezone

OUTCOMES = ("saved", "nothing_captured", "error")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_capture_result(step, *, mode: str, outcome: str, message: str = "",
                       detail: dict | None = None) -> dict:
    if outcome not in OUTCOMES:
        outcome = "error"
    rec = {
        "mode": mode,
        "outcome": outcome,
        "message": message or outcome.replace("_", " "),
        "at": _now(),
        "detail": detail or {},
    }
    step.last_capture = rec
    return rec


def message_for_show(result: dict) -> str:
    if result.get("outcome") == "error":
        return str(result.get("message") or result.get("error") or "capture error")
    if result.get("outcome") == "nothing_captured":
        return str(result.get("message") or "nothing captured")
    if result.get("summary"):
        return str(result["summary"])
    got = result.get("got")
    cc = result.get("click_count")
    if got is not None and cc:
        if int(got) >= int(cc):
            return f"saved {cc} anchor(s)"
        return f"saved {got} of {cc}"
    if result.get("anchor") or result.get("anchors"):
        n = len(result.get("anchors") or []) or (1 if result.get("anchor") else 0)
        return f"saved {max(n, 1)} anchor(s)"
    return "saved"


def outcome_from_show(result: dict) -> tuple[str, str]:
    if result.get("ok") is False or result.get("error"):
        return "error", str(result.get("error") or result.get("message") or "capture failed")
    if result.get("skipped_own_ui"):
        return "nothing_captured", "Mimic Agent UI excluded — point at the other app"
    witnesses = result.get("witnesses") or {}
    if isinstance(witnesses, dict) and witnesses.get("skipped_own_ui"):
        return "nothing_captured", "own UI skipped"
    wpack = witnesses.get("witnesses") if isinstance(witnesses.get("witnesses"), dict) else witnesses
    if isinstance(wpack, dict):
        any_saw = any((wpack.get(k) or {}).get("saw") for k in ("a11y", "dom", "vision"))
        if not any_saw and not result.get("anchor") and not result.get("anchors"):
            return "nothing_captured", "no witness saw a target at that point"
    if result.get("ignored"):
        return "saved", str(result.get("note") or "extra click ignored")
    if result.get("incomplete"):
        got = int(result.get("got") or result.get("heard") or 0)
        return "nothing_captured", str(
            result.get("chain_prompt") or f"only {got} of {result.get('click_count') or 2} click(s) captured"
        )
    anchor = result.get("anchor")
    anchors = result.get("anchors") or []
    if anchor or anchors:
        return "saved", message_for_show(result)
    if result.get("witness_results") or result.get("captures"):
        return "saved", message_for_show(result)
    return "nothing_captured", "nothing captured"


def outcome_from_watch(result: dict) -> tuple[str, str]:
    if result.get("ok") is False or result.get("error"):
        return "error", str(result.get("error") or "watch failed")
    learned = result.get("learned") or {}
    if learned.get("summary") or learned.get("vision"):
        return "saved", "learned ✓"
    if result.get("skipped_own_ui"):
        return "nothing_captured", "only Mimic Agent UI was visible"
    return "nothing_captured", "could not tell what this step does from what was seen"
