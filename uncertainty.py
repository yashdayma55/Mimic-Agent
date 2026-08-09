"""
Uncertainty detection for human-in-the-loop clarification.

Deterministic EXTERNAL checks (not model self-report). Any signal that trips
causes a pause so the user can pick an element id.

Signals:
  1. structural_ambiguity  — always available
  2. cross_call_disagreement — optional (~2x reason cost)
  3. token_entropy_uncertain — optional (local logprobs only)
"""

import math
import re
from collections import Counter

# ---- tunable thresholds ----
# Structural: names this similar (normalized) count as the same label family
NAME_SIMILARITY_RATIO = 0.85
# Structural: minimum other matches (besides chosen) to flag ambiguity
MIN_AMBIGUOUS_PEERS = 1  # chosen + >=1 peer => 2+ plausible

# Cross-call: only compare these action kinds
CROSS_CALL_ACTIONS = frozenset({"click", "type"})

# Token entropy (signal 3): Shannon entropy over token probs
ENTROPY_HIGH = 1.2          # nats; above this -> uncertain
TOP1_TOP2_MARGIN_NARROW = 0.15  # probability gap; below this -> uncertain


def _norm_name(name):
    """Normalize element name for comparison."""
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    # strip common trailing junk (counts, shortcuts)
    s = re.sub(r"[\d]+$", "", s).strip()
    return s


def _names_similar(a, b):
    """True if two names are effectively the same label (exact or near-exact)."""
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # one contains the other (short labels)
    if len(na) >= 3 and len(nb) >= 3 and (na in nb or nb in na):
        return True
    # character overlap ratio (Dice on char bigrams)
    def bigrams(s):
        if len(s) < 2:
            return Counter([s])
        return Counter(s[i:i + 2] for i in range(len(s) - 1))
    ba, bb = bigrams(na), bigrams(nb)
    if not ba or not bb:
        return False
    overlap = sum((ba & bb).values())
    total = sum(ba.values()) + sum(bb.values())
    if total <= 0:
        return False
    dice = (2.0 * overlap) / total
    return dice >= NAME_SIMILARITY_RATIO


def _coerce_id(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def structural_ambiguity(action, elements):
    """Signal 1: is the intended target ambiguous among perceived elements?

    If the action is a click with an id, check whether OTHER elements share a
    very similar name/role. Return (True, reason, candidate_ids) if 2+ plausible
    matches exist, else (False, '', []).
    """
    if not action or not elements:
        return False, "", []

    kind = (action.get("action") or "").strip().lower()
    if kind != "click":
        return False, "", []

    eid = _coerce_id(action.get("id"))
    if eid is None:
        return False, "", []

    by_id = {e.get("id"): e for e in elements if e.get("id") is not None}
    chosen = by_id.get(eid)
    if not chosen:
        return False, "", []

    cname = chosen.get("name") or ""
    ctype = (chosen.get("control_type") or "").strip()
    if not _norm_name(cname):
        # unnamed target: look for other unnamed same role
        peers = [
            e for e in elements
            if e.get("id") != eid
            and not _norm_name(e.get("name"))
            and (e.get("control_type") or "").strip() == ctype
        ]
        if len(peers) < MIN_AMBIGUOUS_PEERS:
            return False, "", []
        cand = [eid] + [e["id"] for e in peers]
        reason = (
            f"chosen id={eid} is unnamed {ctype}; "
            f"{len(peers)} other unnamed {ctype}(s) look the same"
        )
        return True, reason, cand

    peers = []
    for e in elements:
        if e.get("id") == eid:
            continue
        if ctype and (e.get("control_type") or "").strip() != ctype:
            # same name different role is usually fine; still allow same-name any role
            if not _names_similar(cname, e.get("name")):
                continue
        if _names_similar(cname, e.get("name")):
            peers.append(e)

    if len(peers) < MIN_AMBIGUOUS_PEERS:
        return False, "", []

    cand = [eid] + [e["id"] for e in peers]
    # stable unique order
    seen = set()
    ordered = []
    for i in cand:
        if i not in seen:
            seen.add(i)
            ordered.append(i)
    reason = (
        f"click id={eid} '{cname}' ({ctype}) has "
        f"{len(peers)} similar peer(s): "
        + ", ".join(
            f"[{e['id']}] {(e.get('name') or '')!r}" for e in peers[:6]
        )
    )
    return True, reason, ordered


def _action_fingerprint(action):
    """Comparable identity of an action for disagreement checks."""
    if not action:
        return ("", None, "")
    kind = (action.get("action") or "").strip().lower()
    eid = _coerce_id(action.get("id"))
    # for type, include a short text hash so totally different types disagree
    text = (action.get("text") or "")[:40] if kind == "type" else ""
    extra = ""
    if kind == "scroll":
        extra = (action.get("to_find") or action.get("direction") or "")[:40]
    elif kind == "navigate":
        extra = (action.get("url") or "")[:60]
    return (kind, eid, text or extra)


def cross_call_disagreement(reason_fn, goal, elements, image_path, history):
    """Signal 2: call the reasoner TWICE and compare target/action.

    reason_fn is agent_reason.reason_next_action. Second call uses a light
    correction nudge so the prompt differs (temperature/seed not always
    exposed on hosted APIs). Only meaningful for click/type; skip wait/done.
    """
    if reason_fn is None or not goal or not elements or not image_path:
        return False, "", []

    try:
        a1 = reason_fn(goal, elements, image_path, history)
    except Exception as e:
        return False, f"cross-call first reason failed: {e}", []

    kind1 = (a1.get("action") or "").strip().lower()
    if kind1 not in CROSS_CALL_ACTIONS and kind1 not in ("done", "stuck", "wait"):
        # still compare if first is click-like via id
        if _coerce_id(a1.get("id")) is None:
            return False, "", []
    if kind1 in ("done", "stuck", "wait", "copy", "paste"):
        return False, "", []

    # Second call: nudge via correction so the request is not byte-identical
    try:
        a2 = reason_fn(
            goal, elements, image_path, history,
            correction=(
                "Re-decide carefully. If several similar elements could work, "
                "pick the single best one for the goal."
            ),
        )
    except Exception as e:
        return False, f"cross-call second reason failed: {e}", []

    fp1, fp2 = _action_fingerprint(a1), _action_fingerprint(a2)
    kind_a, id_a, _ = fp1
    kind_b, id_b, _ = fp2

    # Agreement: same action kind and (if both have ids) same id
    if kind_a == kind_b:
        if id_a is not None and id_b is not None:
            if id_a == id_b:
                return False, "", []
            reason = (
                f"cross-call disagreement: first click/target id={id_a}, "
                f"second id={id_b}"
            )
            return True, reason, [i for i in (id_a, id_b) if i is not None]
        if fp1 == fp2:
            return False, "", []
        reason = (
            f"cross-call disagreement: first {fp1!r} vs second {fp2!r}"
        )
        cands = [i for i in (id_a, id_b) if i is not None]
        return True, reason, cands

    reason = (
        f"cross-call disagreement: first action={kind_a!r} id={id_a}, "
        f"second action={kind_b!r} id={id_b}"
    )
    cands = [i for i in (id_a, id_b) if i is not None]
    return True, reason, cands


def token_entropy_uncertain(logprobs):
    """Signal 3 (optional): Shannon entropy / top1-top2 margin on token probs.

    logprobs: iterable of log-probabilities (natural log) for the top-k tokens
    of the action decision, OR a list of {'logprob': float, ...} dicts.
    If None/empty, skip silently -> (False, '', []).
    """
    if not logprobs:
        return False, "", []

    values = []
    for item in logprobs:
        if isinstance(item, (int, float)):
            values.append(float(item))
        elif isinstance(item, dict):
            if "logprob" in item:
                values.append(float(item["logprob"]))
            elif "prob" in item:
                p = float(item["prob"])
                if p > 0:
                    values.append(math.log(p))
        else:
            continue
    if not values:
        return False, "", []

    # Convert logprobs -> probabilities (normalize over provided top-k)
    max_lp = max(values)
    exps = [math.exp(lp - max_lp) for lp in values]
    z = sum(exps) or 1.0
    probs = [e / z for e in exps]
    probs.sort(reverse=True)

    entropy = -sum(p * math.log(p) for p in probs if p > 0)
    margin = (probs[0] - probs[1]) if len(probs) >= 2 else 1.0

    if entropy >= ENTROPY_HIGH:
        return True, f"token entropy high ({entropy:.3f} >= {ENTROPY_HIGH})", []
    if margin <= TOP1_TOP2_MARGIN_NARROW:
        return (
            True,
            f"top1-top2 margin narrow ({margin:.3f} <= {TOP1_TOP2_MARGIN_NARROW})",
            [],
        )
    return False, "", []


def assess_uncertainty(action, elements, *, reason_fn=None, goal=None,
                       image_path=None, history=None, logprobs=None,
                       run_cross_call=True):
    """Combine signals with OR logic.

    Always runs structural_ambiguity.
    Runs cross_call_disagreement if reason_fn provided and run_cross_call.
    Runs token_entropy_uncertain if logprobs provided.

    Returns (is_uncertain, reason, candidate_ids).
    """
    reasons = []
    candidates = []

    u1, r1, c1 = structural_ambiguity(action, elements)
    if u1:
        reasons.append(r1)
        for i in c1:
            if i not in candidates:
                candidates.append(i)

    if run_cross_call and reason_fn is not None:
        u2, r2, c2 = cross_call_disagreement(
            reason_fn, goal, elements, image_path, history
        )
        if u2:
            reasons.append(r2)
            for i in c2:
                if i not in candidates:
                    candidates.append(i)

    u3, r3, c3 = token_entropy_uncertain(logprobs)
    if u3:
        reasons.append(r3)
        for i in c3:
            if i not in candidates:
                candidates.append(i)

    if not reasons:
        return False, "", []
    return True, " | ".join(reasons), candidates


if __name__ == "__main__":
    # Fabricate three identical 'Apply' buttons — structural should flag.
    elements = [
        {"id": 10, "name": "Cancel", "control_type": "Button"},
        {"id": 14, "name": "Apply", "control_type": "Button"},
        {"id": 15, "name": "Apply", "control_type": "Button"},
        {"id": 22, "name": "Apply", "control_type": "Button"},
        {"id": 30, "name": "Close", "control_type": "Button"},
    ]
    action = {"action": "click", "id": 14, "why": "apply the change"}

    uncertain, reason, cands = structural_ambiguity(action, elements)
    print("=== uncertainty.py self-test (structural_ambiguity) ===")
    print(f"  action: {action}")
    print(f"  uncertain: {uncertain}")
    print(f"  reason:    {reason}")
    print(f"  candidates:{cands}")
    assert uncertain, "expected ambiguity among three Apply buttons"
    assert 14 in cands and 15 in cands and 22 in cands
    assert 10 not in cands and 30 not in cands

    # Unique target should be fine
    ok_action = {"action": "click", "id": 10, "why": "cancel"}
    u2, r2, c2 = structural_ambiguity(ok_action, elements)
    print(f"\n  unique Cancel -> uncertain={u2} (expect False)")
    assert not u2

    # Entropy skip when no logprobs
    u3, _, _ = token_entropy_uncertain(None)
    assert not u3
    # High-entropy flat distribution
    flat = [math.log(0.25)] * 4
    u4, r4, _ = token_entropy_uncertain(flat)
    print(f"  flat logprobs -> uncertain={u4} reason={r4!r}")
    assert u4

    # assess_uncertainty OR-combines without cross-call
    u5, r5, c5 = assess_uncertainty(
        action, elements, reason_fn=None, run_cross_call=False
    )
    print(f"\n  assess_uncertainty -> uncertain={u5} cands={c5}")
    assert u5 and set(c5) >= {14, 15, 22}
    print("\nOK")
