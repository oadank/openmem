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
