# -*- coding: utf-8 -*-
"""
统一日志模块：优先用 loguru（爬虫侧已用），不可用则回退到标准库 logging。
被后端 postger_reserve_chat.py 和所有 RAG 模块复用，保证日志风格一致。
"""
from __future__ import annotations

try:
    # loguru：第三方日志库，提供开箱即用的彩色控制台输出、文件轮转等，
    # 比标准 logging 配置更简单。安装：pip install loguru
    from loguru import logger as log

    # 默认输出到 stderr（带颜色），如需落盘可在此 add 文件 sink。
    # loguru 默认就有一个 stderr sink，这里不再重复配置，保持轻量。
except Exception:  # pragma: no cover - 仅当 loguru 缺失时走标准 logging
    import logging

    log = logging.getLogger("rag")
    if not log.handlers:
        logging.basicConfig(level="INFO", format="%(asctime)s | %(levelname)s | %(message)s")

__all__ = ["log"]
