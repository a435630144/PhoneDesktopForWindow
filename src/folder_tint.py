# -*- coding: utf-8 -*-
"""文件夹背景色解析（M5，指导文档 §4.2「背景色」，决策 D5）。

entry["tint"] 存 "#RRGGBB"；图标渐变（AppIcon._paint_folder）与弹窗
背板共用同一颜色（iOS 文件夹语言）。分辨率优先级：
  1. entry 显式 tint（用户在色板选的）
  2. tint == "auto" -> 读 entry["tint_auto"]（成员平均色缓存）
  3. 预设第一项（theme.FOLDER_TINTS[0]）

auto_tint：成员图标平均色——各成员 pixmap 缩到 1×1 取均值（指导文档
原文实现）。**「自动」必须作为状态保存**（`tint = "auto"`），算出的颜色
只进 `tint_auto` 缓存：旧实现把具体色写进 tint，成员增删后颜色再也不变，
色板也认不出"当前是自动"（v15e 修）。
"""
from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtGui import QColor

from . import theme

# 色板里的"液态玻璃"选项（v14 反馈 10）：液态玻璃与颜色**同级、互斥**，
# 存在同一个 `entry["tint"]` 字段里——选它就是选了一种"颜色"。
LIQUID = "liquid"
# 「自动」：不是颜色，是"按成员平均色算"的状态。算出的色进 tint_auto。
AUTO = "auto"

# 液态玻璃文件夹的图标底色（不再是 tint 渐变，而是一块半透明玻璃）
GLASS_FILL = QColor(255, 255, 255, 26)
GLASS_EDGE = QColor(255, 255, 255, 92)


def folder_liquid(entry: dict) -> bool:
    """这个文件夹的背景是不是液态玻璃。"""
    return str(entry.get("tint") or "").strip().lower() == LIQUID


def folder_auto(entry: dict) -> bool:
    """这个文件夹是不是「自动」配色（颜色随成员变化重算）。"""
    return str(entry.get("tint") or "").strip().lower() == AUTO


def folder_tint(entry: dict) -> QColor:
    """entry 的文件夹背景色（显式 tint 优先，缺省预设第一项）。

    液态玻璃时返回白色（图标/弹窗的选中态、hover 描边都拿它当基色）。
    """
    raw = entry.get("tint")
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low == LIQUID:
            return QColor(255, 255, 255)
        if low == AUTO:
            cached = QColor(str(entry.get("tint_auto") or ""))
            return cached if cached.isValid() else theme.FOLDER_TINTS[0]
        c = QColor(raw)
        if c.isValid():
            return c
    return theme.FOLDER_TINTS[0]


def _avg_color(pixmaps: List) -> Optional[QColor]:
    """一批 pixmap 的平均色（Qt 的 scaled(1,1) 自带区域平均，比手算稳）。"""
    rs = gs = bs = n = 0
    for pm in pixmaps:
        if pm is None or pm.isNull():
            continue
        one = pm.scaled(1, 1)
        img = one.toImage()
        if img.isNull():
            continue
        px = img.pixelColor(0, 0)
        rs += px.red()
        gs += px.green()
        bs += px.blue()
        n += 1
    if not n:
        return None
    return QColor(rs // n, gs // n, bs // n)


def auto_tint(apps: list, pixmap_fn: Optional[Callable] = None,
              size: int = 32) -> Optional[QColor]:
    """成员图标平均色（「自动」选项）。

    pixmap_fn(app, size, dpr) -> QPixmap 可注入（单测）；缺省用
    appsource.icon_pixmap。无成员或取不到任何图标时返回 None
    （调用方保持现色）。
    """
    if not apps:
        return None
    if pixmap_fn is None:
        from . import appsource  # 延迟导入：appsource 依赖 theme
        pixmap_fn = lambda app, s: appsource.icon_pixmap(app, s, 1.0)  # noqa: E731
    try:
        pms = [pixmap_fn(a, size) for a in apps]
    except Exception:
        return None
    return _avg_color(pms)
