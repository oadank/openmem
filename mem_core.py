#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
openmem 记忆中枢 Python 内核
存储: PostgreSQL(openmem库) embedding: 本机BGE:11435  LLM: litellm GwV4F :4000
所有子命令输出 UTF-8 JSON 到 stdout，错误输出到 stderr 并 exit 1
"""
import sys, os, json, re, time, hashlib, argparse
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# 本地密钥/路径放 .env（gitignore），源码不写死真值
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

import psycopg2
import requests

# 凭据打码闸（2026-09-12）：任何写库内容先遮凭据值
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cred_guard import sanitize_credentials

PG_CONN = {
    "host": os.environ.get("OPENMEM_PG_HOST", "127.0.0.1"),
    "port": int(os.environ.get("OPENMEM_PG_PORT", "5432")),
    "database": os.environ.get("OPENMEM_PG_DB", "openmem"),
    "user": os.environ.get("OPENMEM_PG_USER", "postgres"),
    "password": os.environ.get("OPENMEM_PG_PASSWORD", ""),
}
EMBED_URL = os.environ.get("OPENMEM_EMBED_URL", "http://127.0.0.1:11435/v1/embeddings")
EMBED_MODEL = os.environ.get("OPENMEM_EMBED_MODEL", "bge-small-zh-v1.5")
LLM_URL = os.environ.get("OPENMEM_LLM_URL", "http://127.0.0.1:4000/v1/chat/completions")
LLM_KEY = os.environ.get("OPENMEM_LLM_KEY", "")
LLM_MODEL = os.environ.get("OPENMEM_LLM_MODEL", "GwV4F")

# 检索加权：pinned（权威种子/铁律）条目在 RRF 融合后乘此系数，防止被大量同类条目稀释
PINNED_BOOST = 1.5
# 结构化台账（非经验记忆）默认不进检索池；显式按 source/category 查时放行
LEDGER_EXCLUDE = "(source='agent-matrix' AND category='services')"

# 老大基础画像：优先 persona.local.txt（不入库），其次环境变量，最后占位
def _load_persona():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.local.txt")
    if os.path.isfile(p):
        return open(p, encoding="utf-8").read().strip()
    if os.environ.get("OPENMEM_PERSONA"):
        return os.environ["OPENMEM_PERSONA"]
    return "（请在 Web「AI 设置」改画像，或写 persona.local.txt，勿把真画像提交进 git）"

PERSONA = _load_persona()

ASK_RULES = """你是 openmem——老大陈丹（阿丹）的统一记忆中枢，现在以「咨询老师」身份回答其他 AI agent 的提问。
你的使命：基于【记忆上下文】，给出像老大本人亲自回答一样的答案——全面、准确、口语化、直接。
规则：
1. 只依据记忆上下文和基础画像回答；上下文没有的，明确写「记忆里没有这条」，绝不编造。
2. 涉及密码/密钥/token：只说存放位置（环境变量名、文件路径、服务名），绝不输出明文。
3. 信息有多个版本时，取时间最新的，并注明「以 X 年 X 月为准」。
4. 答案末尾单独一行输出引用编号，格式：REF: [id1, id2]（没有引用写 REF: []）。
5. 简体中文，白话直接，能用列表不用长段落。
6. 当你按规则 1 说了「记忆里没有」，且这件事可以通过查本机搞清楚（读配置文件/看目录/调接口/跑只读命令），
   就在答案最后一行追加：RESEARCH: 一句话写清要查什么（怎么查也提示一下）。
   例如「RESEARCH: C 盘根目录的文件夹清单，跑 dir /b /ad C:/ 就能拿到」。
   系统会自动派出研究智能体去实地查证并把结果补在答案下面，你只管把任务说清楚。
   注意：纯观点/判断类问题、或记忆里已经有答案的，不要写 RESEARCH。"""

ASK_SYSTEM = ASK_RULES + "\n\n【老大的基础画像】\n" + PERSONA + "\n"

# AI 运行配置默认值（可被 app_config 表覆盖；Web 设置页只暴露模型/提示词，调参用最优内置值）
DEFAULT_CONFIG = {
    "ask_header": ASK_RULES,
    "persona": PERSONA,
    "llm_base_url": os.environ.get("OPENMEM_LLM_BASE_URL", "http://127.0.0.1:4000"),
    "llm_key": os.environ.get("OPENMEM_LLM_KEY", ""),
    "model": os.environ.get("OPENMEM_LLM_MODEL", "Gwglm5.3"),
    "temperature": 0.3,        # 低温度保证记忆问答稳定不编造
    "max_tokens": 4000         # 推理模型思维链+长答案都要余量，2000 会被截断
}

def _llm_endpoint(cfg):
    base = (cfg.get("llm_base_url") or "http://127.0.0.1:4000").rstrip("/")
    if base.endswith("/v1/chat/completions"):
        return base
    return base + "/v1/chat/completions"

def _llm_key(cfg):
    return cfg.get("llm_key") or LLM_KEY

def get_config():
    """读运行配置：DB app_config 覆盖默认值；表不存在/行缺失时用默认"""
    cfg = dict(DEFAULT_CONFIG)
    try:
        conn = connect(); cur = conn.cursor()
        cur.execute("SELECT key, value FROM app_config")
        for k, v in cur.fetchall():
            if k in cfg:
                cfg[k] = v
        cur.close(); conn.close()
    except Exception:
        pass
    return cfg

def build_system_prompt(cfg):
    return cfg["ask_header"] + "\n\n【老大的基础画像】\n" + cfg["persona"] + "\n"


# ── 基础设施 ──────────────────────────────────────────────

def connect():
    return psycopg2.connect(**PG_CONN)

def emb(texts, retries=3):
    payload = {"input": texts, "model": EMBED_MODEL, "encoding_format": "float"}
    for attempt in range(retries):
        try:
            resp = requests.post(EMBED_URL, json=payload, timeout=120)
            resp.raise_for_status()
            return [item["embedding"] for item in resp.json()["data"]]
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
            else:
                raise RuntimeError(f"embedding服务失败: {e}")

def to_pg_array(vec):
    return "{" + ",".join(repr(float(x)) for x in vec) + "}"

def vec_literal(vec):
    """-> pgvector 字面量 '[a,b,c]'（配合 SQL 里的 %s::vector 用）"""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"

def content_hash(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def out(obj):
    print(json.dumps(obj, ensure_ascii=False))

def clean_nul(t):
    return t.replace("\x00", "") if t else ""

def llm_chat(messages, cfg=None, max_tokens=None, timeout=180):
    cfg = cfg or get_config()
    resp = requests.post(_llm_endpoint(cfg), json={
        "model": cfg["model"], "messages": messages,
        "max_tokens": max_tokens or cfg["max_tokens"], "temperature": cfg["temperature"]
    }, headers={"Authorization": f"Bearer {_llm_key(cfg)}"}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

def llm_chat_stream(messages, cfg=None, max_tokens=None, timeout=180):
    """流式版：yield 文本增量"""
    cfg = cfg or get_config()
    resp = requests.post(_llm_endpoint(cfg), json={
        "model": cfg["model"], "messages": messages,
        "max_tokens": max_tokens or cfg["max_tokens"], "temperature": cfg["temperature"],
        "stream": True
    }, headers={"Authorization": f"Bearer {_llm_key(cfg)}"}, timeout=timeout, stream=True)
    resp.raise_for_status()
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            j = json.loads(data)
            delta = (j.get("choices") or [{}])[0].get("delta", {}).get("content")
            if delta:
                yield delta
        except Exception:
            continue

def gen_related(query, answer):
    """基于问答生成 3 条追问建议，失败返回空"""
    try:
        txt = llm_chat([
            {"role": "system", "content": "基于刚才的问答，生成3条用户最可能追问的简短问题。只输出JSON数组，如 [\"问题1\",\"问题2\",\"问题3\"]，禁止输出其他任何内容。"},
            {"role": "user", "content": f"原问题：{query}\n回答开头：{answer[:600]}"}
        ], max_tokens=200, timeout=60)
        m = re.search(r"\[[\s\S]*?\]", txt)
        arr = json.loads(m.group(0)) if m else []
        return [str(x)[:60] for x in arr if isinstance(x, str)][:3]
    except Exception:
        return []

def do_ask_stream(query, requester, history=None, session_id=None, extra_context=None):
    """流式咨询：yield 事件 dict（status/delta/done/related），结束后写 ask_log
    history = 多轮追问上下文 [{q,a}]（web 控制台追问时传，最多取最近 4 轮）
    session_id = 同一次会话的归组 id（Web 每次新提问生成，追问沿用；bot 单问为 None）
    extra_context = 额外上下文文本（如研究智能体的实地查证回执），拼进 system"""
    cfg = get_config()
    yield {"event": "status", "msg": "正在检索全部记忆…"}
    ctx, refs = retrieve_context(query)
    yield {"event": "status", "msg": f"命中 {len(refs)} 条相关记忆，正在组织答案…"}
    if extra_context:
        ctx = ctx + "\n\n【实地查证回执（研究智能体刚刚查的，可信度高于陈旧记忆）】\n" + extra_context
    messages = [
        {"role": "system", "content": build_system_prompt(cfg) + "\n【记忆上下文】\n" + ctx},
    ]
    for h in (history or [])[-4:]:
        try:
            hq = str(h.get("q") or "")[:500]
            ha = str(h.get("a") or "")[:4000]
        except Exception:
            continue
        if hq:
            messages.append({"role": "user", "content": f"（此前一轮）问题：{hq}"})
            messages.append({"role": "assistant", "content": ha or "（无回答）"})
    tail = "（这是接着上文对话的追问，请结合上文连贯回答）\n" if history else ""
    messages.append({"role": "user", "content": f"提问方：{requester}（这是一个 AI agent）\n{tail}问题：{query}\n请以老大本人的口吻和立场给出完整准确的回答。"})
    parts = []
    for delta in llm_chat_stream(messages, cfg=cfg):
        parts.append(delta)
        yield {"event": "delta", "text": delta}
    answer = "".join(parts)
    conn = connect(); cur = conn.cursor()
    cur.execute("INSERT INTO ask_log (requester, query, answer, refs, session_id) VALUES (%s,%s,%s,%s,%s)",
                (requester, query, answer, json.dumps(refs), session_id))
    conn.commit(); cur.close(); conn.close()
    yield {"event": "done", "answer": answer, "refs": refs}
    related = gen_related(query, answer)
    if related:
        yield {"event": "related", "questions": related}


# ── 写入 / 检索 ───────────────────────────────────────────

def cmd_write(a):
    a.content, _cred_hits = sanitize_credentials(a.content)
    emb_vec = emb([a.content])[0]
    conn = connect(); cur = conn.cursor()
    ch = content_hash(a.content)
    cur.execute("""
        INSERT INTO memory_entries (layer, category, source, content, embedding, embedding_v, tags,
                                    confidence, pinned, content_hash)
        VALUES (%s,%s,%s,%s,%s,%s::vector,%s,%s,%s,%s)
        ON CONFLICT (source, content_hash) DO UPDATE
          SET content=EXCLUDED.content, embedding=EXCLUDED.embedding,
              embedding_v=EXCLUDED.embedding_v, tags=EXCLUDED.tags,
              updated_at=now(), last_verified_at=now()
        RETURNING id, (xmax = 0) AS inserted
    """, (a.layer, a.category, a.source, clean_nul(a.content), to_pg_array(emb_vec),
          vec_literal(emb_vec), a.tags, a.confidence, a.pinned, ch))
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    out({"ok": True, "id": str(row[0]), "inserted": bool(row[1]),
         "cred_masked": _cred_hits})


def cmd_update(a):
    """按 id 更新一条记忆（2026-09-14 新增）。
    只改传了的字段；content 变了就重算 embedding + content_hash。
    说明：openmem 此前的规矩是"写错了直接删了重写"，但删+写会换 id、
    打断 superseded_by / memory_conflicts 的引用链。mh_update 保留 id，
    是干净的原地更新。
    """
    a.content, _cred_hits = (sanitize_credentials(a.content) if a.content else (None, 0))
    conn = connect(); cur = conn.cursor()
    cur.execute("SELECT id FROM memory_entries WHERE id=%s", (a.id,))
    if not cur.fetchone():
        cur.close(); conn.close()
        out({"ok": False, "error": "id 不存在: %s" % a.id})
        return

    sets, params = [], []
    if a.content is not None:
        v = emb([a.content])[0]
        sets += ["content=%s", "embedding=%s", "embedding_v=%s::vector", "content_hash=%s"]
        params += [clean_nul(a.content), to_pg_array(v), vec_literal(v), content_hash(a.content)]
    for col in ("layer", "category", "source"):
        v = getattr(a, col, None)
        if v:
            sets.append("%s=%%s" % col); params.append(v)
    if a.tags is not None:
        sets.append("tags=%s"); params.append(a.tags)
    if a.confidence is not None:
        sets.append("confidence=%s"); params.append(a.confidence)
    if a.pinned is not None:
        sets.append("pinned=%s"); params.append(a.pinned)

    if not sets:
        cur.close(); conn.close()
        out({"ok": False, "error": "没有要更新的字段"})
        return

    sets += ["updated_at=now()", "last_verified_at=now()"]
    params.append(a.id)
    try:
        cur.execute("UPDATE memory_entries SET " + ", ".join(sets) + " WHERE id=%s RETURNING id",
                    tuple(params))
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        # source+content_hash 唯一约束：改成别条已有的内容会撞
        out({"ok": False, "error": "更新失败（可能与已有条目 (source, content_hash) 重复）: %s" % e})
        return
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    out({"ok": True, "id": str(row[0]), "updated": True, "cred_masked": _cred_hits})


# ── 混合检索公共件（2026-09-11 第一档优化）─────────────────
# 旧实现：把 4000 条 embedding 从 PG 数组字符串拉到 Python 里逐条 split/float/dot，
#        单次 2.8s 几乎全耗在这。现改为 pgvector(HNSW) 库内检索 + pg_trgm 关键词通道。

def _filters(a):
    """按 search 参数拼 WHERE 片段与参数（不含向量条件）"""
    sql = "superseded_by IS NULL" if not getattr(a, "include_archived", False) else "TRUE"
    params = []
    for col in ("layer", "category", "source"):
        v = getattr(a, col, None)
        if v:
            sql += " AND %s=%%s" % col
            params.append(v)
    if getattr(a, "tags", None):
        sql += " AND tags && %s"
        params.append(a.tags)
    if getattr(a, "since", None):
        sql += " AND created_at >= %s"
        params.append(a.since)
    # 默认排除结构化台账（agent-matrix/services，nssm 明细表）；显式指定 source 或 category 时放行
    if not (getattr(a, "source", None) or getattr(a, "category", None)):
        sql += " AND NOT " + LEDGER_EXCLUDE
    return sql, params


def _vector_top(cur, q_lit, where, params, limit):
    """通道 1：HNSW 向量检索（库内算，不再走 Python）"""
    cur.execute("""SELECT id, 1 - (embedding_v <=> %s::vector) AS score
                   FROM memory_entries
                   WHERE """ + where + """ AND embedding_v IS NOT NULL
                   ORDER BY embedding_v <=> %s::vector
                   LIMIT %s""", [q_lit] + params + [q_lit, limit])
    return cur.fetchall()


def _keyword_top(cur, query, where, params, limit):
    """通道 2：pg_trgm 关键词——端口号/文件名/服务名这类精确符号靠它兜住"""
    cur.execute("""SELECT id, similarity(content, %s) AS score
                   FROM memory_entries
                   WHERE """ + where + """
                   ORDER BY score DESC
                   LIMIT %s""", [query] + params + [limit])
    return cur.fetchall()


# 符号词元：标识符 / 文件名 / 路径 / 服务名 / IP / 版本号
_SYM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-:/\\]{2,}")
_SYM_STOP = {"the", "and", "for", "with", "this", "that", "true", "false", "null", "none", "def", "var"}


def _like_escape(s):
    """LIKE 模式转义（PG 默认转义符是反斜杠，且 _ 与 % 是通配符）"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sym_tokens(query, maxn=8):
    """从查询里抽出「符号型」词元：函数名 / 文件名 / 路径 / 端口 / 服务名 / IP"""
    out, seen = [], set()
    for raw in _SYM_RE.findall(query or ""):
        t = raw.strip(".-_/:\\")
        if len(t) < 3:
            continue
        k = t.lower()
        if k in seen or k in _SYM_STOP:
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= maxn:
            break
    return out


def _symbol_top(cur, query, where, params, limit):
    """通道 3：符号精确命中（2026-09-12 加）

    通道 2 的 similarity(content, query) 是【整条比整条】——条目越长分母越大，
    埋在中后段的短符号（函数名/端口号/文件名/服务名）分数被稀释到近乎 0。
    实测：库内含 syncModelToCli 的有 4 条，trgm 只有 0.010~0.022，检索时
    全部被无关长条目挤出榜外；查 uninsneveruninstall 也只排到第 2。

    这里改为：先从查询里抽出符号词元，逐个做子串命中（ILIKE，走 trgm GIN 索引），
    按【命中词元数】排序，同分时短条目前置（短条目更可能以该符号为主题）。
    """
    toks = _sym_tokens(query)
    if not toks:
        return []
    pats = ["%" + _like_escape(t) + "%" for t in toks]
    # [2026-09-13 修] 参数顺序必须与 SQL 中占位符的出现顺序一致：
    #   unnest(%s) → where 里的占位符 → ANY(%s) → LIMIT %s
    # 原写法 [pats, pats] + params + [limit] 把 where 的占位符顶到了第二个 pats 上：
    # 带 source/category/tags/since 过滤时，`source=%s` 会收到一个 list
    # → `operator does not exist: text = text[]` → 事务中断 → 整次检索报
    # 「当前事务被终止, 事务块结束之前的查询被忽略」（真正的报错行还甩锅给后面的 _boost_pinned）。
    # 不带过滤时 where 里没有占位符，凑巧对上，所以这个 bug 藏了很久。
    cur.execute("""SELECT id,
                          (SELECT count(*) FROM unnest(%s::text[]) AS t(p)
                            WHERE content ILIKE t.p)::float AS hits
                   FROM memory_entries
                   WHERE """ + where + """ AND content ILIKE ANY(%s)
                   ORDER BY hits DESC, length(content) ASC
                   LIMIT %s""", [pats] + params + [pats, limit])
    return cur.fetchall()


def _rrf(vec_rows, kw_rows, sy_rows=None, k=60, kw_weight=0.85, sy_weight=1.1):
    """Reciprocal Rank Fusion：三路排名融合，天然免疫量纲差异

    sy_weight 略高于 kw_weight：符号精确命中是比模糊文本相似更强的信号。
    """
    fused = {}
    for rank, (mid, _s) in enumerate(vec_rows):
        fused[mid] = fused.get(mid, 0.0) + 1.0 / (k + rank + 1)
    for rank, (mid, _s) in enumerate(kw_rows):
        fused[mid] = fused.get(mid, 0.0) + kw_weight / (k + rank + 1)
    for rank, (mid, _s) in enumerate(sy_rows or []):
        fused[mid] = fused.get(mid, 0.0) + sy_weight / (k + rank + 1)
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)


def _boost_pinned(cur, fused):
    """pinned（权威种子/铁律）条目 RRF 分数 ×PINNED_BOOST，保证地基条目不被海量同类稀释"""
    ids = [mid for mid, _ in fused]
    if not ids:
        return fused
    ph = ",".join(["%s"] * len(ids))
    cur.execute("SELECT id FROM memory_entries WHERE id::text IN (" + ph + ") AND pinned",
                [str(i) for i in ids])
    pin = set(str(r[0]) for r in cur.fetchall())
    if not pin:
        return fused
    return sorted(((mid, sc * (PINNED_BOOST if str(mid) in pin else 1.0)) for mid, sc in fused),
                  key=lambda x: x[1], reverse=True)


def _hybrid_ids(cur, query, q_vec, where, params, want, pool=80):
    """三路召回（向量 + 整条文本 + 符号精确）+ RRF 融合 + pinned 加权 → [(id, fused_score)] 前 want 条"""
    q_lit = vec_literal(q_vec)
    vec_rows = _vector_top(cur, q_lit, where, params, pool)
    try:
        kw_rows = _keyword_top(cur, query, where, params, max(20, want))
    except Exception:
        # [2026-09-13] 必须回滚：SQL 出错后事务进入 aborted 状态，
        # 不回滚则后续每条语句都报「当前事务被终止」，try 形同虚设。
        cur.connection.rollback()
        kw_rows = []
    try:
        sy_rows = _symbol_top(cur, query, where, params, max(20, want))
    except Exception:
        cur.connection.rollback()
        sy_rows = []
    fused = _boost_pinned(cur, _rrf(vec_rows, kw_rows, sy_rows))
    return fused[:want]


def cmd_search(a):
    t0 = time.time()
    q_vec = emb([a.query])[0]
    conn = connect(); cur = conn.cursor()
    where, params = _filters(a)
    pool = max(60, min(400, a.candidates // 40)) if getattr(a, "candidates", None) else 80
    fused = _hybrid_ids(cur, a.query, q_vec, where, params, a.top_k, pool=pool)
    ids = [mid for mid, _ in fused]
    scored = []
    if ids:
        ph = ",".join(["%s"] * len(ids))
        cur.execute("""SELECT id, layer, category, source, content, tags, pinned, created_at
                       FROM memory_entries WHERE id IN (""" + ph + """)""", ids)
        by_id = {r[0]: r for r in cur.fetchall()}
        for mid, sc in fused:
            r = by_id.get(mid)
            if not r:
                continue
            scored.append({"id": str(r[0]), "layer": r[1], "category": r[2], "source": r[3],
                           "content": r[4][:1500], "tags": r[5], "pinned": r[6],
                           "created_at": r[7].isoformat(), "score": round(float(sc), 5)})
    cur.close(); conn.close()
    out({"ok": True, "results": scored, "pool": pool,
         "elapsed_ms": int((time.time() - t0) * 1000)})

def cmd_service(a):
    """查 nssm 服务台账（agent-matrix/services）。

    台账正文是同一套 markdown 表格模板，向量区分度极低，语义检索查不准——
    所以单独走「服务名精确匹配」。不传 name 则列出全部服务名。
    """
    name = (a.name or "").strip()
    conn = connect(); cur = conn.cursor()
    if not name:
        cur.execute("""SELECT content FROM memory_entries
                       WHERE source='agent-matrix' AND category='services' AND superseded_by IS NULL""")
        names = []
        for (c,) in cur.fetchall():
            m = re.search(r'服务明细\s*([^】]+)】', c or '')
            if m:
                names.append(m.group(1).strip())
        cur.close(); conn.close()
        out({"ok": True, "count": len(set(names)), "services": sorted(set(names))})
        return
    def _q(pat):
        cur.execute("""SELECT id, content, created_at FROM memory_entries
                       WHERE source='agent-matrix' AND category='services' AND superseded_by IS NULL
                         AND content LIKE %s ORDER BY created_at DESC LIMIT 20""", [pat])
        return cur.fetchall()
    rows = _q('%服务明细 ' + name + '】%')          # 先精确
    exact = bool(rows)
    if not rows:
        rows = _q('%服务明细 %' + name + '%】%')     # 再子串
    res = [{"id": str(r[0]), "content": r[1], "created_at": r[2].isoformat()} for r in rows]
    cur.close(); conn.close()
    out({"ok": True, "name": name, "exact": exact, "count": len(res), "records": res})


def cmd_get(a):
    conn = connect(); cur = conn.cursor()
    cur.execute("""SELECT id, layer, category, source, content, tags, pinned, confidence,
                          created_at, updated_at, superseded_by
                   FROM memory_entries WHERE id=%s""", (a.id,))
    r = cur.fetchone(); cur.close(); conn.close()
    if not r:
        out({"ok": False, "error": "not found"}); return
    out({"ok": True, "entry": {"id": str(r[0]), "layer": r[1], "category": r[2], "source": r[3],
        "content": r[4], "tags": r[5], "pinned": r[6], "confidence": r[7],
        "created_at": r[8].isoformat(), "updated_at": r[9].isoformat(),
        "superseded_by": str(r[10]) if r[10] else None}})


# ── AI 对 AI 咨询（核心能力）─────────────────────────────

def retrieve_scores(query, candidates=4000, top_k=12):
    """只算相似度不组上下文：给 ask 的研究降级判断用（返回 top 分数列表）"""
    conn = connect(); cur = conn.cursor()
    q_lit = vec_literal(emb([query])[0])
    cur.execute("""SELECT 1 - (embedding_v <=> %s::vector)
                   FROM memory_entries
                   WHERE superseded_by IS NULL AND embedding_v IS NOT NULL
                   ORDER BY embedding_v <=> %s::vector
                   LIMIT %s""", (q_lit, q_lit, top_k))
    scores = [float(r[0]) for r in cur.fetchall()]
    cur.close(); conn.close()
    return scores

def retrieve_context(query, top_k=12):
    """pinned 事实 + 混合检索 top，组成记忆上下文（2026-09-11 改 pgvector 库内检索）"""
    conn = connect(); cur = conn.cursor()
    cur.execute("""SELECT id, source, content, created_at FROM memory_entries
                   WHERE pinned AND superseded_by IS NULL ORDER BY created_at DESC LIMIT 40""")
    pinned = cur.fetchall()
    q_vec = emb([query])[0]
    fused = _hybrid_ids(cur, query, q_vec, "superseded_by IS NULL", [], top_k, pool=80)
    ids = [mid for mid, _ in fused]
    top = []
    if ids:
        ph = ",".join(["%s"] * len(ids))
        cur.execute("""SELECT id, layer, category, source, content, created_at
                       FROM memory_entries WHERE id IN (""" + ph + """)""", ids)
        by_id = {r[0]: r for r in cur.fetchall()}
        top = [by_id[m] for m in ids if m in by_id]
    cur.close(); conn.close()

    ctx_lines = ["【钉住的关键事实】"]
    refs = []
    for r in pinned:
        ctx_lines.append(f"- (id={str(r[0])[:8]}, source={r[1]}) {r[2][:600]}")
        refs.append(str(r[0]))
    ctx_lines.append("\n【相关记忆】")
    for r in top:
        d = r[5].strftime("%Y-%m-%d") if r[5] else ""
        ctx_lines.append(f"- (id={str(r[0])[:8]}, source={r[3]}, {d}, {r[1]}/{r[2]}) {r[4][:800]}")
        refs.append(str(r[0]))
    return "\n".join(ctx_lines), refs

def do_ask(query, requester):
    cfg = get_config()
    ctx, refs = retrieve_context(query)
    answer = llm_chat([
        {"role": "system", "content": build_system_prompt(cfg) + "\n【记忆上下文】\n" + ctx},
        {"role": "user", "content": f"提问方：{requester}（这是一个 AI agent）\n问题：{query}\n请以老大本人的口吻和立场给出完整准确的回答。"}
    ], cfg=cfg)
    conn = connect(); cur = conn.cursor()
    cur.execute("INSERT INTO ask_log (requester, query, answer, refs) VALUES (%s,%s,%s,%s)",
                (requester, query, answer, json.dumps(refs)))
    conn.commit(); cur.close(); conn.close()
    return answer, refs

def cmd_ask(a):
    answer, refs = do_ask(a.query, a.agent)
    out({"ok": True, "answer": answer, "refs": refs, "model": LLM_MODEL})


# ── 预生成答案工具（标准化提示词，调用即取最新）─────────

def generate_tool_answer(tool_name, prompt_template):
    cfg = get_config()
    ctx, refs = retrieve_context(prompt_template)
    answer = llm_chat([
        {"role": "system", "content": build_system_prompt(cfg) + "\n【记忆上下文】\n" + ctx},
        {"role": "user", "content": f"这是为预设工具「{tool_name}」生成标准答案。\n工具提示词：{prompt_template}\n请输出该问题的最新、最准、可直接交付的答案（不用寒暄）。"}
    ], cfg=cfg)
    return answer, refs

def cmd_tool_call(a):
    conn = connect(); cur = conn.cursor()
    cur.execute("""SELECT id, name, prompt_template, cached_answer, answer_updated_at, refresh_interval
                   FROM preset_tools WHERE name=%s AND enabled""", (a.name,))
    r = cur.fetchone()
    if not r:
        cur.close(); conn.close()
        out({"ok": False, "error": f"工具不存在: {a.name}"}); return
    tid, name, tmpl, cached, updated_at, interval = r
    fresh = False
    if cached and updated_at and interval:
        from datetime import timedelta
        iv = interval if isinstance(interval, timedelta) else timedelta(days=1)
        fresh = datetime.now(timezone.utc).replace(tzinfo=None) < (updated_at + iv).replace(tzinfo=None) if updated_at.tzinfo is None else datetime.now(timezone.utc) < updated_at + iv
    cur.close(); conn.close()
    if cached and fresh and not a.force_refresh:
        out({"ok": True, "name": name, "answer": cached, "cached": True,
             "answer_updated_at": updated_at.isoformat() if updated_at else None})
        return
    answer, refs = generate_tool_answer(name, tmpl)
    conn = connect(); cur = conn.cursor()
    cur.execute("UPDATE preset_tools SET cached_answer=%s, answer_updated_at=now() WHERE id=%s",
                (answer, tid))
    conn.commit(); cur.close(); conn.close()
    out({"ok": True, "name": name, "answer": answer, "cached": False, "refs": refs})

def cmd_tool_create(a):
    conn = connect(); cur = conn.cursor()
    cur.execute("""INSERT INTO preset_tools (name, description, prompt_template, refresh_interval)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (name) DO UPDATE SET description=EXCLUDED.description,
                     prompt_template=EXCLUDED.prompt_template RETURNING id""",
                (a.name, a.description, a.prompt, a.interval))
    tid = cur.fetchone()[0]; conn.commit(); cur.close(); conn.close()
    answer, refs = generate_tool_answer(a.name, a.prompt)
    conn = connect(); cur = conn.cursor()
    cur.execute("UPDATE preset_tools SET cached_answer=%s, answer_updated_at=now() WHERE id=%s", (answer, tid))
    conn.commit(); cur.close(); conn.close()
    out({"ok": True, "id": str(tid), "name": a.name, "answer": answer})

def cmd_tool_refresh(a):
    conn = connect(); cur = conn.cursor()
    only_expired = getattr(a, "expired_only", False)
    if a.name:
        cur.execute("SELECT id, name, prompt_template FROM preset_tools WHERE name=%s AND enabled", (a.name,))
    elif only_expired:
        cur.execute("""SELECT id, name, prompt_template FROM preset_tools
                       WHERE enabled AND (answer_updated_at IS NULL
                             OR answer_updated_at + COALESCE(refresh_interval, interval '1 day') < now())""")
    else:
        cur.execute("SELECT id, name, prompt_template FROM preset_tools WHERE enabled")
    tools = cur.fetchall(); cur.close(); conn.close()
    if not tools:
        out({"ok": True, "refreshed": [], "note": "无过期工具"}); return
    results = []
    for tid, name, tmpl in tools:
        try:
            answer, _ = generate_tool_answer(name, tmpl)
            conn = connect(); cur = conn.cursor()
            cur.execute("UPDATE preset_tools SET cached_answer=%s, answer_updated_at=now() WHERE id=%s", (answer, tid))
            conn.commit(); cur.close(); conn.close()
            results.append({"name": name, "ok": True})
        except Exception as e:
            results.append({"name": name, "ok": False, "error": str(e)})
    out({"ok": True, "refreshed": results})

def cmd_tools_list(a):
    conn = connect(); cur = conn.cursor()
    cur.execute("""SELECT name, description, answer_updated_at, enabled, refresh_interval
                   FROM preset_tools ORDER BY name""")
    rows = cur.fetchall(); cur.close(); conn.close()
    out({"ok": True, "tools": [{"name": r[0], "description": r[1],
        "answer_updated_at": r[2].isoformat() if r[2] else None,
        "enabled": r[3], "refresh_interval": str(r[4])} for r in rows]})


# ── 批量导入 / 状态 ──────────────────────────────────────

def cmd_import_batch(a):
    with open(a.file, encoding="utf-8") as f:
        items = json.load(f)
    total = len(items)
    # 闸 0：凭据打码——任何来源的内容进库前先遮凭据值（2026-09-12）
    masked_n = 0
    for x in items:
        if isinstance(x.get("content"), str):
            x["content"], n = sanitize_credentials(x["content"])
            masked_n += 1 if n else 0
    # 闸 1：拒收 agentmemory-insights（抽象方法论，会污染检索；与 server.js mh_write 策略一致）
    items = [x for x in items if (x.get("source") or "").strip().lower() != "agentmemory-insights"]
    rejected = total - len(items)
    # 闸 2：增量——已存在的 (source, content_hash) 直接跳过，不再白算 embedding
    conn0 = connect(); cur0 = conn0.cursor()
    cur0.execute("SELECT source, content_hash FROM memory_entries")
    existing = set(cur0.fetchall())
    cur0.close(); conn0.close()
    items = [x for x in items if (x.get("source", "unknown"), content_hash(x["content"])) not in existing]
    skipped = total - rejected - len(items)
    inserted, dup, failed = 0, 0, 0
    B = 32
    for i in range(0, len(items), B):
        batch = items[i:i+B]
        texts = [clean_nul(x["content"]) for x in batch]
        try:
            vecs = emb(texts)
        except Exception as e:
            print(f"[batch {i}] embed fail: {e}", file=sys.stderr); failed += len(batch); continue
        conn = connect(); cur = conn.cursor()
        for x, vec in zip(batch, vecs):
            try:
                # 双写 embedding_v（vector 列，HNSW 检索用）——2026-09-12 修复漏写 bug
                cur.execute("""
                    INSERT INTO memory_entries (layer, category, source, content, embedding, embedding_v, tags,
                                                confidence, pinned, content_hash, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s::vector,%s,%s,%s,%s, COALESCE(%s, now()))
                    ON CONFLICT (source, content_hash) DO NOTHING
                """, (x.get("layer", "k"), x.get("category", "misc"), x.get("source", "unknown"),
                      clean_nul(x["content"]), to_pg_array(vec), vec_literal(vec), x.get("tags", []),
                      x.get("confidence", 0.5), x.get("pinned", False), content_hash(x["content"]),
                      x.get("created_at")))
                if cur.rowcount: inserted += 1
                else: dup += 1
            except Exception as e:
                print(f"[insert fail] {x.get('source')}: {e}", file=sys.stderr); failed += 1
        conn.commit(); cur.close(); conn.close()
        print(f"[进度] {min(i+B,total)}/{total}", file=sys.stderr)
    out({"ok": True, "total": total, "inserted": inserted, "duplicate": dup,
         "skipped_existing": skipped, "rejected_insights": rejected, "failed": failed,
         "cred_masked_entries": masked_n})

def cmd_status(a):
    conn = connect(); cur = conn.cursor()
    for label, sql in [("total", "SELECT count(*) FROM memory_entries"),
                       ("pinned", "SELECT count(*) FROM memory_entries WHERE pinned"),
                       ("archived", "SELECT count(*) FROM memory_entries WHERE superseded_by IS NOT NULL"),
                       ("conflicts", "SELECT count(*) FROM memory_conflicts WHERE status='pending'"),
                       ("tools", "SELECT count(*) FROM preset_tools WHERE enabled"),
                       ("asks", "SELECT count(*) FROM ask_log")]:
        cur.execute(sql); n = cur.fetchone()[0]
        out({"metric": label, "value": n})
    cur.close(); conn.close()


# ── CLI ──────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write")
    w.add_argument("--content", required=True)
    w.add_argument("--source", required=True)
    w.add_argument("--layer", default="k", choices=["k", "m"])
    w.add_argument("--category", default="misc")
    w.add_argument("--tags", default="", help="逗号分隔")
    w.add_argument("--confidence", type=float, default=0.5)
    w.add_argument("--pinned", action="store_true")
    w.set_defaults(func=lambda a: cmd_write(_tags(a)))

    u = sub.add_parser("update")
    u.add_argument("--id", required=True, help="要更新的条目 id（uuid）")
    u.add_argument("--content", help="新内容；给了就重算 embedding")
    u.add_argument("--layer", choices=["k", "m"])
    u.add_argument("--category")
    u.add_argument("--source")
    u.add_argument("--tags", help="逗号分隔；给了就整组替换")
    u.add_argument("--confidence", type=float)
    u.add_argument("--pinned", type=lambda s: str(s).lower() in ("1", "true", "yes"), help="true/false")
    u.set_defaults(func=lambda a: cmd_update(_tags_opt(a)))

    s = sub.add_parser("search")
    s.add_argument("--query", required=True)
    s.add_argument("--layer"); s.add_argument("--category"); s.add_argument("--source")
    s.add_argument("--tags", default=""); s.add_argument("--since")
    s.add_argument("--top_k", type=int, default=10)
    s.add_argument("--candidates", type=int, default=4000)
    s.add_argument("--include_archived", action="store_true")
    s.set_defaults(func=lambda a: cmd_search(_tags(a)))

    sv = sub.add_parser("service")
    sv.add_argument("--name", default="", help="服务名关键词；留空列出全部")
    sv.set_defaults(func=cmd_service)

    g = sub.add_parser("get"); g.add_argument("--id", required=True)
    g.set_defaults(func=cmd_get)

    k = sub.add_parser("ask"); k.add_argument("--query", required=True)
    k.add_argument("--agent", default="unknown-agent")
    k.set_defaults(func=cmd_ask)

    t = sub.add_parser("tool_call"); t.add_argument("--name", required=True)
    t.add_argument("--force_refresh", action="store_true")
    t.set_defaults(func=cmd_tool_call)

    tc = sub.add_parser("tool_create")
    tc.add_argument("--name", required=True); tc.add_argument("--prompt", required=True)
    tc.add_argument("--description", default=""); tc.add_argument("--interval", default="1 day")
    tc.set_defaults(func=cmd_tool_create)

    tr = sub.add_parser("tool_refresh"); tr.add_argument("--name")
    tr.add_argument("--expired_only", action="store_true", help="只刷已过期的（后台保鲜线程用）")
    tr.set_defaults(func=cmd_tool_refresh)

    sub.add_parser("tools_list").set_defaults(func=cmd_tools_list)

    ib = sub.add_parser("import_batch"); ib.add_argument("--file", required=True)
    ib.set_defaults(func=cmd_import_batch)

    sub.add_parser("status").set_defaults(func=cmd_status)

    a = p.parse_args()
    try:
        a.func(a)
    except Exception as e:
        print(f"[openmem error] {e}", file=sys.stderr)
        sys.exit(1)

def _tags(a):
    a.tags = [t.strip() for t in (a.tags or "").split(",") if t.strip()]
    return a

def _tags_opt(a):
    """update 用：--tags 没传(None) 表示"不动"，传了(含空串) 才整组替换"""
    if getattr(a, "tags", None) is None:
        return a
    a.tags = [t.strip() for t in a.tags.split(",") if t.strip()]
    return a

if __name__ == "__main__":
    main()
