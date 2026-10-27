# -*- coding: utf-8 -*-
"""v15b / 8.4.0 性能批：四条"别再把热路径当冷路径"的判据。

背景（`tools/perf_probe.py` 实测，一屏 104 个图标、4 个液态表面）：
  * 翻页单帧 42ms（>16.7ms 预算 2.5 倍）——两处：
    ① `_apply_flip_frame` 每帧对每个图标重算落定格点，profile 里 3090 次
       `winreg.OpenKey` 全从 `resolved_icon_fit` → `native_icon_size` 来；
    ② 每个液态表面每帧重算折射（4 表面 × 30 帧 = 120 次）。
  * 启动 370ms——其中 ~250ms 是给 4K 壁纸烘饱和度，而默认 frost=100%
    下清晰图**从来不会被采样**。

契约（回归判据，不看绝对毫秒——CI 机器差异太大，只看"有没有白干"）：
  P1  原生图标尺寸带 TTL 缓存：连续调用只读一次注册表；失效后立刻重读
  P2  翻页基准只算一次：同一段动画里 `_flip_bases` 是同一次填的，且
      帧位置严格 = 落定格点 + `flip_px`（缓存不能改变几何）
  P3  折射贴图在"整页滑动中"可复用：位置变了但尺寸/参数/壁纸没变 ->
      返回同一张；落定后按新位置重算
  P4  `rebuild()` 与 `_add_icon()` 同参：大文件夹重建后仍是 2×2 瓷砖
      （旧实现在这里漏传 `big`，重启即掉回 1×1）

跑法：python tests/test_perf_batch.py   （offscreen）
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src import grid_flow, liquid_glass, wallpaper  # noqa: E402
from src import win32_layer  # noqa: E402
from src.desktop_window import DesktopWindow  # noqa: E402
from src.storage import Storage  # noqa: E402
from src.wallpaper import Wallpaper  # noqa: E402

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def fake_wall():
    img = QImage(400, 300, QImage.Format_ARGB32)
    img.fill(QColor(40, 70, 100))
    soft = img.scaled(134, 100, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return Wallpaper(image=img, soft=soft, rect=QRect(0, 0, 400, 300),
                     dpr=1.0, version=3)


def make_state(tmp):
    p = tmp / "perf.json"
    st = Storage(path=str(p))
    st._data["settings"].update({"hide_native_icons": False,
                                 "auto_clone_desktop": False,
                                 "columns": 8,
                                 "auto_arrange": False})
    st._data["widgets"] = []
    layout = []
    for i in range(12):
        layout.append({"id": "a%d" % i, "type": "app",
                       "col": i % 8, "row": i // 8,
                       "app": {"name": "应用 %d" % i, "kind": "app",
                               "path": "C:/x/%d.exe" % i}})
    layout.append({"id": "big", "type": "folder", "name": "大文件夹",
                   "tint": "#3f6fa8", "big": True, "col": 0, "row": 3,
                   "apps": [{"name": "m%d" % j, "target": "m%d" % j,
                             "kind": "app"} for j in range(9)]})
    st._data["layout"] = layout
    st.save()
    return st


def main(app, tmp):
    # ---------- P1：原生图标尺寸 TTL ----------
    win32_layer.invalidate_native_icon_size()
    v1 = win32_layer.native_icon_size()
    t1 = win32_layer._native_icon_cache["t"]
    v2 = win32_layer.native_icon_size()
    t2 = win32_layer._native_icon_cache["t"]
    check("P1a 连续调用命中缓存（不重新读表）",
          v1 == v2 and t1 > 0 and t1 == t2, "v=%s t=%.3f/%.3f" % (v1, t1, t2))

    t0 = time.perf_counter()
    for _ in range(2000):
        win32_layer.native_icon_size()
    cost_ms = (time.perf_counter() - t0) * 1000.0
    check("P1b 2000 次调用 < 20ms（读表的话要上百毫秒）", cost_ms < 20.0,
          "%.2f ms" % cost_ms)

    win32_layer.invalidate_native_icon_size()
    check("P1c invalidate 后缓存清空",
          win32_layer._native_icon_cache["t"] == 0.0)
    win32_layer.native_icon_size()
    check("P1d 失效后再次命中并回填",
          win32_layer._native_icon_cache["t"] > 0)

    # ---------- P4：rebuild 与 _add_icon 同参 ----------
    st = make_state(tmp)
    grid_flow.set_grid(st, (1600, 1000))
    win = DesktopWindow(st)
    win.show()
    app.processEvents()
    big = win.icons.get("big")
    check("P4a 首次构建即 2×2 瓷砖", big is not None and big._big is True)
    win.rebuild()
    app.processEvents()
    big2 = win.icons.get("big")
    check("P4b rebuild 后仍是 2×2 瓷砖（旧实现这里掉回 1×1）",
          big2 is not None and big2._big is True)
    check("P4c 瓷砖尺寸 = 两格", big2.width() == grid_flow.cell_size(st)[0] * 2,
          "%d" % big2.width())

    # ---------- P2：翻页基准只算一次 ----------
    grid_flow.begin_flip(1, 1)
    win._flip_bases = None
    win._apply_flip_frame()
    bases1 = win._flip_bases
    snap1 = {eid: ic.pos() for eid, ic in win.icons.items()}
    grid_flow.set_flip_progress(0.5)
    win._apply_flip_frame()
    check("P2a 基准表是同一份（没有每帧重算）",
          win._flip_bases is bases1 and bases1, "%s" % (len(bases1 or {}),))
    moved = any(snap1[eid] != ic.pos() for eid, ic in win.icons.items()
                if eid in snap1 and not ic.isHidden())
    check("P2b 帧位置仍随进度变化（缓存没把几何焊死）", moved)

    # 位置严格 = 落定 + flip_px
    ok_pos = True
    for eid, ic in win.icons.items():
        entry = st.find_entry(eid)
        if entry is None or ic.isHidden():
            continue
        x, y = win._entry_pos(entry)
        dx, dy = grid_flow.flip_px(st, grid_flow.entry_page(st, entry))
        if (ic.pos().x(), ic.pos().y()) != (x + dx, y + dy):
            ok_pos = False
            break
    check("P2c 位置 = 落定格点 + flip_px（与无缓存时逐像素一致）", ok_pos)

    win._on_flip_done()
    check("P2d 动画结束清掉基准缓存", win._flip_bases is None)

    # ---------- P3：滑动中复用折射贴图 ----------
    real = wallpaper.snapshot
    wallpaper.snapshot = fake_wall
    try:
        lens = liquid_glass.LiveLens()
        style = liquid_glass.Lens(strength=0.5, band=20.0)
        pm1 = lens.pixmap(QRect(100, 100, 200, 150), 12.0, style, 1.0)
        check("P3a 首帧算出贴图", not pm1.isNull())
        pm2 = lens.pixmap(QRect(140, 100, 200, 150), 12.0, style, 1.0,
                          follow_move=True)
        check("P3b 滑动中（位置变、其余不变）复用同一张贴图", pm2 is pm1)
        pm3 = lens.pixmap(QRect(140, 100, 200, 150), 12.0, style, 1.0)
        check("P3c 落定后（follow_move=False）按新位置重算", pm3 is not pm1)
        pm4 = lens.pixmap(QRect(180, 100, 260, 150), 12.0, style, 1.0,
                          follow_move=True)
        check("P3d 尺寸变了就不许复用（复用会拉伸图案）", pm4 is not pm3)
    finally:
        wallpaper.snapshot = real

    win.close()


def run():
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="perf-"))
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
