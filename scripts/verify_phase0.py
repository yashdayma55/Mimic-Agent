#!/usr/bin/env python
"""Phase 0 multi-target resolve() telemetry harness.

For each target: build an ElementRef, call resolve(), record the result.
Does NOT click. Does NOT navigate. Arrange each group during the 8s countdown.

Usage:
  python scripts/verify_phase0.py
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mimicagent.core.capture import (
    focus_app,
    inspect_window_a11y,
)
from mimicagent.core.element_ref import A11yRef, ElementRef, SemanticRef
from mimicagent.core.resolver import ResolveResult, resolve

APP_CHROME = "chrome.exe"


@dataclass
class Target:
    name: str
    ref: ElementRef


@dataclass
class Row:
    target: str
    layer_used: str
    confidence: str
    coords: str
    elapsed_ms: str
    ok: bool
    notes: str = ""


def _ref(
    *,
    description: str,
    window_title_hint: str,
    name: str | None = None,
    control_type: str | None = None,
    automation_id: str | None = None,
    anchor_name: str | None = None,
    name_contains: str | None = None,
    name_regex: str | None = None,
    nth_of_type: int | None = None,
    subtree_root: str | None = None,
) -> ElementRef:
    return ElementRef(
        a11y=A11yRef(
            automation_id=automation_id,
            name=name,
            control_type=control_type,
            anchor_name=anchor_name,
            name_contains=name_contains,
            name_regex=name_regex,
            nth_of_type=nth_of_type,
            subtree_root=subtree_root,
        ),
        semantic=SemanticRef(
            description=description,
            app=APP_CHROME,
            window_title_hint=window_title_hint,
        ),
    )


def _person_from_linkedin_title(title: str) -> str:
    """'Aditi Sharma | LinkedIn - Google Chrome' -> 'Aditi Sharma'."""
    t = (title or "").strip()
    for suffix in (" - Google Chrome", " - Chrome"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    t = re.sub(r"^\(\d+\)\s*", "", t)
    if "|" in t:
        t = t.split("|", 1)[0].strip()
    t = t.replace("LinkedIn", "").strip(" -|")
    return t


def _group_a(person_name: str) -> list[Target]:
    """LinkedIn targets: name heading uses the person from the window title."""
    if person_name:
        print(f"  [harness] LinkedIn person from window title: {person_name!r}")
        print("  [harness] headline name_contains=' at ' (Role at Company pattern)")
    return [
        Target(
            "chrome_extensions_button",
            _ref(
                name="Extensions",
                control_type="Button",
                window_title_hint="LinkedIn",
                description=(
                    "the Extensions puzzle-piece icon button in the Chrome toolbar "
                    "(NOT a webpage button, NOT the address bar)"
                ),
            ),
        ),
        Target(
            "linkedin_profile_name_heading",
            _ref(
                name=None,
                control_type="Text",
                name_contains=person_name or None,
                window_title_hint="LinkedIn",
                description=(
                    "the person's full name heading at the top of the LinkedIn profile "
                    "(large name under the profile photo, NOT the browser tab title, "
                    "NOT the search box)"
                ),
            ),
        ),
        Target(
            "linkedin_profile_headline",
            _ref(
                name=None,
                control_type="Text",
                name_contains=" at ",
                window_title_hint="LinkedIn",
                description=(
                    "the LinkedIn profile headline line under the name "
                    "(job title / company, e.g. Senior Engineering Manager at OCI) "
                    "NOT the About section, NOT a job posting card"
                ),
            ),
        ),
    ]


GROUP_A: list[Target] = _group_a("")  # names for abort rows; rebuilt after focus

GROUP_B: list[Target] = [
    Target(
        "apollo_dropdown_item",
        _ref(
            name="Apollo.io",
            control_type="Button",
            window_title_hint="LinkedIn",
            subtree_root=None,
            description=(
                "the 'Apollo.io' / 'Apollo.io: Free B2B Phone Number & Email' row "
                "inside the already-open Chrome Extensions dropdown menu "
                "(NOT the puzzle-piece toolbar button itself)"
            ),
        ),
    ),
    Target(
        "apollo_revealed_email_line",
        _ref(
            name=None,
            control_type="Text",
            name_contains="@",
            name_regex=r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}",
            window_title_hint="LinkedIn",
            subtree_root=None,
            description=(
                "the already-revealed email address text in the Apollo.io contact "
                "panel under the Emails heading (an address like name@company.com "
                "with a Work label). NOT the Access email button, NOT Gmail, "
                "NOT the LinkedIn page body"
            ),
        ),
    ),
    Target(
        "apollo_copy_icon",
        _ref(
            name="Copy",
            control_type="Button",
            window_title_hint="LinkedIn",
            subtree_root=None,
            description=(
                "the copy/clipboard icon beside the revealed email address in the "
                "Apollo.io contact panel Emails section (two overlapping squares). "
                "NOT Compose email, NOT Access email, NOT the browser copy"
            ),
        ),
    ),
]

GROUP_C: list[Target] = [
    Target(
        "gmail_to_field",
        _ref(
            name="To",
            control_type="Edit",
            window_title_hint="Gmail",
            subtree_root="New Message",
            description=(
                "the To recipients input inside the Gmail compose window "
                "(next to the To label). NOT the top Gmail search bar, "
                "NOT Cc/Bcc, NOT Subject"
            ),
        ),
    ),
    Target(
        "gmail_subject_field",
        _ref(
            name="Subject",
            control_type="Edit",
            window_title_hint="Gmail",
            subtree_root="New Message",
            description=(
                "the Subject input inside the Gmail compose window "
                "(row labeled Subject under To). NOT To, NOT the message body"
            ),
        ),
    ),
    Target(
        "gmail_body_field",
        _ref(
            name="Message Body",
            control_type="Edit",
            window_title_hint="Gmail",
            subtree_root="New Message",
            description=(
                "the large Message Body editable area inside the Gmail compose "
                "window (below Subject). NOT To, NOT Subject, NOT Send"
            ),
        ),
    ),
]

# (title, setup instruction, required window_title_hint, targets)
GROUPS: list[tuple[str, str, str, list[Target]]] = [
    (
        "A — Chrome on a LinkedIn profile",
        "Open Chrome on a LinkedIn /in/ profile (Apollo signed in). "
        "Leave the profile visible. Do not click anything else. "
        "Do not leave a google.com tab frontmost.",
        "LinkedIn",
        GROUP_A,
    ),
    (
        "B — Apollo extension dropdown open",
        "On that LinkedIn profile: open the Chrome Extensions dropdown, open "
        "Apollo.io, and make sure an email is already revealed (so the address "
        "and copy icon are visible). Leave that panel open.",
        "LinkedIn",
        GROUP_B,
    ),
    (
        "C — Gmail compose window open",
        "Open Gmail compose (To / Subject / Body visible). Leave the compose "
        "window open and unsent. Do not click Send.",
        "Gmail",
        GROUP_C,
    ),
]


def _aborted_row(target_name: str, reason: str) -> Row:
    return Row(
        target=target_name,
        layer_used="-",
        confidence="-",
        coords="-",
        elapsed_ms="-",
        ok=False,
        notes=reason,
    )


def _ensure_group_window(title_hint: str) -> str:
    """Focus Chrome by title hint. Return focused title, or '' to abort."""
    print(f"  [harness] focusing chrome.exe with title hint {title_hint!r}")
    print(
        "  [harness] Chrome flag note: web content is only in the UIA tree if "
        "Chrome was launched with --force-renderer-accessibility "
        "(or chrome://accessibility enabled for this tab)."
    )
    title = focus_app(["chrome.exe"], title_hint=title_hint)
    print(f"  [harness] focused window title: {title!r}")
    if not title or title_hint.lower() not in title.lower():
        print(
            f"  [harness] ERROR: expected a window title containing {title_hint!r}, "
            f"got {title!r}. Not resolving against the wrong frontmost window."
        )
        print("  [harness] aborting this group.")
        return ""
    inspect_window_a11y(title)
    return title


def _countdown(seconds: int = 8) -> None:
    """Visible countdown. Do not use input() — Enter in the VS Code terminal
    steals focus from Chrome and breaks the run."""
    print(
        f"  [harness] resolving in {seconds}s — leave Chrome in front, "
        "do not click this terminal"
    )
    for remaining in range(seconds, 0, -1):
        print(f"  [harness] {remaining}...", flush=True)
        time.sleep(1)
    print("  [harness] go", flush=True)


def _coords(result: ResolveResult) -> str:
    if not result.coordinates:
        return "-"
    x, y = result.coordinates
    return f"({x},{y})"


def _run_target(target: Target, window_title: str) -> Row:
    print()
    print(f"--- target: {target.name} ---")
    print(f"  [harness] window before resolve: {window_title!r}")
    t0 = time.perf_counter()
    try:
        result = resolve(target.ref)
    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f"  [harness] resolve crashed: {e}")
        return Row(
            target=target.name,
            layer_used="5",
            confidence="0.00",
            coords="-",
            elapsed_ms=str(elapsed),
            ok=False,
            notes=f"exception: {e}",
        )

    elapsed = int((time.perf_counter() - t0) * 1000)
    layer = result.layer_used if result.layer_used is not None else 5
    ok = bool(result.success) and layer != 5
    print(
        f"  [harness] {target.name}: success={result.success} "
        f"layer={layer} conf={result.confidence:.2f} "
        f"coords={_coords(result)} {elapsed}ms"
    )
    return Row(
        target=target.name,
        layer_used=str(layer),
        confidence=f"{result.confidence:.2f}",
        coords=_coords(result),
        elapsed_ms=str(elapsed),
        ok=ok,
        notes=result.notes,
    )


def _print_table(rows: list[Row]) -> None:
    headers = ("target", "layer_used", "confidence", "coords", "elapsed_ms")
    cols = [
        [r.target for r in rows],
        [r.layer_used for r in rows],
        [r.confidence for r in rows],
        [r.coords for r in rows],
        [r.elapsed_ms for r in rows],
    ]
    widths = [
        max(len(headers[i]), max((len(v) for v in cols[i]), default=0))
        for i in range(len(headers))
    ]

    def fmt(vals: tuple[str, ...]) -> str:
        return "  ".join(vals[i].ljust(widths[i]) for i in range(len(headers)))

    print()
    print("=" * 72)
    print("TELEMETRY")
    print("=" * 72)
    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(
            fmt((r.target, r.layer_used, r.confidence, r.coords, r.elapsed_ms))
        )


def _print_summary(rows: list[Row]) -> None:
    layer_counts = {n: 0 for n in range(1, 6)}
    failed = 0
    for r in rows:
        try:
            layer = int(r.layer_used)
        except ValueError:
            layer = 5
        if layer not in layer_counts:
            layer = 5
        layer_counts[layer] += 1
        if not r.ok:
            failed += 1

    print()
    print("SUMMARY")
    print(f"  targets: {len(rows)}")
    for n in range(1, 6):
        print(f"  layer {n}: {layer_counts[n]}")
    print(f"  failed:  {failed}")


def main() -> int:
    print("=" * 72)
    print("MimicAgent Phase 0 — multi-target resolve() telemetry")
    print("resolve only — no clicks, no navigation")
    print("=" * 72)

    rows: list[Row] = []
    for title, instruction, title_hint, targets in GROUPS:
        print()
        print("=" * 72)
        print(f"SETUP — Group {title}")
        print("=" * 72)
        print(instruction)
        print()
        _countdown(8)
        focused_title = _ensure_group_window(title_hint)
        if not focused_title:
            for target in targets:
                rows.append(
                    _aborted_row(
                        target.name,
                        f"aborted: window title did not contain {title_hint!r}",
                    )
                )
            continue
        if title.startswith("A —"):
            person = _person_from_linkedin_title(focused_title)
            targets = _group_a(person)
        for target in targets:
            # Re-focus via focus_app; use its returned title (do not re-query HWND).
            title_now = focus_app(["chrome.exe"], title_hint=title_hint)
            if not title_now or title_hint.lower() not in title_now.lower():
                print(
                    f"  [harness] focus_lost on {target.name}: "
                    f"expected {title_hint!r}, got {title_now!r} — continuing"
                )
                rows.append(
                    _aborted_row(
                        target.name,
                        f"focus_lost: focus_app returned {title_now!r}",
                    )
                )
                continue
            rows.append(_run_target(target, title_now))

    _print_table(rows)
    _print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
