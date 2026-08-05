"""
MimicAgent Phase 5 - correction memory.

Remembers corrections and recalls them when a similar step appears on future runs.

API:
  open_memory(path)           - open/create the vector store
  remember(db, step, edit)    - embed the step situation and store the correction
  recall(db, step, threshold) - find the closest past correction for a step
  fingerprint(step)           - build a stable text description of a step (what we embed)
"""

import json
import sqlite3
import sqlite_vec
import ollama

EMBED_MODEL = "nomic-embed-text"
DEFAULT_DB   = "corrections.db"


# ---- text description of a step (the key we embed and later match against) ----
def fingerprint(step):
    """Build a stable plain-text description of a step's SITUATION.
    We embed the situation (what the step is about), not the correction sentence,
    so future similar steps match even when worded differently."""
    action = step.get("action", "")
    name   = step.get("elem_name", "something")
    if action == "type":
        return f"type text into the {name} field"
    if action == "click":
        return f"click the {name} element"
    return f"{action} on {name}"


def embed(text):
    """Turn text into a 768-float vector using the local embedding model."""
    r = ollama.embeddings(model=EMBED_MODEL, prompt=text)
    return r["embedding"]


# ---- open (or create) the vector store ----
def open_memory(path=DEFAULT_DB):
    """Open the corrections SQLite database and ensure the tables exist."""
    db = sqlite3.connect(path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)

    # the vector index (one row per remembered correction)
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_corrections
        USING vec0(embedding float[768])
    """)

    # the metadata table (situation text + what edit was made)
    db.execute("""
        CREATE TABLE IF NOT EXISTS corr_meta (
            rowid     INTEGER PRIMARY KEY,
            situation TEXT,
            edit_json TEXT
        )
    """)
    db.commit()
    return db


# ---- store a correction ----
def remember(db, step, edit):
    """Embed the step's situation and store (vector, edit) in the memory.
    Call this right after a correction is successfully applied."""
    situation = fingerprint(step)
    vec       = embed(situation)
    # use a stable row id derived from the situation so we don't duplicate
    rowid = abs(hash(situation)) % (2 ** 31)

    db.execute(
        "INSERT OR REPLACE INTO vec_corrections(rowid, embedding) VALUES (?, ?)",
        (rowid, json.dumps(vec))
    )
    db.execute(
        "INSERT OR REPLACE INTO corr_meta(rowid, situation, edit_json) VALUES (?, ?, ?)",
        (rowid, situation, json.dumps(edit))
    )
    db.commit()
    print(f"   [memory] remembered: '{situation}' -> {edit.get('edit')}")


# ---- recall the closest past correction ----
def recall(db, step, threshold=10.0):
    """Before doing a step, look for a close past correction.
    Returns (situation_text, edit_dict) if a close match exists, else (None, None).
    threshold: lower = stricter match (0.0 = identical, 1.0 = anything)."""
    situation = fingerprint(step)
    vec       = embed(situation)

    rows = db.execute("""
        SELECT rowid, distance
        FROM vec_corrections
        WHERE embedding MATCH ?
        ORDER BY distance LIMIT 1
    """, (json.dumps(vec),)).fetchall()

    if not rows:
        return None, None

    rowid, distance = rows[0]
    if distance > threshold:
        return None, None       # closest match is too far away - not similar enough

    meta = db.execute(
        "SELECT situation, edit_json FROM corr_meta WHERE rowid = ?", (rowid,)
    ).fetchone()

    if not meta:
        return None, None

    past_situation, edit_json = meta
    return past_situation, json.loads(edit_json)


# =====================================================================
# standalone test - store a correction, then recall it
# =====================================================================
if __name__ == "__main__":
    db = open_memory("test_corrections.db")

    step_email   = {"action": "type", "elem_name": "Email", "text": "old@example.com"}
    edit_email   = {"edit": "change_text", "new_text": "yash@gmail.com"}
    step_submit  = {"action": "click", "elem_name": "Submit"}
    step_contact = {"action": "type", "elem_name": "Contact Email", "text": ""}
    step_username = {"action": "type", "elem_name": "Username", "text": ""}

    print("--- storing a correction for the Email field ---")
    remember(db, step_email, edit_email)

    print("\n--- distances from the stored 'Email field' correction ---")
    for label, step in [
        ("identical  (Email)",         step_email),
        ("similar    (Contact Email)", step_contact),
        ("related    (Username)",      step_username),
        ("different  (Submit click)",  step_submit),
    ]:
        fp  = fingerprint(step)
        vec = embed(fp)
        rows = db.execute(
            "SELECT distance FROM vec_corrections WHERE embedding MATCH ? ORDER BY distance LIMIT 1",
            (json.dumps(vec),)
        ).fetchall()
        dist = rows[0][0] if rows else None
        print(f"   {label:28} '{fp}'  distance = {dist}")

    print("\n--- recall() results at threshold=10.0 ---")
    for label, step in [("Email", step_email), ("Contact Email", step_contact),
                        ("Submit", step_submit)]:
        past, edit = recall(db, step, threshold=10.0)
        got = edit["edit"] if edit else "nothing"
        print(f"   {label:14} -> recalled: {got}")

    db.close()
    import os
    os.remove("test_corrections.db")
    print("\n--- diagnostic complete ---")