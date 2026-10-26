# -*- coding: utf-8 -*-
"""可拖拽的桌面图标控件（网格与文件夹弹窗共用）——手机桌面式深色风格。

视觉：
  - 图标 + 下方两行白字标签（带阴影，任意壁纸可读）
  - 悬停：圆角浅色高亮；选中：蓝色浅底 + 蓝色描边
  - 文件夹：深色半透明圆角方形 + 2x2 迷你图标（1/2/3/4 成员自适应布局）

交互：
  - 左键按下移动超过系统拖拽阈值 -> 自管理拖拽循环（grabMouse + 合成拖放事件）
  - 拖到另一个图标上            -> mergeRequested（创建 / 合并文件夹）
  - 拖到窗口空白处              -> 由主窗口移动到目标网格
  - 文件夹内图标拖出            -> payload 形如 "M:fid:idx"
  - 单击        -> 选中（clicked）；双击 -> 打开
  - 右键        -> 打开 / 在文件夹中显示 / 移出文件夹 / 解散 / 移除

动效（苹果 HIG / Fluid Interfaces 规范，真弹簧见 src/effects.py）：
  - 按下 scale(0.97) 100ms ease-out（pointer-down 即反馈，不等松手）
  - 松开 / 悬停归位：临界阻尼弹簧（damping 1.0，无动量不过冲）
  - 拖动落位：欠阻尼弹簧 0.62（带动量才有 ~7% 一次衰减过冲）
  - 悬停 1.04 轻放大；拖动提拉 1.06
  - 双击/打开文件夹脉冲：峰值 1.08 上冲 90ms + 临界阻尼回落
  - rebuild 错峰淡入（stagger 30ms/个，欠阻尼 0.72 落位微弹）
  - 系统关闭动画效果时（prefers-reduced-motion 等价）自动降级为短淡入
"""
from PySide6.QtCore import (QEasingCurve, QMimeData, QPoint, QPointF,
                            QPropertyAnimation, QRect, QRectF, QSize, Qt,
                            Signal, Property, QTimer)
from PySide6.QtGui import (QBrush, QColor, QCursor, QDragEnterEvent,
                           QDragLeaveEvent, QDragMoveEvent, QDropEvent,
                           QFontMetrics, QLinearGradient, QPainterPath, QPen,
                           QPixmap, QPainter)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMenu,
                               QSlider, QToolTip, QVBoxLayout,
                               QWidget, QWidgetAction)

from . import appsource
from . import drag_merge
from . import effects
from . import interaction
from . import label_style as _ls
from .folder_tint import folder_liquid, folder_tint
from .stepper import Stepper
from . import theme
import os

from .storage import entry_app

MIME_TYPE = "application/x-desktoporg-item"


def _cursor_pos() -> QPoint:
    """当前光标的全局坐标。

    单独抽出来是为了可测：长按菜单开着时 QMenu 有自己的 grab，
    AppIcon 收不到 mouseMove，只能主动读光标。"把系统级读取做成模块
    级函数"让测试可以替换它，而不必真的去挪鼠标。
    """
    return QCursor.pos()


def _left_button_down() -> bool:
    """物理左键是否仍按着（长按后"还在滑"还是"已松手"的分界）。"""
    return bool(QApplication.mouseButtons() & Qt.LeftButton)

# 拖到图标上后进入「合并文件夹」武装状态所需的悬停时长（ms）。
# 未满即松手 -> 走网格让位置换（安卓 launcher 默认语义）；
# 悬停足够久图标高亮后松手 -> 合并文件夹。两套手势由此分流。
#
# 2026-10-21 反馈 1（v7e）：450 → 350。用户原话「挤开反应迟钝，但不能
# 快到老来不及弹出合并」——挤开本身也提速到 470（PUSH_DWELL_MS），
# 若提示还是 450，两者只差 20ms，手感上就是"没提示就被挤走"。把**提示
# 提前**（350）而不是把挤开推后，两条诉求才能同时满足：提示先到 120ms。
DWELL_MERGE_MS = 350
# 拖放命中分流（2026-10-21 反馈 7 = v7 指导文档 §5.2 双区模型）：
#   拖影中心在目标核心区（中心 55% 内缩矩形）-> 合并候选（武装 dwell）
#   拖影中心在核心区外（让位区）            -> 桌面窗口（悬停够久挤走）
# 重叠比只当"是否压在图标上"的门槛（MERGE_ARM_RATIO=0.15）：低于它视为
# 没碰上，两套手势都不触发。旧模型按重叠比 0.30 一刀切（F3），两个病根
# 正对反馈 7 原话："判定范围太大"（0.30 刚擦上就弹合并）+ "实时挤开效果
# 没有了"（0.15~0.30 被 defer 成死区）。阈值单点定义在 drag_merge，
# 这里只做 re-export。
MERGE_ARM_RATIO = drag_merge.MERGE_ARM_RATIO

# v14 反馈 1：文件夹**悬停自动展开**的驻留时长（ms）。比合并提示
# （DWELL_MERGE_MS 350）短一截：悬停展开要"瞄一眼就开"的轻快感，而合并
# 提示慢一点才不会被误触。离开图标 / 按下 / 形成拖动都会撤表。
FOLDER_HOVER_OPEN_MS = 260

# v6 §5.1（反馈 6）：拖动浮影 = 当前图标的 render 快照，半透明 0.8——
# 替换旧 QDrag 的白块占位，拖动中保留原始样式（图标 + 标签 + 状态）。
GHOST_OPACITY = 0.8

# 左上角多选徽标直径的**逻辑基准**（v10 B2 / 反馈 15-b）。
# 实测病根：旧式 `17 * self._m` 的 `_m` 里含 dpr，而 Qt 已经把逻辑值
# 放大成物理值一次 → 徽标物理按 dpr² 增长（125% 下 26px，而 cell 只
# 105px，比值 0.248 vs 100% 的 0.202）。`_m` 去掉 dpr 后这里恢复 ∝dpr¹。
BADGE_PX = 17


def _is_tooltip_window(w) -> bool:
    """判断窗口是不是 QToolTip 提示气泡（合并提示 / 控件 tooltip）。

    判据必须是**窗口类型相等**：`windowType() == Qt.ToolTip`
    （实测真气泡 label flags=0x2800f00d、windowType=13）。

    不要用 `windowFlags() & Qt.ToolTip` 这种位与：Qt.ToolTip=0xD 与
    Qt.Window(0x1) / Qt.Tool(0xB) / Qt.Popup(0x9) 的位都有交集
    （0x1&0xD=0x1、0xB&0xD=0x9），普通无父窗（flags=0x800f001）和
    Qt.Tool/Popup 真落点窗都会被误滤。2026-10-21 全量回归实测：
    test_drag_ghost D6「命中其它部件时原样返回」正是被误滤挂掉的。
    """
    if w is None:
        return False
    # 2026-10-26 崩溃修复（用户 crash.log 33 次）：SettingsDialog 等部件
    # 持有 `self.window = <DesktopWindow>` 属性，**遮蔽**了 QWidget.window()
    # 方法——拖拽命中测试经过设置窗时 `w.window()` 调到 DesktopWindow
    # 实例（not callable）直接崩。改走 parentWidget() 链找顶层（该链上的
    # 方法没有被遮蔽），并保持 RuntimeError 防御。
    try:
        win = w
        while True:
            p = win.parentWidget()
            if p is None:
                break
            win = p
        return win is not None and win.windowType() == Qt.ToolTip
    except RuntimeError:
        return False


class _DragGhost(QWidget):
    """浮动拖影：frameless + 半透明背景 + 对鼠标事件透明。

    替代 QDrag 的拖拽图像。QDrag.exec() 在 Windows 上走 OLE DoDragDrop
    模态循环，注入（合成）鼠标输入会被该循环吞掉释放事件导致拖拽永不
    成落（DEVLOG Day4 实证：17ms 内取消、零 DragEnter），故改自管理
    拖拽循环 + 本拖影。Qt.ToolTip 标志：无任务栏按钮、不抢焦点、
    在桌面层应用窗口之上可见。
    """

    def __init__(self, pixmap: QPixmap):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._pm = pixmap
        # 快照带 devicePixelRatio（DPR 感知）：固定尺寸要用逻辑尺寸，
        # 否则 125% 缩放下拖影会比图标大一圈（同 AppIcon 的尺寸教训）
        dpr = max(1.0, float(pixmap.devicePixelRatio()))
        self.setFixedSize(max(1, int(round(pixmap.width() / dpr))),
                          max(1, int(round(pixmap.height() / dpr))))

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pm)


class AppIcon(QWidget):
    activated = Signal(dict)            # 双击启动
    clicked = Signal(str)               # 单击选中（entry id）
    folderClicked = Signal(str)         # 单击文件夹（entry id）
    bigToggleRequested = Signal(str, bool)   # 放大/缩小（id, 目标态）v14u
    folderHoverOpened = Signal(str)     # 悬停自动展开（v14：与点击分开，
                                        # 悬停展开的弹窗鼠标离开后自动关）
    mergeRequested = Signal(str, str)   # 拖拽合并：dragged_payload, target_entry_id
    removeRequested = Signal(str)       # 右键移除（entry id）
    removeFromFolder = Signal(str)      # 文件夹内右键移出（payload "M:fid:idx"）
    dissolveRequested = Signal(str)     # 解散文件夹（entry id）
    frameRequested = Signal(str, int, int)   # 文件夹尺寸上限（entry id, 列, 行）
    iconScaleChanged = Signal(str, int)      # 单图标大小（entry id, 百分比）
    openLocation = Signal(dict)         # 在资源管理器中定位（桌面文件）
    dragStarted = Signal()
    dragFinished = Signal()
    # v14z：拖动中每次鼠标移动（与拖影同步）——多选整块跟随靠它驱动。
    # 不能只靠桌面窗口的 dragMoveEvent：指针压在**别的图标**上时，合成
    # 事件发给那个图标，桌面窗口收不到，整块就会冻住而拖影继续走。
    dragMoved = Signal()
    reflowHover = Signal(str, int, int)  # 拖动悬停：payload, 指针全局 x, y
    reflowDrop = Signal(str, int, int)   # 悬停未满即松手：payload, 指针全局 x, y

    def __init__(self, entry: dict, icon_size: int, in_folder: bool = False,
                 parent=None, chrome_scale: float = 1.0, layout=None,
                 compact: bool = False, show_label: bool = True,
                 pad_x: int = None, label_style=None, big: bool = False):
        super().__init__(parent)
        self.entry = entry
        self.in_folder = in_folder
        self.selected = False
        # v14 反馈 3：标签文字样式（颜色/加粗/阴影/描边）。缺省 = Windows
        # 原生白字 + 柔和黑阴影——不传的旧调用（探针 / 测试）观感不变。
        self._label_style = label_style or _ls.default_label_style()
        self._big = bool(big)          # 大文件夹瓷砖（2×2 跨格，v14t）
        # 外围 chrome 缩放（留白 / 标签字号 / 行高随**用户缩放**同比放大）。
        # v10 B2：不含 dpr——Qt 高 DPI 缩放已经把逻辑值渲染成物理值时乘过
        # 一次（125% 下 1536×864 逻辑视口就是证据），再乘就是 dpr²。
        self._m = max(0.5, float(chrome_scale))
        self._no_label = not bool(show_label)
        self._compact = False      # 文件夹内紧凑格（单行小标签路径）
        # v6 批次 A2（指导文档 §2.2）：grid_sizing.IconLayout 三档方案。
        # 传入时 chrome 全部按 cell/widget 比例派生（标签单行小字 elide，
        # 密度加密后瘦身——旧绝对值 24m/40m 在 cell 变小后会把图标挤成小点）；
        # 不传（folder 成员 / 探针 / 旧调用）保持 2026-10-18 旧行为。
        self._layout = layout
        if self._big and layout is not None:
            # v14t 大文件夹：widget 直接占 2×2 cell（layout 已按此构建），
            # 内部 3×3 成员格 + 标签
            self.icon_size = int(layout.icon_box)
            self._box_x = 0
            self._label_h = int(layout.label_h)
            self._label_font_px = self._scale_font_px(
                int(layout.font_px))
            self.setFixedSize(int(layout.widget_w), int(layout.widget_h))
        elif layout is not None:
            self.icon_size = int(layout.icon_box)      # 绘制区（pixmap 边长）
            self._box_x = int(layout.chrome_x)         # 绘制区左侧留白（居中）
            self._label_h = int(layout.label_h)
            self._label_font_base = int(layout.font_px)
            self._label_font_px = self._scale_font_px(self._label_font_base)
            self.setFixedSize(int(layout.widget_w), int(layout.widget_h))
        elif compact:
            # 文件夹内部格（2026-10-19 反馈 6）：手机文件夹是**紧凑图标阵**。
            # 2026-10-20 反馈 7 反转了"不显示文件名"的旧规格：成员要有名字。
            # 有标签时格高 = 图标 + 内边距 + 单行标签（标签单行 elide，
            # 两行会把格高撑到桌面图标那么大，失去文件夹的密度感）。
            # pad_x（v10 A9 / 反馈 16）：**横向**内边距可单独给大——3×3
            # 满页旧宽高比 0.78（竖长方形），加宽后接近正方形。不给时等于 pad。
            pad = max(2, int(round(4 * self._m)))
            px = pad if pad_x is None else max(pad, int(pad_x))
            self.icon_size = int(icon_size)
            self._box_x = px
            self._compact = True
            self._label_font_base = max(10, int(round(11 * self._m)))
            self._label_font_px = self._scale_font_px(self._label_font_base)
            if self._no_label:
                self._label_h = 0
                self.setFixedSize(icon_size + px * 2, icon_size + pad * 2)
            else:
                self._label_h = max(12, int(round(self._label_font_px * 1.45)))
                self.setFixedSize(icon_size + px * 2,
                                  icon_size + pad * 2 + self._label_h)
        else:
            self.icon_size = icon_size
            self._box_x = None
            self._label_h = int(round(34 * self._m))
            # 旧路径标签字号也按 chrome 现算存档（12px @100%，15px @125%），
            # 属性语义统一：_label_font_px 始终有值
            self._label_font_base = max(11, int(round(theme.LABEL_SIZE * self._m)))
            self._label_font_px = self._scale_font_px(self._label_font_base)
            # chrome 精修（2026-10-18 用户反馈"图标大小不对"）：旧值 28/46 的
            # 留白占比过大，图标显小。收紧到 24/40，标签贴近图标 6px。
            self.setFixedSize(icon_size + int(round(24 * self._m)),
                              icon_size + int(round(40 * self._m)))
        self.drag_payload = entry.get("id", "")
        self.payload_provider = None  # 多选拖拽时由主窗口注入，返回逗号分隔的 id 列表
        self._press_pos = None
        self._dragged = False
        # 拖动中最后一次鼠标全局位置：多选整块跟随按 (last - press_global)
        # 算位移，与拖影同源（v14z）。不取 QCursor：合成输入下它不可靠。
        self._last_gpos = None
        # 自管理拖拽循环状态（替代 QDrag.exec 的 OLE DoDragDrop，见 _exec_drag）：
        #   _drag_loop     拖动进行中（grabMouse 已捕获全部鼠标事件）
        #   _drag_target   当前悬停目标（本应用部件；应用外/无目标为 None）
        #   _drag_ghost_w  浮动拖影部件
        #   _drag_mime     本次拖拽的 MIME 数据（payload 协议不变）
        self._drag_loop = False
        self._drag_ids = set()       # 本次拖动的整块 id（命中测试排除自己人）
        self._drag_target = None
        self._drag_ghost_w = None
        self._drag_mime = None
        self._hover = False
        self._merge_target = False
        # 双区模型状态（2026-10-21 反馈 7）：拖影中心相对"当前最佳
        # 重叠图标"是否在核心区内。_core_in 是滞回的历史输入
        # （drag_merge.in_merge_core 的 was_in），换目标即清零。
        self._core_target_id = None
        self._core_in = False
        # 文件夹「打开中」态（M5）：弹窗打开期间源图标高亮
        self._folder_open = False
        # 拖动虚影：拖动期间本图标不绘制（格子在视觉上被让出），但不 hide()——
        # hide 会牵动显隐生命周期与命中测试，纯绘制跳过零风险
        self._drag_ghost = False
        self._drag_crop_off = QPoint(0, 0)   # 快照裁切偏移（拖影对位用）
        # dwell 合并：悬停 450ms 才武装「合并文件夹」（未满即松手 = 让位置换）
        self._merge_armed = False
        self._dwell = QTimer(self)
        self._dwell.setSingleShot(True)
        self._dwell.setInterval(DWELL_MERGE_MS)
        self._dwell.timeout.connect(self._arm_merge)
        # ---------- v9 V4：手机模式的长按 ----------
        # 按下即起表；抬起 / 形成拖动 / 超过拖拽阈值都要撤表，否则"按住拖"
        # 会先弹出一个菜单再开始拖（用户看到菜单闪一下）。
        self._long = QTimer(self)
        self._long.setSingleShot(True)
        self._long.setInterval(interaction.LONG_PRESS_MS)
        self._long.timeout.connect(self._on_long_press)
        self._long_fired = False       # 本次按下是否已弹出长按菜单
        self._long_menu = None         # 长按菜单（持引用，防 GC + 便于主动关）
        self._long_origin = None       # 长按触发时的全局坐标
        self._press_global = None
        # 长按菜单开着时的"滑动即拖动"轮询：菜单是独立弹层（有自己的
        # 事件抓取），AppIcon 收不到 mouseMove，只能靠自己读光标位置判断
        self._long_watch = QTimer(self)
        self._long_watch.setInterval(30)
        self._long_watch.timeout.connect(self._watch_long_slide)
        # v14 反馈 1：文件夹悬停自动展开的驻留计时（只对文件夹起作用，
        # 判据见 `_hover_expand_ok`——单测直接钉那些条件）
        self._hover_open = QTimer(self)
        self._hover_open.setSingleShot(True)
        self._hover_open.setInterval(FOLDER_HOVER_OPEN_MS)
        self._hover_open.timeout.connect(self._on_hover_open)
        # 多选态（手机模式菜单 ▸ 选择）：左上角画空心圆 / 选中画对钩
        self._selection_visible = False
        # 外围 chrome 缩放（留白 / 标签字号 / 行高随**用户缩放**同比放大）。
        # v10 B2：不含 dpr——Qt 高 DPI 缩放已经把逻辑值渲染成物理值时乘过
        # 一次（125% 下 1536×864 逻辑视口就是证据），再乘就是 dpr²。
        self._m = max(0.5, float(chrome_scale))
        # 动效状态（三个独立属性，动画互不冲突；paintEvent 相乘）
        self._hover_scale = 1.0    # 悬停轻放大
        self._press_scale = 1.0    # 按压 / 拖动提拉 / 脉冲
        self._appear = 1.0         # 错峰淡入进度（rebuild stagger）
        self._anims = []           # 持有动画引用，防 GC
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        # ClickFocus：点击即获键盘焦点。自管理拖拽循环里 Esc 取消依赖
        # 焦点投递（_exec_drag 中 setFocus）——默认 NoFocus 策略下
        # keyPressEvent 永不触发，Esc 分支是死代码。ClickFocus 不加
        # Tab 停靠点，不污染窗口 Tab 序。
        self.setFocusPolicy(Qt.ClickFocus)
        self.setToolTip(self._tooltip())

    # ---------- 动效属性（QPropertyAnimation 直接驱动） ----------
    def _get_hover_scale(self):
        return self._hover_scale

    def _set_hover_scale(self, v):
        if v != self._hover_scale:
            self._hover_scale = v
            self.update()

    def _get_press_scale(self):
        return self._press_scale

    def _set_press_scale(self, v):
        if v != self._press_scale:
            self._press_scale = v
            self.update()

    def _get_appear(self):
        return self._appear

    def _set_appear(self, v):
        if v != self._appear:
            self._appear = v
            self.update()

    hover_scale = Property(float, _get_hover_scale, _set_hover_scale)
    press_scale = Property(float, _get_press_scale, _set_press_scale)
    appear = Property(float, _get_appear, _set_appear)

    def _scale_font_px(self, px: int) -> int:
        """基准字号 × 用户字号缩放（v14q；夹 9~40 防极端）。"""
        try:
            k = float(getattr(self._label_style, "font_scale", 1.0) or 1.0)
        except Exception:        # noqa: BLE001
            k = 1.0
        return max(9, min(40, int(round(int(px) * k))))

    def _animate(self, prop, start, end, duration, easing=None):
        """启动属性动画（自动停止同属性上的旧动画并持有引用防 GC）。"""
        for a in list(self._anims):
            if a.propertyName() == prop:
                a.stop()
                if a in self._anims:      # stop 发 finished 可能已移走
                    self._anims.remove(a)
        # v14p：FrameDriver——时钟走全局渲染频率档（QPropertyAnimation
        # 的 Qt 内部时钟固定 ~60fps，设置管不到；接口对齐 _anims 管理）
        anim = effects.drive_property(
            self, prop, start, end, effects.dur(duration),
            curve=easing if easing is not None else effects.smooth_out())
        anim.finished.connect(lambda: self._anims.remove(anim)
                              if anim in self._anims else None)
        self._anims.append(anim)
        return anim

    def pulse(self, scale=1.08, up_ms=90):
        """启动 / 打开反馈脉冲：快速放大到峰值后弹簧回落。

        苹果规范：峰值克制（1.06~1.08——1.12+ 显廉价）；上冲 OutCubic 90ms，
        回落用临界阻尼弹簧（无动量不过冲）。旧实现峰值 1.12/1.14 且回落是
        OutCubic，已按规范下调。

        注意：pulse 自带回落动画，调用方不得再叠加同属性归位动画
        （_animate 会按属性名停掉旧动画，曾导致 pulse 上冲被立即杀掉、
        从未完整播放——2026-10-18 修复）。
        """
        for a in list(self._anims):
            if a.propertyName() == b"press_scale":
                a.stop()
                if a in self._anims:      # stop 发 finished 可能已移走
                    self._anims.remove(a)
        # v14p：FrameDriver（帧率档受控）
        up = effects.drive_property(
            self, b"press_scale", float(self._press_scale), float(scale),
            effects.dur(up_ms), curve=QEasingCurve.OutCubic)

        def _back():
            if up in self._anims:
                self._anims.remove(up)
            # 回落：临界阻尼弹簧（点按反馈无动量，不过冲）
            self._animate(b"press_scale", float(self._press_scale), 1.0, 240,
                          effects.spring_curve(1.0, 240))

        up.finished.connect(_back)
        self._anims.append(up)

    def appear_in(self, delay_ms=0):
        """错峰淡入（rebuild 后 iOS 风依次落位）：缩放 0.7->1 + 渐显。
        欠阻尼弹簧（damping 0.72，约 2% 过冲）——落位带一点活力，
        但不过 3%（过大显廉价）。系统关闭动画效果时退化为立即显示。"""
        self._appear = 0.0
        self.update()
        delay = 0 if effects.reduced_motion() else max(0, delay_ms)
        QTimer.singleShot(delay, self._play_appear)

    def _play_appear(self):
        if self._appear >= 1.0:
            return
        self._animate(b"appear", 0.0, 1.0, 260, effects.spring_curve(0.72, 260))

    # ---------- 位置动画（网格实时让位） ----------
    def snap_to(self, x: int, y: int):
        """立刻到位——**拖动中整块跟随**专用（v14z）。

        与 `animate_to` 的分工：那个是"让位"语义（被挤走的图标弹簧滑走，
        有生命感）；这个是"我手里抓着的那块"——必须与指针逐像素同步，
        任何缓动都会变成"追赶"。先停掉飞行中的 pos 动画再 move：否则旧
        弹簧会在下一帧把图标拉回上一格（与 animate_to 里的 2026-10-18
        竞态同源）。
        """
        for a in list(self._anims):
            if a.propertyName() == b"pos":
                a.stop()
                if a in self._anims:
                    self._anims.remove(a)
        self.move(int(x), int(y))

    def drag_delta(self):
        """本次拖动相对**按下点**的全局位移（px）；不在拖动中返回 None。

        多选整块跟随的唯一取源：与拖影用的是同一个 gpos 序列，所以成员
        与手中图标逐像素同步，不会各跑各的——拖影跟手、成员却按格子重启
        280ms 弹簧，正是用户说的"只有 hover 那个是瞬间移动，其余不连贯"。
        """
        if not self._drag_loop or self._press_global is None \
                or self._last_gpos is None:
            return None
        return self._last_gpos - self._press_global

    def animate_to(self, x: int, y: int, duration: int = 280, damping: float = 0.78):
        """弹簧滑行到目标格（安卓 launcher 的让位语言）。

        与 press_scale / hover_scale / appear 是不同 property，可并行不冲突。
        曲线：欠阻尼 0.78 ≈ 2% 一次微弹——有生命但不廉价；
        落位（带动量）才用 0.62 ≈ 7% 过冲。
        reduced-motion 时直接跳到位（保留结果、去掉位移）。

        2026-10-18 M2 修复（probe17 S2/S5 实证的竞态）：无论走哪个
        分支，都必须先停掉飞行中的旧 pos 动画——否则旧动画会在下一帧
        把图标继续拖向它的旧目标。典型复现：预览动画刚起播就取消回滚，
        图标当前位置恰等于回滚目标 -> 早退分支放行旧动画 -> 松手后
        图标滑到预览位而非原位。
        """
        tx, ty = int(x), int(y)
        for a in list(self._anims):
            if a.propertyName() == b"pos":
                a.stop()
                if a in self._anims:      # stop 发 finished 可能已移走
                    self._anims.remove(a)
        if (self.pos().x(), self.pos().y()) == (tx, ty):
            return
        if effects.reduced_motion():
            self.move(tx, ty)
            return
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(effects.dur(duration))
        anim.setStartValue(self.pos())
        anim.setEndValue(QPoint(tx, ty))
        anim.setEasingCurve(effects.spring_curve(damping, duration))
        anim.finished.connect(lambda: self._anims.remove(anim)
                              if anim in self._anims else None)
        self._anims.append(anim)
        anim.start()
        return anim

    def set_drag_ghost(self, on: bool):
        """拖动期间本图标停止绘制（视觉上让出格子），不改变 geometry 与命中测试。"""
        if self._drag_ghost != on:
            self._drag_ghost = on
            self.update()

    # ---------- dwell 合并 ----------
    def _arm_merge(self):
        self._merge_armed = True
        self._merge_target = True
        self.update()
        QToolTip.showText(self.mapToGlobal(QPoint(self.width() // 2, 8)),
                          "松手合并为文件夹")

    def _disarm_merge(self):
        self._dwell.stop()
        self._merge_armed = False
        # 提示必须跟着收起：拖动中鼠标被 grabMouse 抓住、拖影是另一个
        # 窗口，Qt 认为鼠标没离开控件，QToolTip 只会等系统超时自然消失
        # （v7 §5.3 反馈 15 病根）。状态机"离开核心区"就地清掉。
        QToolTip.hideText()
        if self._merge_target:
            self._merge_target = False
            self.update()

    # ---------- 属性 ----------
    @property
    def is_folder(self) -> bool:
        return self.entry.get("type") == "folder"

    @property
    def is_file(self) -> bool:
        return entry_app(self.entry).get("kind") == "file"

    def _display_name(self) -> str:
        if self.is_folder:
            # M5：文件夹可重命名（指导文档 §4.2）；entry["name"] 缺省「文件夹」
            return self.entry.get("name") or "文件夹"
        return entry_app(self.entry).get("name", "")

    def _tooltip(self) -> str:
        if self.is_folder:
            name = self.entry.get("name") or "文件夹"
            names = "、".join(a.get("name", "") for a in self.entry.get("apps", [])[:8])
            return "%s：%s" % (name, names) if names else name
        app = self.entry.get("app", {})
        target = app.get("target", "")
        return "%s\n%s" % (app.get("name", ""), target) if target else app.get("name", "")

    def set_selected(self, on: bool):
        if self.selected != on:
            self.selected = on
            self.update()

    def set_folder_open(self, on: bool):
        """文件夹「打开中」态（M5，指导文档 §4.2 源 cell）：弹窗打开期间
        源图标高亮（ACCENT_SOFT + 描边），关闭恢复。"""
        if self._folder_open != bool(on):
            self._folder_open = bool(on)
            self.update()

    def refresh_meta(self):
        """名称/成员变化后刷新 tooltip 与重绘（M5 重命名 / 增删成员）。"""
        self.setToolTip(self._tooltip())
        self.update()

    # ---------- 绘制 ----------
    def paintEvent(self, event):
        if self._drag_ghost:
            return  # 拖动中：本格子已让出，由其他图标滑入填充
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        dpr = self.devicePixelRatioF()

        # 动效缩放：按压 x 悬停 x 错峰淡入（以图标视觉中心为原点，
        # widget geometry 不变——命中测试 / 框选判定完全不受影响）
        s = self._hover_scale * self._press_scale * self._appear
        if abs(s - 1.0) > 0.002 or self._appear < 0.995:
            cy = 2 + self.icon_size / 2.0
            p.translate(w / 2.0, cy)
            p.scale(s, s)
            p.translate(-w / 2.0, -cy)
        if self._appear < 0.995:
            p.setOpacity(self._appear)

        # 背景状态：文件夹打开中 / 悬停 / 合并目标 / 选中
        if self._folder_open:
            # 打开态最醒目：实心 tint + 强描边（一眼看出弹窗属于哪个 cell）
            p.setPen(QPen(theme.ACCENT, 1.5))
            p.setBrush(theme.ACCENT_SOFT)
            p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), theme.RADIUS_ITEM, theme.RADIUS_ITEM)
        elif self._merge_target:
            p.setPen(QPen(theme.ACCENT_BORDER, 1.5))
            p.setBrush(theme.ACCENT_SOFT)
            p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), theme.RADIUS_ITEM, theme.RADIUS_ITEM)
        elif self.selected:
            # 选中态加强：1.5px 描边 + 56% tint（多选/框选后一眼可辨）
            p.setPen(QPen(theme.ACCENT_BORDER, 1.5))
            p.setBrush(theme.ACCENT_SOFT)
            p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), theme.RADIUS_ITEM, theme.RADIUS_ITEM)
        elif self._hover:
            # v14j 反馈：hover 高亮样式可自定义可关（label_style.hover_style）
            cfg = self._hover_cfg()
            if cfg.enabled:
                fill = QColor(255, 255, 255, max(0, min(255,
                        int(round(cfg.fill_alpha * 255)))))
                # v14n：color_custom 进样式（原来每帧读两次 storage）
                border = QColor(cfg.color) if cfg.color_custom                     else theme.HOVER_BORDER
                p.setPen(QPen(border, 1))
                p.setBrush(fill)
                p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2),
                                  theme.RADIUS_ITEM, theme.RADIUS_ITEM)

        self._paint_icon_only(p, dpr)
        if not self._no_label:
            # 缩放动画中（s 偏离 1）走 pixmap 贴图，消除文字逐帧重排抽动；
            # v14j：贴图按当前帧缩放烘焙（1:1 贴，不重采样发糊）
            self._paint_label(p, scaled=abs(s - 1.0) > 0.002, scale=s)
        # 多选态标记画在最上层：压在图标左上角（不参与缩放，避免跟着
        # 悬停/按压一起抖）
        self._paint_select_badge(p)

    def _paint_icon_only(self, p: QPainter, dpr: float = 1.0):
        """只画图标本体（文件夹瓷砖或应用图标 pixmap），不画任何 chrome。

        正常绘制与拖影快照共用：快照模式下背景态/标签被跳过，剩下的就是
        "原图标"——用户拖动时看到的应该是手指下那个图标，不是一张卡片。
        """
        if self.is_folder:
            self._paint_folder(p, dpr)
            return
        app = self.entry.get("app", {})
        # 高清 pixmap：逻辑尺寸=icon_size（DPR 感知），按逻辑宽度居中，
        # 绝不能用 pm.width()（物理像素）算偏移——那是图标错位/变虚的根因。
        # v6 A2：有 layout 时绘制区左侧留白由 chrome_x 精确给出
        #（cell 变小后按比例派生，不再 (w-icon_size)//2 近似）。
        pm = appsource.icon_pixmap(app, self.icon_size, dpr)
        px = (self._box_x if self._box_x is not None
              else (self.width() - self.icon_size) // 2)
        p.drawPixmap(px, 2, pm)

    def _paint_folder(self, p: QPainter, dpr: float = 1.0):
        """深色圆角方形 + 迷你图标矩阵（安卓风，按成员数 1/2/3/4 自适应）。

        瓷砖材质（2026-10-18 升级）：垂直渐变（顶亮底沉，受光方向与液态
        玻璃一致）+ 顶部 1px 内高光 + 细描边——替代原来的纯色平底。
        M5：渐变取 entry 色板色（folder_tint，与弹窗背板同一颜色），
        缺省回退 theme 深灰瓷砖（iOS 文件夹语言：图标与弹窗同色）。
        """
        s = self.icon_size
        if self._big:
            self._paint_folder_big(p, dpr)
            return
        if self._box_x is not None:
            # v6 A2：layout 模式——瓷砖贴满绘制区（左右各 1px 呼吸）
            box = QRectF(self._box_x + 1, 3, s - 2, s - 2)
        else:
            box = QRectF(3, 3, s - 6, s - 6)
        tint = folder_tint(self.entry)
        liquid = folder_liquid(self.entry)
        if liquid:
            # v14j 反馈：液态瓷砖走真折射（_paint_folder_lens 按全局矩形
            # 从壁纸快照裁 + 边缘位移）；壁纸快照不可用/表面被关时退回
            # 半透明白渐变兜底（v14d 前的固定观感）。
            # （2026-10-25 事故修复：原来写成 `if liquid and not lens():`
            #  兜底 / else tint 渐变——镜头**成功**时也落进 else，把不透明
            #  tint 渐变盖在折射上，瓷砖变成"白底"。拆成独立两层。）
            if self._paint_folder_lens(p, box, s, dpr):
                pass
            else:
                grad = QLinearGradient(0, box.top(), 0, box.bottom())
                grad.setColorAt(0.0, QColor(255, 255, 255, 58))
                grad.setColorAt(1.0, QColor(255, 255, 255, 22))
                p.setPen(QPen(QColor(255, 255, 255, 104), 1))
                p.setBrush(QBrush(grad))
                p.drawRoundedRect(box, s * 0.24, s * 0.24)
        else:
            grad = QLinearGradient(0, box.top(), 0, box.bottom())
            grad.setColorAt(0.0, tint.lighter(135))
            grad.setColorAt(1.0, tint.darker(165))
            p.setPen(QPen(theme.FOLDER_BORDER, 1))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(box, s * 0.24, s * 0.24)
            # 顶部内高光（光线打在瓷砖上沿，与玻璃 specular 同语言）
            hl = QLinearGradient(box.left() + s * 0.18, 0,
                                 box.right() - s * 0.18, 0)
            hl.setColorAt(0.0, QColor(255, 255, 255, 0))
            hl.setColorAt(0.5, theme.FOLDER_HILIGHT)
            hl.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(QPen(QBrush(hl), 1.0))
            p.drawLine(QPointF(box.left() + s * 0.16, box.top() + 1.2),
                       QPointF(box.right() - s * 0.16, box.top() + 1.2))
        members = self.entry.get("apps", [])
        n = len(members)
        # v7 B5 反馈 13（2026-10-21）：比例 0.42/0.34/0.30（1/2/3-4 成员），
        # 一律走 appsource.draw_icon 的 contain 居中绘制。
        if n <= 1:
            slots = [(0.50, 0.50, 0.42)]
        elif n == 2:
            slots = [(0.28, 0.50, 0.34), (0.72, 0.50, 0.34)]
        elif n == 3:
            slots = [(0.50, 0.27, 0.30), (0.27, 0.73, 0.30),
                     (0.73, 0.73, 0.30)]
        else:
            slots = [(0.27, 0.27, 0.30), (0.73, 0.27, 0.30),
                     (0.27, 0.73, 0.30), (0.73, 0.73, 0.30)]
        clip = QPainterPath()
        clip.addRoundedRect(box, s * 0.24, s * 0.24)
        p.save()
        p.setClipPath(clip)      # 防溢出瓷砖（圆角外不再露 icon 边角）
        for (fx, fy, fr), app in zip(slots, members[:4]):
            cs = max(8, int(round(s * fr)))
            cx = box.left() + fx * box.width()
            cy = box.top() + fy * box.height()
            appsource.draw_icon(p, app, QRectF(cx - cs / 2.0, cy - cs / 2.0,
                                               cs, cs), dpr)
        p.restore()

    BIG_COLS = 3                    # 大瓷砖内部 3×3 成员格
    BIG_SLOTS = 9

    def _paint_folder_big(self, p, dpr: float):
        """大文件夹瓷砖（v14t，用户截图语义）：2×2 跨格，内部 3×3 直显成员。

        * 排布从左到右、从上到下（优先左上对齐）；
        * 排布满（>9 个成员）时最后一格为「…」占位符；
        * 瓷砖材质与普通文件夹一致（液态配色走折射，颜色走渐变）；
        * 交互：成员格点击直接启动（双模式），空白格点击展开（见
          mousePressEvent/_member_at）。
        """
        w, h = self.width(), self.height()
        members = self.entry.get("apps") or []
        full = len(members) > self.BIG_SLOTS
        visible = members[:self.BIG_SLOTS - 1] if full else members[:self.BIG_SLOTS]
        pad = max(6, int(round(10 * self._m)))
        label_h = self._label_h if self._label_h else int(round(18 * self._m))
        grid_w = w - pad * 2
        grid_h = h - pad - label_h
        cell_w = grid_w / 3.0
        cell_h = grid_h / 3.0

        # 瓷砖底（液态/颜色渐变，与 1×1 瓷砖同语言）
        box = QRectF(1, 1, w - 2, h - 2)
        if self._big and not self._paint_folder_lens(p, box, w, dpr):
            tint = folder_tint(self.entry)
            grad = QLinearGradient(0, box.top(), 0, box.bottom())
            grad.setColorAt(0.0, tint.lighter(135))
            grad.setColorAt(1.0, tint.darker(165))
            p.setPen(QPen(theme.FOLDER_BORDER, 1))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(box, theme.RADIUS_PANEL, theme.RADIUS_PANEL)

        clip = QPainterPath()
        clip.addRoundedRect(box, theme.RADIUS_PANEL, theme.RADIUS_PANEL)
        p.save()
        p.setClipPath(clip)
        slots = visible + [None] if full else visible + [None] * (self.BIG_SLOTS - len(visible))
        for idx in range(self.BIG_SLOTS):
            r, c = divmod(idx, 3)
            cx = box.left() + pad + c * cell_w + cell_w / 2.0
            cy = box.top() + pad + r * cell_h + cell_h / 2.0
            if idx < len(visible):
                m = visible[idx]
                side = min(cell_w, cell_h) * 0.72
                try:
                    appsource.draw_icon(
                        p, m, QRectF(cx - side / 2.0, cy - side / 2.0,
                                     side, side), dpr)
                except Exception:      # noqa: BLE001
                    pass
            elif idx == self.BIG_SLOTS - 1 and full:
                # 「…」占位符（用户语义：排布满了最后一位是 …）
                p.setPen(QPen(QColor(255, 255, 255, 200), 2))
                y = cy
                for dx in (-8, 0, 8):
                    p.drawEllipse(QPointF(cx + dx, y), 2.2, 2.2)
        p.restore()

    def _member_at(self, pos) -> int:
        """点击位置 -> 内部成员下标（-1 = 空白/…占位/出界）。"""
        if not self._big:
            return -1
        w, h = self.width(), self.height()
        pad = max(6, int(round(10 * self._m)))
        label_h = self._label_h if self._label_h else int(round(18 * self._m))
        grid_w = w - pad * 2
        grid_h = h - pad - label_h
        if not (pad <= pos.x() <= w - pad and pad <= pos.y() <= pad + grid_h):
            return -1
        c = int((pos.x() - pad) / (grid_w / 3.0))
        r = int((pos.y() - pad) / (grid_h / 3.0))
        c = max(0, min(2, c))
        r = max(0, min(2, r))
        idx = r * 3 + c
        members = self.entry.get("apps") or []
        if idx >= len(members):
            return -1                  # 空槽 / … 占位
        return idx

    def _paint_folder_lens(self, p, box, s: int, dpr: float) -> bool:
        """折叠态液态玻璃瓷砖：真折射（v14j 反馈）。

        弹窗是折射玻璃，折叠瓷砖却只有半透明白渐变——同一个文件夹两种
        材质语言（用户指出"未展开时四周没有物理效果"）。这里按瓷砖的
        **全局矩形**从壁纸快照裁一块走 liquid_glass 折射（与弹窗同一管
        线、同一总闸）。返回 False = 快照不可用/总闸关，调用方走兜底。

        瓷砖随 hover/press 动画逐帧重绘：折射贴图用 LiveLens 缓存
        （矩形/参数/壁纸变了才重算），动画帧零成本。
        """
        try:
            from . import liquid_glass
            storage = getattr(self, "storage", None) or self._storage()
            if storage is None:
                return False
            if not liquid_glass.surface_enabled(storage, "folder"):
                return False
            radius = s * 0.24
            global_top_left = self.mapToGlobal(box.topLeft().toPoint())
            grect = QRect(global_top_left,
                          QSize(max(2, int(box.width())),
                                max(2, int(box.height()))))
            lens = liquid_glass.lens_style(storage)
            if getattr(self, "_tile_lens", None) is None:
                self._tile_lens = liquid_glass.LiveLens()
            pm = self._tile_lens.pixmap(grect, radius, lens,
                                        float(self.devicePixelRatioF() or 1.0))
            if pm.isNull():
                return False
            return liquid_glass.draw_refracted(p, box, radius, pm)
        except Exception:            # noqa: BLE001 —— 折射失败走兜底渐变
            return False

    def _paint_label(self, p: QPainter, scaled: bool = False,
                     scale: float = 1.0):
        """画标签。scaled=True 时走 pixmap 缓存贴图。

        悬停/按压/错峰淡入动画期间 painter 带非整数缩放变换，逐帧直接
        drawText 会让字形取整每帧跳变——阴影/描边与文字互相错位抽动
        （用户反馈"放大时字体抽一下"）。动画帧改为把整层标签（含阴影
        描边）渲染进一张透明 pixmap 一次，再随变换平滑缩放地贴上去。
        静止态（s≈1）仍直接画文字，保持像素级清晰。

        v14j：贴图按当前帧缩放 `scale` 烘焙（见 _label_pixmap），贴图与
        矩阵 1:1，动画帧不再是"放大后的软图"——阴影与文字同层同缩放，
        发糊与错位一起消失。
        """
        items = self._label_items()
        if not items:
            return
        if not scaled:
            for rect, flags, line in items:
                _ls.draw_label_text(p, rect, flags, line,
                                    self._label_style, self._label_font_px)
            return
        origin, pm = self._label_pixmap(items, scale)
        p.save()
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawPixmap(origin, pm)
        p.restore()

    def _label_items(self):
        """当前标签的 (rect, flags, line) 列表（三条布局路径在此汇合）。"""
        name = self._display_name()
        if not name:
            return []
        if self._compact:
            # 文件夹成员：图标正下方一行小字（居中 + 末尾省略），
            # 与图标同一列宽，长文件名不会溢出到邻格（反馈 7）
            font = theme.ui_font(self._label_font_px,
                                 bold=self._label_style.bold)
            fm = QFontMetrics(font)
            rect = QRect(self._box_x, 2 + self.icon_size,
                         max(1, self.width() - self._box_x * 2),
                         max(1, self._label_h))
            line = fm.elidedText(name, Qt.ElideRight, rect.width())
            if not line:
                return []
            return [(rect, Qt.AlignHCenter | Qt.AlignVCenter, line)]
        if self._layout is not None:
            # 反馈 12：默认两行折行（一行显示不下折到第二行，末行省略号）。
            # 行数由 `grid_sizing.LABEL_LINES` 决定，标签区高度
            # （`layout.label_h`）已按两行预留；格子装不下时 grid_sizing
            # 自动退回单行，这里按实际预留高度算行数，两边不会不一致。
            font = theme.ui_font(self._label_font_px,
                                 bold=self._label_style.bold)
            fm = QFontMetrics(font)
            rect = QRect(2, self.icon_size + 2,
                         self.width() - 4, self._label_h)
            max_lines = 2 if self._label_h >= fm.height() * 2 - 2 else 1
            lines = self._wrap_elide(name, fm, rect.width(), max_lines)
            lh = fm.height() + 1
            return [(QRect(rect.left(), rect.top() + i * lh,
                           rect.width(), lh), Qt.AlignCenter, line)
                    for i, line in enumerate(lines) if line]
        # 旧路径（无 layout）：标签字号随 chrome 缩放（DPI×用户缩放）：
        # 100% 时 12px，125% 时 15px；最多两行折行 + 末行省略号
        font = theme.ui_font(self._label_font_px, bold=self._label_style.bold)
        fm = QFontMetrics(font)
        rect = QRect(2, self.icon_size + int(round(6 * self._m)),
                     self.width() - 4, int(round(34 * self._m)))
        lines = self._wrap_elide(name, fm, rect.width(), 2)
        lh = fm.height() + 1
        return [(QRect(rect.left(), rect.top() + i * lh,
                       rect.width(), lh), Qt.AlignCenter, line)
                for i, line in enumerate(lines) if line]

    def _label_pixmap(self, items, scale: float = 1.0):
        """把标签整层渲染成透明 pixmap（按内容键缓存，动画帧复用）。

        v14j 反馈：动画帧贴图按 1.0 倍烘焙再被矩阵放大（最高 1.04~1.08），
        双线性重采样把 1px 阴影一起软化——"文字动阴影没动/发糊"。现在
        按**当前帧缩放** `scale` 烘焙（字号×scale、dpr×scale），贴图与
        矩阵 1:1，放大帧同样是原生锐度；缓存 key 量化到 0.02 步进，一次
        hover 动画只烘 2~3 张（上升沿/下降沿），不会逐帧重排。
        """
        st = self._label_style
        # 量化：0.02 步进（视觉上无法分辨的缩放差共用一张贴图）
        q = max(0.02, round(max(0.2, scale) / 0.02) * 0.02)
        key = (tuple((r.x(), r.y(), r.width(), r.height(), f, l)
                     for r, f, l in items),
               st.color.rgba(), st.bold, st.shadow, st.shadow_color.rgba(),
               round(st.shadow_alpha, 3), st.outline,
               st.outline_color.rgba(), st.outline_width,
               self._label_font_px, q)
        cached = getattr(self, "_label_pm_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1], cached[2]
        bound = items[0][0]
        for r, _f, _l in items[1:]:
            bound = bound.united(r)
        # 阴影/描边最大越界 = 描边 3px + 阴影偏移 1px，四周留 4px 防裁
        margin = 4
        dpr = self.devicePixelRatioF() * q
        pm = QPixmap(max(1, int((bound.width() + margin * 2) * dpr)),
                     max(1, int((bound.height() + margin * 2) * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        pp = QPainter(pm)
        pp.setRenderHint(QPainter.Antialiasing)
        pp.setRenderHint(QPainter.TextAntialiasing)
        for r, flags, line in items:
            rr = QRect(r.x() - bound.x() + margin, r.y() - bound.y() + margin,
                       r.width(), r.height())
            _ls.draw_label_text(pp, rr, flags, line,
                                self._label_style, self._label_font_px * q)
        pp.end()
        origin = QPoint(bound.x() - margin, bound.y() - margin)
        self._label_pm_cache = (key, origin, pm)
        return origin, pm

    @staticmethod
    def _wrap_elide(name: str, fm: QFontMetrics, width: int, max_lines: int) -> list:
        """按像素宽度折行（西文按词、中文按字），超过 max_lines 时末行省略号。

        反馈 12：旧实现只按空格切词，中文文件名（没有空格）一行放不下只能
        整体省略——「显示不全」就是这么来的。这里在词内继续按字符断，
        中英混排都能折两行。
        """
        text = str(name or "").strip()
        if not text:
            return []
        tokens = []          # (文本, 与前一段之间要不要空格)
        for w_ in text.split(" "):
            if not w_:
                continue
            if fm.horizontalAdvance(w_) <= width:
                tokens.append((w_, True))
                continue
            cur = ""
            for ch in w_:
                if cur and fm.horizontalAdvance(cur + ch) > width:
                    tokens.append((cur, True))
                    cur = ch
                else:
                    cur += ch
            if cur:
                tokens.append((cur, True))
        lines, cur = [], ""
        for tok, spaced in tokens:
            trial = (cur + (" " if spaced else "") + tok) if cur else tok
            if fm.horizontalAdvance(trial) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = tok
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            # 超长名：把第 max_lines 行起的所有内容**并进去再省略**。旧实现
            # 只对"最后保留行"做 elidedText——那一行本来就放得下时省略号不会
            # 出现，后面的字被静默吃掉（反馈 12：文件名"显示不全"的另一种）。
            sep = " " if " " in text else ""      # 中文直接拼，西文保留词间空格
            kept = lines[:max_lines - 1]
            kept.append(fm.elidedText(sep.join(lines[max_lines - 1:]),
                                      Qt.ElideRight, width))
            return kept
        return [fm.elidedText(l, Qt.ElideRight, width) for l in lines]

    # ---------- 鼠标 ----------
    def _picking_origin(self) -> bool:
        """宿主是否处于「取点网格原点」态（v7 §3.2）。

        取点态下点图标 = 在图标那个位置定原点，**不许**启动应用/开文件夹/
        改选中——否则用户往图标附近点一下就误启动了一个程序。
        """
        try:
            return bool(getattr(self.window(), "_picking_origin", False))
        except RuntimeError:
            return False

    def _forward_pick(self, e):
        """把这一次按下转交给桌面窗口的取点逻辑（控件坐标已换算）。"""
        try:
            win = self.window()
            finish = getattr(win, "_finish_pick", None)
            if callable(finish):
                pos = self.mapTo(win, e.position().toPoint())
                finish(pos)
        except RuntimeError:
            pass

    # ---------- 交互方式（v9 V4） ----------
    def _host(self):
        """宿主桌面窗口（文件夹弹窗里的图标没有，返回 None）。"""
        try:
            return self.window()
        except RuntimeError:
            return None

    def _storage(self):
        return getattr(self._host(), "storage", None)

    def _mobile(self) -> bool:
        return interaction.is_mobile(self._storage())

    def _select_mode(self) -> bool:
        """宿主是否处于多选态（手机模式菜单 ▸ 选择）。"""
        return bool(getattr(self._host(), "_selection_mode", False))

    # ---------- 长按（v9 V4-2 / V4-3） ----------
    def _on_long_press(self):
        """按住 450ms 不动：弹出菜单（手机语义），顶部多一项「选择」。"""
        if self._drag_loop or self._dragged or self._press_pos is None:
            return
        # 取点态下长按也不该弹菜单（那一态的唯一语义是"点哪儿定哪儿"）
        if self._picking_origin():
            return
        self._long_fired = True
        self._long_origin = self._press_global or _cursor_pos()
        # 按压态回弹：菜单都出来了还缩着会显得卡住
        self._animate(b"press_scale", self._press_scale, 1.0, 160,
                      effects.spring_curve(1.0, 160))
        menu = self._build_context_menu(long_press=True)
        self._long_menu = menu
        menu.aboutToHide.connect(self._on_long_menu_hidden)
        # popup 而不是 exec：exec 会开自己的模态循环，滑动事件被菜单吃掉，
        # 就再也做不到"长按后直接滑动转拖动"（用户要的手感）
        menu.popup(self._long_origin)
        self._long_watch.start()

    def _watch_long_slide(self):
        """菜单开着时轮询光标：动了就关菜单、转拖动。

        QMenu 弹出时有 mouse grab，AppIcon 收不到 mouseMove——只能主动读
        `QCursor.pos()`。30ms 一次，人手滑动 3px/帧都能跟上，代价可忽略。
        """
        if self._long_menu is None:
            self._long_watch.stop()
            return
        if not _left_button_down():
            # 已经松手，菜单在等用户点选：判定交给菜单自己
            self._long_watch.stop()
            return
        pos = _cursor_pos()
        origin = self._long_origin or pos
        if (pos - origin).manhattanLength() < interaction.LONG_SLIDE_SLOP:
            return
        self._long_watch.stop()
        self._close_long_menu()
        self._dragged = True
        # 等 popup 真正撤掉 grab 再起拖动循环（同一次事件里 grabMouse 会被弹层抢回）
        QTimer.singleShot(0, self._slide_into_drag)

    def _slide_into_drag(self):
        if self._drag_loop or not self._dragged:
            return
        # 菜单关闭（_close_long_menu → aboutToHide → _on_long_menu_hidden）
        # 会把 _press_pos 清成 None 防"误判单击"，但拖影跟随与命中/落点
        # 计算都依赖这个抓取偏移——没有它拖影会冻结在原地、落点按错误
        # 矩形计算。从按下时记录的全局坐标重建（长按期间图标未移动，
        # 局部偏移仍有效）。
        if self._press_pos is None and self._press_global is not None:
            self._press_pos = self.mapFromGlobal(self._press_global)
        self.dragStarted.emit()
        self._exec_drag()

    def _close_long_menu(self):
        m = self._long_menu
        self._long_menu = None
        self._long_watch.stop()
        if m is not None:
            try:
                m.close()
            except RuntimeError:
                pass

    def _stop_long(self):
        """撤掉长按表并关掉已弹出的菜单（形成拖动 / 打包拆除时用）。"""
        self._long.stop()
        if self._long_menu is not None:
            self._close_long_menu()

    def _on_long_menu_hidden(self):
        self._long_watch.stop()
        self._long_menu = None
        # 菜单收起后本次按下就结束了：清掉按下态，避免后续误判成"单击"
        # （点菜单项时抬起事件不会回到图标）
        self._press_pos = None
        self._long_fired = False

    # ---------- 多选态（v9 V4-2） ----------
    def set_selection_visible(self, on: bool):
        """进入/退出多选态：左上角出现空心圆（选中变对钩）。"""
        on = bool(on)
        if self._selection_visible != on:
            self._selection_visible = on
            self.update()

    def set_label_style(self, style):
        """热更新标签文字样式（v14 反馈 3：设置里改完立刻重画，不重建图标）。"""
        self._label_style = style
        # v14q：字号缩放变了要重算字号（贴图缓存 key 含字号，自动失效）
        base = getattr(self, "_label_font_base", None)
        if base is not None:
            self._label_font_px = self._scale_font_px(base)
        self.update()

    def invalidate_hover_style(self):
        """hover 样式设置变了（v14j）：丢弃缓存，下次 enter/重绘重取。"""
        self._hover_style_cache = None
        self.update()

    def _paint_select_badge(self, p):
        """左上角多选标记：空心圆 → 实心对钩。"""
        if not self._selection_visible:
            return
        d = max(16, int(round(BADGE_PX * self._m)))
        left = (self._box_x if self._box_x is not None else int(round(2 * self._m)))
        rect = QRectF(left + 1, 1, d, d)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self.selected:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(rect)
            pen = QPen(QColor("#FFFFFF"), max(1.6, d * 0.12))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
            p.drawPolyline([
                QPointF(x + w * 0.26, y + h * 0.52),
                QPointF(x + w * 0.44, y + h * 0.70),
                QPointF(x + w * 0.76, y + h * 0.33),
            ])
        else:
            p.setPen(QPen(QColor(255, 255, 255, 190), max(1.2, d * 0.09)))
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawEllipse(rect.adjusted(0.8, 0.8, -0.8, -0.8))

    def mousePressEvent(self, e):
        if self._picking_origin():
            self._forward_pick(e)
            return
        if e.button() == Qt.LeftButton and self._big:
            # v14t：大瓷砖按下记录成员槽位（空白 = -1）——release 分流
            # 启动 / 展开；拖拽仍走既有逻辑（拖的是文件夹本体）
            self._press_member = self._member_at(e.pos())
        if e.button() == Qt.LeftButton:
            self._press_pos = e.pos()
            self._press_global = e.globalPos()
            self._dragged = False
            self._long_fired = False
            # 按下即撤悬停展开表：点击有点击自己的语义（打开/关闭开关），
            # 悬停计时不该在按下后又补一刀
            self._hover_open.stop()
            # 苹果 HIG 按压反馈：scale(0.97) 100ms ease-out，pointer-down
            # 即触发不等松手（按下即反馈，消灭延迟）
            self._animate(b"press_scale", self._press_scale, 0.97, 100,
                          QEasingCurve.OutCubic)
            # v9 V4：手机模式按住 450ms 弹菜单。Windows 模式不起表
            # （那套语义是"单击选中 / 双击打开"，长按没有定义）
            if self._mobile():
                self._long.start()
        elif e.button() == Qt.RightButton and self._drag_loop:
            # 拖动中按右键 = 取消（与 OLE 拖拽语义一致）
            self._cancel_drag()

    def mouseDoubleClickEvent(self, e):
        if self._big and e.button() == Qt.LeftButton and not self._mobile():
            member = self._member_at(e.pos())
            members = self.entry.get("apps") or []
            if 0 <= member < len(members):
                self.activated.emit(members[member])
                return
            if member < 0:
                self.folderClicked.emit(self.entry["id"])
                return
        super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_loop:
            # 拖动进行中：grabMouse 保证移动事件全部送到本部件，
            # 由拖拽循环驱动拖影 / 命中测试 / 合成拖放事件
            self._drag_move(e.globalPos())
            return
        if self._press_pos is None or not (e.buttons() & Qt.LeftButton):
            return
        if (e.pos() - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        # 形成拖动：撤长按表，并关掉可能已经弹出的长按菜单（"按住不放
        # 久了会先出菜单，再拖就该是拖"——菜单不能挂在那儿跟着走）
        self._stop_long()
        self._dragged = True
        self.dragStarted.emit()
        self._exec_drag()

    def mouseReleaseEvent(self, e):
        if self._drag_loop and e.button() == Qt.LeftButton:
            self._finish_drag(e.globalPos())
            return
        long_fired = self._long_fired
        self._long.stop()
        if e.button() == Qt.LeftButton:
            self._long_fired = False
        if e.button() == Qt.LeftButton and self._big and self._press_pos is not None                 and not self._dragged and not self._select_mode():
            # v14t 大瓷砖：成员格（双模式）/ 空白格（展开）
            member = getattr(self, "_press_member", -1)
            self._press_pos = None
            self._animate(b"press_scale", self._press_scale, 1.0, 160,
                          effects.spring_curve(1.0, 160))
            if member < 0:
                self.pulse()
                self.folderClicked.emit(self.entry["id"])
                return
            members = self.entry.get("apps") or []
            if member < len(members):
                app = members[member]
                if self._mobile():
                    self.pulse()
                    self.activated.emit(app)
                # Windows 习惯 = 双击启动（mouseDoubleClickEvent 分流）
            return
        if e.button() == Qt.LeftButton and self._press_pos is not None and not self._dragged:
            if long_fired:
                # 长按已弹菜单：这次抬起不算"单击"——否则用户长按看一眼菜单，
                # 松手就把应用启动了（手机上也不会这样）
                self._animate(b"press_scale", self._press_scale, 1.0, 180,
                              effects.spring_curve(1.0, 180))
            elif self.is_folder:
                # 打开文件夹：脉冲反馈（自带弹簧回落，不再叠加归位动画）
                self.pulse()
                self.folderClicked.emit(self.entry["id"])
            elif self._mobile() and not self._select_mode():
                # 手机语义：单击直接启动（Windows 模式的"单击选中"是另一套）
                self.pulse()
                self.activated.emit(self.entry.get("app", {}))
            else:
                self.clicked.emit(self.entry["id"])
                # 松开回弹：点按无动量，按苹果规范用临界阻尼弹簧（无过冲）
                self._animate(b"press_scale", self._press_scale, 1.0, 220,
                              effects.spring_curve(1.0, 220))
        elif e.button() == Qt.LeftButton and self._press_pos is not None:
            # 未形成点击的抬起（如拖拽被取消）：临界阻尼平滑归位
            self._animate(b"press_scale", self._press_scale, 1.0, 180,
                          effects.spring_curve(1.0, 180))
        self._press_pos = None

    def keyPressEvent(self, e):
        if self._drag_loop and e.key() == Qt.Key_Escape:
            # Esc 取消拖拽（与 OLE 拖拽语义一致）
            self._cancel_drag()
            e.accept()
            return
        super().keyPressEvent(e)

    # ---------- 自管理拖拽循环（替代 QDrag.exec 的 OLE DoDragDrop） ----------
    def _drag_snapshot(self) -> QPixmap:
        """拖动浮影快照 = **图标本体 + 文件名**，透明底，烘焙 GHOST_OPACITY。

        2026-10-19 两轮反馈叠在一起：
          - "拖拽还有一个白框，不好看，只要原图标就行了"（`render()` 会铺
            调色板底色 → 白框）；
          - "图标拖拽的时候丢失了文件名"（为去白框把标签一起裁掉了）。
        正解不是二选一：**不 render 部件**（避开调色板底色），改用与
        paintEvent 同一份 `_paint_icon_only` + `_paint_label` 直接画到
        透明 pixmap 上——既有原图标也有名字，且没有卡片底色。

        必须在 set_drag_ghost(True) **之前**调用（虚影态 paintEvent 早退）。
        """
        dpr = max(1.0, float(self.devicePixelRatioF()))
        w, h = max(1, self.width()), max(1, self.height())
        pm = QPixmap(max(1, int(round(w * dpr))), max(1, int(round(h * dpr))))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        self._paint_icon_only(p, dpr)
        if not self._no_label:
            self._paint_label(p)
        p.end()
        self._drag_crop_off = QPoint(0, 0)
        if GHOST_OPACITY >= 1.0:
            return pm
        faded = QPixmap(pm.size())
        faded.setDevicePixelRatio(dpr)
        faded.fill(Qt.transparent)
        p = QPainter(faded)
        p.setOpacity(GHOST_OPACITY)
        p.drawPixmap(0, 0, pm)
        p.end()
        return faded

    def _exec_drag(self):
        """启动拖拽：grabMouse + 浮动拖影 + 合成 Qt 拖放事件。

        不再走 QDrag.exec()/OLE DoDragDrop——该模态循环会吞掉注入的
        鼠标释放事件，合成输入下端到端拖拽永不落位（DEVLOG Day4 实证：
        17ms 内即取消返回、零个 DragEnter；真实鼠标下 OLE 虽可用，
        但两条输入路径行为不一致）。grabMouse 让真实鼠标与合成输入
        走同一条代码路径，drop 侧（dragEnter/dragMove/drop 事件处理器）
        完全不变，只是改由本循环派发合成事件驱动。
        """
        payload = self.drag_payload
        if self.payload_provider is not None:
            try:
                payload = self.payload_provider(self.drag_payload) or payload
            except Exception:
                pass
        # 提拉感：拖动源放大 1.06。自管理循环不阻塞 Qt 定时器，
        # 动画照常驱动，这里仍直接置位保证即时反馈。
        self._set_press_scale(1.06)
        # 快照必须先取：set_drag_ghost(True) 之后 paintEvent 早退
        # （本格视觉让出），再 render 抓到的是全透明空图——拖影一片
        # 空白，用户看不到自己拖的是什么（v6 §5.1 顺序病根）。
        pm = self._drag_snapshot()
        # 虚影：本格子在拖动期间视觉让出（其他图标滑入填充），
        # 不 hide()——不动 geometry 与命中测试，撤销只翻一个标志位
        self.set_drag_ghost(True)
        self._drag_mime = QMimeData()
        self._drag_mime.setData(MIME_TYPE, payload.encode())
        self._drag_ghost_w = _DragGhost(pm)
        self._drag_ghost_w.show()
        self._drag_target = None
        # 双区模型状态每次拖动从零开始（上次的核心区判定不作数）
        self._core_target_id = None
        self._core_in = False
        self._drag_loop = True
        self._last_gpos = None
        # 本次拖动的整块 id 集合：命中测试要排除自己人（v14z）
        self._drag_ids = {p for p in str(payload).split(",") if p}
        # P3：拖动开始时缓存同窗桌面图标候选（_best_overlap_icon 用）
        host = self.window()
        if host is not None:
            try:
                self._drag_kids = [w for w in host.findChildren(AppIcon)
                                   if w is not self and not w.in_folder]
            except RuntimeError:
                self._drag_kids = None
        # 键盘焦点给拖拽源：Esc 取消（keyPressEvent）走焦点投递，
        # grabMouse 只管鼠标。真实鼠标与注入按键共用同一路径。
        self.setFocus()
        self.grabMouse()

    def _drag_move(self, gpos):
        """拖动中：拖影跟随光标 + 命中测试 + 合成 dragEnter/dragMove/dragLeave。"""
        self._last_gpos = QPoint(gpos)
        ghost = self._drag_ghost_w
        if ghost is not None and self._press_pos is not None:
            # 快照裁到图标区后左上角不等于部件左上角：减去裁切偏移，
            # 图标才会正好落在指针下（否则会偏出半个标签行）
            off = self._drag_crop_off
            ghost.move(int(gpos.x() - self._press_pos.x() + off.x()),
                       int(gpos.y() - self._press_pos.y() + off.y()))
        self.dragMoved.emit()
        target = self._hit_target(gpos)
        if target is not self._drag_target:
            prev, self._drag_target = self._drag_target, target
            if prev is not None:
                self._send_drag_event(prev, QDragLeaveEvent())
            if target is not None:
                self._send_drag_event(target, QDragEnterEvent(
                    target.mapFromGlobal(gpos), Qt.MoveAction,
                    self._drag_mime, Qt.LeftButton, Qt.NoModifier))
        elif target is not None:
            self._send_drag_event(target, QDragMoveEvent(
                target.mapFromGlobal(gpos), Qt.MoveAction,
                self._drag_mime, Qt.LeftButton, Qt.NoModifier))

    def _hit_target(self, gpos):
        """拖放命中：**双区模型**分流（2026-10-21 反馈 7 = v7 §5.2）。

        用户要的手机语义是两套手势，按**拖影中心**落在目标图标哪个区判：

          - 中心在目标核心区（中心 55% 内缩矩形，带 0.06 滞回）
            → 合并候选：返回目标图标（发 dragEnter 武装 dwell 提示）
          - 中心在核心区外（让位区）
            → 返回桌面窗口（dragMove 驱动 _reflow_preview 武装推让，
              悬停 PUSH_DWELL_MS 后目标实时滑走）

        重叠比只当"是否压在图标上"的门槛（`MERGE_ARM_RATIO`）：低于它
        视为没碰上，不走双区（纯擦过不误判，也不驱动挤走）。

        旧实现按重叠比 0.30 一刀切（2026-10-20 F3），两个病根正对反馈 7
        原话："加入文件夹的判定范围太大了，且没有了边缘判定为碰撞时候的
        实时挤开效果"——0.30 时刚擦上去就弹合并提示；0.15~0.30 区间又被
        defers_push 整体 defer，挤走路径整个死掉。

        文件夹内部（in_folder）保持原逻辑：成员只在弹窗里拖动/拖出，
        不参与桌面合并。
        """
        ghost = self._drag_ghost_w
        if not self.in_folder:
            best, ratio = self._best_overlap_icon()
            if best is not None and ratio >= drag_merge.MERGE_ARM_RATIO:
                g = self._ghost_rect()
                t = self._icon_global_rect(best)
                if g is not None and t is not None:
                    same = (self._core_target_id == best.drag_payload)
                    was_in = self._core_in if same else None
                    if drag_merge.in_merge_core(
                            (g.x(), g.y(), g.width(), g.height()),
                            t, was_in):
                        # 中心压住对方正中 -> 合并候选
                        self._core_target_id = best.drag_payload
                        self._core_in = True
                        return best
                    # 让位区：记住"在这个目标外"，滞回据此收缩/外扩
                    self._core_target_id = best.drag_payload
                    self._core_in = False
            else:
                self._core_target_id = None
                self._core_in = False
        target = self._widget_at_drag(gpos)
        if self.in_folder:
            if target is None:
                # widgetAt 在拖影（Qt.ToolTip 窗）下面恒为 None，而拖动中
                # 光标几乎一直落在拖影上——旧实现直接返回 None，合成
                # dragMove / drop 一个都发不出去：拖出成员既无落点预览，
                # 松手也不提取（2026-10-21 反馈 6，慢速拖 100% 复现）。
                # 自算承载面补上。
                target = self._drag_surface_at(gpos)
            return target
        # v10 A9（反馈 2）：**Dock 条带优先**。Dock 是独立分层窗，`widgetAt`
        # 在它的透明边 / 圆角外会穿透到下面的整理窗口，于是"拖到 Dock 上"
        # 时有时拿到 Dock、有时拿到桌面，落点判定在两条路径间摇摆——用户
        # 看到的就是"拖不进 Dock"。这里按条带几何直接判定（与
        # `desktop_window._dock_hit` 同一口径：visible + 窗口矩形包含点）。
        dock = self._dock_window()
        if dock is not None:
            try:
                if dock.is_visible_bar() and dock.geometry().contains(gpos):
                    return dock
            except (RuntimeError, AttributeError):
                pass
        if isinstance(target, AppIcon) and target is not self:
            # 只擦到边缘：目标保持不动，落点交给桌面窗口（实时挤走语义）
            return self._host_desktop(gpos)
        if target is None:
            return self._host_desktop(gpos)
        return target

    def _widget_at_drag(self, gpos):
        """拖动中的 `widgetAt`：穿拖影 + **滤掉合并提示气泡**。

        旧实现在这两步上各栽过一次：

        1. 光标被拖影（Qt.ToolTip 窗）盖住时 `widgetAt` 恒返回拖影本身
           —— 改探**拖影中心**穿透它（下面的 is-ghost 分支）；
        2. 「松手合并」提示弹出后，`widgetAt` 在光标处捡到的是 QToolTip
           内部的 QLabel（objectName=qtooltip_label），旧代码把它当成
           拖放目标：合成 dragMove 全发给气泡，桌面侧推让 / 让位预览
           整条断掉——现象是"拖到核心区出了提示，再拖出来挤开效果不
           恢复"（2026-10-21 反馈 7 端到端实测抓到）。气泡窗的类型是
           `Qt.ToolTip`（见 `_is_tooltip_window`）；滤掉后按"光标下没有
           合法落点"处理，由 `_host_desktop` 兜底。

        拖影自己不过滤（外层靠 `is ghost` 判定穿透），FolderPopup /
        ScrimWindow 等真落点是 Qt.Tool/Popup 类型，与 ToolTip 类型不
        相等，不受影响。
        """
        def at(p):
            w = QApplication.widgetAt(p)
            if w is None or w is self._drag_ghost_w:
                return w
            if _is_tooltip_window(w):
                return None
            return w

        ghost = self._drag_ghost_w
        target = at(gpos)
        if target is ghost or target is self:
            if ghost is not None:
                gc = ghost.mapToGlobal(QPoint(ghost.width() // 2,
                                             ghost.height() // 2))
                t2 = at(gc)
                if t2 is not ghost and t2 is not self:
                    target = t2
                else:
                    target = None
            else:
                target = None
        return target

    def _drag_surface_at(self, gpos):
        """拖影（`Qt.ToolTip` 窗）下面的真实承载面。

        `QApplication.widgetAt()` 在 tooltip 窗下返回 None；
        `topLevelAt()` 也只会返回拖影自己——两者都看不穿它。于是拖动中
        光标被拖影盖住时命中恒为 None：桌面图标有 `_host_desktop` 兜底
        没事，**文件夹成员**却在 `if self.in_folder: return target` 直接
        返回 None，合成 dragMove / drop 全都发不出去。

        自算：遍历可见顶层窗（拖影与自身除外），取**包含该点中面积最小**
        的一个——面积最小是 z-order 的可靠代理（弹窗 < 幕布 < 全屏桌面），
        不需要引用 DesktopWindow / ScrimWindow，也不引入循环依赖。
        提示气泡（窗口类型 Qt.ToolTip，判据见 `_is_tooltip_window`）同样
        排除：它不是承载面，成员拖出时若被它捡到，合成 dragMove 会发给
        气泡而丢失（同 `_widget_at_drag` 的病根，2026-10-21 反馈 7）。
        """
        best = None
        best_area = None
        for tl in QApplication.topLevelWidgets():
            if tl is None or tl is self._drag_ghost_w:
                continue
            try:
                if not tl.isVisible():
                    continue
                if _is_tooltip_window(tl):
                    continue
                r = tl.frameGeometry()
            except RuntimeError:
                continue
            if not r.contains(gpos):
                continue
            area = max(1, r.width()) * max(1, r.height())
            if best_area is None or area < best_area:
                best, best_area = tl, area
        return best

    def _ghost_rect(self):
        ghost = self._drag_ghost_w
        if ghost is None:
            return None
        try:
            return QRect(ghost.mapToGlobal(QPoint(0, 0)), ghost.size())
        except RuntimeError:
            return None

    @staticmethod
    def _icon_global_rect(widget):
        """同级图标的全局矩形（(x, y, w, h) 元组；已销毁返回 None）。

        双区模型的目标矩形来源：与拖影矩形同一坐标系（全局），
        `drag_merge.in_merge_core` 直接消费。
        """
        try:
            tl = widget.mapToGlobal(QPoint(0, 0))
            return (tl.x(), tl.y(), widget.width(), widget.height())
        except RuntimeError:
            return None

    def _overlap_ratio(self, other) -> float:
        """拖影与另一个部件的相交比例（占较小矩形面积的百分比 0~1）。

        算法单点定义在 drag_merge.overlap_ratio（桌面窗口侧共用同一份，
        避免两处各写一套导致手感漂移）。
        """
        g = self._ghost_rect()
        if g is None or g.isEmpty():
            return 0.0
        try:
            r = QRect(other.mapToGlobal(QPoint(0, 0)), other.size())
        except RuntimeError:
            return 0.0
        return drag_merge.overlap_ratio(
            (g.x(), g.y(), g.width(), g.height()),
            (r.x(), r.y(), r.width(), r.height()))

    def _best_overlap_icon(self):
        """与拖影重叠最多的同级桌面图标（排除自身/文件夹成员）。

        P3（2026-10-26）：候选列表在拖动开始时缓存一次（_drag_kids），
        不再每次鼠标事件 findChildren + 全量扫——鼠标 125~1000Hz 下这是
        拖动期间最大的 CPU 项。列表失效由拖动结束清缓存保证。
        """
        host = self.window()
        if host is None:
            return None, 0.0
        kids = getattr(self, "_drag_kids", None)
        if kids is None:
            try:
                kids = [w for w in host.findChildren(AppIcon)
                        if w is not self and not w.in_folder]
            except RuntimeError:
                return None, 0.0
            self._drag_kids = kids
        best, best_ratio = None, 0.0
        dragging = getattr(self, "_drag_ids", None) or set()
        for w in kids:
            if not w.isVisible():
                continue
            # v14z：多选整块跟手后，成员就压在拖影底下——它们不是合并
            # 目标（同一块东西不能拖进自己），必须先排除，否则命中测试
            # 永远命中"自己人"，桌面窗口收不到 dragMove，让位预览直接死。
            if getattr(w, "drag_payload", "") in dragging:
                continue
            ratio = self._overlap_ratio(w)
            if ratio > best_ratio:
                best, best_ratio = w, ratio
        return best, best_ratio

    def _host_desktop(self, gpos):
        """退化为宿主桌面窗口（空白区 / 拖影遮挡 / 边缘擦碰）。"""
        win = self.window()
        if win is not None and win is not self:
            try:
                if win.rect().contains(win.mapFromGlobal(gpos)):
                    return win
            except RuntimeError:
                return None
        return None

    def _dock_window(self):
        """宿主桌面窗口挂着的 Dock（没有则 None）。

        图标不知道 Dock 的存在，也不该知道——通过宿主窗口的公开属性拿引用，
        与 `widgetAt` 是"另一条判定路径"，但落点几何只有一份（Dock 自己）。
        """
        win = self.window()
        dock = getattr(win, "_dock", None)
        if dock is None or dock is self:
            return None
        return dock

    def _send_drag_event(self, widget, event):
        """派发合成拖放事件；目标已被销毁（rebuild/deleteLater）时静默跳过。"""
        try:
            QApplication.sendEvent(widget, event)
        except RuntimeError:
            pass

    def _finish_drag(self, gpos):
        """松手：落点有效 -> 合成 drop；gpos 为 None（取消）-> dragLeave。"""
        self._drag_loop = False
        self._drag_ids = set()
        try:
            self.releaseMouse()
        except RuntimeError:
            pass
        target, self._drag_target = self._drag_target, None
        if target is not None:
            if gpos is not None:
                self._send_drag_event(target, QDropEvent(
                    target.mapFromGlobal(gpos), Qt.MoveAction,
                    self._drag_mime, Qt.LeftButton, Qt.NoModifier))
            else:
                self._send_drag_event(target, QDragLeaveEvent())
        self._after_drag()

    def _cancel_drag(self):
        self._finish_drag(None)

    def _after_drag(self):
        self._drag_kids = None          # P3：候选缓存随拖动结束失效
        """拖拽结束公共清理：撤拖影、发 dragFinished、落位回弹动画。

        整体防御 RuntimeError：drop 触发 rebuild 后本部件可能已被
        deleteLater（C++ 对象删除后任何触碰都抛 RuntimeError）。
        """
        try:
            ghost, self._drag_ghost_w = self._drag_ghost_w, None
            if ghost is not None:
                ghost.close()
            self._press_pos = None
            self.dragFinished.emit()
            # 松手收尾：**不再放大回弹**（2026-10-20 反馈 10）。
            # 旧实现从 1.06 起用欠阻尼 0.62 弹回 1.0——网格模式有落位动画
            # 盖着还好，无界模式位置没变，那一下"鼓包"格外突兀。
            # 改成从当前缩放临界阻尼归位：无过冲，末值精确 1.0。
            self._animate(b"press_scale", self._press_scale, 1.0, 140,
                          effects.spring_curve(1.0, 140))
        except RuntimeError:
            # 拖动期间本部件已被删除/重建（drop -> rebuild() 的 deleteLater、
            # FolderPopup 关闭等），信号源 C++ 对象已不存在。必须吞掉：
            # 异常会冲掉 dragFinished 信号 -> DesktopWindow _on_drag_finished
            # 不执行 -> _drag_active 永久卡死 -> nativeEvent 全窗口
            # HTCLIENT，桌面点击全部失效。
            pass
        # 安全网：dragFinished 信号万一丢失，也要把虚影撤掉，
        # 否则图标会永久不可见（DEVLOG 4.3 同源风险的绘制版）
        QTimer.singleShot(0, self._safe_unghost)

    def _safe_unghost(self):
        try:
            if self._drag_ghost:
                self.set_drag_ghost(False)
        except RuntimeError:
            pass

    def mouseDoubleClickEvent(self, e):
        if self._picking_origin():
            self._forward_pick(e)   # 取点态双击也只定点，不启动应用
            return
        if self.is_folder:
            self.pulse()
            self.folderClicked.emit(self.entry["id"])
        else:
            self.pulse()   # 启动反馈脉冲
            self.activated.emit(self.entry.get("app", {}))

    def _hover_cfg(self):
        """hover 样式与动画参数（懒取 + 热更新用）。"""
        cfg = getattr(self, "_hover_style_cache", None)
        if cfg is None:
            from . import label_style as _lsm
            cfg = _lsm.hover_style(self._storage())
            self._hover_style_cache = cfg
        return cfg

    def enterEvent(self, e):
        self._hover = True
        cfg = self._hover_cfg()
        # v14j 反馈：hover 放大幅度/时长可配置；100% = 关闭放大动画
        target = cfg.scale_pct / 100.0
        if abs(target - 1.0) > 0.001:
            self._animate(b"hover_scale", self._hover_scale, target,
                          cfg.anim_ms, effects.spring_curve(1.0, cfg.anim_ms))
        # v14 反馈 1：文件夹悬停自动展开（手机文件夹的"瞄一眼"语义）
        if self._hover_expand_ok():
            self._hover_open.setInterval(self._hover_open_ms())
            self._hover_open.start()
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self._hover_open.stop()
        cfg = self._hover_cfg()
        self._animate(b"hover_scale", self._hover_scale, 1.0,
                      max(80, cfg.anim_ms), effects.spring_curve(1.0, 160))
        self.update()

    def _hover_open_ms(self) -> int:
        """悬停自动展开驻留（v14u：设置可调，默认 260ms）。"""
        try:
            v = int(self._storage().get_setting("folder_hover_open_ms"))
        except Exception:        # noqa: BLE001
            return 260
        return max(80, min(1500, v))

    def _hover_expand_ok(self) -> bool:
        """文件夹悬停自动展开的放行判据（v14 反馈 1）。

        不放在 enterEvent 里就地判断，是为了让单测能直接钉这几条：
          1. 只对**桌面上的文件夹**（文件夹内部成员不展开自己）；
          2. 拖动中 / 已按下 / 长按菜单开着都不展开（手势优先）；
          3. 多选态不展开（那会儿悬停是"勾选预备"）；
          4. 这个文件夹已经开着就不触——`open_folder` 对同一 id 是**关**，
             悬停再触发一次等于用户什么都没做就把它关了；
          5. 设置 `folder_hover_expand` 可关（悬停展开不是所有人都习惯）。
        """
        if not self.is_folder or self.in_folder:
            return False
        if self._drag_loop or self._press_pos is not None:
            return False
        win = self.window()
        if win is None:
            return False
        if getattr(win, "_selection_mode", False):
            return False
        popup = getattr(win, "_popup", None)
        if popup is not None:
            try:
                if popup.entry.get("id") == self.entry.get("id"):
                    return False
            except Exception:      # noqa: BLE001
                return False
        st = getattr(win, "storage", None)
        if st is None:
            return False
        try:
            return bool(st.get_setting("folder_hover_expand"))
        except Exception:      # noqa: BLE001
            return False

    def _on_hover_open(self):
        """悬停计时到点：展开文件夹（与点击同一出口，iOS 语义的开关不变）。"""
        if not self._hover_expand_ok():
            return
        # v14：走独立信号——窗口据此给弹窗打「悬停打开」标记，
        # 鼠标离开图标与弹窗后自动收起（点击打开的弹窗不自动关）
        self.folderHoverOpened.emit(self.entry["id"])

    # ---------- 拖放接收 ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_TYPE):
            dragged = e.mimeData().data(MIME_TYPE).data().decode()
            if dragged not in (self.drag_payload, self.entry.get("id")):
                e.acceptProposedAction()
                # 悬停计时：够久才武装合并；未满即松手 = 让位置换
                self._dwell.start()

    def dragMoveEvent(self, e):
        if not e.mimeData().hasFormat(MIME_TYPE):
            return
        dragged = e.mimeData().data(MIME_TYPE).data().decode()
        if dragged in (self.drag_payload, self.entry.get("id")):
            return
        if self.in_folder:
            return  # 文件夹内部不互相合并 / 不让位
        if self._merge_armed:
            return  # 已进入合并分支，不再驱动让位预览
        # 2026-10-19 反馈 4：悬停在另一个图标上**不再驱动实时让位**。
        # 旧实现这里 emit reflowHover -> 主窗口立刻把本图标让位/交换走 ->
        # 指针下的目标消失 -> dragLeave -> dwell(450ms) 归零 ->
        # 「松手合并为文件夹」提示永远出不来（用户："还没有出现提示两个
        # 图标就分开位置了"）。现在悬停只维持 dwell 计时，目标留在原地；
        # 松手时若未武装（不够久）仍由 dropEvent 走 reflowDrop 做让位交换。
        # 让位预览只在拖到**空白格**时生效（主窗口 dragMoveEvent 负责）。
        e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._disarm_merge()
        e.accept()

    def dropEvent(self, e):
        dragged = e.mimeData().data(MIME_TYPE).data().decode()
        armed = self._merge_armed          # 必须先取，_disarm_merge 会清掉它
        self._disarm_merge()
        if dragged in (self.drag_payload, self.entry.get("id")):
            return
        if self.in_folder:
            return  # 文件夹内部不互相合并
        if not armed:
            # 悬停未满 450ms：不合并，走网格让位。
            # 必须直连上报主窗口，不能靠 e.ignore() 冒泡——实测（probe15 G11a）
            # drop 事件不会从图标传播到父窗口（propagated=0），冒泡丢失会让
            # 预览永不提交、最终被 _cancel_preview 回退，表现为"拖了却没动"。
            # 2026-10-18 M2：同样报指针全局坐标（见 dragMoveEvent 注释）。
            e.acceptProposedAction()
            g = self.mapToGlobal(e.position().toPoint())
            self.reflowDrop.emit(dragged, g.x(), g.y())
            return
        self._merge_target = False
        self.update()
        e.acceptProposedAction()
        self.mergeRequested.emit(dragged, self.entry["id"])

    # ---------- 右键菜单 ----------
    def contextMenuEvent(self, e):
        # v9 V4：多选态下的右键 = 批量操作（对单个图标调大小/改写语义没意义）
        if self._select_mode():
            host = self._host()
            build = getattr(host, "_build_batch_menu", None)
            if callable(build):
                menu = build()
                if menu is not None:
                    menu.exec(e.globalPos())
                    return
        self._build_context_menu().exec(e.globalPos())

    def _build_context_menu(self, long_press: bool = False):
        """构建图标右键菜单（拆出来便于单测：v7 反馈 14 的尺寸子菜单）。

        `long_press=True` = 手机模式长按弹出的那份：顶部多一项「选择」
        （进入多选，随后可批量合并 / 移页 / 删除）。
        """
        menu = QMenu(self)
        menu.setStyleSheet(theme.MENU_QSS)
        # 子菜单是 Python 所有的 QMenu，需要持引用防 GC（DEVLOG 坑 #39）
        self._ctx_submenus = []
        if long_press:
            host = self._host()
            if host is not None and hasattr(host, "enter_selection_mode"):
                # v10 A3（反馈 7）：长按进多选时**自动勾选被长按的这个图标**。
                # 以前直接绑 `host.enter_selection_mode`——QAction.triggered
                # 会把 checked=False 传进 first_id，恒为空 → 进了多选态却一个
                # 都没选中，用户还得再点一下（安卓 launcher 是长按即选中）。
                menu.addAction("选择", lambda: host.enter_selection_mode(
                    self.entry.get("id")))
                menu.addSeparator()
        if self.in_folder:
            menu.addAction("打开", self._open)
            menu.addAction("在文件夹中显示", self._reveal)
            menu.addAction("移出文件夹", self._remove_from_folder)
        elif self.is_folder:
            menu.addAction("打开", self._open_folder)
            # v14u：大文件夹 per-folder（entry["big"]）——小 → 「放大」、
            # 大 → 「缩小」；原「文件夹大小」子菜单移入弹窗空白右键
            # （内部尺寸布局）。
            if self._big:
                menu.addAction("缩小（普通文件夹）", self._toggle_big)
            else:
                menu.addAction("放大（大文件夹）", self._toggle_big)
            menu.addAction("解散文件夹", self._dissolve)
            menu.addAction("从整理桌面移除", self._remove_self)
        elif self.is_file:
            menu.addAction("打开", self._open)
            menu.addAction("在文件夹中显示", self._reveal)
            self._add_shell_verbs(menu)
            menu.addAction("从整理桌面移除", self._remove_self)
        else:
            menu.addAction("打开", self._open)
            # 反馈 12：单个图标调大小（默认 100%，滑条 50%~200%，
            # 可直接键入 >200；<50% 给警告——太小会点不到，设置页另有
            # 「已配置额外大小的图标」管理区可以一键恢复）
            scale_w = self._scale_action_widget(menu)
            if scale_w is not None:
                menu.addAction("图标大小")
                wa = QWidgetAction(menu)
                wa.setDefaultWidget(scale_w)
                menu.addAction(wa)
            menu.addAction("从整理桌面移除", self._remove_self)
        return menu

    def _scale_action_widget(self, menu):
        """「图标大小」滑条 + 数字框（QMenu 内嵌，反馈 12）。

        滑条与数字框双向同步：滑条只给 50%~200%（常用区间），数字框可以
        键入更大值（4K 屏 / 触屏用户可能真需要 250%）；低于 50% 时下方
        出现一行警告，但**不阻止**——用户要有权把自己的图标调小。
        """
        try:
            cur = int(round(float(self.entry.get("icon_scale", 1.0) or 1.0) * 100))
        except (TypeError, ValueError):
            cur = 100
        cur = max(10, min(400, cur))
        holder = QWidget(menu)
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(14, 4, 14, 6)
        lay.setSpacing(4)
        row = QWidget(holder)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        sld = QSlider(Qt.Horizontal, row)
        sld.setRange(50, 200)
        sld.setValue(max(50, min(200, cur)))
        sld.setFixedWidth(160)
        spin = Stepper(row)
        spin.setRange(10, 400)
        spin.setSuffix("%")
        spin.setValue(cur)
        spin.setFixedWidth(76)
        rl.addWidget(sld)
        rl.addWidget(spin)
        warn = QLabel("小于 50% 可能小到点不到", holder)
        warn.setStyleSheet("color: #E85858; font-size: 11px;")
        warn.setVisible(cur < 50)
        lay.addWidget(row)
        lay.addWidget(warn)

        def _emit(pct):
            self.iconScaleChanged.emit(self.entry["id"], int(pct))

        def _from_slider(v):
            if spin.value() != v:
                spin.blockSignals(True)
                spin.setValue(int(v))
                spin.blockSignals(False)
            warn.setVisible(int(v) < 50)
            _emit(v)

        def _from_spin(v):
            if sld.value() != v:
                sld.blockSignals(True)
                sld.setValue(max(50, min(200, int(v))))
                sld.blockSignals(False)
            warn.setVisible(int(v) < 50)
            _emit(v)

        sld.valueChanged.connect(_from_slider)
        spin.valueChanged.connect(_from_spin)
        self._scale_widgets = (holder, sld, spin, warn)   # 测试可定位
        return holder

    def _open(self):
        if not self.is_folder:
            self.activated.emit(self.entry.get("app", {}))

    def _reveal(self):
        self.openLocation.emit(self.entry.get("app", {}))

    def _add_shell_verbs(self, menu):
        """按文件类型追加原生右键菜单（v14j 静态动词 → v14m COM 镜像）。

        v14m：走 pywin32 IContextMenu 镜像资源管理器**同款**菜单树
        （含第三方处理器：360 压缩的解压项等），点击 InvokeCommand 与
        资源管理器同一条代码路径。zip 额外提供**自带「全部解压缩」**
        （Shell 引擎 CopyHere，不受 ProgID 接管影响——第三方处理器的
        解压项在程序化调用下可能静默无效）。COM 失败退回静态动词。
        """
        try:
            from . import appsource, shell_verbs
            target = appsource.reveal_path_for(self.entry.get("app", {}))
            if not target:
                return
            ext = os.path.splitext(target)[1].lower()
            added = False
            # ① zip：自带「全部解压缩」（保证可用，插在最前）
            if ext in shell_verbs.COMPRESSED_EXTS:
                dest_dir = os.path.dirname(target)

                def _do_extract(t=target, d=dest_dir):
                    base = os.listdir(d) if os.path.isdir(d) else []
                    ok = shell_verbs.extract_here(t, d)
                    if ok:
                        # 非阻塞盯完成（v14n：原来 UI 线程轮询最长 30s，
                        # 大压缩包会把整个界面冻住）
                        shell_verbs.watch_extract(
                            d, base,
                            lambda done: shell_verbs.notify_extract(
                                d if done else t))
                    else:
                        shell_verbs.notify_extract(t)

                menu.addAction("全部解压缩", _do_extract)
                added = True
            # ② 原生菜单镜像（COM）
            nodes, invoker = shell_verbs.native_menu(target)
            if nodes and invoker is not None:
                self._build_native_menu(menu, nodes, invoker)
                added = True
            if not added:
                # ③ 静态动词兜底（COM 不可用时的旧路线）
                verbs = shell_verbs.shell_verbs(target)
                if verbs:
                    menu.addSeparator()
                    for label, argv in verbs:
                        menu.addAction(
                            label, lambda a=list(argv):
                            shell_verbs.run_verb(a))
        except Exception:        # noqa: BLE001 —— 动词失败不影响基础菜单
            return

    def _build_native_menu(self, menu, nodes, invoker):
        """把 native_menu 的节点树镜像进 QMenu（保留分隔线与子菜单）。"""
        for node in nodes:
            if node["sep"]:
                menu.addSeparator()
                continue
            label = node["label"]
            if not label:
                continue
            if node["children"] is not None:
                sub = menu.addMenu(label)
                self._build_native_menu(sub, node["children"], invoker)
                if sub.isEmpty():
                    sub.menuAction().setVisible(False)
                continue
            if label in ("打开(O)", "打开"):
                continue          # 顶部已有「打开」，去重
            mid = node["id"]
            menu.addAction(label, lambda m=mid: invoker(m))

    def _open_folder(self):
        self.folderClicked.emit(self.entry["id"])

    def _toggle_big(self):
        """放大 / 缩小（v14u）：发 bigToggleRequested（id, 目标态）。"""
        self.bigToggleRequested.emit(self.entry["id"],
                                     not bool(self.entry.get("big")))

    def _dissolve(self):
        self.dissolveRequested.emit(self.entry["id"])

    def _remove_self(self):
        self.removeRequested.emit(self.entry["id"])

    def _remove_from_folder(self):
        self.removeFromFolder.emit(self.drag_payload)
