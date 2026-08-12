# -*- coding: utf-8 -*-
"""
入库模块：读取上传文件 -> 切块 -> 逐块向量化 -> 写入 document_chunks。

pypdf：纯 Python 的 PDF 解析库（无需系统装 poppler），
用 PdfReader 逐页提取文本。安装：pip install pypdf
"""
from __future__ import annotations

import os
from typing import Tuple

from sqlalchemy import text

from src.config import cfg
from src.db.connection import engine
from src.rag.chunking import chunk_text
from src.rag.embedding import embed, vec_to_literal
from src.utils.logger import log


def read_file(path: str) -> Tuple[str, str]:
    """
    读取文件文本，返回 (text, file_type)。
    支持 .txt / .md / .pdf；其他类型按 txt 尝试读取。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        # 延迟导入 pypdf：只有处理 PDF 时才需要，避免无 PDF 场景的额外依赖耦合
        from pypdf import PdfReader

        reader = PdfReader(path)
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return "\n".join(pages), "pdf"
    if ext == ".md":
        with open(path, encoding="utf-8") as f:
            return f.read(), "md"
    with open(path, encoding="utf-8") as f:
        return f.read(), "txt"


def create_document(title: str, source: str, file_type: str) -> int:
    """在 documents 表插入一条记录，返回自增 id。"""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "INSERT INTO documents(title, source, file_type) "
                "VALUES (:t, :s, :ft) RETURNING id"
            ),
            {"t": title, "s": source, "ft": file_type},
        )
        doc_id = result.scalar()
        conn.commit()
    log.info(f"新建文档记录 id={doc_id} title={title}")
    return int(doc_id)


def ingest_document(doc_id: int, doc_text: str) -> int:
    """
    对给定文档文本：切块 -> 逐块向量化 -> 批量入库 -> 更新 chunk_count。
    返回切块数量。
    注意：参数名用 doc_text 而非 text，避免遮蔽上方 from sqlalchemy import text。
    """
    chunks = chunk_text(doc_text)
    if not chunks:
        log.warning(f"文档 {doc_id} 无可入库文本")
        return 0

    rows = []
    for idx, piece in enumerate(chunks):
        emb = embed(piece)
        if not emb:  # 单块向量化失败则跳过该块（不阻断整体）
            log.warning(f"文档 {doc_id} 第 {idx} 块向量化失败，跳过")
            continue
        rows.append(
            {
                "doc_id": doc_id,
                "ci": idx,
                "content": piece,
                "emb": vec_to_literal(emb),
            }
        )

    if rows:
        with engine.connect() as conn:
            # executemany：传入参数字典列表，SQLAlchemy 会批量执行
            conn.execute(
                text(
                    "INSERT INTO document_chunks(doc_id, chunk_index, content, embedding) "
                    "VALUES (:doc_id, :ci, :content, CAST(:emb AS vector))"
                ),
                rows,
            )
            conn.execute(
                text("UPDATE documents SET chunk_count = :n WHERE id = :did"),
                {"n": len(rows), "did": doc_id},
            )
            conn.commit()

    log.info(f"文档 {doc_id} 入库完成，共 {len(rows)} 块")
    return len(rows)


def list_documents() -> list:
    """返回全部文档列表。"""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, title, source, file_type, chunk_count, created_at "
                "FROM documents ORDER BY created_at DESC"
            )
        ).mappings().all()
    return [dict(r) for r in rows]


def delete_document(doc_id: int) -> bool:
    """
    删除文档（级联删除其所有切块，由外键 ON DELETE CASCADE 保证）。
    返回是否真的删除了一行。
    """
    with engine.connect() as conn:
        result = conn.execute(
            text("DELETE FROM documents WHERE id = :did RETURNING id"),
            {"did": doc_id},
        )
        deleted = result.scalar() is not None
        conn.commit()
    # 删 DB 记录后，顺带尝试删上传的原文件（可选，失败不影响）
    if deleted:
        log.info(f"已删除文档 id={doc_id}")
    return deleted


def get_document(doc_id: int) -> dict | None:
    """取单条文档记录。"""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, title, source, file_type, chunk_count, created_at "
                "FROM documents WHERE id = :did"
            ),
            {"did": doc_id},
        ).mappings().first()
    return dict(row) if row else None
