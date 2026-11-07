import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "dropi_tracker.db"
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

# Query one product that has stock=0 or precio=0
row = conn.execute("SELECT * FROM productos LIMIT 5").fetchall()
for r in row:
    print(dict(r))
conn.close()
