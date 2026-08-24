"""Per-person outreach orchestrator — reuses harness/agent engines + safety gate."""

from __future__ import annotations

import random
import re
import sys
import time
from typing import Any

from email_workflow_automation import config
from email_workflow_automation.apollo import get_email_for_linkedin_profile, mask_email
from email_workflow_automation.browser_util import (
    active_page,
    connect,
    normalize_linkedin_profile_url,
)
from email_workflow_automation.draft import draft_outreach_email
from email_workflow_automation.target import capture_current_profile

from safety_gate import confirm_irreversible_step, harness_step_check, require_irreversible_confirmation

try:
    from harness import _run_reason_substep
except Exception:
    _run_reason_substep = None

try:
    from prereq_reasoner import prepare_for
except Exception:
    prepare_for = None

try:
    from browser_locator import disconnect_browser
except Exception:
    disconnect_browser = None


def _quiet_teardown():
    if disconnect_browser:
        try:
            disconnect_browser()
        except Exception:
            pass


def _cdp_optional() -> bool:
    """Whether this pipeline may use CDP.

    When REQUIRE_DEBUG_CHROME=False (default), always False — outreach runs on
    the user's normal Chrome via vision only. Never attach to / relaunch a
    debug session (that breaks Apollo extension sign-in).
    """
    if not config.REQUIRE_DEBUG_CHROME:
        return False
    return connect()


def _chrome_exe() -> str | None:
    import os

    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    return next((p for p in candidates if os.path.exists(p)), None)


def _focus_existing_chrome(title_hint: str | None = "LinkedIn") -> bool:
    """Bring normal Chrome to the foreground. Falls back to any Chrome window."""
    try:
        from prereq_reasoner import focus_app

        if title_hint and focus_app(["chrome.exe"], title_hint=title_hint):
            return True
        return bool(focus_app(["chrome.exe"]))
    except Exception as e:
        print(f"  [run] focus Chrome failed: {e}")
        return False


def _open_url_in_normal_chrome(
    url: str,
    *,
    title_hint: str | None = None,
    wait: float = 4.0,
) -> bool:
    """Open *url* in the user's NORMAL Chrome profile (no debug flags).

    chrome.exe <url> reuses the signed-in profile (Apollo intact). Never passes
    --remote-debugging-port or --user-data-dir=C:\\chrome-debug.
    """
    import subprocess

    chrome = _chrome_exe()
    try:
        if chrome:
            subprocess.Popen(
                [chrome, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"  [run] launched normal Chrome -> {url}")
        else:
            import webbrowser

            webbrowser.open(url)
            print(f"  [run] launched default browser -> {url}")
    except Exception as e:
        print(f"  [run] failed to open Chrome: {e}")
        return False

    time.sleep(max(1.0, wait))
    focused = _focus_existing_chrome(title_hint)
    if not focused:
        time.sleep(1.5)
        focused = _focus_existing_chrome(title_hint)
    print(f"  [run] Chrome focused={focused} hint={title_hint!r}")
    return True


def _ensure_prereqs():
    """Never require/relaunch debug Chrome for outreach (breaks Apollo).

    Launch/focus the user's NORMAL Chrome only.
    """
    print(
        f"  [run] REQUIRE_DEBUG_CHROME={config.REQUIRE_DEBUG_CHROME} "
        f"— using normal Chrome (vision-first, no debug relaunch)"
    )
    if config.REQUIRE_DEBUG_CHROME:
        if prepare_for:
            try:
                prepare_for(goal="LinkedIn profile outreach and Gmail compose in Chrome")
            except Exception as e:
                print(f"  [run] prereq warning: {e}")
        return

    try:
        from prereq_reasoner import ensure_capability

        ready = ensure_capability("browser")
        if not ready:
            chrome = _chrome_exe()
            if chrome:
                import subprocess

                subprocess.Popen([chrome], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print("  [run] launched normal Chrome (was not detected)")
                time.sleep(2.5)
    except Exception as e:
        print(f"  [run] browser prereq warning: {e}")
        chrome = _chrome_exe()
        if chrome:
            import subprocess

            subprocess.Popen([chrome], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("  [run] launched normal Chrome after prereq error")
            time.sleep(2.5)

    _focus_existing_chrome(None)


def _resolve_to_address(person_email: str | None) -> tuple[str, str]:
    """Return (to_address, mode_note).

    TEST MODE (LIVE_MODE=False): Gmail To is always SAFE_TEST_RECIPIENT.
    LIVE MODE: To is the Apollo-copied / resolved person email.
    Send is still blocked at the tollgate unless LIVE_MODE=True and user types yes.
    """
    if not config.LIVE_MODE:
        safe = (config.SAFE_TEST_RECIPIENT or "").strip()
        if not safe:
            raise ValueError(
                "LIVE_MODE=False but SAFE_TEST_RECIPIENT is empty. "
                "Set EWA_SAFE_TEST_RECIPIENT or email_workflow_automation/.safe_recipient"
            )
        return safe, (
            f"TEST MODE — Gmail To={safe!r} (not {person_email!r}); "
            "send still blocked at tollgate"
        )
    if not person_email or not str(person_email).strip():
        raise ValueError("LIVE_MODE=True but no recipient email resolved")
    addr = str(person_email).strip()
    return addr, f"LIVE MODE — To={addr!r}"


def _paste_email_fallback(hint: str | None = None, reason: str = "") -> str | None:
    print()
    print("=" * 60)
    print("EMAIL FALLBACK — confirm / paste the person's email")
    print("=" * 60)
    if reason:
        print(f"Reason: {reason}")
    if hint:
        print(f"Vision suggested (masked): {mask_email(hint)}")
        print(
            "Press Enter to ACCEPT the suggestion, paste a corrected email, "
            "or type skip: ",
            end="",
            flush=True,
        )
    else:
        print("Apollo did not get a usable email.")
        print("Paste their email address (or press Enter to skip): ", end="", flush=True)
    try:
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if hint and (not raw or raw.lower() in ("y", "yes", "ok", "accept")):
        return hint
    if not raw or raw.lower() in ("s", "skip", "n", "no"):
        return None
    m = re.search(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", raw)
    return m.group(0) if m else raw


def _navigate(url: str, why: str = "") -> bool:
    """Open *url* in Chrome. CDP when required; otherwise normal Chrome + URL."""
    if _cdp_optional():
        from agent_act import do_action

        ok, msg = do_action(
            {"action": "navigate", "url": url, "why": why or f"navigate to {url}"},
            [],
            target_procs=["chrome.exe"],
        )
        print(f"  {'OK' if ok else 'FAIL'} navigate (CDP): {msg}")
        time.sleep(1.5)
        return ok

    hint = None
    u = (url or "").lower()
    if "linkedin" in u:
        hint = "LinkedIn"
    elif "mail.google" in u or "gmail" in u:
        hint = "Gmail"
    print(f"  [run] vision navigate ({why or 'open url'}) — normal Chrome, no CDP")
    return _open_url_in_normal_chrome(url, title_hint=hint, wait=4.0)


def _resolve_person_email() -> tuple[str | None, dict]:
    """Apollo Access + Copy on LinkedIn tab; confirm/paste on miss."""
    if _cdp_optional():
        from email_workflow_automation.browser_util import switch_to_tab

        switch_to_tab("linkedin.com/in/")
        time.sleep(0.3)
    else:
        _focus_existing_chrome("LinkedIn")

    result = get_email_for_linkedin_profile()
    # Never log full email from result blob carelessly — print masked summary
    print(
        f"  [apollo] found={result.get('found')} copied={result.get('copied')} "
        f"source={result.get('source')} "
        f"masked={mask_email(result.get('email') or result.get('raw') or '')} "
        f"note={result.get('note')}"
    )
    if result.get("found") and result.get("email"):
        return result["email"], result
    if result.get("copied") and result.get("email"):
        return result["email"], result

    # Invalid vision read or other failure — ask user; do not use malformed silently
    hint = None
    if result.get("needs_confirm") and (result.get("email") or result.get("raw")):
        hint = result.get("email") or result.get("raw")
    pasted = _paste_email_fallback(
        hint=hint,
        reason=result.get("note") or "email not resolved automatically",
    )
    if pasted:
        return pasted, {
            "found": True,
            "email": pasted,
            "source": "paste",
            "prior": result,
        }
    return None, result


def _gmail_compose_deeplink(to_addr: str, subject: str, body: str) -> str:
    """Gmail compose deep link with To/Subject/Body pre-filled (unsent).

    quote(..., safe='') so spaces are %20 (not +) and newlines are %0A.
    """
    from urllib.parse import quote

    return (
        "https://mail.google.com/mail/u/0/?view=cm&fs=1"
        f"&to={quote(to_addr or '', safe='')}"
        f"&su={quote(subject or '', safe='')}"
        f"&body={quote(body or '', safe='')}"
    )


def _open_gmail_compose_deeplink(
    to_addr: str, subject: str, body: str
) -> tuple[bool, str]:
    """Open the compose deep link in the user's normal Chrome. Never clicks fields."""
    url = _gmail_compose_deeplink(to_addr, subject, body)
    print(
        f"  [gmail] opening compose deep link "
        f"(len={len(url)}, to={to_addr!r}, su_chars={len(subject)}, "
        f"body_chars={len(body)})"
    )
    ok = _open_url_in_normal_chrome(url, title_hint="Gmail", wait=5.0)
    if not ok:
        return False, "failed to open Gmail compose deep link in Chrome"
    _focus_existing_chrome("Gmail")
    time.sleep(2.5)
    return True, f"opened compose deep link (To={to_addr!r}, unsent)"


def _selftest_gmail_compose_url() -> None:
    """Offline round-trip: quote -> parse_qs must match originals exactly."""
    from urllib.parse import parse_qs, urlparse

    to_addr = "you@example.com"
    subject = "Why I'd be a great fit for OCI's engineering team"
    paras = [
        "Hi Jyoti,",
        "",
        "I came across OCI and wanted to reach out directly.",
        "",
        "GitHub: https://github.com/YashDayma55/DebateMind?q=eval&x=1",
        "Portfolio: https://portfolio-brown-two-80.vercel.app",
        "",
        "I'm Yash Dayma, currently completing my M.S. in Computer Science.",
        "",
    ]
    body = "\n".join(paras) + "\n"
    pad = (
        "Every project is deployed and open source. "
        "See https://github.com/YashDayma55 for more.\n"
    )
    while len(body) < 2700:
        body += pad

    url = _gmail_compose_deeplink(to_addr, subject, body)
    qs = parse_qs(urlparse(url).query, keep_blank_values=True)
    got_to = qs.get("to", [""])[0]
    got_su = qs.get("su", [""])[0]
    got_body = qs.get("body", [""])[0]
    body_q = url.split("&body=", 1)[-1]
    assert "%0A" in url, "newlines must encode as %0A"
    assert "%20" in url, "spaces must encode as %20"
    assert "+" not in body_q, "encoded body must not use + for spaces"
    assert got_to == to_addr, (got_to, to_addr)
    assert got_su == subject, (got_su, subject)
    assert got_body == body, (
        f"body mismatch lens {len(got_body)} vs {len(body)}"
    )
    print("GMAIL COMPOSE URL SELFTEST OK")
    print(f"  url_len={len(url)} body_chars={len(body)}")
    print(f"  to={got_to!r}")
    print(f"  su={got_su!r}")
    print(f"  body_newlines={body.count(chr(10))} decoded_newlines={got_body.count(chr(10))}")
    print(f"  body_starts={got_body[:70]!r}")
    print(f"  url_prefix={url[:140]}...")


def _send_tollgate(person: str, to_addr: str, subject: str = "") -> str:
    """Always show irreversible tollgate. Test mode never actually sends."""
    desc = f"Send outreach email to {to_addr} (re: {person})"
    step = harness_step_check(None, action={"action": "click", "why": "send email"}, description=desc)

    print()
    print(f"  [run] recipient: {to_addr}")
    if subject:
        print(f"  [run] subject: {subject}")
    if not config.LIVE_MODE:
        print("  [run] TEST MODE — send is blocked even if you type 'yes'")

    if not confirm_irreversible_step(step):
        print("\n  STOPPED at send tollgate.")
        return "stopped_at_tollgate"

    if not config.LIVE_MODE:
        print("  would send (test mode)")
        return "would_send_test_mode"

    # LIVE_MODE: user typed yes — attempt Send via reason loop (still supervised)
    print("  [run] LIVE MODE — proceeding to Send (watch the screen)")
    if _run_reason_substep is None:
        return "live_no_reason_engine"
    outcome = _run_reason_substep(
        "Click the Send button in Gmail to send this email.",
        require_approval=True,
        max_steps=4,
        prefer_browser=True,
    )
    return f"live_send_{outcome}"


def run_outreach_for_person(profile_url: str) -> dict[str, Any]:
    """Full supervised two-tab chain for one LinkedIn profile URL.

    Flow:
      Tab 1 — LinkedIn profile (stays open)
        1. Navigate to profile
        2. Gather company context (name/title/company)
        3. Apollo: Extensions -> Access email -> Copy (clipboard)
        4. Draft outreach email from knowledge + context
      Tab 2 — Gmail compose
        5. Open compose deep link (to/su/body URL-encoded; unsent)
        6. STOP at send tollgate (never sends in test mode)
    """

    profile_url = normalize_linkedin_profile_url(profile_url)
    if not profile_url or "linkedin.com/in/" not in profile_url.lower():
        return {"ok": False, "error": f"invalid profile URL: {profile_url!r}"}

    print("=" * 60)
    print("EMAIL OUTREACH — two-tab flow (supervised)")
    print(f"  LIVE_MODE={config.LIVE_MODE}")
    print(f"  REQUIRE_DEBUG_CHROME={config.REQUIRE_DEBUG_CHROME}")
    print(f"  profile={profile_url}")
    print("=" * 60)

    try:
        _ensure_prereqs()

        # ── TAB 1: LinkedIn profile ──────────────────────────────
        print("\n  [STEP 1] LinkedIn profile (Tab 1)")
        if not _navigate(profile_url, "open LinkedIn profile"):
            return {"ok": False, "error": "could not open profile URL in Chrome"}
        # Make sure Chrome is frontmost before vision capture / Apollo
        _focus_existing_chrome("LinkedIn")
        time.sleep(1.0)

        captured = capture_current_profile()
        if not captured:
            captured = {
                "person": "(unknown)",
                "profile_url": profile_url,
                "params": {"person": "(unknown)", "profile_url": profile_url},
            }
        print(f"  [target] {captured}")

        # ── STEP 2: Company context (while still on LinkedIn tab) ──
        print("\n  [STEP 2] Gather company context from LinkedIn")
        from email_workflow_automation.company_context import gather_company_context
        from email_workflow_automation.knowledge import load_knowledge

        knowledge = load_knowledge()
        ctx = gather_company_context(profile_url)
        print(f"  [company] name={ctx.get('full_name')!r} company={ctx.get('company')!r} "
              f"needs_company_info={ctx.get('needs_company_info')}")

        # ── STEP 3: Apollo Access + Copy (LinkedIn tab) ──
        print("\n  [STEP 3] Apollo Access email + Copy (LinkedIn tab)")
        person_email, apollo_info = _resolve_person_email()
        if not person_email and config.LIVE_MODE:
            return {"ok": False, "error": "no email for live send", "apollo": apollo_info}

        try:
            to_addr, mode_note = _resolve_to_address(person_email)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        print(f"  [run] {mode_note}")

        # ── STEP 4: Draft the email (no mid-pipeline approval) ──
        print("\n  [STEP 4] Draft outreach email")
        draft = draft_outreach_email(ctx, knowledge)
        print(f"  [draft] projects={draft.get('project_keys')} "
              f"needs_company_info={draft.get('needs_company_info')}")
        print()
        print("=" * 60)
        print("EMAIL DRAFT (auto-continuing — approval only at Send tollgate)")
        print("=" * 60)
        print(f"To (live recipient): {draft.get('person') or captured.get('person')}")
        print(f"Subject: {draft.get('subject', '')}")
        print("-" * 60)
        print(draft.get("body", ""))
        print("-" * 60)
        print("  [run] skipping draft/compose prompts — opening Gmail via deep link")

        # ── TAB 2: Gmail compose deep link (no field clicks, no reason-loop) ──
        print("\n  [STEP 5] Open Gmail compose via deep link (Tab 2)")
        print("  [run] compose URL prefill — To/Subject/Body encoded; unsent")
        ok_fill, fill_msg = _open_gmail_compose_deeplink(
            to_addr, draft["subject"], draft["body"]
        )

        print(f"  [{'OK' if ok_fill else 'WARN'}] compose fill: {fill_msg}")
        if not ok_fill:
            return {"ok": False, "error": "compose fill failed", "fill": fill_msg}

        print("Draft is in the Gmail compose window - review it there.")
        print("  [run] ONLY approval left: Send tollgate below")
        send_outcome = _send_tollgate(
            captured["person"], to_addr, subject=draft.get("subject") or ""
        )

        return {
            "ok": send_outcome in ("would_send_test_mode",) or send_outcome.startswith("live_send_done"),
            "person": captured["person"],
            "profile_url": captured["profile_url"],
            "person_email": person_email,
            "to_address": to_addr,
            "live_mode": config.LIVE_MODE,
            "apollo": apollo_info,
            "draft_subject": draft["subject"],
            "send_outcome": send_outcome,
        }
    finally:
        _quiet_teardown()


def run_one(profile_url: str) -> dict[str, Any]:
    """Clean entry point for a single profile."""
    return run_outreach_for_person(profile_url)


def run_list(profile_urls: list[str]) -> list[dict[str, Any]]:
    """Process URLs one at a time with jitter between runs."""
    results = []
    urls = [normalize_linkedin_profile_url(u) for u in profile_urls if u and str(u).strip()]
    for i, url in enumerate(urls):
        print()
        print("#" * 60)
        print(f"BATCH {i + 1}/{len(urls)}: {url}")
        print("#" * 60)
        results.append(run_outreach_for_person(url))
        if i < len(urls) - 1:
            delay = config.BATCH_DELAY_SEC + random.uniform(1.0, 4.0)
            print(f"  [batch] waiting {delay:.1f}s before next person...")
            time.sleep(delay)
    return results


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg in ("selftest-compose-url", "selftest"):
        _selftest_gmail_compose_url()
        sys.exit(0)
    if not arg:
        print("usage: python -m email_workflow_automation.run <linkedin-profile-url>")
        print("       python -m email_workflow_automation.run selftest-compose-url")
        sys.exit(1)
    print(run_outreach_for_person(arg))
