# -*- coding: utf-8 -*-
"""通用 openmem MCP 调用：python mcp_call.py <tool> '<json-args>'"""
import sys, json, urllib.request

EP = 'http://127.0.0.1:3466/mcp'

def call(tool, args, timeout=180):
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': tool, 'arguments': args}}
    req = urllib.request.Request(EP, data=json.dumps(body).encode('utf-8'), method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'application/json, text/event-stream')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode('utf-8')
    for ln in raw.splitlines():
        if ln.startswith('data:'):
            raw = ln[5:].strip(); break
    d = json.loads(raw)
    if 'error' in d:
        return 'ERROR ' + json.dumps(d['error'], ensure_ascii=False)
    return d.get('result', {}).get('content', [{}])[0].get('text', '')

if __name__ == '__main__':
    tool = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print(call(tool, args))
