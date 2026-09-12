# -*- coding: utf-8 -*-
"""评测脚本：用评测集量化问答质量。

用法：
    python eval/eval_qa.py            # 规则层评测（免费、离线、快）
    python eval/eval_qa.py --agent    # 额外跑 Agent 题（需配置 API Key，少量花费）

判定规则（双判定，避免"整档返回也算对"的宽松误判）：
- must：期望关键词**全部**出现才算对（空表示该题应无回答）
- must_not：出现任一即为错（用于检测夹带无关字段 / 空泛长回答）
- 兼容旧格式：无 must 时回退读取 expected

输出：正确率、命中分布、过长回答统计、失败清单（用于定位数据缺失 / 匹配缺陷 / 检索问题）。
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
GAMEDATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "gamedata"
)

LONG_ANSWER_LEN = 500  # 超过此长度的回答计入"过长"统计（贴近问题应短而准）


def count_placeholder_residue():
    """数据体检：成就条件里不应残留 {param0}。

    抓取时 `fetch_ambr.py` 会用 detail.progress 还原它；一旦有人把成就抓取
    改回 ambr 的模型（模型没有 progress 字段），占位符就会重新泄漏到回答里。
    """
    bad = 0
    if os.path.isdir(GAMEDATA_DIR):
        for fn in os.listdir(GAMEDATA_DIR):
            if not (fn.startswith("成就_") and fn.endswith(".txt")):
                continue
            try:
                with io.open(os.path.join(GAMEDATA_DIR, fn), encoding="utf-8") as f:
                    bad += f.read().count("{param")
            except OSError:
                continue
    return bad


def load_set(path):
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def judge(text, must, must_not=None):
    """must 全部命中且 must_not 全部未命中才算对；must 为空表示应无回答。"""
    s = "" if text is None else str(text).strip()
    if not must:
        return s == ""
    if not s:
        return False
    if any(k not in s for k in must):
        return False
    if must_not and any(k in s for k in must_not):
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="评测问答质量")
    parser.add_argument("--agent", action="store_true", help="额外跑 Agent 题（需 API Key）")
    parser.add_argument("--set", default=SET_FILE, help="评测集路径")
    args = parser.parse_args()

    rows = load_set(args.set)
    stats = {"total": 0, "correct": 0, "skip": 0, "long": 0}
    hit_dist = {}
    fails = []

    for row in rows:
        q = row["question"]
        qtype = row.get("type", "any")
        must = row.get("must", row.get("expected", [])) or []
        must_not = row.get("must_not", []) or []
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
                fails.append((qtype, q, must, must_not, f"异常：{str(e)[:60]}"))
                stats["total"] += 1
                hit_dist[actual] = hit_dist.get(actual, 0) + 1
                continue
        else:
            text, hit, _trace = main_mod._search_reply(q)
            actual = hit or "none"

        hit_dist[actual] = hit_dist.get(actual, 0) + 1
        stats["total"] += 1
        if text is not None and len(str(text)) > LONG_ANSWER_LEN:
            stats["long"] += 1
        if judge(text, must, must_not):
            stats["correct"] += 1
        else:
            snippet = (str(text) or "")[:70].replace("\n", " ")
            fails.append((qtype, q, must, must_not, snippet))

    print("\n=== 评测结果 ===")
    print(f"题目总数：{stats['total']}（跳过 {stats['skip']}）")
    if stats["total"]:
        print(f"答对：{stats['correct']}（正确率 {stats['correct'] * 100 // stats['total']}%）")
        print(f"过长回答（>{LONG_ANSWER_LEN}字）：{stats['long']}（空泛指标，越少越好）")
    print("命中分布：", " ".join(f"{k}={v}" for k, v in sorted(hit_dist.items())))
    residue = count_placeholder_residue()
    print(f"{'[WARN]' if residue else '[OK]'} 数据体检：成就条件残留 {{param0}} = {residue} 处（应为 0）")
    if fails:
        print("\n--- 失败清单 ---")
        for qtype, q, must, must_not, snippet in fails:
            print(f"[{qtype}] {q}")
            print(f"   必含：{must}   不得含：{must_not}")
            print(f"   实际：{snippet}")
    else:
        print("\n全部通过！")
    print()


if __name__ == "__main__":
    main()
