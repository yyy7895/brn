# -*- coding: utf-8 -*-
"""
文本切块模块：把长文本切成固定字符窗口的小块，相邻块之间保留 overlap（重叠）。

【chunk（切块）】大模型上下文有限，整篇文档直接塞进去不现实，
也影响检索精度；切成小块后，只把"相关块"喂给模型。
【overlap（重叠）】相邻块共享一段文字，避免把完整句子/题目从中间切断，
导致语义丢失。中文按字符切即可（无需先分词）。
"""
from __future__ import annotations

from typing import List


def chunk_text(
    text: str, chunk_size: int = 500, overlap: int = 50
) -> List[str]:
    """
    按字符窗口切块。
    参数：
      chunk_size：每块最大字符数（默认 500，适合中文）
      overlap：相邻块重叠字符数（默认 50）
    返回：块字符串列表（去掉空块）。
    """
    text = (text or "").strip()
    if not text:
        return []

    step = chunk_size - overlap  # 每次窗口前进的步长
    if step <= 0:  # 防御：overlap 过大导致步长非正
        step = chunk_size

    chunks: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        piece = text[i : i + chunk_size].strip()
        if piece:
            chunks.append(piece)
        if i + chunk_size >= n:  # 已到末尾
            break
        i += step
    return chunks
