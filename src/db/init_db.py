# -*- coding: utf-8 -*-
"""
建表入口：幂等执行 schema.sql（建扩展、建 documents/document_chunks 表、建索引）。
后端启动时调用一次；可重复执行（全部 IF NOT EXISTS）。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from src.db.connection import engine
from src.utils.logger import log

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db() -> None:
    """
    执行 schema.sql 全文。
    psycopg2 支持把多条分号分隔的 SQL 一次性发给 PostgreSQL 执行，
    所以这里把整个 schema.sql 作为一条 text() 传入即可。
    """
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.connect() as conn:
        conn.execute(text(sql))
        conn.commit()
    log.info("RAG 建表完成（documents / document_chunks / HNSW 索引已就绪）")


if __name__ == "__main__":
    init_db()
