# -*- coding: utf-8 -*-
"""液态玻璃：透明背景 + 边缘折射（v14 反馈 9 重做版）。

## 参考实现

GlassDesk 1.4.0（`src/GlassDesk.UI/Effects/TransitionGlass.hlsl`，WPF 像素
着色器；其镜头剖面源自 Kyant0/AndroidLiquidGlass，Apache-2.0）的核心算法：

    t        = 1 - depth / band                   # 0 在带内沿，1 在最外沿
    disp     = (1 - sqrt(1 - t²)) * 32 * strength # 圆剖面：边缘最大、带内归零
    sampleUV = uv + disp * normal                 # 沿**外法线向内**采样

也就是：**只有边缘一圈带内的底图被位移，中部一点都不动**。视觉上是一块
"厚玻璃压在壁纸上"——边缘把后面的图案放大/挤压出一道折射带，玻璃本身
是透明的，不加深色底、不画高光条、不描彩色边。

## 为什么不用逐像素位移

Qt 没有 per-pixel shader。纯 Python 逐像素采样实测 340×280@dpr2 要 450ms、
760×300@dpr2 要 950ms，常驻表面根本扛不住。

## 实现：逐行/逐列条带位移

除圆角外，玻璃边缘的法线是轴向的——顶边只需要"这一行取下面第 disp 行"，
左右同理。于是把带内每一行（列）单独从源图搬一条 1px 条带，位移量按
`_displacement(depth)` 变化：

  * 一条 1px 条带的搬运是 C++ 的 `drawImage`，整条边 48 行也只要几毫秒；
  * 沿法线方向逐像素精确（不是近似缩放），高对比图案上不会出现扇形伪影；
  * 位移是**渐进的**（每行不同），相邻行差 < 1px，看起来是连续的弯曲。

圆角区两次覆盖（先横条后竖条）取其中的横向位移——面积只有 4×24² 像素，
在 24px 尺度上肉眼分不出斜向法线与轴向位移的差别，但**不会出接缝**。

曾经用"同心环带逐环放大"逼近，方向对但高对比底板上会出现扇形重影
（2026-10-24 目检否决），换成条带法。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QColor, QImage, QPainter, QPainterPath, QPen,
                           QPixmap)

from . import theme

# ---------------------------------------------------------------- 常量

SURFACES = ("folder", "dock", "widget")     # 三个可挂表面

DEFAULT_STRENGTH = 55       # 折射强度（0~100 -> 0~1.0 位移倍率）
DEFAULT_BAND = 22           # 折射带宽度（DIP，夹 6~40）
DISP_SCALE = 32.0           # 32 DIP 基准位移（与 GlassDesk 同源）
STRENGTH_MIN, STRENGTH_MAX = 0, 100
BAND_MIN, BAND_MAX = 6, 40


@dataclass(frozen=True)
class Lens:
    """一次折射需要的参数（0 强度 = 直通原图）。"""

    strength: float = DEFAULT_STRENGTH / 100.0   # 0~1
    band: float = float(DEFAULT_BAND)            # DIP


# ---------------------------------------------------------------- 参数读取


def _flag(value, default: bool) -> bool:
    """只认真布尔 / 0 / 1 / 'true' / 'false'——`bool("false")` 是 True 的坑。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "1"):
            return True
        if s in ("false", "0"):
            return False
    return default


def _get(storage, key: str, default):
    """读一个设置；Storage 自带 DEFAULT 回退，这里再兜一层（裸 dict 也行）。"""
    try:
        v = storage.get_setting(key)
    except (AttributeError, TypeError):
        try:
            v = storage[key]
        except (KeyError, TypeError):
            v = None
    return default if v is None else v


def _clamp(value, lo, hi, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:                      # NaN
        return default
    return max(lo, min(hi, v))


def lens_style(storage) -> Lens:
    """从 storage 读一份折射参数（唯一入口；设置面板只写键）。"""
    strength = _clamp(_get(storage, "liquid_strength", DEFAULT_STRENGTH),
                      STRENGTH_MIN, STRENGTH_MAX, DEFAULT_STRENGTH)
    band = _clamp(_get(storage, "liquid_band", DEFAULT_BAND),
                  BAND_MIN, BAND_MAX, DEFAULT_BAND)
    return Lens(strength=strength / 100.0, band=float(band))


def master_enabled(storage) -> bool:
    """液态玻璃总开关（一票否决三个表面）。"""
    return _flag(_get(storage, "liquid_glass", True), True)


def surface_enabled(storage, surface: str) -> bool:
    """某个表面是否启用液态玻璃（总开关一票否决）。"""
    if surface not in SURFACES:
        return False
    if not master_enabled(storage):
        return False
    return _flag(_get(storage, "liquid_" + surface, True), True)


# ---------------------------------------------------------------- 折射


def _displacement(depth: float, band: float, strength: float,
                  dpr: float = 1.0) -> float:
    """GlassDesk 的圆剖面：带内沿 0 -> 最外沿 max（**物理像素**）。

    `depth` / `band` 都是物理像素，位移基准 32 DIP 也要乘 dpr——漏乘会让
    高 DPI 屏上的折射只有一半强度（dpr=2 时肉眼几乎看不出）。
    """
    if band <= 0.01 or strength <= 0.0:
        return 0.0
    t = 1.0 - max(0.0, min(band, depth)) / band
    return (1.0 - math.sqrt(max(0.0, 1.0 - t * t))) * DISP_SCALE * strength * dpr


def refract(backdrop: QPixmap, radius: float, lens: Lens,
            dpr: float = 1.0, frost: float = 3.0) -> QPixmap:
    """把 `backdrop`（正好覆盖元素矩形）做成边缘折射贴图。

    - `radius`：元素圆角（逻辑 DIP）；`dpr`：backdrop 的物理/逻辑比；
    - `frost`：中部磨砂半径（0 = 不磨砂）。**边缘带始终取清晰底图**——
      真玻璃的边是透镜（成像清晰），中部才是磨砂面；两边都糊的话折射就
      看不出来了；
    - 位移为 0（关闭/弱到不可见）时直接返回原图，不做任何拷贝。

    中部在 frost=0 时与输入逐像素相等（判据 L6b）。
    """
    if backdrop is None or backdrop.isNull():
        return QPixmap()
    dpr = float(dpr or 1.0)
    if lens is None or lens.strength <= 0.0 or lens.band <= 0.5:
        return backdrop

    w_dev = float(backdrop.width())
    h_dev = float(backdrop.height())
    if w_dev < 6 or h_dev < 6:
        return backdrop
    band_dev = min(lens.band * dpr, min(w_dev, h_dev) * 0.5)
    radius_dev = max(0.0, min(float(radius) * dpr,
                              w_dev * 0.5, h_dev * 0.5))
    if band_dev < 1.0:
        return backdrop

    # 边缘带取清晰底图，中部取磨砂底图（frost=0 时两者同一张）
    src = backdrop.toImage().convertToFormat(QImage.Format_ARGB32)
    base = src
    if frost > 0.05:
        try:
            from . import effects
            frosted = effects.blur_pixmap(backdrop, blur_radius=frost)
            if not frosted.isNull():
                base = frosted.toImage().convertToFormat(QImage.Format_ARGB32)
        except Exception:      # noqa: BLE001 —— 模糊失败就用清晰底图
            base = src
    out = QImage(int(w_dev), int(h_dev), QImage.Format_ARGB32)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)

    full = QRectF(0.0, 0.0, w_dev, h_dev)
    src_rect = QRectF(0.0, 0.0, w_dev, h_dev)
    clip = QPainterPath()
    clip.addRoundedRect(full, radius_dev, radius_dev)
    p.setClipPath(clip)
    p.drawImage(full, base, src_rect)         # 中部：磨砂面

    band_i = int(round(band_dev))
    if band_i >= 1:
        # 顶/底：带内每一行取"更靠内"的同一行（沿 y 位移）
        for d in range(band_i):
            disp = _displacement(float(d), band_dev, lens.strength, dpr)
            if disp < 0.35:
                continue
            y_top = float(d)
            y_bot = h_dev - 1.0 - float(d)
            # 顶边：往下（更内）取；底边：往上取
            p.drawImage(QRectF(0.0, y_top, w_dev, 1.0), src,
                        QRectF(0.0, y_top + disp, w_dev, 1.0))
            p.drawImage(QRectF(0.0, y_bot, w_dev, 1.0), src,
                        QRectF(0.0, y_bot - disp, w_dev, 1.0))
        # 左/右：带内每一列取"更靠内"的同一列（沿 x 位移）
        for d in range(band_i):
            disp = _displacement(float(d), band_dev, lens.strength, dpr)
            if disp < 0.35:
                continue
            x_left = float(d)
            x_right = w_dev - 1.0 - float(d)
            p.drawImage(QRectF(x_left, 0.0, 1.0, h_dev), src,
                        QRectF(x_left + disp, 0.0, 1.0, h_dev))
            p.drawImage(QRectF(x_right, 0.0, 1.0, h_dev), src,
                        QRectF(x_right - disp, 0.0, 1.0, h_dev))

    p.end()
    return QPixmap.fromImage(out)


def draw_refracted(p: QPainter, rect, radius: float, pm: QPixmap,
                   border: Optional[QColor] = None) -> bool:
    """把**已算好**的折射贴图画进 `rect`（圆角裁剪 + 可选 1px 描边）。

    表面持有缓存时用这个（开合动画每帧都会画，不能每帧重算折射）。
    """
    if pm is None or pm.isNull():
        return False
    rect = QRectF(rect)
    if rect.width() <= 1 or rect.height() <= 1:
        return False
    r = max(0.0, min(float(radius), rect.width() / 2.0, rect.height() / 2.0))
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)
    p.save()
    p.setClipPath(path)
    p.drawPixmap(rect, pm, QRectF(pm.rect()))
    p.restore()
    if border is not None:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(border, 1.0))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5),
                          max(0.5, r - 0.5), max(0.5, r - 0.5))
    return True


def paint_lens(p: QPainter, rect, radius: float, backdrop: QPixmap,
               lens: Lens, border: Optional[QColor] = None,
               dpr: float = 1.0) -> bool:
    """一步到位：折射 + 绘制（无缓存场景 / 一次性截图用）。

    常驻或带动画的表面请自己缓存 `refract()` 的结果再调 `draw_refracted()`。
    """
    if backdrop is None or backdrop.isNull():
        return False
    return draw_refracted(p, rect, radius, refract(backdrop, radius, lens, dpr),
                          border)


def grab_backdrop(rect) -> QPixmap:
    """抓一块屏幕内容当折射底图（**清晰**，磨砂在 `refract` 里做）。

    必须在目标窗口 **show() 之前**调用，否则会把它自己抓进去。
    """
    try:
        from . import effects
        return effects.grab_backdrop(rect)
    except Exception:      # noqa: BLE001 —— 抓屏失败不该让表面画不出来
        return QPixmap()


def border_color() -> QColor:
    """液态表面的 1px 描边（很淡，只用来界定边界，不是高光）。"""
    return QColor(255, 255, 255, theme.GLASS_BORDER)
