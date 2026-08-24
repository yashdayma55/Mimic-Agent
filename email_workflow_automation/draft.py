"""Draft personalized outreach from the knowledge file + reasoned slots.

The model only writes: subject, opening, project_keys, project_framings, closing.
Identity, experience, project descriptions, links, and signature are assembled
in code VERBATIM from knowledge.yaml.
"""

from __future__ import annotations

import json
import re
import sys
import textwrap
from typing import Any

from email_workflow_automation import config as ewa_config
from email_workflow_automation.knowledge import (
    all_allowed_urls,
    load_knowledge,
    project_by_key,
    project_catalog_for_prompt,
)

try:
    from config import API_MODEL, KEY_FILE
except Exception:
    API_MODEL = "claude-sonnet-4-5"
    KEY_FILE = "my_key.txt"

_URL_RE = re.compile(r"https?://[^\s)|,\]]+", re.I)


def _load_key() -> str:
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _call_json_model(prompt: str, *, max_tokens: int = 900) -> dict:
    import requests

    key = _load_key()
    if not key or not str(key).startswith("sk-ant"):
        raise RuntimeError("no Claude API key")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": API_MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90,
    )
    try:
        resp.raise_for_status()
    except Exception as e:
        raw = ""
        try:
            raw = resp.text or ""
        except Exception:
            raw = "<no response text>"
        raise RuntimeError(
            f"Claude API error {resp.status_code}: {raw[:1500]}"
        ) from e
    text = resp.json()["content"][0]["text"]
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("model returned no JSON object")
    return json.loads(text[start:end])


def _neutral_opening(first_name: str, company: str) -> str:
    co = company.strip() if company else "your team"
    return (
        f"I came across {co} and wanted to reach out directly. "
        "I would like to learn more about the problems you are working on "
        "and whether my background in backend and AI systems could help."
    )


def _slots_prompt(context: dict, knowledge: dict) -> str:
    ident = knowledge["identity"]
    exp = knowledge["experience"]["paragraph"]
    catalog = project_catalog_for_prompt(knowledge)
    company = context.get("company") or ""
    summary = (context.get("company_summary") or "").strip()
    needs = bool(context.get("needs_company_info"))
    first = context.get("first_name") or "there"
    title = context.get("title") or ""
    full = context.get("full_name") or ""

    company_block = summary if summary else "(NO RELIABLE COMPANY SUMMARY — do not invent product/mission facts)"
    opening_rule = (
        "OPENING: 2-4 sentences showing genuine understanding of THIS company's "
        "product/mission and why it connects to the user's work. Use ONLY the "
        "company_summary below. Do not invent products, customers, or metrics."
        if not needs
        else
        "OPENING: company_summary is missing. Write a NEUTRAL 2-sentence opening "
        "that does NOT assert what the company builds. No invented products."
    )

    return f"""You are filling ONLY the generated slots of a job-outreach email.
You must NOT rewrite identity, experience, project descriptions, URLs, or the signature.
Those are inserted verbatim in code.

Recipient: {full} (first={first})
Title: {title}
Company: {company}
company_summary:
{company_block}

VERBATIM identity (do not echo in JSON, just know it exists):
{ident.get('identity_line')}

VERBATIM experience (do not echo in JSON):
{exp}

PROJECT CATALOG (select 3 or 4 keys; do NOT rewrite descriptions):
{catalog}

{opening_rule}

FRAMINGS: for each selected project, ONE short clause tying that project's FACTS
to this company. Do not add new metrics, tech, or URLs.

CLOSING: a short honest paragraph about fit/ask. End with the sentence
"Resume attached." (exactly). Do not include the signature.

SUBJECT: in this style: "Why I think I'd be a great fit for {company or "the"} engineering team"
(adapt company name; keep it professional, no clickbait).

Return ONLY JSON:
{{
  "subject": "...",
  "opening": "...",
  "project_keys": ["debatemind", "medledger"],
  "project_framings": {{"debatemind": "one short clause", "medledger": "one short clause"}},
  "closing": "...",
  "why_projects": "one sentence why these projects"
}}
"""


def _validate_slots(slots: dict, knowledge: dict, context: dict) -> dict:
    if not isinstance(slots, dict):
        raise ValueError("slots is not a dict")
    subject = str(slots.get("subject") or "").strip()
    opening = str(slots.get("opening") or "").strip()
    closing = str(slots.get("closing") or "").strip()
    keys = slots.get("project_keys") or []
    framings = slots.get("project_framings") or {}
    if not subject:
        raise ValueError("missing subject")
    if not opening:
        raise ValueError("missing opening")
    if not closing:
        raise ValueError("missing closing")
    if not isinstance(keys, list):
        raise ValueError("project_keys must be a list")
    keys = [str(k).strip().lower() for k in keys if str(k).strip()]
    # unique, valid, 3-4
    seen = []
    for k in keys:
        if k in seen:
            continue
        if project_by_key(knowledge, k) is None:
            raise ValueError(f"unknown project_key: {k}")
        seen.append(k)
    if len(seen) < 3 or len(seen) > 4:
        raise ValueError(f"need 3-4 project_keys, got {len(seen)}: {seen}")
    if not isinstance(framings, dict):
        raise ValueError("project_framings must be a dict")
    clean_fr: dict[str, str] = {}
    for k in seen:
        clause = str(framings.get(k) or framings.get(k.title()) or "").strip()
        # framing is optional-but-preferred; empty is allowed
        if _URL_RE.search(clause):
            raise ValueError(f"framing for {k} contains a URL")
        clean_fr[k] = clause
    if context.get("needs_company_info"):
        opening = _neutral_opening(
            context.get("first_name") or "there",
            context.get("company") or "",
        )
    # Model sometimes echoes the greeting — strip it; code owns "Hi <First>,"
    first = context.get("first_name") or "there"
    opening = re.sub(
        rf"^(?:Hi|Hello|Hey)\s+{re.escape(first)}\s*,?\s*",
        "",
        opening,
        flags=re.I,
    ).strip()
    if "Resume attached." not in closing:
        closing = closing.rstrip(".") + " Resume attached."
    return {
        "subject": subject,
        "opening": opening,
        "project_keys": seen,
        "project_framings": clean_fr,
        "closing": closing,
        "why_projects": str(slots.get("why_projects") or "").strip(),
    }


def _weave(description: str, framing: str) -> str:
    desc = (description or "").strip()
    fr = (framing or "").strip().rstrip(".")
    if not fr:
        return desc
    if desc.endswith("."):
        return f"{desc[:-1]} — {fr}."
    return f"{desc} — {fr}."


def assemble_email(context: dict, knowledge: dict, slots: dict) -> tuple[str, str]:
    """Deterministic assembly. Raises if a guardrail fails."""
    first = context.get("first_name") or "there"
    greeting = f"Hi {first},"
    identity = (knowledge["identity"].get("identity_line") or "").strip()
    experience = (knowledge["experience"].get("paragraph") or "").strip()
    preamble = (knowledge.get("projects_preamble") or "").strip()
    epilogue = (knowledge.get("projects_epilogue") or "").strip()
    signature = (knowledge.get("signature") or "").strip()
    links = knowledge.get("links") or {}
    link_block = (
        f"GitHub: {links.get('github')}\n"
        f"Portfolio: {links.get('portfolio')}"
    )

    numbered = []
    for i, key in enumerate(slots["project_keys"], start=1):
        proj = project_by_key(knowledge, key)
        if not proj:
            raise ValueError(f"assemble: missing project {key}")
        desc = (proj.get("one_paragraph_description") or "").strip()
        line = _weave(desc, slots["project_framings"].get(key, ""))
        numbered.append(f"{i}. {line}")

    parts = [
        greeting,
        "",
        slots["opening"].strip(),
        "",
        identity,
        "",
        experience,
        "",
        preamble,
        "",
        "\n\n".join(numbered),
    ]
    if epilogue:
        parts.extend(["", epilogue])
    parts.extend(["", link_block, "", slots["closing"].strip(), "", signature])
    body = "\n".join(parts).strip() + "\n"
    _assert_guardrails(body, knowledge, identity, experience, signature)
    return slots["subject"].strip(), body


def _assert_guardrails(
    body: str,
    knowledge: dict,
    identity: str,
    experience: str,
    signature: str,
) -> None:
    if identity not in body:
        raise ValueError("guardrail: identity_line missing from assembled body")
    if experience not in body:
        raise ValueError("guardrail: experience paragraph missing from assembled body")
    if signature not in body:
        raise ValueError("guardrail: signature missing from assembled body")
    allowed = all_allowed_urls(knowledge)
    found = {m.rstrip(").,;") for m in _URL_RE.findall(body)}
    extra = sorted(u for u in found if u not in allowed)
    if extra:
        raise ValueError(f"guardrail: invented URL(s): {extra}")


def _is_credit_low_error(e: Exception) -> bool:
    s = str(e).lower()
    return "credit balance is too low" in s or "plans & billing" in s


def _draft_without_llm(context: dict, knowledge: dict) -> dict:
    company = (context.get("company") or "").strip()
    first = context.get("first_name") or "there"
    needs = bool(context.get("needs_company_info"))

    if company:
        subject = f"Why I think I'd be a great fit for {company}'s engineering team"
    else:
        subject = "Why I think I'd be a great fit for the engineering team"

    if needs:
        opening = _neutral_opening(first, company)
    else:
        summary = (context.get("company_summary") or "").strip()
        if not summary:
            opening = _neutral_opening(first, company)
        else:
            sentences = re.split(r"(?<=[.!?])\s+", summary)
            picked = " ".join([x for x in sentences[:2] if x]).strip()
            if not picked:
                opening = _neutral_opening(first, company)
            else:
                co = company if company else "your team"
                opening = (
                    f"I came across {co} and wanted to reach out directly. "
                    f"{picked} "
                    "I build production backend and AI systems and would love to help apply that mindset to real user workflows."
                )

    keys: list[str] = ["debatemind", "flowlog", "mimicagent"]
    summary_l = (context.get("company_summary") or "").lower()
    if any(w in summary_l for w in ("health", "medical", "patient", "clinic", "ehr")):
        keys.append("medledger")

    # Ensure 3-4 unique keys
    seen = set()
    project_keys: list[str] = []
    for k in keys:
        if k not in seen and project_by_key(knowledge, k):
            seen.add(k)
            project_keys.append(k)
    if len(project_keys) < 3:
        for k in ("medledger", "flowlog", "mimicagent", "debatemind"):
            if len(project_keys) >= 3:
                break
            if k not in seen and project_by_key(knowledge, k):
                seen.add(k)
                project_keys.append(k)
    if len(project_keys) > 4:
        project_keys = project_keys[:4]

    default_framings = {
        "debatemind": "production multi-agent orchestration with structured evaluation for reliable workflows",
        "medledger": "LLM-driven structured record extraction designed for regulated, high-stakes data",
        "mimicagent": "desktop UI automation with human approval and correction memory",
        "flowlog": "distributed pipeline reliability for high-throughput, real-time decision systems",
    }
    project_framings = {k: default_framings.get(k, "") for k in project_keys}

    why_projects = (
        "These projects reflect production-grade backend, distributed systems, and AI that stays reliable under real-world constraints."
    )
    closing = (
        f"I’d welcome the chance to discuss how I can contribute to {company or 'your team'} "
        "across backend, data pipelines, and agentic workflows. Resume attached."
    )

    return {
        "subject": subject,
        "opening": opening,
        "project_keys": project_keys,
        "project_framings": project_framings,
        "closing": closing,
        "why_projects": why_projects,
    }


def _draft_from_knowledge(context: dict, knowledge: dict | None = None) -> dict:
    knowledge = knowledge or load_knowledge()
    context = dict(context or {})
    if context.get("needs_company_info") or not (context.get("company_summary") or "").strip():
        context["needs_company_info"] = True

    last_err = None
    slots = None
    for attempt in (1, 2):
        try:
            raw = _call_json_model(_slots_prompt(context, knowledge))
            slots = _validate_slots(raw, knowledge, context)
            subject, body = assemble_email(context, knowledge, slots)
            return {
                "subject": subject,
                "body": body,
                "person": context.get("full_name") or "",
                "profile_url": context.get("profile_url") or "",
                "project_keys": slots["project_keys"],
                "project_framings": slots["project_framings"],
                "why_projects": slots.get("why_projects") or "",
                "needs_company_info": bool(context.get("needs_company_info")),
                "slots": slots,
            }
        except Exception as e:
            if attempt == 1 and _is_credit_low_error(e):
                print("  [draft] Claude credits too low — using deterministic fallback (no model call)")
                slots_raw = _draft_without_llm(context, knowledge)
                slots = _validate_slots(slots_raw, knowledge, context)
                subject, body = assemble_email(context, knowledge, slots)
                return {
                    "subject": subject,
                    "body": body,
                    "person": context.get("full_name") or "",
                    "profile_url": context.get("profile_url") or "",
                    "project_keys": slots["project_keys"],
                    "project_framings": slots["project_framings"],
                    "why_projects": slots.get("why_projects") or "",
                    "needs_company_info": bool(context.get("needs_company_info")),
                    "slots": slots,
                }
            last_err = e
            print(f"  [draft] attempt {attempt} failed: {e}")
    raise RuntimeError(f"draft failed after retry: {last_err}")


# ---------------------------------------------------------------------------
# Legacy short template (kept for old callers)
# ---------------------------------------------------------------------------

def _template_draft(person: str, profile_url: str) -> tuple[str, str]:
    first = person.split()[0] if person and person != "(unknown name)" else "there"
    subject = ewa_config.DEFAULT_SUBJECT.format(person=person)
    body = textwrap.dedent(
        f"""\
        Hi {first},

        I came across your profile on LinkedIn ({profile_url}) and wanted to reach out briefly.

        [Add 1-2 sentences on why you're reaching out and what you admire about their work.]

        Would you be open to a short conversation?

        Best,
        {ewa_config.DEFAULT_SENDER_NAME}
        """
    ).strip()
    return subject, body


def draft_outreach_email(
    context: Any = None,
    knowledge: dict | None = None,
    extra_context: str | None = None,
    use_llm: bool = True,
    profile_url: str | None = None,
) -> dict:
    """New: draft_outreach_email(context_dict, knowledge).
    Legacy: draft_outreach_email(person_str, knowledge=None) with profile_url=.
    """
    if isinstance(context, dict):
        return _draft_from_knowledge(context, knowledge)

    person = context if isinstance(context, str) else ""
    # Second positional used to be profile_url when knowledge was omitted
    if knowledge is not None and not isinstance(knowledge, dict):
        # called as draft_outreach_email(person, profile_url)
        profile_url = str(knowledge)
        knowledge = None
        return _legacy_draft(person, profile_url or "", extra_context, use_llm)

    if profile_url:
        return _legacy_draft(person, profile_url, extra_context, use_llm)

    if knowledge is None:
        knowledge = load_knowledge()
    ctx = {
        "first_name": (person.split()[0] if person else "there"),
        "full_name": person,
        "title": "",
        "company": "",
        "company_summary": extra_context or "",
        "needs_company_info": not bool(extra_context),
        "profile_url": profile_url or "",
    }
    return _draft_from_knowledge(ctx, knowledge)


def _legacy_draft(person: str, profile_url: str, extra_context: str | None, use_llm: bool) -> dict:
    subject, body = _template_draft(person, profile_url)
    return {"subject": subject, "body": body, "person": person, "profile_url": profile_url}


def approve_draft(draft: dict) -> dict | None:
    """Show draft; user approves (y), edits (e), or cancels."""
    print()
    print("=" * 60)
    print("EMAIL DRAFT — review before typing into Gmail")
    print("=" * 60)
    print(f"To (live recipient): {draft.get('person', '?')}")
    print(f"Subject: {draft.get('subject', '')}")
    print("-" * 60)
    print(draft.get("body", ""))
    print("-" * 60)
    print("Approve this draft? (y = yes / e = edit lines / n = cancel): ", end="", flush=True)
    try:
        ans = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    if ans in ("n", "no", "cancel", "c"):
        return None
    if ans in ("e", "edit"):
        print("Paste revised body (end with a blank line, then Ctrl-Z Enter on Windows):")
        lines = []
        try:
            while True:
                line = input()
                lines.append(line)
        except EOFError:
            pass
        if lines:
            draft = dict(draft)
            draft["body"] = "\n".join(lines).strip()
        return draft
    if ans in ("y", "yes", ""):
        return draft
    print("  (unrecognized — treating as cancel)")
    return None


def _selftest_task1() -> None:
    print("=" * 60)
    print("SELF-TEST TASK 1 — knowledge file")
    print("=" * 60)
    k = load_knowledge()
    projects = k["projects"]
    print(f"projects: {len(projects)}")
    for p in projects:
        links = p.get("links") or {}
        desc = (p.get("one_paragraph_description") or "")[:80]
        print(f"  - {p.get('key')}: desc_chars={len(p.get('one_paragraph_description') or '')} "
              f"links={list(links)} preview={desc!r}...")
        assert (p.get("one_paragraph_description") or "").strip()
        assert links
    print()
    print("--- identity_line ---")
    print(k["identity"]["identity_line"])
    print()
    print("--- signature ---")
    print(k["signature"])
    print()
    print("--- experience ---")
    print(k["experience"]["paragraph"])
    print()
    urls = all_allowed_urls(k)
    print(f"allowed URLs ({len(urls)}):")
    for u in sorted(urls):
        print(f"  {u}")
    print("\nSTOP after Task 1. Verify wording is exactly yours.")


def _selftest_task2() -> None:
    print("=" * 60)
    print("SELF-TEST TASK 2 — gather_company_context (live profile)")
    print("=" * 60)
    from email_workflow_automation.company_context import gather_company_context

    ctx = gather_company_context()
    print()
    print(f"full_name: {ctx.get('full_name')!r}")
    print(f"first_name: {ctx.get('first_name')!r}")
    print(f"title: {ctx.get('title')!r}")
    print(f"company: {ctx.get('company')!r}")
    print(f"needs_company_info: {ctx.get('needs_company_info')}")
    print(f"summary_source: {ctx.get('summary_source')!r}")
    print(f"person_sources: {ctx.get('person_sources')}")
    if not ctx.get("full_name") and not ctx.get("company"):
        print()
        print("No LinkedIn profile on screen — cannot invent name/title/company.")
        print("Bring a LinkedIn /in/ profile (+ Apollo panel if possible) to the")
        print("foreground and re-run: python -u -m email_workflow_automation.draft task2")
    summary = ctx.get("company_summary") or ""
    print("--- company_summary (first 300 chars) ---")
    print(summary[:300] or "(empty)")
    print("\nSTOP after Task 2.")


def _selftest_task3() -> None:
    print("=" * 60)
    print("SELF-TEST TASK 3 — draft_outreach_email (print FULL email)")
    print("=" * 60)
    from email_workflow_automation.company_context import gather_company_context

    knowledge = load_knowledge()
    ctx = gather_company_context()
    if ctx.get("needs_company_info"):
        print("  [draft] needs_company_info=True — opening will be neutral (no invented company facts)")
    draft = draft_outreach_email(ctx, knowledge)
    print()
    print(f"projects chosen: {draft.get('project_keys')}")
    print(f"why: {draft.get('why_projects')}")
    print(f"framings: {draft.get('project_framings')}")
    print()
    print("=" * 60)
    print(f"Subject: {draft.get('subject')}")
    print("=" * 60)
    print(draft.get("body"))
    print("=" * 60)
    print("\nSTOP after Task 3. Read the full email above.")


def _selftest_task4() -> None:
    print("=" * 60)
    print("SELF-TEST TASK 4 — two-tab flow: Apollo email → Gmail compose → tollgate")
    print("=" * 60)
    from email_workflow_automation.apollo import mask_email
    from email_workflow_automation.browser_util import switch_to_tab
    from email_workflow_automation.company_context import gather_company_context
    from email_workflow_automation.run import (
        _open_gmail_compose_deeplink,
        _resolve_person_email,
        _resolve_to_address,
        _send_tollgate,
    )

    print(f"  LIVE_MODE={ewa_config.LIVE_MODE} (must stay False for this test)")

    # ── TAB 1: LinkedIn profile (must already be open) ──
    print("\n  [TAB 1] LinkedIn profile — gathering company context")
    switch_to_tab("linkedin.com/in/")
    import time as _time
    _time.sleep(0.5)

    knowledge = load_knowledge()
    ctx = gather_company_context()

    # ── Apollo email extraction on LinkedIn tab ──
    print("\n  [TAB 1] Running Apollo email extraction")
    person_email, apollo_info = _resolve_person_email()
    print(
        f"  [apollo] found={bool(person_email)} "
        f"masked={mask_email(person_email or '')}"
    )
    to_addr, mode_note = _resolve_to_address(person_email)
    print(f"  [run] {mode_note}")

    # ── Draft the email ──
    print("\n  [DRAFT] Generating outreach email")
    draft = draft_outreach_email(ctx, knowledge)
    print(f"Subject: {draft['subject']}")
    print(f"projects: {draft.get('project_keys')}")

    # ── TAB 2: Gmail compose deep link (no field clicks) ──
    print("\n  [TAB 2] Opening Gmail compose via deep link")
    ok_fill, fill_msg = _open_gmail_compose_deeplink(
        to_addr, draft["subject"], draft["body"]
    )
    print(f"  [{'OK' if ok_fill else 'WARN'}] compose fill: {fill_msg}")

    print("Draft is in the Gmail compose window - review it there.")
    send_outcome = _send_tollgate(
        ctx.get("full_name") or "recipient",
        to_addr,
        subject=draft.get("subject") or "",
    )
    print(f"  send_outcome={send_outcome}")
    print("\nSTOP after Task 4.")


if __name__ == "__main__":
    task = (sys.argv[1] if len(sys.argv) > 1 else "task1").strip().lower()
    if task in ("task1", "k1", "1"):
        _selftest_task1()
    elif task in ("task2", "k2", "2"):
        _selftest_task2()
    elif task in ("task3", "k3", "3"):
        _selftest_task3()
    elif task in ("task4", "k4", "4"):
        _selftest_task4()
    else:
        print("Unknown task. Use: task1, task2, task3, task4")
        sys.exit(1)
