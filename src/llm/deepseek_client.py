# -*- coding: utf-8 -*-
"""
DeepSeek LLM 封装：流式直接调官方 API（LlamaIndex 流式适配器有 bug），
非流式走 LlamaIndex（正常工作，便于后续接 RAG/工具调用）。

【为什么流式不走 LlamaIndex】
llama-index-llms-deepseek 适配器的 astream_chat() 返回的 chunk.delta 全是空字符串，
疑似版本 bug。故流式直接用 httpx 调 DeepSeek OpenAI 兼容接口，自行解析 SSE。
非流式 llm.chat() 工作正常，保留。

【思考字段处理】
deepseek-v4-flash 模型在流式 chunk 的 delta 里同时带 content 和 reasoning_content。
reasoning_content 是思考过程（对应前端 <thinking> 折叠块），content 是正式回答。
"""
from __future__ import annotations

import json
from typing import List

import httpx
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.llms.deepseek import DeepSeek

from src.config import cfg
from src.utils.logger import log

# 模块级单例：DeepSeek 客户端（非流式用）+ httpx 客户端（流式用）
_llm: DeepSeek | None = None
_stream_client: httpx.AsyncClient | None = None

# 历史消息 role 字符串 -> LlamaIndex MessageRole 枚举
_ROLE_MAP = {
    "user": MessageRole.USER,
    "assistant": MessageRole.ASSISTANT,
    "system": MessageRole.SYSTEM,
}


def _get_llm() -> DeepSeek:
    """懒加载 DeepSeek LLM 单例（非流式用）。"""
    global _llm
    if _llm is None:
        if not cfg.DEEPSEEK_API_KEY:
            raise RuntimeError(
                "DEEPSEEK_API_KEY 未配置，请在项目根 .env 设置后重启服务"
            )
        _llm = DeepSeek(
            api_key=cfg.DEEPSEEK_API_KEY,
            model=cfg.CHAT_MODEL,
            api_base=cfg.DEEPSEEK_BASE_URL,
        )
    return _llm


def _get_stream_client() -> httpx.AsyncClient:
    """懒加载 httpx 异步客户端（流式用，复用连接池）。"""
    global _stream_client
    if _stream_client is None:
        _stream_client = httpx.AsyncClient(timeout=300.0)
    return _stream_client


def to_chat_messages(history: List[dict]) -> List[ChatMessage]:
    """[{role, content}, ...] -> LlamaIndex ChatMessage 列表。"""
    msgs = []
    for m in history:
        role = _ROLE_MAP.get(m.get("role", "user"), MessageRole.USER)
        msgs.append(ChatMessage(role=role, content=m.get("content", "")))
    return msgs


async def stream_chat(history: List[dict]):
    """
    流式调用 DeepSeek API，逐 token yield (content, thinking)。
    直接用 httpx 调官方接口，自行解析 SSE（绕过 LlamaIndex 流式 bug）。
    history: [{role, content}, ...] 含 system(RAG资料) + 历史 + 当前问题。
    """
    if not cfg.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    client = _get_stream_client()
    payload = {
        "model": cfg.CHAT_MODEL,
        "messages": history,
        "stream": True,
    }
    url = cfg.DEEPSEEK_BASE_URL + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.DEEPSEEK_API_KEY}"}

    async with client.stream("POST", url, json=payload, headers=headers) as resp:
        async for line in resp.aiter_lines():
            if not line.strip():
                continue
            # SSE 格式：data: {...}  或  data: [DONE]
            if not line.startswith("data:"):
                continue
            data_str = line.removeprefix("data:").strip()
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = data.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {}) or {}
            content = delta.get("content", "") or ""
            thinking = delta.get("reasoning_content", "") or ""
            if content or thinking:
                yield content, thinking


def chat(history: List[dict]) -> str:
    """非流式调用 DeepSeek，返回完整回答文本（走 LlamaIndex）。"""
    llm = _get_llm()
    msgs = to_chat_messages(history)
    resp = llm.chat(messages=msgs)
    return resp.message.content or ""
