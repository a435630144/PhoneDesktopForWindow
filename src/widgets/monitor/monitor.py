# -*- coding: utf-8 -*-
"""系统监控组件（v8 C4，指导文档_v8.md §4.1）。

三层分离（规格 §2.2）：
    sample()                  → Snapshot（纯数据，ctypes 读系统计数）
    layout(rect)              → 环的位置（纯算术，可单测）
    draw(painter, rect, snap) → 画（纯绘制，可画到 QImage 上验收）
    MonitorWidget(GlassWidget) → 只管定时器与把三者接起来

为什么用 ctypes 而不是 psutil：
    项目唯一依赖是 PySide6（打包体积 / 免装依赖是硬约束）。三个指标都有
    现成 Win32 API，且不需要轮询驱动、不产生额外进程。

CPU 的"首采样无基准"是有意为之：GetSystemTimes 返回的是累计值，必须两次
差分才有占用率。首帧返回 None 并显示 `--`，而不是假装 0%——0% 是错误信息。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Dict, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen

from ... import effects
from ... import theme
from ...widget_style import DEFAULT_STYLE, Style
from ...widget_glass import PAD, GlassWidget

RING_STROKE_RATIO = 0.18      # 环线宽 / 半径
WIDE_RATIO = 1.6              # 宽高比 >= 此值走宽版（4×2 是 1.69，2×2 是 0.85）
WARN_AT = 70.0                # 黄色阈值
DANGER_AT = 88.0              # 红色阈值
ANIM_MS = 320                 # 数值推进时长（阻尼 0.9，不过冲）

# ---------------------------------------------------------------- Win32

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _ft(v: _FILETIME) -> int:
    return (int(v.dwHighDateTime) << 32) | int(v.dwLowDateTime)


_cpu_prev = None      # 上一次 GetSystemTimes 的三个累计值


def sample_cpu() -> Optional[float]:
    """CPU 占用率（%）。首次调用无基准返回 None；失败返回 None。"""
    global _cpu_prev
    try:
        idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
        if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern),
                                      ctypes.byref(user)):
            return None
        cur = (_ft(idle), _ft(kern), _ft(user))
        prev, _cpu_prev = _cpu_prev, cur
        if prev is None:
            return None
        d_idle = cur[0] - prev[0]
        # kernel 时间**包含** idle，所以总时间 = kernel + user
        d_total = (cur[1] - prev[1]) + (cur[2] - prev[2])
        if d_total <= 0:
            return None
        return _clamp((1.0 - d_idle / float(d_total)) * 100.0)
    except Exception:      # noqa: BLE001  —— 单个 API 挂了不许拖垮整个组件
        return None


def sample_memory():
    """(占用 %, 已用 GB, 总量 GB)；失败返回 (None, None, None)。"""
    try:
        m = _MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(m)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return None, None, None
        total = int(m.ullTotalPhys) / (1024.0 ** 3)
        used = (int(m.ullTotalPhys) - int(m.ullAvailPhys)) / (1024.0 ** 3)
        return float(m.dwMemoryLoad), used, total
    except Exception:      # noqa: BLE001
        return None, None, None


def sample_disk(letter: str = "C:"):
    """(占用 %, 剩余 GB, 总量 GB)；盘符不存在/无权限返回 None。"""
    try:
        letter = (letter or "C:").strip().rstrip("\\")
        if len(letter) == 1:
            letter += ":"
        path = letter + "\\"
        free_bytes = ctypes.c_ulonglong()
        total_bytes = ctypes.c_ulonglong()
        total_free = ctypes.c_ulonglong()
        ok = kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(path),
            ctypes.byref(free_bytes), ctypes.byref(total_bytes),
            ctypes.byref(total_free))
        if not ok or int(total_bytes.value) <= 0:
            return None
        total = int(total_bytes.value)
        free = int(free_bytes.value)
        return (1.0 - free / float(total)) * 100.0, free / (1024.0 ** 3), total / (1024.0 ** 3)
    except Exception:      # noqa: BLE001
        return None


@dataclass(frozen=True)
class Snapshot:
    """一次采样的结果。任一字段为 None = 该项不可用（画 `--`）。"""

    cpu: Optional[float] = None
    mem: Optional[float] = None
    mem_used_gb: Optional[float] = None
    mem_total_gb: Optional[float] = None
    disk: Optional[float] = None
    disk_free_gb: Optional[float] = None
    disk_total_gb: Optional[float] = None
    disk_letter: str = "C:"
    ts: float = 0.0


def sample(disk_letter: str = "C:") -> Snapshot:
    """采一次三项指标（任一项失败只影响它自己）。"""
    cpu = sample_cpu()
    mem, mem_used, mem_total = sample_memory()
    disk = sample_disk(disk_letter)
    if disk is None:
        disk_pct = disk_free = disk_total = None
    else:
        disk_pct, disk_free, disk_total = disk
    return Snapshot(cpu=cpu, mem=mem, mem_used_gb=mem_used, mem_total_gb=mem_total,
                    disk=disk_pct, disk_free_gb=disk_free, disk_total_gb=disk_total,
                    disk_letter=(disk_letter or "C:"), ts=time.time())


# ---------------------------------------------------------------- 纯绘制

def _clamp(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


def progress_color(value: float, style: Optional[Style] = None) -> QColor:
    """负载分档配色：正常 = ACCENT、偏高 = 琥珀、危险 = DANGER。"""
    v = _clamp(value)
    if v >= DANGER_AT:
        return QColor(theme.DANGER)
    if v >= WARN_AT:
        return QColor(232, 176, 88)
    return QColor((style or DEFAULT_STYLE).accent)


RING_KEYS = ("cpu", "mem", "disk")
RING_LABELS = {"cpu": "CPU", "mem": "内存", "disk": "磁盘"}


def _wide_slots(inner: QRectF) -> Dict:
    """宽版（4×2）三列的列矩形与列内纵向槽位。

    v10 反馈 26 重构：旧版是"左 56% 三个小环 + 右侧一列数值文字"，
    宽幅下左重右轻、环与数字各说一半，用户评"太丑"。新版三列等分——
    每列自上而下「标签 / 大数值 / 进度条 / 容量明细」，重心均匀，宽度
    铺满 inner，读数一眼扫过。

    纯算术：固定 px 间距在跨度变化时会把内容挤成一坨，所以剩余空间按
    比例均分成四段间距（标签上 / 标签-数值 / 数值-进度条 / 进度条-明细）。
    高度不足 130px（组件库缩略图）时明细行高度为 0，直接不画。
    """
    h = max(1.0, inner.height())
    gap = max(6.0, inner.width() * 0.02)
    col_w = max(1.0, (inner.width() - gap * 2.0) / 3.0)
    label_h = max(11.0, min(20.0, h * 0.15))
    value_h = max(20.0, min(68.0, h * 0.42))
    bar_h = max(4.0, min(9.0, h * 0.035))
    detail_h = 0.0 if h < 130.0 else max(12.0, min(18.0, h * 0.12))
    spare = max(0.0, h - (label_h + value_h + bar_h + detail_h))
    if detail_h > 0.0:
        y_above, g_label, g_bar, g_detail = (spare * 0.40, spare * 0.12,
                                             spare * 0.28, spare * 0.20)
    else:
        # 没有明细行时把它那份留白还给上下两段，别让进度条孤零零贴底
        y_above, g_label, g_bar, g_detail = (spare * 0.52, spare * 0.12,
                                             spare * 0.36, 0.0)
    cols = []
    for i, key in enumerate(RING_KEYS):
        col_x = inner.left() + (col_w + gap) * i
        y = inner.top() + y_above
        label = QRectF(col_x, y, col_w, label_h)
        y += label_h + g_label
        value = QRectF(col_x, y, col_w, value_h)
        y += value_h + g_bar
        bar_w = col_w * 0.74
        bar = QRectF(col_x + (col_w - bar_w) / 2.0, y, bar_w, bar_h)
        y += bar_h + g_detail
        detail = QRectF(col_x, y, col_w, detail_h)
        cols.append({"key": key, "rect": QRectF(col_x, inner.top(), col_w, h),
                     "label": label, "value": value, "bar": bar,
                     "detail": detail})
    return {"cols": cols, "gap": gap}


def layout(rect) -> Dict:
    """算出三种布局之一（v12 反馈 36）。

    - **compact**（2x1 宽扁）：三行横排，每行「标签 / 进度条 / 百分比」，
      内容一眼扫完，不需要圆环。
    - **wide**（4x2 宽版）：三列等分，每列「标签 / 大数值 / 进度条 / 明细」。
    - **row**（2x2 近方形）：三环横排，环下标签（保留给老布局）。

    判定：高度 < 宽度×0.42 → compact（2x1 的 h/w ≈ 0.35~0.50）；
    否则 w >= h×WIDE_RATIO → wide；否则 row。
    """
    rect = QRectF(rect)
    w = max(1.0, rect.width())
    h = max(1.0, rect.height())
    inner = rect.adjusted(min(PAD, w * 0.12), min(PAD, h * 0.10),
                          -min(PAD, w * 0.12), -min(PAD, h * 0.10))
    # 2x1 是"矮"的（绝对高度小），先于 wide 判定。
    # 2x1 和 4x2 的宽高比接近（都是 ~2:1），只能靠绝对尺寸区分：
    # 2x1 高度 ≈ 1 格（100~180px），4x2 高度 ≈ 2 格（220~360px）。
    if h < 200.0 and w < 500.0:
        rows = _compact_rows(inner)
        return {"mode": "compact", "rows": rows, "inner": inner}
    wide = w >= h * WIDE_RATIO
    rings = []
    if wide:
        slots = _wide_slots(inner)
        return {"mode": "wide", "cols": slots["cols"], "gap": slots["gap"],
                "inner": inner}
    cell_w = inner.width() / 3.0
    diameter = max(4.0, min(cell_w - 6.0, inner.height() * 0.50))
    cy = inner.top() + diameter / 2.0 + (inner.height() - diameter) * 0.26
    for i, key in enumerate(RING_KEYS):
        cx = inner.left() + cell_w * (i + 0.5)
        r = diameter / 2.0
        rings.append({"key": key, "rect": QRectF(cx - r, cy - r, diameter, diameter)})
    return {"mode": "row", "rings": rings, "inner": inner,
            "label_y": cy + diameter / 2.0 + 4.0,
            "label_h": max(10.0, inner.bottom() - (cy + diameter / 2.0) - 4.0)}


def _compact_rows(inner: QRectF) -> list:
    """2x1 紧凑横排：三行「标签 / 进度条 / 百分比」（v12 反馈 36）。

    三行等高，每行高度均分 inner；行内：标签（左固定宽）→ 进度条（中）→ 百分比（右固定宽）。
    """
    rows = []
    gap = max(2.0, inner.height() * 0.06)
    row_h = max(8.0, (inner.height() - gap * 2) / 3.0)
    label_w = max(28.0, inner.width() * 0.18)
    pct_w = max(32.0, inner.width() * 0.14)
    bar_h = max(4.0, row_h * 0.42)
    for i, key in enumerate(RING_KEYS):
        y = inner.top() + i * (row_h + gap)
        cy = y + row_h / 2.0
        label = QRectF(inner.left(), y, label_w, row_h)
        bar = QRectF(inner.left() + label_w + 4.0,
                     cy - bar_h / 2.0,
                     inner.width() - label_w - pct_w - 8.0, bar_h)
        pct = QRectF(bar.right() + 4.0, y, pct_w, row_h)
        rows.append({"key": key, "rect": QRectF(inner.left(), y,
                                                inner.width(), row_h),
                     "label": label, "bar": bar, "pct": pct})
    return rows


def _fmt(value: Optional[float]) -> str:
    return "--" if value is None else "%d%%" % int(round(value))


def _draw_pct(p: QPainter, rect: QRectF, text: str, color: QColor) -> None:
    """居中画「大数字 + 小号 %」：两者共享基线，读起来是一个数。

    宽版的核心读数。整串同一字号时百分号会把数字挤窄、显得笨（旧版
    右列就是这种"表格感"），分级字号是这类仪表盘的通行做法。
    """
    num = text[:-1] if text.endswith("%") else text
    suf = "%" if text.endswith("%") else ""
    fs = max(12, int(rect.height() * 0.78))
    f_num = theme.ui_font(fs, bold=True)
    f_suf = theme.ui_font(max(9, int(fs * 0.52)), bold=True)
    fm_num = QFontMetricsF(f_num)
    fm_suf = QFontMetricsF(f_suf)
    w_num = fm_num.horizontalAdvance(num)
    w_suf = fm_suf.horizontalAdvance(suf) if suf else 0.0
    x = rect.center().x() - (w_num + w_suf) / 2.0
    baseline = rect.center().y() + (fm_num.ascent() - fm_num.descent()) / 2.0
    p.setFont(f_num)
    p.setPen(QPen(color))
    p.drawText(QPointF(x, baseline), num)
    if suf:
        p.setFont(f_suf)
        c = QColor(color)
        c.setAlpha(205)
        p.setPen(QPen(c))
        p.drawText(QPointF(x + w_num, baseline), suf)


def _detail_text(key: str, snap: Snapshot) -> str:
    """宽版每列的副行文字：内存/磁盘给容量，CPU 给负载档位。

    CPU 没有第二个量纲可显示，留空会让第三列底下缺一块（三列不齐）；
    用档位词补位——信息不多，但正好和另两列的"容量有余量"形成同一层
    语义（都在回答"还有多少空间"）。
    """
    if key == "cpu":
        if snap.cpu is None:
            return ""
        if snap.cpu >= DANGER_AT:
            return "过载"
        if snap.cpu >= WARN_AT:
            return "偏高"
        return "正常"
    if key == "mem":
        if snap.mem_used_gb is not None and snap.mem_total_gb:
            return "%.1f / %.0f GB" % (snap.mem_used_gb, snap.mem_total_gb)
    elif key == "disk":
        if snap.disk_free_gb is not None and snap.disk_total_gb:
            return "剩 %.0f / %.0f GB" % (snap.disk_free_gb, snap.disk_total_gb)
        return str(snap.disk_letter or "")
    return ""


def draw(p: QPainter, rect, snap: Snapshot, smooth: Optional[Dict] = None, style: Optional[Style] = None) -> None:
    """把快照画进 rect。`smooth` 传动画中的插值（缺省用原始值）。"""
    st = style or DEFAULT_STYLE
    rect = QRectF(rect)
    if rect.width() <= 4 or rect.height() <= 4:
        return
    sm = smooth or {}
    L = layout(rect)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)

    for ring in L.get("rings") or []:
        key = ring["key"]
        box = ring["rect"]
        raw = getattr(snap, key)
        val = raw if raw is None else _clamp(sm.get(key, raw))
        r = box.width() / 2.0
        pen_w = max(2.0, r * RING_STROKE_RATIO)

        # 轨道
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 30), pen_w, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(box)

        # 进度弧（从 12 点顺时针）
        if val is not None and val > 0.5:
            p.setPen(QPen(progress_color(val, st), pen_w, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(box, 90 * 16, -int(round(val / 100.0 * 360 * 16)))

        # 环心数值（太小就不写字，避免糊成一团）
        # 2026-10-22：**宽版不写环心**。wide 布局已经把数值放到右侧一列，
        # 两处都画 = 同一个数字出现两次；组件库缩略图（318×116 就是 wide）
        # 里右列还窄，数字直接压到卡片外面去（README 截图时暴露）。
        if L["mode"] == "row" and r >= 12:
            fs = max(7, int(round(min(r * 0.62, 26))))
            f = theme.ui_font(fs, bold=True)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT))
            txt = _fmt(val if raw is not None else None)
            p.drawText(box, Qt.AlignCenter, txt)

    if L["mode"] == "compact":
        # v12 反馈 36：2x1 紧凑横排——三行「标签 / 进度条 / 百分比」
        for row in L.get("rows") or []:
            key = row["key"]
            raw = getattr(snap, key)
            val = raw if raw is None else _clamp(sm.get(key, raw))
            color = QColor(st.text) if val is None else progress_color(val, st)

            p.setFont(theme.ui_font(max(9, int(row["label"].height() * 0.70))))
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(row["label"], Qt.AlignLeft | Qt.AlignVCenter,
                       RING_LABELS[key])

            bar = row["bar"]
            radius = bar.height() / 2.0
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(bar, radius, radius)
            if val is not None and val > 0.0:
                p.setBrush(color)
                p.drawRoundedRect(
                    QRectF(bar.x(), bar.y(),
                           max(bar.height(), bar.width() * val / 100.0),
                           bar.height()), radius, radius)

            p.setFont(theme.ui_font(max(9, int(row["pct"].height() * 0.60)),
                                    bold=True))
            p.setPen(color)
            p.drawText(row["pct"], Qt.AlignRight | Qt.AlignVCenter,
                       _fmt(None if raw is None else val))
        p.restore()
        return

    if L["mode"] == "row":
        fs = max(7, int(round(min(L["rings"][0]["rect"].height() * 0.20, 13))))
        p.setFont(theme.ui_font(fs))
        p.setPen(QColor(theme.TEXT_DIM))
        cell_w = L["inner"].width() / 3.0
        for i, key in enumerate(RING_KEYS):
            lr = QRectF(L["inner"].left() + cell_w * i, L["label_y"],
                        cell_w, max(10.0, L["label_h"]))
            p.drawText(lr, Qt.AlignHCenter | Qt.AlignTop, RING_LABELS[key])
    else:
        # 宽版三列（v10 反馈 26 重构）：标签 / 大数值 / 进度条 / 容量明细
        for col in L["cols"]:
            key = col["key"]
            raw = getattr(snap, key)
            val = raw if raw is None else _clamp(sm.get(key, raw))
            color = QColor(st.text) if val is None else progress_color(val, st)

            p.setFont(theme.ui_font(max(9, int(col["label"].height() * 0.78))))
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(col["label"], Qt.AlignHCenter | Qt.AlignVCenter,
                       RING_LABELS[key])

            _draw_pct(p, col["value"], _fmt(None if raw is None else val), color)

            bar = col["bar"]
            radius = bar.height() / 2.0
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(bar, radius, radius)
            if val is not None and val > 0.0:
                p.setBrush(color)
                p.drawRoundedRect(
                    QRectF(bar.x(), bar.y(),
                           max(bar.height(), bar.width() * val / 100.0),
                           bar.height()), radius, radius)
            p.setBrush(Qt.NoBrush)

            if col["detail"].height() > 0:
                detail = _detail_text(key, snap)
                if detail:
                    p.setFont(theme.ui_font(
                        max(9, int(col["detail"].height() * 0.72))))
                    p.setPen(QColor(theme.TEXT_FAINT))
                    p.drawText(col["detail"], Qt.AlignHCenter | Qt.AlignVCenter,
                               detail)

        # 列分隔线（很淡）：高瘦预览里省掉，避免三条竖线把版面切碎
        if L["inner"].height() >= 110.0:
            p.setPen(QPen(QColor(255, 255, 255, 20), 1))
            top = L["inner"].top() + L["inner"].height() * 0.16
            bottom = L["inner"].bottom() - L["inner"].height() * 0.16
            for i in (0, 1):
                x = L["cols"][i]["rect"].right() + L["gap"] / 2.0
                p.drawLine(QPointF(x, top), QPointF(x, bottom))
    p.restore()


# ---------------------------------------------------------------- 组件

class MonitorWidget(GlassWidget):
    TYPE = "monitor"

    def __init__(self, storage, entry):
        super().__init__(storage, entry)
        self._snap = Snapshot()
        self._smooth: Dict[str, float] = {}
        self._anim_from: Dict[str, float] = {}
        self._anim_t0 = 0.0
        self._curve = effects.spring_curve(0.9, ANIM_MS)
        self._anim = QTimer(self)
        # v14j：tick 间隔跟随全局动画帧率（渲染频率档位），不再写死 16ms
        self._anim.setInterval(max(8, round(1000.0 / max(1, self._fps()))))
        self._anim.timeout.connect(self._step)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()

    # ---------- 数据节拍 ----------
    def _fps(self) -> int:
        """全局动画帧率（设置 ▸ 外观 ▸ 渲染频率；读不到回 60）。"""
        try:
            from ... import liquid_glass
            return liquid_glass.live_fps(self.storage)
        except Exception:            # noqa: BLE001
            return 60

    def _disk_letter(self) -> str:
        return str((self.entry.get("settings") or {}).get("disk_letter") or "C:")

    def _tick(self):
        # B11/P1-6（2026-10-26）：网络盘断连时 sample 同步阻塞数秒——
        # 自适应跳采样：上次采样耗时 >500ms 则本轮跳过（保留旧值显示），
        # 且连续恶化时把数据节拍放宽到 5s，避免每秒卡一次 UI。
        import time as _time
        t0 = _time.perf_counter()
        prev = self._snap
        self._snap = sample(self._disk_letter())
        cost = (_time.perf_counter() - t0) * 1000.0
        if cost > 500 and self._timer.interval() < 5000:
            self._timer.setInterval(5000)
        # 帧率档位可能改过——每秒对齐一次（QTimer 重复 set 恒等值无副作用）
        self._anim.setInterval(max(8, round(1000.0 / max(1, self._fps()))))
        self._anim_from = {k: getattr(prev, k) for k in RING_KEYS
                          if getattr(prev, k) is not None}
        # 首次有值直接落位（没有起点就不该从 0 弹上去）
        for k in RING_KEYS:
            if getattr(prev, k) is None and getattr(self._snap, k) is not None:
                self._smooth[k] = getattr(self._snap, k)
        self._anim_t0 = time.perf_counter()
        if self._anim_from:
            self._anim.start()
        self.update()

    def _step(self):
        t = (time.perf_counter() - self._anim_t0) / (ANIM_MS / 1000.0)
        done = t >= 1.0
        e = 1.0 if done else self._curve.valueForProgress(t)
        for k, a in self._anim_from.items():
            b = getattr(self._snap, k)
            self._smooth[k] = a + ((b if b is not None else a) - a) * e
        if done:
            self._anim.stop()
            self._anim_from = {}
        self.update()

    # ---------- 绘制 ----------
    def paint_content(self, p: QPainter):
        draw(p, QRectF(self.card_rect()), self._snap, self._smooth, style=self.style)

    def on_settings_changed(self):
        super().on_settings_changed()   # 清样式缓存 + 重绘
        self._tick()

    def closeEvent(self, e):
        for t in ("_timer", "_anim"):
            timer = getattr(self, t, None)
            if timer is not None:
                timer.stop()
        super().closeEvent(e)
