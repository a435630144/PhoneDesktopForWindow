# -*- coding: utf-8 -*-
"""液态玻璃文件夹弹窗 + 幕布 + 行内输入框（2026-10-18 M5 重做，指导文档 §4）。

UI 对标 iOS 文件夹 + Liquid Glass 材质语言：
  - 大网格：成员最多 4 列铺开（单行 4 列起步）
  - 标题居中（点按 → 行内重命名，BandInputBox）；vibrancy：材质上加
    字重 + 微字距
  - 液态玻璃大圆角背板 + 右上角自绘 × 关闭按钮
  - 底部工具条：「＋ 添加图标」「颜色」两个 pill 按钮
  - 背景色板：8 预设 + 「自动」（成员图标平均色，folder_tint.auto_tint）；
    图标渐变与弹窗背板共用同一颜色（iOS 文件夹语言）
  - ＋ 添加面板：桌面条目网格（排除已有成员），点选即加入；
    也支持把桌面图标直接拖到弹窗上加入（dragEnter 识别 payload）
  - 空文件夹态：「空文件夹」+ 提示「从桌面拖入或点 ＋ 添加」

层级与动效：
  - 挂桌面带（Progman 上方、应用窗口之下）：**不用
    WindowStaysOnTopHint**——那会盖住所有应用程序窗口（用户截图里
    黑弹窗压住桌面中央的根源）。作为桌面的一部分，打开应用时被盖住
    是正确行为。
  - M5：弹窗插到**桌面带内最顶**（win32_layer.band_topmost_hwnd →
    insert_above_window）——压得住整理窗口与所有小组件（旧实现只保证
    高于整理窗口，小组件会压住弹窗，指导文档 §4.1 现状证据）
  - ScrimWindow 幕布：桌面带内、弹窗下一层的全屏半透明黑
    （rgba(0,0,0,0.28)，200ms 淡入淡出）；**点击幕布 = 关闭弹窗**
    （与 DesktopWindow 空白点击关闭双保险）；幕布同时压暗图标与
    小组件（iOS 打开文件夹语义）。弹窗与幕布都纳入 BandGuard
  - NOACTIVATE：点击/拖动照常接收（NOACTIVATE 只禁激活），但永不抢焦点
  - 打开：玻璃表面从锚点图标位置"涌出"（缩放 0.62->1.0，欠阻尼弹簧
    0.8 = 液态 wobble）+ 淡入；窗口尺寸全程不变——不动 geometry 动画，
    避免 setFixedSize 钳制起点 / 子控件不随窗口缩放等一系列坑
  - 关闭：反向（缩放收 0.55 + 淡出）后才真正 close；临界阻尼、防重入
  - 关闭方式：× 按钮 / 点击幕布 / 点击桌面空白处 / 再次点击文件夹图标。
    NOACTIVATE 窗口收不到键盘焦点，故 Esc 不可用
  - refresh() 背板重抓：先 hide() → 抓屏 → show()（同帧完成，无闪烁）

液态玻璃实现说明（为什么不用 acrylic API）：
  SetWindowCompositionAttribute 的 ACCENT_ENABLE_ACRYLICBLURBEHIND 与
  Qt 的 WA_TranslucentBackground + WS_EX_LAYERED 组合冲突（acrylic 不显示）。
  改为 show() 前抓屏 -> C++ 层高斯模糊 -> paintEvent 自绘，跨 Win10/Win11
  稳定，观感完全可控。桌面场景背后基本静态，一次性抓取完全够用。
"""
import traceback

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent,
                            QParallelAnimationGroup, QPoint,
                            QPointF, QPropertyAnimation, QRect, QRectF,
                            QTimer, Qt, Signal, Property)
from PySide6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QPainter,
                           QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from . import appsource
from . import effects
from . import folder_layout
from . import grid_flow
from . import label_style
from . import liquid_glass
from . import theme
from . import win32_layer
from .folder_tint import LIQUID, auto_tint, folder_liquid, folder_tint
from .iconwidget import AppIcon


# ---------- 文件夹弹窗度量（纯函数，v7 B5 反馈 10） ----------
# 旧实现 `_member_icon_size() = max(34, icon_metric() * 0.62)`：成员图标随
# **桌面图标尺寸**线性放大，而弹窗宽有 240 下限——用户把图标调大后弹窗里
# 的成员挤满、留白被吃掉（"弹窗图标过大"）。现在完全由 cell 驱动：
POPUP_ICON_MIN, POPUP_ICON_MAX = 40, 56
POPUP_WIDTH_MAX = 520
# v10 A9（反馈 16）：成员格的**横向**额外留白（乘 chrome_scale，**左右各一份**）。
# 只加宽不加高：3×3 满页旧宽高比 0.78（竖长方形），加这个后约 0.96（接近方形）。
# 12 而不是 24：留白是双侧生效的，24 会过了头变成横长方形（1.19）。
MEMBER_PAD_X = 12


def popup_metrics(cell_w: int, n_members: int = 0, avail_h: int = 0) -> dict:
    """文件夹弹窗度量（**不读 storage**，纯函数，可单测）。

    v7 §7.1：`icon` 只由 cell 宽度决定（clamp 40~56），与桌面图标档位
    解耦；成员越多先缩 gap、再让 icon 落到下限，宽度上限 520。
    """
    cell_w = max(1, int(cell_w))
    n = max(0, int(n_members))
    icon = max(POPUP_ICON_MIN, min(POPUP_ICON_MAX, int(round(cell_w * 0.66))))
    if n <= 4:
        cols = 2
    elif n <= 9:
        cols = 3
    else:
        cols = 4
    rows = max(1, (n + cols - 1) // cols) if n else 1
    gap = 12
    box = icon + 16
    pad = 16
    title_h, bar_h = 30, 38
    width = pad * 2 + cols * box + (cols - 1) * gap
    # 先减 gap 再减 icon（顺序固定：图标可读性优先于间距）
    while width > POPUP_WIDTH_MAX and gap > 4:
        gap -= 1
        width = pad * 2 + cols * box + (cols - 1) * gap
    guard = 0
    while width > POPUP_WIDTH_MAX and icon > POPUP_ICON_MIN and guard < 40:
        icon -= 2
        box = icon + 16
        width = pad * 2 + cols * box + (cols - 1) * gap
        guard += 1
    width = max(240, min(POPUP_WIDTH_MAX, width))
    height = title_h + rows * box + (rows - 1) * gap + bar_h + pad * 2
    if avail_h:
        height = min(height, int(avail_h * 0.82))
    return {"icon": icon, "box": box, "gap": gap, "pad": pad,
            "title_h": title_h, "bar_h": bar_h, "cols": cols, "rows": rows,
            "width": width, "height": height}

ZOOM_FROM = 0.62   # 打开时玻璃初始缩放（相对最终尺寸）
ZOOM_TO = 0.55     # 关闭时玻璃收起的缩放
SCRIM_ALPHA = 71   # 幕布黑透明度（0.28 × 255 ≈ 71）
TINT_OVERLAY_ALPHA = 46   # 弹窗背板上的色板色覆盖层透明度

BESIDE_GAP = 10     # 弹窗与锚点图标之间的间距（px）
BESIDE_MARGIN = 8   # 弹窗与可用屏幕边缘的最小间距（px）


def place_beside(anchor, w: int, h: int, avail,
                 gap: int = BESIDE_GAP, margin: int = BESIDE_MARGIN):
    """弹窗「贴着锚点图标展开」的位置（纯函数，返回 (x, y)）。

    2026-10-20 用户反馈 F4：文件夹展开后弹窗跑到网格区中心、离文件夹很远。
    原决策（v6 §6.2「居中当模态面板」）被本次反馈覆盖——改为贴着图标展开：

      1. **下方优先**：y = 图标底 + gap（最符合"从图标里长出来"的直觉）
      2. 下方越出可用屏幕 -> **上方**：y = 图标顶 - gap - h
      3. 上下都放不下 -> 夹紧进可用屏幕（留 margin）
      4. x 水平**居中对齐图标**，再夹紧进屏幕

    anchor / avail 均为 (x, y, w, h)：anchor 是图标全局矩形，avail 是
    可用屏幕矩形（已扣任务栏）。
    """
    ax, ay, aw, ah = [int(v) for v in anchor]
    vx, vy, vw, vh = [int(v) for v in avail]
    w, h = max(1, int(w)), max(1, int(h))

    below = ay + ah + gap
    above = ay - gap - h
    if below + h <= vy + vh:
        y = below
    elif above >= vy:
        y = above
    else:
        y = below            # 上下都放不下：交给下面的夹紧
    lo_y, hi_y = vy + margin, vy + vh - h - margin
    y = lo_y if hi_y < lo_y else max(lo_y, min(y, hi_y))

    x = ax + aw // 2 - w // 2
    lo_x, hi_x = vx + margin, vx + vw - w - margin
    x = lo_x if hi_x < lo_x else max(lo_x, min(x, hi_x))
    return int(x), int(y)


class ScrimWindow(QWidget):
    """文件夹打开时的全屏幕布：压暗桌面带内所有元素（iOS 打开文件夹语义）。

    点击幕布 = 关闭弹窗（双保险之一；DesktopWindow 空白点击关闭是另一重）。
    挂桌面带（NOACTIVATE，不抢焦点；不置顶，不盖应用窗口）。

    2026-10-19 用户反馈修复：幕布必须**转发拖放**。它铺满整个桌面带且
    层级高于整理窗口——从弹窗里把成员往外拖，光标一离开弹窗就落在幕布
    上；旧实现没有 dragEnter/dropEvent，合成 drop 被静默吞掉，
    `_on_extract` 永不执行（用户："拖拽出来后不显示"）。
    **2026-10-21 反馈 6 补 dragMove 转发**：只转发 drop 不够——拖动
    过程中桌面侧完全没有预览（红框 / 让位），用户看不到"这会落到哪、
    会不会撞到东西"。幕布是全屏最顶窗口，合成 dragMove 也只会送到它
    这里，所以必须一并转出去。
    """
    clicked = Signal()
    dropReceived = Signal(object, QPoint)   # (QMimeData, 全局落点)
    dragMoved = Signal(object, QPoint)      # (QMimeData, 全局落点) 反馈 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(QGuiApplication.primaryScreen().availableGeometry())
        self.setAcceptDrops(True)
        self._closing = False
        self._anims = []

    # ---------- 拖放转发 ----------
    def _accepts(self, md) -> bool:
        from .iconwidget import MIME_TYPE
        return bool(md is not None
                    and (md.hasFormat(MIME_TYPE) or md.hasUrls()))

    def dragEnterEvent(self, e):
        if self._accepts(e.mimeData()):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if self._accepts(e.mimeData()):
            e.acceptProposedAction()
            # 反馈 6：把"正在拖过"也转给桌面窗口（落点预览的唯一来源）
            self.dragMoved.emit(e.mimeData(),
                                self.mapToGlobal(e.position().toPoint()))

    def dropEvent(self, e):
        if not self._accepts(e.mimeData()):
            return
        self.dropReceived.emit(e.mimeData(),
                               self.mapToGlobal(e.position().toPoint()))
        e.acceptProposedAction()

    # ---------- 淡入淡出 ----------
    def fade_in(self):
        self.setWindowOpacity(0.0)
        self._fade(0.0, 1.0, 200)

    def fade_out_then_close(self):
        if self._closing:
            return
        self._closing = True
        self._fade(float(self.windowOpacity()), 0.0, 200,
                   QEasingCurve.InCubic, done=self._really_close)

    def _fade(self, start, end, ms, easing=None, done=None):
        for a in list(self._anims):
            a.stop()
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(effects.dur(ms))
        anim.setStartValue(float(start))
        anim.setEndValue(float(end))
        anim.setEasingCurve(easing if easing is not None
                            else QEasingCurve.OutCubic)
        if done is not None:
            anim.finished.connect(done)
        anim.finished.connect(lambda: self._anims.remove(anim)
                              if anim in self._anims else None)
        self._anims.append(anim)
        anim.start()
        return anim

    def _really_close(self):
        try:
            win32_layer.unregister_band_window(int(self.winId()))
        except Exception:
            pass
        super().close()

    # ---------- 交互 ----------
    def mousePressEvent(self, e):
        self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, SCRIM_ALPHA))


class BandInputBox(QWidget):
    """行内单行输入框（M5：重命名 / 添加面板搜索共用）。

    NOACTIVATE 窗口收不到键盘焦点，行内编辑必须由这个**可激活**的
    小型 frameless 窗口承担（与便签 interactive 组件同决策：要键盘
    就不能 NOACTIVATE，见 DEVLOG 6.3）。Enter / 失焦提交，Esc 取消；
    空白输入视为取消（保持原值）。挂桌面带、插到调用方窗口之上。
    """
    committed = Signal(str)
    cancelled = Signal()

    def __init__(self, text="", placeholder="", title="", parent=None):
        super().__init__(parent)
        self.title = title
        self._done = False          # 提交/取消只允许发生一次
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(240, 36)
        self.line = QLineEdit(self)
        self.line.setGeometry(0, 0, 240, 36)
        self.line.setText(text or "")
        self.line.setPlaceholderText(placeholder or title or "")
        self.line.setFrame(False)
        self.line.setToolTip(title)
        self.line.setStyleSheet(
            "QLineEdit { background: %s; color: %s; border: 1px solid %s;"
            " border-radius: 10px; padding: 0 12px;"
            " font-family: %s; font-size: 14px; }" % (
                theme.rgba(theme.SURFACE_POPUP), theme.rgba(theme.TEXT),
                theme.rgba(theme.ACCENT),
                ", ".join('"%s"' % f for f in theme.FONT_FAMILIES)))
        self.line.returnPressed.connect(self._commit)
        self.line.textChanged.connect(lambda: self._resize_to_text())

    # ---------- 几何 ----------
    def show_at(self, global_center):
        """在给定全局点居中显示并抢焦点（全选文字，直接覆盖输入）。"""
        self.move(int(global_center.x() - self.width() / 2),
                  int(global_center.y() - self.height() / 2))
        self.show()
        self.line.setFocus()
        self.line.selectAll()
        # 2026-10-20 反馈 6：桌面带里的窗口大多是 NOACTIVATE，点到别处
        # 焦点**不会转移**，focusOutEvent 永远不来 -> 用户以为"必须按 Enter"。
        # 应用级事件过滤按"点在哪"判定，比依赖焦点可靠。
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def attach_above(self, other_window):
        """挂桌面带并插到 other_window 之上（弹窗内浮现的输入框）。"""
        try:
            if not win32_layer.attach_to_desktop(self):
                return False
            if other_window is not None:
                return win32_layer.insert_above_window(
                    int(self.winId()), int(other_window.winId()))
        except Exception:
            pass
        return False

    def _resize_to_text(self):
        pass  # 固定宽度即可；长名滚动

    # ---------- 提交 / 取消 ----------
    def _commit(self):
        if getattr(self, "_done", False):
            return                       # 焦点丢失与点击可能各来一次，只提交一次
        self._done = True
        t = self.line.text().strip()
        if t:
            self.committed.emit(t)
        else:
            self.cancelled.emit()
        self.close()

    def _cancel(self):
        if getattr(self, "_done", False):
            return
        self._done = True
        self.cancelled.emit()
        self.close()

    def eventFilter(self, obj, ev):
        """点到框外即提交（比 focusOutEvent 可靠，见 show_at 注释）。"""
        if ev.type() == QEvent.MouseButtonPress and not self._done:
            try:
                inside = self.frameGeometry().contains(QCursor.pos())
            except Exception:
                inside = True
            if not inside:
                self._commit()
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._cancel()
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e):
        # 焦点真转移时也提交（指导文档 §4.2）
        self._commit()
        super().focusOutEvent(e)

    def close(self):
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        try:
            win32_layer.unregister_band_window(int(self.winId()))
        except Exception:
            pass
        super().close()


class FolderPopup(QWidget):
    launchApp = Signal(dict)
    extractRequested = Signal(str)   # payload "M:fid:idx"（M: 标记与含冒号的条目 id 消歧）
    removeMember = Signal(str)       # payload "M:fid:idx"
    memberAdded = Signal(str)        # M5：桌面图标加入文件夹（entry id）
    renamed = Signal()               # M5：重命名提交（源图标刷新标签/tooltip）
    tintChanged = Signal()           # M5：色板换色（源图标刷新渐变）
    closed = Signal()

    # 色板解析与自动色共用（AppIcon._paint_folder 用同一份实现）
    auto_tint = staticmethod(auto_tint)
    folder_tint = staticmethod(folder_tint)

    def __init__(self, storage, entry, anchor_rect=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.entry = entry
        # v14 反馈 3：成员标签样式（与桌面图标同一份 settings，热更新见
        # `apply_label_style`）。构造时取一次——_build 里的成员图标用它。
        self._label_style = label_style.label_style(storage)
        self._dragging = False
        self._closing = False
        self._backdrop = QPixmap()
        # v14 反馈 10：液态玻璃背景改为**实时**折射（按弹窗当前全局矩形
        # 从壁纸快照裁），开合动画期间只缩放这一张贴图。
        self._lens = liquid_glass.LiveLens()
        self._wall_timer = QTimer(self)
        self._wall_timer.setInterval(1200)
        self._wall_timer.timeout.connect(self._check_wallpaper)
        self._wall_timer.start()
        # v14 反馈 9 追加：悬停展开的自动关闭。鼠标既不在文件夹图标上、
        # 也不在弹窗上 -> 连丢 3 拍（~750ms）就收起。点击打开的弹窗不启用。
        self._opened_by_hover = False
        self._auto_close_miss = 0
        self._auto_close = QTimer(self)
        self._auto_close.setInterval(250)
        self._auto_close.timeout.connect(self._auto_close_tick)
        self._zoom = 1.0             # 玻璃表面缩放（开合动效驱动）
        self._zoom_center = QPointF(0.5, 0.5)  # 缩放中心（弹窗本地坐标）
        self._anchor_rect = anchor_rect  # 锚点图标全局矩形（动画起点参照）
        # M5 交互态
        self._empty = False          # 空文件夹态
        self._empty_hint = ""        # 空态提示文案
        # 2026-10-20 用户反馈 1：「样式」与「快速添加」不再画在弹窗里，
        # 各自由一个**独立表单**承载（folder_forms.py）。原因见该模块
        # docstring：内联浮层挤在按成员数算出的宽度里必然排版堆叠；换色
        # 要弹窗长高 → _build 重建成员 → 全部重放一次出场动画。
        self._tint_form = None
        self._add_form = None
        self._editor = None          # 行内输入框（重命名）
        self._page = 0               # 当前页（反馈 3：超过容量左右分页）
        self._swipe_x = None         # 横向拖拽起点（翻页手势用）
        self._swipe_y = None
        self._close_hover = False
        self._title_hover = False
        self._bar_hover = {}         # pill key -> bool
        # 桌面带成员：不置顶（不盖应用窗口）、不激活（不抢焦点）。
        # 挂载由 DesktopWindow.open_folder 在 show() 之后完成。
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self._build(anchor_rect)
        self._open_animate()

    # ---------- 布局 ----------
    def _metrics(self):
        """DPI × 用户缩放感知的布局度量（成员与桌面网格同大小）。"""
        icon_size = max(56, self.storage.icon_metric())
        m = self.storage.ui_scale()
        pad = int(round(16 * m))
        title_h = int(round(36 * m))
        # 反馈 8：底部工具条取消——「样式」「快速添加」都移到右上角按钮组，
        # 弹窗底边直接贴成员格（iOS 文件夹没有底部工具条）。
        bar_h = 0
        return icon_size, m, pad, title_h, bar_h

    def _members_rect(self):
        """成员网格区域（标题之下、页点之上，左右等边距）。"""
        _s, m, pad, title_h, _bar = self._metrics()
        top = title_h
        return QRect(pad, top, max(1, self.width() - pad * 2),
                     max(1, self.height() - top - self._pager_h() - pad))

    def _pager_h(self):
        """页点占位高度（只有一页时为 0，不占地方也不画）。"""
        if self._page_total() <= 1:
            return 0
        m = self.storage.ui_scale()
        return max(12, int(round(18 * m)))

    def _page_total(self) -> int:
        """总页数（反馈 3：超出容量左右分页，与桌面 page_count 同语义）。"""
        n = len(self.entry.get("apps") or [])
        return folder_layout.page_count(n, self.storage.folder_frame(self.entry))

    def _pager_rect(self):
        """页点热区（整宽一条，命中即翻页；只有一页时返回空矩形）。"""
        if self._page_total() <= 1:
            return QRect()
        _s, m, pad, title_h, _bar = self._metrics()
        top = self.height() - self._pager_h() - pad // 2
        return QRect(0, top, self.width(), self._pager_h())

    def _pager_dots(self):
        """页点矩形列表 [(rect, page), ...]（绘制与点击共用，避免两处算法分叉）。"""
        total = self._page_total()
        if total <= 1:
            return []
        m = self.storage.ui_scale()
        d = max(5, int(round(6 * m)))
        gap = max(6, int(round(9 * m)))
        band = self._pager_rect()
        total_w = total * d + (total - 1) * gap
        x0 = band.center().x() - total_w // 2
        y0 = band.center().y() - d // 2
        return [(QRect(x0 + i * (d + gap), y0, d, d), i) for i in range(total)]

    def _member_icon_size(self):
        """文件夹内部图标边长：**只由网格 cell 决定**（v7 B5 反馈 10）。

        旧实现读 `icon_metric() * 0.62`——桌面图标档位一调，弹窗成员跟着
        无限放大，而弹窗宽度有下限，比例当场失衡。现在走纯函数
        `popup_metrics()`：clamp(cell_w * 0.66, 40, 56)，与图标档位解耦。
        """
        try:
            cell_w = grid_flow.cell_size(self.storage)[0]
        except Exception:
            cell_w = 83
        return popup_metrics(cell_w, len(self.entry.get("apps", [])))["icon"]

    def _member_label_h(self):
        """成员名的单行标签高度（与 AppIcon 紧凑格同一公式）。"""
        m = self.storage.ui_scale()
        font_px = max(10, int(round(11 * m)))
        return max(12, int(round(font_px * 1.45)))

    def _member_pad_x(self):
        """成员格**横向**内边距（v10 A9 / 反馈 16）：比纵向多一块。

        弹窗旧实现成员格是方的（宽=高），3×3 满页宽高比只有 0.78，用户原话
        "展开是长方形，太窄了"。横向多留 `MEMBER_PAD_X`，高度不动——
        标题条与页码条本来就占高，再压就把成员挤扁了。
        **这个值必须与 AppIcon 紧凑格的 pad_x 同一个来源**，否则布局数学按
        宽格算、控件按窄格画，内容块立刻不再居中（P7/P9c 会红）。
        """
        m = self.storage.ui_scale()
        return max(2, int(round(4 * m))) + int(round(MEMBER_PAD_X * m))

    def _member_box(self):
        """单个成员占位方块 (宽, 高)：图标 + 内边距 + 单行文件名（反馈 7）。"""
        m = self.storage.ui_scale()
        pad = max(2, int(round(4 * m)))
        icon = self._member_icon_size()
        return (icon + self._member_pad_x() * 2,
                icon + pad * 2 + self._member_label_h())

    def _grid_shape(self, n: int):
        """按文件夹尺寸上限 + 实际成员数算 (列, 行)（folder_layout 纯函数）。

        反馈 14：默认上限 3×3，最小 2×2，按数量收缩、优先排满一行。
        """
        return folder_layout.grid_shape(n, self.storage.folder_frame(self.entry))

    def _member_cols(self, n: int) -> int:
        """成员列数（= 当前上限内能铺满一行的列数）。"""
        return self._grid_shape(n)[0]

    def _member_pitch(self):
        """成员格间距 (横向, 纵向)：紧凑，只留一个小间隙。"""
        m = self.storage.ui_scale()
        bw, bh = self._member_box()
        gap = max(4, int(round(10 * m)))
        return bw + gap, bh + gap

    def _title_rect(self):
        _s, m, pad, title_h, _b = self._metrics()
        # 标题左边界留出按钮组：标题不能画到右上角三个按钮下面
        return QRect(pad, 6, max(1, self.width() - pad * 2 - 86), title_h - 8)

    # ---------- 右上角按钮组（反馈 8：○ = 样式，＋ = 快速添加） ----------
    def _header_btn_size(self):
        return max(20, int(round(22 * self.storage.ui_scale())))

    def _header_rects(self):
        """右上角按钮矩形：{"close", "add", "style"}（从右往左排列）。"""
        m = self.storage.ui_scale()
        s = self._header_btn_size()
        gap = max(4, int(round(6 * m)))
        top = max(6, int(round(10 * m)))
        right = self.width() - max(6, int(round(10 * m)))
        out = {}
        for key in ("close", "add", "style"):
            out[key] = QRect(right - s, top, s, s)
            right -= s + gap
        return out

    def _close_rect(self):
        return self._header_rects()["close"]

    def _add_btn_rect(self):
        return self._header_rects()["add"]

    def _style_btn_rect(self):
        return self._header_rects()["style"]

    def _bar_rects(self):
        """底部工具条已取消（反馈 8）；保留空实现供旧调用探测。"""
        return []

    def _build(self, anchor_rect, keep_backdrop=False, keep_pos=False,
               page=None, slide=0):
        """重排弹窗（成员格 / 尺寸 / 定位）。

        keep_pos=True：色板开合这类"只改高度"的重排——位置保持不动，
        否则会按 anchor=None 回退到屏幕居中，弹窗会从图标旁边跳走。

        page：显式翻页（反馈 3）。只切成员不挪窗（尺寸已封顶在上限，
        翻页不改变弹窗大小），slide=±1 时新页成员从对应侧滑入。
        """
        for child in self.findChildren(AppIcon):
            child.deleteLater()
        # 成员部件全部销毁：飞行中的成员拖拽已死亡，标志位一并复位。
        # 否则 _dragging 卡死会让 focusOutEvent 再也无法自动收起弹窗
        # （拖源被删 -> dragFinished 送不达 -> _on_drag_finish 不执行）。
        self._dragging = False
        self._swipe_x = None
        icon_size, m, pad, title_h, bar_h = self._metrics()
        members = self.entry.get("apps", []) or []
        n = len(members)
        self._empty = not members
        self._empty_hint = ("空文件夹\n点右上角 ＋ 从桌面添加"
                            if self._empty else "")
        # 反馈 3：超过容量不再加行，改左右分页（参考桌面分页语义）
        frame = self.storage.folder_frame(self.entry)
        if page is None:
            self._page = folder_layout.clamp_page(self._page, n, frame)
        else:
            self._page = folder_layout.clamp_page(page, n, frame)
        page_members, offset = folder_layout.page_items(members, self._page, frame)
        # 反馈 14：尺寸 = 上限 × 成员数（2 个 -> 2×2，3 个 -> 3×2，默认 3×3）
        cols, rows = self._grid_shape(n)
        bw, bh = self._member_box()
        px, py = self._member_pitch()
        gap_x, gap_y = px - bw, py - bh
        content_w = cols * bw + (cols - 1) * gap_x
        content_h = rows * bh + (rows - 1) * gap_y
        w = pad + content_w
        h = title_h + content_h + self._pager_h() + pad
        # 高度不超过可用屏幕的 82%：桌面文件全拖进来的极端情况不顶出屏幕
        # 2026-10-20（用户反馈 F4，覆盖 v6 §6.2 的居中决策）：**贴着源图标
        # 展开**——下方优先、下方放不下翻上方、都放不下夹紧进屏幕。旧实现
        # 居中到网格区中心，用户看到的是"弹窗离文件夹很远"。
        # 无 anchor（探针 / 异常路径）时仍回退网格区居中，保持旧行为兜底。
        cx, cy = grid_flow.page_grid_center(self.storage)
        probe = anchor_rect.center() if anchor_rect is not None \
            else QPoint(int(cx), int(cy))
        screen = QGuiApplication.screenAt(probe) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        max_h = int(geo.height() * 0.82)
        self.setFixedSize(max(240, w), min(max(150, h), max_h))
        if keep_pos:
            x, y = self.x(), self.y()
            x = max(geo.left() + 8, min(x, geo.right() - self.width() - 8))
            y = max(geo.top() + 8, min(y, geo.bottom() - self.height() - 8))
        elif anchor_rect is not None:
            x, y = place_beside(
                (anchor_rect.x(), anchor_rect.y(),
                 anchor_rect.width(), anchor_rect.height()),
                self.width(), self.height(),
                (geo.x(), geo.y(), geo.width(), geo.height()))
        else:
            x = cx - self.width() // 2
            y = cy - self.height() // 2
            x = max(geo.left() + 8, min(x, geo.right() - self.width() - 8))
            y = max(geo.top() + 8, min(y, geo.bottom() - self.height() - 8))
        self.move(x, y)
        if anchor_rect is not None and not keep_pos:
            # 玻璃从图标位置涌出：缩放中心 = 图标中心（弹窗本地坐标）
            self._zoom_center = self.mapFromGlobal(anchor_rect.center())
        elif keep_pos:
            pass      # 原地改高度：缩放中心保持原值
        else:
            self._zoom_center = QPointF(self.width() / 2, self.height() / 2)
        if not keep_backdrop:
            # 玻璃背板：必须在 show() 之前抓屏，否则会把自己抓进去。
            # 液态玻璃（文件夹颜色 = 液态）走实时壁纸快照，不需要抓屏；
            # 选颜色的文件夹仍用 8px 重霜的传统玻璃。
            rect = QRect(self.x(), self.y(), self.width(), self.height())
            if self._liquid_on():
                self._backdrop = QPixmap()
                self._lens.reset()
            else:
                self._backdrop = effects.glass_backdrop(rect)
        top = title_h
        # 反馈 4：**居中平均排布**。两层居中——① 内容块在窗口里水平居中
        # （窗口有 240px 下限，窄内容（2 个成员）时旧实现从 x=pad/2 起排，
        #  右侧空一大块，用户看到"布局偏左"）；② 每一行单独居中，末行
        # 不足一排时不再齐左，而是排在当行中间。
        origin_x = max(0, (self.width() - content_w) // 2)
        for i, app in enumerate(page_members):
            # payload 用**全局下标**（offset + i）：三处消费（拖出 / 并入 /
            # 右键移出）都直接 `folder["apps"][idx]`，用页内下标会取错成员
            member_payload = "M:%s:%d" % (self.entry.get("id"), offset + i)
            entry = {"id": member_payload, "type": "app", "app": app}
            icon = AppIcon(entry, self._member_icon_size(), in_folder=True,
                           parent=self, chrome_scale=m, compact=True,
                           show_label=True,   # 反馈 7：成员必须有文件名
                           pad_x=self._member_pad_x(),   # v10 A9：横向加宽
                           label_style=self._label_style)   # v14 反馈 3
            icon.drag_payload = member_payload
            icon.dragStarted.connect(self._on_drag_start)
            icon.dragFinished.connect(self._on_drag_finish)
            icon.activated.connect(self.launchApp.emit)
            icon.removeFromFolder.connect(self.removeMember.emit)
            r, c = divmod(i, cols)
            row_n = min(cols, len(page_members) - r * cols)
            row_w = row_n * bw + (row_n - 1) * gap_x
            mx = origin_x + (content_w - row_w) // 2 + c * (bw + gap_x)
            my = top + r * (bh + gap_y)
            icon.move(mx, my)
            if slide:
                # 翻页手势：新页成员从对应侧滑入（ slide=+1 下一页从右进）
                icon.move(mx + slide * self.width(), my)
                icon.animate_to(mx, my, duration=300, damping=1.0)
                icon.show()
            else:
                # 弹窗内图标错峰淡入（玻璃涌出后依次浮现）
                icon.appear_in(120 + i * 45)
        # 2026-10-19 用户反馈"文件夹样式有时候出问题"：稳态必须铺满整个
        # 窗口。开合动画中断 / 被 refresh 打断时 `_zoom` 可能停在中间值，
        # 而 paintEvent 只按 zoom 缩放后的 rect 画玻璃——结果是"图标和工具
        # 栏都在、面板却只有中间一小块"。这里在没有动画在跑时强制归位，
        # 并把整窗刷一次（几何变了，旧脏区不足以覆盖新尺寸）。
        anim = getattr(self, "_open_anim", None)
        try:
            running = anim is not None and anim.state() == QAbstractAnimation.Running
        except RuntimeError:
            running = False
        if not running:
            self._zoom = 1.0
        # 表单贴着弹窗：弹窗因成员增减改了大小，表单位置要跟着走
        for attr in ("_tint_form", "_add_form"):
            form = getattr(self, attr, None)
            if form is not None and form.isVisible():
                form.show_beside(self)
        self.update()

    # ---------- 翻页（反馈 3：超容量左右分页） ----------
    def _set_page(self, page: int):
        """翻到指定页（夹紧 + 同页短路）；尺寸不变，只切成员 + 滑入动画。"""
        total = self._page_total()
        target = max(0, min(int(page), total - 1))
        if target == self._page:
            return
        slide = 1 if target > self._page else -1
        self._build(None, keep_backdrop=True, keep_pos=True,
                    page=target, slide=slide)

    def _page_step(self, delta: int):
        """相对翻页（滚轮 / 滑适用）。"""
        self._set_page(self._page + delta)

    def wheelEvent(self, e):
        """滚轮翻页（与桌面整理模式同一阈值 15，见 desktop_window.wheelEvent）。"""
        if self._page_total() <= 1:
            e.ignore()
            return
        dy = e.angleDelta().y()
        dx = e.angleDelta().x()
        delta = dy if abs(dy) >= abs(dx) else dx
        if abs(delta) >= 15:
            self._page_step(-1 if delta < 0 else 1)
            e.accept()
            return
        e.ignore()

    # ---------- 开合动效 ----------
    def _open_animate(self):
        """液态涌现：背板从图标位置缩放着陆 + 淡入。

        曲线按苹果 Fluid Interfaces 规范取值（真弹簧，见 effects.spring_curve）：
          - sheet 档弹窗 = 欠阻尼弹簧（damping 0.8，一次极小衰减过冲
            ≈ Motion bounce 0.2），duration 380ms 为稳定时间——可见
            运动集中在前期，尾部 30% 是不可见的收敛尾（iOS 弹簧语义）
          - 过冲刻意保持 ~0.7%：玻璃表面缩放 >1 会被窗口边界裁切圆角，
            wobble 幅度以不露裁切痕迹为上限（图标无此约束，可用 0.62）
          - 系统关闭动画效果时（reduced-motion 等价）dur() 自动压减时长
        """
        self.setWindowOpacity(0.0)
        self._zoom = ZOOM_FROM
        group = QParallelAnimationGroup(self)
        opa = QPropertyAnimation(self, b"windowOpacity", self)
        opa.setDuration(effects.dur(180))
        opa.setStartValue(0.0)
        opa.setEndValue(1.0)
        opa.setEasingCurve(QEasingCurve.OutCubic)
        zoom = QPropertyAnimation(self, b"zoom", self)
        zoom.setDuration(effects.dur(380))
        zoom.setStartValue(float(ZOOM_FROM))
        zoom.setEndValue(1.0)
        zoom.setEasingCurve(effects.spring_curve(0.8, 380))
        group.addAnimation(opa)
        group.addAnimation(zoom)
        group.start()
        self._open_anim = group  # 持有引用，避免被 GC
        self.show()

    def close(self):
        """反向收起（缩放 + 淡出）后才真正关闭；动画期间防重入。
        进出场同路径：都绕锚点图标中心收放；收起用临界阻尼（关场不带
        弹性——苹果规范里只有带动量的开场才弹）。"""
        if self._closing:
            return
        self._closing = True
        if self._editor is not None:
            self._editor.close()
            self._editor = None
        group = QParallelAnimationGroup(self)
        opa = QPropertyAnimation(self, b"windowOpacity", self)
        opa.setDuration(effects.dur(180))
        opa.setStartValue(float(self.windowOpacity()))
        opa.setEndValue(0.0)
        opa.setEasingCurve(QEasingCurve.InCubic)
        zoom = QPropertyAnimation(self, b"zoom", self)
        zoom.setDuration(effects.dur(200))
        zoom.setStartValue(float(self._zoom))
        zoom.setEndValue(float(ZOOM_TO))
        zoom.setEasingCurve(effects.spring_curve(1.0, 200))
        group.finished.connect(self._really_close)
        group.start()
        self._close_anim = group  # 持有引用，避免被 GC

    def _really_close(self):
        try:
            win32_layer.unregister_band_window(int(self.winId()))
        except Exception:
            pass
        super().close()

    def refresh(self):
        """成员变化后刷新；文件夹条目已消失（解散）则自动关闭。

        M5：背板重抓——先 hide() → 抓屏 → show()（同帧完成，无闪烁，
        指导文档 §4.2 保留项 + §6 风险表）。空文件夹不再自动关闭：
        显示空态（「空文件夹」+ 提示），由用户主动关。
        """
        found = self.storage.find_entry(self.entry.get("id"))
        if not found or found.get("type") != "folder":
            self.close()
            return
        self.entry = found
        # v6 §6.2：_build 现在会按网格区中心重新定位——必须先 build
        # （移动 + 重建成员）再抓背板，否则背板抓在旧位置（窗口已移走）。
        self._build(None, keep_backdrop=True)
        self._regrab_backdrop()

    def apply_label_style(self, style):
        """v14 反馈 3：设置里改了文字样式——已开着的弹窗热更新成员标签。

        只换样式对象 + update()，不 _build（那会重放全部出场动画，
        用户改个颜色不该看一遍"文件夹重新打开"）。
        """
        self._label_style = style
        for icon in self.findChildren(AppIcon):
            try:
                icon.set_label_style(style)
            except (RuntimeError, AttributeError):
                pass
        self.update()

    # ---------- 液态玻璃背景（v14 反馈 10：实时折射） ----------
    def _liquid_on(self) -> bool:
        """这个文件夹的背景是不是液态玻璃（配色 = 液态，且总闸没关）。"""
        return (folder_liquid(self.entry)
                and liquid_glass.surface_enabled(self.storage, "folder"))

    def _lens_pixmap(self) -> QPixmap:
        """整窗矩形的折射贴图（按当前位置实时裁，缓存）。"""
        lens = liquid_glass.lens_style(self.storage)
        return self._lens.pixmap(self.geometry(), float(theme.GLASS_RADIUS),
                                 lens,
                                 float(self.devicePixelRatioF() or 1.0))

    def _check_wallpaper(self) -> None:
        """低频轮询：换壁纸 / 改分辨率后重画（弹窗开着时也跟）。"""
        if not self.isVisible() or not self._liquid_on():
            return
        lens = liquid_glass.lens_style(self.storage)
        if self._lens.stale(self.geometry(), float(theme.GLASS_RADIUS), lens):
            self.update()

    def _paint_liquid_surface(self, p, rect, z) -> bool:
        """液态玻璃表面：透明底 + 边缘折射。返回 False = 交给普通玻璃兜底。"""
        if not self._liquid_on():
            return False
        pm = self._lens_pixmap()
        if pm.isNull():
            return False
        return liquid_glass.draw_refracted(
            p, rect, theme.GLASS_RADIUS * z, pm)

    # ---------- 悬停展开的自动关闭（v14 反馈 9 追加） ----------
    def set_opened_by_hover(self, on: bool):
        """标记这次展开是否由"悬停"触发——只有悬停展开才自动关。"""
        self._opened_by_hover = bool(on)
        self._auto_close_miss = 0
        if self._opened_by_hover:
            self._auto_close.start()
        else:
            self._auto_close.stop()

    def _pointer_inside(self) -> bool:
        """鼠标是否还在"文件夹图标"或"弹窗自己"上。

        用全局光标坐标做命中（`underMouse()` 依赖 enter/leave 事件，分层
        窗口上会丢事件，不可靠）。锚点矩形不存在时只认弹窗自身。
        """
        pos = QCursor.pos()
        if self.frameGeometry().contains(pos):
            return True
        anchor = self._anchor_rect
        if anchor is not None and anchor.isValid() and anchor.contains(pos):
            return True
        return False

    def _auto_close_tick(self):
        """连丢 3 拍（~750ms）才关：鼠标短暂掠过的抖动不该把弹窗关掉。"""
        if not self._opened_by_hover or self._closing:
            self._auto_close.stop()
            return
        if self._dragging or self._pointer_inside():
            self._auto_close_miss = 0
            return
        self._auto_close_miss += 1
        if self._auto_close_miss >= 3:
            self._auto_close.stop()
            self.close()

    def _regrab_backdrop(self):
        """背板重抓：隐藏本窗 → 抓屏 → 同帧显示回（避免把自己抓进去）。

        重新 show 会改变 z-order（Windows 把刚显示的窗口提到同层前部），
        必须补一次带内最顶挂载，否则下一次鼠标移动时点到的又是底下的
        桌面窗口 —— 这是"移动鼠标就丢焦点"的另一半病根。
        """
        self.hide()
        rect = QRect(self.x(), self.y(), self.width(), self.height())
        if self._liquid_on():
            # 液态玻璃按新位置实时裁壁纸，不需要抓屏
            self._backdrop = QPixmap()
            self._lens.reset()
        else:
            self._backdrop = effects.glass_backdrop(rect)
        self.show()
        self.attach_above_band()

    def attach_above_band(self):
        """挂桌面带后插到**带内最顶**（指导文档 §4.2，决策 D5）：
        枚举桌面带成员（整理窗口 + 各小组件 + 幕布），插到当前最顶者
        之上——弹窗压得住桌面带内所有元素。必须在 attach_to_desktop
        成功之后调用。"""
        try:
            hwnd = int(self.winId())
        except Exception:
            return False
        top = win32_layer.band_topmost_hwnd(exclude_hwnd=hwnd)
        if not top:
            return False
        return win32_layer.insert_above_window(hwnd, top)

    # ---------- M5：重命名 ----------
    def _open_rename(self):
        if self._editor is not None:
            self._editor.close()
            self._editor = None
        box = BandInputBox(text=self.entry.get("name") or "文件夹",
                           title="重命名")
        box.committed.connect(self._on_renamed)
        box.cancelled.connect(self._on_editor_cancelled)
        self._editor = box
        c = self.mapToGlobal(self._title_rect().center())
        box.show_at(c)
        box.attach_above(self)

    def _on_editor_cancelled(self):
        self._editor = None

    def _on_renamed(self, name):
        self.entry["name"] = name
        self.storage.save()
        self._editor = None       # 输入框提交后自行关闭，摘除悬空引用
        self.renamed.emit()      # 源图标刷新标签 / tooltip
        self.update()

    # ---------- M5：背景色 / 快速添加（2026-10-20 反馈 1 改为独立表单） ----------
    def _set_tint(self, color):
        """写色并刷新文件夹图标。

        注意**不要**调 `refresh()`：那会走 `_build()` 销毁重建全部成员
        图标、每人重放一次 `appear_in` —— 用户看到的就是"换个颜色，文件夹
        里的图标全部重新渲染一遍"（反馈 1 的后半句）。换色只影响弹窗背板
        的 tint 覆盖层与源图标瓷砖，`update()` 重画一次即可。
        """
        if isinstance(color, str) and color.strip().lower() == LIQUID:
            # v14 反馈 10：液态玻璃是色板里的一个"颜色"，与具体颜色互斥
            self.entry["tint"] = LIQUID
            self.storage.save()
            self._lens.reset()
            self.tintChanged.emit()
            self.update()
            return
        if color is None:
            color = self.auto_tint(self.entry.get("apps", []))
            if color is None:
                return                  # 取不到成员图标：保持现色
        self.entry["tint"] = color.name()
        self.storage.save()
        self.tintChanged.emit()
        self.update()

    def _open_tint_form(self):
        """右上角 ○：独立的颜色表单（贴弹窗右侧展开）。"""
        from .folder_forms import TintForm
        if self._tint_form is not None and self._tint_form.isVisible():
            self._tint_form.hide_form()
            return
        self._close_forms()
        if folder_liquid(self.entry):
            cur = LIQUID                 # 当前就是液态玻璃
        else:
            try:
                cur = QColor(self.entry.get("tint") or "")
                if not cur.isValid():
                    cur = None
            except Exception:
                cur = None
        form = TintForm(self.storage, current=cur, parent=None)
        form.tintPicked.connect(self._set_tint)
        form.closed.connect(self._on_form_closed)
        self._tint_form = form
        form.show_beside(self)

    def _open_add_form(self):
        """右上角 ＋：独立的「添加应用」表单（贴弹窗右侧展开）。"""
        from .folder_forms import AddAppsForm
        if self._add_form is not None and self._add_form.isVisible():
            self._add_form.hide_form()
            return
        self._close_forms()
        form = AddAppsForm(self.storage, self._add_candidates())
        form.picked.connect(self._on_add_picked)
        form.closed.connect(self._on_form_closed)
        self._add_form = form
        other = self._tint_form
        form.show_beside(self, avoid=other)

    def _on_add_picked(self, entry_id):
        """表单点选一个应用：加入成功后**立刻收起表单**。

        候选列表是打开那一刻的快照——留在屏上会出现"刚加进去的应用还在
        候选里"，再点一次被唯一性守卫静默拒绝，看着像点了没反应。
        加完就收起、下次点 ＋ 重新取快照，语义单一。
        """
        ok = self._add_member_by_id(entry_id)
        form = self._add_form
        if ok and form is not None:
            form.hide_form()

    def _on_form_closed(self):
        sender = self.sender()
        for attr in ("_tint_form", "_add_form"):
            if getattr(self, attr, None) is sender:
                setattr(self, attr, None)
        self.update()

    def _close_forms(self):
        for attr in ("_tint_form", "_add_form"):
            form = getattr(self, attr, None)
            if form is not None:
                setattr(self, attr, None)
                try:
                    form.closed.disconnect()
                except Exception:
                    pass
                form.hide()
                form.deleteLater()

    def _member_keys(self):
        """本文件夹成员的身份键集合（id / target / name 三选一命中即同一元素）。"""
        keys = set()
        for m in self.entry.get("apps") or []:
            if not m:
                continue
            for k in ("id", "target", "name"):
                v = m.get(k)
                if v:
                    keys.add((k, v))
        return keys

    def _add_candidates(self):
        """可加入的候选 = **当前在桌面上的应用**（反馈 8）。

        排除三类，保证"同一元素同一时刻只能属于桌面或某一个文件夹"：
          1. 本文件夹自己；
          2. 本文件夹已有成员（按 id / target / name 三重比对——文件夹
             成员可能是早期版本写进去的、没有 id 的裸 dict）；
          3. **任何**文件夹的成员（虚拟文件夹是布局概念，成员在磁盘桌面上
             依然存在，只看本文件夹会让同一个元素出现在两个文件夹里）。
        """
        mine = self._member_keys()
        taken_ids = set(self.storage.folder_member_ids())
        taken = set()
        for e in self.storage.layout:
            if e.get("type") != "folder":
                continue
            for m in e.get("apps") or []:
                if not m:
                    continue
                for k in ("id", "target", "name"):
                    v = m.get(k)
                    if v:
                        taken.add((k, v))
        out = []
        for e in self.storage.layout:
            if e.get("type") != "app":
                continue
            app = e.get("app") or {}
            if not app:
                continue
            if e.get("id") == self.entry.get("id"):
                continue
            if e.get("id") in taken_ids:
                continue
            keys = [(k, app.get(k)) for k in ("id", "target", "name") if app.get(k)]
            if any(k in taken or k in mine for k in keys):
                continue
            out.append(e)
        return out

    def _add_member_by_id(self, entry_id):
        """把桌面条目加入文件夹（＋ 点选 / 拖入共用）。返回是否成功。

        唯一性守卫（反馈 8）：只接受"当前在桌面上的应用条目"，且该应用
        不能已经是任何文件夹的成员——否则同一个元素会同时出现在桌面和
        多个文件夹里（历史 bug，见 DEVLOG 坑 #81）。
        """
        e = self.storage.find_entry(entry_id)
        if e is None or e.get("type") != "app":
            return False
        app = e.get("app") or {}
        if not app:
            return False
        if entry_id in self.storage.folder_member_ids():
            return False
        keys = [(k, app.get(k)) for k in ("id", "target", "name") if app.get(k)]
        if any(k in self._member_keys() for k in keys):
            return False
        self.entry.setdefault("apps", []).append(app)
        self.storage.save()
        # 反馈 3：新成员追加在末尾（大概率落在新起的最后一页）——翻过去，
        # 否则用户点完 ＋ 看不见刚加的东西（页点显示有第 2 页却停在首页）
        cap = folder_layout.capacity(self.storage.folder_frame(self.entry))
        self._page = max(0, (len(self.entry["apps"]) - 1)) // max(1, cap)
        self.memberAdded.emit(entry_id)   # DesktopWindow：移出布局 + 动画
        self.refresh()
        return True

    # ---------- 拖放：桌面图标拖到弹窗上加入 ----------
    def dragEnterEvent(self, e):
        from .iconwidget import MIME_TYPE
        if e.mimeData().hasFormat(MIME_TYPE):
            e.acceptProposedAction()

    def dropEvent(self, e):
        from .iconwidget import MIME_TYPE
        data = e.mimeData().data(MIME_TYPE).data().decode()
        for eid in [p for p in data.split(",") if p]:
            if eid.startswith("M:"):
                continue                     # 成员拖出是 extract，不在这里
            if self.storage.find_entry(eid) is not None:
                self._add_member_by_id(eid)
        e.acceptProposedAction()

    def _on_drag_start(self):
        self._dragging = True

    def _on_drag_finish(self):
        self._dragging = False

    # ---------- 绘制：液态玻璃 ----------
    def _get_zoom(self):
        return self._zoom

    def _set_zoom(self, v):
        if v != self._zoom:
            self._zoom = v
            self.update()

    zoom = Property(float, _get_zoom, _set_zoom)

    def _toggle_style_panel(self):
        """右上角 ○：打开独立的颜色表单（反馈 1：不再内联、不再重排弹窗）。"""
        self._open_tint_form()

    def _toggle_add_panel(self):
        """右上角 ＋：打开独立的「添加应用」表单（候选只列当前桌面上的元素）。"""
        self._open_add_form()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.pos()
        hdr = self._header_rects()
        # 右上角三件套（反馈 8）：× 关闭 / ＋ 快速添加 / ○ 文件夹样式
        if hdr["close"].contains(pos):
            self.close()
            return
        if hdr["add"].contains(pos):
            self._toggle_add_panel()
            return
        if hdr["style"].contains(pos):
            self._toggle_style_panel()
            return
        if self._title_rect().contains(pos):
            self._open_rename()
            return
        # 反馈 3：点页点直接跳到该页
        for rect, page in self._pager_dots():
            if rect.contains(pos):
                self._set_page(page)
                return
        # 其余空白区 = 翻页手势起点（成员图标是子控件，有自己的拖拽，
        # 摸不到这里；标题/留白/页点带才是滑动手腕区）
        self._swipe_x = pos.x()
        self._swipe_y = pos.y()

    def mouseMoveEvent(self, e):
        pos = e.pos()
        self._close_hover = self._close_rect().contains(pos)
        self._title_hover = self._title_rect().contains(pos)
        for key, rect in self._header_rects().items():
            self._bar_hover[key] = rect.contains(pos)
        any_hover = (self._close_hover or self._title_hover
                     or any(self._bar_hover.values()))
        # 反馈 3：横向滑动翻页（手机语言）。阈值取成员格宽的 1/3，
        # 避免误触；触发后立刻清起点，一次按压只翻一页。
        if self._swipe_x is not None:
            dx = pos.x() - self._swipe_x
            dy = pos.y() - (self._swipe_y or pos.y())
            if abs(dx) >= 28 and abs(dx) > abs(dy):
                self._page_step(-1 if dx > 0 else 1)
                self._swipe_x = None
                any_hover = True
        self.setCursor(Qt.PointingHandCursor if any_hover else Qt.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, e):
        self._swipe_x = None
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 缩放系数夹紧到合法区间：任何中断残留的中间值都不该让面板缩水
        # （稳态 1.0 = 面板与内容区完全重合，2026-10-19 反馈）
        z = min(1.0, max(0.05, float(self._zoom)))
        # 玻璃表面：从图标位置**扩张到整个弹窗**（z<1 时是一个以图标中心
        # 为心的小矩形，周围全透明——视觉上就是玻璃从图标处涌出）。
        #
        # 2026-10-19 反馈 7a（"文件夹的背景也消失了"）根因就在这一行：
        # 旧实现 rect = (zoom_center - size*z/2, size*z)，无论 z 多大都
        # **以图标中心为中心**。图标在网格里、弹窗居中于屏幕，两者相距
        # 几百像素——于是 z=1 时玻璃刚好整块画在窗口外面，paintEvent 里
        # 只剩标题/×/成员/工具条这些定位在窗口内的控件可见：看起来就是
        # "文件夹没有背景了"。正确做法是在「图标点」与「窗口矩形」之间
        # 按 z 线性插值：z=1 恰好等于窗口矩形。
        cx, cy = self._zoom_center.x(), self._zoom_center.y()
        full_w, full_h = float(self.width()), float(self.height())
        left = cx + (0.0 - cx) * z
        top = cy + (0.0 - cy) * z
        right = cx + (full_w - cx) * z
        bottom = cy + (full_h - cy) * z
        rect = QRectF(left, top, max(0.0, right - left), max(0.0, bottom - top))
        try:
            if not self._paint_liquid_surface(p, rect, z):
                effects.paint_glass(p, rect, self._backdrop,
                                    theme.GLASS_RADIUS * z)
        except Exception:
            # shell 类应用里 paintEvent 异常会穿过多层活动 QPainter 直接
            # 杀死进程（实测 Fatal error: Aborted / SIGSEGV）。兜底：
            # 画纯色圆角背板，保证弹窗可用；同时留痕便于排查。
            traceback.print_exc()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(24, 26, 32, 235))
            p.drawRoundedRect(rect, theme.GLASS_RADIUS * z,
                              theme.GLASS_RADIUS * z)
        if z <= 0.55:
            return
        # M5：色板色覆盖层（与文件夹图标同一颜色，iOS 文件夹语言）。
        # v14 反馈 10：选「液态玻璃」时没有颜色可叠——背景就是折射出来的壁纸。
        if not self._liquid_on():
            tint = self.folder_tint(self.entry)
            overlay = QColor(tint.red(), tint.green(), tint.blue(),
                             TINT_OVERLAY_ALPHA)
            p.setPen(Qt.NoPen)
            p.setBrush(overlay)
            p.drawRoundedRect(rect, theme.GLASS_RADIUS * z,
                              theme.GLASS_RADIUS * z)
        # 标题：随玻璃浮现渐显；vibrancy 规范——材质上文字提高对比、
        # 稍加字重、微加字距（不用平灰）。iOS 文件夹标题居中。
        p.setOpacity(min(1.0, (z - 0.55) / 0.35))
        tr = self._title_rect()
        p.setPen(theme.TEXT)
        f = theme.ui_font(15, bold=True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 0.3)
        p.setFont(f)
        p.drawText(tr, Qt.AlignHCenter | Qt.AlignVCenter,
                   self.entry.get("name") or "文件夹")
        # 右上角按钮组（反馈 8）：× 关闭 / ＋ 快速添加 / ○ 文件夹样式
        hdr = self._header_rects()
        for key in ("close", "add", "style"):
            r = hdr[key]
            hovered = self._bar_hover.get(key, False)
            active = ((key == "add" and self._form_visible("_add_form"))
                      or (key == "style" and self._form_visible("_tint_form")))
            if hovered or active:
                p.setBrush(QColor(255, 255, 255, 30) if hovered
                           else QColor(theme.ACCENT.red(), theme.ACCENT.green(),
                                       theme.ACCENT.blue(), 60))
                p.setPen(Qt.NoPen)
                p.drawEllipse(r)
            cx, cy = r.center().x(), r.center().y()
            if key == "close":
                # 自绘十字：比"×"字符字形更居中精致
                p.setPen(QPen(theme.TEXT_DIM, 1.5, Qt.SolidLine, Qt.RoundCap))
                d = r.width() * 0.24
                p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
                p.drawLine(QPointF(cx + d, cy - d), QPointF(cx - d, cy + d))
            elif key == "add":
                # ＋：快速添加桌面应用
                p.setPen(QPen(theme.TEXT_DIM, 1.5, Qt.SolidLine, Qt.RoundCap))
                e1 = r.width() * 0.26
                p.drawLine(QPointF(cx - e1, cy), QPointF(cx + e1, cy))
                p.drawLine(QPointF(cx, cy - e1), QPointF(cx, cy + e1))
            else:
                # ○：文件夹样式——圆环内填当前文件夹色，一眼看出是"换色"
                tint = self.folder_tint(self.entry)
                p.setPen(QPen(theme.TEXT_DIM, 1.4))
                p.setBrush(QColor(tint.red(), tint.green(), tint.blue(), 235))
                d = r.width() * 0.30
                p.drawEllipse(QRectF(cx - d, cy - d, d * 2, d * 2))
        # 反馈 3：页点（iOS launcher 语言）。当前页实心大白点，邻页半透明，
        # 过渡页用当前文件夹色——一眼看出"还有下一页"。
        for rect, page in self._pager_dots():
            cur = (page == self._page)
            p.setPen(Qt.NoPen)
            if cur:
                p.setBrush(QColor(255, 255, 255, 235))
            elif page == self._page - 1 or page == self._page + 1:
                p.setBrush(QColor(255, 255, 255, 90))
            else:
                p.setBrush(QColor(255, 255, 255, 45))
            p.drawEllipse(rect)
        # 空态提示（成员网格区居中两行）
        if self._empty:
            p.setPen(theme.TEXT)
            f2 = theme.ui_font(14, bold=True)
            p.setFont(f2)
            area = self._members_rect()
            p.drawText(area, Qt.AlignHCenter | Qt.AlignBottom, "空文件夹")
            p.setPen(theme.TEXT_DIM)
            f3 = theme.ui_font(11)
            p.setFont(f3)
            p.drawText(area.adjusted(0, 0, 0, -26),
                       Qt.AlignHCenter | Qt.AlignBottom,
                       "从桌面拖入，或点右上角 ＋ 添加")

    def _form_visible(self, attr) -> bool:
        """对应表单当前是否展开（按钮高亮用）。"""
        form = getattr(self, attr, None)
        try:
            return form is not None and form.isVisible()
        except RuntimeError:
            return False

    _close_hover = False
    _title_hover = False

    def focusOutEvent(self, e):
        """**不自动关闭**（2026-10-19 用户反馈 7）。

        旧实现一失焦就 close()：弹窗是 NOACTIVATE 桌面带窗口，鼠标一动
        引起的激活变化（点图标 / 幕布重挂 / 小组件刷新）都会触发 FocusOut，
        于是"刚展开正常，一动鼠标就收起、背景消失、选不到里面的文件"。
        关闭路径改为显式三条：幕布点击 / 桌面空白点击 / 右上角 ×。
        """
        e.accept()

    def closeEvent(self, e):
        self._close_forms()      # 表单是独立窗口，弹窗关掉必须一起收
        self.closed.emit()
        super().closeEvent(e)
