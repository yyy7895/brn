# RAG 知识库聊天机器人

基于 RAG（检索增强生成）的知识库聊天机器人，支持文档上传、向量化检索、流式对话、思考过程展示和历史记录持久化。聊天生成走 DeepSeek 官方 API，向量化走本地 Ollama。

## 项目架构

```
用户 <-> Gradio 前端 (web.py, :7860) <-> FastAPI 后端 (postger_reserve_chat.py, :8000)
                                        |
                          +-------------+-------------+
                          |             |             |
                  DeepSeek API      PostgreSQL      RAG 检索
                 (deepseek-v4-       (聊天历史)      (pgvector)
                   flash 聊天)     + Ollama embedding
                                  (nomic-embed-text)
```

## 核心功能

- **流式对话**：DeepSeek 官方 API（deepseek-v4-flash），思考过程实时折叠展示
- **RAG 知识库**：上传文档（TXT/MD/PDF）→ 段落切块 → Ollama 向量化 → pgvector 余弦相似检索 + MMR 重排 → 注入 Prompt
- **引用溯源**：回答标注资料编号，结尾展示引用来源列表
- **聊天历史**：PostgreSQL 持久化存储，支持多用户隔离
- **多用户支持**：通过 user_id 区分不同用户的对话历史

## 技术栈

| 组件 | 技术 |
|------|------|
| 前端 | Gradio 6.x（流式输出 + reasoning 折叠） |
| 后端 | FastAPI + sse-starlette（SSE 流式推送） |
| 聊天 LLM | DeepSeek 官方 API（deepseek-v4-flash，经 LlamaIndex + httpx） |
| 向量化 | Ollama nomic-embed-text（768 维） |
| 数据库 | PostgreSQL 18 + pgvector（HNSW 向量索引） |
| ORM | SQLAlchemy |

## 项目结构

```
RAG/
├── web.py                      # Gradio 前端：聊天 + 知识库管理面板
├── postger_reserve_chat.py     # FastAPI 后端：聊天 + RAG + KB 接口
├── requirements                # 依赖清单
├── pyproject.toml              # 项目依赖声明
├── .env.example                # 环境变量模板
├── .env                        # 本地配置（DB_URL、DEEPSEEK_API_KEY，不入库）
├── src/
│   ├── config.py               # 集中配置（从 .env 读取）
│   ├── db/
│   │   ├── connection.py       # 数据库连接池（SQLAlchemy engine）
│   │   ├── init_db.py          # 启动幂等建表
│   │   └── schema.sql          # RAG 表结构（documents / document_chunks + HNSW）
│   ├── llm/
│   │   ├── __init__.py
│   │   └── deepseek_client.py  # DeepSeek 封装（流式 httpx / 非流式 LlamaIndex）
│   ├── rag/
│   │   ├── chunking.py         # 文本切块（按段落 + 超长窗口二次切）
│   │   ├── embedding.py        # 向量化（调 Ollama /api/embeddings）
│   │   ├── retriever.py        # pgvector 余弦相似检索 + MMR 重排
│   │   └── ingest.py           # 文档入库：读取→切块→向量化→写入DB
│   └── utils/
│       └── logger.py           # 日志（loguru）
└── data/uploads/               # 上传文件存放目录
```

## 快速开始

### 1. 环境准备

- Python >= 3.8
- PostgreSQL 18（需安装 pgvector 扩展，Windows 用官方 v0.8.6-pg18 预编译包）
- [Ollama](https://ollama.com/) 本地服务（仅向量化用）
- DeepSeek 官方 API Key（[platform.deepseek.com](https://platform.deepseek.com) 申请）

### 2. 安装依赖

```bash
pip install -r requirements
# 或使用 pyproject.toml
pip install .
```

### 3. 拉取 Ollama 向量化模型

```bash
ollama pull nomic-embed-text
```

### 4. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 PostgreSQL 连接信息 + DEEPSEEK_API_KEY
```

关键配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OLLAMA_BASE` | Ollama 服务地址（向量化用） | `http://127.0.0.1:11434` |
| `CHAT_MODEL` | 聊天模型 | `deepseek-v4-flash` |
| `DEEPSEEK_API_KEY` | DeepSeek 官方 Key（必填） | 空 |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | `https://api.deepseek.com` |
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

> 注意：必须使用项目 venv 的 python（依赖装在 venv 里），系统 python 启动后接口会缺依赖报错。

```bash
# 终端1：启动后端（默认 8000 端口）
.venv\Scripts\python postger_reserve_chat.py

# 终端2：启动前端（默认 7860 端口）
.venv\Scripts\python web.py
```

浏览器访问 `http://127.0.0.1:7860` 即可使用。

## RAG 工作流程

```
【入库】文档上传 → 按段落切块(超长段 500 字符 + 50 overlap 二次切)
    → 逐块向量化(nomic-embed-text, 768维)
    → 写入 document_chunks 表(HNSW 索引)

【检索】用户提问 → 问题向量化
    → pgvector 余弦相似召回 TOP_K×5 候选
    → MMR 重排去冗余取 TOP_K → 过滤 score<0.3 噪声块
    → 拼接 [资料N: 标题] 注入 Prompt

【生成】system(资料) + 最近10条历史 + 问题 → DeepSeek API 流式生成
    → SSE 逐 token 下发(thinking 折叠 + content 展示)
    → done 事件返回 sources 引用溯源 → 后台写库(仅存 content)
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat/stream` | 流式聊天（SSE：message/done/error） |
| POST | `/chat` | 非流式聊天（兜底） |
| GET | `/history` | 分页获取聊天历史 |
| GET | `/history/delete` | 清空指定用户历史 |
| POST | `/kb/upload` | 上传文档到知识库 |
| POST | `/kb/ingest/{doc_id}` | 触发切块 + 向量化入库 |
| GET | `/kb/documents` | 列出知识库文档 |
| DELETE | `/kb/documents/{id}` | 删除知识库文档（级联删块） |

## License

MIT
