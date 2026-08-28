# RAG 知识库聊天机器人 —— 需求文档

> 版本：v2.0 ｜ 更新日期：2026-08-25
> 状态：已实现（本需求文档用于说明项目要用的东西、实现的功能与边界）

---

## 一、项目概述

### 1.1 项目名称
RAG 知识库聊天机器人（RAG = Retrieval-Augmented Generation，检索增强生成）

### 1.2 项目目标
构建一个可上传私有文档、基于文档内容智能问答的聊天机器人：
- 用户上传文档（TXT/MD/PDF）后，AI 依据文档内容回答，而非通用知识。
- 全程本地私有部署 + 官方大模型 API 相结合，兼顾隐私与回答质量。
- 支持流式输出、思考过程展示、引用溯源、多用户历史隔离。

### 1.3 解决痛点
| 痛点 | 说明 |
|------|------|
| 大模型知识截止 | 通用 LLM 不知道企业/个人私有文档内容 |
| 数据隐私合规 | 机密资料不能上传到云 LLM API（本地向量化 + 本地存储） |
| 微调成本高 | 文档更新频繁，微调/蒸馏不经济，RAG 可即时更新知识 |

---

## 二、用户需求

### 2.1 目标用户
- 需要基于私有文档问答的个人/团队（企业内部知识库、学习资料、产品文档）。
- 前端提供 Web 界面，无需命令行操作。

### 2.2 核心使用场景
1. 上传文档到知识库（拖拽/选择文件）。
2. 在聊天框提问，AI 流式回答，思考过程折叠展示。
3. 回答标注引用来源（来自哪份文档）。
4. 切换用户 ID，查看各自独立的聊天历史。

---

## 三、功能需求

### 3.1 功能清单总览

| 编号 | 功能 | 优先级 | 状态 |
|------|------|--------|------|
| FR-1 | 流式对话（SSE 逐字输出） | P0 | ✅ 已实现 |
| FR-2 | 思考过程实时展示（可折叠） | P0 | ✅ 已实现 |
| FR-3 | 文档上传与解析（TXT/MD/PDF） | P0 | ✅ 已实现 |
| FR-4 | 文本切块 + 向量化入库 | P0 | ✅ 已实现 |
| FR-5 | RAG 检索增强回答 | P0 | ✅ 已实现 |
| FR-6 | 检索引用溯源标注 | P1 | ✅ 已实现 |
| FR-7 | 聊天历史持久化 + 多用户隔离 | P1 | ✅ 已实现 |
| FR-8 | 知识库文档管理（列表/删除） | P1 | ✅ 已实现 |
| FR-9 | 低相关检索降级为纯对话 | P1 | ✅ 已实现 |

### 3.2 详细功能需求

**FR-1 流式对话**
- 用户发送消息后，回答以 SSE 事件流逐 token 推送至前端。
- 前端每 4 个 token 节流刷新一次界面，避免渲染卡顿。
- 请求超时阈值：后端 300s（思考模型生成慢）。

**FR-2 思考过程展示**
- 模型返回 `reasoning_content`（思考过程）与 `content`（正式回答）两个字段。
- 思考过程包在 `<thinking>` 标签中，Gradio `reasoning_tags` 自动折叠为「Reasoning」块。
- 思考过程不存入聊天历史（避免污染后续上下文）。

**FR-3 文档上传与解析**
- 支持 `.txt` / `.md` / `.pdf` 三种格式，前端文件选择器限定类型。
- 上传流程：`POST /kb/upload` 落库拿 doc_id → `POST /kb/ingest/{doc_id}` 触发解析入库。

**FR-4 文本切块与向量化**
- 按段落（`\n\n`）切块，超长段落用固定窗口（500 字符 + 50 overlap）二次切分。
- 逐块调用向量化模型（nomic-embed-text，768 维）生成 embedding。
- 向量与文本存入 `document_chunks` 表。

**FR-5 RAG 检索增强回答**
- 检索链路：问题 → 向量化 → pgvector HNSW 余弦相似检索候选（TOP_K×5）→ MMR 重排取 TOP_K。
- 过滤相似度 < 0.3 的低相关块；过滤后为空则降级为纯对话（不阻断聊天）。
- 检索到的资料以 `[资料N: 标题]` 格式拼接进 system prompt，引导模型引用编号。

**FR-6 引用溯源**
- 后端 `done` 事件返回 `sources` 数组（doc_id / 标题 / 块内容预览）。
- 前端在回答末尾渲染「引用来源」Markdown 列表。

**FR-7 历史持久化**
- 聊天记录存 `chat_history` 表，按 `user_id` 隔离。
- 前端切换 user_id 时自动加载该用户历史。
- 发给 LLM 的历史仅取最近 10 条（控制 prompt 长度）。

**FR-8 知识库管理**
- 前端「知识库管理」折叠面板：上传、列表（doc_id/标题/类型/块数/时间）、删除。
- 删除文档级联删除其所有 chunk 与原始文件。

---

## 四、非功能需求

| 类别 | 需求 |
|------|------|
| 性能 | 首 token 出现时间尽量快；历史查询走 DB LIMIT 不拉全量 |
| 并发 | 后端异步（async/await）处理，支持并发 SSE 连接 |
| 稳定性 | 任一环节异常（RAG 失败/DB 断开/API 报错）降级不崩溃 |
| 安全 | 数据库密码走环境变量，不硬编码；API Key 存 .env 不入库不提交 |
| 可维护性 | 模块化分层（config/db/rag/llm），统一 loguru 日志 |
| 可移植性 | Windows 本地部署，venv 隔离依赖 |

---

## 五、技术需求（这个项目要用到的东西）

### 5.1 技术栈总览

| 层次 | 技术选型 | 用途 |
|------|----------|------|
| 前端 | Gradio 6.x | Web 界面：聊天 + 知识库管理 |
| 后端 | FastAPI + sse-starlette | API 服务 + SSE 流式推送 |
| 聊天 LLM | DeepSeek 官方 API（`deepseek-v4-flash`） | 对话生成（含 reasoning 思考） |
| 向量化 | Ollama `nomic-embed-text`（768 维） | 文档/问题 → embedding |
| 数据库 | PostgreSQL 18 + pgvector 0.8.6 | 历史存储 + 向量检索（HNSW 索引） |
| ORM/驱动 | SQLAlchemy + psycopg2 | 数据库访问 |
| 流式 HTTP | httpx（异步，复用连接池） | 调用 DeepSeek API / Ollama |
| LLM 框架 | LlamaIndex（llama-index-llms-deepseek） | 非流式 LLM 调用封装 |
| 日志 | loguru | 统一日志输出 |

### 5.2 Python 依赖清单

| 包 | 用途 |
|----|------|
| `gradio>=6.0` | 前端界面 |
| `fastapi` | 后端 Web 框架 |
| `uvicorn` | ASGI 服务器 |
| `sse-starlette` | SSE 流式响应 |
| `sqlalchemy` | ORM / 连接池 |
| `psycopg2-binary` | PostgreSQL 驱动 |
| `httpx` | 异步 HTTP（调 DeepSeek/Ollama） |
| `requests` | 同步 HTTP（前端拉取 /kb/documents 等） |
| `pypdf` | PDF 文本提取 |
| `loguru` | 日志 |
| `llama-index-core` | LlamaIndex 核心 |
| `llama-index-llms-deepseek` | DeepSeek LLM 适配器 |

### 5.3 外部服务与环境要求

| 项 | 要求 |
|----|------|
| Python | >= 3.8（本项目 venv 内 3.14 可用） |
| PostgreSQL | 18.x，需安装 pgvector 扩展（Windows 用官方 v0.8.6-pg18 预编译包） |
| Ollama | 本地服务（仅用于向量化，聊天已迁移到 DeepSeek API） |
| Ollama 模型 | `nomic-embed-text`（`ollama pull nomic-embed-text`） |
| DeepSeek | 官方 API Key（platform.deepseek.com 申请），模型 `deepseek-v4-flash` |

### 5.4 关键配置项（.env）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OLLAMA_BASE` | Ollama 服务地址（向量化用） | `http://127.0.0.1:11434` |
| `CHAT_MODEL` | 聊天模型 | `deepseek-v4-flash` |
| `DEEPSEEK_API_KEY` | DeepSeek 官方 Key（必填） | 空 |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | `https://api.deepseek.com` |
| `EMBED_MODEL` | 向量化模型 | `nomic-embed-text` |
| `EMBED_DIM` | 向量维度 | `768` |
| `DB_URL` | PostgreSQL 连接串 | `postgresql://postgres:***@127.0.0.1:5432/myname` |
| `TOP_K` | 检索返回块数 | `4` |
| `UPLOAD_DIR` | 上传目录 | `data/uploads` |

---

## 六、系统架构

```
用户浏览器
    │
    ▼
Gradio 前端 (web.py, :7860)
    │  HTTP/SSE
    ▼
FastAPI 后端 (postger_reserve_chat.py, :8000)
    │
    ├──> src/llm/deepseek_client.py ──> DeepSeek 官方 API (deepseek-v4-flash)
    │        ├── 流式：httpx 直连解析 SSE（content + reasoning_content）
    │        └── 非流式：LlamaIndex
    │
    ├──> src/rag/embedding.py ──> Ollama nomic-embed-text (768 维)
    │
    └──> src/db/connection.py ──> PostgreSQL + pgvector (HNSW)
             ├── chat_history      # 聊天历史
             ├── documents         # 文档元数据
             └── document_chunks   # 文本块 + embedding 向量
```

---

## 七、接口需求

### 7.1 聊天与历史

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat/stream` | 流式聊天（SSE：message / done / error 事件） |
| POST | `/chat` | 非流式聊天（兜底） |
| GET | `/history` | 分页查询聊天历史 `{code, data, total, page, page_size}` |
| GET | `/history/delete` | 清空指定用户历史 |

### 7.2 知识库管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/kb/upload` | 上传文档 → 落 documents 表，返回 doc_id |
| POST | `/kb/ingest/{doc_id}` | 切块 + 向量化 + 写入 document_chunks |
| GET | `/kb/documents` | 文档列表 |
| DELETE | `/kb/documents/{id}` | 删除文档（级联删块 + 删原文件） |

### 7.3 SSE 事件协议

```
event: message    # 流式 token，data: {"token": "...", "thinking": "...", "done": false}
event: done       # 结束，data: {"full_response": "...", "sources": [...]}
event: error      # 异常，data: {"error": "..."}
```

---

## 八、RAG 工作流程

```
【入库】文档上传 → 按段落切块(超长二次切分) → 逐块向量化(nomic-embed-text 768维)
        → 写入 document_chunks(embedding 列 + HNSW 索引)

【检索】用户问题 → 向量化 → HNSW 余弦相似召回 TOP_K×5 候选
        → MMR 重排去冗余取 TOP_K → 过滤 score<0.3 噪声块
        → 拼 [资料N: 标题] 进 system prompt

【生成】system(资料) + 最近10条历史 + 当前问题 → DeepSeek API 流式生成
        → SSE 逐 token 下发前端(thinking 折叠 + content 展示)
        → done 事件返回 sources 引用溯源 → 后台写库(仅存 content)
```

---

## 九、边界与已知限制

1. **向量化依赖本地 Ollama**：DeepSeek 官方无 embedding 接口，检索向量化仍走 Ollama nomic-embed-text；Ollama 未启动时 RAG 降级为纯对话。
2. **已入库文档不自动重切块**：优化分块策略后，旧文档需删除后重新上传。
3. **无鉴权**：user_id 为前端输入字符串，未做 JWT 鉴权（单人本地使用场景）。
4. **无并发任务队列**：大文件入库为同步阻塞接口，未引入 Celery。
5. **依赖版本冲突**：安装 llama-index 时 huggingface-hub 被降级（0.36.2），gradio 要求 >=1.2.0，重启前端前需升级修复。
