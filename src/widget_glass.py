# -*- coding: utf-8 -*-
"""玻璃卡片基类与兜底预览（v8 C2，指导文档_v8.md §2.3）。

为什么要"静态玻璃"而不是实时高斯模糊：
    文件夹弹窗的玻璃（`effects.paint_glass`）需要先抓一块背景 pixmap 再模糊，
    对瞬态浮层没问题；但桌面组件是**常驻**的，每个组件每帧抓一次桌面 +
    高斯模糊，等于把 v7e 刚做稳的 60fps 主动推翻。桌面组件的玻璃只需要
    "看得到壁纸"这一层观感：竖向半透明渐变 + 顶部 specular 高光 + 细边。

绘制顺序与层级（由基类 `FloatingWidget.paintEvent` 保证）：
    锚定：alpha=2 命中填充 → 玻璃底 → (编辑框) → 内容
    悬浮：玻璃底 → 内容 → 关闭按钮 / 缩放手柄

`draw_preview()` 是组件库卡片的**兜底**画法（组件没提供 preview 时用）。
组件自己的预览必须实现成"同一个 draw() 换个 model"，不要另画一套。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QTimer, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,
                          QPainterPath, QPen, QPixmap)

from . import theme
from . import liquid_glass
from . import wallpaper
from . import widget_style
from .widget_base import FloatingWidget

# 统一内边距（组件内容不要自己编数字，否则四个组件四种留白）
PAD = 16
GAP = 10
TITLE_H = 22

# 预览卡的圆角比桌面略小：缩略图尺寸小，20 会显得过圆
PREVIEW_RADIUS = 14


def glass_radius(anchored: bool) -> float:
    """玻璃圆角：与主题玻璃一致（20）。

    锚定 / 悬浮用同一个值——组件会在两种形态间切换，圆角跳变会让人
    觉得"换了一个组件"；参数保留只为将来按形态微调时不必改调用点。
    """
    return float(theme.GLASS_RADIUS)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """按比例混合两色（t=0 -> a，t=1 -> b）。玻璃三段渐变靠它派生。"""
    t = max(0.0, min(1.0, float(t)))
    return QColor(int(round(a.red() + (b.red() - a.red()) * t)),
                  int(round(a.green() + (b.green() - a.green()) * t)),
                  int(round(a.blue() + (b.blue() - a.blue()) * t)))


def _tint_stops(tint_color, a: int):
    """玻璃三段渐变的三个颜色。自定义背景色时按"上浅下深"派生。"""
    if tint_color is None:
        return (QColor(22, 24, 31, a), QColor(17, 18, 24, a + 18),
                QColor(13, 14, 19, a + 38))
    base = QColor(tint_color)
    base.setAlpha(255)
    top = _mix(base, QColor(255, 255, 255), 0.08)
    bottom = _mix(base, QColor(0, 0, 0), 0.22)
    top.setAlpha(max(0, min(255, a)))
    mid = QColor(base)
    mid.setAlpha(max(0, min(255, a + 18)))
    bottom.setAlpha(max(0, min(255, a + 38)))
    return top, mid, bottom


def paint_glass(painter: QPainter, rect: QRectF, radius: float = None,
                tint_alpha: int = None, tint_color=None) -> None:
    """画一块静态玻璃（竖向渐变 + 顶部 specular + 1px 边框）。

    - `rect` 可以是任意大小（1×1 也不许抛），组件库缩略图会喂极小矩形；
    - 圆角外的像素保持透明（`fill` 用 path 裁剪），锚定组件靠这一点把
      卡片外的点击交给真实桌面；
    - `tint_color`（v9）：用户自定义背景色。None = 主题深色玻璃。
    """
    rect = QRectF(rect)
    if rect.width() <= 1 or rect.height() <= 1:
        return
    r = float(radius if radius is not None else theme.GLASS_RADIUS)
    # 半径不能超过短边一半，否则 QPainterPath 会画出畸形圆角
    r = max(1.0, min(r, rect.width() / 2.0, rect.height() / 2.0))
    a = int(tint_alpha if tint_alpha is not None else theme.GLASS_TINT_ALPHA)
    c_top, c_mid, c_bottom = _tint_stops(tint_color, a)

    path = QPainterPath()
    path.addRoundedRect(rect, r, r)

    painter.save()
    painter.setClipPath(path)
    painter.setPen(Qt.NoPen)

    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, c_top)
    grad.setColorAt(0.55, c_mid)
    grad.setColorAt(1.0, c_bottom)
    painter.setBrush(QBrush(grad))
    painter.drawRect(rect)

    # 顶部 specular：两端渐隐的白线（液态玻璃的灵魂，与文件夹弹窗同语言）
    hi = QLinearGradient(rect.topLeft(), rect.topRight())
    hi.setColorAt(0.0, QColor(255, 255, 255, 0))
    hi.setColorAt(0.5, QColor(255, 255, 255, theme.GLASS_SPECULAR))
    hi.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QBrush(hi), 1.4))
    y = rect.top() + 1.0
    painter.drawLine(QPointF(rect.left() + r * 0.55, y),
                     QPointF(rect.right() - r * 0.55, y))
    painter.restore()

    # 边框画在裁剪之外：1px 压在边缘上才不会被自己裁掉一半
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(255, 255, 255, theme.GLASS_BORDER), 1.0))
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5),
                            max(1.0, r - 0.5), max(1.0, r - 0.5))


def _elide(text: str, font: QFont, width: float) -> str:
    """单行省略（组件库卡片宽度固定，长说明不能被硬裁成半个字）。"""
    from PySide6.QtGui import QFontMetricsF
    fm = QFontMetricsF(font)
    if fm.horizontalAdvance(text) <= width:
        return text
    out = text
    while out and fm.horizontalAdvance(out + "…") > width:
        out = out[:-1]
    return (out + "…") if out else ""


def draw_preview(painter: QPainter, rect, name: str = "", summary: str = "",
                 dpr: float = 1.0) -> None:
    """组件库兜底预览：玻璃底 + 组件名 + 一句说明。

    组件自带的 `spec.preview` 缺省时用它——保证"每个组件都有预览"，
    新组件不必强制实现 preview 才能上架（渐进增强）。
    """
    rect = QRectF(rect)
    if rect.width() <= 2 or rect.height() <= 2:
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    paint_glass(painter, rect, PREVIEW_RADIUS)

    painter.setPen(QColor(theme.TEXT))
    f1 = theme.ui_font(15, bold=True)
    painter.setFont(f1)
    name_rect = QRectF(rect.left() + 18, rect.center().y() - 22,
                       rect.width() - 36, 26)
    painter.drawText(name_rect, Qt.AlignHCenter | Qt.AlignVCenter,
                     _elide(name, f1, name_rect.width()))

    painter.setPen(QColor(theme.TEXT_DIM))
    f2 = theme.ui_font(11)
    painter.setFont(f2)
    sub_rect = QRectF(rect.left() + 18, rect.center().y() + 6,
                      rect.width() - 36, 20)
    painter.drawText(sub_rect, Qt.AlignHCenter | Qt.AlignVCenter,
                     _elide(summary, f2, sub_rect.width()))
    painter.restore()


class GlassWidget(FloatingWidget):
    """玻璃质感的组件基类。

    只改"底色"，交互与几何完全继承 `FloatingWidget`：
      - 拖动 / 吸附 / 八点编辑 / 关闭按钮 / 命中填充都不变；
      - 子类实现 `paint_content(painter)` 画自己的内容；
      - 子类可覆盖 `on_settings_changed()` 响应组件设置变更（v8 C8）；
      - `self.style`（v9）给出用户配好的配色，绘制内容时不要再直接写死
        `theme.TEXT`，否则"文字颜色"设置对这些组件无效。
    """

    def __init__(self, storage, entry, interactive: bool = False):
        super().__init__(storage, entry, interactive=interactive)
        # v14 反馈 10：折射底图跟着位置/壁纸实时重算
        # v14g：重算频率走设置档位（低/中/高/自定义），provider 每次
        # 触发时重解——改档位不用重建组件，即时生效
        self._lens = liquid_glass.LiveLens()
        self._lens_throttle = liquid_glass.Throttle(
            lambda: liquid_glass.live_fps(self.storage))
        self._lens_timer = QTimer(self)
        self._lens_timer.setSingleShot(True)
        self._lens_timer.timeout.connect(self.update)
        # 壁纸轮询：1.2s 一次版本比对（读注册表有 0.4s 缓存，几乎不耗）
        self._wall_timer = QTimer(self)
        self._wall_timer.setInterval(1200)
        self._wall_timer.timeout.connect(self._check_wallpaper)
        self._wall_timer.start()

    def _style(self):
        """取（并缓存）外观样式；设置变更时 `on_settings_changed` 清缓存。"""
        st = getattr(self, "_style_cache", None)
        if st is None:
            st = widget_style.of(self.entry)
            self._style_cache = st
        return st

    @property
    def style(self):
        return self._style()

    def prepare_lens_backdrop(self):
        """预热壁纸快照（**必须在 show() 之前**调用的历史接口）。

        v14 反馈 10 起折射底图不再按组件抓屏，而是从整块壁纸快照里按位置
        裁——这里只负责把快照建出来（首帧 100~200ms，别让它落在第一次
        paintEvent 里），组件怎么移动都自动跟。
        """
        if not self._liquid_on():
            self._lens.reset()
            return
        try:
            wallpaper.snapshot()
        except Exception:          # noqa: BLE001 —— 壁纸读不到就走抓屏兜底
            pass

    def _liquid_on(self) -> bool:
        """本组件的背景是不是液态玻璃（配色开关 + 全局总闸）。"""
        try:
            on = bool(self.style.liquid)
        except Exception:          # noqa: BLE001
            on = False
        return on and liquid_glass.master_enabled(self.storage)

    def _card_global_rect(self) -> QRect:
        """卡片在屏幕上的矩形（折射要按"玻璃实际在哪"取底图）。"""
        card = self.card_rect()
        return QRect(self.mapToGlobal(card.topLeft()), card.size())

    def _lens_pixmap(self, radius: float) -> QPixmap:
        """折射贴图（缓存）：位置 / 壁纸版本 / 参数变了才重算。"""
        lens = liquid_glass.lens_style(self.storage)
        return self._lens.pixmap(self._card_global_rect(), radius, lens,
                                 float(self.devicePixelRatioF() or 1.0),
                                 follow_move=liquid_glass.in_motion())

    def _queue_lens_refresh(self) -> None:
        """位置/尺寸变了：把重算压到 ~30fps（拖动时别把主线程拖满）。

        构造期 `FloatingWidget.__init__` 里的 resize/move 也会走到这里，
        那时计时器还没建——一律 getattr 兜底，缺了就直通重绘。
        """
        if getattr(self, "_lens", None) is None or not self._liquid_on():
            return
        throttle = getattr(self, "_lens_throttle", None)
        timer = getattr(self, "_lens_timer", None)
        if throttle is None or timer is None:
            return
        if throttle.ready():
            self.update()
            return
        # 本帧不允许重算 -> 补一个"到点重绘"，拖动停下后还能跟上最后一格
        timer.start(throttle.due_ms())

    def _check_wallpaper(self) -> None:
        """低频轮询壁纸版本：换了壁纸 / 改了分辨率，玻璃跟着换。"""
        if not self._liquid_on() or not self.isVisible():
            return
        lens = liquid_glass.lens_style(self.storage)
        if self._lens.stale(self._card_global_rect(), self.style.radius, lens):
            self.update()

    def moveEvent(self, e):
        super().moveEvent(e)
        self._queue_lens_refresh()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._queue_lens_refresh()

    def _paint_surface(self, p):
        st = self.style
        # v10 A8：玻璃画在**卡片**矩形上——编辑态窗口外扩了一圈，玻璃
        # 必须留在卡片位置，外扩区只保留 alpha=2 的命中填充。
        card = QRectF(self.card_rect())
        # v14 反馈 10：液态玻璃 = 透明底 + **实时**折射的壁纸。
        # 开没开由配色决定（st.liquid），不再是全局开关说了算。
        if self._liquid_on():
            pm = self._lens_pixmap(st.radius)
            if not pm.isNull() and liquid_glass.draw_refracted(
                    p, card, st.radius, pm):
                return
        paint_glass(p, card, st.radius,
                    tint_alpha=st.tint_alpha, tint_color=st.bg)

    def paint_content(self, p):
        """子类实现。默认什么都不画（防止漏实现时整块空白看不出来）。"""

    def on_settings_changed(self):
        """组件设置应用后的回调（默认清样式缓存 + 重绘）。"""
        self._style_cache = None
        self._lens.reset()             # 配色/参数可能变了，折射贴图重算
        self.update()
