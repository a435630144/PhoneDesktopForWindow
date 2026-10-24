# -*- coding: utf-8 -*-
"""窗口死亡自愈（2026-10-25 用户实机事故的根因回归）。

病根：桌面带窗口被外部销毁（explorer 未重启——第三方桌面整理 / 显示桌面
切换会连带清理跨进程子窗口）后，Qt 事件循环照常跑，但所有原生窗口已消失。
旧 BandGuard 只认"漂移 / explorer 重启 / 显示桌面"，对死句柄无任何处理：
enforce/restore 在死句柄上空转，进程变成"无窗口僵尸"——不退出、不重建、
原生图标被扣在隐藏态，用户桌面空白且右键失灵。

契约：
  R1 受护句柄被销毁 -> 控件 hide()+show() 重建原生窗口，重挂 + 新句柄重新登记
  R2 死句柄无活控件 -> 仅从登记表移除，不抛异常
  R3 复活风暴（观察窗内达上限）-> 恢复原生图标并走 QApplication.quit，不再拉锯
  R4 全部死亡且无控件可复活 -> 同样恢复 + 退出
  R5 正常轮次零成本：句柄都活着 -> _revive_dead_windows 返回 False 且不动表

运行：python tests/test_band_guard_revive.py
"""
import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt

from src import band_guard, win32_layer

failures = []
_user32 = ctypes.windll.user32


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def kill_hwnd(hwnd):
    _user32.DestroyWindow(int(hwnd))


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    # 挂载与恢复都打桩：本测试不碰真实桌面带 / 真实图标状态
    attach_calls = []
    real_attach = band_guard.attach_to_desktop
    band_guard.attach_to_desktop = (
        lambda widget, below_hwnd=0, layer=0:
        (attach_calls.append(layer), True)[1])
    quit_fired = []
    real_quit = QApplication.quit
    QApplication.quit = staticmethod(lambda *a: quit_fired.append(True))
    restores = []
    real_restore = win32_layer.restore_native_icons
    win32_layer.restore_native_icons = lambda: restores.append(True)

    try:
        # R1 常规复活：销毁 -> 重建 -> 重挂 -> 新句柄入表
        w1 = QWidget()
        w1.setAttribute(Qt.WA_DontShowOnScreen, True)  # 原生窗口不闪屏
        w1.show()
        guard = band_guard.BandGuard(interval_ms=50)
        guard.track(w1, layer=win32_layer.BAND_LAYER_DESKTOP)
        old = int(w1.winId())
        check("R1-track", (old, win32_layer.BAND_LAYER_DESKTOP) in guard._items)
        kill_hwnd(old)
        app.processEvents()
        check("R1-dead", not _user32.IsWindow(old))
        handled = guard._revive_dead_windows()
        app.processEvents()
        new = int(w1.winId())
        # 探针结论：hide()/show() 拿回陈旧死句柄，destroy()+show() 才真重建。
        # 新句柄可能复用旧数值（Windows 同线程复用 hwnd），判活看 IsWindow。
        check("R1-revived-return", handled is True)
        check("R1-new-hwnd", _user32.IsWindow(new) and w1.isVisible(),
              "old=%s new=%s alive=%s" % (old, new, bool(_user32.IsWindow(new))))
        check("R1-retracked", (new, win32_layer.BAND_LAYER_DESKTOP) in guard._items)
        check("R1-reattached", win32_layer.BAND_LAYER_DESKTOP in attach_calls)
        check("R1-no-quit", not quit_fired and not restores)

        # R2 死句柄无控件引用：只清理登记，不抛异常不退出
        guard._items.append((12345, win32_layer.BAND_LAYER_WIDGET))
        handled = guard._revive_dead_windows()
        check("R2-dropped", handled is True
              and not any(h == 12345 for h, _ in guard._items))
        check("R2-no-quit", not quit_fired and not restores)

        # R5 句柄都活着：返回 False，登记表原样（零成本轮次）
        before = list(guard._items)
        check("R5-idle", guard._revive_dead_windows() is False
              and list(guard._items) == before)

        # R3 复活风暴：观察窗内已达上限 -> 放弃拉锯，恢复图标 + quit
        w1.show()
        old = int(w1.winId())
        guard._items = [(old, win32_layer.BAND_LAYER_DESKTOP)]
        import time
        guard._revives = [time.monotonic()] * band_guard._REVIVE_MAX
        kill_hwnd(old)
        app.processEvents()
        guard._revive_dead_windows()
        app.processEvents()
        check("R3-gave-up-quit", len(quit_fired) == 1)
        check("R3-restored", len(restores) == 1)
        check("R3-not-revived", not any(
            _user32.IsWindow(h) for h, _ in guard._items) or not guard._items)

        # R4 全部死亡且无控件：恢复 + 退出
        quit_fired.clear()
        restores.clear()
        guard2 = band_guard.BandGuard(interval_ms=50)
        w2 = QWidget()
        w2.setAttribute(Qt.WA_DontShowOnScreen, True)
        w2.show()
        guard2.track(w2)
        kill_hwnd(int(w2.winId()))
        # 控件引用置空 + 反复销毁，制造"无控件可复活"
        guard2._widgets = {}
        old_items = list(guard2._items)
        for h, _ in old_items:
            kill_hwnd(h)
        app.processEvents()
        guard2._revive_dead_windows()
        app.processEvents()
        check("R4-all-dead-quit", len(quit_fired) == 1 and len(restores) == 1)
        check("R4-items-cleared", not guard2._items)
    finally:
        band_guard.attach_to_desktop = real_attach
        QApplication.quit = real_quit
        win32_layer.restore_native_icons = real_restore

    print("=" * 60)
    if failures:
        print("FAILED: %d -> %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
