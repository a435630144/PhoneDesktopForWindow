# -*- coding: utf-8 -*-
"""轮播式翻页契约（v10 B3，反馈 6）。

用户原话：「翻页太快；滑到第二页时第一页最右图标残留；要轮播式接续」。

三条病根（代码层已核）：

  1. `goto_page` 给**每个图标各起一条** 280ms 弹簧 —— 各自动画 = 没有"整页
     平移"的接续感，且 280ms 偏快；
  2. 离页图标靠 `anim.finished` 才隐藏 —— 弹簧过冲（`spring_curve(1.0)` 带
     轻微超调）时最后一帧仍在屏内，就是"第一页最右图标残留"；
  3. 新页图标从**上次残留的几何位置**滑入（不是从屏外），方向随机。

本批改法：**一个标量 `flip_progress` 驱动整页平移**。

    位移(页单位) = (page - target) + dir × (1 - progress)

  * `page == target`（滑入页）：progress 0 → dir（屏外）→ 1 → 0
  * `page == target-dir`（滑出页）：0 → -dir（移出屏外）
  * 其余页不参与（隐藏）

契约（本文件锁死）：
  F1  静止态 `flip_delta` = page - current（整数，与旧实现逐值相等）
  F2  翻页中：滑入页 = dir×(1-off)、滑出页 = -dir×off（上一页方向相反）
  F3  `page_offset` 的像素换算：horizontal 用网格宽、vertical 用 rows×ch、
      且静止时与旧实现（整数页差）**逐值相等**
  F4  真窗 `goto_page` 结束后图标位置 == 目标页格点
  F5  动画中途（progress=0.5）位置在起点与终点之间（整页平移而非跳变）
  F6  连翻三页不错位（中途再翻一次，最终落到最后一页）
  F7  `reduced_motion` 直接跳（不起动画对象）
  F8  翻页期间可见图标**全部属于这两页**（无第三页残留）
  F9  时长 420ms（反馈 6「翻页太快」）
  F10 瞬移只给动画帧（`flip_px` 全页共用同一份）；逻辑几何全程落定

运行：python tests/test_page_flip.py   （offscreen；判据看退出码）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


class FakeStorage:
    """grid_flow 的最小依赖面（page_axis / columns / metrics 由 grid_flow 快照给）。"""

    def __init__(self, cols=10, axis="horizontal"):
        self.columns = cols
        self._s = {"page_axis": axis, "icon_match_native": True,
                   "grid_dims_mode": "auto", "layout_mode": "grid"}

    def get_setting(self, k, default=None):
        return self._s.get(k, default)

    def set_setting(self, k, v):
        self._s[k] = v

    def icon_metric(self):
        return 72

    def ui_scale(self):
        return 1.0

    def grid_dims_mode(self):
        return "auto"

    def grid_origin(self):
        return None


def make_desktop(pages=3):
    """造一个跨 pages 页的真窗口。返回 (window, storage, rpp)。"""
    from src import grid_flow
    from src.desktop_window import DesktopWindow
    from src.storage import Storage

    tmp = Path(tempfile.mkdtemp(prefix="page_flip_"))
    st = Storage(path=tmp / "state.json")
    st._data["settings"]["auto_clone_desktop"] = False
    st._data["settings"]["dock_enabled"] = False     # 别让 Dock 改动可用区
    st._data["layout"] = []
    st.save()

    w = DesktopWindow(st)
    w.resize(1920, 1080)
    w.refresh_grid()
    rpp = grid_flow.rows_per_page()

    # 每条占**独立一行**（row = i），3 列错开——这样 33 条正好铺满 3 页；
    # 用 i // cols 的话 22 列下 33 条只占 2 行，永远只有 1 页（造数据踩过）。
    for i in range(rpp * pages):
        col, row = i % 3, i
        st.layout.append({"id": "e%03d" % i, "type": "app",
                          "app": {"name": "应用%d" % i, "target": "C:/x%d.exe" % i},
                          "col": col, "row": row})
    st.save()
    w.rebuild()
    return w, st, rpp


def main():
    from PySide6.QtWidgets import QApplication
    from src import effects, grid_flow

    app = QApplication.instance() or QApplication([])      # noqa: F841

    # ---------- F1：静止态与旧实现逐值相等 ----------
    grid_flow.set_page(2)
    check("F1 静止态同页 = 0", grid_flow.flip_delta(2) == 0.0,
          str(grid_flow.flip_delta(2)))
    check("F1 静止态下一页 = +1", grid_flow.flip_delta(3) == 1.0,
          str(grid_flow.flip_delta(3)))
    check("F1 静止态上一页 = -1", grid_flow.flip_delta(1) == -1.0,
          str(grid_flow.flip_delta(1)))

    # ---------- F2：翻页中的浮点位移 ----------
    grid_flow.set_page(1)                    # target 立即生效
    grid_flow.begin_flip(1, +1)              # 第 0 页 -> 第 1 页
    grid_flow.set_flip_progress(0.0)
    check("F2 progress=0 滑入页在 +1 页",
          grid_flow.flip_delta(1) == 1.0, str(grid_flow.flip_delta(1)))
    check("F2 progress=0 滑出页在 0（起点）",
          grid_flow.flip_delta(0) == 0.0, str(grid_flow.flip_delta(0)))
    grid_flow.set_flip_progress(0.5)
    check("F2 progress=.5 滑入页 = +0.5",
          abs(grid_flow.flip_delta(1) - 0.5) < 1e-9, str(grid_flow.flip_delta(1)))
    check("F2 progress=.5 滑出页 = -0.5",
          abs(grid_flow.flip_delta(0) + 0.5) < 1e-9, str(grid_flow.flip_delta(0)))
    grid_flow.set_flip_progress(1.0)
    check("F2 progress=1 滑入页归零", grid_flow.flip_delta(1) == 0.0,
          str(grid_flow.flip_delta(1)))
    check("F2 progress=1 滑出页 = -1",
          grid_flow.flip_delta(0) == -1.0, str(grid_flow.flip_delta(0)))
    check("F2 第三页始终在 +1 页（屏外，不参与）",
          grid_flow.flip_delta(2) == 1.0, str(grid_flow.flip_delta(2)))
    # 反向（上一页）：旧页在 target 之后
    grid_flow.set_page(1)
    grid_flow.begin_flip(1, -1)
    grid_flow.set_flip_progress(0.0)
    check("F2 反向 progress=0 滑入页在 -1 页",
          grid_flow.flip_delta(1) == -1.0, str(grid_flow.flip_delta(1)))
    check("F2 反向 progress=0 旧页（第 2 页）还在原位",
          grid_flow.flip_delta(2) == 0.0, str(grid_flow.flip_delta(2)))
    grid_flow.set_flip_progress(1.0)
    check("F2 反向 progress=1 旧页移到 +1 页",
          grid_flow.flip_delta(2) == 1.0, str(grid_flow.flip_delta(2)))
    grid_flow.end_flip()

    # ---------- F3：像素换算 ----------
    fs = FakeStorage()
    grid_flow.set_page(0)
    w0, _ = grid_flow.page_offset(fs, 0)
    check("F3 同页偏移为 0", w0 == 0, str(w0))
    step = grid_flow.page_canvas_w(fs)
    w1, _ = grid_flow.page_offset(fs, 1)
    check("F3 horizontal 一页 = 网格宽", w1 == step, "%d vs %d" % (w1, step))
    fs_v = FakeStorage(axis="vertical")
    _, h1 = grid_flow.page_offset(fs_v, 1)
    expect_h = grid_flow.rows_per_page() * grid_flow.cell_size(fs_v)[1]
    check("F3 vertical 一页 = rows×ch", h1 == expect_h, "%d vs %d" % (h1, expect_h))
    check("F3 vertical 不带横向位移",
          grid_flow.page_offset(fs_v, 1)[0] == 0, "dx")

    # ---------- F4–F9：真窗口 ----------
    w, st, rpp = make_desktop(pages=3)
    check("F4 造出 3 页（rpp=%d）" % rpp, grid_flow.page_count(st) >= 3,
          str(grid_flow.page_count(st)))

    # 直接跳页（无动画）作为参照
    w.goto_page(1, animate=False)
    ref = {eid: (icon.x(), icon.y()) for eid, icon in w.icons.items()
           if not icon.isHidden()}
    check("F4 无动画翻页后 current_page = 1",
          grid_flow.current_page() == 1, str(grid_flow.current_page()))
    check("F4 无动画结束后只显示第 1 页",
          all((grid_flow.entry_page(st, st.find_entry(eid)) == 1)
              == (not icon.isHidden())
              for eid, icon in w.icons.items()), "visible set")

    # 动画翻页：冻结在 progress=0，手动逐帧驱动
    w.goto_page(0, animate=False)
    start = {eid: (icon.x(), icon.y()) for eid, icon in w.icons.items()
             if not icon.isHidden()}
    w.goto_page(1, animate=True)
    anim = w._flip_anim
    check("F9 起了翻页动画", anim is not None, str(anim))
    check("F9 翻页时长 420ms", anim is not None and anim.duration() == 420,
          str(anim.duration() if anim else None))
    check("F9 动画用弹簧曲线（damping=1.0）",
          anim is not None
          and anim.easingCurve().type() == effects.spring_curve(1.0, 420).type(),
          str(anim.easingCurve().type() if anim else None))
    # v14p：vis 与 mid 都在 stop **之前**取样——"翻页期间"语义。
    # （FrameDriver.stop 发 finished 会立刻 settle 收起旧页；旧
    # QVariantAnimation 在 offscreen 瞬完、stop 是无操作——两种时序
    # 都要求取样先行。）
    vis_pages = {grid_flow.entry_page(st, st.find_entry(eid))
                 for eid, icon in w.icons.items() if not icon.isHidden()}
    w._on_flip_step(0.5)
    mid = {eid: (icon.x(), icon.y()) for eid, icon in w.icons.items()
           if not icon.isHidden()}
    if anim is not None:
        anim.stop()
    check("F8 翻页期间两页都可见（旧页没被提前藏）",
          vis_pages == {0, 1}, str(vis_pages))
    check("F8 没有第三页残留", 2 not in vis_pages, str(vis_pages))

    # 中间帧：位置应在起点与终点之间
    # 顺序有讲究：图标的 `pos()` 是**帧**位置（含瞬移），`_entry_pos` 是
    # **落定**位置——所以中途只能取样前者、终点只能取样后者（第一版中途
    # 就取 `_entry_pos`，start/mid/final 里 mid == final，恒不成立）。
    eid0 = next(e for e in start if grid_flow.entry_page(st, st.find_entry(e)) == 0)
    ent0 = st.find_entry(eid0)
    sx = start[eid0][0]
    mx = mid[eid0][0]

    w._on_flip_done()
    fx = w._entry_pos(ent0)[0]              # 落定后同一图标的终点位置
    check("F5 中途位置在起点与终点之间",
          min(sx, fx) < mx < max(sx, fx), "start=%d mid=%d final=%d" % (sx, mx, fx))
    check("F5 中途位移正好一半",
          abs((sx - mx) * 2 - (sx - fx)) <= 2,
          "start=%d mid=%d final=%d" % (sx, mx, fx))

    end = {eid: (icon.x(), icon.y()) for eid, icon in w.icons.items()
           if not icon.isHidden()}
    check("F4 动画结束后与无动画结果一致",
          end == {k: v for k, v in ref.items() if k in end}, "%d 项" % len(end))
    check("F4 动画结束后 flip 状态已清空", grid_flow.flip_state() is None,
          str(grid_flow.flip_state()))

    # ---------- F6：连翻三页 ----------
    w.goto_page(0, animate=False)
    w.goto_page(1, animate=True)
    if w._flip_anim is not None:
        w._flip_anim.stop()
    w._on_flip_step(0.4)                     # 滑到一半再翻
    w.goto_page(2, animate=True)
    if w._flip_anim is not None:
        w._flip_anim.stop()
    w._on_flip_step(0.5)
    w._on_flip_done()
    check("F6 连翻后 current_page = 2", grid_flow.current_page() == 2,
          str(grid_flow.current_page()))
    w.goto_page(2, animate=False)
    final = {eid: (icon.x(), icon.y()) for eid, icon in w.icons.items()
             if not icon.isHidden()}
    check("F6 连翻不错位（与直接跳第 2 页一致）",
          all(final[eid] == w._entry_pos(st.find_entry(eid)) for eid in final),
          "positions")

    # ---------- F7：reduced_motion 直接跳 ----------
    old_rm = effects.reduced_motion
    effects.reduced_motion = lambda: True
    try:
        w.goto_page(0, animate=False)
        w.goto_page(1, animate=True)
        check("F7 reduced-motion 不起动画对象", w._flip_anim is None,
              str(w._flip_anim))
        check("F7 reduced-motion 直接到位",
              all((icon.x(), icon.y()) == w._entry_pos(st.find_entry(eid))
                  for eid, icon in w.icons.items() if not icon.isHidden()),
              "positions")
    finally:
        effects.reduced_motion = old_rm

    w.close()

    # ---------- F10：瞬移只给动画帧，逻辑几何全程落定 ----------
    # 「一个标量」分两层落地：
    #   * `flip_px` = 该帧所有参与页共用的**同一个**像素瞬移（图标/组件同源）；
    #   * `cell_pos` / `widget_pos` / `page_offset` 始终是**落定**坐标——
    #     命中、落点、红框不能随动画帧抖动（F7/F8 的边缘翻页落点靠这条）。
    w10, st10, rpp10 = make_desktop(pages=2)
    ent = {"id": "w0", "type": "notes", "anchored": True, "col": 0, "row": 0}
    grid_flow.set_page(0)
    grid_flow.end_flip()
    p_static = grid_flow.widget_pos(st10, ent)
    step10 = grid_flow.page_canvas_w(st10)
    grid_flow.begin_flip(1, +1)
    grid_flow.set_page(1)                   # 与 goto_page 同序：页先落定，瞬移后起
    check("F10 翻页中 current_page 已等于 target（顺序不变式）",
          grid_flow.current_page() == grid_flow.flip_state()[0],
          "page=%s flip=%s" % (grid_flow.current_page(), grid_flow.flip_state()))
    grid_flow.set_flip_progress(0.5)
    sh_in = grid_flow.flip_px(st10, 1)      # 滑入页（target）
    sh_out = grid_flow.flip_px(st10, 0)     # 滑出页（target - dir）
    p_mid = grid_flow.widget_pos(st10, ent)
    pos_a = grid_flow.cell_pos(st10, 2, 11)     # 当前页的格子（第 1 页第 1 行）
    grid_flow.set_flip_progress(0.9)
    pos_b = grid_flow.cell_pos(st10, 2, 11)
    grid_flow.end_flip()
    check("F10 滑入/滑出页共用同一个瞬移（整页刚体平移）",
          sh_in == sh_out, "in=%s out=%s" % (sh_in, sh_out))
    check("F10 中途瞬移 = 半页",
          abs(sh_in[0] * 2 - step10) <= 2 and sh_in[1] == 0,
          "shift=%s step=%d" % (sh_in, step10))
    check("F10 翻页中逻辑坐标不抖（命中/红框/落点用落定几何）",
          pos_a == pos_b and grid_flow.widget_pos(st10, ent) == p_mid,
          "%s vs %s" % (pos_a, pos_b))
    check("F10 翻页中当前页页偏移为 0（落定）",
          grid_flow.page_offset(st10, 1) == (0, 0),
          str(grid_flow.page_offset(st10, 1)))
    check("F10 静止态瞬移为 0", grid_flow.flip_px(st10, 1) == (0, 0),
          str(grid_flow.flip_px(st10, 1)))
    from src import widget_geom
    check("F10 组件与图标同源（卡片位 = 格点 + 居中留缝）",
          grid_flow.widget_pos(st10, ent)
          == widget_geom.card_pos(st10, 0, 0,
                                  grid_flow.widget_span(st10, ent)),
          "widget_pos=%s" % (grid_flow.widget_pos(st10, ent),))
    w10.close()

    print("")
    if failures:
        print("FAILED %d: %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
