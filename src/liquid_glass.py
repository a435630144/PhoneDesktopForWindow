# -*- coding: utf-8 -*-
"""液态玻璃：透明背景 + 边缘折射（v14 反馈 9/10）。

## 参考实现

GlassDesk 1.4.0（`src/GlassDesk.UI/Effects/TransitionGlass.hlsl`，WPF 像素
着色器；其镜头剖面源自 Kyant0/AndroidLiquidGlass，Apache-2.0）的核心算法：

    t        = 1 - depth / band                   # 0 在带内沿，1 在最外沿
    disp     = (1 - sqrt(1 - t²)) * 32 * strength # 圆剖面：边缘最大、带内归零
    sampleUV = uv + disp * normal                 # 沿**外法线向内**采样

也就是：**只有边缘一圈带内的底图被位移，中部一点都不动**。视觉上是一块
"厚玻璃压在壁纸上"——边缘把后面的图案放大/挤压出一道折射带，玻璃本身
是透明的，不加深色底、不画高光条、不描彩色边。

## 底图从哪来（v14 反馈 10：必须实时）

`LiveLens` 每次绘制按**表面当前的全局矩形**从 `wallpaper` 的整块壁纸快照
里裁一块，所以：

  * 组件拖到哪，玻璃里就是那个位置的图案（位置一变，key 变，重算）；
  * 换壁纸 / 改分辨率，版本号变，重算；
  * 不再"show() 之前抓一次屏"，也就没有"左上角天空色一直跟着组件跑"。

无壁纸时退回一次性抓屏（`grab_backdrop`），只是不再实时。

## 为什么不用逐像素位移

Qt 没有 per-pixel shader。纯 Python 逐像素采样实测 340×280@dpr2 要 450ms、
760×300@dpr2 要 950ms，常驻表面根本扛不住。

## 实现：逐行/逐列条带位移

除圆角外，玻璃边缘的法线是轴向的——顶边只需要"这一行取下面第 disp 行"，
左右同理。于是把带内每一行（列）单独从源图搬一条 1px 条带，位移量按
`_displacement(depth)` 变化：

  * 一条 1px 条带的搬运是 C++ 的 `drawImage`，整条边 44 行也只要几毫秒；
  * 沿法线方向逐像素精确（不是近似缩放），高对比图案上不会出现扇形伪影；
  * 位移是**渐进的**（每行不同），相邻行差 < 1px，看起来是连续的弯曲；
  * 条带按深度渐隐（`alpha` 0 -> 1），边缘清晰、往里过渡到磨砂面，
    不会出现"一圈硬边"。

圆角区两次覆盖（先横条后竖条，各自带渐隐）——面积只有 4×24² 像素，
在 24px 尺度上肉眼分不出斜向法线与轴向位移的差别，但**不会出接缝**。

曾经用"同心环带逐环放大"逼近，方向对但高对比底板上会出现扇形重影
（2026-10-24 目检否决），换成条带法。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import (QColor, QImage, QPainter, QPainterPath, QPen,
                           QPixmap)

from . import theme

# ---------------------------------------------------------------- 常量

SURFACES = ("folder", "dock", "widget")     # 三个可挂表面

DEFAULT_STRENGTH = 60       # 折射强度（0~100 -> 0~1.0 位移倍率）
DEFAULT_BAND = 24           # 折射带宽度（DIP，夹 6~40）
DISP_SCALE = 32.0           # 32 DIP 基准位移（与 GlassDesk 同源）
STRENGTH_MIN, STRENGTH_MAX = 0, 100
BAND_MIN, BAND_MAX = 6, 40
LIVE_FPS = 30               # 拖动时折射重算上限（约 33ms 一次）


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
    """某个表面是否允许液态玻璃（总开关一票否决）。

    v14 反馈 10 起，液态玻璃是**每个元素自己的配色**：

      * 文件夹 -> 色板选「液态玻璃」（`entry["tint"] == "liquid"`）；
      * 小组件 -> 组件设置里的「液态玻璃」开关（`settings["bg_liquid"]`）；
      * Dock   -> 只有它没有逐元素设置，保留自己的开关 `liquid_dock`。

    所以这里只剩"总闸 + Dock 自己的开关"两层，不再有全局的表面开关——
    否则会出现"组件设置里开着、全局却关着"的死结。
    """
    if surface not in SURFACES:
        return False
    if not master_enabled(storage):
        return False
    if surface == "dock":
        return _flag(_get(storage, "liquid_dock", True), True)
    return True


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


def _sharp_alpha(t: float) -> float:
    """条带透明度：外沿 1.0（清晰成象），内沿 0（交给磨砂面）。

    少了这条渐变，折射带的内边界会出现一圈"硬过渡"——看起来像贴了张
    半透明贴纸，而不是玻璃的连续弯曲。
    """
    if t <= 0.10:
        return 0.0
    return min(1.0, (t - 0.10) / 0.25)


def _scrim_for(img: QImage) -> QColor:
    """按底图亮度给一层极淡的暗纱（只在很亮的壁纸上出现）。

    完全不加纱时，白字压在浅色壁纸（Win11 默认壁纸的左上就是浅蓝）上看不清；
    加固定深色底又变成"一块灰板"。这里按亮度自适应：亮度 <=150 一点不加，
    255 时最多 40/255——既保留透明感，又保住可读性。
    """
    if img.isNull():
        return QColor(0, 0, 0, 0)
    one = img.scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    c = one.pixelColor(0, 0)
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    if lum <= 150:
        return QColor(0, 0, 0, 12)      # 暗壁纸：留一点"材质在场"的存在感
    return QColor(0, 0, 0, min(42, int(round(12 + (lum - 150) / 105.0 * 30))))


def refract(backdrop, radius: float, lens: Lens, dpr: float = 1.0,
            frost: float = 3.0, base=None, margin: float = 0.0,
            scrim: bool = True, hairline: bool = True) -> QPixmap:
    """把 `backdrop` 做成边缘折射贴图（输出只有表面本身那么大）。

    参数
    ----
    - `radius`：元素圆角（逻辑 DIP）；`dpr`：backdrop 的物理/逻辑比；
    - `margin`（逻辑 DIP）：backdrop 比表面**四周各大出多少**——边缘折射
      要采到表面**外面**的图案（见下），所以底图必须带一圈外扩；
    - `base`：已磨砂的同尺寸底图（实时路径在外层磨好，这里不再逐帧模糊）；
    - `frost`：没有 `base` 时的磨砂半径（0 = 中部也清晰）。

    ## 为什么采样方向是"向外"

    半径 r 处看玻璃边缘，看到的是**外面**的背景被压进来（像放大镜边缘）。
    设 s = d - offset(d)（d 是离边缘的深度），offset 从外沿的 max 降到内沿
    的 0，则 s 单调递增——不会出现折叠。

    反过来"向内采样"（s = d + offset）会让 s 在边缘附近非单调：一圈里同
    一段图案被采两次，视觉上是几条被抹平的色块（2026-10-24 目检抓到）。
    这也是为什么底图要比表面大一圈。

    中部在 frost=0 时与输入逐像素相等（判据 L6b）。
    """
    if backdrop is None:
        return QPixmap()
    is_pm = isinstance(backdrop, QPixmap)
    if is_pm and backdrop.isNull():
        return QPixmap()
    if not is_pm and not isinstance(backdrop, QImage):
        return QPixmap()

    dpr = float(dpr or 1.0)
    src = backdrop.toImage() if is_pm else backdrop
    if src.isNull():
        return QPixmap()
    src = src.convertToFormat(QImage.Format_ARGB32)

    m_dev = max(0.0, float(margin or 0.0) * dpr)
    sw = src.width() - 2.0 * m_dev
    sh = src.height() - 2.0 * m_dev
    if sw < 6 or sh < 6:
        if m_dev > 0.0:              # 外扩把图吃完了：退回不带外扩的算法
            return refract(src, radius, lens, dpr, frost, base, margin=0.0)
        return QPixmap.fromImage(src)
    if lens is None or lens.strength <= 0.0 or lens.band <= 0.5:
        return QPixmap.fromImage(src.copy(int(round(m_dev)), int(round(m_dev)),
                                          max(1, int(round(sw))),
                                          max(1, int(round(sh)))))

    band_dev = min(lens.band * dpr, min(sw, sh) * 0.5)
    radius_dev = max(0.0, min(float(radius) * dpr, sw * 0.5, sh * 0.5))
    if band_dev < 1.0:
        return QPixmap.fromImage(src.copy(int(round(m_dev)), int(round(m_dev)),
                                          max(1, int(round(sw))),
                                          max(1, int(round(sh)))))

    # 中部底图：优先用外部磨砂图（实时路径），否则现场模糊
    base_img = None
    if base is not None:
        b = base.toImage() if isinstance(base, QPixmap) else base
        if not b.isNull():
            if (b.width(), b.height()) != (src.width(), src.height()):
                b = b.scaled(src.width(), src.height(),
                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            base_img = b.convertToFormat(QImage.Format_ARGB32)
    if base_img is None:
        base_img = src
        if frost > 0.05:
            try:
                from . import effects
                frosted = effects.blur_pixmap(QPixmap.fromImage(src),
                                              blur_radius=frost)
                if not frosted.isNull():
                    base_img = frosted.toImage().convertToFormat(
                        QImage.Format_ARGB32)
            except Exception:      # noqa: BLE001 —— 模糊失败就用清晰底图
                base_img = src

    sw_i = max(1, int(round(sw)))
    sh_i = max(1, int(round(sh)))
    out = QImage(sw_i, sh_i, QImage.Format_ARGB32)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)

    surface = QRectF(0.0, 0.0, float(sw_i), float(sh_i))
    src_surface = QRectF(m_dev, m_dev, float(sw_i), float(sh_i))
    clip = QPainterPath()
    clip.addRoundedRect(surface, radius_dev, radius_dev)
    p.setClipPath(clip)
    # 暗纱先烘进中部底图，而不是事后 fillPath：边缘折射带与中部从
    # **同一张**处理过的底图采样（否则带内是原始壁纸、带外是磨砂+纱，
    # 亮度/色彩两套真相——用户反馈的"磨砂区与折射区明显色差"即此）
    if scrim:
        veil = _scrim_for(base_img)
        if veil.alpha() > 0:
            vp = QPainter(base_img)
            vp.fillRect(base_img.rect(), veil)
            vp.end()
    p.drawImage(surface, base_img, src_surface)     # 中部：磨砂面

    max_sx = float(src.width() - 1)
    max_sy = float(src.height() - 1)

    def _clamp(v: float, hi: float) -> float:
        return max(0.0, min(hi, v))

    band_i = int(round(band_dev))
    if band_i >= 1:
        # 顶/底：带内每一行取"更靠外"的同一行（沿 y 向外采样）
        # 采样源用 base_img（与中部同磨砂同纱）而非原始 src——折射的
        # 几何形变保留，但色彩/亮度与磨砂面一体，不再有带内外色差
        for d in range(band_i):
            t = 1.0 - float(d) / band_dev
            alpha = _sharp_alpha(t)
            disp = _displacement(float(d), band_dev, lens.strength, dpr)
            if alpha <= 0.01 or disp < 0.35:
                continue
            p.setOpacity(alpha)
            y_top = float(d)
            y_bot = sh_i - 1.0 - float(d)
            sy_top = _clamp(m_dev + y_top - disp, max_sy)
            sy_bot = _clamp(m_dev + y_bot + disp, max_sy)
            p.drawImage(QRectF(0.0, y_top, float(sw_i), 1.0), base_img,
                        QRectF(m_dev, sy_top, float(sw_i), 1.0))
            p.drawImage(QRectF(0.0, y_bot, float(sw_i), 1.0), base_img,
                        QRectF(m_dev, sy_bot, float(sw_i), 1.0))
        # 左/右：带内每一列取"更靠外"的同一列（沿 x 向外采样）
        for d in range(band_i):
            t = 1.0 - float(d) / band_dev
            alpha = _sharp_alpha(t)
            disp = _displacement(float(d), band_dev, lens.strength, dpr)
            if alpha <= 0.01 or disp < 0.35:
                continue
            p.setOpacity(alpha)
            x_left = float(d)
            x_right = sw_i - 1.0 - float(d)
            sx_left = _clamp(m_dev + x_left - disp, max_sx)
            sx_right = _clamp(m_dev + x_right + disp, max_sx)
            p.drawImage(QRectF(x_left, 0.0, 1.0, float(sh_i)), base_img,
                        QRectF(sx_left, m_dev, 1.0, float(sh_i)))
            p.drawImage(QRectF(x_right, 0.0, 1.0, float(sh_i)), base_img,
                        QRectF(sx_right, m_dev, 1.0, float(sh_i)))
        p.setOpacity(1.0)

    if hairline:
        # 轮廓线：亮底用暗线、暗底用亮线，只 26/255——界定边界，不是高光
        one = base_img.scaled(1, 1, Qt.IgnoreAspectRatio,
                              Qt.SmoothTransformation).pixelColor(0, 0)
        lum = 0.299 * one.red() + 0.587 * one.green() + 0.114 * one.blue()
        pen = (QColor(0, 0, 0, 26) if lum >= 150 else QColor(255, 255, 255, 26))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(pen, 1.0))
        p.drawRoundedRect(surface.adjusted(0.5, 0.5, -0.5, -0.5),
                          max(0.5, radius_dev - 0.5),
                          max(0.5, radius_dev - 0.5))

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
    """抓一块屏幕内容当折射底图（**一次性兜底**，无壁纸快照时用）。

    必须在目标窗口 **show() 之前**调用，否则会把它自己抓进去。
    """
    try:
        from . import effects
        return effects.grab_backdrop(rect)
    except Exception:      # noqa: BLE001 —— 抓屏失败不该让表面画不出来
        return QPixmap()


def border_color() -> QColor:
    """液态表面的 1px 描边（很淡，只用来界定边界，不是高光）。"""
    return QColor(255, 255, 255, 26)


# ---------------------------------------------------------------- 实时折射


def lens_margin(lens: Lens) -> float:
    """折射要采到表面外面多少（逻辑 DIP）：带宽 + 最大位移 + 余量。"""
    if lens is None:
        return 0.0
    return float(lens.band) + DISP_SCALE * float(lens.strength) + 2.0


class LiveLens:
    """一个表面的折射贴图缓存，**跟着位置和壁纸实时变**。

    用法（每次 paintEvent 调一次即可）：

        pm = self._lens.pixmap(全局矩形, 圆角, 参数, dpr)

    key = (壁纸版本, 矩形, 圆角, 强度, 带宽)——组件被拖动时矩形变，自动重算；
    壁纸换了版本号变，也自动重算。裁剪 + 位移整体 5~10ms（300×240@dpr2），
    只在变化后的第一帧发生。
    """

    def __init__(self):
        self._key = None
        self._pm = QPixmap()
        self._ver = -1

    def reset(self) -> None:
        self._key = None
        self._pm = QPixmap()
        self._ver = -1

    @property
    def pixmap_cached(self) -> QPixmap:
        return self._pm

    def version(self) -> int:
        """当前壁纸快照版本（-1 = 无快照，走兜底抓屏）。"""
        try:
            from . import wallpaper
            wall = wallpaper.snapshot()
        except Exception:            # noqa: BLE001
            return -1
        return wall.version if wall is not None else -1

    def stale(self, rect, radius: float, lens: Lens) -> bool:
        """矩形/参数/壁纸变了没有（用于决定要不要触发重绘）。"""
        rect = QRect(rect)
        key = (self.version(), rect.x(), rect.y(), rect.width(),
               rect.height(), round(float(radius), 2),
               round(lens.strength, 4), round(lens.band, 3))
        return key != self._key

    def pixmap(self, rect, radius: float, lens: Lens,
               dpr: float = 1.0) -> QPixmap:
        rect = QRect(rect)
        if rect.isEmpty():
            return QPixmap()
        ver = self.version()
        key = (ver, rect.x(), rect.y(), rect.width(), rect.height(),
               round(float(radius), 2), round(lens.strength, 4),
               round(lens.band, 3))
        if key == self._key and not self._pm.isNull():
            return self._pm
        self._key = key

        # 外扩一圈：边缘折射采的是表面**外面**的图案
        m = int(math.ceil(lens_margin(lens)))
        outer = QRect(rect.x() - m, rect.y() - m,
                      rect.width() + 2 * m, rect.height() + 2 * m)

        if ver >= 0:
            try:
                from . import wallpaper
                wall = wallpaper.snapshot()
            except Exception:        # noqa: BLE001
                wall = None
            if wall is not None:
                sharp = wall.crop(outer)
                if not sharp.isNull():
                    k = sharp.width() / max(1.0, float(outer.width()))
                    soft = wall.soft_crop(outer, (sharp.width(), sharp.height()))
                    self._pm = refract(sharp, radius, lens, k, base=soft,
                                       margin=m)
                    return self._pm

        # 兜底：没有壁纸（纯色 / 动态壁纸 / 读注册表失败）→ 一次性抓屏
        pm = grab_backdrop(outer)
        if pm.isNull():
            self._pm = QPixmap()
            return self._pm
        k = pm.width() / max(1.0, float(outer.width()))
        self._pm = refract(pm, radius, lens, k or dpr, margin=m)
        return self._pm


class Throttle:
    """把"每帧都要重算"的动作压到 `LIVE_FPS`（拖动时别把主线程拖满）。"""

    def __init__(self, fps: int = LIVE_FPS):
        self._gap = 1.0 / max(1, int(fps))
        self._last = 0.0

    def ready(self) -> bool:
        now = time.perf_counter()
        if now - self._last >= self._gap:
            self._last = now
            return True
        return False

    def due_ms(self) -> int:
        """距离下一次允许执行还有多少毫秒（给单次定时器用）。"""
        left = self._gap - (time.perf_counter() - self._last)
        return max(1, int(round(left * 1000)))
