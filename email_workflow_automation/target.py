"""Capture person + profile URL from the live LinkedIn tab."""

from __future__ import annotations

from email_workflow_automation.browser_util import (
    active_page,
    active_target_http,
    cdp_debug_info,
    connect,
    page_title,
    require_linkedin_profile,
)
from email_workflow_automation import config as ewa_config


def _name_from_title(title: str) -> str:
    if not title:
        return ""
    for suffix in (" | LinkedIn", " - LinkedIn", " on LinkedIn"):
        if suffix in title:
            return title.split(suffix)[0].strip()
    if "LinkedIn" not in title:
        return title.strip()
    return ""


def _extract_name_from_page(page) -> str:
    """Best-effort name from LinkedIn profile DOM."""
    selectors = [
        "h1.text-heading-xlarge",
        "h1.inline.t-24",
        "main h1",
        "h1",
        '[data-anonymize="person-name"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                text = (loc.inner_text(timeout=2000) or "").strip()
                if text and len(text) < 120:
                    return text.split("\n")[0].strip()
        except Exception:
            continue
    return _name_from_title(page_title(page))


def _capture_from_http(target: dict) -> dict:
    url = (target.get("url") or "").strip()
    title = (target.get("title") or "").strip()
    person = _name_from_title(title) or "(unknown name)"
    params = {
        "person": person,
        "profile_url": url,
        "first_name": person.split()[0] if person != "(unknown name)" else person,
    }
    return {"person": person, "profile_url": url, "params": params, "source": "cdp_http"}


def _capture_from_vision() -> dict | None:
    """Vision fallback when debug Chrome / CDP is unavailable."""
    try:
        from email_workflow_automation.apollo import (
            _call_vision_json,
            capture_fullscreen_raw_no_focus,
        )
    except Exception as e:
        print(f"[target] vision import failed: {e}")
        return None

    prompt = (
        "Look at this screenshot. Identify the LinkedIn person currently shown "
        "(profile page and/or Apollo.io contact panel). "
        "Return ONLY JSON: "
        '{"full_name": "...", "profile_url": "https://www.linkedin.com/in/... or empty", '
        '"what_you_see": "..."}. '
        "Use empty strings if unknown. Do not invent a URL."
    )
    try:
        path, _meta = capture_fullscreen_raw_no_focus()
        obj, err = _call_vision_json(path, prompt, max_tokens=250)
        if obj is None:
            print(f"[target] vision miss: {err}")
            return None
        person = str(obj.get("full_name") or "").strip()
        profile_url = str(obj.get("profile_url") or "").strip()
        if not person:
            return None
        if person.startswith("("):
            return None
        params = {
            "person": person,
            "profile_url": profile_url,
            "first_name": person.split()[0] if person else person,
        }
        print(f"[target] captured via vision: {person!r} @ {profile_url or '(url unknown)'}")
        return {
            "person": person,
            "profile_url": profile_url,
            "params": params,
            "source": "vision",
        }
    except Exception as e:
        print(f"[target] vision error: {e}")
        return None


def capture_current_profile() -> dict | None:
    """Read name + profile URL from the active LinkedIn profile tab.

    With REQUIRE_DEBUG_CHROME=False, skip CDP and use vision immediately so we
    never attach to a debug session that would displace the user's Apollo Chrome.
    """
    use_cdp = bool(getattr(ewa_config, "REQUIRE_DEBUG_CHROME", False))
    if use_cdp:
        cdp_ok, _detail = cdp_debug_info()
        if cdp_ok:
            target = active_target_http()
            if target:
                ok, profile_url, note = require_linkedin_profile(target)
                if ok:
                    out = _capture_from_http({**target, "url": profile_url})
                    print(f"[target] captured via CDP HTTP: {out['person']!r} @ {profile_url}")
                    return out
                print(f"[target] HTTP tab found but {note}")

            if connect(timeout=5):
                page = active_page()
                if page is not None:
                    ok, profile_url, note = require_linkedin_profile(page)
                    if ok:
                        person = _extract_name_from_page(page)
                        if not person:
                            person = "(unknown name)"
                        params = {
                            "person": person,
                            "profile_url": profile_url,
                            "first_name": (
                                person.split()[0]
                                if person and person != "(unknown name)"
                                else person
                            ),
                        }
                        return {
                            "person": person,
                            "profile_url": profile_url,
                            "params": params,
                            "source": "playwright",
                        }
                    print(f"[target] {note}")
            else:
                print("[target] could not attach Playwright to debug Chrome")
                return None
        else:
            print(f"[target] debug Chrome required but not available: {_detail}")
            return None

    print("[target] vision-first capture (REQUIRE_DEBUG_CHROME=False)")
    return _capture_from_vision()
