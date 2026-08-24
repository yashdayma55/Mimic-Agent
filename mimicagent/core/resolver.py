"""Five-layer ElementRef resolver with telemetry on which layer won."""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from pydantic import BaseModel

from mimicagent.core import config
from mimicagent.core.capture import (
    capture_raw_no_focus,
    capture_som_marked,
    crop_around_screen_point,
    focus_app,
)
from mimicagent.core.element_ref import ElementRef


class ResolveResult(BaseModel):
    success: bool
    layer_used: int | None = None  # 1..5; None if nothing ran cleanly
    coordinates: tuple[int, int] | None = None
    confidence: float = 0.0
    notes: str = ""


def _log_layer(layer: int, name: str, ok: bool, detail: str = "") -> None:
    status = "HIT" if ok else "miss"
    print(f"  [resolve] layer {layer} ({name}): {status} {detail}".rstrip())


def _load_api_key() -> str:
    try:
        with open("my_key.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


_VISION_SYSTEM = (
    "Return ONLY a single JSON object. No markdown, no code fences, no prose "
    "before or after the JSON."
)

_JSON_ONLY_INSTRUCTION = (
    "Return ONLY a JSON object, no prose, no markdown fences."
)


def _strip_markdown_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _parse_json_object(raw_text: str) -> dict | None:
    t = _strip_markdown_fences(raw_text)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(t[start : end + 1])
    except json.JSONDecodeError:
        return None


def _call_vision_json(
    image_path: str, prompt: str, *, max_tokens: int = 300
) -> tuple[dict | None, str]:
    """Send one image + prompt to Claude; parse first JSON object in response.

    Headers/payload match email_workflow_automation._call_vision_json
    (stdlib urllib instead of requests).
    """
    key = _load_api_key()
    key_ok = bool(key) and key.startswith("sk-ant")
    print(
        f"  [vision] key_loaded={key_ok} prefix={key[:12]!r} "
        f"timeout=60 model={config.VISION_MODEL!r}"
    )
    if not key_ok:
        return None, "no Claude API key for vision (my_key.txt)"
    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        # Payload/headers copied from ewa._call_vision_json (working production).
        # Headers/shape match ewa._call_vision_json; system forces JSON-only
        # (ewa callers prefix prompts with "Return ONLY JSON:").
        image_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": b64,
            },
        }

        def _post(user_text: str) -> tuple[int | None, str, str]:
            payload = {
                "model": "claude-sonnet-4-5",
                "max_tokens": max_tokens,
                "system": _VISION_SYSTEM,
                "messages": [{
                    "role": "user",
                    "content": [
                        image_block,
                        {"type": "text", "text": user_text},
                    ],
                }],
            }
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    status = getattr(resp, "status", None) or resp.getcode()
                    raw_body = resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                print(f"  [vision] HTTP {e.code} body[:500]={err_body[:500]!r}")
                return e.code, err_body, ""
            print(f"  [vision] HTTP {status} body[:500]={raw_body[:500]!r}")
            if not raw_body.strip():
                return status, raw_body, ""
            data = json.loads(raw_body)
            raw_text = (data.get("content") or [{}])[0].get("text") or ""
            print(f"  [vision] model_text[:300]={raw_text[:300]!r}")
            return status, raw_body, raw_text

        user_prompt = prompt
        if _JSON_ONLY_INSTRUCTION not in prompt and "Return ONLY JSON" not in prompt:
            user_prompt = f"{_JSON_ONLY_INSTRUCTION}\n{prompt}"
        elif _JSON_ONLY_INSTRUCTION not in prompt:
            # Prompt already says Return ONLY JSON; still ban fences/prose on first call.
            user_prompt = f"{_JSON_ONLY_INSTRUCTION}\n{prompt}"
        _status, _body, raw_text = _post(user_prompt)
        obj = _parse_json_object(raw_text)
        if obj is not None:
            return obj, ""

        print("  [vision] parse failed; retrying with explicit JSON-only instruction")
        retry_prompt = (
            user_prompt
            + "\n\nReturn ONLY a JSON object, no prose."
        )
        _status, _body, raw_text2 = _post(retry_prompt)
        obj = _parse_json_object(raw_text2)
        if obj is not None:
            return obj, ""
        print(f"  [vision] FINAL parse failure raw_text={raw_text2!r}")
        return None, f"no JSON object in model text ({len(raw_text2)} chars)"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Layer 1 / 2 helpers
# ---------------------------------------------------------------------------


def bbox_is_degenerate(left: int, top: int, right: int, bottom: int) -> bool:
    """True if the hit is unusable. Empty-name false hits often report (0,0)."""
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return True
    cx, cy = (left + right) // 2, (top + bottom) // 2
    if cx == 0 and cy == 0:
        return True
    return False


def _ctrl_bbox(ctrl) -> tuple[int, int, int, int] | None:
    try:
        rect = ctrl.BoundingRectangle
        box = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:
        return None
    if bbox_is_degenerate(*box):
        return None
    return box


def _ctrl_center(box: tuple[int, int, int, int]) -> tuple[int, int]:
    left, top, right, bottom = box
    return (left + right) // 2, (top + bottom) // 2


def _type_matches(ctrl, control_type: str | None) -> bool:
    if not control_type:
        return True
    ctype_l = control_type.strip().lower().replace(" ", "")
    try:
        cct = (ctrl.ControlTypeName or "").lower().replace(" ", "")
    except Exception:
        return False
    return ctype_l in cct or ctype_l.replace("control", "") in cct


def _name_filters_match(
    cname: str,
    *,
    name: str | None,
    name_contains: str | None,
    name_regex: str | None,
) -> bool:
    """Empty control names never match. Empty filter fields are ignored."""
    if not (cname or "").strip():
        return False
    c_l = cname.strip().lower()
    if name and name.strip().lower() not in c_l:
        return False
    if name_contains is not None and name_contains.lower() not in c_l:
        return False
    if name_regex:
        try:
            if re.search(name_regex, cname) is None:
                return False
        except re.error:
            return False
    return True


def _iter_controls(root, max_nodes: int = 4000):
    stack = [root]
    steps = 0
    while stack and steps < max_nodes:
        steps += 1
        c = stack.pop()
        yield c
        try:
            for ch in reversed(c.GetChildren() or []):
                stack.append(ch)
        except Exception:
            continue


def _walk_find(
    root,
    *,
    automation_id: str | None = None,
    name: str | None = None,
    control_type: str | None = None,
    max_nodes: int = 4000,
):
    """Window/anchor lookup. Empty names never match. Degenerate bboxes skipped."""
    aid = (automation_id or "").strip()
    name_l = (name or "").strip()
    for c in _iter_controls(root, max_nodes=max_nodes):
        try:
            if aid:
                if (c.AutomationId or "") == aid and _ctrl_bbox(c) is not None:
                    return c
                continue
            cname = (c.Name or "").strip()
            if not cname:
                continue
            if not _type_matches(c, control_type):
                continue
            if name_l and name_l.lower() not in cname.lower():
                continue
            if not name_l and not control_type:
                continue
            if _ctrl_bbox(c) is None:
                continue
            return c
        except Exception:
            continue
    return None


def _walk_find_last(
    root,
    *,
    name: str,
    max_nodes: int = 4000,
):
    """Last (topmost) control whose Name contains *name*."""
    needle = (name or "").strip().lower()
    if not needle:
        return None
    last = None
    for c in _iter_controls(root, max_nodes=max_nodes):
        try:
            cname = (c.Name or "").strip()
            if cname and needle in cname.lower() and _ctrl_bbox(c) is not None:
                last = c
        except Exception:
            continue
    return last


_EMAIL_NAME_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


def _dump_email_ancestor_chain(root) -> None:
    """Print ancestors of the first node whose Name looks like an email.

    Used to pick a real Apollo container for subtree_root (not a leaf like
    'Apollo.io' with 0 children).
    """
    email_node = None
    for c in _iter_controls(root, max_nodes=5000):
        try:
            cname = (c.Name or "").strip()
            if cname and _EMAIL_NAME_RE.search(cname):
                email_node = c
                break
        except Exception:
            continue
    if email_node is None:
        print("  [resolve] email ancestor dump: no email-named node found")
        return
    print(
        f"  [resolve] email ancestor dump start "
        f"name={email_node.Name!r} type={email_node.ControlTypeName!r}"
    )
    cur = email_node
    for depth in range(16):
        try:
            kids = cur.GetChildren() or []
            print(
                f"  [resolve] ancestor[{depth}] name={cur.Name!r} "
                f"control_type={cur.ControlTypeName!r} child_count={len(kids)}"
            )
            parent = cur.GetParentControl()
            if parent is None:
                break
            cur = parent
        except Exception as e:
            print(f"  [resolve] ancestor dump stopped: {e}")
            break


def _log_nearby_text_names(root, *, limit: int = 40) -> None:
    names: list[str] = []
    for c in _iter_controls(root, max_nodes=5000):
        try:
            cct = (c.ControlTypeName or "")
            if "text" not in cct.lower():
                continue
            cname = (c.Name or "").strip()
            if not cname:
                continue
            names.append(cname[:80])
        except Exception:
            continue
        if len(names) >= limit:
            break
    print(f"  [resolve] nearby Text names ({len(names)}): {names}")


def _layer1_a11y_exact(ref: ElementRef) -> ResolveResult | None:
    if not ref.a11y or not (ref.a11y.automation_id or "").strip():
        _log_layer(1, "a11y exact", False, "no automation_id")
        return None

    import uiautomation as auto

    aid = ref.a11y.automation_id.strip()
    try:
        root = auto.GetRootControl()
        if ref.a11y.anchor_name:
            anchor = _walk_find(root, name=ref.a11y.anchor_name)
            if anchor is not None:
                root = anchor
        ctrl = _walk_find(root, automation_id=aid)
        if ctrl is None:
            _log_layer(1, "a11y exact", False, f"AutomationId={aid!r} not found")
            return None
        box = _ctrl_bbox(ctrl)
        if box is None:
            _log_layer(1, "a11y exact", False, f"AutomationId={aid!r} degenerate bbox")
            return None
        cx, cy = _ctrl_center(box)
        _log_layer(1, "a11y exact", True, f"AutomationId={aid!r} -> ({cx},{cy})")
        return ResolveResult(
            success=True,
            layer_used=1,
            coordinates=(cx, cy),
            confidence=0.95,
            notes=f"a11y exact AutomationId={aid!r}",
        )
    except Exception as e:
        _log_layer(1, "a11y exact", False, str(e))
        return None


def _layer2_a11y_semantic(ref: ElementRef) -> ResolveResult | None:
    if not ref.a11y:
        _log_layer(2, "a11y semantic", False, "no a11y ref")
        return None
    name = (ref.a11y.name or "").strip()
    ctype = (ref.a11y.control_type or "").strip()
    name_contains = ref.a11y.name_contains if (ref.a11y.name_contains or "").strip() else None
    name_regex = (ref.a11y.name_regex or "").strip() or None
    nth = ref.a11y.nth_of_type
    has_name_filter = bool(name or name_contains or name_regex)
    if not has_name_filter and nth is None:
        # Type-only (e.g. name='' type=Text) is how we got (0,0) false hits.
        _log_layer(
            2,
            "a11y semantic",
            False,
            "need name, name_contains, name_regex, or nth_of_type "
            "(empty name + type-only is rejected)",
        )
        try:
            import uiautomation as auto

            root = auto.GetRootControl()
            hint = ref.semantic.window_title_hint
            if hint:
                win = _walk_find(root, name=hint)
                if win is not None:
                    root = win
            _log_nearby_text_names(root)
        except Exception as e:
            print(f"  [resolve] nearby Text dump skipped: {e}")
        return None
    if not has_name_filter and not ctype:
        _log_layer(2, "a11y semantic", False, "no name/type filters")
        return None

    import uiautomation as auto

    try:
        root = auto.GetRootControl()
        hint = ref.semantic.window_title_hint
        if hint:
            win = _walk_find(root, name=hint)
            if win is not None:
                root = win
        if ref.a11y.anchor_name:
            anchor = _walk_find(root, name=ref.a11y.anchor_name)
            if anchor is not None:
                root = anchor
        _dump_email_ancestor_chain(root)
        unscoped_root = root
        sub_hint = (ref.a11y.subtree_root or "").strip()
        if sub_hint:
            scoped = _walk_find_last(root, name=sub_hint)
            if scoped is None:
                print(
                    f"  [resolve] WARNING: subtree_root={sub_hint!r} not found; "
                    "failing open to unscoped tree"
                )
            else:
                try:
                    n_kids = len(scoped.GetChildren() or [])
                except Exception:
                    n_kids = -1
                if n_kids == 0:
                    print(
                        f"  [resolve] WARNING: subtree_root={sub_hint!r} "
                        f"name={(scoped.Name or '')!r} is a leaf "
                        f"(child_count=0); failing open to unscoped tree"
                    )
                else:
                    print(
                        f"  [resolve] layer 2 scoped to subtree_root={sub_hint!r} "
                        f"name={(scoped.Name or '')!r} child_count={n_kids}"
                    )
                    root = scoped

        def _collect_hits(search_root):
            found = []
            for c in _iter_controls(search_root):
                try:
                    cname = (c.Name or "").strip()
                    if not cname:
                        continue
                    if not _type_matches(c, ctype or None):
                        continue
                    if has_name_filter and not _name_filters_match(
                        cname,
                        name=name or None,
                        name_contains=name_contains,
                        name_regex=name_regex,
                    ):
                        continue
                    box = _ctrl_bbox(c)
                    if box is None:
                        continue
                    found.append((c, cname, box))
                except Exception:
                    continue
            return found

        hits = _collect_hits(root)
        if not hits and root is not unscoped_root:
            print(
                "  [resolve] WARNING: scoped subtree produced 0 hits; "
                "failing open to unscoped tree"
            )
            root = unscoped_root
            hits = _collect_hits(root)

        if "text" in ctype.lower() or not hits:
            _log_nearby_text_names(root)

        if not hits:
            _log_layer(
                2,
                "a11y semantic",
                False,
                f"name={name!r} contains={name_contains!r} regex={name_regex!r} "
                f"type={ctype!r} nth={nth}",
            )
            return None

        print(f"  [resolve] layer 2 hit_count={len(hits)} nth={nth}")

        if nth is not None:
            if nth < 1 or nth > len(hits):
                _log_layer(
                    2,
                    "a11y semantic",
                    False,
                    f"nth_of_type={nth} but only {len(hits)} hits",
                )
                return None
            ctrl, cname, box = hits[nth - 1]
        elif len(hits) > 1:
            # Ambiguous: first-hit would pick e.g. 'My Network' out of 155.
            sample = [h[1][:40] for h in hits[:8]]
            _log_layer(
                2,
                "a11y semantic",
                False,
                f"ambiguous hits={len(hits)} (no nth_of_type); "
                f"sample={sample} — falling through",
            )
            return None
        else:
            ctrl, cname, box = hits[0]

        cx, cy = _ctrl_center(box)
        _log_layer(
            2,
            "a11y semantic",
            True,
            f"name={cname!r} type={ctype!r} -> ({cx},{cy}) "
            f"(hits={len(hits)} nth={nth})",
        )
        return ResolveResult(
            success=True,
            layer_used=2,
            coordinates=(cx, cy),
            confidence=0.8,
            notes=f"a11y semantic name={cname!r} type={ctype!r}",
        )
    except Exception as e:
        _log_layer(2, "a11y semantic", False, str(e))
        return None


def _format_som_candidates(elements: list) -> str:
    """Numbered SoM list: id, control_type, name, bbox — full set, not a sample."""
    lines = []
    for el in elements:
        eid = el.get("id")
        ctype = el.get("control_type") or el.get("control_type") or ""
        name = el.get("name") or ""
        box = el.get("rect") or el.get("bbox")
        if box is None and el.get("cx") is not None:
            box = (el.get("cx"), el.get("cy"), el.get("cx"), el.get("cy"))
        lines.append(
            f"  id={eid} control_type={ctype} name={name!r} bbox={box}"
        )
    return "\n".join(lines)


def _layer3_prompt(
    desc: str,
    app: str,
    hint: str | None,
    listing: str,
    id_min: int,
    id_max: int,
) -> str:
    n = max(0, id_max - id_min + 1) if id_max >= id_min else 0
    return (
        f"{_JSON_ONLY_INSTRUCTION}\n"
        f"The screenshot has numbered Set-of-Mark boxes. Match against the "
        f"candidate list below — do not invent an id.\n"
        f"Description:\n  {desc}\n"
        f"App hint: {app}; window hint: {hint}\n"
        f"Valid ids: integers from {id_min} to {id_max} inclusive ({n} candidates), "
        f"or null if none match.\n"
        f"Candidates (id, control_type, name, bbox):\n{listing}\n"
        'Return ONLY a JSON object, no prose, no markdown fences: '
        '{"id": <int or null>, "confidence": 0-1, "why": "..."}. '
        f"id must be between {id_min} and {id_max}, or null."
    )


def _som_pick_from_obj(
    obj: dict, elements: list
) -> tuple[dict | None, int | None, str, float, int | None]:
    """Return (element, id, why, conf, bad_id). bad_id is set when out of range."""
    why = str(obj.get("why") or "")
    conf = float(obj.get("confidence") or 0.7)
    chosen = obj.get("id")
    if chosen is None:
        return None, None, why or "id=null", conf, None
    try:
        chosen_id = int(chosen)
    except (TypeError, ValueError):
        return None, None, f"bad id {chosen!r}", conf, None
    el = next((e for e in elements if e.get("id") == chosen_id), None)
    if el is None:
        return None, None, f"id {chosen_id} not in list", conf, chosen_id
    return el, chosen_id, why, conf, None


# ---------------------------------------------------------------------------
# Layer 3 — vision SoM
# ---------------------------------------------------------------------------


def _layer3_vision_som(ref: ElementRef) -> ResolveResult | None:
    desc = ref.semantic.description
    app = ref.semantic.app
    hint = ref.semantic.window_title_hint

    # Bring target app forward so SoM tree matches the screenshot
    if app:
        focus_app([app], title_hint=hint)

    subtree = None
    if ref.a11y:
        subtree = (ref.a11y.subtree_root or "").strip() or None

    elements, marked_path, meta = capture_som_marked(
        no_focus=False,
        redact=True,
        proc_names=[app] if app else None,
        title_hint=hint,
        subtree_root=subtree,
    )
    if not elements:
        _log_layer(3, "vision SoM", False, "no SoM elements")
        return None

    valid_ids = [int(el["id"]) for el in elements if el.get("id") is not None]
    id_min, id_max = (min(valid_ids), max(valid_ids)) if valid_ids else (1, 0)
    listing = _format_som_candidates(elements)
    prompt = _layer3_prompt(desc, app, hint, listing, id_min, id_max)
    obj, err = _call_vision_json(marked_path, prompt, max_tokens=500)
    if obj is None:
        _log_layer(3, "vision SoM", False, err)
        return None

    el, chosen_id, why, conf, bad = _som_pick_from_obj(obj, elements)
    if el is not None:
        cx, cy = int(el["cx"]), int(el["cy"])
        _log_layer(3, "vision SoM", True, f"#{chosen_id} -> ({cx},{cy}) {why[:80]}")
        return ResolveResult(
            success=True,
            layer_used=3,
            coordinates=(cx, cy),
            confidence=conf,
            notes=f"vision SoM #{chosen_id}: {why}",
        )
    if bad is None:
        _log_layer(3, "vision SoM", False, why or "id=null")
        return None

    print(
        f"  [resolve] layer 3 id={bad} out of range "
        f"(valid {id_min}-{id_max}); retrying with range in prompt"
    )
    retry_prompt = (
        f"{prompt}\n\n"
        f"The id {bad} is invalid. Choose an id between {id_min} and {id_max} "
        f"from the candidate list, or null. "
        f"{_JSON_ONLY_INSTRUCTION}"
    )
    obj2, err2 = _call_vision_json(marked_path, retry_prompt, max_tokens=500)
    if obj2 is None:
        _log_layer(3, "vision SoM", False, err2 or "retry failed")
        return None
    el, chosen_id, why, conf, bad2 = _som_pick_from_obj(obj2, elements)
    if el is None:
        _log_layer(
            3,
            "vision SoM",
            False,
            f"id still invalid ({bad2!r}); falling through to layer 4",
        )
        return None
    cx, cy = int(el["cx"]), int(el["cy"])
    _log_layer(3, "vision SoM", True, f"#{chosen_id} -> ({cx},{cy}) {why[:80]} (retry)")
    return ResolveResult(
        success=True,
        layer_used=3,
        coordinates=(cx, cy),
        confidence=conf,
        notes=f"vision SoM #{chosen_id}: {why}",
    )


# ---------------------------------------------------------------------------
# Layer 4 — visual template match + VLM verify
# ---------------------------------------------------------------------------


def _layer4_visual_template(ref: ElementRef) -> ResolveResult | None:
    if not ref.visual or not ref.visual.crop_path:
        _log_layer(4, "visual template", False, "no visual.crop_path")
        return None
    crop_path = ref.visual.crop_path
    if not Path(crop_path).is_file():
        _log_layer(4, "visual template", False, f"missing file {crop_path}")
        return None

    import cv2

    raw_path, meta = capture_raw_no_focus()
    screen = cv2.imread(raw_path)
    templ = cv2.imread(crop_path)
    if screen is None or templ is None:
        _log_layer(4, "visual template", False, "cv2 could not read images")
        return None
    if templ.shape[0] > screen.shape[0] or templ.shape[1] > screen.shape[1]:
        _log_layer(4, "visual template", False, "template larger than screen")
        return None

    result = cv2.matchTemplate(screen, templ, cv2.TM_CCOEFF_NORMED)
    _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
    if max_val < 0.55:
        _log_layer(4, "visual template", False, f"low match conf={max_val:.3f}")
        return None

    th, tw = templ.shape[:2]
    # max_loc is image-pixel top-left; convert to screen via meta
    ix = max_loc[0] + tw // 2
    iy = max_loc[1] + th // 2
    scale = float(meta.get("scale") or 1.0) or 1.0
    ox = int(meta.get("ox") or 0)
    oy = int(meta.get("oy") or 0)
    sx = int(ix / scale + ox)
    sy = int(iy / scale + oy)

    verify_path = crop_around_screen_point(raw_path, meta, sx, sy)
    prompt = (
        f"Does this crop show the UI element described as: "
        f"{ref.semantic.description!r}? "
        'Return ONLY JSON: {"is_target": true/false, "confidence": 0-1, '
        '"what_you_see": "..."}.'
    )
    obj, err = _call_vision_json(verify_path, prompt, max_tokens=200)
    if obj is None:
        _log_layer(4, "visual template", False, f"vlm verify failed: {err}")
        return None
    if not obj.get("is_target"):
        _log_layer(
            4,
            "visual template",
            False,
            f"vlm rejected: {obj.get('what_you_see')}",
        )
        return None

    conf = float(obj.get("confidence") or max_val)
    _log_layer(
        4,
        "visual template",
        True,
        f"match={max_val:.3f} -> ({sx},{sy}) verified",
    )
    return ResolveResult(
        success=True,
        layer_used=4,
        coordinates=(sx, sy),
        confidence=conf,
        notes=f"template match={max_val:.3f}; {obj.get('what_you_see')}",
    )


# ---------------------------------------------------------------------------
# Layer 5 — escalate (no click)
# ---------------------------------------------------------------------------


def _layer5_escalate(ref: ElementRef, prior_notes: str) -> ResolveResult:
    msg = (
        f"Could not resolve element. Need human help. "
        f"description={ref.semantic.description!r} app={ref.semantic.app!r}. "
        f"Prior: {prior_notes}"
    )
    _log_layer(5, "escalate", True, "requesting human assistance (no click)")
    return ResolveResult(
        success=False,
        layer_used=5,
        coordinates=None,
        confidence=0.0,
        notes=msg,
    )


def resolve(ref: ElementRef) -> ResolveResult:
    """Try layers 1→5 in order. Logs which layer succeeded every call."""
    print(
        f"  [resolve] start desc={ref.semantic.description!r} "
        f"app={ref.semantic.app!r}"
    )
    notes: list[str] = []

    for layer_fn, label in (
        (_layer1_a11y_exact, "1"),
        (_layer2_a11y_semantic, "2"),
        (_layer3_vision_som, "3"),
        (_layer4_visual_template, "4"),
    ):
        try:
            result = layer_fn(ref)
        except Exception as e:
            notes.append(f"layer error: {e}")
            print(f"  [resolve] layer exception: {e}")
            result = None
        if result is not None and result.success:
            print(
                f"  [resolve] SUCCESS layer={result.layer_used} "
                f"coords={result.coordinates} conf={result.confidence:.2f}"
            )
            return result
        if result is not None:
            notes.append(result.notes)

    return _layer5_escalate(ref, " | ".join(notes) if notes else "all layers missed")
