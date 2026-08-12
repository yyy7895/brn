# -*- coding: utf-8 -*-
"""干净测试后端 /chat/stream：用无历史的 user_id，打印所有 SSE 事件 + 时间戳。"""
import time
import json
import requests

URL = "http://127.0.0.1:8000/chat/stream"
PARAMS = {"user_id": "PROBE_NOHIST", "current_msg": "AI命令模版文档是关于什么的？"}

t0 = time.time()
resp = requests.post(URL, params=PARAMS, stream=True, timeout=180)
print(f"[{time.time()-t0:5.1f}s] HTTP {resp.status_code}")

event_counts = {}
msg_token_events = 0
empty_token_events = 0
thinking_events = 0
error_event = None
done_event = None
first_token_t = None
first_thinking_t = None

for line in resp.iter_lines(decode_unicode=True):
    if not line:
        continue
    if line.startswith(":"):
        # ping
        continue
    if line.startswith("event:"):
        ev = line[6:].strip()
        event_counts[ev] = event_counts.get(ev, 0) + 1
        if ev == "error":
            error_event = time.time() - t0
        continue
    if line.startswith("data:"):
        raw = line[5:].strip()
        try:
            d = json.loads(raw)
        except Exception:
            continue
        if "token" in d or "thinking" in d:
            msg_token_events += 1
            if first_token_t is None and d.get("token"):
                first_token_t = time.time() - t0
            if first_thinking_t is None and d.get("thinking"):
                first_thinking_t = time.time() - t0
            if d.get("token"):
                empty_token_events = empty_token_events + 0
            else:
                empty_token_events += 1
            if d.get("thinking"):
                thinking_events += 1
        if d.get("full_response") is not None:
            done_event = time.time() - t0
            print(f"[{done_event:5.1f}s] DONE full_response 长度={len(d.get('full_response',''))}")
            print("最终答案预览:", repr(d.get("full_response", "")[:200]))

print("\n=== 汇总 ===")
print("event 计数:", event_counts)
print(f"message 事件总数: {msg_token_events}")
print(f"  其中空 token 事件: {empty_token_events}（应为0：修复后跳过空内容）")
print(f"  含 thinking 事件: {thinking_events}")
print(f"首个 thinking 出现时间: {first_thinking_t}s（应很快，证明思考过程实时下发）")
print(f"首个非空 token 出现时间: {first_token_t}s")
print(f"error 事件时间: {error_event}")
print(f"总耗时: {time.time()-t0:.1f}s")
