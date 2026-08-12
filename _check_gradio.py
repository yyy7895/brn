# -*- coding: utf-8 -*-
"""查 gradio 版本 + Chatbot 默认 type"""
import gradio as gr
import inspect

print("gradio version:", gr.__version__)

# 看 Chatbot 构造函数 signature 里 type 的默认值
sig = inspect.signature(gr.Chatbot.__init__)
t_param = sig.parameters.get("type")
print("Chatbot.type 默认值:", t_param.default if t_param else "无 type 参数")

# 直接造一个看 type
c = gr.Chatbot(label="t")
print("实例 .type:", getattr(c, "type", "无 type 属性"))
