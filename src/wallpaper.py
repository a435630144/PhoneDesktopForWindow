# -*- coding: utf-8 -*-
"""桌面壁纸快照：液态玻璃的**实时**折射底图来源（v14 反馈 10）。

## 为什么不再抓屏

v14b 的实现是"表面 show() 之前抓一次屏"——只在打开那一刻对，之后：

  * 组件被拖到别处，玻璃里的图案还停在**创建时的位置**（用户实测：
    左上角天空色一直跟着组件跑）；
  * 换壁纸 / 改分辨率，所有表面都还是旧图；
  * 实时抓屏本身也不行——抓到的会包含表面自己，形成反馈。

## 做法

一次性把**整块虚拟桌面**的壁纸合成成一张图（按注册表里的填充方式：填满 /
适应 / 拉伸 / 居中 / 平铺 / 跨屏），然后任何表面按自己的**全局矩形**从这张
图上裁一块。位置一变，裁的窗口就变，图案自然跟着走——这就是"实时"：

    crop = wallpaper.snapshot().crop(表面全局矩形)

成本：裁一块 = 一次 `QImage.copy()`（<0.1ms），所以拖动时每帧重算都行。

版本号 `version` 由（壁纸路径 / 文件 mtime / 填充方式 / 屏幕布局 / DPR）
求得。表面每隔 1 秒对比一次版本号，变了就重取——换壁纸无需重启。

无壁纸（纯色桌面 / 动态壁纸引擎 / 读注册表失败）时返回 None，调用方退回
v14b 的一次性抓屏兜底。

已知边界：动态壁纸（Wallpaper Engine 之类）在注册表里往往仍是一个静态
文件，快照与肉眼所见会不一致；这类桌面建议关掉液态玻璃。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap

SOFT_DIV = 3            # 磨砂底图的分辨率除数（1/3 够用，省 9 倍内存）
REG_TTL = 0.4           # 注册表读取缓存（秒）——1 秒轮询一次版本号也不打紧

# WallpaperStyle 取值（HKCU\Control Panel\Desktop）
STYLE_CENTER = 0
STYLE_TILE = 1
STYLE_STRETCH = 2
STYLE_FIT = 6
STYLE_FILL = 10
STYLE_SPAN = 22


@dataclass(frozen=True)
class Wallpaper:
    """一整块虚拟桌面的壁纸（物理像素），带磨砂版。"""

    image: QImage            # 物理像素，覆盖 rect
    soft: QImage             # 物理像素 / SOFT_DIV，磨砂
    rect: QRect              # 逻辑坐标的虚拟桌面
    dpr: float
    version: int

    # ---------- 取用 ----------

    def crop(self, rect) -> QImage:
        """裁一块清晰底图（`rect` 为全局逻辑坐标）。"""
        return _crop(self.image, self.rect, self.dpr, rect, 1.0)

    def soft_crop(self, rect, size) -> QImage:
        """裁一块磨砂底图并放大到 `size`（物理像素）。"""
        r = _crop(self.soft, self.rect, self.dpr / SOFT_DIV, rect, 1.0)
        if r.isNull() or size is None:
            return r
        w, h = int(size[0]), int(size[1])
        if w < 1 or h < 1 or (r.width() == w and r.height() == h):
            return r
        return r.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def contains(self, rect) -> bool:
        return self.rect.contains(QRect(rect))


def _crop(img: QImage, base: QRect, dpr: float, rect, scale: float) -> QImage:
    """从整块底图裁 `rect`（逻辑）→ 物理像素。

    **尺寸严格等于请求尺寸**：超出图外的部分留透明，不做夹取。折射带要采
    表面外面一圈，夹取会让"表面原点"在底图里偏移，边缘图案整体错位。
    """
    if img.isNull():
        return QImage()
    rect = QRect(rect)
    if rect.isEmpty():
        return QImage()
    k = float(dpr) * float(scale)
    w = max(1, int(round(rect.width() * k)))
    h = max(1, int(round(rect.height() * k)))
    x = int(round((rect.x() - base.x()) * k))
    y = int(round((rect.y() - base.y()) * k))
    out = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    sx = max(0, x)
    sy = max(0, y)
    ex = min(img.width(), x + w)
    ey = min(img.height(), y + h)
    if ex > sx and ey > sy:
        p = QPainter(out)
        p.drawImage(sx - x, sy - y, img, sx, sy, ex - sx, ey - sy)
        p.end()
    return out


# ---------------------------------------------------------------- 注册表


def _registry() -> Tuple[str, int, bool, QColor]:
    """(壁纸路径, 填充方式, 是否平铺, 桌面纯色)。"""
    path, style, tile = "", STYLE_FILL, False
    bg = QColor(0, 0, 0)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Control Panel\Desktop") as k:
            try:
                path = str(winreg.QueryValueEx(k, "WallPaper")[0] or "")
            except OSError:
                path = ""
            try:
                raw = winreg.QueryValueEx(k, "WallpaperStyle")[0]
                style = int(str(raw).strip() or STYLE_FILL)
            except (OSError, ValueError):
                style = STYLE_FILL
            try:
                raw = winreg.QueryValueEx(k, "TileWallpaper")[0]
                tile = str(raw).strip() == "1"
            except OSError:
                tile = False
    except (ImportError, OSError):
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Control Panel\Colors") as k:
            raw = str(winreg.QueryValueEx(k, "Background")[0] or "")
            parts = [int(p) for p in raw.split()[:3]]
            if len(parts) == 3:
                bg = QColor(parts[0], parts[1], parts[2])
    except (ImportError, OSError, ValueError):
        pass
    return path, style, tile, bg


# ---------------------------------------------------------------- 组合


def _cover(p: QPainter, target: QRect, img: QImage) -> None:
    """等比放大到铺满（Fill）。"""
    if target.isEmpty():
        return
    s = max(target.width() / max(1, img.width()),
            target.height() / max(1, img.height()))
    w = max(1, int(round(img.width() * s)))
    h = max(1, int(round(img.height() * s)))
    p.drawImage(QRect(target.x() + (target.width() - w) // 2,
                      target.y() + (target.height() - h) // 2, w, h), img)


def _fit(p: QPainter, target: QRect, img: QImage) -> None:
    """等比缩小到装得下（Fit），多出的部分留纯色。"""
    if target.isEmpty():
        return
    s = min(target.width() / max(1, img.width()),
            target.height() / max(1, img.height()))
    w = max(1, int(round(img.width() * s)))
    h = max(1, int(round(img.height() * s)))
    p.drawImage(QRect(target.x() + (target.width() - w) // 2,
                      target.y() + (target.height() - h) // 2, w, h), img)


def _tile(p: QPainter, target: QRect, img: QImage) -> None:
    p.save()
    p.setClipRect(target)
    for y in range(target.top(), target.bottom() + 1, max(1, img.height())):
        for x in range(target.left(), target.right() + 1, max(1, img.width())):
            p.drawImage(x, y, img)
    p.restore()


def _compose(path: str, style: int, tile: bool, bg: QColor,
             rect: QRect, dpr: float) -> Optional[QImage]:
    img = QImage(path) if path else QImage()
    if img.isNull():
        return None
    w = max(1, int(round(rect.width() * dpr)))
    h = max(1, int(round(rect.height() * dpr)))
    canvas = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(bg)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    try:
        if style == STYLE_SPAN:
            _cover(p, QRect(0, 0, w, h), img)
        else:
            screens = QGuiApplication.screens() or []
            if not screens:
                _cover(p, QRect(0, 0, w, h), img)
            for s in screens:
                g = s.geometry()
                sr = QRect(int(round((g.x() - rect.x()) * dpr)),
                           int(round((g.y() - rect.y()) * dpr)),
                           max(1, int(round(g.width() * dpr))),
                           max(1, int(round(g.height() * dpr))))
                if sr.isEmpty():
                    continue
                if tile:
                    _tile(p, sr, img)
                elif style == STYLE_STRETCH:
                    p.drawImage(sr, img)
                elif style == STYLE_FIT:
                    _fit(p, sr, img)
                elif style == STYLE_CENTER:
                    p.save()
                    p.setClipRect(sr)
                    p.drawImage(sr.center().x() - img.width() // 2,
                                sr.center().y() - img.height() // 2, img)
                    p.restore()
                else:                       # FILL / 未知 -> 填满
                    _cover(p, sr, img)
    finally:
        p.end()
    return canvas


def _soft(image: QImage) -> QImage:
    """磨砂版：先缩到 1/3 再模糊（内存与耗时都省一个数量级）。

    模糊半径要在 1/3 尺度上调小：`blur_pixmap` 是"缩小 -> 模糊 -> 放大"，
    有效半径 = radius × downscale × SOFT_DIV。这里取 1.0 × 1 × 3 ≈ 3px
    ——只把壁纸柔化一点点，图案仍然看得出来（再糊就成一块奶白板了）。
    """
    if image.isNull():
        return QImage()
    w = max(8, image.width() // SOFT_DIV)
    h = max(8, image.height() // SOFT_DIV)
    small = image.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    try:
        from . import effects
        pm = effects.blur_pixmap(QPixmap.fromImage(small), blur_radius=1.0,
                                 downscale=1)
        if not pm.isNull():
            return pm.toImage().convertToFormat(
                QImage.Format_ARGB32_Premultiplied)
    except Exception:        # noqa: BLE001 —— 模糊失败就退回缩放版
        pass
    return small.convertToFormat(QImage.Format_ARGB32_Premultiplied)


def _desktop_rect() -> QRect:
    rect = QRect()
    for s in QGuiApplication.screens() or []:
        rect = rect.united(s.geometry())
    return rect


def _dpr() -> float:
    screen = QGuiApplication.primaryScreen()
    try:
        return float(screen.devicePixelRatio()) if screen else 1.0
    except (AttributeError, RuntimeError):
        return 1.0


# ---------------------------------------------------------------- 缓存

_CACHE = {"key": None, "wall": None, "t": 0.0}
_KEYCACHE = {"key": None, "t": 0.0}


def _key() -> tuple:
    now = time.monotonic()
    if _KEYCACHE["key"] is not None and now - _KEYCACHE["t"] < REG_TTL:
        return _KEYCACHE["key"]
    path, style, tile, bg = _registry()
    mtime = 0
    size = 0
    if path:
        try:
            st = os.stat(path)
            mtime, size = st.st_mtime_ns, st.st_size
        except OSError:
            pass
    rect = _desktop_rect()
    key = (path, mtime, size, style, tile, bg.rgb(),
           rect.x(), rect.y(), rect.width(), rect.height(), round(_dpr(), 3))
    _KEYCACHE["key"] = key
    _KEYCACHE["t"] = now
    return key


def version() -> int:
    """底图版本号（变了 = 壁纸/布局/DPR 变了）。"""
    return hash(_key()) & 0x7FFFFFFF


def snapshot(refresh: bool = False) -> Optional[Wallpaper]:
    """当前壁纸快照（不带缓存命中的话重建一次）。"""
    key = _key()
    if not refresh and _CACHE["key"] == key and _CACHE["wall"] is not None:
        return _CACHE["wall"]
    path, style, tile, bg = _registry()
    rect = _desktop_rect()
    dpr = _dpr()
    if not path or rect.isEmpty():
        _CACHE.update(key=key, wall=None, t=time.monotonic())
        return None
    image = _compose(path, style, tile, bg, rect, dpr)
    if image is None or image.isNull():
        _CACHE.update(key=key, wall=None, t=time.monotonic())
        return None
    wall = Wallpaper(image=image, soft=_soft(image), rect=QRect(rect),
                     dpr=dpr, version=hash(key) & 0x7FFFFFFF)
    _CACHE.update(key=key, wall=wall, t=time.monotonic())
    return wall


def invalidate() -> None:
    """丢掉缓存（测试 / 手动换壁纸后强制重建）。"""
    _CACHE.update(key=None, wall=None, t=0.0)
    _KEYCACHE.update(key=None, t=0.0)
