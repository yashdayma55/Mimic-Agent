import sqlite3

conn = sqlite3.connect("recording.db")
conn.row_factory = sqlite3.Row          # lets us read columns by name

rows = conn.execute("SELECT * FROM events ORDER BY ts").fetchall()

print(f"Total events: {len(rows)}\n")

for i, e in enumerate(rows, 1):
    if e["kind"] == "click":
        print(f"{i:3}. CLICK ({e['x']},{e['y']}) -> '{e['elem_name']}' [{e['elem_type']}]")
    else:
        print(f"{i:3}. KEY   {e['key']}")

conn.close()