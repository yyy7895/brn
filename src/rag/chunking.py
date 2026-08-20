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
    语义切块：优先按段落（双换行）切，保持句子/表格完整；
    仅当单段超过 chunk_size 时，才对该段用字符窗口 + overlap 二次切。

    优化5 说明：旧实现纯按字符窗口滑动切，会从句子/表格/代码块中间硬切，
    导致上下文语义断裂、检索精度下降。新实现先以 \\n\\n 分段，累积到接近
    chunk_size 形成块，既保留段落边界又控制块大小。

    参数：
      chunk_size：每块最大字符数（默认 500，适合中文）
      overlap：超长段落二次切时的字符重叠数（默认 50）
    返回：块字符串列表（去掉空块）。
    """
    text = (text or "").strip()
    if not text:
        return []

    def _char_window(segment: str) -> List[str]:
        """对超长段落按字符窗口切（带 overlap），避免从句中切断。"""
        step = chunk_size - overlap  # 窗口前进步长
        if step <= 0:  # 防御：overlap 过大导致步长非正
            step = chunk_size
        out: List[str] = []
        i = 0
        n = len(segment)
        while i < n:
            piece = segment[i : i + chunk_size].strip()
            if piece:
                out.append(piece)
            if i + chunk_size >= n:  # 已到末尾
                break
            i += step
        return out

    chunks: List[str] = []
    # 按双换行切段落，保留段落语义（Markdown 的 ## 标题段落也天然在此被分开）
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    buffer = ""  # 累积短段落，拼到接近 chunk_size 再输出
    for para in paragraphs:
        # 单段超长：先把已累积的 buffer 输出，再对该段做字符窗口切
        if len(para) > chunk_size:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(_char_window(para))
            continue
        # 累积：若加入当前段落后会超过 chunk_size，则先输出已累积的块
        if buffer and len(buffer) + len(para) + 2 > chunk_size:
            chunks.append(buffer)
            buffer = para
        else:
            buffer = (buffer + "\n\n" + para) if buffer else para
    if buffer:
        chunks.append(buffer)

    return chunks
