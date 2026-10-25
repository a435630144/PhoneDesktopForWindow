# -*- coding: utf-8 -*-
"""Dock 栏尺寸自定义（v11 反馈 29）。

用户原话：「新增给 dock 栏加入更多的自定义配置，比如 dock 栏的大小，dock 栏
内图标的间隔，大小比例等等。」

四个旋钮各管一件事，不许合并（合并就回到"想要更紧凑但图标别变小做不到"）：
    图标大小 dock_icon      图标本体边长
    图标间隔 dock_gap       图标之间
    内边距   dock_pad       条带厚度（条带"有多大"）
    悬停倍数 dock_hover_pct 指上去放大多少（= 大小比例）

契约：
  G1  读值夹紧：0~24 / 2~20 / 100~150；脏值回默认
  G2  metrics 接受 gap/pad/hover，厚度 = pad*2 + icon*hover
  G3  metrics_for 从 storage 读四个值（唯一入口）
  G4  间距改小 -> 条带变短；内边距改大 -> 条带变厚
  G5  hover 倍数进 DockMetrics，绘制/让位都用它（不再读模块常量）
  G6  设置页有四个步进器，初值来自 storage
  G7  改间隔/内边距/悬停 -> 写各自的键 + 立刻重排条带（轻量路径）
  G8  改图标大小仍走全量重排（要重建图标，不能省）

运行：python tests/test_dock_custom.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from src import dock
from src.settings_dialog import SettingsDialog
from src.storage import Storage

STATE = Path(tempfile.gettempdir()) / "test_dock_custom_state.json"
failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


class FakeWindow:
    attached = True

    def __init__(self):
        self.calls = []

    def update(self):
        self.calls.append("update")

    def apply_dock_style(self):
        self.calls.append("style")

    def apply_dock_metrics(self, reflow=True):
        self.calls.append(("metrics", bool(reflow)))

    def apply_dock_settings(self):
        self.calls.append("settings")


def main(app):
    if STATE.exists():
        STATE.unlink()
    st = Storage(path=str(STATE))
    win = FakeWindow()

    # ---------- G1 夹紧 ----------
    check("G1 默认值 = 模块常量",
          dock.gap_px(st) == dock.DOCK_GAP and dock.pad_px(st) == dock.DOCK_PAD
          and dock.hover_pct(st) == dock.DEFAULT_HOVER_PCT,
          "%s %s %s" % (dock.gap_px(st), dock.pad_px(st), dock.hover_pct(st)))
    st.set_setting("dock_gap", 999)
    st.set_setting("dock_pad", -5)
    st.set_setting("dock_hover_pct", 400)
    check("G1b 越界夹紧（24 / 2 / 150）",
          (dock.gap_px(st), dock.pad_px(st), dock.hover_pct(st)) == (24, 2, 150),
          "%s" % ((dock.gap_px(st), dock.pad_px(st), dock.hover_pct(st)),))
    st.set_setting("dock_gap", "oops")
    st.set_setting("dock_pad", None)
    st.set_setting("dock_hover_pct", "x")
    check("G1c 脏值回默认",
          (dock.gap_px(st), dock.pad_px(st), dock.hover_pct(st))
          == (dock.DOCK_GAP, dock.DOCK_PAD, dock.DEFAULT_HOVER_PCT),
          "%s" % ((dock.gap_px(st), dock.pad_px(st), dock.hover_pct(st)),))

    # ---------- G2 / G3 metrics ----------
    m = dock.metrics(1.0, 4, 32, 10, 8, 1.5)
    check("G2 图标/间隔/内边距按参数生效",
          m.icon == 32 and m.gap == 10 and m.pad == 8, str(m))
    check("G2b 厚度 = pad*2 + icon（v14r：不乘 hover）",
          m.thickness == 8 * 2 + 32, str(m.thickness))
    check("G2c 长度 = pad*2 + n*icon + (n-1)*gap",
          m.length == 8 * 2 + 4 * 32 + 3 * 10, str(m.length))
    check("G2d hover 进快照", abs(m.hover - 1.5) < 1e-9, str(m.hover))
    check("G2e 不传参数 = 模块默认（向后兼容）",
          dock.metrics(1.0, 4) == dock.metrics(1.0, 4, dock.DOCK_ICON,
                                              dock.DOCK_GAP, dock.DOCK_PAD,
                                              dock.DOCK_HOVER))

    st.set_setting("dock_gap", 0)
    st.set_setting("dock_pad", 4)
    st.set_setting("dock_hover_pct", 100)
    m2 = dock.metrics_for(st, 4)
    check("G3 metrics_for 读 storage（间隔 0 / 内边距 4 / 不放大）",
          m2.gap == 0 and m2.pad == 4 and abs(m2.hover - 1.0) < 1e-9, str(m2))
    check("G4 内边距变小 + 无放大 -> 厚度 = 8 + 默认图标",
          m2.thickness == 8 + dock.DOCK_ICON,
          str(m2.thickness))

    # ---------- G5 绘制使用 metrics.hover ----------
    st.set_setting("dock_pad", 6)
    st.set_setting("dock_gap", 6)
    st.set_setting("dock_hover_pct", 140)
    d = dock.DockWindow(st)
    d.resize(600, 120)
    d.apply_geometry((1920, 1080))
    d._hover_index = 1
    d._hover_t = 1.0
    grow140 = d._spread(2)
    st.set_setting("dock_hover_pct", 100)
    d._m = dock.metrics_for(st, len(d.items()))
    d._hover_index = 1
    d._hover_t = 1.0
    grow100 = d._spread(2)
    check("G5 让位位移随 hover 倍数变化（150% > 100%）",
          grow140 > grow100 >= 0, "%s vs %s" % (grow140, grow100))
    d.close()

    # ---------- G6 ~ G8 设置页 ----------
    st.set_setting("dock_gap", 8)
    st.set_setting("dock_pad", 9)
    st.set_setting("dock_hover_pct", 130)
    st.set_setting("dock_enabled", True)
    dlg = SettingsDialog(win, st, None, page="dock")
    check("G6 步进器都在（含四向内边距）",
          all(hasattr(dlg, n) for n in
              ("sp_dock_icon", "sp_dock_gap", "sp_dock_hover",
               "sp_dock_pad_top", "sp_dock_pad_bottom",
               "sp_dock_pad_left", "sp_dock_pad_right")))
    check("G6b 初值来自 storage",
          (dlg.sp_dock_gap.value(), dlg.sp_dock_pad_top.value(),
           dlg.sp_dock_hover.value()) == (8, 9, 130),
          "%s" % ((dlg.sp_dock_gap.value(), dlg.sp_dock_pad_top.value(),
                   dlg.sp_dock_hover.value()),))
    check("G6c 步进器值域正确",
          dlg.sp_dock_gap.minimum() == 0 and dlg.sp_dock_gap.maximum() == 24
          and dlg.sp_dock_pad_top.minimum() == 2
          and dlg.sp_dock_pad_top.maximum() == 20
          and dlg.sp_dock_hover.minimum() == 100
          and dlg.sp_dock_hover.maximum() == 150)

    win.calls.clear()
    dlg.sp_dock_gap.setValue(12)
    app.processEvents()
    check("G7 改间隔写 dock_gap", st.get_setting("dock_gap") == 12,
          repr(st.get_setting("dock_gap")))
    check("G7b 改间隔走轻量重排（reflow=False，不重建图标）",
          ("metrics", False) in win.calls, str(win.calls))
    win.calls.clear()
    dlg.sp_dock_pad_top.setValue(14)
    app.processEvents()
    check("G7c 改上内边距写 dock_pad_top",
          st.get_setting("dock_pad_top") == 14,
          repr(st.get_setting("dock_pad_top")))
    win.calls.clear()
    dlg.sp_dock_hover.setValue(150)
    app.processEvents()
    check("G7d 改悬停倍数写 dock_hover_pct",
          st.get_setting("dock_hover_pct") == 150,
          repr(st.get_setting("dock_hover_pct")))
    check("G7e 三项都走轻量重排",
          ("metrics", False) in win.calls, str(win.calls))

    # 悬停放大关掉时，倍数旋钮应禁用（不留"能点但无效"的旋钮）
    dlg.chk_dock_hover.setChecked(False)
    app.processEvents()
    check("G7f 关闭悬停放大 -> 倍数旋钮禁用",
          not dlg.sp_dock_hover.isEnabled())

    win.calls.clear()
    dlg.sp_dock_icon.setValue(40)
    app.processEvents()
    check("G8 改图标大小写 dock_icon", st.get_setting("dock_icon") == 40)
    check("G8b 改图标大小走全量重排（要重建图标）",
          "settings" in win.calls, str(win.calls))
    dlg.close()

    # ---------- G9 关闭 Dock 时整组禁用 ----------
    dlg2 = SettingsDialog(win, st, None, page="dock")
    dlg2.chk_dock.setChecked(False)
    app.processEvents()
    check("G9 关掉 Dock -> 尺寸组禁用",
          not dlg2.sp_dock_gap.isEnabled()
          and not dlg2.sp_dock_pad_top.isEnabled()
          and not dlg2.sp_dock_hover.isEnabled())
    dlg2.close()

    if STATE.exists():
        STATE.unlink()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    main(app)
    print("-" * 56)
    if failures:
        print("FAILED %d: %s" % (len(failures), failures))
        sys.exit(1)
    print("ALL dock_custom PASSED")
