# -*- coding: utf-8 -*-
"""
补成就模式 UI：成就总览（辑网格 / 辑内列表）+ 成就卡片（背单词式）。

- 总览：辑网格（图标+进度条+计数），点辑进入该辑成就列表
- 列表：一横条一个成就（名称+条件+搜攻略+勾选框），未完成前置
- 卡片：一个成就一张卡（筛选+完成+搜攻略+上一个/下一个）
- 撤销：操作入栈 + 底部 Toast 提示（误触可立即撤销）
"""

import io
import json
import os
import time

from PySide6.QtCore import QUrl, Qt, QTimer, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QCheckBox, QComboBox, QStackedWidget,
    QFrame, QFileDialog, QMessageBox, QScrollArea, QProgressBar,
)

import achievement as A

ACHIEVE_STYLE = """
QWidget#setCard { background: #262a3a; border-radius: 10px; }
QWidget#setCard:hover { background: #2d3245; border: 1px solid rgba(212,182,106,0.45); }
QWidget#achCard { background: #262a3a; border-radius: 12px; border: 1px solid rgba(212,182,106,0.22); }
QWidget#achRow { background: #262a3a; border-radius: 8px; }
QWidget#achRow:hover { background: #2d3245; }
QWidget#toast {
    background: #333850; border: 1px solid rgba(212,182,106,0.4);
    border-radius: 8px;
}
QPushButton#viewBtn {
    background: #2f3448; color: #d4b66a; border: none; border-radius: 7px; padding: 7px 0;
}
QPushButton#viewBtn:hover { background: #3a4058; }
QPushButton#viewBtn:checked { background: #d4b66a; color: #1b1d2b; font-weight: bold; }
QPushButton#toolBtn { background: #262a3a; color: #a8a8b8; border: none; border-radius: 6px; padding: 6px 0; }
QPushButton#toolBtn:hover { background: #333850; color: #e8e8f0; }
QPushButton#subTab { background: transparent; color: #a8a8b8; border: none; border-radius: 14px; padding: 6px 0; }
QPushButton#subTab:hover { background: rgba(212,182,106,0.1); }
QPushButton#subTab:checked { background: rgba(212,182,106,0.18); color: #d4b66a; font-weight: bold; }
QPushButton#smBtn { background: rgba(212,182,106,0.16); color: #d4b66a; border: none; border-radius: 5px; padding: 4px 9px; font-size: 11px; }
QPushButton#smBtn:hover { background: rgba(212,182,106,0.34); color: #f0e0b8; }
QPushButton#searchBtn { background: rgba(212,182,106,0.18); color: #d4b66a; border: none; border-radius: 8px; padding: 9px 0; font-size: 13px; }
QPushButton#searchBtn:hover { background: rgba(212,182,106,0.34); color: #f5e6c8; }
QPushButton#doneBtn { background: #6fcf97; color: #1b1d2b; border: none; border-radius: 8px; padding: 8px 0; font-weight: bold; }
QPushButton#doneBtn:hover { background: #82dda8; }
QPushButton#doneBtn[undo="true"] { background: #2f3448; color: #a8a8b8; }
QPushButton#doneBtn[undo="true"]:hover { background: #3a4058; color: #e8e8f0; }
QPushButton#navBtn { background: #2f3448; color: #d0d0dc; border: none; border-radius: 6px; padding: 6px 14px; }
QPushButton#navBtn:hover { background: #3a4058; color: #ffffff; }
QPushButton#backBtn { background: #2f3448; color: #d0d0dc; border: none; border-radius: 6px; padding: 4px 10px; }
QPushButton#backBtn:hover { background: #3a4058; color: #ffffff; }
QPushButton#undoBtn { background: #d4b66a; color: #1b1d2b; border: none; border-radius: 5px; padding: 4px 12px; font-weight: bold; }
QPushButton#undoBtn:hover { background: #e3c87e; }
QComboBox {
    background: #2f3448; color: #f0f0f5; border: none; border-radius: 6px;
    padding: 6px; font-size: 12px;
}
QComboBox:hover { background: #3a4058; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: #262a3a; color: #f0f0f5; border: 1px solid rgba(212,182,106,0.3);
    selection-background-color: rgba(212,182,106,0.28);
}
QCheckBox { color: #a8a8b8; font-size: 12px; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 4px;
    border: 2px solid #5a5f75; background: transparent;
}
QCheckBox::indicator:checked { background: #6fcf97; border-color: #6fcf97; }
QCheckBox::indicator:hover { border-color: #d4b66a; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
QScrollBar::handle { background: rgba(212,182,106,0.28); border-radius: 3px; min-height: 24px; }
QScrollBar::handle:hover { background: rgba(212,182,106,0.5); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


class SetCard(QWidget):
    """辑卡片：图标 + 名称 + 进度条 + 完成计数。"""
    clicked = Signal(str)

    def __init__(self, name, done, total, icon="🏆"):
        super().__init__()
        self.setObjectName("setCard")
        self.setAttribute(Qt.WA_StyledBackground, True)  # 让 QSS 背景对 QWidget 子类生效
        self.name = name
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)
        ic = QLabel(icon)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet("font-size: 22px;")
        nm = QLabel(name)
        nm.setAlignment(Qt.AlignCenter)
        nm.setWordWrap(True)
        nm.setStyleSheet("color: #f0f0f5; font-size: 11px;")
        bar = QProgressBar()
        bar.setMaximum(total or 1)
        bar.setValue(done)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        color = "#6fcf97" if (total and done >= total) else "#d4b66a"
        bar.setStyleSheet(
            "QProgressBar {background:#14151f; border:none; border-radius:3px;}"
            f"QProgressBar::chunk {{background:{color}; border-radius:3px;}}"
        )
        cnt = QLabel(f"{done}/{total}")
        cnt.setAlignment(Qt.AlignCenter)
        cnt.setStyleSheet("color:#a8a8b8; font-size:10px;")
        lay.addWidget(ic)
        lay.addWidget(nm)
        lay.addWidget(bar)
        lay.addWidget(cnt)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        self.clicked.emit(self.name)
        super().mousePressEvent(e)


class AchRow(QWidget):
    """一横条一个成就：名称 + 条件 + 搜攻略 + 勾选框。"""
    toggled = Signal(object, bool)

    def __init__(self, ach):
        super().__init__()
        self.setObjectName("achRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.ach = ach
        done = ach.get("_done")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        main = QVBoxLayout()
        main.setSpacing(1)
        nm = QLabel(ach.get("name", ""))
        nm.setStyleSheet(
            "color:#6e6e80; font-size:12px; text-decoration:line-through;" if done
            else "color:#ffffff; font-size:12px; font-weight:bold;"
        )
        cd = QLabel(ach.get("cond", ""))
        cd.setWordWrap(True)
        cd.setStyleSheet(
            "color:#5c5c6e; font-size:10px;" if done else "color:#a8a8b8; font-size:10px;"
        )
        main.addWidget(nm)
        main.addWidget(cd)
        row.addLayout(main, 1)
        btn = QPushButton("搜攻略")
        btn.setObjectName("smBtn")
        btn.clicked.connect(lambda: open_search(ach.get("name", "")))
        row.addWidget(btn)
        self.chk = QCheckBox()
        self.chk.setChecked(bool(done))
        self.chk.stateChanged.connect(
            lambda s: self.toggled.emit(ach.get("id"), s == 2)
        )
        row.addWidget(self.chk)


class Toast(QFrame):
    """底部撤销提示条。"""
    undo_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        self.txt = QLabel("")
        self.txt.setStyleSheet("color:#e6e6ee; font-size:11px;")
        btn = QPushButton("撤销")
        btn.setObjectName("undoBtn")
        btn.clicked.connect(self.undo_clicked.emit)
        lay.addWidget(self.txt, 1)
        lay.addWidget(btn)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_msg(self, msg, ms=4000):
        self.txt.setText(msg)
        self.show()
        self.raise_()
        self._timer.start(ms)


def open_search(name):
    """用系统默认浏览器打开米游社搜索。"""
    QDesktopServices.openUrl(QUrl(A.search_url(name)))


class AchievePanel(QWidget):
    """补成就模式面板。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(ACHIEVE_STYLE)
        self.undo_stack = []
        self.cur_set = None
        self.tab = "all"
        self.only_undone = True
        self.set_filter = None
        self.card_idx = 0
        self._build_ui()
        self.refresh()

    # ===== 构建界面 =====
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)
        root.setSpacing(6)

        # 视图切换 + 导入导出
        sw = QHBoxLayout()
        sw.setSpacing(6)
        self.btn_overview = QPushButton("🗂 成就总览")
        self.btn_card = QPushButton("🃏 成就卡片")
        for b in (self.btn_overview, self.btn_card):
            b.setObjectName("viewBtn")
            b.setCheckable(True)
        self.btn_overview.setChecked(True)
        self.btn_overview.clicked.connect(lambda: self.switch_view("overview"))
        self.btn_card.clicked.connect(lambda: self.switch_view("card"))
        sw.addWidget(self.btn_overview)
        sw.addWidget(self.btn_card)

        self.btn_import = QPushButton("⬆ 导入成就")
        self.btn_export = QPushButton("⬇ 导出成就")
        self.btn_import.setObjectName("toolBtn")
        self.btn_export.setObjectName("toolBtn")
        self.btn_import.clicked.connect(self.do_import)
        self.btn_export.clicked.connect(self.do_export)
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(self.btn_import)
        row2.addWidget(self.btn_export)

        root.addLayout(sw)
        root.addLayout(row2)

        # 导入状态提示（持久化快照信息）
        self.state_lbl = QLabel("")
        self.state_lbl.setStyleSheet("color:#7a7a8c; font-size:10px;")
        self.state_lbl.setWordWrap(True)
        root.addWidget(self.state_lbl)

        # 内容区
        self.stack = QStackedWidget()
        self.page_grid = self._build_grid_page()
        self.page_list = self._build_list_page()
        self.page_card = self._build_card_page()
        for p in (self.page_grid, self.page_list, self.page_card):
            self.stack.addWidget(p)
        root.addWidget(self.stack, 1)

        # 撤销 Toast
        self.toast = Toast(self)
        self.toast.undo_clicked.connect(self.do_undo)
        root.addWidget(self.toast)

    def _build_grid_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_btns = {}
        for key, text in (("all", "全部"), ("undone", "未完成"), ("done", "已完成")):
            b = QPushButton(text)
            b.setObjectName("subTab")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.set_tab(k))
            self.tab_btns[key] = b
            tabs.addWidget(b)
        self.tab_btns["all"].setChecked(True)
        lay.addLayout(tabs)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.grid_box = QWidget()
        self.grid_lay = QGridLayout(self.grid_box)
        self.grid_lay.setSpacing(8)
        self.grid_lay.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.grid_box)
        lay.addWidget(self.scroll, 1)
        return page

    def _build_list_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.btn_back = QPushButton("‹ 返回")
        self.btn_back.setObjectName("backBtn")
        self.btn_back.clicked.connect(self.back_to_grid)
        self.list_title = QLabel("")
        self.list_title.setStyleSheet("color:#f0f0f5; font-size:13px; font-weight:bold;")
        head.addWidget(self.btn_back)
        head.addWidget(self.list_title, 1)
        lay.addLayout(head)
        self.ach_list = QListWidget()
        self.ach_list.setStyleSheet(
            "QListWidget{background:transparent;border:none;}"
            "QListWidget::item{margin-bottom:4px;}"
        )
        lay.addWidget(self.ach_list, 1)
        return page

    def _build_card_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.set_combo = QComboBox()
        self.set_combo.setStyleSheet(
            "QComboBox{background:#2a2e40;color:#e6e6ee;border:none;border-radius:6px;padding:6px;font-size:12px;}"
        )
        self.set_combo.currentIndexChanged.connect(self.on_filter_changed)
        self.chk_undone = QCheckBox("只看未完成")
        self.chk_undone.setChecked(True)
        self.chk_undone.setStyleSheet("color:#9a9aa6;font-size:12px;")
        self.chk_undone.stateChanged.connect(self.on_filter_changed)
        bar.addWidget(self.set_combo, 1)
        bar.addWidget(self.chk_undone)
        lay.addLayout(bar)

        card = QWidget()
        card.setObjectName("achCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 14, 14, 14)
        cl.setSpacing(6)
        self.card_set = QLabel("")
        self.card_set.setStyleSheet("color:#a8a8b8;font-size:11px;")
        self.card_name = QLabel("")
        self.card_name.setWordWrap(True)
        self.card_name.setStyleSheet("color:#ffffff;font-size:17px;font-weight:bold;")
        self.card_cond = QLabel("")
        self.card_cond.setWordWrap(True)
        self.card_cond.setStyleSheet("color:#dcdce6;font-size:12px;")
        cl.addWidget(self.card_set)
        cl.addWidget(self.card_name)
        cl.addWidget(self.card_cond)

        drow = QHBoxLayout()
        drow.setSpacing(10)
        self.card_chk = QCheckBox()
        self.card_chk.setStyleSheet("QCheckBox::indicator{width:22px;height:22px;}")
        self.card_btn = QPushButton("✔ 标记完成")
        self.card_btn.setObjectName("doneBtn")
        self.card_btn.clicked.connect(self.toggle_current)
        drow.addWidget(self.card_chk)
        drow.addWidget(self.card_btn, 1)
        cl.addLayout(drow)
        lay.addWidget(card)

        self.btn_search = QPushButton("🔍 米游社搜攻略")
        self.btn_search.setObjectName("searchBtn")
        self.btn_search.clicked.connect(self.search_current)
        lay.addWidget(self.btn_search)

        nav = QHBoxLayout()
        b_prev = QPushButton("‹ 上一个")
        b_next = QPushButton("下一个 ›")
        for b in (b_prev, b_next):
            b.setObjectName("navBtn")
        b_prev.clicked.connect(self.prev_card)
        b_next.clicked.connect(self.next_card)
        self.card_pos = QLabel("0 / 0")
        self.card_pos.setStyleSheet("color:#a8a8b8;font-size:12px;")
        nav.addWidget(b_prev)
        nav.addWidget(self.card_pos, 0, Qt.AlignCenter)
        nav.addWidget(b_next)
        lay.addLayout(nav)
        lay.addStretch(1)
        return page

    # ===== 视图切换 =====
    def switch_view(self, v):
        self.btn_overview.setChecked(v == "overview")
        self.btn_card.setChecked(v == "card")
        if v == "card":
            self.card_idx = 0
        self.refresh()

    def set_tab(self, key):
        self.tab = key
        for k, b in self.tab_btns.items():
            b.setChecked(k == key)
        self.refresh()

    # ===== 刷新 =====
    def refresh(self):
        info = A.state_info()
        if info.get("imported"):
            self.state_lbl.setText(
                f"已完成 {info['done']} 条 · 导入于 {info['when']}（重新导出后可点导入刷新）"
            )
        else:
            self.state_lbl.setText("未导入成就数据，点「⬆ 导入成就」选择 Yae 导出的 UIAF 文件")
        if self.btn_card.isChecked():
            self._refresh_filter()
            self._refresh_card()
            self.stack.setCurrentWidget(self.page_card)
            return
        if self.cur_set:
            self._refresh_list()
            self.stack.setCurrentWidget(self.page_list)
        else:
            self._refresh_grid()
            self.stack.setCurrentWidget(self.page_grid)

    def _refresh_grid(self):
        while self.grid_lay.count():
            item = self.grid_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        data = A.groups(self.tab)
        if not data:
            empty = QLabel("无符合条件的辑")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:#7a7a8c;font-size:12px;")
            self.grid_lay.addWidget(empty, 0, 0, 1, 2)
            return
        for i, (s, d, t) in enumerate(data):
            c = SetCard(s, d, t, A.icon_of(s))
            c.clicked.connect(self.open_set)
            self.grid_lay.addWidget(c, i // 2, i % 2)

    def _refresh_list(self):
        self.ach_list.clear()
        if not self.cur_set:
            return
        achs = A.achievements_of(self.cur_set, self.tab)
        done_n = sum(1 for a in achs if a.get("_done"))
        self.list_title.setText(f"{A.icon_of(self.cur_set)} {self.cur_set}  {done_n}/{len(achs)}")
        for a in achs:
            row = AchRow(a)
            row.toggled.connect(self.toggle_done)
            item = QListWidgetItem(self.ach_list)
            item.setSizeHint(row.sizeHint())
            self.ach_list.setItemWidget(item, row)

    def _refresh_filter(self):
        self.set_combo.blockSignals(True)
        self.set_combo.clear()
        self.set_combo.addItem("全部辑", None)
        for s in A.available_sets(self.chk_undone.isChecked()):
            self.set_combo.addItem(s, s)
        # 当前选中若失效则回退到全部
        if self.set_filter:
            idx = self.set_combo.findData(self.set_filter)
            if idx < 0:
                self.set_filter = None
        if self.set_filter:
            self.set_combo.setCurrentIndex(self.set_combo.findData(self.set_filter))
        else:
            self.set_combo.setCurrentIndex(0)
        self.set_combo.blockSignals(False)

    def _refresh_card(self):
        L = A.card_list(self.chk_undone.isChecked(), self.set_filter)
        if not L:
            self.card_set.setText("")
            self.card_name.setText("没有符合条件的成就")
            self.card_cond.setText("可取消「只看未完成」或换一个辑")
            self.card_chk.setVisible(False)
            self.card_btn.setVisible(False)
            self.btn_search.setVisible(False)
            self.card_pos.setText("0 / 0")
            self._cur = None
            return
        self.card_chk.setVisible(True)
        self.card_btn.setVisible(True)
        self.btn_search.setVisible(True)
        if self.card_idx >= len(L):
            self.card_idx = len(L) - 1
        if self.card_idx < 0:
            self.card_idx = 0
        a = L[self.card_idx]
        self._cur = a
        self.card_set.setText(a.get("set", ""))
        self.card_name.setText(a.get("name", ""))
        self.card_cond.setText(a.get("cond", ""))
        done = a.get("_done")
        self.card_chk.blockSignals(True)
        self.card_chk.setChecked(bool(done))
        self.card_chk.blockSignals(False)
        self.card_btn.setText("取消完成" if done else "✔ 标记完成")
        self.card_btn.setProperty("undo", "true" if done else "false")
        self.card_btn.style().polish(self.card_btn)
        self.card_pos.setText(f"{self.card_idx + 1} / {len(L)}")

    # ===== 交互 =====
    def open_set(self, s):
        self.cur_set = s
        self.refresh()

    def back_to_grid(self):
        self.cur_set = None
        self.refresh()

    def on_filter_changed(self):
        self.set_filter = self.set_combo.currentData()
        self.only_undone = self.chk_undone.isChecked()
        self.card_idx = 0
        self._refresh_filter()
        self._refresh_card()

    def next_card(self):
        self.card_idx += 1
        self._refresh_card()

    def prev_card(self):
        self.card_idx -= 1
        self._refresh_card()

    def search_current(self):
        a = getattr(self, "_cur", None)
        if a:
            open_search(a.get("name", ""))

    def toggle_current(self):
        a = getattr(self, "_cur", None)
        if a:
            self.toggle_done(a.get("id"), not a.get("_done"))

    def toggle_done(self, aid, done):
        """标记完成/取消：入撤销栈 + Toast + 刷新（总览与卡片共享数据，天然同步）。"""
        name = ""
        for x in A.load_achievements():
            if x.get("id") == aid:
                name = x.get("name", "")
                break
        self.undo_stack.append((aid, not done))
        A.set_done(aid, done)
        self.toast.show_msg(
            ("已标记完成：" if done else "已取消完成：") + (name or str(aid))
        )
        if done and self.btn_card.isChecked():
            L = A.card_list(self.chk_undone.isChecked(), self.set_filter)
            if self.card_idx >= max(0, len(L) - 1):
                self.card_idx = max(0, len(L) - 1)
        self.refresh()

    def do_undo(self):
        if not self.undo_stack:
            self.toast.hide()
            return
        aid, prev = self.undo_stack.pop()
        A.set_done(aid, prev)
        # 卡片模式：定位回该成就（若仍在筛选结果内）
        if self.btn_card.isChecked():
            L = A.card_list(self.chk_undone.isChecked(), self.set_filter)
            for i, a in enumerate(L):
                if a.get("id") == aid:
                    self.card_idx = i
                    break
        self.toast.hide()
        self.refresh()

    # ===== 导入导出 =====
    def do_import(self):
        # 默认打开上次导入的目录，方便快速选择新导出的文件
        prev = A.state_info().get("source") or ""
        start = os.path.dirname(prev) if prev else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 UIAF 成就文件", start, "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            done, total = A.import_uiaf(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))
            return
        ret = QMessageBox.question(
            self, "导入成功",
            f"已导入 {total} 条成就（已完成 {done} 条）。\n\n"
            "是否清空本地手动标记，以官方数据为准？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if ret == QMessageBox.Yes:
            A.clear_manual()
        self.undo_stack.clear()
        self.refresh()

    def do_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出成就进度", "achievements_export.json", "JSON 文件 (*.json)"
        )
        if not path:
            return
        achs = A.load_achievements()
        lst = []
        for a in achs:
            aid = a.get("id")
            done = A.is_done(aid)
            lst.append({
                "id": aid,
                "status": 3 if done else 1,
                "current": 0,
                "timestamp": 0,
            })
        data = {
            "info": {
                "export_app": "GenshinGuide",
                "export_app_version": "1.0",
                "export_timestamp": int(time.time()),
                "uiaf_version": "v1.1",
            },
            "list": lst,
        }
        try:
            with io.open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(
            self, "导出完成", f"已导出 {len(lst)} 条成就到：\n{path}"
        )
