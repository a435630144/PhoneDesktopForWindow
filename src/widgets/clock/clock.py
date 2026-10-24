# -*- coding: utf-8 -*-
"""时钟小组件：玻璃卡片 + 大号时间 + 中文日期（v9 V1 重写）。

v9 之前它用 `QLabel` + RichText 排版：玻璃化之后与四个新组件成了两套
语言（一个新组件是自绘、两个老组件是控件）。这一版统一成
**纯绘制**：

    model  → `ClockModel`（纯数据，可构造任意时刻）
    layout → `layout(rect, model)`（纯算术，测试与调试器都能量）
    draw   → `draw(painter, rect, model, style)`（可画到 QImage 验像素）

`ClockWidget` 只负责"每秒 tick 一次"，组件库预览复用同一个 `draw()`。
"""
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QDateTime, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetricsF, QPainter

from ... import theme
from ...widget_glass import GlassWidget
from ...widget_style import DEFAULT_STYLE, Style

# 内边距：与 widget_glass.PAD 同一套语言（组件之间留白必须一致）
PAD = 18


@dataclass(frozen=True)
class ClockModel:
    hhmm: str
    ss: str
    date: str
    weekday: str
    show_seconds: bool = True
    show_date: bool = True


_WEEKDAYS_CN = {
    "Monday": "星期一", "Tuesday": "星期二", "Wednesday": "星期三",
    "Thursday": "星期四", "Friday": "星期五", "Saturday": "星期六", "Sunday": "星期日",
    "周一": "星期一", "周二": "星期二", "周三": "星期三",
    "周四": "星期四", "周五": "星期五", "周六": "星期六", "周日": "星期日",
}


def model_now(now: Optional[QDateTime] = None, settings: Optional[dict] = None) -> ClockModel:
    """当前时刻 → 模型。`settings` 只关心 show_seconds / show_date 两个开关。"""
    n = now if now is not None else QDateTime.currentDateTime()
    s = settings if isinstance(settings, dict) else {}
    show_sec = s.get("show_seconds", True)
    show_date = s.get("show_date", True)
    wd = n.toString("dddd")
    # L5：不显示秒时 ss 留空——模型按值比较，ss 每秒都变会让"整分钟才
    # 重绘"的短路（m == self._model）永远不成立，等于白优化
    return ClockModel(hhmm=n.toString("HH:mm"),
                      ss=(n.toString("ss") if show_sec else ""),
                      date=n.toString("M月d日"),
                      weekday=_WEEKDAYS_CN.get(wd, wd),
                      show_seconds=bool(show_sec), show_date=bool(show_date))


def _fonts(rect: QRectF):
    """按容器高度比例出字号（2×2 与将来更大的跨度都自动适配）。"""
    h = max(80.0, rect.height())
    t = max(20.0, h * 0.215)
    return (theme.ui_font(int(round(t)), bold=True),
            theme.ui_font(max(9, int(round(t * 0.46)))),
            theme.ui_font(max(9, int(round(h * 0.072)))))


def layout(rect: QRectF, model: Optional[ClockModel] = None) -> dict:
    """返回时间/秒/日期的绘制基线（纯算术，便于断言）。"""
    rect = QRectF(rect)
    f_time, f_sec, f_date = _fonts(rect)
    fm_t = QFontMetricsF(f_time)
    fm_s = QFontMetricsF(f_sec)
    fm_d = QFontMetricsF(f_date)
    cx = rect.center().x()
    time_h = fm_t.height()
    # 时间行中心：整体略高于几何中心（iOS 桌面 widget 的视觉重心）
    time_cy = rect.center().y() - (fm_d.height() * 0.55 if (model is None or model.show_date) else 0)
    sec_w = fm_s.horizontalAdvance("00") * 0.62 + 4
    tw = fm_t.horizontalAdvance("00:00")
    total = tw + (sec_w if (model is None or model.show_seconds) else 0)
    x0 = cx - total / 2.0
    baseline = time_cy + (fm_t.ascent() - fm_t.descent()) / 2.0
    return {
        "font_time": f_time, "font_sec": f_sec, "font_date": f_date,
        "time_x": x0, "time_baseline": baseline,
        "time_width": tw,
        "sec_x": x0 + tw + 4, "sec_baseline": baseline,
        "date_rect": QRectF(rect.left() + PAD,
                            baseline + fm_d.ascent() + 8,
                            max(1.0, rect.width() - PAD * 2), fm_d.height() + 4),
        "time_height": time_h,
    }


def draw(painter: QPainter, rect, model: ClockModel, style: Optional[Style] = None) -> None:
    """画时钟内容（不含玻璃底：底色由 `GlassWidget` 负责）。"""
    style = style or DEFAULT_STYLE
    L = layout(QRectF(rect), model)
    p = painter
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QColor(style.text))
    p.setFont(L["font_time"])
    p.drawText(QPointF(L["time_x"], L["time_baseline"]), model.hhmm)
    if model.show_seconds:
        p.setPen(QColor(style.dim))
        p.setFont(L["font_sec"])
        p.drawText(QPointF(L["sec_x"], L["sec_baseline"]), model.ss)
    if model.show_date:
        p.setPen(QColor(style.dim))
        p.setFont(L["font_date"])
        p.drawText(L["date_rect"], Qt.AlignHCenter | Qt.AlignTop,
                   ("%s %s" % (model.date, model.weekday)).strip())


class ClockWidget(GlassWidget):
    TYPE = "clock"

    def __init__(self, storage, entry):
        # interactive=False：纯展示，保持 NOACTIVATE，永不被激活/抢焦点
        super().__init__(storage, entry, interactive=False)
        self._model = model_now(settings=entry.get("settings"))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _tick(self):
        m = model_now(settings=self.entry.get("settings"))
        # 秒数不显示时只在分钟变化时重绘（省掉 59/60 的无用重绘）
        if m == self._model:
            return
        self._model = m
        self.update()

    def paint_content(self, p):
        draw(p, QRectF(self.card_rect()), self._model, self.style)

    def on_resized(self):
        """尺寸变化（span 切换 / DPI 变化）：字号是比例算的，重绘即可。"""
        if hasattr(self, "_model"):
            self.update()
