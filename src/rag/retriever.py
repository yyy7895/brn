# -*- coding: utf-8 -*-
"""
检索模块：用 pgvector 做余弦相似检索，取与用户问题最相关的 top-k 个文档块。

【pgvector 余弦检索】
  - `<=>` 是 pgvector 的余弦距离算子：0=完全相同，2=方向相反；值越小越相似。
  - `1 - (embedding <=> q)` 把距离转成相似度（0~1，越大越相关）。
  - ORDER BY embedding <=> q 即按相似度从高到低排序。
  - q::vector：把字符串字面量 '[0.1,...]' 转成 vector 类型。
"""
from __future__ import annotations

from typing import List

from sqlalchemy import text

from src.config import cfg
from src.db.connection import engine
from src.rag.embedding import vec_to_literal
from src.utils.logger import log


def pgv_search(
    query_embedding: List[float], top_k: int | None = None
) -> List[dict]:
    """
    余弦检索 top-k 块。
    返回：[{id, doc_id, chunk_index, content, doc_title, score}, ...]
          score 为相似度（越大越相关）。
    """
    if not query_embedding:
        return []

    k = top_k or cfg.TOP_K
    vec_str = vec_to_literal(query_embedding)

    sql = text(
        """
        SELECT dc.id,
               dc.doc_id,
               dc.chunk_index,
               dc.content,
               d.title AS doc_title,
               1 - (dc.embedding <=> CAST(:q AS vector)) AS score
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
        return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"【检索失败】{repr(e)}")
        return []
