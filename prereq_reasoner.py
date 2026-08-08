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


def _debug_port_open(port=9222):
    """True if Chrome's remote-debugging HTTP endpoint responds on the port."""
    try:
        import urllib.request
        url = f"http://localhost:{port}/json/version"
        with urllib.request.urlopen(url, timeout=2) as resp:
            return getattr(resp, "status", 200) == 200
    except Exception:
        return False


def _launch_debug_chrome(wait=3):
    """Launch Chrome with --remote-debugging-port=9222 and the debug profile."""
    chrome = next((p for p in CAPABILITIES["browser"]["launch"] if os.path.exists(p)), None)
    if not chrome:
        print("   could not find chrome.exe to launch in debug mode")
        return False
    try:
        subprocess.Popen([chrome, "--remote-debugging-port=9222",
                          r"--user-data-dir=C:\chrome-debug"])
        print("   launched Chrome with remote debugging on 9222")
        time.sleep(wait)
        return _debug_port_open(9222)
    except Exception as e:
        print(f"   could not launch debug Chrome: {e}")
        return False


def focus_app(proc_names, title_hint=None):
    """Bring an app's window to the foreground so perception sees the RIGHT window.

    When multiple windows match the process: prefer the one whose TITLE best
    matches title_hint; otherwise pick the most recently active (topmost in
    z-order) matching window. Returns True if a window was focused."""
    try:
        import win32gui, win32process, win32con
    except Exception:
        return False

    targets = [pn.lower() for pn in proc_names]
    # EnumWindows is topmost-first (z-order); keep that order.
    candidates = []  # (hwnd, title) in z-order

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
                candidates.append((hwnd, title))
        except Exception:
            pass

    win32gui.EnumWindows(enum_cb, None)
    if not candidates:
        return False

    chosen_hwnd = None
    chosen_title = None
    reason = "most recently active match"

    if title_hint:
        hint = title_hint.strip().lower()
        if hint:
            scored = []
            for hwnd, title in candidates:
                t = title.lower()
                if hint in t:
                    # Prefer tighter title matches (hint covers more of the title)
                    score = len(hint) / max(len(t), 1)
                    scored.append((score, hwnd, title))
            if scored:
                scored.sort(key=lambda x: x[0], reverse=True)
                _, chosen_hwnd, chosen_title = scored[0]
                reason = f"title hint '{title_hint}'"

    if chosen_hwnd is None:
        # First in z-order among process matches = most recently active
        chosen_hwnd, chosen_title = candidates[0]
        if title_hint:
            reason = f"most recently active (no title matched '{title_hint}')"

    try:
        win32gui.ShowWindow(chosen_hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(chosen_hwnd)
        time.sleep(0.6)
        short = (chosen_title or "")[:70]
        print(f"   focused [{proc_names[0]}] '{short}' ({reason})")
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
        if _debug_port_open(9222):
            print("   debug Chrome already running on 9222")
            return True
        if _is_running("chrome.exe"):
            print("   Chrome is running WITHOUT the debug port. To control it, Chrome must be")
            print("   relaunched in debug mode. Close Chrome and relaunch in debug mode? (y/n)")
            ans = input("   ").strip().lower()
            if ans != "y":
                print("   browser control is unavailable (debug port not open)")
                return False
            try:
                subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
                               capture_output=True, text=True, timeout=30)
            except Exception as e:
                print(f"   could not close Chrome: {e}")
                return False
            time.sleep(2)
            return _launch_debug_chrome(wait=3)
        # no Chrome at all — launch debug Chrome
        return _launch_debug_chrome(wait=3)

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
            hint = None
            if goal:
                # soft title hint from goal text (site/app name substring)
                g = goal.lower()
                for token in ("linkedin", "gmail", "github", "youtube",
                              "notion", "chatgpt", "claude", "google",
                              "notepad"):
                    if token in g:
                        hint = token
                        break
            focus_app(capdef.get("procs", []), title_hint=hint)
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