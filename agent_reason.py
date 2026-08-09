"""
Stage B step 2: REASON - ask the model for the single next action.

Given a goal + the perceived screen, the strong model returns ONE action from a
closed vocabulary (never a whole plan):
  {"action": "click", "id": <box number>, "why": "..."}
  {"action": "type",  "text": "...",      "why": "..."}
  {"action": "press", "key": "enter",     "why": "..."}
  {"action": "scroll","direction":"down", "to_find": "optional", "why": "..."}
  {"action": "navigate","url":"https://...","why":"..."}
  {"action": "switch_tab","match":"<title or url text>","why":"..."}
  {"action": "copy",  "why": "..."}
  {"action": "paste", "why": "..."}
  {"action": "wait",  "seconds": <n>,     "why": "..."}
  {"action": "hotkey","keys":"^a",        "why": "..."}
  {"action": "done",                       "why": "goal reached"}
  {"action": "stuck",                      "why": "cannot proceed"}

Closed vocabulary = every output is something the engine can execute + validate.
"""

import json
import base64
import requests

try:
    from config import API_MODEL, KEY_FILE
except Exception:
    API_MODEL = "claude-sonnet-4-5"
    KEY_FILE = "my_key.txt"

ALLOWED_ACTIONS = {
    "click", "type", "press", "scroll",
    "navigate", "switch_tab", "copy", "paste", "wait", "hotkey",
    "done", "stuck",
}

# Cap history lines sent to the model to keep prompts bounded
_HISTORY_CAP = 12


def _load_key():
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def reason_next_action(goal, elements, image_path, history=None, correction=None):
    """Ask the model for the single next action toward the goal.
    Returns an action dict, or {"action":"stuck", ...} on error.
    If correction is set, the model must follow that mid-step user instruction."""
    key = _load_key()
    if not key:
        return {"action": "stuck", "why": "no API key"}

    menu = "\n".join(f"{el['id']}: {el['control_type']} '{el['name']}'" for el in elements)
    if not menu.strip():
        menu = ("(no page elements visible — blank/New Tab or empty DOM. "
                "If the goal needs a website, use navigate with a full https URL. "
                "Do NOT click or type in the browser address bar.)")
    hist = ""
    if history:
        recent = history[-_HISTORY_CAP:]
        hist = "\n\nActions taken so far:\n" + "\n".join(f"- {h}" for h in recent)

    corr = ""
    if correction:
        corr = (
            f'\nThe user is correcting your previous choice. Follow this instruction for the '
            f'next action: "{correction}". Choose the single next action that follows it.\n'
        )

    prompt = f"""You are a careful desktop automation agent. Your GOAL is:
"{goal}"
{corr}
The screen has numbered red boxes on every clickable element. Here they are:
{menu}
{hist}

Decide the SINGLE next action to move toward the goal. Do not plan ahead, just
the one next action. To go to a website or URL, ALWAYS use the navigate action ({{"action":"navigate","url":"https://..."}}). Do NOT click or type in the browser address bar. The navigate action loads the page directly. Only interact with elements that are part of the web PAGE, never the browser's address bar or toolbar. To type text, prefer clicking the main editable text AREA (a Document or Edit element) rather than a tab or title, then use the type action. For typing, use type_mode "replace" to overwrite a field (default for form fields) or "append" to add to existing text. You may use navigate to go straight to a known URL rather than clicking through, switch_tab to move between open tabs, copy/paste to move text between fields, wait when a page is loading, and hotkey for keyboard combos (e.g. ^a, ^{{END}}). If a target is off-screen, use scroll with optional to_find (text of the target) so the page brings it into view; then click it on a later step. Only return done if the CURRENT screenshot visibly shows the goal is complete. Do not claim done based only on past actions in the history. Respond with ONLY a JSON object, one of:
{{"action":"click","id":<number>,"why":"<short>"}}
{{"action":"type","text":"<text>","type_mode":"<replace|append>","why":"<short>"}}
{{"action":"press","key":"<enter|tab|esc|...>","why":"<short>"}}
{{"action":"scroll","direction":"<up|down>","to_find":"<optional off-screen target text>","why":"<short>"}}
{{"action":"navigate","url":"https://...","why":"<short>"}}
{{"action":"switch_tab","match":"<text in tab title or url>","why":"<short>"}}
{{"action":"copy","why":"<short>"}}
{{"action":"paste","why":"<short>"}}
{{"action":"wait","seconds":<n>,"why":"<short>"}}
{{"action":"hotkey","keys":"<pywinauto send_keys chord>","why":"<short>"}}
{{"action":"done","why":"the goal is already satisfied"}}
{{"action":"stuck","why":"cannot find a way forward"}}"""

    try:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        if not key.startswith("sk-ant"):
            return {"action": "stuck", "why": "only Claude keys wired"}
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": API_MODEL, "max_tokens": 400,
                  "messages": [{"role": "user", "content": [
                      {"type": "image", "source": {"type": "base64",
                       "media_type": "image/png", "data": b64}},
                      {"type": "text", "text": prompt}]}]},
            timeout=60)
        r.raise_for_status()
        raw = r.json()["content"][0]["text"]
        action = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        act = action.get("action")
        # gracefully map common near-misses to the closed vocabulary
        alias = {"key": "press", "keypress": "press", "keyboard": "press",
                 "click_element": "click", "tap": "click", "input": "type",
                 "enter": "press", "finish": "done", "complete": "done",
                 "goto": "navigate", "open_url": "navigate", "url": "navigate",
                 "tab": "switch_tab", "switch": "switch_tab",
                 "sleep": "wait", "keydown": "hotkey", "shortcut": "hotkey"}
        if act in alias:
            action["action"] = alias[act]
            act = alias[act]
        if act not in ALLOWED_ACTIONS:
            # show what the model actually returned so we can see the real shape
            return {"action": "stuck", "why": f"unrecognized action shape: {action}"}
        return action
    except Exception as e:
        return {"action": "stuck", "why": f"error: {e}"}


if __name__ == "__main__":
    import sys
    from agent_loop import perceive
    goal = sys.argv[1] if len(sys.argv) > 1 else "open the View menu"
    print(f"=== Stage B step 2: REASON toward goal '{goal}' ===")
    elements, path, _page_info = perceive()
    print(f"perceived {len(elements)} elements")
    action = reason_next_action(goal, elements, path)
    print(f"\nproposed next action: {action}")
    if action.get("action") == "click":
        match = next((e for e in elements if e["id"] == action.get("id")), None)
        if match:
            print(f"  -> would click box {action['id']}: "
                  f"{match['control_type']} '{match['name']}'")
