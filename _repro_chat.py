# -*- coding: utf-8 -*-
"""复现 /chat/stream，抓全部 SSE 事件，定位错误。"""
import json, requests, time

r = requests.post(
    "http://127.0.0.1:8000/chat/stream",
    params={"user_id": "DEBUG_ERR", "current_msg": "你好，用一句话介绍你自己"},
    stream=True,
    timeout=180,
)
print("status:", r.status_code)
events = []
token_count = 0
t0 = time.time()
for line in r.iter_lines(decode_unicode=True):
    if not line:
        continue
    if line.startswith("event:"):
        ev = line[6:].strip()
        events.append(ev)
        print(f"[{time.time()-t0:5.1f}s] EVENT: {ev}")
        continue
    if line.startswith("data:"):
        raw = line[5:].strip()
        try:
            d = json.loads(raw)
        except Exception:
            print("  非JSON data:", raw[:200])
            continue
        if d.get("error"):
            print("  ❌ error data:", d["error"])
        if d.get("full_response") is not None:
            print("  full_response 长度:", len(d["full_response"]))
        tok = d.get("token") or ""
        if tok:
            token_count += 1
            if token_count <= 3:
                print(f"  token#{token_count}: {tok!r}")
print(f"\n事件序列: {events}")
print(f"总 token 数: {token_count}")
