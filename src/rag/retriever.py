# -*- coding: utf-8 -*-
"""
检索模块：用 pgvector 做余弦相似检索，取与用户问题最相关的 top-k 个文档块；
并提供 MMR 重排，在相关性与多样性间取平衡。

【pgvector 余弦检索】
  - `<=>` 是 pgvector 的余弦距离算子：0=完全相同，2=方向相反；值越小越相似。
  - `1 - (embedding <=> q)` 把距离转成相似度（0~1，越大越相关）。
  - ORDER BY embedding <=> q 即按相似度从高到低排序。
  - CAST(:q AS vector)：把字符串字面量 '[0.1,...]' 转成 vector 类型
    （避免 :v::vector 参数绑定语法错误，详见 project_memory 经验）。
"""
from __future__ import annotations

import json
import math
from typing import List

from sqlalchemy import text

from src.config import cfg
from src.db.connection import engine
from src.rag.embedding import vec_to_literal
from src.utils.logger import log


def pgv_search(
    query_embedding: List[float],
    top_k: int | None = None,
    return_embedding: bool = False,
) -> List[dict]:
    """
    余弦检索 top-k 块。
    返回：[{id, doc_id, chunk_index, content, doc_title, score}, ...]
          score 为相似度（越大越相关）。

    return_embedding=True 时多取 embedding 列，供 MMR 重排计算块间相似度用。
    （pgvector 经 SQLAlchemy 返回为字符串字面量，这里解析成 float 列表）
    """
    if not query_embedding:
        return []

    k = top_k or cfg.TOP_K
    vec_str = vec_to_literal(query_embedding)
    # return_embedding 时附带块向量，供 MMR 计算块间相似度
    emb_col = ", dc.embedding AS embedding" if return_embedding else ""

    sql = text(
        f"""
        SELECT dc.id,
               dc.doc_id,
               dc.chunk_index,
               dc.content,
               d.title AS doc_title,
               1 - (dc.embedding <=> CAST(:q AS vector)) AS score{emb_col}
        FROM document_chunks dc
        LEFT JOIN documents d ON d.id = dc.doc_id
        WHERE dc.embedding IS NOT NULL
        ORDER BY dc.embedding <=> CAST(:q AS vector)
        LIMIT :k
        """
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, {"q": vec_str, "k": k}).mappings().all()
        result = [dict(r) for r in rows]
        if return_embedding:
            # pgvector 返回的 embedding 是字符串如 "[0.1,-0.03,...]"，解析成列表
            for r in result:
                emb = r.get("embedding")
                if isinstance(emb, str):
                    try:
                        r["embedding"] = json.loads(emb)
                    except Exception:
                        r["embedding"] = []
        return result
    except Exception as e:
        log.error(f"【检索失败】{repr(e)}")
        return []


def _cosine(a: List[float], b: List[float]) -> float:
    """
    纯 Python 余弦相似度（不依赖 numpy）。
    768 维向量，调用次数有限（候选数 × 已选数），性能足够。
    """
    if not a or not b:
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def rerank_mmr(
    query_embedding: List[float],
    candidates: List[dict],
    top_k: int,
    lambda_: float = 0.5,
) -> List[dict]:
    """
    MMR（Maximal Marginal Relevance）重排：在相关性与多样性间取平衡，
    既选与 query 相关的、又彼此不重复的块，避免 top-k 全是同一文档的近似段落。

    原理：每轮从候选中选 mmr 最大的：
      mmr = λ * rel(query, c) - (1 - λ) * max(sim(c, 已选))
        rel：候选与 query 的相关性（复用 DB 已算的 score，0~1）
        sim：候选与已选块之间的余弦相似度（多样性惩罚）
      λ=0.5：相关性与多样性各占一半

    优化7 说明：本函数是"多样性去冗余"，不等于 cross-encoder 语义重排。
      cross-encoder（如 bge-reranker-base）会用 query 和 chunk 拼接送入打分，
      精度更高但需 pip install sentence-transformers + 下载模型（约 1GB）。
      若后续要更强 rerank，可在此函数内替换 _score 为 cross-encoder 调用，
      接口签名保持不变即可。
    """
    if not candidates:
        return []
    k = min(top_k, len(candidates))
    pool = sorted(candidates, key=lambda r: r.get("score", 0), reverse=True)
    selected: List[dict] = []
    remaining = list(pool)
    while len(selected) < k and remaining:
        best_idx = 0
        best_mmr = -1.0
        for i, c in enumerate(remaining):
            rel = c.get("score", 0)
            if selected:
                max_sim = max(
                    _cosine(c.get("embedding", []), s.get("embedding", []))
                    for s in selected
                )
            else:
                max_sim = 0.0
            mmr = lambda_ * rel - (1 - lambda_) * max_sim
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = i
        selected.append(remaining.pop(best_idx))
    return selected
