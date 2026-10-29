# -*- coding: utf-8 -*-
"""桌面面板小组件：桌面右侧信息面板（v10 A9 重构）。

## 这一版改了什么（2026-10-23 用户反馈 9）

> 桌面面板组件的编辑配色等功能无效，另外内部的天气与天气组件冲突，
> 重构一下组件的功能。

两个问题的根因是同一件事：**面板是唯一没接入组件样式层的老组件**。

  1. 配色无效：面板继承 `FloatingWidget` 并自己写死 RGB（`rgb(216,220,226)`
     之类），而"背景色 / 文字色 / 强调色 / 圆角"全在 `widget_style` 里。
     改动：继承 `GlassWidget`，所有颜色从 `self.style` 派生，QSS 在
     `_apply_style()` 里统一重建（构造时 + 设置变更时）。
  2. 天气冲突：面板里那张卡是写死的本地假数据（固定温度与城市），而桌面上
     还有一个真的联网天气组件。改动：天气卡订阅 `weather/source.py` 的
     共享源（与天气组件同一份城市、同一份缓存）。

## 结构（自上而下，每块都能单独关掉）

  - 搜索框：过滤 应用（开始菜单扫描）+ 桌面文件，回车启动第一个
  - 快捷文件夹：最近下载 / 桌面 / 图片 / 文档 / 视频
  - 磁盘卡：系统盘已用 / 总量 + 占用条
  - 最近下载卡：按修改时间列最近 5 个文件
  - 天气卡：**共享天气源**（与天气组件同一份数据）

与其它组件一样挂在桌面带（Progman 正上方），可拖动 / 缩放 / 关闭。
"""
import datetime
import os
import shutil
from pathlib import Path

from PySide6.QtCore import QFileInfo, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFileIconProvider, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

from ... import appsource
from ... import i18n
from ... import theme
from ... import widget_style
from ...widget_glass import GlassWidget
from ..weather import source as wsource
from ..weather import weather_api as wa
from ..weather.weather import draw_weather_icon

_ICON_PROVIDER = QFileIconProvider()

_GUID_BY_NAME = {
    "Downloads": "downloads", "下载": "downloads",
    "Desktop": "desktop", "桌面": "desktop",
    "Pictures": "pictures", "图片": "pictures",
    "Documents": "documents", "文档": "documents",
    "Videos": "videos", "视频": "videos",
}

CARD_H = 96          # 磁盘 / 天气卡固定高度
RECENT_MAX = 5       # 最近下载最多 5 行


def _known_folder(*names):
    """用户目录：Shell 已知目录 API 优先（处理 OneDrive 重定向），
    中英文目录名兜底；找不到返回 None。"""
    for n in names:
        p = appsource.shell_known_folder(_GUID_BY_NAME.get(n, ""))
        if p and Path(p).exists():
            return Path(p)
    home = Path.home()
    for n in names:
        p = home / n
        if p.exists():
            return p
    return None


def _dim(color: QColor, factor: float) -> QColor:
    out = QColor(color)
    out.setAlphaF(max(0.0, min(1.0, color.alphaF() * factor)))
    return out


def _qss_color(c: QColor) -> str:
    return "rgba(%d,%d,%d,%.3f)" % (c.red(), c.green(), c.blue(), c.alphaF())


def _font_css() -> str:
    return ", ".join('"%s"' % f for f in theme.FONT_FAMILIES)


class StyleCard(QWidget):
    """面板里的卡片基类：颜色全部来自面板的 Style（v10 A9）。

    旧实现的子控件直接写死 RGB，于是"背景色 / 文字色"设置对它们无效。
    这里只负责"卡片底"，内容由子类 `paint_body()` 画。
    """

    def __init__(self, style_provider, parent=None):
        super().__init__(parent)
        self._style_provider = style_provider
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    @property
    def style(self):
        try:
            return self._style_provider() or widget_style.DEFAULT_STYLE
        except Exception:      # noqa: BLE001
            return widget_style.DEFAULT_STYLE

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        st = self.style
        # 卡片底 = 用户背景色（未设 -> 主文字色 10% 的淡玻璃）
        base = QColor(st.bg) if st.bg is not None else QColor(st.text)
        base.setAlphaF(min(0.9, 0.18 + st.alpha * 0.10) if st.bg is not None
                       else 0.10)
        p.setBrush(base)
        pen = QColor(st.text)
        pen.setAlphaF(0.14)
        p.setPen(QPen(pen, 1))
        r = min(float(theme.RADIUS_CARD), self.height() / 2.0)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), r, r)
        self.paint_body(p)

    def paint_body(self, p):
        """子类实现。"""


class DiskCard(StyleCard):
    """系统磁盘占用卡：标题 + 占用条 + 已用/总量。"""

    def __init__(self, style_provider, parent=None):
        super().__init__(style_provider, parent)
        self.setFixedHeight(CARD_H)
        self.used = 0
        self.total = 0
        self._read()

    def _read(self):
        try:
            root = (os.environ.get("SystemDrive", "C:") or "C:") + "\\"
            du = shutil.disk_usage(root)
            self.used, self.total = du.used, du.total
        except Exception:      # noqa: BLE001
            self.used, self.total = 0, 0

    @staticmethod
    def _gb(n) -> str:
        return "%.1f GB" % (n / 1e9) if n < 1000e9 else "%.0f GB" % (n / 1e9)

    def paint_body(self, p):
        st = self.style
        pad = 14
        p.setPen(QColor(st.text))
        p.setFont(theme.ui_font(14, bold=True))
        drive = (os.environ.get("SystemDrive", "C:") or "C:")
        p.drawText(QRect(pad, 12, 220, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   i18n.T("panel.disk_title", drive=drive))
        if self.total <= 0:
            p.setPen(QColor(st.faint))
            p.setFont(theme.ui_font(11))
            p.drawText(QRect(pad, 46, self.width() - pad * 2, 20),
                       Qt.AlignVCenter, i18n.T("panel.disk_unavailable"))
            return
        free = self.total - self.used
        p.setPen(QColor(st.dim))
        p.setFont(theme.ui_font(12))
        p.drawText(QRect(self.width() - pad - 200, 12, 200, 22),
                   Qt.AlignRight | Qt.AlignVCenter,
                   i18n.T("panel.disk_free", size=self._gb(free)))
        bar = QRectF(pad, 46, self.width() - pad * 2, 8)
        p.setPen(Qt.NoPen)
        track = QColor(st.text)
        track.setAlphaF(0.12)
        p.setBrush(track)
        p.drawRoundedRect(bar, 4, 4)
        fw = max(8.0, bar.width() * self.used / self.total)
        p.setBrush(QColor(st.accent))
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), fw, bar.height()), 4, 4)
        p.setPen(QColor(st.dim))
        p.setFont(theme.ui_font(12))
        pct = self.used * 100.0 / self.total
        p.drawText(QRect(pad, 62, self.width() - pad * 2, 20),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   i18n.T("panel.disk_used", used=self._gb(self.used),
                          total=self._gb(self.total), pct=pct))


class WeatherCard(StyleCard):
    """天气卡：**订阅共享天气源**（v10 A9），不画本地假数据。

    与「天气」组件同一份城市 / 缓存 / 刷新结果——面板不会和天气组件打架。
    源里没有数据时显示"右键 ▸ 组件设置，填写城市"，与天气组件一致。
    """

    def __init__(self, storage, style_provider, parent=None):
        super().__init__(style_provider, parent)
        self.setFixedHeight(CARD_H)
        self.storage = storage
        self._src = wsource.shared(storage)
        self._src.subscribe(self._on_source)

    def _on_source(self):
        self.update()

    def release(self):
        try:
            self._src.unsubscribe(self._on_source)
        except (RuntimeError, AttributeError):
            pass

    def paint_body(self, p):
        st = self.style
        model = self._src.model()
        state = self._src.state()
        pad = 14
        cx, cy, r = pad + 16, 48, 11
        if model is None or state == "empty":
            half = r * 2
            draw_weather_icon(p, "cloud",
                              QRectF(cx - r, cy - half / 2, half, half),
                              QColor(st.faint))
            p.setPen(QColor(st.dim))
            p.setFont(theme.ui_font(12))
            p.drawText(QRect(cx + 20, 26, self.width() - cx - 30, 40),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       i18n.T("panel.weather_unset"))
            return
        icon_col = QColor(255, 195, 87) if model.icon == "sun" else QColor(st.accent)
        draw_weather_icon(p, model.icon, QRectF(cx - r, cy - r, r * 2, r * 2),
                          icon_col)
        p.setPen(QColor(st.text))
        p.setFont(theme.ui_font(26, bold=True))
        p.drawText(QRect(cx + 24, 26, 120, 40), Qt.AlignLeft | Qt.AlignVCenter,
                   wa.fmt_temp(model.temp))
        p.setPen(QColor(st.dim))
        p.setFont(theme.ui_font(13))
        p.drawText(QRect(cx + 24, 26, 160, 40), Qt.AlignRight | Qt.AlignVCenter,
                   model.text)
        p.setPen(QColor(st.faint))
        p.setFont(theme.ui_font(12))
        city = self._src.city() or model.city or ""
        key = "panel.weather_cached" if state == "cached" else "panel.weather_city_note"
        p.drawText(QRect(self.width() - pad - 240, 62, 240, 20),
                   Qt.AlignRight | Qt.AlignVCenter, i18n.T(key, city=city))


class RecentCard(StyleCard):
    """最近下载卡：按修改时间列最近文件，点击打开。"""

    def __init__(self, style_provider, parent=None):
        super().__init__(style_provider, parent)
        self.rows = []   # [(path, row_widget)]
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(14, 12, 14, 12)
        self._lay.setSpacing(6)
        self._title = QLabel("", self)
        self._title.setStyleSheet("background: transparent;")
        self._btn_open = QToolButton(self)
        self._btn_open.setCursor(Qt.PointingHandCursor)
        self._btn_open.clicked.connect(self._open_folder)
        head = QHBoxLayout()
        head.addWidget(self._title)
        head.addStretch(1)
        head.addWidget(self._btn_open)
        self._lay.addLayout(head)
        self.row_lay = QVBoxLayout()
        self.row_lay.setSpacing(2)
        self._lay.addLayout(self.row_lay)
        self._lay.addStretch(1)
        self._reload()
        self.setMinimumHeight(150)

    # ---------- 配色 ----------
    def refresh_style(self):
        st = self.style
        self._title.setText(i18n.T("panel.recent_title"))
        self._title.setStyleSheet(
            "color: %s; font-size: 13px; background: transparent;"
            % _qss_color(st.dim))
        self._btn_open.setText(i18n.T("panel.recent_open"))
        self._btn_open.setStyleSheet(
            "QToolButton{background: transparent; color: %s; border: none;"
            " font-size: 12px; padding: 2px 4px;}" % _qss_color(st.accent))
        for _path, row in self.rows:
            for lab, role in getattr(row, "_labels", ()):
                col = st.text if role == "name" else st.faint
                lab.setStyleSheet(
                    "color: %s; font-size: 12px; background: transparent;"
                    % _qss_color(QColor(col)))

    def _open_folder(self):
        d = _known_folder("Downloads", "下载")
        if d is not None:
            appsource.launch({"target": str(d)})

    def _clear_rows(self):
        while self.row_lay.count():
            item = self.row_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.rows = []

    def _reload(self):
        self._clear_rows()
        d = _known_folder("Downloads", "下载")
        items = []
        if d is not None:
            try:
                for f in d.iterdir():
                    if f.is_file():
                        items.append(f)
            except Exception:      # noqa: BLE001
                pass
        try:
            items.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        except Exception:      # noqa: BLE001
            pass
        now = datetime.datetime.now()
        for f in items[:RECENT_MAX]:
            row = QWidget()
            row.setCursor(Qt.PointingHandCursor)
            row.setStyleSheet("background: transparent;")
            hl = QHBoxLayout(row)
            hl.setContentsMargins(4, 3, 4, 3)
            hl.setSpacing(8)
            icon = QLabel(row)
            pm = _ICON_PROVIDER.icon(QFileInfo(str(f))).pixmap(QSize(20, 20))
            icon.setPixmap(pm)
            name = QLabel(f.name, row)
            dt = datetime.datetime.fromtimestamp(f.stat().st_mtime)
            when = i18n.T("panel.today") if dt.date() == now.date() \
                else dt.strftime("%m-%d")
            when_lbl = QLabel(when, row)
            hl.addWidget(icon)
            hl.addWidget(name, 1)
            hl.addWidget(when_lbl)
            row._labels = ((name, "name"), (when_lbl, "faint"))
            row.mousePressEvent = lambda e, p=str(f): appsource.launch({"target": p})
            self.row_lay.addWidget(row)
            self.rows.append((str(f), row))
        if not self.rows:
            empty = QLabel(i18n.T("panel.recent_empty"), self)
            self.row_lay.addWidget(empty)
            self.rows.append(("", empty))
        self.refresh_style()
        self.update()


def _magnifier_pixmap() -> QPixmap:
    """自绘放大镜图标（浅灰）。"""
    pm = QPixmap(18, 18)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(150, 156, 165, 220))
    p.drawEllipse(QRect(3, 3, 9, 9))
    p.drawLine(11, 11, 15, 15)
    p.end()
    return pm


class DesktopPanel(GlassWidget):
    """桌面面板（v10 A9）：玻璃底 + 样式层配色 + 模块可开关。"""

    TYPE = "panel"

    def __init__(self, storage, entry):
        # interactive=True：搜索框 / 按钮需要键盘焦点
        super().__init__(storage, entry, interactive=True)
        self.setMinimumSize(*self._mode_minimum())
        self._apps_cache = None
        self._cards = []
        self._build()
        self._apply_style()

    def _mode_minimum(self):
        """悬浮态要容得下搜索框 + 至少一张卡片；锚定态交给基类（1 格）。

        200×260 是**内容**下限，不该成为网格里的碰撞尺寸——否则用户把
        面板缩成 3×1，窗口被撑住、跨度反推回 3×3（v15e）。
        """
        if getattr(self, "entry", {}).get("anchored"):
            return super()._mode_minimum()
        return (200, 260)

    # ---------- 配置（entry.settings） ----------
    def _opt(self, key, default=True) -> bool:
        s = self.entry.get("settings") or {}
        v = s.get(key, default)
        return default if v is None else bool(v)

    # ---------- UI ----------
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        self._lay = lay

        self.ed_search = QLineEdit()
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.addAction(QIcon(_magnifier_pixmap()),
                                 QLineEdit.LeadingPosition)
        self.ed_search.textChanged.connect(self._on_search)
        self.ed_search.returnPressed.connect(self._launch_first)
        lay.addWidget(self.ed_search)

        self.list_results = QListWidget()
        self.list_results.setIconSize(QSize(24, 24))
        self.list_results.setMaximumHeight(200)
        self.list_results.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list_results.hide()
        self.list_results.itemClicked.connect(lambda it: self._launch_item(it))
        lay.addWidget(self.list_results)

        self.quick_box = QWidget()
        self.quick_box.setStyleSheet("background: transparent;")
        folders = QGridLayout(self.quick_box)
        folders.setContentsMargins(0, 0, 0, 0)
        folders.setSpacing(4)
        quick = (
            ("panel.folder_downloads", _known_folder("Downloads", "下载")),
            ("panel.folder_desktop", _known_folder("Desktop", "桌面")),
            ("panel.folder_pictures", _known_folder("Pictures", "图片")),
            ("panel.folder_documents", _known_folder("Documents", "文档")),
            ("panel.folder_videos", _known_folder("Videos", "视频")),
        )
        self._quick_buttons = []
        for i, (key, path) in enumerate(quick):
            btn = QToolButton()
            btn.setProperty("i18nKey", key)
            if path is not None:
                btn.setIcon(_ICON_PROVIDER.icon(QFileInfo(str(path))))
                btn.setToolTip(str(path))
                btn.clicked.connect(
                    lambda checked=False, p=str(path): appsource.launch({"target": p}))
            else:
                btn.setEnabled(False)
                btn.setToolTip(i18n.T("panel.folder_missing"))
            btn.setIconSize(QSize(28, 28))
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setFixedSize(60, 56)
            btn.setCursor(Qt.PointingHandCursor)
            folders.addWidget(btn, i // 3, i % 3)
            self._quick_buttons.append(btn)
        folders.setColumnStretch(2, 1)
        lay.addWidget(self.quick_box)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea{background: transparent;}")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._inner_lay = QVBoxLayout(inner)
        self._inner_lay.setContentsMargins(0, 0, 0, 0)
        self._inner_lay.setSpacing(12)

        prov = lambda: self.style          # noqa: E731
        self.disk_card = DiskCard(prov)
        self.recent_card = RecentCard(prov)
        self.weather_card = WeatherCard(self.storage, prov)
        for card in (self.disk_card, self.recent_card, self.weather_card):
            self._cards.append(card)
            self._inner_lay.addWidget(card)
        self._inner_lay.addStretch(1)
        self.scroll.setWidget(inner)
        lay.addWidget(self.scroll, 1)

    # ---------- 样式 ----------
    def _apply_style(self):
        """把 st（背景/文字/强调色/圆角）落到所有子控件（v10 A9 反馈 9）。

        旧面板把这些颜色写死在 QSS 里，于是「组件设置 ▸ 外观」改了什么
        都不会变——用户以为功能坏了。现在**一处派生、一次刷新**。
        """
        st = self.style
        font = _font_css()
        self.ed_search.setPlaceholderText(i18n.T("panel.search_placeholder"))
        self.ed_search.setStyleSheet(
            "QLineEdit{background: rgba(255,255,255,0.10); border: 1px solid %s;"
            " border-radius: 10px; padding: 7px 10px; color: %s;"
            " font-family: %s; font-size: 13px;}"
            "QLineEdit:focus{border: 1px solid %s;}"
            % (_qss_color(_dim(st.text, 0.18)), _qss_color(st.text), font,
               _qss_color(st.accent)))
        self.list_results.setStyleSheet(theme.LIST_QSS)
        btn_qss = (
            "QToolButton{background: transparent; color: %s; border: none;"
            " border-radius: 10px; padding: 4px; font-family: %s; font-size: 12px;}"
            "QToolButton:hover{background: rgba(255,255,255,22);}"
            "QToolButton:pressed{background: rgba(255,255,255,34);}"
            "QToolButton:disabled{color: %s;}"
            % (_qss_color(st.text), font, _qss_color(st.faint)))
        for btn in self._quick_buttons:
            btn.setText(i18n.T(btn.property("i18nKey")))
            btn.setStyleSheet(btn_qss)
        for card in self._cards:
            card.update()
        self.recent_card.refresh_style()
        self._apply_sections()

    def _apply_sections(self):
        """按设置显示 / 隐藏各个模块（面板 = 可组合的信息面板）。"""
        self.ed_search.setVisible(self._opt("show_search", True))
        self.quick_box.setVisible(self._opt("show_folders", True))
        self.disk_card.setVisible(self._opt("show_disk", True))
        self.recent_card.setVisible(self._opt("show_recent", True))
        self.weather_card.setVisible(self._opt("show_weather", True))

    def on_settings_changed(self):
        super().on_settings_changed()      # 清样式缓存
        self._apply_style()

    # ---------- 搜索 ----------
    def _on_search(self, text):
        # B12/P1-6（2026-10-26）：每键直查会连打 all_apps + scan_desktop_files
        # （OneDrive 桌面下明显卡顿）——200ms 防抖合并连续输入
        if not hasattr(self, "_search_debounce"):
            from PySide6.QtCore import QTimer
            self._search_debounce = QTimer(self)
            self._search_debounce.setSingleShot(True)
            self._search_debounce.setInterval(200)
            self._search_debounce.timeout.connect(self._do_search)
        self._pending_search = text
        self._search_debounce.start()

    def _do_search(self):
        text = (getattr(self, "_pending_search", "") or "").strip()
        self.list_results.clear()
        if not text:
            self.list_results.hide()
            return
        low = text.lower()
        hits = []
        if self._apps_cache is None:
            try:
                self._apps_cache = appsource.all_apps()
            except Exception:      # noqa: BLE001
                self._apps_cache = []
        for app in self._apps_cache:
            if low in app.get("name", "").lower():
                hits.append(app)
            if len(hits) >= 8:
                break
        for f in appsource.scan_desktop_files():
            if low in f.get("name", "").lower():
                hits.append(f)
            if len(hits) >= 12:
                break
        if not hits:
            item = QListWidgetItem(i18n.T("panel.no_results"))
            item.setFlags(Qt.NoItemFlags)
            self.list_results.addItem(item)
        else:
            for app in hits[:8]:
                item = QListWidgetItem(appsource.icon_for(app, 24),
                                       app.get("name", ""))
                item.setData(Qt.UserRole, app)
                self.list_results.addItem(item)
        self.list_results.show()

    def _launch_first(self):
        if self.list_results.count():
            self._launch_item(self.list_results.item(0))

    def _launch_item(self, item):
        app = item.data(Qt.UserRole)
        if app:
            appsource.launch(app)
            self.ed_search.clear()
            self.list_results.hide()

    def closeEvent(self, e):
        try:
            self.weather_card.release()
        finally:
            super().closeEvent(e)
