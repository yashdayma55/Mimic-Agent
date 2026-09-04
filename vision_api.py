"""
Multi-provider vision API adapter for MimicAgent's Tier 5.

Drop in ANY key from Claude / OpenAI / Gemini. It auto-detects the provider
from the key format and routes to the right adapter, normalizing every
response to the same shape: {"found", "what_you_see", "confidence"}.

Adding a new provider later = one small adapter function + one detect rule.
"""

import base64
import json
import requests


# ---- the one prompt, shared across providers ----
PROMPT = (
    "Look at the CENTER of this cropped screenshot. Is there a clickable UI "
    "element there (a button, menu, link, icon, or field)? "
    'Respond ONLY with JSON: {"found": true/false, "what_you_see": "what the element is", '
    '"confidence": "high/medium/low"}. Set found=true if any clickable element is near the center.'
)


def detect_provider(api_key):
    """Guess the provider from the key's format."""
    k = api_key.strip()
    if k.startswith("sk-ant-"):
        return "claude"
    if k.startswith("sk-"):
        return "openai"
    if k.startswith("AIza"):
        return "gemini"
    return "unknown"


def _parse(raw_text):
    """Pull the JSON object out of the model text (tolerant of stray text)."""
    try:
        start = raw_text.find("{")
        end = raw_text.rfind("}") + 1
        return json.loads(raw_text[start:end])
    except Exception:
        return {"found": False, "what_you_see": f"could not parse: {raw_text[:60]}", "confidence": "low"}


# ---- ADAPTER: Anthropic Claude ----
def _ask_claude(image_bytes, api_key):
    b64 = base64.b64encode(image_bytes).decode()
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
           "model": "claude-sonnet-4-5",
            "max_tokens": 200,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": PROMPT},
                ],
            }],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]


# ---- ADAPTER: OpenAI GPT-4o ----
def _ask_openai(image_bytes, api_key):
    b64 = base64.b64encode(image_bytes).decode()
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 200,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ---- ADAPTER: Google Gemini ----
def _ask_gemini(image_bytes, api_key):
    b64 = base64.b64encode(image_bytes).decode()
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-1.5-flash:generateContent?key={api_key}")
    resp = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{
                "parts": [
                    {"text": PROMPT},
                    {"inline_data": {"mime_type": "image/png", "data": b64}},
                ]
            }]
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


# ---- the single entry point the rest of MimicAgent calls ----
_ADAPTERS = {
    "claude": _ask_claude,
    "openai": _ask_openai,
    "gemini": _ask_gemini,
}


def ask_vision_api(image_bytes, api_key, provider=None):
    """Send the image to whichever provider the key belongs to.
    Returns normalized {found, what_you_see, confidence}."""
    return ask_vision_with_prompt(image_bytes, api_key, PROMPT, provider=provider)


def ask_vision_with_prompt(image_bytes, api_key, prompt: str, provider=None):
    """Vision call with a custom prompt; returns parsed JSON dict."""
    provider = provider or detect_provider(api_key)
    if provider not in _ADAPTERS:
        return {"found": False, "what_you_see": "unknown provider for this key", "confidence": "low"}
    try:
        raw = _ask_with_prompt(image_bytes, api_key, prompt, provider)
        result = _parse(raw)
        result["provider"] = provider
        return result
    except Exception as e:
        return {"found": False, "what_you_see": f"api error: {e}", "confidence": "low"}


def _ask_with_prompt(image_bytes, api_key, prompt: str, provider: str) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    if provider == "claude":
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-5",
                "max_tokens": 300,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                        {"type": "text", "text": prompt},
                    ],
                }],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]
    if provider == "openai":
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o",
                "max_tokens": 300,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    if provider == "gemini":
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            json={
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/png", "data": b64}},
                    ],
                }],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    raise ValueError(f"unsupported provider {provider}")


def ask_vision_freeform(image_bytes, api_key, question: str, provider=None) -> str:
    """Ask a free-form question about a screenshot; returns plain text (no OS input)."""
    provider = provider or detect_provider(api_key)
    if provider not in _ADAPTERS:
        return "unknown provider for this key"
    prompt = (
        (question or "").strip()
        + "\n\nAnswer in plain English based only on what you see in the screenshot. "
        "Be specific about UI elements, text, and their locations."
    )
    try:
        return (_ask_with_prompt(image_bytes, api_key, prompt, provider) or "").strip()
    except Exception as e:
        return f"vision error: {e}"


if __name__ == "__main__":
    # quick offline check: detection only (no real call)
    for test_key in ["sk-ant-abc123", "sk-proj-abc123", "AIzaSyAbc123", "weird-key"]:
        print(f"{test_key[:14]:16} -> {detect_provider(test_key)}")