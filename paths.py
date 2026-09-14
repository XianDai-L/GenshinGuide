# -*- coding: utf-8 -*-
"""统一路径管理：区分「只读资源目录」与「可写用户数据目录」。

背景：打包成 exe 后，程序目录可能不可写（如装在 Program Files），且
资源目录可能被放在 PyInstaller 的只读 `_internal/` 里。因此必须把
「随包分发的只读资源」和「运行时产生的用户数据」拆开：

- 只读资源（docs/ index/ models/ data/relations.json data/achievements.json）
  · 源码运行：项目根目录
  · 冻结打包：exe 所在目录（onedir 布局，由 `build.py` 把数据拷到 exe 同级）
    —— 放 exe 同级而非 `_internal/`，是因为 ChromaDB / fastembed 需要写锁文件，
    要求资源目录可写；将来装到 Program Files 时这两处应改为拷到用户目录。

- 可写用户数据（settings.json / logs/ / data 下的运行时文件 / icon_cache/）
  · 源码运行：项目根目录（与改造前完全一致，不破坏开发习惯）
  · 冻结打包：`%APPDATA%/GenshinGuide`（可用环境变量 `GENSHIN_GUIDE_HOME` 覆盖）

判定「冻结」用 `sys.frozen`，这是 PyInstaller / cx_Freeze 的通用标志。
"""

import os
import sys

APP_DIR_NAME = "GenshinGuide"
ENV_HOME = "GENSHIN_GUIDE_HOME"

# 源码模式下的项目根目录（本文件所在目录）
_SOURCE_ROOT = os.path.dirname(os.path.abspath(__file__))


def is_frozen():
    """是否运行在冻结（打包）环境中。"""
    return bool(getattr(sys, "frozen", False))


def resource_dir():
    """资源根目录（打包后为 exe 所在目录）。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _SOURCE_ROOT


def user_dir():
    """可写用户数据根目录（不存在则创建）。"""
    if not is_frozen():
        return _SOURCE_ROOT
    override = os.environ.get(ENV_HOME)
    if override:
        path = os.path.abspath(override)
    else:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, APP_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        # 极端情况下 %APPDATA% 不可写，退化到用户主目录
        path = os.path.join(os.path.expanduser("~"), APP_DIR_NAME)
        os.makedirs(path, exist_ok=True)
    return path


RESOURCE_DIR = resource_dir()
USER_DIR = user_dir()

# ===== 只读资源 =====
DOCS_DIR = os.path.join(RESOURCE_DIR, "docs")
INDEX_DIR = os.path.join(RESOURCE_DIR, "index")
MODELS_DIR = os.path.join(RESOURCE_DIR, "models")
RELATIONS_FILE = os.path.join(RESOURCE_DIR, "data", "relations.json")
ACHIEVEMENTS_FILE = os.path.join(RESOURCE_DIR, "data", "achievements.json")

# ===== 可写用户数据 =====
LOG_DIR = os.path.join(USER_DIR, "logs")
QA_LOG_FILE = os.path.join(LOG_DIR, "qa.jsonl")
SETTINGS_FILE = os.path.join(USER_DIR, "settings.json")
USER_DATA_DIR = os.path.join(USER_DIR, "data")
WINDOW_STATE_FILE = os.path.join(USER_DATA_DIR, "window_state.json")
ICON_CACHE_DIR = os.path.join(USER_DATA_DIR, "icon_cache")
ACH_MANUAL_FILE = os.path.join(USER_DATA_DIR, "achievement_manual.json")
UIAF_STATE_FILE = os.path.join(USER_DATA_DIR, "uiaf_state.json")


def ensure_parent(path):
    """确保给定文件路径的父目录存在。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path
