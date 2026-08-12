# -*- coding: utf-8 -*-
"""
向量化模块：调 Ollama /api/embeddings 把文本转成 768 维浮点向量。

【embedding（嵌入/向量化）】把一段文字映射成一串数字（向量），
语义相近的文字向量也相近，是"用数字表示语义"的基础。
nomic-embed-text 模型输出 768 维。
"""
from __future__ import annotations

from typing import List

import httpx

from src.config import cfg
from src.utils.logger import log


def embed(text: str) -> List[float]:
    """
    把一段文本转向量。
    调用 Ollama 的 /api/embeddings 接口：
      请求体: {"model": "nomic-embed-text", "prompt": "<文本>"}
      返回体: {"embedding": [0.01, -0.03, ...]}  长度 768
    httpx.Client 是同步 HTTP 客户端（同进程内复用连接）。
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                cfg.OLLAMA_EMBED_URL,
                json={"model": cfg.EMBED_MODEL, "prompt": text},
            )
            resp.raise_for_status()
            return resp.json().get("embedding", [])
    except Exception as e:
        log.error(f"【向量化失败】{repr(e)}")
        return []


def vec_to_literal(vec: List[float]) -> str:
    """
    把向量转成 pgvector 能解析的字符串字面量，如 '[0.1,0.2,...]'。
    SQL 里用 `:emb::vector` 把该字符串转换成 vector 类型，
    这样无需安装 pgvector 的 Python 适配包。
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"
