import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import numpy as np
import psycopg2

conn = psycopg2.connect(host='localhost', database='openmem', user='postgres')
cur = conn.cursor()

# The 8 sample clusters identified by their keep entry IDs
# From the scan output:
samples = [
    {"sim": 0.925, "keep": "44a0ba33-5706-4ed3-a7a6-1657f7eb189", "desc": "飞书 bot 间通信正确姿势"},
    {"sim": 0.92,  "keep": "1dc3a672-fb68-491f-a335-1d750ce750a", "desc": "2026-08-30 断电重启"},
    {"sim": 0.973, "keep": "2bc500c0-8420-414f-841c-7e0502c23d49", "desc": "项目进度模板"},
    {"sim": 0.939, "keep": "17ca8402-d6f5-43da-aedb-d6b8745ebb13", "desc": "缓存命中率数据链修复"},
    {"sim": 0.946, "keep": "8bb9f2ef-9b86-4b55-a167-0a8b527f5377", "desc": "能力配齐"},
    {"sim": 0.932, "keep": "08f3b063-bf1b-4cc8-be46-dcfd0ea0e79d", "desc": "四家 bot 终验修复"},
    {"sim": 0.963, "keep": "469e510e-98c3-483b-a9e0-06b4b2eec263", "desc": "配置精准穿透五原则"},
    {"sim": 0.966, "keep": "ffc498c2-15b3-4331-bc8a-d9f8c975696e", "desc": "思考层消失修复"},
]

# Actually, let me just re-run the scan and match by the sample text prefixes
# from the task description. Let me get all clusters and match.

cur.execute("""SELECT id, layer, category, source, content, embedding, pinned, created_at
               FROM memory_entries WHERE superseded_by IS NULL ORDER BY created_at DESC""")
rows = cur.fetchall()

meta = []
for r in rows:
    meta.append({"id": str(r[0]), "layer": r[1], "category": r[2], "source": r[3],
                 "content": r[4], "pinned": r[6],
                 "created": r[7].isoformat()[:10] if r[7] else ""})

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
clusters, seen = [], []
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
            seen.extend(ids)
            clusters.append({
                "keep": mi["id"],
                "archive": [normal[idxs[b]]["id"] for b in group],
                "sim": round(float(max(sim[a][b] for b in group)), 3),
                "sample": mi["content"],
                "members": [{"id": normal[idxs[b]]["id"], "content": normal[idxs[b]]["content"],
                             "created": normal[idxs[b]]["created"]} for b in [a] + group]})

# Now match the 8 samples by their sample text prefixes
# Sample 1: 飞书 bot 间通信正确姿势
# Sample 2: 2026-08-30 断电重启
# Sample 3: 项目进度模板
# Sample 4: 缓存命中率数据链修复
# Sample 5: 能力配齐
# Sample 6: 四家 bot 终验修复
# Sample 7: 配置精准穿透五原则
# Sample 8: 思考层消失修复

keywords = [
    ("飞书 bot 间通信正确姿势", 0.925),
    ("断电重启后 13600", 0.92),
    ("项目进度模板", 0.973),
    ("缓存命中率数据链修复", 0.939),
    ("能力配齐", 0.946),
    ("四家 bot 终验修复", 0.932),
    ("配置精准穿透五原则", 0.963),
    ("思考层消失修复", 0.966),
]

matched = []
for kw, expected_sim in keywords:
    for c in clusters:
        if kw in c["sample"] and abs(c["sim"] - expected_sim) < 0.01:
            matched.append((kw, c))
            break

print(f"Matched {len(matched)} of {len(keywords)} samples")
print()

for kw, c in matched:
    print("=" * 100)
    print(f"### 样本: {kw} (相似度 {c['sim']})")
    print(f"KEEP id={c['keep']} created={c['members'][0]['created']}")
    print(f"KEEP 内容全文:")
    print(c['members'][0]['content'])
    print()
    for j, m in enumerate(c['members'][1:]):
        print(f"--- ARCHIVE[{j}] id={m['id']} created={m['created']}")
        print(f"ARCHIVE[{j}] 内容全文:")
        print(m['content'])
        print()
    print()

conn.close()
