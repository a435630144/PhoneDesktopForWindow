# -*- coding: utf-8 -*-
"""文件夹的独立表单：添加应用 / 更换颜色（2026-10-20 用户反馈 1）。

## 为什么从弹窗里搬出来

旧实现把「添加」候选和「色板」都画在文件夹弹窗**自己的矩形里**：

  - 添加候选是覆盖在成员格上的一层浮层，列宽按**当前成员数**算出的
    弹窗宽度平均分——成员只有 2 个时弹窗很窄，4 列候选挤在 300px 里，
    图标和文件名互相压（用户截图里"添加按钮直接排版堆叠乱了"）；
  - 色板是标题下方的一条，展开要让弹窗**长高**，于是 `_build()` 重排
    ——而重排会销毁并重建全部成员图标，每个都重放一次 `appear_in`
    错峰淡入。用户看到的是"换个颜色，文件夹里的图标全部重新渲染一遍"。

两件事都源于同一个错误假设：**这些是弹窗的一部分**。它们其实是
**另一个层级的界面**——所以现在各自是一个独立的小窗口，贴着文件夹
弹窗展开，不复用弹窗的矩形、不触发弹窗重排。

## 窗口形态

和 `BandInputBox`（重命名输入框）同一套：frameless + `Qt.Tool` +
`WindowDoesNotAcceptFocus`，挂桌面带后插到弹窗之上。差别是它不需要
键盘焦点（没有输入框），所以保持 NOACTIVATE——点了不会把用户的
前台窗口弄掉。
"""
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QColorDialog, QWidget

from . import appsource
from . import theme
from . import win32_layer
from .folder_tint import AUTO, LIQUID, auto_tint

FORM_RADIUS = 14
FORM_BG = QColor(24, 25, 30, 248)
FORM_BG_HOVER = QColor(255, 255, 255, 16)
GAP = 10          # 与锚点窗口的间距


def _wrap_lines(name: str, fm, width: int, max_lines: int) -> list:
    """折行（西文按词、中文按字），超过 max_lines 时末行省略。

    与 `AppIcon._wrap_elide` 同一套规则——两处都是"格子里的应用名"，
    规则分家必然出现"桌面能折行、添加表单不能"的割裂感。
    """
    from .iconwidget import AppIcon
    return AppIcon._wrap_elide(name, fm, max(1, int(width)), max_lines)


def _scale(storage) -> float:
    try:
        return float(storage.ui_scale())
    except Exception:
        return 1.0


def _screen_avail(widget, probe: QPoint = None):
    """可用屏幕矩形（已扣任务栏），probe 用于多屏判定。"""
    pt = probe or (widget.pos() if widget is not None else None)
    scr = QGuiApplication.screenAt(pt) if pt is not None else None
    scr = scr or QGuiApplication.primaryScreen()
    g = scr.availableGeometry()
    return g.x(), g.y(), g.width(), g.height()


class FolderForm(QWidget):
    """表单基类：圆角实心面板 + 标题 + 右上角关闭。"""

    closed = Signal()

    def __init__(self, storage, title: str, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.title = title
        self._mouse = None
        self._anchor_window = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)

    # ---------- 度量 ----------
    def m(self) -> float:
        return _scale(self.storage)

    def pad(self) -> int:
        return max(8, int(round(12 * self.m())))

    def title_h(self) -> int:
        return max(26, int(round(34 * self.m())))

    def close_size(self) -> int:
        return max(18, int(round(20 * self.m())))

    def _close_rect(self) -> QRect:
        s = self.close_size()
        m = max(6, int(round(8 * self.m())))
        return QRect(self.width() - s - m, m, s, s)

    def _title_rect(self) -> QRect:
        p = self.pad()
        return QRect(p, 0, max(1, self.width() - p * 2 - self.close_size() - 20),
                     self.title_h())

    def _content_rect(self) -> QRect:
        p = self.pad()
        top = self.title_h()
        return QRect(p, top, max(1, self.width() - p * 2),
                     max(1, self.height() - top - p))

    # ---------- 定位 ----------
    def show_beside(self, anchor_window, avoid=None):
        """贴着 anchor_window（文件夹弹窗）展开：右侧优先，其次左侧，其次下方。

        avoid：另一个已打开的表单（两个表单同时开时向下错开，不重叠）。
        """
        self._anchor_window = anchor_window
        self.adjustSize()
        aw = self.width()
        ah = self.height()
        ag = anchor_window.frameGeometry() if anchor_window is not None else QRect()
        vx, vy, vw, vh = _screen_avail(self, ag.center())
        right = ag.right() + GAP + 1
        left = ag.left() - GAP - aw
        below = ag.bottom() + GAP + 1
        above = ag.top() - GAP - ah
        if right + aw <= vx + vw:
            x, y = right, ag.top()
        elif left >= vx:
            x, y = left, ag.top()
        elif below + ah <= vy + vh:
            x, y = ag.left(), below
        elif above >= vy:
            x, y = ag.left(), above
        else:
            x, y = ag.right() - aw, ag.bottom() - ah
        if avoid is not None and avoid.isVisible():
            og = avoid.frameGeometry()
            if QRect(x, y, aw, ah).intersects(og):
                y = og.bottom() + GAP
        x = max(vx + 4, min(x, vx + vw - aw - 4))
        y = max(vy + 4, min(y, vy + vh - ah - 4))
        self.move(x, y)
        self.show()
        self.attach_above(anchor_window)
        self.update()

    def attach_above(self, other_window):
        """挂桌面带并插到 other_window 之上（与 BandInputBox 同款）。"""
        try:
            if not win32_layer.attach_to_desktop(self):
                return False
            if other_window is not None:
                return win32_layer.insert_above_window(
                    int(self.winId()), int(other_window.winId()))
        except Exception:
            pass
        return False

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.NoPen)
        p.setBrush(FORM_BG)
        p.drawRoundedRect(r, FORM_RADIUS, FORM_RADIUS)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRoundedRect(r, FORM_RADIUS, FORM_RADIUS)
        # 标题
        p.setPen(theme.TEXT)
        p.setFont(theme.ui_font(13, bold=True))
        p.drawText(self._title_rect(), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        # 关闭 ×
        cr = self._close_rect()
        hovered = (self._mouse is not None and cr.contains(self._mouse))
        if hovered:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawEllipse(cr)
        p.setPen(QPen(theme.TEXT if hovered else theme.TEXT_DIM, 1.5,
                      Qt.SolidLine, Qt.RoundCap))
        cx, cy = cr.center().x(), cr.center().y()
        d = cr.width() * 0.24
        p.drawLine(QPoint(cx - d, cy - d), QPoint(cx + d, cy + d))
        p.drawLine(QPoint(cx + d, cy - d), QPoint(cx - d, cy + d))
        self.paint_content(p)

    def paint_content(self, p):
        """子类绘制内容区。"""

    # ---------- 交互 ----------
    def hit_content(self, pos):
        """返回点击到的内容 id（子类实现；None = 没点到）。"""
        return None

    def on_pick(self, key):
        """内容被点选（子类实现）。"""

    def mouseMoveEvent(self, e):
        self._mouse = e.pos()
        hit = None
        if self._close_rect().contains(e.pos()):
            hit = "close"
        else:
            hit = self.hit_content(e.pos())
        self.setCursor(Qt.PointingHandCursor if hit is not None
                       else Qt.ArrowCursor)
        self.update()

    def leaveEvent(self, e):
        self._mouse = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._close_rect().contains(e.pos()):
            self.hide_form()
            return
        key = self.hit_content(e.pos())
        if key is not None:
            self.on_pick(key)

    def hide_form(self):
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide_form()
            return
        super().keyPressEvent(e)


class AddAppsForm(FolderForm):
    """「＋」：从**当前桌面上的应用**里点选加入文件夹。

    候选由 `FolderPopup._add_candidates()` 给出（已排除本文件夹成员 /
    任何文件夹成员 / 非应用条目），这里只负责排版与点选。
    """

    picked = Signal(str)

    # v14 反馈 8：单行 5 列太挤（图标 34 + 名字 46px，中文名三个字就省略）。
    # 改成 4 列 + 更宽的格子 + 两行名字：格子从 52 加到 64，名字可用宽度
    # 从 46 到 58，第二行再给一半——长名终于看得全。
    COLS = 4
    VISIBLE_ROWS = 3          # 超过就滚动（滚轮）

    def __init__(self, storage, candidates, parent=None):
        super().__init__(storage, "添加应用", parent)
        self._cands = list(candidates)
        self._scroll = 0
        self._layout_cache = []
        self._resize()

    # ---------- 度量 ----------
    def _cell(self):
        m = self.m()
        icon = max(30, int(round(36 * m)))
        return icon, int(round(64 * m)), int(round(58 * m))

    def _rows(self):
        n = len(self._cands)
        cols = self.COLS
        need = (n + cols - 1) // cols
        return max(1, min(self.VISIBLE_ROWS, need))

    def _resize(self):
        p = self.pad()
        _icon, cw, chh = self._cell()
        rows = self._rows()
        w = p * 2 + cw * self.COLS
        h = self.title_h() + p + chh * rows + p
        self.setFixedSize(max(240, w), max(120, h))
        self._layout_cache = self._build_cells()

    def _build_cells(self):
        """[(QRect, entry_id)] —— 按可见行数铺；滚动偏移已计入。"""
        p = self.pad()
        _icon, cw, chh = self._cell()
        area_top = self.title_h() + p // 2
        out = []
        start = self._scroll * self.COLS
        for i, e in enumerate(self._cands[start:start + self.COLS * self.VISIBLE_ROWS]):
            r, c = i // self.COLS, i % self.COLS
            out.append((QRect(p + c * cw, area_top + r * chh, cw, chh),
                        e.get("id")))
        return out

    def max_scroll(self):
        rows = (len(self._cands) + self.COLS - 1) // self.COLS
        return max(0, rows - self.VISIBLE_ROWS)

    def scroll_by(self, delta):
        old = self._scroll
        self._scroll = max(0, min(self.max_scroll(), self._scroll + delta))
        if self._scroll != old:
            self._layout_cache = self._build_cells()
            self.update()

    def wheelEvent(self, e):
        self.scroll_by(-1 if e.angleDelta().y() > 0 else 1)

    # ---------- 绘制 ----------
    def paint_content(self, p):
        if not self._cands:
            p.setPen(theme.TEXT_DIM)
            p.setFont(theme.ui_font(11))
            p.drawText(self._content_rect(), Qt.AlignCenter,
                       "桌面上没有可添加的应用\n（已在文件夹里的不会重复列出）")
            return
        icon_size, _cw, _ch = self._cell()
        dpr = self.devicePixelRatioF()
        for rect, eid in self._layout_cache:
            e = self.storage.find_entry(eid)
            if e is None:
                continue
            app = e.get("app") or {}
            if self._mouse is not None and rect.contains(self._mouse):
                p.setPen(Qt.NoPen)
                p.setBrush(FORM_BG_HOVER)
                p.drawRoundedRect(QRectF(rect.adjusted(2, 2, -2, -2)), 8, 8)
            pm = appsource.icon_pixmap(app, icon_size, dpr)
            ix = rect.center().x() - icon_size // 2
            p.drawPixmap(ix, rect.top() + 2, pm)
            # 名字两行（v14 反馈 8）：中文按字、西文按词折行，末行省略
            p.setPen(theme.TEXT_DIM)
            p.setFont(theme.ui_font(10))
            name = app.get("name") or "?"
            fm = p.fontMetrics()
            for i, line in enumerate(_wrap_lines(name, fm, rect.width() - 6, 2)):
                p.drawText(QRect(rect.left() + 3,
                                 rect.top() + icon_size + 5 + i * 13,
                                 rect.width() - 6, 13),
                           Qt.AlignHCenter, line)
        # 滚动提示
        if self.max_scroll() > 0:
            p.setPen(theme.TEXT_FAINT)
            p.setFont(theme.ui_font(9))
            p.drawText(self._content_rect(), Qt.AlignRight | Qt.AlignBottom,
                       "%d/%d" % (self._scroll + 1, self.max_scroll() + 1))

    # ---------- 交互 ----------
    def hit_content(self, pos):
        for rect, eid in self._layout_cache:
            if rect.contains(pos):
                return eid
        return None

    def on_pick(self, eid):
        self.picked.emit(eid)


class TintForm(FolderForm):
    """「○」：文件夹背景色。预设两行 + 「自动」 + 「自定义」。"""

    tintPicked = Signal(object)     # None = 自动；QColor = 具体色

    COLS = 8

    def __init__(self, storage, current=None, parent=None):
        super().__init__(storage, "文件夹颜色", parent)
        self._current = current
        self._layout_cache = []
        self._resize()

    def _swatch(self):
        return max(20, int(round(24 * self.m())))

    def _gap(self):
        return max(6, int(round(9 * self.m())))

    def _resize(self):
        p = self.pad()
        s, g = self._swatch(), self._gap()
        cols = self.COLS
        rows = (len(self._entries()) + cols - 1) // cols
        w = p * 2 + cols * s + (cols - 1) * g
        h = self.title_h() + p // 2 + rows * s + (rows - 1) * g + p
        self.setFixedSize(max(220, w), max(110, h))
        self._layout_cache = self._build_cells()

    def _entries(self):
        """[(key, QColor|None|"liquid")]：液态玻璃 + 预设色 + 自动 + 自定义。

        v14 反馈 10：液态玻璃是**一种配色**，和具体颜色平级——所以它就排在
        色板第一位，选了它就不再有颜色（背景直接透出壁纸）。
        """
        out = [(LIQUID, "liquid")]
        out += [(c.name(), c) for c in theme.FOLDER_TINTS]
        out.append(("auto", None))
        out.append(("custom", "custom"))
        return out

    def _build_cells(self):
        p = self.pad()
        s, g = self._swatch(), self._gap()
        top = self.title_h() + p // 2
        out = []
        for i, (key, color) in enumerate(self._entries()):
            r, c = i // self.COLS, i % self.COLS
            out.append((QRect(p + c * (s + g), top + r * (s + g), s, s),
                        key, color))
        return out

    def paint_content(self, p):
        for rect, key, color in self._layout_cache:
            hovered = (self._mouse is not None and rect.contains(self._mouse))
            if key == LIQUID:
                self._paint_liquid_swatch(p, rect)
            elif key == "auto":
                p.setPen(QPen(theme.TEXT_DIM, 1))
                p.setBrush(theme.SURFACE_RAISED)
                p.drawEllipse(QRectF(rect))
                p.setPen(theme.TEXT)
                p.setFont(theme.ui_font(10, bold=True))
                p.drawText(rect, Qt.AlignCenter, "A")
            elif key == "custom":
                p.setPen(QPen(theme.TEXT_DIM, 1))
                p.setBrush(QColor(255, 255, 255, 18))
                p.drawEllipse(QRectF(rect))
                p.setPen(theme.TEXT)
                p.setFont(theme.ui_font(12, bold=True))
                p.drawText(rect, Qt.AlignCenter, "＋")
            else:
                p.setPen(QPen(theme.BORDER_STRONG, 1))
                p.setBrush(color)
                p.drawEllipse(QRectF(rect))
            if self._is_current(key, color):
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(theme.ACCENT, 2))
                p.drawEllipse(QRectF(rect).adjusted(-2, -2, 2, 2))
            elif hovered:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, 120), 1.5))
                p.drawEllipse(QRectF(rect).adjusted(-1, -1, 1, 1))

    def _paint_liquid_swatch(self, p, rect):
        """液态玻璃色块：一颗通透的镜片（不是某个具体颜色）。"""
        r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        c0 = QPointF(r.center().x() - r.width() * 0.12,
                     r.center().y() - r.height() * 0.12)
        grad = QRadialGradient(c0, max(1.0, r.width() * 0.72))
        grad.setColorAt(0.0, QColor(255, 255, 255, 210))
        grad.setColorAt(0.55, QColor(255, 255, 255, 120))
        grad.setColorAt(1.0, QColor(255, 255, 255, 40))
        # 透明底纹（灰白格）——"透明"的通用符号，一眼区别于纯色圆片
        p.save()
        clip = QPainterPath()
        clip.addEllipse(r)
        p.setClipPath(clip)
        p.setPen(Qt.NoPen)
        step = max(4.0, r.width() / 4.0)
        p.fillRect(QRectF(r), QColor(212, 214, 220))
        yy = r.top()
        row = 0
        while yy < r.bottom():
            xx = r.left() + (step if row % 2 else 0)
            while xx < r.right():
                p.fillRect(QRectF(xx, yy, step, step), QColor(150, 153, 162))
                xx += step * 2
            yy += step
            row += 1
        # 玻璃质感：半透明白 + 左上更亮（薄透镜）
        p.setBrush(QBrush(grad))
        p.drawEllipse(r)
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 205), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r)
        # 内侧一道暗弧：镜片的厚度（不是彩色高光）
        p.setPen(QPen(QColor(0, 0, 0, 70), 1.2))
        inner = r.adjusted(r.width() * 0.16, r.height() * 0.16,
                           -r.width() * 0.16, -r.height() * 0.16)
        p.drawArc(inner, 200 * 16, 120 * 16)

    def _is_current(self, key, color):
        cur = self._current
        if isinstance(cur, str):
            # 「自动」也以字符串哨兵传进来，必须先于液态判断（v15e）
            if cur.strip().lower() == AUTO:
                return key == "auto"
            return key == LIQUID
        if cur is None:
            return key == "auto"
        if not isinstance(color, QColor):
            return False
        return (cur.red(), cur.green(), cur.blue()) == (color.red(),
                                                        color.green(),
                                                        color.blue())

    def hit_content(self, pos):
        for rect, key, _color in self._layout_cache:
            if rect.contains(pos):
                return key
        return None

    def on_pick(self, key):
        if key == LIQUID:
            self.tintPicked.emit(LIQUID)
            self.hide_form()
            return
        if key == "auto":
            self.tintPicked.emit(None)
            self.hide_form()
            return
        if key == "custom":
            self._pick_custom()
            return
        for _rect, k, color in self._layout_cache:
            if k == key and isinstance(color, QColor):
                self.tintPicked.emit(color)
                self.hide_form()
                return

    def _pick_custom(self):
        """「自定义」：开 Qt 内置取色盘（不用系统原生对话框——它在桌面
        带的 NOACTIVATE 窗口上方可能被压住）。"""
        start = self._current or QColor(94, 92, 230)
        try:
            col = QColorDialog.getColor(
                start, self, "自定义文件夹颜色",
                QColorDialog.DontUseNativeDialog)
        except Exception:
            return
        if col.isValid():
            self.tintPicked.emit(col)
            self.hide_form()
