# -*- coding: utf-8 -*-
"""
共享数据库连接：全项目（后端 chat_history + RAG 的 documents/document_chunks）
共用一个 SQLAlchemy engine，避免重复创建连接池。
"""
from __future__ import annotations

from sqlalchemy import create_engine

from src.config import cfg
from src.utils.logger import log

# create_engine：SQLAlchemy 创建数据库连接池的入口。
#   pool_pre_ping=True：每次借出连接前先发一个轻量探测，
#   避免拿到已断开的连接（PostgreSQL 重启后尤为重要）。
engine = create_engine(cfg.DB_URL, pool_pre_ping=True)

# DB_URL 也暴露出来，供后端日志等场景使用（脱敏后打印）
DB_URL: str = cfg.DB_URL


def get_engine():
    """返回全局共享 engine（便于测试时替换）。"""
    return engine


def safe_dsn() -> str:
    """返回脱敏的 DSN（隐藏密码），用于日志打印。"""
    if "@" in DB_URL:
        head, tail = DB_URL.split("@", 1)
        return head.split(":", 1)[0] + ":***@" + tail
    return DB_URL


log.info(f"DB engine 已创建: {safe_dsn()}")
