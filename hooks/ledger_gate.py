# -*- coding: utf-8 -*-
"""台账闸门：片子比错误台账新 = 改了片子没记账 -> 拒交付。
只拦 present_files / Write / Edit 和跑闸门、记账的 Bash；读文件与查记忆一律放行。
"""
import glob
import io
import json
import os
import sys

PROJ_ROOT = "C:" + chr(92) + "D" + chr(92) + chr(0x6f2b) + chr(0x5267)
LEDGER = chr(0x9519) + chr(0x8bef) + chr(0x53f0) + chr(0x8d26) + '.md'
FILM_PATS = (chr(0x6210) + chr(0x7247) + '*.mp4', 'merged_*.mp4',
             chr(0x6bcd) + chr(0x7248) + '*.mp4')
DENY = ('[台账闸门] 项目里有比错误台账更新的片子：改了片子没记账。'
        '真错就走 ledger' + '_add.py；没错就跑 ledger' + '_ack.py 写一句复核无问题。')


def projects():
    if not os.path.isdir(PROJ_ROOT):
        return []
    return [os.path.join(PROJ_ROOT, d) for d in os.listdir(PROJ_ROOT)
            if os.path.isdir(os.path.join(PROJ_ROOT, d))]


def newest(pattern, root):
    hits = glob.glob(os.path.join(root, pattern))
    return max(hits, key=os.path.getmtime) if hits else None


def check():
    for root in projects():
        ledger = os.path.join(root, LEDGER)
        if not os.path.isfile(ledger):
            continue
        film = None
        for pat in FILM_PATS:
            f = newest(pat, root)
            if f and (film is None or os.path.getmtime(f) > os.path.getmtime(film)):
                film = f
        if not film:
            continue
        if os.path.getmtime(film) <= os.path.getmtime(ledger):
            continue
        ack = os.path.join(root, '.gates', 'ledger_ack.json')
        if os.path.isfile(ack):
            try:
                a = float(json.load(io.open(ack, encoding='utf-8')).get('recorded_at', 0))
            except Exception:
                a = 0.0
            if a >= os.path.getmtime(film):
                continue
        return DENY + ' | film=' + os.path.basename(film)
    return None


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0
    tool = str(payload.get('tool_name') or '')
    if tool not in ('present_files', 'Write', 'Edit', 'Bash'):
        return 0
    if tool == 'Bash':
        cmd = str((payload.get('tool_input') or {}).get('command') or '')
        if ('gate' + '.py') not in cmd and ('ledger' + '_add') not in cmd:
            return 0
    msg = check()
    if msg:
        sys.stdout.write(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': msg}}, ensure_ascii=False))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
