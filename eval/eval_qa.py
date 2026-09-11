# -*- coding: utf-8 -*-
"""评测脚本：用评测集量化问答质量。

用法：
    python eval/eval_qa.py            # 规则层评测（免费、离线、快）
    python eval/eval_qa.py --agent    # 额外跑 Agent 题（需配置 API Key，少量花费）

判定规则：
- field / keyword / rag / any 题：期望关键词任一出现在回答中即算对
- none 题：应无回答（命中 none）才算对
- agent 题：调用 Agent 后按期望关键词判定

输出：正确率、命中分布、失败清单（用于定位数据缺失 / 匹配缺陷 / 检索问题）。
"""

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent
import main as main_mod

SET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_set.jsonl")


def load_set(path):
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def judge(text, expected):
    """期望关键词任一命中即算对；expected 为空表示应无回答。"""
    if not expected:
        return text is None or str(text).strip() == ""
    if not text:
        return False
    return any(k in str(text) for k in expected)


def main():
    parser = argparse.ArgumentParser(description="评测问答质量")
    parser.add_argument("--agent", action="store_true", help="额外跑 Agent 题（需 API Key）")
    parser.add_argument("--set", default=SET_FILE, help="评测集路径")
    args = parser.parse_args()

    rows = load_set(args.set)
    stats = {"total": 0, "correct": 0, "skip": 0}
    hit_dist = {}
    fails = []

    for row in rows:
        q = row["question"]
        qtype = row.get("type", "any")
        expected = row.get("expected", [])
        if qtype == "agent":
            if not args.agent:
                continue
            if not agent.is_available():
                stats["skip"] += 1
                print(f"[跳过] Agent 未配置 Key：{q}")
                continue
            try:
                status, result, _trace = agent.run_agent(q, max_rounds=4)
                text = result if status == "ok" else None
                actual = "agent"
            except Exception as e:
                text = None
                actual = "agent_error"
                fails.append((qtype, q, expected, f"异常：{str(e)[:60]}"))
                stats["total"] += 1
                hit_dist[actual] = hit_dist.get(actual, 0) + 1
                continue
        else:
            text, hit, _trace = main_mod._search_reply(q)
            actual = hit or "none"

        hit_dist[actual] = hit_dist.get(actual, 0) + 1
        stats["total"] += 1
        if judge(text, expected):
            stats["correct"] += 1
        else:
            snippet = (str(text) or "")[:60].replace("\n", " ")
            fails.append((qtype, q, expected, snippet))

    print("\n=== 评测结果 ===")
    print(f"题目总数：{stats['total']}（跳过 {stats['skip']}）")
    if stats["total"]:
        print(f"答对：{stats['correct']}（正确率 {stats['correct'] * 100 // stats['total']}%）")
    print("命中分布：", " ".join(f"{k}={v}" for k, v in sorted(hit_dist.items())))
    if fails:
        print("\n--- 失败清单 ---")
        for qtype, q, expected, snippet in fails:
            print(f"[{qtype}] {q}")
            print(f"   期望含：{expected}")
            print(f"   实际：{snippet}")
    else:
        print("\n全部通过！")
    print()


if __name__ == "__main__":
    main()
