# -*- coding: utf-8 -*-
"""v14p：全局动画帧率驱动器（effects.FrameDriver）。

背景：QPropertyAnimation 走 Qt 内部统一时钟（固定 ~60fps，私有 API 不
暴露），设置里的渲染频率档管不到它。FrameDriver 用自有 QTimer 复刻
QPropertyAnimation 的接口（propertyName/stop/finished/start），tick
间隔取 frame_interval(storage)=1000/live_fps——图标 hover/落位弹簧、
组件移动、翻页、文件夹开合、Dock 悬停全部换装，帧率档真正全局生效。

  F1 frame_interval 档位换算（low=33ms / high≈17ms / 自定义夹紧 / 兜底60）
  F2 drive_property 数值动画到位 + finished 恰好一次
  F3 QPoint 插值（pos 动画终点精确）
  F4 同属性替换：旧动画被停（finished 发一次，接口与 Qt 对齐）
  F5 真实链路：iconwidget._animate 返回 FrameDriver 且 propertyName 对
  F6 low 档下间隔确实变长（30fps → ≥33ms）

运行：python tests/test_frame_driver.py   （offscreen）
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QObject, Property  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from src import effects   # noqa: E402
from src.storage import Storage   # noqa: E402

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def _st(name="s.json", **kv):
    st = Storage(path=Path(tempfile.mkdtemp()) / name)
    for k, v in kv.items():
        st.set_setting(k, v)
    return st


def settle(app, ms):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main(app):
    # ---------- F1 frame_interval ----------
    check("F1 low 档 30fps -> 33ms",
          effects.frame_interval(_st(liquid_fps_mode="low")) == 33)
    check("F1b high 档 60fps -> 17ms",
          effects.frame_interval(_st(liquid_fps_mode="high")) == 17)
    check("F1c 自定义 144 -> 7ms",
          effects.frame_interval(_st(liquid_fps_mode="custom",
                                     liquid_fps_custom=144)) == 7)
    check("F1d 下限夹 4ms",
          effects.frame_interval(_st(liquid_fps_mode="custom",
                                     liquid_fps_custom=240)) == 4)
    # live_fps(None) 走默认档 medium=45 -> 22ms
    check("F1e 无 storage 走默认档 45fps -> 22ms",
          effects.frame_interval(None) == 22)

    # ---------- F2 数值动画 ----------
    class Obj(QObject):
        def __init__(self):
            super().__init__()
            self._v = 0.0

        def _get(self):
            return self._v

        def _set(self, v):
            self._v = float(v)

        v = Property(float, _get, _set)

    o = Obj()
    fired = []
    d = effects.drive_property(o, b"v", 0.0, 10.0, 60,
                               done=lambda: fired.append(1))
    check("F2 propertyName 对齐（bytes）", d.propertyName() == b"v")
    settle(app, 200)
    check("F2b 终值精确到位", abs(o.v - 10.0) < 1e-6, "%.3f" % o.v)
    check("F2c finished 恰好一次", len(fired) == 1, str(fired))

    # ---------- F3 QPoint 插值 ----------
    w = QWidget()
    d3 = effects.drive_property(w, b"pos", QPoint(0, 0), QPoint(100, 40), 60)
    settle(app, 200)
    check("F3 pos 动画终点精确", (w.x(), w.y()) == (100, 40),
          "(%d,%d)" % (w.x(), w.y()))

    # ---------- F4 同属性替换 ----------
    o2 = Obj()
    f1, f2 = [], []
    da = effects.drive_property(o2, b"v", 0.0, 5.0, 400,
                                done=lambda: f1.append(1))
    db = effects.drive_property(o2, b"v", 5.0, 9.0, 50,
                                done=lambda: f2.append(1))
    # 模拟 _anims 管理：同属性停旧
    da.stop()
    settle(app, 250)
    check("F4 旧动画被停且只发一次 finished", len(f1) == 1, str(f1))
    check("F4b 新动画跑到终点", abs(o2.v - 9.0) < 1e-6, "%.2f" % o2.v)

    # ---------- F5 iconwidget._animate 换装 ----------
    from src.iconwidget import AppIcon
    icon = AppIcon({"id": "fd", "name": "D",
                    "app": {"kind": "app", "name": "D", "target": "T"},
                    "col": 0, "row": 0, "page": 0}, 64)
    icon.resize(96, 96)
    a = icon._animate(b"hover_scale", 1.0, 1.04, 60)
    check("F5 _animate 返回 FrameDriver",
          isinstance(a, effects.FrameDriver) and a.propertyName() == b"hover_scale")
    settle(app, 200)
    check("F5b hover_scale 到位", abs(icon._hover_scale - 1.04) < 1e-6,
          "%.4f" % icon._hover_scale)

    # ---------- F6 档位影响 tick 间隔（真实驱动器） ----------
    st_low = _st("low.json", liquid_fps_mode="low")

    class OwnerStorage(QWidget):
        pass
    ow = OwnerStorage()
    ow.storage = st_low
    d6 = effects.drive_property(ow, b"windowOpacity", 0.0, 1.0, 300)
    check("F6 low 档驱动器间隔 33ms", d6._timer.interval() == 33,
          str(d6._timer.interval()))
    d6.stop()

    print()
    if failures:
        print("==== test_frame_driver: %d failed: %s" % (len(failures),
                                                         failures))
        return 1
    print("==== ALL frame_driver PASSED")
    return 0


if __name__ == "__main__":
    _app = QApplication.instance() or QApplication(sys.argv)
    rc = main(_app)
    print("%d/%d checks" % (total[0] - len(failures), total[0]))
    sys.exit(rc)
