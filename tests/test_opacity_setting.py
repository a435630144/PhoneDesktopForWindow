# -*- coding: utf-8 -*-
"""v14i：悬浮兜底不透明度固定化——`opacity` 用户设置退役。

历史：v10 A8（反馈 14）把"悬浮模式不透明度"做成挂载态置灰的滑条；v14h
梳理时迁到「模式」页；v14i 用户定论——悬浮形态只是**挂载失败的兜底**
（正常机器永远见不到），为它留一个"挂载态永远无效"的设置只会造成困惑，
改用固定值 `desktop_window.FLOATING_OPACITY`（=0.95，即原出厂默认）。

判据：
  O1 DEFAULT_SETTINGS 不再有 opacity 键；设置面板不再有 sld_opacity
  O2 兜底窗不透明度恒 0.95——老 state.json 里残留 0.42 也不受影响
  O3 悬挂态托盘隐藏/再现往返仍是 0.95（固定值不依赖 storage）
  O4 挂载态恒 1.0（分层窗全局 alpha 会连带命中测试——透明区穿透的教训）

运行：python tests/test_opacity_setting.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication      # noqa: E402

from src import desktop_window as dw            # noqa: E402
from src.desktop_window import DesktopWindow    # noqa: E402
from src.settings_dialog import SettingsDialog  # noqa: E402
from src.storage import DEFAULT_SETTINGS, Storage   # noqa: E402

STATE = Path(tempfile.gettempdir()) / "test_opacity_state.json"
failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def make_state(opacity=0.95):
    if STATE.exists():
        STATE.unlink()
    st = Storage(path=str(STATE))
    st._data["settings"]["auto_clone_desktop"] = False
    st._data["settings"]["opacity"] = opacity   # 模拟老用户 state.json 残留
    st._data["layout"] = []
    st.save()
    return st


class OpacitySpy:
    """记录 setWindowOpacity 的调用值（offscreen 下不保证回显，记录调用
    值才是确定性判据）。"""

    def __init__(self):
        self.calls = []

    def __call__(self, v):
        self.calls.append(float(v))


def main(app):
    # ---------- O1：设置退役 ----------
    check("O1 DEFAULT_SETTINGS 不再有 opacity 键",
          "opacity" not in DEFAULT_SETTINGS,
          "%s" % DEFAULT_SETTINGS.get("opacity"))
    st = make_state(opacity=0.42)
    dlg = SettingsDialog(type("W", (), {"attached": False})(), st, None)
    check("O1b 设置面板不再有不透明度滑条", not hasattr(dlg, "sld_opacity"))
    check("O1c 常量固定 0.95", dw.FLOATING_OPACITY == 0.95,
          "%.2f" % dw.FLOATING_OPACITY)
    dlg.deleteLater()

    # ---------- O2：兜底窗恒 0.95（残留值不影响） ----------
    w2 = DesktopWindow(make_state(opacity=0.42))
    w2.resize(1920, 1040)
    spy2 = OpacitySpy()
    w2.setWindowOpacity = spy2
    w2._enter_floating_mode()
    check("O2 兜底窗不透明度恒 0.95（无视残留 0.42）",
          bool(spy2.calls) and spy2.calls[-1] == 0.95, "calls=%s" % spy2.calls)

    # ---------- O3：悬挂态托盘隐藏/再现往返仍是 0.95 ----------
    w3 = DesktopWindow(make_state(opacity=0.42))
    w3.resize(1920, 1040)
    w3.attached = False
    w3._attach_known_failed = True        # 挂载失败过的悬浮态
    spy3 = OpacitySpy()
    w3.setWindowOpacity = spy3
    w3.show_desktop()                     # 托盘再现的真实入口
    check("O3 悬挂态再现后仍是 0.95",
          bool(spy3.calls) and spy3.calls[-1] == 0.95, "calls=%s" % spy3.calls)

    # ---------- O4：挂载态恒 1.0（有意为之，不是漏改） ----------
    # 分层窗全局 alpha 会连带命中测试（透明区穿透坑 #39 的教训），挂载
    # 分支的 1.0 是 v10 A8 决策点 2 的选定方案，固定化后继续成立。
    w4 = DesktopWindow(make_state(opacity=0.42))
    w4.resize(1920, 1040)
    w4.attached = True
    spy4 = OpacitySpy()
    w4.setWindowOpacity = spy4
    w4._sync_native_icons = lambda: None   # offscreen 下不动原生图标
    old_attach = dw.attach_to_desktop
    old_enforce = dw.win32_layer.enforce_band_order
    dw.attach_to_desktop = lambda *a, **k: True
    dw.win32_layer.enforce_band_order = lambda: None
    try:
        w4.show_desktop()
    finally:
        dw.attach_to_desktop = old_attach
        dw.win32_layer.enforce_band_order = old_enforce
    check("O4 挂载态再现恒 1.0",
          bool(spy4.calls) and spy4.calls[-1] == 1.0, "calls=%s" % spy4.calls)

    print()
    if failures:
        print("==== test_opacity_setting: %d 项失败: %s"
              % (len(failures), failures))
        return 1
    print("==== ALL opacity_setting PASSED")
    return 0


if __name__ == "__main__":
    app = QApplication(sys.argv)
    sys.exit(main(app))
