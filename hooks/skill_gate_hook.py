#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
openmem 技能闸门 hook（UserPromptSubmit）
挂在 WorkBuddy / CodeBuddy 的 settings.json hooks.UserPromptSubmit 上。

设计原则（2026-10-30 老大拍板）：
- 每轮都进模型上下文 → 必须抠字。总纲只留「可执行动作 + 一句原因」。
- 技能卡只保留「真相关」的：RRF 分数 >= MIN_SCORE。纯向量单路 top1 恒为
  1/(20+1)=0.04762，零印证=噪音；>=0.05 表示至少还有符号/关键词路二次印证。
  没有真相关就只注入总纲、不塞噪音卡。
- 不记状态、不拼上轮 query：AI 有完整上下文，省略句它自己接得上。
- fail-open：任何异常都绝不拦截用户发言，超时 20s。
- 运行流水写 hook_runs.log。
"""
import sys, json, subprocess, datetime

PY = r"C:/Users/oadan/AppData/Local/Programs/Python/Python312/python.exe"
MEM_CORE = r"C:/D/opt/openmem/mem_core.py"
RUNLOG = r"C:/D/opt/openmem/hooks/hook_runs.log"

# 真相关阈值（2026-10-30 二次修正）
# 旧判据：RRF 分数 >= 0.05。对纯中文 query 结构性失效 —— 中文抽不出符号词元，
# 符号路整路空，RRF 只剩向量单路，top1 恒为 1/(20+1)=0.04762，永远差一点点。
# 新判据：分数 >= 0.05（符号路有二次印证）**或** zh_hit >= 3（中文 2-gram 字面重合数）。
# 实测区分度：相关技能 zh_hit=4~5，无关技能 zh_hit=0~1。
MIN_SCORE = 0.05
MIN_ZH_HIT = 3

# ==== 极简总纲：只留动作，长度恒定 ====
GUIDE = (
    "【openmem 技能闸门·必读】\n"
    "⓪ openmem 工具都是 MCP 延迟工具：先 ToolSearch 取全名 mcp__openmem__mh_xxx，"
    "再 DeferExecuteTool；直接写短名会报 not available —— 那是名字错，不是没挂载\n"
    "① 命中技能 → mh_get(id) 取全文（本地有 Skill(名) 就先 Skill 加载），再动手\n"
    "② 生图/视频 → 先 mh_pr_search 取词（人像 img_char_sfw / 视频 vid_h3），禁手编\n"
    "③ 查答案/环境/端口 → 先 mh_tool，别搜别猜\n"
    "④ 写记忆 → 先 mh_skill 领票；提示词只进 mh_pr_*\n"
    "⑤ 外部API/GitHub → 先 mh_search 查通道，403 别空手重试"
)

CARD_TITLE = "相关技能："


def _log(msg):
    try:
        with open(RUNLOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}\n")
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


def _card_line(i, e):
    c = (e.get("content") or "")
    name = when = idv = ""
    for ln in c.split("\n"):
        ln = ln.strip()
        if ln.startswith("【技能】"):
            name = ln[4:].strip()
        elif ln.startswith("【何时用】"):
            when = ln[5:].strip()
        elif ln.startswith("【详情id】"):
            idv = ln[6:].strip()
    if not name:
        name = (c.split("\n") or [""])[0].strip()
    w = (when[:50] + "…") if len(when) > 50 else when
    idpart = f" (id {idv})" if idv else ""
    tail = f" — {w}" if w else ""
    return f"{i}. [{e.get('score', 0)}] {name}{idpart}{tail}"


def main():
    sid = "-"
    try:
        raw_bytes = sys.stdin.buffer.read()
        raw = raw_bytes.decode("utf-8", "replace") if raw_bytes else ""
        payload = json.loads(raw) if raw.strip() else {}
        prompt = (payload.get("prompt") or "").strip()
        sid = payload.get("session_id") or "-"
        if not prompt:
            return
        _log(f"RUN sid={sid} prompt={prompt[:100]!r}")
        q = prompt[:400]
        proc = subprocess.run(
            [PY, MEM_CORE, "search", "--query", q,
             "--category", "uskill_idx", "--top_k", "3",
             "--requester", "hook-skill-gate"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=20,
        )
        raw_results = []
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                raw_results = (json.loads(proc.stdout).get("results") or [])
            except Exception:
                raw_results = []
        rel = [e for e in raw_results
               if float(e.get("score", 0)) >= MIN_SCORE or int(e.get("zh_hit", 0)) >= MIN_ZH_HIT]
        if not rel:
            _log(f"  NONE sid={sid} raw={len(raw_results)} 无相关（只注入总纲）")
            _emit(GUIDE)
            return
        # 字面重合比向量分数更能说明相关性：zh_hit 高的排前面（stable sort，同分保持原序）
        rel.sort(key=lambda e: -int(e.get("zh_hit", 0)))
        _log(f"  HIT sid={sid} raw={len(raw_results)} rel={len(rel)} top={(rel[0].get('content') or '').split(chr(10))[0].strip()}")
        lines = [GUIDE, CARD_TITLE]
        for i, e in enumerate(rel, 1):
            lines.append(_card_line(i, e))
        _emit("\n".join(lines))
    except Exception as ex:
        _log(f"ERR sid={sid} {ex!r}")


if __name__ == "__main__":
    main()
