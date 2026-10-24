# -*- coding: utf-8 -*-
"""便签小组件：玻璃卡片 + 自动保存（v9 V1 重写）。

v9 之前是"深色卡片 + 内嵌一块 QTextEdit"：玻璃化之后输入框的实心底
与玻璃底色叠成两层（用户描述"玻璃里嵌了块黑板"）。现在：

  - 标题与留白由 `paint_content()` 画（与时钟同一套 PAD / 字号语言）；
  - `QTextEdit` 背景透明、无边框，只负责文字与光标；
  - 文字颜色跟随组件设置（`text_color`），改色即时生效。

输入区仍是真控件：`QTextEdit` 的 IME、选区、撤销都不需要自己实现。
"""
from typing import Optional

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QTextEdit, QVBoxLayout

from ... import theme
from ...widget_glass import GlassWidget
from ...widget_style import DEFAULT_STYLE, Style, of as style_of

PAD = 18
HEAD_H = 20        # 标题行高度（本组件自己的；别与 widget_glass.TITLE_H 混用）
MAX_LEN = 4000          # 单个便签的字数上限（超出后拒绝输入，防误粘贴整篇文档）


def text_rect(rect: QRectF) -> QRectF:
    """正文区（绘制与控件几何共用同一份算术）。"""
    r = QRectF(rect)
    return QRectF(r.left() + PAD, r.top() + PAD + HEAD_H,
                  max(1.0, r.width() - PAD * 2),
                  max(1.0, r.height() - PAD * 2 - HEAD_H))


def draw(painter: QPainter, rect, text: str, style: Optional[Style] = None) -> None:
    """画标题 + 正文预览（组件库缩略图与调试器用；桌面组件走真控件）。"""
    style = style or DEFAULT_STYLE
    r = QRectF(rect)
    p = painter
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QColor(style.faint))
    p.setFont(theme.ui_font(12))
    p.drawText(QRectF(r.left() + PAD, r.top() + PAD - 4, r.width() - PAD * 2, HEAD_H),
               Qt.AlignLeft | Qt.AlignVCenter, "便签")
    p.setPen(QColor(style.text))
    p.setFont(theme.ui_font(13))
    body = text_rect(r)
    lines = (text or "").splitlines()[:6] or ["写点什么，自动保存…"]
    fm_h = 20
    for i, line in enumerate(lines):
        if body.top() + (i + 1) * fm_h > body.bottom() + fm_h:
            break
        p.drawText(QRectF(body.left(), body.top() + i * fm_h, body.width(), fm_h),
                   Qt.AlignLeft | Qt.AlignVCenter, line)


class NotesWidget(GlassWidget):
    TYPE = "notes"

    def __init__(self, storage, entry):
        # interactive=True：去掉 NOACTIVATE，QTextEdit 才能获得键盘焦点输入
        super().__init__(storage, entry, interactive=True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(PAD, PAD + HEAD_H, PAD, PAD)
        lay.setSpacing(0)
        self.edit = QTextEdit()
        self.edit.setPlaceholderText("写点什么，自动保存…")
        self.edit.setPlainText(entry.get("text", ""))
        # 右键交给组件本体：QTextEdit 的默认菜单会吃掉「编辑」入口
        # （NoContextMenu = 上下文菜单事件上送给父部件，Qt 文档语义）
        self.edit.setContextMenuPolicy(Qt.NoContextMenu)
        lay.addWidget(self.edit)
        self._apply_edit_style()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save_text)
        self.edit.textChanged.connect(self._on_text_changed)

    # ---------- 样式 ----------
    def _apply_edit_style(self):
        st = self.style
        self.edit.setStyleSheet(
            ("QTextEdit{background: transparent; border: none; padding: 0;"
             " color: %s; font-family: %s; font-size: 13px;"
             " selection-background-color: %s;}"
             "QTextEdit:focus{border: none;}")
            % (st.text.name(), ", ".join('"%s"' % f for f in theme.FONT_FAMILIES),
               theme.rgba(QColor(st.accent.red(), st.accent.green(),
                                 st.accent.blue(), 120))))
        self.edit.setPlaceholderText("写点什么，自动保存…")

    def on_settings_changed(self):
        super().on_settings_changed()
        self._apply_edit_style()

    def paint_content(self, p):
        st = self.style
        r = QRectF(self.card_rect())
        p.setPen(QColor(st.faint))
        p.setFont(theme.ui_font(12))
        p.drawText(QRectF(r.left() + PAD, r.top() + PAD - 4,
                          max(1.0, r.width() - PAD * 2), HEAD_H),
                   Qt.AlignLeft | Qt.AlignVCenter, "便签")

    def on_resized(self):
        """尺寸变化后正文区几何由布局管，不需重算——但空便签提示要重绘。"""
        if hasattr(self, "edit"):
            self.update()

    # ---------- 数据 ----------
    def _on_text_changed(self):
        # 超长截断（防误粘贴整篇文档把 state.json 撑大）：只留前面部分
        text = self.edit.toPlainText()
        if len(text) > MAX_LEN:
            cursor = self.edit.textCursor()
            pos = cursor.position()
            self.edit.blockSignals(True)
            self.edit.setPlainText(text[:MAX_LEN])
            self.edit.blockSignals(False)
            cursor.setPosition(min(pos, MAX_LEN))
            self.edit.setTextCursor(cursor)
        self._save_timer.start()

    def _save_text(self):
        self.entry["text"] = self.edit.toPlainText()
        self.storage.save()

    def _flush_pending(self):
        # B14：销毁/退出时防抖定时器不再触发，最后一次编辑直接刷盘
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_text()

    def on_edit_mode(self, on: bool):
        """编辑态：正文让出鼠标，拖动控制点才不会被 QTextEdit 吞掉。

        只影响鼠标（键盘焦点照旧），退出编辑态后排字行为完全恢复。
        """
        self.edit.setAttribute(Qt.WA_TransparentForMouseEvents, bool(on))
