# -*- coding: utf-8 -*-
"""开发者选项分页 + 分项开关（v11 反馈 32）。

用户原话：「设置面板给开发者选项单独设置一个 sheet，首先是顶部开关来控制
开发者选项是否开启，关闭状态下则后续功能变回无法交互，如果开启，则对每项
功能分别设置开启和关闭的开关，现在所有的功能都集成在了网格调试信息里。」

契约：
  V1  四个分项设置默认全开（总闸打开 = 旧行为，零迁移）
  V2  「开发者」分页存在：总开关 + 四个分项 + 网格原点整组
  V3  总闸关闭 -> 分项开关与原点整组禁用（不可交互）
  V4  总闸打开 -> 分项解禁；每个分项写自己的键，不串
  V5  `_dev_flag`：总闸关一律 False；开则按分项返回
  V6  像素级：只关「网格线」时不再画绿线/品红原点，但其它项照旧
  V7  像素级：只关「组件碰撞」时不再铺红底，网格线仍在
  V8  取点态闪网格与总闸无关（force_grid 绕过总开关）

运行：python tests/test_dev_options.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from src import grid_flow
from src.desktop_window import DesktopWindow
from src.settings_dialog import SettingsDialog
from src.storage import DEFAULT_SETTINGS, Storage

W, H = 1920, 1040
STATE = Path(tempfile.gettempdir()) / "test_dev_options_state.json"
FLAGS = ("dev_grid", "dev_widget_collision", "dev_perf", "dev_window_check")

failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def make_state(**flags):
    if STATE.exists():
        STATE.unlink()
    st = Storage(path=str(STATE))
    st._data["settings"]["hide_native_icons"] = True     # 整理模式
    st._data["settings"]["auto_clone_desktop"] = False
    st._data["settings"]["dev_mode"] = flags.pop("dev", True)
    st._data["layout"] = []
    if flags.pop("widget", False):
        st._data["widgets"] = [{"id": "wm1", "type": "monitor", "hidden": False,
                               "anchored": True, "col": 0, "row": 0,
                               "colspan": 2, "rowspan": 2}]
    for k, v in flags.items():
        st._data["settings"][k] = bool(v)
    st.save()
    return st


def _is_green(px: QColor) -> bool:
    return (px.green() > 90 and px.green() - px.red() > 50
            and px.green() - px.blue() > 30)


def _is_pink(px: QColor) -> bool:
    """原点十字签名：品红 (255,64,160)——蓝显著高于绿。

    不能只用"红高 + 有点蓝"：组件碰撞红底的边缘像素叠在组件白玻璃上会混成
    (252,102,103) 这种三文鱼色（V6b 曾被它咬住）。十字的蓝永远 > 130，
    混色伪影的蓝只有 ~100，用蓝/绿差把两者分开。
    """
    return px.red() > 200 and px.blue() > 130 and px.green() < 110


def _is_red(px: QColor) -> bool:
    return (px.red() > 90 and px.red() - px.green() > 25
            and px.red() - px.blue() > 15)


def _count(img, x0, y0, x1, y1, pred) -> int:
    n = 0
    for yy in range(max(0, y0), min(img.height(), y1)):
        for xx in range(max(0, x0), min(img.width(), x1)):
            if pred(img.pixelColor(xx, yy)):
                n += 1
    return n


def render_window(w) -> QImage:
    img = QImage(w.size(), QImage.Format_ARGB32)
    img.fill(0)
    w.render(img)
    return img


def main(app):
    # ---------- V1 默认值 ----------
    for key in FLAGS:
        check("V1 %s 默认 True（总闸打开即旧行为）" % key,
              DEFAULT_SETTINGS.get(key) is True, repr(DEFAULT_SETTINGS.get(key)))

    # ---------- V2 / V3 / V4 设置页 ----------
    st = make_state(dev=False)
    dlg = SettingsDialog(SimpleNamespace(attached=True, update=lambda: None),
                         st, None, page="advanced")
    check("V2 开发者分页已选中", dlg.stack.currentIndex() == 8,
          "idx=%d" % dlg.stack.currentIndex())
    subs = getattr(dlg, "_dev_switches", {})
    check("V2b 四个分项开关都在", len(subs) == 4 and set(subs) == set(FLAGS),
          str(sorted(subs)))
    check("V3 总闸关闭 -> 分项全部禁用",
          all(not s.isEnabled() for s in subs.values()),
          str([s.isEnabled() for s in subs.values()]))
    check("V3b 总闸关闭 -> 网格原点整组禁用",
          not dlg.chk_origin.isEnabled() and not dlg.btn_pick_origin.isEnabled())
    dlg.chk_dev.setChecked(True)
    app.processEvents()
    check("V4 总闸打开 -> 分项解禁",
          all(s.isEnabled() for s in subs.values()))
    check("V4b 总闸落盘", st.get_setting("dev_mode") is True)

    subs["dev_grid"].setChecked(False)
    app.processEvents()
    check("V4c 关掉「网格线」只写自己那个键",
          st.get_setting("dev_grid") is False
          and st.get_setting("dev_widget_collision") is True
          and st.get_setting("dev_perf") is True
          and st.get_setting("dev_window_check") is True)
    subs["dev_perf"].setChecked(False)
    app.processEvents()
    check("V4d 关掉「帧率」不牵连别的",
          st.get_setting("dev_perf") is False
          and st.get_setting("dev_widget_collision") is True)
    dlg.chk_dev.setChecked(False)
    app.processEvents()
    check("V4e 总闸再关 -> 分项重新禁用",
          all(not s.isEnabled() for s in subs.values()))
    dlg.close()

    # ---------- V5 _dev_flag ----------
    st5 = make_state(dev=False, dev_grid=True)
    w5 = DesktopWindow(st5)
    w5.resize(W, H)
    w5._update_grid_metrics()
    check("V5 总闸关 -> 分项恒 False（哪怕键是 True）",
          w5._dev_flag("dev_grid") is False
          and w5._dev_flag("dev_perf") is False)
    st5.set_setting("dev_mode", True)
    check("V5b 总闸开 -> 按分项返回",
          w5._dev_flag("dev_grid") is True
          and w5._dev_flag("dev_widget_collision") is True)
    st5.set_setting("dev_grid", False)
    check("V5c 分项关 -> False", w5._dev_flag("dev_grid") is False)
    del st5._data["settings"]["dev_perf"]
    check("V5d 旧 state 缺键 -> 默认 True（零迁移）",
          w5._dev_flag("dev_perf") is True)
    w5.close()

    # ---------- V6 只关网格线：无绿线/原点，但碰撞红底仍在 ----------
    st6 = make_state(dev=True, widget=True, dev_grid=False)
    w6 = DesktopWindow(st6)
    w6.resize(W, H)
    w6._update_grid_metrics()
    m6 = grid_flow.current_metrics()
    img6 = render_window(w6)
    mid6 = m6.ox + m6.cols * m6.cw // 2
    green6 = _count(img6, mid6 - 40, m6.oy - 2, mid6 + 40, m6.oy + 4, _is_green)
    pink6 = _count(img6, m6.ox - 4, m6.oy - 4, m6.ox + 6, m6.oy + 6, _is_pink)
    check("V6 关掉网格线 -> 边界无绿线", green6 == 0, "green=%d" % green6)
    check("V6b 关掉网格线 -> 无原点十字", pink6 == 0, "pink=%d" % pink6)
    rect6 = w6._widget_collision_rects()[0][0]
    red6 = _count(img6, rect6.x() + 3, rect6.y() + 3,
                  rect6.right() - 3, rect6.bottom() - 3, _is_red)
    check("V6c 碰撞红底不受影响（分项独立）", red6 > 200, "red=%d" % red6)
    w6.close()

    # ---------- V7 只关碰撞：无红底，网格线仍在 ----------
    st7 = make_state(dev=True, widget=True, dev_widget_collision=False)
    w7 = DesktopWindow(st7)
    w7.resize(W, H)
    w7._update_grid_metrics()
    m7 = grid_flow.current_metrics()
    img7 = render_window(w7)
    mid7 = m7.ox + m7.cols * m7.cw // 2
    green7 = _count(img7, mid7 - 40, m7.oy - 2, mid7 + 40, m7.oy + 4, _is_green)
    rect7 = w7._widget_collision_rects()[0][0]
    red7 = _count(img7, rect7.x() + 3, rect7.y() + 3,
                  rect7.right() - 3, rect7.bottom() - 3, _is_red)
    check("V7 关掉碰撞 -> 无红底", red7 == 0, "red=%d" % red7)
    check("V7b 网格线仍在（分项独立）", green7 > 0, "green=%d" % green7)
    w7.close()

    # ---------- V8 force_grid 绕过总闸 ----------
    st8 = make_state(dev=False, dev_grid=False)
    w8 = DesktopWindow(st8)
    w8.resize(W, H)
    w8._update_grid_metrics()
    m8 = grid_flow.current_metrics()
    # 真实路径：`_flash_grid_debug` 置 _grid_flash -> paintEvent 用 force_grid
    # 重绘一次。直接调 _paint_grid_debug 会被随后的 render 覆盖（同一块画布）。
    w8._grid_flash = True
    img8 = render_window(w8)
    green8 = _count(img8, m8.ox - 2, m8.oy + 20, m8.ox + 4, m8.oy + 90, _is_green)
    check("V8 取点闪网格不受总闸限制（force_grid）", green8 > 0, "green=%d" % green8)
    w8.close()

    if STATE.exists():
        STATE.unlink()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    main(app)
    print("-" * 56)
    if failures:
        print("FAILED %d: %s" % (len(failures), failures))
        sys.exit(1)
    print("ALL dev_options PASSED")
