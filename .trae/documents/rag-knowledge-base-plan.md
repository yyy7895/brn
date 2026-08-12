# RAG 知识库改造方案

## Context（背景与目标）

当前项目 `d:\Users\Lenovo\AppData\Programs Trae CN\爬虫` 是一个学习通题目爬虫，用户在原项目里另起了一摊聊天后端（`postger_reserve_chat.py`）和前端（`web.py`），目前是「Ollama 直聊 + PostgreSQL 存历史」的形态，没有任何 RAG 能力。

用户希望做成 **RAG 知识库聊天**：上传文档 → 切块 → 向量化 → 检索后注入 Prompt → 流式回答。

排查发现现有前后端存在多处阻塞性 Bug（详见下文「问题清单」），即便不上 RAG 也跑不通。本方案分两步走：**先修 Bug 让现有聊天能跑**，**再加 RAG 能力**。

技术选型（已与用户确认）：
- 向量库：**pgvector**（复用现有 PostgreSQL，不引入新服务）
- LLM：继续用 Ollama（`deepseek-r1:7b`）
- Embedding：建议 `bge-m3`（中文效果好，1024 维），备选 `nomic-embed-text`（768 维，更小）

---

## 一、现有问题清单（必须修复）

### 后端 `postger_reserve_chat.py`

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| B1 | **`/history` GET 接口缺失** | 全文 | 前端 `load_history` 调 `GET /history?page=&page_size=` 永远 404，前端启动拿不到历史 |
| B2 | `DB_URL` 隐式字符串拼接 | L19-20 | 易拼错密码，建议改整字符串 + 环境变量 |
| B3 | `get_history` 无分页 | L92-100 | 历史一长就把全部塞进 LLM 上下文，超 token |
| B4 | `/chat` 同步函数里阻塞调 `requests.post` | L109 | 占用线程池，已引入 httpx 应统一 async |
| B5 | 密码硬编码源码 | L19 | 安全隐患，应放 `.env` |
| B6 | 表结构无 RAG 字段 | — | 当前只有 `chat_history` 一张表 |

### 前端 `web.py`

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| F1 | **`user_id` 被覆盖** | L17 `user_id = user_id_input.value` | 用户在界面输入的 ID 永远拿不到，恒为 `"001"` |
| F2 | `user_id_input` 组件未渲染 | L12 vs L73 Blocks | 用户看不到、改不了 ID |
| F3 | Chatbot 格式混乱 | L56 vs L19-21 | `load_history` 返回 `[[u,b],...]`，`chat_with_stream` 追加 `{"role","content"}` 字典，混用 |
| F4 | 模块级创建组件 | L12, L62 | 导入即执行 `load_history("001")`，后端没起就报错 |
| F5 | SSE 解析忽略 event 类型 | L31-43 | `event: error` 的 data 也被当 token 拼接 |
| F6 | `server_port=None` | L99 | Gradio 行为不确定，应明确端口 |
| F7 | `requests` 同步拉 SSE | L24-28 | 流式体验差，建议 `httpx` + async 迭代 |

---

## 二、RAG 架构设计

### 2.1 数据流

```
用户问题
  ↓
[1] 调 Ollama /api/embeddings 把问题向量化
  ↓
[2] pgvector 余弦相似检索 top-k (k=4) 文档块
  ↓
[3] 拼接 system prompt: "依据以下资料回答...{检索到的块}"
  ↓
[4] 调 Ollama /api/chat 流式生成 → SSE 推给前端
  ↓
[5] 完成后存 chat_history
```

### 2.2 PostgreSQL 表结构（pgvector 扩展）

需要先在 PG 里执行（一次性）：

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id          BIGSERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    source      TEXT,                 -- 文件名 / URL
    file_type   TEXT,                 -- txt / md / pdf
    chunk_count INT DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1024),         -- bge-m3 维度；换 nomic 改 768
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON document_chunks(doc_id);
```

### 2.3 新增后端接口

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/kb/upload` | 上传文档（multipart），存原文件到 `data/uploads/`，落 `documents` 表 |
| POST | `/kb/ingest/{doc_id}` | 触发切块 + 向量化 + 入库 |
| GET  | `/kb/documents` | 文档列表 |
| DELETE | `/kb/documents/{doc_id}` | 删除文档（级联删块） |
| GET  | `/history` | **新增**：分页返回 chat_history（修 B1） |

### 2.4 改造 `/chat/stream`

在现有生成器里插入「检索 → 拼 system prompt」：

```python
# 伪代码
query_emb = await embed(message)                       # 调 Ollama /api/embeddings
retrieved = pgv_search(query_emb, top_k=4)             # SQL 余弦检索
context = "\n---\n".join(c.content for c in retrieved)
history.insert(0, {"role": "system",
                   "content": f"依据以下资料回答用户问题：\n{context}"})
# ... 后续 Ollama 流式逻辑不变
```

---

## 三、目录结构与文件清单

### 新增文件

```
src/
├── rag/
│   ├── __init__.py
│   ├── embedding.py      # 调 Ollama /api/embeddings，返回 list[float]
│   ├── chunking.py       # 文本切块：固定窗口 + overlap（中文按字符）
│   ├── retriever.py      # pgvector 余弦检索 top-k
│   └── ingest.py         # 读取上传文件 → 切块 → 嵌入 → 入库
├── db/
│   ├── __init__.py
│   ├── schema.sql        # 上面的建表 DDL
│   ├── init_db.py        # 执行 schema.sql（幂等）
│   └── connection.py     # 复用 engine（从 postger_reserve_chat.py 抽出来）
└── config.py             # 从 .env 读 OLLAMA_URL / DB_URL / EMBED_MODEL 等
.env.example
requirements.txt          # 补全依赖（pyproject.toml 也同步加）
```

### 修改文件

| 文件 | 改动 |
|------|------|
| `postger_reserve_chat.py` | 抽 DB engine 到 `src/db/connection.py`；新增 `/history`、`/kb/*` 路由；`/chat/stream` 插入检索；`/chat` 改 async + httpx；密码读 env |
| `web.py` | 删模块级组件；修 `user_id` 覆盖；Chatbot 用 `type="messages"` 统一字典格式；SSE 解析按 event 分流；新增「知识库管理」Tab（上传/列表/删除）；端口固定 7860 |
| `pyproject.toml` | 补 dependencies：`fastapi`, `uvicorn[standard]`, `sqlalchemy>=2`, `psycopg2-binary`, `pgvector`, `sse-starlette`, `httpx`, `gradio>=4`, `python-multipart`, `pypdf` |
| `requirements.txt`（新建） | 同步一份 pip 可用的 pin 版本，便于不用 uv 的人装 |

### 不动的文件
- `main.py`、`src/modules/*`、`src/utils/*`、`config.yaml`（爬虫那摊保持原样）

---

## 四、依赖与环境

### 需要新装（`.venv` 现有 fastapi/gradio/httpx/sqlalchemy/psycopg2/sse_starlette 已就位）

```bash
pip install pgvector python-multipart pypdf
```

> `pgvector` 是 Python 客户端适配包（给 SQLAlchemy 用 `Vector` 类型）；PostgreSQL 服务端还要 `CREATE EXTENSION vector`（见 schema.sql，需 PG 装了 pgvector 扩展二进制）。

### Ollama 模型

```bash
ollama pull bge-m3              # 或 nomic-embed-text
# deepseek-r1:7b 已有
```

### `.env.example`

```env
OLLAMA_URL=http://127.0.0.1:11434
CHAT_MODEL=deepseek-r1:7b
EMBED_MODEL=bge-m3
EMBED_DIM=1024
DB_URL=postgresql://postgres:yyy007@127.0.0.1:5432/myname
TOP_K=4
UPLOAD_DIR=data/uploads
```

---

## 五、前端布局（web.py 改造后）

```
Blocks("本地AI聊天机器人（RAG增强）")
├── gr.Tab("聊天")
│   ├── gr.Row: [user_id Textbox]              # 真正渲染出来，可改
│   ├── gr.Chatbot(type="messages", height=500)
│   └── gr.Textbox(placeholder="问点什么...")
└── gr.Tab("知识库管理")
    ├── gr.File(label="上传文档", file_types=[".txt",".md",".pdf"])
    ├── gr.Button("开始向量化")
    ├── gr.Dataframe(label="文档列表")          # 调 /kb/documents
    └── gr.Button("删除选中")
```

---

## 六、实施步骤（建议顺序）

1. **修前端 Bug**（F1-F7）+ **后端补 `/history`**（B1）→ 让现有聊天跑通，作为基线
2. **建表 + 装依赖**（pgvector、pypdf、python-multipart）→ 跑 `init_db.py`
3. **写 RAG 模块**（embedding → chunking → retriever → ingest）
4. **加 `/kb/*` 接口 + 前端知识库 Tab**
5. **改造 `/chat/stream` 注入检索结果**
6. **端到端测试**（见下节）

> 第 1 步可以先交付一个可用版本，第 2-5 步增量加 RAG。

---

## 七、验证方法（端到端）

1. **服务启动**
   - 后端：`python postger_reserve_chat.py`（uvicorn 跑 8000）
   - 前端：`python web.py`（gradio 跑 7860）
   - Ollama：`ollama serve`，确认 `bge-m3`、`deepseek-r1:7b` 已 pull

2. **基线聊天**
   - 打开 `http://127.0.0.1:7860`，切换 user_id，问"你是谁"，应流式返回
   - 刷新页面，历史应能加载（验证 `/history` 接口 + Chatbot 格式统一）

3. **知识库**
   - 上传一个 `xxx.txt`（例如几段学习通题目解析）→ 点"开始向量化"→ 文档列表出现 → DB `document_chunks` 有数据
   - 问文档里出现过的内容，回答应引用文档信息；问无关内容，回答正常（不幻觉引用）

4. **删除文档** → `document_chunks` 级联删干净

5. **错误路径**
   - 后端没起时前端不崩（`load_history` 异常被吞，显示空历史）
   - 上传非支持格式 → 友好报错
   - Ollama 不可达 → `/chat/stream` 返回 `event: error`

---

## 八、关键复用点

- 复用现有 `src/utils/logger.py` 的 `log`（loguru）替代 `print`，与爬虫侧日志风格统一
- 复用 `src/modules/config_manager.py` 的 `Config` 单例模式思路，给 RAG 写一个 `src/config.py` 读 `.env`
- 后端 `engine` 抽到 `src/db/connection.py`，`postger_reserve_chat.py` 与新增 RAG 模块共用，避免重复建 engine
- 流式生成器主体逻辑保留，只在前面插检索步骤

---

## 九、风险与权衡

- **pgvector 扩展需 PG 服务端支持**：若用户的 PostgreSQL 没装 pgvector 扩展包，`CREATE EXTENSION vector` 会失败。Windows 上需用带 pgvector 的 PG 安装包或自己编译。若装不上，降级方案：改用 Chroma（但用户已选 pgvector，先按 pgvector 走，若装不上再讨论）
- **`bge-m3` 模型 1.2GB**：首次 pull 较慢，备选 `nomic-embed-text`（270MB，768 维，中文效果稍弱但够用）
- **检索质量**：固定窗口切块对中文题目可能切坏题目边界，后续可升级为按句子/段落切。首版先用 500 字 + 50 字 overlap 的简单切法跑通
- **不改 `pyproject.toml` 的 `[project.name]`**：项目名仍是 `chaoxing-crawler`，聊天/RAG 是附加能力，不重命名项目
