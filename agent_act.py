"""
Stage B step 3: ACT - route the model's chosen action to real tools.

Maps the closed-vocabulary action to code that already exists:
  click      -> pyautogui / playwright click the chosen numbered element
  type       -> keyboard send the text
  press      -> keyboard send a key
  scroll     -> pyautogui scroll
  navigate   -> Playwright page.goto(url) via CDP
  switch_tab -> bring matching Chrome tab to front
  copy/paste -> Ctrl+C / Ctrl+V
  wait       -> short sleep (capped)
  hotkey     -> send_keys chord
The reasoning already chose; acting just routes it to the crew.
"""

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import time
import pyautogui
from pywinauto.keyboard import send_keys


def _coerce_id(val):
    """The model sometimes returns the id as a string; make it an int."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _escape_for_send_keys(text):
    """Wrap pywinauto special chars in {} so they type literally."""
    special = set("^+%~(){}[]")
    out = []
    for ch in text:
        if ch in special:
            out.append("{" + ch + "}")
        else:
            out.append(ch)
    return "".join(out)


def _refocus_target(target_procs, title_hint=None):
    """Re-focus the target app window right before acting, because the terminal
    approval prompt steals focus back to PowerShell. Beats that focus-steal.
    Prefer title_hint when multiple windows match (e.g. several Chrome windows)."""
    if not target_procs:
        return
    try:
        from prereq_reasoner import focus_app
        focus_app(target_procs, title_hint=title_hint)
    except Exception:
        pass


def _looks_like_omnibox_element(el):
    """True if an accessibility/DOM element looks like Chrome's address bar."""
    name = (el.get("name") or "").lower()
    return (
        "omnibox" in name
        or "address and search" in name
        or (name.startswith("address") and "bar" in name)
    )


def _is_address_bar_focused():
    """Detect Chrome omnibox focus via UI Automation."""
    try:
        from agent_loop import _is_address_bar_focused as _check
        return _check()
    except Exception:
        pass
    try:
        from pywinauto.uia_defines import IUIA
        el = IUIA().iuia.GetFocusedElement()
        name = (el.CurrentName or "").lower()
        aid = (el.CurrentAutomationId or "").lower()
        if any(k in name for k in ("address and search", "address bar", "omnibox")):
            return True
        if "omnibox" in aid:
            return True
    except Exception:
        pass
    return False


def _refocus_page_body(elements):
    """Move focus out of the omnibox into the page body / main editable."""
    # Prefer CDP: blur + click viewport center (never opens a new Chrome)
    browser_els = [e for e in elements if e.get("browser")]
    if browser_els:
        try:
            page = _get_browser_page()
            if page is not None:
                try:
                    page.evaluate(
                        "() => { try { document.activeElement && document.activeElement.blur(); } catch (e) {} }"
                    )
                except Exception:
                    pass
                try:
                    vp = page.viewport_size or {"width": 800, "height": 600}
                    page.mouse.click(max(10, vp["width"] // 2), max(10, vp["height"] // 2))
                except Exception:
                    page.mouse.click(400, 300)
                print("  [type] re-focused page body via CDP (left address bar)")
                time.sleep(0.25)
                return True
        except Exception as e:
            print(f"  [type] CDP body re-focus failed: {e}")

    # Native fallback: click largest Document/Edit that is not the omnibox
    editables = [
        e for e in elements
        if e.get("control_type") in ("Document", "Edit")
        and not _looks_like_omnibox_element(e)
    ]
    if editables:
        def _area(el):
            L, T, R, B = el["rect"]
            return max(0, R - L) * max(0, B - T)
        target = max(editables, key=_area)
        try:
            pyautogui.click(target["cx"], target["cy"])
            print(f"  [type] re-focused page body via click on "
                  f"{target.get('control_type')} '{target.get('name', '')[:40]}'")
            time.sleep(0.25)
            return True
        except Exception as e:
            print(f"  [type] body re-focus click failed: {e}")
    return False


def _get_browser_page():
    """Return the active CDP page, or None if Playwright/Chrome unavailable."""
    try:
        from browser_perceive import get_active_page
        page = get_active_page()
        if page is not None:
            return page
    except Exception:
        pass
    try:
        from browser_locator import connect_browser, _active_page
        if connect_browser():
            return _active_page()
    except Exception:
        pass
    return None


def _scroll_into_view_if_needed(page, selector):
    """Bring a browser element into view before click/type. Best-effort."""
    if not page or not selector:
        return
    try:
        page.locator(selector).scroll_into_view_if_needed(timeout=3000)
    except Exception as e:
        print(f"  [act] scroll_into_view skipped ({e})")


def _browser_scroll_to_find(page, hint):
    """Find a DOM node whose text/aria-label contains hint; scroll it into view.

    Prefers headings and links with a short matching label over huge containers.
    Returns True if an element was found and scrolled into view.
    """
    if not page or not (hint or "").strip():
        return False
    hint = hint.strip()

    # 1) Prefer compact DOM matches (headings / links / aria-label)
    try:
        found = page.evaluate(
            """(needle) => {
                const n = String(needle || '').toLowerCase();
                if (!n) return false;
                const maxLabel = Math.max(80, n.length * 8);
                const nodes = document.querySelectorAll(
                    'h1,h2,h3,h4,h5,h6,a,button,[role="heading"],[role="link"],'
                    + '[aria-label],span,li,p,div,label'
                );
                const scored = [];
                for (const el of nodes) {
                    const aria = (el.getAttribute('aria-label') || '')
                        .replace(/\\s+/g, ' ').trim().toLowerCase();
                    let text = (el.innerText || el.textContent || '')
                        .replace(/\\s+/g, ' ').trim().toLowerCase();
                    if (text.length > 240) text = text.slice(0, 240);
                    const ariaHit = aria && aria.includes(n);
                    const textHit = text && text.includes(n) && text.length <= maxLabel;
                    if (!ariaHit && !textHit) continue;
                    const tag = el.tagName.toLowerCase();
                    let score = 0;
                    if (['h1','h2','h3','h4','h5','h6'].includes(tag)
                        || el.getAttribute('role') === 'heading') score += 100;
                    if (tag === 'a' || el.getAttribute('role') === 'link') score += 50;
                    if (tag === 'button' || el.getAttribute('role') === 'button') score += 40;
                    const label = ariaHit ? aria : text;
                    if (label === n) score += 30;
                    else if (label.startsWith(n)) score += 15;
                    score -= Math.min(label.length, 120);
                    scored.push({el, score});
                }
                if (!scored.length) return false;
                scored.sort((a, b) => b.score - a.score);
                scored[0].el.scrollIntoView({block: 'center', inline: 'nearest'});
                return true;
            }""",
            hint,
        )
        if found:
            return True
    except Exception as e:
        print(f"  [scroll] to_find DOM scan failed: {e}")

    # 2) Playwright text locator fallback
    try:
        loc = page.get_by_text(hint, exact=False).first
        loc.scroll_into_view_if_needed(timeout=3000)
        return True
    except Exception:
        return False


def _browser_page_scroll(page, direction, amount=600):
    """Scroll the page document via CDP (reliable; does not need OS focus)."""
    delta = amount if direction == "down" else -amount
    page.evaluate(
        """(dy) => { window.scrollBy(0, dy); }""",
        delta,
    )
    try:
        y = page.evaluate("() => window.scrollY || window.pageYOffset || 0")
        print(f"  [scroll] page scrollY now {int(y)}")
    except Exception:
        pass


def _browser_click_element(page, el, eid):
    """Click a browser element via Playwright selector; fall back to vx,vy."""
    sel = el.get("selector")
    if sel:
        _scroll_into_view_if_needed(page, sel)
        try:
            page.click(sel, timeout=5000)
            return True, (
                f"clicked box {eid} (browser selector): "
                f"{el.get('control_type')} '{el.get('name')}'"
            )
        except Exception as e:
            print(f"  [click] selector click failed ({e}); trying mouse coords")
    vx = el.get("vx", el.get("cx"))
    vy = el.get("vy", el.get("cy"))
    page.mouse.click(vx, vy)
    return True, (
        f"clicked box {eid} (browser coords): "
        f"{el.get('control_type')} '{el.get('name')}'"
    )


def _pick_browser_type_target(page, elements):
    """Choose a browser field to type into (selector-based).

    Prefers the focused data-mimic-id element, then Edit/Document/ComboBox
    with a selector (Google search is often a ComboBox).
    """
    by_id = {e.get("id"): e for e in elements if e.get("browser") and e.get("selector")}
    # 1) Currently focused tagged element
    try:
        focused_id = page.evaluate(
            """() => {
                const a = document.activeElement;
                if (!a) return null;
                const id = a.getAttribute('data-mimic-id');
                if (id) return parseInt(id, 10);
                const tagged = a.closest('[data-mimic-id]');
                return tagged ? parseInt(tagged.getAttribute('data-mimic-id'), 10) : null;
            }"""
        )
        if focused_id in by_id:
            return by_id[focused_id]
    except Exception:
        pass
    # 2) Editable-ish controls with selectors
    editable_types = ("Document", "Edit", "ComboBox")
    candidates = [
        e for e in elements
        if e.get("browser") and e.get("selector")
        and e.get("control_type") in editable_types
        and not _looks_like_omnibox_element(e)
    ]
    if not candidates:
        return None

    def _score(el):
        name = (el.get("name") or "").lower()
        L, T, R, B = el["rect"]
        area = max(0, R - L) * max(0, B - T)
        bonus = 0
        if any(k in name for k in ("search", "query", "find", "search google")):
            bonus += 10_000_000
        if el.get("control_type") == "Edit":
            bonus += 1000
        return bonus + area

    return max(candidates, key=_score)


def _browser_type_into(page, el, text, mode):
    """Type into a browser field via fill (replace) or click+keyboard (append)."""
    sel = el.get("selector")
    if not sel:
        return False, "no selector"
    _scroll_into_view_if_needed(page, sel)
    try:
        if mode == "append":
            page.click(sel, timeout=5000)
            page.keyboard.press("End")
            page.keyboard.type(text, delay=20)
        elif mode == "as-is":
            page.click(sel, timeout=5000)
            page.keyboard.type(text, delay=20)
        else:
            # replace (default): fill focuses and sets value
            try:
                page.fill(sel, text, timeout=5000)
            except Exception:
                # Some controls (combobox) reject fill — click + select-all + type
                page.click(sel, timeout=5000)
                page.keyboard.press("Control+a")
                page.keyboard.type(text, delay=20)
        return True, (
            f'typed "{text}" (browser selector {sel}, mode={mode}) '
            f"into {el.get('control_type')} '{el.get('name')}'"
        )
    except Exception as e:
        return False, f"browser type failed: {e}"


def do_action(action, elements, target_procs=None, title_hint=None):
    """Perform one action dict. Returns (ok, message).
    title_hint helps focus the correct window when several match."""
    kind = action.get("action")

    _refocus_target(target_procs, title_hint=title_hint)

    if kind == "click":
        eid = _coerce_id(action.get("id"))
        match = next((e for e in elements if e["id"] == eid), None)
        if not match:
            return False, f"no element with id {action.get('id')}"
        # Browser: prefer Playwright page.click(selector); coords only as fallback
        if match.get("browser"):
            try:
                page = _get_browser_page()
                if page is None:
                    return False, "browser click failed: no active page"
                return _browser_click_element(page, match, eid)
            except Exception as e:
                return False, f"browser click failed: {e}"
        try:
            pyautogui.click(match["cx"], match["cy"])
            return True, f"clicked box {eid}: {match['control_type']} '{match['name']}'"
        except Exception as e:
            return False, f"click failed: {e}"

    if kind == "type":
        text_missing = "text" not in action
        text = "" if text_missing else (action.get("text") or "")
        mode = action.get("type_mode", "replace")

        # Browser path: fill/click via data-mimic-id selector (reliable)
        browser_els = [e for e in elements if e.get("browser")]
        if browser_els:
            page = _get_browser_page()
            if page is not None:
                target = _pick_browser_type_target(page, elements)
                if target and target.get("selector"):
                    ok, msg = _browser_type_into(page, target, text, mode)
                    if ok:
                        if text_missing:
                            return True, f'typed "" (mode={mode}; text missing; browser)'
                        return True, msg
                    print(f"  [type] {msg}; falling back to keyboard send_keys")
                else:
                    print("  [type] no browser selector target; falling back to send_keys")

        # Never let free-text type land in the address bar / omnibox.
        if _is_address_bar_focused():
            print("  [type] address bar focused — moving focus to page body first")
            _refocus_page_body(elements)
        # Native / fallback: click editable then send_keys
        editables = [
            e for e in elements
            if e.get("control_type") in ("Document", "Edit", "ComboBox")
            and not _looks_like_omnibox_element(e)
        ]
        if editables:
            def _area(el):
                L, T, R, B = el["rect"]
                return max(0, R - L) * max(0, B - T)
            target = max(editables, key=_area)
            if target.get("browser") and target.get("selector"):
                try:
                    page = _get_browser_page()
                    if page is not None:
                        _scroll_into_view_if_needed(page, target["selector"])
                        page.click(target["selector"], timeout=5000)
                        time.sleep(0.2)
                    else:
                        pyautogui.click(target["cx"], target["cy"])
                        time.sleep(0.2)
                except Exception:
                    pyautogui.click(target["cx"], target["cy"])
                    time.sleep(0.2)
            elif target.get("browser"):
                try:
                    page = _get_browser_page()
                    if page is not None:
                        page.mouse.click(target.get("vx", target["cx"]),
                                         target.get("vy", target["cy"]))
                        time.sleep(0.2)
                    else:
                        pyautogui.click(target["cx"], target["cy"])
                        time.sleep(0.2)
                except Exception:
                    pyautogui.click(target["cx"], target["cy"])
                    time.sleep(0.2)
            else:
                pyautogui.click(target["cx"], target["cy"])
                time.sleep(0.2)
        elif any(e.get("browser") for e in elements):
            if _is_address_bar_focused():
                _refocus_page_body(elements)
        if mode == "replace":
            send_keys("^a")
        elif mode == "append":
            send_keys("^{END}")
        send_keys(_escape_for_send_keys(text), with_spaces=True)
        if text_missing:
            return True, f'typed "" (mode={mode}; text missing)'
        return True, f'typed "{text}" (mode={mode})'

    if kind == "press":
        if not action.get("key"):
            return False, "press with no key"
        key = str(action.get("key")).lower()
        keymap = {"enter": "{ENTER}", "tab": "{TAB}", "esc": "{ESC}",
                  "escape": "{ESC}", "space": "{SPACE}", "backspace": "{BACKSPACE}"}
        send_keys(keymap.get(key, key))
        return True, f"pressed {key}"

    if kind == "scroll":
        direction = action.get("direction") or "down"
        to_find = (action.get("to_find") or "").strip()

        # Prefer browser_perceive.get_active_page (same picker as perceive)
        page = None
        try:
            from browser_perceive import get_active_page
            page = get_active_page()
        except Exception:
            page = None
        if page is None:
            page = _get_browser_page()

        if to_find and page is not None:
            if _browser_scroll_to_find(page, to_find):
                return True, f"scrolled '{to_find}' into view"
            # Not in DOM yet (lazy content) — incremental page scroll for next perceive
            try:
                _browser_page_scroll(page, direction, amount=700)
                return True, f"scrolled {direction}, searching for '{to_find}'"
            except Exception as e:
                print(f"  [scroll] page scroll failed ({e}); falling back to OS scroll")

        if page is not None and not to_find:
            # Browser: scroll the document via CDP so the next perceive sees new content
            try:
                _browser_page_scroll(page, direction, amount=600)
                return True, f"scrolled {direction}"
            except Exception as e:
                print(f"  [scroll] page scroll failed ({e}); falling back to OS scroll")

        amount = -400 if direction == "down" else 400
        pyautogui.scroll(amount)
        if to_find:
            return True, f"scrolled {direction}, searching for '{to_find}'"
        return True, f"scrolled {direction}"

    if kind == "navigate":
        url = (action.get("url") or "").strip()
        if not url:
            return False, "navigate with no url"
        page = _get_browser_page()
        if page is None:
            return False, "no browser to navigate"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return True, f"navigated to {url}"
        except Exception as e:
            return False, f"navigate failed: {e}"

    if kind == "switch_tab":
        match_text = (action.get("match") or "").strip()
        if not match_text:
            return False, "switch_tab with no match"
        try:
            from browser_locator import connect_browser
            import browser_locator as bl
            if not connect_browser() or bl._browser is None:
                return False, "no browser to switch tabs"
            needle = match_text.lower()
            for ctx in bl._browser.contexts:
                for page in ctx.pages:
                    try:
                        title = (page.title() or "")
                        url = (page.url or "")
                        if needle in title.lower() or needle in url.lower():
                            page.bring_to_front()
                            return True, f"switched to tab '{title or url}'"
                    except Exception:
                        continue
            return False, f"no tab matches '{match_text}'"
        except Exception as e:
            return False, f"switch_tab failed: {e}"

    if kind == "copy":
        send_keys("^c")
        return True, "copied (Ctrl+C)"

    if kind == "paste":
        send_keys("^v")
        return True, "pasted (Ctrl+V)"

    if kind == "wait":
        try:
            seconds = float(action.get("seconds", 1))
        except (TypeError, ValueError):
            return False, "wait with invalid seconds"
        if seconds < 0:
            return False, "wait with negative seconds"
        capped = min(seconds, 10.0)
        time.sleep(capped)
        return True, f"waited {capped}s"

    if kind == "hotkey":
        keys = action.get("keys")
        if not keys:
            return False, "hotkey with no keys"
        try:
            send_keys(str(keys))
            return True, f"hotkey {keys}"
        except Exception as e:
            return False, f"hotkey failed: {e}"

    if kind == "done":
        return True, "done"

    if kind == "stuck":
        return False, f"stuck: {action.get('why')}"

    return False, f"unknown action {kind}"


if __name__ == "__main__":
    import sys
    from agent_loop import perceive
    from agent_reason import reason_next_action

    goal = sys.argv[1] if len(sys.argv) > 1 else "open the View menu"
    print(f"=== Stage B step 3: full PERCEIVE -> REASON -> ACT for '{goal}' ===")
    elements, path, _page_info = perceive()
    print(f"perceived {len(elements)} elements")
    action = reason_next_action(goal, elements, path)
    print(f"proposed: {action}")

    confirm = input("perform this action? (y/n): ").strip().lower()
    if confirm == "y":
        ok, msg = do_action(action, elements)
        print(f"{'OK' if ok else 'FAIL'}: {msg}")
    else:
        print("cancelled.")
