# -*- coding: utf-8 -*-
"""v15e：设置面板刷新 / 模式联动 / 自动配色 / 面板最小尺寸（8.5.1 批次）。

这批修的都是"控件显示与实际不一致"的类别——用户改了没反应、或看到的
是上一次打开时的旧值。判据尽量做成**可独立调用**的纯逻辑，不依赖整套
窗口装配（设置面板构造 3400 行，太重）。

  Q1 「设置窗重开」必须重建：关掉后再打开不能复用隐藏实例（旧值）
  Q2 仅组件模式 ↔ 布局下拉：可用态与显示值都跟着 storage 走
  Q3 液态玻璃卡「重置」要同步 Dock 页的液态开关（否则被写回 False）
  Q4 「自动」配色是状态：成员变化后重算，色板能认出选中项
  Q5 面板最小尺寸按形态给：锚定 1 格、悬浮 200×260

运行：python tests/test_audit_v15e.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QComboBox, QLabel

from src import dock, folder_tint, grid_flow
from src.folder_popup import FolderPopup
from src.settings_dialog import SettingsDialog
from src.storage import Storage
from src.widget_base import FloatingWidget
from src.widgets.panel.panel import DesktopPanel

app = QApplication.instance() or QApplication([])
TMP = Path(tempfile.gettempdir()) / "test_audit_v15e.json"
failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def fresh():
    if TMP.exists():
        TMP.unlink()
    st = Storage(path=str(TMP))
    st._data["settings"].update({"auto_clone_desktop": False,
                                 "hide_native_icons": True,
                                 "takeover_desktop": True})
    st.save()
    return st


# --------------------------- Q1 设置窗重开重建 ---------------------------
import src.settings_dialog as sd


class FakeDlg:
    made = 0

    def __init__(self, *a, **k):
        FakeDlg.made += 1
        self.visible = False
        self.deleted = False
        self.pages = []

    def select_page(self, page):
        self.pages.append(page)

    def show(self):
        self.visible = True

    def raise_(self):
        pass

    def raise_(self):       # noqa: F811 —— 占位，保持接口形状
        pass

    def activateWindow(self):
        pass

    def isVisible(self):
        return self.visible

    def deleteLater(self):
        self.deleted = True
        self.visible = False


def q1():
    real = sd.SettingsDialog
    sd.SettingsDialog = FakeDlg
    sd._dialog = None
    FakeDlg.made = 0
    try:
        st = fresh()
        d1 = sd.open_settings(None, st, None)
        check("Q1 首次打开新建实例", FakeDlg.made == 1 and d1 is not None)
        # 模拟用户点「完成」：QDialog 只是隐藏，对象还活着
        d1.visible = False
        d2 = sd.open_settings(None, st, None)
        check("Q1b 关闭后重开不复用隐藏实例", d2 is not d1 and FakeDlg.made == 2,
              "made=%d same=%r" % (FakeDlg.made, d2 is d1))
        check("Q1c 旧实例被销毁", d1.deleted is True)
        # 开着的时候仍然复用（不要每点一次托盘就重建）
        d3 = sd.open_settings(None, st, None)
        check("Q1d 可见时复用同一实例", d3 is d2 and FakeDlg.made == 2)
    finally:
        sd.SettingsDialog = real
        sd._dialog = None


# --------------------------- Q2 模式下拉同步 ---------------------------
def q2():
    st = fresh()
    dlg = SettingsDialog.__new__(SettingsDialog)
    dlg.storage = st
    cmb = QComboBox()
    cmb.addItem("网格", "grid")
    cmb.addItem("无界", "free")
    cmb._hint_label = QLabel("hint")
    dlg.cmb_layout_mode = cmb

    st.set_setting("widgets_only", True)
    st.set_setting("layout_mode", "free")
    dlg._sync_layout_mode_controls()
    check("Q2 仅组件模式：下拉锁死且显示无界",
          cmb.isEnabled() is False and cmb.currentData() == "free",
          "enabled=%r cur=%r" % (cmb.isEnabled(), cmb.currentData()))
    check("Q2b 提示文案切成锁定说明",
          "锁定" in cmb._hint_label.text() or cmb._hint_label.text() != "hint",
          cmb._hint_label.text()[:24])

    st.set_setting("widgets_only", False)
    st.set_setting("layout_mode", "grid")
    dlg._sync_layout_mode_controls()
    check("Q2c 关掉仅组件模式：下拉恢复可选且跟着 storage",
          cmb.isEnabled() is True and cmb.currentData() == "grid",
          "enabled=%r cur=%r" % (cmb.isEnabled(), cmb.currentData()))


# --------------------------- Q3 液态重置同步 Dock 开关 ---------------------------
class FakeWindow:
    def __init__(self, storage):
        self.storage = storage

    def rebuild(self, stagger=False):
        pass

    def refresh_grid(self):
        pass

    def apply_native_icons_setting(self):
        pass

    def set_layout_mode(self, mode):
        self.storage.set_setting("layout_mode", mode)


def q3():
    st = fresh()
    st.set_setting("liquid_dock", False)
    dlg = SettingsDialog(FakeWindow(st), st, None, page="liquid")
    box = getattr(dlg, "chk_dock_liquid", None)
    check("Q3 液态重置前：Dock 开关是关的", box is not None and not box.isChecked())
    dlg._reset_liquid()
    check("Q3b 重置后 storage.liquid_dock=True",
          bool(st.get_setting("liquid_dock")) is True)
    check("Q3c 重置后 Dock 页开关同步为开",
          box is not None and box.isChecked() is True,
          "checked=%r" % (None if box is None else box.isChecked()))
    # 用户随后动一下 Dock：不能把刚才的 True 写回 False
    dlg._apply_dock()
    check("Q3d 再改 Dock 不会把液态写回关",
          bool(st.get_setting("liquid_dock")) is True)


# --------------------------- Q4 自动配色是状态 ---------------------------
class Sig:
    def __init__(self):
        self.n = 0

    def emit(self):
        self.n += 1


class SaveCounter:
    def __init__(self):
        self.n = 0

    def save(self):
        self.n += 1


def q4():
    entry = {"id": "F", "type": "folder", "tint": folder_tint.AUTO,
             "tint_auto": "#112233",
             "apps": [{"name": "a", "path": "C:/x/a.lnk"}]}
    check("Q4 自动状态可识别", folder_tint.folder_auto(entry) is True)
    check("Q4b 解析走 tint_auto 缓存",
          folder_tint.folder_tint(entry).name() == "#112233")

    pop = FolderPopup.__new__(FolderPopup)
    pop.entry = entry
    pop.storage = SaveCounter()
    pop.tintChanged = Sig()
    pop.auto_tint = staticmethod(lambda apps: QColor(1, 2, 3))
    changed = pop._sync_auto_tint()
    check("Q4c 成员变化后重算并落盘",
          changed is True and entry["tint_auto"] == "#010203"
          and pop.storage.n == 1 and pop.tintChanged.n == 1,
          "tint_auto=%r save=%d sig=%d" % (entry["tint_auto"],
                                           pop.storage.n, pop.tintChanged.n))
    check("Q4d 无变化时不重复写", pop._sync_auto_tint() is False)

    # 具体色 / 液态仍然是显式值，不能被"自动"劫持
    entry2 = {"tint": "#5e5ce6"}
    check("Q4e 显式色不受影响",
          folder_tint.folder_auto(entry2) is False
          and folder_tint.folder_tint(entry2).name() == "#5e5ce6")
    # 坏缓存回退预设
    entry3 = {"tint": "auto", "tint_auto": "not-a-color"}
    check("Q4f 坏缓存回退预设",
          folder_tint.folder_tint(entry3).name()
          == folder_tint.theme.FOLDER_TINTS[0].name())


# --------------------------- Q5 面板最小尺寸按形态 ---------------------------
def q5():
    st = fresh()
    grid_flow.set_grid(st, (1920, 1040))
    m = grid_flow.current_metrics()
    panel = DesktopPanel.__new__(DesktopPanel)
    panel.entry = {"anchored": True, "type": "panel"}
    check("Q5 锚定态最小尺寸 = 1 格",
          panel._mode_minimum() == (m.cw, m.ch),
          "%r vs %r" % (panel._mode_minimum(), (m.cw, m.ch)))
    panel.entry = {"anchored": False, "type": "panel"}
    check("Q5b 悬浮态保留内容下限 200×260",
          panel._mode_minimum() == (200, 260), panel._mode_minimum())


# --------------------------- Q6 Dock 四向内边距按方向生效 ---------------------------
def q6():
    st = fresh()
    st.set_setting("dock_position", "bottom")
    st.set_setting("dock_pad_top", 4)
    st.set_setting("dock_pad_bottom", 4)
    st.set_setting("dock_pad_left", 20)
    st.set_setting("dock_pad_right", 4)
    st.save()
    n = 6
    m = dock.metrics_for(st, n)
    icon, gap = m.icon, m.gap
    check("Q6 底边 Dock：厚度只由上下内边距决定",
          m.thickness == 4 + 4 + icon,
          "thickness=%d icon=%d（期望 %d）" % (m.thickness, icon, 8 + icon))
    check("Q6b 长度按左右内边距",
          m.length == 24 + n * icon + (n - 1) * gap,
          "length=%d（期望 %d）" % (m.length, 24 + n * icon + (n - 1) * gap))
    bar = (0, 1000 - m.thickness, m.length, m.thickness)
    x0, y0, _w, _h = dock.item_rect(bar, "bottom", m, 0)
    check("Q6c 第一个图标左内边距 = 20", x0 - bar[0] == 20, x0 - bar[0])
    xl, _yl, wl, _hl = dock.item_rect(bar, "bottom", m, n - 1)
    check("Q6d 最后一个图标右边不越界",
          xl + wl == bar[0] + m.length - 4, xl + wl - (bar[0] + m.length))

    # 左侧 Dock：上 40 / 下 4 / 左右 4 —— 主轴变成上下
    st.set_setting("dock_position", "left")
    st.set_setting("dock_pad_top", 18)
    st.set_setting("dock_pad_bottom", 4)
    st.set_setting("dock_pad_left", 4)
    st.set_setting("dock_pad_right", 4)
    st.save()
    m2 = dock.metrics_for(st, n)
    check("Q6e 左侧 Dock：厚度按左右内边距",
          m2.thickness == 4 + 4 + icon, m2.thickness)
    check("Q6f 左侧 Dock：长度按上下内边距",
          m2.length == 22 + n * icon + (n - 1) * gap,
          "length=%d（期望 %d）" % (m2.length, 22 + n * icon + (n - 1) * gap))
    bar2 = (0, 0, m2.thickness, m2.length)
    _x, y2, _w2, _h2 = dock.item_rect(bar2, "left", m2, 0)
    check("Q6g 左侧 Dock 第一项上内边距 = 18", y2 == 18, y2)


# --------------------------- Q7 夹紧的跨度不许反推成更大 ---------------------------
def q7():
    st = fresh()
    grid_flow.set_grid(st, (1920, 1040))
    from PySide6.QtCore import QRect

    def fake(card_w, card_h, min_h, span_from=(3, 3)):
        w = FloatingWidget.__new__(FloatingWidget)
        w.storage = st
        w.entry = {"id": "P", "type": "panel", "anchored": True,
                   "col": 0, "row": 0, "colspan": 3, "rowspan": 1}
        w.card_rect = lambda: QRect(0, 0, card_w, card_h)
        w.minimumWidth = lambda: 84
        w.minimumHeight = lambda: min_h
        w._span_from_geometry = lambda: span_from
        w.written = []
        w._write_occupancy = lambda col, row, cs, rs: w.written.append((cs, rs))
        w._chrome_pad = lambda: 0
        return w

    hit = fake(227, 260, 260)
    changed = hit._sync_span_from_geometry()
    check("Q7 尺寸被最小高度夹住时不把跨度撑大",
          changed is False and hit.written == [],
          "changed=%r written=%r" % (changed, hit.written))

    from src import widget_geom
    want_w, want_h = widget_geom.card_size(st, (3, 3))
    ok = fake(want_w, want_h, 91)
    changed2 = ok._sync_span_from_geometry()
    check("Q7b 真正拖大仍会同步跨度（不误挡）",
          changed2 is True and ok.written == [(3, 3)],
          "changed=%r written=%r want=%r" % (changed2, ok.written,
                                             (want_w, want_h)))


for fn in (q1, q2, q3, q4, q5, q6, q7):
    try:
        fn()
    except Exception as exc:      # noqa: BLE001 —— 单条异常不掩盖其它判据
        import traceback
        traceback.print_exc()
        failures.append("%s 异常：%s" % (fn.__name__, exc))

print("-" * 56)
print("%d FAIL" % len(failures))
if failures:
    print("FAILED:", ", ".join(failures))
# Qt 顶层窗析构偶发段错误（见 test_folder_popup 同款注释）：直接退。
# os._exit 不刷缓冲——先 flush，否则重定向到管道时一个字都看不到。
sys.stdout.flush()
os._exit(1 if failures else 0)
