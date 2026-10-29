# -*- coding: utf-8 -*-
"""图标标签文字样式 + 拖拽 cell 预览框样式（v14 反馈 3 / 反馈 13）。

## 为什么单独一个模块

标签原来有三条绘制路径（桌面格 `layout` / 文件夹紧凑格 `compact` / 旧
chrome 路径），各写一份 `theme.LABEL_COLOR` + 一层阴影；预览框
（`desktop_window._paint_highlight`）又是另一份硬编码红。「改样式」要改
四处，必然漂移（v7 双源漂移的教训）。这里收成**纯函数层**：读设置 ->
返回 NamedTuple，绘制只认返回值，设置面板也只写这几个键。

## 默认值 = 1:1 还原 Windows 原生（反馈 3）

Windows 桌面图标标签：**白字 + 一层柔和黑阴影**，无描边、无加粗。
`LabelStyle` / `PreviewStyle` 的缺省即这套；用户改过的值从 storage 读，
**读不到 / 脏值一律回退默认**（与 `dock.py` 的 `_clamped_int` 同一哲学：
设置面板只保证"能改"，不保证"改不坏"）。

## 键位一览（全部走 storage，无第二份真相）

    label_color / label_bold
    label_shadow / label_shadow_color / label_shadow_alpha（0~100）
    label_outline / label_outline_color / label_outline_width（1~3）
    cell_preview_color（"" = 跟随 theme.DANGER）
    cell_preview_fill（0~40）/ cell_preview_width（1~4）/ cell_preview_radius（0~16）
"""
from __future__ import annotations

from typing import NamedTuple

from PySide6.QtGui import QColor

from . import theme

# ---------------------------------------------------------------- 常量

DEFAULT_LABEL_COLOR = "#f4f5f7"        # = theme.LABEL_COLOR（白字，Windows 原生）
DEFAULT_SHADOW_COLOR = "#000000"
DEFAULT_SHADOW_ALPHA = 75              # %（190/255 ≈ 74%，取整到 75 同观感）
DEFAULT_OUTLINE_COLOR = "#000000"
DEFAULT_OUTLINE_WIDTH = 1

DEFAULT_PREVIEW_FILL = 8               # %（现行 20/255 ≈ 8%）
DEFAULT_PREVIEW_WIDTH = 2
DEFAULT_PREVIEW_RADIUS = 8

LABEL_SHADOW_MIN, LABEL_SHADOW_MAX = 0, 100
FONT_SCALE_MIN, FONT_SCALE_MAX = 70, 140      # %（v14q 图标字号调节）
DEFAULT_FONT_SCALE = 100
LABEL_OUTLINE_MIN, LABEL_OUTLINE_MAX = 1, 3
PREVIEW_FILL_MIN, PREVIEW_FILL_MAX = 0, 40
PREVIEW_WIDTH_MIN, PREVIEW_WIDTH_MAX = 1, 4
PREVIEW_RADIUS_MIN, PREVIEW_RADIUS_MAX = 0, 16

# 预览框预设（反馈 13）：一键换色 = 只写 cell_preview_color 一个键，
# 浓度/粗细/圆角是独立旋钮，不跟着预设跳（用户调好的不被覆盖）。
# 前两个走 theme token（ACCENT / DANGER 已存在），后两个按 FOLDER_TINTS
# 的惯例就地定义——状态色不该为一个预设往 theme 里加全局 token。
PREVIEW_PRESETS = (
    ("danger", "红", QColor(theme.DANGER)),
    ("accent", "蓝", QColor(theme.ACCENT)),
    ("success", "绿", QColor(88, 186, 120)),
    ("warn", "橙", QColor(240, 152, 62)),
)
_PRESET_BY_KEY = {k: c for k, _l, c in PREVIEW_PRESETS}


# ---------------------------------------------------------------- 数据结构

class LabelStyle(NamedTuple):
    """图标标签绘制样式（逻辑值，绘制前再乘 ui_scale）。"""

    color: QColor
    bold: bool
    shadow: bool
    shadow_color: QColor
    shadow_alpha: float          # 0~1
    outline: bool
    outline_color: QColor
    outline_width: int           # 1~3
    font_scale: float = 1.0      # 字号缩放（v14q：0.7~1.4，基准=图标档字号）


class PreviewStyle(NamedTuple):
    """拖拽落点预览框样式（`desktop_window._paint_highlight` 唯一数据源）。"""

    color: QColor
    fill_alpha: float            # 0~1
    width: int                   # 1~4
    radius: int                  # 0~16
    enabled: bool = True         # False = 拖动时不画落点指示框


class HoverStyle(NamedTuple):
    """图标 hover 高亮样式（v14j 反馈：可自定义、可关）。

    `AppIcon.paintEvent` 的 `_hover` 分支唯一数据源；颜色 `""` = 跟随
    theme.HOVER_BORDER / theme.HOVER（原内置白）。动画幅度 100 = 关闭
    放大（hover 仍画高亮底，只是不缩放）。
    """

    color: QColor                # "" -> theme.HOVER_BORDER 同源白
    fill_alpha: float            # 0~1（默认 20/255）
    enabled: bool = True         # False = hover 不画高亮底/边
    scale_pct: int = 104         # hover 放大幅度 %（100 = 不放大）
    anim_ms: int = 140           # hover 动画时长 ms（80~400）
    color_custom: bool = False   # True = 用户自定义过描边色（v14n）


# ---------------------------------------------------------------- 读取助手

def _color(raw, fallback: str) -> QColor:
    """解析 "#RRGGBB"；空 / None / 非法一律回退（绝不留下透明或乱色）。"""
    c = QColor(str(raw or "").strip())
    return c if c.isValid() else QColor(fallback)


def _flag(storage, key: str, default: bool) -> bool:
    """布尔设置：只认真布尔 / 0 / 1；字符串（"yes"/"true"）一律回默认。

    Python 的 `bool("false")` 是 True——脏字符串直接当真会把开关悄悄
    焊死，所以这里显式白名单。
    """
    try:
        v = storage.get_setting(key)
    except Exception:      # noqa: BLE001
        return default
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return bool(v)
    return default


def _int(storage, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(storage.get_setting(key))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _pct(storage, key: str, default: int) -> float:
    """0~100 的百分设置 -> 0~1 浮点（夹紧 + 脏值回退）。"""
    return _int(storage, key, default, LABEL_SHADOW_MIN, LABEL_SHADOW_MAX) / 100.0


# ---------------------------------------------------------------- 对外读取

def default_label_style() -> LabelStyle:
    """无 storage 时的缺省（探针 / 旧调用 / 单元测试）。"""
    return LabelStyle(
        color=QColor(DEFAULT_LABEL_COLOR), bold=False, shadow=True,
        shadow_color=QColor(DEFAULT_SHADOW_COLOR),
        shadow_alpha=DEFAULT_SHADOW_ALPHA / 100.0,
        outline=False, outline_color=QColor(DEFAULT_OUTLINE_COLOR),
        outline_width=DEFAULT_OUTLINE_WIDTH)


def font_scale_of(storage) -> float:
    """图标标签字号缩放倍数（0.7~1.4）。

    **唯一来源**：绘制（`AppIcon._scale_font_px`）与布局
    （`grid_sizing.icon_layout(font_scale=…)`）都读它——两处各取一次设置就
    会出现"预留按 100%、画的是 140%"的错位（v15a 用户反馈的裁剪 bug）。
    无 storage（探针 / 旧调用）时按 1.0。
    """
    if storage is None:
        return 1.0
    return _int(storage, "label_font_scale", DEFAULT_FONT_SCALE,
                FONT_SCALE_MIN, FONT_SCALE_MAX) / 100.0


def label_style(storage) -> LabelStyle:
    """按当前设置求标签样式（**唯一入口**，绘制层不许自己拼颜色）。"""
    if storage is None:
        return default_label_style()
    return LabelStyle(
        color=_color(storage.get_setting("label_color"), DEFAULT_LABEL_COLOR),
        bold=_flag(storage, "label_bold", False),
        shadow=_flag(storage, "label_shadow", True),
        shadow_color=_color(storage.get_setting("label_shadow_color"),
                            DEFAULT_SHADOW_COLOR),
        shadow_alpha=_pct(storage, "label_shadow_alpha",
                          DEFAULT_SHADOW_ALPHA),
        outline=_flag(storage, "label_outline", False),
        outline_color=_color(storage.get_setting("label_outline_color"),
                             DEFAULT_OUTLINE_COLOR),
        outline_width=_int(storage, "label_outline_width",
                           DEFAULT_OUTLINE_WIDTH,
                           LABEL_OUTLINE_MIN, LABEL_OUTLINE_MAX),
        font_scale=font_scale_of(storage))


def default_preview_style() -> PreviewStyle:
    """预览框缺省 = 现行红框（theme.DANGER + 8% 填充 + 2px + R8）。"""
    return PreviewStyle(
        color=QColor(theme.DANGER), fill_alpha=DEFAULT_PREVIEW_FILL / 100.0,
        width=DEFAULT_PREVIEW_WIDTH, radius=DEFAULT_PREVIEW_RADIUS)


def preview_style(storage) -> PreviewStyle:
    """按当前设置求预览框样式（唯一入口）。"""
    if storage is None:
        return default_preview_style()
    raw = str(storage.get_setting("cell_preview_color") or "").strip()
    color = _color(raw, QColor(theme.DANGER).name())
    return PreviewStyle(
        color=color,
        fill_alpha=_int(storage, "cell_preview_fill", DEFAULT_PREVIEW_FILL,
                        PREVIEW_FILL_MIN, PREVIEW_FILL_MAX) / 100.0,
        width=_int(storage, "cell_preview_width", DEFAULT_PREVIEW_WIDTH,
                   PREVIEW_WIDTH_MIN, PREVIEW_WIDTH_MAX),
        radius=_int(storage, "cell_preview_radius", DEFAULT_PREVIEW_RADIUS,
                    PREVIEW_RADIUS_MIN, PREVIEW_RADIUS_MAX),
        enabled=_flag(storage, "cell_preview_enabled", True))


def apply_preview_preset(storage, name: str) -> bool:
    """一键预设：只写 `cell_preview_color`。返回是否写成功（非法名不动盘）。"""
    c = _PRESET_BY_KEY.get(str(name or ""))
    if c is None:
        return False
    storage.set_setting("cell_preview_color", c.name())
    return True


# hover 高亮（v14j）
HOVER_SCALE_MIN, HOVER_SCALE_MAX = 100, 112
HOVER_ANIM_MIN, HOVER_ANIM_MAX = 80, 400
DEFAULT_HOVER_SCALE = 104            # AppIcon enterEvent 的原字面量
DEFAULT_HOVER_ANIM = 140


def hover_style(storage) -> HoverStyle:
    """按当前设置求图标 hover 高亮样式（唯一入口）。"""
    if storage is None:
        return HoverStyle(color=QColor(255, 255, 255, 36),
                          fill_alpha=20 / 255.0,
                          scale_pct=DEFAULT_HOVER_SCALE,
                          anim_ms=DEFAULT_HOVER_ANIM)
    raw = str(storage.get_setting("icon_hover_color") or "").strip()
    return HoverStyle(
        color=_color(raw, QColor(255, 255, 255, 36).name()),
        color_custom=bool(raw),
        fill_alpha=_int(storage, "icon_hover_fill", 8, 0, 40) / 100.0,
        enabled=_flag(storage, "icon_hover_enabled", True),
        scale_pct=_int(storage, "icon_hover_scale", DEFAULT_HOVER_SCALE,
                       HOVER_SCALE_MIN, HOVER_SCALE_MAX),
        anim_ms=_int(storage, "icon_hover_anim", DEFAULT_HOVER_ANIM,
                     HOVER_ANIM_MIN, HOVER_ANIM_MAX))


# ---------------------------------------------------------------- 绘制助手

def draw_label_text(p: QPainter, rect, flags, text: str, style: LabelStyle,
                    font_px: int):
    """按样式画一段标签文字（阴影 -> 描边 -> 文字，三层顺序固定）。

    三层顺序不能换：描边必须压在阴影上（否则阴影把描边糊成一团），文字
    压在描边上。文件夹紧凑格 / 桌面格 / 旧路径共用这一个函数——"改样式"
    从此只有一处。

    描边用"八方向偏移重画"实现（QPainter 没有文字描边 API）：粗细 1/2/3
    对应 1/2/3 px 的偏移量，代价是每段文字多画 8 遍，标签量级（一屏几十
    个）完全无感。
    """
    if not text:
        return
    # v15f：标签走 label_font（Segoe UI 优先）——系统桌面就是这套字形，
    # 用雅黑打头会把西文画宽一圈、看起来"不像原版"。
    p.setFont(theme.label_font(font_px, bold=style.bold))
    if style.shadow:
        # 原生观感 = 一圈很淡的暗晕 + 一道 (+1,+1) 主阴影。
        # v15f：晕的浓度从 0.55 降到 0.30——0.55 时四周那圈黑把字形"喂胖"
        # 了一圈，用户对比系统桌面时看到的就是"字体不对/笔画太粗"。
        # 主阴影保持全浓度，保证浅色壁纸上依然读得清。
        alpha = max(0.0, min(1.0, style.shadow_alpha))
        halo = QColor(style.shadow_color)
        halo.setAlphaF(alpha * 0.30)
        p.setPen(halo)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(rect.adjusted(dx, dy, dx, dy), flags, text)
        sc = QColor(style.shadow_color)
        sc.setAlphaF(alpha)
        p.setPen(sc)
        p.drawText(rect.adjusted(1, 1, 0, 0), flags, text)
    if style.outline:
        oc = QColor(style.outline_color)
        p.setPen(oc)
        w = max(LABEL_OUTLINE_MIN, min(LABEL_OUTLINE_MAX, style.outline_width))
        for dx in (-w, 0, w):
            for dy in (-w, 0, w):
                if dx or dy:
                    p.drawText(rect.adjusted(dx, dy, dx, dy), flags, text)
    p.setPen(QColor(style.color))
    p.drawText(rect, flags, text)
