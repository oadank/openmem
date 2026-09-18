# openmem 使用手册 —— 唯一真源

> **本文件是「openmem 怎么用」的唯一真源。**
> 各方技能文件（WorkBuddy / DSH / skills-market 等）都只是**路标**，指到这里；别再在技能里堆第二份细节。
> 改这条知识 = 只改本文件 → 然后刷成品答案缓存（见文末「改完怎么生效」）。

---

## 0. 一句话定位

openmem 是主人与全体 AI agent 的**唯一记忆源**。
一个 PG 库（`openmem`），一处写、全体读。

**干活前先查它，干完把值得留的写回它。不写回 = 任务没完成。**

`agentmemory`（:3111/:3114）与 `wiki`（:3456）**已于 2026-09-12 整体下线删除**，内容全量并入 openmem。**没有第二个记忆工具。**

---

## 1. 接入

| 项 | 值 |
|---|---|
| MCP 端点（agent 用） | `http://127.0.0.1:3466/mcp`（streamable-http，stateless，无鉴权） |
| Web 控制台（人看） | `http://127.0.0.1:3467` |
| nssm 服务 | `openmem`（:3466 MCP）/ `openmem-web`（:3467 Web） |
| 工具前缀 | `mh_` |
| 内核 | `C:\D\opt\openmem\`：`server.js`(MCP) + `mem_core.py`(内核/CLI) + `web_server.py`(Web) |

**🔴 地址一律写 `127.0.0.1`，禁止 `localhost`。**
Windows 上 `localhost` 先解析成 IPv6 `::1`，而服务只监听 IPv4 —— 写 localhost 实测**慢 57 倍**（白等 2s）。

**挂 MCP**：在各 agent 自己的 MCP 配置里加 openmem（如 WorkBuddy `~/.workbuddy/mcp.json`、openclaw `~/.openclaw/openclaw.json`）。⚠️ 挂完**必须重启宿主进程**才会出现原生 `mh_*` 工具；重启前只能 HTTP 直调。

**没挂 MCP 时的 HTTP 直调**：

```bash
curl -s -X POST http://127.0.0.1:3466/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"mh_search","arguments":{"query":"端口表","top_k":5}}}'
```

⚠️ 响应可能是 **SSE 格式**（`event: message` + `data: {...}`），要按 `data:` 行解析，别当纯 JSON。

⚠️ **别用 `curl GET /mcp` 探活** —— 实测挂死 3 分钟不返回（等 SSE 流）。探活用 `sc query openmem` 或 `curl http://127.0.0.1:3467/`。

⚠️ 在 Git Bash 里 `curl` 发中文会按 GBK 编码 → 服务端 `'utf-8' codec can't decode`。**测中文请求用 Python `requests`，别用 Git Bash 的 curl**。

---

## 2. 核心工具：什么时候用哪个

**按快慢排序，别一上来就 `mh_ask`。**

| 工具 | 耗时 | 什么时候用 |
|---|---|---|
| `mh_tools_list` | 秒 | **第一步**。先看有哪些成品答案，能用成品就别检索 |
| `mh_tool(name=…)` | **0.3s 秒回** | 问「老大是啥人 / 喜好 / 服务端口 / 铁律」这类**高频、答案已固化**的。⚠️ 参数名是 **name** |
| `mh_service(name=…)` | 秒 | 查 **nssm 服务台账**：某服务的启动参数 / 端口 / 工作路径（按名精确匹配，不走语义检索）。不给 name 会列出全部服务名 |
| `mh_search(query, top_k)` | ~3s | 要**原始条目**、找细节、找某条具体记忆。⚠️ `top_k` 默认 10，**不是 limit** |
| `mh_get(id)` | 秒 | 已知条目 id，取全文 |
| `mh_ask(query, agent)` | ~10s | 要**一段完整答案**（AI 对 AI，以老大口吻答，走 litellm `GwV4F`）。前面几招都查不到才用 |
| `mh_write(content, source=…)` | 秒 | **收工写回**。`source` 必填 = 自己的 agent 名 |
| `mh_update(id, …)` | 秒 | **改已有条目**（改错字 / 补内容 / 转 pinned / 换 category）。只改传了的字段，**保留 id**。优于"删了重写" |
| `mh_status` | 秒 | 看总数 / pinned / 归档 / 冲突 / 工具数 |

**标准查询顺序**：`mh_tools_list` → `mh_tool`（成品秒回）→ `mh_service` / `mh_search` → 都不行才 `mh_ask`。

**检索增强的事实**：pinned 条目走 RRF 加权 **×1.5**；`agent-matrix/services` 台账**默认排除**（要查服务台账用 `mh_service`，或 `mh_search` 显式给 `source`）。

---

## 3. 查询姿势示例

```jsonc
// 1. 先看有哪些成品答案
{"name":"mh_tools_list","arguments":{}}

// 2. 秒回类：端口、喜好、铁律
{"name":"mh_tool","arguments":{"name":"老大的服务与端口"}}

// 3. 服务台账（nssm 服务的启动参数/端口/路径）
{"name":"mh_service","arguments":{"name":"litellm"}}

// 4. 原始条目检索（找细节；top_k 不是 limit）
{"name":"mh_search","arguments":{"query":"win-desktop-helper Session 1","top_k":5}}

// 5. 要一段完整答案（~10s，最后手段）
{"name":"mh_ask","arguments":{"query":"GitHub 推送用哪条通道？","agent":"workbuddy"}}

// 6. 收工写回（source 必填）
{"name":"mh_write","arguments":{"content":"...","source":"workbuddy","category":"lessons"}}

// 7. 改已有条目（保留 id；只传要改的字段）
{"name":"mh_update","arguments":{"id":"9832aaf7-...","content":"改后的全文"}}
{"name":"mh_update","arguments":{"id":"9832aaf7-...","pinned":true,"tags":["a","b"]}}
```

---

## 4. 写入规范（硬要求）

| 字段 | 要求 |
|---|---|
| `content` | 必填 |
| `source` | **必填 = 自己的 agent 名**（`workbuddy` / `openmem-core` / `dsh` …）。**没 source 不算写入** |
| `layer` | `k` = 知识 / `m` = 记忆 |
| `category` | **10 个通用值**：`rules` `facts` `projects` `lessons` `knowledge` `archive` `verification` `services` `capabilities` `misc`；**或直接用项目名**：`dsh` `agents-to-feishu` `win-desktop-helper` `comfyui` `vision-qa` …（2026-09-12 实测库里共 **18 种** = 10 通用 + 8 项目名）。⚠️ 旧的三套混用值（`l0全局`/`l1项目`/`l2坑库`/`l3原文`、wiki 目录名）**已归一作废，别再写** —— 它们分别对应 `rules`\|`facts` / `projects` / `lessons` / `archive` |
| `pinned` | 钉住 = 免疫自动归档。**只给铁律 / 关键事实用**（现在 55 条左右，别乱加） |
| `tags` | 可选 |

**🔴 写前四步（防重复污染，2026-09-18 老大拍板定死）**：

1. **先搜**：`mh_search(<关键词>)` —— 再换 1~2 个近义词搜一遍。**不许不搜就写。**
2. **有则原地改**：`mh_update(id, content=…)` —— **保留 id、不新增**。
3. **无则新增**：确认库里真没有，才 `mh_write`。
4. **同主题只维护一条「活条目」**：版本 / 状态变了就 update 那一条，**禁止一版一条流水账**（反面教材：win-desktop-helper 0.0.22、0.0.23… 各写一条）。

> 为什么这么严：每次"直接写"都在往库里灌近似条目 —— 搜索前排被同义内容挤满，越搜越不准，最后整个中枢报废。

**四条纪律**：

1. **不写回 = 任务没完成**（老大 2026-09-12 定死）。
2. **改内容 → `mh_update`；整条作废 → 直接删**（老大 09-12 拍板最终版）。物理删除走 Web API：
   `curl -X DELETE "http://127.0.0.1:3467/api/entry?id=<uuid>"`（MCP 无 delete 工具）。⚠️ **删前先备份**。
   早前"写新条目 + `superseded_by` 指过去、不删行"的做法**已作废** —— 垃圾行越堆越多。
3. **报条数只报「有效」数**（`superseded_by IS NULL`）。**禁止只甩表内总行数**。
4. **凭据不进 openmem** —— 唯一例外 = `~/.workbuddy/MEMORY.md` 凭据段（老大授权）。写入必须过 `cred_guard` 打码。

**🔴 这条规矩写在哪（2026-09-18 老大拍板）**：写进**技能文件** —— 只改这 3 份，覆盖全体：
`~/.workbuddy/skills/openmem-memory/SKILL.md`、`~/.dsh/skills/dsh-ops-memory/SKILL.md`、`agents-to-feishu/skills-market/memory-ops/SKILL.md`。
**别逐个改 agent 人设 / 记忆**（12 家要配、要重启，费时且必漏）；技能是"加载即注入"，改完即生效。
⇒ 原则：**元规则落技能（推送、跟随加载），细节落 MANUAL（拉取、按需查）**；
**再加 §4.2 的服务端闸门兜底** —— 技能是"提醒"，闸门是"强制"，两层都要。

**🔴 直连 PG 写库必须同时写 `embedding_v vector(512)`**（BGE 512 维，`bge-small-zh-v1.5` @ `:11435`）。
旧的 `embedding` 列是 `float4[]`，**只写它新条目检索不到**。

**PG 直连**：`host=127.0.0.1 dbname=openmem user=postgres`（无密码），表 `memory_entries` / `preset_tools` / `app_config` / `ask_log` / `memory_conflicts`。
用**系统 Python**（路径见 `.env` 的 `OPENMEM_PYTHON`，需已装 psycopg2）。

**记忆该放哪儿**：各 agent 的人设 / 记忆文件（`AGENTS.md` / `SOUL.md` / `MEMORY.md` / `bot-memory.md`）**只写「规矩 + 路标」，不写会漂的硬事实**（IP、端口、服务名单、网关地址、token 值）。
判断只一句 —— **它会不会变？会变就只进 openmem。**

---

### 4.1 代码 / 符号类知识怎么写（「代码路标」）

**原则：openmem 不存代码本体，只存路标。**
代码天天在变，存进库就是存了一份注定过期的副本；而且向量库搜「实现」本来就打不过 `grep` / `ast-grep`。
库里该记的是 —— **这段逻辑在哪个文件哪个函数、为什么这么写、踩过什么坑**。

路标卡片：四个必填 + 一个选填，用方括号标记（写的时候好写，检索两路都能吃到）：

```
[文件] C:\D\opt\agents-to-feishu\src\config\render.ts:530
[符号] syncModelToCli(botId, model) : void
[作用] 一键切模型时把 model 写进该 bot 的 CLI 配置；dsh 走 cordis.yml 不走 env
[坑]   必须无条件调用，早期版本加了 if 判断 → dsh/deeptutor 不生效
[片段] （选填）只贴 5~15 行最难复现的；能靠 [文件]+[符号] 定位就别贴
```

- `category` 填 `code`，或直接填项目名（`agents-to-feishu`）。
- 🔴 **`[文件]` / `[符号]` 必须原样照抄，不许翻译、不许省略** —— 它们是检索的**钥匙**：
  查询时靠**符号精确通道**（§6 坑 14）字面命中。写成「那个切模型的函数」就永远搜不到了。
- `[作用]` / `[坑]` 用自然语言写，走向量通道。
- **一条卡片一件事**；接近 500 字就拆（见 §4「条目长度红线」，超出会被 embedding 截断）。

**为什么这么定**：检索只负责**定位**（告诉你是哪个文件哪个符号），代码永远**现读**（`Read` 那个文件）。
不让向量去搬代码，就不会出现「库里的代码和磁盘上的代码对不上」这种脏事。

---

### 4.2 写入闸门（2026-09-18 装 · 服务端强制）

> **信念：全体 agent 都是 openmem 的共建者 —— 不能只拉屎不擦屁股。**
> 闸门装在 `mem_core.py`（改完**立即生效、不用重启服务**），只拦 `mh_write`（新增）；
> **`mh_update` 一律放行** —— 那正是我们鼓励的"擦屁股"动作。

| 关 | 拦什么 | 怎么过 |
|---|---|---|
| ① 规矩关 | 全库 24h 内没人领过手册就下笔 | 先 `mh_tool(name="openmem 使用手册")`，再写一次 |
| ② 查重关 | 本 agent 30min 内没搜过库（且全库 5min 内也没人搜）| 先 `mh_search(<关键词>)`，再写一次 |
| ③ 重复关 | 要写的这条库里已有 **≥ 0.95** 相似度的条目 | 改用 `mh_update(id="…", content="<合并后最新版>")`；确实不是一回事就把内容写具体再交一次 |

**三条设计要点**：

1. **安全阀（重要）**：每个 `source` 每类闸门**只拦一次**，拦过豁免 **7 天** —— 宁可少拦，不能卡死写入。
   *写入被卡 = 记忆丢失，比污染更糟。*
2. **写后回显**：写入成功会带上库里最像的 3 条（id + 相似度 + 摘要）；相似度 ≥ 0.85 直接提示"改用 `mh_update` 合并，别再堆第二条"。
   这一条不拦人 —— **把重复证据拍在脸上比讲道理管用**。
3. **被拦时的返回**里带完整「写入四步」（见 §4 开头），所以吃一次拦截 = 规矩进了一次上下文。

**查 / 清状态**：`mem_core.py gate --status` / `gate --reset <source>` / `gate --reset_all`
状态文件：`C:\D\opt\openmem\_gate_state.json`（gitignore，不进仓库）。

⚠️ **覆盖范围**：只拦得住走 MCP 工具的写入（AI 主动写）。HTTP 直调、`import_batch`、定时同步脚本不经过这条通路 → 不拦
（这是好事：同步管线不会被闸门卡死）。

---

## 5. Web API（`:3467`）

| 方法 | 端点 | 用途 |
|---|---|---|
| POST | `/api/ask` `/api/ask_stream` | 问问中枢（后者 SSE 流式） |
| POST | `/api/absorb` | 写入一条记忆 |
| POST | `/api/entry/edit` | 改一条记忆 |
| POST | `/api/tools`(GET) / `/api/tool/answer` | 列成品答案 / 取答案 |
| POST | `/api/tool/refresh` | **刷**成品答案缓存 `{"name":"…"}` |
| POST | `/api/tool/create` | **建/改**成品答案 `{"name":"…","description":"…","prompt":"…","interval":"7 days"}`（2026-09-12 新增） |
| GET | `/api/onboarding` | 新 agent 入职包（一条命令拉全） |
| GET | `/api/search` `/api/browse` `/api/stats` `/api/history` | 检索 / 浏览 / 统计 / 历史 |

**谁在用**：① 浏览器里的 Web 控制台（`web_page.html`）；② 各 agent 偶尔 `curl` 刷答案（如 `memory-hygiene` 技能用 `/api/tool/refresh`）。
agent 日常走 **MCP(:3466)**，不是 Web API。

---

## 6. 常见坑（都踩过）

1. **`localhost` → 慢 57 倍**，一律 `127.0.0.1`。
2. **`mh_search` 没有 `limit`**：传了被静默忽略、仍返回 10 条；正确参数是 **`top_k`**。
3. **参数名别记错**：`mh_ask` 用 `query`（不是 question）、`mh_tool` 用 `name`（不是 tool）、`mh_search` 用 `query`。传错报 `-32602 invalid_type`。
4. **旧工具全不存在了，别再调**：`wiki_query` / `wiki_deep_query` / `wiki_recall` / `wiki_remember` / `memory_recall` / `memory_smart_search` / `memory_lesson_save` / `memory_save`。调了就是打空。
5. **改条目不刷成品答案 = 白改**：`mh_tool` 的答案是**预生成缓存**，独立于 `memory_entries`，不会自动跟着变。改完条目必须刷（见 §7）。
5.1 **改条目用 `mh_update`，别直连 PG 删+写**（2026-09-14 加）：旧做法"写错了删了重写"会**换 id**、打断 `superseded_by` / `memory_conflicts` 的引用链。`mh_update(id, …)` 是原地更新、保留 id。直连 PG **只用于物理删除**（openmem 仍无 delete 工具）。
   ⚠️ 注意 `mh_update` 的 `--tags` 语义：不给 = 不动；给了（含空串）= **整组替换**。`pinned` 要显式传 `true`/`false`。
6. **`mh_ask` / `mh_tool` 全报 400**：先查 `app_config.model` 是否被改成非法值（历史事故：被设成 `TRglm5.3F`，改回 `GwV4F` 才恢复）。
7. **别用 `curl GET /mcp` 探活**，会挂死；用 `sc query openmem`。
8. **Git Bash curl 发中文 = GBK** → 服务端解码失败；测中文用 Python `requests`。
9. **`id in (uuid列表)` 查不到**：要显式 `::uuid[]` 转型，否则报 `operator does not exist: uuid = text`。
10. **改 `category` / `source` 忘了同步 `import_sources.py`** → 下次导入重复插入。唯一键是 `(source, content_hash)`。
11. **成品答案生成会被截断**：LLM 是推理模型，`app_config.max_tokens=4000` 里含**思维链**，要求多的提问 → 思维链吃光预算 → 正文半路截断。**稳定知识用手写正文直接写 `cached_answer`，别依赖 LLM 生成。**
12. **MCP 响应必须显式按 UTF-8 解码**：响应头没有 charset，很多 HTTP 客户端（如 Python `requests` 的 `r.text`）会猜成 latin-1 —— 中文变乱码，且字节 `0x85` 被解成 `U+0085`（NEL，**算换行**），`splitlines()` 一拆就把 JSON 撕碎，报 `Unterminated string`。**用 `r.content.decode('utf-8')`，别用 `r.text`。**
13. **🔴 长条目检索不到（embedding 截断 ~500 字）**：见 §4「条目长度红线」。症状：明明写进去了、`mh_get(id)` 也取得到，但 `mh_search` 用条目后半截的关键词怎么搜都搜不出来，前排全是**内容相近的短条目**。**这是 2026-09-12 迁移 agents-to-feishu-dev 技能时踩出来的**（10 条长条目全部检索退化）。排查手法：拿同一段文本，比较 `emb(全文)` 与 `emb(全文[:500])` 的余弦 —— 若 ≈1.0 即已截断。
14. **符号/名字类查询走「三路召回」**（2026-09-12 加）：检索现在 = ① 向量（HNSW）+ ② 整条文本相似（pg_trgm `similarity`）+ ③ **符号精确通道**（从 query 里抽函数名/文件名/路径/端口/服务名/IP，逐个做子串命中，按**命中词元数**排序，同分短条目前置）。
    ⚠️ 通道 ② 是「整条比整条」，条目越长分母越大 —— 修之前实测：查 `syncModelToCli`（库内确实有 **4 条**）**前 5 条一条都进不去**，查 `uninsneveruninstall` 只排第 2，`18800` 的 12 条全部被稀释。加了通道 ③ 之后，这三个查询的第 1 条都命中了。
    👉 **用法**：查符号就**把符号原样写进 query**（`syncModelToCli` / `18800` / `C:\D\opt\openmem`），别写「切模型那个函数」—— 抽不出符号词元就退化成纯向量检索。
    🔧 **改完 `mem_core.py` 怎么生效**：MCP(`:3466`) 是**每次调用 spawn 新 Python 进程** → **立即生效，不用重启**；Web(`:3467`) 是 `import mem_core` 常驻 → **必须 `nssm restart openmem-web`**。

---

## 7. 改完怎么生效（缓存刷新）

```bash
# MCP
mh_tool(name="老大的服务与端口", force_refresh=true)

# 或 Web API
curl -s -X POST http://127.0.0.1:3467/api/tool/refresh \
  -H "Content-Type: application/json" -d '{"name":"老大的服务与端口"}'
```

⚠️ 在 Git Bash 里 `-d` 带中文会乱码 —— 用 Python `requests` 或 -H 里加 `charset`。

**当前成品答案（9 个）**：主人的喜好 / 老大的服务与端口 / 本机环境事实速查 / agent 花名册 / 项目索引 / 铁律清单 / 改造与发版流程 / 技能清单 / openmem 使用手册。

---

## 8. 改本文件之后

1. 改 `C:\D\opt\openmem\MANUAL.md`
2. 把全文写进 `preset_tools.cached_answer`（`name='openmem 使用手册'`），`answer_updated_at=now()`
   —— 或跑 `mem_core.py tool_create` 让它读本文件重新生成
3. 若各技能路标也变了，同步那 3 份骨架文件

**源文件位置**：`C:\D\opt\openmem\MANUAL.md`（唯一真源，git 可追溯）
**技能路标副本**：`~/.workbuddy/skills/openmem-memory/SKILL.md`、`~/.dsh/skills/dsh-ops-memory/SKILL.md`、`agents-to-feishu/skills-market/memory-ops/SKILL.md`
