# -*- coding: utf-8 -*-
"""
集中配置：从项目根目录的 .env 读取配置，提供 cfg 单例。
不依赖 python-dotenv（少装一个包），内置极简 .env 解析器。
被 src/db/connection.py、src/rag/* 及后端共用。
"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录：本文件在 src/config.py，上两级即根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env(env_path: Path) -> None:
    """
    极简 .env 解析器：把 `KEY=VALUE` 行写入 os.environ。
    规则：
      - 跳过空行和 # 注释行；
      - 不覆盖已存在的环境变量（让真实环境变量优先于 .env）；
      - 去掉值两侧的引号（单/双引号）。
    """
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# 启动时加载一次 .env
_load_env(PROJECT_ROOT / ".env")


class Config:
    """全局配置单例（实例化为 cfg）。所有值来自环境变量（已含 .env）。"""

    # ---- Ollama（仅向量化用；聊天已切换到官方 DeepSeek API）----
    # OLLAMA_BASE：Ollama 服务根地址，向量化接口挂在它下面
    OLLAMA_BASE: str = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")
    # 向量化模型：nomic-embed-text，输出 768 维向量
    EMBED_MODEL: str = os.getenv("EMBED_MODEL", "nomic-embed-text")
    EMBED_DIM: int = int(os.getenv("EMBED_DIM", "768"))

    # ---- DeepSeek 官方 API（聊天生成，经 LlamaIndex 调用）----
    # DEEPSEEK_API_KEY：在 .env 配置（必填，否则聊天会报 401）
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    # 聊天模型：可选 deepseek-v4-pro / deepseek-v4-flash / deepseek-chat / deepseek-reasoner
    # （deepseek-reasoner 带 reasoning_content 思考过程，对应前端 <thinking> 折叠块）
    CHAT_MODEL: str = os.getenv("CHAT_MODEL", "deepseek-v4-flash")

    # ---- 数据库 ----
    # 真实凭据仅允许从 .env / 环境变量读取，不保留任何实际密码于仓库中。
    DB_URL: str = os.getenv("DB_URL", "postgresql://postgres:CHANGE_ME@127.0.0.1:5432/brn")

    # ---- RAG 检索参数 ----
    # TOP_K：每次检索取最相似的 K 个文档块拼进 prompt
    TOP_K: int = int(os.getenv("TOP_K", "4"))
    # UPLOAD_DIR：上传文件存放目录（相对项目根）
    UPLOAD_DIR: Path = PROJECT_ROOT / os.getenv("UPLOAD_DIR", "data/uploads")

    @property
    def OLLAMA_CHAT_URL(self) -> str:
        """Ollama 聊天接口：POST /api/chat"""
        return self.OLLAMA_BASE + "/api/chat"

    @property
    def OLLAMA_EMBED_URL(self) -> str:
        """Ollama 向量化接口：POST /api/embeddings"""
        return self.OLLAMA_BASE + "/api/embeddings"


cfg = Config()

# 确保上传目录存在（幂等）
cfg.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
