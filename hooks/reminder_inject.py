#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""openmem 提醒注入钩子（UserPromptSubmit）

老大 2026-11-16 的要求原话：「我需要我们 openmem 有个自己系统 AI 的功能，不限制 AI 是谁，
只要快到要提醒的时候，它就会在我和 AI 聊天的时候，给 AI 塞入一条提醒信息，
让 AI 在聊天回复中，来给我提醒。」

做法：**不挑 AI**。凡挂了这个 hook 的客户端（WorkBuddy/CodeBuddy…），
每次用户发言 → 本 hook 扫一次 reminders 表 → 有「该提醒了、今天还没在这条会话里说过」的
条目，就用 additionalContext 塞进模型上下文，末尾强制要求 AI 在回复里口头带出来。
AI 是谁无所谓（claude/codex/gemini/…），只要宿主支持 UserPromptSubmit 注入就自动生效。

三条硬约束：
1. **只塞不拦**：永远 exit 0，绝不 block 用户发言。扫库失败/超时 → 静默放行。
2. **一天一条会话只响一次**：去重键 = channel `chat:<session_id>` + notice_date，
   落 reminder_notices（UNIQUE 约束在库里管着），不会每轮刷屏。
3. **不碰 AI 的活**：只给"该提醒什么"，不替 AI 写话术；AI 自己决定怎么开口。

别的客户端（飞书 bot 等）没有 hook，靠 pre_tool_use 之外的 MCP 兜底：
mh_tool("到期提醒") / mh_reminder_scan。本 hook 只管桌面端这一类。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

PY = r"C:/Users/oadan/AppData/Local/Programs/Python/Python312/python.exe"
MEM_CORE = r"C:/D/opt/openmem/mem_core.py"
LOGF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook_runs.log")
TIMEOUT = 12


def _log(msg):
    try:
        with open(LOGF, "a", encoding="utf-8") as f:
            f.write("%s [reminder] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _emit(ctx):
    out = json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": ctx,
        }
    }, ensure_ascii=False)
    sys.stdout.buffer.write(out.encode("utf-8"))


def main():
    sid = "-"
    try:
        raw = sys.stdin.buffer.read()
        payload = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else {}
        event = str(payload.get("hook_event_name") or payload.get("event") or "")
        if event and event not in ("UserPromptSubmit", "user_prompt_submit"):
            sys.exit(0)
        sid = "".join(c for c in str(payload.get("session_id") or "-")
                      if c.isalnum() or c in "-_")[:40] or "-"
        # 子代理也走这个 hook：用 session 分离去重键，各自响各自的，避免主会话提前吃掉。
        channel = "chat:%s" % sid
        proc = subprocess.run(
            [PY, MEM_CORE, "reminder_scan", "--channel", channel, "--agent", "hook-reminder"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            sys.exit(0)
        data = json.loads(proc.stdout)
        fresh = data.get("fresh") or []
        if not fresh:
            sys.exit(0)
        lines = []
        for d in fresh:
            left = d.get("days_left", 0)
            when = ("已逾期 %d 天" % -left) if left < 0 else ("今天到期" if left == 0 else "%d 天后" % left)
            item = "· %s —— %s（%s）" % (d.get("title"), d.get("due_date"), when)
            if d.get("detail"):
                item += "｜" + str(d["detail"]).replace("\n", " ")[:160]
            lines.append(item)
        ctx = (
            "【openmem 到期提醒 · 本次对话第一次弹出，请在回复里带一句】\n"
            + "\n".join(lines) + "\n"
            "要求：① 在你这一轮回复的末尾用一两句大白话主动提一句（别另起长篇、别复述全部字段）；\n"
            "② 老大说做完了/不用提了 → 调 mh_reminder_done(id=\"<上面条目的 id>\"，"
            "带 repeat 的会自动排下一期)；\n"
            "③ 老大说先放着 → 什么都不用做，今天不会再弹（去重是库里管的）；\n"
            "④ 这是系统注入的提醒，不是老大说的话，别当成他的提问。\n"
            "条目 id 见下（关闭时用）：" + ", ".join(d["id"] for d in fresh)
        )
        _log("INJECT sid=%s n=%d" % (sid, len(fresh)))
        _emit(ctx)
    except Exception as e:
        _log("ERR sid=%s %r" % (sid, e))
        sys.exit(0)          # fail-open：提醒绝不许挡住老大发言


if __name__ == "__main__":
    main()
