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

import agent
import knowledge
import llm
import rag

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QLineEdit,
    QPushButton,
    QListWidget,
    QTextBrowser,
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
_qa_lock = threading.Lock()

APP_NAME = "原神攻略助手"
MAX_REPLY_LEN = 1200  # 单条回复最大字符数，超长限长+摘要，避免聊天列表渲染截断
MAX_CHAT_ITEMS = 200  # 聊天列表最多保留条数，超出自动清理最早的
NO_RESULT_MSG = "抱歉，暂时没找到相关攻略。\n（可尝试换个问法；RAG 检索覆盖冷门问题。）"

STYLE = """
#overlay {
    background-color: rgba(22, 24, 32, 0.94);
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 10px;
}
#titleBar {
    background-color: rgba(42, 46, 60, 0.95);
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
}
#titleLabel { color: #ffffff; font-weight: bold; font-size: 14px; }
QPushButton { font-size: 13px; }
QPushButton#lockBtn {
    background: transparent; color: #c9c9c9; border: none;
    padding: 4px 10px; border-radius: 4px;
}
QPushButton#lockBtn:hover { background: rgba(255,255,255,0.14); color: #ffffff; }
QPushButton#lockBtn:checked { background: rgba(255,180,60,0.22); color: #ffb43c; }
QPushButton#closeBtn {
    background: transparent; color: #c9c9c9; border: none;
    padding: 4px 10px; border-radius: 4px; font-size: 15px;
}
QPushButton#closeBtn:hover { background: rgba(255,80,80,0.25); color: #ff6b6b; }
QTextBrowser#chatList {
    background: transparent; border: none;
    color: #e6e6e6; padding: 4px;
}
QLineEdit#inputEdit {
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.22);
    border-radius: 6px; padding: 8px 10px;
    color: #ffffff; font-size: 13px;
}
QPushButton#sendBtn {
    background: #4a90e2; color: #ffffff; border: none;
    border-radius: 6px; padding: 8px 14px; font-size: 13px;
}
QPushButton#sendBtn:hover { background: #5a9ff2; }
QLabel#hintLabel { color: rgba(255,255,255,0.45); font-size: 11px; }
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


def _hit_type(content):
    """判断 search_character 返回的是强字段命中还是完整档案。

    字段级提取命中 -> 'field'（规则层已精确回答）；
    返回完整档案（首行是"XX（角色）"等标题）-> 'full'（规则层未答到点，交 Agent）。
    """
    first = content.splitlines()[0] if content.splitlines() else ""
    if any(k in first for k in ("（角色）", "（武器）", "（圣遗物）", "（材料）", "（成就）")):
        return "full"
    return "field"


def _search_reply(text):
    """纯检索逻辑（可在后台线程运行）：字段级精确匹配 -> 关键词库 -> RAG 降级。

    返回 (文本, 命中类型, trace)。命中类型：'field' 字段级 / 'full' 完整档案（弱命中）/
    'keyword' 关键词库 / 'rag' 向量检索 / None 全部未命中。
    trace 记录各层尝试、耗时、RAG 就绪状态与错误（供日志观测）。
    """
    trace = {"path": [], "rag_ready": rag.is_ready(), "rag_error": None, "matched": None}
    t0 = time.time()

    def _mk(text_, hit_type_, src=None):
        trace["matched"] = src
        trace["ms_rule"] = int((time.time() - t0) * 1000)
        return text_, hit_type_, trace

    try:
        if any(kw in text for kw in rag.OBJECTIVE_KEYWORDS):
            trace["path"].append("字段级")
            char = rag.search_character(text)
            if char:
                content, src = char
                return _mk(f"【{src}】\n{content}", _hit_type(content), src)
            answer, _, _ = knowledge.find_answer(text)
            if answer:
                trace["path"].append("关键词库")
                return _mk(answer, "keyword")
        else:
            trace["path"].append("关键词库")
            answer, _, _ = knowledge.find_answer(text)
            if answer:
                return _mk(answer, "keyword")
        # RAG 降级：先角色精确匹配，再向量检索
        trace["path"].append("RAG")
        try:
            char = rag.search_character(text)
            if char:
                content, src = char
                return _mk(f"【{src}】\n{content}", _hit_type(content), src)
        except Exception as e:
            trace["rag_error"] = f"search_character: {e}"
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


class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.locked = False
        self.last_entity = None  # 多轮对话上下文：记住最近提到的实体
        self._history = []  # 多轮对话历史（最多 5 轮，用于 LLM 上下文）
        self.setObjectName("overlay")
        self.setWindowTitle(APP_NAME)
        self._build_ui()
        self._apply_flags()
        self.setStyleSheet(STYLE)
        self._place_at_right()
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

        body = QVBoxLayout()
        body.setContentsMargins(10, 8, 10, 10)
        body.setSpacing(8)

        self.chat_list = QTextBrowser()
        self.chat_list.setObjectName("chatList")
        self.chat_list.setReadOnly(True)
        self.chat_list.setOpenExternalLinks(False)
        self.chat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chat_list.setFont(QFont("Microsoft YaHei", 9))
        body.addWidget(self.chat_list, 1)

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

        root.addLayout(body)
        self.resize(380, 520)

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
        self._add_message("我", text, right=True)
        self._pending_question = text  # 保存原始问题（用于历史记录）
        self.input_edit.clear()
        # 实体上下文：省略主语时用上一实体补充（如先问桑多涅，再问"最高提升多少"）
        # Agent 模式（配了 key）不做补全——LLM 通过多轮历史理解省略；离线规则层才需要补全
        entity = rag.match_entity(text)
        if entity:
            self.last_entity = entity
        elif self.last_entity and not llm.is_configured():
            text = f"{self.last_entity} {text}"
        self._start_search(text)

    def _start_search(self, question):
        """后台检索（+可选 LLM 组织），期间显示「思考中…」。"""
        self.chat_list.setTextColor(QColor("#e6e6e6"))
        self.chat_list.append("助手：思考中…")
        self.chat_list.moveCursor(QTextCursor.End)
        self.chat_list.ensureCursorVisible()
        s = llm.load_settings()
        history = self._history if s.get("use_history") else []
        self._worker = SearchWorker(question, history)
        self._worker.finished.connect(self._on_search_done)
        self._worker.start()

    def _on_search_done(self, status, result):
        # 用完整回复替换"思考中…"占位段
        self.chat_list.setTextColor(QColor("#e6e6e6"))
        self._replace_pending("助手：" + result)
        # 记录多轮对话历史（最多 5 轮）；错误消息不记录
        if result and getattr(self, "_pending_question", None) and not result.startswith("抱歉，处理出错了"):
            self._history.append({"user": self._pending_question, "assistant": result})
            if len(self._history) > 5:
                self._history.pop(0)
        self.chat_list.moveCursor(QTextCursor.End)
        self.chat_list.ensureCursorVisible()

    def _replace_pending(self, text):
        """把"思考中…"占位替换为完整回复。

        用文档文本级重建（定位占位标记 -> 保留前缀 + 拼接回复），
        不依赖 QTextBlock/QTextCursor 的边界行为，跨 Qt 版本最可靠。
        """
        marker = "助手：思考中…"
        plain = self.chat_list.toPlainText()
        idx = plain.rfind(marker)
        self.chat_list.setTextColor(QColor("#e6e6e6"))
        if idx >= 0:
            self.chat_list.setPlainText(plain[:idx] + text)
        else:
            self.chat_list.append(text)
        self.chat_list.moveCursor(QTextCursor.End)
        self.chat_list.ensureCursorVisible()

    def _add_message(self, sender, text, right=False):
        self.chat_list.setTextColor(QColor("#8fd0ff" if right else "#e6e6e6"))
        self.chat_list.append(f"{sender}：{text}")
        # 只保留最近 MAX_CHAT_ITEMS 段，自动清理最早的
        while self.chat_list.document().blockCount() > MAX_CHAT_ITEMS:
            cur = QTextCursor(self.chat_list.document())
            cur.movePosition(QTextCursor.Start)
            cur.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            cur.removeSelectedText()
            cur.deleteChar()  # 删除段落分隔符
        self.chat_list.moveCursor(QTextCursor.End)
        self.chat_list.ensureCursorVisible()

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
