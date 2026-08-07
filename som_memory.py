"""
Stage A step 5: cache Set-of-Mark adaptations so repeats are free and offline.

Reuses the Phase 5 memory machinery (embeddings + sqlite-vec). When Set-of-Mark
resolves a failed step via the API, we remember the SITUATION -> the element it
picked. On the next run, before spending an API call, we check this memory: if
we have adapted this same situation before, reuse the remembered element locally.

This is what keeps the online tier RARE - a workflow run repeatedly converges
back to running on the fast local tiers.
"""

import json
import sqlite3
import sqlite_vec
import ollama

EMBED_MODEL = "nomic-embed-text"
DB = "adaptations.db"


def _embed(text):
    return ollama.embeddings(model=EMBED_MODEL, prompt=text)["embedding"]


def _situation(step):
    """A stable text description of the step we had to adapt."""
    name = step.get("elem_name") or step.get("instruction") or "element"
    action = step.get("action", "click")
    return f"{action} the {name}"


def open_adapt_memory(path=DB):
    db = sqlite3.connect(path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS vec_adapt USING vec0(embedding float[768])")
    db.execute("""CREATE TABLE IF NOT EXISTS adapt_meta (
                     rowid INTEGER PRIMARY KEY, situation TEXT, picked TEXT)""")
    db.commit()
    return db


def remember_adaptation(db, step, picked_element):
    """Store: this situation was adapted by picking this element (by its name/type)."""
    sit = _situation(step)
    vec = _embed(sit)
    rowid = abs(hash(sit)) % (2 ** 31)
    picked = {"name": picked_element.get("what_you_see", ""),
              "som_id": picked_element.get("som_id")}
    db.execute("INSERT OR REPLACE INTO vec_adapt(rowid, embedding) VALUES (?, ?)",
               (rowid, json.dumps(vec)))
    db.execute("INSERT OR REPLACE INTO adapt_meta(rowid, situation, picked) VALUES (?, ?, ?)",
               (rowid, sit, json.dumps(picked)))
    db.commit()
    print(f"      [adapt-memory] remembered: '{sit}' -> {picked['name']}")


def recall_adaptation(db, step, threshold=10.0):
    """Return the remembered picked-element name for a similar past situation, or None.
    We match on the situation; the caller then re-finds that named element via the
    fast tiers (which now WILL succeed because we know its real name)."""
    sit = _situation(step)
    vec = _embed(sit)
    rows = db.execute("SELECT rowid, distance FROM vec_adapt WHERE embedding MATCH ? "
                      "ORDER BY distance LIMIT 1", (json.dumps(vec),)).fetchall()
    if not rows or rows[0][1] > threshold:
        return None
    meta = db.execute("SELECT picked FROM adapt_meta WHERE rowid = ?", (rows[0][0],)).fetchone()
    if not meta:
        return None
    return json.loads(meta[0])


if __name__ == "__main__":
    db = open_adapt_memory("test_adapt.db")
    step = {"action": "click", "elem_name": "the settings gear icon", "instruction": "open settings"}
    picked = {"what_you_see": "Button 'Manage'", "som_id": 54}

    print("--- remember an adaptation ---")
    remember_adaptation(db, step, picked)

    print("\n--- recall it (same situation) ---")
    got = recall_adaptation(db, step)
    print("recalled:", got)

    print("\n--- recall with an unrelated step ---")
    other = {"action": "click", "elem_name": "the print button", "instruction": "print"}
    print("recalled:", recall_adaptation(db, other))

    db.close()
    import os; os.remove("test_adapt.db")
    print("\n--- test done ---")