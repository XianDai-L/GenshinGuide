# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

不要单独运行本文件，统一用 `python build.py`：
它会先按本 spec 打包，再把内置数据（index/models/docs/data）拷到 exe 同级。

体积控制要点：
- 只打 main.py 入口，抓取脚本（fetch_*/setup_data）与 eval 显式排除，不会进包
- chromadb / fastembed / onnxruntime / tokenizers / huggingface_hub 含原生库与
  数据文件，PyInstaller 自带 hook 覆盖不全，这里统一 collect_all
- 排除未用到的 Qt 模块（WebEngine/Qt3D/Quick/Multimedia…）
"""

import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(SPECPATH)

datas = []
binaries = []
hiddenimports = ["PIL", "rfc3987_syntax"]

# 含 Rust/C++ 扩展、动态库或数据文件，必须整包收集
# rfc3987_syntax / jsonschema_specifications 是 chromadb 依赖链上用到的
# 「纯数据」包（.lark 语法 / JSON Schema），漏掉会在冻结环境直接报文件不存在
for _pkg in (
    "chromadb",
    "fastembed",
    "onnxruntime",
    "tokenizers",
    "huggingface_hub",
    "posthog",
    "rfc3987_syntax",
    "jsonschema_specifications",
    "referencing",
):
    try:
        _d, _b, _h = collect_all(_pkg)
    except Exception:  # 某些包缺失时不应中断打包
        continue
    datas += _d
    binaries += _b
    hiddenimports += _h

# 未用到的重量级模块，排除以控制体积
excludes = [
    # 仅开发者使用，不进用户包
    "ambr", "eval", "fetch_ambr", "fetch_guides", "setup_data",
    # 科学计算 / 交互式环境
    "tkinter", "pandas", "matplotlib", "IPython", "jupyter", "notebook",
    # 未使用的 Qt 模块
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtGraphs", "PySide6.QtHelp",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtNfc",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPositioning",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSpatialAudio", "PySide6.QtStateMachine",
    "PySide6.QtTest", "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GenshinGuide",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="GenshinGuide",
)
