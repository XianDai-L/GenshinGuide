# -*- coding: utf-8 -*-
"""
Agent 层：LLM 路由 + 本地工具调用（function calling）。

配置了 API key 时，规则检索层没答出来的复杂问题交给本模块：
LLM 理解问题 -> 调用本地查询工具（真实数据）-> 组织回答。
规则层命中/无 key 时由调用方直接走离线检索，不经过这里。
"""

import json
import time

import llm
import rag

SYSTEM_PROMPT = (
    "你是原神攻略助手。玩家可能问角色、武器、圣遗物、材料、天赋升级等信息。"
    "必须遵守："
    "1) 只能依据工具返回的真实数据回答，禁止编造任何数值或内容；"
    "2) 每个实体/每个信息点调用一次工具；跨实体的问题（如'怎么养'）要多次调用工具逐步查询；"
    "3) 工具没返回的信息，明确说'资料里没有'，不要推测；"
    "4) 回答简洁、条理清晰；结合多个工具结果时按'角色→武器→圣遗物→材料'组织；"
    "5) 查询角色的专武、下位替代武器、推荐圣遗物、圣遗物备选、圣遗物主词条、"
    "配队、命座使用率等推荐配置时，必须调用 search_relation 工具；"
    "search_entity 只用于查档案字段（元素、特效、突破材料、星级等），"
    "不要用 search_entity 去查推荐类信息。"
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_entity",
            "description": (
                "按名字查询角色/武器/圣遗物/材料的档案信息。可指定 field 只取部分内容，"
                "field 支持：突破材料、天赋、天赋升级、命座、特效、副属性、基础攻击力、"
                "2件套、4件套、获取途径、元素、武器类型、星级、生日、简介。"
                "注意：查询专武/推荐圣遗物/配队等推荐配置请用 search_relation，"
                "本工具只查档案字段。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "实体名称，如 胡桃、护摩之杖、炽烈的炎之魔女、霓裳花"},
                    "field": {"type": "string", "description": "可选，字段词；留空返回完整档案"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_talent_materials",
            "description": (
                "查询角色天赋升级材料。不传 tier 返回三天赋全升满（2~10级）总量；"
                "传 tier（如 '6升7'、'1升10'、'升到9'）返回对应等级段消耗。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "character": {"type": "string", "description": "角色名，如 胡桃"},
                    "tier": {"type": "string", "description": "可选，等级段描述，如 '6升7'、'升到9'"},
                },
                "required": ["character"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_material_schedule",
            "description": "查询材料的获取途径、刷取时间（周几）、刷取秘境。适用于天赋书、突破材料、角色升级材料。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "材料名，如 「勤劳」的教导、霓裳花、未熟之玉"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_relation",
            "description": (
                "查询角色的推荐配置信息。kind 支持：专武、下位替代武器、推荐圣遗物、"
                "圣遗物备选、圣遗物主词条、玩法、命座使用率、配队。"
                "注意：玩法字段是英文原文，需要你用中文转述给玩家。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "character": {"type": "string", "description": "角色名，如 胡桃"},
                    "kind": {"type": "string", "description": "查询类型，见函数描述"},
                },
                "required": ["character", "kind"],
            },
        },
    },
]


def _execute_tool(name, args):
    """执行本地查询工具，返回文本结果。失败返回说明（让 LLM 知道查不到）。"""
    try:
        if name == "search_entity":
            target = (args.get("name") or "").strip()
            field = (args.get("field") or "").strip()
            if not target:
                return "参数错误：缺少 name"
            query = f"{target} {field}" if field else target
            hit = rag.search_character(query)
            if not hit:
                return f"未找到「{target}」"
            content, src = hit
            # 传了 field 却返回完整档案：说明该字段不在档案中（如把"专武"当字段），
            # 提示改用 search_relation，避免 LLM 误以为"没有"
            if field:
                first = content.splitlines()[0] if content.splitlines() else ""
                if any(k in first for k in ("（角色）", "（武器）", "（圣遗物）", "（材料）", "（成就）")):
                    content = "\n".join(l for l in content.splitlines() if not l.startswith("【图标】"))
                    return (
                        f"「{target}」的档案里没有字段「{field}」。\n"
                        f"若想查专武/下位替代武器/推荐圣遗物/配队等推荐配置，请改用 search_relation 工具。\n"
                        f"完整档案如下：\n{content}"
                    )
            content = "\n".join(l for l in content.splitlines() if not l.startswith("【图标】"))
            return f"[{src}]\n{content}"
        if name == "get_talent_materials":
            char = (args.get("character") or "").strip()
            tier = (args.get("tier") or "").strip()
            if not char:
                return "参数错误：缺少 character"
            hit = rag.search_character(char)
            if not hit:
                return f"未找到角色「{char}」"
            content, _ = hit
            raw = rag.get_talent_field(content)
            if not raw:
                return f"角色「{char}」没有天赋升级数据"
            q = f"{char} 天赋{tier}" if tier else f"{char} 天赋材料"
            parsed = rag.parse_talent_upgrade(q, raw)
            return parsed or raw
        if name == "get_material_schedule":
            mname = (args.get("name") or "").strip()
            if not mname:
                return "参数错误：缺少 name"
            info = rag.get_material_info(mname, ["获取途径", "刷取时间", "刷取秘境"])
            if not info:
                return f"未找到材料「{mname}」"
            return "、".join(f"{k}：{v}" for k, v in info.items())
        if name == "search_relation":
            char = (args.get("character") or "").strip()
            kind = (args.get("kind") or "").strip()
            # 与规则层共用同一实现，保证两条链路答案一致
            out = rag.search_relation(char, kind)
            if not out:
                return f"关系表里没有「{char}」的{kind}数据"
            return out
    except Exception as e:  # noqa: BLE001
        return f"工具执行出错：{e}"
    return "未知工具"


def run_agent(question, history=None, settings=None, max_rounds=4, context=None):
    """Agent 主流程：LLM 路由 + 工具调用循环。

    context：规则层已检索到的资料（可选）。传入时让 Agent 优先基于它回答，
    资料不足才调用工具补充，避免"有答案却答不存在"。

    返回 (status, result, trace)。status 为 'ok' 或 'error'。
    trace 记录每轮工具调用（名称/参数/结果长度/耗时），供观测日志。
    有 key 才可调用；失败抛异常由调用方兜底。
    """
    s = settings or llm.load_settings()
    if not s.get("api_key"):
        raise RuntimeError("未配置 API Key")

    trace = {"tools": [], "rounds": 0, "status": "ok"}
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        for h in history[-5:]:
            if h.get("user"):
                messages.append({"role": "user", "content": h["user"]})
            if h.get("assistant"):
                messages.append({"role": "assistant", "content": h["assistant"]})
    user_content = question
    if context:
        user_content = (
            "【已检索资料】\n"
            f"{context}\n\n"
            f"【问题】\n{question}\n\n"
            "资料若已能回答请直接作答；资料不足再调用工具补充。"
        )
    messages.append({"role": "user", "content": user_content})

    for _ in range(max_rounds):
        trace["rounds"] += 1
        msg = llm._chat(s["provider"], s["api_key"], s.get("model"), messages, tools=TOOLS)
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            content = (msg.get("content") or "").strip()
            if content:
                return "ok", content, trace
            return "error", "模型没有返回内容", trace
        # 回填 assistant 的工具调用，再逐个执行
        messages.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": tool_calls,
        })
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            t0 = time.time()
            result = _execute_tool(name, args)
            trace["tools"].append({
                "name": name,
                "args": args,
                "result_len": len(result),
                "ms": int((time.time() - t0) * 1000),
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result,
            })
        # 继续循环，让模型基于工具结果生成回答或再次调用
    # 超过轮数：汇总已查到的资料，不丢信息
    tool_msgs = [m["content"] for m in messages if m.get("role") == "tool"]
    if tool_msgs:
        trace["status"] = "truncated"
        return "ok", "已查询到以下资料：\n\n" + "\n\n".join(tool_msgs), trace
    trace["status"] = "error"
    return "error", "Agent 处理轮数超限", trace


def is_available(settings=None):
    """Agent 是否可用（配置了 API key）。"""
    s = settings or llm.load_settings()
    return bool(s.get("api_key"))
