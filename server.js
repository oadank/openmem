#!/usr/bin/env node
/**
 * openmem 记忆中枢 MCP Server
 * 老大的统一记忆：一个库（PostgreSQL openmem），一处写，全体读
 *
 * 用法:
 *   node server.js                 # stdio 模式
 *   node server.js --http 3466     # HTTP 模式（nssm 服务用）
 *
 * 工具:
 *   mh_write   写入记忆/知识（强制 source）
 *   mh_update  按 id 原地更新一条记忆（保留 id，重算向量）
 *   mh_search  混合检索（向量+关键词过滤+时间，pinned 加权）
 *   mh_service 查 nssm 服务台账（按服务名精确匹配）
 *   mh_get     按 id 取单条
 *   mh_ask     AI 对 AI 咨询：像问老大本人一样，给完整准确答案（litellm GwV4F）
 *   mh_tool    预生成答案工具：标准化提示词，直接拿最新备好的答案（秒回）
 *   mh_skill   写入规矩（写入闸门第 1 关的准入入口；默认只给核心版，detail=full 才给手册）
 *   mh_tools_list / mh_status
 */

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { z } from 'zod';
import { spawn } from 'child_process';
import { createServer } from 'http';
import { existsSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 本地 .env（gitignore）：OPENMEM_PYTHON 等
try {
  const envPath = join(__dirname, '.env');
  if (existsSync(envPath)) {
    for (const line of readFileSync(envPath, 'utf8').split(/\r?\n/)) {
      const t = line.trim();
      if (!t || t.startsWith('#') || !t.includes('=')) continue;
      const i = t.indexOf('=');
      const k = t.slice(0, i).trim();
      const v = t.slice(i + 1).trim().replace(/^["']|["']$/g, '');
      if (k && !(k in process.env)) process.env[k] = v;
    }
  }
} catch { /* ignore */ }

const args = process.argv.slice(2);
const httpPortIdx = args.indexOf('--http');
const HTTP_PORT = httpPortIdx >= 0 ? parseInt(args[httpPortIdx + 1], 10) : null;

const PYTHON = process.env.OPENMEM_PYTHON || 'python';
const CORE = join(__dirname, 'mem_core.py');
if (!existsSync(CORE)) { console.error(`[openmem] 内核缺失: ${CORE}`); process.exit(1); }

function runCore(subArgs, timeout = 120000) {
  return new Promise((resolve, reject) => {
    const proc = spawn(PYTHON, [CORE, ...subArgs], {
      cwd: __dirname,
      timeout,
      env: { ...process.env, PYTHONUTF8: '1' }
    });
    let stdout = '', stderr = '';
    proc.stdout.on('data', d => { stdout += d; });
    proc.stderr.on('data', d => { stderr += d; });
    proc.on('close', code => {
      if (code === 0) resolve(stdout);
      else reject(new Error(stderr.trim() || `mem_core exit ${code}`));
    });
    proc.on('error', reject);
  });
}

function parseCore(raw) {
  try { return JSON.parse(raw); }
  catch { throw new Error(`内核输出异常: ${raw.slice(0, 300)}`); }
}

const T = (text, isError = false) => ({ content: [{ type: 'text', text }], ...(isError ? { isError } : {}) });
const J = (obj) => T(JSON.stringify(obj, null, 2));

function createOpenmemServer() {
  const s = new McpServer({ name: 'openmem', version: '0.1.0' });

  s.tool('mh_write', '写入一条记忆/知识到老大的统一记忆中枢（openmem）。source 必填=写入方 agent 名。layer: k=知识 m=记忆。category: rules/facts/projects/lessons/knowledge/archive/verification/services/capabilities/misc（或项目名如 dsh / agents-to-feishu）。重要结论不写入=任务不算完成。',
    {
      content: z.string().min(1).max(20000).describe('记忆内容正文'),
      source: z.string().min(1).max(100).describe('写入方 agent 名，如 codex / dsh / WorkBuddy'),
      layer: z.enum(['k', 'm']).default('k').describe('k=知识 m=记忆'),
      category: z.string().max(100).default('misc').describe('rules/facts/projects/lessons/knowledge/archive/verification/services/capabilities/misc，或项目名如 dsh / agents-to-feishu'),
      tags: z.array(z.string()).default([]).describe('标签列表'),
      confidence: z.number().min(0).max(1).default(0.5),
      pinned: z.boolean().default(false).describe('关键事实钉住，免疫自动归档')
    },
    async (p) => {
      try {
        if (/^agentmemory-insights$/i.test(p.source || ''))
          return T('拒绝写入：agentmemory 总结管线的自动产出（insights 抽象方法论）不收进 openmem——会污染检索，老大已定性。有具体上下文的 lessons 可以用真实 agent 名写。', true);
        const a = ['write', '--content', p.content, '--source', p.source,
          '--layer', p.layer, '--category', p.category,
          '--tags', (p.tags || []).join(','), '--confidence', String(p.confidence)];
        if (p.pinned) a.push('--pinned');
        return J(parseCore(await runCore(a)));
      } catch (e) { return T(`写入失败: ${e.message}`, true); }
    });

  s.tool('mh_update', '按 id **原地更新**一条记忆（改错字、补内容、转 pinned、换 category 都用这个）。只改传了的字段，未传的保持不动；改了 content 会自动重算向量与 hash。**保留 id**，不打断 superseded_by / 冲突记录引用链——优于"删了重写"。改内容前建议先 mh_get 看一眼原文。',
    {
      id: z.string().uuid().describe('要更新的条目 id'),
      content: z.string().min(1).max(20000).optional().describe('新内容（给了就整条替换并重算向量）'),
      layer: z.enum(['k', 'm']).optional(),
      category: z.string().max(100).optional(),
      source: z.string().max(100).optional(),
      tags: z.array(z.string()).optional().describe('给了就整组替换，不给则不动'),
      confidence: z.number().min(0).max(1).optional(),
      pinned: z.boolean().optional().describe('true/false；不给则不动')
    },
    async (p) => {
      try {
        const a = ['update', '--id', p.id];
        if (p.content) a.push('--content', p.content);
        for (const k of ['layer', 'category', 'source']) if (p[k]) a.push(`--${k}`, p[k]);
        if (Array.isArray(p.tags)) a.push('--tags', p.tags.join(','));
        if (typeof p.confidence === 'number') a.push('--confidence', String(p.confidence));
        if (typeof p.pinned === 'boolean') a.push('--pinned', String(p.pinned));
        return J(parseCore(await runCore(a)));
      } catch (e) { return T(`更新失败: ${e.message}`, true); }
    });

  s.tool('mh_search', '检索老大的统一记忆，返回**原始条目**（适合查历史细节、具体事件、某个坑的完整经过）。混合检索：向量语义 + 元数据过滤(layer/category/source/tags/时间)。⚠️ 但「老大是什么人 / 什么偏好 / 本机环境 / 服务与端口 / 某项目怎么改」这类**标准问题请先试 mh_tools_list → mh_tool**（成品答案，秒回、后台已保鲜），不要直接搜、更不要猜。注意：nssm 服务台账（agent-matrix/services）默认不返回，要查某服务的启动参数/端口用 source="agent-matrix" 显式指定。🔴 硬规矩：访问 GitHub 或任何需凭据/代理的外部 API 前，必须先在此搜访问方法（如 query="GitHub 凭据"）再动手；报限流/403 先怀疑没走通道，禁止空手重试。',
    {
      query: z.string().min(1).max(2000).describe('查询问题或关键词'),
      layer: z.enum(['k', 'm']).optional(),
      category: z.string().optional(),
      source: z.string().optional(),
      tags: z.array(z.string()).default([]),
      since: z.string().optional().describe('ISO 日期，如 2026-09-01'),
      top_k: z.number().min(1).max(50).default(10),
      include_archived: z.boolean().default(false).describe('含已归档旧版本'),
      requester: z.string().max(100).optional().describe('你自己的 agent 名（留痕治理用，可不填）')
    },
    async (p) => {
      try {
        const a = ['search', '--query', p.query, '--top_k', String(p.top_k),
          '--tags', (p.tags || []).join(','), '--requester', String(p.requester || 'mcp-client')];
        for (const k of ['layer', 'category', 'source', 'since']) if (p[k]) a.push(`--${k}`, p[k]);
        if (p.include_archived) a.push('--include_archived');
        return J(parseCore(await runCore(a)));
      } catch (e) { return T(`检索失败: ${e.message}`, true); }
    });

  s.tool('mh_service', '查本机 nssm 服务台账：按服务名返回该服务的启动参数/端口/路径等配置明细（不走语义检索，按名精确匹配）。不知道确切名字就先不传 name，会列出全部服务名。',
    { name: z.string().optional().describe('服务名关键词，如 dsh / claude / visionqa；留空列出全部服务名') },
    async (p) => {
      try {
        const a = ['service'];
        if (p.name) a.push('--name', p.name);
        return J(parseCore(await runCore(a)));
      } catch (e) { return T(`查服务台账失败: ${e.message}`, true); }
    });

  s.tool('mh_get', '按 id 取单条记忆完整内容。',
    { id: z.string().uuid() },
    async (p) => {
      try { return J(parseCore(await runCore(['get', '--id', p.id]))); }
      catch (e) { return T(`读取失败: ${e.message}`, true); }
    });

  s.tool('mh_ask', 'AI 对 AI 咨询：像直接问老大陈丹本人一样提问，openmem 基于全部记忆给出完整、准确、口语化的答案（走 litellm GwV4F，**数秒级，较慢**）。**只在 mh_tool 里没有对应成品答案、且问题需要理解+综合时才用**：要标准答案走 mh_tool（秒回），要原始记忆片段走 mh_search（更快）。',
    {
      query: z.string().min(1).max(4000).describe('要问老大/问记忆的问题'),
      agent: z.string().max(100).default('unknown-agent').describe('提问方 agent 名')
    },
    async (p) => {
      try {
        return J(parseCore(await runCore(['ask', '--query', p.query, '--agent', p.agent], 240000)));
      } catch (e) { return T(`咨询失败: ${e.message}`, true); }
    });

  s.tool('mh_skill', '【必须先调用】openmem 写入规矩 —— 往 openmem 写记忆（mh_write）前的准入门槛。返回**核心写入规矩**（一页准入条文：写入四步 / 三道闸门 / 一句话原则），不走 LLM、秒回。**没有"完整手册"这回事**（MANUAL.md 已于 2026-09-18 删除 —— 该留的都塞进了各 agent 的 openmem 技能文件）。⚠️ 被写入闸门第 1 关拦下时，调用本工具（**带 agent**）即可放行 —— 不带 agent 不计入你名下、会继续被拦。**票制：领一次只放行一条 —— 写成功这条票就作废，下次再写还得回来再领一次。**',
    {
      agent: z.string().max(100).optional().describe('你自己的 agent 名（如 claude / codex / mimo / workbuddy / dsh）。**必填** —— 闸门靠它记账；不填则第 1 关会继续拦你。')
    },
    async (p) => {
      try {
        const a = ['skill', '--agent', p.agent || ''];
        return J(parseCore(await runCore(a)));
      } catch (e) { return T(`领规矩失败: ${e.message}`, true); }
    });

  s.tool('mh_tool', '【首选 · 秒回】调用预生成答案工具：标准化提示词 + 后台已备好最新最准的答案，**不走 LLM、几乎零等待**。凡「老大是什么人 / 偏好习惯 / 铁律清单 / 本机环境 / 服务与端口 / 12 bot 花名册 / 项目索引」这类**标准问题一律先用这个**，别用 mh_search 现搜、更别凭印象猜。不知道有哪些成品答案就先调 mh_tools_list（一次看清全部 + 新鲜度）。⚠️ 写入规矩请用 mh_skill，别在这里领。',
    {
      name: z.string().min(1).describe('工具名，如 主人的喜好'),
      agent: z.string().max(100).optional().describe('你自己的 agent 名（如 claude / codex / mimo），选填。'),
      force_refresh: z.boolean().default(false).describe('强制重新生成')
    },
    async (p) => {
      try {
        const a = ['tool_call', '--name', p.name];
        if (p.agent) a.push('--agent', p.agent);
        if (p.force_refresh) a.push('--force_refresh');
        return J(parseCore(await runCore(a, 240000)));
      } catch (e) { return T(`工具调用失败: ${e.message}`, true); }
    });

  s.tool('mh_tools_list', '列出全部预生成答案工具及答案新鲜度。', {},
    async () => {
      try { return J(parseCore(await runCore(['tools_list']))); }
      catch (e) { return T(`失败: ${e.message}`, true); }
    });

  s.tool('mh_status', 'openmem 健康状态：总条数/钉住数/归档数/待审冲突/工具数/咨询次数。', {},
    async () => {
      try {
        const raw = await runCore(['status']);
        const metrics = {};
        for (const line of raw.trim().split('\n').filter(Boolean)) {
          const o = JSON.parse(line); metrics[o.metric] = o.value;
        }
        return J({ ok: true, ...metrics });
      } catch (e) { return T(`失败: ${e.message}`, true); }
    });

  return s;
}

// 预设工具保鲜（2026-09-11 第一档优化）：后台按 refresh_interval 自动刷新过期缓存，
// 解决"隔天第一次调用要现场重算、慢十几秒"的问题。只刷过期的，不浪费 LLM 调用。
function startToolRefresher() {
  const TICK_MS = 30 * 60 * 1000; // 每 30 分钟扫一次
  const run = async () => {
    try {
      const raw = await runCore(['tool_refresh', '--expired_only'], 900000);
      console.error(`[openmem] 预设工具保鲜: ${raw.trim().slice(0, 300)}`);
    } catch (e) {
      console.error(`[openmem] 预设工具保鲜失败: ${e.message}`);
    }
  };
  setTimeout(run, 60 * 1000);
  const timer = setInterval(run, TICK_MS);
  if (timer.unref) timer.unref();
  console.error('[openmem] 预设工具保鲜线程已启动（每 30 分钟检查过期）');
}

async function main() {
  startToolRefresher();
  if (HTTP_PORT) {
    const httpServer = createServer(async (req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*');
      res.setHeader('Access-Control-Allow-Methods', 'POST, GET, OPTIONS');
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Mcp-Session-Id');
      if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
      if (req.url !== '/mcp' && !req.url.startsWith('/mcp/')) {
        res.writeHead(404, { 'Content-Type': 'text/plain' });
        res.end('Not Found. Use POST /mcp'); return;
      }
      let body = '';
      for await (const chunk of req) body += chunk;
      let parsedBody;
      try { parsedBody = JSON.parse(body); } catch { parsedBody = undefined; }

      const requestServer = createOpenmemServer();
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
      await requestServer.connect(transport);

      // 补全 Accept header（兼容不发送 text/event-stream 的客户端，同 wiki）
      const origRawHeaders = req.rawHeaders;
      const hasAccept = origRawHeaders.some((h, i) => i % 2 === 0 && h.toLowerCase() === 'accept');
      const acceptVals = origRawHeaders.filter((h, i) => i % 2 === 1).join(',');
      if (!hasAccept || !acceptVals.includes('text/event-stream') || !acceptVals.includes('application/json')) {
        Object.defineProperty(req, 'rawHeaders', {
          get() {
            const filtered = [];
            for (let i = 0; i < origRawHeaders.length; i += 2) {
              if (origRawHeaders[i].toLowerCase() === 'accept') continue;
              filtered.push(origRawHeaders[i], origRawHeaders[i + 1]);
            }
            filtered.push('Accept', 'application/json, text/event-stream');
            return filtered;
          }, configurable: true
        });
      }
      await transport.handleRequest(req, res, parsedBody);
    });
    httpServer.listen(HTTP_PORT, '0.0.0.0', () => {
      console.error(`[openmem] HTTP 模式启动: http://0.0.0.0:${HTTP_PORT}/mcp`);
    });
  } else {
    const transport = new StdioServerTransport();
    await createOpenmemServer().connect(transport);
    console.error('[openmem] stdio 模式启动');
  }
}

main().catch(e => { console.error('[openmem fatal]', e); process.exit(1); });
