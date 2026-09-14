import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
import psycopg2

conn = psycopg2.connect(host='localhost', database='openmem', user='postgres')
cur = conn.cursor()

cur.execute("""SELECT id, layer, category, source, content, embedding, pinned, created_at
               FROM memory_entries WHERE superseded_by IS NULL ORDER BY created_at DESC""")
rows = cur.fetchall()

meta = []
for r in rows:
    meta.append({"id": str(r[0]), "layer": r[1], "category": r[2], "source": r[3],
                 "content": r[4], "pinned": r[6],
                 "created": r[7].isoformat()[:10] if r[7] else ""})

# A. snapshots
snapshots = []
groups = {}
for m in meta:
    if m["source"] != "agent-matrix" or m["pinned"]:
        continue
    key = m["category"]
    groups.setdefault(key, []).append(m)
for key, ms in groups.items():
    if len(ms) > 1:
        snapshots.append({"keep": ms[0]["id"], "archive": [m["id"] for m in ms[1:]],
                          "source": key, "count": len(ms) - 1,
                          "sample": ms[0]["content"][:80]})

# B. clusters
normal = [m for m in meta if m["source"] != "agent-matrix"]
emap = {}
if normal:
    ids = [m["id"] for m in normal]
    ph = ",".join(["%s"] * len(ids))
    cur.execute(f"SELECT id, embedding FROM memory_entries WHERE id IN ({ph}) AND superseded_by IS NULL", tuple(ids))
    for rid, emb in cur.fetchall():
        try:
            emap[str(rid)] = np.array([float(x) for x in
                                       (emb if isinstance(emb, list) else str(emb).strip("{}").split(","))],
                                      dtype=np.float32)
        except Exception:
            continue

idxs = [i for i, m in enumerate(normal) if m["id"] in emap and not m["pinned"]]
clusters, seen = [], set()
if len(idxs) > 1:
    ids_list = [normal[i]["id"] for i in idxs]
    mat = np.stack([emap[i] for i in ids_list])
    norms = np.array([np.linalg.norm(v) + 1e-9 for v in mat])
    sim = mat @ mat.T / (norms[:, None] * norms[None, :])
    for a in range(len(idxs)):
        mi = normal[idxs[a]]
        if mi["id"] in seen or mi["pinned"]:
            continue
        group = [b for b in range(a + 1, len(idxs))
                 if sim[a][b] > 0.92 and normal[idxs[b]]["id"] not in seen]
        if group:
            ids = [mi["id"]] + [normal[idxs[b]]["id"] for b in group]
            seen.update(ids)
            clusters.append({
                "keep": mi["id"],
                "archive": [normal[idxs[b]]["id"] for b in group],
                "sim": round(float(max(sim[a][b] for b in group)), 3),
                "sample": mi["content"],
                "members": [{"id": normal[idxs[b]]["id"], "content": normal[idxs[b]]["content"],
                             "created": normal[idxs[b]]["created"]} for b in [a] + group]})

print("=" * 80)
print(f"TOTAL clusters: {len(clusters)}, snapshots: {len(snapshots)}")
print("=" * 80)

# Print all clusters with their sample text to identify the 8 samples
for i, c in enumerate(clusters):
    print(f"\n--- Cluster {i} (sim={c['sim']}) ---")
    print(f"  KEEP id={c['keep']} created={c['members'][0]['created']}")
    print(f"  KEEP content[:150]={c['sample'][:150]!r}")
    for j, m in enumerate(c['members'][1:]):
        print(f"  ARCHIVE[{j}] id={m['id']} created={m['created']}")
        print(f"  ARCHIVE[{j}] content[:150]={m['content'][:150]!r}")

conn.close()
