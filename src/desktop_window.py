# -*- coding: utf-8 -*-
"""桌面整理主窗口：克隆图标网格、文件夹合并/解散、挂载到桌面层（Progman 上方的桌面带）。

原生桌面图标（SHELLDLL_DefView）在运行期间被隐藏，桌面文件由本窗口
克隆渲染——位置、大小、样式完全由第三方桌面掌控；退出时恢复原生图标。
"""
import os

from PySide6.QtCore import (QPoint, QProcess, QRect, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from . import appsource
from . import effects
from . import grid_flow
from . import i18n
from . import interaction
from . import label_style
from . import page_rules
from . import theme
from . import win32_layer
from .band_guard import BandGuard
from .folder_popup import FolderPopup, ScrimWindow
from .iconwidget import DWELL_MERGE_MS, MIME_TYPE, AppIcon
from .storage import Storage, entry_app
from .win32_layer import attach_to_desktop

# v10 B3（反馈 6「翻页太快」）：整页轮播时长。旧实现是每图标 280ms 弹簧；
# 420ms 是"能看清内容在移动"的下限（400ms 以下会被感知成瞬切）。
FLIP_MS = 420

# 悬浮兜底窗（挂载失败时）的固定不透明度——v14i 起 `opacity` 用户设置
# 退役，兜底形态不值得一个"挂载态永远无效"的滑条（原默认就是 0.95）
FLOATING_OPACITY = 0.95

WM_NCHITTEST = 0x84
HTTRANSPARENT, HTCLIENT = -1, 1

# 拥挤推让的悬停时长（ms）：拖到别人格子上停这么久，对方实时让开。
# 2026-10-20 用户反馈 F3："想合并却先被挤走"——旧值 260ms 比合并武装
# （DWELL_MERGE_MS）**短**，拖影抖动一下掉回"擦边"目标就被推走，
# 合并提示永远等不到。改成 dwell + 250ms：**提示先出现，推让后到**。
# 2026-10-21 反馈 7 起合并/挤走改由双区模型分流（iconwidget._hit_target
# 按拖影中心判核心区），本窗口只在**让位区**的 dragMove 里被驱动——
# 合并候选的拖动根本到不了这里（合成事件发给目标图标），"提示优先"
# 由路由结构保证；进核心区时桌面收到的 dragLeave 也会撤销推让
# （见 dragLeaveEvent），两者不会双触发。
#
# 2026-10-21 反馈 1（v7e）：700 → 470（dwell 350 + 120）。用户原话
# 「挤开反应可以更快，但不能快到老来不及弹出合并」——所以不是把推让
# 拉近到与提示同级，而是**两头一起提前**：提示 350（早 100ms）、推让
# 470（早 230ms），间距反而从 250 收到 120——反应更快、先后关系仍在。
# 双区模型下二者几何互斥，这个间距是"手感留白"而非并发保护。
PUSH_DWELL_MS = DWELL_MERGE_MS + 120

# 反馈 8（2026-10-21）：「图标位于屏幕边缘且左或右存在分页的时候，会在
# hover 图标的情况下，把图标拽到下一个分页，就跟手机的逻辑一样。」
# 拖动图标到翻页轴末端的边缘带内悬停这么久 -> 翻到邻页（手机 launcher
# 语义）。两条约束：
#   1. 必须短于 PUSH_DWELL_MS——同一边缘带上"先翻页、后推让"，否则两个
#      手势抢同一位置（翻页后旧页的推让武装也会被 _on_edge_flip 撤掉）；
#   2. 没有邻页（首页再往上 / 末页再往下）不武装，见 _edge_dir_at。
EDGE_FLIP_BAND = 24            # 指针进边缘带（px）才算"在边缘"
# 2026-10-21 反馈 1（v7e）：600 → 360。推让整体提前到 470 后，翻页若仍
# 停在 600 就会变成"先推让、后翻页"，边缘列的目标图标先被挤走再整页滑
# 动——顺序反了（翻页是视图动作，应该先发生）。360 为推让留 110ms 余量。
EDGE_FLIP_DWELL_MS = 360       # 悬停多久才翻页


class DesktopWindow(QWidget):
    visibilityChanged = Signal(bool)
    openSettingsRequested = Signal()
    openWidgetCenterRequested = Signal()   # v8 C3：打开组件库
    openAddAppsRequested = Signal()
    quitRequested = Signal()

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        self.attached = False
        self._attach_retries = 0
        self._attach_known_failed = False
        self._floating_flag_set = False
        self._popup = None
        self._scrim = None             # M5：文件夹幕布（弹窗下一层）
        self._folder_open_icon = None  # M5：处于打开态的源图标（AppIcon）
        # M6 分页：存储里的 row 始终是全局行（零迁移），页是纯视图概念。
        # _page 是 grid_flow 运行时页的委托 property（单一状态源——所有
        # 位置换算 cell_pos/pos_cell/snap_point/widget 都读它）；
        # 持久化在 settings["current_page"]。
        self._page = max(0, int(storage.get_setting("current_page") or 0))
        self._widget_layer = None      # main.py 装配时注入（翻页联动组件）
        self._selected = set()      # 多选：entry id 集合（单击/Ctrl/框选）
        # v9 V4：手机模式的多选态（长按菜单 ▸ 选择 进入）。与 `_selected`
        # 分工：`_selected` 是"选了谁"，`_selection_mode` 是"现在是不是在
        # 选"。分开的理由——Windows 模式也有选中集合（单击/Ctrl/框选），
        # 但那时单击必须"选中"，手机模式的单击必须"启动"，只有
        # `_selection_mode` 能区分这两种同集合不同语义的状态。
        self._selection_mode = False
        self._swipe_start = None    # 手机模式空白滑动起点（控件坐标）
        self._swipe_moved = False
        self._drag_active = False   # QDrag 进行中：命中测试临时全窗口接收
        self._drag_entry_id = None  # 当前正在拖的条目（多选拖动时判成员）
        # v10 C3：Dock 栏（独立分层窗，由 main.py 注入；None = 未启用/测试）
        self._dock = None
        # v10 B3：轮播翻页动画（一个标量驱动整页平移，见 goto_page）
        self._flip_anim = None
        self._flip_skip = None      # 拖拽中的条目：翻页帧不挪它的几何
        self._ole_active = False    # 外部文件 OLE 拖入进行中（拖放目标全窗口）
        self._band_start = None     # 橡皮筋起点（控件坐标）
        self._band_rect = None
        self._band_moved = False
        # v7 §3.2 / 反馈 21：网格原点取点态（等用户点一下桌面确定左上角）
        self._picking_origin = False
        self._grid_flash = False    # 取点 / 重置后短时显示网格线（ms 级反馈）
        # v6 §5.2：拖动中目标 cell 红框高亮（判定结果可见、可预期）
        # 2026-10-21 反馈 5：组件是多格占位，红框必须按 span 画整块
        # （_hl_span），否则 2×1 组件只框住 1 格，用户以为只检测一半
        self._hl_cell = None
        self._hl_span = (1, 1)
        # 反馈 8（v7e）：帧统计（paintEvent 自绘耗时 / 绘制面积），
        # 用于盯"全程 60 帧"——整屏重绘会直接把 last_area 顶到屏幕面积。
        self._frame_stats = {"count": 0, "last_ms": 0.0, "max_ms": 0.0,
                             "area": 0, "last_area": 0, "ring": [], "ts": []}
        self.icons = {}
        # v6 §4：图标当前构建所用的 IconLayout 签名（快照就绪前后 cell
        # 变化时检测到即重建——启动时 rebuild 早于 GridMetrics，旧 chrome
        # 路径尺寸 96×112 会大于 cell 75×92 互相重叠）
        self._icon_layout_sig = None
        # 实时让位（Live Reflow）状态（2026-10-18 M2 起用位移清单）：
        #   _preview_moves  拖动期间算出的位移清单 {id: (col,row)}（只改内存）
        #   _preview_drag   正在被拖的条目 id
        #   _layout_snap    拖动开始时的布局快照（取消回退 / 撤销栈用）
        #   _undo_stack     布局快照栈（Ctrl+Z，最多 20 步）
        self._preview_moves = None
        self._preview_drag = None
        self._layout_snap = None
        self._undo_stack = []
        self._drag_multi = False    # 多选拖动：走整体平移，不参与让位置换
        # 锚定组件拖动实时预览（2026-10-18 M4，与图标拖动同一套语义）：
        #   _widget_snap          进入预览时的布局快照（松手回退基准）
        #   _widget_preview_rect  组件新占位矩形（变化检测，防抖）
        self._widget_snap = None
        self._widget_preview_rect = None
        # 拥挤推让（2026-10-19 反馈 9）：拖到**别人的格子**上停一会儿，对方
        # 实时让开（手机 launcher 的"边缘重叠 → 挤走"）；停在图标中心则
        # 由目标图标自己 450ms 武装合并提示（AppIcon._dwell）。两个手势靠
        # 拖影与目标的重叠比例分流，见 iconwidget._drag_move。
        self._push_timer = QTimer(self)
        self._push_timer.setSingleShot(True)
        self._push_timer.setInterval(PUSH_DWELL_MS)
        self._push_timer.timeout.connect(self._push_occupied)
        self._push_cell = None
        self._push_dragged = None
        # 反馈 8：边缘悬停翻页（手机 launcher 语义）。拖动中指针进入
        # 翻页轴末端的边缘带并停住 EDGE_FLIP_DWELL_MS -> 翻到邻页。
        #   _edge_flip_dir   当前武装的方向（-1 上一页 / +1 下一页 / 0 未武装）
        #   _edge_flip_pos   最后一次处在边缘带内的指针（窗口坐标），
        #                    _on_edge_flip 靠它续武装（按住不放连续翻）
        self._edge_flip_timer = QTimer(self)
        self._edge_flip_timer.setSingleShot(True)
        self._edge_flip_timer.setInterval(EDGE_FLIP_DWELL_MS)
        self._edge_flip_timer.timeout.connect(self._on_edge_flip)
        self._edge_flip_dir = 0
        self._edge_flip_pos = None
        # WindowDoesNotAcceptFocus(WS_EX_NOACTIVATE)：永不被激活，
        # 保证桌面带窗口永远压在应用窗口之下，不会抢焦点 / 置顶。
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setWindowTitle(i18n.T("app.name"))   # v14h：遗留 Demo 名纠正
        # 桌面带守护：Win+D / 切屏等导致漂移时自动拉回桌面带
        self._band_guard = BandGuard(self, on_tick=self._on_guard_tick)
        # 桌面目录监听（v6 §7.4）：由 set_auto_sync 按设置启停，不在构造时启动
        # （测试 / 探针直接构造窗口，不该被动起文件监听）
        self._watcher = None
        # 低层键盘钩子（v11 反馈 31 / v14 反馈 2）：setup_desktop_hotkeys 时创建
        self._hotkeys = None
        # v14 反馈 11：纯净模式（双击空白处只留壁纸）。会话内有效
        self._zen = False
        self._zen_hidden = []
        self._zen_dock_was = True
        # 载入时规整一次，避免越界 / 重叠
        self.storage.compact_layout()
        self.rebuild(stagger=True)  # 首次显示错峰淡入

    # ---------- 模式 ----------
    def _organize_mode(self) -> bool:
        """桌面整理接管 = 「接管系统桌面」**且不是仅小组件模式**。

        v6 §7.2 的唯一入口；2026-10-21 反馈 7（v7e）追加第二项：
        用户原话"切换到仅小组件模式的时候，应该显示原生桌面图标，现在是
        空白"。旧实现让 widgets_only 继续藏着原生图标、只是不画自己的
        图标 —— 于是桌面上什么都没有。语义上"仅小组件"= **只保留小组件，
        桌面还给系统**：原生图标可见可点，网格/页点/框选/右键菜单全部退场。

        这一判定同时决定：原生图标显隐、命中分流（HTTRANSPARENT）、
        是否画页点与空态提示。
        """
        return self.storage.takeover() and not self._widgets_only()

    def _widgets_only(self) -> bool:
        """仅小组件模式（2026-10-21 反馈 9，v7e 反馈 7 修订）。

        开：只保留小组件——我们自己的图标不建、页点/空态提示不画、
        组件切无界（不占格）；**桌面还给系统**：原生图标恢复可见可点
        （`_organize_mode()` 因此返回 False，命中整窗穿透）。
        """
        return bool(self.storage.get_setting("widgets_only"))

    def set_widgets_only(self, on: bool):
        """开 / 关「仅小组件模式」（设置页唯一入口）。

        开：记住当前布局模式 → 切无界（组件无界）→ 重建（图标不再建）。
        关：恢复记住的模式 → 重建（图标回来）。
        模式没变时（本就无界 / prev=free）也要重建——图标显隐就在
        rebuild 里表达，这是单一落地处。
        """
        on = bool(on)
        if on == self._widgets_only():
            return
        self.storage.set_setting("widgets_only", on)
        if on:
            if not bool(self.storage.get_setting("show_widgets")):
                # 仅小组件 = 只显示组件：组件开关必须跟着开，否则桌面全空
                # （用户先关「显示桌面小组件」再开本模式的顺序）。
                self.storage.set_setting("show_widgets", True)
                if self._widget_layer is not None:
                    try:
                        self._widget_layer.set_visible(True)
                    except Exception:
                        pass
            self.storage.set_setting("widgets_only_prev_mode",
                                     grid_flow.layout_mode(self.storage))
            target = "free"
        else:
            target = str(self.storage.get_setting("widgets_only_prev_mode")
                         or "grid")
        switched = (target == "free") != grid_flow.free_mode(self.storage)
        if switched:
            self.set_layout_mode(target)     # 内部自带坐标换算 + rebuild
        self.rebuild(stagger=True)
        # 反馈 7（v7e）：仅小组件模式把桌面还给系统——原生图标按
        # `_organize_mode()`（接管 且 非 widgets_only）显隐，进出都要同步，
        # 否则进来一片空白、出去原生图标还压在壁纸上。
        self._sync_native_icons()
        self.storage.save()
        self.update()

    def _dev_mode(self) -> bool:
        """开发者模式：桌面渲染网格可视化边框，排查布局问题用。

        设置里可开关（SettingsDialog.chk_dev）。**默认关闭**：
        DEFAULT_SETTINGS["dev_mode"] 自 2026-10-19 起就是 False，
        且 storage._load() 会在新版首次启动时把旧 state 里遗留的 True
        一次性关掉（v10 A2，反馈 3——覆盖安装不清 %APPDATA%，否则用户
        一升级就看到满屏调试网格）。
        """
        return bool(self.storage.get_setting("dev_mode"))

    # ---------- 显示 / 隐藏 ----------
    def show_desktop(self):
        if self.attached or self._attach_known_failed:
            self._show_current_mode()
            return
        self._attach_retries = 2
        self._try_attach_once()

    def start_onboarding(self, replay: bool = False):
        """打开新手操作指引（首次启动自动调用；设置 ▸ 通用可重看）。

        入口放在桌面窗口上而不是 main.py：指引要高亮图标 / Dock / 组件，
        这些东西的归属者就是这里（`_dock` / `_widget_layer` 都是窗口注入的）。
        """
        from . import onboarding
        return onboarding.show_guide(self, self.storage, self._widget_layer,
                                     self._dock, replay=replay)

    def _try_attach_once(self):
        if self.attached:
            return
        screen = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(screen)
        # 先显示再挂载：挂载后的可见性 / z-order 检查才有意义（未显示的窗口恒为 false）
        self.show()
        self.update()  # 强制全窗重绘：命中填充（alpha=2）立即进入 surface
        self._sync_native_icons()
        if attach_to_desktop(self, layer=win32_layer.BAND_LAYER_DESKTOP):
            self.attached = True
            self._band_guard.track(self, layer=win32_layer.BAND_LAYER_DESKTOP)
            # M1：几何确定后重算网格（WidgetLayer 随后按新 origin 落位）
            self._update_grid_metrics()
            self.visibilityChanged.emit(True)
            return
        self._attach_retries -= 1
        if self._attach_retries > 0:
            # 桌面层可能还没就绪，稍后重试
            QTimer.singleShot(1500, self._try_attach_once)
        else:
            self._attach_known_failed = True
            self._enter_floating_mode()

    def _enter_floating_mode(self):
        """悬浮模式兜底（挂载失败时）：置顶 + 带面板背景。

        v14i：不透明度固定 FLOATING_OPACITY（原 `opacity` 用户设置退役——
        悬浮形态只是挂载失败的兜底，正常机器永远见不到，为它留一个
        "挂载态永远无效"的滑条只会造成困惑）。
        """
        screen = QApplication.primaryScreen().availableGeometry()
        if not self._floating_flag_set:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            self._floating_flag_set = True
        self.setGeometry(QRect(screen.left() + 60, screen.top() + 40,
                               min(screen.width(), 1180), min(screen.height(), 780)))
        self.setWindowOpacity(FLOATING_OPACITY)
        self.show()
        self._update_grid_metrics()
        self.visibilityChanged.emit(True)

    def _show_current_mode(self):
        """模式已确定后的再次显示（托盘切换显隐时走这里）。"""
        if self.attached:
            if self._floating_flag_set:
                self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
                self._floating_flag_set = False
            self.setGeometry(QApplication.primaryScreen().availableGeometry())
            self._update_grid_metrics()
        else:
            self._enter_floating_mode()
            return
        self.setWindowOpacity(1.0)
        self.show()
        self.update()  # 强制全窗重绘：命中填充（alpha=2）立即进入 surface
        self._sync_native_icons()
        # 重新显示后 Windows 可能改变 z-order，重新插回桌面带
        attach_to_desktop(self, layer=win32_layer.BAND_LAYER_DESKTOP)
        win32_layer.enforce_band_order()
        # 每次重新出场顺手清一遍孤儿组件窗口：它们看不见却是 HTCLIENT，
        # 会把组件上的点击吃掉（v10 反馈 28 的"盲区"）
        if self._widget_layer is not None:
            try:
                self._widget_layer.sweep_orphans()
            except (RuntimeError, AttributeError):
                pass
        self.visibilityChanged.emit(True)

    def hide_desktop(self):
        # M5：隐藏整理窗口前先收好文件夹弹窗与幕布（否则两者留在屏幕上）
        self._close_popup()
        self.hide()
        # 隐藏整理窗口时恢复原生图标：用户要回自己的桌面时不能什么都没有
        if not self._attach_known_failed:
            win32_layer.set_native_icons_visible(True)
        self.visibilityChanged.emit(False)

    def toggle(self):
        if self.isVisible():
            self.hide_desktop()
        else:
            self.show_desktop()

    # ---------- 原生桌面图标 ----------
    def _sync_native_icons(self):
        """按「接管系统桌面」开关隐藏 / 恢复系统原生桌面图标（克隆模式核心）。

        反馈 7：判定必须走 `_organize_mode()`，不能直读 takeover——仅小组件
        模式下接管开着但桌面要给回系统（原生图标可见、可点）。
        """
        hide = self._organize_mode()
        win32_layer.set_native_icons_visible(not hide)

    def apply_native_icons_setting(self):
        """设置 / 托盘里切换桌面整理模式后调用。

        整理模式：隐藏原生图标，窗口全盘接管。
        原生模式：恢复原生图标；窗口保持显示（全透明 + 空白区命中穿透），
        手动添加的图标与「从资源管理器拖入」照常工作。
        """
        self._sync_native_icons()
        self.show_desktop()
        self.rebuild(stagger=True)  # 模式切换：图标错峰淡入的仪式感

    def _on_guard_tick(self):
        """守护周期回调：保持原生图标隐藏（explorer 重启等会令其重新可见）；
        M1 附加：工作区几何变化（DPI / 分辨率 / 任务栏位置变化，
        WM_SETTINGCHANGE/WM_DISPLAYCHANGE 的轮询兜底）时重算网格。"""
        if self._organize_mode() and self.isVisible():
            # 三重巡检（2026-10-19）：
            #   1) 缓存标志——进程内我们自己知道的隐藏状态；
            #   2) 图标列表实查——explorer 重启 / 重建 SysListView32 后句柄
            #      会换，缓存标志仍是 True 却已经露出图标；
            #   3) 第三方桌面层——腾讯桌面整理等会把自绘图层重新 SHOW 回来。
            if (not win32_layer.native_icons_hidden()
                    or win32_layer.native_icon_list_visible()
                    or win32_layer.third_party_overlays_visible()):
                win32_layer.set_native_icons_visible(False)
        if self.attached and self.isVisible():
            geo = QApplication.primaryScreen().availableGeometry()
            if (geo.width(), geo.height()) != (self.width(), self.height()):
                self.setGeometry(geo)
                self._update_grid_metrics()

    def paintEvent(self, event):
        # 反馈 8（v7e）：帧耗时探针。只测本窗口自绘耗时（不含 DWM 合成），
        # 但足以定位"每次 update 都整屏刷"这类结构性浪费——测试断言
        # 红框换格时**绘制面积**远小于全屏，就是这条链的守门人。
        import time as _time
        t0 = _time.perf_counter()
        self._paint_frame(event)
        self._note_frame((_time.perf_counter() - t0) * 1000.0, event.rect())

    def _note_frame(self, ms: float, rect):
        """记录一帧绘制耗时 / 面积（环形缓冲，零依赖，恒开）。"""
        import time as _time
        st = self._frame_stats
        st["count"] += 1
        st["last_ms"] = ms
        st["max_ms"] = max(st["max_ms"], ms)
        st["area"] += int(rect.width()) * int(rect.height())
        st["last_area"] = int(rect.width()) * int(rect.height())
        ring = st["ring"]
        ring.append(ms)
        if len(ring) > 120:
            del ring[0]
        ts = st["ts"]
        ts.append(_time.perf_counter())
        if len(ts) > 120:
            del ts[0]

    def frame_stats(self) -> dict:
        """帧统计快照（探针 / 测试 / 排查卡顿用）。

        fps 是**重绘帧率**（桌面静止时没有重绘，读数会很低/为 0，属正常）；
        动画/拖动期间它才是有效指标。峰值 max_ms 是进程内累计最大自绘耗时，
        超过 16.7ms 的那一帧就是会掉帧的帧。
        """
        import time as _time
        st = self._frame_stats
        ring = sorted(st["ring"])
        p95 = ring[int(len(ring) * 0.95)] if ring else 0.0
        n = max(1, len(ring))
        ts = st["ts"]
        fps = 0.0
        if len(ts) >= 2 and (_time.perf_counter() - ts[-1]) < 0.5:
            span = ts[-1] - ts[0]
            if span > 0.001:
                fps = (len(ts) - 1) / span
        return {"count": st["count"], "last_ms": st["last_ms"],
                "max_ms": st["max_ms"], "avg_ms": sum(ring) / n,
                "p95_ms": p95, "last_area": st["last_area"],
                "total_area": st["area"], "fps": fps}

    def _paint_frame(self, event):
        hl = self._hl_cell is not None
        if self.attached:
            # 命中填充：分层窗口（WA_TranslucentBackground + WS_EX_LAYERED）的
            # 逐像素 alpha 命中测试会跳过全透明像素——WM_NCHITTEST 根本发不到
            # 本窗口，导致拖到空白格 drop 丢失、空白区按下点击穿透（橡皮筋不
            # 启动）。全窗铺一层 alpha=2（0.8% 不透明度，视觉不可见）使整个
            # surface 可被系统命中；「是否吃事件」仍由 nativeEvent 策略决定
            # （原生模式空闲对非图标区返回 HTTRANSPARENT，穿透不受影响）。
            #
            # 反馈 7（v7e）：仅小组件模式**一片都不画**。alpha=0 的像素系统
            # 不会命中——本窗口整块变成"不存在"，原生桌面图标照常可点，
            # 组件是独立顶层窗口、各自吃自己的矩形。
            if not self._widgets_only():
                self._paint_hit_fill()
            # 空态提示只在整理模式画：原生模式下桌面是系统原生的，不能盖任何东西；
            # 仅小组件模式（反馈 9）也不画——没有图标就没有"空空如也"
            if (not self.icons and self._organize_mode()
                    and not self._widgets_only()):
                self._paint_empty_hint(event)
            if self._band_rect is not None:
                self._paint_band(event)
            if self._organize_mode() and not self._widgets_only():
                if self._dev_mode() or self._grid_flash:
                    # force_grid：取点/重置后那一下"闪网格"与开发者开关无关，
                    # 它是一次性反馈（v11：分项开关不能把这条反馈也关掉）
                    self._paint_grid_debug(force_grid=self._grid_flash)
                if not self._zen:
                    self._paint_dots()
            if hl:
                self._paint_highlight(event)   # v6 §5.2：目标格红框置顶
            if self._picking_origin:
                if not self._dev_mode() and not self._grid_flash:
                    # 取点态必须看得见网格（否则用户不知道在点哪儿）
                    self._paint_grid_debug(force_grid=True)
                self._paint_pick_hint()        # 提示条最后画（不能被网格盖住）
            return  # 除上述元素外全透明，融入壁纸
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(theme.SURFACE)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect().adjusted(6, 6, -6, -6), theme.RADIUS_PANEL, theme.RADIUS_PANEL)
        p.setPen(theme.TEXT)
        p.setFont(theme.ui_font(13))
        p.drawText(24, 30, "桌面整理 Demo · 悬浮模式（未能挂载桌面层，功能不受影响）")
        if         self._organize_mode() and not self._widgets_only():
            # 悬浮兜底同样认"取点闪网格"：挂载失败时用户也会从设置里取点，
            # 那一下必须看得见网格（与附着分支同一判据）。
            if self._dev_mode() or self._grid_flash:
                self._paint_grid_debug(force_grid=self._grid_flash)
            if not self._zen:
                self._paint_dots()
        if hl:
            self._paint_highlight(event)

    def _paint_pick_hint(self):
        """取点态提示条：屏幕顶部居中胶囊「点击确定网格左上角 · Esc 取消」。

        不用 toast/子窗口：分层窗口里多一个顶层窗口就多一份命中/z-order 负担，
        自绘在本窗口最上层最省事。
        """
        text = "点击确定网格左上角位置  ·  Esc 取消"
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        font = theme.ui_font(13)
        font.setBold(True)
        p.setFont(font)
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 34
        h = 34
        r = QRect((self.width() - w) // 2, 18, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 180))
        p.drawRoundedRect(r, h // 2, h // 2)
        p.setPen(QColor(255, 255, 255, 235))
        p.drawText(r, Qt.AlignCenter, text)

    def _paint_highlight(self, event):
        """v6 §5.2：目标格预览框（2px 描边 + 8% 填充，圆角与网格语言一致）。

        2026-10-21 反馈 5：按 `_hl_span` 画**整块**占位矩形（组件 2×1 就
        框 2 格、3×4 就框 12 格）——单格红框会让用户以为只检测到一格，
        左侧格子"不碰撞"的观感正是这么来的。

        v14 反馈 13：颜色 / 填充浓度 / 粗细 / 圆角全部进设置 ▸ 图标
        （`label_style.preview_style()` 是唯一数据源，这里不再硬编码红）。
        """
        col, row = self._hl_cell
        cs, rs = self._hl_span
        x, y = grid_flow.cell_pos(self.storage, col, row)
        cw, ch = grid_flow.cell_size(self.storage)
        w = cw * cs + max(0, cs - 1) * 0
        h = ch * rs + max(0, rs - 1) * 0
        pv = label_style.preview_style(self.storage)
        if not pv.enabled:
            return          # 设置 ▸ 图标 ▸ 预览框可整体关闭（反馈：不需要指示框）
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        edge = QColor(pv.color)
        edge.setAlphaF(1.0)
        fill = QColor(pv.color)
        fill.setAlphaF(max(0.0, min(1.0, pv.fill_alpha)))
        p.setPen(QPen(edge, pv.width))
        p.setBrush(fill)
        p.drawRoundedRect(QRect(x + 1, y + 1, w - 2, h - 2), pv.radius, pv.radius)

    def _paint_hit_fill(self):
        """全窗 alpha=2 命中填充（视觉不可见，使透明像素也可被系统命中测试选中）。"""
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 2))
        p.end()

    def _paint_band(self, event):
        """橡皮筋框选矩形（仅整理模式会绘制：原生模式空白区事件已穿透）。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._band_rect
        p.setPen(QPen(theme.ACCENT, 1.2))
        p.setBrush(QColor(theme.ACCENT.red(), theme.ACCENT.green(), theme.ACCENT.blue(), 40))
        p.drawRoundedRect(r, 4, 4)

    # ---------- 橡皮筋框选（整理模式；原生模式事件已穿透给真实桌面） ----------
    def mousePressEvent(self, e):
        # v7 §3.2：取点态优先——**必须在最前面**，否则这一次按下会被当作
        # 框选起点 / 翻页 / 关弹窗，用户点了桌面却只关掉一个弹窗。
        if self._picking_origin:
            self._finish_pick(e.position().toPoint())
            return
        # 新一次按下必然不在拖动中：清掉可能因信号丢失而卡住的拖动状态
        # （_drag_active 卡死会让 nativeEvent 全窗口 HTCLIENT，桌面点不动；
        #  _drag_multi 卡死会让单选拖动也走多选平移分支）
        self._drag_active = False
        self._drag_multi = False
        # 新一次按下：上一次可能因信号丢失留下的滑动起点要清掉
        self._swipe_start = None
        self._swipe_moved = False
        # 点桌面 = 退出组件编辑态（安卓语义：点空白结束编辑）
        if self._widget_layer is not None:
            try:
                self._widget_layer.exit_all_edit()
            except RuntimeError:
                pass
        # 文件夹弹窗开着时点桌面空白处 = 关闭弹窗（iOS 文件夹语义；
        # 弹窗自身 NOACTIVATE 收不到 Esc，× 按钮 + 这里 + 再点图标共三条路径）
        if self._popup is not None and e.button() == Qt.LeftButton:
            self._close_popup()
            return
        # M6：页点点击翻页（整理模式可点；原生模式 dots 不画也不命中——
        # 空白区事件穿透真实桌面，翻页交给用户切模式后使用）
        if e.button() == Qt.LeftButton and self._organize_mode():
            pts = self._dot_centers()
            if pts:
                dr = self._dots_rect()
                if dr is not None and dr.contains(e.pos()):
                    # 就近页点：圆心水平距离最小者
                    best = min(range(len(pts)),
                               key=lambda i: abs(pts[i].x() - e.pos().x()))
                    self.goto_page(best)
                    return
        if e.button() == Qt.LeftButton and self._organize_mode():
            if interaction.is_mobile(self.storage):
                # v9 V4-5：手机模式空白处不是框选，而是"整页滑动翻页"的起点。
                # 两套手势不能共存：同一个拖动动作到底是画选框还是翻页，
                # 没有第三种信息可以分辨（用户要的就是手机那种）。
                self._swipe_start = e.pos()
                self._swipe_moved = False
            else:
                self._band_start = e.pos()
                self._band_rect = None
                self._band_moved = False

    def mouseDoubleClickEvent(self, e):
        """v14 反馈 11：双击桌面空白处 = 纯净模式（只留壁纸）。

        图标 / 组件 / Dock / 页点全部收起，再双击一次原样恢复。事件只会
        在**空白区**到达本窗口（图标和组件是各自的子窗口，自己吃掉），
        所以这里不需要再做命中判定。
        """
        if e.button() != Qt.LeftButton:
            return
        self.toggle_zen()

    def toggle_zen(self, on: bool = None):
        """纯净模式开关（会话内有效，不写盘——重启回到正常桌面）。"""
        on = (not self._zen) if on is None else bool(on)
        if on == self._zen:
            return
        self._zen = on
        if on:
            self._zen_hidden = [ic for ic in self.icons.values() if ic.isVisible()]
            for ic in self.icons.values():
                ic.hide()
            dock = getattr(self, "_dock", None)
            if dock is not None:
                # hide() 会同步清掉可见标志，必须先记录再隐藏，
                # 否则退出纯净模式时 dock.show() 被恒 False 的条件挡住
                self._zen_dock_was = dock.isVisible()
                dock.hide()
            layer = getattr(self, "_widget_layer", None)
            if layer is not None:
                layer.set_visible(False)
        else:
            for ic in getattr(self, "_zen_hidden", []):
                try:
                    ic.show()
                except RuntimeError:      # noqa: BLE001 —— 已销毁的图标
                    pass
            dock = getattr(self, "_dock", None)
            if dock is not None and getattr(self, "_zen_dock_was", True):
                dock.show()
            layer = getattr(self, "_widget_layer", None)
            if layer is not None:
                layer.set_visible(True)
            self._cull_page_icons()
        self.update()
        from .tray import notify_via_tray
        notify_via_tray(
            "纯净模式" if on else "已退出纯净模式",
            "桌面已清空，只留壁纸；双击空白处恢复" if on else "图标、组件与 Dock 已恢复")

    def mouseMoveEvent(self, e):
        if self._swipe_start is not None:
            if not (e.buttons() & Qt.LeftButton):
                return
            delta = e.pos() - self._swipe_start
            axis = grid_flow.page_axis(self.storage)
            main = delta.x() if axis != grid_flow.PAGE_AXIS_VERTICAL else delta.y()
            if abs(main) >= interaction.SWIPE_PX:
                self._swipe_moved = True
                # 够一格就翻，并把起点前移一页：一次长滑可以连翻多页
                # （手机上也是这个手感），又不会因为松手晚而只翻一页。
                step = -1 if main > 0 else 1
                self.goto_page(self._page + step)
                if axis != grid_flow.PAGE_AXIS_VERTICAL:
                    self._swipe_start = QPoint(self._swipe_start.x()
                                               + step * interaction.SWIPE_PX,
                                               self._swipe_start.y())
                else:
                    self._swipe_start = QPoint(self._swipe_start.x(),
                                               self._swipe_start.y()
                                               + step * interaction.SWIPE_PX)
            return
        if self._band_start is None or not (e.buttons() & Qt.LeftButton):
            return
        if (e.pos() - self._band_start).manhattanLength() >= QApplication.startDragDistance():
            self._band_moved = True
        if self._band_moved:
            old = self._band_rect
            self._band_rect = QRect(self._band_start, e.pos()).normalized()
            # 反馈 8：框选也走脏区（旧框 ∪ 新框），不再整屏重绘
            r = self._band_rect
            if old is not None:
                r = r.united(old)
            self.update(r.adjusted(-2, -2, 2, 2))

    def mouseReleaseEvent(self, e):
        if self._swipe_start is not None:
            if e.button() != Qt.LeftButton:
                return
            moved = self._swipe_moved
            self._swipe_start = None
            self._swipe_moved = False
            # 手机语义：多选态点空白 = 退出多选（且不启动任何应用）；
            # 非多选态点空白 = 取消选中（与 Windows 模式一致）
            if not moved:
                if self._selection_mode:
                    self.exit_selection_mode()
                else:
                    self._clear_selection()
            return
        if e.button() != Qt.LeftButton or self._band_start is None:
            return
        if self._band_moved and self._band_rect is not None:
            self._select_in_rect(self._band_rect)
        else:
            self._clear_selection()
        self._band_start = None
        self._band_rect = None
        self._band_moved = False
        self.update()

    def wheelEvent(self, e):
        """M6：整理模式下滚轮翻页（与页点互补的快捷路径）。

        阈值 15（1/8 度）= 普通滚轮一格的 1/8：高精度滚轮单次增量小
        也能翻页，又不至于被触摸板微抖误触。已到首/末页时消费事件
        不再传递（没有上层需要）。原生模式不拦——滚轮还给真实桌面。
        """
        if self._organize_mode() and not self._zen:
            # B7：纯净模式下不翻页——图标都隐藏着，翻了也看不见，
            # 反而会把图标 show() 出来穿透纯净态
            dy = e.angleDelta().y()
            dx = e.angleDelta().x()
            # 左右翻页模式下触控板横向滑动同样翻页（y 仍兼容鼠标滚轮）
            delta = dy if abs(dy) >= abs(dx) else dx
            if abs(delta) >= 15:
                self.goto_page(self._page + (-1 if delta < 0 else 1))
            e.accept()
            return
        super().wheelEvent(e)

    def _paint_empty_hint(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(theme.TEXT_FAINT)
        p.setFont(theme.ui_font(13))
        p.drawText(self.rect(), Qt.AlignCenter,
                   "桌面文件会显示在这里\n从资源管理器把文件拖进来，或右键托盘「添加应用」")

    # ---------- 命中测试：分层放行策略 ----------
    def nativeEvent(self, eventType, message):
        """桌面模式下本窗口铺满全屏且基本全透明，命中测试分四层：
        （前提：paintEvent 的 alpha=2 命中填充保证 WM_NCHITTEST 能发到本窗口，
          否则分层窗口会跳过全透明像素，本策略根本没有执行机会）
        - 图标附近（含边距）-> HTCLIENT：本窗口处理（点击/拖动/我们的菜单）
        - 拖拽进行中          -> 全窗口 HTCLIENT：OLE 拖放的命中判定必须能选中
                                  本窗口，否则图标永远落不回桌面（拖动失效根因）
        - 外部文件拖入进行中  -> 全窗口 HTCLIENT：从资源管理器拖文件放到空白区
        - 整理模式            -> 全窗口 HTCLIENT：框选、自己的右键菜单
        - 原生模式空闲        -> HTTRANSPARENT：空白区点击/右键/框选全部落到
                                  真实桌面（SHELLDLL_DefView），原生交互零损失
        """
        if eventType in (b"windows_generic_MSG", "windows_generic_MSG"):
            try:
                import ctypes
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
            except Exception:
                return super().nativeEvent(eventType, message)
            if msg.message == WM_NCHITTEST:
                if self._drag_active or self._ole_active or self._picking_origin:
                    # 取点态：全窗接收，否则原生模式下空白区是 HTTRANSPARENT，
                    # 用户点桌面根本传不到本窗口（"定位了没反应"）
                    return True, HTCLIENT
                x = ctypes.c_int16(msg.lParam & 0xFFFF).value
                y = ctypes.c_int16((msg.lParam >> 16) & 0xFFFF).value
                pt = QPoint(x, y)
                # 小组件矩形：让出命中（反馈 1）。带内层号已经保证组件压在
                # 整理窗口之上，这里是**兜底**——层序尚未生效的那一两百毫秒
                # 里，也不该让全屏窗口把组件上的点击 / 右键吞掉。
                if self._hit_widget(pt):
                    return True, HTTRANSPARENT
                if self._organize_mode():
                    return True, HTCLIENT
                pad = int(round(14 * self.storage.ui_scale()))
                for icon in self.icons.values():
                    r = QRect(icon.mapToGlobal(QPoint(0, 0)), icon.size())
                    if r.adjusted(-pad, -pad, pad, pad).contains(pt):
                        return True, HTCLIENT
                return True, HTTRANSPARENT
        return super().nativeEvent(eventType, message)

    def _logical_point(self, pt):
        """WM_NCHITTEST 的 lParam 是**物理像素**，Qt 几何是**逻辑坐标**。

        v10 反馈 28：125% 缩放下两者差 25%，`_hit_widget` 直接拿物理点
        比逻辑矩形，兜底放行区只盖住组件左上角的一小块——其余部分被本
        窗口 `HTCLIENT` 吞掉，表现就是"组件上有一块怎么点都没反应"。
        """
        try:
            dpr = float(self.devicePixelRatioF()) or 1.0
        except (RuntimeError, AttributeError):
            dpr = 1.0
        if abs(dpr - 1.0) < 1e-6:
            return pt
        return QPoint(int(round(pt.x() / dpr)), int(round(pt.y() / dpr)))

    def _hit_widget(self, global_pos) -> bool:
        """命中点是否落在某个可见小组件领地内（nativeEvent 兜底放行用）。

        判据两级：窗口矩形（视觉真相）+ span 盒（领地）——卡片按填充比例
        比盒子小一圈，那圈缝也不该由全屏整理窗口吞掉。
        """
        layer = self._widget_layer
        if layer is None:
            return False
        try:
            items = list(layer.widgets.values())
        except Exception:
            return False
        pt = self._logical_point(global_pos)
        from . import widget_geom
        for w in items:
            try:
                if not w.isVisible():
                    continue
                r = QRect(w.mapToGlobal(QPoint(0, 0)), w.size())
                if r.contains(pt):
                    return True
                e = getattr(w, "entry", None) or {}
                if not (e.get("anchored") and "col" in e and "row" in e):
                    continue
                span = grid_flow.widget_span(self.storage, e)
                bw, bh = widget_geom.span_box(self.storage, span)
                bx, by = grid_flow.cell_pos(self.storage, int(e["col"]),
                                            int(e["row"]))
                if QRect(bx, by, bw, bh).contains(pt):
                    return True
            except (RuntimeError, TypeError, ValueError):
                continue
        return False

    # ---------- 网格几何（几何定义统一在 grid_flow，勿在此重复实现） ----------
    def _cell(self):
        return grid_flow.cell_size(self.storage)

    def _dock_inset(self):
        """Dock 条带占用的四边内缩 (l,t,r,b)——网格可用区的唯一扣除入口。

        Dock 关闭 / 模块异常时一律全 0（网格恢复满工作区，绝不因 Dock
        出问题把桌面布局搞坏）。
        """
        from . import dock as dock_mod
        try:
            return dock_mod.storage_inset(self.storage)
        except Exception:      # noqa: BLE001
            return (0, 0, 0, 0)

    def _origin(self):
        return grid_flow.origin()

    def _update_grid_metrics(self):
        """M1（指导文档 §1）：按当前窗口尺寸重算 GridMetrics 写入
        grid_flow——origin()/columns 等共享几何随之更新为按工作区
        四边等距铺满。已有图标只 move 到新格点（不 rebuild：保留
        动效状态与选中态，也避免闪烁）。

        M6：rows_per_page 随几何变化（DPI/分辨率/任务栏）后，总页数
        可能变少——越界的当前页夹紧回末页。

        v6 §4/A2 连带（probe23 S0 抓到的真 bug）：启动时 __init__ 的
        rebuild 早于快照就绪，图标走旧 chrome 路径（96×112 > cell
        75×92，同列图标互相重叠）；快照就绪后 cell 变 -> 三档 layout
        也变，只 move 不重建尺寸永远是旧的。快照就绪且 layout 签名
        变化时整体重建一次（仅发生在启动 / DPI 变化，日常拖拽位移
        不走本路径，实例保留语义不受影响）。
        """
        before = grid_flow.current_metrics()
        # v10 C3：Dock 条带从可用区里扣掉——inset 是唯一扣除入口，
        # 具体四元组由 dock.storage_inset() 算（本类不认识 Dock 尺寸模型）
        grid_flow.set_grid(self.storage, (self.width(), self.height()),
                           inset=self._dock_inset())
        after = grid_flow.current_metrics()
        if self._dock is not None:
            # 条带贴边 + 沿副轴居中，窗口尺寸变了要跟着重排（幂等）
            self._dock.apply_geometry((self.width(), self.height()))
        self._clamp_page()
        layout = self._icon_layout()
        if layout != self._icon_layout_sig:
            self._icon_layout_sig = layout
            if self.icons:
                self.rebuild()
            return
        if before is not None and before == after:
            return
        for eid, icon in self.icons.items():
            entry = self.storage.find_entry(eid)
            if entry is not None:
                icon.move(*self._entry_pos(entry))
        self._cull_page_icons()
        if self._widget_layer is not None:
            self._widget_layer.sync_page(self._page)
        self.update()

    def refresh_grid(self):
        """网格参数（列数 / 档位 / 图标像素）变更后立即重算并重排。

        反馈 2（2026-10-19）：「软件调整完网格尺寸之后，应该自动刷新而不是
        需要手动关闭才开启接管」。旧设置路径只调 rebuild()，而 rebuild
        读的是 `grid_flow.current_metrics()` **旧快照**——cell / cols 没重算，
        图标位置尺寸全是老值；用户只能关掉「接管系统桌面」再打开
        （走 _try_attach_once -> _update_grid_metrics）才看到变化。

        这里把「重算快照 + 按新几何重建 + 组件重新落位」收成一个入口，
        设置面板与桌面右键的图标大小入口统一调用。
        """
        # 用惰性哨兵让 _update_grid_metrics 走 rebuild 分支（签名必不等），
        # 避免"先重算再额外 rebuild 一次"的双重建
        self._icon_layout_sig = object()
        self._update_grid_metrics()
        if self._widget_layer is not None:
            self._widget_layer.apply_layout_mode()
        self.update()

    # ---------- 网格原点：手动定位 / 重置（v7 §3.2 / 反馈 21） ----------
    def current_origin(self):
        """当前网格原点 (x, y)——读快照，未就绪时回退桌面默认边距。"""
        return grid_flow.origin()

    def _begin_pick_origin(self):
        """进入取点态：下一次左键按下即网格左上角（落点在 _finish_pick）。

        不要求已挂载桌面层：悬浮兜底时窗口也是铺满工作区的普通窗口，
        点上去照样能取点（挂载与否都不影响本流程）。
        """
        self._picking_origin = True
        self.setCursor(Qt.CrossCursor)
        self.update()                       # 画提示条 + 网格
        return True

    def _cancel_pick_origin(self):
        """退出取点态（Esc / 右键菜单再次触发）——不改 storage。"""
        self._picking_origin = False
        self.unsetCursor()
        self.update()

    def _finish_pick(self, pos):
        """取点完成：pos（控件坐标）落成手动原点 -> 重排 -> 落盘。"""
        x, y = int(pos.x()), int(pos.y())
        self._picking_origin = False
        self.unsetCursor()
        self.storage.set_grid_origin("manual", x, y)
        self.refresh_grid()                 # 重算快照 + 重建 + 组件落位
        self.storage.save()
        self._flash_grid_debug(600)
        return x, y

    def _reset_grid_origin(self):
        """重置网格位置：两键一起清空 -> 回居中（唯一重置入口）。"""
        self.storage.reset_grid_origin()
        self.refresh_grid()
        self.storage.save()

    def _flash_grid_debug(self, ms=600):
        """短时显示网格线（取点 / 重置后的几何反馈，不常驻）。

        dev_mode 常开时网格本来就画着，不必再闪。
        """
        if self._dev_mode():
            return
        self._grid_flash = True
        self.update()
        QTimer.singleShot(int(ms), self._end_grid_flash)

    def _end_grid_flash(self):
        self._grid_flash = False
        self.update()

    def resizeEvent(self, event):
        # M1：窗口几何变化（挂载后铺满工作区 / 悬浮兜底 / 显示器几何变化）
        # 时重算网格。未挂载时窗口尺寸无网格意义，跳过。
        super().resizeEvent(event)
        if self.attached:
            self._update_grid_metrics()

    def _entry_pos(self, entry):
        # M6：列夹紧 + 页内局部行映射统一在 grid_flow.cell_pos（DRY）；
        # v6 §1.3：双模式唯一入口（无界模式读 fx/fy + 页间偏移）
        return grid_flow.entry_pos(self.storage, entry)

    def _cell_at(self, pos):
        # M6：反算含当前页偏移（页内 y -> 全局 row），委托 grid_flow
        return grid_flow.pos_cell(self.storage, pos.x(), pos.y())

    def _drag_cell(self, fallback: QPoint):
        """v6 §5.2 落点判定：拖动图标框（浮动拖影矩形）vs 候选 cell 面积最大。

        拖影是拖动图标的视觉化身（位置 = 光标 - 抓取偏移），它的矩形
        就是规格说的「拖动图标框」。无拖影时退化为指针所在格
        （_cell_at 旧语义）——合成 drop / 外部文件 OLE 拖入 / 探针
        直调都走这条，保持既有行为不变。
        """
        icon = self.icons.get(self._drag_entry_id or "")
        ghost = getattr(icon, "_drag_ghost_w", None) if icon is not None else None
        if ghost is None:
            cell = self._cell_at(fallback)
        else:
            g = ghost.geometry()                # frameless 顶层：全局矩形
            tl = self.mapFromGlobal(g.topLeft())
            cell = grid_flow.cell_by_overlap(
                self.storage, (tl.x(), tl.y(), g.width(), g.height()))
        # dock_side_grid：Dock 条带占住的格子不放（无预览、松手回弹原位）
        if cell is not None:
            try:
                from . import dock as _dock
                if cell in _dock.side_grid_blocked(self.storage):
                    return None
            except Exception:      # noqa: BLE001
                pass
        return cell

    def _hl_rect(self, cell, span) -> QRect:
        """红框的窗口矩形（空 cell 返回空矩形）。绘制与脏区共用同一份定义。"""
        if cell is None:
            return QRect()
        cs, rs = span
        x, y = grid_flow.cell_pos(self.storage, cell[0], cell[1])
        cw, ch = grid_flow.cell_size(self.storage)
        return QRect(x - 3, y - 3, cw * max(1, int(cs)) + 6,
                     ch * max(1, int(rs)) + 6)

    def _set_highlight(self, cell, span=(1, 1)):
        """v6 §5.2：目标格红框。只在跨格/换跨度时重绘。

        span：占位格数（反馈 5）。组件是多格占位，红框要画成整块；
        图标永远是 1×1（默认值）。

        2026-10-21 反馈 8（v7e）：**只重绘两个框的并集**。旧实现调
        `self.update()`（全窗）——全屏分层窗口每次 update 都是一次整屏
        ARGB 合成，dragMove 每跨一格就来一次，正是"拖动时掉帧"的主因。
        红框之外那块像素根本没变（命中填充是整片均匀 alpha=2），
        只擦旧框 + 画新框即可。
        """
        span = tuple(span)
        if cell == self._hl_cell and span == self._hl_span:
            return
        old = self._hl_rect(self._hl_cell, self._hl_span)
        self._hl_cell = cell
        self._hl_span = span if cell is not None else (1, 1)
        new = self._hl_rect(self._hl_cell, self._hl_span)
        r = old.united(new)
        if r.isValid() and not r.isEmpty():
            self.update(r)
        elif old.isValid() and new.isValid():
            self.update()

    def _drag_pos(self, fallback: QPoint):
        """拖影左上角的窗口坐标（v6 §1.3 无界松手落点用）。

        与 _drag_cell 同一取源：浮动拖影 = 拖动图标的视觉化身。无拖影
        （合成 drop / 探针直调）退化为指针位置。
        """
        icon = self.icons.get(self._drag_entry_id or "")
        ghost = getattr(icon, "_drag_ghost_w", None) if icon is not None else None
        if ghost is None:
            return fallback
        return self.mapFromGlobal(ghost.geometry().topLeft())

    def _free_drop(self, ids, pos):
        """无界模式松手（v6 §1.3）：整块按拖影位移量平移到像素位。

        单选/多选同一条语义：主条目（_drag_entry_id，回退 payload 首个）
        落到 pos（夹紧进当前页画布），其余选中成员保持**相对偏移**平移
        （与 §5.0 多选的形状保持同语言）；row 同步为当前页载体。
        不吸附、不避让、可重叠；不走撤销栈外的任何重排。
        """
        page = grid_flow.current_page()
        rpp = grid_flow.rows_per_page()
        canvas_h = grid_flow.page_canvas_h(self.storage)
        primary = self._drag_entry_id if self._drag_entry_id in ids else ids[0]
        pent = self.storage.find_entry(primary)
        if pent is None:
            return
        picon = self.icons.get(primary)
        pw = picon.width() if picon is not None else 0
        ph = picon.height() if picon is not None else 0
        # 主条目新位置（夹紧进画布：x∈[0,W-w]，y∈[0,画布高-h]）
        nx = max(0, min(int(pos.x()), max(0, self.width() - pw)))
        ny = max(0, min(int(pos.y()), max(0, canvas_h - ph)))
        # 位移量 = 新位置 - 拖动前位置（拖动前位置在 entry.fx/fy，预览在
        # 无界模式下不改写布局，读到的就是拖动前的值）
        dx = nx - int(pent.get("fx", 0) or 0)
        dy = ny - int(pent.get("fy", 0) or 0)
        moved = {}
        for eid in ids:
            e = self.storage.find_entry(eid)
            if e is None:
                continue
            icon = self.icons.get(eid)
            w = icon.width() if icon is not None else 0
            h = icon.height() if icon is not None else 0
            e["fx"] = max(0, min(int(e.get("fx", 0) or 0) + dx,
                                 max(0, self.width() - w)))
            e["fy"] = max(0, min(int(e.get("fy", 0) or 0) + dy,
                                 max(0, canvas_h - h)))
            # v7 B2：页归属写 fpage，**不再改写 row**。row 是网格坐标，
            # 无界松手去动它等于把用户整理好的网格行号悄悄冲掉
            # （反馈 13「切回网格一堆 cell 重叠」的另一半病根）。
            e["fpage"] = page
            moved[eid] = (e["fx"], e["fy"])
        if not moved:
            return
        self._push_undo(self._layout_snap)
        self.storage.save()
        for eid, (fx, fy) in moved.items():
            icon = self.icons.get(eid)
            if icon is None:
                continue
            # 2026-10-21 反馈 2（v7e）：「无界模式移动图标还有一个奇怪的
            # 回弹动画」。落点就是拖影松手的那一格像素——拖影已经全程
            # 贴在指针下面，图标本体此时还在**起点**（拖动期间它以拖影
            # 为代表、本体不可见）。旧实现 animate_to 从起点弹到落点，
            # 弹簧曲线 0.8 阻尼带过冲 → 肉眼看到"回弹一下才停住"。
            # 正确语义：拖影消失的同时本体出现在落点，位移为零。
            # 先 move 再取消 ghost（顺序反了会先在起点闪一帧本体）。
            icon.move(int(fx), int(fy))
            icon.set_drag_ghost(False)
        self._cull_page_icons()
        self._clear_preview()
        self.update()

    def set_layout_mode(self, mode: str):
        """v6 §1.3：切换网格 / 无界模式（托盘 + 桌面右键入口）。

        双坐标换算（grid_flow.to_free_coords / to_grid_coords）后整体
        rebuild——图标尺寸体系（三档 layout ↔ 用户像素值）与组件占位
        语义都随模式变，只有重建能一次性换干净；翻页状态夹回合法页。

        2026-10-19 反馈 3：判定按**有效模式**（未接管时 grid 设置值也
        算无界）。用户主动切「网格模式」意味着要网格——网格必须由
        接管承载（原生图标不隐藏会出现两套图标），故自动开启接管。
        """
        mode = "free" if str(mode) == "free" else "grid"
        # 仅小组件模式（反馈 9）：组件必须无界，网格请求直接忽略——
        # 菜单里的布局入口在该模式下已隐藏，这里是护栏（设置页的布局
        # 下拉同样可能发来 grid）。
        if self._widgets_only() and mode != "free":
            return
        if (mode == "free") == grid_flow.free_mode(self.storage):
            return
        if mode == "grid" and not self.storage.takeover():
            self.storage.set_takeover(True)
            self._sync_native_icons()
        self.storage.set_setting("layout_mode", mode)
        if mode == "free":
            grid_flow.to_free_coords(self.storage)
        else:
            grid_flow.to_grid_coords(self.storage)
        self.storage.save()
        self.rebuild(stagger=True)      # 模式切换的仪式感：错峰淡入
        self._clamp_page()
        if self._widget_layer is not None:
            self._widget_layer.apply_layout_mode()
        self.update()

    # ---------- 分页（M6，指导文档 §7-M6） ----------
    # _page 委托 grid_flow 运行时页（单一状态源）：所有位置换算
    # （cell_pos/pos_cell/snap_point/widget_base/widget_layer）都经
    # grid_flow.current_page() 读同一份，杜绝双源漂移。
    @property
    def _page(self):
        return grid_flow.current_page()

    @_page.setter
    def _page(self, value):
        grid_flow.set_page(value)

    def set_widget_layer(self, layer):
        """注入 WidgetLayer（main.py 装配）：翻页时组件与图标联动滑动。"""
        self._widget_layer = layer

    # ---------- v10 C3：Dock 栏接线 ----------
    def set_dock(self, dock_window):
        """注入 Dock 窗口（main.py 装配）。

        Dock 是**独立分层窗**，不参与图标/组件的布局；它只通过
        `dock.storage_inset()` 影响网格可用区。这里挂引用是为了三件事：
        拖入判定（落点是否在条带内）、设置变更后重排、关闭时清理。
        """
        self._dock = dock_window
        if dock_window is not None:
            dock_window.apply_geometry((self.width(), self.height()))
        self.refresh_grid()

    def apply_dock_settings(self):
        """Dock 开关 / 位置 / 外观过变化：重排条带 + 重算网格。

        唯一入口（设置页只调它）：先让 Dock 自己落位（它读 storage 的
        enabled/position/外观），再 `refresh_grid()` 用新的 inset 重算几何。
        顺序不能反——先 refresh 会拿到旧条带厚度。

        2026-10-23 反馈 6（v10 A9）：位置切换"没生效"的根因是**分层窗口
        只改了 Qt 侧几何**——Dock 在桌面带里的 z-order 由 win32 维护，
        切到顶部/左右侧后窗口可能仍被旧位置的层序关系影响（Qt 的
        setGeometry 与 SetWindowPos 交替后偶发回退）。这里补一次
        `refresh_band()`：重新挂到桌面带并让层序归位，位置与层级一起刷新。
        """
        if self._dock is not None:
            try:
                self._dock.apply_geometry((self.width(), self.height()))
                self._dock.refresh_band()
            except (RuntimeError, AttributeError):
                # 窗口已被销毁（退出路径）——别让设置收尾炸掉整个程序
                self._dock = None
        self.refresh_grid()

    def apply_dock_style(self):
        """只刷新 Dock **外观**：重绘条带，不碰网格。

        v10 反馈 27：设置页拖透明度/圆角/边框滑条时走这里。旧路径一律调
        `apply_dock_settings()`，于是每个 valueChanged 都
        `refresh_band()`（win32 重挂 + 层序归位）+ `refresh_grid()`（删掉
        重建全部图标 QWidget）——主线程被拖死，还会在重建过程中崩溃。
        外观不改变条带几何，重绘一帧就够。
        """
        d = self._dock
        if d is None:
            return
        try:
            d.update()
        except (RuntimeError, AttributeError):
            self._dock = None

    # ---------- 图标文字样式（v14 反馈 3） ----------
    def _label_style(self):
        """当前标签样式（唯一入口：rebuild / _add_icon / 设置热更新都读它）。"""
        return label_style.label_style(self.storage)

    def apply_hover_style(self):
        """hover 高亮/动画设置变更（v14j）：全图标丢缓存重绘。"""
        for icon in list(self.icons.values()):
            try:
                icon.invalidate_hover_style()
            except (RuntimeError, AttributeError):
                pass
        self.update()

    def apply_label_style(self):
        """设置里改完文字样式：把新样式推进每个图标并重绘（不重建部件）。

        不走 `rebuild()`：那会删掉重建全部图标 QWidget，选中态 / 飞行中的
        拖拽 / 淡入动画全部作废（v10 反馈 27 同款事故）。标签样式只影响
        `_paint_label`，换对象 + update() 就够了。
        """
        st = self._label_style()
        for icon in list(self.icons.values()):
            try:
                icon.set_label_style(st)
            except (RuntimeError, AttributeError):
                pass     # 已销毁的图标（rebuild 进行中）直接跳过
        # 文件夹弹窗里的成员标签同样消费这份样式——弹窗是懒重建的，
        # 下一次 open 自然拿到新值；已开着的弹窗由它自己的 update 兜底。
        popup = getattr(self, "_popup", None)
        if popup is not None:
            try:
                popup.apply_label_style(st)
            except (RuntimeError, AttributeError):
                pass     # 弹窗已销毁（关闭动画中）直接跳过
        self.update()

    def apply_liquid_style(self):
        """液态玻璃设置变更：推进三个表面各重绘一帧（v14 反馈 9）。

        不走 rebuild / refresh_band：外观叠加层不改变任何几何，update()
        就够了——与 `apply_dock_style()` 同一条纪律（v10 反馈 27 的教训：
        每个 valueChanged 都重建会把主线程拖死）。
        """
        d = self._dock
        if d is not None:
            try:
                d.update()
            except (RuntimeError, AttributeError):
                self._dock = None
        layer = getattr(self, "_widget_layer", None)
        if layer is not None:
            for w in list(getattr(layer, "widgets", {}).values()):
                try:
                    w.update()
                except (RuntimeError, AttributeError):
                    pass     # 已销毁的组件直接跳过
        popup = getattr(self, "_popup", None)
        if popup is not None:
            try:
                popup.update()
            except (RuntimeError, AttributeError):
                pass         # 弹窗已销毁（关闭动画中）直接跳过
        self.update()

    def apply_dock_metrics(self, reflow_grid: bool = True):
        """Dock **尺寸**变化（图标间隔 / 内边距 / 悬停倍数）：重排条带。

        v11（反馈 29）：这三个值改的是条带厚度与长度，条带必须立刻按新
        尺寸落位（`apply_geometry` 只算几何 + setGeometry + update，很轻）；
        `reflow_grid=False` 时**不**重建网格图标——拖步进器/滑条时逐次
        重建会像 v10 反馈 27 那样把主线程拖死，网格交给设置页的防抖。

        与 `apply_dock_settings()` 的分工：那条路径还要 `refresh_band()`
        （win32 重挂 + 层序归位），只有开关/位置这类"会换边"的改动才需要。
        """
        d = self._dock
        if d is not None:
            try:
                d.apply_geometry((self.width(), self.height()))
            except (RuntimeError, AttributeError):
                self._dock = None
        if reflow_grid:
            self.refresh_grid()

    def _dock_hit(self, pos) -> bool:
        """窗口坐标 pos 是否落在 Dock 条带上（屏幕/全局坐标换算后比较）。"""
        d = self._dock
        if d is None:
            return False
        try:
            if not d.is_visible_bar():
                return False
            g = d.geometry()
            p = self.mapToGlobal(pos)
        except (RuntimeError, AttributeError):
            return False
        return g.contains(p)

    def _dock_accepts_drop(self, entry_id: str) -> bool:
        """把一条桌面条目塞进 Dock（拖放/转发共用）；返回是否被接收。"""
        d = self._dock
        if d is None:
            return False
        try:
            from . import dock as dock_mod
            entry = self.storage.find_entry(entry_id)
            if not dock_mod.entry_is_app(entry):
                return False
            if not d.add_item(dock_mod.item_from_entry(entry)):
                return False
            self.storage.save()
            return True
        except Exception:      # noqa: BLE001 —— 拖放失败不该炸桌面
            return False

    def _clamp_page(self):
        """总页数变少（DPI/分辨率变化、条目减少）后把越界当前页夹回。"""
        total = grid_flow.page_count(self.storage)
        if self._page > total - 1:
            self.goto_page(total - 1, animate=False)

    def set_page_axis(self, axis: str):
        """切换翻页方向（2026-10-19 反馈 5）：horizontal（默认）/ vertical。

        页容量不变，只换页与页之间的滑行轴。切换后立刻按新方向重排——
        无动画（直接 move），避免"从屏幕外飞进来"的错位感；组件走
        WidgetLayer.sync_page 与新方向对齐。
        """
        axis = "vertical" if str(axis).strip().lower() == "vertical" else "horizontal"
        self.storage.set_setting("page_axis", axis)
        for eid, icon in self.icons.items():
            entry = self.storage.find_entry(eid)
            if entry is not None:
                icon.move(*self._entry_pos(entry))
        if self._widget_layer is not None:
            self._widget_layer.sync_page(self._page)
        self.update()

    def goto_page(self, page, animate=True, skip_id=None):
        """翻到指定页：运行时页同步 + 持久化 + **整页轮播式平移**。

        v10 B3（反馈 6）：动画从"每个图标各起一条弹簧"改成**一个标量**
        （`grid_flow._flip`）驱动整页刚体平移——`progress` 从 0 走到 1，
        所有参与页的图标/组件读同一个 `flip_px`，于是当前页滑出、邻页
        滑入是同一段时间轴上的同一段位移（iOS launcher 的接续感）。
        `animate=False` 用于启动夹紧等无仪式场景（直接 move，不播动画）。
        `reduced_motion()` 同样走直接到位。

        skip_id（反馈 8）：翻页帧跳过该条目。拖拽中的图标由拖影代表
        （不可见），几何不该被翻页帧拉走。

        v6 §4 分页禁半截：桌面窗口 = 工作区大小，网格四边等距铺满，
        上下边距（约半格）放不下一整格图标——邻页图标静止态会从屏底/
        屏顶露半截（子控件只被裁到窗口边缘，裁不到网格带）。因此翻页
        期间可见集合被**收编成恰好两页**（`_show_flip_pages`，在动画
        开始前就生效，不依赖 `finished`——旧实现靠 finished 隐藏离页
        图标，弹簧过冲的最后一帧仍露一角，就是"最右图标残留"）。
        """
        page = max(0, min(int(page), grid_flow.page_count(self.storage) - 1))
        if page == self._page:
            return
        old = self._page
        direction = 1 if page > old else -1
        # 连翻（上一段还没滑完就再翻）：先按**此刻的进度**把位置落定，
        # 再从这一帧接着滑——否则会跳回起点，看起来"抖一下再滑"。
        if self._flip_anim is not None:
            self._flip_anim.stop()
            self._apply_flip_frame()
            grid_flow.end_flip()
            self._flip_anim = None
        self._page = page
        grid_flow.set_page(page)
        self.storage.set_setting("current_page", page)
        self._flip_skip = skip_id

        playing = animate and not effects.reduced_motion()
        if playing:
            # v10 B3：**一个标量驱动整页平移**。帧位置 = 落定格点 + `flip_px`，
            # 图标与组件读同一份 → 同向同速同曲线，这就是反馈 6 要的接续感。
            grid_flow.begin_flip(page, direction)
            self._show_flip_pages((old, page))
            anim = QVariantAnimation(self)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setDuration(effects.dur(FLIP_MS))
            anim.setEasingCurve(effects.spring_curve(1.0, FLIP_MS))
            anim.valueChanged.connect(self._on_flip_step)
            anim.finished.connect(self._on_flip_done)
            self._flip_anim = anim
            anim.start()
            # 立刻画一帧起点（新页图标在屏外、旧页在原位），避免"先跳一格再滑"
            self._apply_flip_frame()
        else:
            grid_flow.end_flip()
            self._settle_flip()
        if self._widget_layer is not None:
            if playing:
                self._widget_layer.apply_flip_frame()
            else:
                self._widget_layer.settle_flip()
        self.update()

    # ---------- 轮播翻页帧（v10 B3） ----------
    def _apply_flip_frame(self):
        """按当前 `flip_progress` 把可见图标摆到帧位置（只 move，不播动画）。

        位置 = 落定格点（`_entry_pos`，逻辑几何全程静止）+ `flip_px`
        （纯视觉瞬移）。所有可见图标读同一份标量，所以整页同向同速滑动。
        """
        skip = self._flip_skip
        for eid, icon in self.icons.items():
            if eid == skip:
                continue          # 拖拽中的条目由拖影代表，几何不参与翻页
            entry = self.storage.find_entry(eid)
            if entry is None or icon.isHidden():
                continue
            x, y = self._entry_pos(entry)
            dx, dy = grid_flow.flip_px(
                self.storage, grid_flow.entry_page(self.storage, entry))
            icon.move(x + dx, y + dy)

    def _on_flip_step(self, value):
        grid_flow.set_flip_progress(value)
        self._apply_flip_frame()
        if self._widget_layer is not None:
            self._widget_layer.apply_flip_frame()
        self.update()

    def _on_flip_done(self):
        grid_flow.end_flip()
        self._flip_anim = None
        self._flip_skip = None
        self._settle_flip()
        if self._widget_layer is not None:
            self._widget_layer.settle_flip()
        self.update()

    def _show_flip_pages(self, pages):
        """只让这几页的图标可见（其余隐藏）——翻页期间恰好是旧页 + 新页。

        「无第三页残留」在这里落地：动画还没开始就先按页收编，不再依赖
        `anim.finished`（旧实现在弹簧过冲的最后一帧仍露一角，就是反馈 6
        的"第一页最右图标残留"）。
        """
        keep = set(int(p) for p in pages)
        for eid, icon in self.icons.items():
            entry = self.storage.find_entry(eid)
            if entry is None:
                continue
            want = ((not self._zen) and
                    grid_flow.entry_page(self.storage, entry) in keep)
            if want and icon.isHidden():
                icon.show()
            elif not want and not icon.isHidden():
                icon.hide()

    def _settle_flip(self):
        """翻页结束/无动画跳页：图标落到最终格点 + 只留当前页可见。"""
        page = self._page
        for eid, icon in self.icons.items():
            entry = self.storage.find_entry(eid)
            if entry is None:
                continue
            if grid_flow.entry_page(self.storage, entry) == page:
                icon.move(*self._entry_pos(entry))
                if icon.isHidden() and not self._zen:
                    icon.show()
            elif not icon.isHidden():
                icon.hide()

    def _cull_page_icons(self):
        """v6 §4 分页禁半截：只显示当前页图标，其余页隐藏。

        桌面窗口 = 工作区大小，网格四边等距铺满后上下边距（约半格）
        放不下一整格图标——邻页首/末行图标静止态会从屏底/屏顶露半截。
        rebuild / 新增 / rescan / DPI 变化后复核；翻页滑行期间可见集合
        由 `_show_flip_pages` 收编成两页，滑停由 `_settle_flip` 收口。
        """
        rpp = grid_flow.rows_per_page()
        page = grid_flow.current_page()
        for eid, icon in self.icons.items():
            entry = self.storage.find_entry(eid)
            if entry is None:
                continue
            icon.setVisible((not self._zen) and
                            grid_flow.entry_page(self.storage, entry) == page)

    def _cull_moved_icons(self, moves: dict):
        """按位移清单复核可见性（v6 §4）：落到当前页的显示、别页的隐藏。

        拖动预览路径用（O(位移数)）：多选块跨页拖动时，成员落到当前页
        要立即可见，落到别页立即收编——只动 moves 里的图标，不跑全量。
        """
        rpp = grid_flow.rows_per_page()
        page = grid_flow.current_page()
        for eid in moves:
            entry = self.storage.find_entry(eid)
            icon = self.icons.get(eid)
            if entry is None or icon is None:
                continue
            icon.setVisible(grid_flow.entry_page(self.storage, entry) == page)

    def _dot_centers(self):
        """页点圆心列表（底部居中；只有 1 页时返回空——不画点）。

        v10 反馈 25：Dock 启用时底部条带（~51px）盖住 y=height-26 的页点。
        修：y 扣掉 Dock 底部 inset——页点画在可用区底部，不再被条带遮挡。
        Dock 在顶部时 dots 在底部不受影响；左右时 dots 居中也不受影响。
        """
        total = grid_flow.page_count(self.storage)
        if total <= 1:
            return []
        r, gap = 4, 14
        total_w = total * 2 * r + (total - 1) * gap
        x0 = (self.width() - total_w) // 2
        ins = self._dock_inset()   # (left, top, right, bottom)
        y = self.height() - 26 - ins[3]   # 扣底部 Dock 占位
        return [QPoint(x0 + i * (2 * r + gap) + r, y) for i in range(total)]

    def _dots_rect(self):
        """页点命中区（含 6px 热区）。无页点时 None。"""
        pts = self._dot_centers()
        if not pts:
            return None
        return QRect(pts[0].x() - 6, pts[0].y() - 6,
                     pts[-1].x() - pts[0].x() + 12, 12)

    def _paint_dots(self):
        """页点（page dots）：当前页 ACCENT 实心，其余 TEXT_DIM 半透明。
        只在整理模式画+可点——原生模式空白区事件穿透真实桌面，画了
        也点不到（与橡皮筋/空态提示同策略）。"""
        pts = self._dot_centers()
        if not pts:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cur = self._page
        for i, c in enumerate(pts):
            if i == cur:
                p.setPen(Qt.NoPen)
                p.setBrush(theme.ACCENT)
                p.drawEllipse(c, 5, 5)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(theme.TEXT_DIM.red(), theme.TEXT_DIM.green(),
                                  theme.TEXT_DIM.blue(), 110))
                p.drawEllipse(c, 4, 4)
        p.end()

    def _dev_flag(self, key: str, default: bool = True) -> bool:
        """开发者分项开关（v11 反馈 32）：总闸关着 -> 一律 False。

        `dev_mode` 是总闸，`dev_grid` / `dev_widget_collision` / `dev_perf` /
        `dev_window_check` 是四项叠加层。旧 state 没有这些键 -> 默认 True，
        所以"打开总闸"的行为与旧版完全一致（零迁移成本）。
        """
        if not self._dev_mode():
            return False
        v = self.storage.get_setting(key)
        return default if v is None else bool(v)

    def _paint_grid_debug(self, force_grid: bool = False):
        """开发者模式：网格可视化边框（2026-10-18，排查布局问题用）。

        画四样东西（调试色与主题色刻意错开——ACCENT 是蓝，这里用春绿/
        品红，肉眼一眼区分；像素签名见 tests/test_dev_mode.py）：
          - 网格工作区外边界（ox,oy → +cols*cw, +rows*ch）：2px 春绿实线
          - 内部网格线（cols+1 竖 + rows+1 横）：1px 春绿 140 alpha
          - 原点十字（ox,oy）：8px 品红——四边等距铺满的锚点
          - 左上几何信息文字（ox/oy/列行/格尺寸/页码）：春绿 12px
        只在整理模式画（原生模式桌面是系统原生的，不盖任何东西）；
        GridMetrics 快照未就绪（挂载前）时跳过。

        **v11（反馈 32）拆成四项独立开关**（设置 ▸ 开发者）：`dev_grid`
        管网格线/原点/几何文字，`dev_widget_collision` 管组件碰撞红底，
        `dev_perf` 管帧率读数，`dev_window_check` 管窗口一致性巡检。
        `force_grid=True` 是"取点 / 重置后闪一下"这类一次性反馈，与开关
        无关（总闸关着时用户取点也必须看得见网格）。
        """
        m = grid_flow.current_metrics()
        if m is None:
            return
        grid_on = bool(force_grid) or self._dev_flag("dev_grid")
        collision_on = self._dev_flag("dev_widget_collision")
        perf_on = self._dev_flag("dev_perf")
        check_on = self._dev_flag("dev_window_check")
        if not (grid_on or collision_on or perf_on or check_on):
            return
        x0, y0 = m.ox, m.oy
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # v10 反馈 26：组件碰撞体积红底先铺（最底层）——网格线/信息文字
        # 叠在它上面才看得见；反过来会把网格线染成红绿混色。
        if collision_on:
            self._paint_widget_collision(p)
        if grid_on:
            self._paint_grid_lines(p, m)
            self._paint_dev_readout(p, m, x0, y0)
        if perf_on:
            self._paint_dev_perf(p, x0, y0)
        if check_on:
            self._paint_dev_window_check(p, x0, y0)
        p.end()

    def _paint_grid_lines(self, p: QPainter, m):
        """网格线 + 原点十字（`dev_grid` 那一项）。"""
        green = QColor(0, 255, 170)      # 春绿：线 + 文字
        pink = QColor(255, 64, 160)      # 品红：原点十字
        x0, y0 = m.ox, m.oy
        x1, y1 = m.ox + m.cols * m.cw, m.oy + m.rows * m.ch
        p.setPen(QPen(green, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRect(x0, y0, x1 - x0, y1 - y0))
        # 内部网格线（alpha 140：亮壁纸上也清晰，又不抢图标焦点）
        p.setPen(QPen(QColor(0, 255, 170, 140), 1))
        for c in range(1, m.cols):
            x = x0 + c * m.cw
            p.drawLine(x, y0, x, y1)
        for r in range(1, m.rows):
            y = y0 + r * m.ch
            p.drawLine(x0, y, x1, y)
        # 原点十字（四边等距铺满的锚点）
        p.setPen(QPen(pink, 2))
        p.drawLine(x0 - 5, y0, x0 + 5, y0)
        p.drawLine(x0, y0 - 5, x0, y0 + 5)

    def _paint_dev_readout(self, p: QPainter, m, x0: int, y0: int):
        """左上角几何信息文字（列行 / 格尺寸 / 页码）。"""
        total = grid_flow.page_count(self.storage)
        p.setPen(QPen(QColor(0, 255, 170, 220), 1))
        p.setFont(theme.ui_font(12))
        p.drawText(QRect(x0 + 6, y0 + 6, 560, 20),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   "DEV 网格 ox=%d oy=%d %d列×%d行 格%dx%d 第%d/%d页"
                   % (m.ox, m.oy, m.cols, m.rows, m.cw, m.ch,
                      self._page + 1, max(1, total)))

    def _paint_dev_perf(self, p: QPainter, x0: int, y0: int):
        """帧率 / 峰值 / 绘制面积读数（`dev_perf` 那一项）。

        反馈 8（v7e）：排查"掉帧卡顿"时不用再开性能分析器。
        """
        st = self.frame_stats()
        p.setPen(QPen(QColor(0, 255, 170, 200), 1))
        p.drawText(QRect(x0 + 6, y0 + 24, 560, 18),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   "帧 %.0ffps  均 %.1fms  峰 %.1fms  上次绘制 %.1f万px"
                   % (st["fps"], st["avg_ms"], st["max_ms"],
                      st["last_area"] / 10000.0))

    def _paint_dev_window_check(self, p: QPainter, x0: int, y0: int):
        """组件真实窗口 vs 卡片尺寸的一致性巡检（`dev_window_check`）。

        v10 反馈 27：不一致 = 盒外有一圈吞点击的透明区（用户"没有红底却
        点不动"的直接证据），红字报出来。
        """
        rows = self._widget_window_rects()
        bad = [r for r in rows
               if r[3] and (r[1].width() != r[2][0] or r[1].height() != r[2][1])]
        if bad:
            p.setPen(QPen(QColor(255, 96, 96, 240), 1))
            txt = "组件窗口异常 %d/%d：" % (len(bad), len(rows)) + "  ".join(
                "%s %dx%d(应%dx%d)" % (r[0], r[1].width(), r[1].height(),
                                       r[2][0], r[2][1]) for r in bad[:3])
        else:
            p.setPen(QPen(QColor(0, 255, 170, 200), 1))
            txt = ("组件窗口 %d/%d 与卡片一致 · 图标 %d"
                   % (len(rows), len(rows), len(self.icons)))
        p.drawText(QRect(x0 + 6, y0 + 42, 700, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, txt)

    # ---------- 组件碰撞可视化（v10 反馈 26） ----------
    def _widget_collision_rects(self) -> list:
        """当前页锚定组件的**碰撞矩形**（span 盒，非卡片矩形）。

        返回 `[(QRect, (cs, rs)), …]`，窗口坐标。供调试红底与判据共用——
        几何一律走 `grid_flow.cell_pos` + `widget_geom.span_box`，不在这里
        重新推导（三矩形唯一换算层，见 widget_geom 模块头）。
        """
        from . import widget_geom
        out = []
        page = grid_flow.current_page()
        for entry in self.storage.widgets:
            if not entry.get("anchored") or entry.get("hidden"):
                continue
            try:
                if grid_flow.entry_page(self.storage, entry) != page:
                    continue
                cs, rs = grid_flow.widget_span(self.storage, entry)
                x, y = grid_flow.cell_pos(self.storage, int(entry.get("col", 0)),
                                          int(entry.get("row", 0)))
                bw, bh = widget_geom.span_box(self.storage, (cs, rs))
            except (TypeError, ValueError, AttributeError):
                continue        # 脏条目跳过，别让调试绘制炸掉桌面
            out.append((QRect(int(x), int(y), int(bw), int(bh)), (cs, rs)))
        return out

    def _widget_window_rects(self) -> list:
        """组件**真实窗口**矩形（本窗口坐标）+ 应有卡片尺寸 + 是否锚定。

        v10 反馈 27 第三条（"没有红底却点不动"）：红底画的是**推算**出来的
        span 盒，窗口却是 Qt 实际摆出来的那块地。两者不一致时（历史 span
        残留、编辑态外扩没收回、悬浮组件的自由像素尺寸），多出来的透明区
        就是吞点击的盲区。调试画真实窗口，让偏差可视化——别再靠推算自证。
        """
        from . import widget_geom
        out = []
        layer = getattr(self, "_widget_layer", None)
        widgets = getattr(layer, "widgets", None) if layer is not None else None
        if not widgets:
            return out
        for entry in self.storage.widgets:
            w = widgets.get(entry.get("id"))
            if w is None:
                continue
            try:
                if not w.isVisible():
                    continue
                g = w.geometry()
                tl = self.mapFromGlobal(w.mapToGlobal(QPoint(0, 0)))
                cs, rs = grid_flow.widget_span(self.storage, entry)
                card = widget_geom.card_size(self.storage, (cs, rs))
            except (RuntimeError, AttributeError, TypeError, ValueError):
                continue
            out.append((entry.get("id"), QRect(tl, g.size()), card,
                        bool(entry.get("anchored"))))
        return out

    def _paint_widget_collision(self, p: QPainter):
        """调试模式：给组件碰撞体积铺半透明红底 + 画卡片边框。

        为什么值得画（用户反馈"四周交互还是有问题"）：组件卡片按
        `widget_fill` 居中缩小，**卡片与 span 盒之间的缝隙**才是命中/避让
        的真实边界。红底铺满 span 盒 = 组件真正"占的地"；卡片边框画出视觉
        边界——两者差多少，一眼可见。

        v10 反馈 27 追加：再画**真实窗口矩形**（橙实线）。用户遇到"没有
        红底却点不动"——那块地不属于 span 盒，只能是某个窗口在吞点击。
        真实窗口比推算的卡片更可信：窗口一旦大于卡片（历史 span 残留 /
        编辑态没收回 / 悬浮组件），多出来的透明区就是盲区本身。两者尺寸
        不一致时窗口框画成红色并计入调试信息。
        """
        from . import widget_geom
        red_fill = QColor(255, 56, 56, 64)
        red_edge = QColor(255, 56, 56, 170)
        card_edge = QColor(255, 160, 60, 220)
        win_ok = QColor(255, 176, 64, 235)      # 窗口与卡片一致：橙实线
        win_bad = QColor(255, 48, 48, 255)      # 不一致：刺眼红实线
        float_edge = QColor(190, 120, 255, 220)  # 悬浮组件：紫框（无 span 盒）
        icon_edge = QColor(0, 210, 255, 110)    # 图标窗口：淡青细框
        p.save()
        p.setRenderHint(QPainter.Antialiasing, False)
        try:
            for rect, span in self._widget_collision_rects():
                p.setPen(QPen(red_edge, 1))
                p.setBrush(red_fill)
                p.drawRect(rect)
                # 卡片矩形（视觉边界）：与碰撞盒的差 = fill 留出的缝隙
                try:
                    cw, ch = widget_geom.card_size(self.storage, span)
                    dx, dy = widget_geom.box_inset(self.storage, span)
                    card = QRect(rect.x() + dx, rect.y() + dy, cw, ch)
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(card_edge, 1, Qt.DashLine))
                    p.drawRect(card)
                except (TypeError, ValueError, AttributeError):
                    pass
                # 左上角标注：格数与盒尺寸（多组件并排时定位用）
                p.setPen(QPen(QColor(255, 200, 200, 230), 1))
                p.setFont(theme.ui_font(10))
                p.drawText(QRect(rect.x() + 4, rect.y() + 2, rect.width() - 8, 14),
                           Qt.AlignLeft | Qt.AlignTop,
                           "%d×%d 盒 %dx%d" % (span[0], span[1],
                                               rect.width(), rect.height()))
            # 真实窗口矩形（v10 反馈 27）：与卡片一致才画橙线
            p.setFont(theme.ui_font(9))
            for _wid, win, card_size, anchored in self._widget_window_rects():
                same = (win.width() == card_size[0]
                        and win.height() == card_size[1])
                col = (win_ok if same else win_bad) if anchored else float_edge
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(col, 2 if not same else 1))
                p.drawRect(win)
                p.setPen(QPen(col, 1))
                p.drawText(QRect(win.x() + 3, win.y() + 15,
                                 max(60, win.width() - 6), 14),
                           Qt.AlignLeft | Qt.AlignTop,
                           "%s win %dx%d" % ("悬浮" if not anchored else "窗口",
                                             win.width(), win.height()))
            # 图标窗口矩形：淡青细框——"点不动"时先看图标窗口在哪
            p.setPen(QPen(icon_edge, 1))
            for icon in self.icons.values():
                try:
                    if not icon.isVisible():
                        continue
                    g = icon.geometry()
                    p.drawRect(QRect(self.mapFromGlobal(icon.mapToGlobal(QPoint(0, 0))),
                                     g.size()))
                except (RuntimeError, AttributeError):
                    continue
        finally:
            p.restore()


    # ---------- 重建 ----------
    def _icon_size(self):
        """图标绘制边长（**双模式同源**，v7 B2 / 反馈 11）。

        旧实现按模式分叉：网格走 `icon_metric()`、无界直接吃 `icon_px`
        （默认 96）。切模式那一刻图标像素突变，42 个图标一起改大小 →
        视觉上"挤成一坨"（反馈 11）。现在两模式同一条来源——真正决定
        视觉尺寸的是 `_icon_layout()`（cell × 占比），这里只提供
        AppIcon 的基线边长，必须与网格模式一致。

        `icon_px` 键保留（旧 state 里还有），但**不再驱动任何模式**。
        """
        return self.storage.icon_metric()

    def _wire_icon(self, icon):
        """把一个 AppIcon 的全部信号接到窗口（rebuild 与增量 _add_icon 共用）。"""
        icon.activated.connect(self._on_activate)
        icon.clicked.connect(self._on_icon_clicked)
        icon.folderClicked.connect(self.open_folder)
        # v14：悬停展开走独立入口——弹窗记「悬停打开」，鼠标离开后自动关
        icon.folderHoverOpened.connect(
            lambda eid: self.open_folder(eid, by_hover=True))
        icon.mergeRequested.connect(self._on_merge)
        icon.removeRequested.connect(self._on_remove_entry)
        icon.dissolveRequested.connect(self._on_dissolve)
        icon.frameRequested.connect(self._on_folder_frame)
        icon.iconScaleChanged.connect(self._on_icon_scale)
        icon.openLocation.connect(self._on_open_location)
        icon.dragStarted.connect(self._on_drag_started)
        icon.dragFinished.connect(self._on_drag_finished)
        icon.reflowHover.connect(self._on_reflow_hover)
        icon.reflowDrop.connect(self._on_reflow_drop)
        icon.payload_provider = self._drag_payload_for

    def rebuild(self, stagger=False):
        for icon in self.icons.values():
            icon.deleteLater()
        self.icons.clear()
        self._selected = {eid for eid in self._selected
                          if self.storage.find_entry(eid) is not None}
        # 仅小组件模式（反馈 9）：只显示组件——图标一个都不建。
        # 条目仍完整留在 storage 里，关掉该模式重建即回来。
        if not self._widgets_only():
            size = self._icon_size()             # v6 §1.3：双模式图标边长
            chrome = self.storage.ui_scale()
            for idx, entry in enumerate(self.storage.layout):
                # v6 A2：三档 × 当前 cell；反馈 12 再乘该条目的 icon_scale
                layout = self._icon_layout(entry)
                icon = AppIcon(entry, size, parent=self, chrome_scale=chrome,
                               layout=layout,
                               label_style=self._label_style())
                self._wire_icon(icon)
                icon.set_selected(entry["id"] in self._selected)
                icon.set_selection_visible(self._selection_mode)
                icon.move(*self._entry_pos(entry))
                if stagger:
                    # 错峰淡入（模式切换 / 首次显示的仪式感；业务 rebuild 不加，
                    # 拖动落位要即时反馈）
                    icon.appear_in(min(idx * 30, 600))
                icon.show()
                self.icons[entry["id"]] = icon
        # 安全网：rebuild 销毁全部图标部件，飞行中的拖拽其源已不存在——
        # dragFinished 可能永远送不达（源被 deleteLater 后 emit 抛
        # RuntimeError，被 IconWidget._after_drag 吞掉）。与 dropEvent
        # 末尾同理（无论 dragFinished 是否送达，drop 完成即复位），
        # 这里无条件复位拖动状态；否则 _drag_active 卡死会让
        # nativeEvent 保持全窗口 HTCLIENT，桌面点击全部失效。
        self._drag_active = False
        self._drag_entry_id = None
        self._drag_multi = False
        self._ole_active = False
        self._cull_page_icons()      # v6 §4：只显示当前页（禁半截）
        self.update()

    # ---------- 增量更新（2026-10-18 M4：去 rebuild 化） ----------
    def _icon_layout(self, entry=None):
        """图标 layout（v6 A2 / v7 B1 §2.2，**双模式同源**）。

        档位（storage.icon_fit）× 当前 cell（GridMetrics 快照，屏幕驱动）
        -> grid_sizing.IconLayout；快照未就绪返回 None（回退旧 chrome 路径）。

        v7 B2（反馈 11）：**无界模式不再返回 None**。旧实现在无界模式把
        尺寸交给 `icon_px`，切模式 = 图标从 cell×占比 跳到 96px，整套
        图标"挤成一坨"。现在两模式共用同一份 layout，切模式尺寸零变化。

        entry 传入时再乘它的 `icon_scale`（反馈 12：单图标大小覆盖）。
        """
        from . import grid_sizing
        m = grid_flow.current_metrics()
        if m is None:
            return None
        scale = 1.0
        if isinstance(entry, dict):
            try:
                scale = float(entry.get("icon_scale", 1.0) or 1.0)
            except (TypeError, ValueError):
                scale = 1.0
        return grid_sizing.icon_layout(
            m.cw, m.ch, self.storage.resolved_icon_fit(m.cw), scale=scale)

    def _add_icon(self, entry):
        """增量新增一个图标（add_app / rescan / 文件夹提取等）。
        已在位返回原实例；新图标淡入出场（appear_in(0)）。"""
        if entry["id"] in self.icons:
            return self.icons[entry["id"]]
        size = self._icon_size()
        chrome = self.storage.ui_scale()
        layout = self._icon_layout(entry)          # v6 A2 + 反馈 12 单图标倍数
        icon = AppIcon(entry, size, parent=self, chrome_scale=chrome,
                       layout=layout,
                       label_style=self._label_style())
        self._wire_icon(icon)
        icon.set_selected(entry["id"] in self._selected)
        icon.set_selection_visible(self._selection_mode)
        icon.move(*self._entry_pos(entry))
        icon.appear_in(0)
        icon.show()
        self.icons[entry["id"]] = icon
        self._cull_page_icons()      # v6 §4：新增图标可能落在别页
        return icon

    def _remove_icon(self, entry_id):
        """增量移除一个图标（remove_entry / 合并被吸收方 / rescan 丢失）。"""
        if self._folder_open_icon is not None and entry_id == self._folder_open_icon_entry_id():
            self._exit_folder_open_state()
        icon = self.icons.pop(entry_id, None)
        self._selected.discard(entry_id)
        if icon is not None:
            icon.deleteLater()

    def _folder_open_icon_entry_id(self):
        """打开态源图标对应的 entry id（AppIcon.entry 引用同一 dict）。"""
        icon = self._folder_open_icon
        if icon is None:
            return None
        try:
            return (icon.entry or {}).get("id")
        except RuntimeError:
            return None

    def _sync_icons(self):
        """位置类变化的全量同步：每个图标弹簧滑到自己的格子。

        已在目标位上的 animate_to 早退（零成本）。这是 M4「去 rebuild
        化」的核心——位置变化一律走动画，悬停态 / 进行中动画 / 选中态
        全部保留（指导文档 §4.1）。
        """
        for eid, icon in self.icons.items():
            entry = self.storage.find_entry(eid)
            if entry is None:
                continue
            icon.animate_to(*self._entry_pos(entry))
        self._cull_page_icons()      # v6 §4：rescan 等重排后按页显隐

    def _sync_structure(self):
        """结构同步：storage.layout 与 self.icons 对齐（移除多余、补上
        缺失），再 _sync_icons 让位置变化走弹簧动画。

        替代业务路径里的 rebuild()——合并 / 解散 / 增删条目不再销毁重建
        全部 AppIcon。rebuild 只剩模式切换 / 图标大小变更 / 首屏这类
        「整体刷新」仪式场景。条目 type 变化（app→folder）无需重建图标：
        AppIcon.is_folder 是动态属性，直接读同一份 entry dict。
        """
        live = {e["id"] for e in self.storage.layout}
        for eid in [eid for eid in self.icons if eid not in live]:
            self._remove_icon(eid)
        for e in self.storage.layout:
            if e["id"] not in self.icons:
                self._add_icon(e)
        self._selected &= live
        self._sync_icons()

    # ---------- 多选 ----------
    def _on_icon_clicked(self, entry_id):
        if self._selection_mode:
            # 多选态：单击 = 勾选 / 取消勾选，**不启动应用**
            # （手机选多个应用时点一下就开始跑，是最难受的误操作）
            if entry_id in self._selected:
                self._selected.discard(entry_id)
            else:
                self._selected.add(entry_id)
            self._refresh_selection()
            return
        ctrl = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
        if ctrl:
            if entry_id in self._selected:
                self._selected.discard(entry_id)
            else:
                self._selected.add(entry_id)
        else:
            self._selected = {entry_id}
        self._refresh_selection()

    def _refresh_selection(self):
        for eid, icon in self.icons.items():
            icon.set_selected(eid in self._selected)

    def _select_in_rect(self, rect):
        """橡皮筋框选：与矩形相交即选中（Ctrl 追加）。"""
        if not (QApplication.keyboardModifiers() & Qt.ControlModifier):
            self._selected.clear()
        for eid, icon in self.icons.items():
            if rect.intersects(QRect(icon.pos(), icon.size())):
                self._selected.add(eid)
        self._refresh_selection()

    def _clear_selection(self):
        if self._selected:
            self._selected.clear()
            self._refresh_selection()

    # ---------- 手机模式：多选态（v9 V4） ----------
    def enter_selection_mode(self, first_id=None):
        """进入多选态：所有图标左上角出现空心圆（选中项变对钩）。"""
        self._selection_mode = True
        if first_id:
            self._selected = {first_id}
        self._sync_selection_visible()
        self._refresh_selection()

    def exit_selection_mode(self):
        """退出多选态：清掉多选标记与选中集合（不影响应用本身）。"""
        self._selection_mode = False
        self._selected.clear()
        self._sync_selection_visible()
        self._refresh_selection()

    def _sync_selection_visible(self):
        for icon in self.icons.values():
            try:
                icon.set_selection_visible(self._selection_mode)
            except RuntimeError:
                pass

    def set_interaction_mode(self, key):
        """切换交互方式（桌面右键 ▸ 交互方式 / 设置页，同一个值）。

        切换时必须清掉"半途手势"：正在拖的选框、正在累计的滑动位移都
        属于上一套语义，留着会在新语义下被当成另一件事执行（框选残影
        画到松手才消失 / 一按就翻页）。
        """
        was_mobile = interaction.is_mobile(self.storage)
        interaction.set_mode(self.storage, key)
        if was_mobile and not interaction.is_mobile(self.storage):
            self.exit_selection_mode()
        self._band_start = None
        self._band_rect = None
        self._band_moved = False
        self._swipe_start = None
        self._swipe_moved = False
        self.update()

    def _selected_entries(self):
        """选中的条目，按布局顺序（合并/移页要稳定顺序，不能靠 set 的迭代序）。"""
        out = []
        for e in self.storage.layout:
            if e.get("id") in self._selected:
                out.append(e)
        return out

    def _build_batch_menu(self):
        """多选态的批量菜单：合并为文件夹 / 移动到分页 / 删除选中。"""
        n = len(self._selected)
        menu = QMenu(self)
        menu.setStyleSheet(theme.MENU_QSS)
        self._ctx_submenus = []
        if n == 0:
            menu.addAction("全选", self._select_all)
            menu.addSeparator()
            menu.addAction("退出选择", self.exit_selection_mode)
            return menu
        menu.addAction("合并为文件夹（%d）" % n, self._batch_merge)
        sub = menu.addMenu("移动到分页")
        self._ctx_submenus.append(sub)
        cur = self._page
        total = max(grid_flow.page_count(self.storage), cur + 2, 2)
        for i in range(total):
            act = sub.addAction("第 %d 页" % (i + 1),
                                lambda _c=False, i=i: self._batch_move_to_page(i))
            act.setCheckable(True)
            act.setChecked(i == cur)
        menu.addAction("删除选中（%d）" % n, self._remove_selected)
        menu.addSeparator()
        menu.addAction("退出选择", self.exit_selection_mode)
        return menu

    def _select_all(self):
        """全选当前页（其它页的条目不在视野里，全选会让用户莫名删掉看不见的东西）。"""
        self._selected = {eid for eid, icon in self.icons.items()
                          if not icon.isHidden()}
        self._refresh_selection()

    def _batch_merge(self):
        """把选中的条目合并成一个文件夹（第一个当容器，其余并入）。"""
        entries = self._selected_entries()
        if len(entries) < 2:
            return
        target = entries[0]
        for other in entries[1:]:
            if not other.get("app") and other.get("type") != "folder":
                continue
            if target.get("type") == "folder":
                self._absorb(target, other)
                continue
            if other.get("type") == "folder":
                target["type"] = "folder"
                target["app"] = {}
                target["apps"] = [m for m in [target.get("app")] if m]
                self._absorb(target, other)
            else:
                members = [m for m in (target.get("app"), other.get("app")) if m]
                target["type"] = "folder"
                target["app"] = {}      # 空 dict 而非 None（坑 #39）
                target["apps"] = members
                self.storage.remove_entry(other["id"])
        self.storage.compact_layout()
        self.storage.save()
        self._selected = {target["id"]}
        self._sync_structure()
        self._refresh_selection()
        if self._popup is not None:
            self._popup.refresh()

    def _batch_move_to_page(self, page):
        """把选中的条目搬到第 page 页（0 基）。

        网格模式：在目标页里按顺序找空格（跳过已被占用的格与本次已分配的格）；
        无界模式：只改 `fpage`（无界坐标本来就不共享网格，页归属是唯一语义）。
        两种模式都只写"页归属"这一个事实，不动尺寸与其它字段。
        """
        entries = self._selected_entries()
        if not entries:
            return
        page = max(0, int(page))
        if grid_flow.free_mode(self.storage):
            for e in entries:
                e["fpage"] = page
            self.storage.save()
            self._sync_icons()
            self.goto_page(page)
            return

        rpp = grid_flow.rows_per_page()
        cols = max(1, int(self.storage.columns))
        moving = {e.get("id") for e in entries}
        blocked = set()
        for e in self.storage.layout:
            if e.get("id") in moving:
                continue
            blocked.add((int(e.get("col", 0) or 0), int(e.get("row", 0) or 0)))
        for col, row in grid_flow.widget_cells(self.storage):
            blocked.add((col, row))
        lo = page * rpp
        free = [(c, r) for r in range(lo, lo + rpp) for c in range(cols)
                if (c, r) not in blocked]
        for e, (c, r) in zip(entries, free):
            e["col"], e["row"] = c, r
        self.storage.save()
        self._sync_icons()
        self.goto_page(page)


    # ---------- 拖拽 ----------
    def _on_drag_started(self):
        # 快照当前布局：拖动期间 preview 只改内存，取消时据此回退；
        # 提交时同一快照压入撤销栈（Ctrl+Z）
        self._layout_snap = [(e.get("id"), int(e.get("col", 0)), int(e.get("row", 0)))
                             for e in self.storage.layout]
        self._preview_moves = None
        self._preview_drag = None

    def _on_drag_finished(self):
        self._drag_active = False
        self._drag_entry_id = None
        self._drag_multi = False
        self._hl_cell = None            # v6 §5.2：红框随拖拽结束撤下
        self._cancel_push()             # 反馈 9：未触发的推让随松手作废
        self._cancel_edge_flip()        # 反馈 8：未触发的翻页随松手作废
        if self._preview_moves is not None:
            # 没有落点（拖到窗外 / 拖到资源管理器 / 按 Esc 取消）-> 回退
            self._cancel_preview()
        self.update()

    def _drag_payload_for(self, entry_id: str) -> str:
        """拖拽 payload：单选=条目 id；多选且拖的是选中成员=逗号分隔 id 集合。
        同时置 _drag_active：命中测试在此期间全窗口接收，保证 OLE 拖放能落下。"""
        self._drag_entry_id = entry_id
        self._drag_active = True
        if len(self._selected) > 1 and entry_id in self._selected:
            self._drag_multi = True
            return ",".join(sorted(self._selected))
        self._drag_multi = False
        return entry_id

    # ---------- 实时让位（Live Reflow） ----------
    def dragMoveEvent(self, e):
        """拖动经过空白格：按落点算一次让位预览并驱动其他图标弹簧滑行。

        这是「安卓式滑动让位」的实时性来源——不等松手，被越过的图标
        立即让出格子（design.md 2.3）。反馈 8 的边缘悬停翻页检测也在
        这条路上（_drag_move_at 首行），翻页后用拖影位置重算一次。
        """
        if not e.mimeData().hasFormat(MIME_TYPE):
            return
        e.acceptProposedAction()
        pos = e.position().toPoint()
        # v10 C3：拖到 Dock 条带上——交给 Dock 亮边框，网格不做让位预览
        # （两套"落点"同时亮会让用户以为会落两处）
        if self._dock is not None:
            on_dock = self._dock_hit(pos)
            self._dock.set_drop_highlight(on_dock)
            if on_dock:
                return
        self._drag_move_at(e.mimeData().data(MIME_TYPE).data().decode(), pos)

    def _drag_move_at(self, dragged: str, pos):
        """dragMove 主体：边缘翻页检测 + 落点红框 + 让位预览。

        边缘翻页检测放在 free_mode 之前：无界模式下 `_edge_dir_at`
        自己返回 0，顺带撤销可能残留的武装（模式不会在拖动中变，防御性）。
        反馈 8 的翻页后重算也走本函数（_on_edge_flip），不复制逻辑。
        """
        self._maybe_edge_flip(pos)
        if grid_flow.free_mode(self.storage):
            return      # v6 §1.3：无界模式无让位预览、无目标格红框
        cell = self._drag_cell(pos)
        if dragged.startswith("M:"):
            # 反馈 6：文件夹成员拖出——落点按 _on_extract 的真实规则算
            # （目标被占时会落到第一个空格），红框必须画在那一格，
            # 否则用户看着红框松手、图标却出现在别处
            self._set_highlight(self._extract_landing_cell(cell))
            return
        if self._hl_cell == cell:
            # 反馈 8（v7e，性能）：**同一格不重算**。dragMove 按鼠标采样率
            # 来（高精度鼠标 125~1000Hz），而布局只与"哪一格"有关——同一格
            # 重复跑 _free_moves/_arrange_moves + 重启动画是纯粹的每帧浪费。
            # 只补一件事：推让武装可能被 dragLeave 撤销过（拖影压到图标再
            # 出来），这里把它重新武装，其余一律跳过。
            occ = self.storage.occupant(cell[0], cell[1], exclude_id=dragged)
            if occ is not None and self._push_cell != cell:
                self._push_cell = cell
                self._push_dragged = dragged
                self._push_timer.start()
            return
        self._set_highlight(cell)       # v6 §5.2：红框跟随面积判定结果
        self._reflow_preview(dragged, cell)

    # ---------- 边缘悬停翻页（反馈 8，手机 launcher 语义） ----------
    def _edge_dir_at(self, pos) -> int:
        """指针（窗口坐标）处的翻页方向。

        返回 -1（上一页）/ +1（下一页）/ 0（不在带上或翻不过去）。
        只在桌面图标拖动中（`_drag_active`，由 payload 提供者置上）
        且网格模式有意义——无界模式没有页概念；文件夹成员提取的
        M: payload 不置 `_drag_active`，天然排除。
        """
        if not self._drag_active or grid_flow.free_mode(self.storage):
            return 0
        if grid_flow.page_axis(self.storage) == grid_flow.PAGE_AXIS_VERTICAL:
            if pos.y() <= EDGE_FLIP_BAND:
                d = -1
            elif pos.y() >= self.height() - EDGE_FLIP_BAND:
                d = 1
            else:
                return 0
        else:
            if pos.x() <= EDGE_FLIP_BAND:
                d = -1
            elif pos.x() >= self.width() - EDGE_FLIP_BAND:
                d = 1
            else:
                return 0
        total = max(1, grid_flow.page_count(self.storage))
        if not (0 <= grid_flow.current_page() + d < total):
            return 0        # 第一页再往上 / 末页再往下：没有邻页，不武装
        return d

    def _maybe_edge_flip(self, pos):
        """拖动中检测边缘悬停翻页（反馈 8）。

        每次 dragMove 调一次：在带上 -> 起/续 dwell；离开带 -> 撤销。
        翻页后**不要求离开再回来**：手机语义是"按住不放就一页页继续翻"，
        续武装在 `_on_edge_flip` 里做（指针可能停住不动，等不到下一次
        dragMove）；翻到底后 `_edge_dir_at` 返回 0，自然收口。
        """
        d = self._edge_dir_at(pos)
        if d == 0:
            self._edge_flip_timer.stop()
            self._edge_flip_dir = 0
            self._edge_flip_pos = None
            return
        self._edge_flip_pos = QPoint(pos)
        if d != self._edge_flip_dir:
            self._edge_flip_dir = d
            self._edge_flip_timer.start()
        elif not self._edge_flip_timer.isActive():
            self._edge_flip_timer.start()

    def _on_edge_flip(self):
        """边缘悬停到点：翻一页（反馈 8）。

        翻页只改视图：拖影不动、网格滑动，松手时 `_drag_cell` 按**新页**
        页偏移反算落点（cell_by_overlap 经 grid_flow.current_page），
        "拽到下一个分页"由此闭环。
        """
        d = self._edge_flip_dir
        if d == 0:
            return
        # 旧页的推让武装作废：目标格已随页滑走，700ms 后推一个看不见的
        # 图标没有意义，还会在新页留下幽灵位移
        self._cancel_push()
        # skip_id：拖拽中的图标由拖影代表，不被翻页动画拉走——它不可见，
        # 且落盘时是 move() 到新格，残留的 pos 动画会把它继续拖向旧目标
        self.goto_page(grid_flow.current_page() + d,
                       skip_id=self._drag_entry_id)
        # 续武装（按住不放连续翻）：以最后一次在带内的指针复核——翻到底后
        # _edge_dir_at 返回 0，这里就不续，计时器自然收起
        if self._edge_flip_pos is not None and \
                self._edge_dir_at(self._edge_flip_pos) == d:
            self._edge_flip_timer.start()
        # 红框/预览还停在旧页的格子上（cell_pos 按新页换算后画到屏外）：
        # 用拖影当前位置把 dragMove 主体重算一次——与"用户继续移动鼠标"
        # 同一条路径，松手前的最后一帧也是新页落点
        icon = self.icons.get(self._drag_entry_id or "")
        ghost = getattr(icon, "_drag_ghost_w", None) if icon is not None else None
        if ghost is None:
            return
        try:
            dragged = icon._drag_mime.data(MIME_TYPE).data().decode()
        except Exception:
            dragged = ""
        if dragged:
            pos = self.mapFromGlobal(ghost.geometry().center())
            self._drag_move_at(dragged, pos)

    def _cancel_edge_flip(self):
        """松手 / 拖出窗口 / 拖到图标上（dragLeave）：撤销边缘翻页。"""
        self._edge_flip_timer.stop()
        self._edge_flip_dir = 0
        self._edge_flip_pos = None

    def _extract_landing_cell(self, cell):
        """成员拖出的**实际落点**（与 `_on_extract` 同一规则）。

        目标格空 -> 就落目标格；被占 -> `storage.first_free_cell()`
        （`add_app` 用的就是它，不另写一份扫描——两套规则必然漂移）。
        """
        col, row = int(cell[0]), int(cell[1])
        if self.storage.occupant(col, row) is not None:
            free = self.storage.first_free_cell()
            if free is not None:
                col, row = int(free[0]), int(free[1])
        return (col, row)

    def _on_reflow_hover(self, dragged: str, gx: int, gy: int):
        """拖动悬停（2026-10-18 M2：参数是指针全局坐标，非图标格点）。

        目标格只从指针全局位置反算——预览会改写 entry，读移动中的
        entry 等于追逐漂移（指导文档 §2.3 病根 3）。2026-10-19 v6
        §5.2：反算升级为拖影矩形的面积判定（_drag_cell），指针位置
        仅作无拖影时的退化输入。v6 §1.3：无界模式整条跳过。
        """
        if grid_flow.free_mode(self.storage):
            return
        pos = self.mapFromGlobal(QPoint(gx, gy))
        cell = self._drag_cell(pos)
        self._set_highlight(cell)
        self._reflow_preview(dragged, cell)

    def _on_reflow_drop(self, dragged: str, gx: int, gy: int):
        """在图标上松手且未武装合并：直接走让位提交。

        不能依赖 drop 事件从图标冒泡到本窗口（实测不传播），故由
        AppIcon.reflowDrop 直连（probe15 G11a）。参数为指针全局坐标。

        2026-10-19 v6 §5.0：多选松手落在图标上同样走这条信号——旧代码
        把 "a,b" 当时条目 id 传给 _reflow_commit_or_compute，查不到 →
        整个 drop 被吞（多选落图标 = 无操作）。改路由 _drop_internal 的
        multi_move 分支，与空白格 drop 同一条语义。

        2026-10-19 v6 §5.2：落点格走拖影矩形面积判定（_drag_cell）。
        2026-10-19 v6 §1.3：无界模式走 _free_drop（像素落点，无让位）。
        """
        pos = self.mapFromGlobal(QPoint(gx, gy))
        if grid_flow.free_mode(self.storage):
            # v6 §1.3：无界模式（含多选）走像素落点，无让位
            self._free_drop([p for p in dragged.split(",") if p],
                            self._drag_pos(pos))
            return
        if self._drag_multi:
            self._drop_internal(dragged, pos)
            return
        self._reflow_commit_or_compute(dragged, self._drag_cell(pos))

    # ---------- 锚定小组件占位（widget 是网格一等公民） ----------
    def _widget_cells(self):
        """可见锚定小组件占据的格子集合（从 storage 读，与实例解耦）。

        v6 §1.3：无界模式下组件不占格（元素可相互重叠）——返回空集，
        让位 / 避让 / 落点拒绝全部自然失效。
        """
        if grid_flow.free_mode(self.storage):
            return set()
        return grid_flow.widget_cells(self.storage)

    def on_widget_grid_changed(self):
        """小组件占位变化（落定/移动）：把被挤到的图标挪到第一个空格。

        用户描述"小组件占据格子位置，挤走其他对象"的直接实现：
        widget 松手吸附后 gridChanged -> 这里 displace -> 图标滑走。
        2026-10-18 M4：位移走弹簧动画（_animate_icons_to_layout），
        不再 rebuild——被挤图标只是换格子，实例/悬停态/动画全部保留。

        v10 反馈 28：这里优先消费拖动快照。`anchorDrop` 早于 `_snap_to_grid`
        发射，松手瞬间拿不到最终占位；真正的一次性收尾都放在
        `_settle_widget_snap()`（组件落定后按最终 entry 算）。
        """
        if self._widget_snap is not None:
            self._settle_widget_snap()
            return
        moved = self._displace_from_widgets()
        if moved:
            self.storage.save()
            self._animate_icons_to_layout(moved)

    def _displace_blocked(self, comp_cells, src_cells) -> set:
        """挤位禁区：组件占位 ∪ 其他图标占位（排除本次要挪走的那些）。

        2026-10-20 F2（用户反馈「移动组件时实时预览把图标叠到同一格」）：
        旧实现只把**组件占位**当禁区传给 `displace_cells`，后者把其余格
        全算空闲——被组件压到的图标于是被分配到**另一个图标正占着的格**
        上，预览里直接叠成一坨。禁区必须含图标自己的占位。

        src_cells 是**要被挪走**的原格：它们本来就在组件矩形里（禁区已有），
        但仍要从"图标禁区"里剔除——否则 displaced 图标会因无空格可去而
        退回原地（留在组件下面）。
        """
        src = set(src_cells)
        icon_cells = {(int(e.get("col", 0)), int(e.get("row", 0)))
                      for e in self.storage.layout}
        return set(comp_cells) | (icon_cells - src)

    def _displace_from_widgets(self) -> dict:
        """把落在小组件占位格上的图标挪到空格（排除组件占位）。
        返回 {id: (col,row)} 位移清单（空 = 无需 displace）。

        2026-10-19：改批量分配（grid_flow.displace_cells）——逐个找空格
        会撞车叠格（反馈 7b）。
        """
        wc = self._widget_cells()
        if not wc:
            return {}
        rpp = grid_flow.rows_per_page()
        lo, hi = grid_flow.page_row_range(grid_flow.current_page(), rpp, 1)
        need = []
        for e in self.storage.layout:
            cell = (int(e.get("col", 0)), int(e.get("row", 0)))
            if cell in wc:
                need.append((e, cell))
        # 2026-10-20 F2：禁区补上其他图标占位，否则被挤的图标会被分到
        # 已经站着人的格子上（预览/落定都叠）
        blocked = self._displace_blocked(wc, [c for _e, c in need])
        plan = grid_flow.displace_cells([c for _e, c in need], blocked,
                                        self.storage.columns, (lo, hi))
        moved = {}
        for e, cell in need:
            dst = plan.get(cell)
            if dst is None:
                continue
            e["col"], e["row"] = dst
            moved[e["id"]] = dst
        return moved

    # ---------- 锚定组件拖动实时预览（2026-10-18 M4） ----------
    def _widget_entry_rect(self, entry) -> set:
        """组件 entry 当前占位矩形（按 span 展开）。"""
        cs, rs = grid_flow.widget_span(self.storage, entry)
        col = max(0, int(entry.get("col", 0)))
        row = max(0, int(entry.get("row", 0)))
        return {(col + dc, row + dr) for dc in range(cs) for dr in range(rs)}

    def _restore_snap(self, snap):
        """按快照写回布局（仅内存；落盘由调用方决定）。"""
        by_id = {e.get("id"): e for e in self.storage.layout}
        for eid, col, row in snap:
            e = by_id.get(eid)
            if e is not None:
                e["col"], e["row"] = col, row

    def _on_widget_hover(self, widget_id: str, gx: int, gy: int):
        """锚定组件拖动悬停（widget_base.anchorHover，指针全局坐标）。

        与图标拖动同一套实时预览语义（M2）：按指针位置算组件新占位
        矩形，把矩形内图标实时挤到最近空格——组件跟着手指走，图标
        同步让位，不等松手。只改内存 + 动画；松手由 anchorDrop 回退，
        组件自身吸附落定后的 gridChanged 按最终占位做正式 displace。
        """
        entry = next((w for w in self.storage.widgets
                      if w.get("id") == widget_id), None)
        if entry is None or not entry.get("anchored") or entry.get("hidden"):
            return
        if grid_flow.free_mode(self.storage):
            return      # v6 §1.3：无界模式组件不占格，图标不被挤走
        cs, rs = grid_flow.widget_span(self.storage, entry)
        # 反馈 5：gx/gy 是组件**自身左上角**的全局坐标（widget_base 松手前
        # 报的就是 self.pos()，与 _snap_to_grid 的 pos_cell(self.x(), self.y())
        # 同口径）——按它逐格，rect 才正好覆盖组件真实 footprint。旧实现收
        # 的是指针坐标：2×1 组件抓中间拖时指针在第 2 格、左缘在第 1 格，
        # 预览矩形少算左格 → "左侧 1×1 不碰撞"。
        col, row = self._cell_at(self.mapFromGlobal(QPoint(gx, gy)))
        # v6 §4：预览矩形按 span 夹紧（clamp_widget_cell）——旧实现只夹
        # 列不夹行，2×2 组件拖到页底最后一行时矩形含页外行，图标被挤到
        # 视野外、组件松手吸附时还会跳一格
        col, row = grid_flow.clamp_widget_cell(self.storage, col, row, cs, rs)
        rect = {(col + dc, row + dr) for dc in range(cs) for dr in range(rs)}
        if self._widget_preview_rect == rect:
            return
        if self._widget_snap is None:
            # 进入预览：快照当前布局（松手回退基准）
            self._widget_snap = [(e.get("id"), int(e.get("col", 0)),
                                  int(e.get("row", 0)))
                                 for e in self.storage.layout]
        else:
            # 预览态连续移动：先回到快照再按新矩形 displace，
            # 否则上一次预览的挤位会叠加漂移
            self._restore_snap(self._widget_snap)
        # 其他组件的占位仍要避开；本组件的旧占位释放（图标可回填）
        comp_blocked = rect | (self._widget_cells() - self._widget_entry_rect(entry))
        # 2026-10-19 反馈 7b：批量分配（互不重复）。旧实现逐个
        # nearest_free_cell 用同一份静态 blocked，两个图标会被分到同一格
        # ——"排满后再拖组件，图标直接叠在一起"。
        rpp = grid_flow.rows_per_page()
        lo, hi = grid_flow.page_row_range(grid_flow.current_page(), rpp, 1)
        need = []
        for e in self.storage.layout:
            c, r = int(e.get("col", 0)), int(e.get("row", 0))
            if (c, r) in comp_blocked:
                need.append((e, (c, r)))
        # 2026-10-20 F2：禁区再补「其他图标占位」——只给组件占位时
        # displace_cells 会把图标分到别人正占着的格上（预览直接叠格）
        blocked = self._displace_blocked(comp_blocked, [c for _e, c in need])
        plan = grid_flow.displace_cells([c for _e, c in need], blocked,
                                        self.storage.columns, (lo, hi))
        moves = {}
        for e, cell in need:
            dst = plan.get(cell)
            if dst is None:
                continue
            e["col"], e["row"] = dst
            moves[e["id"]] = dst
        self._widget_preview_rect = rect
        # 反馈 5：拖动中就把目标占位用红框画出来（整 span）——与图标拖动的
        # "当前格红底"同一语言，组件用户也能看见"我要占这几格、会撞到什么"
        self._set_highlight((col, row), (cs, rs))
        self._animate_icons_to_layout(moves, sync_all=True)

    def _on_widget_drop(self, widget_id: str, gx: int, gy: int):
        """组件松手：**只撤预览装饰，一个图标都不动**。

        v10 反馈 25/28（两次都报"松手先漂回冲突格再让位"）：根因是**时机**。
        `widget_base` 里 `anchorDrop.emit()` 排在 `_snap_to_grid()` **之前**
        （见 widget_base 931/932 行），所以到这里组件的 `entry.col/row` 还是
        旧格，`_displace_from_widgets()` 只能按旧占位算——拖到冲突格时它算出
        "没有图标需要挪"（图标此刻在预览后的空格里），于是走 `_sync_icons()`
        把图标**动画送回原位**（正是用户看到的"漂回冲突格"）；等吸附动画结束、
        `_write_occupancy` 无条件发 `gridChanged`，才再挤一次——两段动画。

        现在这里只做**数据**回退（`_restore_snap` 只改 entry.col/row，不动
        视觉），还原后的挤位交给 `_settle_widget_snap()`：gridChanged 到达时
        （或 450ms 兜底）那时 entry 已是最终落点，一次算准、一次动画。
        数据必须立刻回退（T7 契约：drop 后 cells 与预览前一致），只是不再
        顺手 `_sync_icons()` 把视觉也弹回去。
        """
        if self._widget_snap is not None:
            self._restore_snap(self._widget_snap)   # 数据回退，视觉留在预览位
        self._widget_preview_rect = None
        # 反馈 5：红框是组件拖动期间画的，松手必须撤（否则留在桌面上）
        self._hl_cell = None
        self._hl_span = (1, 1)
        self.update()
        if self._widget_snap is not None:
            # 兜底：组件落回原格时 gridChanged 也会发（_write_occupancy 里
            # `changed or anchored`），但低版本组件/异常路径可能不发——450ms
            # 后仍未消费就自己收尾（吸附动画 240ms + 余量）。
            QTimer.singleShot(450, self._settle_widget_snap)

    def _settle_widget_snap(self):
        """组件**落定后**收尾：按最终占位一次性还原 + 挤位（幂等）。

        只有一次 `_animate_icons_to_layout` 调用，targets 来自最终布局；
        预览已经把图标摆到位的那些 start == end，`animate_to` 早退，所以
        不会出现"先回去再出来"的第二段动画。
        """
        snap = self._widget_snap
        if snap is None:
            return
        self._widget_snap = None
        self._restore_snap(snap)          # 数据回到拖动前（视觉仍在预览位）
        moved = self._displace_from_widgets()
        if moved:
            self.storage.save()
            # sync_all=True 必不可少：预览里被挪走、但最终落点不再需要它的
            # 图标不在 moved 里，不补动画就会停在预览时的空格上（数据在
            # 原位、视觉在别处），下一次 sync 才跳回去——W2 判据当场抓到。
            self._animate_icons_to_layout(moved, sync_all=True)
            return
        # 最终落点没压到任何图标（含"拖回原地"）：预览挪走的图标滑回原位
        self._sync_icons()

    def _reflow_preview(self, dragged_id: str, cell):
        """实时让位预览（2026-10-18 M2：iOS 局部让位，reflow_moves）。

        与旧 reflow_order 路径的区别：dragged **精确落到 target（含
        空格）**，只有新旧位置之间的图标滑动一格——修复 P2 病根 1
        （空格永不生效）与病根 2（拖到右侧空白被夹紧到最后一列 →
        半幅布局平移；现在 cols 由 GridMetrics 铺满，落点即所见）。
        目标格由调用方从指针全局位置反算（追逐漂移已消灭）。

        2026-10-19 v6 §3.2：auto_arrange=False（**默认，自由移动**）时
        不走递进——只落 dragged 自己、原位留空（_free_moves）；True 才
        走 reflow_moves 递进（旧 M2 行为）。

        2026-10-19 v6 §5.0：多选拖动（_drag_multi）走 multi_move 整块
        刚性平移预览——旧代码直接 return，多选拖动全程无预览、松手瞬间
        跳位；且 drop 路径逐成员 clamp 会聚列叠格（反馈病根）。
        """
        if dragged_id is None:
            return
        if grid_flow.free_mode(self.storage):
            return      # v6 §1.3：无界模式无让位——自由摆布、可重叠
        if self._drag_multi:
            self._reflow_preview_multi(dragged_id, cell)
            return
        if dragged_id not in self.icons:
            return
        # 2026-10-19 反馈 4/9：目标格被**别的图标**占着时不立刻让位——
        # 用户可能想合并（悬停 450ms 出提示）。但也**不能一直不动**：
        # 手机语义是"边缘重叠并悬浮一会儿 -> 实时把对方挤走"。折中：
        # 起一个推让计时器，停留够久再算位移（松手/移开即取消）。
        # 拖动中每次 dragMove 都会重置它，只有真正停住才触发。
        #
        # 2026-10-21 反馈 7 起这里是**纯粹的让位区入口**：合并候选的拖动
        # 根本到不了本窗口（iconwidget._hit_target 按拖影中心判核心区，
        # 压住对方正中的合成事件直接发给目标图标），所以"目标被占"=
        # 用户要的就是挤走——不再需要 F3 那种按重叠比 defer 的门控
        # （那层门控把 0.15~0.30 整段变成死区：不合并也不挤走，正是
        # 反馈 7 报的"实时挤开效果没有了"）。提示优先改由路由结构 +
        # dragLeaveEvent 撤销推让来保证（不双触发）。
        if not self._drag_multi:
            occ = self.storage.occupant(cell[0], cell[1], exclude_id=dragged_id)
            if occ is not None:
                self._push_cell = cell
                self._push_dragged = dragged_id
                self._push_timer.start()
                return
        self._push_timer.stop()
        self._push_cell = None
        if self._auto_arrange_at(cell[1]):
            # 自动排列：该页密排（v9 V2 起按页）——清单由 _arrange_moves 统一定义
            moves = self._arrange_moves(dragged_id, cell)
        else:
            moves = self._free_moves(dragged_id, cell)
        if not moves:
            return
        self._apply_preview_moves(dragged_id, moves)

    def _auto_arrange(self) -> bool:
        """全局自动排序开关（v6 §3.2）：False=自由移动（默认）。

        v9 V2 起**不再直接决定落位**——只有"该页没有独立规则"时才是
        它的值。判定请走 `_auto_arrange_at(row)` / `_arrange_moves()`，
        它们会把每页规则（`page_rules`）算进去。
        """
        return bool(self.storage.get_setting("auto_arrange"))

    def _auto_arrange_at(self, row) -> bool:
        """该行所属页是否自动排列（v9 V2：每页独立规则的唯一入口）。"""
        return page_rules.mode_for(self.storage, grid_flow.page_of(int(row)))

    def _arrange_moves(self, dragged: str, cell, rows=None):
        """落位清单唯一入口：自动排列 = 密排，否则 = 局部让位（保留空格）。

        反馈 4（v7e）：用户原话"自动排列的时候，用户只能手动控制小组件的
        位置，其余图标应该自动排列铺满整行后再排列到第二行"。旧实现两种
        情况下都走 `reflow_moves`（只把新旧位置之间的图标滑动一格）——
        别处的空格永远还在，勾了"自动排列图标"跟没勾一样。现在开关
        真正参与算法选择：开 -> `auto_arrange_moves` 从左上角连续密排。
        """
        if self._auto_arrange_at(cell[1]):
            return grid_flow.auto_arrange_moves(
                self.storage.layout, self.storage.columns, dragged, cell,
                extra_occupied=self._widget_cells(),
                rpp=grid_flow.rows_per_page(),
                auto_pages=page_rules.auto_pages(
                    self.storage, grid_flow.page_count(self.storage)))
        return grid_flow.reflow_moves(
            self.storage.layout, self.storage.columns, dragged, cell,
            rows=rows, extra_occupied=self._widget_cells())

    def _push_occupied(self):
        """悬停够久：把占着落点的图标实时挤走（手机 launcher 语义）。

        只在"拖到自己想放的位置并停住"时触发；拖动中不断重置计时器，
        所以快速划过不会推人。挤走方式跟随自动排列开关：
          开 -> reflow_moves（递进，后面的图标整体让一格）
          关 -> 与占位者互换（自由摆放的直觉，不产生空洞链）
        """
        cell, dragged = self._push_cell, self._push_dragged
        self._push_cell = None
        if cell is None or dragged is None:
            return
        if dragged not in self.icons:
            return
        occ = self.storage.occupant(cell[0], cell[1], exclude_id=dragged)
        if occ is None:
            return  # 已经空出来了（上次推让生效）
        if self._auto_arrange_at(cell[1]):
            moves = self._arrange_moves(dragged, cell)
        else:
            moves = None
        if not moves:
            moves = self._free_moves(dragged, cell)
        if not moves:
            return
        self._apply_preview_moves(dragged, moves)

    def _cancel_push(self):
        """松手/取消：撤掉未触发的推让（避免松手后对方突然跳走）。"""
        self._push_timer.stop()
        self._push_cell = None
        self._push_dragged = None

    def _reflow_preview_multi(self, payload: str, cell):
        """多选拖动实时预览（v6 §5.0）：整块刚性平移跟着指针走。

        主条目 = 真正被抓住的图标（_drag_entry_id）——payload 是
        sorted 逗号串，拿首 id 当时条目会让整块锚点跳错（用户抓的是
        块内哪个图标，哪个才落指针）。
        布局已被上次预览改写也没关系：multi_move 在改写后布局上算的
        等价于增量 delta（块内相对偏移守恒，与单选 reflow 的增量预览
        同构；交换出去的占据者不会再次被换回）。
        """
        ids = [p for p in payload.split(",") if p]
        primary = self._drag_entry_id
        if primary not in ids:
            primary = ids[0] if ids else None
        if primary is None:
            return
        live = {e.get("id") for e in self.storage.layout}
        if primary not in live:
            return
        moves = grid_flow.multi_move(self.storage.layout,
                                     self.storage.columns, primary, cell,
                                     set(ids),
                                     extra_occupied=self._widget_cells())
        if not moves:
            return
        self._apply_preview_moves(primary, moves)

    def _free_moves(self, dragged_id: str, cell) -> dict:
        """自由移动位移清单（v6 §3.2）：只落 dragged 自己，原位留空。

        目标被非 dragged 占据时与占据者**交换**到源格（自由摆放的
        直觉：不丢条目、不覆盖）；目标落在组件占位格上则拒绝（不覆盖
        组件，拖动无效——与 reflow 的 extra_occupied 语义一致）。
        """
        by_id = {e.get("id"): e for e in self.storage.layout}
        src = by_id.get(dragged_id)
        if src is None:
            return {}
        old = (int(src.get("col", 0)), int(src.get("row", 0)))
        if cell == old:
            return {}
        occupant = None
        for e in self.storage.layout:
            if e.get("id") == dragged_id:
                continue
            if (int(e.get("col", 0)), int(e.get("row", 0))) == cell:
                occupant = e
                break
        if occupant is None and cell in self._widget_cells():
            return {}                      # 组件占位格：拒绝
        moves = {dragged_id: cell}
        if occupant is not None:
            moves[occupant["id"]] = old    # 交换到源格
        return moves

    def _apply_preview_moves(self, dragged_id: str, moves: dict):
        """预览位移落地共用路径：去重 -> 写内存 -> 最小集合动画。"""
        if moves == self._preview_moves:
            return  # 无变化不重算：dragMove 频率很高，省掉无用动画重启
        self._preview_moves = moves
        self._preview_drag = dragged_id
        # 只改内存：落盘发生在 drop（commit）或根本不来（cancel 回退）
        by_id = {e.get("id"): e for e in self.storage.layout}
        for eid, (c, r) in moves.items():
            e = by_id.get(eid)
            if e is not None:
                e["col"], e["row"] = c, r
        self._animate_icons_to_layout(moves, skip=dragged_id)
        self._cull_moved_icons(moves)   # v6 §4：跨页位移按落点页显隐

    def _animate_icons_to_layout(self, moves: dict, skip=None, sync_all=False):
        """把发生位移的图标弹簧滑到新格（reduced-motion 时直接跳位）。

        只动画 moves 里的图标——位移集合最小化是 M2 的核心性质，
        顺带避免每次 dragMove 全量迭代 200 个图标。

        sync_all=True（组件拖动实时预览专用，反馈 1）：**除了 moves，
        还要把"布局位置与视觉位置不一致"的图标全部滑回去**。
        预览每次重算都先 `_restore_snap` 把数据回退到快照——上一轮被
        挤走、这一轮已回到原格的图标不在本次 moves 里，不补动画的话
        它们会**停在上一轮的目标格**上，与刚被挤过去的图标叠在一起，
        正是用户看到的"避让预览阶段就堆叠"。
        """
        targets = dict(moves or {})
        if sync_all:
            for e in self.storage.layout:
                eid = e.get("id")
                if eid in targets or eid == skip:
                    continue
                icon = self.icons.get(eid)
                if icon is None:
                    continue
                tx, ty = self._entry_pos(e)
                if icon.pos() != QPoint(tx, ty):
                    targets[eid] = (int(e.get("col", 0)), int(e.get("row", 0)))
        for eid in targets:
            if eid == skip:
                continue
            icon = self.icons.get(eid)
            if icon is None:
                continue
            entry = self.storage.find_entry(eid)
            if entry is None:
                continue
            icon.animate_to(*self._entry_pos(entry))

    def _commit_preview(self):
        """落点有效：把 preview 写盘。图标已在位上，无需 rebuild。"""
        self._cancel_push()
        if self._preview_moves is None:
            return
        dragged = self._preview_drag
        moves = self._preview_moves
        self._push_undo(self._layout_snap)
        self.storage.save()
        if dragged and dragged in self.icons:
            entry = self.storage.find_entry(dragged)
            icon = self.icons[dragged]
            if entry is not None:
                icon.move(*self._entry_pos(entry))
            icon.set_drag_ghost(False)
        # 2026-10-19 反馈 4 连带：占用格改成"松手才换"后，被交换走的
        # 占据者不会经过 _apply_preview_moves 的动画路径（拖动中没有预览），
        # 只 move 拖拽源会让 storage 与画面不一致（下一帧视觉跳变）。
        # 这里把 moves 里其余成员一并弹簧落位——已到位的 animate_to 早退。
        for eid in moves:
            if eid == dragged:
                continue
            icon = self.icons.get(eid)
            entry = self.storage.find_entry(eid)
            if icon is None or entry is None:
                continue
            icon.animate_to(*self._entry_pos(entry))
            icon.set_drag_ghost(False)
        self._preview_moves = None
        self._preview_drag = None
        self._layout_snap = None
        self._cull_page_icons()      # v6 §4：提交后按落点页显隐

    def _push_undo(self, snap):
        """把改动前的布局快照压入撤销栈（Ctrl+Z 回退用，最多 20 步）。

        覆盖让位/多选平移等布局类操作（指导文档 §2.3/§5-P0-3；
        组件推动与文件夹增删成员在 M3-M5 接入同一条栈）。
        """
        if snap is None:
            return
        if self._undo_stack and self._undo_stack[-1] == snap:
            return  # 与栈顶相同不重复压
        self._undo_stack.append(snap)
        del self._undo_stack[:-20]

    def _undo_layout(self):
        """Ctrl+Z：回退到上一个布局快照，全体图标弹簧滑回。"""
        if not self._undo_stack:
            return
        snap = self._undo_stack.pop()
        by_id = {e.get("id"): e for e in self.storage.layout}
        for eid, col, row in snap:
            e = by_id.get(eid)
            if e is not None:
                e["col"], e["row"] = col, row
        self.storage.save()
        self._clear_preview()
        for eid, icon in self.icons.items():
            entry = by_id.get(eid)
            if entry is None:
                continue
            icon.animate_to(*self._entry_pos(entry), duration=220, damping=1.0)
        self._cull_page_icons()      # v6 §4：撤销回退后按当前页显隐
        self.update()

    def _reflow_commit_or_compute(self, dragged_id: str, cell):
        """单选落位：有预览就直接提交（落点与预览一致，避免坐标反算偏差）；
        没有预览（拖动中未经 dragMove 直达）就按落点现算一次。

        2026-10-18 M4：两条路径都走 _commit_preview 的动画落位——
        不再 rebuild（实例保留：悬停态、进行中动画、选中态不丢；
        原来这里 rebuild 会把全场图标瞬间跳位，正是 P2 病根 4）。
        """
        if self._preview_moves is not None and self._preview_drag == dragged_id:
            self._commit_preview()
            return
        if self._auto_arrange_at(cell[1]):
            moves = self._arrange_moves(dragged_id, cell)
        else:
            moves = self._free_moves(dragged_id, cell)
        if not moves:
            # 预览在飞但落点无效（如陈旧 payload：drop 携带的 id 已不在
            # 布局）——必须先回退快照再落盘。旧代码只 _clear_preview()，
            # 预览已改写的内存布局被留下：图标停在原位、storage 却是
            # 预览态（probe15 G11c 败因：半截预览态）。反过来先 save 会把
            # 半截状态写进 disk，顺序不能倒。
            self._cancel_preview()
            self.storage.save()
            return
        by_id = {e.get("id"): e for e in self.storage.layout}
        for eid, (c, r) in moves.items():
            e = by_id.get(eid)
            if e is not None:
                e["col"], e["row"] = c, r
        # 借提交路径收尾：压撤销栈 + 落盘 + 动画落位 + 清预览
        self._preview_moves = moves
        self._preview_drag = dragged_id
        self._commit_preview()

    def _cancel_preview(self):
        """取消：按快照回退布局，所有图标弹簧滑回原位。"""
        snap, self._layout_snap = self._layout_snap, None
        self._preview_moves = None
        self._preview_drag = None
        if snap is None:
            return
        by_id = {e.get("id"): e for e in self.storage.layout}
        for eid, col, row in snap:
            e = by_id.get(eid)
            if e is not None:
                e["col"], e["row"] = col, row
        for eid, icon in self.icons.items():
            entry = by_id.get(eid)
            if entry is None:
                continue
            icon.animate_to(*self._entry_pos(entry), duration=220, damping=1.0)
            icon.set_drag_ghost(False)
        self._cull_page_icons()      # v6 §4：回退后按当前页显隐
        self.update()

    def _clear_preview(self):
        self._preview_moves = None
        self._preview_drag = None
        self._layout_snap = None

    # ---------- 业务 ----------
    def _on_activate(self, app):
        if app:
            appsource.launch(app)

    def _on_open_location(self, app):
        # 反馈 6：定位规则——普通文件指它自己、快捷方式指**原文件**、
        # 桌面文件指桌面文件夹；壳层入口（ms-settings: 等）没有文件位置。
        target = appsource.reveal_path_for(app)
        if target:
            appsource.reveal_in_explorer(target)

    def add_app(self, app: dict):
        # M6：新条目落到用户当前这一页的第一个空格（当前页满才回退
        # 全局第一个空格——否则用户在第 2 页点添加、图标加到第 0 页
        # 会"消失"）
        slot = grid_flow.first_free_cell_in_page(self.storage, self._page)
        if slot is not None:
            self.storage.add_app(app, *slot)
        else:
            self.storage.add_app(app)
        self.storage.save()
        # M4：增量新增（新图标淡入；指定格被占时的挤走位移走动画）
        self._sync_structure()

    # ---------- 桌面目录自动同步（v6 §7.4） ----------
    def set_auto_sync(self, on: bool):
        """启停桌面目录监听（设置项 / 启动时按 auto_sync_desktop 决定）。

        监听走 QFileSystemWatcher（Windows 底层即 ReadDirectoryChangesW，
        零新增依赖），debounce 1s 后调 storage.sync_desktop_incremental()。
        """
        from .desktop_watch import DesktopWatcher
        if on:
            if self._watcher is None:
                self._watcher = DesktopWatcher(
                    self.storage, on_sync=self._on_desk_sync, parent=self)
            else:
                self._watcher.start()
        elif self._watcher is not None:
            self._watcher.stop()

    def _on_desk_sync(self, added: int, removed: int):
        """桌面目录变动：增量补/删图标。

        只做结构同步（_sync_structure 走弹簧动画），**不重排、不翻页**——
        文件系统事件是被动发生的，用户摆放的位置必须原样保留。
        """
        self._dissolve_degenerate_folders()   # v14j：外部移走文件后 folder 解除
        self._sync_structure()
        self._cull_page_icons()
        self.storage.save()

    def _dissolve_degenerate_folders(self):
        """成员 ≤1 的文件夹自动解散（v14j）。

        storage 的增量/重扫会先修剪"磁盘上已消失"的文件成员；这里把因此
        只剩 0/1 名的文件夹收掉（最后一名放回桌面、文件夹本体移除）。
        2026-10-25 用户反馈：移走文件夹内的文件只剩一个时，文件夹不自行
        解除——当时三条弹窗内路径都有检查，唯独外部移走/删除绕过了。
        """
        for e in list(self.storage.layout):
            if e.get("type") != "folder":
                continue
            apps = e.get("apps") or []
            if len(apps) == 1:
                self._dissolve_folder(e)
            elif not apps:
                self._on_dissolve(e.get("id"))

    def rescan_desktop(self):
        """重新扫描桌面文件：补新增、清丢失（仅布局，不动文件）。
        M4：增量同步——新增淡入 / 丢失移除 / compact 移位走弹簧动画。

        v6 §4：rescan 末尾 pack_layout 把全体图标铺回第 0 页（§3.1
        播种语义）——若当前页 >0，不停在已被清空的旧页（那会看到空
        桌面），显式回到第 0 页展示重扫结果。
        """
        added, removed = self.storage.rescan_desktop()
        self._dissolve_degenerate_folders()   # v14j：重扫后收掉退化文件夹
        self._sync_structure()
        if self._page != 0:
            self.goto_page(0, animate=False)
        return added, removed

    def _on_remove_entry(self, entry_id):
        entry = self.storage.find_entry(entry_id)
        self.storage.remove_entry(entry_id)
        self._remove_icon(entry_id)     # M4：只移除目标图标实例
        self.storage.compact_layout()
        self.storage.save()
        self._sync_icons()              # compact 的补位动画
        # 桌面文件只是从整理布局移除，文件本身仍在桌面文件夹
        if entry and entry_app(entry).get("kind") == "file":
            from .tray import notify_via_tray  # 延迟导入避免循环
            notify_via_tray("已从整理桌面移除", "文件仍保留在桌面文件夹中，可通过「重新扫描桌面文件」找回")

    def _on_dissolve(self, entry_id):
        entry = self.storage.find_entry(entry_id)
        if not entry or entry.get("type") != "folder":
            return
        for app in entry.get("apps", []):
            self.storage.add_app(app)
        self.storage.remove_entry(entry_id)
        self.storage.compact_layout()
        self.storage.save()
        self._sync_structure()          # M4：成员增量淡入 + 文件夹图标移除

    def _dissolve_folder(self, folder):
        """反馈 10：文件夹只剩 1 个成员时自动解除。

        “一个成员的文件夹”没有存在意义——用户要的是直接看到那个图标。
        最后一个成员弹回桌面：网格模式优先占文件夹原格（视觉连续：
        文件夹在哪消失、图标就在哪出现），原格被占则退第一个空格；
        无界模式交给 add_app 的自由落点。返回 True 表示已解除。
        """
        apps = folder.get("apps") or []
        if len(apps) != 1:
            return False
        last = apps[0]
        col = row = None
        if not grid_flow.free_mode(self.storage):
            try:
                col, row = int(folder.get("col")), int(folder.get("row"))
            except (TypeError, ValueError):
                col = row = None
        self.storage.remove_entry(folder.get("id"))
        if col is not None and self.storage.occupant(col, row) is not None:
            col = row = None        # 原格已被占：不挤走无关图标
        self.storage.add_app(last, col, row)
        return True

    def _on_merge(self, dragged_payload: str, target_id: str):
        # 2026-10-19 反馈 5：合并前**回退**拖动预览（不是清掉状态）。
        # 旧实现 _clear_preview() 丢弃了快照回退，被递进的图标就停在预览
        # 位置，用户看到的是"加了个文件夹，整张桌面自己重排了一遍"。
        self._cancel_preview()
        target = self.storage.find_entry(target_id)
        if target is None:
            return

        # 拖的是文件夹成员（payload: "M:fid:idx"，M: 标记避免与含冒号的条目 id 混淆）
        # -> 并入目标
        if dragged_payload.startswith("M:"):
            fid, _, idx = dragged_payload[2:].rpartition(":")
            src = self.storage.find_entry(fid)
            if src is None or src.get("type") != "folder" or not idx.isdigit():
                return
            idx = int(idx)
            if not (0 <= idx < len(src.get("apps", []))):
                return
            app = src["apps"][idx]
            if target["type"] == "folder":
                if app not in target["apps"]:
                    target["apps"].append(app)
            else:
                members = [m for m in (target.get("app"), app) if m]
                target["type"] = "folder"
                # 空 dict 而非 None：`e.get("app", {})` 的默认值只在键不存在时
                # 生效，写 None 会让所有链式 `.get` 在文件夹条目上炸（坑 #39）
                target["app"] = {}
                target["apps"] = members
            src["apps"].pop(idx)
            # 反馈 10：成员被并走后源文件夹只剩 1 个 → 自动解除
            if len(src.get("apps") or []) == 1:
                self._dissolve_folder(src)
            elif not src["apps"]:
                self.storage.remove_entry(fid)
            self.storage.compact_layout()
            self.storage.save()
            self._sync_structure()     # M4：成员并入/空文件夹移除走增量
            if self._popup is not None:
                self._popup.refresh()
            return

        # B5：多选拖动的 payload 是 "id1,id2,..."（逗号串），旧实现把整串
        # 当单个 id 查找 → find_entry 返回 None → 静默吞掉。拆开逐个并入；
        # 语义固定为"落点目标承载文件夹"，目标先是图标就升级成文件夹
        ids = [i for i in str(dragged_payload).split(",") if i]
        ids = [i for i in ids if i != target["id"]]
        if not ids:
            return
        if len(ids) > 1:
            if target["type"] != "folder":
                first = self.storage.find_entry(ids[0])
                if first is None:
                    return
                members = [m for m in (target.get("app"), first.get("app")) if m]
                target["type"] = "folder"
                target["app"] = {}     # 同上：None 会让链式 `.get` 在文件夹上炸
                target["apps"] = members
                self.storage.remove_entry(first["id"])
                rest = ids[1:]
            else:
                rest = ids
            for eid in rest:
                e = self.storage.find_entry(eid)
                if e is not None and e["id"] != target["id"]:
                    self._absorb(target, e)
            self.storage.compact_layout()
            self.storage.save()
            self._sync_structure()
            if self._popup is not None:
                self._popup.refresh()
            return

        dragged = self.storage.find_entry(ids[0])
        if dragged is None:
            return

        if dragged["type"] == "folder" and target["type"] == "folder":
            self._absorb(target, dragged)
        elif target["type"] == "folder":
            self._absorb(target, dragged)
        elif dragged["type"] == "folder":
            self._absorb(dragged, target)
        else:
            members = [m for m in (target.get("app"), dragged.get("app")) if m]
            target["type"] = "folder"
            target["app"] = {}     # 同上：None 会让链式 `.get` 在文件夹上炸
            target["apps"] = members
            self.storage.remove_entry(dragged["id"])
        self.storage.compact_layout()
        self.storage.save()
        # M4：合并是结构变化（条目移除 + type 变更）——增量同步。
        # target 的图标实例保留：AppIcon.is_folder 动态读 entry，自动
        # 按文件夹样式重绘，无需重建。
        self._sync_structure()
        if self._popup is not None:
            self._popup.refresh()

    def _absorb(self, folder_entry, other):
        members = other.get("apps", []) if other.get("type") == "folder" else [other.get("app")]
        for m in members:
            if m and m not in folder_entry["apps"]:
                folder_entry["apps"].append(m)
        self.storage.remove_entry(other["id"])

    # ---------- 文件夹弹窗 ----------
    def open_folder(self, entry_id, by_hover: bool = False):
        entry = self.storage.find_entry(entry_id)
        if not entry or entry.get("type") != "folder":
            return
        if self._popup is not None:
            if self._popup.entry.get("id") == entry_id:
                self._close_popup()      # 再次点击同一文件夹 = 关闭（iOS 语义）
                return
            self._close_popup()
        icon = self.icons.get(entry_id)
        anchor_rect = None
        if icon is not None:
            # 传图标完整全局矩形：液态玻璃弹窗的展开动画起点
            anchor_rect = QRect(icon.mapToGlobal(QPoint(0, 0)), icon.size())
        popup = FolderPopup(self.storage, entry, anchor_rect)
        # v14：悬停展开的弹窗在鼠标离开图标与弹窗后自动收起；点击打开的
        # 弹窗保持原行为（用户是主动要用它，不该自己消失）
        popup.set_opened_by_hover(by_hover)
        popup.launchApp.connect(self._on_activate)
        popup.extractRequested.connect(self._on_extract)
        popup.removeMember.connect(self._on_remove_member)
        popup.memberAdded.connect(self._on_popup_member_added)
        popup.renamed.connect(lambda: self._refresh_folder_icon(entry_id))
        popup.tintChanged.connect(lambda: self._refresh_folder_icon(entry_id))
        popup.closed.connect(self._on_popup_closed)
        self._popup = popup
        popup.show()
        # M5：幕布先挂（弹窗下层层）——压暗桌面带内所有元素；点击幕布
        # 关闭弹窗（与 DesktopWindow 空白点击关闭双保险）。
        scrim = ScrimWindow()
        scrim.clicked.connect(self._close_popup)
        # 2026-10-19 反馈：幕布铺满桌面带且层级最高，拖放必须由它转发到
        # 桌面窗口——否则"从文件夹里把图标拖出来"的 drop 会被幕布吞掉
        scrim.dropReceived.connect(self._drop_from_scrim)
        # 反馈 6：拖动**过程中**的落点预览也走幕布转发（红框 / 让位）
        scrim.dragMoved.connect(self._preview_from_scrim)
        self._scrim = scrim
        scrim.show()
        scrim.fade_in()
        # 挂桌面带：不用 StaysOnTopHint（那会盖住所有应用窗口）。幕布与
        # 弹窗都插 Progman 正上方；弹窗再 attach_above_band() 插到桌面带
        # 内最顶（压得住整理窗口与所有小组件）。必须 insert_above_window
        # （普通窗口语义）：attach_to_desktop 的 below_hwnd 分支是插到
        # 参照窗口下方——弹窗在整理窗口之下会被其全窗口 HTCLIENT 命中
        # 测试挡住，点不到（2026-10-18 实测）。
        if self.attached:
            win32_layer.attach_to_desktop(
                scrim, layer=win32_layer.BAND_LAYER_SCRIM)
            # v6 §6.2 覆盖性：幕布必须吞掉桌面层所有点击（不穿透）。
            # 只插 Progman 正上方会让幕布落在整理窗口之下——桌面窗口是
            # 全窗口 HTCLIENT，命中测试先收到点击，幕布形同虚设（图标层
            # 实际可点）。与弹窗同机制：插到桌面带内最顶。
            top = win32_layer.band_topmost_hwnd(exclude_hwnd=int(scrim.winId()))
            if top:
                win32_layer.insert_above_window(int(scrim.winId()), top)
            self._band_guard.track(scrim, layer=win32_layer.BAND_LAYER_SCRIM)
            win32_layer.attach_to_desktop(
                popup, layer=win32_layer.BAND_LAYER_POPUP)
            popup.attach_above_band()
            self._band_guard.track(popup, layer=win32_layer.BAND_LAYER_POPUP)
        self._folder_open_icon = icon
        if icon is not None:
            icon.set_folder_open(True)

    def _close_popup(self):
        """主动关闭弹窗（切文件夹 / 同文件夹二击 / Esc / 幕布点击 / 隐藏桌面）。

        与 _on_popup_closed（弹窗自行关闭路径：× 按钮、空文件夹 refresh
        自动关等）分离：主动关闭先摘除 closed 信号——旧弹窗的收起动画
        要 200ms 才发 closed，不摘的话会误清随后新建的弹窗引用
        （open_folder 连续切换文件夹的竞态）。
        """
        popup = self._popup
        self._popup = None
        self._exit_folder_open_state()
        if popup is not None:
            try:
                popup.closed.disconnect(self._on_popup_closed)
            except Exception:
                pass
            self._untrack_band_widget(popup)
            self._keep_until_destroyed(popup)
            popup.close()
        scrim = self._scrim
        self._scrim = None
        if scrim is not None:
            self._untrack_band_widget(scrim)
            self._keep_until_destroyed(scrim)
            scrim.fade_out_then_close()

    def _untrack_band_widget(self, w):
        """短命窗口（弹窗/幕布）关闭前从守护摘除。

        2026-10-25 事故：它们 open 时被 track、close 后从不 untrack，
        句柄一死 BandGuard 就当"被外部销毁"去 destroy()+show() 拉活，
        多次 hover 开合喂爆复活计数器（120 秒 8 次）→ 恢复原生图标并
        整个程序退出接管。必须在句柄还活着时摘（winId() 此刻有效）。
        """
        guard = getattr(self, "_band_guard", None)
        if guard is None or w is None:
            return
        try:
            guard.untrack(int(w.winId()))
        except (RuntimeError, AttributeError, TypeError):
            pass

    def _keep_until_destroyed(self, w):
        """替正在播收起动画的窗口保住 Python 引用。

        弹窗/幕布的收起动画（180~200ms）挂在窗口自己身上，唯一的 Python
        引用一旦被清（self._popup=None），wrapper 被 GC 就连带销毁 C++
        窗口——动画中途夭折、_really_close 不执行、unregister 残留。
        挂到 destroyed 信号再放手。
        """
        keep = getattr(self, "_closing_band_widgets", None)
        if keep is None:
            keep = self._closing_band_widgets = []
        keep.append(w)
        try:
            w.destroyed.connect(
                lambda *_, p=w: self._discard_closing_widget(p))
        except (RuntimeError, TypeError):
            self._discard_closing_widget(w)

    def _discard_closing_widget(self, w):
        keep = getattr(self, "_closing_band_widgets", None)
        if keep:
            self._closing_band_widgets = [x for x in keep if x is not w]

    def _on_popup_closed(self):
        """弹窗自行关闭后的收尾（closed 信号）：清引用 + 源图标退出
        打开态 + 幕布淡出销毁。幂等：_close_popup 已清场时为空操作。"""
        popup = self._popup
        self._popup = None
        self._untrack_band_widget(popup)
        self._keep_until_destroyed(popup)
        # 反馈 6：拖出预览的红框是幕布转发驱动的，弹窗一关必须撤掉，
        # 否则桌面上留一个永不消失的红框（成员的 dragFinished 送不到
        # DesktopWindow，_on_drag_finished 的清理路径不走这里）
        self._hl_cell = None
        self._hl_span = (1, 1)
        self._exit_folder_open_state()
        scrim = self._scrim
        self._scrim = None
        if scrim is not None:
            self._untrack_band_widget(scrim)
            self._keep_until_destroyed(scrim)
            scrim.fade_out_then_close()

    def _exit_folder_open_state(self):
        """源图标退出打开态（高亮描边撤销）。图标可能已被增量移除
        （文件夹解散走 _remove_icon），deleteLater 后的部件访问抛
        RuntimeError——吞掉即可。"""
        icon = self._folder_open_icon
        self._folder_open_icon = None
        if icon is not None:
            try:
                icon.set_folder_open(False)
            except RuntimeError:
                pass

    def _refresh_folder_icon(self, entry_id):
        """重命名 / 换色后刷新源图标（标签 + tooltip + 渐变重绘）。"""
        icon = self.icons.get(entry_id)
        if icon is not None:
            try:
                icon.refresh_meta()
            except RuntimeError:
                pass

    def set_icon_scale(self, entry_id, percent, refresh: bool = True) -> bool:
        """设置单个图标的大小（百分比，反馈 12）。

        100% 等价于"没配过"——直接删键，便于设置页的管理区只看差异项。
        取值范围 10%~400%：滑条给 50~200 的常用区间，数字框可键入更大
        值；低于 50% 时右键面板会显示警告，但不阻止（用户有权调小）。
        """
        e = self.storage.find_entry(entry_id)
        if e is None or e.get("type") != "app":
            return False
        try:
            pct = int(percent)
        except (TypeError, ValueError):
            return False
        pct = max(10, min(400, pct))
        if pct == 100:
            e.pop("icon_scale", None)
        else:
            e["icon_scale"] = round(pct / 100.0, 3)
        if refresh:
            self.storage.save()
            self.refresh_grid()
        return True

    def _on_icon_scale(self, entry_id, percent):
        """右键滑条改大小：120ms 合并 rebuild（拖动时可能连发几十次）。"""
        if not self.set_icon_scale(entry_id, percent, refresh=False):
            return
        t = getattr(self, "_icon_scale_timer", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(120)
            t.timeout.connect(self._flush_icon_scale)
            self._icon_scale_timer = t
        t.start()

    def _flush_icon_scale(self):
        self.storage.save()
        self.refresh_grid()

    def refresh_folder_popup(self):
        """设置页改了「文件夹默认大小」后让已打开的弹窗按新上限重排。"""
        if self._popup is not None:
            self._popup.refresh()

    def _on_folder_frame(self, entry_id, cols, rows):
        """右键「文件夹大小」（反馈 14）：写 entry["frame"] 并立即重排弹窗。

        设置的是**上限**；成员少时弹窗会按实际数量收缩（2 个 -> 2×2）。
        """
        if not self.storage.set_folder_frame(entry_id, cols, rows):
            return
        icon = self.icons.get(entry_id)
        if icon is not None:
            try:
                icon.refresh_meta()
            except RuntimeError:
                pass
        if self._popup is not None and self._popup.entry.get("id") == entry_id:
            self._popup.refresh()

    def _on_popup_member_added(self, entry_id):
        """＋ 添加面板 / 拖入：桌面条目并入文件夹。成员 apps 的追加由
        popup._add_member_by_id 完成，这里只把桌面上的源条目移出布局
        （M4 增量：不 rebuild）。"""
        e = self.storage.find_entry(entry_id)
        if e is None:
            return
        self.storage.remove_entry(entry_id)
        self.storage.compact_layout()
        self.storage.save()
        self._sync_structure()

    def _on_extract(self, payload: str, pos=None):
        """把文件夹成员拖出到桌面（落到哪个格子就用哪个格子）。
        payload: "M:fid:idx" 或 "fid:idx"（M: 标记可带可不带；
        fid 自身可能含冒号，故 rpartition 从最后一个冒号切）。

        pos: 松手点在桌面窗口内的坐标（可选）。给了就按那个格子落位；
        格子上已经有图标时退回"第一个空格"——**不挤走无关图标**
        （提取是用户主动把东西拿出来，不该顺手把别人挪走）。
        """
        if payload.startswith("M:"):
            payload = payload[2:]
        fid, _, idx = payload.rpartition(":")
        folder = self.storage.find_entry(fid)
        if not folder or folder.get("type") != "folder" or not idx.isdigit():
            return
        idx = int(idx)
        if not (0 <= idx < len(folder.get("apps", []))):
            return
        col = row = None
        if pos is not None:
            try:
                tc, tr = self._cell_at(pos)
                if self.storage.occupant(tc, tr) is None:
                    col, row = tc, tr
            except Exception:
                col = row = None
        app = folder["apps"].pop(idx)
        known = {e.get("id") for e in self.storage.layout}
        entry = self.storage.add_app(app, col, row)
        # 防御：若同一 id 已在桌面（历史数据里"桌面+文件夹"重复过），
        # add_app 会直接返回已有条目而不新增——不处理的话成员已从文件夹
        # pop 掉、桌面上那个又可能不在当前页，用户看到的就是"拖出来没了"。
        # 把它挪到松手处并确保可见，比静默丢弃好得多。
        if (entry is not None and entry.get("id") in known
                and col is not None and row is not None):
            entry["col"], entry["row"] = int(col), int(row)
        # 反馈 10：拖出后只剩 1 个成员 → 自动解除文件夹（最后一个一起弹回桌面）
        if len(folder.get("apps") or []) == 1:
            self._dissolve_folder(folder)
        elif not folder["apps"]:
            self.storage.remove_entry(fid)
        self.storage.compact_layout()
        self.storage.save()
        self._sync_structure()          # M4：提取成员增量淡入 + 空文件夹移除
        # M5：文件夹被解散时 refresh 自动关弹窗；仍有成员时刷新成员网格
        if self._popup is not None:
            self._popup.refresh()

    def _drop_from_scrim(self, md, gpos):
        """幕布上的落点转发到桌面窗口（2026-10-19 反馈：拖出成员丢失）。

        幕布是桌面带里最顶的全屏窗口，从弹窗往外拖必然经过它；它只负责
        把 (mime, 全局坐标) 送过来，真正的语义仍由桌面窗口的
        `_drop_internal` / `_drop_files` 决定（单一实现，不复制逻辑）。
        """
        if md is None:
            return
        pos = self.mapFromGlobal(gpos) if gpos is not None else QPoint(0, 0)
        if md.hasFormat(MIME_TYPE):
            self._hl_cell = None            # 反馈 6：松手即撤预览红框
            self._hl_span = (1, 1)
            self.update()
            self._drop_internal(md.data(MIME_TYPE).data().decode(), pos)
        elif md.hasUrls():
            self._drop_files(md.urls(), pos)

    def _preview_from_scrim(self, md, gpos):
        """幕布上**拖动中**的落点预览（2026-10-21 反馈 6）。

        从文件夹里拖出成员且没松手时，用户要能看见"这会落到哪、会不会
        撞到桌面上的东西"——与图标拖动同一套语言：目标格红底。红框位置
        必须与 `_on_extract` 的落点规则**同口径**（目标空 -> 目标格；
        被占 -> 第一个空格），否则用户看着红框松手、图标却落在别处。
        空格规则直接调 `storage.first_free_cell()`（add_app 用的就是它），
        不另写一份扫描——两套规则必然漂移。
        """
        if md is None or not md.hasFormat(MIME_TYPE):
            return
        payload = md.data(MIME_TYPE).data().decode()
        if not payload.startswith("M:"):
            # 非成员拖放（桌面图标 / 多选 / 外部文件）不在这里预览：
            # 它们的 dragMove 由 DesktopWindow.dragMoveEvent 直接收到
            # （没有幕布遮挡时），或由 _ole_active 路径处理
            return
        if grid_flow.free_mode(self.storage):
            return
        pos = self.mapFromGlobal(gpos) if gpos is not None else QPoint(0, 0)
        col, row = self._cell_at(pos)
        self._set_highlight(self._extract_landing_cell((col, row)))

    def _on_remove_member(self, payload: str):
        """文件夹内右键「移出文件夹」：成员弹回桌面（与拖出同一语义）。
        payload: "M:fid:idx" 或 "fid:idx"。

        2026-10-21 反馈 10：移出后文件夹只剩 1 个成员时自动解除文件夹
        （`_dissolve_folder`），最后一个成员一起弹回桌面。
        顺带修掉一个老 bug：旧实现只 pop 不 add_app，成员被无声丢弃
        （拖出能回桌面、右键移出却消失，同一意图两个入口两种命运）。
        """
        if payload.startswith("M:"):
            payload = payload[2:]
        fid, _, idx = payload.rpartition(":")
        folder = self.storage.find_entry(fid)
        if not folder or folder.get("type") != "folder" or not idx.isdigit():
            return
        idx = int(idx)
        if not (0 <= idx < len(folder.get("apps", []))):
            return
        app = folder["apps"].pop(idx)
        self.storage.add_app(app)          # 第一个空格弹回桌面
        remaining = len(folder.get("apps") or [])
        if remaining == 1:
            self._dissolve_folder(folder)  # 反馈 10：只剩一个 → 解除
        elif remaining == 0:
            self.storage.remove_entry(fid)
        self.storage.compact_layout()
        self.storage.save()
        self._sync_structure()          # M4：空文件夹移除走增量
        # M5：同上——解散自动关，否则刷到空态
        if self._popup is not None:
            self._popup.refresh()

    # ---------- 拖放 ----------
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasFormat(MIME_TYPE) or md.hasUrls():
            if md.hasUrls():
                # 外部文件拖入：整个窗口作为放置目标（否则空白格 drop 丢失）
                self._ole_active = True
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._ole_active = False
        if self._dock is not None:
            self._dock.set_drop_highlight(False)
        # 反馈 7：拖影从桌面（让位区）滑到目标图标核心区的瞬间，合成
        # dragLeave 先落到本窗口——此刻必须撤销已武装的推让，否则
        # 700ms 后目标会在「松手合并」提示亮着的时候被挤走（F3 病根
        # 的另一个入口：提示与推让双触发）。
        self._cancel_push()
        # 反馈 8：同理撤销边缘翻页——拖影压到边缘带的图标上时，用户
        # 要的是合并/让位，不是翻页（两手势不双触发）。
        self._cancel_edge_flip()
        e.accept()

    def dropEvent(self, e):
        md = e.mimeData()
        pos = e.position().toPoint()
        if md.hasFormat(MIME_TYPE):
            payload = md.data(MIME_TYPE).data().decode()
            # v10 C3：落在 Dock 条带上 -> 入 Dock（只收应用，Q2 拍板）。
            # 被拒（文件/文件夹/组件，或已在 Dock 里）则继续走常规落点语义，
            # **不吞事件**——用户拖到 Dock 上发现不收，松手仍应回原位。
            if self._dock is not None and self._dock_hit(pos):
                self._dock.set_drop_highlight(False)
                if self._dock_accepts_drop(payload):
                    e.acceptProposedAction()
                    self._drag_active = False
                    self._drag_multi = False
                    self._ole_active = False
                    return
            self._drop_internal(payload, pos)
        elif md.hasUrls():
            self._drop_files(md.urls(), pos)
        e.acceptProposedAction()
        # 无论内部拖拽的 dragFinished 信号是否送达（拖拽源可能已被
        # rebuild 删除），drop 完成后拖动状态一律复位
        self._drag_active = False
        self._drag_multi = False
        self._ole_active = False

    def _drop_internal(self, payload: str, pos):
        # 文件夹成员提取（payload: "M:fid:idx"）：M: 标记与含冒号的条目 id 消歧
        if payload.startswith("M:"):
            self._clear_preview()
            self._on_extract(payload[2:], pos)
            return
        ids = [p for p in payload.split(",") if p]
        entries = [e for e in (self.storage.find_entry(i) for i in ids) if e is not None]
        if not entries:
            # payload 全是不在布局的 id（条目被并发移除等）：有预览在飞
            # 就回退快照，别留半截预览态（同 _reflow_commit_or_compute）
            self._cancel_preview()
            return

        if grid_flow.free_mode(self.storage):
            # v6 §1.3 无界松手：整块按拖影位移量平移到像素位（不吸附 /
            # 不避让 / 可重叠——§1.2 自由摆布语义）
            self._free_drop(ids, self._drag_pos(pos))
            return

        if len(entries) == 1 and not self._drag_multi:
            # 单选：走让位（拖动期间已生成预览 -> 直接提交）
            self._reflow_commit_or_compute(entries[0]["id"], self._drag_cell(pos))
            return

        # 多选：整体平移语义（v6 §5.0 multi_move——不叠格/形状保持/
        # 边缘夹紧/占据者交换不丢条目）。旧实现逐成员
        # max(0, min(cols-1, col+dcol)) 各自夹紧，多个成员的 nc 全落
        # cols-1 聚成一列叠格（反馈病根）；且主条目取 payload 首 id
        # （sorted 串）而非真正被拖的图标，锚点错误。
        if self._preview_moves is not None and self._preview_drag in ids:
            # 拖动期间已生成预览（落点与预览一致）-> 直接提交
            self._commit_preview()
        else:
            primary = self._drag_entry_id
            if primary not in ids:
                primary = entries[0]["id"]
            moves = grid_flow.multi_move(self.storage.layout,
                                         self.storage.columns, primary,
                                         self._drag_cell(pos), set(ids),
                                         extra_occupied=self._widget_cells())
            if moves:
                by_id = {e.get("id"): e for e in self.storage.layout}
                for eid, (c, r) in moves.items():
                    e = by_id.get(eid)
                    if e is not None:
                        e["col"], e["row"] = c, r
                # 借提交路径收尾：压撤销栈 + 落盘 + 动画落位 + 清预览
                self._preview_moves = moves
                self._preview_drag = primary
                self._commit_preview()
            else:
                # 脏布局等无效输入：不动布局，仅收尾（与单选无位移同策）
                self._cancel_preview()
                return
        # M4：多选平移是位置类变化——动画到位，不 rebuild。
        # 逐个复位拖拽幽灵态：增量路径不再销毁图标实例，
        # set_drag_ghost(True) 的残影必须显式清掉。
        for e in entries:
            icon = self.icons.get(e["id"])
            if icon is None:
                continue
            icon.set_drag_ghost(False)
            icon.animate_to(*self._entry_pos(e))
        self.update()

    def _drop_files(self, urls, pos):
        """从资源管理器拖入文件：已在桌面的直接克隆，不在的先移入桌面文件夹。"""
        col, row = self._cell_at(pos)
        added = 0
        for url in urls:
            path = url.toLocalFile()
            if not path or not os.path.exists(path):
                continue
            try:
                if not appsource.on_desktop(path):
                    path = appsource.move_to_desktop(path)
                self.storage.add_app(appsource.desktop_file_entry(path), col, row)
                added += 1
                col += 1
                if col >= self.storage.columns:
                    col, row = 0, row + 1
            except Exception:
                continue
        if added:
            self.storage.save()
            self._sync_structure()      # M4：拖入文件增量淡入 + 被挤位移动画
            from .tray import notify_via_tray
            notify_via_tray("已添加到整理桌面", "新增 %d 个项目" % added)

    # ---------- 背景右键 / 按键 ----------
    def contextMenuEvent(self, e):
        """桌面空白处右键菜单。"""
        self._build_context_menu().exec(e.globalPos())

    def _build_context_menu(self):
        """构建桌面右键菜单（拆出来便于单测）。

        整理模式（路线A）下原生桌面右键被我们接管（nativeEvent 全窗口
        HTCLIENT），Windows 原生菜单全部消失——2026-10-18 用户反馈
        "桌面右键功能也没了"，2026-10-19 追加反馈 6：「右键要还原原系统
        的所有功能，比如新建 word」。

        因此接管态菜单按系统原生的顺序组织：查看 / 刷新 / 粘贴 / 新建▸ /
        显示设置 / 个性化 / 终端 / 打开桌面文件夹。其中「新建」子菜单由
        `shell_new.shell_new_templates()` 枚举注册表 ShellNew 模板生成
        （Word / Excel / PPT / 压缩包…用户装了什么就有什么）。
        """
        menu = QMenu(self)
        menu.setStyleSheet(theme.MENU_QSS)
        if self._organize_mode():
            # 查看 ▸（图标大小 + 自动排列 + 网格调试）
            view = menu.addMenu(i18n.T("menu.view"))
            # v7 §3.3：三档加勾选（勾选状态由 icon_fit 反查，自定义比例时三档全不勾）
            size_menu = view.addMenu(i18n.T("menu.icon_size"))
            cur_tier = self._current_icon_tier()
            for label, tier in (("大图标", "large"), ("中图标", "medium"),
                                ("小图标", "small")):
                act = size_menu.addAction(
                    i18n.TL("menu.icon_size." + tier, label))
                act.setCheckable(True)
                act.setChecked(cur_tier == tier)
                act.triggered.connect(
                    lambda checked=False, t=tier: self._set_icon_tier(t))
            view.addSeparator()
            # 2026-10-19 反馈 3：把"自动排列"做成**持久的开关**（与资源
            # 管理器「查看 ▸ 自动排列图标」同语义）——勾选即立刻整理，
            # 之后拖动始终递进生效；取消则自由摆放（允许空格）。
            auto_act = view.addAction(i18n.T("menu.auto_arrange"))
            auto_act.setCheckable(True)
            auto_act.setChecked(bool(self.storage.get_setting("auto_arrange")))
            auto_act.triggered.connect(self._toggle_auto_arrange)
            view.addAction(i18n.T("menu.tidy_now"), self._tidy)
            # 2026-10-20 用户反馈 4：「显示网格调试信息」不属于日常操作，
            # 从桌面右键「查看」里移除——它在 设置 ▸ 高级 ▸ 开发者调试
            # 卡片里（settings_dialog.chk_dev），入口只有一个，避免两处
            # 状态各说各话。

            # 刷新 / 粘贴（与系统桌面同一位置）
            menu.addAction(i18n.T("menu.refresh"), self.rescan_desktop)
            paste = menu.addAction(i18n.T("menu.paste"), self._paste_files)
            paste.setEnabled(self._clipboard_has_files())

            menu.addSeparator()
            new_menu = menu.addMenu(i18n.T("menu.new"))
            new_menu.setObjectName("ctxNewMenu")   # 测试/自动化定位用
            self._fill_new_menu(new_menu)
            menu.addSeparator()

            # 系统设置与常用入口
            menu.addAction(i18n.T("menu.display_settings"),
                           lambda: self._open_uri("ms-settings:display"))
            menu.addAction(i18n.T("menu.personalize"),
                           lambda: self._open_uri("ms-settings:personalization-background"))
            menu.addAction(i18n.T("menu.terminal"), self._open_terminal)
            menu.addAction(i18n.T("menu.open_desktop"), self._open_desktop_folder)
            menu.addSeparator()

            # 本软件的布局能力
            # 仅小组件模式（反馈 9）下整段不出现：没有图标，布局切换与
            # 空白页都无从谈起（组件在无界态不翻页）。
            if not self._widgets_only():
                free_now = grid_flow.free_mode(self.storage)
                menu.addAction(i18n.T("tray.to_free") if not free_now
                               else i18n.T("tray.to_grid"),
                               self._toggle_layout_mode)
                # 分页（反馈 17）：**两种模式都有**——旧实现把「添加空白页」
                # 写在 `if free_now:` 里，网格模式下用户根本找不到分页入口。
                menu.addAction(i18n.T("menu.add_blank_page"),
                               self._add_blank_page)
                if self._page in grid_flow.extra_pages(self.storage):
                    menu.addAction(i18n.T("menu.remove_blank_page"),
                                   self._remove_blank_page)
                # v9 V4：交互方式也放在这里（与设置 ▸ 模式 是同一个值，
                # 只是多一个就近入口；写值统一走 interaction.set_mode）
                mode_menu = menu.addMenu(i18n.T("menu.interaction"))
                cur_mode = interaction.mode(self.storage)
                for key, text in interaction.MODES:
                    act = mode_menu.addAction(
                        i18n.TL("interaction." + key, text))
                    act.setCheckable(True)
                    act.setChecked(cur_mode == key)
                    act.triggered.connect(
                        lambda checked=False, k=key: self.set_interaction_mode(k))
            # 网格原点定位是**开发者功能**（v7 §3.4），入口只在设置页
            # 「高级 → 调试」里，且要开发者开关打开才可配置。
            # 2026-10-20 用户反馈 5：桌面右键不该出现这两项。
            menu.addSeparator()

        if self._selected:
            menu.addAction(
                i18n.T("menu.delete_selected", n=len(self._selected)),
                self._remove_selected)
        # 2026-10-21 反馈 9：右键的「小组件模式」开关移除——与设置页
        # 「显示桌面小组件」是同一个值的两个旋钮，必然互相打架；
        # 新增的「仅小组件模式」开关同样只在设置页，入口唯一。
        # 上一段（布局能力）已经收过分隔线，这里不再重复加，避免出现两条
        menu.addAction(i18n.T("menu.add_widget"),
                       self.openWidgetCenterRequested.emit)
        menu.addAction(i18n.T("menu.settings"),
                       self.openSettingsRequested.emit)
        return menu

    def _fill_new_menu(self, new_menu):
        """「新建」子菜单：文件夹 + 系统注册的全部 ShellNew 模板（反馈 6）。"""
        from . import shell_new
        new_menu.addAction(
            i18n.T("menu.new.folder"),
            lambda: self._create_on_desktop(i18n.T("menu.new.folder_name"), True))
        templates = shell_new.shell_new_templates()
        if templates:
            new_menu.addSeparator()
        for tpl in templates:
            new_menu.addAction(
                tpl["label"],
                lambda checked=False, t=tpl: self._create_from_template(t))

    def _clipboard_has_files(self) -> bool:
        """剪贴板里是否有可粘贴的本地文件 / 目录（决定「粘贴」是否置灰）。"""
        try:
            md = QApplication.clipboard().mimeData()
            return bool(md is not None and md.hasUrls()
                        and any(u.isLocalFile() for u in md.urls()))
        except Exception:
            return False

    # ---------- v11 反馈 31：桌面 Ctrl+C / Ctrl+V ----------
    def setup_desktop_hotkeys(self):
        """装低层键盘钩子（设置 `desktop_hotkeys` 关着就不装）。

        v14 反馈 2：钩子挪到窗口自己身上——设置里改开关要能立刻生效
        （`set_desktop_hotkeys`），关窗也必须撤钩（`closeEvent`）。
        """
        from .desktop_hotkeys import DesktopHotkeys
        if self._hotkeys is not None:
            return
        if not bool(self.storage.get_setting("desktop_hotkeys")):
            return
        dock = getattr(self, "_dock", None)
        own = [int(self.winId())]
        if dock is not None:
            try:
                own.append(int(dock.winId()))
            except Exception:      # noqa: BLE001
                pass
        self._hotkeys = DesktopHotkeys(
            on_copy=self.copy_selection,
            on_paste=self.paste_from_clipboard,
            own_hwnds=lambda: own,
            can_copy=self.has_selection,
            can_paste=self.clipboard_has_files,
        )
        self._hotkeys.install()

    def set_desktop_hotkeys(self, on: bool):
        """开 / 关桌面快捷键（设置页即时生效）。"""
        on = bool(on)
        if on:
            self.setup_desktop_hotkeys()
            return
        if self._hotkeys is not None:
            self._hotkeys.uninstall()
            self._hotkeys = None

    def has_selection(self) -> bool:
        """是否有选中项（快捷键判据：没东西可复制就别吞键）。"""
        return bool(self._selected)

    def copy_selection(self) -> bool:
        """把选中的图标写进系统剪贴板（本地文件 URL，资源管理器也认）。

        只收 `type == "app"` 且 `target` 是真实文件/目录的条目：
        扫描来的应用（`shell:AppsFolder\\…`）与虚拟文件夹没有可复制的
        磁盘对象，塞进剪贴板只会产生一个打不开的条目。
        """
        from PySide6.QtCore import QMimeData, QUrl
        paths = []
        for eid in sorted(self._selected):
            entry = self.storage.find_entry(eid)
            if entry is None or entry.get("type") != "app":
                continue
            target = str(entry_app(entry).get("target") or "")
            if target and os.path.exists(target):
                paths.append(target)
        if not paths:
            return False
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        try:
            QApplication.clipboard().setMimeData(md)
            # 留一份 Python 引用：某些平台插件在 mimeData 被回收后读不到
            # 内容（粘贴成空），持有到下一次复制为止，代价只有一个对象。
            self._clip_mime = md
        except Exception:      # noqa: BLE001 —— 剪贴板被占用时静默失败
            return False
        return True

    def paste_from_clipboard(self):
        """公开入口：把剪贴板里的文件复制到桌面（Ctrl+V / 右键「粘贴」共用）。"""
        self._paste_files()

    def clipboard_has_files(self) -> bool:
        """公开别名：剪贴板里有没有可粘贴的本地文件（快捷键判据用）。"""
        return self._clipboard_has_files()

    def _paste_files(self):
        """粘贴：把剪贴板里的本地文件/目录复制到桌面（系统语义）。"""
        import shutil
        from . import shell_new
        try:
            urls = QApplication.clipboard().mimeData().urls()
        except Exception:
            return
        folders = appsource._desktop_folders()
        if not folders:
            return
        dest = str(folders[0])
        added = 0
        for u in urls:
            src = u.toLocalFile()
            if not src or not os.path.exists(src):
                continue
            base, ext = os.path.splitext(os.path.basename(src))
            try:
                target = shell_new.unique_path(dest, base, ext)
                if os.path.isdir(src):
                    shutil.copytree(src, target)
                else:
                    shutil.copy2(src, target)
                added += 1
            except Exception:
                continue
        if added:
            self.rescan_desktop()
            from .tray import notify_via_tray
            notify_via_tray("已粘贴到桌面", "%d 个项目" % added)

    def _create_from_template(self, tpl):
        """按 ShellNew 模板在桌面新建文件（Office 文档带默认样式）。"""
        from . import shell_new
        folders = appsource._desktop_folders()
        if not folders:
            return
        path = shell_new.create_from_template(tpl, str(folders[0]))
        if not path:
            from .tray import notify_via_tray
            notify_via_tray("新建失败", "系统没有为该类型提供模板")
            return
        self.rescan_desktop()

    def _toggle_dev_mode(self, checked: bool):
        self.storage.set_setting("dev_mode", bool(checked))
        self.update()

    def _open_terminal(self):
        """在桌面目录打开终端（Windows Terminal 优先，回退 cmd）。"""
        folders = appsource._desktop_folders()
        if not folders:
            return
        d = str(folders[0])
        try:
            if QProcess.startDetached("wt.exe", ["-d", d]):
                return
        except Exception:
            pass
        try:
            QProcess.startDetached("cmd.exe", ["/c", "start", "cmd", "/k",
                                               'cd /d "%s"' % d])
        except Exception:
            pass

    def _create_on_desktop(self, name: str, is_dir: bool):
        """右键新建：真实创建到桌面目录，然后重扫让网格立即出现。"""
        try:
            appsource.create_on_desktop(name, is_dir)
        except Exception:
            return
        added, _ = self.rescan_desktop()

    def _set_icon_size(self, px: int):
        self.storage.set_setting("icon_size", int(px))
        self.refresh_grid()

    def _current_icon_tier(self):
        """当前图标档位（v7 §3.3）：由 icon_fit 反查最近预设。

        medium 的 fit 是 `native_px/cell_w`（跟随屏幕与缩放），所以不能只看
        settings 里的 `icon_tier`——那只是最近一次预设的缓存。
        返回 None 表示"三档都不勾"（v7 B2：用户选了「自定义」档，
        哪怕数值恰好等于某个预设，也不该勾上那个预设）。
        """
        from . import grid_sizing
        from .storage import tier_for_fit
        if self.storage.icon_tier() == grid_sizing.TIER_CUSTOM:
            return None
        m = grid_flow.current_metrics()
        fit = self.storage.icon_fit()
        if fit is None:
            try:
                native = win32_layer.native_icon_size()
            except Exception:
                native = 48
            cw = m.cw if m is not None else grid_sizing.cell_from_native(native)[0]
            fit = grid_sizing.fit_for(native, cw, "medium")
        return tier_for_fit(fit)

    def _set_icon_tier(self, tier: str):
        """右键「图标大小」三档（v7 §3.1）：档位 = 图标在格子里的占比。

        **只改图标本体，不碰格距**——列数/行数不变（v6 的档位会改格距，
        正是"切档位整个网格都变"的病根）。

        反馈 2：改完必须立刻重算几何（refresh_grid），只 rebuild 会沿用
        旧 cell 快照，看起来"改了没反应"。
        """
        from . import grid_sizing
        # 显式传 tier：从「自定义」切回某档时，tier_for_fit 反查可能因
        # 数值接近而误判（v7 B2）
        self.storage.apply_icon_fit(grid_sizing.parse_fit(tier), tier=tier)
        self.refresh_grid()

    def _toggle_auto_arrange(self, checked: bool):
        """自动排列开关（反馈 3）：勾选即整理一次，之后始终按阅读序递进。

        语义与资源管理器「查看 ▸ 自动排列图标」一致：**先打开，再总是
        生效**。未开启时保留空格（自由摆放），拖进空格不会再触发整段
        平移——这正是用户说的"前方有空格时移动逻辑很混乱"。
        """
        on = bool(checked)
        self.storage.set_setting("auto_arrange", on)
        if on:
            self._tidy()

    def _toggle_layout_mode(self):
        """v6 §1.3：网格 <-> 无界 切换（桌面右键 / 托盘同一入口）。"""
        self.set_layout_mode(
            "grid" if grid_flow.free_mode(self.storage) else "free")

    def _add_blank_page(self):
        """v6 §1.3：末尾追加一页空白页（extra_pages 记录页码）。

        取 max(当前总页数, 数据页数) 作为新页号——不与已有页冲突；
        追加后翻过去，用户面对一张干净的画布。
        """
        extras = grid_flow.extra_pages(self.storage)
        total = grid_flow.page_count(self.storage)
        new_page = max(total, extras[-1] + 1 if extras else 0)
        extras.append(new_page)
        self.storage.set_setting("extra_pages", sorted(set(extras)))
        self.goto_page(new_page)
        self.update()

    def _remove_blank_page(self):
        """v6 §1.3：删除当前空白页（仅 extra_pages 里的空页可删——
        有数据的页删了会丢条目，直接拒绝并提示）。"""
        page = self._page
        extras = grid_flow.extra_pages(self.storage)
        if page not in extras:
            from .tray import notify_via_tray
            notify_via_tray("无法删除", "当前页不是空白页（有内容或不存在）")
            return
        extras.remove(page)
        self.storage.set_setting("extra_pages", extras)
        self._clamp_page()
        self.update()

    @staticmethod
    def _open_uri(uri: str):
        """ms-settings: 等系统 URI 走 explorer 解析（最稳，无需注册协议）。"""
        try:
            QProcess.startDetached("explorer", [uri])
        except Exception:
            pass

    def _open_desktop_folder(self):
        folders = appsource._desktop_folders()
        if folders:
            try:
                os.startfile(str(folders[0]))
            except Exception:
                pass

    def _exit_organize_mode(self):
        """退出整理模式：关闭「接管系统桌面」并恢复原生图标显示。"""
        self.storage.set_takeover(False)
        self._sync_native_icons()

    def _remove_selected(self):
        for eid in list(self._selected):
            self.storage.remove_entry(eid)
            self._remove_icon(eid)
        self._selected.clear()
        self.storage.compact_layout()
        self.storage.save()
        self._sync_icons()              # M4：批量移除后 compact 补位走动画

    def _tidy(self, pages=None):
        """整理桌面。`pages` 给定时只整理这些页（v9 V2 每页规则）。

        None = 旧行为（全局铺满，图标连续跨页）。
        """
        self.storage.tidy_layout(pages)
        self.storage.save()
        self.rebuild(stagger=True)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self._picking_origin:
            # 取点态下 Esc 只取消取点，不顺带把桌面藏起来（否则用户丢了上下文）
            self._cancel_pick_origin()
            return
        if e.key() == Qt.Key_Escape:
            self.hide_desktop()
        elif e.key() == Qt.Key_Delete and self._selected:
            self._remove_selected()
        elif e.key() == Qt.Key_A and (e.modifiers() & Qt.ControlModifier):
            # B6：与长按菜单的 _select_all 统一语义——只选当前页。
            # 旧实现选了所有页（含隐藏页），Ctrl+A 后 Delete 会把
            # 用户看不见的图标一并删掉
            self._select_all()
            self._refresh_selection()
        elif e.key() == Qt.Key_Z and (e.modifiers() & Qt.ControlModifier):
            # Ctrl+Z：布局撤销（让位 / 多选平移，指导文档 §2.3）
            self._undo_layout()

    def closeEvent(self, e):
        self._band_guard.untrack(self)
        # 低层键盘钩子必须跟着窗口一起撤：它是**进程级全局钩子**，窗口没了
        # 还挂着会让每次按键都 round-trip 进一个没有 UI 的进程。
        self.set_desktop_hotkeys(False)
        # 关窗必须收干净自己的计时器：边缘翻页 / 推让都是"还会改全局页号
        # 与布局"的异步动作，窗口已经退场还让它跑，会把另一个窗口的页翻掉
        # （tests/test_edge_flip.py F7 的落点列随机漂移就是这么来的）。
        self._cancel_edge_flip()
        self._cancel_push()
        # 翻页瞬态是 grid_flow 的模块级单例：窗口先走一步时必须把它清掉，
        # 否则下一次（重开/新窗口）所有格点坐标都会偏移半页。
        if self._flip_anim is not None:
            self._flip_anim.stop()
            self._flip_anim = None
        grid_flow.end_flip()
        if self.storage.get_setting("minimize_to_tray"):
            e.ignore()
            self.hide_desktop()
        else:
            self.quitRequested.emit()
            e.accept()
