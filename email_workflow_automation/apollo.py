"""Apollo extension email extraction — DOM first, then layered vision read.

HONEST NOTE: DOM is best (exact text) when Apollo injects into the page, but the
extension popup context is often NOT reachable from Playwright — then vision-crop
reads the on-screen reveal. Full-screen vision misreads emails cut off at screen
edges; cropping around the Access-email click fixes that. Scroll is last resort
before asking the user to confirm/paste.
"""

from __future__ import annotations

import re
import time

try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

from email_workflow_automation.browser_util import (
    active_page,
    all_pages,
    connect,
    last_connect_error,
    page_url,
)

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)
# Spec format check for vision reads
_EMAIL_FORMAT_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_JUNK_EMAILS = {
    "noreply@linkedin.com",
    "support@linkedin.com",
    "help@linkedin.com",
    "no-reply@apollo.io",
    "support@apollo.io",
}


def _is_noise_email(email: str) -> bool:
    """True for our own inbox, safe test recipient, or known junk — not a prospect."""
    e = (email or "").strip().lower()
    if not e or e in _JUNK_EMAILS:
        return True
    try:
        from email_workflow_automation import config as ewa_config

        safe = (ewa_config.SAFE_TEST_RECIPIENT or "").strip().lower()
        if safe and e == safe:
            return True
    except Exception:
        pass
    try:
        from email_workflow_automation.knowledge import load_knowledge

        own = (load_knowledge().get("identity", {}).get("email") or "").strip().lower()
        if own and e == own:
            return True
    except Exception:
        pass
    return False


def _focus_linkedin_tab() -> bool:
    """Bring LinkedIn to the foreground for Apollo vision clicks.

    Always use OS window focus on the user's normal Chrome. Never require CDP.
    """
    ok = False
    try:
        from prereq_reasoner import focus_app

        focused = focus_app(["chrome.exe"], title_hint="LinkedIn")
        time.sleep(0.4)
        ok = bool(focused)
    except Exception:
        pass
    return ok

# Full-screen marked capture (toolbar must be in-frame — not page-only CDP shot)
_FULLSCREEN_MARKED_PATH = "email_workflow_automation_fullscreen_marked.png"
_FULLSCREEN_RAW_PATH = "email_workflow_automation_fullscreen_raw.png"
_NOFOCUS_MARKED_PATH = "email_workflow_automation_nofocus_marked.png"
# Saved crops for eyeballing panel targeting (Task 1-3)
_PANEL_REGION_PATH = "email_workflow_automation_apollo_panel_region.png"
_PANEL_MARKED_PATH = "email_workflow_automation_apollo_panel_marked.png"
_VERIFY_CROP_PATH = "email_workflow_automation_apollo_access_verify.png"
_TILE_DIR = "email_workflow_automation_tiles"
_TILE_VERIFY_PATH = "email_workflow_automation_tile_verify.png"
_REVEAL_DETECT_CROP_PATH = "email_workflow_automation_apollo_reveal_detect.png"
_COPY_HOVER_PATH = "email_workflow_automation_apollo_copy_hover.png"
_COPY_VERIFY_PATH = "email_workflow_automation_apollo_copy_verify.png"
# Higher cap so Chrome toolbar icons are less likely to be truncated mid-tree
_FULLSCREEN_MAX_ELEMS = 120

# Capture counter — every click_by_vision call takes a NEW screenshot (never reuse).
_CAPTURE_SEQ = 0

# HONEST TRADEOFF (coordinate vision):
# Vision models are less precise at raw (x,y) than at picking numbered SoM boxes —
# that is why Set-of-Mark exists. Use SoM FIRST for tree-visible elements; only fall
# back to coordinate vision when SoM finds nothing (e.g. Chrome Extensions dropdown,
# which is NOT in the accessibility tree). Coordinate vision works best on LARGE,
# distinct targets where small errors still land; it may misclick on tiny/dense UI.
# Best-effort fallback, not a precise primary.
#
# HONEST NOTE (re-scan): Re-capturing after each click fixes STALENESS (the dropdown
# will be in the new image). It does NOT by itself guarantee the model can pinpoint
# Apollo — the dropdown still isn't in the accessibility tree, so we still rely on
# coordinate vision. Fresh capture is necessary; coordinate accuracy on that fresh
# dropdown is what we're really testing.
#
# HONEST NOTE (focus-free / popup): Chrome extension popups auto-close the instant
# they lose focus. Calling focus_app / SetForegroundWindow before capture DISMISSES
# the Apollo popup. capture_fullscreen_no_focus() grabs pixels as-is with NO refocus.
# If the popup STILL closes (Chrome dismisses on any outside interaction), Option A
# is not sufficient — fall back to Apollo's SIDE PANEL (docked, does not auto-close)
# or chrome-extension://<id>/popup.html. Log clearly so we can tell.


def mask_email(email: str) -> str:
    """Mask for logs: r***@company.com"""
    if not email or "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def _clean_email(raw: str) -> str | None:
    if not raw:
        return None
    m = _EMAIL_RE.search(raw.strip())
    if not m:
        return None
    email = m.group(0).lower()
    if email in _JUNK_EMAILS:
        return None
    if email.endswith("@linkedin.com") or email.endswith("@apollo.io"):
        return None
    return email


def _score_email(email: str, context: str) -> int:
    score = 0
    ctx = (context or "").lower()
    if "apollo" in ctx:
        score += 3
    if "mailto" in ctx:
        score += 3
    if "email" in ctx:
        score += 2
    if "copy" in ctx:
        score += 1
    if "business" in ctx or "work" in ctx:
        score += 1
    return score


def _collect_emails_from_page(page) -> list[tuple[str, str, int]]:
    found: list[tuple[str, str, int]] = []
    try:
        hits = page.evaluate(
            """() => {
                const RE = /\\b[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,}\\b/g;
                const out = [];
                const seen = new Set();
                const add = (email, ctx) => {
                    email = (email || '').toLowerCase().trim();
                    if (!email || seen.has(email)) return;
                    seen.add(email);
                    out.push({email, ctx: (ctx || '').slice(0, 160)});
                };
                const harvestText = (text, ctx) => {
                    if (!text) return;
                    RE.lastIndex = 0;
                    let m;
                    while ((m = RE.exec(text)) !== null) add(m[0], ctx);
                };

                // 1. mailto: links (exact href)
                document.querySelectorAll("a[href^='mailto:']").forEach((a) => {
                    const href = a.getAttribute('href') || '';
                    const email = href.replace(/^mailto:/i, '').split('?')[0];
                    add(email, 'mailto ' + (a.innerText || a.textContent || '').slice(0, 80));
                });

                // 2. Apollo panel containers (+ one-level shadow roots)
                const apolloSel =
                    '[class*="apollo" i], [id*="apollo" i], [data-apollo], ' +
                    '[aria-label*="apollo" i], [class*="zp-" i]';
                document.querySelectorAll(apolloSel).forEach((el) => {
                    const text = (el.innerText || el.textContent || '').slice(0, 6000);
                    harvestText(text, 'apollo-container ' + text.slice(0, 80));
                    const root = el.shadowRoot;
                    if (root) {
                        harvestText(
                            (root.textContent || '').slice(0, 6000),
                            'apollo-shadow ' + (root.textContent || '').slice(0, 80)
                        );
                        root.querySelectorAll("a[href^='mailto:']").forEach((a) => {
                            const href = a.getAttribute('href') || '';
                            add(
                                href.replace(/^mailto:/i, '').split('?')[0],
                                'apollo-shadow-mailto'
                            );
                        });
                    }
                });

                // 3. Visible text nodes (page + typical panel)
                const nodes = document.querySelectorAll(
                    'a, button, span, div, p, li, td, input, [data-test-id]'
                );
                for (const el of nodes) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 1 || rect.height < 1) continue;
                    if (rect.bottom < 0 || rect.top > innerHeight) continue;
                    const text = (el.innerText || el.textContent || el.value || '').trim();
                    if (!text || text.length > 500) continue;
                    const cls = (el.className || '').toString();
                    const id = el.id || '';
                    const aria = el.getAttribute('aria-label') || '';
                    harvestText(
                        text,
                        [text.slice(0, 120), cls.slice(0, 80), id, aria].join(' ')
                    );
                }
                return out;
            }"""
        )
    except Exception as e:
        print(f"  [apollo-dom] evaluate failed: {type(e).__name__}: {e}")
        return found

    for item in hits or []:
        email = _clean_email(item.get("email", ""))
        if not email:
            continue
        ctx = item.get("ctx") or ""
        found.append((email, ctx, _score_email(email, ctx)))

    best: dict[str, tuple[str, str, int]] = {}
    for email, ctx, score in found:
        prev = best.get(email)
        if prev is None or score > prev[2]:
            best[email] = (email, ctx, score)
    return list(best.values())


def _try_open_apollo_ui(page) -> None:
    selectors = [
        '[aria-label*="Apollo" i]',
        '[title*="Apollo" i]',
        'button:has-text("Apollo")',
        'a:has-text("Apollo")',
        '[class*="apollo" i]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=1500)
                time.sleep(1.2)
                return
        except Exception:
            continue


def _frame_label(frame, prefix: str, index: int) -> str:
    try:
        name = (frame.name or "").strip()
        url = (frame.url or "").strip()
    except Exception:
        name, url = "", ""
    return (name or url or f"{prefix}_{index}")[:80]


def _dom_targets() -> list[tuple[str, object]]:
    """Main page + iframes, then other Chrome pages (side panel / extension)."""
    frames: list[tuple[str, object]] = []
    seen: set[int] = set()

    def add_page(page, prefix: str) -> None:
        if page is None:
            return
        pid = id(page)
        if pid in seen:
            return
        seen.add(pid)
        try:
            frames.append((f"{prefix}:main", page.main_frame))
            for i, frame in enumerate(page.frames):
                if frame == page.main_frame:
                    continue
                frames.append((_frame_label(frame, prefix, i), frame))
        except Exception as e:
            print(f"  [apollo-dom] skip {prefix}: {e}")

    page = active_page()
    add_page(page, "active")

    for i, other in enumerate(all_pages()):
        url = page_url(other).lower()
        if "apollo" in url or url.startswith("chrome-extension://"):
            prefix = f"ext_{i}"
        else:
            prefix = f"page_{i}"
        add_page(other, prefix)
    return frames


def read_email_from_dom() -> dict:
    """Read a revealed email from page DOM + child frames (no clicks).

    HONEST NOTE: Apollo may inject into the page DOM, but the extension popup
    context is often NOT reachable from Playwright — then this returns found:False
    and the vision-crop layer reads the on-screen reveal instead.
    """
    if not connect():
        err = last_connect_error() or "Playwright attach failed"
        print(f"  [apollo-dom] attach failed: {err}")
        return {
            "found": False,
            "email": None,
            "note": err,
            "source": "dom",
        }

    candidates: list[tuple[str, str, int, str]] = []
    targets = _dom_targets()
    if not targets:
        return {
            "found": False,
            "email": None,
            "note": "no active page",
            "source": "dom",
        }

    for frame_label, frame in targets:
        try:
            extra = 0
            url = ""
            try:
                url = (frame.url or "").lower()
            except Exception:
                url = ""
            if "apollo" in url or url.startswith("chrome-extension://"):
                extra = 5
            for email, ctx, score in _collect_emails_from_page(frame):
                candidates.append((email, ctx, score + extra, frame_label))
        except Exception as e:
            print(f"  [apollo-dom] frame {frame_label!r} scrape error: {e}")

    if not candidates:
        print("  [apollo-dom] found:False — no email in page/frames/extension DOM")
        return {
            "found": False,
            "email": None,
            "note": "no email in page DOM, child frames, or extension pages",
            "source": "dom",
        }

    candidates.sort(key=lambda x: x[2], reverse=True)
    best_email, ctx, score, frame_label = candidates[0]

    if not _EMAIL_FORMAT_RE.match(best_email):
        return {
            "found": False,
            "email": best_email,
            "raw": best_email,
            "note": "DOM text matched but failed format check",
            "source": "dom",
            "format_ok": False,
        }

    print(
        f"  [apollo-dom] found masked={mask_email(best_email)} "
        f"frame={frame_label!r} score={score}"
    )
    return {
        "found": True,
        "email": best_email,
        "source": "dom",
        "context": ctx[:80],
        "frame": frame_label,
        "note": f"dom email {mask_email(best_email)}",
        "format_ok": True,
    }


# ---------------------------------------------------------------------------
# Full-screen vision path (mss + SoM; toolbar in frame)
# ---------------------------------------------------------------------------

def _focus_chrome() -> bool:
    """Bring Chrome frontmost so the accessibility tree + screenshot match."""
    try:
        from prereq_reasoner import focus_app
        ok = focus_app(["chrome.exe"], title_hint="LinkedIn")
        if not ok:
            ok = focus_app(["chrome.exe"])
        time.sleep(0.4)
        return bool(ok)
    except Exception as e:
        print(f"  [apollo-vision] focus_app failed: {e}")
        return False


def capture_fullscreen_marked(save_path: str = _FULLSCREEN_MARKED_PATH):
    """mss full-screen grab + Set-of-Mark marks (including Chrome toolbar).

    Uses set_of_mark.grab_full_screen / collect_clickable_elements / draw_marks
    and som_redact — does NOT use the page-only browser CDP screenshot, so the
    Extensions puzzle-piece can appear in-frame and get numbered.

    Returns (elements, marked_png_path).
    """
    # Reuse existing SoM modules; do not modify set_of_mark DPI logic.
    from set_of_mark import collect_clickable_elements, grab_full_screen, draw_marks

    focused = _focus_chrome()
    print(f"  [apollo-vision] chrome frontmost={focused} before fullscreen capture")

    # Accessibility tree of the frontmost window (Chrome when focused)
    elements = collect_clickable_elements(max_elems=_FULLSCREEN_MAX_ELEMS)
    # mss virtual desktop — same DPI-aware path as Stage A
    img, ox, oy, scale = grab_full_screen()
    for el in elements:
        el["sx"] = int((el["cx"] - ox) * scale)
        el["sy"] = int((el["cy"] - oy) * scale)

    try:
        from som_redact import redact_image
        img = redact_image(img, elements, ox, oy)
    except Exception as e:
        print(f"  [apollo-vision] redact skipped: {e}")

    annotated = draw_marks(img, elements, ox, oy, scale)
    annotated.save(save_path)
    print(
        f"  [apollo-vision] fullscreen SoM -> {save_path} "
        f"({len(elements)} elements, img {annotated.size[0]}x{annotated.size[1]})"
    )
    return elements, save_path


def capture_fullscreen_no_focus(save_path: str = _NOFOCUS_MARKED_PATH):
    """Full-screen mss grab + SoM marks WITHOUT any focus-stealing call.

    Does NOT call focus_app, SetForegroundWindow, or bring-to-front — grabs
    pixels of the CURRENT screen state so a transient extension popup that is
    open stays open through the capture.

    Reuses set_of_mark grab/mark/redact; does NOT touch set_of_mark DPI logic.
    Returns (elements, marked_png_path).
    """
    from set_of_mark import collect_clickable_elements, grab_full_screen, draw_marks

    print(
        "  [apollo-vision] NO-FOCUS capture — no focus_app / SetForegroundWindow"
    )

    # Accessibility tree of whatever is frontmost NOW (must not steal focus first)
    elements = collect_clickable_elements(max_elems=_FULLSCREEN_MAX_ELEMS)
    img, ox, oy, scale = grab_full_screen()
    for el in elements:
        el["sx"] = int((el["cx"] - ox) * scale)
        el["sy"] = int((el["cy"] - oy) * scale)

    try:
        from som_redact import redact_image
        img = redact_image(img, elements, ox, oy)
    except Exception as e:
        print(f"  [apollo-vision] redact skipped: {e}")

    annotated = draw_marks(img, elements, ox, oy, scale)
    annotated.save(save_path)
    print(
        f"  [apollo-vision] no-focus SoM -> {save_path} "
        f"({len(elements)} elements, img {annotated.size[0]}x{annotated.size[1]})"
    )
    return elements, save_path


def capture_fullscreen_raw_no_focus(save_path: str | None = None):
    """Raw mss grab + meta WITHOUT focus — for coordinate vision on open popups."""
    from set_of_mark import grab_full_screen

    print("  [apollo-vision] NO-FOCUS raw capture")
    img, ox, oy, scale = grab_full_screen()
    if save_path is None:
        save_path = "email_workflow_automation_nofocus_raw.png"
    img.save(save_path)
    meta = {
        "width": img.size[0],
        "height": img.size[1],
        "ox": ox,
        "oy": oy,
        "scale": scale,
    }
    print(
        f"  [apollo-vision] no-focus raw -> {save_path} "
        f"{meta['width']}x{meta['height']}"
    )
    return save_path, meta


# Pinned Apollo toolbar icon — skip Extensions dropdown entirely
_PINNED_APOLLO_INTENT = (
    "the pinned Apollo.io extension icon on the Chrome toolbar "
    "(its own icon, NOT the puzzle-piece Extensions button)"
)

_POPUP_CONTENT_KEYWORDS = (
    "access email",
    "show email",
    "get email",
    "reveal email",
    "apollo",
    "apollo.io",
)


def _detect_apollo_popup_in_elements(elements) -> bool:
    """True if accessibility tree names suggest Apollo popup content."""
    for el in elements or []:
        name = (el.get("name") or "").lower()
        if any(k in name for k in _POPUP_CONTENT_KEYWORDS):
            return True
    return False


def _vision_detect_apollo_popup(screenshot_path: str) -> tuple[bool, str]:
    """Ask vision if Apollo popup content is visible in a focus-free screenshot."""
    import base64
    import json
    import requests

    key = _load_api_key()
    if not key or not key.startswith("sk-ant"):
        return False, "no API key for popup detection"

    prompt = (
        "Look at this screenshot. Is the Apollo.io extension POPUP currently open "
        "and visible, showing content like 'Access email', 'Show email', a person's "
        "name, or other Apollo panel UI? "
        'Return ONLY JSON: {"visible": true/false, "why": "..."}'
    )
    try:
        with open(screenshot_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5",
                "max_tokens": 150,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            },
            timeout=45,
        )
        r.raise_for_status()
        raw = r.json()["content"][0]["text"]
        obj = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        visible = bool(obj.get("visible"))
        why = str(obj.get("why") or "")
        return visible, why
    except Exception as e:
        return False, f"vision popup detect failed: {e}"


def detect_apollo_popup_open() -> dict:
    """Focus-free capture + check whether Apollo popup content is visible."""
    elements, marked_path = capture_fullscreen_no_focus()
    if _detect_apollo_popup_in_elements(elements):
        return {
            "open": True,
            "method": "som_elements",
            "marked_path": marked_path,
            "element_count": len(elements),
        }

    raw_path, _meta = capture_fullscreen_raw_no_focus()
    visible, why = _vision_detect_apollo_popup(raw_path)
    if visible:
        return {
            "open": True,
            "method": "vision",
            "raw_path": raw_path,
            "why": why,
            "element_count": len(elements),
        }

    return {
        "open": False,
        "method": "none",
        "marked_path": marked_path,
        "raw_path": raw_path,
        "why": why or "popup content not seen",
        "element_count": len(elements),
        "note": "popup closed before capture or not yet open",
    }


def _click_screen_no_refocus(sx: int, sy: int) -> tuple[bool, str]:
    """Direct pyautogui click — never calls focus_app / SetForegroundWindow."""
    import pyautogui
    try:
        pyautogui.click(sx, sy)
        return True, f"pyautogui.click({sx},{sy}) no-refocus"
    except Exception as e:
        return False, str(e)


def _press_screen_no_refocus(sx: int, sy: int) -> tuple[bool, str]:
    """Move to the point and send a real left click (no window refocus).

    Uses SetCursorPos + mouse_event, then pyautogui.click. The first pulse on
    an unfocused Chrome window is often consumed as focus; callers retry.
    """
    import ctypes
    import pyautogui

    sx, sy = int(sx), int(sy)
    try:
        pyautogui.moveTo(sx, sy, duration=0.05)
        time.sleep(0.04)
        ctypes.windll.user32.SetCursorPos(sx, sy)
        time.sleep(0.04)
        # MOUSEEVENTF_LEFTDOWN / LEFTUP at current cursor position
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.04)
        pyautogui.click(sx, sy)
        return True, f"press+click({sx},{sy}) no-refocus"
    except Exception as e:
        try:
            pyautogui.click(sx, sy)
            return True, f"pyautogui.click({sx},{sy}) fallback"
        except Exception as e2:
            return False, f"{e}; {e2}"


def _click_pinned_apollo_som(*, allow_refocus: bool = True) -> tuple[bool, int | None, str]:
    """Single SoM click on the pinned Apollo toolbar icon."""
    from som_pick import pick_element_by_intent
    from agent_act import do_action

    if allow_refocus:
        _focus_chrome()
    elements, path = (
        capture_fullscreen_marked()
        if allow_refocus
        else capture_fullscreen_no_focus()
    )
    chosen_id, reason = pick_element_by_intent(path, elements, _PINNED_APOLLO_INTENT)
    if chosen_id is None:
        return False, None, f"SoM no match ({reason})"

    match = next((e for e in elements if e.get("id") == chosen_id), None)
    if not match:
        return False, None, f"SoM id={chosen_id} not in list"

    if allow_refocus:
        action = {"action": "click", "id": chosen_id, "why": "pinned Apollo icon"}
        ok, msg = do_action(
            action, elements, target_procs=["chrome.exe"], title_hint="LinkedIn"
        )
    else:
        ok, msg = _click_screen_no_refocus(match["cx"], match["cy"])

    name = (match.get("name") or "").strip() or "(unnamed)"
    if ok:
        return True, chosen_id, f"SoM clicked #{chosen_id} '{name}' — {reason}"
    return False, chosen_id, f"SoM pick ok but click failed: {msg}"


def _click_pinned_apollo_coordinate_no_refocus() -> tuple[bool, str]:
    """Coordinate-vision click on pinned icon without refocus (for retries)."""
    raw_path, meta = capture_fullscreen_raw_no_focus()
    found, sx, sy, why = locate_by_coordinate_vision(
        _PINNED_APOLLO_INTENT, raw_path, meta=meta
    )
    if not found or sx is None or sy is None:
        return False, f"coordinate miss: {why}"
    ok, msg = _click_screen_no_refocus(sx, sy)
    if ok:
        return True, f"coordinate no-refocus ({sx},{sy}) — {why}"
    return False, msg


def open_apollo_via_pinned_icon(
    wait_after_click: float = 0.9,
    max_clicks: int = 3,
) -> dict:
    """Click pinned Apollo icon ONCE (odd retries) and confirm popup opens.

    Rules:
    - Never use Extensions puzzle-piece in this flow.
    - Extension icon TOGGLES: keep total clicks ODD (1, 3) so popup ends OPEN.
    - After opening, detect via focus-free capture only (no refocus).
    - Retry clicks use no-refocus path so we don't dismiss an open popup.
    """
    clicks = 0
    last_det = None
    notes = []

    while clicks < max_clicks:
        if clicks > 0:
            time.sleep(0.35)  # never double-click back-to-back

        clicks += 1
        print(f"  [apollo] pinned-icon click #{clicks} (expect popup OPEN)")

        if clicks == 1:
            ok, eid, note = _click_pinned_apollo_som(allow_refocus=True)
        else:
            # Retries: no refocus — refocus would dismiss an open popup
            ok, eid, note = _click_pinned_apollo_som(allow_refocus=False)
            if not ok:
                ok2, note2 = _click_pinned_apollo_coordinate_no_refocus()
                ok, eid, note = ok2, None, note2

        notes.append(f"click#{clicks}: {note}")
        print(f"  [apollo] click result: ok={ok} note={note}")

        if not ok:
            if clicks % 2 == 1 and clicks < max_clicks:
                continue
            break

        time.sleep(wait_after_click)
        last_det = detect_apollo_popup_open()
        if last_det.get("open"):
            print(
                f"  [apollo] popup detected via {last_det.get('method')} "
                f"(clicks={clicks})"
            )
            return {
                "ok": True,
                "clicks": clicks,
                "popup": last_det,
                "notes": notes,
            }

        print(
            f"  [apollo] popup not detected after click #{clicks} — "
            f"{last_det.get('note') or last_det.get('why')}"
        )
        # Only retry on odd click counts (1 failed detect -> try 3rd click max)
        if clicks >= max_clicks:
            break
        if clicks % 2 == 0:
            # Even clicks toggle closed — need one more odd click
            print("  [apollo] even click count — one more odd click to re-open")

    return {
        "ok": False,
        "clicks": clicks,
        "popup": last_det,
        "notes": notes,
        "note": (
            last_det.get("note") if last_det else "popup never detected"
        ) or "popup closed before capture — try Apollo side panel fallback",
    }


# ---------------------------------------------------------------------------
# Extensions dropdown path (click puzzle-piece ONCE — never double-click)
# ---------------------------------------------------------------------------
#
# HONEST NOTE: The whole failure was an EVEN number of Extensions-icon clicks
# (open then close). Exactly one open-click + never re-clicking that icon after
# the dropdown is open + focus-free captures should keep the dropdown open long
# enough for vision to read the "Apollo.io" label.

_EXTENSIONS_INTENT = "the Extensions puzzle-piece icon in the Chrome toolbar"

_DROPDOWN_CONTENT_KEYWORDS = (
    "apollo",
    "apollo.io",
    "manage extensions",
    "manage your extensions",
    "free b2b",
    "email finder",
)


def _detect_extensions_dropdown_in_elements(elements) -> bool:
    """True if tree names suggest the extensions dropdown list is visible."""
    hits = 0
    for el in elements or []:
        name = (el.get("name") or "").lower()
        if any(k in name for k in _DROPDOWN_CONTENT_KEYWORDS):
            hits += 1
    return hits >= 1


def _vision_detect_extensions_dropdown(screenshot_path: str) -> tuple[bool, str]:
    """Ask vision if the Chrome extensions dropdown is open with extension items."""
    import base64
    import json
    import requests

    key = _load_api_key()
    if not key or not key.startswith("sk-ant"):
        return False, "no API key for dropdown detection"

    prompt = (
        "Look at this screenshot. Is the Chrome EXTENSIONS DROPDOWN menu currently "
        "open and visible, showing a list of extensions (e.g. text containing "
        "'Apollo.io', 'Apollo.io: Free B2B Phone Number & Email Finder', "
        "'Manage extensions', or other extension names)? "
        'Return ONLY JSON: {"visible": true/false, "why": "..."}'
    )
    try:
        with open(screenshot_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5",
                "max_tokens": 150,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            },
            timeout=45,
        )
        r.raise_for_status()
        raw = r.json()["content"][0]["text"]
        obj = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        return bool(obj.get("visible")), str(obj.get("why") or "")
    except Exception as e:
        return False, f"vision dropdown detect failed: {e}"


def detect_extensions_dropdown_open() -> dict:
    """Focus-free capture + verify extensions dropdown list content is visible."""
    elements, marked_path = capture_fullscreen_no_focus()
    if _detect_extensions_dropdown_in_elements(elements):
        return {
            "open": True,
            "method": "som_elements",
            "marked_path": marked_path,
            "element_count": len(elements),
            "has_apollo_hint": any(
                "apollo" in (e.get("name") or "").lower() for e in elements
            ),
        }

    raw_path, _meta = capture_fullscreen_raw_no_focus()
    visible, why = _vision_detect_extensions_dropdown(raw_path)
    if visible:
        return {
            "open": True,
            "method": "vision",
            "raw_path": raw_path,
            "marked_path": marked_path,
            "why": why,
            "element_count": len(elements),
            "has_apollo_hint": "apollo" in why.lower(),
        }

    return {
        "open": False,
        "method": "none",
        "marked_path": marked_path,
        "raw_path": raw_path,
        "why": why or "dropdown content not seen",
        "element_count": len(elements),
        "note": "dropdown closed before capture or not yet open",
    }


def _click_extensions_icon_som(*, allow_refocus: bool = True) -> tuple[bool, int | None, str]:
    """Single SoM click on the Extensions puzzle-piece — never call twice in a row."""
    from som_pick import pick_element_by_intent
    from agent_act import do_action

    if allow_refocus:
        _focus_chrome()
    elements, path = (
        capture_fullscreen_marked()
        if allow_refocus
        else capture_fullscreen_no_focus()
    )
    chosen_id, reason = pick_element_by_intent(path, elements, _EXTENSIONS_INTENT)
    if chosen_id is None:
        return False, None, f"SoM no match ({reason})"

    match = next((e for e in elements if e.get("id") == chosen_id), None)
    if not match:
        return False, None, f"SoM id={chosen_id} not in list"

    if allow_refocus:
        ok, msg = do_action(
            {"action": "click", "id": chosen_id, "why": "Extensions puzzle-piece"},
            elements,
            target_procs=["chrome.exe"],
            title_hint="LinkedIn",
        )
    else:
        ok, msg = _click_screen_no_refocus(match["cx"], match["cy"])

    name = (match.get("name") or "").strip() or "(unnamed)"
    if ok:
        return True, chosen_id, f"SoM clicked #{chosen_id} '{name}' — {reason}"
    return False, chosen_id, f"SoM pick ok but click failed: {msg}"


def _click_extensions_icon_coordinate_no_refocus() -> tuple[bool, str]:
    """Coordinate click on puzzle-piece without refocus (retry path only)."""
    raw_path, meta = capture_fullscreen_raw_no_focus()
    found, sx, sy, why = locate_by_coordinate_vision(
        _EXTENSIONS_INTENT, raw_path, meta=meta
    )
    if not found or sx is None or sy is None:
        return False, f"coordinate miss: {why}"
    ok, msg = _click_screen_no_refocus(sx, sy)
    if ok:
        return True, f"coordinate no-refocus ({sx},{sy}) — {why}"
    return False, msg


def open_extensions_dropdown(
    wait_after_click: float = 1.0,
    max_clicks: int = 3,
) -> dict:
    """Click Extensions puzzle-piece ONCE (odd retries) and keep dropdown open.

    The dropdown TOGGLES on each click — an even count closes it. Never click
    the Extensions icon twice in a row. After opening, verify via focus-free
    capture only (no refocus — refocus can dismiss the dropdown too).
    """
    clicks = 0
    last_det = None
    notes = []

    while clicks < max_clicks:
        if clicks > 0:
            time.sleep(0.35)  # never double-click back-to-back

        clicks += 1
        print(f"  [apollo] extensions-icon click #{clicks} (expect dropdown OPEN)")

        if clicks == 1:
            ok, eid, note = _click_extensions_icon_som(allow_refocus=True)
        else:
            ok, eid, note = _click_extensions_icon_som(allow_refocus=False)
            if not ok:
                ok2, note2 = _click_extensions_icon_coordinate_no_refocus()
                ok, eid, note = ok2, None, note2

        notes.append(f"click#{clicks}: {note}")
        print(f"  [apollo] click result: ok={ok} note={note}")

        if not ok:
            if clicks % 2 == 1 and clicks < max_clicks:
                continue
            break

        time.sleep(wait_after_click)
        last_det = detect_extensions_dropdown_open()
        if last_det.get("open"):
            print(
                f"  [apollo] dropdown detected via {last_det.get('method')} "
                f"(clicks={clicks}, apollo_hint={last_det.get('has_apollo_hint')})"
            )
            return {
                "ok": True,
                "clicks": clicks,
                "dropdown": last_det,
                "notes": notes,
            }

        print(
            f"  [apollo] dropdown not detected after click #{clicks} — "
            f"{last_det.get('note') or last_det.get('why')}"
        )
        if clicks >= max_clicks:
            break
        if clicks % 2 == 0:
            print("  [apollo] even click count — one more odd click to re-open")

    return {
        "ok": False,
        "clicks": clicks,
        "dropdown": last_det,
        "notes": notes,
        "note": (
            last_det.get("note") if last_det else "dropdown never detected"
        ) or "dropdown closed before capture",
    }


_APOLLO_DROPDOWN_ITEM_INTENT = (
    "the list item labeled 'Apollo.io' (Apollo.io: Free B2B Phone Number & Email "
    "Finder) in the open extensions dropdown"
)


def click_apollo_in_dropdown(wait_after_click: float = 1.2) -> dict:
    """Find + click Apollo.io in the OPEN extensions dropdown (focus-free only).

    Vision reads the label — no hardcoded coordinates. SoM first on a
    focus-free marked capture; coordinate fallback on the raw no-focus shot.
    Clicks ONCE via pyautogui (no refocus, never touches Extensions icon).
    """
    from som_pick import pick_element_by_intent

    print("  [apollo] TASK2: find Apollo.io label in open dropdown (focus-free)")

    elements, marked_path = capture_fullscreen_no_focus()
    dropdown_ok = _detect_extensions_dropdown_in_elements(elements)
    if not dropdown_ok:
        raw_check, _meta = capture_fullscreen_raw_no_focus()
        visible, why = _vision_detect_extensions_dropdown(raw_check)
        if not visible:
            return {
                "ok": False,
                "found": False,
                "clicked": False,
                "note": f"dropdown not open — cannot click Apollo item ({why})",
            }

    som_reason = "empty element list"
    chosen_id, som_reason = pick_element_by_intent(
        marked_path, elements, _APOLLO_DROPDOWN_ITEM_INTENT
    )

    if chosen_id is not None:
        match = next((e for e in elements if e.get("id") == chosen_id), None)
        if match:
            name = (match.get("name") or "").strip() or "(unnamed)"
            print(
                f"  [apollo] path=SoM found #{chosen_id}: '{name}' — {som_reason}"
            )
            ok, msg = _click_screen_no_refocus(match["cx"], match["cy"])
            note = f"SoM clicked #{chosen_id} '{name}' — {som_reason}; {msg}"
            if ok:
                print(f"  [apollo] Apollo item click OK ({note})")
                time.sleep(wait_after_click)
                panel = detect_apollo_popup_open()
                return {
                    "ok": bool(panel.get("open")),
                    "found": True,
                    "clicked": True,
                    "path": "SoM",
                    "chosen_id": chosen_id,
                    "note": note,
                    "panel": panel,
                }
            return {
                "ok": False,
                "found": True,
                "clicked": False,
                "path": "SoM",
                "note": note,
            }

    print(f"  [apollo] SoM miss ({som_reason}) — coordinate fallback on focus-free raw")

    raw_path, meta = capture_fullscreen_raw_no_focus()
    found, sx, sy, why = locate_by_coordinate_vision(
        _APOLLO_DROPDOWN_ITEM_INTENT, raw_path, meta=meta
    )
    if not found or sx is None or sy is None:
        return {
            "ok": False,
            "found": False,
            "clicked": False,
            "path": "coordinate",
            "note": f"Apollo item not found — SoM ({som_reason}); coord ({why})",
        }

    print(f"  [apollo] path=coordinate found screen=({sx},{sy}) — {why}")
    ok, msg = _click_screen_no_refocus(sx, sy)
    note = f"coordinate clicked ({sx},{sy}) — {why}; {msg}"
    if not ok:
        return {
            "ok": False,
            "found": True,
            "clicked": False,
            "path": "coordinate",
            "note": note,
        }

    print(f"  [apollo] Apollo item click OK ({note})")
    time.sleep(wait_after_click)
    panel = detect_apollo_popup_open()
    return {
        "ok": bool(panel.get("open")),
        "found": True,
        "clicked": True,
        "path": "coordinate",
        "note": note,
        "panel": panel,
    }


_ACCESS_EMAIL_INTENT = (
    "the Access email / Show email / Get email / reveal email button "
    "in the Apollo panel or popup"
)

_ACCESS_EMAIL_TEXT_PATTERNS = [
    "access email",
    "show email",
    "reveal email",
    "view email",
    "get email",
]

# Matcher self-test / optional broader scan (plain "access email" wins via tie-break)
_ACCESS_EMAIL_TEXT_PATTERNS_TEST = [
    "access email & phone",
    "access email",
    "show email",
    "reveal email",
    "view email",
]


def find_element_by_text(
    elements: list,
    patterns: list[str],
) -> tuple[dict | None, list[dict]]:
    """Find first SoM element whose name matches a text pattern (priority order).

    Patterns are tried in list order. Within the same pattern tier, prefer exact
    name match, then shorter names (plain 'Access email' beats 'Access email & phone').

    Returns (best_match, all_matches) where best_match has keys:
      element, id, name, rect, cx, cy, pattern, pattern_index
    """
    all_matches: list[dict] = []

    def _tier_score(name: str, pattern: str) -> tuple[int, int]:
        n = (name or "").strip().lower()
        p = pattern.lower()
        if n == p:
            return (0, len(n))
        # "access email" alone beats "access email & phone" when both match pattern
        if p in n and n.startswith(p):
            rest = n[len(p):].strip()
            if not rest:
                return (0, len(n))
            if rest.startswith("&"):
                return (2, len(n))
            return (1, len(n))
        if p in n:
            return (3, len(n))
        return (99, len(n))

    best: dict | None = None
    for pi, pattern in enumerate(patterns):
        tier: list[dict] = []
        pl = pattern.lower()
        for el in elements or []:
            name = (el.get("name") or "").strip()
            if not name or pl not in name.lower():
                continue
            rec = {
                "element": el,
                "id": el.get("id"),
                "name": name,
                "rect": el.get("rect"),
                "cx": el.get("cx"),
                "cy": el.get("cy"),
                "pattern": pattern,
                "pattern_index": pi,
                "score": _tier_score(name, pattern),
            }
            tier.append(rec)
            all_matches.append(rec)
        if tier:
            tier.sort(key=lambda r: (r["score"][0], r["score"][1]))
            best = tier[0]
            break

    return best, all_matches


def _click_som_element(element: dict) -> tuple[bool, str]:
    """Click a SoM element at its accessibility-tree center (no coordinate guessing)."""
    cx, cy = element.get("cx"), element.get("cy")
    if cx is None or cy is None:
        return False, "element has no cx/cy"
    return _click_screen_no_refocus(int(cx), int(cy))


def find_email_in_som_elements(elements: list) -> dict | None:
    """Scan SoM element names for an exact email string (best read path)."""
    for el in elements or []:
        name = (el.get("name") or "").strip()
        if not name:
            continue
        if _EMAIL_FORMAT_RE.match(name):
            cleaned = _clean_email(name) or name.lower()
            if cleaned and _EMAIL_FORMAT_RE.match(cleaned):
                return {
                    "found": True,
                    "email": cleaned,
                    "element_id": el.get("id"),
                    "element_name": name,
                    "source": "som_text",
                }
        m = _EMAIL_RE.search(name)
        if m:
            cleaned = _clean_email(m.group(0))
            if cleaned and _EMAIL_FORMAT_RE.match(cleaned):
                return {
                    "found": True,
                    "email": cleaned,
                    "element_id": el.get("id"),
                    "element_name": name,
                    "source": "som_text",
                }
    return None


def _crop_around_element_rect(
    img,
    element: dict,
    meta: dict,
    *,
    pad_x: int = 30,
    pad_top: int = 10,
    extend_below: int = 220,
    extend_right: int = 120,
) -> tuple[object, tuple[int, int, int, int]]:
    """Crop read region from a SoM element rect (screen coords -> image pixels)."""
    rect = element.get("rect")
    if not rect or len(rect) < 4:
        cx = element.get("cx") or 0
        cy = element.get("cy") or 0
        rect = (cx - 80, cy - 20, cx + 200, cy + extend_below)
    L, T, R, B = rect
    ox = int(meta.get("ox") or 0)
    oy = int(meta.get("oy") or 0)
    scale = float(meta.get("scale") or 1.0) or 1.0
    w, h = img.size
    left = int((L - ox) * scale) - pad_x
    top = int((T - oy) * scale) - pad_top
    right = int((R - ox) * scale) + extend_right
    bottom = int((B - oy) * scale) + extend_below
    left = max(0, left)
    top = max(0, top)
    right = min(w, max(left + 40, right))
    bottom = min(h, max(top + 40, bottom))
    if right - left > w * 0.9:
        left = max(0, int(w * 0.55))
        right = w - 10
    return img.crop((left, top, right, bottom)), (left, top, right, bottom)


def _peek_already_revealed_email() -> dict:
    """One vision call: is an email already visible under Emails in Apollo?

    Avoids a full 15-tile Access-email scan when the address was revealed earlier.
    """
    prompt = (
        "Look at this screenshot of a LinkedIn page with an Apollo.io contact panel. "
        "Under the Emails section: is there an ALREADY-REVEALED email address "
        "(like name@company.com) visible — NOT a button labeled Access email / Show email? "
        "Return ONLY JSON: "
        '{"found": true/false, "email": "exact@address.com or null", '
        '"x": <int or null>, "y": <int or null>, "what_you_see": "..."}. '
        "x,y = CENTER of the email text in THIS image's pixels if found. "
        "If only Access email button is visible (no address yet), found=false. "
        "Do not invent an email."
    )
    try:
        path, meta = capture_fullscreen_raw_no_focus()
        obj, err = _call_vision_json(path, prompt, max_tokens=220)
        if obj is None:
            print(f"  [apollo] already-revealed peek fail: {err}")
            return {"found": False, "note": err or "peek failed"}
        if not obj.get("found"):
            print("  [apollo] already-revealed peek: no visible email yet")
            return {"found": False, "what_you_see": obj.get("what_you_see")}
        raw = str(obj.get("email") or "").strip()
        cleaned = _clean_email(raw) if raw else None
        if not cleaned or not _EMAIL_FORMAT_RE.match(cleaned):
            print(f"  [apollo] already-revealed peek: bad email format masked={mask_email(raw)}")
            return {"found": False, "raw": raw, "note": "peek email failed format"}
        if _is_noise_email(cleaned):
            print(f"  [apollo] already-revealed peek: noise email masked={mask_email(cleaned)}")
            return {"found": False, "note": "peek email is noise"}
        sx = sy = None
        try:
            if obj.get("x") is not None and obj.get("y") is not None:
                sx, sy = _image_xy_to_screen(float(obj["x"]), float(obj["y"]), meta)
        except Exception:
            sx = sy = None
        print(f"  [apollo] already-revealed peek hit masked={mask_email(cleaned)}")
        return {
            "found": True,
            "email": cleaned,
            "screen_x": sx,
            "screen_y": sy,
            "what_you_see": obj.get("what_you_see"),
        }
    except Exception as e:
        print(f"  [apollo] already-revealed peek error: {e}")
        return {"found": False, "note": str(e)}


def click_access_email_in_apollo(wait_after_click: float = 1.5) -> dict:
    """Find Access email via overlapping tile vision scan (panel invisible to a11y tree).

    Fast path: if the email is already revealed (from a prior Access), detect it
    with ONE full-panel vision call and skip the expensive 15-tile Access scan.
    """
    print("  [apollo] Access email: check already-revealed, else tile scan")

    time.sleep(0.6)
    peek = _peek_already_revealed_email()
    if peek.get("found") and peek.get("email"):
        em = peek["email"]
        print(
            f"  [apollo] email already revealed (fast peek) "
            f"masked={mask_email(em)} — skip Access-email tile scan"
        )
        return {
            "ok": True,
            "found": True,
            "clicked": False,
            "revealed": True,
            "path": "already_revealed_peek",
            "screen_x": peek.get("screen_x"),
            "screen_y": peek.get("screen_y"),
            "tile_index": None,
            "verify": peek,
            "email": em,
            "click_attempts": 0,
            "tile_count": 0,
            "note": f"already revealed (fast peek) masked={mask_email(em)}",
        }

    print("  [apollo] Access email: tile scan (a11y tree cannot see Apollo panel)")
    full_path, meta = capture_fullscreen_raw_no_focus()
    tiles = split_into_tiles(full_path)
    intent = (
        "In the Apollo.io contact panel, under the Emails heading: "
        "either the button labeled 'Access email' / 'Show email' / 'reveal email' "
        "(NOT 'Compose email'), "
        "OR the already-revealed email address text (like name@company.com)"
    )
    scan = find_target_by_tile_scan(
        intent,
        tiles,
        full_image_path=full_path,
        meta=meta,
        zoom_factor=2,
        verify=True,
    )

    if not scan.get("found"):
        return {
            "ok": False,
            "found": False,
            "clicked": False,
            "path": "tile_scan",
            "note": scan.get("why") or "Access email not found by tile scan",
            "tile_index": scan.get("tile_index"),
            "tile_logs": scan.get("tile_logs"),
            "tile_count": len(tiles),
        }

    sx, sy = int(scan["screen_x"]), int(scan["screen_y"])
    ti = scan.get("tile_index")
    verify = scan.get("verify") or {}
    print(
        f"  [apollo] tile #{ti} verified screen=({sx},{sy}) "
        f"verify={verify.get('is_target')} "
        f"access_btn={verify.get('is_access_button')} "
        f"email_text={verify.get('is_email_text')} "
        f"crop={verify.get('verify_path')}"
    )

    already = None
    raw_v = verify.get("email")
    if raw_v:
        already = _clean_email(str(raw_v)) or str(raw_v).strip()
        if already and not _EMAIL_FORMAT_RE.match(already):
            already = None
    if already or verify.get("is_email_text"):
        if already:
            print(
                f"  [apollo] email already revealed under Emails "
                f"masked={mask_email(already)} — skip Access-email click"
            )
            return {
                "ok": True,
                "found": True,
                "clicked": False,
                "revealed": True,
                "path": "already_revealed",
                "screen_x": sx,
                "screen_y": sy,
                "tile_index": ti,
                "verify": verify,
                "email": already,
                "click_attempts": 0,
                "tile_count": len(tiles),
                "note": (
                    f"already revealed on tile #{ti} anchor=({sx},{sy}) "
                    f"masked={mask_email(already)}"
                ),
            }

    # Click the refined button center. If that still misses (whitespace to the
    # right of a left-aligned pill), walk a few points left/right on the same row.
    click_points = [
        (sx, sy),
        (sx - 28, sy),
        (sx - 52, sy),
        (sx + 24, sy),
        (sx, sy + 10),
    ]
    max_clicks = len(click_points)
    revealed = False
    last_det: dict | None = None
    attempts_used = 0
    detected_email: str | None = None
    last_xy = (sx, sy)

    for attempt, (cx, cy) in enumerate(click_points, start=1):
        attempts_used = attempt
        last_xy = (cx, cy)
        ok, msg = _press_screen_no_refocus(cx, cy)
        print(
            f"  [apollo] tile #{ti} click#{attempt}/{max_clicks} at ({cx},{cy}) {msg}"
        )
        if attempt == 1:
            time.sleep(0.15)
            _press_screen_no_refocus(cx, cy)
            print(f"  [apollo] tile #{ti} click#1b (activate) at ({cx},{cy})")
        time.sleep(0.85)

        det = reveal_detected((sx, sy))
        last_det = det
        if det.get("revealed"):
            revealed = True
            detected_email = det.get("email")
            sx, sy = cx, cy
            break
        what = str(det.get("what_you_see") or "")
        what = what.encode("ascii", "ignore").decode("ascii")
        print(
            f"  [apollo] click#{attempt} did NOT reveal — "
            f"still_access_button={det.get('still_shows_access_button')} "
            f"email_visible={det.get('email_visible')} what={what!r}"
        )

    out = {
        "ok": True,
        "found": True,
        "clicked": True,
        "revealed": revealed,
        "reveal_detect": last_det or {},
        "path": "tile_scan",
        "screen_x": last_xy[0],
        "screen_y": last_xy[1],
        "tile_index": ti,
        "verify": verify,
        "note": (
            f"tile_scan #{ti} anchor=({last_xy[0]},{last_xy[1]}) revealed={revealed} "
            f"clicks_used={attempts_used}/{max_clicks}"
        ),
        "tile_count": len(tiles),
        "tile_logs": scan.get("tile_logs"),
        "click_attempts": attempts_used,
        "email": detected_email,
    }
    if revealed:
        print(f"  [apollo] Access email reveal detected after {attempts_used} clicks.")
        time.sleep(wait_after_click)
    else:
        print(
            f"  [apollo] Access email reveal NOT detected after {attempts_used} clicks — ask user to click manually at ({sx},{sy})."
        )
    return out


def _clipboard_text() -> str:
    """Read current clipboard text. Never log the raw value."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            text = root.clipboard_get()
        except Exception:
            text = ""
        root.destroy()
        return str(text or "").strip()
    except Exception:
        pass
    try:
        import subprocess

        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return (p.stdout or "").strip()
    except Exception:
        return ""


_COPY_ICON_PROMPT = (
    "This is a tight crop of the Apollo Emails row after hovering the revealed "
    "email address. Find the COPY / clipboard icon that appears beside the address "
    "(two overlapping squares, or a small clipboard). "
    "Do NOT pick Compose email, an envelope, a green checkmark, or the email text. "
    "Return ONLY JSON: "
    '{"found": true/false, "x": <int>, "y": <int>, "why": "..."}. '
    "x,y = CENTER of the copy icon in THIS image's pixels. If absent, found=false."
)

_VERIFY_COPY_PROMPT = (
    "Does this crop show a COPY / clipboard icon (the control that copies the "
    "email address onto the clipboard)? "
    "NOT Compose email, NOT an envelope, NOT a checkmark, NOT the email text. "
    "Return ONLY JSON: "
    '{"is_target": true/false, "what_you_see": "brief description"}.'
)


def copy_revealed_email_from_apollo(
    email_anchor: tuple[int, int] | None,
    *,
    expected_email: str | None = None,
) -> dict:
    """Hover the revealed email so the icon-only copy control appears, then click it.

    Copy is icon-only (not in the a11y tree), so this uses hover + a tight crop
    + vision, then verifies before clicking. Clipboard is regex-checked; the
    raw address is never printed.
    """
    from PIL import Image
    import pyautogui

    if not email_anchor:
        return {
            "ok": False,
            "copied": False,
            "email": None,
            "note": "no email anchor for copy hover",
        }

    ax, ay = int(email_anchor[0]), int(email_anchor[1])
    print(f"  [apollo] copy: hover email line at ({ax},{ay}) so copy icon appears")
    try:
        pyautogui.moveTo(ax + 24, ay, duration=0.12)
        time.sleep(0.45)
    except Exception as e:
        return {
            "ok": False,
            "copied": False,
            "email": None,
            "note": f"hover failed: {e}",
        }

    full_path, meta = capture_fullscreen_raw_no_focus()
    ix, iy = _screen_to_image_xy(ax, ay, meta)
    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)

    # Email text + the icon row to its right.
    crop_w, crop_h = 560, 100
    left = max(0, ix - 24)
    top = max(0, iy - 36)
    if left + crop_w > w:
        left = max(0, w - crop_w)
    if top + crop_h > h:
        top = max(0, h - crop_h)
    right = min(w, left + crop_w)
    bottom = min(h, top + crop_h)

    zoom = 2
    with Image.open(full_path) as img:
        cropped = img.crop((left, top, right, bottom))
        zoomed = _zoom_crop(cropped, factor=zoom)
        zoomed.save(_COPY_HOVER_PATH)
        print(
            f"  [apollo] copy hover crop box=({left},{top},{right},{bottom}) "
            f"-> {_COPY_HOVER_PATH} zoomed={zoomed.size[0]}x{zoomed.size[1]}"
        )

    obj, err = _call_vision_json(_COPY_HOVER_PATH, _COPY_ICON_PROMPT, max_tokens=220)
    sx = sy = None
    if obj and obj.get("found"):
        try:
            lx = float(obj.get("x")) / zoom
            ly = float(obj.get("y")) / zoom
            sx, sy = _image_xy_to_screen(left + lx, top + ly, meta)
            why = str(obj.get("why") or "")
            why = why.encode("ascii", "ignore").decode("ascii")
            print(f"  [apollo] copy icon candidate screen=({sx},{sy}) why={why!r}")
        except (TypeError, ValueError):
            sx = sy = None

    # If vision missed, try a few points to the right of the email (icon row).
    candidates: list[tuple[int, int]] = []
    if sx is not None and sy is not None:
        candidates.append((sx, sy))
    for dx in (70, 100, 130, 160):
        candidates.append((ax + dx, ay))

    chosen = None
    verify = {}
    for cx, cy in candidates:
        v = _verify_point_on_capture(
            cx, cy, meta, full_path, _VERIFY_COPY_PROMPT,
            save_path=_COPY_VERIFY_PATH,
            crop_w=140, crop_h=90,
        )
        what = str(v.get("what_you_see") or "").encode("ascii", "ignore").decode("ascii")
        print(
            f"  [apollo] copy verify ({cx},{cy}) is_target={v.get('is_target')} "
            f"what={what!r}"
        )
        if v.get("is_target"):
            chosen = (cx, cy)
            verify = v
            break

    if chosen is None:
        return {
            "ok": False,
            "copied": False,
            "email": None,
            "note": "copy icon not verified after hover",
            "verify": verify,
            "hover_path": _COPY_HOVER_PATH,
        }

    cx, cy = chosen
    ok, msg = _press_screen_no_refocus(cx, cy)
    print(f"  [apollo] copy click at ({cx},{cy}) {msg}")
    time.sleep(0.15)
    _press_screen_no_refocus(cx, cy)
    time.sleep(0.4)

    clip = _clipboard_text()
    cleaned = _clean_email(clip) if clip else None
    if cleaned and not _EMAIL_FORMAT_RE.match(cleaned):
        cleaned = None
    clip_ok = bool(cleaned)
    if expected_email and cleaned:
        clip_ok = cleaned.lower() == expected_email.strip().lower()

    if cleaned:
        print(
            f"  [apollo] copy clipboard masked={mask_email(cleaned)} "
            f"format_ok=True match_expected={clip_ok if expected_email else 'n/a'}"
        )
    else:
        print("  [apollo] copy click done but clipboard had no valid email")

    return {
        "ok": bool(ok),
        "copied": bool(cleaned),
        "email": cleaned,
        "format_ok": bool(cleaned),
        "clipboard_matches_read": (
            cleaned.lower() == expected_email.strip().lower()
            if (cleaned and expected_email)
            else None
        ),
        "screen_x": cx,
        "screen_y": cy,
        "note": (
            f"copy icon at ({cx},{cy}) "
            f"clipboard={'ok ' + mask_email(cleaned) if cleaned else 'no valid email'}"
        ),
        "verify": verify,
        "hover_path": _COPY_HOVER_PATH,
        "verify_path": _COPY_VERIFY_PATH,
    }


def _read_email_som_then_crop(
    *,
    matched_element: dict | None = None,
    after_scroll: bool = False,
) -> dict:
    """Text-first read: SoM element email text, else crop around matched element rect."""
    from PIL import Image

    layer = "scroll" if after_scroll else "vision_crop"
    if after_scroll and matched_element:
        cx, cy = matched_element.get("cx"), matched_element.get("cy")
        if cx is not None and cy is not None:
            _scroll_apollo_panel_minimal(int(cx), int(cy))

    elements, _marked = capture_fullscreen_no_focus()
    som_hit = find_email_in_som_elements(elements)
    if som_hit and som_hit.get("found"):
        em = som_hit["email"]
        print(
            f"  [apollo] som_text read #{som_hit.get('element_id')} "
            f"{som_hit.get('element_name')!r} masked={mask_email(em)}"
        )
        return {
            "found": True,
            "email": em,
            "raw": em,
            "read_layer": "som_text",
            "source": "som_text",
            "format_ok": True,
            "note": f"som_text from element #{som_hit.get('element_id')}",
            "element_id": som_hit.get("element_id"),
            "element_name": som_hit.get("element_name"),
        }

    if not matched_element:
        return {
            "found": False,
            "email": None,
            "note": "no SoM email text and no matched_element for crop",
            "format_ok": False,
            "source": layer,
        }

    path, meta = capture_fullscreen_raw_no_focus()
    with Image.open(path) as img:
        cropped, box = _crop_around_element_rect(img, matched_element, meta)
        zoomed = _zoom_crop(cropped, factor=2)
        crop_path = _CROP_REVEAL_PATH
        zoomed.save(crop_path)
        print(
            f"  [apollo] {layer} crop around SoM rect box={box} -> {crop_path} "
            f"zoomed={zoomed.size[0]}x{zoomed.size[1]}"
        )

    result = _vision_read_email_from_path(crop_path, cropped=True)
    result["read_layer"] = layer
    result["source"] = layer
    if result.get("found"):
        result["note"] = f"{layer}: {result.get('note', '')}"
    else:
        result["note"] = f"{layer} miss: {result.get('note', '')}"
    return result


_PANEL_LOCATE_PROMPT = (
    "Find the Apollo.io extension panel or popup on this screenshot — the rectangular UI "
    "showing contact information (name, title, company, Access email button, etc.). "
    "NOT the LinkedIn profile page behind it, NOT the whole browser window, NOT the "
    "person's profile photo on LinkedIn. "
    'Return ONLY JSON: {"found": true/false, "x1": <left>, "y1": <top>, "x2": <right>, '
    '"y2": <bottom>, "why": "..."}. '
    "Coordinates are pixels in THIS image (x1,y1 = top-left, x2,y2 = bottom-right)."
)

_VERIFY_ACCESS_PROMPT = (
    "This crop is from the Apollo.io contact panel Emails area. "
    "Return ONLY JSON: "
    '{"is_target": true/false, "is_access_button": true/false, '
    '"is_email_text": true/false, "email": "exact@address.com or null", '
    '"cx": <int>, "cy": <int>, "what_you_see": "brief description"}. '
    "is_access_button=true ONLY for a button labeled Access email / Show email / "
    "Reveal email. NOT 'Compose email'. "
    "is_email_text=true if a plain-text email address is visible under Emails "
    "(icon row beside it is OK). "
    "is_target=true if either is_access_button or is_email_text. "
    "cx,cy = CENTER of the Access email BUTTON (the rounded rectangle with that "
    "label) or of the revealed email TEXT, in THIS crop's pixels. "
    "Do NOT return the Emails heading or empty whitespace."
)


def _call_vision_json(image_path: str, prompt: str, *, max_tokens: int = 300) -> tuple[dict | None, str]:
    """Send one image + prompt to Claude; parse first JSON object in response."""
    import base64
    import json
    import requests

    key = _load_api_key()
    if not key or not key.startswith("sk-ant"):
        return None, "no Claude API key for vision"
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5",
                "max_tokens": max_tokens,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            },
            timeout=60,
        )
        r.raise_for_status()
        raw_text = r.json()["content"][0]["text"]
        obj = json.loads(raw_text[raw_text.find("{"): raw_text.rfind("}") + 1])
        return obj, ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Tile-based vision scanning (Apollo panel invisible to accessibility tree)
#
# HONEST NOTE: Tile scanning trades SPEED (many vision calls) for ACCURACY
# (small regions). If tile-level coordinates fail verification consistently,
# the practical fallback is: user clicks "Access email" manually and the agent
# continues from the revealed email.
# ---------------------------------------------------------------------------

def split_into_tiles(
    image_path: str,
    tile_w: int = 960,
    tile_h: int = 540,
    overlap: int = 120,
    *,
    save_dir: str = _TILE_DIR,
) -> list[dict]:
    """Split a full-screen capture into overlapping tiles; save each to disk."""
    import os
    from PIL import Image

    os.makedirs(save_dir, exist_ok=True)
    tiles: list[dict] = []
    stride_x = max(1, tile_w - overlap)
    stride_y = max(1, tile_h - overlap)

    with Image.open(image_path) as img:
        full_w, full_h = img.size
        idx = 0
        y = 0
        while y < full_h:
            x = 0
            while x < full_w:
                x2 = min(x + tile_w, full_w)
                y2 = min(y + tile_h, full_h)
                crop = img.crop((x, y, x2, y2))
                path = os.path.join(save_dir, f"tile_{idx:03d}.png")
                crop.save(path)
                tiles.append({
                    "tile_index": idx,
                    "origin_x": x,
                    "origin_y": y,
                    "width": x2 - x,
                    "height": y2 - y,
                    "tile_image_path": path,
                    "full_width": full_w,
                    "full_height": full_h,
                })
                idx += 1
                if x + tile_w >= full_w:
                    break
                x += stride_x
            if y + tile_h >= full_h:
                break
            y += stride_y

    print(
        f"  [apollo-tile] split {full_w}x{full_h} -> {len(tiles)} tiles "
        f"({tile_w}x{tile_h} overlap={overlap}) dir={save_dir}"
    )
    for t in tiles:
        print(
            f"    tile #{t['tile_index']}: origin=({t['origin_x']},{t['origin_y']}) "
            f"size={t['width']}x{t['height']} -> {t['tile_image_path']}"
        )
    return tiles


def _tile_local_to_screen(
    tile: dict,
    local_x: float,
    local_y: float,
    meta: dict,
    *,
    zoom_factor: float = 1.0,
) -> tuple[int, int]:
    """Map tile-local pixel (pre-zoom) to screen coords via image->screen transform."""
    z = float(zoom_factor) or 1.0
    ix = tile["origin_x"] + (float(local_x) / z)
    iy = tile["origin_y"] + (float(local_y) / z)
    return _image_xy_to_screen(ix, iy, meta)


def _verify_point_on_capture(
    screen_x: int,
    screen_y: int,
    meta: dict,
    full_image_path: str,
    prompt: str,
    *,
    save_path: str = _TILE_VERIFY_PATH,
    crop_w: int = 200,
    crop_h: int = 120,
) -> dict:
    """Crop ~200x120 around screen point on full capture; vision yes/no."""
    from PIL import Image

    ix, iy = _screen_to_image_xy(screen_x, screen_y, meta)
    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)
    left = max(0, ix - crop_w // 2)
    top = max(0, iy - crop_h // 2)
    if left + crop_w > w:
        left = max(0, w - crop_w)
    if top + crop_h > h:
        top = max(0, h - crop_h)
    right = min(w, left + crop_w)
    bottom = min(h, top + crop_h)

    with Image.open(full_image_path) as img:
        verify_img = img.crop((left, top, right, bottom))
        verify_img.save(save_path)

    obj, err = _call_vision_json(save_path, prompt, max_tokens=220)
    if obj is None:
        return {
            "is_target": False,
            "what_you_see": err,
            "verify_path": save_path,
            "crop_box": (left, top, right, bottom),
        }
    is_target = bool(obj.get("is_target"))
    what = str(obj.get("what_you_see") or obj.get("why") or "")
    out = {
        "is_target": is_target,
        "what_you_see": what,
        "verify_path": save_path,
        "is_access_button": bool(obj.get("is_access_button")),
        "is_email_text": bool(obj.get("is_email_text")),
        "email": obj.get("email"),
        "crop_box": (left, top, right, bottom),
    }
    # Refine click to the button/text center inside this crop (not crop-center
    # whitespace to the right of a left-aligned Access email button).
    try:
        if obj.get("cx") is not None and obj.get("cy") is not None:
            lcx = float(obj.get("cx"))
            lcy = float(obj.get("cy"))
            crop_w_act = max(1, right - left)
            crop_h_act = max(1, bottom - top)
            lcx = min(max(0.0, lcx), crop_w_act - 1)
            lcy = min(max(0.0, lcy), crop_h_act - 1)
            rsx, rsy = _image_xy_to_screen(left + lcx, top + lcy, meta)
            out["local_cx"] = lcx
            out["local_cy"] = lcy
            out["refined_screen"] = (rsx, rsy)
    except (TypeError, ValueError):
        pass
    return out


_REVEAL_DETECT_PROMPT = (
    "This crop is the Apollo.io contact panel Emails section. "
    "Return ONLY JSON: "
    "{\"revealed\": true/false, \"email_visible\": true/false, "
    "\"still_shows_access_button\": true/false, "
    "\"email\": \"exact@address.com or null\", \"what_you_see\": \"...\"}. "
    "Rules: "
    "If a plain-text email ADDRESS (like name@company.com) is visible under the "
    "'Emails' heading (an icon row beside it is OK): email_visible=true, "
    "still_shows_access_button=false, email=<the exact address>, revealed=true. "
    "If an 'Access email' / 'Show email' / 'Reveal email' BUTTON is still visible "
    "under Emails: still_shows_access_button=true, email_visible=false, "
    "email=null, revealed=false. "
    "revealed=true ONLY if an email-format string is visible under Emails. "
    "Do not set revealed just because the button is gone."
)


def reveal_detected(
    anchor_point: tuple[int, int],
    *,
    full_image_path: str | None = None,
    meta: dict | None = None,
    save_path: str = _REVEAL_DETECT_CROP_PATH,
) -> dict:
    """Detect whether the Apollo email line appeared under the Emails heading.

    Focus-free capture; crop includes the Emails heading + the line below it
    (where either 'Access email' or the revealed address sits), 2x zoom.
    revealed = an email-format string is visible under Emails.
    not revealed = the Access email button is still showing.
    """
    from PIL import Image

    ax, ay = int(anchor_point[0]), int(anchor_point[1])

    if full_image_path is None or meta is None:
        full_image_path, meta = capture_fullscreen_raw_no_focus()

    ix, iy = _screen_to_image_xy(ax, ay, meta)
    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)

    # Include the 'Emails' heading above the click and the address line.
    crop_w, crop_h = 540, 160
    left = max(0, ix - 80)
    top = max(0, iy - 70)
    if left + crop_w > w:
        left = max(0, w - crop_w)
    if top + crop_h > h:
        top = max(0, h - crop_h)
    right = min(w, left + crop_w)
    bottom = min(h, top + crop_h)

    with Image.open(full_image_path) as img:
        cropped = img.crop((left, top, right, bottom))
        zoomed = _zoom_crop(cropped, factor=2)
        zoomed.save(save_path)

    obj, err = _call_vision_json(save_path, _REVEAL_DETECT_PROMPT, max_tokens=220)
    if obj is None:
        return {
            "revealed": False,
            "email_visible": False,
            "still_shows_access_button": True,
            "email": None,
            "what_you_see": err,
            "anchor_screen": (ax, ay),
            "detect_path": save_path,
        }

    still_btn = bool(obj.get("still_shows_access_button"))
    raw_email = obj.get("email")
    cleaned = None
    if raw_email:
        cleaned = _clean_email(str(raw_email)) or str(raw_email).strip()
        if cleaned and not _EMAIL_FORMAT_RE.match(cleaned):
            cleaned = None
    email_visible = bool(cleaned) or bool(obj.get("email_visible"))
    # revealed = email-format string under Emails; button still showing = not revealed
    revealed = bool(cleaned) or (email_visible and not still_btn)
    if still_btn and not cleaned:
        revealed = False
    what = str(obj.get("what_you_see") or "")
    try:
        what = _EMAIL_RE.sub(lambda m: mask_email(m.group(0)), what)
    except Exception:
        pass

    print(
        f"  [apollo-detect] anchor=({ax},{ay}) revealed={revealed} "
        f"email_visible={email_visible} still_access_button={still_btn} "
        f"masked={mask_email(cleaned or '')} crop={save_path}"
    )
    return {
        "revealed": revealed,
        "email_visible": email_visible,
        "still_shows_access_button": still_btn,
        "email": cleaned,
        "what_you_see": what,
        "anchor_screen": (ax, ay),
        "detect_path": save_path,
    }


def find_target_by_tile_scan(
    intent: str,
    tiles: list[dict],
    *,
    full_image_path: str,
    meta: dict,
    zoom_factor: int = 2,
    verify: bool = True,
    verify_prompt: str | None = None,
) -> dict:
    """Scan tiles for intent; map local coords to screen; verify before returning."""
    from PIL import Image

    verify_prompt = verify_prompt or _VERIFY_ACCESS_PROMPT
    tile_logs: list[dict] = []
    z = max(1, int(zoom_factor))

    for tile in tiles:
        ti = tile["tile_index"]
        tw, th = tile["width"], tile["height"]
        send_path = tile["tile_image_path"]

        if z > 1:
            with Image.open(tile["tile_image_path"]) as timg:
                zoomed = _zoom_crop(timg, factor=z)
                send_path = tile["tile_image_path"].replace(
                    ".png", f"_z{z}.png"
                )
                zoomed.save(send_path)

        prompt = (
            f"In THIS image ({tw * z if z > 1 else tw}x{th * z if z > 1 else th}), find: {intent}. "
            'Return ONLY JSON: {"found": true/false, "x": <int>, "y": <int>, '
            '"confidence": "high"|"medium"|"low", "why": "..."}. '
            "x,y = CENTER of the target in THIS image's pixels. If absent, found=false."
        )
        obj, err = _call_vision_json(send_path, prompt, max_tokens=220)
        log_entry = {
            "tile_index": ti,
            "origin": (tile["origin_x"], tile["origin_y"]),
            "size": (tw, th),
            "error": err or None,
        }
        if obj is None:
            log_entry["found"] = False
            log_entry["why"] = err
            tile_logs.append(log_entry)
            print(f"  [apollo-tile] #{ti}: API error — {err}")
            continue

        found = bool(obj.get("found"))
        conf = str(obj.get("confidence") or "low").lower()
        why = str(obj.get("why") or "")
        # Windows console may use a cp1252 codec; sanitize any non-ASCII
        # characters (vision "why" text can include arrows like '→').
        why_snip = str(why[:80]).replace("→", "->")
        why_snip = why_snip.encode("ascii", errors="ignore").decode("ascii")
        log_entry.update({"found": found, "confidence": conf, "why": why})
        tile_logs.append(log_entry)
        print(
            f"  [apollo-tile] #{ti} origin=({tile['origin_x']},{tile['origin_y']}) "
            f"found={found} confidence={conf} why={why_snip!r}"
        )

        if not found or conf not in ("high", "medium"):
            continue

        try:
            lx = float(obj.get("x"))
            ly = float(obj.get("y"))
        except (TypeError, ValueError):
            print(f"  [apollo-tile] #{ti}: invalid x/y in response")
            continue

        # Vision coords are in the zoomed tile image
        local_x = lx / z
        local_y = ly / z
        sx, sy = _tile_local_to_screen(tile, local_x, local_y, meta, zoom_factor=1.0)
        print(
            f"  [apollo-tile] #{ti} candidate screen=({sx},{sy}) "
            f"local=({local_x:.0f},{local_y:.0f})"
        )

        if verify:
            v = None
            chosen_sx, chosen_sy = sx, sy
            # Point often lands on the Emails heading; nudge down and re-verify.
            for dy in (0, 36, 64, 90):
                vx, vy = sx, sy + dy
                cand = _verify_point_on_capture(
                    vx, vy, meta, full_image_path, verify_prompt,
                    crop_w=360, crop_h=130,
                )
                what_snip = str(cand.get("what_you_see") or "").replace("→", "->")
                what_snip = what_snip.encode("ascii", "ignore").decode("ascii")
                print(
                    f"  [apollo-tile] verify dy={dy} is_target={cand.get('is_target')} "
                    f"btn={cand.get('is_access_button')} email_text={cand.get('is_email_text')} "
                    f"what={what_snip!r} -> {cand.get('verify_path')}"
                )
                if cand.get("is_target"):
                    v = cand
                    refined = cand.get("refined_screen")
                    if refined and len(refined) == 2:
                        chosen_sx, chosen_sy = int(refined[0]), int(refined[1])
                        print(
                            f"  [apollo-tile] refined click to button center "
                            f"({chosen_sx},{chosen_sy}) from crop local="
                            f"({cand.get('local_cx')},{cand.get('local_cy')})"
                        )
                    else:
                        chosen_sx, chosen_sy = vx, vy
                    break
            if v is None:
                continue
            sx, sy = chosen_sx, chosen_sy
        else:
            v = {"is_target": True, "verify_path": None, "what_you_see": "verify skipped"}

        return {
            "found": True,
            "screen_x": sx,
            "screen_y": sy,
            "tile_index": ti,
            "why": why,
            "confidence": conf,
            "verify": v,
            "tile_logs": tile_logs,
            "local_x": local_x,
            "local_y": local_y,
        }

    return {
        "found": False,
        "screen_x": None,
        "screen_y": None,
        "tile_index": None,
        "why": "no tile passed scan+verify",
        "tile_logs": tile_logs,
    }


_REVEALED_EMAIL_TILE_INTENT = (
    "the revealed email address text in the Apollo.io contact panel"
)


def _read_email_by_tile_scan(
    full_image_path: str,
    meta: dict,
) -> dict:
    """Tile-scan for revealed email, crop tightly around hit, vision-read."""
    from PIL import Image

    tiles = split_into_tiles(full_image_path)
    scan = find_target_by_tile_scan(
        _REVEALED_EMAIL_TILE_INTENT,
        tiles,
        full_image_path=full_image_path,
        meta=meta,
        zoom_factor=2,
        verify=False,
    )
    if not scan.get("found"):
        return {
            "found": False,
            "email": None,
            "note": scan.get("why") or "tile scan found no email text",
            "format_ok": False,
            "source": "tile_scan",
            "tile_logs": scan.get("tile_logs"),
        }

    sx, sy = int(scan["screen_x"]), int(scan["screen_y"])
    ix, iy = _screen_to_image_xy(sx, sy, meta)
    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)
    crop_w, crop_h = 420, 100
    left = max(0, ix - 40)
    top = max(0, iy - 20)
    if left + crop_w > w:
        left = max(0, w - crop_w)
    if top + crop_h > h:
        top = max(0, h - crop_h)
    right = min(w, left + crop_w)
    bottom = min(h, top + crop_h)

    with Image.open(full_image_path) as img:
        cropped = img.crop((left, top, right, bottom))
        zoomed = _zoom_crop(cropped, factor=2)
        zoomed.save(_CROP_REVEAL_PATH)
        print(
            f"  [apollo-tile] read crop box=({left},{top},{right},{bottom}) "
            f"-> {_CROP_REVEAL_PATH} zoomed={zoomed.size[0]}x{zoomed.size[1]}"
        )

    result = _vision_read_email_from_path(_CROP_REVEAL_PATH, cropped=True)
    result["read_layer"] = "tile_scan"
    result["source"] = "tile_scan"
    result["tile_index"] = scan.get("tile_index")
    if result.get("found"):
        result["note"] = f"tile_scan tile #{scan.get('tile_index')}: {result.get('note', '')}"
    else:
        result["note"] = f"tile_scan miss: {result.get('note', '')}"
    return result


def _read_email_by_anchor_crop(
    anchor_screen: tuple[int, int],
) -> dict:
    """Read revealed email by cropping around a verified anchor point."""
    from PIL import Image

    full_path, meta = capture_fullscreen_raw_no_focus()
    ax, ay = int(anchor_screen[0]), int(anchor_screen[1])
    ix, iy = _screen_to_image_xy(ax, ay, meta)

    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)
    # Tight crop on the email line just below the Emails heading
    # (the revealed address replaces the Access email button at this anchor).
    crop_w, crop_h = 500, 80
    left = max(0, ix - 20)
    top = max(0, iy - 16)
    if left + crop_w > w:
        left = max(0, w - crop_w)
    if top + crop_h > h:
        top = max(0, h - crop_h)
    right = min(w, left + crop_w)
    bottom = min(h, top + crop_h)

    with Image.open(full_path) as img:
        cropped = img.crop((left, top, right, bottom))
        zoomed = _zoom_crop(cropped, factor=2)
        zoomed.save(_CROP_REVEAL_PATH)
        print(
            f"  [apollo-anchor] read crop box=({left},{top},{right},{bottom}) "
            f"-> {_CROP_REVEAL_PATH} zoomed={zoomed.size[0]}x{zoomed.size[1]}"
        )

    result = _vision_read_email_from_path(_CROP_REVEAL_PATH, cropped=True)
    if result.get("found"):
        result["note"] = f"anchor_crop: {result.get('note', '')}"
    else:
        result["note"] = f"anchor_crop miss: {result.get('note', '')}"
    result["read_layer"] = "anchor_crop"
    result["source"] = "anchor_crop"
    return result


def _normalize_image_box(x1, y1, x2, y2) -> tuple[int, int, int, int]:
    a1, a2 = int(round(float(x1))), int(round(float(x2)))
    b1, b2 = int(round(float(y1))), int(round(float(y2)))
    return min(a1, a2), min(b1, b2), max(a1, a2), max(b1, b2)


def _validate_panel_image_box(
    box: tuple[int, int, int, int],
    meta: dict,
) -> tuple[bool, str]:
    """Panel box must be in-bounds, not tiny, not the whole screen."""
    x1, y1, x2, y2 = box
    w = int(meta.get("width") or 1)
    h = int(meta.get("height") or 1)
    bw, bh = x2 - x1, y2 - y1
    if bw < 60 or bh < 80:
        return False, f"panel too small ({bw}x{bh})"
    if bw > w * 0.85 or bh > h * 0.92:
        return False, f"panel too large ({bw}x{bh} vs screen {w}x{h})"
    if x1 < 0 or y1 < 0 or x2 > w or y2 > h:
        return False, f"panel out of bounds ({x1},{y1},{x2},{y2}) for {w}x{h}"
    return True, ""


def _image_box_to_screen_box(
    box: tuple[int, int, int, int],
    meta: dict,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    sx1, sy1 = _image_xy_to_screen(x1, y1, meta)
    sx2, sy2 = _image_xy_to_screen(x2, y2, meta)
    return min(sx1, sx2), min(sy1, sy2), max(sx1, sx2), max(sy1, sy2)


def _crop_image_file(
    raw_path: str,
    image_box: tuple[int, int, int, int],
    save_path: str,
):
    """Crop image_box (image pixels) from raw_path and save."""
    from PIL import Image

    x1, y1, x2, y2 = image_box
    with Image.open(raw_path) as img:
        cropped = img.crop((x1, y1, x2, y2))
        cropped.save(save_path)
        return cropped


def locate_apollo_panel_region(
    screenshot_path: str | None = None,
    meta: dict | None = None,
    *,
    save_crop_path: str = _PANEL_REGION_PATH,
) -> dict:
    """Focus-free capture + vision bbox for the Apollo panel (screen coords).

    Returns {found, image_box, screen_box, crop_path, meta, note, why}.
    Saves a crop of the detected panel for eyeballing.
    """
    if screenshot_path is None or meta is None:
        screenshot_path, meta = capture_fullscreen_raw_no_focus()

    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)
    prompt = _PANEL_LOCATE_PROMPT + f" Image size: {w}x{h}."
    obj, err = _call_vision_json(screenshot_path, prompt, max_tokens=250)
    if obj is None:
        return {
            "found": False,
            "note": err or "panel vision failed",
            "meta": meta,
            "raw_path": screenshot_path,
        }

    why = str(obj.get("why") or "")
    if not obj.get("found"):
        return {
            "found": False,
            "note": why or "Apollo panel not visible",
            "meta": meta,
            "raw_path": screenshot_path,
            "why": why,
        }

    try:
        box = _normalize_image_box(
            obj["x1"], obj["y1"], obj["x2"], obj["y2"]
        )
    except (KeyError, TypeError, ValueError):
        return {
            "found": False,
            "note": f"invalid panel bbox in vision response: {obj!r}",
            "meta": meta,
            "raw_path": screenshot_path,
        }

    ok, vnote = _validate_panel_image_box(box, meta)
    if not ok:
        return {
            "found": False,
            "note": f"panel bbox rejected: {vnote} ({why})",
            "image_box": box,
            "meta": meta,
            "raw_path": screenshot_path,
            "why": why,
        }

    screen_box = _image_box_to_screen_box(box, meta)
    _crop_image_file(screenshot_path, box, save_crop_path)
    print(
        f"  [apollo-panel] region image_box={box} screen_box={screen_box} "
        f"-> {save_crop_path} ({why})"
    )
    return {
        "found": True,
        "image_box": box,
        "screen_box": screen_box,
        "crop_path": save_crop_path,
        "meta": meta,
        "raw_path": screenshot_path,
        "why": why,
        "note": f"panel at screen {screen_box}",
    }


def _elements_in_panel(
    elements: list,
    panel_screen_box: tuple[int, int, int, int],
    panel_image_box: tuple[int, int, int, int],
    meta: dict,
) -> list[dict]:
    """Filter SoM elements to panel; remap rects to crop-local for draw_marks."""
    sx1, sy1, sx2, sy2 = panel_screen_box
    px1, py1, _, _ = panel_image_box
    ox = int(meta.get("ox") or 0)
    oy = int(meta.get("oy") or 0)
    scale = float(meta.get("scale") or 1.0) or 1.0
    out = []
    for el in elements or []:
        cx, cy = el.get("cx"), el.get("cy")
        if cx is None or cy is None:
            continue
        if not (sx1 <= cx <= sx2 and sy1 <= cy <= sy2):
            continue
        L, T, R, B = el["rect"]
        Lc = int((L - ox) * scale) - px1
        Tc = int((T - oy) * scale) - py1
        Rc = int((R - ox) * scale) - px1
        Bc = int((B - oy) * scale) - py1
        out.append({
            **el,
            "rect": (Lc, Tc, Rc, Bc),
            "cx": cx,
            "cy": cy,
            "screen_cx": cx,
            "screen_cy": cy,
        })
    return out


def _verify_access_email_target(
    screen_x: int,
    screen_y: int,
    meta: dict,
    raw_path: str | None = None,
    *,
    save_path: str = _VERIFY_CROP_PATH,
    crop_w: int = 200,
    crop_h: int = 120,
) -> dict:
    """Small crop around proposed point; vision confirms Access-email button."""
    from PIL import Image

    if raw_path is None:
        raw_path, meta = capture_fullscreen_raw_no_focus()

    ix, iy = _screen_to_image_xy(screen_x, screen_y, meta)
    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)
    left = max(0, ix - crop_w // 2)
    top = max(0, iy - crop_h // 2)
    if left + crop_w > w:
        left = max(0, w - crop_w)
    if top + crop_h > h:
        top = max(0, h - crop_h)
    right = min(w, left + crop_w)
    bottom = min(h, top + crop_h)

    with Image.open(raw_path) as img:
        verify_img = img.crop((left, top, right, bottom))
        verify_img.save(save_path)

    obj, err = _call_vision_json(save_path, _VERIFY_ACCESS_PROMPT, max_tokens=200)
    if obj is None:
        return {
            "is_target": False,
            "what_you_see": err,
            "verify_path": save_path,
            "screen_x": screen_x,
            "screen_y": screen_y,
        }

    is_target = bool(obj.get("is_target"))
    what = str(obj.get("what_you_see") or "")
    print(
        f"  [apollo-verify] screen=({screen_x},{screen_y}) is_target={is_target} "
        f"what={what!r} -> {save_path}"
    )
    return {
        "is_target": is_target,
        "what_you_see": what,
        "verify_path": save_path,
        "screen_x": screen_x,
        "screen_y": screen_y,
    }


def find_access_email_in_panel(
    panel: dict,
    *,
    raw_path: str | None = None,
    meta: dict | None = None,
) -> dict:
    """Search for Access email INSIDE the panel region only; verify before click.

    panel: result dict from locate_apollo_panel_region (must have image_box, screen_box).
    Returns {found, screen_x, screen_y, path, verified, verify, note, candidates}.
    """
    from set_of_mark import collect_clickable_elements, draw_marks
    from som_pick import pick_element_by_intent

    if not panel.get("found"):
        return {
            "found": False,
            "note": panel.get("note") or "no panel region",
        }

    image_box = panel["image_box"]
    screen_box = panel["screen_box"]
    raw_path = raw_path or panel.get("raw_path")
    meta = meta or panel.get("meta")
    if not raw_path or not meta:
        raw_path, meta = capture_fullscreen_raw_no_focus()

    px1, py1, px2, py2 = image_box
    crop_origin = (px1, py1)
    panel_crop_path = panel.get("crop_path") or _PANEL_REGION_PATH
    _crop_image_file(raw_path, image_box, panel_crop_path)

    candidates: list[dict] = []

    # (a) SoM on panel crop — elements whose centers fall inside panel
    elements = collect_clickable_elements(max_elems=_FULLSCREEN_MAX_ELEMS)
    panel_elements = _elements_in_panel(
        elements, screen_box, image_box, meta
    )
    if panel_elements:
        from PIL import Image
        with Image.open(panel_crop_path) as panel_img:
            marked = draw_marks(panel_img, panel_elements, 0, 0, 1.0)
            marked.save(_PANEL_MARKED_PATH)
        chosen_id, reason = pick_element_by_intent(
            _PANEL_MARKED_PATH, panel_elements, _ACCESS_EMAIL_INTENT
        )
        if chosen_id is not None:
            match = next(
                (e for e in panel_elements if e.get("id") == chosen_id), None
            )
            if match:
                sx = int(match["screen_cx"])
                sy = int(match["screen_cy"])
                candidates.append({
                    "path": "SoM",
                    "screen_x": sx,
                    "screen_y": sy,
                    "note": f"SoM #{chosen_id} '{match.get('name')}' — {reason}",
                })

    # (b) coordinate vision ON the panel crop (not full screen)
    found, sx, sy, why = locate_by_coordinate_vision(
        _ACCESS_EMAIL_INTENT,
        panel_crop_path,
        meta=meta,
        crop_origin=crop_origin,
    )
    if found and sx is not None and sy is not None:
        candidates.append({
            "path": "coordinate",
            "screen_x": int(sx),
            "screen_y": int(sy),
            "note": f"coordinate on panel crop — {why}",
        })

    if not candidates:
        return {
            "found": False,
            "note": "Access email not found inside panel region",
            "panel": panel,
            "panel_crop_path": panel_crop_path,
        }

    for cand in candidates:
        verify = _verify_access_email_target(
            cand["screen_x"],
            cand["screen_y"],
            meta,
            raw_path=raw_path,
            save_path=_VERIFY_CROP_PATH,
        )
        cand["verify"] = verify
        if verify.get("is_target"):
            return {
                "found": True,
                "screen_x": cand["screen_x"],
                "screen_y": cand["screen_y"],
                "path": cand["path"],
                "verified": True,
                "verify": verify,
                "note": cand["note"],
                "panel": panel,
                "panel_crop_path": panel_crop_path,
            }
        print(
            f"  [apollo] rejected {cand['path']} ({cand['screen_x']},{cand['screen_y']}): "
            f"{verify.get('what_you_see')}"
        )

    return {
        "found": False,
        "verified": False,
        "note": "candidates found but none verified as Access email button",
        "candidates": candidates,
        "panel": panel,
        "panel_crop_path": panel_crop_path,
    }


def capture_fullscreen_raw(save_path: str = _FULLSCREEN_RAW_PATH):
    """mss full-screen grab with NO SoM marks — for pure coordinate vision.

    Returns (path, meta) where meta = {width, height, ox, oy, scale} matching
    set_of_mark.grab_full_screen (virtual desktop origin + DPI scale).
    Image pixel (sx,sy) -> screen: screen_x = sx/scale + ox, screen_y = sy/scale + oy.
    """
    from set_of_mark import grab_full_screen

    _focus_chrome()
    img, ox, oy, scale = grab_full_screen()
    img.save(save_path)
    meta = {
        "width": img.size[0],
        "height": img.size[1],
        "ox": ox,
        "oy": oy,
        "scale": scale,
    }
    print(
        f"  [apollo-vision] raw fullscreen -> {save_path} "
        f"{meta['width']}x{meta['height']} origin=({ox},{oy}) scale={scale}"
    )
    return save_path, meta


def _image_xy_to_screen(ix: float, iy: float, meta: dict) -> tuple[int, int]:
    """Same inverse transform as SoM sx/sy <-> screen cx/cy (do not touch DPI logic)."""
    scale = float(meta.get("scale") or 1.0) or 1.0
    ox = int(meta.get("ox") or 0)
    oy = int(meta.get("oy") or 0)
    screen_x = int(ix / scale + ox)
    screen_y = int(iy / scale + oy)
    return screen_x, screen_y


def locate_by_coordinate_vision(
    intent: str,
    screenshot_path: str,
    meta: dict | None = None,
    *,
    crop_origin: tuple[int, int] | None = None,
) -> tuple[bool, int | None, int | None, str]:
    """Ask vision for raw image-pixel (x,y) of intent; map to screen coords.

    When crop_origin=(px1,py1) is set, the screenshot is a sub-crop of the full
    desktop image; vision x,y are relative to the crop and origin is added before
    mapping to screen coords.

    HONEST TRADEOFF: less precise than SoM numbered picks — use only when SoM
    has no match (e.g. extension dropdown items outside the accessibility tree).

    Returns (found, screen_x, screen_y, why).
    """
    import base64
    import json
    import requests
    from PIL import Image

    try:
        with Image.open(screenshot_path) as im:
            w, h = im.size
    except Exception as e:
        return False, None, None, f"could not open screenshot: {e}"

    if meta is None:
        # Re-read virtual-desktop origin the same way grab_full_screen does
        from set_of_mark import grab_full_screen
        _img, ox, oy, scale = grab_full_screen()
        meta = {"width": w, "height": h, "ox": ox, "oy": oy, "scale": scale}
        # Prefer dimensions of the file we send to the model
        meta["width"] = w
        meta["height"] = h

    key = _load_api_key()
    if not key or not key.startswith("sk-ant"):
        return False, None, None, "no Claude API key for coordinate vision"

    prompt = (
        f"You are looking at a screenshot at resolution {w}x{h}. "
        f"Find: {intent}. "
        'Return ONLY JSON: {"found": true/false, "x": <pixel_x>, "y": <pixel_y>, "why": "..."}. '
        "The x,y must be the CENTER of the target in pixels of THIS image "
        f"(0<=x<{w}, 0<=y<{h}). If not visible, found=false."
    )
    if crop_origin:
        prompt = (
            f"This is a CROPPED region ({w}x{h}) of a larger screen — only the Apollo panel. "
            f"Find: {intent}. "
            'Return ONLY JSON: {"found": true/false, "x": <pixel_x>, "y": <pixel_y>, "why": "..."}. '
            f"x,y are center pixels in THIS crop (0<=x<{w}, 0<=y<{h}). If not visible, found=false."
        )
    try:
        with open(screenshot_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5",
                "max_tokens": 200,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            },
            timeout=60,
        )
        r.raise_for_status()
        raw_text = r.json()["content"][0]["text"]
        obj = json.loads(raw_text[raw_text.find("{"): raw_text.rfind("}") + 1])
    except Exception as e:
        return False, None, None, f"coordinate vision API error: {e}"

    why = str(obj.get("why") or "")
    if not obj.get("found"):
        return False, None, None, why or "target not visible"

    try:
        ix = float(obj.get("x"))
        iy = float(obj.get("y"))
    except (TypeError, ValueError):
        return False, None, None, f"invalid x/y in response: {obj!r}"

    if not (0 <= ix < w and 0 <= iy < h):
        return (
            False,
            None,
            None,
            f"x,y out of image bounds ({ix},{iy}) for {w}x{h}: {why}",
        )

    if crop_origin:
        ix += crop_origin[0]
        iy += crop_origin[1]

    screen_x, screen_y = _image_xy_to_screen(ix, iy, meta)
    origin_note = f" crop_origin={crop_origin}" if crop_origin else ""
    print(
        f"  [apollo-vision] coordinate vision: image=({ix:.0f},{iy:.0f})"
        f"{origin_note} "
        f"-> screen=({screen_x},{screen_y}) why={why}"
    )
    return True, screen_x, screen_y, why


def click_by_vision(
    intent: str,
    save_path: str | None = None,
    settle_delay: float = 1.2,
):
    """Full-screen SoM pick first; pure coordinate vision only on SoM miss.

    Always takes a NEW full-screen capture at call time — never reuses a stale
    screenshot from a previous step. settle_delay waits BEFORE capturing so a
    just-opened menu/panel can finish rendering (act -> wait -> re-perceive).

    HONEST TRADEOFF: SoM is precise for accessibility-tree elements. The Chrome
    Extensions dropdown (and similar popups) are NOT in the tree, so SoM draws
    no numbers on them. On SoM miss we fall back to coordinate vision.

    Returns (clicked: bool, chosen_id_or_None, note).
    note starts with 'SoM' or 'coordinate-vision fallback' so callers can log path.
    """
    global _CAPTURE_SEQ
    import pyautogui
    from som_pick import pick_element_by_intent
    from agent_act import do_action

    last_note = "no attempt"
    last_coords = None
    for attempt in (1, 2):
        print(
            f"  [apollo-vision] click_by_vision attempt {attempt}/2 — intent={intent!r}"
        )

        # Settle BEFORE capture so UI opened by the previous click is in-frame
        if settle_delay and settle_delay > 0:
            print(f"  [apollo] settle {settle_delay:.1f}s before fresh capture")
            time.sleep(settle_delay)

        # --- path A: Set-of-Mark on a FRESH capture (never reuse prior step image) ---
        _CAPTURE_SEQ += 1
        cap_n = _CAPTURE_SEQ
        marked_path = save_path or f"email_workflow_automation_cap_{cap_n}_marked.png"
        elements, path = capture_fullscreen_marked(save_path=marked_path)
        print(
            f"  [apollo] capture #{cap_n} for intent {intent!r} -> "
            f"{len(elements)} elements ({path})"
        )

        chosen_id, reason = (None, "empty element list")
        if elements:
            chosen_id, reason = pick_element_by_intent(path, elements, intent)

        if chosen_id is not None:
            match = next((e for e in elements if e.get("id") == chosen_id), None)
            if match:
                name = (match.get("name") or "").strip() or "(unnamed)"
                ctype = match.get("control_type") or "?"
                print(
                    f"  [apollo-vision] path=SoM chose #{chosen_id}: {ctype} '{name}' "
                    f"at screen ({match.get('cx')},{match.get('cy')}) — {reason}"
                )
                _focus_chrome()
                action = {
                    "action": "click",
                    "id": chosen_id,
                    "why": f"SoM vision click for: {intent}",
                }
                ok, msg = do_action(
                    action, elements, target_procs=["chrome.exe"], title_hint="LinkedIn"
                )
                if ok:
                    note = f"SoM clicked #{chosen_id} '{name}' — {reason}"
                    print(f"  [apollo-vision] click OK ({note})")
                    return True, chosen_id, note
                last_note = f"SoM pick ok (#{chosen_id}) but click failed: {msg}"
                print(f"  [apollo-vision] miss: {last_note}")
            else:
                last_note = f"SoM chose id={chosen_id} not in element list ({reason})"
                print(f"  [apollo-vision] miss: {last_note}")
        else:
            last_note = f"SoM no match ({reason})"
            print(f"  [apollo-vision] {last_note} — trying coordinate-vision fallback")

            # --- path B: pure coordinate vision on a FRESH raw capture ---
            _CAPTURE_SEQ += 1
            raw_n = _CAPTURE_SEQ
            raw_path, meta = capture_fullscreen_raw(
                save_path=f"email_workflow_automation_cap_{raw_n}_raw.png"
            )
            print(
                f"  [apollo] capture #{raw_n} (raw) for intent {intent!r} -> "
                f"{meta['width']}x{meta['height']}"
            )
            found, sx, sy, why = locate_by_coordinate_vision(intent, raw_path, meta=meta)
            last_coords = (sx, sy, why)
            if found and sx is not None and sy is not None:
                print(
                    f"  [apollo-vision] path=coordinate-vision fallback "
                    f"screen=({sx},{sy}) why={why}"
                )
                _focus_chrome()
                synth_id = 9001
                synth = {
                    "id": synth_id,
                    "name": intent[:40],
                    "control_type": "Custom",
                    "rect": (sx - 2, sy - 2, sx + 2, sy + 2),
                    "cx": sx,
                    "cy": sy,
                }
                try:
                    ok, msg = do_action(
                        {
                            "action": "click",
                            "id": synth_id,
                            "why": f"coordinate-vision fallback: {intent}",
                        },
                        [synth],
                        target_procs=["chrome.exe"],
                        title_hint="LinkedIn",
                    )
                except Exception as e:
                    ok, msg = False, str(e)
                if not ok:
                    try:
                        pyautogui.click(sx, sy)
                        ok, msg = True, f"pyautogui.click({sx},{sy})"
                    except Exception as e2:
                        ok, msg = False, f"act+pyautogui failed: {e2}"

                if ok:
                    note = (
                        f"coordinate-vision fallback clicked screen=({sx},{sy}) — {why}"
                    )
                    print(f"  [apollo-vision] click OK ({note})")
                    return True, None, note

                last_note = (
                    f"coordinate-vision found ({sx},{sy}) but click failed: {msg}"
                )
                print(f"  [apollo-vision] miss: {last_note}")
            else:
                last_note = f"coordinate-vision miss: {why}"
                print(f"  [apollo-vision] miss: {last_note}")

        if attempt == 1:
            time.sleep(0.6)

    if last_coords and last_coords[0] is not None:
        last_note = (
            f"{last_note} | last coordinate returned screen="
            f"({last_coords[0]},{last_coords[1]}) why={last_coords[2]}"
        )
    return False, None, last_note or "vision click failed after retry"


def _load_api_key() -> str:
    try:
        with open("my_key.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


_CROP_REVEAL_PATH = "email_workflow_automation_apollo_crop.png"


def _screen_to_image_xy(screen_x: int, screen_y: int, meta: dict) -> tuple[int, int]:
    """Inverse of _image_xy_to_screen — screen coords to mss image pixels."""
    scale = float(meta.get("scale") or 1.0) or 1.0
    ox = int(meta.get("ox") or 0)
    oy = int(meta.get("oy") or 0)
    ix = int((screen_x - ox) * scale)
    iy = int((screen_y - oy) * scale)
    return ix, iy


def _default_apollo_crop_anchor(meta: dict) -> tuple[int, int]:
    """Fallback when Access-email click coords are unknown (right-side panel)."""
    w = int(meta.get("width") or 1920)
    h = int(meta.get("height") or 1080)
    return int(w * 0.82), int(h * 0.35)


def _crop_reveal_region(
    img,
    anchor_ix: int,
    anchor_iy: int,
    *,
    crop_w: int = 640,
    crop_h: int = 280,
) -> tuple[object, tuple[int, int, int, int]]:
    """Crop around the reveal; shift inward if near the screen edge."""
    w, h = img.size
    left = anchor_ix - 80
    top = anchor_iy - 40

    if left + crop_w > w - 10:
        left = max(0, w - crop_w - 10)
    if left < 0:
        left = 0
    if top + crop_h > h - 10:
        top = max(0, h - crop_h - 10)
    if top < 0:
        top = 0

    right = min(w, left + crop_w)
    bottom = min(h, top + crop_h)
    return img.crop((left, top, right, bottom)), (left, top, right, bottom)


def _zoom_crop(img, factor: int = 2):
    """Upscale cropped pixels so vision can read small/edge text."""
    from PIL import Image

    if factor <= 1:
        return img
    w, h = img.size
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
    return img.resize((max(1, w * factor), max(1, h * factor)), resample)


def _scroll_apollo_panel_minimal(screen_x: int, screen_y: int) -> None:
    """Small scroll inside the Apollo panel region (focus-free)."""
    import pyautogui

    x = max(1, screen_x)
    y = max(1, screen_y)
    print(f"  [apollo] minimal panel scroll at ({x},{y})")
    try:
        pyautogui.scroll(-3, x=x, y=y)
    except TypeError:
        pyautogui.moveTo(x, y)
        pyautogui.scroll(-3)
    time.sleep(0.7)


def _panel_contact_read_box(
    panel_image_box: tuple[int, int, int, int],
    meta: dict,
) -> tuple[int, int, int, int]:
    """Contact/read area inside the panel — trim header, clamp at screen edges."""
    x1, y1, x2, y2 = panel_image_box
    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)
    ph = y2 - y1
    # Skip ~12% header (name/title); read the contact/email area below
    read_y1 = y1 + int(ph * 0.12)
    read_y2 = y2 - 4
    read_x1 = x1 + 4
    read_x2 = x2 - 4
    if read_x2 - read_x1 < 40:
        read_x1, read_x2 = x1, x2
    if read_y2 - read_y1 < 40:
        read_y1, read_y2 = y1, y2
    read_x1 = max(0, read_x1)
    read_y1 = max(0, read_y1)
    read_x2 = min(w, read_x2)
    read_y2 = min(h, read_y2)
    return read_x1, read_y1, read_x2, read_y2


def _read_email_vision_crop(
    *,
    access_click_screen: tuple[int, int] | None = None,
    panel_image_box: tuple[int, int, int, int] | None = None,
    panel_screen_box: tuple[int, int, int, int] | None = None,
    after_scroll: bool = False,
) -> dict:
    """Vision-read from a cropped/zoomed region.

    Prefers panel_image_box (Task 3) over click-anchor when provided.
    """
    from PIL import Image

    layer = "scroll" if after_scroll else "vision-crop"
    scroll_at = None
    if panel_screen_box:
        sx1, sy1, sx2, sy2 = panel_screen_box
        scroll_at = ((sx1 + sx2) // 2, (sy1 + sy2) // 2)
    elif access_click_screen:
        scroll_at = access_click_screen

    if after_scroll:
        if scroll_at:
            _scroll_apollo_panel_minimal(*scroll_at)
        else:
            path0, meta0 = capture_fullscreen_raw_no_focus()
            ax, ay = _default_apollo_crop_anchor(meta0)
            sx, sy = _image_xy_to_screen(ax, ay, meta0)
            _scroll_apollo_panel_minimal(sx, sy)

    path, meta = capture_fullscreen_raw_no_focus()
    with Image.open(path) as img:
        if panel_image_box:
            read_box = _panel_contact_read_box(panel_image_box, meta)
            cropped = img.crop(read_box)
            box = read_box
            anchor_note = f"panel_contact {read_box}"
        elif access_click_screen:
            anchor_ix, anchor_iy = _screen_to_image_xy(*access_click_screen, meta)
            cropped, box = _crop_reveal_region(img, anchor_ix, anchor_iy)
            anchor_note = f"click_anchor ({anchor_ix},{anchor_iy})"
        else:
            anchor_ix, anchor_iy = _default_apollo_crop_anchor(meta)
            cropped, box = _crop_reveal_region(img, anchor_ix, anchor_iy)
            anchor_note = f"default ({anchor_ix},{anchor_iy})"

        zoomed = _zoom_crop(cropped, factor=2)
        crop_path = _CROP_REVEAL_PATH
        zoomed.save(crop_path)
        print(
            f"  [apollo] {layer} crop box={box} -> {crop_path} "
            f"src={cropped.size[0]}x{cropped.size[1]} "
            f"zoomed={zoomed.size[0]}x{zoomed.size[1]} "
            f"{anchor_note}"
        )

    result = _vision_read_email_from_path(crop_path, cropped=True)
    prefix = layer
    if result.get("found"):
        result["note"] = f"{prefix}: {result.get('note', '')}"
    else:
        result["note"] = f"{prefix} miss: {result.get('note', '')}"
    result["source"] = prefix
    return result


def read_revealed_email_cascade(
    *,
    matched_element: dict | None = None,
    access_click_screen: tuple[int, int] | None = None,
) -> dict:
    """Layered read: DOM -> som_text -> tile_scan read -> paste."""
    print("  [apollo] read cascade: DOM -> som_text -> tile_scan")

    dom = read_email_from_dom()
    if dom.get("found"):
        dom["read_layer"] = "dom"
        print(f"  [apollo] cascade layer=dom masked={mask_email(dom.get('email') or '')}")
        return dom
    print(f"  [apollo] DOM miss ({dom.get('note')}) — trying SoM text")

    elements, _marked = capture_fullscreen_no_focus()
    som_hit = find_email_in_som_elements(elements)
    if som_hit and som_hit.get("found"):
        em = som_hit["email"]
        print(
            f"  [apollo] cascade layer=som_text masked={mask_email(em)} "
            f"element #{som_hit.get('element_id')}"
        )
        return {
            "found": True,
            "email": em,
            "raw": em,
            "read_layer": "som_text",
            "source": "som_text",
            "format_ok": True,
            "note": f"som_text from element #{som_hit.get('element_id')}",
        }

    print("  [apollo] som_text miss — trying anchor-crop read, then tile-scan")

    if access_click_screen:
        anchor_read = _read_email_by_anchor_crop(access_click_screen)
        if anchor_read.get("found"):
            anchor_read["read_layer"] = "anchor_crop"
            return anchor_read
        print(
            f"  [apollo] anchor_crop miss — falling back to tile scan ({anchor_read.get('note')})"
        )

    full_path, meta = capture_fullscreen_raw_no_focus()
    tile_read = _read_email_by_tile_scan(full_path, meta)
    if tile_read.get("found"):
        tile_read["read_layer"] = "tile_scan"
        print(
            f"  [apollo] cascade layer=tile_scan "
            f"masked={mask_email(tile_read.get('email') or '')} "
            f"format_ok={tile_read.get('format_ok')}"
        )
        return tile_read

    print("  [apollo] cascade layer=paste — ask user to confirm/paste")

    out: dict = {
        "found": False,
        "email": None,
        "needs_confirm": True,
        "read_layer": "paste",
        "note": (
            "automated read failed after DOM, som_text, and tile_scan — "
            "confirm/paste from screen"
        ),
        "format_ok": False,
        "source": "cascade",
    }
    raw = tile_read.get("raw") or tile_read.get("email")
    if raw:
        out["raw"] = raw
        out["email"] = raw
    return out


def _vision_read_email_from_path(save_path: str, *, cropped: bool = False) -> dict:
    """Ask vision to read email from an existing screenshot path."""
    import base64
    import json
    import requests

    key = _load_api_key()
    if not key or not key.startswith("sk-ant"):
        return {
            "found": False,
            "email": None,
            "raw": "",
            "note": "no Claude API key for vision email read",
            "format_ok": False,
        }

    prompt = (
        "Look at this screenshot. Focus on the Apollo.io extension panel/sidebar/popup. "
        "Read the exact email address now visible there (after Access email / Show email). "
        "Return ONLY JSON: {\"email\": \"the.exact@address.com\"} or "
        "{\"email\": null, \"reason\": \"...\"} if none is visible. "
        "No other text."
    )
    if cropped:
        prompt = (
            "This is a tight crop of the Apollo Emails row — the line just below "
            "the 'Emails' heading. An icon row may sit beside the address. "
            "Return ONLY the email address as JSON: "
            "{\"email\": \"the.exact@address.com\"} or "
            "{\"email\": null, \"reason\": \"...\"} if none is visible. "
            "Read every character. Ignore icons. No other text."
        )
    try:
        with open(save_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5",
                "max_tokens": 200,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            },
            timeout=60,
        )
        r.raise_for_status()
        raw_text = r.json()["content"][0]["text"]
        obj = json.loads(raw_text[raw_text.find("{"): raw_text.rfind("}") + 1])
        raw_email = (obj.get("email") or "").strip() if obj.get("email") else ""
        if not raw_email:
            return {
                "found": False,
                "email": None,
                "raw": "",
                "note": obj.get("reason") or "vision saw no email",
                "format_ok": False,
            }

        cleaned = _clean_email(raw_email) or raw_email.strip()
        format_ok = bool(_EMAIL_FORMAT_RE.match(cleaned))
        if format_ok:
            print(
                f"  [apollo-vision] read email (masked): "
                f"{mask_email(cleaned)} format_ok=True"
            )
            return {
                "found": True,
                "email": cleaned,
                "raw": cleaned,
                "note": "read via vision",
                "format_ok": True,
            }
        print(
            f"  [apollo-vision] invalid vision read (masked raw): "
            f"{mask_email(cleaned)} format_ok=False"
        )
        return {
            "found": False,
            "email": cleaned,
            "raw": cleaned,
            "note": "vision read did not look like a valid email - confirm/paste",
            "format_ok": False,
        }
    except Exception as e:
        return {
            "found": False,
            "email": None,
            "raw": "",
            "note": f"vision email read failed: {e}",
            "format_ok": False,
        }


def _read_email_from_screen(
    save_path: str = "email_workflow_automation_apollo_reveal.png",
    *,
    no_focus: bool = False,
) -> dict:
    """Re-capture fullscreen and ask vision to read the Apollo panel email."""
    if no_focus:
        print("  [apollo] TASK3b: read email (focus-free capture)")
        path, _meta = capture_fullscreen_raw_no_focus(save_path=save_path)
    else:
        from set_of_mark import grab_full_screen
        _focus_chrome()
        img, _ox, _oy, _scale = grab_full_screen()
        img.save(save_path)
        path = save_path
        print(
            f"  [apollo-vision] reveal capture -> {path} "
            f"({img.size[0]}x{img.size[1]})"
        )
    return _vision_read_email_from_path(path)


def get_email_via_extensions_dropdown() -> dict:
    """Full flow: Extensions (1 click) -> Apollo item -> Access email -> read.

    Never double-clicks Extensions. All captures/clicks after dropdown open
    use focus-free paths so popups stay open.
    """
    steps = []

    print("  [apollo] === extensions dropdown flow ===")

    dd = open_extensions_dropdown()
    steps.append({
        "step": "extensions_dropdown",
        "ok": dd.get("ok"),
        "clicks": dd.get("clicks"),
        "note": dd.get("note"),
    })
    if not dd.get("ok"):
        return {
            "found": False,
            "failed_step": "extensions_dropdown",
            "steps": steps,
            "source": "extensions_dropdown",
            "note": dd.get("note") or "dropdown failed",
        }

    ap = click_apollo_in_dropdown()
    steps.append({
        "step": "apollo_item",
        "ok": ap.get("ok"),
        "found": ap.get("found"),
        "clicked": ap.get("clicked"),
        "path": ap.get("path"),
        "note": ap.get("note"),
    })
    if not ap.get("clicked"):
        return {
            "found": False,
            "failed_step": "apollo_item",
            "steps": steps,
            "source": "extensions_dropdown",
            "note": ap.get("note") or "Apollo item click failed",
        }

    acc = click_access_email_in_apollo()
    steps.append({
        "step": "access_email",
        "ok": acc.get("ok"),
        "found": acc.get("found"),
        "clicked": acc.get("clicked"),
        "path": acc.get("path"),
        "tile_index": acc.get("tile_index"),
        "tile_count": acc.get("tile_count"),
        "revealed": acc.get("revealed"),
        "click_attempts": acc.get("click_attempts"),
        "note": acc.get("note"),
    })
    if not acc.get("clicked") and not acc.get("revealed"):
        return {
            "found": False,
            "failed_step": "access_email",
            "steps": steps,
            "source": "extensions_dropdown",
            "note": acc.get("note") or "Access email click failed",
        }

    if not acc.get("revealed"):
        return {
            "found": False,
            "note": (
                "Access email click attempts completed but reveal not detected — "
                "confirm/paste from screen"
            ),
            "steps": steps,
            "source": "extensions_dropdown",
            "read_layer": "paste",
            "format_ok": False,
            "needs_confirm": True,
            "email": None,
        }

    access_anchor = None
    if acc.get("screen_x") is not None and acc.get("screen_y") is not None:
        access_anchor = (int(acc["screen_x"]), int(acc["screen_y"]))

    # If Access step already saw a revealed address (fast peek / tile email_text),
    # seed the read so we don't re-do a slow miss cascade when possible.
    seed_email = acc.get("email") if acc.get("revealed") else None
    if seed_email and _EMAIL_FORMAT_RE.match(str(seed_email)) and not _is_noise_email(str(seed_email)):
        read = {
            "found": True,
            "email": seed_email,
            "raw": seed_email,
            "format_ok": True,
            "read_layer": acc.get("path") or "already_revealed",
            "note": f"seeded from access step masked={mask_email(seed_email)}",
        }
        print(f"  [apollo] using already-revealed email from access step")
    else:
        read = read_revealed_email_cascade(
            access_click_screen=access_anchor,
        )
    em = read.get("email") or read.get("raw") or ""
    steps.append({
        "step": "read_email",
        "read_layer": read.get("read_layer"),
        "format_ok": read.get("format_ok"),
        "note": read.get("note"),
        "masked": mask_email(em),
    })

    copy_info = copy_revealed_email_from_apollo(
        access_anchor,
        expected_email=em if read.get("format_ok") else None,
    )
    steps.append({
        "step": "copy_email",
        "ok": copy_info.get("ok"),
        "copied": copy_info.get("copied"),
        "format_ok": copy_info.get("format_ok"),
        "clipboard_matches_read": copy_info.get("clipboard_matches_read"),
        "note": copy_info.get("note"),
        "masked": mask_email(copy_info.get("email") or ""),
    })
    if copy_info.get("copied") and copy_info.get("email"):
        em = copy_info["email"]
        read = dict(read)
        read["found"] = True
        read["email"] = em
        read["format_ok"] = True
        read["read_layer"] = read.get("read_layer") or "clipboard"
        read["note"] = (
            f"{read.get('note') or ''} | clipboard {mask_email(em)}"
        ).strip(" |")

    out = {
        "found": bool(read.get("found")),
        "note": read.get("note") or "",
        "steps": steps,
        "source": "extensions_dropdown",
        "read_layer": read.get("read_layer"),
        "format_ok": read.get("format_ok"),
        "needs_confirm": read.get("needs_confirm"),
        "copied": copy_info.get("copied"),
        "copy_note": copy_info.get("note"),
    }
    if read.get("email") is not None:
        out["email"] = read["email"]
    if read.get("raw") and not read.get("found"):
        out["email"] = read.get("raw")
        out["raw"] = read.get("raw")
    return out


def _apply_vision_email_result(vision: dict, path_name: str) -> dict:
    """Normalize vision result for get_email_for_current_profile."""
    layer = vision.get("read_layer") or path_name
    if vision.get("found"):
        print(
            f"  [apollo] path={path_name} layer={layer} masked="
            f"{mask_email(vision.get('email') or '')}"
        )
        return vision

    if vision.get("format_ok") is False and (vision.get("email") or vision.get("raw")):
        vision = dict(vision)
        vision["needs_confirm"] = True
        vision["found"] = False
        print(
            f"  [apollo] vision misread layer={layer} masked="
            f"{mask_email(vision.get('email') or vision.get('raw') or '')} "
            f"— confirm/paste required"
        )
        return vision

    if vision.get("read_layer") == "paste" or vision.get("needs_confirm"):
        print(f"  [apollo] automated read failed — confirm/paste (layer={layer})")

    return vision


def get_email_for_current_profile() -> dict:
    """DOM read first (fast), then extensions-dropdown Apollo flow.

    After Access email, the read step uses DOM -> vision-crop -> scroll cascade.
    """
    dom = read_email_from_dom()
    if dom.get("found") and dom.get("email") and not _is_noise_email(dom["email"]):
        dom["read_layer"] = "dom"
        print(
            f"  [apollo] fast-path dom masked={mask_email(dom.get('email') or '')}"
        )
        return dom

    print("  [apollo] DOM fast-path miss — running extensions -> Apollo flow")
    print("  [apollo] path=extensions_dropdown")
    vision = get_email_via_extensions_dropdown()
    return _apply_vision_email_result(vision, "extensions_dropdown")


def get_email_for_linkedin_profile() -> dict:
    """Outreach pipeline: LinkedIn tab -> Access email -> Copy to clipboard.

    Skips DOM fast-path on Gmail/other tabs. Always runs the full extensions
    dropdown flow so Access + Copy execute on the profile page.
    """
    print("  [apollo] LinkedIn tab — Access email + Copy pipeline")
    _focus_linkedin_tab()
    print("  [apollo] path=extensions_dropdown (Access -> read -> Copy icon)")
    vision = get_email_via_extensions_dropdown()
    result = _apply_vision_email_result(vision, "extensions_dropdown")
    if result.get("copied"):
        print(
            f"  [apollo] copied to clipboard masked="
            f"{mask_email(result.get('email') or '')}"
        )
    return result


def _click_path_from_note(note: str) -> str:
    """Classify which vision path produced the click note."""
    n = note or ""
    if n.startswith("SoM") or "SoM clicked" in n:
        return "SoM"
    if "coordinate-vision fallback" in n:
        return "coordinate"
    return "unknown"


def get_email_via_fullscreen_vision() -> dict:
    """Multi-step full-screen vision: Extensions -> Apollo -> Access email -> read.

    Re-captures between each click (screen changes each time). Stops on the first
    failed click and names which step failed.

    Step 2 (Apollo in dropdown) typically needs the coordinate-vision fallback
    because the Extensions menu is outside the accessibility tree.
    """
    steps = []

    def _record(step_name, ok, eid, note):
        path = _click_path_from_note(note)
        rec = {
            "step": step_name,
            "clicked": ok,
            "id": eid,
            "note": note,
            "path": path,
        }
        # Surface last returned screen coords on miss (embedded in note by click_by_vision)
        if not ok and "screen=(" in (note or ""):
            rec["miss_coords_note"] = note
        print(f"  [apollo-vision] {step_name}: clicked={ok} path={path}")
        steps.append(rec)
        return rec

    # 1. Extensions puzzle-piece (toolbar)
    print("  [apollo-vision] STEP 1/4: Extensions puzzle-piece")
    ok, eid, note = click_by_vision(
        "the Extensions puzzle-piece icon in the Chrome toolbar"
    )
    _record("extensions_toolbar", ok, eid, note)
    if not ok:
        return {
            "found": False,
            "note": f"step failed: extensions_toolbar — {note}",
            "failed_step": "extensions_toolbar",
            "steps": steps,
            "source": "fullscreen_vision",
        }

    # Wait for dropdown to fully render BEFORE the next capture (tree can't see it)
    print("  [apollo-vision] waiting for Extensions dropdown to render...")
    time.sleep(1.5)

    # 2. Apollo in dropdown (SoM usually misses -> coordinate fallback)
    print("  [apollo-vision] STEP 2/4: Apollo.io in extensions dropdown")
    ok, eid, note = click_by_vision(
        "the Apollo.io item in the extensions dropdown menu"
    )
    _record("apollo_dropdown", ok, eid, note)
    if not ok:
        return {
            "found": False,
            "note": f"step failed: apollo_dropdown — {note}",
            "failed_step": "apollo_dropdown",
            "steps": steps,
            "source": "fullscreen_vision",
        }
    time.sleep(1.5)

    # 3. Access email in Apollo panel
    print("  [apollo-vision] STEP 3/4: Access email in Apollo panel")
    ok, eid, note = click_by_vision(
        "the Access email / Show email / reveal email button in the Apollo panel"
    )
    _record("access_email", ok, eid, note)
    if not ok:
        return {
            "found": False,
            "note": f"step failed: access_email — {note}",
            "failed_step": "access_email",
            "steps": steps,
            "source": "fullscreen_vision",
        }
    time.sleep(1.5)

    # 4. Read revealed email
    print("  [apollo-vision] STEP 4/4: read revealed email")
    read = _read_email_from_screen()
    steps.append({
        "step": "read_email",
        "clicked": True,
        "path": "vision_read",
        "format_ok": read.get("format_ok"),
        "note": read.get("note"),
        "masked": mask_email(read.get("email") or read.get("raw") or ""),
    })

    out = {
        "found": bool(read.get("found")),
        "note": read.get("note") or "",
        "steps": steps,
        "source": "fullscreen_vision",
        "format_ok": read.get("format_ok"),
    }
    if read.get("email") is not None:
        out["email"] = read["email"]
    if read.get("raw") and not read.get("found"):
        out["email"] = read.get("raw")  # raw for confirm/paste path
        out["raw"] = read.get("raw")
    return out


if __name__ == "__main__":
    import sys

    task = (sys.argv[1] if len(sys.argv) > 1 else "tile4").strip().lower()

    # --- Tile-based vision scan (Tasks 1-4) ---
    if task in ("tile1", "t1", "split"):
        print("=" * 60)
        print("SELF-TEST TASK 1 — split_into_tiles()")
        print("Prereq: Apollo panel visible on screen")
        print("=" * 60)
        full_path, meta = capture_fullscreen_raw_no_focus()
        tiles = split_into_tiles(full_path)
        print()
        print(f"--- {len(tiles)} tiles saved under {_TILE_DIR}/ ---")
        print(f"full capture: {full_path} ({meta.get('width')}x{meta.get('height')})")
        print("Eyeball tiles — Apollo panel should appear WHOLE in at least one tile.")
        print("\nSTOP after Task 1.")

    elif task in ("tile2", "t2", "scan"):
        print("=" * 60)
        print("SELF-TEST TASK 2 — find_target_by_tile_scan()")
        print("Prereq: Apollo panel open, Access email button visible")
        print("=" * 60)
        full_path, meta = capture_fullscreen_raw_no_focus()
        tiles = split_into_tiles(full_path)
        intent = (
            "the 'Access email' button in the Apollo.io contact panel"
        )
        scan = find_target_by_tile_scan(
            intent, tiles, full_image_path=full_path, meta=meta
        )
        print()
        print("--- result ---")
        print(f"found: {scan.get('found')}")
        print(f"tile_index: {scan.get('tile_index')}")
        print(f"screen: ({scan.get('screen_x')}, {scan.get('screen_y')})")
        print(f"why: {scan.get('why')}")
        v = scan.get("verify") or {}
        print(f"verify is_target: {v.get('is_target')}")
        print(f"verify what_you_see: {v.get('what_you_see')}")
        print(f"verify crop: {v.get('verify_path')}")
        print("Eyeball verification crop — should show Access email button.")
        print("\nSTOP after Task 2.")

    elif task in ("tile3", "t3", "click"):
        print("=" * 60)
        print("SELF-TEST TASK 3 — tile-scan Access email click (live)")
        print("Prereq: Apollo panel open, Access email NOT yet clicked")
        print("WATCH THE SCREEN")
        print("=" * 60)
        result = click_access_email_in_apollo()
        print()
        print("--- result ---")
        print(f"clicked: {result.get('clicked')}")
        print(f"path: {result.get('path')}")
        print(f"tile_index: {result.get('tile_index')}")
        print(f"tile_count: {result.get('tile_count')}")
        print(f"screen: ({result.get('screen_x')}, {result.get('screen_y')})")
        v = result.get("verify") or {}
        print(f"verify is_target: {v.get('is_target')}")
        print(f"verify crop: {v.get('verify_path')}")
        print(f"note: {result.get('note')}")
        print(f"revealed: {result.get('revealed')}")
        print(f"click_attempts: {result.get('click_attempts')}")
        det = result.get("reveal_detect") or {}
        print(f"reveal still_access_button: {det.get('still_shows_access_button')}")
        print(f"reveal email_visible: {det.get('email_visible')}")
        print(f"reveal email (masked): {mask_email(result.get('email') or det.get('email') or '')}")
        print(f"reveal what_you_see: {det.get('what_you_see')!r}")
        print(f"detect crop: {det.get('detect_path')}")
        print("\nSTOP after Task 3.")

    elif task in ("tile5", "t5", "detect", "d1"):
        print("=" * 60)
        print("SELF-TEST TASK 1 — reveal_detected() BEFORE/AFTER manual click")
        print("Prereq: Apollo panel open; Access email button visible.")
        print("=" * 60)

        full_path, meta = capture_fullscreen_raw_no_focus()
        tiles = split_into_tiles(full_path)
        intent = (
            "the 'Access email' or 'Show email' or 'reveal email' button "
            "in the Apollo.io contact panel"
        )
        scan = find_target_by_tile_scan(
            intent,
            tiles,
            full_image_path=full_path,
            meta=meta,
            zoom_factor=2,
            verify=True,
        )
        if not scan.get("found"):
            print(f"tile scan failed: {scan.get('why')}")
            print("\nSTOP after Task 1.")
        else:
            sx, sy = int(scan["screen_x"]), int(scan["screen_y"])
            print(f"anchor to test: ({sx},{sy}) tile #{scan.get('tile_index')}")
            det1 = reveal_detected(
                (sx, sy),
                full_image_path=full_path,
                meta=meta,
                save_path=_REVEAL_DETECT_CROP_PATH,
            )
            print(
                f"before click: revealed={det1.get('revealed')} still_access_button={det1.get('still_shows_access_button')}"
            )
            print(f"before what_you_see: {det1.get('what_you_see')!r}")
            input(
                "NOW manually click the Access email button on screen. "
                "Press Enter after the email is revealed. "
            )
            det2 = reveal_detected((sx, sy))
            print(
                f"after click: revealed={det2.get('revealed')} still_access_button={det2.get('still_shows_access_button')}"
            )
            print(f"after what_you_see: {det2.get('what_you_see')!r}")
            print(f"saved detector crop: {_REVEAL_DETECT_CROP_PATH}")
            print("\nSTOP after Task 1.")

    elif task in ("tile6", "t6", "read", "d2"):
        print("=" * 60)
        print("SELF-TEST TASK 3 — read revealed email after agent reveal detection")
        print("Prereq: Apollo panel open; Access email visible.")
        print("=" * 60)
        result_click = click_access_email_in_apollo()
        print()
        print(f"click revealed: {result_click.get('revealed')}")
        if not result_click.get("revealed"):
            print("Reveal was not detected; aborting read step.")
            print("\nSTOP after Task 3.")
        else:
            ax = result_click.get("screen_x")
            ay = result_click.get("screen_y")
            read = read_revealed_email_cascade(
                access_click_screen=(int(ax), int(ay)),
            )
            em = read.get("email") or read.get("raw") or ""
            print()
            print("--- read result ---")
            print(f"found: {read.get('found')}")
            print(f"read_layer: {read.get('read_layer') or read.get('source')}")
            print(f"format_ok: {read.get('format_ok')}")
            print(f"email (masked): {mask_email(em) if em else '(none)'}")
            print(f"note: {read.get('note')}")
            print(f"saved read crop: {_CROP_REVEAL_PATH}")
            print("\nSTOP after Task 3.")

    elif task in ("tile4", "t4", "e2e", "4"):
        print("=" * 60)
        print("SELF-TEST TASK 4 — end-to-end through copy")
        print("extensions -> Apollo -> Access email -> read -> hover/copy icon")
        print("WATCH THE SCREEN")
        print("=" * 60)
        result = get_email_for_current_profile()
        print()
        print("--- result ---")
        print(f"found: {result.get('found')}")
        print(f"source: {result.get('source')}")
        print(f"read_layer: {result.get('read_layer')}")
        print(f"format_ok: {result.get('format_ok')}")
        print(f"copied: {result.get('copied')}")
        print(f"copy_note: {result.get('copy_note')}")
        print(f"needs_confirm: {result.get('needs_confirm')}")
        em = result.get("email") or result.get("raw") or ""
        print(f"email (masked): {mask_email(em) if em else '(none)'}")
        print(f"note: {result.get('note')}")
        if result.get("steps"):
            print("--- steps ---")
            for s in result["steps"]:
                print(f"  {s.get('step')}: {s}")
        print(
            f"\nSaved: tiles in {_TILE_DIR}/, verify {_TILE_VERIFY_PATH}, "
            f"read {_CROP_REVEAL_PATH}, copy hover {_COPY_HOVER_PATH}"
        )
        print("\nSTOP after Task 4.")

    # --- SoM text match tests (legacy) ---
    elif task in ("som1", "text1", "matcher"):
        print("=" * 60)
        print("SELF-TEST TASK 1 — find_element_by_text()")
        print("=" * 60)
        fake = [
            {"id": 70, "name": "Access email button", "rect": (600, 200, 700, 230), "cx": 650, "cy": 215},
            {"id": 71, "name": "Access email & phone", "rect": (600, 240, 750, 270), "cx": 675, "cy": 255},
            {"id": 5, "name": "Apollo.io: Free B2B Phone Number & Email", "rect": (0, 0, 10, 10), "cx": 5, "cy": 5},
        ]
        match, all_m = find_element_by_text(fake, _ACCESS_EMAIL_TEXT_PATTERNS)
        print(f"\nbest match: #{match.get('id') if match else None} "
              f"{match.get('name') if match else None!r}")
        print(f"all candidates ({len(all_m)}):")
        for c in all_m:
            print(f"  #{c.get('id')} {c.get('name')!r} pattern={c.get('pattern')!r}")
        ok = match and match.get("id") == 70
        print(f"\nTask 1 {'PASS' if ok else 'FAIL'} — expected #70 plain Access email")
        print("\nSTOP after Task 1.")

    elif task in ("som2", "text2", "access"):
        print("=" * 60)
        print("SELF-TEST TASK 2 — Access email SoM text click (live)")
        print("Prereq: Apollo panel open, Access email button visible")
        print("WATCH THE SCREEN")
        print("=" * 60)
        result = click_access_email_in_apollo()
        print()
        print("--- result ---")
        print(f"element_count: {result.get('element_count')}")
        print(f"clicked: {result.get('clicked')}")
        print(f"path: {result.get('path')}")
        print(f"element_id: {result.get('element_id')}")
        print(f"element_name: {result.get('element_name')!r}")
        print(f"note: {result.get('note')}")
        cands = result.get("candidates") or []
        print(f"candidates ({len(cands)}):")
        for c in cands[:10]:
            print(f"  #{c.get('id')} {c.get('name')!r} pattern={c.get('pattern')!r}")
        print("\nSTOP after Task 2.")

    elif task in ("som3", "text3", "read"):
        print("=" * 60)
        print("SELF-TEST TASK 3 — read email (SoM text first, then crop)")
        print("Prereq: Access email already clicked, email visible")
        print("=" * 60)
        elements, _ = capture_fullscreen_no_focus()
        # Try to recover Access-email element as crop anchor
        anchor_match, _ = find_element_by_text(elements, _ACCESS_EMAIL_TEXT_PATTERNS)
        anchor = anchor_match["element"] if anchor_match else None
        read = _read_email_som_then_crop(matched_element=anchor)
        print()
        print("--- result ---")
        print(f"read_layer: {read.get('read_layer') or read.get('source')}")
        print(f"found: {read.get('found')}")
        print(f"format_ok: {read.get('format_ok')}")
        em = read.get("email") or read.get("raw") or ""
        print(f"email (masked): {mask_email(em) if em else '(none)'}")
        print(f"note: {read.get('note')}")
        print(f"read crop: {_CROP_REVEAL_PATH}")
        print("\nSTOP after Task 3.")

    elif task in ("som4", "e2e", "4"):
        print("=" * 60)
        print("SELF-TEST TASK 4 — end-to-end (SoM text Access email + read cascade)")
        print("WATCH THE SCREEN")
        print("=" * 60)
        result = get_email_for_current_profile()
        print()
        print("--- result ---")
        print(f"found: {result.get('found')}")
        print(f"source: {result.get('source')}")
        print(f"read_layer: {result.get('read_layer')}")
        print(f"format_ok: {result.get('format_ok')}")
        print(f"needs_confirm: {result.get('needs_confirm')}")
        em = result.get("email") or result.get("raw") or ""
        print(f"email (masked): {mask_email(em) if em else '(none)'}")
        print(f"note: {result.get('note')}")
        if result.get("steps"):
            print("--- steps ---")
            for s in result["steps"]:
                print(f"  {s.get('step')}: {s}")
        print("\nSTOP after Task 4.")

    # --- Legacy panel-targeting tests ---
    elif task in ("panel1", "region", "p1"):
        print("=" * 60)
        print("SELF-TEST TASK 1 — locate_apollo_panel_region()")
        print("Prereq: Apollo panel open on screen")
        print("=" * 60)
        result = locate_apollo_panel_region()
        print()
        print("--- result ---")
        print(f"found: {result.get('found')}")
        print(f"image_box: {result.get('image_box')}")
        print(f"screen_box: {result.get('screen_box')}")
        print(f"crop_path: {result.get('crop_path')}")
        print(f"note: {result.get('note')}")
        print(f"why: {result.get('why')}")
        print("\nEyeball the saved panel crop PNG.")
        print("\nSTOP after Task 1.")

    elif task in ("panel2", "access", "p2"):
        print("=" * 60)
        print("SELF-TEST TASK 2 — find Access email IN panel + verify + click")
        print("Prereq: Apollo panel open, Access email NOT yet clicked")
        print("WATCH THE SCREEN")
        print("=" * 60)
        raw_path, meta = capture_fullscreen_raw_no_focus()
        panel = locate_apollo_panel_region(raw_path, meta)
        print(f"\npanel found: {panel.get('found')}")
        print(f"panel screen_box: {panel.get('screen_box')}")
        print(f"panel crop: {panel.get('crop_path')}")
        if not panel.get("found"):
            print(f"note: {panel.get('note')}")
            print("\nSTOP after Task 2.")
        else:
            target = find_access_email_in_panel(panel, raw_path=raw_path, meta=meta)
            print(f"\ntarget found: {target.get('found')}")
            print(f"proposed screen: ({target.get('screen_x')}, {target.get('screen_y')})")
            verify = target.get("verify") or {}
            print(f"verify is_target: {verify.get('is_target')}")
            print(f"verify what_you_see: {verify.get('what_you_see')}")
            print(f"verify crop: {verify.get('verify_path')}")
            if target.get("found"):
                ok, msg = _click_screen_no_refocus(
                    int(target["screen_x"]), int(target["screen_y"])
                )
                print(f"clicked: {ok} ({msg})")
            else:
                print(f"note: {target.get('note')}")
                if target.get("candidates"):
                    for c in target["candidates"]:
                        v = c.get("verify") or {}
                        print(
                            f"  rejected {c.get('path')} ({c.get('screen_x')},{c.get('screen_y')}): "
                            f"is_target={v.get('is_target')} what={v.get('what_you_see')!r}"
                        )
            print("\nEyeball panel crop + verify crop PNGs.")
            print("\nSTOP after Task 2.")

    elif task in ("panel3", "readcrop", "p3"):
        print("=" * 60)
        print("SELF-TEST TASK 3 — panel-based read crop (after Access email clicked)")
        print("Prereq: Access email already clicked, email visible in Apollo panel")
        print("=" * 60)
        time.sleep(0.3)
        raw_path, meta = capture_fullscreen_raw_no_focus()
        panel = locate_apollo_panel_region(raw_path, meta)
        if not panel.get("found"):
            print(f"panel not found: {panel.get('note')}")
            print("\nSTOP after Task 3.")
        else:
            read = _read_email_vision_crop(
                panel_image_box=panel.get("image_box"),
                panel_screen_box=panel.get("screen_box"),
            )
            print()
            print("--- result ---")
            print(f"found: {read.get('found')}")
            print(f"format_ok: {read.get('format_ok')}")
            em = read.get("email") or read.get("raw") or ""
            print(f"email (masked): {mask_email(em) if em else '(none)'}")
            print(f"read crop: {_CROP_REVEAL_PATH}")
            print(f"note: {read.get('note')}")
            print("\nEyeball the read crop PNG.")
            print("\nSTOP after Task 3.")

    elif task in ("panel4", "e2e", "p4", "4"):
        print("=" * 60)
        print("SELF-TEST TASK 4 — end-to-end (panel targeting + read cascade)")
        print("Extensions -> Apollo -> panel Access email -> DOM/vision-crop/scroll")
        print("WATCH THE SCREEN")
        print("=" * 60)
        result = get_email_for_current_profile()
        print()
        print("--- result ---")
        print(f"found: {result.get('found')}")
        print(f"source: {result.get('source')}")
        print(f"read_layer: {result.get('read_layer')}")
        print(f"format_ok: {result.get('format_ok')}")
        print(f"needs_confirm: {result.get('needs_confirm')}")
        em = result.get("email") or result.get("raw") or ""
        print(f"email (masked): {mask_email(em) if em else '(none)'}")
        print(f"note: {result.get('note')}")
        if result.get("steps"):
            print("--- steps ---")
            for s in result["steps"]:
                print(f"  {s.get('step')}: {s}")
        print("\nSaved crops: panel, verify, read — eyeball if miss.")
        print("\nSTOP after Task 4.")

    # --- Earlier tasks (attach / dom) ---
    elif task in ("attach", "cdp"):
        from email_workflow_automation.browser_util import active_page_info, cdp_debug_info

        print("=" * 60)
        print("SELF-TEST TASK 1 — Playwright CDP attach")
        print("=" * 60)
        ok, detail = cdp_debug_info()
        print(f"CDP endpoint: ok={ok}\n  {detail}")
        if ok:
            info = active_page_info()
            print(f"\nPlaywright attach: ok={info.get('ok')}")
            if info.get("ok"):
                print("  Playwright attached and can see the active page:")
                print(f"  title: {info.get('title')!r}")
                print(f"  url:   {info.get('url')!r}")
            else:
                print(f"  exact error: {info.get('note')}")
        else:
            print("\nSTART debug Chrome (--remote-debugging-port=9222), then re-run.")
        print("\nSTOP after Task 1.")

    elif task in ("dom",):
        print("=" * 60)
        print("SELF-TEST TASK 2 — read_email_from_dom()")
        print("Prereq: Access email already clicked, email visible on screen")
        print("=" * 60)
        result = read_email_from_dom()
        print()
        print("--- result ---")
        print(f"found: {result.get('found')}")
        em = result.get("email") or ""
        print(f"email (masked): {mask_email(em) if em else '(none)'}")
        print(f"frame: {result.get('frame', '(n/a)')}")
        print(f"note: {result.get('note')}")
        print("\nSTOP after Task 2.")

    elif task in ("cascade", "old3"):
        print("=" * 60)
        print("SELF-TEST TASK 3 — full flow + read cascade")
        print("Extensions -> Apollo -> Access email -> DOM/vision-crop/scroll")
        print("WATCH THE SCREEN")
        print("=" * 60)
        result = get_email_via_extensions_dropdown()
        result = _apply_vision_email_result(result, "extensions_dropdown")
        print()
        print("--- result ---")
        print(f"found: {result.get('found')}")
        print(f"read_layer: {result.get('read_layer')}")
        print(f"format_ok: {result.get('format_ok')}")
        print(f"needs_confirm: {result.get('needs_confirm')}")
        em = result.get("email") or result.get("raw") or ""
        print(f"email (masked): {mask_email(em) if em else '(none)'}")
        print(f"note: {result.get('note')}")
        print("\nSTOP after Task 3.")

    elif task in ("old4",):
        print("=" * 60)
        print("SELF-TEST — get_email_for_current_profile() (legacy entry)")
        print("WATCH THE SCREEN")
        print("=" * 60)
        result = get_email_for_current_profile()
        print()
        print("--- result ---")
        print(f"found: {result.get('found')}")
        print(f"source: {result.get('source')}")
        print(f"read_layer: {result.get('read_layer')}")
        print(f"format_ok: {result.get('format_ok')}")
        print(f"needs_confirm: {result.get('needs_confirm')}")
        em = result.get("email") or result.get("raw") or ""
        print(f"email (masked): {mask_email(em) if em else '(none)'}")
        print(f"note: {result.get('note')}")
        if result.get("steps"):
            print("--- steps ---")
            for s in result["steps"]:
                print(f"  {s.get('step')}: {s}")
        print("\nSTOP.")

    else:
        print("Unknown task. Use: tile1, tile2, tile3, tile4 (or som1..som4, attach, dom)")
