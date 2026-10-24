# -*- coding: utf-8 -*-
"""组件商店页（v10 A10）：设置 ▸ 组件库 ▸ 商店。

与「本地组件」并列的第二个页签。职责只有三件：

  1. 展示商店清单（名称 / 说明 / 版本 / 分类 / 状态）；
  2. 安装 / 更新 / 卸载（**全部转调 `widget_store`**，这里不直接下载、
     不直接解压——安全校验只有一条路径）；
  3. 把"现在是哪条线路 / 多久前更新的"如实写出来，失败时把两个镜像的
     原因都摆给用户看（比"加载失败"四个字有用得多）。

网络在**后台线程**：`_FetchWorker` 拉清单，结果经 Qt 信号回主线程。
界面首屏先用 `cached_index()`（上次成功的清单），不等网络。
"""
from __future__ import annotations

import os
import threading
from typing import List

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import i18n
from . import theme
from . import widget_plugin
from . import widget_store
from .widget_center import CARD_MAX_COLS, GRID_GAP, columns_for

SHOP_CARD_W = 268        # 目标卡宽（可被拉伸）
SHOP_CARD_MIN_W = 240    # 最小卡宽：再窄就减列（与组件库同一套列规则）
SHOP_CARD_H = 132


def _age_text(sec: float) -> str:
    if sec == float("inf"):
        return i18n.T("shop.never")
    if sec < 90:
        return i18n.T("shop.just_now")
    if sec < 3600:
        return i18n.T("shop.minutes_ago", n=int(sec // 60))
    if sec < 86400:
        return i18n.T("shop.hours_ago", n=int(sec // 3600))
    return i18n.T("shop.days_ago", n=int(sec // 86400))


class _FetchWorker(QObject):
    """后台拉清单（一个 QObject 只为拿一个能跨线程排队的信号）。"""

    done = Signal(object, str)      # (index, error)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.done.emit(widget_store.fetch_index(force=True), "")
        except Exception as exc:      # noqa: BLE001 —— 失败原因原样交给界面
            self.done.emit(None, str(exc))


class _InstallWorker(QObject):
    """B4：后台下载组件包。

    下载是大头（3 镜像 × 3 候选 URL × 20s 超时，网络差时可达分钟级），
    旧实现在 UI 线程同步跑，点一下"安装"界面能冻结几十秒。这里只把
    **下载**放线程；解压/导入（import_zip 会执行插件模块代码）仍在
    主线程做。
    """

    done = Signal(object, str)      # (tmp_path, error)

    def __init__(self, entry: dict):
        super().__init__()
        self._entry = entry

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.done.emit(widget_store.download(self._entry), "")
        except Exception as exc:      # noqa: BLE001
            self.done.emit(None, str(exc))


class ShopCard(QFrame):
    """一个商店条目（自绘边框 + 三行文字 + 一个动作按钮）。"""

    action = Signal(str)            # type

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.entry = dict(entry)
        self.setObjectName("shopCard")
        # W16：与组件库同一套响应式规则——最小宽 + 高度固定 + 水平拉伸
        self.setMinimumWidth(SHOP_CARD_MIN_W)
        self.setFixedHeight(SHOP_CARD_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            "QFrame#shopCard{background: rgba(255,255,255,0.04);"
            " border: 1px solid rgba(255,255,255,0.08); border-radius: 12px;}"
            "QFrame#shopCard:hover{border: 1px solid rgba(255,255,255,0.18);}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        name = QLabel(str(entry.get("name") or entry.get("type") or ""))
        name.setStyleSheet("color: %s; font-size: 14px; font-weight: 600;"
                           " background: transparent;" % theme.rgba(theme.TEXT))
        head.addWidget(name)
        head.addStretch(1)
        ver = QLabel("v" + str(entry.get("version") or ""))
        ver.setStyleSheet("color: %s; font-size: 11px; background: transparent;"
                          % theme.rgba(theme.TEXT_FAINT))
        head.addWidget(ver)
        lay.addLayout(head)

        summary = QLabel(str(entry.get("summary") or ""))
        summary.setWordWrap(True)
        summary.setStyleSheet("color: %s; font-size: 12px; background: transparent;"
                              % theme.rgba(theme.TEXT_DIM))
        lay.addWidget(summary, 1)

        foot = QHBoxLayout()
        cat = QLabel(str(entry.get("category") or ""))
        cat.setStyleSheet("color: %s; font-size: 11px; background: transparent;"
                          % theme.rgba(theme.TEXT_FAINT))
        foot.addWidget(cat)
        foot.addStretch(1)
        self.btn = QPushButton("")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setFixedHeight(26)
        self.btn.setStyleSheet(theme.BUTTON_QSS)
        self.btn.clicked.connect(lambda: self.action.emit(
            str(self.entry.get("type") or "")))
        foot.addWidget(self.btn)
        lay.addLayout(foot)
        self._sync_state()

    def _sync_state(self):
        state = widget_store.card_state(self.entry)
        self.state = state
        text, enabled = {
            "builtin": (i18n.T("shop.state_builtin"), False),
            "installed": (i18n.T("shop.state_installed"), False),
            "update": (i18n.T("shop.state_update"), True),
            "available": (i18n.T("shop.state_install"), True),
        }.get(state, (i18n.T("shop.state_install"), True))
        self.btn.setText(text)
        self.btn.setEnabled(enabled)
        if state == "installed":
            # 已安装的商店组件允许卸载（内置的不允许，按钮直接置灰）
            self.btn.setEnabled(True)
            self.btn.setText(i18n.T("shop.state_uninstall"))
        if state == "builtin":
            self.btn.setEnabled(False)
            self.btn.setText(i18n.T("shop.state_builtin"))

    def refresh_state(self):
        self._sync_state()


class WidgetShopPage(QWidget):
    """商店页：状态行 + 刷新 + 卡片网格。"""

    installedChanged = Signal()

    def __init__(self, layer=None, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.setObjectName("widgetShopPage")
        self.setStyleSheet("QWidget{background: transparent;}"
                           " QLabel{background: transparent;}")
        self._cards: List[ShopCard] = []
        self._busy = False
        self._worker = _FetchWorker()
        self._worker.done.connect(self._on_fetched)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel(i18n.T("shop.title"))
        title.setStyleSheet("color: %s; font-size: 17px; font-weight: 600;"
                            % theme.rgba(theme.TEXT))
        head.addWidget(title)
        head.addStretch(1)
        self.btn_refresh = QPushButton(i18n.T("shop.refresh"))
        self.btn_refresh.setStyleSheet(theme.BUTTON_QSS)
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(self.refresh)
        head.addWidget(self.btn_refresh)
        root.addLayout(head)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(
            "color: %s; font-size: 12px;" % theme.rgba(theme.TEXT_DIM))
        root.addWidget(self.lbl_status)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea{background: transparent;}"
            " QScrollArea > QWidget > QWidget{background: transparent;}")
        holder = QWidget()
        self._lay = QGridLayout(holder)
        self._lay.setContentsMargins(0, 4, 8, 4)
        self._lay.setHorizontalSpacing(GRID_GAP)
        self._lay.setVerticalSpacing(GRID_GAP)
        self._lay.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)

        self._render(widget_store.cached_index())

    # ---------- 渲染 ----------
    def _clear(self):
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cards = []

    def _render(self, index: dict):
        self._clear()
        items = list((index or {}).get("widgets") or [])
        if not items:
            tip = QLabel(i18n.T("shop.empty"))
            tip.setAlignment(Qt.AlignCenter)
            tip.setWordWrap(True)
            tip.setStyleSheet("color: %s; font-size: 13px; padding: 40px 0;"
                              % theme.rgba(theme.TEXT_FAINT))
            self._lay.addWidget(tip, 0, 0, 1, 1)
        else:
            # W16：与组件库同一套响应式列数（放得下 2 列才 2 列）
            cols = columns_for(self.scroll.viewport().width(),
                               min_w=SHOP_CARD_MIN_W)
            self._cols = cols
            for c in range(CARD_MAX_COLS):
                self._lay.setColumnStretch(c, 1 if c < cols else 0)
            for i, entry in enumerate(items):
                card = ShopCard(entry, self)
                card.action.connect(self._on_action)
                self._lay.addWidget(card, i // cols, i % cols)
                self._cards.append(card)
        self._sync_status(index)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cols = columns_for(self.scroll.viewport().width(),
                           min_w=SHOP_CARD_MIN_W)
        if getattr(self, "_cols", None) != cols:
            self._render(widget_store.cached_index())

    def _sync_status(self, index: dict = None, error: str = ""):
        idx = index if index is not None else widget_store.cached_index()
        mirror = str(idx.get("_mirror") or widget_store.cached_mirror() or "—")
        age = _age_text(widget_store.cache_age())
        if error:
            self.lbl_status.setText(
                i18n.T("shop.status_error", mirror=mirror, age=age, error=error))
            self.lbl_status.setStyleSheet(
                "color: %s; font-size: 12px;" % theme.rgba(theme.DANGER))
            return
        self.lbl_status.setText(
            i18n.T("shop.status", mirror=mirror,
                   n=len(idx.get("widgets") or []), age=age))
        self.lbl_status.setStyleSheet(
            "color: %s; font-size: 12px;" % theme.rgba(theme.TEXT_DIM))

    # ---------- 拉取 ----------
    def refresh(self):
        if self._busy:
            return
        self._busy = True
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText(i18n.T("shop.refreshing"))
        self._worker.start()

    def _on_fetched(self, index, error):
        self._busy = False
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText(i18n.T("shop.refresh"))
        if error:
            # 保留旧清单 + 把两条线路的失败原因如实写出来
            self._sync_status(error=error)
            return
        self._render(index or {})

    # ---------- 动作 ----------
    def _on_action(self, wtype: str):
        entry = next((c.entry for c in self._cards if c.entry.get("type") == wtype),
                     None)
        if entry is None:
            return
        state = widget_store.card_state(entry)
        try:
            if state == "installed":
                ok = QMessageBox.question(
                    self, i18n.T("shop.uninstall_title"),
                    i18n.T("shop.uninstall_confirm", name=entry.get("name") or wtype),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if ok != QMessageBox.Yes:
                    return
                widget_store.uninstall(wtype)
            elif state in ("available", "update"):
                self._start_install(entry)
                return
        except Exception as exc:      # noqa: BLE001 —— 安装失败要原样告诉用户
            QMessageBox.warning(self, i18n.T("shop.failed"), str(exc))
            return
        for card in self._cards:
            card.refresh_state()
        self.installedChanged.emit()

    # ---------- 安装（B4：下载在后台线程） ----------
    def _start_install(self, entry: dict):
        if getattr(self, "_installing", False):
            return
        self._installing = True
        wtype = entry.get("type")
        for card in self._cards:
            card.btn.setEnabled(False)
            if card.entry.get("type") == wtype:
                card.btn.setText(i18n.T("shop.installing"))
        self._install_worker = _InstallWorker(entry)
        self._install_worker.done.connect(self._on_downloaded)
        self._install_worker.start()

    def _on_downloaded(self, tmp, error: str):
        self._installing = False
        for card in self._cards:
            card.btn.setEnabled(True)
        if error or not tmp:
            if error:
                QMessageBox.warning(self, i18n.T("shop.failed"), error)
            for card in self._cards:
                card.refresh_state()
            return
        try:
            # 导入（解压 + 执行插件模块代码）必须在主线程
            widget_plugin.import_zip(tmp)
        except Exception as exc:      # noqa: BLE001
            QMessageBox.warning(self, i18n.T("shop.failed"), str(exc))
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        for card in self._cards:
            card.refresh_state()
        self.installedChanged.emit()

    # ---------- 测试/外部 ----------
    def cards(self) -> List[ShopCard]:
        return list(self._cards)
