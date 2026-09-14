#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent-matrix（36 服务总表）入库 openmem + 自动同步
SERVICES.md 按服务分块 + lecoo.md 机器档案(pinned) + README 总览 + capabilities.json 按 agent 分块
sync(force=False)：4 个源文件指纹（md5）没变就跳过；变了 → 删旧 source='agent-matrix' 全量重导。
web_server.py 每 5 分钟后台调一次 sync(False) 实现「及时更新」。"""
import sys, os, json, re, hashlib, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
import mem_core as mc

BASE = r"C:\D\opt\agent-matrix"
SRC_FILES = ["lecoo.md", "README.md", "SERVICES.md", "capabilities.json"]


def build_items():
    items = []
    # 1. lecoo.md 机器档案 → pinned
    txt = open(os.path.join(BASE, "lecoo.md"), encoding="utf-8").read()
    items.append({"content": f"【agent-matrix·机器档案 lecoo】\n{txt}",
                  "source": "agent-matrix", "layer": "m", "category": "projects",
                  "tags": ["agent-matrix", "lecoo", "机器档案"], "pinned": True})
    # 2. README 总览
    txt = open(os.path.join(BASE, "README.md"), encoding="utf-8").read()
    items.append({"content": f"【agent-matrix·项目总览】本机 36 个 nssm 服务总表项目。可视化总览见 C:\\D\\opt\\agent-matrix\\index.html（可搜索+两服务diff+能力矩阵+健康实测）。\n{txt}",
                  "source": "agent-matrix", "layer": "k", "category": "knowledge",
                  "tags": ["agent-matrix", "服务总表"]})
    # 3. SERVICES.md：铁律+总览+网关模型表+运维备注 整段入；服务明细按 ### 分块
    svc = open(os.path.join(BASE, "SERVICES.md"), encoding="utf-8").read()
    sections = re.split(r"(?m)^## ", svc)
    for sec in sections:
        if not sec.strip():
            continue
        title = sec.split("\n", 1)[0].strip()
        body = sec.split("\n", 1)[1] if "\n" in sec else ""
        if title.startswith("2."):  # 纵向明细：再按服务切
            for sub in re.split(r"(?m)^### ", body):
                if not sub.strip():
                    continue
                name = sub.split("\n", 1)[0].strip()
                content = sub.split("\n", 1)[1] if "\n" in sub else ""
                if len(content.strip()) < 30:
                    continue
                items.append({"content": f"【agent-matrix·服务明细 {name}】\n{content.strip()[:6000]}",
                              "source": "agent-matrix", "layer": "k", "category": "services",
                              "tags": ["agent-matrix", "服务", name]})
        else:
            if len(body.strip()) < 30:
                continue
            items.append({"content": f"【agent-matrix·{title}】\n{body.strip()[:8000]}",
                          "source": "agent-matrix", "layer": "m", "category": "rules" if "铁律" in title else "projects",
                          "tags": ["agent-matrix", title[:30]]})
    # 4. capabilities.json：feishu bots / cli / known_issues
    cap = json.load(open(os.path.join(BASE, "capabilities.json"), encoding="utf-8"))
    items.append({"content": f"【agent-matrix·飞书 bot 能力总表】speech_enabled={cap['feishu'].get('speech_enabled')} tts_default_engine={cap['feishu'].get('tts_default_engine')} vision_enabled={cap['feishu'].get('vision_enabled')}\n" +
                  json.dumps(cap["feishu"].get("bots", {}), ensure_ascii=False)[:5000],
                  "source": "agent-matrix", "layer": "k", "category": "capabilities",
                  "tags": ["agent-matrix", "飞书bot", "能力矩阵"]})
    for name, info in cap["cli"].items():
        items.append({"content": f"【agent-matrix·CLI 身份 {name}】\n" + json.dumps(info, ensure_ascii=False)[:6000],
                      "source": "agent-matrix", "layer": "k", "category": "capabilities",
                      "tags": ["agent-matrix", "CLI", name]})
    if cap.get("known_issues"):
        items.append({"content": "【agent-matrix·已知问题】\n" + json.dumps(cap["known_issues"], ensure_ascii=False)[:6000],
                      "source": "agent-matrix", "layer": "m", "category": "lessons",
                      "tags": ["agent-matrix", "已知问题"]})
    return items


def files_fingerprint():
    """4 个源文件内容 md5（文件缺失抛异常，让调用方感知）"""
    h = hashlib.md5()
    for f in SRC_FILES:
        with open(os.path.join(BASE, f), "rb") as fp:
            h.update(f"<{f}>\n".encode())
            h.update(fp.read())
    return h.hexdigest()


def _stored_hash():
    try:
        conn = mc.connect(); cur = conn.cursor()
        cur.execute("SELECT value FROM app_config WHERE key='agentmatrix_hash'")
        r = cur.fetchone(); cur.close(); conn.close()
        return r[0] if r else None
    except Exception:
        return None


def _store_hash(fp):
    from psycopg2.extras import Json
    conn = mc.connect(); cur = conn.cursor()
    cur.execute("""INSERT INTO app_config (key, value) VALUES ('agentmatrix_hash', %s)
                   ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""", (Json(fp),))
    conn.commit(); cur.close(); conn.close()


def entry_count():
    conn = mc.connect(); cur = conn.cursor()
    cur.execute("SELECT count(*) FROM memory_entries WHERE source='agent-matrix'")
    n = cur.fetchone()[0]; cur.close(); conn.close()
    return n


def sync(force=False):
    """指纹变了（或 force）→ 删旧重导；没变 → 跳过。返回 dict 供 API/CLI 用"""
    fp = files_fingerprint()
    if not force and _stored_hash() == fp:
        return {"ok": True, "changed": False, "count": entry_count(), "fingerprint": fp}
    # 先解除对本组条目的引用（superseded_by 自引用 FK + 冲突表），再删旧
    conn = mc.connect(); cur = conn.cursor()
    cur.execute("""UPDATE memory_entries SET superseded_by=NULL
                   WHERE superseded_by IN (SELECT id FROM memory_entries WHERE source='agent-matrix')""")
    cur.execute("""DELETE FROM memory_conflicts
                   WHERE a_id IN (SELECT id FROM memory_entries WHERE source='agent-matrix')
                      OR b_id IN (SELECT id FROM memory_entries WHERE source='agent-matrix')""")
    cur.execute("DELETE FROM memory_entries WHERE source='agent-matrix'")
    conn.commit(); cur.close(); conn.close()
    # 重导（批量 embedding，走内核 import_batch）
    items = build_items()
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_import_agentmatrix.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mc.cmd_import_batch(argparse.Namespace(file=tmp))
    os.remove(tmp)
    _store_hash(fp)
    res = json.loads(buf.getvalue())
    return {"ok": True, "changed": True, "fingerprint": fp,
            "total": res.get("total"), "inserted": res.get("inserted"),
            "duplicate": res.get("duplicate"), "failed": res.get("failed"),
            "count": entry_count()}


if __name__ == "__main__":
    print(json.dumps(sync(force="--force" in sys.argv or len(sys.argv) == 1), ensure_ascii=False))
