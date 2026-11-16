# -*- coding: utf-8 -*-
"""复核凭据：这版片子审过了就留一句，不管有没有错。

  python ledger_ack.py --project "C:/D/漫剧/江苏一通年会形象片" --note "S01-S16 复核无问题"
  python ledger_ack.py --project "..." --note "P01 已修，复查通过" --code P01

写 <项目>/.gates/ledger_ack.json：{recorded_at, note, code, by}
- recorded_at 取当前时间，供 ledger_gate.py 判「记账不晚于片子」。
- 有真错：先 ledger_add.py 进错误台账，再跑本脚本记「已修/复查」。
- 没错：只跑本脚本写「复核无问题」，不往错误台账塞假条目。
"""
import argparse
import io
import json
import os
import time

ap = argparse.ArgumentParser()
ap.add_argument("--project", required=True)
ap.add_argument("--note", required=True)
ap.add_argument("--code", default="")
ap.add_argument("--by", default="workbuddy")
a = ap.parse_args()

gates = os.path.join(a.project, ".gates")
os.makedirs(gates, exist_ok=True)
p = os.path.join(gates, "ledger_ack.json")
rec = {"recorded_at": time.time(),
       "recorded_at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
       "note": a.note, "code": a.code, "by": a.by}
json.dump(rec, io.open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("ack", p, rec["recorded_at_text"], a.note)
