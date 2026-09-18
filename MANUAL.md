# openmem 使用手册

> 主人与全体 agent 的**唯一记忆源**。技能文件只是路标，真源在这里。
> **干活前先查，干完写回。不写回 = 任务没完成。**

## 1. 是什么 / 在哪

一个 PG 库（`openmem`），一处写、全体读。
旧 `agentmemory`(:3111/:3114) 与 `wiki`(:3456) **已于 2026-09-12 删除并入本库，没有第二个记忆工具。**

| 项 | 值 |
|---|---|
| MCP（agent 用） | `http://127.0.0.1:3466/mcp`（streamable-http，无鉴权） |
| Web 控制台（人看） | `http://127.0.0.1:3467` |
| nssm 服务 | `openmem`(:3466) / `openmem-web`(:3467) |
| 内核 | `C:\D\opt\openmem\`：`server.js` + `mem_core.py` + `web_server.py` |
| PG | `127.0.0.1/openmem`，`user=postgres` 无密码；表 `memory_entries` / `preset_tools` / `app_config` / `ask_log` |

🔴 地址一律 `127.0.0.1`，**禁 `localhost`**（Win 先解析 ::1，实测慢 57 倍）。
挂 MCP 后**必须重启宿主进程**才有原生 `mh_*` 工具。

## 2. 工具：什么时候用哪个

按快慢排序，**别一上来就 `mh_ask`**。

| 工具 | 耗时 | 用途 |
|---|---|---|
| `mh_tools_list` | 秒 | **第一步**：看有哪些成品答案 |
| `mh_tool(name=…)` | 0.3s | 高频固化问题（老大是啥人 / 端口 / 铁律）。⚠️ 参数是 **name** |
| `mh_service(name=…)` | 秒 | nssm 服务台账（启动参数 / 端口 / 路径），精确匹配 |
| `mh_search(query, top_k)` | 3s | 原始条目、找细节。⚠️ **是 `top_k` 不是 `limit`** |
| `mh_get(id)` | 秒 | 已知 id 取全文 |
| `mh_ask(query, agent)` | 10s | AI 对 AI 要一段完整答案，**最后手段** |
| `mh_skill(agent=…)` | 秒 | **写记忆前先领规矩**（写入闸门第 1 关的入口）。默认只给核心规矩，`detail="full"` 才给手册 |
| `mh_write(content, source)` | 秒 | 收工写回，`source` 必填 |
| `mh_update(id, …)` | 秒 | 改已有条目，**保留 id**，优于删了重写 |
| `mh_status` | 秒 | 总数 / pinned / 冲突 |

**标准顺序**：`mh_tools_list` → `mh_tool` → `mh_service` / `mh_search` → 都不行才 `mh_ask`。

## 3. 写入规范

| 字段 | 要求 |
|---|---|
| `content` | 必填 |
| `source` | **必填 = 自己的 agent 名**（`workbuddy` / `dsh` / …） |
| `layer` | `k` 知识 / `m` 记忆 |
| `category` | 18 类：10 通用（`rules` `facts` `projects` `lessons` `knowledge` `archive` `verification` `services` `capabilities` `misc`）+ 项目名。旧 `l0全局`/`l1项目`/`l2坑库`/`l3原文` **已作废** |
| `pinned` | 只给铁律，别乱加 |

**写前四步**：先搜 → 有则 `mh_update` → 无则 `mh_write` → 同主题只留一条活条目。

**硬规矩**
- 改内容用 `mh_update`；整条作废**直接删**，不搞归档：`curl -X DELETE "http://127.0.0.1:3467/api/entry?id=<uuid>"`
- 报条数只报**有效**数（`superseded_by IS NULL`）
- **凭据不进 openmem**（唯一例外：`~/.workbuddy/MEMORY.md` 凭据段）
- **记忆文件只写「规矩 + 路标」，不写会漂的硬事实**（IP / 端口 / 服务名 / token）——判断只一句：**它会不会变？会变就只进 openmem**
- 直连 PG 写库**必须同时写 `embedding_v vector(512)`**，否则新条目检索不到
- 🔴 **条目别超 ~500 字** —— 超了 embedding 会截断，**后半截永远搜不到**（已踩）

### 3.1 代码类知识：只存路标，不存代码

```
[文件] C:\D\opt\agents-to-feishu\src\config\render.ts:530
[符号] syncModelToCli(botId, model) : void
[作用] 一键切模型时把 model 写进该 bot 的 CLI 配置
[坑]   必须无条件调用，早期加了 if 判断 → dsh/deeptutor 不生效
[片段] （选填）只贴 5~15 行最难复现的
```
🔴 `[文件]` / `[符号]` **必须原样照抄** —— 它们是检索钥匙，翻译成「那个切模型的函数」就永远搜不到。
一条卡片一件事。**检索只负责定位，代码永远现读。**

## 4. 写入闸门（服务端强制）

> 信念：**全体 agent 都是 openmem 的共建者 —— 不能只拉屎不擦屁股。**
> 装在 `mem_core.py`（改完立即生效、不用重启），只拦 `mh_write`；**`mh_update` 一律放行**（那才是擦屁股）。

| 关 | 拦什么 | 怎么过 |
|---|---|---|
| ① 规矩关 | **你自己** 12h 内没领过写入规矩 | `mh_skill(agent="<你的名字>")` → 领完再写一次 |
| ② 查重关 | **你自己** 30min 内没搜过库 | `mh_search(…, requester="<你的名字>")` 后再写 |
| ③ 重复关 | 与已有条目相似度 ≥ 0.95 | 改用 `mh_update(id=…)` 合并 |

🔴 **领规矩 = 调独立工具 `mh_skill`**（不是 `mh_tool`）—— 它就是干这个的，秒回、不走 LLM：
- 默认只给**核心规矩**（`RULES.md`，≈800 字符 / ≈500 token）
- `detail="full"` 才附完整手册；`topic="闸门"` 按章节抽段
  —— 照 `win-desktop-helper` 的 `get_skill` 模式：**默认永远不是全量**。
- **必须带 `agent`**：领了才记到你名下；不带 = 白领，第 1 关继续拦。

🔴 **按 agent 各自记账** —— 别人领过、别人搜过**不算你的**。
🔴 **规矩 ≠ 手册**：规矩是准入条文（`RULES.md`）；本文件是**完整说明书（按需查，不是准入条件）**。
- ①关**不设豁免**，必须真领（领一次管 12 小时）；②③关每关只拦一次、豁免 7 天 —— 宁可少拦，**不能卡死写入**（写入被卡 = 记忆丢失，比污染更糟）。
- 写后回显：成功会带库里最像的 3 条；≥0.85 直接提示改用 `mh_update`。
- 查 / 清状态：`mem_core.py gate --status` / `gate --reset <source>` / `gate --reset_all`
- 覆盖范围：只拦走 MCP 的写入。HTTP 直调、`import_batch`、同步脚本不经过 → 不拦（同步管线不会被卡死）。

## 5. 常见坑

（完整清单按关键词 `openmem 坑` 搜库）

1. `mh_search` 没有 `limit`，参数是 **`top_k`**；传错报 `-32602`
2. **改条目不刷成品答案 = 白改**（`mh_tool` 是预生成缓存，独立于 `memory_entries`）
3. **长条目检索不到**：embedding 截断 ~500 字，写进去了也搜不出来
4. **符号类查询要把符号原样写进 query**（`syncModelToCli` / `18800`），别写大白话
5. `curl GET /mcp` 探活会挂死 3 分钟 —— 探活用 `sc query openmem`
6. Git Bash 的 `curl` 发中文 = GBK 乱码 —— 测中文用 Python `requests`
7. MCP 响应**显式 UTF-8 解码**：`r.content.decode('utf-8')`，**别用 `r.text`**
8. 直连 PG 查 uuid 列表要 `::uuid[]` 转型；改 `category`/`source` 记得同步 `import_sources.py`
9. `mh_ask` / `mh_tool` 全报 400 → 查 `app_config.model` 是否被改成非法值

## 6. 改完怎么生效

- **改 `mem_core.py`**：MCP 立即生效（每次调用 spawn 新 Python）；Web 端要 `nssm restart openmem-web`。
- **改本文件后必须刷成品答案**：
  ```bash
  mh_tool(name="openmem 使用手册", force_refresh=true)
  # 或
  curl -s -X POST http://127.0.0.1:3467/api/tool/refresh -H "Content-Type: application/json" -d '{"name":"openmem 使用手册"}'
  ```
- **改本文件的流程**：改 `MANUAL.md` → 全文写进 `preset_tools.cached_answer`（`name='openmem 使用手册'`）→ 技能路标若变，同步那 3 份。

**规矩正文另在** `RULES.md`（闸门①领的那份，精简准入条文）—— 别跟本文件混。
**技能路标（3 份）**：`~/.workbuddy/skills/openmem-memory/SKILL.md`、`~/.dsh/skills/dsh-ops-memory/SKILL.md`、`agents-to-feishu/skills-market/memory-ops/SKILL.md`

## 7. Web API（`:3467`，人 / 脚本用；agent 日常走 MCP）

`/api/ask` `/api/search` `/api/absorb`(写) `/api/entry/edit` `/api/entry`(DELETE) `/api/tool/refresh` `/api/tool/create` `/api/onboarding`(入职包) `/api/stats`
