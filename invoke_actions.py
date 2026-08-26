"""Named invocation actions. Arguments are escaped; no arbitrary shell."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import webbrowser

_UNSAFE = re.compile(r"[;&|`$<>\n\r]|\$\(|&&|\|\|")


class InvokeError(ValueError):
    pass


def _reject_unsafe(value: str, label: str) -> str:
    text = str(value or "")
    if _UNSAFE.search(text):
        raise InvokeError(f"rejected unsafe {label}: {text!r}")
    return text


def launch_app(name: str) -> dict:
    name = _reject_unsafe(name, "app name")
    mapping = {
        "notepad": "notepad.exe",
        "notepad.exe": "notepad.exe",
        "calc": "calc.exe",
        "calculator": "calc.exe",
        "explorer": "explorer.exe",
    }
    exe = mapping.get(name.lower().strip(), None)
    if exe is None:
        # only bare names with .exe, no path separators
        if re.fullmatch(r"[A-Za-z0-9_.-]+", name) and name.lower().endswith(".exe"):
            exe = name
        else:
            raise InvokeError(f"unknown app {name!r}")
    subprocess.Popen([exe], shell=False)
    return {"ok": True, "action": "launch_app", "exe": exe}


def open_url(url: str) -> dict:
    url = _reject_unsafe(url, "url")
    if not re.match(r"^https?://", url, re.I):
        raise InvokeError(f"open_url only allows http(s), got {url!r}")
    webbrowser.open(url)
    return {"ok": True, "action": "open_url", "url": url}


def open_path(path: str) -> dict:
    path = _reject_unsafe(path, "path")
    if not os.path.exists(path):
        raise InvokeError(f"path does not exist: {path}")
    os.startfile(path)  # type: ignore[attr-defined]
    return {"ok": True, "action": "open_path", "path": path}


def move_file(src: str, dst: str) -> dict:
    src = _reject_unsafe(src, "src")
    dst = _reject_unsafe(dst, "dst")
    shutil.move(src, dst)
    return {"ok": True, "action": "move_file", "src": src, "dst": dst}


def copy_file(src: str, dst: str) -> dict:
    src = _reject_unsafe(src, "src")
    dst = _reject_unsafe(dst, "dst")
    shutil.copy2(src, dst)
    return {"ok": True, "action": "copy_file", "src": src, "dst": dst}


def parse_src_dst(value: str) -> tuple[str, str]:
    text = (value or "")
    if "->" in text:
        a, b = text.split("->", 1)
        return a.strip(), b.strip()
    parts = text.split(None, 1)
    if len(parts) != 2:
        raise InvokeError("expected 'src -> dst'")
    return parts[0], parts[1]


def run_invoke(action: str, value: str, extra: dict | None = None) -> dict:
    extra = extra or {}
    if action == "launch_app":
        return launch_app(value)
    if action == "open_url":
        return open_url(value)
    if action == "open_path":
        return open_path(value)
    if action == "move_file":
        src, dst = extra.get("src"), extra.get("dst")
        if not (src and dst):
            src, dst = parse_src_dst(value)
        return move_file(src, dst)
    if action == "copy_file":
        src, dst = extra.get("src"), extra.get("dst")
        if not (src and dst):
            src, dst = parse_src_dst(value)
        return copy_file(src, dst)
    raise InvokeError(f"not an invoke action: {action}")
