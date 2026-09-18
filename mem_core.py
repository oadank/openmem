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

_UUID_RE = re.compile(r'^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$')

def resolve_entry_id(cur, raw):
    """id 入口统一解析（2026-09-18 老大拍板）：完整 uuid 直过；≥6 位十六进制短 id
    按前缀查库——唯一命中自动补全，多义报候选列表，零命中报不存在。
    与 web_server._resolve_entry_id 同口径，MCP 侧（mh_get/mh_update）不再弹 -32602。"""
    rid = (raw or '').strip().lower()
    if _UUID_RE.match(rid):
        return rid, None
    if not re.match(r'^[0-9a-f]{6,32}$', rid):
        return None, 'id 格式不对：传完整 36 位 uuid，或其开头 ≥6 位十六进制短 id: %s' % raw
    cur.execute("SELECT id::text FROM memory_entries WHERE id::text LIKE %s LIMIT 2", (rid + '%',))
    rows = [r[0] for r in cur.fetchall()]
    if len(rows) == 1:
        return rows[0], None
    if not rows:
        return None, 'id 查无此条（短 id 无命中）: %s' % raw
    return None, '短 id 有歧义（命中多条），请加长前缀或用完整 uuid。候选: %s' % rows

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


# ── 写入闸门（2026-09-18 老大拍板）─────────────────────────
# 信念：全体 agent 都是 openmem 的共建者 —— 不能只拉屎不擦屁股。
#   ① 规矩闸门：**你自己**没领过规矩 → 拦（**票制**：领一次 = 一张票，写成功一条用掉一张）
#   ② 查重闸门：**你自己** 30min 内没搜过库 → 拦，逼它先 mh_search
#   ③ 重复闸门：要写的这条库里已有 ≥GATE_DUP_HI 相似度 → 拦，逼它去 mh_update
#      + 放行时回显最像的 N 条，把"重复证据"直接拍它脸上
#
# 🔴 没有豁免（2026-09-18 老大第二次纠正，别改回去）：
#   初版我自创了「每关每个 agent 只拦一次、拦过豁免 7 天」—— 老大当场否掉：
#   「谁让你这么定的？你为何给自己和其他 agents 开后门？你看桌面助手是这样的规定吗？
#     github token 的闸门是你这样的规定吗？」
#   对照属实，两个参照物都没有"豁免"这回事：
#     · win-desktop-helper：guideRead 是**进程内布尔**，客户端每起一个新会话进程就归零，无豁免。
#     · api-gate：PATH 包装，**每次外部调用都判**，无豁免。
#   我当初加豁免的理由是"怕卡死写入"—— 纯属自作多情：被拦 ≠ 失败，
#   返回体里当场给了过法，照做就过。**闸门只有判 / 不判，没有"拦过一次就放过几天"。**
# 只拦 mh_write（新增）；mh_update 一律放行 —— 那正是我们鼓励的"擦屁股"动作。
# 改本文件立即生效（MCP 每次调用 spawn 新 Python，无需重启服务）。
#
# 🔴 2026-09-18 老大纠正（重要，别改回去）：
#   ①② 必须**按 agent 各自记账**。规矩是每个 agent 自己的责任，不是集体荣誉 ——
#   「codex 领过规矩，所以 claude 可以随便写」是错的，等于①关退化成"每天随机抽一个
#   倒霉蛋去学规矩、其余人免考"。查重同理：别人搜过库 ≠ 你搜过。
#   旧版用全库共享的 last_handbook_at / last_search_at 判定，是图省 token 的错误折中，已废。

GATE_SEARCH_MIN = 30      # 查重时间窗（分钟）——按 agent 各自算；超窗即拦，无豁免
GATE_DUP_HI = 0.95        # 相似度红线：≥ 视为重复
GATE_DUP_SHOW = 3         # 回显相似条数

# 规矩 = 准入条文，精简（RULES.md）—— 闸门要的是"领它这个动作"，不是知识。
GATE_SKILL_TOOL = "openmem 写入规矩"     # 兼容老路（mh_tool 的名字）；新路 = 独立工具 mh_skill
GATE_SKILL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "RULES.md")
# 🔴 2026-09-18 老大拍板：**MANUAL.md 已删**（11491 字符 / 7117 token 的胖手册）。
#   原则：能塞进技能文件（agent 自动加载的那三份）的塞进去，塞不进的一律不留。
#   ⇒ mh_skill 只给规矩一种，没有 detail="full" 的胖分支，不再有第二份真源。

# 🔴 状态载体：PG 表 gate_state / gate_meta（2026-09-18 从 _gate_state.json 迁入）
#   为什么搬：JSON 是「读全文 → 改一个键 → 写回全文」，两个 agent 同时写就丢票、丢搜索时刻
#   （os.replace 只保证文件不写坏，保证不了不丢更新）；PG 按行 upsert 天然并发安全，
#   还能审计「谁什么时候领的票」。旧 JSON 已迁移，改名 .migrated 留档、不再读。
GATE_PG_READY = False     # 进程级：建表只做一次（MCP 每次调用 spawn 新进程，代价可忽略）
GATE_ALIAS = {"mimo code": "mimo", "mimo-code": "mimo", "dsh-bot": "dsh",
              "work buddy": "workbuddy", "codebuddy": "workbuddy"}
_GATE_FAIL = object()     # _gate_sql 的失败哨兵


def _gate_norm(source):
    """agent 名归一 —— 🔴 闸门按 source 字符串记账，大小写不归一 = 同一个人两个身份。
    实测库里 source 就分裂过：workbuddy 66 / WorkBuddy 28、mimo 9 / MiMo Code 36。
    归一只影响闸门记账，**不动 memory_entries 里的历史 source**。
    """
    a = re.sub(r"\s+", " ", (source or "").strip().lower())
    return GATE_ALIAS.get(a, a) or "unknown"


def _gate_sql(sql, params=(), fetch=None):
    """闸门专用短连接。🔴 **fail-open**：任何异常都吞掉并返回失败哨兵 ——
    闸门是附加纪律，绝不能因为 PG 抽风就把记忆写入整条卡死（宁可放行，不许锁死）。
    返回：fetch="all"→list / fetch="one"→row / fetch=None→None；失败→_GATE_FAIL。
    """
    try:
        conn = connect(); cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall() if fetch == "all" else (cur.fetchone() if fetch == "one" else None)
        conn.commit(); cur.close(); conn.close()
        return rows
    except Exception:
        return _GATE_FAIL


def _gate_init():
    global GATE_PG_READY
    if GATE_PG_READY:
        return True
    a = _gate_sql("""CREATE TABLE IF NOT EXISTS gate_state (
          agent text PRIMARY KEY, searched_at double precision DEFAULT 0, search_count integer DEFAULT 0,
          rules_ready boolean DEFAULT false, rules_taught_at double precision DEFAULT 0,
          rules_taught_count integer DEFAULT 0, rules_used_at double precision DEFAULT 0,
          skill_denied integer DEFAULT 0, skill_denied_at double precision DEFAULT 0,
          search_denied integer DEFAULT 0, search_denied_at double precision DEFAULT 0,
          dup_denied integer DEFAULT 0, dup_seen text[] DEFAULT '{}',
          updated_at timestamptz DEFAULT now())""")
    b = _gate_sql("CREATE TABLE IF NOT EXISTS gate_meta (key text PRIMARY KEY, val double precision DEFAULT 0)")
    GATE_PG_READY = (a is not _GATE_FAIL) and (b is not _GATE_FAIL)
    return GATE_PG_READY

# 🔴 2026-09-18 老大拍板重做（照 win-desktop-helper 的 get_skill 模式）：
#   win 那边是：独立工具 get_skill（描述自带【必须先调用】）+ 默认只给核心版 +
#   detail="full" 才给完整 + topic="关键词" 按标题抽段 + 拦下只说一句「去调 get_skill」。
#   我照抄，只改两点（openmem 与它不同）：
#     ① 它拦「所有工具」，openmem 只拦 mh_write —— 读操作零污染，拦读纯粹招骂。
#     ② 它把状态放进程内布尔（每个 bridge 进程一份 → 天然「每会话归零」）；
#        openmem 是无状态 HTTP（server.js 每个请求都新建 server+transport、sessionIdGenerator: undefined
#        → 服务端根本不知道"会话"为何物，**做不出真正的"每会话领一次"**）。
#        取最严的近似：**每次写入前都要领一次规矩 —— 票制，领一次放行一条、写完即消费。**
#        宁可每条多花 ~500 token，也不留"领一次管好几天"的口子。键 = mh_write 本来就有的 source。


def gate_rules_text():
    """规矩真源 = RULES.md（走本地文件，不经 AI 生成、可版本控制）"""
    try:
        with open(GATE_SKILL_FILE, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return ("# openmem 写入规矩（RULES.md 读取失败：%s）\n\n"
                "① 先搜（mh_search，带 requester）→ ② 有则 mh_update → ③ 无则 mh_write → "
                "④ 同主题只维护一条活条目。错的直接删，不搞归档。" % e)

# 规矩正文外置到 RULES.md（由 gate_rules_text() 读取）—— 2026-09-18 老大纠正：
# 闸门要的是"领规矩"这个动作，不是把一份胖手册塞回上下文。
# 拦截返回体里不再附规矩正文，只留一句话提示「去调 mh_skill」。


def gate_skill_text(detail="", topic=""):
    """mh_skill 的正文 —— **只有规矩一种**，没有"完整手册"这回事了。

    2026-09-18 老大拍板：MANUAL.md 太大（11491 字符 / 7117 token）**已删**；
    原则是「能塞进技能文件的塞进去，塞不进的一律不留」。
    detail / topic 参数保留**纯粹为兼容老调用方，一律忽略**。
    """
    return gate_rules_text()


def _gate_agent_get(agent):
    """取某 agent 的闸门行。三种返回**必须分清**（09-18 修，别把新人当故障放行）：
      · dict        = 有这行
      · None        = 查得到、但没这行 → **新人**，按「手上没票」处理（该拦就拦）
      · _GATE_FAIL  = PG 读不到 → 基础设施故障，调用方 fail-open 放行
    """
    row = _gate_sql("""SELECT rules_ready, coalesce(searched_at,0), coalesce(dup_seen,'{}'),
                              coalesce(skill_denied,0), coalesce(search_denied,0), coalesce(dup_denied,0)
                       FROM gate_state WHERE agent=%s""", (_gate_norm(agent),), fetch="one")
    if row is _GATE_FAIL:
        return _GATE_FAIL
    if row is None:
        return None
    return {"agent": _gate_norm(agent), "rules_ready": bool(row[0]), "searched_at": float(row[1] or 0),
            "dup_seen": list(row[2] or []), "skill_denied": row[3],
            "search_denied": row[4], "dup_denied": row[5]}


def gate_mark_search(requester):
    """mh_search 命中 → 留痕（供「写前查重」闸门放行用）

    🔴 只认**自报姓名**的 requester（2026-09-18 老大纠正）。
    不填姓名就只记全库统计、不记到任何人名下 —— 否则所有不填 requester 的 agent
    会共用 "mcp-client" 一个身份，变成"A 搜了，B 也能白嫖"。
    名字按 _gate_norm 归一（09-18 晚：大小写/别名算同一个人）。
    """
    rq = re.sub(r"\s+", " ", (requester or "").strip().lower())
    now = time.time()
    _gate_sql("INSERT INTO gate_meta (key,val) VALUES ('last_search_at',%s) "
              "ON CONFLICT (key) DO UPDATE SET val=EXCLUDED.val", (now,))
    if rq and rq != "mcp-client":
        _gate_sql("""INSERT INTO gate_state (agent, searched_at, search_count) VALUES (%s,%s,1)
                     ON CONFLICT (agent) DO UPDATE SET searched_at=EXCLUDED.searched_at,
                       search_count=gate_state.search_count+1, updated_at=now()""",
                  (_gate_norm(rq), now))


def gate_mark_skill(agent):
    """领到写入规矩 → 发一张**票**（①关唯一的放行依据）

    2026-09-18 老大拍板重做：规矩做成**独立工具 mh_skill**（描述自带【必须先调用】），
    闸门拦下只说一句「去调 mh_skill」—— 不再把它塞进 mh_tool(name="…") 的一个字符串参数里
    （那样工具清单里根本看不出"该领规矩了"，还得记住一个字符串）。
    必须带 agent：没报名字 = 记不到任何人头上 = 不算数。

    🔴 票制（2026-09-18 老大第二次纠正，别改回窗口制）：
       **领一次 = 一张票，写成功一条消费掉一张。**
       openmem 无状态、做不出「每会话领一次」，就取最严的近似：**每次写入前都要领**。
       （旧版「领一次管 12 小时」= 领一次能管好几条，正是老大骂的"开后门"，已废。）
    """
    a = (agent or "").strip()
    if not a or a.lower() in ("unknown", "unknown-agent", "mcp-client"):
        return False
    now = time.time()
    r = _gate_sql("""INSERT INTO gate_state (agent, rules_ready, rules_taught_at, rules_taught_count)
                     VALUES (%s,true,%s,1)
                     ON CONFLICT (agent) DO UPDATE SET rules_ready=true,
                       rules_taught_at=EXCLUDED.rules_taught_at,
                       rules_taught_count=gate_state.rules_taught_count+1, updated_at=now()""",
                  (_gate_norm(a), now))
    if r is _GATE_FAIL:
        return False            # 票没发出去就老实说没发，别骗调用方（否则①关会一直拦它）
    _gate_sql("INSERT INTO gate_meta (key,val) VALUES ('last_skill_at',%s) "
              "ON CONFLICT (key) DO UPDATE SET val=EXCLUDED.val", (now,))
    return True


def gate_consume_skill_ticket(source):
    """写入成功 → 把这张票用掉（下次再写，还得先领）"""
    _gate_sql("""UPDATE gate_state SET rules_ready=false, rules_used_at=%s, updated_at=now()
                 WHERE agent=%s AND rules_ready""", (time.time(), _gate_norm(source)))
    return True


def gate_mark_handbook(tool_name, agent=""):
    """兼容老路：mh_tool(name="openmem 写入规矩") 也算领规矩（新路 = mh_skill）"""
    if not tool_name or GATE_SKILL_TOOL not in str(tool_name):
        return False
    return gate_mark_skill(agent)


def gate_check_write(source):
    """闸门 ①②：返回 None=放行；返回 dict=拦下（已可直接 out()）

    🔴 两道闸门都按 source（=「你自己」）记账，不看别人（2026-09-18 老大纠正）。
    规矩/查重是每个 agent 各自的义务 —— 别人领过、别人搜过，与你无关。
    🔴 **两道都没有豁免**：每次都真判，不存在"拦过一次就放过几天"（2026-09-18 老大第二次纠正）。
    🔴 **fail-open（09-18 定）**：闸门状态**读不出来**（PG 抽风）→ 直接放行。闸门是附加纪律，
    绝不能因为基础设施故障把「写记忆」这条主链锁死。但「新人第一次写」≠ 故障 —— 该拦照拦。
    """
    rq = _gate_norm(source)
    ag = _gate_agent_get(rq)
    if ag is _GATE_FAIL:              # 只有真故障才放行
        return None
    rules_ready = bool(ag and ag["rules_ready"])
    searched_at = ag["searched_at"] if ag else 0.0
    now = time.time()

    # ① 规矩闸门：你自己手上没票（= 没领过规矩，或那张票已经用掉了）→ 拦
    #    🔴 票制（老大定）：领一次管一条，无窗口、无豁免。
    if not rules_ready:
        _gate_sql("""INSERT INTO gate_state (agent, skill_denied, skill_denied_at) VALUES (%s,1,%s)
                     ON CONFLICT (agent) DO UPDATE SET skill_denied=gate_state.skill_denied+1,
                       skill_denied_at=EXCLUDED.skill_denied_at, updated_at=now()""", (rq, now))
        return {
            "ok": False, "gate": "rules", "denied": True,
            "reason": "写入被拦（第 1 关 / 共 3 关）：你（%s）手上没有规矩票。" % source,
            "how_to_pass": '调用 mh_skill(agent="%s") —— 它就在你的工具清单里、无需别的参数，'
                           '几秒返回（就是给你规矩）。拿到票后直接再写一次即可放行；'
                           '**写成功一条，票就用掉了，下次再写还得重新领。**' % source,
            "note": "票制：领一次管一条（openmem 是无状态服务，做不出「每会话一次」）。"
                    "规矩是每个 agent 各自的义务 —— 别人领过，不代表你可以跳过。",
        }

    # ② 查重闸门：你自己 30min 内没搜过库 → 拦。**无豁免，超窗就拦。**
    if (now - searched_at) > GATE_SEARCH_MIN * 60:
        _gate_sql("""INSERT INTO gate_state (agent, search_denied, search_denied_at) VALUES (%s,1,%s)
                     ON CONFLICT (agent) DO UPDATE SET search_denied=gate_state.search_denied+1,
                       search_denied_at=EXCLUDED.search_denied_at, updated_at=now()""", (rq, now))
        return {
            "ok": False, "gate": "search", "denied": True,
            "reason": "写入被拦（第 2 关 / 共 3 关）：你（%s）下笔前没搜过库 —— 重复条目就是这么来的。" % source,
            "how_to_pass": '先 mh_search(<你这条的关键词>, requester="%s")，'
                           '**必须带上 requester 才记得到你名下**；看完结果再写一次即可放行。' % source,
            "note": "本关按 agent 各自记账，超 %d 分钟没搜过就拦，**无豁免**。" % GATE_SEARCH_MIN,
        }
    return None


def _gate_neighbours(cur, emb_vec, limit=GATE_DUP_SHOW, exclude_id=None):
    """库里与本条最像的 N 条（跨 source，含相似度）"""
    q = vec_literal(emb_vec)
    ex = exclude_id or "00000000-0000-0000-0000-000000000000"
    cur.execute("""SELECT id, source, category, content, 1 - (embedding_v <=> %s::vector) AS score
                   FROM memory_entries
                   WHERE superseded_by IS NULL AND embedding_v IS NOT NULL AND id <> %s
                   ORDER BY embedding_v <=> %s::vector
                   LIMIT %s""", [q, ex, q, limit])
    return [{"id": str(r[0]), "source": r[1], "category": r[2],
             "score": round(float(r[4]), 4), "snippet": (r[3] or "")[:120]}
            for r in cur.fetchall()]


def gate_check_dup(cur, source, emb_vec, content):
    """闸门 ③：库里已有高度相似的条目 → 拦，逼它 mh_update。

    🔴 内含**安全阀**（09-18 补进文档）：同一版内容被拦过一次后、再交一次即放行 ——
    防「确实该写却永远过不去」把 agent 卡死。安全阀是防死锁，不是给闸门开口子。
    """
    try:
        near = _gate_neighbours(cur, emb_vec, GATE_DUP_SHOW)
    except Exception:
        cur.connection.rollback()
        return None
    if not near or near[0]["score"] < GATE_DUP_HI:
        return None
    rq = _gate_norm(source)
    ag = _gate_agent_get(rq)
    if ag is _GATE_FAIL:
        return None                    # fail-open
    h = content_hash(content)[:16]
    seen = list(ag["dup_seen"]) if ag else []
    if h in seen:                      # 同一版内容坚持要写 → 放行（安全阀）
        return None
    seen = (seen + [h])[-30:]
    _gate_sql("""INSERT INTO gate_state (agent, dup_seen, dup_denied) VALUES (%s,%s,1)
                 ON CONFLICT (agent) DO UPDATE SET dup_seen=EXCLUDED.dup_seen,
                   dup_denied=gate_state.dup_denied+1, updated_at=now()""", (rq, seen))
    return {
        "ok": False, "gate": "dup", "denied": True,
        "reason": "写入被拦（第 3 关）：与库里已有条目相似度 %.2f，基本是同一件事。" % near[0]["score"],
        "similar": near,
        "how_to_pass": '改用 mh_update(id="%s", content="<合并后的最新版>")。' % near[0]["id"],
        "note": "确实不是一回事，就把内容写得更具体再交一次。",
    }


# ── 写入 / 检索 ───────────────────────────────────────────

def cmd_write(a):
    a.content, _cred_hits = sanitize_credentials(a.content)
    emb_vec = emb([a.content])[0]
    conn = connect(); cur = conn.cursor()

    # ── 写入闸门 ①②：规矩 + 查重（2026-09-18）────────────
    gate = gate_check_write(a.source)
    if gate:
        cur.close(); conn.close(); out(gate); return
    # ── 写入闸门 ③：与库里已有条目高度雷同 → 逼它去 update
    dup = gate_check_dup(cur, a.source, emb_vec, a.content)
    if dup:
        cur.close(); conn.close(); out(dup); return

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
    # 写后回显：把库里最像的几条拍给它看（正面引导去重，不拦人）
    similar = []
    try:
        similar = _gate_neighbours(cur, emb_vec, GATE_DUP_SHOW, exclude_id=str(row[0]))
    except Exception:
        cur.connection.rollback()
    conn.commit(); cur.close(); conn.close()
    # ①关票制：写成功 → 把这张规矩票用掉（下次再写还得重新领；09-18 老大定，无窗口）
    try:
        gate_consume_skill_ticket(a.source)
    except Exception:
        pass
    res = {"ok": True, "id": str(row[0]), "inserted": bool(row[1]),
           "cred_masked": _cred_hits}
    if similar:
        res["similar"] = similar
        if similar[0]["score"] >= 0.85:
            res["tip"] = ("注意：库里已有相似度 %.2f 的条目（%s）。若属同主题，请改用 "
                          'mh_update(id="%s", content="<合并后的最新版>")，别再堆第二条。'
                          % (similar[0]["score"], similar[0]["id"], similar[0]["id"]))
    out(res)


def cmd_skill(a):
    """领 openmem 写入规矩（独立工具；照 win-desktop-helper 的 get_skill 模式）。

    2026-09-18 老大拍板重做。规则：
    · 只给**核心规矩**（RULES.md，一页准入条文）—— 没有"完整手册"这回事（MANUAL.md 已删）。
    · **必须带 agent**：领到即发票，这是写入闸门第 1 关唯一的放行依据。
    · 票制（2026-09-18 老大定，无窗口无豁免）：**领一次 = 一张票，写成功一条消费掉一张。**
      openmem 是无状态 HTTP（没有 session 概念），做不出「每会话领一次」，
      就取最严的近似 —— **每次写入前都得先领**。
    """
    ag = (getattr(a, "agent", "") or "").strip()
    txt = gate_skill_text(getattr(a, "detail", ""), getattr(a, "topic", ""))
    counted = False
    try:
        counted = gate_mark_skill(ag)
    except Exception:
        pass
    res = {"ok": True, "skill": txt, "chars": len(txt),
           "counted_for": ag if counted else None,
           "next": "票已发（管一条）。直接再调一次 mh_write 即放行；"
                   "写成功一条这张票就用掉了，下次再写还得重新领。"}
    if not counted:
        res["warn"] = ('没带 agent（或名字无效）→ 本次领规矩**不计入**任何人名下，'
                       '第 1 关仍会拦你。请带 agent="<你的 agent 名>" 再调一次。')
    out(res)


def cmd_update(a):
    """按 id 更新一条记忆（2026-09-14 新增）。
    只改传了的字段；content 变了就重算 embedding + content_hash。
    说明：openmem 此前的规矩是"写错了直接删了重写"，但删+写会换 id、
    打断 superseded_by / memory_conflicts 的引用链。mh_update 保留 id，
    是干净的原地更新。
    """
    a.content, _cred_hits = (sanitize_credentials(a.content) if a.content else (None, 0))
    conn = connect(); cur = conn.cursor()
    rid, err = resolve_entry_id(cur, a.id)
    if err:
        cur.close(); conn.close()
        out({"ok": False, "error": err})
        return
    a.id = rid

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
    # —— 检索留痕（2026-09-15）：只记查询与命中 id，供治理用；失败绝不影响检索 ----------
    try:
        conn2 = connect(); cur2 = conn2.cursor()
        cur2.execute("""CREATE TABLE IF NOT EXISTS search_log (
            id BIGSERIAL PRIMARY KEY,
            requester TEXT DEFAULT '',
            query TEXT NOT NULL,
            top_k INT,
            hits JSONB,
            created_at TIMESTAMPTZ DEFAULT now())""")
        cur2.execute("INSERT INTO search_log (requester, query, top_k, hits) VALUES (%s,%s,%s,%s)",
                     (getattr(a, 'requester', '') or '', a.query[:2000], a.top_k,
                      json.dumps([e['id'] for e in scored[:10]])))
        conn2.commit(); cur2.close(); conn2.close()
    except Exception:
        pass
    # —— 闸门留痕（2026-09-18）：给「写前查重」闸门当放行依据；失败绝不影响检索 ----------
    try:
        gate_mark_search(getattr(a, "requester", "") or "")
    except Exception:
        pass
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
    rid, err = resolve_entry_id(cur, a.id)
    if err:
        cur.close(); conn.close()
        out({"ok": False, "error": err}); return
    cur.execute("""SELECT id, layer, category, source, content, tags, pinned, confidence,
                          created_at, updated_at, superseded_by
                   FROM memory_entries WHERE id=%s""", (rid,))
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
    agent = (getattr(a, "agent", "") or "").strip()

    # ── 领规矩（真源 RULES.md，本地文件直读：不走 AI 生成、不动库）────────────
    # 2026-09-18 老大定：闸门要的是「领规矩」这个动作 —— 规矩是精简准入条文。
    # （MANUAL.md 那份 11491 字符的胖手册**已删**：能塞进技能文件的塞进去，塞不进的一律不留。）
    if GATE_SKILL_TOOL in (a.name or ""):
        ok = False
        try:
            ok = gate_mark_handbook(a.name, agent)
        except Exception:
            pass
        res = {"ok": True, "name": GATE_SKILL_TOOL, "answer": gate_rules_text(),
               "cached": True, "from_file": os.path.basename(GATE_SKILL_FILE),
               "counted_for": agent if ok else None}
        if not ok:
            res["warn"] = ('未提供 agent，本次**不计入**你的领规矩记录 —— 带上 '
                           'agent="<你的 agent 名>" 再调一次，否则第 1 关会继续拦你。')
        out(res)
        return

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

def cmd_gate(a):
    """写入闸门状态查看 / 重置（状态在 PG：表 gate_state / gate_meta）"""
    if not _gate_init():
        out({"ok": False, "error": "闸门状态表不可达（PG 抽风）—— 此时闸门全程 fail-open（放行）"})
        return
    if getattr(a, "reset_all", False):
        _gate_sql("DELETE FROM gate_state")
        _gate_sql("DELETE FROM gate_meta")
        out({"ok": True, "reset": "all", "note": "闸门状态已清空（所有人重新算）"})
        return
    src = getattr(a, "reset", None)
    if src:
        rq = _gate_norm(src)
        _gate_sql("DELETE FROM gate_state WHERE agent=%s", (rq,))
        out({"ok": True, "reset": rq, "note": "该 agent 的留痕已清（下次写入会重新过闸）"})
        return
    now = time.time()

    def _ago(ts):
        if not ts:
            return None
        d = now - ts
        return {"at": datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
                "ago_min": int(d // 60)}

    rows = _gate_sql("""SELECT agent, rules_ready, rules_taught_at, rules_used_at, searched_at,
                               skill_denied, search_denied, dup_denied,
                               coalesce(jsonb_array_length(to_jsonb(dup_seen)),0)
                        FROM gate_state ORDER BY agent""", fetch="all")
    if rows is _GATE_FAIL or rows is None:
        rows = []
    agents = {}
    for r in rows:
        agents[r[0]] = {
            "rules_ready": bool(r[1]),                       # ①关：手上有没有票（有票才放行）
            "rules_taught_at": _ago(r[2]),                   # 领票时刻（留痕）
            "rules_used_at": _ago(r[3]),                     # 上次把票用掉的时刻
            "searched_at": _ago(r[4]),                       # ②关放行依据（你自己搜过库的时刻）
            "denied": {"rules": r[5], "search": r[6], "dup": r[7]},
            "dup_seen_n": r[8],                              # ③关安全阀已放行过的内容数
        }
    mrows = _gate_sql("SELECT key, val FROM gate_meta", fetch="all")
    meta = {} if (mrows is _GATE_FAIL or mrows is None) else {r[0]: r[1] for r in mrows}
    out({"ok": True,
         "now": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
         "_note": "last_* 只是全库统计，不参与任何闸门判定（判定一律看 agents 里各自的时刻）",
         "stats_only": {"last_skill": _ago(meta.get("last_skill_at")),
                        "last_search_any": _ago(meta.get("last_search_at"))},
         "params": {"gate": "①票制（领一次管一条）+ ②无豁免（超时即拦）+ ③重复关（带防死锁安全阀）",
                    "search_min": GATE_SEARCH_MIN, "dup_hi": GATE_DUP_HI},
         "agents": agents,
         "state_store": "PG 表 gate_state / gate_meta（2026-09-18 从 _gate_state.json 迁入）"})


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
    s.add_argument("--requester", default="", help="调用方 agent 名（留痕用，可空）")
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
    t.add_argument("--agent", default="", help="领规矩留痕用：你自己的 agent 名")
    t.add_argument("--force_refresh", action="store_true")
    t.set_defaults(func=cmd_tool_call)

    sk = sub.add_parser("skill")   # 独立工具 mh_skill（2026-09-18 老大拍板重做）
    sk.add_argument("--agent", default="", help="你自己的 agent 名 —— 写入闸门第 1 关靠它放行")
    sk.add_argument("--detail", default="", help='full = 附完整手册；默认只给核心规矩')
    sk.add_argument("--topic", default="", help="按章节标题抽段，如 闸门 / 工具 / Web API")
    sk.set_defaults(func=cmd_skill)

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

    gt = sub.add_parser("gate", help="写入闸门状态 / 重置（2026-09-18）")
    gt.add_argument("--status", action="store_true", help="看闸门状态（默认行为）")
    gt.add_argument("--reset", metavar="SOURCE", help="清某个 agent 的闸门留痕（票 / 搜索时刻 / 拦截计数）")
    gt.add_argument("--reset_all", action="store_true", help="清空全部闸门状态")
    gt.set_defaults(func=cmd_gate)

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
