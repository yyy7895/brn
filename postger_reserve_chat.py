# -*- coding: utf-8 -*-
"""
本地聊天机器人后端 + RAG 知识库。
- 聊天：Ollama 流式生成，历史存 PostgreSQL
- RAG：上传文档 -> 切块 -> 向量化 -> pgvector 检索 -> 注入 prompt
"""
# ============================================
# 模块1：导入依赖
# ============================================
import os
import asyncio
import json
import shutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import httpx
import requests
from fastapi import FastAPI, Query, UploadFile, File, HTTPException
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse

# 复用项目统一日志（loguru），缺失时回退标准 logging
try:
    from src.utils.logger import log
except Exception:  # pragma: no cover
    import logging
    log = logging.getLogger("chat")

# RAG 相关模块
from src.config import cfg
from src.db.connection import engine, safe_dsn
from src.db.init_db import init_db
from src.rag.embedding import embed
from src.rag.retriever import pgv_search, rerank_mmr
from src.rag import ingest as rag_ingest
from src.llm import deepseek_client as llm_ds

# ============================================
# 模块2：基础配置（统一从 cfg 读取，保留旧变量名做别名，老代码无感）
# ============================================
app = FastAPI(title="本地聊天机器人后端 + RAG")

# 聊天生成已切换到 LlamaIndex 调用官方 DeepSeek（见 src/llm/deepseek_client.py）
# 向量化仍用 Ollama nomic-embed-text（embedding.py 内部引用 cfg.OLLAMA_EMBED_URL）
DB_URL = cfg.DB_URL
# engine 复用 src/db/connection.py 的全局共享连接池
executor = ThreadPoolExecutor()


@app.on_event("startup")
def _on_startup():
    """启动时建 RAG 表（幂等）。PG 临时不可用时也不阻断启动。"""
    try:
        init_db()
    except Exception as e:
        log.error(f"【启动建表失败，RAG 相关接口可能不可用】{repr(e)}")


def _retrieve_context(query: str) -> list:
    """
    RAG 检索：把用户问题向量化 -> pgvector 取候选 -> MMR 重排取 top-k。
    任何异常都返回空列表（降级为纯对话，不阻断聊天）。
    """
    try:
        q_emb = embed(query)
        if not q_emb:
            return []
        # 优化7：先召回较多候选（目标数×5），再用 MMR 重排去冗余、提多样性。
        # 召回需要 embedding 列供 MMR 计算块间相似度。
        rerank_pool = cfg.TOP_K * 5
        candidates = pgv_search(q_emb, top_k=rerank_pool, return_embedding=True)
        if not candidates:
            return []
        ranked = rerank_mmr(q_emb, candidates, cfg.TOP_K)
        # 重排后 embedding 不再需要，移除以免带到上层 / 序列化进 sources
        for r in ranked:
            r.pop("embedding", None)
        return ranked
    except Exception as e:
        log.warning(f"【RAG 检索失败，降级为纯对话】{repr(e)}")
        return []


# ============================================
# 模块3：数据库函数（chat_history）
# ============================================

def save_record(user_id: str, user_msg: str, bot_msg: str):
    """保存单轮问答"""
    sql = text(
        "INSERT INTO chat_history(user_id, user_content, bot_content) "
        "VALUES(:uid, :u, :b)"
    )
    with engine.connect() as conn:
        conn.execute(sql, {"uid": user_id, "u": user_msg, "b": bot_msg})
        conn.commit()


def get_history_messages(user_id: str, limit: int | None = None):
    """
    读取用户历史对话 -> 拼 LLM messages 格式 [{role, content}, ...]。

    limit：只取最近 N 条记录（按时间正序返回，供 LLM 使用）。
      旧实现先拉全量再在 Python 里切片，历史上万条时浪费严重；
      现在 SQL 层用子查询 DESC LIMIT 取最近 N 条，再外层 ASC 还原时间顺序，
      DB 只回传需要的数据。
      limit=None 时查全量（保留旧语义，兼容 list_history 等场景）。
    """
    if limit and limit > 0:
        # 子查询取最近 limit 条（DESC），外层 ASC 还原为时间正序
        sql = text(
            "SELECT user_content, bot_content FROM ("
            "SELECT user_content, bot_content, creat_time "
            "FROM chat_history WHERE user_id=:uid "
            "ORDER BY creat_time DESC LIMIT :lim"
            ") t ORDER BY t.creat_time ASC"
        )
        params = {"uid": user_id, "lim": int(limit)}
    else:
        sql = text(
            "SELECT user_content, bot_content "
            "FROM chat_history "
            "WHERE user_id=:uid "
            "ORDER BY creat_time ASC"
        )
        params = {"uid": user_id}

    with engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    msg_list = []
    for u_txt, b_txt in rows:
        msg_list.append({"role": "user", "content": u_txt})
        msg_list.append({"role": "assistant", "content": b_txt})
    return msg_list


# 旧函数名保留（兼容），重写个别名
get_history = get_history_messages


def list_history_records(user_id: str, page: int = 1, page_size: int = 50):
    """
    分页读取 chat_history 原始记录，用于前端 /history 接口
    返回: (records, total_count)
      records: [{user_content, bot_content, creat_time?}, ...]
    """
    sql_count = text("SELECT COUNT(*) FROM chat_history WHERE user_id=:uid")
    with engine.connect() as conn:
        total = conn.execute(sql_count, {"uid": user_id}).scalar() or 0

    offset = max(0, (page - 1) * page_size)
    sql = text(
        "SELECT user_content, bot_content, creat_time "
        "FROM chat_history "
        "WHERE user_id=:uid "
        "ORDER BY creat_time ASC "
        "LIMIT :lim OFFSET :off"
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {"uid": user_id, "lim": int(page_size), "off": int(offset)},
        ).mappings().all()

    records = [dict(r) for r in rows]
    return records, total


# ============================================
# 模块4：流式聊天接口（保留原逻辑，插入 RAG 检索）
# ============================================
@app.post("/chat/stream")
async def chat_stream(user_id: str, current_msg: str):
    try:
        loop = asyncio.get_running_loop()
        # 优化2：直接 SQL 取最近 10 条（DB 层 LIMIT），避免拉全量再 Python 切片。
        # 思考模型 prompt_eval 随历史增长变慢，限制条数保证首 token 速度。
        LLM_HISTORY_LIMIT = 10
        history = await loop.run_in_executor(
            executor, get_history_messages, user_id, LLM_HISTORY_LIMIT
        )
        history.append({"role": "user", "content": current_msg})

        # ---- RAG：向量化用户问题 + pgvector 检索 top-k 块，拼成 system prompt ----
        # 增加变量存储检索结果，用于最终的引用溯源
        rag_sources = []
        retrieved = await loop.run_in_executor(executor, _retrieve_context, current_msg)
        # 优化4：score 阈值过滤。低相关块（如库内无答案时召回的噪声）会误导模型，
        # 只保留相似度 >= RAG_SCORE_THRESHOLD 的块；过滤后为空则降级为纯对话。
        RAG_SCORE_THRESHOLD = 0.3
        if retrieved:
            retrieved = [r for r in retrieved if r.get("score", 0) >= RAG_SCORE_THRESHOLD]
        if retrieved:
            # 优化6：context 标注来源[资料N: title]，让模型可溯源；
            # 前端 rag_sources 顺序与此处编号一一对应，便于引用展示。
            context_parts = []
            for idx, r in enumerate(retrieved, 1):
                title = r.get("doc_title", "未知文档")
                context_parts.append(f"[资料{idx}: {title}]\n{r['content']}")
            context = "\n---\n".join(context_parts)
            system_prompt = (
                "你是一个知识库问答助手。请优先依据下面提供的资料回答用户问题；"
                "若资料中没有答案，请如实说明，不要编造。"
                "回答时可标注引用的资料编号（如[资料1]）。\n\n【参考资料】\n" + context
            )
            history.insert(0, {"role": "system", "content": system_prompt})
            # 把检索到的文档信息（id, title, content）存下来，返回给前端做引用
            for r in retrieved:
                rag_sources.append({
                    "doc_id": r["doc_id"],
                    "doc_title": r.get("doc_title", "未知文档"),
                    "chunk_content": r["content"][:100] + "..." if len(r["content"]) > 100 else r["content"]
                })

        async def generate():
            full_response = ""   # 只累计正式回答（content），存库用
            full_thinking = ""   # 累计思考内容（reasoning_content），不存库、仅前端展示
            # 经 LlamaIndex 流式调官方 DeepSeek，逐 chunk 取 content + reasoning_content
            # reasoning_content 仅 reasoner 模型有；非思考模型 thinking 为空，前端不显示思考块
            async for token, thinking in llm_ds.stream_chat(history):
                full_response += token
                full_thinking += thinking
                # 只在有实质内容（token 或 thinking）时下发，避免空事件刷屏
                if token or thinking:
                    yield {
                        "event": "message",
                        "data": json.dumps({
                            "token": token,
                            "thinking": thinking,
                            "done": False,
                        }),
                    }
            # 流自然结束即 done（DeepSeek 流式无独立 done chunk）
            # 优化3：后台异步写库，立即返回 done，不等 DB commit
            async def _bg_save():
                try:
                    await loop.run_in_executor(
                        executor, save_record,
                        user_id, current_msg, full_response,
                    )
                except Exception as save_err:
                    log.error(f"【后台存库失败】{repr(save_err)}")
            asyncio.create_task(_bg_save())
            # 把检索到的 sources 返回给前端，用于引用溯源展示
            yield {
                "event": "done",
                "data": json.dumps({
                    "full_response": full_response,
                    "sources": rag_sources,
                }),
            }

        return EventSourceResponse(generate())

    except Exception as e:
        log.error(f"【流式接口异常】{repr(e)}")

        async def error_generate():
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)}),
            }
        return EventSourceResponse(error_generate())


# ============================================
# 模块5：历史记录查询接口
# ============================================
@app.get("/history")
def list_history(
    user_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    try:
        records, total = list_history_records(user_id, page, page_size)
        return {
            "code": 200,
            "msg": "ok",
            "data": records,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Exception as e:
        log.error(f"【/history 异常】{repr(e)}")
        return {
            "code": 500,
            "msg": "服务内部异常",
            "detail": str(e),
            "data": [],
        }


# ============================================
# 模块6：历史记录删除接口
# ============================================
@app.get("/history/delete")
def delete_history(user_id: str):
    try:
        sql = text("DELETE FROM chat_history WHERE user_id=:uid")
        with engine.connect() as conn:
            conn.execute(sql, {"uid": user_id})
            conn.commit()
        log.info(f"已删除用户 {user_id} 的历史记录")
        return {"code": 200, "msg": "历史记录已删除"}
    except Exception as e:
        log.error(f"【删除历史异常】{repr(e)}")
        return {"code": 500, "msg": "服务内部异常", "detail": str(e)}


# ============================================
# 模块7：非流式聊天接口
# ============================================
@app.post("/chat")
def chat(user_id: str, current_msg: str):
    try:
        history = get_history_messages(user_id)
        history.append({"role": "user", "content": current_msg})
        # 经 LlamaIndex 同步调官方 DeepSeek（非流式）
        bot_ans = llm_ds.chat(history)
        save_record(user_id, current_msg, bot_ans)
        return {
            "user_id": user_id,
            "question": current_msg,
            "answer": bot_ans,
            "history": history,
        }
    except Exception as e:
        log.error(f"【/chat 异常】{repr(e)}")
        return {"code": 500, "msg": "服务内部异常", "detail": str(e)}


# ============================================
# 模块8：知识库管理接口 /kb/*
# ============================================
@app.post("/kb/upload")
async def kb_upload(file: UploadFile = File(...)):
    """
    上传文档：存到 UPLOAD_DIR，落 documents 表（chunk_count=0）。
    返回 doc_id，前端再调 /kb/ingest/{doc_id} 触发向量化。
    UploadFile：FastAPI 的异步上传文件句柄（依赖 python-multipart）。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名为空")
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".txt", ".md", ".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 .txt / .md / .pdf")

    # 用时间戳前缀避免同名文件覆盖
    saved_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    saved_path = cfg.UPLOAD_DIR / saved_name
    with open(saved_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc_id = rag_ingest.create_document(
        title=os.path.splitext(file.filename)[0],
        source=saved_name,
        file_type=ext.lstrip("."),
    )
    return {
        "code": 200,
        "msg": "上传成功，尚未向量化",
        "data": {"doc_id": doc_id, "source": saved_name, "file_type": ext.lstrip(".")},
    }


@app.post("/kb/ingest/{doc_id}")
def kb_ingest(doc_id: int):
    """对已上传文档执行 切块 + 向量化 + 入库。"""
    doc = rag_ingest.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc["chunk_count"] and doc["chunk_count"] > 0:
        # 已入库则先清掉旧块再重建（便于重复向量化）
        with engine.connect() as conn:
            conn.execute(
                text("DELETE FROM document_chunks WHERE doc_id=:did"), {"did": doc_id}
            )
            conn.commit()

    path = cfg.UPLOAD_DIR / doc["source"]
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"原始文件不存在: {doc['source']}")
    text_content, _ = rag_ingest.read_file(str(path))
    n = rag_ingest.ingest_document(doc_id, text_content)
    return {"code": 200, "msg": "向量化完成", "data": {"doc_id": doc_id, "chunk_count": n}}


@app.get("/kb/documents")
def kb_list_documents():
    """文档列表。"""
    docs = rag_ingest.list_documents()
    # created_at 转 ISO 字符串，便于前端展示
    for d in docs:
        if d.get("created_at") is not None:
            d["created_at"] = d["created_at"].isoformat()
    return {"code": 200, "msg": "ok", "data": docs}


@app.delete("/kb/documents/{doc_id}")
def kb_delete_document(doc_id: int):
    """删除文档（级联删块）；同时尝试删原文件。"""
    doc = rag_ingest.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    rag_ingest.delete_document(doc_id)
    # 删原文件（失败不影响）
    try:
        path = cfg.UPLOAD_DIR / doc["source"]
        if path.exists():
            path.unlink()
    except Exception as e:
        log.warning(f"删原文件失败: {repr(e)}")
    return {"code": 200, "msg": "已删除", "data": {"doc_id": doc_id}}


@app.get("/")
def root():
    return {
        "msg": "本地聊天机器人后端服务已启动（含 RAG）",
        "interfaces": [
            "POST /chat",
            "POST /chat/stream",
            "GET  /history",
            "GET  /history/delete",
            "POST /kb/upload",
            "POST /kb/ingest/{doc_id}",
            "GET  /kb/documents",
            "DELETE /kb/documents/{doc_id}",
        ],
    }


# ============================================
# 模块9：程序入口
# ============================================
if __name__ == "__main__":
    import uvicorn
    log.info(f"启动后端服务: DB={safe_dsn()}, CHAT=DeepSeek/{cfg.CHAT_MODEL}, EMBED={cfg.EMBED_MODEL}")
    uvicorn.run("postger_reserve_chat:app", host="127.0.0.1", port=8000)
