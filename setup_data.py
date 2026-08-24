# -*- coding: utf-8 -*-
"""
一键准备数据：拉取基础数据 + 关系表 + 构建 RAG 索引。

用法：
    python setup_data.py

说明：
- 数据源为安柏计划（gi.yatta.moe）公开 API，境外站点，可能需要代理
- 基础数据全量拉取约 25 分钟（含礼貌限速 1 秒/次）
- 可分步执行（某步失败后单独重跑该命令即可）：
    python fetch_ambr.py          # 基础数据（角色/武器/圣遗物/材料/成就）
    python fetch_guides.py        # 关系表（专武/圣遗物/主词条/配队等）
    python rag.py rebuild         # 构建 RAG 向量索引
"""

import subprocess
import sys


def run(cmd):
    print(f"\n>>> {' '.join(cmd)}")
    return subprocess.call(cmd)


def main():
    print("开始准备数据……")
    steps = [
        (["python", "fetch_ambr.py"], "基础数据（约 25 分钟）"),
        (["python", "fetch_guides.py"], "关系表（约 2 分钟）"),
        (["python", "rag.py", "rebuild"], "RAG 索引"),
    ]
    ok = 0
    for cmd, desc in steps:
        print(f"\n=== {desc} ===")
        if run(cmd) == 0:
            ok += 1
        else:
            print(f"步骤失败：{desc}，可单独重跑该命令")
    print(f"\n完成：{ok}/{len(steps)} 步成功。")
    if ok < len(steps):
        sys.exit(1)


if __name__ == "__main__":
    main()
