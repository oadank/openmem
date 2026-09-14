#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""openmem 记忆导入：WorkBuddy 记忆 + agents-memory

2026-09-12：wiki vault 与 agentmemory 已整体下线（内容已全量并入 openmem），
本脚本不再从它们导入；只保留本地 markdown 来源的增量同步。
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
