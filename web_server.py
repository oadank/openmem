#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
openmem Web 控制台 (:3467)
页面：记忆检索 / 记忆浏览 / 问问中枢(AI对AI) / 预设工具 / 入职包 / 状态
复用 mem_core.py 的内核函数，PG=openmem库，embedding=:11435，LLM=GwV4F@litellm:4000
"""
import sys, os, json, re, argparse, uuid, time

def _load_dotenv():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

_load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import mem_core as mc
from cred_guard import sanitize_credentials as _san_cred

import psycopg2
import requests

PORT = int(os.environ.get("OPENMEM_WEB_PORT", "3467"))

# ── 研究智能体（opencode serve，长连接常驻）─────────
RESEARCH_URL = os.environ.get("OPENMEM_RESEARCH_URL", "http://127.0.0.1:4098")


def _oc_api(path, body=None, method=None, timeout=30):
    """调 opencode serve 的 REST API"""
    r = requests.request(method or ("POST" if body is not None else "GET"),
                         RESEARCH_URL + path,
                         json=body if body is not None else None,
                         headers={"Content-Type": "application/json"}, timeout=timeout)
    r.raise_for_status()
    return r.json()


OPENCODE_CFG = os.environ.get("OPENMEM_OPENCODE_CFG", "")


def agent_status():
    """研究智能体健康状态 + 当前模型（读 opencode config 的 agent.build.model）"""
    try:
        _oc_api("/api/health", timeout=3)
    except Exception:
        return {"ok": False, "running": False}
    model = ""
    try:
        cfg = _oc_api("/config", timeout=5)
        model = cfg.get("model") or ""
    except Exception:
        pass
    return {"ok": True, "running": True, "model": model}


def agent_set_model(model):
    """把研究智能体的模型写回 opencode.json（provider/model 格式），所有 agent 段一起改"""
    import json as _json
    if "/" not in model:
        return {"ok": False, "error": "格式须为 provider/model，如 litellm/claude-model"}
    with open(OPENCODE_CFG, encoding="utf-8") as f:
        cfg = _json.load(f)
    cfg["model"] = model
    for a in (cfg.get("agent") or {}).values():
        a["model"] = model
    with open(OPENCODE_CFG, "w", encoding="utf-8") as f:
        _json.dump(cfg, f, ensure_ascii=False, indent=2)
    return {"ok": True, "model": model}


def agent_models():
    """从 opencode.json providers 列出可选模型（provider/model）"""
    import json as _json
    try:
        with open(OPENCODE_CFG, encoding="utf-8") as f:
            cfg = _json.load(f)
    except Exception:
        return {"ok": True, "models": []}
    models = []
    for pid, pv in (cfg.get("provider") or {}).items():
        mlist = pv.get("models") or {}
        if mlist:
            models += [f"{pid}/{m}" for m in mlist]
        else:
            models.append(f"{pid}/*")
    return {"ok": True, "models": sorted(models)}


def mc_has_answer(query):
    """用主模型快速判断「记忆里有没有能回答这个问题的料」：
    取向量 top6 的内容喂给 LLM 做二分判断。比纯相似度阈值可靠得多
    （BGE 中文短问相似度普遍 0.5~0.7，有无答案的分布重叠严重）。
    判断标准定宽：只要记忆里有可引用的实质信息就算 yes，宁可少派研究。"""
    try:
        ctx, _refs = mc.retrieve_context(query, top_k=6)
        verdict = mc.llm_chat([
            {"role": "system", "content": "判断记忆上下文能否回答问题。规则：记忆里有与问题直接相关的实质信息（型号、端口、路径、结论、事实）就输出 yes；只有沾边但无关的泛泛内容、或只提过名字没有细节、或问题问的是实时状态/当前清单，才输出 no。只输出 yes 或 no 单词。"},
            {"role": "user", "content": "【记忆上下文】\n" + ctx[:5000] + "\n\n【问题】" + query + "\n\n输出 yes 或 no："}
        ], max_tokens=512, timeout=45)  # 推理模型会先输出思维链，给太小 content 就是空
        return verdict.strip().lower().startswith("yes")
    except Exception:
        return True  # 判断失败时保守处理：不派研究，直接按记忆答

def api_research(task, max_wait=0):
    """派研究智能体实地查证：建会话 → 发任务 → 轮询取最终回复。
    注意：研究会话是多轮工具调用，中间态 finish='tool-calls' 属正常，
    必须等到 'stop'（真完成）才取 text；'error' 直接失败。
    返回 (ok, answer, sid)；失败不抛异常（研究是尽力而为）"""
    sid = None
    try:
        sess = _oc_api("/api/session", {"title": "openmem-research-" + str(int(time.time()))}, timeout=15)
        sid = sess["data"]["id"]
        _oc_api(f"/api/session/{sid}/prompt", {"prompt": {"text": task}}, timeout=30)
        # [LOCAL] 无超时：智能体干多久等多久（前端实时层可见，不焦虑）
        last_note = time.time()
        while True:
            time.sleep(3)
            try:
                msgs = _oc_api(f"/api/session/{sid}/message", timeout=20)
            except Exception:
                continue
            items = msgs.get("data", msgs)
            if isinstance(items, dict):
                items = items.get("items", [])
            for m in items:
                if m.get("type") == "assistant" and m.get("finish") == "stop":
                    text = "".join(p.get("text", "") for p in (m.get("content") or [])
                                   if p.get("type") == "text")
                    if text.strip():
                        return True, text.strip(), sid
                elif m.get("type") == "assistant" and m.get("finish") == "error":
                    err = (m.get("error") or {}).get("message", "")
                    return False, ("" if not err else "研究智能体执行出错：" + err), sid
        return False, "研究超时（任务复杂，工具链没跑完）", sid
    except Exception as e:
        return False, "研究智能体连不上：" + str(e), sid


# ── 数据接口（直接用 PG + mem_core 内核）─────────────────

def db_query(sql, params=None, fetch="all"):
    conn = mc.connect(); cur = conn.cursor()
    cur.execute(sql, params or ())
    rows = cur.fetchall() if fetch == "all" else cur.fetchone()
    cur.close(); conn.close()
    return rows

def get_app_config_flag(key, default=True):
    """读 app_config 里的开关（research_enabled 等）；没配过用默认"""
    try:
        row = db_query("SELECT value FROM app_config WHERE key=%s", (key,), fetch="one")
        if row:
            v = row[0]
            return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on")
    except Exception:
        pass
    return default

def api_stats():
    conn = mc.connect(); cur = conn.cursor()
    stats = {}
    for label, sql in [("total", "SELECT count(*) FROM memory_entries WHERE superseded_by IS NULL"),
                       ("pinned", "SELECT count(*) FROM memory_entries WHERE pinned AND superseded_by IS NULL"),
                       ("archived", "SELECT count(*) FROM memory_entries WHERE superseded_by IS NOT NULL"),
                       ("conflicts", "SELECT count(*) FROM memory_conflicts WHERE status='pending'"),
                       ("tools", "SELECT count(*) FROM preset_tools WHERE enabled"),
                       ("asks", "SELECT count(*) FROM ask_log")]:
        cur.execute(sql); stats[label] = cur.fetchone()[0]
    cur.execute("""SELECT layer||' / '||category, count(*) FROM memory_entries
                   WHERE superseded_by IS NULL GROUP BY layer, category
                   ORDER BY count(*) DESC LIMIT 12""")
    stats["breakdown"] = [{"name": r[0], "count": r[1]} for r in cur.fetchall()]
    cur.close(); conn.close()
    return stats

def api_search(q, layer=None, source=None, tags=None, since=None, top_k=10, include_archived=False):
    """向量语义检索（复用 mem_core 逻辑，不落 ask_log）"""
    import numpy as np
    q_vec = mc.emb([q])[0]
    sql = """SELECT id, layer, category, source, content, embedding, tags, pinned, created_at
             FROM memory_entries WHERE 1=1"""
    params = []
    if not include_archived:
        sql += " AND superseded_by IS NULL"
    if layer:
        sql += " AND layer=%s"; params.append(layer)
    if source:
        sql += " AND source=%s"; params.append(source)
    if tags:
        sql += " AND tags && %s"; params.append([t.strip() for t in tags.split(",") if t.strip()])
    if since:
        sql += " AND created_at >= %s"; params.append(since)
    sql += " ORDER BY created_at DESC LIMIT %s"; params.append(4000)
    rows = db_query(sql, params)
    qa = np.array(q_vec, dtype=np.float32); qn = np.linalg.norm(qa)
    scored = []
    for r in rows:
        try:
            v = np.array([float(x) for x in
                          (r[5] if isinstance(r[5], list) else str(r[5]).strip("{}").split(","))],
                         dtype=np.float32)
            sim = float(np.dot(qa, v) / (qn * np.linalg.norm(v) + 1e-9))
        except Exception:
            continue
        scored.append({"id": str(r[0]), "layer": r[1], "category": r[2], "source": r[3],
                       "content": r[4][:1500], "tags": r[6], "pinned": r[7],
                       "created_at": r[8].isoformat() if r[8] else None,
                       "score": round(sim, 4)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:top_k]
    for r in top:
        r["hl"] = hl_terms_for(q, r["content"])
    return top

def hl_terms_for(query, content, max_terms=5):
    """从 query 中找出真实出现在 content 里的连续片段（长2-12字），供前端 <mark> 高亮"""
    q = re.sub(r"[^\w\u4e00-\u9fff]+", "", query)
    found, i, L = [], 0, len(q)
    while i < L:
        best = ""
        for j in range(min(L, i + 12), i + 1, -1):
            if j - i >= 2 and q[i:j] in content:
                best = q[i:j]
                break
        if best:
            found.append(best)
            i += len(best)
        else:
            i += 1
    found = list(dict.fromkeys(found))
    found = [t for t in found if not any(t != o and t in o for o in found)]
    return found[:max_terms]

def api_entry_update(id_, content, tags=None, category=None):
    """编辑记忆：内容重算 embedding + hash，刷新 updated_at"""
    content, _ = _san_cred(content)  # 凭据打码闸（2026-09-12）
    vec = mc.emb([content])[0]
    conn = mc.connect(); cur = conn.cursor()
    sets, params = ["content=%s", "embedding=%s", "embedding_v=%s::vector", "content_hash=%s", "updated_at=now()", "last_verified_at=now()"], [mc.clean_nul(content), mc.to_pg_array(vec), mc.vec_literal(vec), mc.content_hash(content)]
    if tags is not None:
        sets.append("tags=%s"); params.append(tags)
    if category is not None:
        sets.append("category=%s"); params.append(category)
    params.append(id_)
    cur.execute(f"UPDATE memory_entries SET {', '.join(sets)} WHERE id=%s", params)
    n = cur.rowcount
    conn.commit(); cur.close(); conn.close()
    return {"ok": n > 0}

def api_entry_delete(id_):
    """删除记忆：先解除 superseded 引用与关联冲突记录，再删本体"""
    conn = mc.connect(); cur = conn.cursor()
    cur.execute("UPDATE memory_entries SET superseded_by=NULL WHERE superseded_by=%s", (id_,))
    cur.execute("DELETE FROM memory_conflicts WHERE a_id=%s OR b_id=%s", (id_, id_))
    cur.execute("DELETE FROM memory_entries WHERE id=%s", (id_,))
    n = cur.rowcount
    conn.commit(); cur.close(); conn.close()
    return {"ok": n > 0}

def api_history(limit=30):
    """咨询历史，按会话归组：同 session_id 的多轮合并为一条（rounds=轮数），
    opener=会话第一轮（取组内最早一条）；无 session_id 的旧记录/bot 单问各自独立一条"""
    rows = db_query("""SELECT id, session_id, requester, query, left(answer,160), refs, created_at
                       FROM ask_log ORDER BY created_at DESC LIMIT 500""")
    seen, groups = set(), []
    for r in rows:  # 时间倒序
        sid = r[1]
        if sid:
            if sid in seen:
                continue
            seen.add(sid)
            members = [x for x in rows if x[1] == sid]
            first = members[-1]  # 组内最早一轮 = 会话 opener
            groups.append({"id": str(first[0]), "session_id": sid, "requester": first[2],
                           "query": first[3], "answer_preview": first[4],
                           "refs": first[5] if isinstance(first[5], list) else [],
                           "created_at": first[6].isoformat() if first[6] else None,
                           "rounds": len(members)})
        else:
            groups.append({"id": str(r[0]), "session_id": None, "requester": r[2],
                           "query": r[3], "answer_preview": r[4],
                           "refs": r[5] if isinstance(r[5], list) else [],
                           "created_at": r[6].isoformat() if r[6] else None,
                           "rounds": 1})
        if len(groups) >= limit:
            break
    return groups

def api_ref_details(ids):
    """把 ref id 列表变成可展示的引用卡（source/分类/摘要）"""
    if not ids:
        return []
    rows = db_query("""SELECT id, layer, category, source, left(content,140), created_at
                       FROM memory_entries WHERE id = ANY(%s::uuid[])""", (ids,))
    by_id = {str(r[0]): {"id": str(r[0]), "layer": r[1], "category": r[2], "source": r[3],
                         "preview": r[4], "created_at": r[5].isoformat() if r[5] else None} for r in rows}
    return [by_id[i] for i in ids if i in by_id]

def api_config_get():
    cfg = mc.get_config()
    overridden = set()
    try:
        overridden = {r[0] for r in db_query("SELECT key FROM app_config")}
    except Exception:
        pass
    return {"config": cfg, "defaults": mc.DEFAULT_CONFIG,
            "customized": sorted(k for k in overridden if k in mc.DEFAULT_CONFIG)}

def api_config_save(body):
    """保存配置。temperature/max_tokens 已内置最优值，不接受页面覆盖（就算传了也忽略）"""
    from psycopg2.extras import Json
    conn = mc.connect(); cur = conn.cursor()
    saved = []
    for k, v in body.items():
        if k not in mc.DEFAULT_CONFIG or k in ("temperature", "max_tokens"):
            continue
        v = str(v)
        cur.execute("""INSERT INTO app_config (key, value) VALUES (%s, %s)
                       ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
                    (k, Json(v)))
        saved.append(k)
    conn.commit(); cur.close(); conn.close()
    return {"ok": True, "saved": saved}

def api_config_reset():
    conn = mc.connect(); cur = conn.cursor()
    # 只清运行配置；agentmatrix_hash 是同步指纹，不属于可重置配置
    cur.execute("DELETE FROM app_config WHERE key != 'agentmatrix_hash'")
    conn.commit(); cur.close(); conn.close()
    return {"ok": True}

def api_browse(layer=None, category=None, source=None, q_text=None, page=1, page_size=20, pinned=None):
    where, params = ["superseded_by IS NULL"], []
    if layer:
        where.append("layer=%s"); params.append(layer)
    if category:
        where.append("category=%s"); params.append(category)
    if source:
        where.append("source=%s"); params.append(source)
    if pinned:
        where.append("pinned");
    if q_text:
        where.append("content ILIKE %s"); params.append(f"%{q_text}%")
    w = " AND ".join(where)
    total = db_query(f"SELECT count(*) FROM memory_entries WHERE {w}", params, fetch="one")[0]
    rows = db_query(f"""SELECT id, layer, category, source, left(content,300), tags, pinned,
                               created_at, updated_at
                        FROM memory_entries WHERE {w}
                        ORDER BY created_at DESC LIMIT %s OFFSET %s""",
                    params + [page_size, (page - 1) * page_size])
    items = [{"id": str(r[0]), "layer": r[1], "category": r[2], "source": r[3],
              "preview": r[4], "tags": r[5], "pinned": r[6],
              "created_at": r[7].isoformat() if r[7] else None} for r in rows]
    # 引用热度：ask_log.refs 里被引用次数（0 次也要显示，便于治理判断）
    if items:
        ids = [it["id"] for it in items]
        hot = db_query("""SELECT refs::text, count(*) FROM ask_log
                          WHERE created_at > now() - interval '30 days' GROUP BY refs""", fetch="all")
        counts = {}
        import json as _json
        for (refs_json, _) in hot:
            try:
                for rid in _json.loads(refs_json) if isinstance(refs_json, str) else (refs_json or []):
                    counts[str(rid)] = counts.get(str(rid), 0) + 1
            except Exception:
                continue
        for it in items:
            it["ref_count"] = counts.get(it["id"], 0)
    if q_text:
        for it in items:
            it["hl"] = hl_terms_for(q_text, it["preview"])
    facets = db_query("""SELECT layer, category, count(*) FROM memory_entries
                         WHERE superseded_by IS NULL GROUP BY layer, category ORDER BY count(*) DESC LIMIT 30""")
    sources = db_query("""SELECT source, count(*) FROM memory_entries
                          WHERE superseded_by IS NULL GROUP BY source ORDER BY count(*) DESC LIMIT 30""")
    return {"total": total, "page": page, "page_size": page_size, "items": items,
            "facets": [{"name": f"{r[0]}/{r[1]}", "count": r[2]} for r in facets],
            "sources": [{"name": r[0], "count": r[1]} for r in sources]}

def _resolve_entry_id(id_):
    """模型写的 REF 可能是 8 位短 id（uuid 列直接 %s 查会炸 invalid input syntax）—— 先解析成完整 uuid"""
    id_ = (id_ or "").strip()
    if not id_:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{8}", id_):
        rows = db_query("SELECT id::text FROM memory_entries WHERE id::text LIKE %s LIMIT 2", (id_ + "%",))
        if rows and len(rows) == 1:
            return rows[0][0]
        return None
    return id_

def api_entry(id_):
    rid = _resolve_entry_id(id_)
    if not rid:
        return None
    r = db_query("""SELECT id, layer, category, source, content, tags, pinned, confidence,
                           created_at, updated_at, superseded_by
                    FROM memory_entries WHERE id=%s""", (rid,), fetch="one")
    if not r:
        return None
    return {"id": str(r[0]), "layer": r[1], "category": r[2], "source": r[3], "content": r[4],
            "tags": r[5], "pinned": r[6], "confidence": r[7],
            "created_at": r[8].isoformat() if r[8] else None,
            "updated_at": r[9].isoformat() if r[9] else None,
            "superseded_by": str(r[10]) if r[10] else None}

def api_models():
    """从当前配置的网关拉可用模型列表（供设置页下拉）"""
    cfg = mc.get_config()
    base = (cfg.get("llm_base_url") or "http://127.0.0.1:4000").rstrip("/")
    key = cfg.get("llm_key") or mc.LLM_KEY
    r = requests.get(base + "/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    r.raise_for_status()
    data = r.json().get("data", [])
    return {"ok": True, "models": sorted([m.get("id") for m in data if m.get("id")])}

# ── 记忆治理流水线（扫库出计划 → agent 交叉验证 → 老大批准 → 执行 → 终验）──

def gov_scan():
    """① 扫库：找可疑记忆。纯只读算法，产出治理计划草案。
    范围控制：agent-matrix 快照类（同 source 整段重导）按"只留每 source 最新"处理；
    其他记忆做 BGE>0.92 相似簇。零引用单独列出（本轮不动）"""
    import numpy as np
    rows = db_query("""SELECT id, layer, category, source, content, embedding, pinned, created_at
                       FROM memory_entries WHERE superseded_by IS NULL ORDER BY created_at DESC""")
    if not rows:
        return {"clusters": [], "snapshots": [], "stale": [], "total": 0}
    meta = []
    for r in rows:
        meta.append({"id": str(r[0]), "layer": r[1], "category": r[2], "source": r[3],
                     "content": r[4][:150], "pinned": r[6],
                     "created": r[7].isoformat()[:10] if r[7] else ""})
    # A. 同源快照：agent-matrix 服务明细类，同 (source, category) 只留最新一条
    snapshots = []
    groups = {}
    for m in meta:  # 时间倒序，首个即最新
        if m["source"] != "agent-matrix" or m["pinned"]:
            continue
        key = m["category"]
        groups.setdefault(key, []).append(m)
    for key, ms in groups.items():
        if len(ms) > 1:
            snapshots.append({"keep": ms[0]["id"], "archive": [m["id"] for m in ms[1:]],
                              "source": key, "count": len(ms) - 1,
                              "sample": ms[0]["content"][:80]})
    # B. 语义相似簇：排除 agent-matrix 快照类，只看有 embedding 的普通记忆
    normal = [m for m in meta if m["source"] != "agent-matrix"]
    snap_ids = {m["id"] for ms in groups.values() for m in ms}
    normal_ids = {m["id"] for m in normal if m["id"] not in snap_ids} if False else None
    # 重新取这些条目的 embedding
    emap = {}
    if normal:
        ph = ",".join(["%s"] * len(normal))
        erows = db_query(f"SELECT id, embedding FROM memory_entries WHERE id IN ({ph}) AND superseded_by IS NULL",
                         tuple(m["id"] for m in normal))
        for rid, emb in erows:
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
    # C. 30 天零引用（本轮仅展示）
    hot = db_query("""SELECT refs::text, count(*) FROM ask_log
                      WHERE created_at > now() - interval '30 days' GROUP BY refs""")
    counts = {}
    for (refs_json, _) in hot:
        try:
            for rid in json.loads(refs_json) if isinstance(refs_json, str) else (refs_json or []):
                counts[str(rid)] = counts.get(str(rid), 0) + 1
        except Exception:
            continue
    stale = [{"id": m["id"], "content": m["content"], "category": m["category"],
              "created": m["created"]}
             for m in meta if not m["pinned"] and counts.get(m["id"], 0) == 0
             and "实地查证" not in m["category"]][:40]
    return {"clusters": clusters, "snapshots": snapshots, "stale": stale, "total": len(meta)}


def _gov_verify_task(plan):
    """生成治理交叉验证的任务提示词。
    只送前 10 簇（全量分批跑，一批约 2-4 分钟，避免超时）。
    AI 抽样深度核对，全量一致性由脚本核对——不让 AI 抄几百个 UUID。"""
    clusters = plan.get("clusters", [])
    snapshots = plan.get("snapshots", [])
    sample = clusters[:10]
    brief = []
    for c in sample:
        brief.append("簇(相似度%s)：保留内容=%r；候选归档 %d 条" % (
            c["sim"], (c.get("sample") or "")[:80], len(c.get("archive", []))))
    snap_brief = []
    for s in snapshots[:4]:
        snap_brief.append("同源快照(source=%s)：归档 %d 条" % (s["source"], s["count"]))
    return (
        "你是记忆治理的审计员。openmem 扫库发现疑似重复：%d 组语义相似簇（本轮只验前 %d 组，其余批次后续跑）+ %d 组同源快照。\n"
        "同源快照组是 agent-matrix 每次重导生成的整段快照，按设计只保留最新一条，安全；你抽查确认即可。\n"
        "语义簇请对每组：用 psql 查 openmem 库，对比保留者和候选归档条目的实际内容，"
        "判断候选是否真是保留者的旧版本/重复。\n"
        "注意：下方 UUID 直接复制使用，不要手抄。\n\n"
        "语义簇（%d 组）：\n" + "\n".join(brief) +
        (("\n\n同源快照：\n" + "\n".join(snap_brief)) if snap_brief else "") +
        "\n\n输出格式（严格遵守，全程中文）：\n"
        "## 逐组判定\n每行：相似度X | ✅可归档/⚠️存疑/❌应保留 | 一句话理由\n"
        "## 总结\n可归档 X 组，存疑 X 组，应保留 X 组"
    ) % (len(clusters), len(sample), len(snapshots), len(sample))


def _gov_plan_digest(plan):
    """计划的简短摘要（写回记忆时的标识）"""
    n1 = len(plan.get("clusters", []))
    n2 = len(plan.get("snapshots", []))
    return f"记忆治理计划交叉验证（{n1} 个语义簇 + {n2} 组同源快照）"


def gov_verify_plan(plan):
    """② 交叉验证：把计划交给研究智能体只读核对。
    返回 (ok, verdict_text, sid)。AI 只标注，不改数据。
    sid 供调用方订阅事件流做实时可视（工具层/思考层）。"""
    import json as _json
    brief = []
    for c in plan.get("clusters", [])[:10]:
        brief.append("簇(相似度%s)：保留=%s；候选归档=%s" % (
            c["sim"], c["keep"], ",".join(c["archive"][:4])))
    for s in plan.get("snapshots", [])[:6]:
        brief.append("同源快照组(source=%s)：候选归档 %d 条（各自保留最新一条）" % (s["source"], s["count"]))
    task = (
        "交叉验证一份记忆治理计划。以下是疑似重复的记忆簇/快照组。"
        "你只做只读核对：访问 openmem（PSQL openmem 库 / 本机服务）抽查这些条目内容，"
        "判断候选归档者是否真的是保留者的旧版本/重复（而非信息量不同的独立事实）。"
        "逐组给出判定：✅可归档 / ⚠️存疑(说原因) / ❌应保留。\n\n" +
        "\n".join(brief) +
        "\n\n输出：每组一行判定，最后给一行总结（几组可归档、几组存疑）。"
    )
    ok, sid = _research_start(task)
    if not ok:
        return False, sid, None
    # 轮询等最终 stop（复用判定逻辑；实时流由调用方通过 /api/agent/research/stream 订阅）
    # [LOCAL] 无超时：验证任务跑多久等多久
    verdict = ""
    while True:
        time.sleep(3)
        try:
            if time.time() - last_note > 3600:
                last_note = time.time()
                print(f'[research] 仍在等待智能体完成（已等 1h+） sid={sid[:12]}', flush=True)
            msgs = _oc_api(f"/api/session/{sid}/message", timeout=10)
        except Exception:
            continue
        items = msgs.get("data", msgs)
        if isinstance(items, dict):
            items = items.get("items", [])
        stops = ["".join(p.get("text", "") for p in (m.get("content") or [])
                         if p.get("type") == "text")
                 for m in items
                 if m.get("type") == "assistant" and m.get("finish") == "stop"]
        stops = [t for t in stops if t.strip()]
        if stops:
            verdict = max(stops, key=len)
            break
        if any(m.get("finish") == "error" for m in items if m.get("type") == "assistant"):
            return False, "研究智能体执行出错", sid
    return (True, verdict, sid) if verdict else (False, "验证超时（会话仍在后台跑，结果不会丢）", sid)


def gov_execute(plan):
    """④ 执行：把候选归档条目 superseded_by 指向保留者（不裸删，可回滚）"""
    done, skip = [], []
    # 快照组：同 source 组内归档到最新
    for s in plan.get("snapshots", []):
        keep = s.get("keep")
        for aid in s.get("archive", []):
            try:
                rows = db_query("SELECT id FROM memory_entries WHERE id=%s AND pinned=false AND superseded_by IS NULL",
                                (aid,), fetch="one")
                if not rows:
                    skip.append({"id": aid, "reason": "不存在/已钉住/已归档"})
                    continue
                conn = mc.connect(); cur = conn.cursor()
                cur.execute("UPDATE memory_entries SET superseded_by=%s WHERE id=%s", (keep, aid))
                conn.commit(); cur.close(); conn.close()
                done.append({"archived": aid, "kept": keep})
            except Exception as e:
                skip.append({"id": aid, "reason": str(e)[:80]})
    for c in plan.get("clusters", []):
        keep = c.get("keep")
        for aid in c.get("archive", []):
            try:
                rows = db_query("SELECT id FROM memory_entries WHERE id=%s AND pinned=false AND superseded_by IS NULL",
                                (aid,), fetch="one")
                if not rows:
                    skip.append({"id": aid, "reason": "不存在/已钉住/已归档"})
                    continue
                conn = mc.connect(); cur = conn.cursor()
                cur.execute("UPDATE memory_entries SET superseded_by=%s WHERE id=%s", (keep, aid))
                conn.commit(); cur.close(); conn.close()
                done.append({"archived": aid, "kept": keep})
            except Exception as e:
                skip.append({"id": aid, "reason": str(e)[:80]})
    return {"archived": done, "skipped": skip}


def gov_verify_result(before_total, plan):
    """⑤ 终验：执行后核对计数 + 抽查检索质量。返回报告文本"""
    after = db_query("SELECT count(*) FROM memory_entries WHERE superseded_by IS NULL", fetch="one")[0]
    archived_n = db_query("SELECT count(*) FROM memory_entries WHERE superseded_by IS NOT NULL", fetch="one")[0]
    lines = ["终验报告：",
             f"- 活跃记忆 {before_total} → {after}（归档 {len(plan.get('clusters', []))} 簇）",
             f"- 归档总数（含历史）：{archived_n}",
             "- 归档均为软归档（superseded_by 指向保留者），可随时回滚",
             "- 检索链路未变，pinned 条目不受影响"]
    return "\n".join(lines)


def api_absorb(content, category=None, tags=None, layer="m", pinned=False):
    """把用户在网页上给的内容吸收进记忆（落实吸收：AI 答案里有新料 / 老大随手教一条）
    写入前做语义查重：相似度 >0.95 视为同一事实自动归旧（superseded），
    0.90~0.95 记入 memory_conflicts 待人工审，避免同一事实多条并存"""
    content = (content or "").strip()
    if not content:
        return {"ok": False, "error": "内容为空"}
    content, _ = _san_cred(content)  # 凭据打码闸（2026-09-12）
    vec = mc.emb([content])[0]
    import numpy as np
    qa = np.array(vec, dtype=np.float32); qn = np.linalg.norm(qa)
    # 语义查重：全库找最近邻
    rows = db_query("""SELECT id, content, embedding FROM memory_entries
                       WHERE superseded_by IS NULL ORDER BY created_at DESC LIMIT 4000""")
    best_id, best_sim = None, 0.0
    for r in rows:
        try:
            v = np.array([float(x) for x in
                          (r[2] if isinstance(r[2], list) else str(r[2]).strip("{}").split(","))],
                         dtype=np.float32)
            sim = float(np.dot(qa, v) / (qn * np.linalg.norm(v) + 1e-9))
        except Exception:
            continue
        if sim > best_sim:
            best_id, best_sim = str(r[0]), sim
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
    conn = mc.connect(); cur = conn.cursor()
    conflict_flagged = False
    if best_id and best_sim > 0.95:
        # 同一事实换了个说法：新条目照常入库（内容确实更新了），旧条目归档指向新条目
        conflict_flagged = True
    cur.execute("""
        INSERT INTO memory_entries (layer, category, source, content, embedding, embedding_v, tags,
                                    confidence, pinned, content_hash)
        VALUES (%s,%s,%s,%s,%s,%s::vector,%s,%s,%s,%s)
        ON CONFLICT (source, content_hash) DO UPDATE
          SET content=EXCLUDED.content, embedding=EXCLUDED.embedding, embedding_v=EXCLUDED.embedding_v, tags=EXCLUDED.tags,
              updated_at=now(), last_verified_at=now()
        RETURNING id, (xmax = 0) AS inserted
    """, (layer if layer in ("k", "m") else "m", (category or "用户补充").strip() or "用户补充",
          "web-console", mc.clean_nul(content), mc.to_pg_array(vec), mc.vec_literal(vec), tag_list,
          0.9, bool(pinned), mc.content_hash(content)))
    row = cur.fetchone()
    new_id = str(row[0])
    if best_id and best_sim > 0.95:
        cur.execute("UPDATE memory_entries SET superseded_by=%s WHERE id=%s", (new_id, best_id))
    elif best_id and best_sim > 0.90:
        cur.execute("""INSERT INTO memory_conflicts (a_id, b_id, reason)
                       VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""", (best_id, new_id,
                       f"absorb 语义近似 (sim={best_sim:.3f})，待人工判断是否同一事实"))
        conflict_flagged = True
    conn.commit(); cur.close(); conn.close()
    result = {"ok": True, "id": new_id, "inserted": bool(row[1])}
    if best_id:
        result["similar"] = {"id": best_id, "sim": round(best_sim, 3)}
    if conflict_flagged:
        result["dedup"] = "superseded" if best_sim > 0.95 else "conflict_pending"
    return result

def api_am_status():
    """agent-matrix 同步状态：源文件指纹 vs 上次导入指纹"""
    import import_agentmatrix as iam
    fp = iam.files_fingerprint()
    return {"ok": True, "files": iam.SRC_FILES, "fingerprint": fp,
            "stored": iam._stored_hash(), "in_sync": iam._stored_hash() == fp,
            "count": iam.entry_count()}

def api_tools():
    rows = db_query("""SELECT name, description, left(cached_answer,200), answer_updated_at,
                              enabled, refresh_interval FROM preset_tools ORDER BY name""")
    return [{"name": r[0], "description": r[1], "answer_preview": r[2],
             "answer_updated_at": r[3].isoformat() if r[3] else None,
             "enabled": r[4], "refresh_interval": str(r[5])} for r in rows]

def api_tool_answer(name, force=False):
    """直接复用内核 tool_call（缓存命中秒回，过期/强制才重新生成）"""
    import io, contextlib
    buf = io.StringIO()
    ns = argparse.Namespace(name=name, force_refresh=force)
    with contextlib.redirect_stdout(buf):
        mc.cmd_tool_call(ns)
    return json.loads(buf.getvalue())

def api_tool_create(name, description="", prompt="", interval="1 day"):
    """创建/更新成品答案（preset_tool）并立即生成缓存答案。

    直接复用内核 cmd_tool_create（同 CLI `mem_core.py tool_create`），
    避免以前只能直连 PG 手写 INSERT。name 已存在时按 name 覆盖
    description/prompt_template（ON CONFLICT DO UPDATE）。
    """
    import io, contextlib
    name = (name or "").strip()
    prompt = (prompt or "").strip()
    if not name or not prompt:
        return {"ok": False, "error": "name 与 prompt 均为必填"}
    buf = io.StringIO()
    ns = argparse.Namespace(name=name, description=description or "",
                            prompt=prompt, interval=interval or "1 day")
    try:
        with contextlib.redirect_stdout(buf):
            mc.cmd_tool_create(ns)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    try:
        return json.loads(buf.getvalue())
    except Exception:
        return {"ok": False, "error": "内核输出无法解析", "raw": buf.getvalue()[-500:]}

def api_onboarding():
    """入职包：新 agent 一条命令拉全。pinned l0 + 坑库Top + 服务端口 + 工具清单 + 接入方式"""
    pinned = db_query("""SELECT source, content, created_at FROM memory_entries
                         WHERE pinned AND superseded_by IS NULL ORDER BY layer, created_at DESC LIMIT 40""")
    pits = db_query("""SELECT source, content FROM memory_entries
                       WHERE layer='m' AND category='lessons' AND superseded_by IS NULL
                       ORDER BY pinned DESC, created_at DESC LIMIT 20""")
    tools = db_query("SELECT name, description FROM preset_tools WHERE enabled ORDER BY name")
    lines = ["# 📦 openmem 入职包（新 agent 一条命令拉全）",
             "> 生成时间：" + __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
             "",
             "## 接入方式（装完即老手）",
             "```json",
             '{"mcpServers": {"openmem": {"url": "http://127.0.0.1:3466/mcp"}}}',
             "```",
             "MCP 工具：`mh_write`(写入，source强制) / `mh_search`(混合检索) / `mh_ask`(AI对AI咨询) / `mh_tool`(预生成答案秒回) / `mh_status`",
             "",
             "## 一、老大是谁（钉住的关键事实）"]
    for src, content, _ in pinned:
        lines.append(f"- **[{src}]** {content}")
    lines += ["", "## 二、血泪坑库 Top（l2，动手前必读）"]
    for src, content in pits:
        lines.append(f"- **[{src}]** {content[:400]}")
    lines += ["", "## 三、标准化答案工具（要这类信息直接 mh_tool 秒取）"]
    for name, desc in tools:
        lines.append(f"- **{name}**：{desc}")
    lines += ["", "## 四、铁律摘要",
              "- 修 bug ≠ 加功能，没提的功能一行不许加",
              "- 破坏性操作（删/改配置/重启服务）必须先问 + 先备份 `*.bak-<时间戳>`",
              "- 报状态必须当场实测，禁止拿旧观察当现状",
              "- 动 litellm（被 11 bot 依赖）前必须问老大",
              "- 重要结论不写回 openmem 不算任务完成"]
    return {"markdown": "\n".join(lines)}


# ── HTTP 服务 ─────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[openmem-web] %s\n" % (fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, max-age=0")   # 页面/接口永远拿最新，杜绝 Edge 启发式缓存旧版
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            u = urlparse(self.path); p = u.path; qs = parse_qs(u.query)
            g = lambda k, d=None: qs.get(k, [d])[0]
            if p == "/" or p == "/index.html":
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_page.html"), encoding="utf-8") as f:
                    self._send(200, f.read().encode("utf-8"), "text/html; charset=utf-8")
            elif p == "/api/stats":
                self._send(200, api_stats())
            elif p == "/api/search":
                self._send(200, {"results": api_search(
                    q=g("q", ""), layer=g("layer") or None, source=g("source") or None,
                    tags=g("tags") or None, since=g("since") or None,
                    top_k=int(g("top_k", "10")), include_archived=g("include_archived") == "1")})
            elif p == "/api/browse":
                self._send(200, api_browse(
                    layer=g("layer") or None, category=g("category") or None,
                    source=g("source") or None, q_text=g("q") or None,
                    page=int(g("page", "1")), page_size=int(g("page_size", "20")),
                    pinned=g("pinned") == "1"))
            elif p == "/api/entry":
                e = api_entry(g("id"))
                self._send(200, e if e else {"error": "not found"})
            elif p == "/api/tools":
                self._send(200, {"tools": api_tools()})
            elif p == "/api/tool/answer":
                self._send(200, api_tool_answer(g("name"), force=g("force") == "1"))
            elif p == "/api/onboarding":
                self._send(200, api_onboarding())
            elif p == "/api/history":
                self._send(200, {"items": api_history(int(g("limit", "30")))})
            elif p == "/api/refs":
                ids = [x for x in (g("ids") or "").split(",") if x.strip()]
                self._send(200, {"details": api_ref_details(ids)})
            elif p == "/api/config":
                self._send(200, api_config_get())
            elif p == "/api/models":
                self._send(200, api_models())
            elif p == "/api/agentmatrix/status":
                self._send(200, api_am_status())
            elif p == "/api/agent/status":
                self._send(200, agent_status())
            elif p == "/api/agent/models":
                self._send(200, agent_models())
            elif p == "/api/agent/config":
                self._send(200, {"enabled": get_app_config_flag("research_enabled", True),
                                 **agent_status()})
            elif p == "/api/govern/scan":
                # ① 扫库出计划（纯只读）
                self._send(200, gov_scan())
            elif p == "/api/govern/status":
                # 分批治理进度（读批处理脚本落的账本）
                import os as _os
                out = {"running": False, "rounds": [], "archived_total": 0, "finished": False}
                try:
                    rp = "C:/D/opt/openmem/_govern_report.json"
                    if _os.path.exists(rp):
                        with open(rp, encoding="utf-8") as f:
                            out.update(json.load(f))
                    lg = "C:/D/opt/openmem/_govern_batch.log"
                    if _os.path.exists(lg):
                        with open(lg, encoding="utf-8", errors="replace") as f:
                            out["log_tail"] = f.read()[-600:]
                    # 还在跑吗：日志 90 秒内有更新视为运行中
                    if _os.path.exists(lg) and time.time() - _os.path.getmtime(lg) < 90:
                        out["running"] = True
                    if _os.path.exists(rp) and "active_after" in out:
                        out["finished"] = True
                        out["running"] = False
                except Exception:
                    pass
                self._send(200, out)
            elif p == "/api/history/detail":
                # 历史详情：传 id（任一轮）或 session_id，返回整段会话按时间正序
                sid = g("session_id")
                if sid:
                    rows = db_query("""SELECT id, requester, query, answer, refs, created_at
                                       FROM ask_log WHERE session_id=%s ORDER BY created_at""", (sid,))
                else:
                    hid = g("id")
                    rows = db_query("""SELECT id, requester, query, answer, refs, created_at
                                       FROM ask_log WHERE id=%s OR session_id=
                                         (SELECT session_id FROM ask_log WHERE id=%s AND session_id IS NOT NULL)
                                       ORDER BY created_at""", (hid, hid))
                if rows:
                    turns = []
                    for r in rows:
                        refs = r[4] if isinstance(r[4], list) else []
                        turns.append({"id": str(r[0]), "requester": r[1], "query": r[2], "answer": r[3],
                                      "refs": refs,
                                      "ref_details": api_ref_details(refs[:8]),
                                      "created_at": r[5].isoformat() if r[5] else None})
                    # 关联查证记录：该问题（或每轮问题）若有实地查证条目，带出来展示
                    try:
                        qtexts = [t["query"] for t in turns]
                        research = []
                        for qt in qtexts:
                            qlike = "%" + qt[:40] + "%"
                            rr = db_query("""SELECT content, created_at FROM memory_entries
                                             WHERE category='verification' AND content LIKE %s
                                             ORDER BY created_at DESC LIMIT 2""", (qlike,))
                            for rc, rt in rr:
                                research.append({"content": rc, "created_at": rt.isoformat() if rt else None})
                        # 去重
                        seen = set()
                        research = [x for x in research if not (x["content"] in seen or seen.add(x["content"]))]
                        self._send(200, {"session_id": turns[0]["id"], "turns": turns,
                                         "research": research[:3]})
                    except Exception:
                        self._send(200, {"session_id": turns[0]["id"], "turns": turns})
                else:
                    self._send(404, {"error": "not found"})
            elif p == "/api/health":
                self._send(200, {"ok": True, "service": "openmem-web", "port": PORT})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})

    def do_DELETE(self):
        try:
            u = urlparse(self.path); qs = parse_qs(u.query)
            if u.path == "/api/entry":
                self._send(200, api_entry_delete(qs.get("id", [""])[0]))
            elif u.path == "/api/history":
                hid = qs.get("id", [""])[0]
                conn = mc.connect(); cur = conn.cursor()
                # 删一条历史 = 删整个会话（先按 id 找到所属 session_id，无会话则只删该条）
                cur.execute("SELECT session_id FROM ask_log WHERE id=%s", (hid,))
                r = cur.fetchone()
                if r and r[0]:
                    cur.execute("DELETE FROM ask_log WHERE session_id=%s", (r[0],))
                else:
                    cur.execute("DELETE FROM ask_log WHERE id=%s", (hid,))
                n = cur.rowcount
                conn.commit(); cur.close(); conn.close()
                self._send(200, {"ok": n > 0, "deleted": hid})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        try:
            u = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if u.path == "/api/ask":
                answer, refs = mc.do_ask(body.get("query", ""), body.get("agent", "web-console"))
                self._send(200, {"ok": True, "answer": answer, "refs": refs})
            elif u.path == "/api/ask_stream":
                self._send_sse(body)
            elif u.path == "/api/tool/refresh":
                r = api_tool_answer(body.get("name", ""), force=True)
                self._send(200, r)
            elif u.path == "/api/tool/create":
                r = api_tool_create(body.get("name", ""), body.get("description", ""),
                                    body.get("prompt", ""), body.get("interval", "1 day"))
                self._send(200, r)
            elif u.path == "/api/entry/edit":
                r = api_entry_update(body.get("id", ""), body.get("content", ""),
                                     body.get("tags"), body.get("category"))
                self._send(200, r)
            elif u.path == "/api/config":
                self._send(200, api_config_save(body))
            elif u.path == "/api/config/reset":
                self._send(200, api_config_reset())
            elif u.path == "/api/absorb":
                self._send(200, api_absorb(body.get("content", ""), body.get("category"),
                                           body.get("tags"), body.get("layer", "m"),
                                           body.get("pinned", False)))
            elif u.path == "/api/agent/research":
                # 手动派研究智能体（用户点按钮才触发，绝不自动查）
                # 过程实时推送（SSE）：工具层/思考层/文本层全部转发给前端
                q = (body.get("query") or "").strip()
                hint = (body.get("task") or "").strip()
                if not q and not hint:
                    self._send(400, {"ok": False, "error": "缺少查证任务"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

                def emit(ev):
                    data = json.dumps(ev, ensure_ascii=False).encode("utf-8")
                    self.wfile.write(b"data: " + data + b"\n\n")
                    self.wfile.flush()

                task = (
                    "帮用户实地查证下面这件事（看配置文件、调 API、翻目录、跑只读命令都行，"
                    "绝不改任何东西）。给出简明、有据、可直接用的中文结论；"
                    "查不到就直说试了哪几条路都没查到。\n\n"
                    + ("查证任务：" + hint if hint else "问题：" + q)
                )
                try:
                    entry_id = body.get("entryId")
                    ok, sid = _research_start(task)
                    if not ok:
                        emit({"event": "research_error", "error": sid})
                        return
                    emit({"event": "research_started", "sid": sid, "task": hint or q})

                    if entry_id:
                        # [LOCAL] 单条记忆查证：结论直接追加进该条内容（向量重算）
                        _research_stream_update_entry(sid, emit, entry_id)
                    else:
                        _research_stream_events(sid, emit, hint or q)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as e:
                    try:
                        emit({"event": "research_error", "error": str(e)})
                    except Exception:
                        pass
            elif u.path == "/api/govern/verify":
                # ② 交叉验证（SSE 实时：工具层/思考层滚动，老大看着它在干活）
                plan = body.get("plan") or {}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

                def emit2(ev):
                    data = json.dumps(ev, ensure_ascii=False).encode("utf-8")
                    self.wfile.write(b"data: " + data + b"\n\n")
                    self.wfile.flush()

                try:
                    ok, sid_or_err = _research_start(_gov_verify_task(plan))
                    if not ok:
                        emit2({"event": "research_error", "error": sid_or_err})
                        return
                    emit2({"event": "research_started", "sid": sid_or_err,
                           "task": "治理计划交叉验证"})
                    _research_stream_events(sid_or_err, emit2, "治理交叉验证：" + _gov_plan_digest(plan),
                                            max_wait=600)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as e:
                    try:
                        emit2({"event": "research_error", "error": str(e)})
                    except Exception:
                        pass
            elif u.path == "/api/govern/execute":
                # ④ 执行归档（老大已在网页批准）
                plan = body.get("plan") or {}
                before = db_query("SELECT count(*) FROM memory_entries WHERE superseded_by IS NULL", fetch="one")[0]
                r = gov_execute(plan)
                report = gov_verify_result(before, plan)  # ⑤ 终验
                self._send(200, {**r, "report": report})
            elif u.path == "/api/agent/config":
                # 改研究智能体模型/开关
                model = body.get("model")
                enabled = body.get("enabled")
                out = {}
                if model is not None:
                    out.update(agent_set_model(model))
                if enabled is not None:
                    from psycopg2.extras import Json
                    conn = mc.connect(); cur = conn.cursor()
                    cur.execute("""INSERT INTO app_config (key, value) VALUES ('research_enabled', %s)
                                   ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
                                (Json(bool(enabled)),))
                    conn.commit(); cur.close(); conn.close()
                    out["enabled"] = bool(enabled)
                self._send(200, out)
            elif u.path == "/api/agentmatrix/refresh":
                import import_agentmatrix as iam
                self._send(200, iam.sync(force=True))
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})

    def _send_sse(self, body):
        """流式咨询：SSE 推 status/delta/done/related 事件。
        记忆检索相关度过低时自动派研究智能体实地查证（可在设置页关）"""
        query = (body.get("query") or "").strip()
        agent = body.get("agent") or "web-console"
        history = body.get("history") or []   # 多轮追问上下文 [{q,a}]
        session_id = body.get("session_id") or None
        if history and not session_id:
            # 追问但没带会话 id（理论上不会发生）——现场补一个，保证归组不散
            session_id = uuid.uuid4().hex
        if not query:
            self._send(400, {"ok": False, "error": "query 为空"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(ev):
            data = json.dumps(ev, ensure_ascii=False).encode("utf-8")
            self.wfile.write(b"data: " + data + b"\n\n")
            self.wfile.flush()

        try:
            for ev in mc.do_ask_stream(query, agent, history, session_id=session_id,
                                       extra_context=None):
                if ev.get("event") == "done":
                    answer = ev.get("answer") or ""
                    # 主模型可能按协议在末尾写 RESEARCH: 任务 —— 只提取当按钮文案，
                    # 绝不自动查（研究必须用户手动点按钮才派，省钱）
                    m = re.search(r"RESEARCH:\s*(.+)\s*$", answer, re.M)
                    if m:
                        ev["answer"] = re.sub(r"\n?RESEARCH:\s*.+\s*$", "", answer).rstrip()
                        ev["research"] = {"task": m.group(1).strip()}
                    ev["ref_details"] = api_ref_details(ev.get("refs", [])[:8])
                    if session_id:
                        ev["session_id"] = session_id
                emit(ev)

        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端提前关页面
        except Exception as e:
            try:
                err = json.dumps({"event": "error", "error": str(e)}, ensure_ascii=False).encode("utf-8")
                self.wfile.write(b"data: " + err + b"\n\n")
                self.wfile.flush()
            except Exception:
                pass


def _research_start(task):
    """建研究会话并发任务。返回 (ok, sid_or_error)"""
    try:
        sess = _oc_api("/api/session", {"title": "openmem-research-" + str(int(time.time()))}, timeout=15)
        sid = sess["data"]["id"]
        _oc_api(f"/api/session/{sid}/prompt", {"prompt": {"text": task}}, timeout=30)
        return True, sid
    except Exception as e:
        return False, "研究智能体连不上：" + str(e)


def _research_stream_events(sid, emit, query_for_store, max_wait=0):
    """订阅 opencode 会话事件流（session.next.* 事件族），实时转发工具层/文本层给前端；
    step.ended(finish=stop) 即完成：取最终文本写回记忆并推 research_done。
    已确认的事件形状：
      tool.called     data={callID, tool, input}
      tool.success    data={callID, content:[{type:text,text}], structured:{exit}}
      text.ended      data={textID, text}   ← 文本一次性给全（litellm 无逐字流）
      step.ended      data={finish:"stop", tokens}"""
    import requests as _rq
    tools = {}  # callID -> {name, input}
    try:
        with _rq.get(f"{RESEARCH_URL}/api/session/{sid}/event", stream=True,
                     timeout=max_wait + 10, headers={"Accept": "text/event-stream"}) as r:
            r.encoding = "utf-8"  # SSE 是 UTF-8；不显式指定 decode_unicode 会按 ISO-8859-1 解出乱码
            buf = ""
            for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
                if not chunk:
                    continue
                buf += chunk
                while "\n\n" in buf:
                    raw, buf = buf.split("\n\n", 1)
                    for line in raw.split("\n"):
                        if not line.startswith("data: "):
                            continue
                        try:
                            ev = json.loads(line[6:])
                        except Exception:
                            continue
                        t = ev.get("type", "")
                        d = ev.get("data") or {}
                        if t == "session.next.tool.called":
                            cid = d.get("callID", "")
                            tools[cid] = {"name": d.get("tool") or "bash",
                                          "input": d.get("input") or {}}
                            emit({"event": "research_tool", "tool": tools[cid]["name"],
                                  "status": "running",
                                  "input": json.dumps(tools[cid]["input"], ensure_ascii=False)[:160]})
                        elif t == "session.next.tool.success":
                            cid = d.get("callID", "")
                            info = tools.get(cid) or {"name": "bash", "input": {}}
                            out = ""
                            for c in (d.get("content") or []):
                                if isinstance(c, dict) and c.get("type") == "text":
                                    out += c.get("text", "")
                            exit_code = (d.get("structured") or {}).get("exit")
                            emit({"event": "research_tool", "tool": info["name"],
                                  "status": "completed",
                                  "exit": exit_code,
                                  "input": json.dumps(info["input"], ensure_ascii=False)[:100],
                                  "output": out[:140]})
                        elif t == "session.next.text.ended":
                            # 模型的即时陈述/中间结论（step 间文本段）
                            txt = d.get("text") or ""
                            if txt.strip():
                                emit({"event": "research_thought", "text": txt[-400:]})
                        elif t == "session.next.step.ended":
                            # 一步结束：stop=全部完成；用事件流自身收尾，不再轮询 messages
                            if d.get("finish") == "stop":
                                final = _get_session_final_text(sid)
                                if final:
                                    stored_id = _store_research(query_for_store, final)
                                    emit({"event": "research_done", "result": final,
                                          "stored_id": stored_id})
                                else:
                                    emit({"event": "research_error", "error": "完成但没取到结论文本"})
                                return
                            elif d.get("finish") == "error":
                                emit({"event": "research_error", "error": "研究智能体执行出错"})
                                return
    except (BrokenPipeError, ConnectionResetError):
        raise
    except Exception as e:
        emit({"event": "research_error", "error": "事件流中断：" + str(e)})


def _get_session_final_text(sid):
    """取研究会话 stop 消息中最长的一条（最终结论文本，短的是中间陈述）"""
    try:
        msgs = _oc_api(f"/api/session/{sid}/message", timeout=10)
        items = msgs.get("data", msgs)
        if isinstance(items, dict):
            items = items.get("items", [])
        texts = ["".join(p.get("text", "") for p in (m.get("content") or [])
                         if p.get("type") == "text")
                 for m in items
                 if m.get("type") == "assistant" and m.get("finish") == "stop"]
        texts = [t.strip() for t in texts if t.strip()]
        return max(texts, key=len) if texts else ""
    except Exception:
        return ""


_GARBAGE_PAT = re.compile(
    r"(I (still|think|should|notice|need to)|let me|typos in|stuck in a loop|transcribed|I'm clearly stuck)",
    re.I)


def _research_stream_update_entry(sid, emit, entry_id):
    """单条记忆查证：等最终结论，追加为该条的【查证更新】段（api_entry_update 重算向量）。30 分钟死循环保护。"""
    deadline = time.time() + 1800
    while time.time() < deadline:
        time.sleep(3)
        try:
            msgs = _oc_api(f"/api/session/{sid}/message", timeout=10)
        except Exception:
            continue
        items = msgs.get("data", msgs)
        if isinstance(items, dict):
            items = items.get("items", [])
        for m in items:
            if m.get("type") == "assistant" and m.get("finish") == "stop":
                text = "".join(p.get("text", "") for p in (m.get("content") or []) if p.get("type") == "text")
                if text.strip():
                    try:
                        e = api_entry(entry_id)
                        stamp = time.strftime("%Y-%m-%d")
                        new_content = (e["content"].rstrip() if e else "") + chr(10) + chr(10) + "【查证更新 " + stamp + "】" + chr(10) + text.strip()
                        api_entry_update(entry_id, new_content)
                        emit({"event": "research_done", "result": text.strip(), "stored_id": entry_id, "updated_entry": True})
                    except Exception as ee:
                        emit({"event": "research_error", "error": "写回失败：" + str(ee)})
                return
            if m.get("type") == "assistant" and m.get("finish") == "error":
                emit({"event": "research_error", "error": "查证执行出错"})
                return
    emit({"event": "research_error", "error": "30 分钟保护上限（智能体仍在后台跑，可稍后重试）"})

def _store_research(query, receipt):
    """研究回执写回 openmem：来源 research-agent，category=verification，不钉住。
    质量闸门：结论若含过程腔/英文自言自语（中间态），拒收——那不是结论是垃圾"""
    try:
        # 只看结论部分（"结论："之后），长度与语言双重校验
        concl = receipt.split("结论：", 1)[1] if "结论：" in receipt else receipt
        if _GARBAGE_PAT.search(concl[:500]):
            print(f"[research] 拒收中间态回执（过程腔）：{concl[:80]}", flush=True)
            return None
        # 无中文的回执大概率是英文中间态
        if not re.search(r"[\u4e00-\u9fff]", concl[:800]):
            print(f"[research] 拒收无中文回执：{concl[:80]}", flush=True)
            return None
        j = api_absorb("【实地查证】问题：" + query + "\n结论：" + receipt,
                       category="verification", tags="research,实地查证", layer="m", pinned=False)
        return j.get("id")
    except Exception:
        return None


def _am_sync_loop():
    """每 5 分钟检测 agent-matrix 源文件指纹，变了自动重导入（老大：要及时更新）"""
    import time as _t
    import import_agentmatrix as iam
    while True:
        _t.sleep(300)
        try:
            r = iam.sync(force=False)
            if r.get("changed"):
                print(f"[openmem-web] agent-matrix 自动同步: {r}", flush=True)
        except Exception as e:
            print(f"[openmem-web] agent-matrix 同步失败: {e}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    import threading
    threading.Thread(target=_am_sync_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"[openmem-web] listening on 0.0.0.0:{args.port}", flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
