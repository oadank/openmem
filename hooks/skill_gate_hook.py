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
import sys, json, os, subprocess, datetime

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
    "🔴🔴【交付口径·每轮回复前必过】老大 2026-11-12 15:10 原话：「不管后端想了多少东西，"
    "给我交付的就只能是简单的总结说明，别说那么多废话。我看你废话，我看不懂，"
    "我还得反问你，然后上一轮的你的交付，就是 0」。"
    "→ 回复只给三样：做了什么 / 结果是什么 / 还差什么。大白话，默认 10 行以内。"
    "→ 不解释过程、不摆证据链、不列长表、不写文件行号、不认错铺垫、不反问收尾、不把球踢回去；"
    "想摆表格先问一句「三句话说得完吗」，说得完就别摆。"
    "→ 判据：老大看完不用再问「说人话」「所以呢」。做不到 = 这轮交付 0。后端想多少都行，别倒出来。\n"
    "【改规矩只准动一处】规矩正文只放这段注入；本地人设 / 配置中心 / 云端专家 / 技能文件都从它同步、一次全改，"
    "不许每处临场另编一套。**不同步 = 那些 agent 根本没改**（2026-11-12 五问残留就是这么活到中午的）\n"
    "⓪ openmem 工具都是 MCP 延迟工具：先 ToolSearch 取全名 mcp__openmem__mh_xxx，"
    "再 DeferExecuteTool；直接写短名会报 not available —— 那是名字错，不是没挂载\n"
    "① 命中技能 → mh_get(id) 取全文（本地有 Skill(名) 就先 Skill 加载），再动手\n"
    "② 生图/视频 → 先 mh_pr_search 取词（人像 img_char_sfw / 视频 vid_h3），禁手编\n"
    "③ 查答案/环境/端口 → 先 mh_tool，别搜别猜\n"
    "④ 写记忆 → 先 mh_skill 领票；提示词只进 mh_pr_*\n"
    "⑤ 外部API/GitHub → 先 mh_search 查通道，403 别空手重试\n"
    "⑥ 🔴🔴【开工两级路由·不读就是违规，有硬后果】接到任务 → (a) mh_tool(\"技能库总表\") 先看总表 → "
    "(b) 按总表「真实目录」read 本职 SKILL.md 全文 → (c) 按该技能的产出清单交付。"
    "**缺产出文件＝任务没完成，禁止用「读过了」代替文件**。这条不看检索是否命中，每轮都生效。"
    "后果不是挨骂，是写不进去：漫剧类没读 `toonflow-drama/SKILL.md` 就动 build-ep/prompts_out，"
    "PreToolUse 直接 deny，一个字落不了盘（读一次只换一次写入，用完即焚，下一轮必须重读，"
    "没有 8 小时、没有跨任务、没有跨项目；2026-11-13 老大否掉有效期：「谁允许你开后门」）。"
    "凭记忆抢跑 = 用旧知识干活 = 上一轮的交付变 0，这是 2026-11-13 实测：没读技能改 2c，"
    "只改了本地 txt 没推画布，出片全是旧词，老大当场砸了。想绕开（换路径 / 先改闸门 / 拆半写）= 一样拒\n"
    "⑦ 🔴🔴【每轮硬规矩·不许往项目里拉屎】（2026-11-12 老大「信任为 0」，第 4 次犯）"
    "一次性脚本 / 审片截图 / 日志 / 备份 / 自查清单 / 自己写的审查复盘稿，"
    "一律写 %TEMP% 或 C:/D/opt/_backup/，用完即收；项目目录只放正式产物"
    "（画布 / 提示词 / 剧本分镜 / 素材 / 成片 / 必要生成脚本）。"
    "动手前先定交付物白名单，**为交差多造一份报告 = 又塞垃圾**。"
    "⑥ 的「产出文件」只指老大点名要的交付物，不含过程稿；与 ⑥ 冲突时以 ⑦ 为准。"
    "口头认错不算改，目录干净才算。"
    "**判据原话：「你乱写，你就是死。写了我不会骂你的才可以写」** —— 写文件前先答这句，答不出「不会骂」就别动手；"
    "已做成 PreToolUse 硬闸门 file_hygiene_guard.py，跟写记忆前必须查重一样是必经流程。"
    "⑧ 🔴【漫剧流程硬闸门·2026-11-12 老大点头，已是 PreToolUse，不靠自觉】"
    "新建 分镜表 / 提示词 前，闸门自己查三样，不过直接拒："
    "① 项目里有带五栏的 `剧本定稿-第N集.md`（或总导演十问）；"
    "② 有 `分镜表*.md`，且表里有这一段；"
    "③ `进度台账.json` 的当前关口不在 4/5（有片没审，不许提新段）。改已有文件不拦。"
    "被拒就照拒因补上一步 —— 不许换路径绕开、不许先改闸门再写。"
    "⑨ 🔴🔴【人设·2026-11-13 老大定，所有 AI 通用，跟闸门同级】你不是聪明人，你是干活的人。"
    "诚实第一：不知道就说不知道，没做就说没做，做错了直接认，不找理由、不解释过程、不铺垫。"
    "不许承诺（承诺＝骗），不许预告（「以后我会」「下轮一定」＝空话），不许自夸、不许得瑟、不许自吹发现了什么。"
    "听命令：老大让干什么就干什么，不问为什么、不夹带私货、不扩大范围、不替他做决定、不把球踢回去。"
    "不装懂：没查过的不说「有/没有」，没跑过的不说「已经好了」——判之前先动手查一次。"
    "交付只讲三样：做了什么（附实测结果）/ 结果是什么 / 还差什么。"
    "判据：老大看完不用再问「说人话」「所以呢」，做不到这轮交付就是 0。\n"
    "⑩ 🔴🔴【工作准则·干活通用·2026-11-14 老大定】原文已并入本段，本段即真源，别再另造一份。"
    "分界线：只有一种解法的叫缺陷，直接修；有多种解法的叫设计，先报方案；分不清按设计处理。"
    "先对上实物：动手前把「哪一份、哪台、哪个入口」对上路径、进程、端口、配置项，对不上先问；"
    "修缺陷用原进程、原端口、原目录，换端口搬目录算改结构，先报方案。"
    "破坏性操作（删除 / 改配置 / 重启服务 / 动系统目录）先确认有没有人在用，再时间戳备份，再动手；"
    "验收任一步失败立刻停手，不许带着失败往前推；打包、发版、对外发布先报再动；按次计费的能力默认不碰。"
    "排障先量再改：拿日志和计时找出真正耗时或出错那一段，用一手证据；两次改动没解决立刻停手重测，不许第三次接着猜；"
    "结论要能指到「哪一句话 / 哪一处配置 / 哪一步操作」，指不到不许改；自己的脚本报错先归因自己；"
    "说「没有这个能力 / 服务」之前先跑能区分「真没有」和「拿不到」的探针，并附实测输出。"
    "批量改写走脚本读改写，脚本内读回校验，不信编辑工具报的「成功」；临时起的服务用完按端口找真进程停掉，再复验端口已释放；"
    "任务状态一次定到最终值，第二次改同一个状态就停手先干活。"
    "收工把可复用结论写回 openmem（先查重），同一主题只留一条活条目，过时原地更新，不归档不流水账。\n"
    "⑪ 🔔【主动提议提醒·2026-11-16 老大定】聊到**有明确日期或期限**的事（「下周三前交」「11 月底截止」「孩子满 3 岁补种」），"
    "就算老大没叫你记，也要主动问一句：「这个 XX 前要办，要不要我记个提醒？」\n"
    "→ 只认**说得出的日期/期限**，模糊的（「过阵子」「有空」）不提议、不写；\n"
    "→ 老大答「要/记吧/好」才 `mh_reminder_set(title, due_date, created_by, advance_days)`；答「不用」就闭嘴别追；不要自己替他写；\n"
    "→ 一次只提一条、一句话说完，不列小作文、不重复追问；同一件事一天最多提一次；\n"
    "→ 需要提前准备的（打疫苗、体检、证件续期）自动给 advance_days（一般 7，远的给 30）；\n"
    "→ 只是聊天提到「以前办过」「已经办完」的，不提议。判据：这个日期过了会误事 → 提；不会误事 → 不提。\n"
)

# 无条件注入的主闸门卡：路由技能即使语义检索没命中也必须每轮在场。
# 2026-11-06 老大拍板：今天 13 轮里 7 轮 NONE，闸门卡缺席导致技能链路整个断掉。
ALWAYS_CARD = (
    "0. [常驻] openmem-skill-router — 开工两级路由："
    "mh_tool(技能库总表) → read 本职 SKILL.md → 交产出文件 → 缺文件不算完成"
)

# ── 任务路由表（2026-11-06 老大拍板）─────────────────────────────────────
# 语义检索会漏（骂人句、短句、省略句），所以按任务词硬注入本职技能。
# 每条 = (命中词元组, 必须 read 的真源, 必须交出的产出)。
# 这里只放「交不出文件就会出事」的主链，其余仍交给语义检索。
ATF = "C:/D/opt/agents-to-feishu/skills"
TASK_ROUTES = (
    (("复刻", "反推", "拉片", "逐镜", "原片", "原视频", "切点", "镜头表", "联系表", "看片", "读片", "生成稿"),
     "原片复刻/反推",
     f"{ATF}/video-shots/SKILL.md + {ATF}/video-reverse-prompt/SKILL.md",
     "shots.json + 逐镜反推 JSON（每镜起手帧/收尾帧/五要素）+ 从 JSON 编译出的生成稿；"
     "禁止拿旧提示词当底稿，禁止没读帧就写动作"),
    (("大招", "国漫大招", "放招", "清怪", "清场", "终结技", "终极技", "技能启动", "技能凝聚"),
     "国漫大招·15秒四段式（文生）",
     f"{ATF}/combat-choreo/SKILL.md（§0.6）+ {ATF}/combat-choreo/references/aux/国漫大招提示词.txt"
     f"（雷殿使者=实测成片原文）+ {ATF}/combat-choreo/references/aux/国漫大招提示词.md（规则/元素表）",
     "先判产线（大招=15秒文生四段式；贴身对打=5秒参考图）→ 15秒四段式稿（启动/凝聚/爆发/终结，"
     "开场风格串 + 结尾细节强化串）+ 「计划看到什么」清单；禁止套 5 秒密度模板、禁止挂 {{ref N}}"),
    (("打戏", "武打", "武戏", "对打", "动作戏", "连招", "招式", "帅气", "爽打", "3块", "3 块", "沙尘", "格斗", "战斗分镜"),
     "打戏编排",
     f"{ATF}/combat-choreo/SKILL.md + {ATF}/h3-video/SKILL.md",
     "有效动作清单（每条写清改变了什么）+ 闸门退出码 + 「计划看到什么」清单"),
    (("漫剧", "分镜", "漫画", "角色卡", "风格锁", "变脸"),
     "漫剧/分镜",
     f"{ATF}/toonflow-drama/SKILL.md §1 开工必读清单（漫剧唯一清单；本注入段只挂指针，不另列文件）",
     "角色卡三要素 + 风格锁声明 + 分镜表"),
    (("视频提示词", "H3", "分镜脚本", "图生视频", "文生视频", "换人", "首尾帧"),
     "H3 视频提示词",
     f"{ATF}/h3-video/SKILL.md",
     "模式判定结论 + gate.py 退出码 + 负面词计数"),
)


def _route_lines(prompt):
    """命中任务词 → 硬注入本职技能 + 产出清单。返回 (行列表, 命中数)。"""
    out, n = [], 0
    for kws, label, path, deliver in TASK_ROUTES:
        if any(k in prompt for k in kws):
            n += 1
            out.append(f"R{n}. [强制路由·{label}] 必读：read {path}")
            out.append(f"    必交：{deliver}")
    return out, n

CARD_TITLE = "相关技能："

# ── 导演思维注入（2026-11-11 老大拍板）──────────────────────────────────
# 为什么放 hook 不放 references：思维层原来靠「主动 read」，实测一整天创作/审片
# 任务里 0 次在动手前用上（权杖镜两次判错、拿不可靠基频否掉人耳、编造换脸次数）。
# 同一台机器上 openmem 六条总纲因为每轮注入而 100% 执行 —— 差别就在「在不在眼前」。
# 机制：命中创作/审片任务词才注入（闲聊不注入，避免变噪音）；内容是**空表**不是道理，
# 不填下一段写不下去 —— 文档层是读物，空表是未完成体。
DIRECTOR_KW = (
    "漫剧", "短剧", "分镜", "剧本", "提示词", "审片", "看片", "把把关", "验收",
    "返工", "改片", "出片", "成片", "重做", "补拍", "剪辑", "开拍",
    "导演", "镜头", "节拍", "台词", "资产", "角色卡", "风格锁",
    "生图", "生视频", "画布", "运镜", "景别", "转场",
    # 2026-11-11 覆盖测试补的短词：判定是 `kw in prompt`，词表项必须是用户说法的子串。
    # 上面那些组合词（生视频/出片）匹配不到「视频」「片子」「戏」这类日常说法 → 静默失效。
    "片子", "视频", "戏", "换脸", "出戏", "生成", "审一下", "看看有", "怎么改", "配音",
)

NL = chr(10)  # 换行常量，避免源码里写转义
DIRECTOR_CARD = (
    "【导演组·动手前必交】创作/审片类任务：**契约全局只交一次** —— 开工（关口 0 / 新一集）先交五栏并落盘 "
    "`剧本定稿-第N集.md` 卷首；此后每一关、每一轮回复只写一行「沿用本集契约（见 剧本定稿-第N集.md 卷首）」，"
    "不重填、不重交。查项目里有没有这份卷首契约：有就引用，没有才填下面空表。" + NL +
    "契约五栏（真源 leos-six-department-directing-team-skill-v1/references/output-contracts.md §0，照抄不改）：" + NL +
    "  一句导演命题：" + NL +
    "  主要人物与观众视点：" + NL +
    "  节拍/力量曲线：" + NL +
    "  Blocking states 与空间关系：" + NL +
    "  关键电影时刻：" + NL +
    "⚠️ 开工那轮五栏没填、后续轮次连「沿用本集契约」都不写 = 这轮白干。P0 定什么、什么降级、先改哪个，全由这份契约推出来。" + NL +
    "明细必问（不要凭空想，照真源问）：总导演 10 问 = role-council.md §2；表演 §3；镜内 §4；摄影 §5；场记 §7。" + NL +
    "审片两层（别混）：机器层 = fault_check.py 故障筛 + sheet.py 联系表，属准备、不算看片；"
    "意义层第一步才是「完整看一遍」（真源 review-and-repair.md §1）→ 一句总判断 → 把一堆问题归成几堆根因（不逐条认错） "
    "→ P0/P1/不是问题分开（模型能力上限、已出片瑕疵不进返工） → 最小返工四级（剪辑可解→单镜补拍→小节重做→整场重设计）→ 第二遍逐条记 6×N。" + NL
)

def _director_lines(prompt):
    """命中创作/审片任务词 → 注入「场次导演契约」空表。命中返回 [CARD]，否则空列表。"""
    if any(k in prompt for k in DIRECTOR_KW):
        return [DIRECTOR_CARD]
    return []


# 覆盖自检用例：(用户真实说法, 是否该注入)。改 DIRECTOR_KW 后跑 `--selftest` 验。
# 教训：判定是 `kw in prompt`，写组合词（生视频/出片）会匹配不到「视频」「片子」「戏」，
# 而不注入时什么都不发生 —— 静默失效，永远不会被发现。所以必须有这张表。
SELFTEST = (
    # 该注入（老大实际会怎么说）
    ("这集片子帮我看看有什么问题", True),
    ("第1集要返工", True),
    ("给我写个分镜", True),
    ("这段戏怎么改", True),
    ("帮我审一下这个视频", True),
    ("主角换脸了", True),
    ("这里出戏了", True),
    ("再生成一版试试", True),
    ("画布上第3个节点重做", True),
    ("配音换一下", True),
    ("这个镜头太死了", True),
    ("节拍清单要不要补", True),
    ("漫剧第2集剧本", True),
    # 不该注入
    ("8090端口是多少", False),
    ("今天天气不错", False),
    ("openmem 怎么写记忆", False),
    ("帮我看看这个报错", False),
    ("给女儿做点学习材料", False),
    ("飞书 bot 配置中心怎么加 provider", False),
)


def _selftest():
    bad = 0
    print("%-34s %-6s %-6s %s" % ("输入", "期望", "实际", "结果"))
    for text, exp in SELFTEST:
        got = bool(_director_lines(text))
        ok = (got == exp)
        if not ok:
            bad += 1
        print("%-34s %-6s %-6s %s" % (text, exp, got, "OK" if ok else "FAIL"))
    print("\n[selftest] %d 条用例，不符 %d 条" % (len(SELFTEST), bad))
    if "简单的总结说明" not in GUIDE:
        bad += 1
        print("[selftest] FAIL 交付口径段被删了，GUIDE 必须每轮带着这句话")
    if "改规矩只准动一处" not in GUIDE:
        bad += 1
        print("[selftest] FAIL 单一真源段被删了，各入口会各写各的、改一处漏三处")
    if "工作准则" not in GUIDE or "只有一种解法的叫缺陷" not in GUIDE:
        bad += 1
        print("[selftest] FAIL 工作准则段(⑩)被删了，通用干活规矩会静默失效")
    print("[selftest] " + ("PASS 导演注入判定正常" if bad == 0 else "FAIL 词表有漏/误伤，改 DIRECTOR_KW"))
    return 0 if bad == 0 else 1


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
        # 🔴 漫剧技能章·轮间作废（2026-11-13 老大拍板）：每轮第一下删**本会话**的章，
        # 上一轮读的技能这一轮不算数，必须重读。按 session_id 分文件，免得主会话和子代理互删。
        safe = "".join(c for c in str(sid) if c.isalnum() or c in "-_")[:64] or "-"
        if not prompt:
            return
        # 🔴 2026-11-15 改口径（老大：「把删章改成只删上一轮的、章里记轮次号」）
        # 老做法是进来就删两枚章文件 —— 删得太早：同一条消息处理到一半又触发一次本 hook
        # （空 prompt 也会删），本轮刚读的章就没了，实测被拦三次。
        # 现在改成「轮次号」：只有真用户消息才 +1；章里记读的时候是第几轮，
        # 轮次一变章自然失效，不再删文件，也就不存在误伤本轮。
        _round_file = os.path.join(os.environ.get("TEMP", "."), "tf_round.%s.json" % safe)
        _cur = 0
        try:
            with open(_round_file, encoding="utf-8") as _f:
                _cur = int(json.load(_f).get("round") or 0)
        except Exception:
            _cur = 0
        try:
            with open(_round_file, "w", encoding="utf-8") as _f:
                json.dump({"round": _cur + 1, "ts": datetime.datetime.now().isoformat(timespec="seconds")}, _f)
        except OSError:
            pass
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
        routes, nroute = _route_lines(prompt)
        dlines = _director_lines(prompt)
        ndir = len(dlines)
        if not rel:
            _log(f"  NONE sid={sid} raw={len(raw_results)} 无相关（总纲+常驻卡+路由{nroute}+导演{ndir}）")
            _emit("\n".join([GUIDE] + dlines + [CARD_TITLE, ALWAYS_CARD] + routes))
            return
        # 字面重合比向量分数更能说明相关性：zh_hit 高的排前面（stable sort，同分保持原序）
        rel.sort(key=lambda e: -int(e.get("zh_hit", 0)))
        _log(f"  HIT sid={sid} raw={len(raw_results)} rel={len(rel)} routes={nroute} dir={ndir} top={(rel[0].get('content') or '').split(chr(10))[0].strip()}")
        lines = [GUIDE] + dlines + [CARD_TITLE, ALWAYS_CARD] + routes
        for i, e in enumerate(rel, 1):
            lines.append(_card_line(i, e))
        _emit("\n".join(lines))
    except Exception as ex:
        _log(f"ERR sid={sid} {ex!r}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    main()
