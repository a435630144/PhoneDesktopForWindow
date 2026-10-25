# -*- coding: utf-8 -*-
"""视觉动效工具箱：液态玻璃背板、真弹簧曲线、玻璃表面绘制。

设计参考（2025 苹果 Liquid Glass + Fluid Interfaces）：
  - 液态玻璃：背后内容实时高斯模糊 + 半透明 tint + 边缘高光(specular)
  - 弹簧：全部动效用阻尼谐振子解析解（QEasingCurve 自定义缓动），
    默认临界阻尼（damping 1.0，无过冲）；带动量的手势 / sheet 档
    弹窗才用欠阻尼（0.7~0.85，一次衰减 wobble ≈ Motion bounce 0.2）
  - 控件按压回弹、错峰淡入（stagger）

实现取舍（重要）：
  不用 SetWindowCompositionAttribute 的 acrylic blur——它与 Qt 的
  WA_TranslucentBackground + WS_EX_LAYERED 组合冲突（acrylic 不显示，
  实测踩坑）。改为：show() 前抓屏 -> C++ 层高斯模糊 -> 自绘。
  桌面场景背后基本静态（壁纸+桌面带），一次性抓取完全够用，
  且跨 Win10/Win11 稳定、观感完全可控。

全部只用 Qt 自带模块，零第三方依赖，不影响打包体积。
"""
import math
import time

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QPoint,
                            QPointF, QRect, QRectF, QObject, QTimer,
                            Signal, Qt)
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)

from . import theme

SPI_GETCLIENTAREAANIMATION = 0x1002  # 系统动画效果开关（设置 -> 视觉效果 -> 动画）
_rm_cache = None


def reduced_motion() -> bool:
    """系统动画效果是否被关闭——prefers-reduced-motion 的桌面等价物。

    苹果无障碍规范：关动画时去掉位移/弹性过冲，保留透明度/颜色反馈。
    结果缓存（系统设置运行期不变）。
    """
    global _rm_cache
    if _rm_cache is None:
        try:
            import ctypes
            v = ctypes.c_int(0)
            ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(v), 0)
            _rm_cache = not bool(v.value)
        except Exception:
            _rm_cache = False
    return _rm_cache


def dur(ms: int) -> int:
    """动效时长：系统关动画时压到 120ms 内（保留反馈、去掉大幅位移感）。"""
    return min(ms, 120) if reduced_motion() else ms


# ---------- 缓动曲线 ----------
_SPRING_CACHE = {}


def spring_curve(damping: float = 1.0, duration_ms: int = 320) -> QEasingCurve:
    """真弹簧缓动：阻尼谐振子解析解（取代 OutBack 多项式近似）。

    苹果 Fluid Interfaces 规范「动效全用弹簧」：
      - 默认 UI 弹簧 = 临界阻尼（damping 1.0，无过冲，response 0.3~0.4s）
      - 带动量的手势 / sheet 档弹窗 = 欠阻尼（0.7~0.85，一次衰减 wobble，
        相当于 Motion bounce 0.2）；无动量的淡入禁用过冲
      - duration_ms = 稳定时间：t=1 时已收敛 99.9%，尾部不可见——与 iOS
        弹簧动画语义一致，可见运动集中在前期，这才是真弹簧的形态
        （OutBack 的过冲量与时长无关且只有一次多项式拱，形状是错的）

    实现：PySide6 QEasingCurve 自定义缓动（C++ 每帧回调），解析解几乎
    零开销；按 (damping, duration) 缓存复用。任何时刻可打断——曲线只依赖
    归一化进度，从当前值起播的新动画天然满足可打断性。

    damping 钳制：>1 无意义（过阻尼只是更慢），<=0 不收敛。
    """
    key = (round(float(damping), 3), int(duration_ms))
    cached = _SPRING_CACHE.get(key)
    if cached is not None:
        return cached
    z = min(1.0, max(0.3, float(damping)))
    w = 12.0 / z                      # 归一化角频率：t=1 时收敛到 99.9%
    wd = w * math.sqrt(1.0 - z * z)   # 阻尼角频率（z<1 时为实数）
    zw = z * w

    def _spring(t: float) -> float:
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0
        if z >= 1.0:
            # 临界阻尼：1 - (1+wt)e^{-wt}，不过冲
            return 1.0 - (1.0 + w * t) * math.exp(-w * t)
        # 欠阻尼：1 - e^{-zwt}(cos(wd·t) + zw/wd·sin(wd·t))，一次衰减振荡
        return 1.0 - math.exp(-zw * t) * (math.cos(wd * t) + (zw / wd) * math.sin(wd * t))

    curve = QEasingCurve(QEasingCurve.Type.Custom)
    curve.setCustomType(_spring)
    _SPRING_CACHE[key] = curve
    return curve


def spring_out(overshoot: float = 1.0) -> QEasingCurve:
    """[兼容保留] 弹性出场。旧签名（OutBack overshoot），新代码请用
    spring_curve(0.8, ms)。映射：overshoot 1.0 ≈ damping 0.8。"""
    z = 1.0 / (1.0 + 0.25 * float(overshoot))
    return spring_curve(z, 340)


def smooth_out() -> QEasingCurve:
    """[兼容保留] 临界阻尼出场（苹果默认 UI 弹簧 damping 1.0）。"""
    return spring_curve(1.0, 320)


# ---------- 玻璃背板 ----------
def grab_backdrop(rect: QRect) -> QPixmap:
    """抓取 rect（全局逻辑坐标）处的屏幕内容作为玻璃背板。

    必须在目标窗口 show() 之前调用，否则会把自己抓进去。
    返回的 pixmap 带屏幕 DPR，绘制时按目标矩形缩放即可。
    """
    if rect.isEmpty():
        return QPixmap()
    center = rect.center()
    screen = None
    for s in QGuiApplication.screens():
        if s.geometry().contains(center):
            screen = s
            break
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    try:
        return screen.grabWindow(0, rect.x(), rect.y(),
                                 rect.width(), rect.height())
    except Exception:
        return QPixmap()


def blur_pixmap(pm: QPixmap, blur_radius: float = 6.0,
                downscale: int = 6) -> QPixmap:
    """高斯模糊近似：缩小 downscale 倍 -> C++ 层真高斯 -> 平滑放大回原尺寸。

    - 缩小/放大走 Qt C++ 双线性，快且自带柔化
    - QGraphicsBlurEffect 在 C++ 层完成，质量接近真高斯
    - 300x400 面板整体 < 15ms，打开文件夹时一次性，无感
    """
    if pm.isNull() or pm.width() < 8 or pm.height() < 8:
        return pm
    from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsScene
    small = pm.scaled(max(1, pm.width() // downscale),
                      max(1, pm.height() // downscale),
                      Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    scene = QGraphicsScene()
    item = scene.addPixmap(small)
    eff = QGraphicsBlurEffect()
    eff.setBlurRadius(blur_radius)
    item.setGraphicsEffect(eff)
    out = QPixmap(small.size())
    out.fill(Qt.transparent)
    painter = QPainter(out)
    scene.render(painter)
    painter.end()
    return out.scaled(pm.width(), pm.height(),
                      Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def glass_backdrop(rect: QRect, blur_radius: float = 8.0) -> QPixmap:
    """液态玻璃背板：抓屏 -> 高斯模糊（一步到位）。"""
    return blur_pixmap(grab_backdrop(rect), blur_radius)


# ---------- 玻璃表面绘制 ----------
def paint_glass(p: QPainter, rect: QRect, backdrop: QPixmap,
                radius: float = None):
    """在 painter 上绘制液态玻璃表面（backdrop 为已模糊的背板）。

    层次（自内而外）：
      1. 模糊背板：背后是什么就透什么，随窗口形变（玻璃的"透"）
      2. 垂直 tint 渐变：顶部冷光 -> 中部玻璃色 -> 底部加深（保文字可读）
      3. 顶部 specular 高光线：液态玻璃的灵魂，两端渐隐
      4. 底部反光细线 + 全周 1px 描边
    """
    if radius is None:
        radius = theme.GLASS_RADIUS
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)

    p.save()
    p.setClipPath(path)
    if not backdrop.isNull():
        # 必须走四参重载：PySide6 没有 drawPixmap(QRectF, QPixmap) 两参签名，
        # 传 QRectF 会抛 TypeError——且异常若在重入绘制栈中向上传播，
        # 会穿过多层活动 QPainter 直接杀掉进程（Fatal error: Aborted）。
        # source 用背板完整物理矩形（pixmap 坐标），target 是逻辑矩形，
        # DPR 由 painter 自动映射，1:1 无损。
        p.drawPixmap(rect, backdrop, QRectF(backdrop.rect()))
    # tint 渐变：上浅下深——顶部透光、底部沉实
    grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
    grad.setColorAt(0.0, QColor(255, 255, 255, 30))
    grad.setColorAt(0.35, QColor(20, 22, 28, theme.GLASS_TINT_ALPHA))
    grad.setColorAt(1.0, QColor(15, 16, 21, theme.GLASS_TINT_ALPHA + 38))
    p.fillPath(path, grad)
    p.restore()

    # 顶部高光（水平渐变线，两端透明，中间最亮）
    inset = radius * 0.9
    hl = QLinearGradient(rect.left() + inset, 0, rect.right() - inset, 0)
    hl.setColorAt(0.0, QColor(255, 255, 255, 0))
    hl.setColorAt(0.5, QColor(255, 255, 255, theme.GLASS_SPECULAR))
    hl.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setPen(QPen(QBrush(hl), 1.5))
    p.drawLine(QPointF(rect.left() + inset, rect.top() + 1.2),
               QPointF(rect.right() - inset, rect.top() + 1.2))

    # 底部反光（更弱，模拟玻璃厚度折射）
    bl = QLinearGradient(rect.left() + inset, 0, rect.right() - inset, 0)
    bl.setColorAt(0.0, QColor(255, 255, 255, 0))
    bl.setColorAt(0.5, QColor(255, 255, 255, 46))
    bl.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setPen(QPen(QBrush(bl), 1.0))
    p.drawLine(QPointF(rect.left() + inset, rect.bottom() - 1.2),
               QPointF(rect.right() - inset, rect.bottom() - 1.2))

    # 全周 1px 描边，界定玻璃边界
    p.setPen(QPen(QColor(255, 255, 255, theme.GLASS_BORDER), 1.0))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


# ---------------------------------------------------------------- 帧率驱动器（v14p）

def frame_interval(storage=None) -> int:
    """全局动画帧率对应的 tick 间隔（ms，夹 4~66）。

    帧率档（设置 ▸ 外观 ▸ 渲染频率）是**全局**语义：液态玻璃重算、
    monitor 等自驱动循环、以及 FrameDriver 驱动的所有属性动画共用。
    Qt 的 QPropertyAnimation 走内部统一时钟（固定 ~60fps，私有 API
    无法改频），所以我们的动画一律用 FrameDriver——帧率档真正管到
    图标 hover/落位弹簧、组件移动、翻页、文件夹开合、Dock 悬停。
    """
    fps = 60.0
    try:
        from . import liquid_glass
        fps = float(liquid_glass.live_fps(storage))
    except Exception:            # noqa: BLE001 —— 读不到用 60
        fps = 60.0
    fps = max(24.0, min(240.0, fps))
    return max(4, min(66, round(1000.0 / fps)))


def _lerp(a, b, k: float):
    """按进度插值（float/int/QPoint/QPointF；QColor 走 rgb）。"""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * k
    try:
        if isinstance(a, QColor) and isinstance(b, QColor):
            return QColor(int(a.red() + (b.red() - a.red()) * k),
                          int(a.green() + (b.green() - a.green()) * k),
                          int(a.blue() + (b.blue() - a.blue()) * k),
                          int(a.alpha() + (b.alpha() - a.alpha()) * k))
        pa, pb = QPointF(a), QPointF(b)
        out = QPointF(pa.x() + (pb.x() - pa.x()) * k,
                      pa.y() + (pb.y() - pa.y()) * k)
        if isinstance(a, QPoint.__class__) or isinstance(a, QPoint):
            return QPoint(int(round(out.x())), int(round(out.y())))
        return out
    except Exception:            # noqa: BLE001 —— 未知类型退化为端点
        return b if k >= 0.5 else a


class FrameDriver(QObject):
    """QPropertyAnimation 的**帧率受控**等价物（v14p）。

    接口对齐（propertyName/stop/finished/start），现有 `_anims` 列表
    管理代码（按属性停旧、防 GC、finished 清理）原样兼容。区别只在
    时钟：tick 间隔取 `frame_interval(owner.storage)`——设置里的渲染
    频率档由此真正作用到每一个动画。
    """

    finished = Signal()

    def __init__(self, owner, name, apply, start, end, ms, curve=None):
        super().__init__(owner)
        self._name = name.encode() if isinstance(name, str) else bytes(name)
        self._apply = apply
        self._start = start
        self._end = end
        self._ms = max(1.0, float(ms))
        # 调用方两种传法都有：QEasingCurve 实例（spring_curve）或枚举常量
        # （QEasingCurve.OutCubic——QPropertyAnimation 会隐式转换，这里归一）
        if curve is None:
            curve = QEasingCurve(QEasingCurve.Linear)
        elif not isinstance(curve, QEasingCurve):
            curve = QEasingCurve(curve)
        self._curve = curve
        self._t0 = time.monotonic()
        self._done = False
        self._timer = QTimer(self)
        self._timer.setInterval(frame_interval(getattr(owner, "storage",
                                                       None)))
        self._timer.timeout.connect(self._tick)

    # 接口补齐：duration()/state()——部分调用方/测试沿 QPropertyAnimation
    # 习惯查询（v7e 测试的 state()、翻页测试的 duration()）
    State = QAbstractAnimation.State

    def duration(self) -> int:
        return int(self._ms)

    def easingCurve(self) -> QEasingCurve:
        return self._curve

    def state(self):
        return (QAbstractAnimation.State.Running if self._timer.isActive()
                else QAbstractAnimation.State.Stopped)

    def propertyName(self):
        return self._name

    def start(self):
        self._apply(_lerp(self._start, self._end,
                          self._curve.valueForProgress(0.0)))
        self._timer.start()

    def stop(self):
        """中途停止（与 Qt 同语义：仍发 finished）。"""
        if self._done:
            return
        self._done = True
        self._timer.stop()
        self.finished.emit()

    def _tick(self):
        k = (time.monotonic() - self._t0) * 1000.0 / self._ms
        if k >= 1.0:
            self._done = True
            self._timer.stop()
            self._apply(self._end)
            self.finished.emit()
            return
        self._apply(_lerp(self._start, self._end,
                          self._curve.valueForProgress(k)))


def drive(owner, name, apply, start, end, ms, curve=None, done=None):
    """启动一个帧率受控的值动画（name 仅作 propertyName 标识）。"""
    d = FrameDriver(owner, name, apply, start, end, ms, curve)
    if done is not None:
        d.finished.connect(done)
    d.start()
    return d


def drive_property(owner, prop, start, end, ms, curve=None, done=None):
    """驱动 owner 的 Qt/Python 属性（prop 可 bytes/str）。"""
    name = prop.decode() if isinstance(prop, bytes) else str(prop)

    def _set(v):
        owner.setProperty(name, v)

    return drive(owner, prop, _set, start, end, ms, curve, done)
