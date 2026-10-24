# -*- coding: utf-8 -*-
"""Dock 栏（v10 C3，反馈 17）——纯函数层 + 窗口层。

用户诉求（`docs/指导文档_v10.md` §4.3）：底部一排圆角应用图标（macOS Dock
语言），默认底部，可切顶部/左右，设置里可开关，**开启后网格尺寸重新计算**。

## 三条铁律

1. **可用区扣除只有一个入口**：`storage_inset()` -> `grid_metrics.compute(inset=…)`。
   **不在 `solve_dims` 里塞 if**——否则"可用区"会有第二份定义，网格公式
   迟早与 Dock 各算各的（v7 双源漂移的教训）。
2. **Dock 只是快捷方式副本**：拖入不删网格条目，拖出也只删 `dock_items`
   里那一条。桌面布局永不被 Dock 改写（零数据风险，且幂等）。
3. **只收应用**（Q2 拍板）：文件 / 文件夹 / 组件一律拒绝，`entry_is_app()`
   是唯一判据，UI 层不许自己再写一份。

## 尺寸模型

图标基准 `DOCK_ICON` 逻辑像素 **只乘 `ui_scale()`**（B2 的单一旋钮，
dpr 由 Qt 用一次）。条带厚度预留了 hover 放大空间，所以 hover 时图标
仍在窗口内——**不做 macOS 式"跳出条带"**：分层窗超出窗口的绘制会被
裁剪，要做就得把窗口开大，而多出的透明区会挡住网格图标的点击
（"透明区穿透不是普遍真理"，坑 #39）。v11 若要，得先解决命中测试。

## 分层（与其它模块的关系）

    纯函数（本文件上半）：inset / metrics / bar_rect / item_rect / hit_index
                          move_item / normalize_items / entry_is_app
    DockWindow：绘制 + hover 放大 + 拖入 / 排序 / 拖出
    DesktopWindow：`storage_inset()` 扣可用区 -> refresh_grid
"""
from __future__ import annotations

import os
from typing import NamedTuple

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRect,
                            QRectF, Qt, QTimer, Property, Signal)
from PySide6.QtGui import (QBrush, QColor, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QWidget

from . import appsource, effects, liquid_glass, theme, win32_layer
from .iconwidget import MIME_TYPE

# ---------------------------------------------------------------- 常量

POSITIONS = ("bottom", "top", "left", "right")

POSITION_LABELS = (("bottom", "底部"), ("top", "顶部"),
                   ("left", "左侧"), ("right", "右侧"))

# 条带圆角形态（设置 ▸ Dock 栏）。数据自带中文，界面用 `i18n.TL` 兜底。
# 顺序 = 下拉顺序，`round` 排第一因为它是默认值（用户 2026-10-23 反馈
# "dock 栏的默认 R 角还是太大了"——胶囊形态在 51px 厚的条带上等于半径 25）。
RADIUS_LABELS = (("round", "圆角"), ("pill", "胶囊"), ("square", "直角"))

# 2026-10-23 反馈 2（v10 A9）：默认样式整体缩小一档——40px 图标 + 5 槽位
# 的条带在小屏上像"另一条任务栏"，用户原话"默认样式不要加入这么大的"。
# 现在 32px / 4 槽位（空态长度 ~152px），并且这三项都进了设置 ▸ Dock 栏。
#
# v14 反馈 5：默认样式整体改成 **iOS Dock** 观感——图标更大（44）、间距更松
# （12）、内边距更厚（10）、圆角更圆（20）、背景更透（0.62，壁纸能透出来），
# hover 放大也更接近 iOS 的 1.4。这些都是**默认值**：老用户 state 里已落盘的
# 旧值不被动（见 storage._migrate 的注释），新装/重置的用户拿到新观感。
DOCK_ICON = 44          # 图标基准边长（逻辑 px，100% 缩放；设置可改 24~56）
DOCK_ICON_MIN, DOCK_ICON_MAX = 24, 56
# v11（反馈 29）：间距 / 内边距 / 悬停放大全部进设置——"Dock 大小"由
# 图标 + 内边距 + 悬停倍数三者共同决定，只留一个滑块等于把三种诉求
# 压成一条线（用户：想要"更紧凑但图标别变小"就做不到）。
DOCK_GAP = 12           # 图标间距（图标 PNG 自带透明边，视觉间隙约 2 倍）
DOCK_GAP_MIN, DOCK_GAP_MAX = 0, 24
DOCK_PAD = 10           # 条带内边距（hover 图标与条带边留出呼吸）
DOCK_PAD_MIN, DOCK_PAD_MAX = 2, 20
DOCK_HOVER = 1.40       # hover 放大倍数（1.0 = 不放大；设置里可整项关闭）
DEFAULT_HOVER_PCT = 140  # 设置项 dock_hover_pct 的缺省值（与 DOCK_HOVER 同源）
DOCK_HOVER_MIN, DOCK_HOVER_MAX = 100, 150   # 设置值域（百分比，100 = 不放大）
MIN_SLOTS = 4           # 空态/少量条目时按 4 个槽位撑住长度（条形不会缩成一点）
DOCK_MAX_ITEMS = 24     # 上限（再多滚不动，靠拖出/排序管理；
                        # 与待办清单的 MAX_ITEMS 同名会撞文档门禁的常量表）

HOVER_MS = 160          # hover 放大动画时长（spring_curve，禁近似曲线）
DRAG_OUT_PX = 36        # 拖出多远算"移出 Dock"（松手即移除）

# 条带形状（设置 ▸ Dock 栏 ▸ 圆角形态）。`round` = 指定半径（**默认**），
# `pill` = 胶囊（半径 = 厚度的一半），`square` = 近乎直角——三种都是同一条
# 条带，只改半径。`round` 的半径由 `dock_radius` 单独可调。
#
# 2026-10-23 反馈 24（A11）：默认从 `pill` 改成 `round` + 12px。胶囊在 51px
# 厚的条带上半径 25.5，视觉上像"药丸"；12px 是 macOS Dock 的量级，条带
# 仍然是圆角矩形但不再抢眼。**老 state 由 schema 3 迁移一起改**（见
# `storage._migrate`），否则用户本地已落盘的 "pill" 会让这次改动看不见。
#
# v14 反馈 5：iOS Dock 的圆角更接近"厚度的 22%"——44px 图标的条带厚约
# 64px，20px 圆角正是这个比例；背景透明度 0.62（iOS 的毛玻璃 Shelf 是
# 能透出壁纸的，0.86 的实色块看着像旧版任务栏）。
RADIUS_MODES = ("round", "pill", "square")
DEFAULT_RADIUS_MODE = "round"
DEFAULT_ALPHA = 0.62
DEFAULT_RADIUS = 20
RADIUS_MIN, RADIUS_MAX = 0, 26


def setting(storage, key, default):
    """读设置并兜底缺省。

    `Storage.get_setting(key)` **只接受一个参数**（测试桩则允许 default），
    所以这里不能直接写 `get_setting(key, default)`——那会在真实 storage
    上抛 TypeError（Dock 一构造就崩）。
    """
    try:
        v = storage.get_setting(key)
    except Exception:      # noqa: BLE001 —— 设置层异常不该让 Dock 消失
        return default
    return default if v is None else v


def normalize_radius_mode(value) -> str:
    """非法/缺省一律回 `round`（默认形态），不是最圆的 `pill`。"""
    v = str(value or "").strip().lower()
    return v if v in RADIUS_MODES else DEFAULT_RADIUS_MODE


def radius_px(storage) -> int:
    """`round` 形态的圆角半径（逻辑 px，100% 缩放基准，夹到 0~26）。"""
    try:
        v = int(setting(storage, "dock_radius", DEFAULT_RADIUS))
    except (TypeError, ValueError):
        v = DEFAULT_RADIUS
    return max(RADIUS_MIN, min(RADIUS_MAX, v))


def icon_base(storage) -> int:
    """图标基准边长（设置值夹到 24~56；缺省 `DOCK_ICON`）。"""
    try:
        v = int(setting(storage, "dock_icon", DOCK_ICON))
    except (TypeError, ValueError):
        v = DOCK_ICON
    return max(DOCK_ICON_MIN, min(DOCK_ICON_MAX, v))


def bg_alpha(storage) -> float:
    """条带背景不透明度（0.30~1.0）。"""
    try:
        v = float(setting(storage, "dock_alpha", DEFAULT_ALPHA))
    except (TypeError, ValueError):
        v = DEFAULT_ALPHA
    return max(0.30, min(1.0, v))


# ---- 尺寸与间距（v11 反馈 29）----
def _clamped_int(storage, key, default, lo, hi) -> int:
    """读一个夹在 [lo, hi] 的整数设置（脏值回 default，不抛）。"""
    try:
        v = int(setting(storage, key, default))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def gap_px(storage) -> int:
    """图标间距（逻辑 px，100% 缩放基准，0~24）。"""
    return _clamped_int(storage, "dock_gap", DOCK_GAP,
                        DOCK_GAP_MIN, DOCK_GAP_MAX)


def pad_px(storage) -> int:
    """条带内边距（逻辑 px，2~20）——兼容旧接口，返回四向平均值。"""
    return _clamped_int(storage, "dock_pad", DOCK_PAD,
                        DOCK_PAD_MIN, DOCK_PAD_MAX)


# ---- 四向内边距（v12 反馈 35）----
def pad_side(storage, side: str) -> int:
    """单侧内边距：side ∈ {"top","bottom","left","right"}，2~20。

    优先读 `dock_pad_<side>`；未设置（新用户 / 旧 state）时回退到 `dock_pad`。
    """
    key = "dock_pad_" + side
    try:
        v = storage.get_setting(key)
    except Exception:      # noqa: BLE001
        v = None
    if v is None:
        return pad_px(storage)
    try:
        v = int(v)
    except (TypeError, ValueError):
        return pad_px(storage)
    return max(DOCK_PAD_MIN, min(DOCK_PAD_MAX, v))


def pad_4(storage) -> tuple:
    """返回 (top, bottom, left, right) 四向内边距。"""
    return (pad_side(storage, "top"), pad_side(storage, "bottom"),
            pad_side(storage, "left"), pad_side(storage, "right"))


def hover_pct(storage) -> int:
    """hover 放大倍数（百分比，100~150；100 = 不放大）。"""
    return _clamped_int(storage, "dock_hover_pct", DEFAULT_HOVER_PCT,
                        DOCK_HOVER_MIN, DOCK_HOVER_MAX)


def hover_ratio(storage) -> float:
    """hover 放大倍数（浮点，1.0~1.5；100% -> 1.0）。"""
    return hover_pct(storage) / 100.0


# ---- 边框（v10 反馈 27：粗细与颜色可调）----
BORDER_MIN, BORDER_MAX = 0, 4
DEFAULT_BORDER_WIDTH = 1
DEFAULT_BORDER_COLOR = ""       # 空 = 跟随主题 BORDER


def border_width(storage) -> int:
    """条带边框粗细（0 = 不画边框；夹到 0~4）。"""
    try:
        v = int(setting(storage, "dock_border_width", DEFAULT_BORDER_WIDTH))
    except (TypeError, ValueError):
        v = DEFAULT_BORDER_WIDTH
    return max(BORDER_MIN, min(BORDER_MAX, v))


def border_color(storage) -> QColor:
    """条带边框颜色；空/非法一律回退主题 BORDER（不留下透明边框）。"""
    raw = str(setting(storage, "dock_border_color",
                      DEFAULT_BORDER_COLOR) or "").strip()
    if raw:
        c = QColor(raw)
        if c.isValid():
            return c
    return QColor(theme.BORDER)


def hover_enabled(storage) -> bool:
    return bool(setting(storage, "dock_hover", True))


def dot_enabled(storage) -> bool:
    return bool(setting(storage, "dock_dot", True))


# ---- 屏幕位置偏移（v12 反馈 35）----
OFFSET_MIN, OFFSET_MAX = -200, 200
DEFAULT_OFFSET = 0


def offset_x(storage) -> int:
    """水平偏移（px，-200~200）：正值向右。"""
    return _clamped_int(storage, "dock_offset_x", DEFAULT_OFFSET,
                        OFFSET_MIN, OFFSET_MAX)


def offset_y(storage) -> int:
    """垂直偏移（px，-200~200）：正值向下。"""
    return _clamped_int(storage, "dock_offset_y", DEFAULT_OFFSET,
                        OFFSET_MIN, OFFSET_MAX)


def center_along_edge(storage) -> bool:
    """是否沿所在边居中（默认 True）。关闭后贴边起点。"""
    return bool(setting(storage, "dock_center", True))


class DockMetrics(NamedTuple):
    """一次 Dock 几何快照（逻辑像素）。"""

    icon: int
    gap: int
    pad: int           # 主轴起点内边距（兼容旧代码的 `m.pad`）
    thickness: int      # 条带厚度（副轴）
    length: int         # 条带长度（主轴）
    slots: int = 0      # 参与长度计算的槽位数（含空态撑开的）
    hover: float = DOCK_HOVER   # hover 放大倍数（v11：设置可调）
    # v12 反馈 35：四向内边距（逻辑 px，已乘 ui_scale）
    pad_t: int = 0
    pad_b: int = 0
    pad_l: int = 0
    pad_r: int = 0


# ---------------------------------------------------------------- 纯函数

def _scale(ui_scale) -> float:
    """收敛用户缩放到 0.6~2.0（与 `Storage.user_icon_scale` 同域）。"""
    try:
        s = float(ui_scale)
    except (TypeError, ValueError):
        return 1.0
    return min(2.0, max(0.6, s))


def metrics(ui_scale, count: int, base: int = DOCK_ICON,
            gap: int = DOCK_GAP, pad: int = DOCK_PAD,
            hover: float = DOCK_HOVER,
            pad_t: int = None, pad_b: int = None,
            pad_l: int = None, pad_r: int = None) -> DockMetrics:
    """Dock 几何：图标/间距/内边距只随 `ui_scale` 线性放大。

    条带厚度 = 副轴两向内边距 + hover 后的图标边长——**预留放大空间**是有意的：
    hover 时图标在条带内长大，永远不出窗口（见模块 docstring）。

    `pad_t/b/l/r` 缺省时四向都用 `pad`（兼容旧单值调用）。
    """
    s = _scale(ui_scale)
    icon = max(16, int(round(int(base) * s)))
    g = max(0, int(round(int(gap) * s)))
    p = max(1, int(round(int(pad) * s)))
    pt = max(1, int(round(int(pad_t if pad_t is not None else pad) * s)))
    pb = max(1, int(round(int(pad_b if pad_b is not None else pad) * s)))
    pl = max(1, int(round(int(pad_l if pad_l is not None else pad) * s)))
    pr = max(1, int(round(int(pad_r if pad_r is not None else pad) * s)))
    try:
        h = max(1.0, min(1.5, float(hover)))
    except (TypeError, ValueError):
        h = DOCK_HOVER
    # 厚度取副轴两向之和；长度取主轴两向之和。
    # 水平 dock（bottom/top）：thickness 用 pt+pb，length 用 pl+pr
    # 垂直 dock（left/right）：thickness 用 pl+pr，length 用 pt+pb
    # 这里保守取 max——两种方向下都不会画出比实际小的窗口。
    cross = max(pt + pb, pl + pr)
    main = min(pt + pb, pl + pr)
    thickness = cross + int(round(icon * h))
    slots = max(MIN_SLOTS, min(DOCK_MAX_ITEMS, max(0, int(count))))
    length = main + slots * icon + (slots - 1) * g
    return DockMetrics(icon, g, p, thickness, length, slots, h, pt, pb, pl, pr)


def metrics_for(storage, count: int = 0) -> DockMetrics:
    """按当前设置求一次 Dock 几何（**唯一入口**，别再各自传 ui_scale）。"""
    if count <= 0:
        try:
            count = len(load_items(storage))
        except Exception:      # noqa: BLE001
            count = 0
    pt, pb, pl, pr = pad_4(storage)
    return metrics(storage.ui_scale(), count, icon_base(storage),
                   gap_px(storage), pad_px(storage), hover_ratio(storage),
                   pad_t=pt, pad_b=pb, pad_l=pl, pad_r=pr)


def normalize_position(position) -> str:
    """位置收敛：非法值一律回退 bottom（不静默改网格另一个方向）。"""
    p = str(position or "").strip().lower()
    return p if p in POSITIONS else "bottom"


def inset(enabled, position, thickness) -> tuple:
    """Dock 占用的屏幕边条带 -> (left, top, right, bottom)。

    **这是"网格可用区"的唯一扣除入口**（本模块的对外契约）。
    `grid_metrics.compute(inset=…)` 只认这个四元组，不知道 Dock 是什么。
    """
    if not enabled:
        return (0, 0, 0, 0)
    t = max(0, int(thickness))
    if t == 0:
        return (0, 0, 0, 0)
    pos = normalize_position(position)
    if pos == "top":
        return (0, t, 0, 0)
    if pos == "left":
        return (t, 0, 0, 0)
    if pos == "right":
        return (0, 0, t, 0)
    return (0, 0, 0, t)


def usable_size(size, ins) -> tuple:
    """可用区尺寸（扣掉四边 inset）——最小 1，避免除零。"""
    w = max(1, int(size[0]) - int(ins[0]) - int(ins[2]))
    h = max(1, int(size[1]) - int(ins[1]) - int(ins[3]))
    return (w, h)


def bar_rect(size, position, m: DockMetrics,
             off_x: int = 0, off_y: int = 0,
             center: bool = True) -> tuple:
    """条带矩形 (x, y, w, h)：贴对应边 + 沿副轴居中 + 偏移。

    `off_x` / `off_y` 是屏幕坐标偏移（v12 反馈 35）：
    - 水平 dock：off_x 沿边平移，off_y 向屏幕内/外推
    - 垂直 dock：off_y 沿边平移，off_x 向屏幕内/外推
    `center=False` 时贴起点（左/上），不再沿边居中。
    """
    W, H = int(size[0]), int(size[1])
    pos = normalize_position(position)
    ox, oy = int(off_x), int(off_y)
    if pos in ("bottom", "top"):
        w, h = min(m.length, W), m.thickness
        x = max(0, (W - w) // 2) if center else 0
        x = max(0, min(W - w, x + ox))
        y = H - h if pos == "bottom" else 0
        # 正偏移 = 向屏幕内侧（底部 dock 往上，顶部 dock 往下）
        if pos == "bottom":
            y = max(0, min(H - h, y - oy))
        else:
            y = max(0, min(H - h, y + oy))
        return (x, y, w, h)
    w, h = m.thickness, min(m.length, H)
    y = max(0, (H - h) // 2) if center else 0
    y = max(0, min(H - h, y + oy))
    x = W - w if pos == "right" else 0
    # 正偏移 = 向屏幕内侧（右侧 dock 往左，左侧 dock 往右）
    if pos == "right":
        x = max(0, min(W - w, x - ox))
    else:
        x = max(0, min(W - w, x + ox))
    return (x, y, w, h)


def item_rect(bar, position, m: DockMetrics, index: int) -> tuple:
    """第 index 个图标的矩形（不含 hover 缩放，只算基准位）。"""
    x, y, w, h = bar
    pos = normalize_position(position)
    step = m.icon + m.gap
    if pos in ("bottom", "top"):
        ix = x + m.pad + int(index) * step
        iy = y + (h - m.icon) // 2
    else:
        ix = x + (w - m.icon) // 2
        iy = y + m.pad + int(index) * step
    return (ix, iy, m.icon, m.icon)


def hit_index(bar, position, m: DockMetrics, count: int,
              pos, enabled: bool = True):
    """屏幕/窗口坐标点 -> 命中的条目索引；间隙、条带外、关闭态一律 None。

    只认**图标矩形**（不认整条条带）：条带上留白很多，点空白不该误开应用。
    """
    if not enabled or count <= 0:
        return None
    px, py = int(pos[0]), int(pos[1])
    for i in range(min(int(count), DOCK_MAX_ITEMS)):
        x, y, w, h = item_rect(bar, position, m, i)
        if x <= px < x + w and y <= py < y + h:
            return i
    return None


def move_item(items, src: int, dst: int) -> list:
    """把 src 位置的条目移动到 dst（返回新列表，**不改入参**）。

    越界一律夹紧到 [0, n-1]——拖拽时指针可能在条带外，夹紧比"拒绝移动"
    更符合手感（用户看到的是"拖到最前面"）。
    """
    out = [dict(i) for i in items]
    n = len(out)
    if n <= 1:
        return out
    a = max(0, min(n - 1, int(src)))
    b = max(0, min(n - 1, int(dst)))
    if a == b:
        return out
    out.insert(b, out.pop(a))
    return out


def normalize_items(raw) -> list:
    """把 settings 里的任意脏数据收敛成合法条目列表。

    规则：必须是 dict、`name` 与 `target` 都非空、同 (name, target) 去重、
    最多 `DOCK_MAX_ITEMS` 条。**额外字段原样保留**（未来加 kind/args 不必迁移）。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        target = str(item.get("target") or "").strip()
        if not name or not target:
            continue
        key = (name, target)
        if key in seen:
            continue
        seen.add(key)
        entry = dict(item)
        entry["name"], entry["target"] = name, target
        entry.setdefault("icon_path", target)
        out.append(entry)
        if len(out) >= DOCK_MAX_ITEMS:
            break
    return out


# 可"启动"的文件扩展名（v10 反馈 26）。
# 桌面上的图标绝大多数是 `desktop_file_entry()` 产出的 kind="file" 克隆，
# 而快捷方式（.lnk/.url）天生就是 kind="file"——旧判据把 kind=file 一刀
# 拒掉，用户桌面上几乎全是快捷方式，于是"Dock 拖不进"100% 复现。
# Dock 是启动器，判据应该是"**能不能启动**"，不是"是不是扫描来的 app"。
LAUNCHABLE_SUFFIXES = (".lnk", ".url", ".exe", ".bat", ".cmd", ".com",
                       ".msc", ".appref-ms", ".pif")


def _is_launchable_file(target: str) -> bool:
    """kind=file 的条目能否当应用启动：按扩展名白名单，目录一律不行。"""
    t = str(target or "").strip()
    if not t:
        return False
    if not t.lower().endswith(LAUNCHABLE_SUFFIXES):
        return False
    try:
        if os.path.isdir(t):    # 名叫 "xxx.exe" 的文件夹也要拦住
            return False
    except OSError:
        pass
    return True


def entry_is_app(entry) -> bool:
    """桌面布局条目能否进 Dock（只有"能启动的"才能）。

    - `type == "app"` 且 `app.kind != "file"`（扫描来的应用）→ 收
    - `app.kind == "file"` → **按可启动性判定**：.lnk/.url/.exe/.bat 等收，
      普通文档与文件夹拒（Dock 是启动器，不是收藏夹）
    - 文件夹（type=folder）、组件（type=widget）一律拒
    """
    if not isinstance(entry, dict):
        return False
    if entry.get("type") != "app":
        return False
    app = entry.get("app")
    if not isinstance(app, dict):
        return False
    if not (app.get("name") and app.get("target")):
        return False
    if str(app.get("kind") or "app") == "file":
        return _is_launchable_file(app.get("target"))
    return True


def item_from_entry(entry) -> dict:
    """布局条目 -> Dock 条目（只取自包含的最小字段，重启后仍能启动）。"""
    app = entry.get("app") or {}
    target = str(app.get("target") or "")
    return {"name": str(app.get("name") or ""),
            "target": target,
            "icon_path": str(app.get("icon_path") or target)}


def load_items(storage) -> list:
    """从 settings 读条目（永远返回规范化后的列表，不会 None）。"""
    try:
        raw = storage.get_setting("dock_items")
    except Exception:      # noqa: BLE001 —— 设置层异常不该让 Dock 消失
        return []
    return normalize_items(raw)


def save_items(storage, items) -> list:
    """写回 settings（先规范化再落盘）——幂等，三次往返逐字段相等。"""
    norm = normalize_items(items)
    storage.set_setting("dock_items", [dict(i) for i in norm])
    return norm


def storage_inset(storage) -> tuple:
    """当前设置下的可用区扣除 —— **网格唯一入口**（见模块 docstring 铁律 1）。"""
    if not bool(storage.get_setting("dock_enabled")):
        return (0, 0, 0, 0)
    m = metrics_for(storage)
    return inset(True, storage.get_setting("dock_position"), m.thickness)


def seed_from_layout(storage, limit: int = 6) -> list:
    """首次启用时用桌面上现成的应用把 Dock 填上（规格 §4.3-3「初始为常见应用」）。

    只做一次（`dock_seeded` 标记）：用户手动清空 Dock 后不会被重新塞满——
    "只增不删、幂等"的另一面是"不擅自重建用户改过的集合"。
    """
    items = load_items(storage)
    if bool(storage.get_setting("dock_seeded")):
        return items
    if not items:
        picked = []
        for entry in list(getattr(storage, "layout", None) or []):
            if entry_is_app(entry):
                picked.append(item_from_entry(entry))
                if len(picked) >= max(1, int(limit)):
                    break
        items = save_items(storage, picked)
    storage.set_setting("dock_seeded", True)
    storage.save()
    return items


# ---------------------------------------------------------------- 窗口

class DockWindow(QWidget):
    """Dock 条带窗口（独立分层窗，层号 `BAND_LAYER_DOCK`）。

    自绘而非子控件：图标数量/排序/hover 让位都在一次 `paintEvent` 里完成，
    没有"子控件位置不同步"的第二份真相（与图标层同构）。
    """

    # 条目集合变了（加/删/排序）——调用方据此重算几何/落盘
    changed = Signal()
    # 用户点了某个条目（应用启动后），便于埋点/提示
    launched = Signal(str)

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self._items = load_items(storage)
        self._avail = (1920, 1080)
        self._bar = (0, 0, 0, 0)
        self._m = metrics_for(storage, len(self._items))
        self._position = normalize_position(
            storage.get_setting("dock_position"))
        self._hover_index = None
        self._hover_t = 0.0
        self._anim = None
        # v14 反馈 9（重做）：液态玻璃底图 + 折射贴图缓存。底图必须在
        # show() 之前抓（否则把条带自己抓进去），见 `_grab_lens_backdrop`。
        self._lens_src = QPixmap()
        self._lens_pm = QPixmap()
        self._lens_key = None
        # 拖动排序状态
        self._press_pos = None
        self._press_index = None
        self._drag_index = None
        self._drag_out = False
        self._drop_highlight = False
        # v10 A9（反馈 2）：外部拖入的插入落点（None = 没有落点指示）。
        # 旧实现只会"追加到末尾"且没有任何视觉反馈，用户拖上去看不出会发生
        # 什么，松手没加进去就以为"拖不进 Dock"。
        self._drop_index = None
        self._hover_on = hover_enabled(storage)
        self._dot_on = dot_enabled(storage)

        flags = (Qt.FramelessWindowHint | Qt.Tool
                 | Qt.WindowDoesNotAcceptFocus)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("Dock 栏")

    # ---------- 几何 ----------
    @property
    def enabled(self) -> bool:
        return bool(self.storage.get_setting("dock_enabled"))

    def position(self) -> str:
        return self._position

    def current_metrics(self) -> DockMetrics:
        return self._m

    def apply_geometry(self, avail_size) -> tuple:
        """按可用区重算条带并落位；未开启则隐藏（返回 (0,0,0,0)）。"""
        self._avail = (max(1, int(avail_size[0])), max(1, int(avail_size[1])))
        self._position = normalize_position(
            self.storage.get_setting("dock_position"))
        self._m = metrics_for(self.storage, len(self._items))
        self._hover_on = hover_enabled(self.storage)
        self._dot_on = dot_enabled(self.storage)
        if not self.enabled:
            self.hide()
            self._bar = (0, 0, 0, 0)
            return self._bar
        old_bar = self._bar
        self._bar = bar_rect(
            self._avail, self._position, self._m,
            off_x=offset_x(self.storage), off_y=offset_y(self.storage),
            center=center_along_edge(self.storage))
        # v14 反馈 9（重做）：液态玻璃要在本条带位置抓一张底图。条带挪了
        # 位置/尺寸就得重抓，且必须趁它不可见时抓——先 hide，随后 setGeometry
        # + show 会把它摆回原位。
        if (liquid_glass.surface_enabled(self.storage, "dock")
                and (self._lens_src.isNull() or tuple(self._bar) != old_bar)):
            self._grab_lens_backdrop(QRect(*self._bar))
        # 2026-10-23 反馈 6：位置切换必须**真的动**。旧实现只在"不可见时
        # show"，可见时依赖 setGeometry——几何是对的，但分层窗口在桌面带里
        # 被 SetWindowPos 重排过 z-order 后，Windows 偶尔会把窗口位置回退到
        # 上一次的摆放（Qt 侧 geometry 与实际窗口不同步），用户看到"切了位置
        # 没生效"。这里 setGeometry 之后强制一次 raise/重绘刷新，并由
        # `apply_dock_settings` 再补一次 `refresh_band()`。
        self.setGeometry(*self._bar)
        if not self.isVisible():
            self.show()
        elif not self.underMouse():
            self.update(self.rect())
        self.update()
        return self._bar

    # ---------- 液态玻璃（v14 反馈 9 重做） ----------
    def _grab_lens_backdrop(self, rect):
        """在条带位置抓一张底图（必须在条带不可见时抓，否则抓到自己）。"""
        was_visible = self.isVisible()
        if was_visible:
            self.hide()
        self._lens_src = liquid_glass.grab_backdrop(rect)
        self._lens_pm = QPixmap()
        self._lens_key = None

    def _lens_pixmap(self):
        """折射贴图（缓存）：参数/底图变了才重算（一次 10~30ms）。"""
        if self._lens_src.isNull():
            return QPixmap()
        lens = liquid_glass.lens_style(self.storage)
        radius = float(self._shape_radius(QRectF(self.rect())))
        key = (self.width(), self.height(), round(radius, 2),
               round(lens.strength, 4), round(lens.band, 3),
               self._lens_src.cacheKey())
        if self._lens_key == key and not self._lens_pm.isNull():
            return self._lens_pm
        dpr = float(self._lens_src.width()) / max(1, self.width())
        self._lens_pm = liquid_glass.refract(self._lens_src, radius, lens, dpr)
        self._lens_key = key
        return self._lens_pm

    def refresh_band(self):
        """重新挂到桌面带（位置 / 外观变化后调用）。

        分层窗口的位置由 Qt 的 setGeometry 改，z-order 由 win32 改；两者
        交替操作后必须再确认一次层序，否则切了位置看起来"没生效"
        （窗口还在旧位置的下方被桌面窗口盖住一截）。
        """
        if getattr(self, "attached", False) and self.isVisible():
            self.attached = win32_layer.attach_to_desktop(
                self, layer=win32_layer.BAND_LAYER_DOCK)
            if self.attached:
                win32_layer.enforce_band_order()
        self.update()

    def is_visible_bar(self) -> bool:
        """条带当前是否可见（启用 + 有窗口）——空态也算可见（占位提示）。"""
        return self.enabled and self.isVisible() and self._bar[2] > 0

    # ---------- 条目 ----------
    def items(self) -> list:
        return [dict(i) for i in self._items]

    def set_items(self, items, save: bool = True):
        """替换条目集合（规范化 + 可选落盘 + 重排几何）。"""
        self._items = normalize_items(items)
        if save:
            save_items(self.storage, self._items)
            self.storage.save()
        if self._hover_index is not None and self._hover_index >= len(self._items):
            self._hover_index = None
            self._set_hover_t(0.0)
        self.apply_geometry(self._avail)
        self.changed.emit()

    def add_item(self, item: dict, index=None) -> bool:
        """插入一条（拖入路径）；重复不追加，返回是否真的加了。

        v10 A9（反馈 2）：`index` 是插入位置（None = 追加到末尾）。旧实现
        只能追加，用户拖到第 1 个位置松手却跑到最后，也是"拖不进去"的错觉源。
        """
        if not isinstance(item, dict):
            return False
        norm = normalize_items([item])
        if not norm:
            return False
        if any(i.get("target") == norm[0]["target"] for i in self._items):
            return False
        items = list(self._items)
        if index is None:
            items.extend(norm)
        else:
            i = max(0, min(len(items), int(index)))
            items[i:i] = norm
        self.set_items(items)
        return True

    def _flash_duplicate(self):
        """重复拖入的提示：条带闪一下（不弹窗、不阻塞）。"""
        self._drop_highlight = True
        self.update()
        QTimer.singleShot(220, self._clear_highlight)

    def _clear_highlight(self):
        if not self._drag_active_now():
            self._drop_highlight = False
            self.update()

    def _drag_active_now(self) -> bool:
        return self._drop_index is not None

    def remove_at(self, index: int):
        if 0 <= int(index) < len(self._items):
            rest = list(self._items)
            del rest[int(index)]
            self.set_items(rest)

    def set_drop_highlight(self, on: bool):
        """外部拖拽悬停高亮（桌面窗口在 `dragMoveEvent` 里转发）。

        桌面窗口与 Dock 各有一条 drag 路径（命中投递取决于拖影下的窗口），
        高亮状态必须收在一处，否则两条路径同时亮/同时灭会闪。
        """
        on = bool(on)
        if on != self._drop_highlight:
            self._drop_highlight = on
            self.update()

    # ---------- hover 放大（单一动画驱动全部图标） ----------
    def _get_hover_t(self):
        return self._hover_t

    def _set_hover_t(self, v):
        self._hover_t = max(0.0, min(1.0, float(v)))
        self.update()

    hover_t = Property(float, _get_hover_t, _set_hover_t)

    def _animate_hover(self, to: float):
        if not self._hover_on:
            self._set_hover_t(0.0)
            return
        if self._anim is not None:
            self._anim.stop()
        if effects.reduced_motion():
            self._set_hover_t(to)
            return
        anim = QPropertyAnimation(self, b"hover_t", self)
        anim.setDuration(effects.dur(HOVER_MS))
        anim.setStartValue(self._hover_t)
        anim.setEndValue(float(to))
        # 弹簧曲线（项目铁律：禁 OutBack/OutCubic 近似）
        anim.setEasingCurve(effects.spring_curve(1.0, HOVER_MS))
        anim.start()
        self._anim = anim

    def _spread(self, index: int) -> int:
        """hover 时相邻图标的让位位移（像素，正 = 向后推）。"""
        if self._hover_index is None or self._hover_t <= 0.001:
            return 0
        d = abs(int(index) - int(self._hover_index))
        if d == 0 or d > 2:
            return 0
        grow = self._m.icon * (self._m.hover - 1.0) * self._hover_t
        falloff = 0.5 if d == 1 else 0.18
        shift = int(round(grow * falloff))
        return shift if index > self._hover_index else -shift

    # ---------- 绘制 ----------
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        radius = self._shape_radius(rect)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        # v10 A9：背景不透明度可调（0.30~1.0）——桌面壁纸透出来多少由用户定
        bg = QColor(theme.SURFACE)
        bg.setAlphaF(bg_alpha(self.storage))
        # v14 反馈 9（重做）：液态玻璃表面 = 透明底 + 边缘折射的底图。
        # 旧实现（渐变 + specular + 色散 + 扫光）已删除——用户要的是"透过
        # 玻璃看到壁纸在边缘被折射"，不是一层高光。
        lens_pm = (self._lens_pixmap()
                   if liquid_glass.surface_enabled(self.storage, "dock")
                   else QPixmap())
        if not lens_pm.isNull():
            liquid_glass.draw_refracted(p, rect, radius, lens_pm)
        else:
            # 兜底：没抓到底图（首帧 / 抓屏失败）仍画 iOS Shelf 渐变
            p.fillPath(path, bg)
            p.save()
            p.setClipPath(path)
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            top = QColor(255, 255, 255, 26)
            bottom = QColor(0, 0, 0, 26)
            grad.setColorAt(0.0, top)
            grad.setColorAt(0.55, QColor(255, 255, 255, 6))
            grad.setColorAt(1.0, bottom)
            p.fillRect(rect, grad)
            # 顶部 specular：两端渐隐（与组件玻璃 / 文件夹弹窗同一语言）
            hi = QLinearGradient(rect.topLeft(), rect.topRight())
            hi.setColorAt(0.0, QColor(255, 255, 255, 0))
            hi.setColorAt(0.5, QColor(255, 255, 255, 46))
            hi.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(QPen(QBrush(hi), 1.2))
            p.drawLine(QPointF(rect.left() + radius * 0.6, rect.top() + 1.0),
                       QPointF(rect.right() - radius * 0.6, rect.top() + 1.0))
            p.restore()
        # 边框（v10 反馈 27）：粗细 0~4 + 颜色可调。路径按 粗细/2 内缩，
        # 否则粗边框有一半画到窗口外被裁掉，看起来比设置值细。
        bw = border_width(self.storage)
        if bw > 0:
            border = border_color(self.storage)
            border.setAlphaF(min(1.0, 0.55 + bg_alpha(self.storage) * 0.45))
            half = max(0.5, bw / 2.0)
            edge = QRectF(self.rect()).adjusted(half, half, -half, -half)
            r_edge = max(0.0, radius - (half - 0.5))
            edge_path = QPainterPath()
            edge_path.addRoundedRect(edge, r_edge, r_edge)
            p.setPen(QPen(border, bw))
            p.setBrush(Qt.NoBrush)
            p.drawPath(edge_path)
            p.setBrush(Qt.NoBrush)

        # v14 反馈 9（重做）：折射层已作为背景画在 Shelf 位置，这里不再叠加
        # 任何光学层（旧的高光/色散/扫光已删除）。

        if not self._items:
            self._paint_empty_hint(p, rect)
            return

        dpr = float(self.devicePixelRatioF() or 1.0)
        # 先画非 hover 项（含让位），hover 项最后画——保证放大后的图标
        # 不被邻居盖住（绘制顺序即层序）
        order = [i for i in range(len(self._items)) if i != self._hover_index]
        if self._hover_index is not None and self._hover_index < len(self._items):
            order.append(self._hover_index)
        for i in order:
            self._paint_item(p, i, dpr)

        if self._drop_highlight:
            p.setPen(QPen(theme.ACCENT, 2))
            p.drawPath(path)
            self._paint_drop_indicator(p)

    def _shape_radius(self, rect) -> float:
        """条带圆角（设置 ▸ Dock 栏 ▸ 圆角形态 + 圆角大小）。

        `round` 用 `dock_radius`（默认 20），并夹到厚度的一半——否则半径超过
        半厚时 Qt 会自己截断，看起来与"胶囊"无异，滑块后半段等于白拖。
        """
        mode = normalize_radius_mode(
            self.storage.get_setting("dock_radius_mode"))
        short = min(rect.width(), rect.height()) / 2.0
        if mode == "pill":
            return short
        if mode == "square":
            return min(2.0, short)
        return min(float(radius_px(self.storage)) * _scale(self.storage.ui_scale()),
                   short)

    def _paint_drop_indicator(self, p: QPainter):
        """外部拖入时的插入位置竖线（水平条）／横线（竖向条）。

        没有这个指示，用户拖上去只看到边框变蓝，不知道松手会插到哪里；
        `dropEvent` 按同一个 `_drop_index` 插入，所以指示位置 == 实际落点。
        """
        if self._drop_index is None:
            return
        m = self._m
        _x, _y, w, h = self._bar
        i = max(0, min(int(self._drop_index), len(self._items)))
        step = m.icon + m.gap
        p.setPen(QPen(theme.ACCENT, 3, Qt.SolidLine, Qt.RoundCap))
        # 窗口 == 条带，所以"局部坐标"= 条带内偏移（不掺 bar 原点）
        if self._position in ("bottom", "top"):
            lx = m.pad + i * step - m.gap / 2.0
            p.drawLine(QPointF(lx, m.pad * 0.6), QPointF(lx, h - m.pad * 0.6))
        else:
            ly = m.pad + i * step - m.gap / 2.0
            p.drawLine(QPointF(m.pad * 0.6, ly), QPointF(w - m.pad * 0.6, ly))

    def _paint_empty_hint(self, p, rect):
        p.setPen(QPen(theme.TEXT_FAINT, 1))
        f = theme.ui_font(max(11, int(round(12 * _scale(self.storage.ui_scale())))))
        p.setFont(f)
        p.drawText(rect, Qt.AlignCenter, "拖入应用")

    def _paint_item(self, p, i, dpr):
        m = self._m
        x, y, w, h = item_rect(self._bar, self._position, m, i)
        # 条带可能比窗口本身偏移（窗口就是条带，故相对坐标减 bar 原点）
        lx = x - self._bar[0]
        ly = y - self._bar[1]
        grow = 0.0
        lift = 0.0
        if i == self._hover_index:
            grow = m.icon * (m.hover - 1.0) * self._hover_t
            # v14 反馈 5：iOS 的放大是"抬起"——图标往上飘出 Shelf 一点，
            # 不是原地胀大。抬升量取放大增量的一半（视觉上仍以放大为主）。
            lift = grow * 0.5
        # 尺寸量化到 2px 步进：hover 动画每帧都会变，不量化会让
        # `appsource.icon_pixmap` 的 size 缓存键爆炸（每帧新建 pixmap）
        size = max(16, int(round((m.icon + grow) / 2.0)) * 2)
        shift = self._spread(i)
        horizontal = self._position in ("bottom", "top")
        if horizontal:
            cx = lx + m.icon // 2 + shift
            cy = ly + m.icon // 2 - lift
        else:
            cx = lx + m.icon // 2 - lift
            cy = ly + m.icon // 2 + shift
        target = QRect(int(cx - size // 2), int(cy - size // 2), size, size)

        dropping_out = (self._drag_out and i == self._drag_index)
        if dropping_out:
            p.setOpacity(0.45)
        try:
            pm = appsource.icon_pixmap(self._items[i], size, dpr)
        except Exception:      # noqa: BLE001 —— 单个坏条目不该让整条画不出来
            pm = None
        if pm is not None and not pm.isNull():
            p.drawPixmap(target, pm)
        else:
            self._paint_fallback(p, target, self._items[i])
        p.setOpacity(1.0)

        # hover 项下方的小圆点（macOS 的"运行中"指示位置，这里表示"当前指向"）
        if self._dot_on and i == self._hover_index and self._hover_t > 0.2:
            dot = max(2, int(round(3 * self._hover_t)))
            p.setPen(Qt.NoPen)
            p.setBrush(theme.ACCENT)
            if horizontal:
                p.drawEllipse(QPoint(cx, ly + m.icon + m.pad // 2 + dot), dot, dot)
            else:
                p.drawEllipse(QPoint(lx + m.icon + m.pad // 2, cy), dot, dot)
            p.setBrush(Qt.NoBrush)

    def _paint_fallback(self, p, rect, item):
        """取不到图标时的字母占位（与图标层同一套观感）。"""
        f = theme.ui_font(max(11, rect.width() // 2), bold=True)
        p.setFont(f)
        p.setPen(theme.TEXT)
        ch = (str(item.get("name") or "?").strip() or "?")[0]
        p.drawText(rect, Qt.AlignCenter, ch)

    # ---------- 鼠标：hover / 点击启动 / 拖动排序 ----------
    def _bar_point(self, pos):
        """窗口局部坐标 -> **条带坐标**（屏幕）。

        2026-10-23（v10 A9）：`hit_index` / `bar_rect` / `item_rect` 全都用
        **屏幕坐标**（条带可能是屏幕中间的一段），而鼠标事件给的是窗口局部
        坐标。旧实现直接把局部坐标喂给 `hit_index`——条带不在屏幕左上角时
        （居中摆放的 bottom/top、right 侧）判断恒为"没命中"：hover 不放大、
        点图标不启动、拖出去也判不出"移出"。条带贴左时才碰巧正确，
        所以这个 bug 一直没被发现。这里统一加条带原点，所有判定共用。
        """
        try:
            return (int(pos.x()) + int(self._bar[0]),
                    int(pos.y()) + int(self._bar[1]))
        except (AttributeError, IndexError, TypeError):
            return (int(getattr(pos, "x", lambda: 0)()),
                    int(getattr(pos, "y", lambda: 0)()))

    def _local_index(self, pos):
        return hit_index(self._bar, self._position, self._m,
                         len(self._items), self._bar_point(pos),
                         enabled=self.enabled)

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        idx = self._local_index(pos)
        if idx != self._hover_index:
            self._hover_index = idx
            self._animate_hover(1.0 if idx is not None else 0.0)
        # 拖动排序
        if self._press_index is not None and e.buttons() & Qt.LeftButton:
            if self._drag_index is None:
                if (pos - self._press_pos).manhattanLength() >= 6:
                    self._drag_index = self._press_index
                    self.setCursor(Qt.ClosedHandCursor)
            if self._drag_index is not None:
                self._drag_out = self._outside(pos)
                target = self._drop_target(pos)
                if target is not None and target != self._drag_index:
                    self._items = move_item(self._items, self._drag_index, target)
                    self._drag_index = target
                    self._hover_index = target
                    save_items(self.storage, self._items)
                    self.update()
        self.update()

    def _outside(self, pos) -> bool:
        x, y, w, h = self._bar
        g = QRect(x - DRAG_OUT_PX, y - DRAG_OUT_PX,
                  w + DRAG_OUT_PX * 2, h + DRAG_OUT_PX * 2)
        px, py = self._bar_point(pos)
        return not g.contains(px, py)

    def _insert_index(self, pos) -> int:
        """落点 -> 插入索引（拖入新条目时用；与排序用的 `_drop_target` 同口径）。"""
        m = self._m
        x, y, w, h = self._bar
        horizontal = self._position in ("bottom", "top")
        bp = self._bar_point(pos)
        v = bp[0] if horizontal else bp[1]
        origin = (x if horizontal else y) + m.pad
        step = m.icon + m.gap
        if step <= 0:
            return 0
        return max(0, min(len(self._items), int(round((v - origin) / step))))

    def _drop_target(self, pos):
        """拖动中的落点索引：按住某图标时按"中心越过邻居中心"判换位。

        用中心比较而不是矩形命中——拖动时被拖的图标本身跟着指针走，
        矩形命中会在自己身上打转（永远等于自己）。
        """
        if self._drag_index is None:
            return None
        horizontal = self._position in ("bottom", "top")
        bp = self._bar_point(pos)
        v = bp[0] if horizontal else bp[1]
        m = self._m
        origin = (self._bar[0] if horizontal else self._bar[1]) + m.pad
        step = m.icon + m.gap
        if step <= 0:
            return None
        if self._outside(pos):
            return None
        idx = int((v - origin) // step)
        return max(0, min(len(self._items) - 1, idx))

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position().toPoint()
        self._press_pos = pos
        self._press_index = self._local_index(pos)
        self._drag_index = None
        self._drag_out = False
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position().toPoint()
        pressed, dragged = self._press_index, self._drag_index
        self._press_index = None
        self._press_pos = None
        self._drag_index = None
        self._drag_out = False
        self.unsetCursor()
        self.setCursor(Qt.PointingHandCursor)
        if dragged is not None:
            # 拖出条带松手 = 从 Dock 移除（桌面条目原样保留，铁律 2）
            if self._outside(pos):
                self.remove_at(dragged)
            else:
                save_items(self.storage, self._items)
                self.storage.save()
                self.changed.emit()
            self.update()
            return
        if pressed is not None and pressed < len(self._items):
            self._launch(self._items[pressed])
        self.update()

    def _launch(self, item):
        try:
            appsource.launch(item)
        except Exception:      # noqa: BLE001 —— 目标被删/无权限：静默（不炸桌面）
            return
        self.launched.emit(str(item.get("name") or ""))

    def leaveEvent(self, _e):
        self._hover_index = None
        self._animate_hover(0.0)
        self.update()

    # ---------- 外部拖入（从网格拖应用进来） ----------
    def dragEnterEvent(self, e):
        if not e.mimeData().hasFormat(MIME_TYPE):
            e.ignore()       # 明确拒绝，避免合成事件路径下状态不确定
            return
        self._drop_highlight = True
        self.update()
        e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_TYPE):
            idx = self._insert_index(e.position().toPoint())
            if idx != self._drop_index:
                self._drop_index = idx
                self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self._drop_highlight = False
        self._drop_index = None
        self.update()

    def dropEvent(self, e):
        idx = self._drop_index
        self._drop_highlight = False
        self._drop_index = None
        self.update()
        md = e.mimeData()
        if not md.hasFormat(MIME_TYPE):
            return
        try:
            eid = md.data(MIME_TYPE).data().decode()
        except Exception:      # noqa: BLE001
            return
        entry = None
        try:
            entry = self.storage.find_entry(eid)
        except Exception:      # noqa: BLE001
            entry = None
        if not entry_is_app(entry):
            e.ignore()          # Q2：文件/文件夹/组件不收
            return
        if self.add_item(item_from_entry(entry), index=idx):
            e.acceptProposedAction()
            self.storage.save()
        else:
            # 已在 Dock 里：**也要接受**并给一次视觉反馈（旧实现 e.ignore()，
            # 拖拽源拿不到落点、图标弹回原位，用户以为"拖不进 Dock"）
            self._flash_duplicate()
            e.acceptProposedAction()

    # ---------- 桌面带 ----------
    def showEvent(self, e):
        super().showEvent(e)
        # 层号 = DOCK：压在组件之上（组件层 20）、文件夹幕布之下（30）——
        # 幕布/弹窗是"模态"的，必须能盖住 Dock。
        self.attached = win32_layer.attach_to_desktop(
            self, layer=win32_layer.BAND_LAYER_DOCK)
        if self.attached:
            win32_layer.enforce_band_order()
