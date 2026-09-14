# -*- coding: utf-8 -*-
"""一键打包成可运行 exe（Windows / onedir）。

用法：
    python build.py

产物：
    dist/GenshinGuide/GenshinGuide.exe
    dist/GenshinGuide/{docs,index,models,data}/   ← 内置数据（随包分发）

说明：
- 打包依赖 PyInstaller；若使用独立构建环境，请在该环境里执行本脚本
- 数据放 exe 同级而非 _internal/：ChromaDB / fastembed 需要可写的资源目录
- 首次启动会在 %APPDATA%/GenshinGuide 生成 settings.json / logs / 运行时数据
"""

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist", "GenshinGuide")

# 需要随包分发的内置数据（相对项目根 -> 相对 exe 同级）
DATA_ITEMS = [
    ("docs", "docs"),
    ("index", "index"),
    ("models", "models"),
    (os.path.join("data", "relations.json"), os.path.join("data", "relations.json")),
    (os.path.join("data", "achievements.json"), os.path.join("data", "achievements.json")),
    # 合规文件必须随包分发：数据版权声明与许可范围
    ("NOTICE", "NOTICE"),
    ("LICENSE", "LICENSE"),
]


def rotate_dist():
    """把上一次的产物改名备份。

    PyInstaller 的 COLLECT 会直接删除旧的输出目录，而某些受限环境不允许
    批量删除；改成改名（不删除文件），保证重复打包可正常进行。
    备份目录为 dist/_prev_<时间戳>，可手动清理。
    """
    if not os.path.isdir(DIST):
        return
    backup = os.path.join(ROOT, "dist", "_prev_%s" % time.strftime("%Y%m%d_%H%M%S"))
    os.rename(DIST, backup)
    print("[备份] 旧产物 ->", os.path.relpath(backup, ROOT))


def run_pyinstaller():
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           os.path.join(ROOT, "build.spec")]
    print(">>>", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


def copy_data():
    for src_rel, dst_rel in DATA_ITEMS:
        src = os.path.join(ROOT, src_rel)
        dst = os.path.join(DIST, dst_rel)
        if not os.path.exists(src):
            print(f"[跳过] 源不存在：{src_rel}（可先跑 setup_data.py 生成）")
            continue
        if os.path.isdir(src):
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        print(f"[数据] {src_rel} -> {dst_rel}")


def main():
    rotate_dist()
    if run_pyinstaller() != 0:
        print("PyInstaller 打包失败，请检查上面的报错。")
        return 1
    copy_data()
    print("\n打包完成：", os.path.join(DIST, "GenshinGuide.exe"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
