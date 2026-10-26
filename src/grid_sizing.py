# -*- coding: utf-8 -*-
"""网格尺寸体系 —— v7 两层模型（指导文档_v7.md §2.1 / §3.1 / §3.2）。

## 层一 · 格距（cell）：只由**原生图标边长**决定，与档位无关

    widget_w0 = native_px + 2*CHROME_X
    label_h0  = max(LABEL_H_MIN, round(_font_for_box(native_px)*1.5) + 4)
    widget_h0 = native_px + label_h0
    cell_w    = round(widget_w0 / FIT_BASE)      # FIT_BASE = 0.74
    cell_h    = round(widget_h0 / FIT_BASE)

`FIT_BASE=0.74` 直接沿用 v6 `TIERS["medium"]`，所以**升级后格距与 v6 一模一样**
（本机 native 48 -> 84x91），用户的 `col/row` 零迁移。

v6 的病根是 `cell_for_native(native_px, tier)` 把档位除进了 cell —— 切档位
= 改格距 = 改行列数。v7 起档位不再参与 cell。

## 层二 · 图标占比（icon_fit）：只改图标本体

    icon_box = round(cell_w * icon_fit)          # icon_fit ∈ [0.30, 0.90]

三档是这个比例的预设，「中」档 = `native_px / cell_w`（本机 0.571），
恰好等于原生桌面图标边长 —— 这是"图标跟原桌面一致"的回归锁。

    TIER_FIT          = {"small": 0.42, "medium": None, "large": 0.82}
    TIER_FIT_FALLBACK = {"small": 0.42, "medium": 0.57, "large": 0.82}

`medium` 的 None 表示"跟随 native"（换屏换缩放自动跟随）；无 native 信息时
（旧调用点只传档位串）退回 FALLBACK。

## 行列：列数改 cell，行数只裁剪

    cols_auto = clamp(UW // cw0, 1, MAX_COLS)
    rows_auto = clamp(UH // ch,  1, MAX_ROWS)     # 必须在 ch 定稿之后算

改列数 -> `cw = UW // cols`（floor，保证不溢出）-> `ch = cw*aspect` -> 行上限
随之变小；改行数**一个像素都不动 cell**，只是网格只用前 rows 行。

## 兼容

`cell_for_native(native_px, tier)` 保留签名但**忽略 tier**（deprecated，
v7.1 删）；`icon_layout(cw, ch, "medium")` 等旧调用零改动（走 FALLBACK）。
`cell_for_screen()` 降级为兜底（非 Windows / 读注册表失败时用）。
"""
from __future__ import annotations

from typing import NamedTuple, Optional, Tuple

# ---------------------------------------------------------------- 兜底公式常数
REF_SHORT = 1080.0
CELL_REF = (78.0, 96.0)
K_MIN, K_MAX = 0.7, 1.8

# ---------------------------------------------------------------- 两条主线常数
FIT_BASE = 0.74          # 中档 widget 占格（沿用 v6 TIERS["medium"]）
FIT_MIN, FIT_MAX = 0.30, 0.90

# v6 遗留：widget 整体占格（仅 cell_for_screen 兜底路径与旧文档引用）
TIERS = {"small": 0.58, "medium": 0.74, "large": 1.0}
DEFAULT_TIER = "medium"

# v7：档位 = 图标在格子里的占比；medium 的 None = 跟随 native_px/cell_w
TIER_FIT = {"small": 0.42, "medium": None, "large": 0.82}
TIER_FIT_FALLBACK = {"small": 0.42, "medium": 0.57, "large": 0.82}

# v7 B2（反馈 9）：第四档「自定义」——占比由用户滑条（30%~90%）决定，
# 存在 settings["icon_fit"]；档位本身只回答"这一档是不是用户手调的"。
# 单独一张白名单而不是往 TIERS 里塞：TIERS 是 v6 遗留的 **widget 占格**
# 表（cell_for_screen 等旧路径还要按档位取值），塞进去会 KeyError。
TIER_CUSTOM = "custom"
TIER_NAMES = ("small", "medium", "large", TIER_CUSTOM)
TIER_FIT[TIER_CUSTOM] = None                    # 实际值由 storage.icon_fit 提供
TIER_FIT_FALLBACK[TIER_CUSTOM] = 0.57           # 无 icon_fit 时的兜底

# 行列上限：cell 只能变大不能变小，所以自动值就是上限
MIN_COLS, MAX_COLS = 1, 24
MIN_ROWS, MAX_ROWS = 1, 12

# 默认边距（逻辑 px，调用方按 chrome_scale 缩放后传入）
EDGE_X, EDGE_Y = 26, 22

# 规格表（@cell 83x102）锁死的派生常数：
CHROME_X = 7            # 图标方块左右留白 = widget_w - icon_box 的一半
ICON_BOX_MIN = 16       # 小 cell 下图标方块下限
LABEL_H_MIN = 10        # 标签区下限（单行小字）
FONT_BOX_TIERS = ((40, 9), (58, 10))   # (icon_box 上界, font_px)，其余 14
# 反馈 12：文件名默认折两行。第二行的行距比单行松（单行那 1.5 倍里含上下
# 留白，第二行只补行距）。cell 装不下第二行时自动退回单行——格子尺寸是
# v6 规格（84x91），不能为了标签把格距撑大。
LABEL_LINES = 2
LABEL_LINE_FACTOR = 1.28


class IconLayout(NamedTuple):
    """一次图标 widget 尺寸方案（逻辑像素）。"""

    widget_w: int
    widget_h: int
    icon_box: int       # 图标方块边长（pixmap 绘制区）
    chrome_x: int       # 图标方块左侧留白（居中：(widget_w-icon_box)/2）
    label_h: int        # 标签区高度（widget_h - icon_box）
    font_px: int        # 标签字号（单行 elide）


class Dims(NamedTuple):
    """一次网格几何求解结果（逻辑像素）。

    cols/rows/cw/ch/ox/oy 与 GridMetrics 同义；cols_auto/rows_auto 是
    **只读**上限（供设置页显示"1 .. N"，也供 clamp 用）。
    """

    cols: int
    rows: int
    cw: int
    ch: int
    cols_auto: int
    rows_auto: int
    ox: int
    oy: int


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _clampi(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


def cell_for_screen(W: int, H: int) -> Tuple[int, int]:
    """屏幕工作区尺寸 -> cell（cw, ch），短边归一化 + 密度跨设备一致。

    **v7 起降级为兜底**（非 Windows / 读不到原生图标尺寸时用）：
    默认路径走 `cell_from_native()`，因为"图标与原桌面一致"优先于
    "跨设备每屏格数密度一致"（用户 2026-10-20 明确取舍）。
    """
    short = max(1, min(int(W), int(H)))
    k = _clamp(short / REF_SHORT, K_MIN, K_MAX)
    return int(round(CELL_REF[0] * k)), int(round(CELL_REF[1] * k))


def parse_tier(raw) -> str:
    """档位名归一：小/中/大/自定义四者之一；None/空/非法 -> 默认 medium。

    v7 B2 起 `custom` 是**合法档位**（反馈 9）：它表示"占比由用户滑条
    手调"，实际比例存在 `settings["icon_fit"]`，不在档位映射表里。
    """
    if isinstance(raw, str):
        t = raw.strip().lower()
        if t in TIER_NAMES:
            return t
    return DEFAULT_TIER


def _font_for_box(icon_box: int) -> int:
    for upper, px in FONT_BOX_TIERS:
        if icon_box <= upper:
            return px
    return 14


def scaled_font_px(font_px: int, font_scale: float = 1.0) -> int:
    """基准字号 × 用户字号缩放（夹 9~40）。

    与 `AppIcon._scale_font_px` **同一条规则**：布局预留的标签带高度必须
    按真正要画的那个字号算，否则字号一调大，文字就长出预留区（v15a 用户
    反馈"第二行被 hover 框裁剪"的根因——布局不知道有个缩放旋钮）。
    """
    try:
        k = float(font_scale)
    except (TypeError, ValueError):
        k = 1.0
    return max(9, min(40, int(round(int(font_px) * k))))


def label_height(icon_box: int, lines: int = 1,
                 font_scale: float = 1.0) -> int:
    """标签区高度（与 `AppIcon._paint_label` 同源）。

    `lines=1` 保持 v6 以来的单行规格（`cell_from_native` 只用它，格距不变）；
    `lines=2` 追加第二行的行距——反馈 12「文件名一行显示不下折两行」。
    `font_scale`（v15a）：用户字号缩放（0.7~1.4），标签带同比长高。
    """
    font = scaled_font_px(_font_for_box(icon_box), font_scale)
    h = max(LABEL_H_MIN, int(round(font * 1.5)) + 4)
    for _ in range(max(0, int(lines) - 1)):
        h += int(round(font * LABEL_LINE_FACTOR))
    return h


# ---------------------------------------------------------------- 层一：cell

def cell_from_native(native_px: int) -> Tuple[int, int]:
    """原生图标边长 -> cell（cw, ch）。**不含档位**（v7 §2.1 层一）。

    反解而不是猜常数：icon_box = native_px 时 widget 有多大，再除以
    FIT_BASE 得到"刚好让图标等于原生尺寸"的格子。换屏换缩放自动跟随。
    """
    box = max(ICON_BOX_MIN, int(native_px))
    w = box + 2 * CHROME_X
    h = box + label_height(box)
    return (max(1, int(round(w / FIT_BASE))),
            max(1, int(round(h / FIT_BASE))))


def cell_for_native(native_px: int, tier=None) -> Tuple[int, int]:
    """**DEPRECATED（v7）**：tier 已不参与计算，仅为兼容旧调用点保留签名。

    v6 语义是 `cell = widget / TIERS[tier]`，切档位会改格距（问题 3 病根）；
    v7 起一律转发到 `cell_from_native()`，请新代码直接用后者。
    """
    return cell_from_native(native_px)


# ---------------------------------------------------------------- 层二：fit

def parse_fit(fit_or_tier) -> Optional[float]:
    """float -> 自身夹到 [0.30, 0.90]；档位串 -> TIER_FIT 值（medium 为 None）。"""
    if isinstance(fit_or_tier, bool):
        return None                      # bool 是 int 子类，别当占比
    if isinstance(fit_or_tier, (int, float)):
        return _clamp(float(fit_or_tier), FIT_MIN, FIT_MAX)
    t = parse_tier(fit_or_tier)
    return TIER_FIT[t]


def fit_for(native_px, cw: int, fit_or_tier) -> float:
    """把 fit/档位归一成**实际占比**（medium 用 native_px/cw 解算）。"""
    f = parse_fit(fit_or_tier)
    if f is None:
        npx = int(native_px) if native_px else 0
        f = (npx / max(1, int(cw))) if npx else TIER_FIT_FALLBACK["medium"]
    return _clamp(f, FIT_MIN, FIT_MAX)


def icon_layout(cw: int, ch: int, fit_or_tier="medium",
                scale: float = 1.0, font_scale: float = 1.0) -> IconLayout:
    """cell + 图标占比 -> 图标 widget 尺寸方案（v7 §3.1）。

    第三参接受 float（占比）或档位串；档位串走 `TIER_FIT_FALLBACK`
    （medium=0.57），所以既有 `icon_layout(84, 91, "medium")` 零改动。

    widget = icon_box + 2*CHROME_X（横向 chrome 仍是常量，见 §3.1 说明）；
    高度 = icon_box + 标签区。cell 只用来算 icon_box，不再乘档位。

    scale（反馈 12）：**单图标覆盖倍数**，默认 1.0。它只乘在 icon_box 上
    （标签区跟着图标边长走），不改变格子与全局档位——"每个图标一个乘数"
    是 v7 §4 决策 A 的最后一层。

    font_scale（v15a）：用户字号缩放（0.7~1.4）。标签带按**缩放后**的字号
    预留高度；`font_px` 仍返回**基准**字号——真正绘制时 `AppIcon` 会再乘
    一次缩放，两处必须是同一个数（否则就是"预留按 10px、画的是 14px"）。
    标签带另有硬上限：`widget_h <= ch`，字号再大也不越格压到下一行。
    """
    f = parse_fit(fit_or_tier)
    if f is None:
        f = TIER_FIT_FALLBACK["medium"]
    try:
        s = float(scale)
    except (TypeError, ValueError):
        s = 1.0
    s = _clamp(s, 0.1, 4.0)
    cw = max(1, int(cw))
    box = max(ICON_BOX_MIN, int(round(cw * f * s)))
    chrome_x = CHROME_X
    w = max(1, box + 2 * chrome_x)
    # 反馈 12：默认两行标签。格子（ch）装得下第二行才给——cell 尺寸由
    # `cell_from_native` 单行规格推导，widget 只能在格内纵向余量里长。
    lh = label_height(box, 1, font_scale)
    if LABEL_LINES > 1 and box + label_height(box, LABEL_LINES,
                                              font_scale) <= max(1, int(ch)):
        lh = label_height(box, LABEL_LINES, font_scale)
    # v15a 防御：字号放大后 cell 未必同步变大（格距由原生图标尺寸反解，
    # 不跟字号走），标签带不得越过格子——否则 widget 会盖住下一行。
    avail = max(1, int(ch) - box)
    if lh > avail:
        lh = max(LABEL_H_MIN, avail)
    h = max(1, box + lh)
    return IconLayout(w, h, box, chrome_x, lh, _font_for_box(box))


# ---------------------------------------------------------------- 行列求解

def solve_dims(avail_w, avail_h, native_px, cols=None, rows=None,
               origin=None, edge_x=EDGE_X, edge_y=EDGE_Y) -> Dims:
    """按可用区 + 原生图标尺寸求解网格几何（纯函数，零 Qt 依赖）。

    cols：None = 自动（= cols_auto）；给值则夹到 [1, cols_auto]，
          `cw = UW // cols` —— **列数是唯一改变 cell 尺寸的旋钮**。
    rows：None = 自动；给值则夹到 [1, rows_auto]，**只裁剪行数不改 cell**。
    origin：None = 四边等距居中；给 (x, y) 则夹到完整工作区（允许贴边）。

    两个必须记住的坑：
      1. `UW // cols` 用 floor 而不是 round，否则整行可能溢出 1~2px
         导致最后一列被裁；
      2. `rows_auto` 必须在 `cw/ch` 定稿**之后**算，否则改列数时行上限
         还是旧值，会出现"18 列却排 12 行"的行溢出（验收 D9）。
    """
    cw0, ch0 = cell_from_native(native_px)
    aspect = ch0 / cw0
    AW, AH = max(1, int(avail_w)), max(1, int(avail_h))
    UW = max(1, AW - 2 * int(edge_x))
    UH = max(1, AH - 2 * int(edge_y))

    # ① 列数：自动值即上限（格子只能变大不能变小）
    cols_auto = _clampi(UW // cw0, MIN_COLS, MAX_COLS)
    if cols is None or int(cols) >= cols_auto:
        c, cw, ch = cols_auto, cw0, ch0     # 打到上限 = 与自动完全等价
    else:
        c = _clampi(int(cols), MIN_COLS, cols_auto)
        cw = max(1, UW // c)                # floor：保证 c*cw 不溢出 UW
        ch = max(1, int(round(cw * aspect)))  # aspect 守恒 -> 图标不变形

    # ② 行数：只裁剪"网格用几行"，cell 一个像素都不动
    rows_auto = _clampi(UH // max(1, ch), MIN_ROWS, MAX_ROWS)
    r = rows_auto if rows is None else _clampi(int(rows), MIN_ROWS, rows_auto)

    # ③ 原点：None = 居中；给定则夹到**完整工作区**（不是 UW/UH，否则贴不了边）
    if origin is None:
        ox = int(edge_x) + (UW - c * cw) // 2
        oy = int(edge_y) + (UH - r * ch) // 2
    else:
        ox = _clampi(int(origin[0]), 0, max(0, AW - c * cw))
        oy = _clampi(int(origin[1]), 0, max(0, AH - r * ch))
    return Dims(c, r, cw, ch, cols_auto, rows_auto, max(0, ox), max(0, oy))


def icon_px_default() -> int:
    """无界模式图标像素默认值（§2.3）。"""
    return 96
