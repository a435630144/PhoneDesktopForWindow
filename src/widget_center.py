# -*- coding: utf-8 -*-
"""组件库页面（v8 C3 的窗口形态 → v9 V2 融合进设置面板）。

v8 时它是独立 `QDialog`：从设置页打开会**继承应用级模态**（点不动），
而且作为被拥有窗口被父窗口压住（BUG1）。v9 起不再是窗口，而是设置
面板里的一个分页（`settings_dialog.NAV` 的 "widgets"），模态与层级
问题从结构上消失。

职责：让用户**看见有哪些组件**（预览 + 一句话说明），并且一次点击就能
放到桌面。

与组件的关系：
    卡片预览调 `spec.preview(painter, rect)`；组件没提供 preview 时用
    `widget_glass.draw_preview()` 兜底。**预览不实例化组件**——否则打开
    组件库会顺带启定时器（监控每秒采样、天气立刻联网），不可接受。

四种卡片状态（规格 §3.3）：
    未添加            -> 「添加到桌面」
    多实例已添加      -> 「添加到桌面」（可继续加）
    已添加且隐藏      -> 「显示」
    已添加且显示中    -> 「已在桌面」灰显
    已添加的卡片右侧都有「移除」。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, Signal

QWIDGETSIZE_MAX = 16777215      # Qt 最大控件尺寸（解除 setMaximumWidth 限制用）
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import i18n
from . import theme
from . import widget_registry
from .widget_glass import draw_preview as _fallback_preview
from .widget_registry import WidgetSpec

# 内部标识（筛选状态、计数键）永远用中文常量：显示名可以翻译，落进
# `storage` 里的值不能（v10 D3 的硬规矩）。
ALL = "全部"
CARD_W = 320        # 2 列时的目标卡宽（可被拉伸）
CARD_MIN_W = 300    # 卡片最小宽度：再窄就减列，而不是挤卡片（W16）
CARD_H = 212
CARD_MAX_W = 420    # v14j：单列上限——单列时卡片拉伸到全宽（700+px）既空
                    # 又难看（用户反馈），限宽后居中
GRID_GAP = 12       # 卡片间距（列判定按它算）
CARD_MAX_COLS = 2   # 卡片网格最多两列（别叫 MAX_COLS：网格列上限在 grid_metrics）
COLS = CARD_MAX_COLS  # 旧名兼容


def columns_for(width, min_w: int = CARD_MIN_W, gap: int = GRID_GAP) -> int:
    """响应式列数（纯函数）：放得下几列就几列，上限 `CARD_MAX_COLS`。

    `width` 是卡片区视口宽（不含滚动条）。判据用**最小卡宽**——只有
    `width >= 2*min_w + gap` 才双列，否则单列拉伸占满。宁可减列也不挤
    卡片，横向滚动条从根上不会出现（scroll 侧再关一次兜底）。
    """
    try:
        w = int(width)
    except (TypeError, ValueError):
        return 1
    cols = 1
    while cols < CARD_MAX_COLS and w >= (cols + 1) * min_w + cols * gap:
        cols += 1
    return cols


class CategoryBar(QWidget):
    """组件库分类条：**顶部横向**一排胶囊（v10 A6 / 反馈 12）。

    v9 时分类在左侧竖排列表（150px 固定宽），把卡片区挤窄。改为顶部横排后
    卡片拿满宽度；分类多的时候**横向滚动**而不是换行——换行会白吃掉两行
    卡片高度。选中态用强调色描边，计数角标跟在名字后面。

    对外只暴露三个方法（`current` / `select` / `rebuild`）与一个 `changed`
    信号——内部用 QPushButton 还是 QListWidget 是实现细节，调用方不该依赖。
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(34)
        self.scroll.setStyleSheet("QScrollArea{background: transparent;}")
        holder = QWidget()
        self.row = QHBoxLayout(holder)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(8)
        self.row.addStretch(1)          # 分类少时左对齐，不拉伸铺满
        self.scroll.setWidget(holder)
        lay.addWidget(self.scroll)
        self._chips: List[QPushButton] = []
        self._current = ALL

    # ---------- 内部 ----------
    def _chip_qss(self, active: bool) -> str:
        edge = theme.ACCENT if active else "rgba(255,255,255,26)"
        fg = theme.rgba(theme.TEXT) if active else theme.rgba(theme.TEXT_DIM)
        bg = "rgba(76,194,255,26)" if active else "transparent"
        return ("QPushButton{background: %s; color: %s; border: 1px solid %s;"
                " border-radius: 15px; padding: 4px 12px; font-size: 12px;}"
                % (bg, fg, edge))

    def _on_clicked(self, name: str) -> None:
        if name == self._current:
            return
        self._current = name
        for c in self._chips:
            c.setStyleSheet(self._chip_qss(c.property("cat") == name))
        self.changed.emit()

    # ---------- 对外 ----------
    def current(self) -> str:
        return self._current

    def select(self, name: str) -> None:
        for c in self._chips:
            if c.property("cat") == name:
                if name != self._current:
                    self._on_clicked(name)
                return

    def rebuild(self, items: List[tuple]) -> None:
        """重建整排胶囊。`items` = [(分类名, 数量), …]，第一项默认选中。"""
        keep = self._current
        for c in self._chips:
            self.row.removeWidget(c)
            c.deleteLater()
        self._chips = []
        for name, count in items:
            c = QPushButton("%s  %d" % (i18n.category_label(name), count))
            c.setProperty("cat", name)
            c.setCursor(Qt.PointingHandCursor)
            c.setStyleSheet(self._chip_qss(name == keep))
            c.clicked.connect(lambda _c=False, n=name: self._on_clicked(n))
            self.row.insertWidget(len(self._chips), c)
            self._chips.append(c)
        names = [n for n, _c in items]
        self._current = keep if keep in names else (names[0] if names else ALL)
        for c in self._chips:
            c.setStyleSheet(self._chip_qss(c.property("cat") == self._current))
PREVIEW_H = 118
# 预览区的宽高比（320×118 ≈ 2.7:1）比组件的默认跨度（≈ 1.6:1）扁得多。
# 直接按预览区矩形的形状去调 `draw()`，组件会以为自己是"宽版"：监控组件
# 的 wide 布局把数值挪到右侧一列，2.7:1 下这列只有几十像素，"42%" 直接
# 压出卡片（2026-10-22 出 README 截图时暴露）。所以预览按默认比例收窄
# 居中渲染，看到的才是真机上那个样子。
# 取 1.45：必须**低于** widgets.monitor.WIDE_RATIO(1.6)，否则监控仍走宽版
PREVIEW_ASPECT = 1.45

def _btn_qss() -> str:
    return theme.BUTTON_QSS + (
        "QPushButton:disabled{background: rgba(255,255,255,10);"
        " border: 1px solid rgba(255,255,255,16); color: rgba(242,243,245,110);}"
    )


class PreviewArea(QWidget):
    """卡片上半：实时画组件预览（同一套 draw()，换个示例 model）。"""

    def __init__(self, spec: WidgetSpec, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.setFixedHeight(PREVIEW_H)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        # 按组件默认比例收窄居中（理由见 PREVIEW_ASPECT）
        vw = min(r.width(), r.height() * PREVIEW_ASPECT)
        inner = QRectF(r.center().x() - vw / 2.0, r.top(), vw, r.height())
        path = QPainterPath()
        path.addRoundedRect(r, 12, 12)
        p.save()
        p.setClipPath(path)
        # 只给真正画预览的那块铺底：预览收窄之后，左右留白若也铺深色，
        # 看起来就是"卡片里套了一张深色卡片"（双层面板）
        inner_path = QPainterPath()
        inner_path.addRoundedRect(inner, 10, 10)
        p.fillPath(inner_path, QColor(18, 20, 26, 190))
        try:
            if callable(self.spec.preview):
                self.spec.preview(p, inner)
            else:
                _fallback_preview(p, inner, self.spec.display_name(),
                                  self.spec.display_summary())
        except Exception:      # noqa: BLE001  —— 一个组件画挂不该让整个库白屏
            _fallback_preview(p, inner, self.spec.display_name(),
                              i18n.T("widget.preview_unavailable"))
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.setPen(QColor(255, 255, 255, 20))
        p.drawRoundedRect(r, 12, 12)


class WidgetCard(QFrame):
    """一张组件卡：预览 + 名称 + 说明 + 主按钮 + 移除。"""

    def __init__(self, spec: WidgetSpec, layer, owner, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.layer = layer
        self.owner = owner
        # W16：卡片不再固定宽——固定 320 在窄窗挤出横向滚动条；现在给
        # 最小宽 + 高度固定 + 水平可拉伸，1 列时自然"加宽卡片"。
        self.setMinimumWidth(CARD_MIN_W)
        self.setFixedHeight(CARD_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            "WidgetCard{background: rgba(255,255,255,10);"
            " border: 1px solid rgba(255,255,255,22); border-radius: 14px;}")

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.preview = PreviewArea(spec, self)
        root.addWidget(self.preview)

        name = QLabel(spec.display_name())
        name.setStyleSheet("color: %s; font-size: 14px; font-weight: 600;"
                           " background: transparent;" % theme.rgba(theme.TEXT))
        root.addWidget(name)

        sub = QLabel(spec.display_summary())
        sub.setWordWrap(True)
        sub.setStyleSheet("color: %s; font-size: 11px; background: transparent;"
                          % theme.rgba(theme.TEXT_DIM))
        root.addWidget(sub)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.button = QPushButton(i18n.T("widget_center.add"))
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.setStyleSheet(_btn_qss())
        self.button.clicked.connect(self._on_primary)
        row.addWidget(self.button, 1)

        self.remove_btn = QPushButton(i18n.T("widget_center.remove"))
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.setFixedWidth(56)
        self.remove_btn.setStyleSheet(_btn_qss())
        self.remove_btn.clicked.connect(self._on_remove)
        row.addWidget(self.remove_btn)

        # v9 V3：外部组件可卸载（= 删目录 + 移出注册表）；内置组件不给这个按钮，
        # 因为"删掉时钟"没有语义——它是程序的一部分。
        self.uninstall_btn = QPushButton(i18n.T("widget_center.uninstall"))
        self.uninstall_btn.setCursor(Qt.PointingHandCursor)
        self.uninstall_btn.setFixedWidth(52)
        self.uninstall_btn.setStyleSheet(_btn_qss())
        self.uninstall_btn.setToolTip(i18n.T("widget_center.uninstall_tip"))
        self.uninstall_btn.clicked.connect(self._on_uninstall)
        self.uninstall_btn.setVisible(bool(spec.plugin))
        row.addWidget(self.uninstall_btn)
        root.addLayout(row)

        self.refresh_state()

    # ---------- 状态 ----------
    def entries(self):
        return self.layer.entries_of(self.spec.type)

    def refresh_state(self):
        entries = self.entries()
        hidden = [e for e in entries if e.get("hidden")]
        if not entries:
            self.button.setText(i18n.T("widget_center.add"))
            self.button.setEnabled(True)
            self.remove_btn.setVisible(False)
        elif hidden:
            self.button.setText(i18n.T("widget_center.show"))
            self.button.setEnabled(True)
            self.remove_btn.setVisible(True)
        elif self.spec.multi_instance:
            self.button.setText(i18n.T("widget_center.add"))
            self.button.setEnabled(True)
            self.remove_btn.setVisible(True)
        else:
            self.button.setText(i18n.T("widget_center.present"))
            self.button.setEnabled(False)
            self.remove_btn.setVisible(True)

    # ---------- 动作 ----------
    def _on_primary(self):
        entries = self.entries()
        hidden = next((e for e in entries if e.get("hidden")), None)
        if hidden is not None:
            self.layer.toggle_entry(hidden.get("id"), True)
        else:
            self.layer.add(self.spec.type)
        self.owner.refresh()

    def _on_remove(self):
        entries = self.entries()
        if entries:
            self.layer.remove(entries[0].get("id"))
        self.owner.refresh()

    def _on_uninstall(self):
        from PySide6.QtWidgets import QMessageBox
        ok = QMessageBox.question(
            self, i18n.T("widget_center.uninstall_dialog"),
            i18n.T("widget_center.uninstall_confirm",
                   name=self.spec.display_name()),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        for e in list(self.entries()):
            self.layer.remove(e.get("id"))
        from . import widget_plugin
        widget_plugin.uninstall(self.spec.type)
        self.owner.reload_specs()


class WidgetCenterPage(QWidget):
    """组件库分页（嵌在设置面板里；不再有独立窗口）。"""

    settingsRequested = Signal()

    def __init__(self, layer, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.cards: List[WidgetCard] = []
        self._visible_types: List[str] = []
        # 背景由设置面板提供：这里透明，避免两层底色叠出灰块
        self.setStyleSheet("QWidget{background: transparent;}"
                           " QLabel{background: transparent;}")
        self.setObjectName("widgetCenterPage")

        root = QVBoxLayout(self)
        # v14j 反馈：上方大片空白——页内标题与右栏页标题重复（设置面板
        # 已经有「组件库」页标题），去掉标题行、收紧边距
        root.setContentsMargins(4, 4, 4, 10)
        root.setSpacing(8)

        # 顶部：搜索（v14j：页内标题删除——与右栏 pageTitle 重复元素）
        head = QHBoxLayout()
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(i18n.T("widget_center.search"))
        self.search.setFixedWidth(220)
        self.search.setStyleSheet(theme.INPUT_QSS)
        self.search.textChanged.connect(lambda _t: self.refresh())
        head.addWidget(self.search)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(0)

        # v10 A6（反馈 12）：分类从左侧竖排改成**顶部横向**胶囊排。
        # 竖排那 150px 固定宽把卡片区挤窄，横排后卡片拿满宽度。
        self.cats = CategoryBar(self)
        self.cats.changed.connect(lambda: self.refresh())
        root.addWidget(self.cats)
        root.addSpacing(10)

        # 右：卡片滚动区
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        # W16：横向滚动条永不出现——列数不够就减列拉伸，不靠滚动兜底
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea{background: transparent;}"
                                  " QScrollArea > QWidget > QWidget{background: transparent;}")
        holder = QWidget()
        self.grid = QGridLayout(holder)
        self.grid.setContentsMargins(0, 0, 8, 0)
        self.grid.setHorizontalSpacing(GRID_GAP)
        self.grid.setVerticalSpacing(GRID_GAP)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.empty_tip = QLabel(i18n.T("widget_center.empty"))
        self.empty_tip.setAlignment(Qt.AlignCenter)
        self.empty_tip.setStyleSheet("color: %s; font-size: 13px; padding: 40px 0;"
                                     % theme.rgba(theme.TEXT_FAINT))
        self.empty_tip.setVisible(False)
        self.grid.addWidget(self.empty_tip, 0, 0, 1, COLS)
        self.scroll.setWidget(holder)
        body.addWidget(self.scroll, 1)
        root.addLayout(body, 1)

        # 底部：计数（导入/新建入口在 V3 批次加在这里）
        foot = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: %s; font-size: 12px;"
                                       % theme.rgba(theme.TEXT_FAINT))
        foot.addWidget(self.count_label)
        foot.addStretch(1)

        # v9 V3：导入外部组件（zip）+ 打开组件目录（自己放目录进去也行）
        self.btn_open_dir = QPushButton(i18n.T("button.open_dir"))
        self.btn_open_dir.setStyleSheet(_btn_qss())
        self.btn_open_dir.setToolTip(i18n.T("widget_center.open_dir_tip"))
        self.btn_open_dir.clicked.connect(self._open_plugin_dir)
        foot.addWidget(self.btn_open_dir)

        self.btn_import = QPushButton(i18n.T("button.import"))
        self.btn_import.setStyleSheet(_btn_qss())
        self.btn_import.setToolTip(i18n.T("widget_center.import_tip"))
        self.btn_import.clicked.connect(self.import_zip_dialog)
        foot.addWidget(self.btn_import)

        # v10 A7-2（反馈 13）：组件库里给一个「关闭所有组件」——桌面只要图标时
        # 一个个点「移除」太累。**必须二次确认**：这是"桌面清空"级操作，
        # 误触一次用户就得去设置 ▸ 通用 ▸ 恢复组件 一个个加回来。
        self.btn_hide_all = QPushButton(i18n.T("button.hide_all"))
        self.btn_hide_all.setStyleSheet(_btn_qss())
        self.btn_hide_all.setToolTip(i18n.T("widget_center.hide_all_tip"))
        self.btn_hide_all.clicked.connect(self._hide_all_widgets)
        foot.addWidget(self.btn_hide_all)
        root.addLayout(foot)

        self._build_cards()
        self._build_categories()
        if hasattr(self.layer, "widgetsChanged"):
            self.layer.widgetsChanged.connect(self.refresh)
        self.refresh()

    # ---------- 构建 ----------
    def _build_cards(self):
        for spec in widget_registry.all_specs():
            card = WidgetCard(spec, self.layer, self, self)
            self.cards.append(card)

    def _build_categories(self):
        counts = self.category_counts()
        self.cats.rebuild([(ALL, counts.get(ALL, 0))]
                          + [(n, counts.get(n, 0))
                             for n in widget_registry.categories()])

    def category_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for spec in widget_registry.all_specs():
            counts[spec.category] = counts.get(spec.category, 0) + 1
        counts[ALL] = len(widget_registry.all_specs())
        return counts

    # ---------- 过滤 / 刷新 ----------
    def current_category(self) -> str:
        return self.cats.current()

    def select_category(self, name: str):
        self.cats.select(name)

    def _matches(self, spec: WidgetSpec, cat: str, query: str) -> bool:
        if cat != ALL and spec.category != cat:
            return False
        if not query:
            return True
        q = query.strip().lower()
        # 显示名与声明原文都算命中：中文系统里搜「天气」也命中，
        # 英文系统里搜 "weather" 同样命中（v10 D3）。
        pool = (spec.display_name(), spec.display_summary(),
                spec.name, spec.summary)
        return any(q in text.lower() for text in pool)

    def reload_specs(self):
        """注册表变了（导入/卸载插件）后重建卡片与分类。

        只重建**这一层**，不碰桌面组件实例：卸载流程自己负责把实例删掉，
        导入流程不需要重启任何东西。
        """
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        self._build_cards()
        self._build_categories()
        self.refresh()

    def import_zip_dialog(self):
        """选一个组件 zip 导入（返回 spec；取消/失败返回 None）。"""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from . import widget_plugin
        path, _f = QFileDialog.getOpenFileName(
            self, i18n.T("widget_center.import_dialog"),
            str(widget_plugin.plugin_root().parent),
            i18n.T("widget_center.import_filter"))
        if not path:
            return None
        try:
            spec = widget_plugin.import_zip(path)
        except widget_plugin.PluginError as exc:
            QMessageBox.warning(self, i18n.T("widget_center.import_failed"),
                                str(exc))
            return None
        except Exception as exc:      # noqa: BLE001 —— 任何异常都要给出可读反馈
            QMessageBox.warning(self, i18n.T("widget_center.import_failed"),
                                "%s: %s" % (type(exc).__name__, exc))
            return None
        self.reload_specs()
        self.select_category(ALL)
        self.search.setText(spec.display_name())   # 导入后直接定位到它
        return spec

    def _open_plugin_dir(self):
        import os
        from . import widget_plugin
        root = widget_plugin.plugin_root()
        root.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(root))      # noqa: S606 —— Windows 资源管理器
        except Exception:                # noqa: BLE001
            pass

    def _hide_all_widgets(self):
        """关闭所有组件（v10 A7-2 / 反馈 13）。二次确认后走 layer.hide_all()。"""
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle(i18n.T("widget_center.hide_all_title"))
        box.setText(i18n.T("widget_center.hide_all_text"))
        box.setInformativeText(i18n.T("widget_center.hide_all_info"))
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.button(QMessageBox.Yes).setText(i18n.T("button.close_all"))
        box.button(QMessageBox.No).setText(i18n.T("button.cancel"))
        if box.exec() != QMessageBox.Yes:
            return
        try:
            self.layer.hide_all()
        except Exception:                # noqa: BLE001
            return
        self.refresh()

    def refresh(self):
        cat = self.current_category()
        query = self.search.text() if hasattr(self, "search") else ""
        # 分类角标随数据刷新（数量是静态的，但保持一处计算）
        counts = self.category_counts()
        self.cats.rebuild([(ALL, counts.get(ALL, 0))]
                          + [(n, counts.get(n, 0))
                             for n in widget_registry.categories()])

        shown = []
        for card in self.cards:
            card.refresh_state()
            ok = self._matches(card.spec, cat, query)
            card.setVisible(ok)
            if ok:
                shown.append(card)

        # 重新摆格子（数量少，重建比增量挪动简单且不会错位）
        for card in self.cards:
            self.grid.removeWidget(card)
        self.grid.removeWidget(self.empty_tip)
        # W16：响应式列数——放得下 2 列才 2 列，否则 1 列
        cols = columns_for(self.scroll.viewport().width())
        self._cols = cols
        for c in range(CARD_MAX_COLS):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        # v14j 反馈：单列时卡片拉伸到 700+px 又空又难看——限宽居中；
        # 双列保持均分拉伸。
        if cols == 1:
            vw = self.scroll.viewport().width()
            col_w = min(CARD_MAX_W, max(CARD_MIN_W, vw - 24))
            for card in self.cards:
                card.setMaximumWidth(col_w)
            for i, card in enumerate(shown):
                self.grid.addWidget(card, i, 0, Qt.AlignHCenter)
        else:
            for card in self.cards:
                card.setMaximumWidth(QWIDGETSIZE_MAX)
            for i, card in enumerate(shown):
                self.grid.addWidget(card, i // cols, i % cols)
        self.empty_tip.setVisible(not shown)
        if not shown:
            self.grid.addWidget(self.empty_tip, 0, 0, 1, cols,
                                Qt.AlignHCenter)

        self._visible_types = [c.spec.type for c in shown]
        self.count_label.setText(
            i18n.T("widget_center.count", n=len(self.layer.storage.widgets)))

    def visible_cards(self) -> List[WidgetCard]:
        return [c for c in self.cards if c.isVisible()]

    def resizeEvent(self, e):
        """W16：窗口宽窄变化导致列数变化时重排（只在跨阈值那一下重排）。"""
        super().resizeEvent(e)
        cols = columns_for(self.scroll.viewport().width())
        if getattr(self, "_cols", None) != cols:
            self.refresh()

    def closeEvent(self, e):
        try:
            self.layer.widgetsChanged.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(e)


def open_center(layer, parent=None, window=None):
    """兼容入口：打开设置面板并定位到「组件库」分页。

    v9 起组件库没有独立窗口了，但托盘 / 桌面右键 / 老代码仍调用这个名字。
    延迟 import `settings_dialog`：它在模块级 import 本模块（页面要嵌进去），
    顶层互相 import 会成环。
    """
    from . import settings_dialog
    win = window if window is not None else parent
    storage = getattr(layer, "storage", None)
    return settings_dialog.open_settings(win, storage, layer, page="widgets")
