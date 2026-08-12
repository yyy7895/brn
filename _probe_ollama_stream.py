# -*- coding: utf-8 -*-
"""探测 deepseek-r1:7b 流式响应的 JSON 字段结构（确认 thinking 在哪）。"""
import json
import httpx

URL = "http://127.0.0.1:11434/api/chat"
PAYLOAD = {
    "model": "deepseek-r1:7b",
    "messages": [
        {"role": "system", "content": "你是一个知识库问答助手。请依据资料回答。\n\n【参考资料】\nAI命令模版文档内容示例。"},
        {"role": "user", "content": "这是什么文档？"},
    ],
    "stream": True,
}

seen_keys = set()
msg_keys = set()
count = 0
thinking_count = 0
content_count = 0
sample_lines = []

with httpx.Client(timeout=180.0) as client:
    with client.stream("POST", URL, json=PAYLOAD) as resp:
        for line in resp.iter_lines():
            if not line or not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            count += 1
            seen_keys.update(obj.keys())
            msg = obj.get("message")
            if isinstance(msg, dict):
                msg_keys.update(msg.keys())
                if msg.get("thinking"):
                    thinking_count += 1
                if msg.get("content"):
                    content_count += 1
            if count <= 6 or obj.get("done"):
                sample_lines.append(line)

print("=== 统计 ===")
print("总 chunk 数:", count)
print("顶层字段:", seen_keys)
print("message 字段:", msg_keys)
print("有 thinking 的 chunk:", thinking_count)
print("有 content 的 chunk:", content_count)
print("\n=== 样本（前6 + done）===")
for s in sample_lines[:7]:
    print(s[:300])
