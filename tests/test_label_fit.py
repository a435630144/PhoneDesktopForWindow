# -*- coding: utf-8 -*-
"""v15a / 8.3.4：图标字号放大后标签必须仍在部件内（不裁剪）。

用户反馈（2026-10-27）："当图标的字体调节较大的时候，第二行的字体会被
hover 框给裁剪，形成 UI bug。"

根因是**布局不知道字号缩放**：
  * `grid_sizing.label_height()` 只按基准字号算，`icon_layout()` 也照此
    预留标签区 —— 而 `AppIcon` 绘制时把 `font_px` 又乘了
    `label_style.font_scale`（70~140%）。字号涨了、预留高度没涨。
  * `_label_items()` 的标签矩形从 `icon_size + 2` 起、高度仍是旧
    `label_h`，两行必然越过部件底边（120% 时 +2px，140% 时干脆只剩一行）。
    QWidget 的绘制被自身矩形裁剪，而 hover 高亮框正好画在同一个边界上，
    看上去就是"被 hover 框切掉了"。

契约：
  F1  `label_height()` 吃字号缩放：1.4 倍时两行带比 1.0 倍高
  F2  `icon_layout()` 按缩放后的字号预留标签区，且 widget 永不越过 cell
  F3  真实 AppIcon：字号 1.4 时两行标签的矩形全在部件矩形内
  F4  字号 1.0 的 layout 数值与旧规格逐字段一致（零回归）
  F5  格内居中留白与 layout 同源（放大后图标仍居中、不越格）
  F6  窗口热更新：`apply_label_style()` 改字号 -> 部件尺寸跟着变且不重建；
      三次往返后尺寸逐字段相等（单一真相源）

运行：python tests/test_label_fit.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src import grid_flow, grid_sizing as gs, label_style, theme  # noqa: E402
from src.desktop_window import DesktopWindow  # noqa: E402
from src.iconwidget import AppIcon  # noqa: E402
from src.storage import Storage  # noqa: E402

LONG = "这是一个非常长的应用名称需要折行显示"
failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def _entry(eid="a", name=LONG):
    return {"id": eid, "type": "app", "col": 0, "row": 0,
            "app": {"name": name, "kind": "app", "path": "C:/x/a.exe"}}


def _machine(tmp, name, scale=140):
    st = Storage(path=str(tmp / ("%s.json" % name)))
    st._data["settings"]["columns"] = 8
    st._data["settings"]["auto_arrange"] = False
    st._data["settings"]["label_font_scale"] = scale
    st._data["layout"] = [_entry(), _entry("b", "B")]
    st.save()
    return st


def _rows_inside(icon):
    """标签行矩形是否全部落在部件内（bottom 不含：QRect.bottom = y+h-1）。"""
    out = []
    for r, _flags, _line in icon._label_items():
        out.append((r.y(), r.bottom(), icon.height(), r.bottom() < icon.height()))
    return out


def main(app, tmp):
    # ---------- F1：标签带吃字号缩放 ----------
    h10 = gs.label_height(48, 2, 1.0)
    h14 = gs.label_height(48, 2, 1.4)
    check("F1a 1.4 倍字号的两行标签带更高", h14 > h10, "%d vs %d" % (h14, h10))
    check("F1b 缺省参数 = 1.0（旧调用零改动）",
          gs.label_height(48, 2) == h10 and gs.label_height(48) == gs.label_height(48, 1, 1.0),
          "%s" % (gs.label_height(48),))

    # ---------- F2：layout 按缩放字号预留，且不越格 ----------
    lay0 = gs.icon_layout(84, 91, "medium")
    lay14 = gs.icon_layout(84, 91, "medium", font_scale=1.4)
    fpx = max(9, min(40, int(round(lay14.font_px * 1.4))))
    fm = QFontMetrics(theme.ui_font(fpx))
    need2 = 2 * (fm.height() + 1) - 2
    check("F2a widget 不越过 cell 高", lay14.widget_h <= 91,
          "widget_h=%d" % lay14.widget_h)
    check("F2b 标签带装得下两行放大字号", lay14.label_h >= need2,
          "label_h=%d need=%d (font=%d)" % (lay14.label_h, need2, fpx))
    check("F2c font_px 仍是基准字号（缩放由 AppIcon 乘）",
          lay14.font_px == lay0.font_px, "%s vs %s" % (lay14.font_px, lay0.font_px))
    check("F2d 1.0 倍时仍给两行且高度不变",
          lay0.label_h == gs.label_height(lay0.icon_box, 2),
          "label_h=%s" % lay0.label_h)

    # ---------- F3：真实图标，两行都在部件内 ----------
    st = _machine(tmp, "fit", 140)
    style = label_style.label_style(st)
    ic = AppIcon(_entry(), 0, layout=lay14, label_style=style)
    rows = _rows_inside(ic)
    check("F3a 字号 1.4 仍是两行", len(rows) == 2, "%d 行" % len(rows))
    check("F3b 两行都在部件矩形内",
          all(ok for _y, _b, _h, ok in rows),
          "%s (h=%d, font=%d)" % (rows, ic.height(), ic._label_font_px))
    check("F3c 字号确实被放大", ic._label_font_px == fpx,
          "%s vs %s" % (ic._label_font_px, fpx))

    # 扫一遍所有档位 x 格子尺寸：任何组合都不许画到部件之外
    # （旧实现的"按预留高度硬画两行"在 120% 就会越界；这里用矩阵替代
    #  某个具体像素值，不受测试机字体度量影响）
    bad = []
    for cell in ((84, 91), (72, 78), (110, 119)):
        for pct in (100, 110, 120, 130, 140):
            lay = gs.icon_layout(cell[0], cell[1], "medium",
                                 font_scale=pct / 100.0)
            st_tmp = _machine(tmp, "sweep_%d_%d" % (cell[0], pct), pct)
            ico = AppIcon(_entry(), 0, layout=lay,
                          label_style=label_style.label_style(st_tmp))
            rows = _rows_inside(ico)
            if (not rows or any(not ok for *_x, ok in rows)
                    or ico.height() > cell[1]):
                bad.append((cell, pct, rows, ico.height(), lay.label_h))
    check("F3d 全档位 × 全格子：标签都在部件内且不越格", not bad,
          str(bad[:3]))
    check("F3e 最大字号仍保留两行（84x91 格）",
          len(_rows_inside(AppIcon(
              _entry(), 0, layout=gs.icon_layout(84, 91, "medium",
                                                 font_scale=1.4),
              label_style=label_style.label_style(
                  _machine(tmp, "two", 140))))) == 2)

    # ---------- F4：1.0 倍逐字段零回归 ----------
    base83 = gs.icon_layout(83, 102, "medium")
    check("F4 默认 layout 规格不变",
          tuple(base83) == (61, 79, 47, 7, 32, 10), "%s" % (tuple(base83),))

    # ---------- F5：格内居中与 layout 同源 ----------
    grid_flow.set_grid(st, (1600, 1000))
    m = grid_flow.current_metrics()
    dx, dy = grid_flow.icon_center_offset(st, _entry())
    lay = gs.icon_layout(m.cw, m.ch, st.resolved_icon_fit(m.cw),
                         font_scale=1.4)
    check("F5a 居中偏移 = 格内余量一半（与 layout 同源）",
          (dx, dy) == ((m.cw - lay.widget_w) // 2,
                       (m.ch - lay.widget_h) // 2),
          "offset=%s lay=%s cell=%sx%s" % ((dx, dy), tuple(lay), m.cw, m.ch))
    check("F5b 放大后仍不越格", lay.widget_w <= m.cw and lay.widget_h <= m.ch,
          "widget=%dx%d cell=%dx%d"
          % (lay.widget_w, lay.widget_h, m.cw, m.ch))

    # ---------- F6：窗口热更新（不重建部件） ----------
    win = DesktopWindow(st)
    win.show()
    app.processEvents()
    icon = win.icons["a"]
    h14 = icon.height()
    check("F6a 初始高度 = 1.4 倍 layout",
          h14 == lay14.widget_h, "%d vs %d" % (h14, lay14.widget_h))

    st.set_setting("label_font_scale", 100)
    win.apply_label_style()
    app.processEvents()
    h10_win = icon.height()
    check("F6b 调回 100% 时部件变矮", h10_win < h14, "%d vs %d" % (h10_win, h14))
    check("F6c 热更新不重建部件", win.icons["a"] is icon)
    check("F6d 位置跟着重新居中",
          icon.pos().y() == win._entry_pos(icon.entry)[1],
          "y=%d want=%d" % (icon.pos().y(), win._entry_pos(icon.entry)[1]))
    check("F6e 100% 时标签仍在部件内",
          all(ok for *_x, ok in _rows_inside(icon)), str(_rows_inside(icon)))

    first = (icon.width(), icon.height())
    for _ in range(3):
        st.set_setting("label_font_scale", 140)
        win.apply_label_style()
        st.set_setting("label_font_scale", 100)
        win.apply_label_style()
    check("F6f 三次往返后尺寸逐字段相等",
          (icon.width(), icon.height()) == first,
          "%s vs %s" % ((icon.width(), icon.height()), first))
    check("F6g 往返后仍在格内",
          icon.height() <= grid_flow.current_metrics().ch,
          "%d vs %d" % (icon.height(), grid_flow.current_metrics().ch))
    win.close()


def run():
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="labelfit-"))
    try:
        main(app, tmp)
    except Exception:
        import traceback
        traceback.print_exc()
        failures.append("main() 异常")
    print("-" * 56)
    print("%d checks, %d failed" % (total[0], len(failures)))
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
