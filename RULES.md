# openmem 接入总交代（mh_skill）

> 接上 openmem 必读。只给条文、秒回、没有第二份手册。设计取舍与理由全文见条目 `9503b404`。
> 省 token 可带 `topic=` 只取一节（读 / 写 / 技能 / 代码 / 坑表），坑表恒附、铁律随写类节附上（读节不带）；不带 = 全文。

> 🔴 **通用前置闸门（对所有连上 openmem 的 agent 生效）**：接到任务、动手前，先 `mh_search(category="uskill_idx", top_k=3)` 查技能库；命中现成技能照着做，没命中再自己干。

## 读
- **入口**：MCP `http://127.0.0.1:3466/mcp`（前缀 `mh_`）；人看的控制台 `http://127.0.0.1:3467`。
- 🔴 地址统一写 **`127.0.0.1`**，别写 `localhost` —— 服务只监听 IPv4，部分客户端不回退，别赌。
- **查询梯队**：`mh_tools_list` → `mh_tool(name)`（标准问题秒回，别现搜别猜）→ `mh_service` / `mh_search(query, top_k)` → 最后才 `mh_ask`。动手前先 `mh_status` 拿 capabilities。
- `mh_search` 语义检索；`mh_get(id)` 按条目取全文，别全库搜全文。
- 🔴 联网 / 调外部 API 前先搜 openmem —— 老大记忆优先，别重复造轮子、重复踩坑。

## 写
1. **先搜** `mh_search(关键词, requester="<你的名>")`，换 1~2 个近义词再搜一遍。
2. **有则 `mh_update(id, …)`** 保留 id（引用链不断），不新增。
3. **无则 `mh_write`**（source 必填 = 记账键，见下）。
4. **同主题只维护一条活条目**，版本/状态变了就 update，禁止一版一条流水账。

写错怎么收：**能改** → `mh_update`；**确认两条是同一件事**、增量已并入保留条 → `mh_deprecate(id=下线条, supersede=保留条)` 落 `superseded_by`（🔴 只在正文写「本条取代 X」机器读不到，必须落字段；`restore=true` 可反悔）；**老大点名的误写/垃圾要无痕清除** → `mh_deprecate(id, hard=true)` 物理 DELETE，不可逆。

### 三道闸门（当场判，无豁免）
| 关 | 拦什么 | 怎么过 |
|---|---|---|
| ① 规矩关 | 手上没票（没领过，或那张已用掉） | `mh_skill(agent="<你的名>")` 领票。**票管一条，写成功即作废** |
| ② 查重关 | 你自己 30min 内没搜过库 | `mh_search(…, requester="<你的名>")` 后再写 |
| ③ 重复关 | 与库内条目相似度过高 | ≥0.88 拒 → `mh_update` 合并；≥0.855 拦一次贴老条目原文，读完仍判不同原样再交即放；≥0.84 放行但要你自判 |

🔴 分数不是结论：0.98 可能是同模板不同实体，0.82 可能是同一件事两份 —— 判同判异只能 `mh_get` 读原文。台账族（`source=agent-matrix` 的 services/capabilities）只认「首行同名」，手写条目不豁免。③ 有防死锁阀：同内容被拦过一次、再交即放。

### 🔴 领票按 `source` 记账，不是按 agent（踩过）
写普通记忆 → `mh_skill(agent="workbuddy")`（报你自己的名）；写技能条目 → `mh_skill(agent="<技能名>")`，如 `mh_skill(agent="h3-prompt-writing")`。报错名 = 票买了报不了销，第①关永远拦。规矩/查重均按各自 agent 记账，别人领过搜过不代表你可以跳过。

## 技能
- MCP：`mh_register_skill(file=["<SKILL.md 绝对路径>"], agent="<你的名>")`；命令行 `py -3 mem_core.py register_skill --file <SKILL.md>`（同名重复自动跳过，幂等）。🔴 用 `py -3`：本机 PATH 首位的 `python` 是 WorkBuddy 自带的 3.13，没装 psycopg2。
- 🔴 手写 `category=uskill_idx|uskill_doc` 的 `mh_write` **直接拒收**（`gate: skill_layer`，不看票）；改已有条目走 `mh_update`，新增走 `mh_register_skill`。同名重注册会自动清同 slug 旧摘要/旧名片（防「库里旧、文件新」）。
- 架构：名片 `uskill_idx`（技能名 / 何时用 / **正文真源绝对路径**）+ 详情 `uskill_doc`（≤1200 字摘要），**正文永远只在文件里**，openmem 只做索引 + 路径。判据一句话：「这内容改了之后，会不会出现库里旧 / 文件新两个版本？」会 → 不入库，只写路径。
- 设计取舍与理由全文：`mh_get("9503b404")`。

## 代码
```
[文件] C:\D\opt\agents-to-feishu\src\config\render.ts:530
[符号] syncModelToCli(botId, model) : void
[作用] 一键切模型时把 model 写进该 bot 的 CLI 配置
[坑]   必须无条件调用，早期加了 if 判断 → dsh/deeptutor 不生效
```
🔴 `[文件]` / `[符号]` 必须原样照抄 —— 它们是检索钥匙，翻译成大白话就永远搜不到。一条卡片一件事，代码永远现读。

## 坑表
| 坑 | 对策 |
|---|---|
| `mh_search` 传 `limit` | **不报错但静默失效**，一律用 `top_k` |
| 条目写超 ~512 token | BGE 只算前 512，后半截永远搜不到 —— 长文只存路径 |
| 改了条目没刷成品答案 | = 白改（`mh_tool` 另一套缓存），要 `force_refresh` |
| 符号类查询写大白话 | 符号原样进 query（`syncModelToCli` / `18800`） |
| `curl GET /mcp` 探活 | 挂死 3 分钟 —— 用 `sc query openmem` |
| Git Bash 的 curl 发中文 | GBK 乱码 —— 测中文用 Python requests |
| MCP 响应乱码 | 显式 `r.content.decode('utf-8')`，别用 `r.text` |
| 直连 PG 查 uuid 列表 | 写 `::uuid[]` 转型，否则 `uuid = text` 不存在 |
| 报条数报全量 | 只报有效数（`superseded_by is null`） |

## 铁律
- 🔴 **凭据不进 openmem**（唯一例外 = `~/.workbuddy/MEMORY.md` 凭据段，老大授权）。
- 🔴 **正文不进 openmem**：技能正文、代码、文档全文一律只存路径（判据见「技能」节）。
- 🔴 写 embedding 必须同步 `embedding_v vector(512)`，否则检索不到。
