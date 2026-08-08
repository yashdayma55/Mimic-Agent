"""
Detect whether Chrome is the foreground window.

Used by agent_loop.perceive() to choose DOM (CDP) perception vs the native
accessibility-tree path. Pattern matches prereq_reasoner.focus_app:
win32gui.GetForegroundWindow + psutil process name.
"""

def is_browser_frontmost():
    """True if the current foreground window belongs to chrome.exe."""
    try:
        import win32gui
        import win32process
        import psutil
    except Exception:
        return False

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return False
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        name = psutil.Process(pid).name().lower()
        return name == "chrome.exe"
    except Exception:
        return False


if __name__ == "__main__":
    print(f"browser frontmost: {is_browser_frontmost()}")
