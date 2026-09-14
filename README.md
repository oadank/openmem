# openmem

个人多 Agent 的**统一记忆中枢**：一个 PostgreSQL 库，一处写、全体读。

其他 AI 通过 MCP 接入；人通过 Web 控制台检索、问答、治理记忆。

## 它解决什么

| 问题 | openmem 的做法 |
|---|---|
| 记忆散落在多个 agent / 文件 | 统一进 `memory_entries`，带向量与元数据 |
| 标准问题每个 bot 都重推一遍 | `preset_tools` 预生成答案，秒回且可保鲜 |
| 需要「像问本人一样」的综合回答 | `mh_ask`：检索记忆 + 画像 → LLM 回答，带引用 |

## 架构

```text
MCP clients (Claude / Codex / bots / …)
        │
        ▼
   server.js  ──spawn──►  mem_core.py  ──►  PostgreSQL (openmem)
   :3466 MCP                    │
                                ├── Embeddings API（OpenAI 兼容）
                                └── Chat LLM API（OpenAI 兼容）
   web_server.py :3467  ────────┘
   （Web 控制台，复用同一内核）
```

- **`server.js`**：MCP 层（stdio / Streamable HTTP），工具转发到 Python 内核
- **`mem_core.py`**：业务内核 + CLI（写入、混合检索、咨询、预设工具、导入）
- **`web_server.py` + `web_page.html`**：Web 控制台
- **`cred_guard.py`**：写库前打码（防明文密钥进记忆）

## 数据表

| 表 | 用途 |
|---|---|
| `memory_entries` | 记忆正文 + embedding + pinned / superseded 链 |
| `memory_conflicts` | 治理待审冲突 |
| `preset_tools` | 预生成答案（prompt + 缓存 + 保鲜周期） |
| `ask_log` | 咨询日志与引用 |
| `app_config` | 运行配置（persona / 模型 / key 等，覆盖环境变量默认值） |

建库：

```bash
createdb openmem
psql openmem -f schema.sql
```

## 快速开始

### 1. 依赖

- Node.js 18+
- Python 3.10+：`psycopg2-binary`、`requests`（见 `requirements.txt`）
- PostgreSQL 14+
- OpenAI 兼容的 **embeddings** 与 **chat** 端点

```bash
npm install   # @modelcontextprotocol/sdk + zod（若未提供 package.json，按需安装）
python -m pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env：PG、LLM、embedding 等
```

进程启动时会自动读项目根目录的 `.env`（**不要提交**）。

可选本地文件（均 gitignore）：

| 文件 | 用途 |
|---|---|
| `persona.local.txt` | `mh_ask` 默认主人画像（Web 设置若已保存则以库为准） |
| `cred_guard_local.py` | 短口令精确替换表（公开仓 `cred_guard.py` 默认空表） |

### 3. 启动

```bash
# MCP over HTTP
node server.js --http 3466

# MCP over stdio
node server.js

# Web 控制台
python web_server.py --port 3467
```

## MCP 接入

HTTP：

```json
{ "mcpServers": { "openmem": { "url": "http://127.0.0.1:3466/mcp" } } }
```

stdio：

```json
{ "mcpServers": { "openmem": { "command": "node", "args": ["./server.js"] } } }
```

## MCP 工具

| 工具 | 作用 |
|---|---|
| `mh_write` | 写入记忆（`source` 必填；`layer` k/m） |
| `mh_update` | 按 id 原地更新（保留 id，重算向量） |
| `mh_search` | 混合检索（向量 + 元数据过滤） |
| `mh_get` | 按 id 取单条 |
| `mh_service` | 服务台账按名查询（若已导入 agent-matrix） |
| `mh_ask` | AI 对 AI 咨询，答案带 `REF` 引用 |
| `mh_tool` / `mh_tools_list` | 预生成答案（秒回） |
| `mh_status` | 健康与统计 |

**建议优先级**：标准问题 → `mh_tool` → 需要原始片段 → `mh_search` → 需要综合口吻 → `mh_ask`。

写入示例：

```text
mh_write(content="…结论/坑…", source="<agent名>", layer="m",
         category="lessons", tags=["…"], confidence=0.8)
```

## Web 控制台

默认 `http://127.0.0.1:3467`（**无鉴权**，勿对公网裸奔）。

| 页签 | 能力 |
|---|---|
| 检索 | 语义检索 + 高亮，可编辑/删除 |
| 浏览 | 分页 + 分类/来源过滤 |
| 问问 | 流式问答、引用卡片、历史 |
| 预设工具 | 查看/刷新缓存答案 |
| 入职包 | 导出新 agent 需要的钉住事实与工具清单 |
| AI 设置 | 提示词 / 模型 / key（写入 `app_config`，即时生效） |

主要 API：`/api/stats` `/api/search` `/api/browse` `/api/ask_stream` `/api/tools` `/api/config` `/api/health` 等。

## CLI（不经 MCP）

```bash
python mem_core.py write --content "..." --source me --layer m --category lessons
python mem_core.py search --query "..." --top_k 5
python mem_core.py ask --query "..." --agent mybot
python mem_core.py status
```

## 辅助脚本

| 文件 | 用途 |
|---|---|
| `import_sources.py` | 从本地 markdown 目录批量导入（路径请按环境改） |
| `import_agentmatrix.py` | 服务总表目录指纹同步导入 |
| `mcp_call.py` | 命令行调用 MCP 工具 |
| `audit_*.py` | 一次性审计/聚类辅助 |

## 配置优先级

对 AI 问答相关项（模型、key、persona 等）：

```text
数据库 app_config  >  环境变量 / .env  >  代码默认值
```

## 安全

- 密钥只放 `.env` 或服务环境变量，**不要写进源码或提交 git**
- `cred_guard` 是写入时的尽力打码，不能替代密钥治理
- Web / MCP 默认无鉴权：本机或反向代理 + 认证后再暴露

## License

按你的仓库选择（例如 MIT）。
