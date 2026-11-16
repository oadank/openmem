-- openmem 记忆中枢 schema (P0)
-- 独立库 openmem @ postgresql-x64-15，不碰 wiki 库

-- 核心记忆表：k=知识(Knowledge) m=记忆(Memory)
CREATE TABLE IF NOT EXISTS memory_entries (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  layer            text NOT NULL CHECK (layer IN ('k','m')),
  category         text NOT NULL DEFAULT 'uncategorized',  -- l0全局/l1项目/l2坑库/l3原文
  source           text NOT NULL,                          -- 哪个 agent 写的（强制）
  content          text NOT NULL,
  embedding        real[512],                              -- 本机 BGE bge-small-zh-v1.5
  created_at       timestamptz DEFAULT now(),
  updated_at       timestamptz DEFAULT now(),
  last_verified_at timestamptz DEFAULT now(),
  ttl              interval,                               -- NULL=不过期
  confidence       real DEFAULT 0.5,                       -- 0..1
  pinned           boolean DEFAULT false,                  -- 关键事实钉住，免疫自动归档
  superseded_by    uuid REFERENCES memory_entries(id),     -- 冲突归档链：新赢旧归档
  tags             text[] DEFAULT '{}',
  content_hash     text                                     -- md5(content)，导入/写入去重
);
CREATE UNIQUE INDEX IF NOT EXISTS memory_entries_src_hash_idx ON memory_entries(source, content_hash);
CREATE INDEX IF NOT EXISTS memory_entries_layer_idx   ON memory_entries(layer);
CREATE INDEX IF NOT EXISTS memory_entries_source_idx  ON memory_entries(source);
CREATE INDEX IF NOT EXISTS memory_entries_created_idx ON memory_entries(created_at);
CREATE INDEX IF NOT EXISTS memory_entries_category_idx ON memory_entries(category);
CREATE INDEX IF NOT EXISTS memory_entries_tags_idx    ON memory_entries USING gin(tags);

-- 冲突待审表（governor 治理产出）
CREATE TABLE IF NOT EXISTS memory_conflicts (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  a_id       uuid REFERENCES memory_entries(id),
  b_id       uuid REFERENCES memory_entries(id),
  reason     text,
  status     text DEFAULT 'pending' CHECK (status IN ('pending','resolved','ignored')),
  created_at timestamptz DEFAULT now()
);

-- 预生成答案工具表：标准化提示词，后台提前备好最新答案，调用即取（解决 AI 对 AI 慢）
CREATE TABLE IF NOT EXISTS preset_tools (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name             text UNIQUE NOT NULL,          -- 如「主人的喜好」
  description      text,
  prompt_template  text NOT NULL,                 -- 标准化提示词
  cached_answer    text,                          -- 预生成答案（最新最准）
  answer_updated_at timestamptz,
  refresh_interval interval DEFAULT '1 day',      -- 答案保鲜周期
  enabled          boolean DEFAULT true,
  created_at       timestamptz DEFAULT now()
);

-- 咨询问答日志（谁问过什么、答案引用了哪些记忆）
CREATE TABLE IF NOT EXISTS ask_log (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  requester  text NOT NULL,                       -- 来问的 agent
  query      text NOT NULL,
  answer     text,
  refs       jsonb DEFAULT '[]',                  -- 引用的 memory_entries id
  created_at timestamptz DEFAULT now(),
  session_id text                                  -- 多轮会话 id（Web 追问归组用；bot 单问为 NULL）
);

CREATE TABLE IF NOT EXISTS app_config (
  key        text PRIMARY KEY,                    -- persona / ask_header / model / temperature / max_tokens
  value      jsonb NOT NULL,
  updated_at timestamptz DEFAULT now()
);

-- 提醒表（2026-11-16）：跨 agent 共享的结构化待办，与记忆池分池（不进检索、不参与查重）
-- 用法：mh_reminder_set 记 → mh_tool("到期提醒") 查（status=active 且 due_date<=今天）→ mh_reminder_done 关
CREATE TABLE IF NOT EXISTS reminders (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title        text NOT NULL,
  detail       text,
  due_date     date NOT NULL,
  repeat       text CHECK (repeat IN ('once','weekly','monthly','yearly')),
  status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','done','cancelled')),
  created_by   text NOT NULL,                    -- 建这条的 agent 名
  created_at   timestamptz DEFAULT now(),
  done_at      timestamptz,
  advance_days integer NOT NULL DEFAULT 0,       -- 提前多少天开始提醒（0=到期当天）
  parent_id    uuid REFERENCES reminders(id)     -- repeat 自动排出的下一期指回上一条
);
CREATE INDEX IF NOT EXISTS reminders_status_due_idx ON reminders(status, due_date);

-- 已通知台账：UNIQUE(提醒, 通道, 日期) → 同一天同一通道同一条只响一次（2026-11-16）
CREATE TABLE IF NOT EXISTS reminder_notices (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  reminder_id uuid NOT NULL,
  channel     text NOT NULL,                     -- chat:<会话> / mcp:<agent> / feishu
  agent       text,
  notice_date date NOT NULL,
  created_at  timestamptz DEFAULT now(),
  UNIQUE(reminder_id, channel, notice_date)
);
