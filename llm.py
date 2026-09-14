# -*- coding: utf-8 -*-
"""
里程碑 4：LLM 增强模块（可选）

玩家自填 API Key 后，把检索到的片段交给 LLM 组织成自然语言回答。
未配置 key 时不影响离线检索功能。

支持服务商（OpenAI 兼容接口）：
- deepseek：DeepSeek（deepseek-chat）
- qwen：通义千问（qwen-plus）
- glm：智谱（glm-4-flash 免费）

配置保存在 settings.json（本地）。
"""

import json
import os

import paths

ROOT = paths.RESOURCE_DIR  # 兼容旧引用
SETTINGS_FILE = paths.SETTINGS_FILE

PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "label": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
    "glm": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-flash",
    },
}

DEFAULT_SETTINGS = {"provider": "deepseek", "api_key": "", "model": "", "use_history": False}


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        s = dict(DEFAULT_SETTINGS)
        s.update({k: v for k, v in data.items() if k in s})
        return s
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)


def save_settings(provider=None, api_key=None, model=None, use_history=None):
    s = load_settings()
    if provider is not None:
        s["provider"] = provider
    if api_key is not None:
        s["api_key"] = api_key.strip()
    if model is not None:
        s["model"] = model.strip()
    if use_history is not None:
        s["use_history"] = bool(use_history)
    paths.ensure_parent(SETTINGS_FILE)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    return s


def is_configured():
    s = load_settings()
    return bool(s.get("api_key"))


def _chat(provider, api_key, model, messages, timeout=30, tools=None):
    """调用 OpenAI 兼容的 chat completions 接口。返回 message dict 或抛异常。

    tools 传入时支持 function calling，返回的 message 可能带 tool_calls。
    """
    import requests

    info = PROVIDERS.get(provider)
    if not info:
        raise ValueError(f"未知服务商: {provider}")
    url = f"{info['base_url']}/chat/completions"
    payload = {
        "model": model or info["default_model"],
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1600,
    }
    if tools:
        payload["tools"] = tools
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]


def generate_answer(question, context, history=None, settings=None):
    """用 LLM 把检索片段组织成简洁回答。history 为最近几轮对话（可选）。失败抛异常。"""
    s = settings or load_settings()
    if not s.get("api_key"):
        raise RuntimeError("未配置 API Key")
    system = (
        "你是原神攻略助手。根据提供的资料回答玩家的问题。"
        "要求：1) 只依据资料内容回答，资料里没有的明确说不知道；"
        "2) 回答简洁、准确、条理清晰，不要编造数值；3) 资料是多条时挑选与问题最相关的。"
        "若玩家在追问且省略了主语，请结合最近对话判断指的是谁。"
    )
    messages = [{"role": "system", "content": system}]
    if history:
        for h in history[-5:]:
            if h.get("user"):
                messages.append({"role": "user", "content": h["user"]})
            if h.get("assistant"):
                messages.append({"role": "assistant", "content": h["assistant"]})
    messages.append({"role": "user", "content": f"【资料】\n{context}\n\n【问题】\n{question}"})
    msg = _chat(s["provider"], s["api_key"], s.get("model"), messages)
    return msg.get("content") or ""
