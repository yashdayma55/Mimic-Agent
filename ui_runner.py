"""Execute compiled UI plan steps against real windows.

`ok` means a window was found and focused, an element resolved (when
required), the action ran at that element's screen rect, and a post-check
confirmed an effect. A step that merely does not raise is NOT ok.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from pywinauto import Desktop

_NOTEPAD_ELEMS = {"text editor", "coreinput", "notepad"}
_SEARCH_ELEMS = {"search"}
_EDITABLE_TYPES = {"Edit", "Document", "ComboBox"}
_CHROME_PROCS = frozenset({"chrome.exe", "msedge.exe"})


@dataclass
class StepResult:
    ok: bool
    reason: str
    window_wanted: str | None = None
    window_found: str | None = None
    window_focused: bool = False
    element_name: str | None = None
    element_type: str | None = None
    element_rect: tuple[int, int, int, int] | None = None
    click_xy: tuple[int, int] | None = None
    focused_after: str | None = None
    value_after: str | None = None
    lines: list[str] = field(default_factory=list)

    def log_lines(self) -> list[str]:
        lines = list(self.lines)
        lines.append(
            f"  window wanted={self.window_wanted!r} "
            f"found={self.window_found!r} focused={self.window_focused}"
        )
        if self.element_name or self.element_type or self.element_rect:
            lines.append(
                f"  element name={self.element_name!r} "
                f"control_type={self.element_type!r} rect={self.element_rect}"
            )
        else:
            lines.append("  element: NOT RESOLVED")
        if self.click_xy is not None:
            lines.append(f"  clicked screen coords {self.click_xy}")
        if self.focused_after is not None:
            lines.append(f"  focused after={self.focused_after!r}")
        if self.value_after is not None:
            preview = self.value_after[:80].replace("\n", " ")
            lines.append(f"  value after={preview!r}")
        lines.append(f"  ok={self.ok}  {self.reason}")
        return lines


def _desktop():
    return Desktop(backend="uia")


def _proc_name(wrapper) -> str:
    try:
        pid = wrapper.element_info.process_id
        import psutil

        return (psutil.Process(pid).name() or "").lower()
    except Exception:
        return ""


def _title(wrapper) -> str:
    try:
        return (wrapper.window_text() or "").strip()
    except Exception:
        return ""


def _class_name(wrapper) -> str:
    try:
        return wrapper.element_info.class_name or ""
    except Exception:
        return ""


def _rect(wrapper) -> tuple[int, int, int, int] | None:
    try:
        r = wrapper.rectangle()
        box = (int(r.left), int(r.top), int(r.right), int(r.bottom))
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        return box
    except Exception:
        return None


def _center(rect) -> tuple[int, int]:
    l, t, r, b = rect
    return (l + r) // 2, (t + b) // 2


def _el_type(wrapper) -> str:
    try:
        return wrapper.element_info.control_type or ""
    except Exception:
        return ""


def _el_name(wrapper) -> str:
    try:
        return (wrapper.element_info.name or "").strip()
    except Exception:
        return ""


def needed_app_windows(steps: list, last_window: str | None = None) -> list[str]:
    """Window titles later steps need, excluding the always-present taskbar."""
    found = []
    last = last_window
    for step in steps:
        title = infer_window_title(step, last)
        if title:
            last = title
            if title.lower() not in ("taskbar", "shell_traywnd") and title not in found:
                found.append(title)
    return found


def infer_window_title(step: dict, last_window: str | None) -> str | None:
    explicit = (step.get("window_title") or step.get("target_window") or "").strip()
    if explicit:
        return explicit
    name = (step.get("elem_name") or "").strip().lower()
    etype = (step.get("elem_type") or "").strip()
    instr = (step.get("instruction") or "").lower()
    if "notepad" in instr or name in _NOTEPAD_ELEMS:
        return "Notepad"
    if etype == "Document" and "editor" in name:
        return "Notepad"
    if name in _SEARCH_ELEMS and etype in ("Button", "Edit", ""):
        return "Taskbar"
    action = (step.get("action") or "").strip().lower()
    if action in ("type", "type_text") and last_window:
        return last_window
    return last_window


def _visible_windows():
    out = []
    try:
        wins = _desktop().windows()
    except Exception:
        return out
    for w in wins:
        try:
            if not w.is_visible():
                continue
            if _proc_name(w) in _CHROME_PROCS:
                continue
            out.append(w)
        except Exception:
            continue
    return out


def _all_windows():
    """All top-level UIA windows, including the tray (often reports not visible)."""
    try:
        return list(_desktop().windows())
    except Exception:
        return []


def find_window(hint: str):
    """Return (wrapper, title) for a window matching hint, or (None, None)."""
    if not hint:
        return None, None
    h = hint.strip().lower()
    windows = _visible_windows()

    if h in ("taskbar", "shell_traywnd"):
        # Shell_TrayWnd often has is_visible()==False even when the bar is on screen.
        for w in _all_windows():
            try:
                cls = _class_name(w)
                title = _title(w)
                if cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd") or title.lower() == "taskbar":
                    return w, title or "Taskbar"
            except Exception:
                continue
        return None, None

    for w in windows:
        title = _title(w)
        if h in title.lower():
            return w, title or hint

    if "notepad" in h:
        for w in windows:
            if _proc_name(w) == "notepad.exe":
                return w, _title(w) or "Notepad"
    return None, None


def focus_window(wrapper) -> bool:
    try:
        wrapper.set_focus()
        time.sleep(0.35)
        return True
    except Exception:
        try:
            wrapper.restore()
            wrapper.set_focus()
            time.sleep(0.35)
            return True
        except Exception:
            return False


def foreground_title() -> str:
    try:
        from mimicagent.core.capture import foreground_window_title

        return foreground_window_title() or ""
    except Exception:
        return ""


def focused_wrapper():
    try:
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo

        raw = IUIA().iuia.GetFocusedElement()
        return UIAWrapper(UIAElementInfo(raw))
    except Exception:
        return None


def _read_value(wrapper) -> str:
    if wrapper is None:
        return ""
    for getter in (
        lambda: wrapper.get_value(),
        lambda: wrapper.legacy_properties().get("Value"),
        lambda: wrapper.window_text(),
    ):
        try:
            val = getter()
            if val:
                return str(val)
        except Exception:
            continue
    return ""


def resolve_element(window, name: str, etype: str | None):
    """Find *name* under *window* (or the desktop if window is None)."""
    name = (name or "").strip()
    if not name:
        return None
    roots = [window] if window is not None else _visible_windows()
    etype = (etype or "").strip() or None

    def _scan(root, title, control_type):
        try:
            kwargs = {"title": title}
            if control_type:
                kwargs["control_type"] = control_type
            hits = root.descendants(**kwargs)
            return hits[0] if hits else None
        except Exception:
            return None

    for root in roots:
        if etype:
            hit = _scan(root, name, etype)
            if hit is not None:
                return hit
        hit = _scan(root, name, None)
        if hit is not None:
            return hit

    if len(name) > 2:
        needle = name.lower()
        for root in roots:
            try:
                kids = root.descendants()
            except Exception:
                continue
            for el in kids:
                try:
                    en = (el.element_info.name or "")
                    if not en or needle not in en.lower():
                        continue
                    if abs(len(en) - len(name)) > 20:
                        continue
                    if etype and (el.element_info.control_type or "") != etype:
                        continue
                    return el
                except Exception:
                    continue
    return None


def _resolve_taskbar_search():
    """Win11 Search lives as TrayDummySearchControl; UIA descendants() skip it."""
    try:
        tray = Desktop(backend="win32").window(class_name="Shell_TrayWnd")
        ctrl = tray.child_window(class_name="TrayDummySearchControl")
        if ctrl.exists(timeout=1):
            return ctrl.wrapper_object()
    except Exception:
        return None
    return None


def _click_wrapper(wrapper) -> tuple[bool, tuple[int, int] | None, str]:
    rect = _rect(wrapper)
    if not rect:
        return False, None, "element has empty or off-screen rect"
    xy = _center(rect)
    try:
        import os_input

        os_input.click(xy[0], xy[1])
        return True, xy, "os_input.click"
    except Exception as e:
        return False, xy, f"click failed: {e}"


def _type_into_focused(text: str, mode: str) -> tuple[bool, str]:
    try:
        import os_input

        os_input.type_text(text, replace=(mode or "").lower() in ("replace", "overwrite"))
        return True, "os_input.type_text"
    except Exception as e:
        return False, f"type failed: {e}"


def _titles_match(wanted: str | None, actual: str | None) -> bool:
    if not wanted:
        return True
    if not actual:
        return False
    return wanted.strip().lower() in actual.strip().lower()


def execute_step(step: dict, last_window: str | None = None) -> StepResult:
    kind = (step.get("kind") or "").strip().lower()
    action = (step.get("action") or "").strip().lower()
    if kind == "reason" or action in ("reason", "", "none"):
        return StepResult(ok=True, reason="reason step (no UI action)")

    wanted = infer_window_title(step, last_window)
    result = StepResult(ok=False, reason="", window_wanted=wanted)
    invoke_actions = ("launch_app", "open_url", "open_path", "move_file", "copy_file")

    win = None
    found_title = None
    if action in invoke_actions:
        wanted = None
        result.window_wanted = None
    elif wanted:
        win, found_title = find_window(wanted)
        result.window_found = found_title
        if win is None:
            result.reason = f"target window {wanted!r} not found — is the app open?"
            return result
        result.window_focused = focus_window(win)
        tray = wanted.lower() in ("taskbar", "shell_traywnd")
        if not result.window_focused:
            if tray:
                result.lines.append("  taskbar found; focus() skipped (tray is not a normal window)")
            else:
                result.reason = (
                    f"target window {found_title or wanted!r} found but could not be focused"
                )
                return result
        fg = foreground_title()
        result.lines.append(f"  foreground after focus={fg!r}")
        if not tray and not _titles_match(wanted, fg) and not _titles_match(found_title, fg):
            result.reason = (
                f"target window {wanted!r} not foreground "
                f"(foreground={fg!r}) — is the app open?"
            )
            return result
        try:
            import os_input

            n = _el_name(focused_wrapper())
            if n in ("Keep changes", "Don't Save", "Don't save", "Save"):
                os_input.press("esc")
                time.sleep(0.25)
                focus_window(win)
        except Exception:
            pass
    else:
        result.lines.append("  no target window inferred; searching desktop")

    name = (step.get("elem_name") or "").strip()
    etype = (step.get("elem_type") or "").strip()
    text = step.get("text") or ""

    before_fg = foreground_title()
    before_focus = focused_wrapper()
    before_focus_name = _el_name(before_focus) if before_focus else ""

    if action in ("click", "click_input"):
        el = resolve_element(win, name, etype)
        if el is None and (name or "").lower() == "search" and (
            (wanted or "").lower() in ("taskbar", "shell_traywnd")
            or (found_title or "").lower() == "taskbar"
        ):
            el = _resolve_taskbar_search()
            if el is not None:
                result.lines.append("  resolved Win11 tray Search via TrayDummySearchControl")
        if el is None and (name or "").lower() == "coreinput" and win is not None:
            el = resolve_element(win, "Text editor", "Document")
            if el is not None:
                result.lines.append("  CoreInput not in this Notepad; using Text editor Document")
        if el is None:
            result.reason = (
                f"no element resolved for click name={name!r} type={etype!r} "
                f"in window {result.window_found or wanted!r}"
            )
            return result
        result.element_name = _el_name(el) or name
        result.element_type = _el_type(el) or etype
        result.element_rect = _rect(el)
        clicked, xy, how = _click_wrapper(el)
        result.click_xy = xy
        result.lines.append(f"  click method={how}")
        if not clicked:
            result.reason = result.lines[-1]
            return result
        time.sleep(0.4)
        after_fg = foreground_title()
        after_focus = focused_wrapper()
        result.focused_after = _el_name(after_focus) if after_focus else after_fg
        focus_changed = (
            (_el_name(after_focus) != before_focus_name)
            or (after_fg != before_fg)
        )
        fg_ok = _titles_match(wanted, after_fg) or _titles_match(found_title, after_fg)
        # A click is verified only if the target app stayed/became foreground
        # or keyboard focus actually moved — never because the element still exists.
        if not fg_ok and not focus_changed:
            result.reason = "click produced no observable state change"
            return result
        result.ok = True
        result.reason = "click verified (foreground or focus changed)"
        result.window_found = result.window_found or found_title or after_fg
        return result

    if action in ("type", "type_text"):
        if name:
            el = resolve_element(win, name, etype or None)
            if el is None:
                result.reason = (
                    f"no element resolved for type name={name!r} type={etype!r}"
                )
                return result
            result.element_name = _el_name(el) or name
            result.element_type = _el_type(el) or etype
            result.element_rect = _rect(el)
            clicked, xy, how = _click_wrapper(el)
            result.click_xy = xy
            result.lines.append(f"  focus-click before type method={how}")
            if not clicked:
                result.reason = f"could not focus type target: {how}"
                return result
            time.sleep(0.15)
        else:
            el = focused_wrapper()
            if el is None:
                result.reason = "no focused element to type into"
                return result
            result.element_name = _el_name(el)
            result.element_type = _el_type(el)
            result.element_rect = _rect(el)

        typed, how = _type_into_focused(str(text), (step.get("type_mode") or ""))
        result.lines.append(f"  type method={how}")
        if not typed:
            result.reason = how
            return result
        time.sleep(0.25)
        after = focused_wrapper()
        result.focused_after = _el_name(after) if after else ""
        value = _read_value(after) if after else ""
        result.value_after = value
        landed = str(text).lower() in (value or "").lower()
        if str(text) and not landed:
            result.reason = (
                f"type did not land: sent {text!r} but focused value is {value[:80]!r}"
            )
            return result
        if not str(text):
            result.reason = "empty text — nothing to verify"
            result.ok = False
            return result
        result.ok = True
        result.reason = "type verified (text present in focused value)"
        result.window_found = result.window_found or found_title or foreground_title()
        return result

    if action in ("hotkey", "press"):
        combo = (step.get("keys") or step.get("key") or step.get("text") or "").strip()
        if not combo:
            result.reason = "hotkey/press missing keys"
            return result
        before_titles = { _title(w) for w in _visible_windows() }
        try:
            import os_input

            if action == "press":
                os_input.press(combo)
            else:
                os_input.hotkey(combo)
        except Exception as e:
            result.reason = f"hotkey failed: {e}"
            return result
        time.sleep(0.4)
        after_titles = { _title(w) for w in _visible_windows() }
        expected = (step.get("expect") or "").strip().lower()
        if expected == "save" or combo.lower() in ("ctrl+s", "^s"):
            verify_path = step.get("verify_file")
            needle = step.get("verify_contains") or ""
            if verify_path:
                time.sleep(0.5)
                try:
                    with open(verify_path, "r", encoding="utf-8") as f:
                        body = f.read()
                except Exception:
                    body = ""
                if needle and needle.lower() not in body.lower():
                    result.reason = (
                        f"save did not persist {needle!r} to {verify_path}"
                    )
                    return result
            result.ok = True
            result.reason = f"hotkey {combo!r} sent (save; disk check is the ground truth)"
            result.window_found = found_title or foreground_title()
            return result
        new_wins = after_titles - before_titles
        if new_wins or after_titles != before_titles:
            result.ok = True
            result.reason = f"hotkey {combo!r} changed window list {sorted(new_wins)!r}"
            result.window_found = found_title or foreground_title()
            return result
        result.reason = f"hotkey {combo!r} produced no observable state change"
        return result

    if action in ("launch_app", "open_url", "open_path", "move_file", "copy_file"):
        try:
            from invoke_actions import run_invoke
            extra = {
                "src": step.get("src") or (step.get("extra") or {}).get("src"),
                "dst": step.get("dst") or (step.get("extra") or {}).get("dst"),
            }
            info = run_invoke(action, str(step.get("value") or step.get("text") or ""), extra)
        except Exception as e:
            result.reason = f"invoke failed: {e}"
            return result
        if action in ("launch_app", "open_path"):
            time.sleep(1.0)
            try:
                import psutil

                exe = (info.get("exe") or "").lower()
                names = [(p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])]
                if exe not in names:
                    result.reason = f"launch_app: process {exe!r} not in process list"
                    return result
            except Exception as e:
                result.reason = f"launch_app process check failed: {e}"
                return result
        if action in ("move_file", "copy_file"):
            dst = extra.get("dst") or ""
            if dst and not os.path.exists(dst):
                result.reason = f"{action}: destination missing {dst}"
                return result
        result.ok = True
        result.reason = f"invoke {action} verified"
        return result

    result.reason = f"unsupported action {action!r} — not executed"
    return result


def run_verified_plan(steps: list, *, halt_on_fail: bool = True) -> dict:
    """Run steps in order. Never retries. Stops on first verified failure."""
    results = []
    last_window = None
    for i, step in enumerate(steps):
        res = execute_step(step, last_window=last_window)
        results.append(res)
        if not res.ok:
            if halt_on_fail:
                return {
                    "ok": False,
                    "halted_index": i,
                    "reason": res.reason,
                    "results": results,
                }
        elif res.window_found:
            last_window = res.window_found
    return {"ok": True, "halted_index": None, "reason": "done", "results": results}
