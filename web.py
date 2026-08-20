# ============================================
# 模块1：导入依赖
# ============================================
import os
from datetime import datetime

import gradio as gr
import requests
import json

# 后端服务地址（FastAPI 默认 8000 端口）
BACKEND = "http://127.0.0.1:8000"

# ============================================
# 模块2：辅助函数
# ============================================

def normalize_history(history):
    """
    归一化 Chatbot 历史格式为 Gradio 6.x messages 格式：
    [ {"role": "user"|"assistant", "content": "..."} , ... ]
    兼容旧的 [[user, bot], ...] tuple 格式。
    """
    if not history:
        return []
    normalized = []
    for item in history:
        if isinstance(item, dict) and "role" in item and "content" in item:
            # 已是 messages 格式
            normalized.append({"role": item["role"], "content": item["content"] or ""})
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            # 旧 tuple 格式 [user_content, bot_content]
            u, b = item[0], item[1]
            if u:
                normalized.append({"role": "user", "content": str(u)})
            if b:
                normalized.append({"role": "assistant", "content": str(b)})
    return normalized


def load_history(user_id):
    """从后端加载历史，返回 messages 字典格式（Gradio 6.x 要求）"""
    try:
        resp = requests.get(
            "http://127.0.0.1:8000/history",
            params={"user_id": user_id, "page": 1, "page_size": 50},
            timeout=10,
        )
        resp.raise_for_status()#常用请求状态码检查判断，确保请求成功
        data = resp.json()
        raw = data.get("data", []) if isinstance(data, dict) else []
        # 后端格式: [{"user_content":..., "bot_content":...}, ...]
        # 转成 messages 格式
        messages = []
        for item in raw:
            u = item.get("user_content")
            b = item.get("bot_content")
            if u:
                messages.append({"role": "user", "content": str(u)})
            if b:
                messages.append({"role": "assistant", "content": str(b)})
        return messages
    except Exception as e:
        print(f"[load_history] 加载失败: {e}")
        return []


# ============================================
# 模块2扩展：知识库管理辅助函数
# ============================================
# 后端已提供：
#   POST   /kb/upload          上传文件 → 落 documents 表，返回 doc_id
#   POST   /kb/ingest/{doc_id} 切块 + 向量化 + 入 document_chunks 表
#   GET    /kb/documents       文档列表
#   DELETE /kb/documents/{id}  删除文档（级联删块）
# 下列函数把这些后端能力接到前端 UI。
#
# 【requests multipart 上传】
#   requests.post(url, files={"file": (上传文件名, 文件句柄)})
#   会以 multipart/form-data 发送，后端 FastAPI 用 UploadFile = File(...) 接收。


def refresh_doc_list():
    """拉取已入库文档列表，返回 Dataframe 行数据（list of list）。"""
    try:
        resp = requests.get(f"{BACKEND}/kb/documents", timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        # 顺序与 Dataframe headers 对齐
        rows = []
        for d in data:
            rows.append([
                d.get("id"),
                d.get("title"),
                d.get("file_type"),
                d.get("chunk_count"),
                d.get("created_at"),
            ])
        return rows
    except Exception as e:
        print(f"[refresh_doc_list] 加载失败: {e}")
        return []


def upload_and_ingest(file):
    """
    一键上传并入库：上传文件 → 后端落库拿 doc_id → 触发切块+向量化。
    入参 file：gr.File(type="filepath") 返回的临时文件路径字符串（保留原扩展名）。
    返回：(状态 Markdown, 刷新后的文档列表)。
    """
    if not file:
        return "⚠️ 请先选择文件", refresh_doc_list()
    try:
        # gr.File(type="filepath") 返回临时路径，扩展名保留，据此判断类型
        ext = os.path.splitext(file)[1].lower()
        if ext not in (".txt", ".md", ".pdf"):
            return f"⚠️ 不支持的类型 {ext}，仅支持 .txt / .md / .pdf", refresh_doc_list()

        # 构造带扩展名的上传文件名（后端按扩展名校验、用文件名取标题）
        filename = f"upload_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"

        # 1) multipart 上传：files={"file": (filename, 句柄)}
        with open(file, "rb") as f:
            resp = requests.post(
                f"{BACKEND}/kb/upload",
                files={"file": (filename, f)},
                timeout=120,
            )
        resp.raise_for_status()
        doc_id = resp.json()["data"]["doc_id"]

        # 2) 触发切块 + 向量化入库（大文档慢，timeout 放宽到 300s）
        resp2 = requests.post(
            f"{BACKEND}/kb/ingest/{doc_id}",
            timeout=300,
        )
        resp2.raise_for_status()
        n = resp2.json()["data"]["chunk_count"]
        return (
            f"✅ 入库成功：doc_id={doc_id}，切分 **{n}** 块。\n\n"
            f"AI 现在可在回答你的问题时检索此文档并引用。",
            refresh_doc_list(),
        )
    except Exception as e:
        return f"❌ 上传/入库失败: {e}", refresh_doc_list()


def delete_kb_document(doc_id):
    """删除指定文档（级联删块 + 删原文件），返回状态 + 刷新后的列表。"""
    if not doc_id:
        return "⚠️ 请输入要删除的 doc_id", refresh_doc_list()
    try:
        # requests.delete 发 HTTP DELETE 请求
        resp = requests.delete(
            f"{BACKEND}/kb/documents/{int(doc_id)}",
            timeout=30,
        )
        resp.raise_for_status()
        return f"✅ 已删除文档 {int(doc_id)}", refresh_doc_list()
    except Exception as e:
        return f"❌ 删除失败: {e}", refresh_doc_list()


def chat_with_stream(message, history, user_id):
    """
    流式生成器：调后端 /chat/stream SSE，逐 token yield 回 messages 格式历史。
    入参:
      message  - 用户新输入
      history  - Chatbot 当前 messages（可能为 None）
      user_id  - 用户ID（直接用入参，不再覆盖！）

    注意:
      这里所有 yield 之前都必须对 history 做浅拷贝（返回新对象）。
      Gradio 内部通过引用变化判断是否需要刷新 UI，就地修改 dict/list
      会导致 Gradio 检测不到变化、UI 不更新（之前的 Bug）。
    """

    def snapshot(h):
        """对 messages 做一层浅拷贝，保证每次 yield 返回新的引用。"""
        return [dict(m) for m in h]#把history中的每个元素转换为字典，确保每次 yield 返回新的引用，避免修改原对象

    def build_content(thinking, answer):
        """把思考内容包进 <thinking> 标签，交给 Chatbot reasoning_tags 折叠展示。"""
        if thinking:
            return f"<thinking>{thinking}</thinking>{answer}"
        return answer

    # 归一化历史格式（防 None / 旧 tuple 混入）
    history = normalize_history(history or [])

    # 追加用户消息 + 助手空占位（messages 格式）
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": ""})
    yield snapshot(history)   # 立即把「用户消息」推上去

    try:
        response = requests.post(
            "http://127.0.0.1:8000/chat/stream",
            params={"user_id": user_id, "current_msg": message},
            stream=True,
            timeout=600,          # deepseek-r1 思考阶段可能 1-2 分钟
        )
        response.raise_for_status()#检查 HTTP 请求的响应状态，如果状态码是 4xx 或 5xx（即请求失败），就抛出异常，用于快速发现网络或接口错误。

    except Exception as e:
        history[-1]["content"] = f"[连接后端失败] {e}"
        yield snapshot(history)
        return

    full_answer = ""
    full_thinking = ""   # 思考过程（前端展示用，不存库）
    # 节流：每隔 N 个 token 或 M 秒 yield 一次，避免 Gradio 刷新过快
    TICK_STEP = 4
    tokens_since_yield = 0

    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue
        # SSE 行: "event: xxx" 或 "data: xxx"
        if line.startswith("event:"):
            event = line[6:].strip()
            if event == "error":
                history[-1]["content"] = build_content(full_thinking, full_answer) + "\n[服务端出错]"
                yield snapshot(history)
            elif event == "done":
                # event: done 的 data 里有 full_response，下面 data 行解析时会兜底
                pass
            continue
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()   # 兼容 "data: " 或 "data:"
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # event: done 会附带 data: {"full_response":"..."}
        final_resp = data.get("full_response")
        if final_resp is not None:
            full_answer = final_resp      # 以最终 full_response 为准（兜底）
            history[-1]["content"] = build_content(full_thinking, full_answer)
            yield snapshot(history)
            break

        # 正常增量：token（正式回答）+ thinking（思考）
        token = data.get("token") or data.get("content") or ""
        thinking = data.get("thinking") or ""
        if token:
            full_answer += token
        if thinking:
            full_thinking += thinking
        if token or thinking:
            tokens_since_yield += 1
            history[-1]["content"] = build_content(full_thinking, full_answer)
            # 节流：每 4 个 token 或换行触发一次刷新，减少 Gradio 重渲染压力
            if tokens_since_yield >= TICK_STEP or "\n" in (token + thinking):
                tokens_since_yield = 0
                yield snapshot(history)

        # done 标志
        if data.get("done"):
            break

    # 循环结束后最终补一次，保证 state 100% 与 full_answer 同步
    history[-1]["content"] = build_content(full_thinking, full_answer)
    yield snapshot(history)


# ============================================
# 模块3：构建Gradio界面
# ============================================
def build_demo():
    with gr.Blocks(title="本地AI聊天机器人") as demo:
        gr.Markdown("# 🤖 本地AI聊天机器人（流式输出 + RAG）")

        # ===== 顶栏：用户ID =====
        with gr.Row():
            user_id_input = gr.Textbox(
                label="用户ID",
                value="001",
                scale=1,
            )
            refresh_btn = gr.Button("🔄 刷新历史", scale=0, min_width=160)

        # ===== 聊天窗口（Gradio 6.x 默认 messages 格式）=====
        # value 用可调用函数：app load 时按 user_id_input.value 拉初始历史
        chatbot = gr.Chatbot(
            label="对话窗口",
            height=500,
            value=lambda: load_history("001"),
            # deepseek-r1 等思考模型：把 <thinking>...</thinking> 抽成可折叠的「Reasoning」块，
            # 思考过程实时展示，不再干等几十秒以为卡死
            reasoning_tags=[("<thinking>", "</thinking>")],
        )

        # ===== 输入框 =====
        msg = gr.Textbox(
            label="输入你的问题",
            placeholder="例如：你是谁？（回车发送）",
            submit_btn=True,
        )

        # ===== 知识库管理（上传文档供 AI 检索回答）=====
        # gr.Accordion：可折叠面板，默认折叠不占聊天空间
        # gr.File：文件上传组件，file_types 限制扩展名，type="filepath" 返回路径字符串
        with gr.Accordion("📚 知识库管理（上传文档供 AI 检索回答）", open=False):
            gr.Markdown(
                "上传 `.txt / .md / .pdf` 文档，后端会自动 **解析 → 切块 → 向量化入库**。\n"
                "入库后，AI 回答你的问题时会自动检索这些文档并引用（RAG）。"
            )
            with gr.Row():
                kb_file = gr.File(
                    label="选择文档",
                    file_types=[".txt", ".md", ".pdf"],
                    type="filepath",
                    scale=4,
                )
                upload_btn = gr.Button("📤 上传并入库", variant="primary", scale=1)
            upload_status = gr.Markdown("", label="上传状态")

            gr.Markdown("### 已入库文档")
            with gr.Row():
                refresh_kb_btn = gr.Button("🔄 刷新列表", scale=1)
                del_doc_id = gr.Number(label="删除文档ID", value=None, scale=2)
                del_kb_btn = gr.Button("🗑️ 删除", scale=1)
            doc_table = gr.Dataframe(
                headers=["doc_id", "标题", "类型", "块数", "创建时间"],
                value=refresh_doc_list,
                interactive=False,
                wrap=True,
            )

        # ===== 事件绑定 =====
        # 1) 发送消息：流式生成
        msg.submit(
            chat_with_stream,
            inputs=[msg, chatbot, user_id_input],
            outputs=chatbot,
        ).then(
            lambda: "", inputs=None, outputs=msg,  # 发送完清空输入
        )

        # 2) 刷新按钮：按当前 user_id 重新加载历史
        refresh_btn.click(
            load_history,
            inputs=[user_id_input],
            outputs=chatbot,
        )

        # 3) user_id 变化时自动刷新
        user_id_input.change(
            load_history,
            inputs=[user_id_input],
            outputs=chatbot,
        )

        # 4) 知识库：一键上传并入库（上传 → 切块 → 向量化）
        upload_btn.click(
            upload_and_ingest,
            inputs=[kb_file],
            outputs=[upload_status, doc_table],
        )
        # 5) 知识库：刷新文档列表
        refresh_kb_btn.click(
            refresh_doc_list,
            inputs=None,
            outputs=doc_table,
        )
        # 6) 知识库：删除指定文档（级联删块）
        del_kb_btn.click(
            delete_kb_document,
            inputs=[del_doc_id],
            outputs=[upload_status, doc_table],
        )

    return demo


def start_web_chat():
    demo = build_demo()
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        show_error=True,
    )


# ============================================
# 模块4：程序入口
# ============================================
if __name__ == "__main__":
    start_web_chat()
