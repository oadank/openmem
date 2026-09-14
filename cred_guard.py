# -*- coding: utf-8 -*-
"""openmem 凭据打码闸（2026-09-12，老大拍板「密码改为打码成位置指引」）。

作用：任何进入 memory_entries.content 的文本，先经过 sanitize_credentials()，
把凭据**值**替换为「位置指引」占位（保留用途/环境变量名/存放点），embedding 与
content_hash 都用打码后的文本算。这是防止每天 04:00 自动同步把 ~/.workbuddy/MEMORY.md
里的明文凭据重新灌回库的治本闭环。

设计约束：
- 保守正则：各前缀要求高熵长串，绝不碰 ark-code-latest / whitequark-ipaddr /
  sk-local-placeholder 这类模型别名或假占位。
- 幂等：占位文案本身不匹配任何规则（含 < > ｜ 等字符），二次调用 hits=0。
- 只遮值，不遮变量名/环境变量名——bot 查记忆仍知道「去哪找、怎么用」。
接入点：mem_core.cmd_write / mem_core.cmd_import_batch /
       web_server.api_entry_update / web_server.api_memory_absorb。
"""
import re

# ── 1) 精确上下文替换（短弱口令无高熵形态）──
# 公开仓只放空表；真口令写在 cred_guard_local.py（gitignore），不进 git
LITERAL_MAP = []
try:
    from cred_guard_local import LITERAL_MAP as _LOCAL_LITERALS  # type: ignore
    LITERAL_MAP = list(_LOCAL_LITERALS)
except Exception:
    pass
# 注：本机 LiteLLM 网关 master key（见 .env 的 OPENMEM_LLM_KEY）**不打码**——
# 低危本地 key、多处配置在用；写进闸会造成与库里现存条目 hash 漂移。

# ── 2) 高熵 token 正则（2026-09-12 实测：8 个真 key 全命中，6 个伪凭据全排除）──
TOKEN_PATTERNS = [
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}'),          # GitHub PAT
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{30,}'),        # GitHub fine-grained
    re.compile(r'\bnpm_[A-Za-z0-9]{30,}'),                # npm
    # sk- 要求前缀后再跟 >=24 字符且不以连字符起头；排除显式假占位
    re.compile(r'\bsk-(?!local-|placeholder)[A-Za-z0-9][A-Za-z0-9\-\.]{22,}[A-Za-z0-9]'),
    re.compile(r'\bsta_[A-Za-z0-9]{20,}[0-9A-Fa-f][A-Za-z0-9]{4,}'),  # FreeTheAi
    re.compile(r'\bas_sk_[A-Za-z0-9]{24,}'),              # AnySearch
    re.compile(r'\bark-[0-9a-f]{28,}'),                   # 火山方舟（只认纯 hex 长串）
]
TOKEN_PLACEHOLDER = '<已打码凭据｜真值在 ~/.workbuddy/MEMORY.md 凭据段或对应服务配置/环境变量>'

# ── 3) URL 内嵌口令：scheme://user:pass@ 只遮密码、保留 host/db ──
URL_CRED_RE = re.compile(r'(://[^:/@\s\*]{1,80}):(?!<|\*\*\*)([^@/\s\*|`]{4,200})@')
URL_CRED_REP = r'\1:<口令见 ~/.workbuddy/MEMORY.md 凭据段>@'


def sanitize_credentials(text):
    """→ (打码后文本, 命中数)。无命中时返回原文本。幂等。"""
    if not text:
        return text, 0
    hits = 0
    for k, v in LITERAL_MAP:
        if k in text:
            hits += text.count(k)
            text = text.replace(k, v)
    text, n = URL_CRED_RE.subn(URL_CRED_REP, text)
    hits += n
    for rx in TOKEN_PATTERNS:
        text, n = rx.subn(TOKEN_PLACEHOLDER, text)
        hits += n
    return text, hits
