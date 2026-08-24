"""Load and validate the outreach knowledge file (verbatim facts)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_PKG_DIR = Path(__file__).resolve().parent
KNOWLEDGE_PATH = _PKG_DIR / "knowledge.yaml"

_URL_RE = re.compile(r"https?://[^\s)|,\]]+", re.I)


def load_knowledge(path: Path | str | None = None) -> dict:
    p = Path(path) if path else KNOWLEDGE_PATH
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"knowledge file is not a mapping: {p}")
    _validate(data)
    return data


def _validate(data: dict) -> None:
    for key in ("identity", "experience", "projects", "links", "signature", "template"):
        if key not in data:
            raise ValueError(f"knowledge missing required key: {key}")
    ident = data["identity"]
    if not (ident.get("identity_line") or "").strip():
        raise ValueError("identity.identity_line is empty")
    exp = data["experience"]
    if not (exp.get("paragraph") or "").strip():
        raise ValueError("experience.paragraph is empty")
    projects = data["projects"]
    if not isinstance(projects, list) or len(projects) < 1:
        raise ValueError("projects must be a non-empty list")
    for i, proj in enumerate(projects):
        if not proj.get("key"):
            raise ValueError(f"project[{i}] missing key")
        if not (proj.get("one_paragraph_description") or "").strip():
            raise ValueError(f"project {proj.get('key')} missing one_paragraph_description")
        links = proj.get("links") or {}
        if not isinstance(links, dict) or not links:
            raise ValueError(f"project {proj.get('key')} missing links")
    if not (data.get("signature") or "").strip():
        raise ValueError("signature is empty")
    links = data.get("links") or {}
    if not links.get("github") or not links.get("portfolio"):
        raise ValueError("links.github and links.portfolio are required")


def all_allowed_urls(knowledge: dict) -> set[str]:
    """Every URL that may appear in an assembled email."""
    urls: set[str] = set()

    def add(val: Any) -> None:
        if not val:
            return
        if isinstance(val, str):
            for m in _URL_RE.findall(val):
                urls.add(m.rstrip(").,;"))
        elif isinstance(val, dict):
            for v in val.values():
                add(v)
        elif isinstance(val, list):
            for v in val:
                add(v)

    add(knowledge.get("identity"))
    add(knowledge.get("experience"))
    add(knowledge.get("projects"))
    add(knowledge.get("links"))
    add(knowledge.get("signature"))
    return urls


def project_by_key(knowledge: dict, key: str) -> dict | None:
    for proj in knowledge.get("projects") or []:
        if str(proj.get("key") or "").lower() == str(key).lower():
            return proj
    return None


def project_catalog_for_prompt(knowledge: dict) -> str:
    lines = []
    for proj in knowledge.get("projects") or []:
        tags = ", ".join(proj.get("tags") or [])
        links = proj.get("links") or {}
        link_s = " | ".join(f"{k}={v}" for k, v in links.items())
        lines.append(
            f"- key={proj.get('key')}\n"
            f"  name={proj.get('name')}\n"
            f"  tags=[{tags}]\n"
            f"  links: {link_s}\n"
            f"  description (VERBATIM, do not rewrite):\n"
            f"  {proj.get('one_paragraph_description')}"
        )
    return "\n".join(lines)
