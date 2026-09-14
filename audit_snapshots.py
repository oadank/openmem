import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import psycopg2

conn = psycopg2.connect(host='localhost', database='openmem', user='postgres')
cur = conn.cursor()

# Get snapshot groups (agent-matrix entries grouped by category)
cur.execute("""SELECT id, category, source, content, created_at
               FROM memory_entries 
               WHERE source = 'agent-matrix' AND superseded_by IS NULL
               ORDER BY category, created_at DESC""")
rows = cur.fetchall()

groups = {}
for r in rows:
    cat = r[1]
    groups.setdefault(cat, []).append({
        "id": str(r[0]), "category": cat, "content": r[3][:200], "created": r[4].isoformat()[:19]
    })

print(f"Total agent-matrix entries: {len(rows)}")
print(f"Snapshot groups: {len(groups)}")
print()

for cat, entries in groups.items():
    print(f"=== Category: {cat} ({len(entries)} entries) ===")
    for i, e in enumerate(entries):
        marker = "KEEP" if i == 0 else "ARCHIVE"
        print(f"  [{marker}] id={e['id']} created={e['created']}")
        print(f"       content[:150]={e['content'][:150]!r}")
    print()

conn.close()
