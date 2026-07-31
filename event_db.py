import sqlite3

# 1. Connect — creates recording.db in this folder if it doesn't exist yet
conn = sqlite3.connect("recording.db")

# 2. Turn on WAL mode (readers never block writers; crash-safe).
#    You'll see a recording.db-wal file appear next to recording.db after this runs.
conn.execute("PRAGMA journal_mode=WAL;")

# 3. Create the table (only if it isn't already there)
conn.execute("""
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,
    x           INTEGER,
    y           INTEGER,
    key         TEXT,
    elem_name   TEXT,
    elem_type   TEXT,
    screenshot  TEXT
)
""")

# 4. Five real events taken from your own spy.py run.
#    Column order: (ts, kind, x, y, key, elem_name, elem_type, screenshot)
#    - clicks: key is None (no keyboard key)
#    - keys:   x, y, elem_name, elem_type, screenshot are all None
#    None in Python  ->  NULL (a truly empty cell) in the table.
fake_events = [
    (1785023924.833, "click", 884, 15,  None,        "Yash Dayma | LinkedIn",       "TabItem",  "captures/click_1785023924.833.png"),
    (1785023954.919, "click", 22,  67,  None,        "Back",                        "Button",   "captures/click_1785023954.919.png"),
    (1785023983.227, "click", 1016, 762, None,       "LinkedIn",                    "Document", "captures/click_1785023983.227.png"),
    (1785024163.821, "key",   None, None, "Key.ctrl_l", None,                       None,       None),
    (1785024165.769, "click", 1404, 968, None,       "Write your prompt to Claude", "Edit",     "captures/click_1785024165.769.png"),
]

# 5. Insert all five rows in ONE batched call.
#    The ? placeholders keep SQL and data separate (safe against quotes/apostrophes).
conn.executemany(
    "INSERT INTO events (ts, kind, x, y, key, elem_name, elem_type, screenshot) VALUES (?,?,?,?,?,?,?,?)",
    fake_events,
)

# 6. Commit — turns the "pencil" writes into permanent "ink". Without this, nothing is saved.
conn.commit()

# 7. Read the rows back, ordered by time, to prove the round-trip worked.
print("Events in the database (ordered by time):")
for row in conn.execute("SELECT ts, kind, x, y, key, elem_name FROM events ORDER BY ts"):
    print(row)

conn.close()