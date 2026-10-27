# -*- coding: utf-8 -*-
"""性能基准：把"感觉卡"变成可复现的数字（v15b / 8.4.0 性能批）。

用法：python tools/perf_probe.py

量的是**本进程内**的真实耗时（offscreen 光栅化，相对值可比、绝对值偏乐观），
每项跑多次取中位数：

  1 启动     ：窗口构建 + 首帧（import 不计）
  2 整页重绘 ：一屏图标（含液态文件夹 + 大文件夹），逐个 child.render()
  3 单图标   ：应用图标 / 液态文件夹瓷砖 / 大文件夹瓷砖，一次 paintEvent
  4 翻页帧   ：30 帧滑动里每帧的"移位 + 全部图标重绘"
  5 命中测试 ：400 次 pos→cell（WM_NCHITTEST 与点击共用同一条链）
  6 文件夹弹窗：构建 + 首帧
  7 壁纸快照 ：冷启动 vs 命中
  8 折射调用 ：整页重绘期间真正重算了几次折射（缓存命中应为 0）

判据只看 **ms**：单帧 > 16.7ms 就是会掉帧的帧。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def bench(fn, repeat=5, warmup=1):
    """跑 repeat 次，返回 (中位 ms, 最小 ms)。warmup 不计入（预热字体/缓存）。"""
    for _ in range(warmup):
        fn()
    costs = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        costs.append((time.perf_counter() - t0) * 1000.0)
    costs.sort()
    return costs[len(costs) // 2], costs[0]


def fake_wallpaper():
    """带图案的假壁纸（纯平色会让折射退化成无位移，测不出真实成本）。"""
    from src.wallpaper import Wallpaper
    w, h = 1920 * 2, 1080 * 2
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(36, 66, 104))
    p = QPainter(img)
    p.setPen(QPen(QColor(120, 190, 255), 26))
    for i in range(0, w, 120):
        p.drawLine(i, 0, i + 500, h)
    p.setPen(QPen(QColor(230, 180, 90), 14))
    for j in range(0, h, 90):
        p.drawLine(0, j, w, j + 220)
    p.end()
    soft = img.scaled(w // 3, h // 3, Qt.IgnoreAspectRatio,
                      Qt.SmoothTransformation)
    # stable_version 非 0 = 允许饱和度落盘缓存（整机装配按"热启动"量）
    return Wallpaper(image=img, soft=soft, rect=QRect(0, 0, 1920, 1080),
                     dpr=2.0, version=7, stable_version=7700)


def build_state(tmp, n_apps=100):
    from src.storage import Storage
    st = Storage(path=str(tmp / "perf.json"))
    st._data["settings"].update({
        "hide_native_icons": False,
        "auto_clone_desktop": False,
        "auto_arrange": False,
        "liquid_glass": True,
        "liquid_strength": 55,
    })
    st._data["widgets"] = []
    layout = []
    cols = 18
    for i in range(n_apps):
        col, row = i % cols, i // cols
        layout.append({"id": "a%d" % i, "type": "app", "col": col, "row": row,
                       "app": {"name": "应用 %d" % i, "kind": "app",
                               "path": "C:/x/%d.exe" % i}})
    # 3 个液态文件夹 + 1 个 12 成员大文件夹（都落在首页可见区）
    for k in range(3):
        layout.append({"id": "f%d" % k, "type": "folder",
                       "name": "文件夹 %d" % k, "tint": "liquid",
                       "col": k, "row": 6,
                       "apps": [{"name": "m%d" % j, "target": "m%d" % j,
                                 "kind": "app"} for j in range(4)]})
    layout.append({"id": "big", "type": "folder", "name": "大文件夹",
                   "tint": "#3f6fa8", "big": True, "col": 4, "row": 6,
                   "apps": [{"name": "b%d" % j, "target": "b%d" % j,
                             "kind": "app"} for j in range(12)]})
    st._data["layout"] = layout
    st.save()
    return st


def measure_assembly(app) -> None:
    """整机装配：窗口 + 组件层 + Dock（main.py 的真实顺序）。

    单独用一份 state 与窗口来量，不污染主测量。判据只看"装配总耗时"——
    典型回退是**冗余重建**（历史上 `set_dock` 会把 100+ 图标重建一遍，
    实测 +55ms），所以这里把 set_dock 单独打出来。
    """
    from src.dock import DockWindow, seed_from_layout
    from src.desktop_window import DesktopWindow
    from src.widget_layer import WidgetLayer
    from src import grid_flow

    tmp = Path(tempfile.mkdtemp(prefix="perf-asm-"))
    # 热启动：先把 saturated 的磁盘缓存捂热，再清掉进程内缓存——
    # 与"用户第二次开程序"等价（PIL 不必导入）。
    from src import wallpaper as _wall
    _wall.set_sat_cache_dir(tmp / "satcache")
    _wall.saturated(fake_wallpaper(), 1.4, need_sharp=False)
    _wall._SAT_CACHE["key"] = None
    _wall._SAT_CACHE["wall"] = None
    st = build_state(tmp)
    grid_flow.set_grid(st, (1920, 1040))
    seed_from_layout(st)            # 与 main.py 同序：先填 Dock 再建窗口
    t0 = time.perf_counter()
    win = DesktopWindow(st)
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.setGeometry(0, 0, 1920, 1040)
    win.show()
    win._update_grid_metrics()      # 真实启动：挂载成功后的首次度量
    app.processEvents()
    t1 = time.perf_counter()
    layer = WidgetLayer(st)
    win.set_widget_layer(layer)
    dock = DockWindow(st)
    t2 = time.perf_counter()
    win.set_dock(dock)
    app.processEvents()
    t3 = time.perf_counter()
    print("启动（整机装配）                 %7.1f ms"
          "（窗口 %.1f + 组件层/Dock 构建 %.1f + set_dock %.1f）"
          % ((t3 - t0) * 1000.0, (t1 - t0) * 1000.0,
             (t2 - t1) * 1000.0, (t3 - t2) * 1000.0))
    win.close()
    dock.close()


def main() -> int:
    from src import grid_flow, liquid_glass, wallpaper
    from src.desktop_window import DesktopWindow
    from src.folder_popup import FolderPopup

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="perf-"))
    st = build_state(tmp)
    grid_flow.set_grid(st, (1920, 1040))

    wall = fake_wallpaper()          # 只造一次：每帧重造会污染所有测量
    real_snapshot = wallpaper.snapshot
    wallpaper.snapshot = lambda: wall

    real_refract = liquid_glass.refract
    calls = {"n": 0}

    def counting_refract(*a, **kw):
        calls["n"] += 1
        return real_refract(*a, **kw)

    liquid_glass.refract = counting_refract

    t0 = time.perf_counter()
    win = DesktopWindow(st)
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.show()
    app.processEvents()
    boot_ms = (time.perf_counter() - t0) * 1000.0
    m = grid_flow.current_metrics()
    print("网格 %d 列 × %d 行，cell %dx%d" % (m.cols, m.rows, m.cw, m.ch))
    print("启动（构建 + 首帧）              %7.1f ms" % boot_ms)

    icons = list(win.icons.values())
    n_big = sum(1 for i in icons if getattr(i, "_big", False))
    print("一屏图标 %d 个（文件夹 %d，其中大文件夹 %d）"
          % (len(icons), sum(1 for i in icons if i.is_folder), n_big))

    pm = QPixmap(1920, 1040)
    pm.fill(QColor(0, 0, 0, 0))

    def frame_all():
        p = QPainter(pm)
        for icon in icons:
            if not icon.isHidden():
                icon.render(p, icon.pos())
        p.end()

    calls["n"] = 0
    t = time.perf_counter()
    frame_all()
    first_ms = (time.perf_counter() - t) * 1000.0
    cold = calls["n"]
    calls["n"] = 0
    frame_all()
    warm = calls["n"]
    ms, best = bench(frame_all, repeat=5)
    print("整页重绘                         %7.2f ms（最好 %.2f）  折射重算：冷帧 %d / 热帧 %d"
          % (ms, best, cold, warm))
    print("整页重绘（首帧 / 含冷缓存）       %7.1f ms" % first_ms)

    # ---- 单图标 ----
    def icon_render(icon):
        w, h = max(1, icon.width()), max(1, icon.height())
        target = QPixmap(w, h)
        target.fill(QColor(0, 0, 0, 0))

        def f():
            p = QPainter(target)
            icon.render(p, QPoint(0, 0))
            p.end()
        return f

    app_icon = next(i for i in icons if not i.is_folder)
    folder_icon = next(i for i in icons if i.is_folder and not i._big)
    big_icon = next(i for i in icons if i.is_folder and i._big)
    for label, ic in (("应用图标", app_icon),
                      ("液态文件夹瓷砖 4 成员", folder_icon),
                      ("大文件夹瓷砖 12 成员", big_icon)):
        ms, best = bench(icon_render(ic), repeat=7)
        print("单图标 %-22s %7.2f ms（最好 %.2f）" % (label, ms, best))

    # ---- 翻页帧 ----
    grid_flow.begin_flip(1, 1)
    flip = []
    for i in range(30):
        grid_flow.set_flip_progress(i / 30.0)
        t = time.perf_counter()
        win._apply_flip_frame()
        frame_all()
        flip.append((time.perf_counter() - t) * 1000.0)
    grid_flow.end_flip()
    flip.sort()
    print("翻页帧（30 帧）                  %7.2f ms 中位 / %.2f p95 / %.2f 峰值"
          % (flip[len(flip) // 2], flip[int(len(flip) * 0.95)], flip[-1]))

    # ---- 命中测试 ----
    def hit():
        for x in range(0, 1920, 48):
            win._cell_at(QPoint(x, (x * 7) % 1040))

    ms, best = bench(hit, repeat=5)
    print("命中测试 400 次 _cell_at         %7.2f ms（最好 %.2f）" % (ms, best))

    # ---- 文件夹弹窗 ----
    folder_entry = st.find_entry("f0")

    def popup_open():
        pop = FolderPopup(st, folder_entry, anchor_rect=QRect(200, 200, 88, 80))
        pop.setAttribute(Qt.WA_DontShowOnScreen, True)
        pop.show()
        target = QPixmap(pop.size())
        target.fill(QColor(0, 0, 0, 0))
        p = QPainter(target)
        pop.render(p, QPoint(0, 0))
        p.end()
        pop.close()

    ms, best = bench(popup_open, repeat=5)
    print("文件夹弹窗（构建+首帧）          %7.2f ms（最好 %.2f）" % (ms, best))

    # ---- 整机装配（另起一份 state；放在最后，不污染上面的读数） ----
    measure_assembly(app)

    # ---- 壁纸快照 ----
    liquid_glass.refract = real_refract
    wallpaper.snapshot = real_snapshot
    wallpaper.invalidate()
    ms, best = bench(wallpaper.snapshot, repeat=5, warmup=0)
    print("壁纸快照（冷+热 5 次）           %7.2f ms（最好 %.2f）" % (ms, best))
    return 0


if __name__ == "__main__":
    sys.exit(main())
