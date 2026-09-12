# -*- coding: utf-8 -*-
"""
原神攻略悬浮助手 —— 里程碑 1：悬浮窗口壳

目标：在原神（无边框窗口化）中，按住 Alt 释放鼠标后，
可以点击本窗口、拖动、锁定、输入问题并看到回复。

运行：python main.py
依赖：pip install -r requirements.txt
"""

import io
import json
import os
import sys
import threading
import time

# 确保能从任意工作目录（如桌面 bat 双击）导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import achieve_ui
import agent
import knowledge
import llm
import rag

from PySide6.QtCore import Qt, QPoint, QEvent, QTimer, QThread, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QLineEdit,
    QPushButton,
    QListWidget,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QDialog,
    QComboBox,
    QCheckBox,
    QTabWidget,
    QTextEdit,
)

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT_DIR, "logs")
QA_LOG_FILE = os.path.join(LOG_DIR, "qa.jsonl")
DATA_DIR = os.path.join(ROOT_DIR, "data")
WINDOW_STATE_FILE = os.path.join(DATA_DIR, "window_state.json")
ICON_CACHE_DIR = os.path.join(DATA_DIR, "icon_cache")
ICON_SIZE = 44  # 气泡内图标边长（px）
ICON_TIMEOUT = 6  # 图标下载超时（秒）；数据源在境外，无代理时会走满超时
_qa_lock = threading.Lock()

APP_NAME = "原神攻略助手"
MAX_REPLY_LEN = 1200  # 单条回复最大字符数，超长限长+摘要，避免聊天列表渲染截断
MAX_CHAT_ITEMS = 200  # 聊天列表最多保留条数，超出自动清理最早的
BUBBLE_MAX_WIDTH = 250  # 聊天气泡最大宽度（窗口 380 宽，留出左右方向差）
NO_RESULT_MSG = "抱歉，暂时没找到相关攻略。\n（可尝试换个问法；RAG 检索覆盖冷门问题。）"

# 推荐配置类意图 -> 关系表字段（命中即走规则层，免 LLM、毫秒级）
RELATION_INTENT_MAP = {
    "专武": ["专武", "专属武器", "毕业武器", "本命武器"],
    "下位替代武器": ["下位替代武器", "下位替代", "替代武器", "下位武器", "平替武器", "平替"],
    "推荐圣遗物": ["推荐圣遗物", "圣遗物推荐", "用什么圣遗物", "带什么圣遗物", "圣遗物用什么"],
    "圣遗物备选": ["圣遗物备选", "备选圣遗物", "圣遗物替代"],
    "圣遗物主词条": ["圣遗物主词条", "主词条", "词条怎么选", "堆什么", "堆啥", "堆哪些"],
    "命座使用率": ["命座使用率", "命座占比"],
    "配队": ["配队", "阵容", "队友", "和谁搭", "搭配谁", "队伍搭配"],
}
# 「培养」类问题需要一次聚合多个关系字段，否则回答会空泛
# 只收窄到明确的"养成方案"问法，避免"胡桃培养材料"被误判成推荐配置
GROWTH_KEYWORDS = ["怎么养", "怎么练", "怎么培养", "养成方案", "培养方案", "怎么配装"]
GROWTH_KINDS = ["专武", "推荐圣遗物", "圣遗物主词条", "配队"]
# 问"用什么武器"既可能在问武器类型、也可能在问专武 -> 两者都答，避免漏答
WEAPON_HINT_KEYWORDS = ["武器类型", "什么武器", "用什么武器", "拿什么武器"]


def _relation_intents(text):
    """识别推荐配置意图，返回要查的关系表字段列表（未命中返回 []）。"""
    if any(kw in text for kw in GROWTH_KEYWORDS):
        return list(GROWTH_KINDS)
    best, best_len = None, 0
    for kind, kws in RELATION_INTENT_MAP.items():
        for kw in kws:
            if kw in text and len(kw) > best_len:
                best, best_len = kind, len(kw)
    return [best] if best else []

STYLE = """
#overlay {
    background-color: rgba(20, 22, 30, 0.95);
    border: 1px solid rgba(212, 182, 106, 0.28);
    border-radius: 12px;
}
#titleBar {
    background-color: rgba(32, 35, 48, 0.98);
    border-top-left-radius: 11px;
    border-top-right-radius: 11px;
    border-bottom: 1px solid rgba(212, 182, 106, 0.16);
}
#titleLabel { color: #f5e6c8; font-weight: bold; font-size: 14px; }
QPushButton { font-size: 12px; }
QPushButton#lockBtn {
    background: transparent; color: #b8b8c8; border: none;
    padding: 4px 10px; border-radius: 5px;
}
QPushButton#lockBtn:hover { background: rgba(212,182,106,0.18); color: #f0e0b8; }
QPushButton#lockBtn:checked {
    background: rgba(212,182,106,0.3); color: #d4b66a; font-weight: bold;
}
QPushButton#closeBtn {
    background: transparent; color: #b8b8c8; border: none;
    padding: 4px 10px; border-radius: 5px; font-size: 15px;
}
QPushButton#closeBtn:hover { background: rgba(255,90,90,0.28); color: #ff7b7b; }
QScrollArea#chatScroll { background: transparent; border: none; }
QScrollArea#chatScroll > QWidget > QWidget { background: transparent; }
QWidget#chatBox { background: transparent; }
QFrame#bubbleAssistant { background: #333850; border-radius: 10px; }
QFrame#bubbleUser { background: #d4b66a; border-radius: 10px; }
QLabel#bubbleName { color: #9aa0b5; font-size: 10px; }
QLabel#bubbleNameUser { color: #6a5518; font-size: 10px; }
QLabel#bubbleText { color: #eef0f6; font-size: 13px; }
QLabel#bubbleTextUser { color: #23200f; font-size: 13px; }
QPushButton#newMsgBtn {
    background: rgba(212,182,106,0.94); color: #1b1d2b; border: none;
    border-radius: 12px; padding: 5px 12px; font-size: 11px; font-weight: bold;
}
QPushButton#newMsgBtn:hover { background: #e3c87e; }
QLineEdit#inputEdit {
    background: rgba(255,255,255,0.07);
    border: 1px solid rgba(212,182,106,0.3);
    border-radius: 8px; padding: 8px 12px;
    color: #ffffff; font-size: 13px;
}
QLineEdit#inputEdit:focus {
    border: 1px solid rgba(212,182,106,0.7);
    background: rgba(255,255,255,0.1);
}
QPushButton#sendBtn {
    background: #d4b66a; color: #1b1d2b; border: none;
    border-radius: 8px; padding: 8px 16px; font-size: 13px; font-weight: bold;
}
QPushButton#sendBtn:hover { background: #e3c87e; }
QLabel#hintLabel { color: rgba(255,255,255,0.42); font-size: 11px; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
QScrollBar::handle {
    background: rgba(212,182,106,0.28); border-radius: 3px; min-height: 24px;
}
QScrollBar::handle:hover { background: rgba(212,182,106,0.5); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { height: 0; }
"""


class TitleBar(QFrame):
    """标题栏：负责窗口拖动与锁定开关。"""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._drag_offset = None
        self.setFixedHeight(40)
        self.setObjectName("titleBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(8)

        title = QLabel(APP_NAME)
        title.setObjectName("titleLabel")
        layout.addWidget(title)
        layout.addStretch()

        self.lock_btn = QPushButton("锁定")
        self.lock_btn.setObjectName("lockBtn")
        self.lock_btn.setCheckable(True)
        self.lock_btn.clicked.connect(self._on_lock)
        layout.addWidget(self.lock_btn)

        settings_btn = QPushButton("AI")
        settings_btn.setObjectName("lockBtn")
        settings_btn.setToolTip("AI 设置（可选填 API Key）")
        settings_btn.clicked.connect(self.window.open_settings)
        layout.addWidget(settings_btn)

        log_btn = QPushButton("日志")
        log_btn.setObjectName("lockBtn")
        log_btn.setToolTip("查看问答日志与统计（可观测）")
        log_btn.clicked.connect(self.window.open_log_panel)
        layout.addWidget(log_btn)

        self.achieve_btn = QPushButton("补成就")
        self.achieve_btn.setObjectName("lockBtn")
        self.achieve_btn.setCheckable(True)
        self.achieve_btn.setToolTip("切换到补成就模式（总览 / 卡片）")
        self.achieve_btn.clicked.connect(self.window.switch_mode)
        layout.addWidget(self.achieve_btn)

        close_btn = QPushButton("×")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.window.close)
        layout.addWidget(close_btn)

    def _on_lock(self, checked):
        self.window.set_locked(checked)
        self.lock_btn.setText("已锁定" if checked else "锁定")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.window.locked:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        event.accept()


def _truncate_text(text, max_len=MAX_REPLY_LEN):
    """超长回复限长+摘要，避免聊天列表渲染截断。"""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    nl = cut.rfind("\n")
    if nl > max_len * 0.5:
        cut = cut[:nl]
    return cut.rstrip() + "\n……（内容较长，已截断，可追问具体字段）"


def _titled(src, content):
    """给档案内容补一行「实体名（类型）」标题；内容已带标题则原样返回。

    取代原先直接显示文件名（如 【角色_胡桃.txt】），让回复更像人话。
    """
    kind, name = rag.entity_of_source(src)
    if not kind:
        return f"【{os.path.basename(src)}】\n{content}"
    title = f"{name}（{kind}）"
    first = content.splitlines()[0].strip() if content.splitlines() else ""
    if first == title:
        return content
    return f"{title}\n{content}"


def _related_weapon_profile(char, kinds):
    """问「专武」时，顺带给出那把武器的「默认回复」（全部字段 + 图标）。

    只在用户**明确就问专武**时展开（kinds == ["专武"]）；
    「怎么养」这类聚合意图已含 专武 + 圣遗物 + 主词条 + 配队，再展开武器档案会失去重点。
    """
    if kinds != ["专武"]:
        return ""
    weapon = rag.get_relation(char, "专武")
    if not weapon:
        return ""
    hit = rag.search_character(weapon.strip(), apply_default=True, field_match=False)
    if not hit:
        return ""
    content, src = hit
    return "\n\n" + _titled(src, content)


def _search_reply(text):
    """纯检索逻辑（可在后台线程运行）：关系表 -> 字段级精确匹配 -> 关键词库 -> RAG 降级。

    返回 (文本, 命中类型, trace)。命中类型：'field' 精确命中（关系表 / 档案字段 /
    默认回复 / 成就名）/ 'keyword' 关键词库 / 'rag' 向量检索 / None 全部未命中。
    trace 记录各层尝试、耗时、RAG 就绪状态与错误（供日志观测）。
    """
    trace = {"path": [], "rag_ready": rag.is_ready(), "rag_error": None, "matched": None}
    t0 = time.time()

    def _mk(text_, hit_type_, src=None):
        trace["matched"] = src
        trace["ms_rule"] = int((time.time() - t0) * 1000)
        return text_, hit_type_, trace

    def _char_hit(q):
        """档案命中：按「默认回复」策略返回（角色 = 核心 6 项，其他 = 全部字段）。"""
        char = rag.search_character(q, apply_default=True)
        if not char:
            return None
        content, src = char
        reply = _titled(src, content)
        # "用什么武器"既可问武器类型也可问专武 -> 补充专武，避免漏答
        if "武器类型：" in content and any(kw in q for kw in WEAPON_HINT_KEYWORDS):
            ent = rag.entity_of_source(src)[1]
            extra = rag.search_relation(ent, "专武")
            if extra:
                reply += f"\n\n【{ent}·专武】\n{extra}"
        return reply, "field", src

    try:
        # ① 推荐配置（关系表）：专武/下位替代/推荐圣遗物/主词条/配队等，免 LLM 直接答
        intents = _relation_intents(text)
        if intents:
            ent = rag.match_entity(text)
            if ent:
                blocks = []
                for kind in intents:
                    val = rag.search_relation(ent, kind)
                    if val:
                        blocks.append(f"【{ent}·{kind}】\n{val}")
                if blocks:
                    trace["path"].append("关系表")
                    reply = "\n\n".join(blocks)
                    reply += _related_weapon_profile(ent, intents)
                    return _mk(reply, "field", f"relations:{ent}")
                # 识别到推荐类意图但关系表未收录：如实告知，
                # 避免回退到字段排序后答一堆无关字段（看似答了，其实答非所问）
                trace["path"].append("关系表未收录")
                return _mk(
                    f"关系表暂未收录「{ent}」的{intents[0]}数据。\n"
                    "可以改问该角色的元素、武器类型、突破材料等档案字段。",
                    "field",
                    f"relations-miss:{ent}",
                )
        # ② 字段级精确匹配
        if any(kw in text for kw in rag.OBJECTIVE_KEYWORDS):
            trace["path"].append("字段级")
            hit = _char_hit(text)
            if hit:
                return _mk(*hit)
            answer, _, _ = knowledge.find_answer(text)
            if answer:
                trace["path"].append("关键词库")
                return _mk(answer, "keyword")
        else:
            trace["path"].append("关键词库")
            answer, _, _ = knowledge.find_answer(text)
            if answer:
                return _mk(answer, "keyword")
        # ③ RAG 降级：实体档案 -> 具体成就名 -> 全局向量检索
        trace["path"].append("RAG")
        try:
            hit = _char_hit(text)
            if hit:
                return _mk(*hit)
        except Exception as e:
            trace["rag_error"] = f"search_character: {e}"
        # 成就名不是档案文件名，search_character 找不到，需按成就名单独查
        ach = rag.search_achievement(text)
        if ach:
            content, src = ach
            trace["path"].append("成就名")
            return _mk(_titled(src, content), "field", src)
        r0 = time.time()
        results, rerr = rag.search(text, top_k=2)
        trace["rag_ms"] = int((time.time() - r0) * 1000)
        if rerr:
            trace["rag_error"] = rerr
        parts = []
        for chunk, src, score in results:
            if score >= rag.MIN_SCORE:
                parts.append(f"【{src}】\n{chunk}")
        if parts:
            return _mk("以下内容来自攻略库检索：\n\n" + "\n\n".join(parts), "rag", "RAG")
    except Exception as e:
        trace["error"] = str(e)
        return None, None, trace
    return None, None, trace


def _log_qa(trace):
    """把一条问答决策轨迹追加写入 logs/qa.jsonl（线程安全）。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with _qa_lock, io.open(QA_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(trace, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_qa_logs(limit=2000):
    """读取问答日志（最多 limit 条），返回 list[dict]。"""
    if not os.path.isfile(QA_LOG_FILE):
        return []
    rows = []
    try:
        with io.open(QA_LOG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows[-limit:]


class SearchWorker(QThread):
    """后台线程：检索 +（可选）LLM 组织回答，避免首次问答卡死 UI。"""

    finished = Signal(str, str)  # (status, result)

    def __init__(self, question, history=None):
        super().__init__()
        self.question = question
        self.history = history or []

    def run(self):
        t0 = time.time()
        trace = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "question": self.question,
        }
        reply = None
        try:
            context, hit_type, t = _search_reply(self.question)
            trace.update(t)
            trace["hit_type"] = hit_type or "none"
            # 强命中（字段级/关键词库）：规则层已精确回答，直接返回，Agent 不介入
            if hit_type in ("field", "keyword"):
                reply = _truncate_text(context)
                trace["status"] = "ok"
            # 弱命中（完整档案）或未命中/模糊 RAG：配了 key 时交给 Agent 兜底
            elif agent.is_available():
                try:
                    status, result, atrace = agent.run_agent(
                        self.question, history=self.history, context=context
                    )
                    trace["agent"] = atrace
                    if status == "ok" and result:
                        reply = result
                        trace["status"] = "ok"
                except Exception as e:
                    trace["agent"] = {"status": "error", "error": str(e)}
                if reply is None:
                    reply = _truncate_text(context) if context else NO_RESULT_MSG
                    trace["status"] = "ok" if context else "no_result"
            else:
                reply = _truncate_text(context) if context else NO_RESULT_MSG
                trace["status"] = "ok" if context else "no_result"
        except Exception as e:
            trace["status"] = "error"
            trace["error"] = str(e)
            reply = f"抱歉，处理出错了：{str(e)[:60]}"
        trace["reply_len"] = len(reply)
        trace["ms"] = int((time.time() - t0) * 1000)
        _log_qa(trace)
        self.finished.emit("ok", reply)


class SettingsDialog(QDialog):
    """AI 设置对话框：服务商 + API Key + 模型。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 设置")
        self.setFixedWidth(360)
        s = llm.load_settings()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("服务商"))
        self.provider_combo = QComboBox()
        for key, info in llm.PROVIDERS.items():
            self.provider_combo.addItem(f"{info['label']}（{info['default_model']}）", key)
        idx = self.provider_combo.findData(s.get("provider"))
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        layout.addWidget(self.provider_combo)

        layout.addWidget(QLabel("API Key（留空则使用离线模式）"))
        self.key_edit = QLineEdit(s.get("api_key", ""))
        self.key_edit.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.key_edit)

        layout.addWidget(QLabel("模型（留空用默认）"))
        self.model_edit = QLineEdit(s.get("model", ""))
        self.model_edit.setPlaceholderText("deepseek-v4-flash / qwen-plus / glm-4-flash")
        layout.addWidget(self.model_edit)

        self.history_check = QCheckBox("多轮对话历史（最近5轮）")
        self.history_check.setChecked(bool(s.get("use_history", False)))
        layout.addWidget(self.history_check)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        hint = QLabel("Key 只保存在本地 settings.json。未配置时助手保持离线模式。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)

    def _save(self):
        llm.save_settings(
            provider=self.provider_combo.currentData(),
            api_key=self.key_edit.text().strip(),
            model=self.model_edit.text().strip(),
            use_history=self.history_check.isChecked(),
        )
        self.accept()


def build_stats(rows):
    """从问答日志生成统计文本（命中分布/失败TOP/耗时/Agent 工具）。"""
    from collections import Counter

    if not rows:
        return "暂无日志数据。问答后自动记录。"
    n = len(rows)
    hit = Counter(r.get("hit_type") or "none" for r in rows)
    status = Counter(r.get("status") or "unknown" for r in rows)
    lines = [
        f"总问答数：{n}",
        f"命中分布：{'、'.join(f'{k}={v}' for k, v in hit.most_common())}",
        f"状态分布：{'、'.join(f'{k}={v}' for k, v in status.most_common())}",
    ]
    miss = [r for r in rows if (r.get("hit_type") in (None, "none")) or r.get("status") == "no_result"]
    lines.append(f"未找到：{len(miss)} 次（{len(miss) * 100 // n}%）")
    if miss:
        qc = Counter(r.get("question") for r in miss)
        lines.append("未找到问题 TOP10：")
        for q, c in qc.most_common(10):
            lines.append(f"  {c} 次  {q}")
    times = [r.get("ms", 0) for r in rows if isinstance(r.get("ms"), int)]
    if times:
        lines.append(f"平均耗时：{sum(times) // len(times)} ms | 最大：{max(times)} ms")
    not_ready = sum(1 for r in rows if r.get("rag_ready") is False)
    if not_ready:
        lines.append(f"RAG 未就绪时提问：{not_ready} 次（关注预热竞争）")
    tools = Counter()
    agent_calls = 0
    for r in rows:
        for tc in ((r.get("agent") or {}).get("tools") or []):
            tools[tc.get("name", "?")] += 1
            agent_calls += 1
    if agent_calls:
        lines.append(f"Agent 工具调用：{agent_calls} 次")
        for k, v in tools.most_common():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


class LogPanel(QDialog):
    """日志查看 + 统计面板（标题栏"日志"按钮打开）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("日志与统计")
        self.resize(720, 560)
        tabs = QTabWidget(self)
        tabs.addTab(self._build_log_tab(), "问答日志")
        tabs.addTab(self._build_stats_tab(), "统计")
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)

    def _build_log_tab(self):
        widget = QWidget()
        v = QVBoxLayout(widget)
        row = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._reload_logs)
        row.addWidget(refresh_btn)
        row.addStretch()
        v.addLayout(row)
        self.log_list = QListWidget()
        self.log_list.itemDoubleClicked.connect(self._show_detail)
        v.addWidget(self.log_list)
        self._reload_logs()
        return widget

    def _reload_logs(self):
        self.log_list.clear()
        self._rows = list(reversed(_load_qa_logs()[-300:]))  # 最新在前
        for r in self._rows:
            hit = r.get("hit_type") or "none"
            st = r.get("status") or "?"
            ms = r.get("ms", 0)
            agent_mark = " [Agent]" if r.get("agent") else ""
            self.log_list.addItem(
                f"{r.get('ts', '?')} [{st}] 命中={hit} {ms}ms{agent_mark}  {r.get('question', '')}"
            )

    def _show_detail(self, item):
        idx = self.log_list.row(item)
        if not (0 <= idx < len(self._rows)):
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("完整决策轨迹")
        dlg.resize(640, 520)
        lay = QVBoxLayout(dlg)
        te = QTextEdit()
        te.setPlainText(json.dumps(self._rows[idx], ensure_ascii=False, indent=2))
        lay.addWidget(te)
        dlg.exec()

    def _build_stats_tab(self):
        widget = QWidget()
        v = QVBoxLayout(widget)
        refresh_btn = QPushButton("重新统计")
        te = QTextEdit()
        te.setReadOnly(True)
        refresh_btn.clicked.connect(lambda: te.setPlainText(build_stats(_load_qa_logs())))
        te.setPlainText(build_stats(_load_qa_logs()))
        v.addWidget(refresh_btn)
        v.addWidget(te)
        return widget


def _load_window_pos():
    """读取上次保存的窗口坐标；若已不在任何屏幕可见区域内（如拔掉副屏）则返回 None。"""
    try:
        with io.open(WINDOW_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        x, y = int(data["x"]), int(data["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    for screen in QApplication.screens():
        if screen.availableGeometry().contains(QPoint(x + 10, y + 10)):
            return x, y
    return None


def _write_window_pos(x, y):
    """把窗口坐标写入 data/window_state.json（data/ 不入库，不影响仓库）。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with io.open(WINDOW_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"x": int(x), "y": int(y)}, f)
    except OSError:
        pass


ICON_LINE_PREFIX = "图标："


def _split_icon(text):
    """从回复文本里摘出图标 URL，返回 (去掉图标行的正文, url 或 None)。

    图标改用图片控件单独渲染，所以正文里不再保留这行。
    """
    url = None
    keep = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(ICON_LINE_PREFIX):
            candidate = stripped[len(ICON_LINE_PREFIX):].strip()
            if candidate.startswith("http"):
                if url is None:
                    url = candidate
                continue
        keep.append(line)
    return "\n".join(keep).strip("\n"), url


def _icon_cache_path(url):
    """图标本地缓存路径（data/icon_cache/<md5>.png），避免同角色反复下载。"""
    import hashlib

    return os.path.join(ICON_CACHE_DIR, hashlib.md5(url.encode("utf-8")).hexdigest() + ".png")


class IconLoader(QThread):
    """后台下载图标并落盘缓存。

    必须异步：数据源在境外（gi.yatta.moe），无代理时同步请求会一直等到超时，
    把悬浮窗卡住几秒。失败静默（emit None）——图标是锦上添花，不能影响正文。
    """

    loaded = Signal(object)  # 图片字节；失败为 None

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        data = None
        try:
            import requests

            resp = requests.get(self.url, timeout=ICON_TIMEOUT)
            if resp.status_code == 200 and resp.content:
                data = resp.content
        except Exception:
            data = None
        if data:
            try:
                os.makedirs(ICON_CACHE_DIR, exist_ok=True)
                with open(_icon_cache_path(self.url), "wb") as f:
                    f.write(data)
            except OSError:
                pass
        self.loaded.emit(data)


class ChatBubble(QFrame):
    """单条聊天气泡：助手深灰（靠左）/ 用户金色（靠右）。

    气泡内为「发送者名 + 图标（可选）+ 正文」；正文可选中复制。
    dim=True 用于「思考中…」弱化显示。
    """

    def __init__(self, sender, text, is_user=False, dim=False):
        super().__init__()
        self._is_user = is_user
        self._icon_worker = None
        self.setObjectName("bubbleUser" if is_user else "bubbleAssistant")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMaximumWidth(BUBBLE_MAX_WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 8)
        lay.setSpacing(3)
        name = QLabel(sender)
        name.setObjectName("bubbleNameUser" if is_user else "bubbleName")
        # 图标：默认隐藏（隐藏的 widget 不占布局空间），拿到图片再显示
        self.icon_lbl = QLabel()
        self.icon_lbl.setObjectName("bubbleIcon")
        self.icon_lbl.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.icon_lbl.setVisible(False)
        self.text_lbl = QLabel()
        self.text_lbl.setObjectName("bubbleTextUser" if is_user else "bubbleText")
        self.text_lbl.setWordWrap(True)
        self.text_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(name)
        lay.addWidget(self.icon_lbl)
        lay.addWidget(self.text_lbl)
        self.set_text(text, dim=dim)

    def set_text(self, text, dim=False):
        """更新正文（用于「思考中…」占位气泡替换为正式回复）。"""
        body, url = _split_icon(text or "")
        self.text_lbl.setStyleSheet("color:#9aa0b5;" if dim else "")
        self.text_lbl.setText(body)
        if url and not self._is_user:
            self._load_icon(url)

    def _load_icon(self, url):
        path = _icon_cache_path(url)
        if os.path.isfile(path):  # 命中缓存：直接读本地，不联网
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self._show_icon(pixmap)
                return
        self._icon_worker = IconLoader(url, self)
        self._icon_worker.loaded.connect(self._on_icon_loaded)
        self._icon_worker.start()

    def _on_icon_loaded(self, data):
        if not data:
            return  # 下载失败：静默隐藏，正文照常
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self._show_icon(pixmap)

    def _show_icon(self, pixmap):
        self.icon_lbl.setPixmap(
            pixmap.scaled(ICON_SIZE, ICON_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.icon_lbl.setVisible(True)


class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.locked = False
        self._history = []  # 多轮对话历史（最多 5 轮，用于 LLM 上下文）
        self._rows = []  # 聊天气泡行 widget（用于超限清理最早消息）
        self._at_bottom = True  # 聊天区是否停在底部（S1 自动滚动依据）
        self._want_bottom = True  # 是否跟随到底（用户上滑则置否，回到底部恢复）
        self._pending_bubble = None  # 「思考中」占位气泡引用
        self.setObjectName("overlay")
        self.setWindowTitle(APP_NAME)
        self._build_ui()
        self._apply_flags()
        self.setStyleSheet(STYLE)

        # 窗口位置持久化：移动后防抖保存，避免拖动时高频写盘
        self._pos_timer = QTimer(self)
        self._pos_timer.setSingleShot(True)
        self._pos_timer.timeout.connect(self._save_window_pos)
        self._restore_or_place()

        self.achieve_panel.back_to_chat.connect(self._exit_achieve_mode)
        self._append_welcome()

        self.show()
        self.raise_()

        # 定时保持置顶，防止被游戏窗口压下去
        self._top_timer = QTimer(self)
        self._top_timer.timeout.connect(self._keep_on_top)
        self._top_timer.start(1500)

    def _apply_flags(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint      # 无边框
            | Qt.WindowStaysOnTopHint   # 置顶
            | Qt.Tool                   # 不占任务栏
        )
        self.setAttribute(Qt.WA_TranslucentBackground)   # 圆角半透明
        self.setAttribute(Qt.WA_ShowWithoutActivating)   # 启动时不抢焦点

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = TitleBar(self)
        root.addWidget(self.title_bar)

        # ===== 问答页 =====
        chat_page = QWidget()
        body = QVBoxLayout(chat_page)
        body.setContentsMargins(10, 8, 10, 10)
        body.setSpacing(8)

        # 聊天区：QScrollArea + 气泡列表（助手靠左 / 用户靠右）
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("chatScroll")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.NoFrame)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chat_scroll.viewport().setAutoFillBackground(False)
        self.chat_scroll.setFont(QFont("Microsoft YaHei", 9))
        self.chat_scroll.installEventFilter(self)

        self.chat_box = QWidget()
        self.chat_box.setObjectName("chatBox")
        self.chat_box.setAttribute(Qt.WA_StyledBackground, True)
        self.chat_lay = QVBoxLayout(self.chat_box)
        self.chat_lay.setContentsMargins(2, 2, 2, 2)
        self.chat_lay.setSpacing(8)
        self.chat_lay.addStretch(1)  # 末尾留白：消息自顶部开始堆叠
        self.chat_scroll.setWidget(self.chat_box)
        body.addWidget(self.chat_scroll, 1)

        # 「↓ 新消息」提示：用户上滑看历史时浮出，点击回到底部
        self.new_msg_btn = QPushButton("↓ 新消息", self.chat_scroll)
        self.new_msg_btn.setObjectName("newMsgBtn")
        self.new_msg_btn.setCursor(Qt.PointingHandCursor)
        self.new_msg_btn.clicked.connect(self._scroll_to_bottom)
        self.new_msg_btn.hide()

        self.chat_scroll.verticalScrollBar().valueChanged.connect(self._on_chat_scrolled)
        self.chat_scroll.verticalScrollBar().rangeChanged.connect(self._on_chat_range_changed)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input_edit = QLineEdit()
        self.input_edit.setObjectName("inputEdit")
        self.input_edit.setPlaceholderText("输入问题，例如：胡桃用什么武器")
        self.input_edit.returnPressed.connect(self.send)
        input_row.addWidget(self.input_edit, 1)

        send_btn = QPushButton("发送")
        send_btn.setObjectName("sendBtn")
        send_btn.clicked.connect(self.send)
        input_row.addWidget(send_btn)
        body.addLayout(input_row)

        hint = QLabel("提示：游戏中按住 Alt 释放鼠标后，可点击并拖动本窗口")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        body.addWidget(hint)

        # ===== 补成就页 =====
        self.achieve_panel = achieve_ui.AchievePanel(self)

        # 堆叠：问答 / 补成就（窗口大小不变）
        self.stack = QStackedWidget()
        self.stack.addWidget(chat_page)
        self.stack.addWidget(self.achieve_panel)
        root.addWidget(self.stack, 1)

        self.resize(380, 560)

    def switch_mode(self):
        """切换问答模式 / 补成就模式（标题栏按钮）。"""
        btn = self.sender()
        self._set_achieve_mode(btn.isChecked() if isinstance(btn, QPushButton) else False)

    def _set_achieve_mode(self, achieve):
        """统一入口：切换视图 + 同步标题栏按钮文案，返回引导更直观。"""
        self.title_bar.achieve_btn.setChecked(achieve)
        if achieve:
            self.stack.setCurrentWidget(self.achieve_panel)
            self.achieve_panel.refresh()
            self.title_bar.achieve_btn.setText("‹ 返回问答")
            self.title_bar.achieve_btn.setToolTip("返回问答模式")
        else:
            self.stack.setCurrentIndex(0)
            self.title_bar.achieve_btn.setText("补成就")
            self.title_bar.achieve_btn.setToolTip("切换到补成就模式（总览 / 卡片）")

    def _exit_achieve_mode(self):
        """补成就面板内「返回问答」按钮触发。"""
        self._set_achieve_mode(False)

    def _restore_or_place(self):
        """优先还原上次窗口位置；无记录或坐标失效则放到屏幕右侧默认位。"""
        pos = _load_window_pos()
        if pos:
            self.move(*pos)
        else:
            self._place_at_right()

    def _save_window_pos(self):
        _write_window_pos(self.pos().x(), self.pos().y())

    def moveEvent(self, event):
        super().moveEvent(event)
        # 防抖：拖动过程中不写盘，停下约 300ms 后保存一次
        if getattr(self, "_pos_timer", None) is not None:
            self._pos_timer.start(300)

    def closeEvent(self, event):
        if getattr(self, "_pos_timer", None) is not None:
            self._pos_timer.stop()
        self._save_window_pos()
        super().closeEvent(event)

    def _place_at_right(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - self.width() - 24
        y = screen.top() + 120
        self.move(x, y)

    def _append_welcome(self):
        self._add_message(
            "助手",
            "欢迎使用原神攻略助手！\n"
            "· 游戏中按住 Alt 释放鼠标，即可点击/拖动/锁定本窗口\n"
            "· 输入问题即时回答，如：胡桃突破材料、胡桃怎么养、天赋6升7\n"
            "· 标题栏 AI 按钮：可选配置智能 Agent 增强\n"
            "· 标题栏 日志 按钮：查看问答轨迹与统计",
            right=False,
        )

    def set_locked(self, locked):
        self.locked = locked

    def send(self):
        text = self.input_edit.text().strip()
        if not text:
            return
        if getattr(self, "_worker", None) and self._worker.isRunning():
            # 上一条还在处理中，避免并发覆盖占位项
            self._add_message("助手", "上一条还在处理中，请稍候…", right=False)
            self.input_edit.clear()
            return
        self._add_message("我", text, right=True, force_bottom=True)
        self._pending_question = text  # 保存原始问题（用于历史记录）
        self.input_edit.clear()
        self._start_search(text)

    # ===== 聊天区（气泡） =====
    def eventFilter(self, obj, event):
        # 聊天区尺寸变化时，重新定位「↓ 新消息」浮层按钮
        if obj is self.chat_scroll and event.type() == QEvent.Resize:
            self._position_new_msg_btn()
        return super().eventFilter(obj, event)

    @staticmethod
    def _make_row(bubble, is_user):
        """把气泡包进一行：用户靠右、助手靠左。"""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        if is_user:
            h.addStretch(1)
            h.addWidget(bubble)
        else:
            h.addWidget(bubble)
            h.addStretch(1)
        return row

    def _insert_bubble(self, sender, text, is_user, dim=False):
        """插入一条气泡并做超限清理，返回气泡对象。"""
        bubble = ChatBubble(sender, text, is_user=is_user, dim=dim)
        row = self._make_row(bubble, is_user)
        self.chat_lay.insertWidget(self.chat_lay.count() - 1, row)
        self._rows.append(row)
        while len(self._rows) > MAX_CHAT_ITEMS:
            old = self._rows.pop(0)
            old.setParent(None)
            old.deleteLater()
        return bubble

    def _add_message(self, sender, text, right=False, dim=False, force_bottom=False):
        """追加一条气泡（助手左 / 用户右），并按 S1 规则决定是否自动滚到底。"""
        was_at_bottom = self._at_bottom or force_bottom
        if force_bottom:
            # 用户主动发送：立即视为停在底部，让后续「思考中/回复」继续跟随
            self._at_bottom = True
        bubble = self._insert_bubble(sender, text, right, dim)
        self._schedule_scroll(was_at_bottom)
        return bubble

    def _on_chat_scrolled(self, value):
        sb = self.chat_scroll.verticalScrollBar()
        self._at_bottom = value >= sb.maximum() - 4
        if self._at_bottom:
            self._want_bottom = True  # 用户回到底部 → 恢复跟随
            self.new_msg_btn.hide()
        else:
            self._want_bottom = False  # 用户上滑查看历史 → 停止跟随

    def _on_chat_range_changed(self, _min, _max):
        # 内容高度异步变化时（布局未完成），若仍要跟随则补滚到底
        if self._want_bottom:
            sb = self.chat_scroll.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _scroll_to_bottom(self):
        self._want_bottom = True
        sb = self.chat_scroll.verticalScrollBar()
        sb.setValue(sb.maximum())
        self.new_msg_btn.hide()

    def _position_new_msg_btn(self):
        m = 10
        self.new_msg_btn.adjustSize()
        r = self.chat_scroll.rect()
        self.new_msg_btn.move(
            r.right() - self.new_msg_btn.width() - m,
            r.bottom() - self.new_msg_btn.height() - m,
        )

    def _show_new_msg_hint(self):
        self._position_new_msg_btn()
        self.new_msg_btn.show()
        self.new_msg_btn.raise_()

    def _schedule_scroll(self, was_at_bottom):
        # S1：仅当用户本就停在底部时才跟随到底；否则保持位置并提示「↓ 新消息」
        if was_at_bottom:
            self._want_bottom = True
            QTimer.singleShot(0, self._follow_bottom)
        else:
            self._want_bottom = False
            QTimer.singleShot(0, self._show_new_msg_hint)

    def _follow_bottom(self):
        if self._want_bottom:
            sb = self.chat_scroll.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _start_search(self, question):
        """后台检索（+可选 LLM 组织），期间显示「思考中…」占位气泡。"""
        s = llm.load_settings()
        history = self._history if s.get("use_history") else []
        was_at_bottom = self._at_bottom
        self._pending_bubble = self._insert_bubble("助手", "思考中…", is_user=False, dim=True)
        self._schedule_scroll(was_at_bottom)
        self._worker = SearchWorker(question, history)
        self._worker.finished.connect(self._on_search_done)
        self._worker.start()

    def _on_search_done(self, status, result):
        # 直接把「思考中…」占位气泡更新为完整回复
        was_at_bottom = self._at_bottom
        if self._pending_bubble is not None:
            self._pending_bubble.set_text(result or "")
            self._pending_bubble = None
        else:
            self._insert_bubble("助手", result or "", is_user=False)
        # 记录多轮对话历史（最多 5 轮）；错误消息不记录
        if result and getattr(self, "_pending_question", None) and not result.startswith("抱歉，处理出错了"):
            self._history.append({"user": self._pending_question, "assistant": result})
            if len(self._history) > 5:
                self._history.pop(0)
        self._schedule_scroll(was_at_bottom)

    def _keep_on_top(self):
        if self.isVisible():
            self.raise_()

    def open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    def open_log_panel(self):
        dlg = LogPanel(self)
        dlg.exec()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # 后台预热向量模型与索引，避免首次问答卡 UI
    threading.Thread(target=rag.warmup, daemon=True).start()
    window = OverlayWindow()  # noqa: F841 保持窗口引用，防止被回收
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
