"""
MimicAgent Stage C+ - reasoning-based prerequisites.

Instead of hardcoded keyword rules ("linkedin" -> chrome), the MODEL reasons
about what a goal or workflow needs, choosing from a CLOSED list of capabilities
your code can actually provide. The model decides WHAT; the toolbox does HOW.

  CAPABILITIES        - the things your code knows how to ensure (closed list)
  reason_prerequisites(goal_or_steps) -> list of capability names the task needs
  ensure_capability(name)             -> detect + provide it (returns ready bool)
  prepare_for(goal=None, steps=None)  -> reason then ensure; returns results
"""

import json
import time
import os
import shutil
import subprocess

try:
    import psutil
except ImportError:
    psutil = None


# ---- the CLOSED toolbox: only things the code can actually deliver ----
# each capability: how to detect it (process names) and how to provide it (launchers)
CAPABILITIES = {
    "browser": {
        "desc": "a web browser (Chrome) for any website, web app, or online task",
        "procs": ["chrome.exe"],
        "launch": [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                   r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
    },
    "browser_debug": {
        "desc": "Chrome with remote debugging on port 9222 (needed to CONTROL the browser via the automation tier)",
        "procs": ["chrome.exe"],
        "launch": None,   # special-cased below (needs the debug flag)
    },
    "notepad": {
        "desc": "the Notepad text editor",
        "procs": ["notepad.exe"],
        "launch": ["notepad.exe"],
    },
}


def _load_key():
    try:
        with open("my_key.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def reason_prerequisites(goal=None, steps=None):
    """Ask the model which capabilities (from the closed list) the task needs.
    Returns a list of capability names. Reasoning is flexible; the ANSWER is
    constrained to things the toolbox can provide, so it is always executable."""
    key = _load_key()
    if not key or not key.startswith("sk-ant"):
        return []

    import requests
    cap_menu = "\n".join(f"- {name}: {c['desc']}" for name, c in CAPABILITIES.items())

    if steps:
        # summarize the workflow's evidence (titles/names hint at what it uses)
        sample = []
        for s in steps[:40]:
            bit = s.get("instruction") or s.get("elem_name") or s.get("action", "")
            if bit:
                sample.append(str(bit))
        task_desc = "A recorded workflow with these steps:\n" + "\n".join(sample)
    else:
        task_desc = f"A goal stated as: \"{goal}\""

    prompt = f"""You decide what must be running BEFORE a desktop task can work.

{task_desc}

Here are the ONLY capabilities I can set up (choose from these names only):
{cap_menu}

Which of these capabilities does the task need in place before it starts?
Respond with ONLY a JSON list of capability names, e.g. ["browser"] or [].
Include a capability only if the task clearly needs it."""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-sonnet-4-5", "max_tokens": 150,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=45)
        r.raise_for_status()
        raw = r.json()["content"][0]["text"]
        names = json.loads(raw[raw.find("["): raw.rfind("]") + 1])
        # keep only names we actually know
        return [n for n in names if n in CAPABILITIES]
    except Exception as e:
        print(f"   prerequisite reasoning failed: {e}")
        return []


def _is_running(proc_name):
    if psutil is None:
        return False
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"] and p.info["name"].lower() == proc_name.lower():
                return True
        except Exception:
            continue
    return False


def _launch(candidates, wait=3):
    for path in candidates:
        exe = path if os.path.sep in path else shutil.which(path)
        target = path if os.path.exists(path) else exe
        if target:
            try:
                subprocess.Popen(target)
                print(f"   launched: {target}")
                time.sleep(wait)
                return True
            except Exception as e:
                print(f"   could not launch {target}: {e}")
    return False


def focus_app(proc_names, title_hint=None):
    """Bring an app's window to the foreground so perception sees the RIGHT window.
    Tries by process -> window title. Returns True if a window was focused."""
    try:
        import win32gui, win32process, win32con
    except Exception:
        return False

    targets = [pn.lower() for pn in proc_names]
    found = {"hwnd": None}

    def enum_cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil is None:
                return
            pname = psutil.Process(pid).name().lower()
            title = win32gui.GetWindowText(hwnd)
            if pname in targets and title.strip():
                if (title_hint is None) or (title_hint.lower() in title.lower()):
                    found["hwnd"] = hwnd
        except Exception:
            pass

    win32gui.EnumWindows(enum_cb, None)
    if found["hwnd"]:
        try:
            win32gui.ShowWindow(found["hwnd"], win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(found["hwnd"])
            import time; time.sleep(0.6)
            print(f"   focused the {proc_names[0]} window")
            return True
        except Exception as e:
            print(f"   could not focus window: {e}")
    return False


def ensure_capability(name):
    """Detect + provide one capability. Returns True when ready."""
    cap = CAPABILITIES.get(name)
    if not cap:
        return False

    # special case: browser with debugging port (for the control tier)
    if name == "browser_debug":
        if _is_running("chrome.exe"):
            print("   Chrome is running (note: may need the debug port for control)")
            return True
        chrome = next((p for p in CAPABILITIES["browser"]["launch"] if os.path.exists(p)), None)
        if chrome:
            try:
                subprocess.Popen([chrome, "--remote-debugging-port=9222",
                                  r'--user-data-dir=C:\chrome-debug'])
                print("   launched Chrome with remote debugging on 9222")
                time.sleep(3)
                return True
            except Exception as e:
                print(f"   could not launch debug Chrome: {e}")
                return False
        return False

    if any(_is_running(pn) for pn in cap["procs"]):
        print(f"   '{name}' already available")
        return True
    print(f"   '{name}' not running - providing it")
    return _launch(cap["launch"])


def prepare_for(goal=None, steps=None):
    """Reason about prerequisites, then ensure each. Returns [(cap, ready), ...]."""
    print("[prereq] reasoning about what this task needs...")
    needed = reason_prerequisites(goal=goal, steps=steps)
    if not needed:
        print("   the model judged no special prerequisites are needed.")
        return []
    print(f"   the model says this task needs: {needed}")
    results = []
    for cap in needed:
        ready = ensure_capability(cap)
        # bring the app to the foreground so the agent perceives the RIGHT window
        if ready:
            capdef = CAPABILITIES.get(cap, {})
            focus_app(capdef.get("procs", []))
        results.append((cap, ready))
    return results


if __name__ == "__main__":
    import sys
    print("=== reasoning-based prerequisites ===")

    tests = [
        "apply to a software job on LinkedIn",
        "write a poem in Notepad",
        "add two numbers in my head",
    ]
    for g in tests:
        print(f"\nGOAL: {g}")
        needed = reason_prerequisites(goal=g)
        print(f"  model says needs: {needed}")