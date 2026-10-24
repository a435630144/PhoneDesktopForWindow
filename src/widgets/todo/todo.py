# -*- coding: utf-8 -*-
"""待办清单组件（v8 C5，指导文档_v8.md §4.2；v10 C1 原样搬入 widgets/todo 包）。

三层分离：
    load_items / add_item / toggle_item / remove_item / serialize  纯数据
    layout(rect) + row_rect / check_rect / delete_rect              纯几何
    draw(painter, rect, items, scroll, hover)                      纯绘制
    TodoWidget(GlassWidget)  输入框 + 自绘列表 + 滚轮 + debounce 落盘

为什么默认悬浮而不是锚定：
    锚定组件一律 `WindowDoesNotAcceptFocus`（v6 结论：桌面的一部分不该抢焦点），
    而待办需要键盘输入。含输入框的组件保持悬浮，与便签同一决策。

为什么列表自绘而不用 QScrollArea：
    滚动条会改变内容宽度（一出现文字就换行），桌面小卡片里非常明显；
    自绘 + 滚轮可以保证"内容宽度恒定"，行也不会因为滚动条出现而抖动。
"""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QLineEdit

from ... import theme
from ...widget_style import DEFAULT_STYLE, Style
from ...widget_glass import GAP, PAD, GlassWidget

MAX_ITEMS = 200
ROW_H = 30
INPUT_H = 28
TEXT_MAX = 200          # 单条正文上限（防止一条撑爆布局）
SAVE_DEBOUNCE_MS = 300


# ---------------------------------------------------------------- 纯数据

def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def load_items(raw) -> List[Dict]:
    """把存储里的任意脏数据整理成合法条目列表（坏条目丢弃，不抛）。"""
    out: List[Dict] = []
    if not isinstance(raw, list):
        return out
    for it in raw:
        if not isinstance(it, dict):
            continue
        iid = it.get("id")
        text = it.get("text")
        if not isinstance(iid, str) or not iid:
            continue
        if not isinstance(text, str):
            continue
        out.append({"id": iid, "text": text, "done": bool(it.get("done"))})
    return out


def serialize(items) -> List[Dict]:
    """只保留三个字段：存储里不许混进 UI 状态。"""
    return [{"id": it["id"], "text": it["text"], "done": bool(it["done"])}
            for it in items]


def add_item(items, text: str) -> List[Dict]:
    """追加一条；空白 / 超上限返回原列表的副本。"""
    text = (text or "").strip()
    if not text or len(items) >= MAX_ITEMS:
        return list(items)
    return list(items) + [{"id": _new_id(), "text": text[:TEXT_MAX], "done": False}]


def toggle_item(items, item_id: str) -> List[Dict]:
    out = []
    for it in items:
        if it.get("id") == item_id:
            out.append({"id": it["id"], "text": it["text"], "done": not it["done"]})
        else:
            out.append(it)
    return out


def remove_item(items, item_id: str) -> List[Dict]:
    return [it for it in items if it.get("id") != item_id]


# ---------------------------------------------------------------- 纯几何

def layout(rect) -> Dict:
    """输入框 + 列表区。列表区高度不足时 `visible` 可以是 0。"""
    rect = QRectF(rect)
    pad = min(PAD, rect.width() * 0.1)
    inner = rect.adjusted(pad, pad, -pad, -pad)
    input_h = min(INPUT_H, max(18.0, inner.height() * 0.2))
    input_rect = QRectF(inner.left(), inner.top(), inner.width(), input_h)
    list_top = input_rect.bottom() + GAP * 0.6
    list_rect = QRectF(inner.left(), list_top, inner.width(),
                       max(0.0, inner.bottom() - list_top))
    return {"inner": inner, "input": input_rect, "list": list_rect,
            "row_h": ROW_H, "visible": int(max(0.0, list_rect.height()) // ROW_H)}


def row_rect(list_rect: QRectF, visible_index: int) -> QRectF:
    return QRectF(list_rect.left(), list_rect.top() + visible_index * ROW_H,
                  list_rect.width(), ROW_H)


def check_rect(row: QRectF) -> QRectF:
    """勾选圆的**点击区**（比看到的圆大一圈，方便点）。"""
    d = min(22.0, row.height() - 4)
    return QRectF(row.left(), row.center().y() - d / 2, d, d)


def delete_rect(row: QRectF) -> QRectF:
    d = min(22.0, row.height() - 4)
    return QRectF(row.right() - d, row.center().y() - d / 2, d, d)


def text_left(row: QRectF) -> float:
    return check_rect(row).right() + 6


def text_color(done: bool, style: Optional[Style] = None) -> QColor:
    """完成项弱化（README：完成不是消失，是退到背景里）。"""
    st = style or DEFAULT_STYLE
    return QColor(st.faint) if done else QColor(st.text)


# ---------------------------------------------------------------- 纯绘制

def draw(p: QPainter, rect, items, scroll: int = 0, hover: int = -1,
         show_input: bool = True, style: Optional[Style] = None) -> None:
    st = style or DEFAULT_STYLE
    rect = QRectF(rect)
    if rect.width() <= 4 or rect.height() <= 4:
        return
    L = layout(rect)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)

    list_rect = L["list"]
    if show_input:
        # 输入框底（真控件由 Widget 摆上去，这里只负责预览卡的一致性）
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawRoundedRect(L["input"], 8, 8)
        p.setPen(QColor(st.faint))
        p.setFont(theme.ui_font(11))
        p.drawText(L["input"].adjusted(10, 0, -10, 0),
                   Qt.AlignLeft | Qt.AlignVCenter, "添加待办，回车确认")

    if not items:
        p.setPen(QColor(st.faint))
        fs = max(9, int(min(list_rect.height() * 0.16, 12)))
        p.setFont(theme.ui_font(fs))
        p.drawText(list_rect, Qt.AlignCenter, "没有待办，回车添加第一条")
        p.restore()
        return

    fs = max(9, int(min(ROW_H * 0.45, 13)))
    font = theme.ui_font(fs)
    p.setFont(font)
    fm = QFontMetricsF(font)
    for vi in range(L["visible"]):
        idx = scroll + vi
        if idx >= len(items):
            break
        it = items[idx]
        rr = row_rect(list_rect, vi)
        # 勾选圆
        cr = check_rect(rr)
        cd = min(14.0, cr.height() * 0.64)
        circle = QRectF(cr.center().x() - cd / 2, cr.center().y() - cd / 2, cd, cd)
        if it["done"]:
            p.setBrush(QColor(st.accent))
            p.setPen(Qt.NoPen)
            p.drawEllipse(circle)
            p.setPen(QPen(QColor(16, 18, 24), 1.6, Qt.SolidLine, Qt.RoundCap))
            cx, cy = circle.center().x(), circle.center().y()
            p.drawLine(QPointF(cx - cd * 0.22, cy),
                       QPointF(cx - cd * 0.04, cy + cd * 0.18))
            p.drawLine(QPointF(cx - cd * 0.04, cy + cd * 0.18),
                       QPointF(cx + cd * 0.24, cy - cd * 0.20))
        else:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 90), 1.4))
            p.drawEllipse(circle)

        # 正文
        left = text_left(rr)
        right = delete_rect(rr).left() - 4
        avail = max(10.0, right - left)
        text = it["text"]
        if fm.horizontalAdvance(text) > avail:
            cut = text
            while cut and fm.horizontalAdvance(cut + "…") > avail:
                cut = cut[:-1]
            text = (cut + "…") if cut else ""
        p.setPen(text_color(it["done"], st))
        tr = QRectF(left, rr.top(), avail, rr.height())
        p.drawText(tr, Qt.AlignLeft | Qt.AlignVCenter, text)
        if it["done"]:
            w = min(avail, fm.horizontalAdvance(text))
            y = rr.center().y() + 0.5
            p.setPen(QPen(text_color(True, st), 1.0))
            p.drawLine(QPointF(left, y), QPointF(left + w, y))

        # hover 行的删除 ×
        if hover == vi:
            dr = delete_rect(rr)
            p.setPen(QPen(QColor(st.dim), 1.4, Qt.SolidLine, Qt.RoundCap))
            c = dr.center()
            d = dr.height() * 0.22
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))
    p.restore()


# ---------------------------------------------------------------- 组件

INPUT_QSS = (
    "QLineEdit{background: rgba(255,255,255,14); color: rgb(242,243,245);"
    " border: 1px solid rgba(255,255,255,26); border-radius: 8px;"
    " padding: 2px 9px; font-family: %s; font-size: 12px;}"
    "QLineEdit:focus{border: 1px solid rgba(76,194,255,150);}"
) % ", ".join('"%s"' % f for f in theme.FONT_FAMILIES)


class TodoWidget(GlassWidget):
    TYPE = "todo"

    def __init__(self, storage, entry):
        super().__init__(storage, entry, interactive=True)
        self.entry["items"] = load_items(self.entry.get("items"))
        self._scroll = 0
        self._hover = -1
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText("添加待办，回车确认")
        self.edit.setStyleSheet(INPUT_QSS)
        # 右键交给组件本体（默认菜单会吃掉「编辑 / 组件设置」入口，与便签同理）
        self.edit.setContextMenuPolicy(Qt.NoContextMenu)
        self.edit.returnPressed.connect(self._on_add)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._save)
        self._relayout()

    # ---------- 排版 ----------
    def _relayout(self):
        self.edit.setGeometry(layout(QRectF(self.card_rect()))["input"].toRect())
        self._clamp_scroll()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 基类 __init__ 末尾就 show() 了，resizeEvent 会早于 self.edit 建立；
        # 没有这层守卫会 AttributeError（同 notes 的历史坑）
        if getattr(self, "edit", None) is not None:
            self._relayout()

    def on_edit_mode(self, on: bool):
        """编辑态把输入框让出鼠标（对齐便签：拖动控制点不被控件吞掉）。"""
        self.edit.setAttribute(Qt.WA_TransparentForMouseEvents, bool(on))

    # ---------- 数据 ----------
    def _clamp_scroll(self):
        visible = layout(QRectF(self.card_rect()))["visible"]
        self._scroll = max(0, min(self._scroll, max(0, len(self.items()) - visible)))

    def items(self) -> List[Dict]:
        return self.entry["items"]

    def _set_items(self, items):
        self.entry["items"] = items
        self._clamp_scroll()
        self.update()
        self._save_timer.start()

    def _save(self):
        self.entry["items"] = serialize(self.entry["items"])
        self.storage.save()

    def _flush_pending(self):
        # B14：销毁/退出时防抖定时器不再触发，最后一次编辑直接刷盘
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save()

    def _on_add(self):
        self._set_items(add_item(self.items(), self.edit.text()))
        self.edit.clear()

    # ---------- 交互 ----------
    def _hit_row(self, pos) -> Optional[int]:
        L = layout(QRectF(self.card_rect()))
        for vi in range(L["visible"]):
            if vi + self._scroll >= len(self.items()):
                break
            if row_rect(L["list"], vi).contains(pos):
                return vi
        return None

    def mouseMoveEvent(self, e):
        vi = self._hit_row(e.position())
        if vi != self._hover:
            self._hover = vi if vi is not None else -1
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            vi = self._hit_row(e.position())
            if vi is not None:
                idx = self._scroll + vi
                L = layout(QRectF(self.card_rect()))
                rr = row_rect(L["list"], vi)
                if check_rect(rr).contains(e.position()):
                    self._set_items(toggle_item(self.items(), self.items()[idx]["id"]))
                    return
                if delete_rect(rr).contains(e.position()):
                    self._set_items(remove_item(self.items(), self.items()[idx]["id"]))
                    return
        super().mousePressEvent(e)

    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        if not delta:
            return
        step = -1 if delta > 0 else 1
        before = self._scroll
        self._scroll += step
        self._clamp_scroll()
        if self._scroll != before:
            self.update()
        e.accept()

    # ---------- 绘制 ----------
    def paint_content(self, p: QPainter):
        draw(p, QRectF(self.card_rect()), self.items(), self._scroll, self._hover,
             show_input=False, style=self.style)

    def on_settings_changed(self):
        super().on_settings_changed()   # 清样式缓存 + 重绘
        self._apply_edit_style() if hasattr(self, "_apply_edit_style") else None
        self.update()
