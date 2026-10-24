# -*- coding: utf-8 -*-
"""组件外观样式：把"用户配的颜色/透明度/圆角"翻译成一套绘制用调色板（v9 V1）。

为什么单独一层而不是让每个组件自己读 `entry["settings"]`：
  - 7 个组件 × 5 个字段 × 缺失回退 = 一份必然写歪的重复逻辑；
  - 派生规则（次文字 = 主文字 72%、弱文字 = 52%）只该有一处；
  - 组件库预览不实例化组件，也要能复现同样的配色 —— 必须能脱离控件求值。

字段由 `widget_registry` 自动追加到**每个**组件的 `settings` 里
（见 `_build_registry`），所以"有没有外观设置"不是组件的责任。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from PySide6.QtGui import QColor

from . import theme

# 字段声明住在 `widget_registry.STYLE_FIELDS`（所有组件共享同一份）。
# 这里**不能**在模块级 import 它：widget_glass → widget_style 是模块级
# 依赖，而注册表构造时要 import 组件模块（→ widget_glass），成环。
# 需要字段的地方走这个延迟访问器。

# 不透明度下限：再低文字就看不清了（0 = 完全透明没有意义）
MIN_ALPHA = 0.2
MAX_ALPHA = 1.0


def _color(text: Any, fallback: QColor) -> QColor:
    """把 `#RRGGBB` / `#AARRGGBB` 解析成 QColor；非法或空 -> 回退。"""
    if not isinstance(text, str):
        return QColor(fallback)
    s = text.strip()
    if not s:
        return QColor(fallback)
    c = QColor(s)
    if not c.isValid():
        return QColor(fallback)
    return c


def _shade(c: QColor, alpha_mul: float) -> QColor:
    """同色不同透明度（次/弱文字的派生方式：不掺灰，保持色相）。"""
    out = QColor(c)
    out.setAlphaF(max(0.0, min(1.0, c.alphaF() * alpha_mul)))
    return out


def _flag(value, default: bool) -> bool:
    """只认真布尔 / 0/1 / 'true'/'false'（`bool("false")` 是 True 的坑）。"""
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


def _has_color(raw) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        return QColor(raw.strip()).isValid()
    except (TypeError, ValueError):
        return False


def liquid_enabled(settings: Optional[Dict]) -> bool:
    """背景是不是液态玻璃（v14 反馈 10：液态玻璃 = 一种"配色"）。

    与背景色同级、互斥：
      * 显式写了 `bg_liquid` -> 听它的；
      * 没写 -> 设过背景色的走颜色（尊重老用户的选择），没设过的走液态。

    这条推导是 `resolve()` 与设置面板共用的**唯一**入口——两边各写一次
    就会出现"面板显示开着、实际画的是颜色"。
    """
    s = settings if isinstance(settings, dict) else {}
    raw = s.get("bg_liquid")
    if raw is not None:
        return _flag(raw, True)
    return not _has_color(s.get("bg_color"))


@dataclass(frozen=True)
class Style:
    """一次绘制需要的全部颜色/尺寸（不可变，可安全缓存）。"""

    bg: Optional[QColor]        # None = 主题玻璃底色
    alpha: float                # 背景不透明度倍数（MIN_ALPHA~1.0）
    text: QColor
    dim: QColor
    faint: QColor
    accent: QColor
    radius: float
    custom_bg: bool = False     # 用户是否指定了背景色（预览/诊断用）
    liquid: bool = False        # 背景 = 液态玻璃（与背景色互斥，v14 反馈 10）

    @property
    def tint_alpha(self) -> int:
        """传给 `paint_glass()` 的 tint 不透明度（0~255）。"""
        a = int(round(theme.GLASS_TINT_ALPHA * self.alpha))
        return max(6, min(255, a))


DEFAULT_STYLE = Style(bg=None, alpha=1.0, text=QColor(theme.TEXT),
                      dim=QColor(theme.TEXT_DIM), faint=QColor(theme.TEXT_FAINT),
                      accent=QColor(theme.ACCENT), radius=float(theme.GLASS_RADIUS),
                      liquid=True)


def _alpha(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(MIN_ALPHA, min(MAX_ALPHA, v))


def _radius(value: Any) -> float:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return float(theme.GLASS_RADIUS)
    if v <= 0:
        return float(theme.GLASS_RADIUS)
    return float(max(2, min(40, v)))


def resolve(settings: Optional[Dict]) -> Style:
    """从 `entry["settings"]` 求出一套 Style（缺项走主题默认）。"""
    s = settings if isinstance(settings, dict) else {}

    text_raw = s.get("text_color")
    if isinstance(text_raw, str) and text_raw.strip():
        text = _color(text_raw, theme.TEXT)
        text.setAlpha(255)                       # 文字不许半透明（可读性优先）
        dim = _shade(text, 0.72)
        faint = _shade(text, 0.52)
    else:
        text = QColor(theme.TEXT)
        dim = QColor(theme.TEXT_DIM)
        faint = QColor(theme.TEXT_FAINT)

    bg_raw = s.get("bg_color")
    bg = None
    custom_bg = False
    if isinstance(bg_raw, str) and bg_raw.strip():
        probe = QColor(bg_raw.strip())
        if probe.isValid():          # 非法颜色 = 未设置（不把脏字符串当定制）
            probe.setAlpha(255)      # 透明度统一由 alpha 字段控制
            bg = probe
            custom_bg = True

    accent = _color(s.get("accent_color"), theme.ACCENT)
    accent.setAlpha(255)

    liquid = liquid_enabled(s)
    if liquid:
        # 液态玻璃与背景色互斥：选了液态就不再叠任何 tint
        bg = None
        custom_bg = False

    return Style(bg=bg, alpha=_alpha(s.get("bg_alpha", 1.0)), text=text,
                 dim=dim, faint=faint, accent=accent,
                 radius=_radius(s.get("radius")), custom_bg=custom_bg,
                 liquid=liquid)


def of(entry: Optional[dict]) -> Style:
    """便捷入口：直接吃 entry（组件里最常见的形式）。"""
    if not isinstance(entry, dict):
        return DEFAULT_STYLE
    return resolve(entry.get("settings"))


def css(color: QColor, alpha: Optional[float] = None) -> str:
    """给 QSS 用的 rgba() 字符串（便签输入框等处需要）。"""
    c = QColor(color)
    a = c.alphaF() if alpha is None else max(0.0, min(1.0, float(alpha)))
    return "rgba(%d,%d,%d,%.3f)" % (c.red(), c.green(), c.blue(), a)


def style_fields():
    """通用外观字段声明（延迟到调用时取，避开 widget_glass ↔ registry 循环）。"""
    from .widget_registry import STYLE_FIELDS
    return STYLE_FIELDS
