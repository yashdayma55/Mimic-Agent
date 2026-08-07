"""
MimicAgent Stage C - prerequisites.

Before the goal-driven loop acts, make sure the environment is ready: if the goal
needs an app (e.g. Chrome) and it is not running, launch it and wait. This is the
agent setting up its own environment instead of just failing because Chrome was closed.

  is_running(process_name)     -> True/False via psutil
  launch_app(path, wait=3)     -> start an app and give it time to open
  ensure_app(name, launchers)  -> if not running, launch it; returns True when ready
  ensure_for_goal(goal)        -> infer needed apps from the goal text and ensure them
"""

import time
import subprocess
import shutil
import os

try:
    import psutil
except ImportError:
    psutil = None


# known apps: process name(s) to detect, and how to launch them
KNOWN_APPS = {
    "chrome": {
        "procs": ["chrome.exe"],
        "launch": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
    },
    "notepad": {"procs": ["notepad.exe"], "launch": ["notepad.exe"]},
    "edge": {
        "procs": ["msedge.exe"],
        "launch": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
    },
}


def is_running(process_name):
    """True if a process with this name is currently running."""
    if psutil is None:
        print("   (psutil not installed - cannot check processes)")
        return False
    pn = process_name.lower()
    for p in psutil.process_iter(["name"]):
        try:
            if p.info["name"] and p.info["name"].lower() == pn:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def launch_app(candidates, wait=3):
    """Launch the first launcher path that exists. Returns True if started."""
    for path in candidates:
        # a bare exe name (like notepad.exe) or a real path
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
    print(f"   no launchable path found among: {candidates}")
    return False


def ensure_app(name, wait=3):
    """Make sure a known app is running. Launch it if not. Returns True when ready."""
    app = KNOWN_APPS.get(name.lower())
    if not app:
        print(f"   unknown app '{name}' - cannot ensure")
        return False
    if any(is_running(pn) for pn in app["procs"]):
        print(f"   '{name}' is already running")
        return True
    print(f"   '{name}' is not running - launching it")
    return launch_app(app["launch"], wait=wait)


def ensure_for_goal(goal):
    """Infer which known apps a goal needs from its text, and ensure each is ready.
    Returns a list of (app, ready) results."""
    g = goal.lower()
    needed = []
    # simple keyword inference; extend as needed
    if any(w in g for w in ["chrome", "browser", "web", "linkedin", "http", ".com",
                             "google", "gmail", "website", "online"]):
        needed.append("chrome")
    if "notepad" in g:
        needed.append("notepad")

    results = []
    if not needed:
        print("   (no known app prerequisites inferred from the goal)")
    for app in needed:
        results.append((app, ensure_app(app)))
    return results


if __name__ == "__main__":
    import sys
    if psutil is None:
        print("psutil is not installed. run: pip install psutil")
        sys.exit(1)

    print("=== Stage C: prerequisites test ===")
    print("\n1. is Chrome running?")
    print("   ->", is_running("chrome.exe"))

    print("\n2. ensure Notepad (safe to launch):")
    ensure_app("notepad")

    goal = sys.argv[1] if len(sys.argv) > 1 else "apply to the job on LinkedIn"
    print(f"\n3. ensure prerequisites for goal: '{goal}'")
    print("   results:", ensure_for_goal(goal))