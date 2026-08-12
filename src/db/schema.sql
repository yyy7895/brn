-- ============================================================
-- RAG 知识库建表脚本（幂等，可重复执行）
-- 依赖：PostgreSQL + pgvector 扩展（已通过 CREATE EXTENSION vector 安装）
-- 注意：本脚本只建 RAG 相关表（documents / document_chunks），
--       不动已有的 chat_history 表。
-- ============================================================

-- 1) 启用 pgvector 扩展（若已启用则跳过）
CREATE EXTENSION IF NOT EXISTS vector;

-- 2) 文档元信息表
CREATE TABLE IF NOT EXISTS documents (
    id          BIGSERIAL PRIMARY KEY,        -- 自增主键
    title       TEXT NOT NULL,                -- 文档标题
    source      TEXT,                         -- 原始文件名 / URL
    file_type   TEXT,                         -- 文件类型：txt / md / pdf
    chunk_count INT DEFAULT 0,                -- 切块数量，入库后更新
    created_at  TIMESTAMPTZ DEFAULT now()     -- 创建时间（带时区）
);

-- 3) 文档切块 + 向量表
CREATE TABLE IF NOT EXISTS document_chunks (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT REFERENCES documents(id) ON DELETE CASCADE,
                                            -- 外键：删文档时级联删其所有切块
    chunk_index INT NOT NULL,                -- 该块在文档中的序号（从 0 起）
    content     TEXT NOT NULL,                -- 块的文本内容
    embedding   vector(768),                  -- nomic-embed-text 输出 768 维向量
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 4) 向量相似度索引（HNSW 图索引）
--    HNSW：分层可导航小世界图索引，查询召回率高、无需调 lists/probes，
--    对小数据集也稳定（不像 IVFFlat 在数据少时易漏召回）。
--    vector_cosine_ops：余弦相似度算子类（配合 <=> 余弦距离算子）。
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- 5) 普通索引：按文档查其所有切块时加速
CREATE INDEX IF NOT EXISTS idx_chunks_doc_id
    ON document_chunks(doc_id);
