#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""记忆闸门（2026-11-15 老大令：「每次必搜记忆」做闸门，必须锁死）

规矩：**本轮没查过 openmem，任何动手类工具一律 deny**。
- 记：调 openmem 任意 mh_* 工具 → 落一枚章（%TEMP%/mem_searched.<sid>.stamp）
- 清：UserPromptSubmit（新一轮）→ 删章，逼着重搜
- 放行：只读类（Read/Glob/Grep/ToolSearch/WebSearch/…）不拦，否则连查记忆都进不来
- 崩了自己也 deny？不。闸门自身异常 → 放行并在 stderr 说明，避免把自己锁死。
  但要留日志 hook_runs.log，出问题能查。
"""
from __future__ import annotations

import json
import os
import sys
import time

TMP = r"C:/Users/oadan/AppData/Local/Temp"
LOGF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook_runs.log")

# 只读 / 无害类：不拦
ALLOW_TOOLS = {
    "Read", "read_file", "Glob", "Grep", "ToolSearch", "WebSearch", "WebFetch",
    "Skill", "skill", "AskUserQuestion", "TaskCreate", "TaskList", "TaskGet",
    "TaskUpdate", "TaskOutput", "SendMessage", "present_files", "show_widget",
    "widget_guidelines", "automation_update", "TodoWrite", "ExitPlanMode",
}

# 记章用的 DeferExecuteTool 包装名
DEfer = "DeferExecuteTool"


def _sid(payload: dict) -> str:
    raw = str(payload.get("session_id") or payload.get("sessionId") or "-")
    return "".join(c for c in raw if c.isalnum() or c in "-_")[:64] or "-"


def _stamp(sid: str) -> str:
    return os.path.join(TMP, "mem_searched.%s.stamp" % sid)


def _table_stamp(sid: str) -> str:
    """本轮「技能库总表」查没查过。跟主章同轮清，一轮最多一次（2026-11-15 老大令）。"""
    return os.path.join(TMP, "mem_table.%s.stamp" % sid)


def _log(msg: str) -> None:
    try:
        with open(LOGF, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _deny(msg: str) -> None:
    out = {
        "decision": "block",
        "reason": msg,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": msg,
        },
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stderr.write(msg + "\n")
    sys.exit(2)


def main() -> None:
    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    event = str(payload.get("hook_event_name") or payload.get("event") or "")
    sid = _sid(payload)
    stamp = _stamp(sid)

    # 新一轮：清章
    if event == "UserPromptSubmit" or event == "user_prompt_submit":
        for f in (stamp, _table_stamp(sid)):
            try:
                os.remove(f)
            except OSError:
                pass
        sys.exit(0)

    tool = str(payload.get("tool_name") or payload.get("tool") or "")
    inp = payload.get("tool_input") or payload.get("input") or {}
    if not isinstance(inp, dict):
        inp = {}

    # 调 openmem = 已查记忆，记章
    if tool.startswith("mcp__openmem__"):
        try:
            with open(stamp, "w", encoding="utf-8") as f:
                f.write(tool)
        except Exception:
            pass
        sys.exit(0)
    if tool == DEfer:
        inner = str(inp.get("toolName") or inp.get("tool_name") or inp.get("name") or "")
        if inner.startswith("mcp__openmem__"):
            # 2026-11-15 老大令：总表一轮只查一次（每次 ~5k 字），本轮查过就直接拒，别再烧。
            if inner.endswith("mh_tool"):
                args = inp.get("params") or {}
                nm = str((args or {}).get("name") or "")
                if "总表" in nm:
                    ts = _table_stamp(sid)
                    if os.path.isfile(ts):
                        _log("DENY repeat-table (%s)" % nm)
                        _deny(
                            "【总表本轮已查过，不再重复拉】老大 2026-11-15 定：技能库总表一轮只查一次"
                            "（一次约 5k 字）。要用的是具体条目就直接 `mh_search(query=..., top_k=3)`，"
                            "要技能正文就 Read 真源目录里的 SKILL.md —— 别再把整张表拉一遍。"
                        )
                    try:
                        with open(ts, "w", encoding="utf-8") as f:
                            f.write(nm)
                    except Exception:
                        pass
            try:
                with open(stamp, "w", encoding="utf-8") as f:
                    f.write(inner)
            except Exception:
                pass
            sys.exit(0)

    if tool in ALLOW_TOOLS:
        sys.exit(0)

    if os.path.isfile(stamp):
        sys.exit(0)

    _log("DENY %s (no memory search this turn)" % tool)
    _deny(
        "【记忆闸门·本轮还没查过 openmem，动手被拒】\n"
        "老大 2026-11-15 定的规矩：每次动手前必须先查记忆，不许凭印象干。\n"
        "先做一次（任一即可）：\n"
        "  · ToolSearch([\"mcp__openmem__mh_search\"]) → DeferExecuteTool(mh_search, {query, top_k})\n"
        "  · 或 mh_tool(\"技能库总表\") / mh_tool(\"本机环境事实速查\")\n"
        "查完再回来调这个工具，本轮就放行。"
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # 闸门自身崩了不许锁死人
        sys.stderr.write("memory_gate error: %s\n" % e)
        sys.exit(0)
