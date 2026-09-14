# openmem — 老大的统一记忆中枢

> 一个库（PostgreSQL `openmem`），一处写，全体读。AI 对 AI 咨询 + 预生成答案工具。
> 2026-09-08 P0 建成。命名来自 open* 家族（openclaw/openakita/openmem）。

## 密钥与本地文件（勿提交）

源码不写死密钥。本机真值放这些 **gitignore** 文件：

| 文件 | 内容 |
|---|---|
| `.env` | PG / LLM / embedding / Python 路径等（模板见 `.env.example`） |
| `persona.local.txt` | 老大画像（mem_core 优先读它） |
| `cred_guard_local.py` | 打码用的短口令字面量 |
| `oc-test-config.json` | 测试用 API key 配置 |

## 定位

老大陈丹（阿丹）的**真源记忆**：不是工具，是「记忆本身」。其他 agent 需要记忆时有两种姿势：

1. **mh_ask（AI 对 AI 咨询）**：像问老大本人一样提问，openmem 基于全部记忆 + 老大画像，给出完整、准确、口语化的答案（走 litellm GwV4F）。准确但不求毫秒级。
2. **mh_tool（预生成答案）**：标准化提示词（如「主人的喜好」），后台提前用最新记忆备好答案，调用秒回（<1s），答案按 refresh_interval 保鲜。

## 基础设施

| 项 | 值 |
|---|---|
| 安装目录 | `C:\D\opt\openmem` |
| 服务 | nssm `openmem`（Automatic）→ `node server.js --http 3466` |
| MCP 端点 | `http://localhost:3466/mcp`（Streamable HTTP，stateless，同 wiki 传输层） |
| 存储 | PostgreSQL `postgresql-x64-15`，独立库 `openmem`（不碰 wiki 库） |
| Embedding | 本机 BGE `http://localhost:11435/v1/embeddings`（bge-small-zh-v1.5, 512d） |
| 咨询 LLM | litellm `http://localhost:4000/v1`，key 走 `OPENMEM_LLM_KEY`（见 `.env.example`） |
| Python 内核 | `mem_core.py`（Python312，psycopg2/requests/numpy） |

## MCP 工具（7 个）

| 工具 | 作用 |
|---|---|
| `mh_write` | 写入记忆（source 强制；layer k/m；category l0全局/l1项目/l2坑库/l3原文） |
| `mh_search` | 混合检索：向量语义 + layer/category/source/tags/时间过滤 |
| `mh_get` | 按 id 取单条 |
| `mh_ask` | 【核心】AI 对 AI 咨询，以老大口吻给完整答案，带 REF 引用 |
| `mh_tool` | 调预生成答案工具（秒回） |
| `mh_tools_list` | 列出预设工具与答案新鲜度 |
| `mh_status` | 健康：总条数/钉住/归档/冲突/工具/咨询次数 |

## bot 接入（agents-to-feishu 等 MCP 客户端）

```json
{ "mcpServers": { "openmem": { "url": "http://localhost:3466/mcp" } } }
```

stdio 方式（Claude Code / Codex / Cursor 同 wiki 写法）：

```json
{ "command": "node", "args": ["C:\\D\\opt\\openmem\\server.js"] }
```

## 写入纪律（铁律）

重要结论**不写回就不算任务完成**。每个 agent 干完活调 `mh_write`：

```
mh_write(content="…结论/坑/事实…", source="<你的agent名>", layer="m",
         category="l2坑库", tags=["litellm","restart"], confidence=0.8)
```

- 冲突仲裁：同事实新版本写入后，governor（P2）把旧版 `superseded_by` 指向新版，归档可回溯。
- `pinned=true`：关键事实人工钉住，免疫自动归档。

## 数据表

- `memory_entries`：id/layer/category/source/content/embedding real[512]/created_at/updated_at/last_verified_at/ttl/confidence/pinned/superseded_by/tags/content_hash（source+hash 唯一去重）
- `memory_conflicts`：治理待审
- `preset_tools`：预生成答案（name/prompt_template/cached_answer/refresh_interval）
- `ask_log`：咨询记录（谁问了什么、答案引用了哪些记忆）

## 已导入记忆源

| 源 | 条数 | source 标识 | 状态 |
|---|---|---|---|
| wiki vault (agent-wiki-mcp) | 3246 | `wiki` | ⛔ 2026-09-12 源已下线删除，内容已并入本库 |
| agentmemory（memories+lessons+insights 全量导出） | 1086 | `agentmemory` / `agentmemory-lessons` / `agentmemory-insights` | ⛔ 2026-09-12 源已下线删除，内容已并入本库 |
| WorkBuddy（用户级+工作区日志） | 22 | `WorkBuddy` | ✅ 活跃 |
| agents-memory（各 agent 身份/记忆文件） | 28 | `agents-memory:<agent>` | ✅ 活跃 |

> **2026-09-12**：wiki(:3456) 与 agentmemory(:3114) 已整体下线删除，**openmem 成为唯一记忆工具**。
> 现存增量同步来源只剩 **WorkBuddy 记忆 + agents-memory** 两个（`import_sources.py`）。
> 备份在 `C:\D\opt\_backup\`（`agent-wiki-mcp_20260912-120852.tar.gz` / `agentmemory_20260912-120852.tar.gz` / `wiki_db_20260912-120852.sql`）。

## 运维

```bash
nssm status openmem        # 看状态
nssm restart openmem       # 改 server.js 后重启
# 内核直测（不经 MCP）：
python mem_core.py write --content "..." --source WorkBuddy --layer m --category l2坑库
python mem_core.py search --query "..." --top_k 5
python mem_core.py ask --query "..." --agent codex
python mem_core.py tool_refresh            # 保鲜全部预设工具答案
python mem_core.py import_batch --file x.json   # 批量导入
```

## Web 控制台（:3467，nssm 服务 `openmem-web`）

`http://localhost:3467`（Tailscale 网内可达，绑定 0.0.0.0）。五大功能：

| 页签 | 功能 |
|---|---|
| 🔍 检索 | 语义向量检索 + **命中词高亮**，结果卡片带**编辑/删除**按钮，点卡片看全文 |
| 📚 浏览 | 分页浏览全部记忆，分类/来源 facets 过滤 + 关键词 ILIKE + 高亮，同带编辑/删除 |
| 💬 问问中枢 | **Perplexity 式**：居中大搜索框 → **流式输出**（打字机）→ **引用记忆卡（可点看原文）** → **追问建议** → 历史回看 |
| ⚡ 预设工具 | 查看每个工具的现成答案（缓存秒回）+ 一键刷新重新生成 |
| 📦 入职包 | 新 agent 一条命令拉全，可复制 Markdown |
| ⚙️ AI 设置 | 中枢 AI 的 system prompt（回答规则 + 老大画像）与模型/温度/max_tokens 可视化编辑，存 `app_config` 表，**保存即对 Web + 11 bot 的 mh_ask 生效**（无需重启），可一键恢复内置默认 |

- **状态持久化**：搜索结果 / 浏览状态 / 上次问答 / 当前页签存 localStorage，刷新、切标签不丢
- 编辑记忆自动重算 embedding；删除自动清理 superseded 引用与关联冲突记录

- 文件：`web_server.py`（stdlib http.server，直接复用 mem_core 内核函数）+ `web_page.html`（深色单页）
- 运维：`nssm status/restart openmem-web`；日志 `web-out.log` / `web-err.log`
- API：`/api/stats /api/search /api/browse /api/entry(DELETE) /api/entry/edit(POST) /api/ask_stream(POST, SSE流式) /api/history /api/history/detail /api/refs /api/tools /api/tool/answer /api/tool/refresh(POST) /api/onboarding /api/config(GET/POST) /api/config/reset(POST) /api/health`

## 设置页能力

- **system prompt 可视化编辑**：回答规则 + 老大画像两块，入库 `app_config`，保存即对 Web + 11 bot 的 mh_ask 生效（mem_core `build_system_prompt` 动态组装）
- **模型配置**：网关 Base URL（OpenAI 兼容，自动拼 /v1/chat/completions）+ API Key + 模型名（「拉取模型列表」从网关 /v1/models 取，datalist 下拉也可手填）
- **agent-matrix 自动同步**：`import_agentmatrix.sync()` 对 lecoo.md/README.md/SERVICES.md/capabilities.json 算 md5 指纹存 `app_config.agentmatrix_hash`；web_server 后台线程每 5 分钟检测，变了删旧（source='agent-matrix'）全量重导；设置页可手动「立即全量重导」。注意 `config/reset` 只清运行配置、保留指纹

## 路线图

- P1：飞书 11 bot 全部接 MCP；写入闸门（重要结论强制写回）
- P2：governor 治理循环（去重/冲突/过期，决策 LLM=本机 Ollama :11434）
- P3：入职包（新 agent 一条命令拉全）+ Cumora CLI/文件接入
