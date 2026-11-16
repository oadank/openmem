#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PreToolUse 闸门：写文件前必须先看到一句话，跟「写记忆前必须查重」一样是必经流程。
2026-11-12 老大原话：
  「直接做个闸门吧，每次你乱写文件，就要看到这句话吧」
  「你写 md 等文件前，必须看到一句话，那就是你乱写，你就是死，写了我不会骂你的才可以写」
挂在 ~/.workbuddy/settings.json 的 PreToolUse(matcher="*")。

设计：
- 任何 Write/Edit 落盘 -> 都把老大那句话 + 「写了会不会挨骂」必答项甩进上下文(ack)
- 判「写了肯定挨骂」(项目目录里新建的过程稿/报告/清单/日志/备份/一次性脚本) -> 硬拦 deny
- Bash 往项目里写 -> 同样先看到话；命中垃圾词 -> 硬拦
- 允许区(记忆/技能真源/备份/临时目录)不硬拦，但仍要看到话
- 其余工具静默；任何异常 fail-open，绝不拦老大发言
"""
import sys
import os
import json
import re
import fnmatch
import glob

# 正式产出的唯一真源：漫剧技能六道关口的「各关产出文件」表。
# 闸门认「技能点名的产物」，不认「文件名像不像报告」——名字里有报告/清单
# 不等于垃圾，是不是垃圾只看它在不在过关凭证清单里。
# [2026-11-15] 技能真源在 C:/D/opt/skills/；只写 agents-to-feishu 那条路径会让
# _official_patterns() 读不到六关产出表 → fail-open 回退旧判定 → 连「审查报告-第N集.md」
# （关口 4 的正式凭证）都被当垃圾拦住。两处都认。
_GATE_CARD_CANDIDATES = (
    "C:/D/opt/skills/toonflow-drama/references/gate-cards.md",
    "C:/D/opt/agents-to-feishu/skills/toonflow-drama/references/gate-cards.md",
)
GATE_CARDS = next((x for x in _GATE_CARD_CANDIDATES if os.path.isfile(x)), _GATE_CARD_CANDIDATES[0])
# 项目根已有的正式产物（2026-11-12 清理后留下的白名单），不带季后缀
OFFICIAL_EXTRA = (
    "分镜表.md", "节拍清单.md", "角色卡.md", "风格锁.md", "video_nodes.json",
    "build_prompts.py", "prompts_act1.py", "prompts_act2.py", "prompts_act3.py",
    # 2026-11-12 老大点名：每部剧要有自己的错误台账（通用表在技能里），属正式产物
    "错误台账.md",
    # 2026-11-14 堆7：流程台账是正式产物，不是过程稿
    "进度台账.json",
)

def _is_canvas_json(path: str) -> bool:
    """这个 json 是不是 Toonflow 画布本体（顶层 toonflowCanvas + nodes 数组）。

    [2026-11-15] 优先用技能里的唯一定位器；技能目录被挪走时回退到轻量内联判定，
    绝不让 hook 因为这行崩掉（崩了会拦死所有写操作）。
    """
    try:
        sys.path.insert(0, "C:/D/opt/skills/toonflow-drama/scripts")
        from canvas_file import is_canvas_json
        return is_canvas_json(path)
    except Exception:
        pass
    try:
        if os.path.getsize(path) > 80 * 1024 * 1024:
            return False
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return isinstance(d, dict) and "toonflowCanvas" in d and isinstance(d.get("nodes"), list)
    except Exception:
        return False


BOSS_LINE = (
    "🔴🔴【写文件前必看 · 跟写记忆前必须查重一样，这是写文件的票】\n"
    "老大原话：「你乱写，你就是死。写了我不会骂你的才可以写。」"
    "（人设写了 N 次，2026-11-12 第 4 次犯，信任为 0）\n"
    "目标：{target}\n"
    "先答一句：这个文件写下去，老大会不会骂？\n"
    "· 会骂 —— 过程稿 / 审查报告 / 自查清单 / 日志 / 备份 / 截图 / 一次性脚本 / 为交差造的文档："
    "不许写，改 %TEMP% 或 C:/D/opt/_backup/，或者直接在对话里说。\n"
    "· 不会骂 —— 技能点名的过关凭证 / 老大点名的交付物、画布 / 提示词 / 剧本分镜 / 素材 / 成片 / 生成脚本 / 技能与记忆真源：才写。\n"
    "  带「报告 / 清单」字样不一定挨骂，看它是不是六关要交的凭证；不是凭证又为交差多造一份 = 又塞垃圾。\n"
    "答不出「不会骂」就别动手。"
)

JUNK_WORDS = (
    "报告", "审查", "复盘", "自查", "清单", "草稿", "优化稿", "对比", "存档",
    "总结", "交付", "说明", "记录", "台账", "临时", "巡检", "诊断", "核对",
    "核查", "汇报", "回执", "审计", "备忘", "快照", "残留", "清理",
    "report", "review", "check", "audit", "draft", "scratch", "snapshot",
    "temp", "tmp", "debug", "peek", "test", "summary", "notes",
)
JUNK_EXT = re.compile(r"\.(log|bak|tmp|pyc|nohup|old|orig)$|\.bak[-_.]", re.I)

# 记忆 / 技能 / 备份 / 临时目录的真源：不硬拦
ALLOW_ZONES = (
    "/appdata/local/temp/", "/windows/temp/", "c:/d/opt/_backup/",
    "/.workbuddy/memory/", "/.workbuddy/skills/", "/agents-to-feishu/skills/",
    "/openmem/hooks/", "/.workbuddy/settings", "/toonflow-src/",
    "/toonflow/data/", "/comfyui/", "/scripts/",
)
PROJECT_MARK = ("/漫剧/", "\\漫剧\\", "/d/漫剧")
WRITE_TOOLS = ("Write", "Edit", "write_file", "edit_file", "NotebookEdit")
BASH_TOOLS = ("Bash", "bash", "execute_command", "PowerShell")
WRITE_TOKENS = (">", "tee ", "cp ", "mv ", "<<", " -w ", "write")

# 🔴 提交闸门（2026-11-12 老大点头）：视频/图片生成只许走 Toonflow 画布节点（带 nodeId）
SUBMIT_BLOCK = (
    "【提交闸门·硬拦】不许用命令行直连 8090 提交生成。视频/图片只能在 Toonflow 画布节点上提交"
    "（带 nodeId），否则出的片不回填画布，白跑一条还抢 GPU 队列。查询（/task /queue /output）不受限。"
)
SUBMIT_KEYS = ("8090/video", "8090/image", "8090/v1/images", "8090/v1/videos")
QUERY_KEYS = ("/task/", "/queue", "/output/", "/list_images", "/history", "/v1/models", "/status")


def _norm(s):
    return (s or "").replace("\\", "/").lower()


def _allowed(s):
    n = _norm(s)
    return any(z in n for z in ALLOW_ZONES)


def _official_patterns():
    """读六关产出表，生成正式文件名模式；读不到就返回空（fail-open 回退旧判定）。"""
    pats = set(x.lower() for x in OFFICIAL_EXTRA)
    try:
        t = open(GATE_CARDS, encoding="utf-8").read()
        seg = t.split("**各关产出文件")[1].split("🔴")[0]
        for name in re.findall(r"`([^`]+\.md)`", seg):
            n = name.lower()
            # 第N集 是模板，实际写 第1集 / 第2集…
            pats.add(n.replace("第n集", "第*集"))
            # 项目里不带季后缀的同名正式产物（分镜表.md / 节拍清单.md…）
            short = re.sub(r"-第n集", "", n)
            if short != n:
                pats.add(short)
            pats.add(n.replace("第n集终审", "终审"))
            # 不带季名的产出，实际也可能带 -第N集 后缀（集级节拍清单-第2集.md）
            if "第n集" not in n:
                pats.add(short.replace(".md", "-第*集.md"))
    except Exception:
        pass
    return pats


def _is_official(base):
    return any(fnmatch.fnmatch(base, p) for p in _official_patterns())


def _junk(s):
    base = os.path.basename(_norm(s))
    if not base:
        c = _norm(s)
        return any(w in c for w in JUNK_WORDS) or bool(JUNK_EXT.search(c))
    # 🔴 技能点名的正式产出优先：一律不是垃圾，不看关键词
    if _is_official(base):
        return False
    if JUNK_EXT.search(base) or ".bak" in base or ".pyc" in base:
        return True
    return any(w in base for w in JUNK_WORDS)


def _in_project(s):
    n = _norm(s)
    return any(m.lower() in n for m in PROJECT_MARK) or "/漫剧" in n


# ── 流程前置硬闸门（2026-11-12 老大点头做的三条：契约 / 分镜 / 关口顺序）──
# 只拦「新建」文件；改既有文件一律放行 —— 已跑过流程的老项目改稿不该被卡。
CONTRACT_KEYS = ("一句导演命题", "主要人物与观众视点", "节拍/力量曲线",
                 "Blocking states 与空间关系", "关键电影时刻")
PROJ_RE = re.compile(r"^([a-z]:/d/漫剧/[^/]+)/", re.I)


def _proj_dir(path):
    m = PROJ_RE.match(_norm(path))
    return m.group(1) if m else ""


def _read(p):
    try:
        return open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def _has_contract(proj):
    """项目里有没有一份带五栏（或引用行）的剧本定稿。"""
    for f in glob.glob(os.path.join(proj, "剧本定稿*.md")):
        c = _read(f)
        if (any(k in c for k in CONTRACT_KEYS) or "沿用本集契约" in c
                or "总导演十问" in c or "总导演 10 问" in c):
            return True
    return False


# ── 堆7（2026-11-14）：项目根只许正式产物，中间 .mp4/.json 一律拒；成片只准放 成片/ ──
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".avi")
ROOT_JSON_OK = ("进度台账.json",)


def _artifact_block(path):
    """只拦新建；已存在的旧文件不追（不为过去的事打补丁）。"""
    proj = _proj_dir(path)
    if not proj or os.path.exists(path):
        return ""
    n = _norm(path)
    rel = n[len(proj):] if n.startswith(proj) else n
    parts = [x for x in rel.split("/") if x]
    if not parts:
        return ""
    base = parts[-1]
    ext = os.path.splitext(base)[1]
    if ext in VIDEO_EXT:
        if parts[0] in ("成片", "assets"):
            return ""
        return ("【过程稿·硬拦】%s 是视频，项目根/其他位置不许放。成片只准放 %s成片/，"
                "分段产物落 assets/<节点id>/。中间文件写 %%TEMP%% 或 C:/D/opt/_backup/。"
                % (rel, proj))
    if ext == ".json" and len(parts) == 1:
        # [2026-11-15] 画布文件名由老大在 Toonflow 里改（今天是「浅水村神女.json」），
        # 只认「画布*」前缀会把改名后的画布当过程稿拦死。按内容认：顶层带 toonflowCanvas。
        if base in ROOT_JSON_OK or base.startswith("画布") or base in OFFICIAL_EXTRA or _is_canvas_json(path):
            return ""
        return ("【过程稿·硬拦】项目根不许新建 .json（%s）。只认 进度台账.json / 画布 json（顶层带 toonflowCanvas）/ "
                "技能点名的产物；闸门凭证放 .gates/，中间结果写 %%TEMP%%。" % base)
    return ""


def _flow_block(path):
    """没有前一道关的产出，不许新建下游文件。返回拒绝原因，空串 = 放行。

    ① 契约前置：没有带五栏的剧本定稿 -> 不许新建 分镜表 / 提示词
    ② 分镜前置：没有 分镜表*.md，或表里没有这一段 -> 不许新建该段提示词（防跳关）
    ③ 关口顺序：台账当前关口在 4/5（审片/终审）-> 有片没审，不许提新段
    """
    base = os.path.basename(_norm(path))
    proj = _proj_dir(path)
    if not proj or os.path.exists(path):
        return ""
    is_board = fnmatch.fnmatch(base, "分镜表*.md")
    is_word = ("/prompts_out/" in _norm(path) or base == "build_prompts.py"
               or fnmatch.fnmatch(base, "prompts_act*.py"))
    if not (is_board or is_word):
        return ""
    if not _has_contract(proj):
        return ("【关口 0 未过·硬拦】%s 里没有带五栏的 剧本定稿-第N集.md，不许新建分镜表或提示词。"
                "先交场次导演契约五栏（一句导演命题／主要人物与观众视点／节拍·力量曲线／"
                "Blocking states 与空间关系／关键电影时刻）落盘卷首再回来。真源 gate-cards.md 关口 0。"
                % proj)
    boards = glob.glob(os.path.join(proj, "分镜表*.md"))
    if is_word and not boards:
        return ("【关口 2 未过·硬拦】没有 分镜表*.md，不许新建提示词。"
                "顺序是 节拍 → 分镜表 → 写词；跳过前两关直接写词，出片必然接不上。")
    if is_word and fnmatch.fnmatch(base, "G*.md") and boards:
        seg = base[:-3].lower()
        allb = "".join(_read(b) for b in boards).lower()
        if seg and seg not in allb:
            return ("【关口 2 未过·硬拦】分镜表里找不到 %s 这一段，不许凭空白写词。"
                    "先把这一段落进分镜表（段号／时长／画面／台词／接法）再写提示词。" % base[:-3])
    led = os.path.join(proj, "进度台账.json")
    if is_word and os.path.isfile(led):
        try:
            d = json.loads(_read(led) or "{}")
        except Exception:
            d = {}
        g = str(d.get("当前关口", "")).strip()
        if g in ("4", "5"):
            return ("【关口 4 顺序·硬拦】台账显示当前在关口 %s（审片/终审），有片没审完，"
                    "不许再新建提示词提新段。先审片、审完记台账，再回来写。" % g)
    return ""


def _verdict(payload):
    """返回 (action, target)：deny=硬拦 / ack=放行但必须先看到话 / pass=静默。"""
    tool = str(payload.get("tool_name") or payload.get("tool") or "")
    inp = payload.get("tool_input") or payload.get("input") or {}
    if not isinstance(inp, dict):
        return "pass", "", ""

    if tool in WRITE_TOOLS:
        p = str(inp.get("file_path") or inp.get("path") or "")
        if not p:
            return "pass", "", ""
        # 🔴 流程前置闸门：没契约 / 没分镜 / 有片未审 -> 不许新建下游文件
        block = _flow_block(p) or _artifact_block(p)
        if block:
            return "deny", p, block
        # 只硬拦「新建垃圾文件」；改既有正式产物不拦（已通过的东西不许再动）
        if _junk(p) and not _allowed(p) and not os.path.exists(p):
            return "deny", p, ""
        return "ack", p, ""

    if tool in BASH_TOOLS:
        cmd = str(inp.get("command") or inp.get("cmd") or "")
        c = _norm(cmd)
        if not c:
            return "pass", "", ""
        # 🔴 提交闸门：绕开画布直连 8090 提交 -> 直接拒
        if any(k in c for k in SUBMIT_KEYS) and not any(q in c for q in QUERY_KEYS):
            return "deny", cmd[:80], SUBMIT_BLOCK
        if any(t in c for t in WRITE_TOKENS) and _in_project(cmd):
            if _junk(cmd) and not _allowed(cmd) and not any(
                z in c for z in ("_backup/", "/temp/", "/tmp/")
            ):
                return "deny", cmd[:80], ""
            return "ack", cmd[:80], ""
        return "pass", cmd[:80], ""

    return "pass", "", ""


def _emit(action, target, msg=None):
    msg = msg or BOSS_LINE.format(target=target or "(未取到路径)")
    if action == "deny":
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
    # ack：放行，但同一句话必须进上下文（每次写文件都看得到）
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": msg,
        },
        "systemMessage": msg,
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stderr.write(msg + "\n")


SELFTEST = (
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/听筒里的十七岁/审查报告-前5段交付.md"}}, "deny"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/x/_自查清单.md"}}, "deny"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/x/batch.log"}}, "deny"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/x/G17.md.bak-20261112"}}, "deny"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/听筒里的十七岁/分镜表.md"}}, "ack"),
    # 🔴 流程硬闸门（2026-11-12 老大点头）：没契约 / 没分镜 / 有片未审 -> 新建下游文件直接拦
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/x/prompts_out/G01.md"}}, "deny"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/x/分镜表.md"}}, "deny"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/听筒里的十七岁/prompts_out/G99.md"}}, "deny"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/听筒里的十七岁/prompts_out/G01.md"}}, "ack"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/听筒里的十七岁/剧本定稿-第2集.md"}}, "ack"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/听筒里的十七岁/集级节拍清单-第2集.md"}}, "ack"),
    # 六关正式产出：名字带「清单 / 报告 / 审查」也不许误拦
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/听筒里的十七岁/集级节拍清单.md"}}, "ack"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/听筒里的十七岁/审查报告-第1集.md"}}, "ack"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/听筒里的十七岁/节拍对账-第2集.md"}}, "ack"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/听筒里的十七岁/第1集终审.md"}}, "ack"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/Users/oadan/AppData/Local/Temp/审查报告.md"}}, "ack"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/opt/_backup/清单.txt"}}, "ack"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/opt/openmem/hooks/new_hook.py"}}, "ack"),
    ({"tool_name": "Read", "tool_input": {"file_path": "C:/D/漫剧/x/审查报告.md"}}, "pass"),
    ({"tool_name": "Bash", "tool_input": {"command": "cd C:/D/漫剧/x && echo hi > 自查清单.md"}}, "deny"),
    ({"tool_name": "Bash", "tool_input": {"command": "cd C:/D/漫剧/x && python t.py"}}, "pass"),
    ({"tool_name": "Bash", "tool_input": {"command": "cd C:/D/漫剧/x && python - <<'PY' > C:/D/opt/_backup/清单.txt"}}, "ack"),
    ({"tool_name": "Bash", "tool_input": {"command": "ls C:/D/漫剧/x"}}, "pass"),
    # 提交闸门：绕开画布直连 8090 提交，拦；查询放行
    ({"tool_name": "Bash", "tool_input": {"command": "curl -s -X POST http://127.0.0.1:8090/video -H 'Content-Type: application/json' -d @body.json"}}, "deny"),
    ({"tool_name": "Bash", "tool_input": {"command": "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/video', data=b'{}')\""}}, "deny"),
    ({"tool_name": "Bash", "tool_input": {"command": "curl -s http://127.0.0.1:8090/task/c9fke6jyscuhq55r6"}}, "pass"),
    ({"tool_name": "Bash", "tool_input": {"command": "curl -s http://127.0.0.1:8090/queue"}}, "pass"),
    ({"tool_name": "Bash", "tool_input": {"command": "curl -s -o /tmp/a.mp4 http://127.0.0.1:8090/output/x.mp4"}}, "pass"),
    # 堆7：项目根只许正式产物，中间 .mp4/.json 拒；成片只准 成片/，分段产物 assets/
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/_probe7/x.mp4"}}, "deny"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/_probe7/out.json"}}, "deny"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/_probe7/成片/第1集.mp4"}}, "ack"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/_probe7/assets/n1/video1.mp4"}}, "ack"),
    ({"tool_name": "Write", "tool_input": {"file_path": "C:/D/漫剧/_probe7/进度台账.json"}}, "ack"),
)


def _selftest():
    bad = 0
    for payload, expect in SELFTEST:
        got = _verdict(payload)[0]
        ok = got == expect
        if not ok:
            bad += 1
        print("%-6s %-6s %s" % (expect, got, "OK" if ok else "FAIL"))
    # 真源防漂：产出表里每个名字实例化后都必须放行；表读不到 = FAIL
    try:
        t = open(GATE_CARDS, encoding="utf-8").read()
        seg = t.split("**各关产出文件")[1].split("🔴")[0]
        names = re.findall(r"`([^`]+\.md)`", seg)
    except Exception:
        names = []
    if len(names) < 6:
        print("[selftest] FAIL 六关产出表读不到（<6 项），闸门会漂回按名字判垃圾")
        bad += 1
    for name in names:
        real = name.replace("第N集", "第1集")
        p = {"tool_name": "Write", "tool_input": {
            "file_path": "C:/D/漫剧/听筒里的十七岁/" + real}}
        got = _verdict(p)[0]
        if got != "ack":
            bad += 1
            print("[selftest] FAIL 正式产出被拦: " + real)
    print("[selftest] %d 条用例 + %d 个真源产出，不符 %d 条" % (len(SELFTEST), len(names), bad))
    print("[selftest] " + ("PASS 正式产出全放行、垃圾全拦" if bad == 0 else "FAIL 有漏/误伤"))
    return 1 if bad else 0


def main():
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    try:
        raw = sys.stdin.buffer.read()
        payload = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else {}
        action, target, msg = _verdict(payload)
        if action != "pass":
            _emit(action, target, msg)
    except Exception:
        pass  # fail-open：闸门自己坏了也不许拦老大
    sys.exit(0)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
