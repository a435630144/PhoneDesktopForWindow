# -*- coding: utf-8 -*-
"""设置窗口（2026-10-19 重构：苹果 / AirDeck 风格的侧栏 + 分组行）。

用户反馈 10："设置页面现在文字颜色、样式都不好"，并给出 AirDeck 的
参考图——左侧分栏导航、右侧「标题 + 说明 + 圆角卡片行 + 开关」的结构。

设计落点（全部走 theme token，不再散落十六进制）：
  - 侧栏：品牌区（圆角图标 + 产品名）+ 5 个导航项（选中为实心蓝胶囊）
  - 内容区：页标题 22px 粗体 / 副标题 13px 次级色（对比度达标），
    卡片内每行 = 左标题（主文字）+ 右控件；说明文字压在标题下方
  - 开关：自绘 46×26 胶囊（选中 ACCENT），代替系统勾选框——勾选框在
    深色面板上观感割裂，也是"样式不好"的直接来源
  - 所有项实时生效，控件属性名与旧版保持一致（chk_dev / chk_takeover …）

分组：通用 / 外观与图标 / 布局与网格 / 模式 / 高级。
"""
from PySide6.QtCore import (Property, QElapsedTimer, QEasingCurve,
                            QPropertyAnimation, QSize, Qt, QTimer, QUrl)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QGuiApplication,
                           QIcon, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                               QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import autostart
from . import dock
from . import grid_flow
from . import i18n
from . import interaction
from . import liquid_glass
from . import page_rules
from . import updater
from .stepper import Stepper
from .ui_choice import ComboBox
from . import theme


class Switch(QCheckBox):
    """苹果风格开关（QCheckBox 子类，外部按勾选框语义使用）。

    自绘 46×26 胶囊 + 20px 白色圆钮，勾选时轨道 ACCENT；knob 位置由
    `t`（0→1）驱动 160ms 弹簧，与桌面上的动效语言一致。
    """

    W, H, KNOB = 46, 26, 20

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self._t = 0.0
        self._anim = None
        self.toggled.connect(self._animate_to_state)

    def _get_t(self):
        return self._t

    def _set_t(self, v):
        self._t = max(0.0, min(1.0, float(v)))
        self.update()

    t = Property(float, _get_t, _set_t)

    def _animate_to_state(self, checked):
        if self._anim is not None:
            self._anim.stop()
        anim = QPropertyAnimation(self, b"t", self)
        anim.setDuration(160)
        anim.setStartValue(self._t)
        anim.setEndValue(1.0 if checked else 0.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._anim = anim

    def setChecked(self, checked):
        """初始化时**直接就位**，不播 160ms 动画。

        v11：旧实现在建窗时先 `setChecked(True)` 再显示，动画从 0 开始跑，
        打开设置面板会看到一排开关"从关到开"地翻一遍；更糟的是首帧截图/
        低帧率机器上它看起来就是**关着**的（用户以为设置丢了）。
        未显示时直接置位；已显示时的用户操作仍然走弹簧动画。
        """
        super().setChecked(bool(checked))
        if not self.isVisible():
            if self._anim is not None:
                self._anim.stop()
                self._anim = None
            self._set_t(1.0 if checked else 0.0)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self._t
        off = QColor(255, 255, 255, 34)
        on = QColor(theme.ACCENT)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * t),
            int(off.green() + (on.green() - off.green()) * t),
            int(off.blue() + (on.blue() - off.blue()) * t),
            int(off.alpha() + (on.alpha() - off.alpha()) * t),
        )
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(0, 0, self.W, self.H, self.H / 2, self.H / 2)
        x = 3 + t * (self.W - self.KNOB - 6)
        p.setBrush(QColor(0, 0, 0, 46))
        p.drawEllipse(int(x), 4, self.KNOB, self.KNOB)   # 极淡投影
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(int(x), 3, self.KNOB, self.KNOB)


_dialog = None       # 单例（模块级）：多处入口共用同一个设置窗口


def open_settings(window, storage, layer, page="general"):
    """打开设置面板（单例）并定位到指定分页；已开则提到前面。

    托盘 / 桌面右键 / 组件库入口都走这里——以前各自 `SettingsDialog(...)
    .exec()`，点两次就有两个窗口，且模态会挡住桌面。
    """
    global _dialog
    try:
        if _dialog is not None:
            _dialog.select_page(page)
            _dialog.show()
            _dialog.raise_()
            _dialog.activateWindow()
            return _dialog
    except RuntimeError:      # C++ 对象已被销毁（用户关窗后回收）
        _dialog = None
    dlg = SettingsDialog(window, storage, layer, page=page)
    _dialog = dlg
    dlg.show()
    dlg.raise_()
    return dlg


class SettingsDialog(QDialog):
    # v9 V2：「组件库」分页（原独立窗口融合进来，BUG1 从结构上消失）
    # v14h 重梳理（用户反馈：有的页不用单独分 sheet、有的内容放错页）：
    #   * 原「外观与图标」拆解——图标尺寸/占比两张卡归「图标与标签」页
    #     （图标的事都在一页）；悬浮模式不透明度归「模式」页（悬浮是交互
    #     模式的一部分）；液态玻璃参数多（9 个滑条/开关 + 渲染频率），
    #     独立成「液态玻璃」页；
    #   * 「开发者」并入「高级」——两页各只有两三张卡，合并后一页刚好；
    #   * 9 页 -> 8 页。
    NAV = (("general", "通用"), ("widgets", "组件库"), ("dock", "Dock 栏"),
           ("icons", "图标与标签"), ("liquid", "液态玻璃"),
           ("layout", "布局与网格"),
           ("mode", "模式"), ("advanced", "高级"))

    def __init__(self, window, storage, layer, parent=None, page=None):
        super().__init__(parent)
        self.window = window
        self.storage = storage
        self.layer = layer
        self.setWindowTitle(i18n.T("settings.title"))
        self.setStyleSheet(theme.settings_qss())
        # W16：最小宽从 860 降到 600——否则窗口窄不下去，组件库永远到不了
        # 1 列形态（2×300+12 的列判定在 860 宽下恰好总是双列）。
        self.setMinimumSize(600, 560)
        self._nav_buttons = []
        self._pages = {}
        # Dock 外观滑条的落盘去抖（v10 反馈 27）：拖滑条时每个像素都会
        # 写设置，若同步 save() 就是"每帧一次磁盘 IO + 全量重建图标"，
        # 主线程直接被拖死（用户实测卡到崩溃）。250ms 合并成一次写盘。
        self._dock_save_timer = QTimer(self)
        self._dock_save_timer.setSingleShot(True)
        self._dock_save_timer.timeout.connect(self._flush_storage)
        self._build()
        if page:
            self.select_page(page)

    # ---------- 骨架 ----------
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        right = QWidget()
        right.setObjectName("settingsContent")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(28, 22, 28, 18)
        rl.setSpacing(14)
        self.lbl_page_title = QLabel(i18n.T("settings.nav.general"), right)
        self.lbl_page_title.setObjectName("pageTitle")
        self.lbl_page_sub = QLabel("", right)
        self.lbl_page_sub.setObjectName("pageSubtitle")
        self.lbl_page_sub.setWordWrap(True)
        rl.addWidget(self.lbl_page_title)
        rl.addWidget(self.lbl_page_sub)

        self.stack = QStackedWidget(right)
        self.stack.setStyleSheet("background: transparent;")
        for key, _label in self.NAV:
            page = self._page(key)
            self._pages[key] = page          # 供测试/自动化按页定位控件
            self.stack.addWidget(page)
        rl.addWidget(self.stack, 1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self.btn_close = QPushButton(i18n.T("button.done"))
        self.btn_close.setObjectName("primaryButton")
        self.btn_close.clicked.connect(self.accept)
        bar.addWidget(self.btn_close)
        rl.addLayout(bar)
        root.addWidget(right, 1)

        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(920, int(avail.width() * 0.72)),
                    min(680, int(avail.height() * 0.86)))
        self._select_nav(0)

    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("settingsSide")
        side.setFixedWidth(212)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(16, 22, 16, 16)
        lay.setSpacing(6)

        brand = QHBoxLayout()
        # v14h：logo 用托盘同款应用图标（assets/app_icon.png），不再画
        # 文字字徽；图标读不到时退回字徽（不留空方块）
        logo = QLabel()
        logo.setObjectName("brandLogo")
        logo.setFixedSize(34, 34)
        logo.setAlignment(Qt.AlignCenter)
        try:
            from .paths import asset_path
            _pm = QPixmap(asset_path("app_icon.png"))
            if not _pm.isNull():
                logo.setPixmap(_pm.scaled(26, 26, Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation))
            else:
                logo.setText(i18n.T("settings.brand_logo"))
        except Exception:      # noqa: BLE001
            logo.setText(i18n.T("settings.brand_logo"))
        names = QVBoxLayout()
        names.setSpacing(0)
        t1 = QLabel(i18n.T("settings.brand"))
        t1.setObjectName("brandName")
        t2 = QLabel(i18n.T("settings.brand_sub"))
        t2.setObjectName("brandSub")
        names.addWidget(t1)
        names.addWidget(t2)
        brand.addWidget(logo)
        brand.addLayout(names)
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(14)

        for i, (key, label) in enumerate(self.NAV):
            # NAV 里的中文是兜底；显示名走 i18n（v10 D3）。
            # `&` 在 QPushButton 里是助记符（"Layout & grid" 会画成
            # "Layout  grid"），按钮上必须写成 `&&`；表里保持单个 `&`，
            # 因为同一串还喂给 QLabel 标题（换成 `&&` 会原样显示两个）。
            btn = QPushButton(
                i18n.TL("settings.nav." + key, label).replace("&", "&&"))
            btn.setObjectName("navItem")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _c=False, i=i: self._select_nav(i))
            self._nav_buttons.append(btn)
            lay.addWidget(btn)
        lay.addStretch(1)

        # v11（反馈 34）：左下角 GitHub 图标——一键去仓库看源码 / 提 issue。
        # 图标走 assets/github.svg（QtSvg 插件缺失时退化成文字，不留下
        # 一个空白方块）。
        self.btn_github = QPushButton()
        self.btn_github.setObjectName("githubLink")
        self.btn_github.setCursor(Qt.PointingHandCursor)
        self.btn_github.setFixedSize(32, 32)
        self.btn_github.setToolTip(i18n.T("settings.github.tip"))
        self.btn_github.setAccessibleName(i18n.T("settings.github.tip"))
        try:
            # 显式 import QtSvg：QIcon 读 SVG 依赖 imageformats/qsvg 插件，
            # 不 import 这个模块时打包器可能不收集插件（源码态能显示、
            # 冻结包变成空白方块）。失败也不影响使用（下面有文字兜底）。
            from PySide6 import QtSvg  # noqa: F401
            from .paths import asset_path
            icon = QIcon(asset_path("github.svg"))
        except Exception:      # noqa: BLE001
            icon = QIcon()
        if icon.isNull():
            self.btn_github.setText("GitHub")
            self.btn_github.setObjectName("githubLinkText")
        else:
            self.btn_github.setIcon(icon)
            self.btn_github.setIconSize(QSize(19, 19))
        self.btn_github.clicked.connect(
            lambda: self._open_url(updater.PROJECT_URL))
        lay.addWidget(self.btn_github, 0, Qt.AlignLeft)
        lay.addSpacing(4)

        from . import version
        ver = QLabel(version.label())
        ver.setObjectName("sideVersion")
        # 英文标签比中文长一截，固定 212px 侧栏会把它切断；换行比截断可读
        ver.setWordWrap(True)
        lay.addWidget(ver)
        return side

    def _select_nav(self, index):
        for i, b in enumerate(self._nav_buttons):
            b.setChecked(i == index)
        key = self.NAV[index][0]
        title = i18n.T("settings.nav." + key)
        sub = i18n.T("settings.sub." + key)
        self.lbl_page_title.setText(title)
        self.lbl_page_sub.setText(sub)
        self.stack.setCurrentIndex(index)

    # ---------- 行 / 卡片 ----------
    def _card(self, parent, title="", reset_fn=None):
        """一张设置卡片。`reset_fn` 非 None 时标题右侧加「重置」小按钮。"""
        card = QFrame(parent)
        card.setObjectName("settingsCard")
        card.setProperty("groupTitle", title)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(2)
        if title:
            hdr = QHBoxLayout()
            hdr.setSpacing(8)
            lb = QLabel(title, card)
            lb.setObjectName("cardTitle")
            hdr.addWidget(lb)
            hdr.addStretch(1)
            if reset_fn is not None:
                btn = QPushButton(i18n.T("button.reset_defaults"), card)
                btn.setObjectName("resetBtn")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(reset_fn)
                hdr.addWidget(btn)
            lay.addLayout(hdr)
            lay.addSpacing(6)
        return card, lay

    def _row(self, card_lay, parent, title, control, hint=""):
        """一行：左标题（+说明），右控件。返回行容器（测试可定位）。"""
        row = QWidget(parent)
        row.setObjectName("settingsRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 7, 0, 7)
        h.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(2)
        t = QLabel(title, row)
        t.setObjectName("rowTitle")
        t.setWordWrap(True)
        text.addWidget(t)
        if hint:
            d = QLabel(hint, row)
            d.setObjectName("rowHint")
            d.setWordWrap(True)
            text.addWidget(d)
        h.addLayout(text, 1)
        if control is not None:
            # 行标题同时写进控件：① 屏幕阅读器能念出这一行是什么；
            # ② 测试/自动化可按文本定位（Switch 自绘不画 text，视觉无影响）。
            control.setAccessibleName(title)
            if isinstance(control, QCheckBox) and not control.text():
                control.setText(title)
            control.setToolTip(title if not hint else "%s\n%s" % (title, hint))
            control.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            h.addWidget(control, 0, Qt.AlignVCenter)
        card_lay.addWidget(row)
        return row

    def _page(self, key):
        if key == "widgets":
            # 组件库自带左栏 + 滚动区，再套一层 QScrollArea 会出现双滚动条
            return self._page_widgets()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent;")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 2, 8, 8)
        lay.setSpacing(12)
        builder = getattr(self, "_page_" + key, None)
        if builder is not None:
            builder(body, lay)
        lay.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _page_widgets(self):
        """组件库分页（v10 A10：本地组件 + 商店 两个页签）。

        v9 时这里只有"本地组件"（原独立窗口融合进来）；v10 A10 起组件可以
        从商店下载，两个列表的语义完全不同（一个管桌面上的，一个管能装
        什么的），所以拆成页签而不是硬塞进一张列表。
        """
        if self.layer is None:
            from PySide6.QtWidgets import QLabel as _QL
            tip = _QL(i18n.T("settings.widgets.unavailable"))
            tip.setObjectName("rowHint")
            return tip
        from . import widget_center
        from . import widget_shop

        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.btn_tab_local = QPushButton(i18n.T("widget_center.tab_local"))
        self.btn_tab_shop = QPushButton(i18n.T("widget_center.tab_shop"))
        group = QButtonGroup(self)
        group.setExclusive(True)
        for btn in (self.btn_tab_local, self.btn_tab_shop):
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setStyleSheet(
                "QPushButton{background: transparent; border: none;"
                " border-radius: 8px; padding: 4px 14px; color: %s;"
                " font-size: 13px;}"
                "QPushButton:checked{background: rgba(255,255,255,0.10);"
                " color: %s;}" % (theme.rgba(theme.TEXT_DIM),
                                   theme.rgba(theme.TEXT)))
            group.addButton(btn)
            tabs.addWidget(btn)
        tabs.addStretch(1)
        lay.addLayout(tabs)

        self.widget_stack = QStackedWidget()
        self.center_page = widget_center.WidgetCenterPage(self.layer, self)
        self.shop_page = widget_shop.WidgetShopPage(self.layer, self)
        self.shop_page.installedChanged.connect(self._on_widget_installed)
        self.widget_stack.addWidget(self.center_page)
        self.widget_stack.addWidget(self.shop_page)
        lay.addWidget(self.widget_stack, 1)

        self.btn_tab_local.setChecked(True)
        self.btn_tab_local.clicked.connect(lambda: self._show_widget_tab(0))
        self.btn_tab_shop.clicked.connect(lambda: self._show_widget_tab(1))
        return holder

    def _show_widget_tab(self, index: int):
        """切页签；商店第一次打开才去拉清单（不给没兴趣的人加一次网络请求）。"""
        self.widget_stack.setCurrentIndex(index)
        if index == 1:
            self.btn_tab_shop.setChecked(True)
            if not self.shop_page.cards():
                self.shop_page.refresh()
        else:
            self.btn_tab_local.setChecked(True)

    def _on_widget_installed(self):
        """商店装/卸了组件：本地列表要立刻反映（新增卡片 / 卸载消失）。"""
        try:
            self.center_page.refresh()
        except Exception:      # noqa: BLE001
            pass

    def select_widget_tab(self, which: str) -> None:
        """按名字切组件库页签（`local` / `shop`）；托盘/外部入口可用。"""
        self.select_page("widgets")
        self._show_widget_tab(1 if str(which).lower() == "shop" else 0)

    def select_page(self, key):
        """按 key 切到某一分页（托盘/桌面右键用它定位到「组件库」）。"""
        for i, (k, _label) in enumerate(self.NAV):
            if k == key:
                self._select_nav(i)
                return True
        return False

    # ---------- 各页 ----------
    def _page_general(self, body, lay):
        # ---- v10 D3（反馈 15-a）：界面语言。放在最上面——用户换了系统/换了
        # 语言想找它，第一眼就该看到。切换后整窗重建（见 _apply_language）。
        card, c = self._card(body, i18n.T("settings.card.language"))
        self.cmb_language = ComboBox()
        for value, label in self._language_items():
            self.cmb_language.addItem(label, value)
        cur_lang = self.storage.get_setting("language") or i18n.AUTO
        self.cmb_language.setCurrentIndex(max(0, self.cmb_language.findData(cur_lang)))
        self.cmb_language.currentIndexChanged.connect(self._apply_language)
        self._row(c, card, i18n.T("settings.language"), self.cmb_language,
                  i18n.T("settings.language_hint"))
        lay.addWidget(card)

        card, c = self._card(body, i18n.T("settings.general.card.startup"))
        self.chk_autostart = Switch()
        self.chk_autostart.setChecked(autostart.is_enabled())
        self.chk_autostart.toggled.connect(self._apply_autostart)
        self._row(c, card, i18n.T("settings.general.autostart.title"),
                  self.chk_autostart, i18n.T("settings.general.autostart.hint"))

        self.chk_tray = Switch()
        self.chk_tray.setChecked(bool(self.storage.get_setting("minimize_to_tray")))
        self.chk_tray.toggled.connect(
            lambda v: self.storage.set_setting("minimize_to_tray", bool(v)))
        self._row(c, card, i18n.T("settings.general.tray.title"),
                  self.chk_tray, i18n.T("settings.general.tray.hint"))
        lay.addWidget(card)

        card, c = self._card(body, i18n.T("settings.general.card.desktop"))
        self.chk_widgets = Switch()
        self.chk_widgets.setChecked(bool(self.storage.get_setting("show_widgets")))
        self.chk_widgets.toggled.connect(self._apply_widgets)
        # v10 A4（反馈 10 规则 3）：原 hint「时钟 / 便签 / 桌面面板」是组件
        # 清单的复读，且随新组件增加必然过时——清单归组件库页，这里不再列。
        self._row(c, card, i18n.T("settings.general.show_widgets.title"),
                  self.chk_widgets)

        # v7 反馈 9：仅小组件模式——只留小组件（组件切无界、图标全隐藏）。
        # 入口唯一：托盘 / 桌面右键里的模式开关已移除。
        self.chk_widgets_only = Switch()
        self.chk_widgets_only.setChecked(
            bool(self.storage.get_setting("widgets_only")))
        self.chk_widgets_only.toggled.connect(self._apply_widgets_only)
        self._row(c, card, i18n.T("settings.general.widgets_only.title"),
                  self.chk_widgets_only,
                  i18n.T("settings.general.widgets_only.hint"))

        # v8 C3：组件库入口（第三处：托盘 / 桌面右键 / 这里）
        btn_center = QPushButton(i18n.T("tray.center"))
        btn_center.setStyleSheet(theme.BUTTON_QSS)
        btn_center.clicked.connect(self._open_widget_center)
        self._row(c, card, i18n.T("settings.general.widget_center.title"),
                  btn_center, i18n.T("settings.general.widget_center.hint"))

        # 2026-10-23 反馈 5（v10 A9）：用户点名删掉这一行——
        # 「恢复组件」与上方「显示桌面小组件」开关语义重叠，关着的组件本来
        # 就能在设置 ▸ 组件库逐个点「显示」，「一键全部恢复」反而多一个
        # 让人犹豫的按钮（用户原话："不要设置里的恢复组件按钮。没用。"）。
        # v10 A9（反馈 2）：Dock 的开关 / 位置 / 外观全部搬到独立的
        # 「Dock 栏」分页（`_page_dock`）——dock 已经有一整套样式旋钮，
        # 塞在通用页的一张卡里既放不下也不好找。
        lay.addWidget(card)

        # v11b：帮助入口。指引在首次启动时自动弹一次，之后只能从这里重看
        # （没有入口的引导 = 用户手滑跳过后永远找不回来）。
        card, c = self._card(body, i18n.T("settings.general.card.help"))
        self.btn_guide = QPushButton(i18n.T("settings.general.guide.button"))
        self.btn_guide.setStyleSheet(theme.BUTTON_QSS)
        self.btn_guide.clicked.connect(self._show_onboarding)
        self._row(c, card, i18n.T("settings.general.guide.title"),
                  self.btn_guide, i18n.T("settings.general.guide.hint"))

        # 快捷键只是"告知"，没有可交互项——用静态文字，别做假按钮
        lbl_keys = QLabel(i18n.T("settings.general.shortcuts.value"))
        lbl_keys.setObjectName("rowHint")
        lbl_keys.setWordWrap(True)
        self._row(c, card, i18n.T("settings.general.shortcuts.title"), lbl_keys)

        # v14 反馈 2：快捷键总开关。低层键盘钩子是**全局**的（每次按键都要
        # 进本进程），有用户反馈"鼠标速度异常"——虽然程序从不改系统鼠标
        # 参数，仍给一个开关：怀疑时先关掉，Ctrl+C/V 失效即排除它。
        self.chk_hotkeys = Switch()
        self.chk_hotkeys.setChecked(bool(self.storage.get_setting("desktop_hotkeys")))
        self.chk_hotkeys.toggled.connect(self._apply_desktop_hotkeys)
        self._row(c, card, i18n.T("settings.general.hotkeys.title"),
                  self.chk_hotkeys, i18n.T("settings.general.hotkeys.hint"))
        lay.addWidget(card)

    def _apply_desktop_hotkeys(self, checked):
        self.storage.set_setting("desktop_hotkeys", bool(checked))
        hook = getattr(self.window, "set_desktop_hotkeys", None)
        if hook is not None:
            hook(bool(checked))

    def _show_onboarding(self):
        """关掉设置窗再放指引：遮罩是全屏置顶的，设置窗留在下面反而穿帮。"""
        fn = getattr(self.window, "start_onboarding", None)
        self.close()
        if fn is not None:
            QTimer.singleShot(120, lambda: fn(replay=True))

    def _flush_storage(self):
        """去抖落盘：设置层 save 失败不该弹窗/炸面板。"""
        try:
            self.storage.save()
        except Exception:      # noqa: BLE001
            pass

    def _apply_dock_liquid(self, on):
        """Dock 的液态玻璃开关（v14 反馈 10）：与背景不透明度互斥。"""
        self.storage.set_setting("liquid_dock", bool(on))
        self._sync_dock_liquid()
        self._apply_dock_style()

    def _sync_dock_liquid(self, dock_on=None):
        """液态开着 -> 不透明度置灰（值保留，关掉后回来还是原值）。"""
        chk = getattr(self, "chk_dock_liquid", None)
        sld = getattr(self, "sld_dock_alpha", None)
        if chk is None or sld is None:
            return
        if dock_on is None:
            box = getattr(self, "chk_dock", None)
            dock_on = bool(box.isChecked()) if box is not None else True
        sld.setEnabled(bool(dock_on) and not bool(chk.isChecked()))

    def _apply_dock_style(self, *_):
        """Dock **外观**改动（透明度 / 圆角 / 边框 / hover / dot）。

        v10 反馈 27：这里只写设置 + 让 Dock 重绘，**绝不调
        `apply_dock_settings()`**——那条路径会 `refresh_band()`（win32
        重挂 + 层序归位）再 `refresh_grid()`（删掉重建全部图标 QWidget）。
        拖滑条时每像素走一次，就是用户看到的"每帧全屏重渲染"以及崩溃。

        落盘走 250ms 去抖（`_dock_save_timer`），松手后写一次。
        """
        ctl = lambda name: getattr(self, name, None)  # noqa: E731
        if ctl("sld_dock_alpha") is not None:
            self.storage.set_setting(
                "dock_alpha", round(int(self.sld_dock_alpha.value()) / 100.0, 4))
        mode = None
        if ctl("cmb_dock_radius") is not None:
            mode = dock.normalize_radius_mode(self.cmb_dock_radius.currentData())
            self.storage.set_setting("dock_radius_mode", mode)
        if ctl("sld_dock_radius") is not None:
            self.storage.set_setting(
                "dock_radius", int(self.sld_dock_radius.value()))
        if ctl("sld_dock_border") is not None:
            self.storage.set_setting(
                "dock_border_width", int(self.sld_dock_border.value()))
        if ctl("chk_dock_hover") is not None:
            self.storage.set_setting("dock_hover",
                                     bool(self.chk_dock_hover.isChecked()))
        if ctl("chk_dock_dot") is not None:
            self.storage.set_setting("dock_dot",
                                     bool(self.chk_dock_dot.isChecked()))
        if ctl("chk_dock_border_auto") is not None:
            self._dock_border_color = (
                "" if self.chk_dock_border_auto.isChecked()
                else str(getattr(self, "_dock_border_color", "") or ""))
            self.storage.set_setting("dock_border_color",
                                     self._dock_border_color)
            self._sync_dock_border_btn()
        hook = getattr(self.window, "apply_dock_style", None)
        if hook is not None:
            hook()
        # 悬停倍数只在「悬停放大」开着时可调（关掉整项放大，旋钮没有意义）
        if ctl("sp_dock_hover") is not None and ctl("chk_dock_hover") is not None:
            self.sp_dock_hover.setEnabled(
                bool(self.chk_dock.isChecked())
                and bool(self.chk_dock_hover.isChecked()))
        # 圆角大小只在「圆角」形态下有效（胶囊/直角是固定几何）
        if ctl("sld_dock_radius") is not None:
            self.sld_dock_radius.setEnabled(
                bool(self.chk_dock.isChecked())
                and (mode or dock.normalize_radius_mode(
                    self.storage.get_setting("dock_radius_mode"))) == "round")
        self._dock_save_timer.start(250)

    def _apply_dock_metrics(self, *_):
        """Dock 尺寸与间距：图标大小见 `_apply_dock`（要重建图标），这里管
        间隔 / 四向内边距 / 悬停倍数 / 位置偏移。

        三步：写设置 -> 立刻让条带按新厚度重排（cheap：只 setGeometry +
        update）-> 网格重建走统一防抖（`refresh_grid` 会销毁重建全部图标，
        逐像素调一次就是"拖炸"）。
        """
        ctl = lambda name: getattr(self, name, None)  # noqa: E731
        if ctl("sp_dock_gap") is not None:
            self.storage.set_setting("dock_gap", int(self.sp_dock_gap.value()))
        # v12：四向内边距
        if getattr(self, "_dock_pad_steppers", None):
            for side, sp in self._dock_pad_steppers.items():
                self.storage.set_setting("dock_pad_" + side, int(sp.value()))
        elif ctl("sp_dock_pad") is not None:
            self.storage.set_setting("dock_pad", int(self.sp_dock_pad.value()))
        if ctl("sp_dock_hover") is not None:
            self.storage.set_setting("dock_hover_pct",
                                     int(self.sp_dock_hover.value()))
        # v12：屏幕位置偏移与居中
        if ctl("sp_dock_offx") is not None:
            self.storage.set_setting("dock_offset_x",
                                     int(self.sp_dock_offx.value()))
        if ctl("sp_dock_offy") is not None:
            self.storage.set_setting("dock_offset_y",
                                     int(self.sp_dock_offy.value()))
        if ctl("chk_dock_center") is not None:
            self.storage.set_setting("dock_center",
                                     bool(self.chk_dock_center.isChecked()))
        hook = getattr(self.window, "apply_dock_metrics", None)
        if callable(hook):
            hook(False)          # 只重排条带，不重建网格
        self._schedule_grid_refresh()
        self._dock_save_timer.start(250)

    def _reset_dock_size(self):
        """重置尺寸与间距到出厂值（v12 反馈 35）。"""
        ctl = lambda name: getattr(self, name, None)  # noqa: E731
        if ctl("sp_dock_icon") is not None:
            self.sp_dock_icon.setValue(dock.DOCK_ICON)
        if ctl("sp_dock_gap") is not None:
            self.sp_dock_gap.setValue(dock.DOCK_GAP)
        if getattr(self, "_dock_pad_steppers", None):
            for sp in self._dock_pad_steppers.values():
                sp.setValue(dock.DOCK_PAD)
        if ctl("sp_dock_hover") is not None:
            self.sp_dock_hover.setValue(dock.DEFAULT_HOVER_PCT)   # 默认 140%
        self._apply_dock()

    def _reset_dock_style(self):
        """重置外观到出厂值。"""
        ctl = lambda name: getattr(self, name, None)  # noqa: E731
        if ctl("chk_dock_liquid") is not None:
            self.chk_dock_liquid.setChecked(True)
        if ctl("sld_dock_alpha") is not None:
            self.sld_dock_alpha.setValue(
                int(round(dock.DEFAULT_ALPHA * 100)))   # DEFAULT_ALPHA
        if ctl("cmb_dock_radius") is not None:
            idx = self.cmb_dock_radius.findData(dock.DEFAULT_RADIUS_MODE)
            if idx >= 0:
                self.cmb_dock_radius.setCurrentIndex(idx)
        if ctl("sld_dock_radius") is not None:
            self.sld_dock_radius.setValue(dock.DEFAULT_RADIUS)
        if ctl("sld_dock_border") is not None:
            self.sld_dock_border.setValue(dock.DEFAULT_BORDER_WIDTH)
        if ctl("chk_dock_border_auto") is not None:
            self.chk_dock_border_auto.setChecked(True)
        if ctl("chk_dock_hover") is not None:
            self.chk_dock_hover.setChecked(True)
        if ctl("chk_dock_dot") is not None:
            self.chk_dock_dot.setChecked(True)
        self._apply_dock()

    def _reset_dock_position(self):
        """重置屏幕位置到出厂值。"""
        ctl = lambda name: getattr(self, name, None)  # noqa: E731
        if ctl("chk_dock_center") is not None:
            self.chk_dock_center.setChecked(True)
        if ctl("sp_dock_offx") is not None:
            self.sp_dock_offx.setValue(0)
        if ctl("sp_dock_offy") is not None:
            self.sp_dock_offy.setValue(0)
        self._apply_dock_metrics()

    def _sync_dock_border_btn(self):
        """颜色按钮：色块显示当前边框色；跟随主题时禁用并写「跟随主题」。"""
        raw = str(getattr(self, "_dock_border_color", "") or "").strip()
        c = QColor(raw) if raw else dock.border_color(self.storage)
        if not c.isValid():
            c = dock.border_color(self.storage)
        fg = "#101216" if c.lightness() > 150 else "#f2f5fa"
        btn = self.btn_dock_border_color
        btn.setText(i18n.T("dock.border.auto") if not raw else c.name().upper())
        btn.setStyleSheet(
            "QPushButton{background-color:%s;color:%s;border:1px solid %s;"
            "border-radius:6px;padding:4px 8px;font-size:12px;}"
            "QPushButton:disabled{color:%s;}"
            % (c.name(), fg, theme.rgba(theme.BORDER),
               theme.rgba(theme.TEXT_FAINT)))
        btn.setEnabled(bool(self.chk_dock.isChecked())
                       and not self.chk_dock_border_auto.isChecked())

    def _on_border_auto_toggled(self, checked):
        """从「跟随主题」切到自定义时，以主题描边色作为起点（不跳到纯黑）。"""
        if not checked and not str(self._dock_border_color or "").strip():
            self._dock_border_color = dock.border_color(self.storage).name()
        self._sync_dock_border_btn()
        self._apply_dock_style()

    def _pick_dock_border_color(self):
        raw = str(getattr(self, "_dock_border_color", "") or "").strip()
        start = QColor(raw) if raw else dock.border_color(self.storage)
        if not start.isValid():
            start = QColor(theme.BORDER)
        c = QColorDialog.getColor(start, self,
                                  i18n.T("settings.dock.border_color.title"),
                                  QColorDialog.DontUseNativeDialog)
        if not c.isValid():
            return
        self._dock_border_color = c.name()
        self._sync_dock_border_btn()
        self._apply_dock_style()

    def _apply_dock(self, *_):
        """Dock 开关 / 位置 / 外观（v10 A9）：写设置 -> 交给窗口重排。

        窗口是唯一写者：`apply_dock_settings()` 内部先让 Dock 落位、再
        `refresh_grid()` 用新 inset 重算行列。窗口不可用（探针 / 替身）时
        只落设置，下次启动生效——绝不在这里自己算一遍几何（双源）。

        控件在 `_page_dock` 里，可能尚未建（构造期 / 老测试只建了通用页）：
        一律用 getattr 兜底，缺控件就不写那一项。
        """
        ctl = lambda name: getattr(self, name, None)  # noqa: E731
        on = bool(self.chk_dock.isChecked())
        self.storage.set_setting("dock_enabled", on)
        self.storage.set_setting(
            "dock_position",
            dock.normalize_position(self.cmb_dock_pos.currentData()))
        if ctl("chk_dock_side_grid") is not None:
            self.storage.set_setting(
                "dock_side_grid", bool(self.chk_dock_side_grid.isChecked()))
        if ctl("sp_dock_icon") is not None:
            self.storage.set_setting(
                "dock_icon", int(self.sp_dock_icon.value()))
        if ctl("sp_dock_gap") is not None:
            self.storage.set_setting(
                "dock_gap", int(self.sp_dock_gap.value()))
        if ctl("sp_dock_pad") is not None:
            self.storage.set_setting(
                "dock_pad", int(self.sp_dock_pad.value()))
        if ctl("sp_dock_hover") is not None:
            self.storage.set_setting(
                "dock_hover_pct", int(self.sp_dock_hover.value()))
        if ctl("sld_dock_alpha") is not None:
            self.storage.set_setting(
                "dock_alpha", round(int(self.sld_dock_alpha.value()) / 100.0, 4))
        mode = None
        if ctl("cmb_dock_radius") is not None:
            mode = dock.normalize_radius_mode(self.cmb_dock_radius.currentData())
            self.storage.set_setting("dock_radius_mode", mode)
        if ctl("sld_dock_radius") is not None:
            self.storage.set_setting(
                "dock_radius", int(self.sld_dock_radius.value()))
        if ctl("chk_dock_hover") is not None:
            self.storage.set_setting("dock_hover",
                                     bool(self.chk_dock_hover.isChecked()))
        if ctl("chk_dock_dot") is not None:
            self.storage.set_setting("dock_dot",
                                     bool(self.chk_dock_dot.isChecked()))
        if ctl("sld_dock_border") is not None:
            self.storage.set_setting(
                "dock_border_width", int(self.sld_dock_border.value()))
        if ctl("chk_dock_border_auto") is not None:
            self._dock_border_color = (
                "" if self.chk_dock_border_auto.isChecked()
                else str(getattr(self, "_dock_border_color", "") or ""))
            self.storage.set_setting("dock_border_color",
                                     self._dock_border_color)
        if ctl("chk_dock_liquid") is not None:
            self.storage.set_setting("liquid_dock",
                                     bool(self.chk_dock_liquid.isChecked()))
        self.storage.save()
        # 关掉 Dock 时，子控件一并禁用（不留"能点但无效"的旋钮）
        for name in ("cmb_dock_pos", "sp_dock_icon", "sp_dock_gap",
                     "sld_dock_alpha",
                     "cmb_dock_radius", "chk_dock_hover", "chk_dock_dot",
                     "sld_dock_border", "chk_dock_border_auto",
                     "chk_dock_center", "sp_dock_offx", "sp_dock_offy",
                     "chk_dock_side_grid"):
            w = ctl(name)
            if w is not None:
                w.setEnabled(on)
        # v12：四向内边距也一并禁用
        if getattr(self, "_dock_pad_steppers", None):
            for sp in self._dock_pad_steppers.values():
                sp.setEnabled(on)
        if ctl("sp_dock_hover") is not None:
            self.sp_dock_hover.setEnabled(
                on and bool(self.chk_dock_hover.isChecked()))
        if ctl("btn_dock_border_color") is not None:
            self._sync_dock_border_btn()
        # 圆角大小只在「圆角」形态下有效（胶囊/直角是固定几何）
        if ctl("sld_dock_radius") is not None:
            self.sld_dock_radius.setEnabled(
                on and (mode or dock.normalize_radius_mode(
                    self.storage.get_setting("dock_radius_mode"))) == "round")
        # 液态玻璃与不透明度互斥（v14 反馈 10）
        self._sync_dock_liquid(on)
        hook = getattr(self.window, "apply_dock_settings", None)
        if hook is not None:
            hook()

    def _open_widget_center(self):
        """跳到组件库分页（v9 V2：不再新开窗口）。

        旧实现 `widget_center.open_center(..., parent=self)` 会新建一个
        **被拥有的模态窗口**——点不动且被压在设置面板下面（BUG1）。
        """
        self.select_page("widgets")

    # ---------- v10 D3：界面语言 ----------
    @staticmethod
    def _language_items():
        """下拉项：自动 + 两种语言。

        语言名用**它自己那门语言**写（中文 / English）——这是界面语言选择的
        通行做法：英文界面里把「中文」翻成 "Chinese"，认不出中文的用户反而
        找不到它。
        """
        return ((i18n.AUTO, i18n.T("settings.language.system")),
                (i18n.ZH, "中文"),
                (i18n.EN, "English"))

    def _apply_language(self, _index=0):
        """切换界面语言：落盘 -> 生效 -> 整窗重建。

        为什么整窗重建：所有文案都是建窗时写进控件的（`QLabel(...)`），
        逐控件回写要维护一张"控件 -> key"的映射表，漏一个就永远显示旧语言。
        重建代价是几十毫秒，且用户此时正停在设置页，看不出闪动。
        """
        value = self.cmb_language.currentData()
        if not value or value == (self.storage.get_setting("language") or i18n.AUTO):
            return
        self.storage.set_setting("language", value)
        self.storage.save()
        i18n.set_language(self.storage)
        self._restart_ui()

    def _restart_ui(self):
        """按当前语言重建整个设置窗（托盘菜单在弹出时自建，无需通知）。"""
        global _dialog
        page = self.NAV[self.stack.currentIndex()][0]
        win, storage, layer = self.window, self.storage, self.layer
        if _dialog is self:
            _dialog = None
        self.hide()
        self.deleteLater()
        QTimer.singleShot(
            0, lambda: open_settings(win, storage, layer, page=page))

    def _page_dock(self, body, lay):
        """Dock 栏（v10 A9）：开关 + 位置 + 外观 + 清空。"""
        on = bool(self.storage.get_setting("dock_enabled"))
        card, c = self._card(body, i18n.T("settings.dock.card_basic"))

        self.chk_dock = Switch()
        self.chk_dock.setChecked(on)
        self.chk_dock.toggled.connect(self._apply_dock)
        self._row(c, card, i18n.T("settings.general.dock.title"), self.chk_dock,
                  i18n.T("settings.general.dock.hint"))

        self.cmb_dock_pos = ComboBox()
        # POSITION_LABELS 的元素顺序是 (值, 中文兜底)：按元组顺序解包，
        # 写反了就会把 "bottom" 当标签、把 "底部" 当值（下拉显示英文、
        # 切位置写进 storage 的是"顶部"这种中文，normalize 后全回 bottom）。
        for pos, label in dock.POSITION_LABELS:
            self.cmb_dock_pos.addItem(i18n.TL("dock.pos." + pos, label), pos)
        self.cmb_dock_pos.setCurrentIndex(max(0, self.cmb_dock_pos.findData(
            dock.normalize_position(self.storage.get_setting("dock_position")))))
        self.cmb_dock_pos.setEnabled(on)
        self.cmb_dock_pos.currentIndexChanged.connect(self._apply_dock)
        self._row(c, card, i18n.T("settings.general.dock_position.title"),
                  self.cmb_dock_pos,
                  i18n.T("settings.general.dock_position.hint"))

        # Dock 两侧布置网格（新）：开启后不再整边扣除网格，Dock 实际
        # 占住的格子禁放，条带较短时两侧空区照常摆图标
        self.chk_dock_side_grid = Switch()
        self.chk_dock_side_grid.setChecked(
            bool(self.storage.get_setting("dock_side_grid")))
        self.chk_dock_side_grid.setEnabled(on)
        self.chk_dock_side_grid.toggled.connect(self._apply_dock)
        self._row(c, card, i18n.T("settings.general.dock_side_grid.title"),
                  self.chk_dock_side_grid,
                  i18n.T("settings.general.dock_side_grid.hint"))
        lay.addWidget(card)

        # ---- v11（反馈 29）：尺寸与间距。用户的诉求是"dock 栏的大小、
        # 图标间隔、大小比例都能自己调"——这四项各管一件事，不能合并：
        # 图标大小 = 图标本体；间隔 = 图标之间；内边距 = 条带厚度；
        # 悬停倍数 = 指到图标时放多大。
        card, c = self._card(body, i18n.T("settings.dock.card_size"),
                              reset_fn=self._reset_dock_size)

        self.sp_dock_icon = Stepper()
        self.sp_dock_icon.setRange(dock.DOCK_ICON_MIN, dock.DOCK_ICON_MAX)
        self.sp_dock_icon.setSingleStep(2)
        self.sp_dock_icon.setSuffix(i18n.T("settings.dock.unit_px"))
        self.sp_dock_icon.setFixedWidth(96)
        self.sp_dock_icon.setValue(dock.icon_base(self.storage))
        self.sp_dock_icon.setEnabled(on)
        self.sp_dock_icon.valueChanged.connect(self._apply_dock)
        self._row(c, card, i18n.T("settings.dock.icon.title"),
                  self.sp_dock_icon, i18n.T("settings.dock.icon.hint"))

        self.sp_dock_gap = Stepper()
        self.sp_dock_gap.setRange(dock.DOCK_GAP_MIN, dock.DOCK_GAP_MAX)
        self.sp_dock_gap.setSingleStep(1)
        self.sp_dock_gap.setSuffix(i18n.T("settings.dock.unit_px"))
        self.sp_dock_gap.setFixedWidth(96)
        self.sp_dock_gap.setValue(dock.gap_px(self.storage))
        self.sp_dock_gap.setEnabled(on)
        self.sp_dock_gap.valueChanged.connect(self._apply_dock_metrics)
        self._row(c, card, i18n.T("settings.dock.gap.title"),
                  self.sp_dock_gap, i18n.T("settings.dock.gap.hint"))

        # v12 反馈 35：四向内边距（替代旧的单一 dock_pad）
        self._dock_pad_steppers = {}
        for side in ("top", "bottom", "left", "right"):
            sp = Stepper()
            sp.setRange(dock.DOCK_PAD_MIN, dock.DOCK_PAD_MAX)
            sp.setSingleStep(1)
            sp.setSuffix(i18n.T("settings.dock.unit_px"))
            sp.setFixedWidth(96)
            sp.setValue(dock.pad_side(self.storage, side))
            sp.setEnabled(on)
            sp.valueChanged.connect(self._apply_dock_metrics)
            self._dock_pad_steppers[side] = sp
            setattr(self, "sp_dock_pad_" + side, sp)
            self._row(c, card,
                      i18n.T("settings.dock.pad_%s.title" % side),
                      sp,
                      i18n.T("settings.dock.pad_%s.hint" % side))

        self.sp_dock_hover = Stepper()
        self.sp_dock_hover.setRange(dock.DOCK_HOVER_MIN, dock.DOCK_HOVER_MAX)
        self.sp_dock_hover.setSingleStep(2)
        self.sp_dock_hover.setSuffix("%")
        self.sp_dock_hover.setFixedWidth(96)
        self.sp_dock_hover.setValue(dock.hover_pct(self.storage))
        self.sp_dock_hover.setEnabled(on and dock.hover_enabled(self.storage))
        self.sp_dock_hover.valueChanged.connect(self._apply_dock_metrics)
        self._row(c, card, i18n.T("settings.dock.hover_scale.title"),
                  self.sp_dock_hover,
                  i18n.T("settings.dock.hover_scale.hint"))
        lay.addWidget(card)

        # v12 反馈 35：屏幕位置（偏移 + 居中）
        card_pos, cp = self._card(body, i18n.T("settings.dock.card_position"),
                                   reset_fn=self._reset_dock_position)

        self.chk_dock_center = Switch()
        self.chk_dock_center.setChecked(dock.center_along_edge(self.storage))
        self.chk_dock_center.setEnabled(on)
        self.chk_dock_center.toggled.connect(self._apply_dock_metrics)
        self._row(cp, card_pos, i18n.T("settings.dock.pos_center.title"),
                  self.chk_dock_center,
                  i18n.T("settings.dock.pos_center.hint"))

        self.sp_dock_offx = Stepper()
        self.sp_dock_offx.setRange(dock.OFFSET_MIN, dock.OFFSET_MAX)
        self.sp_dock_offx.setSingleStep(4)
        self.sp_dock_offx.setSuffix(i18n.T("settings.dock.unit_px"))
        self.sp_dock_offx.setFixedWidth(96)
        self.sp_dock_offx.setValue(dock.offset_x(self.storage))
        self.sp_dock_offx.setEnabled(on)
        self.sp_dock_offx.valueChanged.connect(self._apply_dock_metrics)
        self._row(cp, card_pos, i18n.T("settings.dock.offset_x.title"),
                  self.sp_dock_offx,
                  i18n.T("settings.dock.offset_x.hint"))

        self.sp_dock_offy = Stepper()
        self.sp_dock_offy.setRange(dock.OFFSET_MIN, dock.OFFSET_MAX)
        self.sp_dock_offy.setSingleStep(4)
        self.sp_dock_offy.setSuffix(i18n.T("settings.dock.unit_px"))
        self.sp_dock_offy.setFixedWidth(96)
        self.sp_dock_offy.setValue(dock.offset_y(self.storage))
        self.sp_dock_offy.setEnabled(on)
        self.sp_dock_offy.valueChanged.connect(self._apply_dock_metrics)
        self._row(cp, card_pos, i18n.T("settings.dock.offset_y.title"),
                  self.sp_dock_offy,
                  i18n.T("settings.dock.offset_y.hint"))
        lay.addWidget(card_pos)

        card2, c2 = self._card(body, i18n.T("settings.dock.card_style"),
                                reset_fn=self._reset_dock_style)

        # v14 反馈 10：液态玻璃 = 一种配色（和"不透明度"互斥）。
        # 开着时条带背景就是折射出来的壁纸，不透明度没有意义 → 置灰。
        self.chk_dock_liquid = Switch()
        self.chk_dock_liquid.setChecked(
            liquid_glass.surface_enabled(self.storage, "dock"))
        self.chk_dock_liquid.setEnabled(on)
        self.chk_dock_liquid.toggled.connect(self._apply_dock_liquid)
        self._row(c2, card2, i18n.T("settings.dock.liquid.title"),
                  self.chk_dock_liquid, i18n.T("settings.dock.liquid.hint"))

        self.sld_dock_alpha = QSlider(Qt.Horizontal)
        self.sld_dock_alpha.setRange(30, 100)
        self.sld_dock_alpha.setValue(int(round(dock.bg_alpha(self.storage) * 100)))
        self.sld_dock_alpha.setFixedWidth(150)
        self.sld_dock_alpha.setStyleSheet(theme.SLIDER_QSS)
        self.sld_dock_alpha.setEnabled(on)
        self.sld_dock_alpha.valueChanged.connect(self._apply_dock_style)
        self.sld_dock_alpha.sliderReleased.connect(self._flush_storage)
        self._row(c2, card2, i18n.T("settings.dock.alpha.title"),
                  self.sld_dock_alpha, i18n.T("settings.dock.alpha.hint"))

        self.cmb_dock_radius = ComboBox()
        for mode, label in dock.RADIUS_LABELS:
            self.cmb_dock_radius.addItem(
                i18n.TL("dock.radius." + mode, label), mode)
        self.cmb_dock_radius.setCurrentIndex(max(0, self.cmb_dock_radius.findData(
            dock.normalize_radius_mode(
                self.storage.get_setting("dock_radius_mode")))))
        self.cmb_dock_radius.setEnabled(on)
        self.cmb_dock_radius.currentIndexChanged.connect(self._apply_dock)
        self._row(c2, card2, i18n.T("settings.dock.radius.title"),
                  self.cmb_dock_radius, i18n.T("settings.dock.radius.hint"))

        # v10 A11：形态选「圆角」时半径可调（0~26）。胶囊/直角形态下这个
        # 滑块没有意义——禁用而不是隐藏，避免行高跳动让面板看起来"抖"。
        self.sld_dock_radius = QSlider(Qt.Horizontal)
        self.sld_dock_radius.setRange(dock.RADIUS_MIN, dock.RADIUS_MAX)
        self.sld_dock_radius.setValue(dock.radius_px(self.storage))
        self.sld_dock_radius.setFixedWidth(150)
        self.sld_dock_radius.setStyleSheet(theme.SLIDER_QSS)
        self.sld_dock_radius.setEnabled(
            on and dock.normalize_radius_mode(
                self.storage.get_setting("dock_radius_mode")) == "round")
        self.sld_dock_radius.valueChanged.connect(self._apply_dock_style)
        self.sld_dock_radius.sliderReleased.connect(self._flush_storage)
        self._row(c2, card2, i18n.T("settings.dock.radius_px.title"),
                  self.sld_dock_radius,
                  i18n.T("settings.dock.radius_px.hint"))

        # v10 反馈 27：边框粗细（0 = 不画）+ 颜色（可跟随主题）
        self.sld_dock_border = QSlider(Qt.Horizontal)
        self.sld_dock_border.setRange(dock.BORDER_MIN, dock.BORDER_MAX)
        self.sld_dock_border.setValue(dock.border_width(self.storage))
        self.sld_dock_border.setFixedWidth(150)
        self.sld_dock_border.setStyleSheet(theme.SLIDER_QSS)
        self.sld_dock_border.setEnabled(on)
        self.sld_dock_border.valueChanged.connect(self._apply_dock_style)
        self.sld_dock_border.sliderReleased.connect(self._flush_storage)
        self._row(c2, card2, i18n.T("settings.dock.border_w.title"),
                  self.sld_dock_border, i18n.T("settings.dock.border_w.hint"))

        self._dock_border_color = str(
            self.storage.get_setting("dock_border_color") or "")
        self.btn_dock_border_color = QPushButton()
        self.btn_dock_border_color.setFixedWidth(96)
        self.btn_dock_border_color.setCursor(Qt.PointingHandCursor)
        self.btn_dock_border_color.clicked.connect(self._pick_dock_border_color)

        self.chk_dock_border_auto = QCheckBox(
            i18n.T("dock.border.auto"))
        self.chk_dock_border_auto.setChecked(not self._dock_border_color.strip())
        self.chk_dock_border_auto.setEnabled(on)
        self.chk_dock_border_auto.toggled.connect(self._on_border_auto_toggled)

        wrap = QWidget()
        hwrap = QHBoxLayout(wrap)
        hwrap.setContentsMargins(0, 0, 0, 0)
        hwrap.setSpacing(8)
        hwrap.addWidget(self.btn_dock_border_color)
        hwrap.addWidget(self.chk_dock_border_auto)
        self._sync_dock_border_btn()
        self._row(c2, card2, i18n.T("settings.dock.border_color.title"), wrap,
                  i18n.T("settings.dock.border_color.hint"))

        self.chk_dock_hover = Switch()
        self.chk_dock_hover.setChecked(dock.hover_enabled(self.storage))
        self.chk_dock_hover.setEnabled(on)
        self.chk_dock_hover.toggled.connect(self._apply_dock_style)
        self._row(c2, card2, i18n.T("settings.dock.hover.title"),
                  self.chk_dock_hover, i18n.T("settings.dock.hover.hint"))

        self.chk_dock_dot = Switch()
        self.chk_dock_dot.setChecked(dock.dot_enabled(self.storage))
        self.chk_dock_dot.setEnabled(on)
        self.chk_dock_dot.toggled.connect(self._apply_dock_style)
        self._row(c2, card2, i18n.T("settings.dock.dot.title"),
                  self.chk_dock_dot, i18n.T("settings.dock.dot.hint"))
        lay.addWidget(card2)
        # 液态玻璃开着 -> 不透明度不可调（控件建完才能同步）
        self._sync_dock_liquid(on)

        card3, c3 = self._card(body, i18n.T("settings.dock.card_items"))
        self.lbl_dock_count = QLabel("")
        self.lbl_dock_count.setObjectName("rowHint")
        self._sync_dock_count()
        c3.addWidget(self.lbl_dock_count)
        btn_clear = QPushButton(i18n.T("settings.dock.clear"))
        btn_clear.setStyleSheet(theme.BUTTON_QSS)
        btn_clear.clicked.connect(self._clear_dock)
        self._row(c3, card3, i18n.T("settings.dock.clear.title"), btn_clear,
                  i18n.T("settings.dock.clear.hint"))
        lay.addWidget(card3)

    def _sync_dock_count(self):
        """刷新「当前 N 个应用」提示（清空后要立刻变）。"""
        try:
            n = len(dock.load_items(self.storage))
        except Exception:      # noqa: BLE001
            n = 0
        self.lbl_dock_count.setText(
            i18n.T("settings.dock.count", n=n))

    def _clear_dock(self):
        """清空 Dock（只删 dock_items，桌面布局一条不动）。"""
        if not dock.load_items(self.storage):
            return
        ok = QMessageBox.question(
            self, i18n.T("settings.dock.clear.title"),
            i18n.T("settings.dock.clear.confirm"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        dock.save_items(self.storage, [])
        self.storage.save()
        self._sync_dock_count()
        hook = getattr(self.window, "apply_dock_settings", None)
        if hook is not None:
            hook()

    def _page_liquid(self, body, lay):
        """液态玻璃页（v14h）：原「外观与图标」页的液态玻璃卡独立成页。

        参数已有 9 个（总闸 + 折射/光学 6 项 + 渲染频率 2 项），值得整页；
        图标尺寸两张卡挪去「图标与标签」，悬浮不透明度挪去「模式」。
        """
        # ---------- v14 反馈 9：液态玻璃 ----------
        # 一张卡管全部 liquid_* 键（`liquid_glass.py` 是唯一读取入口）：
        # 总开关 + 三个表面开关 + 三个强度滑条 + 扫光开关。强度为 0 的
        # 旋钮不禁用（0 = 显式关掉这一层光学，是合法值），只有总开关
        # 关闭时才把下属控件置灰。
        card_lq, cq = self._card(body, i18n.T("settings.appearance.card_liquid"),
                                 reset_fn=self._reset_liquid)

        self.chk_liquid = Switch()
        self.chk_liquid.setChecked(liquid_glass.master_enabled(self.storage))
        self.chk_liquid.toggled.connect(self._apply_liquid_master)
        self._row(cq, card_lq, i18n.T("settings.appearance.liquid.master.title"),
                  self.chk_liquid, i18n.T("settings.appearance.liquid.master.hint"))

        # v14 反馈 10：液态玻璃是**每个元素自己的配色**——
        # 文件夹在色板里选、组件在组件设置里开关、Dock 在 Dock 分页。
        # 这里只留总闸 + 两个光学参数（强度 / 带宽）。
        self.sld_liquid_strength = self._liquid_slider(
            "liquid_strength", self._apply_liquid_strength)
        self.lbl_liquid_strength = self._liquid_value_lbl(
            self.sld_liquid_strength)
        stg_box = self._liquid_slider_box(self.sld_liquid_strength,
                                          self.lbl_liquid_strength)
        self._row(cq, card_lq,
                  i18n.T("settings.appearance.liquid.strength.title"), stg_box,
                  i18n.T("settings.appearance.liquid.strength.hint"))

        # 折射带宽是 DIP 值（6~40），和强度（0~100%）分开一个量纲
        self.sld_liquid_band = self._liquid_slider(
            "liquid_band", self._apply_liquid_band,
            lo=liquid_glass.BAND_MIN, hi=liquid_glass.BAND_MAX,
            suffix=i18n.T("settings.appearance.liquid.unit_px"))
        self.lbl_liquid_band = self._liquid_value_lbl(
            self.sld_liquid_band, suffix=i18n.T("settings.appearance.liquid.unit_px"))
        bnd_box = self._liquid_slider_box(self.sld_liquid_band,
                                          self.lbl_liquid_band)
        self._row(cq, card_lq,
                  i18n.T("settings.appearance.liquid.band.title"), bnd_box,
                  i18n.T("settings.appearance.liquid.band.hint"))

        # v14e：liquid-glass-react 的四个可调光学层。饱和度默认 140%（lgr
        # 同款，是"像 Apple"的主要来源）；色散/高光默认 0——v14c 目检结论
        # 是"不要发光描边"，它们只作为可选项存在。
        self.sld_liquid_saturation = self._liquid_slider(
            "liquid_saturation", self._apply_liquid_saturation,
            lo=liquid_glass.SATURATION_MIN, hi=liquid_glass.SATURATION_MAX)
        self.lbl_liquid_saturation = self._liquid_value_lbl(
            self.sld_liquid_saturation)
        sat_box = self._liquid_slider_box(self.sld_liquid_saturation,
                                          self.lbl_liquid_saturation)
        self._row(cq, card_lq,
                  i18n.T("settings.appearance.liquid.saturation.title"),
                  sat_box,
                  i18n.T("settings.appearance.liquid.saturation.hint"))

        self.sld_liquid_frost = self._liquid_slider(
            "liquid_frost", self._apply_liquid_frost)
        self.lbl_liquid_frost = self._liquid_value_lbl(self.sld_liquid_frost)
        frz_box = self._liquid_slider_box(self.sld_liquid_frost,
                                          self.lbl_liquid_frost)
        self._row(cq, card_lq,
                  i18n.T("settings.appearance.liquid.frost.title"), frz_box,
                  i18n.T("settings.appearance.liquid.frost.hint"))

        self.sld_liquid_aberration = self._liquid_slider(
            "liquid_aberration", self._apply_liquid_aberration)
        self.lbl_liquid_aberration = self._liquid_value_lbl(
            self.sld_liquid_aberration)
        ab_box = self._liquid_slider_box(self.sld_liquid_aberration,
                                         self.lbl_liquid_aberration)
        self._row(cq, card_lq,
                  i18n.T("settings.appearance.liquid.aberration.title"),
                  ab_box,
                  i18n.T("settings.appearance.liquid.aberration.hint"))

        self.sld_liquid_rimlight = self._liquid_slider(
            "liquid_rimlight", self._apply_liquid_rimlight)
        self.lbl_liquid_rimlight = self._liquid_value_lbl(
            self.sld_liquid_rimlight)
        rim_box = self._liquid_slider_box(self.sld_liquid_rimlight,
                                          self.lbl_liquid_rimlight)
        self._row(cq, card_lq,
                  i18n.T("settings.appearance.liquid.rimlight.title"),
                  rim_box,
                  i18n.T("settings.appearance.liquid.rimlight.hint"))

        # v14g：渲染频率档位（性能调节）。低 30 / 中 45 / 高 60 / 自定义。
        # 自定义值建议 = 显示器刷新率（hint 里动态显示当前 Hz）——再高只是
        # 白白重算，还会把拖拽本身拖卡。
        self.cmb_liquid_fps = ComboBox()
        for mode, key in (("low", "settings.appearance.liquid.fps.low"),
                          ("medium", "settings.appearance.liquid.fps.medium"),
                          ("high", "settings.appearance.liquid.fps.high"),
                          ("custom", "settings.appearance.liquid.fps.custom")):
            self.cmb_liquid_fps.addItem(i18n.T(key), mode)
        self.cmb_liquid_fps.setCurrentIndex(max(0, self.cmb_liquid_fps.findData(
            liquid_glass.normalize_fps_mode(
                self.storage.get_setting("liquid_fps_mode")))))
        self.cmb_liquid_fps.currentIndexChanged.connect(
            self._apply_liquid_fps_mode)
        self._row(cq, card_lq,
                  i18n.T("settings.appearance.liquid.fps.title"),
                  self.cmb_liquid_fps,
                  i18n.T("settings.appearance.liquid.fps.hint"))

        # 自定义档位才可调的帧率步进器（其余档位禁用不隐藏，行高不跳）
        hz = 0.0
        screen = QGuiApplication.primaryScreen()
        try:
            hz = float(screen.refreshRate()) if screen else 0.0
        except (AttributeError, RuntimeError, TypeError):
            hz = 0.0
        self.sp_liquid_fps = Stepper()
        self.sp_liquid_fps.setRange(liquid_glass.FPS_CUSTOM_MIN,
                                    liquid_glass.FPS_CUSTOM_MAX)
        self.sp_liquid_fps.setSuffix(" fps")
        self.sp_liquid_fps.setValue(int(self.storage.get_setting(
            "liquid_fps_custom") or liquid_glass.DEFAULT_FPS_CUSTOM))
        self.sp_liquid_fps.setEnabled(
            self.cmb_liquid_fps.currentData() == "custom")
        self.sp_liquid_fps.valueChanged.connect(self._apply_liquid_fps_custom)
        hz_txt = ("%d" % round(hz)) if hz >= 1.0 else "?"
        self._row(cq, card_lq,
                  i18n.T("settings.appearance.liquid.fps_custom.title"),
                  self.sp_liquid_fps,
                  i18n.T("settings.appearance.liquid.fps_custom.hint",
                         hz=hz_txt))
        lay.addWidget(card_lq)
        self._sync_liquid_controls()

    # ---------- 图标分页（v14 反馈 3 / 反馈 13） ----------
    def _page_icons(self, body, lay):
        """图标与标签（v14h 重梳理由：图标的事都在一页）。

        尺寸/占比两张卡从原「外观与图标」页搬来（原页面已改造成液态
        玻璃专页）；标签文字 + 移动预览框不变。`label_style.py` 仍是
        标签样式的唯一读取入口，实时生效。
        """
        from . import label_style as _ls

        # ---- 卡片：图标尺寸与占比（v14h 自外观页迁入） ----
        card, c = self._card(body, i18n.T("settings.appearance.card.icons"))
        self.cmb_tier = ComboBox()
        # v10 A4（反馈 10 规则 2）：「中（= 原生）」是行为解释进控件标签，
        # 去掉；原生等价关系由右侧「图标 Npx」读数自证。
        for label, tier in (("小", "small"), ("中", "medium"),
                            ("大", "large"), ("自定义", "custom")):
            self.cmb_tier.addItem(
                i18n.TL("settings.appearance.tier." + tier, label), tier)
        cur_tier = self.storage.icon_tier()
        self.cmb_tier.setCurrentIndex(max(0, self.cmb_tier.findData(cur_tier)))
        self.cmb_tier.currentIndexChanged.connect(self._apply_icon_tier)
        self.lbl_icon_px = QLabel()
        self.lbl_icon_px.setObjectName("rowHint")
        tier_box = QWidget()
        tb = QHBoxLayout(tier_box)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(10)
        tb.addWidget(self.lbl_icon_px)
        tb.addWidget(self.cmb_tier)
        self._row(c, card, i18n.T("settings.appearance.icon_size.title"),
                  tier_box, i18n.T("settings.appearance.icon_size.hint"))

        # v7 B2（反馈 9）：第四档「自定义」才启用滑条。三档是预设，
        # 滑条跟着值显示但不可拖——旧实现滑条常驻可拖，用户拖完档位
        # 显示值又跳回最近预设，看着像"逻辑有问题"。
        self.sld_icon_fit = QSlider(Qt.Horizontal)
        self.sld_icon_fit.setRange(30, 90)
        self.sld_icon_fit.setFixedWidth(190)
        self.lbl_icon_fit = QLabel()
        self.lbl_icon_fit.setObjectName("rowHint")
        self.lbl_icon_fit.setFixedWidth(46)
        self.sld_icon_fit.valueChanged.connect(self._apply_icon_fit)
        fit_box = QWidget()
        fb = QHBoxLayout(fit_box)
        fb.setContentsMargins(0, 0, 0, 0)
        fb.setSpacing(8)
        fb.addWidget(self.sld_icon_fit)
        fb.addWidget(self.lbl_icon_fit)
        # 括号保留：30%~90% 是取值域（规则 1 唯一放行的一种括号）。
        self._row(c, card, i18n.T("settings.appearance.icon_fit.title"),
                  fit_box, i18n.T("settings.appearance.icon_fit.hint"))
        lay.addWidget(card)
        self._refresh_icon_labels()

        # ---- 卡片：已配置额外大小的图标（v14h 自外观页迁入） ----
        # 存在意义是"反悔入口"——把图标缩到 30% 会小到点不到，右键菜单
        # 都调不出来；这里可以一键恢复 100%。
        card, c = self._card(body, i18n.T("settings.appearance.card.scaled"))
        self._scaled_box = QVBoxLayout()
        self._scaled_box.setContentsMargins(0, 0, 0, 0)
        self._scaled_box.setSpacing(4)
        holder = QWidget(card)
        holder.setLayout(self._scaled_box)
        c.addWidget(holder)
        self._scaled_rows = []
        lay.addWidget(card)
        self._refresh_scaled_icons()

        # ---- 卡片：标签文字 ----
        card, c = self._card(body, i18n.T("settings.icons.card.text"),
                             reset_fn=self._reset_label_text)

        self._label_color = str(
            self.storage.get_setting("label_color") or "#f4f5f7")
        self.btn_label_color = QPushButton()
        self.btn_label_color.setFixedWidth(120)
        self.btn_label_color.setCursor(Qt.PointingHandCursor)
        self.btn_label_color.clicked.connect(self._pick_label_color)
        self._sync_label_color_btn()
        self._row(c, card, i18n.T("settings.icons.color.title"),
                  self.btn_label_color, i18n.T("settings.icons.color.hint"))

        self.chk_label_bold = Switch()
        self.chk_label_bold.setChecked(
            bool(self.storage.get_setting("label_bold")))
        self.chk_label_bold.toggled.connect(self._apply_label_bold)
        self._row(c, card, i18n.T("settings.icons.bold.title"),
                  self.chk_label_bold, i18n.T("settings.icons.bold.hint"))

        self.chk_label_shadow = Switch()
        self.chk_label_shadow.setChecked(
            bool(self.storage.get_setting("label_shadow")))
        self.chk_label_shadow.toggled.connect(self._apply_label_shadow)
        self._row(c, card, i18n.T("settings.icons.shadow.title"),
                  self.chk_label_shadow, i18n.T("settings.icons.shadow.hint"))

        self.sld_label_shadow = QSlider(Qt.Horizontal)
        self.sld_label_shadow.setRange(0, 100)
        self.sld_label_shadow.setFixedWidth(150)
        self.sld_label_shadow.setValue(
            int(self.storage.get_setting("label_shadow_alpha") or 75))
        self.lbl_label_shadow = QLabel("%d%%" % self.sld_label_shadow.value())
        self.lbl_label_shadow.setObjectName("rowHint")
        self.lbl_label_shadow.setFixedWidth(40)
        self.sld_label_shadow.valueChanged.connect(
            self._apply_label_shadow_alpha)
        sh_box = QWidget()
        sb = QHBoxLayout(sh_box)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(8)
        sb.addWidget(self.sld_label_shadow)
        sb.addWidget(self.lbl_label_shadow)
        self._row(c, card, i18n.T("settings.icons.shadow_alpha.title"),
                  sh_box, i18n.T("settings.icons.shadow_alpha.hint"))

        self.chk_label_outline = Switch()
        self.chk_label_outline.setChecked(
            bool(self.storage.get_setting("label_outline")))
        self.chk_label_outline.toggled.connect(self._apply_label_outline)
        self._row(c, card, i18n.T("settings.icons.outline.title"),
                  self.chk_label_outline, i18n.T("settings.icons.outline.hint"))

        self._label_outline_color = str(
            self.storage.get_setting("label_outline_color") or "#000000")
        self.btn_label_outline_color = QPushButton()
        self.btn_label_outline_color.setFixedWidth(120)
        self.btn_label_outline_color.setCursor(Qt.PointingHandCursor)
        self.btn_label_outline_color.clicked.connect(
            self._pick_label_outline_color)
        self._sync_outline_color_btn()
        self._row(c, card, i18n.T("settings.icons.outline_color.title"),
                  self.btn_label_outline_color,
                  i18n.T("settings.icons.outline_color.hint"))

        self.sp_label_outline_w = Stepper()
        self.sp_label_outline_w.setRange(
            _ls.LABEL_OUTLINE_MIN, _ls.LABEL_OUTLINE_MAX)
        self.sp_label_outline_w.setFixedWidth(96)
        self.sp_label_outline_w.setValue(
            int(self.storage.get_setting("label_outline_width") or 1))
        self.sp_label_outline_w.valueChanged.connect(
            self._apply_label_outline_w)
        self._row(c, card, i18n.T("settings.icons.outline_w.title"),
                  self.sp_label_outline_w,
                  i18n.T("settings.icons.outline_w.hint"))
        lay.addWidget(card)

        # ---- 卡片二：移动预览框（反馈 13） ----
        card2, c2 = self._card(body, i18n.T("settings.icons.card.preview"),
                               reset_fn=self._reset_cell_preview)
        # 整体开关：不需要落点指示框的用户可以完全关掉（新反馈）
        self.chk_cell_preview_enabled = Switch()
        _pv_on = self.storage.get_setting("cell_preview_enabled")
        self.chk_cell_preview_enabled.setChecked(
            _pv_on is None or bool(_pv_on))
        self.chk_cell_preview_enabled.toggled.connect(
            self._apply_cell_preview_enabled)
        self._row(c2, card2, i18n.T("settings.icons.preview.enabled.title"),
                  self.chk_cell_preview_enabled,
                  i18n.T("settings.icons.preview.enabled.hint"))
        self.cmb_cell_preview = ComboBox()
        for key, label, _c in _ls.PREVIEW_PRESETS:
            self.cmb_cell_preview.addItem(
                i18n.TL("settings.icons.preview.preset." + key, label), key)
        self.cmb_cell_preview.addItem(
            i18n.T("settings.icons.preview.preset.custom"), "custom")
        cur_color = str(self.storage.get_setting("cell_preview_color") or "")
        cur_key = ""
        for key, _l, col in _ls.PREVIEW_PRESETS:
            if cur_color and cur_color.lower() == col.name():
                cur_key = key
                break
        if not cur_key:
            cur_key = "custom" if cur_color else "danger"
        self.cmb_cell_preview.setCurrentIndex(
            max(0, self.cmb_cell_preview.findData(cur_key)))
        self.cmb_cell_preview.currentIndexChanged.connect(
            self._apply_cell_preview_preset)
        self._row(c2, card2, i18n.T("settings.icons.preview.preset.title"),
                  self.cmb_cell_preview,
                  i18n.T("settings.icons.preview.preset.hint"))

        self._cell_preview_color = cur_color
        self.btn_cell_preview_color = QPushButton()
        self.btn_cell_preview_color.setFixedWidth(120)
        self.btn_cell_preview_color.setCursor(Qt.PointingHandCursor)
        self.btn_cell_preview_color.clicked.connect(
            self._pick_cell_preview_color)
        self._sync_cell_preview_color_btn()
        self._row(c2, card2, i18n.T("settings.icons.preview.color.title"),
                  self.btn_cell_preview_color,
                  i18n.T("settings.icons.preview.color.hint"))

        self.sld_cell_preview_fill = QSlider(Qt.Horizontal)
        self.sld_cell_preview_fill.setRange(
            _ls.PREVIEW_FILL_MIN, _ls.PREVIEW_FILL_MAX)
        self.sld_cell_preview_fill.setFixedWidth(150)
        self.sld_cell_preview_fill.setValue(
            int(self.storage.get_setting("cell_preview_fill") or 8))
        self.lbl_cell_preview_fill = QLabel(
            "%d%%" % self.sld_cell_preview_fill.value())
        self.lbl_cell_preview_fill.setObjectName("rowHint")
        self.lbl_cell_preview_fill.setFixedWidth(40)
        self.sld_cell_preview_fill.valueChanged.connect(
            self._apply_cell_preview_fill)
        fill_box = QWidget()
        fb = QHBoxLayout(fill_box)
        fb.setContentsMargins(0, 0, 0, 0)
        fb.setSpacing(8)
        fb.addWidget(self.sld_cell_preview_fill)
        fb.addWidget(self.lbl_cell_preview_fill)
        self._row(c2, card2, i18n.T("settings.icons.preview.fill.title"),
                  fill_box, i18n.T("settings.icons.preview.fill.hint"))

        self.sp_cell_preview_width = Stepper()
        self.sp_cell_preview_width.setRange(
            _ls.PREVIEW_WIDTH_MIN, _ls.PREVIEW_WIDTH_MAX)
        self.sp_cell_preview_width.setSuffix("px")
        self.sp_cell_preview_width.setFixedWidth(96)
        self.sp_cell_preview_width.setValue(
            int(self.storage.get_setting("cell_preview_width") or 2))
        self.sp_cell_preview_width.valueChanged.connect(
            self._apply_cell_preview_width)
        self._row(c2, card2, i18n.T("settings.icons.preview.width.title"),
                  self.sp_cell_preview_width,
                  i18n.T("settings.icons.preview.width.hint"))

        self.sp_cell_preview_radius = Stepper()
        self.sp_cell_preview_radius.setRange(
            _ls.PREVIEW_RADIUS_MIN, _ls.PREVIEW_RADIUS_MAX)
        self.sp_cell_preview_radius.setSuffix("px")
        self.sp_cell_preview_radius.setFixedWidth(96)
        self.sp_cell_preview_radius.setValue(
            int(self.storage.get_setting("cell_preview_radius") or 8))
        self.sp_cell_preview_radius.valueChanged.connect(
            self._apply_cell_preview_radius)
        self._row(c2, card2, i18n.T("settings.icons.preview.radius.title"),
                  self.sp_cell_preview_radius,
                  i18n.T("settings.icons.preview.radius.hint"))
        lay.addWidget(card2)
        self._sync_label_controls()

    # ---- 图标页：写设置 + 热更新窗口 ----
    def _label_style_changed(self):
        """图标页任何一项改动：落防抖盘 + 推窗口热更新。

        与 Dock 外观同一套防抖（拖滑条时每像素都写盘会拖死主线程，
        v10 反馈 27）。窗口不可用（探针 / 替身）时只落设置。
        """
        self._flush_storage()
        hook = getattr(self.window, "apply_label_style", None)
        if callable(hook):
            try:
                hook()
            except (RuntimeError, AttributeError):
                pass     # 窗口正在销毁——设置已落盘，下次启动生效

    def _sync_label_color_btn(self):
        c = QColor(self._label_color)
        if not c.isValid():
            c = QColor("#f4f5f7")
        fg = "#101216" if c.lightness() > 150 else "#f2f5fa"
        self.btn_label_color.setText(c.name().upper())
        self.btn_label_color.setStyleSheet(
            "QPushButton{background-color:%s;color:%s;border:1px solid %s;"
            "border-radius:6px;padding:4px 8px;font-size:12px;}"
            % (c.name(), fg, theme.rgba(theme.BORDER)))

    def _sync_outline_color_btn(self):
        c = QColor(self._label_outline_color)
        if not c.isValid():
            c = QColor("#000000")
        fg = "#101216" if c.lightness() > 150 else "#f2f5fa"
        self.btn_label_outline_color.setText(c.name().upper())
        self.btn_label_outline_color.setStyleSheet(
            "QPushButton{background-color:%s;color:%s;border:1px solid %s;"
            "border-radius:6px;padding:4px 8px;font-size:12px;}"
            % (c.name(), fg, theme.rgba(theme.BORDER)))

    def _sync_cell_preview_color_btn(self):
        raw = str(self._cell_preview_color or "").strip()
        c = QColor(raw) if raw else QColor(theme.DANGER)
        if not c.isValid():
            c = QColor(theme.DANGER)
        fg = "#101216" if c.lightness() > 150 else "#f2f5fa"
        self.btn_cell_preview_color.setText(
            i18n.T("button.follow_theme") if not raw else c.name().upper())
        self.btn_cell_preview_color.setStyleSheet(
            "QPushButton{background-color:%s;color:%s;border:1px solid %s;"
            "border-radius:6px;padding:4px 8px;font-size:12px;}"
            "QPushButton:disabled{color:%s;}"
            % (c.name(), fg, theme.rgba(theme.BORDER),
               theme.rgba(theme.TEXT_FAINT)))

    def _sync_label_controls(self):
        """依赖开关的可用态：没开阴影就别让浓度滑条可拖（不留无效旋钮）。"""
        shadow_on = bool(self.chk_label_shadow.isChecked())
        self.sld_label_shadow.setEnabled(shadow_on)
        outline_on = bool(self.chk_label_outline.isChecked())
        self.sp_label_outline_w.setEnabled(outline_on)
        self.btn_label_outline_color.setEnabled(outline_on)

    # ---------- 液态玻璃（v14 反馈 9，重做：透明底 + 边缘折射） ----------
    def _liquid_slider(self, key, slot, lo=0, hi=100, suffix="%"):
        sld = QSlider(Qt.Horizontal)
        sld.setRange(lo, hi)
        sld.setFixedWidth(150)
        sld.setStyleSheet(theme.SLIDER_QSS)
        sld.setValue(int(self.storage.get_setting(key) or 0))
        sld.valueChanged.connect(slot)
        sld.sliderReleased.connect(self._flush_storage)
        sld._liquid_suffix = suffix
        return sld

    def _liquid_value_lbl(self, sld, suffix="%"):
        lbl = QLabel("%d%s" % (sld.value(), suffix))
        lbl.setObjectName("rowHint")
        lbl.setFixedWidth(48)
        return lbl

    def _liquid_slider_box(self, sld, lbl):
        box = QWidget()
        b = QHBoxLayout(box)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(8)
        b.addWidget(sld)
        b.addWidget(lbl)
        return box

    def _sync_liquid_controls(self):
        """总开关关闭时下属控件置灰（0 强度是合法值，滑条不跟着置灰）。"""
        on = bool(self.chk_liquid.isChecked())
        for w in (self.sld_liquid_strength, self.sld_liquid_band,
                  self.sld_liquid_saturation, self.sld_liquid_frost,
                  self.sld_liquid_aberration, self.sld_liquid_rimlight,
                  self.cmb_liquid_fps):
            w.setEnabled(on)
        self.sp_liquid_fps.setEnabled(
            on and self.cmb_liquid_fps.currentData() == "custom")

    def _liquid_changed(self):
        """液态玻璃任何一项改动：防抖落盘 + 推三个表面重绘。"""
        self._flush_storage()
        hook = getattr(self.window, "apply_liquid_style", None)
        if callable(hook):
            try:
                hook()
            except (RuntimeError, AttributeError):
                pass     # 窗口正在销毁——设置已落盘，下次启动生效

    def _apply_liquid_master(self, on):
        self.storage.set_setting("liquid_glass", bool(on))
        self._sync_liquid_controls()
        self._liquid_changed()

    def _apply_liquid_strength(self, v):
        self.lbl_liquid_strength.setText("%d%%" % v)
        self.storage.set_setting("liquid_strength", v)
        self._liquid_changed()

    def _apply_liquid_band(self, v):
        self.lbl_liquid_band.setText(
            "%d%s" % (v, i18n.T("settings.appearance.liquid.unit_px")))
        self.storage.set_setting("liquid_band", v)
        self._liquid_changed()

    def _apply_liquid_saturation(self, v):
        self.lbl_liquid_saturation.setText("%d%%" % v)
        self.storage.set_setting("liquid_saturation", v)
        self._liquid_changed()

    def _apply_liquid_frost(self, v):
        self.lbl_liquid_frost.setText("%d%%" % v)
        self.storage.set_setting("liquid_frost", v)
        self._liquid_changed()

    def _apply_liquid_aberration(self, v):
        self.lbl_liquid_aberration.setText("%d%%" % v)
        self.storage.set_setting("liquid_aberration", v)
        self._liquid_changed()

    def _apply_liquid_rimlight(self, v):
        self.lbl_liquid_rimlight.setText("%d%%" % v)
        self.storage.set_setting("liquid_rimlight", v)
        self._liquid_changed()

    def _apply_liquid_fps_mode(self, *_):
        mode = self.cmb_liquid_fps.currentData()
        if mode:
            self.storage.set_setting("liquid_fps_mode", str(mode))
        self.sp_liquid_fps.setEnabled(mode == "custom")
        self._liquid_changed()

    def _apply_liquid_fps_custom(self, v):
        self.storage.set_setting("liquid_fps_custom", int(v))
        self._liquid_changed()

    def _reset_liquid(self):
        """液态玻璃整卡回出厂（总闸开 + 默认光学参数）。"""
        defaults = (("liquid_glass", True),
                    ("liquid_dock", True),
                    ("liquid_strength", liquid_glass.DEFAULT_STRENGTH),
                    ("liquid_band", liquid_glass.DEFAULT_BAND),
                    ("liquid_saturation", liquid_glass.DEFAULT_SATURATION),
                    ("liquid_frost", liquid_glass.DEFAULT_FROST),
                    ("liquid_aberration", liquid_glass.DEFAULT_ABERRATION),
                    ("liquid_rimlight", liquid_glass.DEFAULT_RIMLIGHT),
                    ("liquid_fps_mode", liquid_glass.DEFAULT_FPS_MODE),
                    ("liquid_fps_custom", liquid_glass.DEFAULT_FPS_CUSTOM))
        for k, v in defaults:
            self.storage.set_setting(k, v)
        # 控件跟着回位（blockSignals：重置不该再触发一轮 apply）
        self.chk_liquid.blockSignals(True)
        self.chk_liquid.setChecked(True)
        self.chk_liquid.blockSignals(False)
        sliders = ((self.sld_liquid_strength, self.lbl_liquid_strength,
                    liquid_glass.DEFAULT_STRENGTH),
                   (self.sld_liquid_band, self.lbl_liquid_band,
                    liquid_glass.DEFAULT_BAND),
                   (self.sld_liquid_saturation, self.lbl_liquid_saturation,
                    liquid_glass.DEFAULT_SATURATION),
                   (self.sld_liquid_frost, self.lbl_liquid_frost,
                    liquid_glass.DEFAULT_FROST),
                   (self.sld_liquid_aberration, self.lbl_liquid_aberration,
                    liquid_glass.DEFAULT_ABERRATION),
                   (self.sld_liquid_rimlight, self.lbl_liquid_rimlight,
                    liquid_glass.DEFAULT_RIMLIGHT))
        for sld, lbl, v in sliders:
            sld.blockSignals(True)
            sld.setValue(v)
            sld.blockSignals(False)
            lbl.setText("%d%s" % (v, sld._liquid_suffix))
        self.cmb_liquid_fps.blockSignals(True)
        self.cmb_liquid_fps.setCurrentIndex(max(
            0, self.cmb_liquid_fps.findData(liquid_glass.DEFAULT_FPS_MODE)))
        self.cmb_liquid_fps.blockSignals(False)
        self.sp_liquid_fps.blockSignals(True)
        self.sp_liquid_fps.setValue(liquid_glass.DEFAULT_FPS_CUSTOM)
        self.sp_liquid_fps.blockSignals(False)
        self._sync_liquid_controls()
        self._liquid_changed()

    def _pick_label_color(self):
        start = QColor(self._label_color)
        if not start.isValid():
            start = QColor("#f4f5f7")
        c = QColorDialog.getColor(start, self,
                                  i18n.T("settings.icons.color.title"),
                                  QColorDialog.DontUseNativeDialog)
        if not c.isValid():
            return
        self._label_color = c.name()
        self._sync_label_color_btn()
        self._apply_label_color()

    def _pick_label_outline_color(self):
        start = QColor(self._label_outline_color)
        if not start.isValid():
            start = QColor("#000000")
        c = QColorDialog.getColor(start, self,
                                  i18n.T("settings.icons.outline_color.title"),
                                  QColorDialog.DontUseNativeDialog)
        if not c.isValid():
            return
        self._label_outline_color = c.name()
        self._sync_outline_color_btn()
        self.storage.set_setting("label_outline_color", c.name())
        self._label_style_changed()

    def _apply_label_color(self, *_):
        self.storage.set_setting("label_color", self._label_color)
        self._label_style_changed()

    def _apply_label_bold(self, on):
        self.storage.set_setting("label_bold", bool(on))
        self._label_style_changed()

    def _apply_label_shadow(self, on):
        self.storage.set_setting("label_shadow", bool(on))
        self._sync_label_controls()
        self._label_style_changed()

    def _apply_label_shadow_alpha(self, v):
        self.lbl_label_shadow.setText("%d%%" % int(v))
        self.storage.set_setting("label_shadow_alpha", int(v))
        self._label_style_changed()

    def _apply_label_outline(self, on):
        self.storage.set_setting("label_outline", bool(on))
        self._sync_label_controls()
        self._label_style_changed()

    def _apply_label_outline_w(self, v):
        self.storage.set_setting("label_outline_width", int(v))
        self._label_style_changed()

    def _apply_cell_preview_enabled(self, on: bool):
        """落点指示框整体开关（关闭 = 拖动时不画预览框）。"""
        self.storage.set_setting("cell_preview_enabled", bool(on))

    def _apply_cell_preview_preset(self, idx):
        key = self.cmb_cell_preview.currentData()
        if key == "custom":
            # 「自定义」= 立刻开取色盘；取消则退回上一个有效预设
            self._pick_cell_preview_color(revert_to=(
                self.cmb_cell_preview, idx))
            return
        from . import label_style as _ls
        _ls.apply_preview_preset(self.storage, key)
        col = QColor(self.storage.get_setting("cell_preview_color"))
        self._cell_preview_color = col.name()
        self._sync_cell_preview_color_btn()
        self._label_style_changed()

    def _pick_cell_preview_color(self, revert_to=None):
        raw = str(self._cell_preview_color or "").strip()
        start = QColor(raw) if raw else QColor(theme.DANGER)
        if not start.isValid():
            start = QColor(theme.DANGER)
        c = QColorDialog.getColor(start, self,
                                  i18n.T("settings.icons.preview.color.title"),
                                  QColorDialog.DontUseNativeDialog)
        if not c.isValid():
            if revert_to is not None:
                cmb, idx = revert_to
                cur = str(self.storage.get_setting("cell_preview_color") or "")
                back = "custom" if cur else "danger"
                cmb.blockSignals(True)
                cmb.setCurrentIndex(max(0, cmb.findData(back)))
                cmb.blockSignals(False)
            return
        self._cell_preview_color = c.name()
        self.storage.set_setting("cell_preview_color", c.name())
        self._sync_cell_preview_color_btn()
        if revert_to is not None:
            cmb, _idx = revert_to
            cmb.blockSignals(True)
            cmb.setCurrentIndex(max(0, cmb.findData("custom")))
            cmb.blockSignals(False)
        self._label_style_changed()

    def _apply_cell_preview_fill(self, v):
        self.lbl_cell_preview_fill.setText("%d%%" % int(v))
        self.storage.set_setting("cell_preview_fill", int(v))
        self._label_style_changed()

    def _apply_cell_preview_width(self, v):
        self.storage.set_setting("cell_preview_width", int(v))
        self._label_style_changed()

    def _apply_cell_preview_radius(self, v):
        self.storage.set_setting("cell_preview_radius", int(v))
        self._label_style_changed()

    def _reset_label_text(self):
        """标签文字回出厂（= Windows 原生：白字 + 柔和黑阴影，无描边）。"""
        from . import label_style as _ls
        d = _ls.default_label_style()
        self.storage.set_setting("label_color", d.color.name())
        self.storage.set_setting("label_bold", d.bold)
        self.storage.set_setting("label_shadow", d.shadow)
        self.storage.set_setting("label_shadow_color",
                                 d.shadow_color.name())
        self.storage.set_setting("label_shadow_alpha",
                                 int(round(d.shadow_alpha * 100)))
        self.storage.set_setting("label_outline", d.outline)
        self.storage.set_setting("label_outline_color",
                                 d.outline_color.name())
        self.storage.set_setting("label_outline_width", d.outline_width)
        # 控件跟着回位（blockSignals：重置不该再触发一轮 apply）
        self._label_color = d.color.name()
        self._sync_label_color_btn()
        for w, v in ((self.chk_label_bold, d.bold),
                     (self.chk_label_shadow, d.shadow),
                     (self.chk_label_outline, d.outline)):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        self.sld_label_shadow.blockSignals(True)
        self.sld_label_shadow.setValue(int(round(d.shadow_alpha * 100)))
        self.sld_label_shadow.blockSignals(False)
        self.lbl_label_shadow.setText("%d%%" % int(round(d.shadow_alpha * 100)))
        self.sp_label_outline_w.blockSignals(True)
        self.sp_label_outline_w.setValue(d.outline_width)
        self.sp_label_outline_w.blockSignals(False)
        self._sync_label_controls()
        self._label_style_changed()

    def _reset_cell_preview(self):
        """预览框回出厂（现行红框：DANGER + 8% 填充 + 2px + R8 + 开）。"""
        from . import label_style as _ls
        d = _ls.default_preview_style()
        self.storage.set_setting("cell_preview_color", "")
        self.storage.set_setting("cell_preview_fill",
                                 int(round(d.fill_alpha * 100)))
        self.storage.set_setting("cell_preview_width", d.width)
        self.storage.set_setting("cell_preview_radius", d.radius)
        self.storage.set_setting("cell_preview_enabled", True)
        if hasattr(self, "chk_cell_preview_enabled"):
            self.chk_cell_preview_enabled.blockSignals(True)
            self.chk_cell_preview_enabled.setChecked(True)
            self.chk_cell_preview_enabled.blockSignals(False)
        self._cell_preview_color = ""
        self._sync_cell_preview_color_btn()
        self.sld_cell_preview_fill.blockSignals(True)
        self.sld_cell_preview_fill.setValue(int(round(d.fill_alpha * 100)))
        self.sld_cell_preview_fill.blockSignals(False)
        self.lbl_cell_preview_fill.setText(
            "%d%%" % int(round(d.fill_alpha * 100)))
        self.sp_cell_preview_width.blockSignals(True)
        self.sp_cell_preview_width.setValue(d.width)
        self.sp_cell_preview_width.blockSignals(False)
        self.sp_cell_preview_radius.blockSignals(True)
        self.sp_cell_preview_radius.setValue(d.radius)
        self.sp_cell_preview_radius.blockSignals(False)
        self.cmb_cell_preview.blockSignals(True)
        self.cmb_cell_preview.setCurrentIndex(
            max(0, self.cmb_cell_preview.findData("danger")))
        self.cmb_cell_preview.blockSignals(False)
        self._label_style_changed()

    def _reset_label_page(self):
        """整页回出厂（两个卡片各管各的，这里一把都重置）。"""
        self._reset_label_text()
        self._reset_cell_preview()

    def _page_layout(self, body, lay):
        card, c = self._card(body, i18n.T("settings.layout.card.grid"))
        # ① 读原生图标尺寸（决定 cell 基准）——v7 §3.1 起移入网格卡片
        self.chk_match_native = Switch()
        self.chk_match_native.setChecked(
            bool(self.storage.get_setting("icon_match_native")))
        self.chk_match_native.toggled.connect(self._apply_match_native)
        self._row(c, card, i18n.T("settings.layout.match_native.title"),
                  self.chk_match_native,
                  i18n.T("settings.layout.match_native.hint"))

        # ② 自动 / 手动行列（v7 §3.2：列数改格子，行数只裁剪）
        self.chk_auto_cols = Switch()
        self._grid_syncing = False
        dims_auto = self.storage.grid_dims_mode() == "auto"
        self.chk_auto_cols.setChecked(dims_auto)
        self.chk_auto_cols.toggled.connect(self._apply_auto_columns)
        self._row(c, card, i18n.T("settings.layout.auto_dims.title"),
                  self.chk_auto_cols, i18n.T("settings.layout.auto_dims.hint"))

        self.spin_cols = Stepper()
        self.spin_cols.setRange(1, 24)
        self.spin_cols.setSuffix(i18n.T("settings.layout.unit_cols"))
        self.spin_cols.setFixedWidth(96)
        self.spin_cols.setEnabled(not dims_auto)
        self.spin_cols.valueChanged.connect(self._apply_columns)
        self._row(c, card, i18n.T("settings.layout.cols.title"), self.spin_cols,
                  i18n.T("settings.layout.cols.hint"))

        self.spin_rows = Stepper()
        self.spin_rows.setRange(1, 12)
        self.spin_rows.setSuffix(i18n.T("settings.layout.unit_rows"))
        self.spin_rows.setFixedWidth(96)
        self.spin_rows.setEnabled(not dims_auto)
        self.spin_rows.valueChanged.connect(self._apply_rows)
        self._row(c, card, i18n.T("settings.layout.rows.title"), self.spin_rows,
                  i18n.T("settings.layout.rows.hint"))

        self.lbl_grid_dims = QLabel()
        self.lbl_grid_dims.setObjectName("rowHint")
        # v10 A4（反馈 10 规则 3）：原 hint 与同卡片的「列数 / 行数」两行
        # 完全同义，删；本行是只读读数，不需要说明。
        self._row(c, card, i18n.T("settings.layout.grid_dims.title"),
                  self.lbl_grid_dims)

        # ③ 网格原点不在这里：v7 §3.4 把它定为**开发者选项**，控件在
        #    「高级」页的「调试」卡片里，且 dev_mode 关闭时不可配置
        #    （2026-10-20 用户反馈 5：不要放右键、也不该跟行列同级）。
        lay.addWidget(card)
        self._refresh_grid_spins()

        # 文件夹默认尺寸（反馈 14）：默认 3×3，最小 2×2；成员少时弹窗会按
        # 实际数量收缩（2 个 -> 2×2），这里设的是**上限**。
        card, c = self._card(body, i18n.T("settings.layout.card.folder"))
        fdef = self.storage.folder_frame_default()
        fbox = QWidget()
        fh = QHBoxLayout(fbox)
        fh.setContentsMargins(0, 0, 0, 0)
        fh.setSpacing(8)
        self.sp_folder_cols = Stepper()
        self.sp_folder_cols.setRange(2, 6)
        self.sp_folder_cols.setSuffix(i18n.T("settings.layout.unit_cols"))
        self.sp_folder_cols.setFixedWidth(82)
        self.sp_folder_cols.setValue(int(fdef[0]))
        self.sp_folder_cols.valueChanged.connect(self._apply_folder_frame_default)
        self.sp_folder_rows = Stepper()
        self.sp_folder_rows.setRange(2, 6)
        self.sp_folder_rows.setSuffix(i18n.T("settings.layout.unit_rows"))
        self.sp_folder_rows.setFixedWidth(82)
        self.sp_folder_rows.setValue(int(fdef[1]))
        self.sp_folder_rows.valueChanged.connect(self._apply_folder_frame_default)
        fh.addWidget(self.sp_folder_cols)
        fh.addWidget(self.sp_folder_rows)
        self._row(c, card, i18n.T("settings.layout.folder_default.title"), fbox,
                  i18n.T("settings.layout.folder_default.hint"))

        # v14：文件夹悬停自动展开（原来藏在「模式」页的同步卡片里，找不到）。
        # 悬停展开的弹窗在鼠标离开图标与弹窗后会自动收起，点击打开的不受
        # 影响——这一条写进 hint，用户不必试才知道。
        self.chk_hover_folder = Switch()
        self.chk_hover_folder.setChecked(
            bool(self.storage.get_setting("folder_hover_expand")))
        self.chk_hover_folder.toggled.connect(self._apply_folder_hover)
        self._row(c, card, i18n.T("settings.layout.folder_hover.title"),
                  self.chk_hover_folder,
                  i18n.T("settings.layout.folder_hover.hint"))
        lay.addWidget(card)

        card, c = self._card(body, i18n.T("settings.layout.card.paging"))
        self.cmb_page_axis = ComboBox()
        self.cmb_page_axis.addItem(i18n.T("settings.layout.page_axis.h"), "horizontal")
        self.cmb_page_axis.addItem(i18n.T("settings.layout.page_axis.v"), "vertical")
        self.cmb_page_axis.setCurrentIndex(
            max(0, self.cmb_page_axis.findData(grid_flow.page_axis(self.storage))))
        self.cmb_page_axis.currentIndexChanged.connect(self._apply_page_axis)
        self._row(c, card, i18n.T("settings.layout.page_axis.title"),
                  self.cmb_page_axis,
                  i18n.T("settings.layout.page_axis.hint"))

        self.chk_auto_arrange = Switch()
        self.chk_auto_arrange.setChecked(bool(self.storage.get_setting("auto_arrange")))
        self.chk_auto_arrange.toggled.connect(self._apply_auto_arrange)
        self._row(c, card, i18n.T("settings.layout.auto_arrange.title"),
                  self.chk_auto_arrange,
                  i18n.T("settings.layout.auto_arrange.hint"))
        lay.addWidget(card)

        # ---------- v9 V2：每页排序规则 ----------
        card, c = self._card(body, i18n.T("settings.layout.card.page_rules"))
        self.chk_page_rules = Switch()
        self.chk_page_rules.setChecked(page_rules.custom_enabled(self.storage))
        self.chk_page_rules.toggled.connect(self._apply_page_rules_custom)
        self._row(c, card, i18n.T("settings.layout.page_rules.title"),
                  self.chk_page_rules,
                  i18n.T("settings.layout.page_rules.hint"))
        self._rules_holder = QWidget(card)
        self._rules_lay = QVBoxLayout(self._rules_holder)
        self._rules_lay.setContentsMargins(0, 6, 0, 2)
        self._rules_lay.setSpacing(6)
        c.addWidget(self._rules_holder)
        self._rule_combos = {}
        self._rebuild_page_rules()
        lay.addWidget(card)

    def _page_mode(self, body, lay):
        # ---- v9 V4-6 交互方式（Windows 习惯 / 手机习惯） ----
        # 放在「模式」页最上面：这是"桌面怎么用"的总开关，比布局细节更基础。
        card, c = self._card(body, i18n.T("settings.mode.card.interaction"))
        self.cmb_interaction = ComboBox()
        for key, text in interaction.MODES:
            self.cmb_interaction.addItem(
                i18n.TL("interaction." + key, text), key)
        self.cmb_interaction.setCurrentIndex(
            max(0, self.cmb_interaction.findData(interaction.mode(self.storage))))
        self.cmb_interaction.currentIndexChanged.connect(self._apply_interaction)
        self._row(c, card, i18n.T("settings.mode.interaction.title"),
                  self.cmb_interaction,
                  i18n.T("settings.mode.interaction.hint"))
        self.lbl_interaction = QLabel(self._describe_interaction())
        self.lbl_interaction.setObjectName("rowHint")
        self.lbl_interaction.setWordWrap(True)
        c.addWidget(self.lbl_interaction)
        lay.addWidget(card)

        # v14i：「悬浮模式不透明度」滑条退役——悬浮形态只是挂载失败的
        # 兜底（用户几乎永远见不到），为它留一个"挂载态永远无效"的设置
        # 只会造成困惑。兜底窗改用固定值 desktop_window.FLOATING_OPACITY。

        card, c = self._card(body, i18n.T("settings.mode.card.takeover"))
        # 反馈 16：**启动即接管**——「接管系统桌面」开关冗余，已删除。
        # 想回到原生桌面就退出程序；只想用小组件就把「通用 → 显示桌面
        # 小组件」打开、图标仍由本程序接管（两者互不牵连）。
        # v10 A4（反馈 10 规则 1）：括号只留给单位/取值域，行为解释不进括号。
        note = QLabel(i18n.T("settings.mode.takeover.note"))
        note.setObjectName("rowHint")
        note.setWordWrap(True)
        c.addWidget(note)

        self.cmb_layout_mode = ComboBox()
        self.cmb_layout_mode.addItem(i18n.T("settings.mode.layout.grid"), "grid")
        self.cmb_layout_mode.addItem(i18n.T("settings.mode.layout.free"), "free")
        self.cmb_layout_mode.setCurrentIndex(
            max(0, self.cmb_layout_mode.findData(grid_flow.layout_mode(self.storage))))
        self.cmb_layout_mode.currentIndexChanged.connect(self._apply_layout_mode)
        # 仅小组件模式（反馈 9）：组件必须无界，布局下拉置灰并说明原因
        # （窗口侧 set_layout_mode 还有护栏，双保险）。
        widgets_only = bool(self.storage.get_setting("widgets_only"))
        self.cmb_layout_mode.setEnabled(not widgets_only)
        self._row(c, card, i18n.T("settings.mode.layout.title"), self.cmb_layout_mode,
                  i18n.T("settings.mode.layout.hint") if not widgets_only
                  else i18n.T("settings.mode.layout.hint_locked"))
        lay.addWidget(card)

        card, c = self._card(body, i18n.T("settings.mode.card.files"))
        self.chk_autoclone = Switch()
        self.chk_autoclone.setChecked(bool(self.storage.get_setting("auto_clone_desktop")))
        self.chk_autoclone.toggled.connect(self._apply_auto_clone)
        self._row(c, card, i18n.T("settings.mode.auto_clone.title"),
                  self.chk_autoclone, i18n.T("settings.mode.auto_clone.hint"))

        self.chk_autosync = Switch()
        self.chk_autosync.setChecked(bool(self.storage.get_setting("auto_sync_desktop")))
        self.chk_autosync.toggled.connect(self._apply_auto_sync)
        self._row(c, card, i18n.T("settings.mode.auto_sync.title"),
                  self.chk_autosync, i18n.T("settings.mode.auto_sync.hint"))

        # v14 反馈 1：文件夹悬停自动展开
        # （v14 追加：已移到「布局与网格 ▸ 文件夹」卡——那里才是文件夹设置）
        lay.addWidget(card)

    def _page_advanced(self, body, lay):
        """高级页（v14h 吸并原「开发者」页）：调试叠加层 / 网格原点 +
        维护 / 更新 / 关于——原两页各只有两三张卡，合并后一页刚好。

        开发者分项（v11 反馈 32）：总开关 dev_mode 关 = 下面全部失效：

            ├─ 网格线 / 原点 / 几何信息   dev_grid
            ├─ 组件碰撞体积               dev_widget_collision
            ├─ 帧率与绘制面积             dev_perf
            ├─ 组件窗口一致性巡检         dev_window_check
            └─ 网格原点定位（手动位置）    grid_origin_mode

        分项默认全开：总开关打开后的行为与旧版一致（零迁移成本）。
        """
        card, c = self._card(body, i18n.T("settings.dev.card_master"))
        self.chk_dev = Switch()
        self.chk_dev.setChecked(bool(self.storage.get_setting("dev_mode")))
        self.chk_dev.toggled.connect(self._apply_dev_mode)
        self._row(c, card, i18n.T("settings.dev.master.title"), self.chk_dev,
                  i18n.T("settings.dev.master.hint"))
        lay.addWidget(card)

        card, c = self._card(body, i18n.T("settings.dev.card_overlays"))
        self._dev_switches = {}
        for key, name in (("dev_grid", "grid"),
                          ("dev_widget_collision", "collision"),
                          ("dev_perf", "perf"),
                          ("dev_window_check", "window")):
            sw = Switch()
            sw.setChecked(self._dev_value(key))
            sw.toggled.connect(
                lambda checked, k=key: self._apply_dev_flag(k, checked))
            self._row(c, card, i18n.T("settings.dev.%s.title" % name), sw,
                      i18n.T("settings.dev.%s.hint" % name))
            self._dev_switches[key] = sw
        lay.addWidget(card)

        # ---- 网格原点定位（v7 §3.4 / 反馈 21）：开发者功能。原来在「高级」
        # 页的调试卡片里，v11 整组搬到这里（高级只留维护 / 更新 / 关于）。
        card, c = self._card(body, i18n.T("settings.dev.card_origin"))
        origin_manual = self.storage.grid_origin_mode() == "manual"
        self.chk_origin = Switch()
        self.chk_origin.setChecked(origin_manual)
        self.chk_origin.toggled.connect(self._apply_origin_mode)
        self._row(c, card, i18n.T("settings.advanced.origin_manual.title"),
                  self.chk_origin,
                  i18n.T("settings.advanced.origin_manual.hint"))

        self.sp_ox = Stepper()
        self.sp_ox.setRange(0, 9999)
        self.sp_ox.setSingleStep(2)
        self.sp_ox.setSuffix(" px")
        self.sp_ox.setFixedWidth(96)
        self.sp_ox.setEnabled(origin_manual)
        self.sp_ox.valueChanged.connect(self._apply_origin_spin)
        self._row(c, card, i18n.T("settings.advanced.origin_x.title"), self.sp_ox,
                  i18n.T("settings.advanced.origin_x.hint"))

        self.sp_oy = Stepper()
        self.sp_oy.setRange(0, 9999)
        self.sp_oy.setSingleStep(2)
        self.sp_oy.setSuffix(" px")
        self.sp_oy.setFixedWidth(96)
        self.sp_oy.setEnabled(origin_manual)
        self.sp_oy.valueChanged.connect(self._apply_origin_spin)
        self._row(c, card, i18n.T("settings.advanced.origin_y.title"), self.sp_oy,
                  i18n.T("settings.advanced.origin_y.hint"))

        self.btn_reset_origin = QPushButton(i18n.T("button.reset_origin"))
        self.btn_reset_origin.setEnabled(origin_manual)
        self.btn_reset_origin.clicked.connect(self._reset_grid_origin)
        self._row(c, card, i18n.T("settings.advanced.origin_reset.title"),
                  self.btn_reset_origin,
                  i18n.T("settings.advanced.origin_reset.hint"))

        # 桌面取点：从右键挪进来的入口（反馈 5）。点它先关设置窗，
        # 用户在桌面空白处点一下即为网格左上角。
        self.btn_pick_origin = QPushButton(i18n.T("button.pick_origin"))
        self.btn_pick_origin.setEnabled(origin_manual)
        self.btn_pick_origin.clicked.connect(self._pick_origin_on_desktop)
        self._row(c, card, i18n.T("settings.advanced.origin_pick.title"),
                  self.btn_pick_origin,
                  i18n.T("settings.advanced.origin_pick.hint"))
        lay.addWidget(card)
        self._sync_origin_controls()
        self._sync_dev_controls()

        # ---- 维护与关于（原高级页内容） ----
        card, c = self._card(body, i18n.T("settings.advanced.card.maintain"))
        btns = QWidget()
        bh = QHBoxLayout(btns)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(8)
        self.btn_rescan = QPushButton(i18n.T("button.rescan"))
        self.btn_rescan.clicked.connect(self._rescan)
        self.btn_reset = QPushButton(i18n.T("button.reset_layout"))
        self.btn_reset.clicked.connect(self._reset_layout)
        bh.addWidget(self.btn_rescan)
        bh.addWidget(self.btn_reset)
        self._row(c, card, i18n.T("settings.advanced.files.title"), btns,
                  i18n.T("settings.advanced.files.hint"))
        lay.addWidget(card)

        # 「关于」：版本标识（v6c 跑错包事故的取证设施——从桌面右键挪到这里，
        # 既满足"右键别塞版本号"，又保留"一眼看出跑的是哪个包"的能力）。
        card, c = self._card(body, i18n.T("settings.advanced.card.about"))
        from . import version
        self.lbl_about_version = QLabel(version.label())
        self.lbl_about_version.setObjectName("rowHint")
        self._row(c, card, i18n.T("settings.advanced.version.title"),
                  self.lbl_about_version,
                  i18n.T("settings.advanced.version.hint"))
        lay.addWidget(card)

        self._build_update_card(body, lay)
        self._build_author_card(body, lay)

    # ---------- 实时生效 ----------
    def _call_window(self, name, *args):
        """调用窗口的可选能力（缺失即忽略）。

        设置页只依赖"窗口"这一契约，不该把 refresh_grid / rebuild 之类
        具体方法名变成硬依赖——缺了会直接抛 AttributeError 把整个设置
        窗口带崩（test_dev_mode D7 用最小桩打出来的真实健壮性问题）。
        """
        fn = getattr(self.window, name, None)
        if callable(fn):
            return fn(*args)
        return None

    def _refresh_grid(self):
        """让窗口按新几何重建：优先 refresh_grid，退化为 rebuild / update。"""
        if getattr(self.window, "refresh_grid", None) is not None:
            return self._call_window("refresh_grid")
        if getattr(self.window, "rebuild", None) is not None:
            return self._call_window("rebuild")
        return self._call_window("update")

    # 连续输入控件（滑条 / 步进器长按连步）的统一落地节流。
    # 两个口径都要满足（2026-10-21 反馈 1）：
    #   停顿 60ms 就落地 -> 拖一下停一下也有实时反馈；
    #   连续拖动能忍的上限 220ms -> 不会"拖了半天桌面没反应"。
    # 实测 refresh_grid() 单次 16~22ms（60 图标）：每像素一次 = GUI 线程
    # 100% 占满，鼠标事件排队 -> "拖不动 / 断触"。
    GRID_REFRESH_DEBOUNCE = 60       # ms
    GRID_REFRESH_MAX_WAIT = 220      # ms

    def _schedule_grid_refresh(self):
        """几何类控件的统一落地入口（2026-10-21 反馈 1：滑条**断触**）。

        `refresh_grid()` 会**销毁重建全部桌面图标**并重新挂载桌面带；
        滑条 `valueChanged` 每像素触发一次，GUI 线程被重建占满，Qt 的
        鼠标抓取被反复打断，用户看到的就是"拖一下就停、不流畅"。
        同类问题也命中行列/原点步进器的长按连步（每 80ms 一次重建）。

        修法：**写入立即做**（storage 很便宜，滑条旁边的百分比立刻变），
        **重建走节流**——纯防抖（停顿即落地）+ 上限等待（连续拖动也保证
        每 220ms 内给一次反馈）。一次性操作（勾选 / 切换档位）仍走
        `_refresh_grid` 直连，不需要节流。
        """
        t = getattr(self, "_grid_timer", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._on_grid_debounced)
            self._grid_timer = t
            self._grid_wait = QElapsedTimer()
            self._grid_wait.start()
        if t.isActive():
            if self._grid_wait.elapsed() >= self.GRID_REFRESH_MAX_WAIT:
                t.stop()                            # 忍够了：立刻给一次反馈
                self._grid_wait.restart()
                self._refresh_grid()
                return
            t.start(self.GRID_REFRESH_DEBOUNCE)     # 重启 = 纯防抖语义
            return
        self._grid_wait.restart()
        t.start(self.GRID_REFRESH_DEBOUNCE)

    def _on_grid_debounced(self):
        self._grid_wait.restart()
        self._refresh_grid()

    def _apply_autostart(self, checked):
        autostart.set_enabled(bool(checked))
        self.storage.set_setting("autostart", bool(checked))

    def _describe_interaction(self, value=None):
        """交互方式的说明文案（v10 D3：按 key 取，回退到模块里的中文原文）。"""
        m = interaction.normalize(value if value is not None
                                  else interaction.mode(self.storage))
        return i18n.TL("interaction.describe." + m,
                       interaction.describe(m))

    def _refresh_icon_labels(self):
        """图标档位说明：显示当前占比与推导出的图标像素（v7 §3.1）。"""
        from . import grid_sizing
        # 滑条可用性先于几何算（几何快照未就绪时下面会 early return，
        # 但"三档预设下滑条只读"这条与几何无关，必须先生效）
        sld = getattr(self, "sld_icon_fit", None)
        if sld is not None:
            sld.setEnabled(self.storage.icon_tier() == "custom")
        m = grid_flow.current_metrics()
        if m is None:
            # 快照未就绪（对话框先于几何创建 / 悬浮兜底）：至少把滑条
            # 摆到**实际生效**的占比上，别让它停在 0% 造成"设置丢了"的
            # 错觉。走 resolved_icon_fit()（medium 也有确定值），旧实现读
            # `icon_fit()` 拿到 None -> 滑条留在 30% -> "中档却显示 30%"。
            self.lbl_icon_px.setText("")
            fit0 = self.storage.resolved_icon_fit()
            if getattr(self, "lbl_icon_fit", None) is not None:
                self.lbl_icon_fit.setText("%d%%" % int(round(fit0 * 100)))
            if sld is not None:
                sld.blockSignals(True)
                sld.setValue(max(sld.minimum(),
                                 min(int(round(fit0 * 100)), sld.maximum())))
                sld.blockSignals(False)
            return
        fit = self._effective_fit(m)
        lay = grid_sizing.icon_layout(m.cw, m.ch, fit)
        self.lbl_icon_px.setText(
            i18n.T("settings.appearance.icon_px", px=lay.icon_box))
        if getattr(self, "lbl_icon_fit", None) is not None:
            self.lbl_icon_fit.setText("%d%%" % int(round(fit * 100)))
        self._sync_fit_controls(fit)

    def _effective_fit(self, m=None):
        """当前**实际生效**的图标占比（medium 用 native_px/cell_w 解算）。

        v7c（反馈 3）：解算逻辑收到 `storage.resolved_icon_fit()`——设置页
        显示的"图标 Npx"必须与桌面真正画出来的尺寸同源，否则面板说 57%
        桌面画 54%（换屏后两者分叉），用户眼里就是"默认档位不对"。
        """
        from . import grid_flow
        m = m if m is not None else grid_flow.current_metrics()
        return self.storage.resolved_icon_fit(m.cw if m is not None else None)

    def _sync_fit_controls(self, fit):
        """把三档下拉与自定义滑条同步到 fit（阻断信号，避免回写死循环）。

        v7 B2（反馈 9）：滑条**只在「自定义」档可拖**。三档预设下它跟着
        显示该档的占比但置灰——否则用户拖动滑条，值立刻被 `tier_for_fit`
        反查成最近预设，看着像"拖了不生效"。
        """
        from .storage import tier_for_fit
        sld = getattr(self, "sld_icon_fit", None)
        cmb = getattr(self, "cmb_tier", None)
        if sld is not None:
            sld.blockSignals(True)
            sld.setValue(int(round(fit * 100)))
            sld.blockSignals(False)
        # 档位以 storage 为准：custom 是显式档位，不能被数值反查覆盖
        stored = self.storage.icon_tier()
        tier = stored if stored == "custom" else (tier_for_fit(fit) or stored)
        if cmb is not None and tier:
            idx = cmb.findData(tier)
            if idx >= 0 and idx != cmb.currentIndex():
                cmb.blockSignals(True)
                cmb.setCurrentIndex(idx)
                cmb.blockSignals(False)
        if sld is not None:
            sld.setEnabled(tier == "custom")

    def _apply_icon_tier(self, _index):
        tier = self.cmb_tier.currentData()
        if not tier:
            return
        from . import grid_sizing
        if tier == "custom":
            # 切到自定义：沿用当前占比（用户接着拖滑条微调），
            # 只把档位名义改掉，视觉不跳变。
            self.storage.apply_icon_fit(self._effective_fit(), tier="custom")
        else:
            self.storage.apply_icon_fit(grid_sizing.parse_fit(tier), tier=tier)
        self._refresh_icon_labels()
        self._refresh_grid()

    def _apply_icon_fit(self, value):
        """自定义占比滑条（30%~90%）：走 storage 唯一写入点，档位=自定义。

        反馈 1（2026-10-21）：滑条**断触**的根因见 `_schedule_grid_refresh`
        ——每像素一次全桌面重建。这里只写值 + 刷标签，重建交给防抖定时器。
        """
        self.storage.apply_icon_fit(int(value) / 100.0, tier="custom")
        self._refresh_icon_labels()
        self._schedule_grid_refresh()

    def _apply_match_native(self, checked):
        self.storage.set_setting("icon_match_native", bool(checked))
        self._refresh_grid()
        self._refresh_icon_labels()
        self._refresh_grid_spins()

    # ---------- v7 §3.2：网格行列（列数改 cell，行数只裁剪）----------
    def _refresh_grid_spins(self):
        """按当前几何刷新两个 spin 的上限与显示值，以及摘要文字。"""
        from . import grid_sizing
        spin_r = getattr(self, "spin_rows", None)
        lbl = getattr(self, "lbl_grid_dims", None)
        if spin_r is None:
            return
        m = grid_flow.current_metrics()
        self._grid_syncing = True
        try:
            if m is None:
                return
            cols_auto = m.cols_auto or m.cols
            rows_auto = m.rows_auto or m.rows
            self.spin_cols.setRange(1, max(1, cols_auto))
            spin_r.setRange(1, max(1, rows_auto))
            self.spin_cols.setValue(min(m.cols, cols_auto))
            spin_r.setValue(min(m.rows, rows_auto))
            if lbl is not None:
                lay = grid_sizing.icon_layout(m.cw, m.ch, self._effective_fit(m))
                lbl.setText(i18n.T(
                    "settings.layout.grid_dims.value",
                    cols=m.cols, rows=m.rows, cw=m.cw, ch=m.ch,
                    icon=lay.icon_box, colmax=cols_auto, rowmax=rows_auto))
        finally:
            self._grid_syncing = False

    def _apply_columns(self, value):
        if getattr(self, "_grid_syncing", False):
            return
        if self.chk_auto_cols.isChecked():
            return
        self.storage.set_setting("grid_cols", int(value))
        self.storage.compact_layout()
        self._schedule_grid_refresh()
        self._refresh_grid_spins()

    def _apply_rows(self, value):
        if getattr(self, "_grid_syncing", False):
            return
        if self.chk_auto_cols.isChecked():
            return
        self.storage.set_setting("grid_rows", int(value))
        self._schedule_grid_refresh()
        self._refresh_grid_spins()

    def _apply_auto_columns(self, checked):
        auto = bool(checked)
        self.spin_cols.setEnabled(not auto)
        if getattr(self, "spin_rows", None) is not None:
            self.spin_rows.setEnabled(not auto)
        if auto:
            self.storage.set_setting("grid_dims_mode", "auto")
        else:
            self.storage.set_setting("grid_dims_mode", "manual")
            self.storage.set_setting("grid_cols", int(self.spin_cols.value()))
            if getattr(self, "spin_rows", None) is not None:
                self.storage.set_setting("grid_rows",
                                         int(self.spin_rows.value()))
            self.storage.compact_layout()
        self.storage.save()
        self._refresh_grid()
        self._refresh_grid_spins()

    # ---------- 网格原点（v7 §3.2 / 反馈 21） ----------
    def _sync_origin_controls(self):
        """同步原点开关 / 两个 spin / 重置按钮的状态与显示值。

        spin 显示的是**实际生效**的原点（快照的 ox/oy），不是 storage 里
        存的原始值——用户填 9999 时屏幕上是夹紧后的位置，界面必须一致。
        """
        chk = getattr(self, "chk_origin", None)
        if chk is None:
            return
        manual = self.storage.grid_origin_mode() == "manual"
        # 开发者功能门槛：dev_mode 关闭时整组只读（数据保持，只是不能改）
        dev = bool(self.storage.get_setting("dev_mode"))
        ox, oy = grid_flow.origin()          # 生效值（快照已按越界夹紧）
        if manual and grid_flow.current_metrics() is None:
            # 快照还没算出来（悬浮兜底 / 未挂载就开设置）：显示用户存的值，
            # 否则界面会显示一个与 storage 无关的常量，用户以为设置没生效
            ox, oy = self.storage.grid_origin() or (ox, oy)
        self._origin_syncing = True
        try:
            chk.blockSignals(True)
            chk.setChecked(manual)
            chk.blockSignals(False)
            chk.setEnabled(dev)
            for sp, v in ((self.sp_ox, ox), (self.sp_oy, oy)):
                sp.blockSignals(True)
                sp.setEnabled(dev and manual)
                sp.setValue(max(0, min(int(v), sp.maximum())))
                sp.blockSignals(False)
            self.btn_reset_origin.setEnabled(dev and manual)
            pick = getattr(self, "btn_pick_origin", None)
            if pick is not None:
                pick.setEnabled(dev and manual)
        finally:
            self._origin_syncing = False

    def _pick_origin_on_desktop(self):
        """关掉设置窗，把桌面让给"取点"（反馈 5：入口从右键挪到开发者页）。"""
        fn = getattr(self.window, "_begin_pick_origin", None)
        if callable(fn) and fn():
            self.accept()

    def _apply_origin_mode(self, checked):
        """开关：开 = 以当前生效原点为起点转手动；关 = 回居中。"""
        if getattr(self, "_origin_syncing", False):
            return
        if checked:
            self.storage.set_grid_origin("manual",
                                         int(self.sp_ox.value()),
                                         int(self.sp_oy.value()))
        else:
            self.storage.reset_grid_origin()
        self._refresh_grid()
        self._sync_origin_controls()

    def _apply_origin_spin(self, _value):
        if getattr(self, "_origin_syncing", False):
            return
        if not self.chk_origin.isChecked():
            return
        self.storage.set_grid_origin("manual",
                                     int(self.sp_ox.value()),
                                     int(self.sp_oy.value()))
        self._schedule_grid_refresh()
        self._sync_origin_controls()

    def _reset_grid_origin(self):
        """重置网格位置：两键一起清空 -> 回居中（与桌面右键同一入口语义）。"""
        self.storage.reset_grid_origin()
        self._refresh_grid()
        self._sync_origin_controls()

    def _refresh_scaled_icons(self):
        """重建「已配置额外大小的图标」列表（反馈 12 的反悔入口）。"""
        box = getattr(self, "_scaled_box", None)
        if box is None:
            return
        while box.count():
            item = box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._scaled_rows = []
        scaled = []
        for e in self.storage.layout:
            if e.get("type") != "app":
                continue
            try:
                s = float(e.get("icon_scale", 1.0) or 1.0)
            except (TypeError, ValueError):
                s = 1.0
            if abs(s - 1.0) > 1e-6:
                scaled.append((e, s))
        if not scaled:
            lb = QLabel(i18n.T("settings.appearance.scaled_empty"))
            lb.setObjectName("rowHint")
            box.addWidget(lb)
            return
        for e, s in scaled:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 2, 0, 2)
            h.setSpacing(10)
            app = e.get("app") or {}
            name = app.get("name") or e.get("id") or "?"
            pct = int(round(s * 100))
            lb = QLabel(i18n.T("settings.appearance.scaled_item",
                               name=name, pct=pct))
            lb.setObjectName("rowTitle")
            btn = QPushButton(i18n.T("button.restore_100"))
            btn.setFixedWidth(96)
            eid = e.get("id")
            btn.clicked.connect(
                lambda _c=False, eid=eid: self._reset_icon_scale(eid))
            h.addWidget(lb, 1)
            h.addWidget(btn, 0)
            box.addWidget(row)
            self._scaled_rows.append((eid, pct, btn))

    def _reset_icon_scale(self, entry_id):
        """把某个图标的大小恢复成 100%（并刷新本页列表）。"""
        hook = getattr(self.window, "set_icon_scale", None)
        if callable(hook):
            hook(entry_id, 100, True)
        else:
            e = self.storage.find_entry(entry_id)
            if e is not None:
                e.pop("icon_scale", None)
                self.storage.save()
        self._refresh_scaled_icons()

    def _apply_folder_frame_default(self, _value=0):
        """文件夹默认展开尺寸（反馈 14）：写全局默认并让已开的弹窗重排。"""
        c = int(self.sp_folder_cols.value())
        r = int(self.sp_folder_rows.value())
        self.storage.set_setting("folder_frame_default", [c, r])
        hook = getattr(self.window, "refresh_folder_popup", None)
        if callable(hook):
            hook()

    def _apply_page_axis(self, _index):
        axis = self.cmb_page_axis.currentData() or "horizontal"
        hook = getattr(self.window, "set_page_axis", None)
        if hook is not None:
            hook(axis)
        else:
            self.storage.set_setting("page_axis", axis)

    def _apply_auto_arrange(self, checked):
        """自动排列（反馈 3）：开关就是"此后一直生效"，开启时立刻整理一次。

        v9 V2：启用"每页单独设置"时只整理**自动排列的那些页**——否则
        用户特意留作自由摆放的页会被这一次总开关顺手排掉。
        """
        self.storage.set_setting("auto_arrange", bool(checked))
        self._rebuild_page_rules()      # 下拉可用性随开关变（v10 A1 起不再有复述文字）
        if checked:
            tidy = getattr(self.window, "_tidy", None)
            if tidy is not None:
                tidy(self._auto_pages())

    def _auto_pages(self):
        """当前应当自动排列的页集合（None = 全部，交给窗口按全局整理）。"""
        if not page_rules.custom_enabled(self.storage):
            return None
        return page_rules.auto_pages(self.storage,
                                     grid_flow.page_count(self.storage))

    # ---------- v9 V2：分页排序规则 ----------
    def _apply_page_rules_custom(self, checked):
        page_rules.set_custom(self.storage, bool(checked))
        self._rebuild_page_rules()
        self._refresh_grid()

    def _rebuild_page_rules(self):
        """按当前页数重建"第 N 页"下拉（页数随屏幕/条目变化，不能写死）。"""
        if not hasattr(self, "_rules_lay"):
            return
        while self._rules_lay.count():
            item = self._rules_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rule_combos = {}
        total = max(1, grid_flow.page_count(self.storage))
        custom = page_rules.custom_enabled(self.storage)
        for page in range(total):
            row = QWidget(self._rules_holder)
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(10)
            lab = QLabel(i18n.T("settings.layout.page_n", n=page + 1), row)
            lab.setFixedWidth(64)
            h.addWidget(lab)
            cmb = ComboBox()
            for value, text in page_rules.MODE_LABELS:
                cmb.addItem(i18n.TL("page_rules." + value, text), value)
            cur = page_rules.rule_of(self.storage, page)
            idx = cmb.findData(cur)
            cmb.setCurrentIndex(max(0, idx))
            cmb.setEnabled(custom)
            cmb.currentIndexChanged.connect(
                lambda _i, p=page, c=cmb: self._apply_page_rule(p, c))
            cmb.setAccessibleName(
                i18n.T("settings.layout.page_n_rule", n=page + 1))
            h.addWidget(cmb, 1)
            # v10 A1（反馈 1）：原来这里再挂一个 hint 标签复述下拉选中的文字
            # （"跟随全局（自动排列）"），与 combo 完全重复——删掉，全局实际结果
            # 看卡片顶部的「自动排列图标」开关。
            self._rules_lay.addWidget(row)
            self._rule_combos[page] = cmb

    def _apply_page_rule(self, page, combo):
        value = combo.currentData() or page_rules.INHERIT
        page_rules.set_mode(self.storage, page, value)
        self._rebuild_page_rules()
        self._refresh_grid()

    def _apply_dev_mode(self, checked):
        self.storage.set_setting("dev_mode", bool(checked))
        # 原点定位 / 分项开关属开发者功能：总闸一翻，整组跟着可用/不可用
        self._sync_dev_controls()
        self._call_window('update')

    # ---------- v11：开发者分项开关 ----------
    def _dev_value(self, key: str, default: bool = True) -> bool:
        """读一个开发者分项设置（缺省 = True，与旧行为一致）。"""
        v = self.storage.get_setting(key)
        return default if v is None else bool(v)

    def _apply_dev_flag(self, key: str, checked):
        self.storage.set_setting(key, bool(checked))
        self._call_window('update')

    def _sync_dev_controls(self):
        """总闸关 -> 分项开关与原点整组置灰（数据保留，只是不能改）。"""
        on = bool(self.storage.get_setting("dev_mode"))
        for sw in getattr(self, "_dev_switches", {}).values():
            sw.setEnabled(on)
        self._sync_origin_controls()

    def _apply_widgets(self, checked):
        on = bool(checked)
        self.storage.set_setting("show_widgets", on)
        if self.layer is not None:
            self.layer.set_visible(on)
        if not on and bool(self.storage.get_setting("widgets_only")):
            # 仅小组件模式下再关小组件 = 桌面什么都不剩：自动退出该模式，
            # 让图标回来（避免"空桌面"死局）。
            self.chk_widgets_only.setChecked(False)

    def _apply_widgets_only(self, checked):
        """仅小组件模式（v7 反馈 9）：写设置 + 通知窗口切模式/重建。

        窗口是单一写者（set_widgets_only 内部自己落设置），这里只转发；
        窗口不可用时（探针 / 单测窗口替身）才自己写，保证设置不丢。
        """
        on = bool(checked)
        if on and not bool(self.storage.get_setting("show_widgets")):
            # 仅小组件 = 只显示组件：组件开关必须跟着开（否则桌面全空），
            # 设置窗里的开关态也同步回落，避免"显示与实际不符"。
            self.chk_widgets.setChecked(True)
        hook = getattr(self.window, "set_widgets_only", None)
        if hook is not None:
            hook(on)
        else:
            self.storage.set_setting("widgets_only", on)

    def _apply_takeover(self, checked):
        self.storage.set_takeover(bool(checked))
        self._call_window('apply_native_icons_setting')
        if checked:
            self.storage.ensure_desktop_clone(force=True)
            self._call_window('rebuild')

    def _apply_layout_mode(self, _index):
        mode = self.cmb_layout_mode.currentData()
        if not mode or mode == grid_flow.layout_mode(self.storage):
            return
        self.window.set_layout_mode(mode)

    def _apply_interaction(self, _index=0):
        """交互方式落盘（v9 V4-6）。

        优先走窗口的 `set_interaction_mode`：它负责清掉半途手势、退出多选
        标记——两处入口（设置页 / 桌面右键）必须做同一件事，各自写一份
        逻辑迟早漂移。
        """
        key = self.cmb_interaction.currentData()
        apply_win = getattr(self.window, "set_interaction_mode", None)
        if callable(apply_win):
            apply_win(key)
        else:
            interaction.set_mode(self.storage, key)
        self.lbl_interaction.setText(self._describe_interaction(key))

    def _apply_auto_clone(self, checked):
        self.storage.set_setting("auto_clone_desktop", bool(checked))
        if checked:
            self.storage.ensure_desktop_clone()
            self._call_window('rebuild')

    def _apply_auto_sync(self, checked):
        self.storage.set_setting("auto_sync_desktop", bool(checked))
        hook = getattr(self.window, "set_auto_sync", None)
        if hook is not None:
            hook(bool(checked))

    def _apply_folder_hover(self, checked):
        """文件夹悬停自动展开（v14 反馈 1）：纯设置项，图标每次悬停现读。"""
        self.storage.set_setting("folder_hover_expand", bool(checked))
        self._flush_storage()

    def _rescan(self):
        added, removed = self.window.rescan_desktop()
        QMessageBox.information(self, i18n.T("msg.rescan.title"),
                                i18n.T("msg.rescan.body",
                                       added=added, removed=removed))

    def _reset_layout(self):
        ret = QMessageBox.question(self, i18n.T("msg.reset_layout.title"),
                                   i18n.T("msg.reset_layout.body"))
        if ret == QMessageBox.Yes:
            self.storage.seed_desktop_clone(replace=True)
            self._call_window('rebuild')

    # ---------- v11：更新检测 / 关于作者 ----------
    def _build_update_card(self, body, lay):
        """「更新」：当前版本 + 检查按钮 + 一行状态。

        检查在后台线程（`updater.UpdateChecker`），UI 只接信号——网络慢
        时窗口照常可操作，用户看到的是"正在检查…"而不是假死。
        """
        card, c = self._card(body, i18n.T("settings.advanced.card.update"))
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        self.lbl_update = QLabel(i18n.T("settings.advanced.update.idle"))
        self.lbl_update.setObjectName("rowHint")
        self.lbl_update.setWordWrap(True)
        self.btn_check_update = QPushButton(
            i18n.T("settings.advanced.update.check"))
        self.btn_check_update.setCursor(Qt.PointingHandCursor)
        self.btn_check_update.clicked.connect(self._check_update)
        h.addWidget(self.lbl_update, 1)
        h.addWidget(self.btn_check_update, 0, Qt.AlignVCenter)
        self._row(c, card, i18n.T("settings.advanced.update.title"), box,
                  i18n.T("settings.advanced.update.hint"))

        self._update_url = updater.RELEASES_URL
        self.btn_open_release = QPushButton(
            i18n.T("settings.advanced.update.open"))
        self.btn_open_release.clicked.connect(
            lambda: self._open_url(self._update_url))
        self._row_open_release = self._row(
            c, card, i18n.T("settings.advanced.update.open_title"),
            self.btn_open_release)
        self._row_open_release.setVisible(False)
        lay.addWidget(card)
        self._updater = None

    def _check_update(self):
        """检查更新（防连点；失败也要把按钮放回来）。"""
        if self._updater is None:
            self._updater = updater.UpdateChecker(self)
            self._updater.finished.connect(self._on_update_finished)
            self._updater.failed.connect(self._on_update_failed)
        if self._updater.busy:
            return
        self.lbl_update.setText(i18n.T("settings.advanced.update.checking"))
        self.btn_check_update.setEnabled(False)
        self._updater.check()

    def _on_update_finished(self, info):
        self.btn_check_update.setEnabled(True)
        info = info or {}
        latest = str(info.get("version") or "")
        if info.get("newer"):
            self._update_url = str(info.get("url") or updater.RELEASES_URL)
            self.lbl_update.setText(
                i18n.T("settings.advanced.update.found", version=latest))
            self._row_open_release.setVisible(True)
        else:
            self.lbl_update.setText(
                i18n.T("settings.advanced.update.up_to_date", version=latest))
            self._row_open_release.setVisible(False)

    def _on_update_failed(self, message):
        self.btn_check_update.setEnabled(True)
        self.lbl_update.setText(
            i18n.T("settings.advanced.update.failed", error=str(message)))

    def _build_author_card(self, body, lay):
        """「关于作者」：一句介绍 + 抖音 / 哔哩哔哩两个入口。"""
        card, c = self._card(body, i18n.T("settings.advanced.card.author"))
        about = QLabel(i18n.T("settings.advanced.author.about"))
        about.setObjectName("rowHint")
        about.setWordWrap(True)
        c.addWidget(about)
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self._author_buttons = []
        for key, label, url in updater.AUTHOR_LINKS:
            btn = QPushButton(i18n.TL("settings.advanced.author." + key, label))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{background: rgba(76,194,255,32);"
                " border: 1px solid rgba(76,194,255,110);"
                " border-radius: 8px; padding: 5px 14px; font-size: 12px;"
                " color: rgb(150,214,255);}"
                "QPushButton:hover{background: rgba(76,194,255,56);}")
            btn.clicked.connect(lambda _c=False, u=url: self._open_url(u))
            h.addWidget(btn)
            self._author_buttons.append((key, btn))
        self._row(c, card, i18n.T("settings.advanced.author.links"), box)
        lay.addWidget(card)

    def _open_url(self, url: str):
        """用系统默认浏览器打开链接（失败不弹窗，静默即可）。"""
        try:
            QDesktopServices.openUrl(QUrl(str(url)))
        except Exception:      # noqa: BLE001
            pass
