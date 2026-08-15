# RAG 知识库聊天机器人

基于 RAG（检索增强生成）的本地知识库聊天机器人，支持文档上传、向量化检索、流式对话和历史记录持久化。

## 项目架构

```
用户 <-> Gradio 前端 (web.py) <-> FastAPI 后端 (postger_reserve_chat.py)
                                        |
                          +-------------+-------------+
                          |             |             |
                     Ollama LLM    PostgreSQL    RAG 检索
                   (deepseek-r1)  (聊天历史)    (pgvector)
```

## 核心功能

- **流式对话**：基于 Ollama + deepseek-r1:7b，支持思考过程实时展示
- **RAG 知识库**：上传文档（TXT/MD/PDF）→ 文本切块 → 向量化 → pgvector 余弦相似检索 → 注入 Prompt
- **聊天历史**：PostgreSQL 持久化存储，支持多用户隔离
- **多用户支持**：通过 user_id 区分不同用户的对话历史

## 技术栈

| 组件 | 技术 |
|------|------|
| 前端 | Gradio 6.x（流式输出 + reasoning 折叠） |
| 后端 | FastAPI + sse-starlette（SSE 流式推送） |
| LLM | Ollama（deepseek-r1:7b 聊天 / nomic-embed-text 向量化） |
| 数据库 | PostgreSQL + pgvector（向量相似检索） |
| ORM | SQLAlchemy |

## 项目结构

```
RAG/
├── web.py                      # Gradio 前端：聊天界面
├── postger_reserve_chat.py     # FastAPI 后端：聊天 + RAG 接口
├── build_knowledge.py          # 知识库构建入口
├── config.yaml                 # 爬虫配置（学习通题目爬虫）
├── pyproject.toml              # 项目依赖声明
├── .env.example                # 环境变量模板
├── sample_kb.txt               # 示例知识库文本
├── src/
│   ├── config.py               # 集中配置（从 .env 读取）
│   ├── db/
│   │   ├── connection.py       # 数据库连接池（SQLAlchemy engine）
│   │   ├── init_db.py          # 建表脚本入口
│   │   └── schema.sql          # RAG 表结构（documents / document_chunks）
│   ├── rag/
│   │   ├── chunking.py         # 文本切块（固定窗口 + overlap）
│   │   ├── embedding.py        # 向量化（调 Ollama /api/embeddings）
│   │   ├── retriever.py        # pgvector 余弦相似检索
│   │   └── ingest.py           # 文档入库：读取→切块→向量化→写入DB
│   └── utils/
│       └── logger.py           # 日志（loguru）
└── data/uploads/               # 上传文件存放目录
```

## 快速开始

### 1. 环境准备

- Python >= 3.8
- PostgreSQL（需安装 pgvector 扩展）
- [Ollama](https://ollama.com/) 本地服务

### 2. 安装依赖

```bash
pip install -r requirements
# 或使用 pyproject.toml
pip install .
```

### 3. 拉取 Ollama 模型

```bash
ollama pull deepseek-r1:7b
ollama pull nomic-embed-text
```

### 4. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 PostgreSQL 连接信息
```

关键配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OLLAMA_BASE` | Ollama 服务地址 | `http://127.0.0.1:11434` |
| `CHAT_MODEL` | 聊天模型 | `deepseek-r1:7b` |
| `EMBED_MODEL` | 向量化模型 | `nomic-embed-text` |
| `EMBED_DIM` | 向量维度 | `768` |
| `DB_URL` | PostgreSQL 连接串 | - |
| `TOP_K` | 检索返回块数 | `4` |
| `UPLOAD_DIR` | 上传目录 | `data/uploads` |

### 5. 初始化数据库

```bash
# 在 PostgreSQL 中执行 src/db/schema.sql，或启动后端时自动建表
psql -U postgres -d your_db -f src/db/schema.sql
```

### 6. 启动服务

```bash
# 终端1：启动后端（默认 8000 端口）
python postger_reserve_chat.py

# 终端2：启动前端（默认 7860 端口）
python web.py
```

浏览器访问 `http://127.0.0.1:7860` 即可使用。

## RAG 工作流程

```
文档上传 → 读取文本 → 切块(chunk_size=500, overlap=50)
    → 逐块向量化(nomic-embed-text, 768维)
    → 写入 document_chunks 表

用户提问 → 问题向量化
    → pgvector 余弦相似检索 TOP_K 个相关块
    → 拼接为 context 注入 Prompt
    → Ollama 流式生成回答
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat/stream` | 流式聊天（SSE） |
| GET | `/history` | 获取聊天历史 |
| POST | `/upload` | 上传文档到知识库 |
| GET | `/documents` | 列出知识库文档 |
| DELETE | `/documents/{id}` | 删除知识库文档 |

## License

MIT
