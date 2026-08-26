"""Optional tools a taught step may use at run time. Web is opt-in per step."""

from __future__ import annotations

import re
import urllib.error
import urllib.request


def web_get(url: str, timeout: float = 8.0) -> dict:
    """Fetch a public http(s) page. No browsing of local/private hosts."""
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return {"ok": False, "error": "only http(s) URLs are allowed"}
    if re.search(r"://(localhost|127\.|0\.0\.0\.0|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", u, re.I):
        return {"ok": False, "error": "private hosts are not allowed"}
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "MimicAgent/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(8000)
            text = raw.decode("utf-8", errors="replace")
            title = ""
            m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()
            return {"ok": True, "url": u, "status": resp.status, "title": title, "text": text[:4000]}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"http {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tools_for_step(step) -> list:
    out = []
    if getattr(step, "web_allowed", False):
        out.append({
            "name": "web_get",
            "when": "look up a public page or fact while running this step",
        })
    return out
