#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""openmem 记忆导入：WorkBuddy 记忆 + agents-memory

🔴 2026-09-18 起**默认停用**（老大拍板，见 main() 开头的说明）：
   openmem 已是唯一记忆真源，本地 md 文件只留规矩骨架 + 路标。
   把整份文件倒进库 = 在库里堆「文件影子」，必然与文件重复、随文件漂移。
   各 agent 的结论一律用 mh_write 手写进库。确需回灌历史文件才加 --legacy。

2026-09-12：wiki vault 与 agentmemory 已整体下线（内容已全量并入 openmem）。
"""
import sys, os, json, hashlib
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

OUT = Path(r"C:\D\opt\openmem")
WB_USER = Path(r"C:\Users\oadan\.workbuddy")
WB_WS = Path(r"C:\D\opt\.workbuddy\memory")
AGENTS_MEM = Path(r"C:\D\opt\agents-memory")

MAX_BODY = 2500

def clean(t):
    return t.replace("\x00", "") if t else ""

def parse_md(path: Path):
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        content = path.read_text(encoding="gbk", errors="ignore")
    title, summary, body = "", "", content
    if content.startswith("---\n"):
        parts = content.split("---\n", 2)
        if len(parts) >= 3:
            for line in parts[1].split("\n"):
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip("\"'")
                elif line.startswith("summary:"):
                    summary = line.split(":", 1)[1].strip().strip("\"'")
            body = parts[2]
    if not title:
        for line in content.split("\n"):
            if line.startswith("# "):
                title = line[2:].strip(); break
    if not title:
        title = path.stem
    return clean(title), clean(summary), clean(body)

def md_items(root: Path, source, layer, cat_fn, skip_dirs=None, skip_files=()):
    items = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        if any(p in skip_dirs for p in rel.parts[:-1]) if skip_dirs else False:
            continue
        if path.name in skip_files or path.name.startswith("."):
            continue
        title, summary, body = parse_md(path)
        if len(body.strip()) < 20:
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
        items.append({
            "content": f"《{title}》 {('｜' + summary) if summary else ''}\n{body[:MAX_BODY]}",
            "source": source, "layer": layer, "category": cat_fn(rel),
            "tags": [], "confidence": 0.6, "created_at": mtime,
        })
    return items

def main():
    # ── 🔴 2026-09-18 起默认停用「整篇倒文件」（老大拍板）───────────────
    # 原因：openmem 已是唯一记忆真源，本地 md 只留规矩骨架 + 路标。
    # 整篇倒文件 = 在库里堆「文件影子」，必然与文件重复、随文件漂移，
    # 且每次同步都把这些脏条目再灌一遍（删了还会回来）。
    # 2026-09-18 已清理 23 条此类影子条目（source=workbuddy, category=archive）。
    # 各 agent 的结论一律用 mh_write 手写进库；确需回灌历史文件才显式加 --legacy。
    if "--legacy" not in sys.argv:
        print("已停用文件同步（默认）。openmem 是唯一真源，结论请用 mh_write 写库。")
        print("确需回灌历史文件：python import_sources.py --legacy")
        return

    # 1. WorkBuddy 记忆（用户级 + 工作区日志）→ m 层
    wb_items = []
    for f in sorted(WB_USER.glob("*.md")):
        title, summary, body = parse_md(f)
        if len(body.strip()) < 20:
            continue
        wb_items.append({"content": f"《{title}》\n{body[:MAX_BODY]}", "source": "workbuddy",
                         "layer": "m", "category": "archive",
                         "tags": ["workbuddy"], "confidence": 0.7,
                         "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
    if WB_WS.exists():
        for f in sorted(WB_WS.glob("*.md")):
            title, summary, body = parse_md(f)
            if len(body.strip()) < 20:
                continue
            wb_items.append({"content": f"《{title}》\n{body[:MAX_BODY]}", "source": "workbuddy",
                             "layer": "m", "category": "archive",
                             "tags": ["workbuddy", "日志"], "confidence": 0.7,
                             "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
    (OUT / "_import_workbuddy.json").write_text(json.dumps(wb_items, ensure_ascii=False), encoding="utf-8")
    print(f"workbuddy: {len(wb_items)} 条")

    # 2. agents-memory（各 agent 身份/记忆文件）→ m 层
    am_items = []
    for agent_dir in AGENTS_MEM.iterdir():
        if not agent_dir.is_dir():
            continue
        for f in sorted(agent_dir.rglob("*.md")):
            if f.name.startswith("."):
                continue
            title, summary, body = parse_md(f)
            if len(body.strip()) < 20:
                continue
            am_items.append({"content": f"《{title}》\n{body[:MAX_BODY]}", "source": f"agents-memory-{agent_dir.name}",
                             "layer": "m", "category": "archive",
                             "tags": ["agent记忆", agent_dir.name], "confidence": 0.5,
                             "created_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat()})
    (OUT / "_import_agentsmem.json").write_text(json.dumps(am_items, ensure_ascii=False), encoding="utf-8")
    print(f"agents-memory: {len(am_items)} 条")

if __name__ == "__main__":
    main()
