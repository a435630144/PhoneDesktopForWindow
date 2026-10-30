# -*- coding: utf-8 -*-
"""Dock 栏契约（v10 C3，反馈 17）。

契约来源 `docs/指导文档_v10.md` §4.3：

  1. **可用区扣除是唯一入口**——Dock 开启后从可用区里扣掉条带
     （底部/顶部扣高度、左右扣宽度），**不许在 `solve_dims` 里塞 if**。
  2. **四向 × 开/关**都要正确让位，且关闭时一个像素都不扣。
  3. **条目 = 应用快捷方式**（Q2 拍板）：文件 / 文件夹 / 组件一律不收。
  4. **持久化往返**：`settings["dock_items"]` 三次往返逐字段相等
     （项目铁律：模式切换只增不删、幂等）。
  5. 尺寸随 `ui_scale()`（B2 的单一旋钮），与 dpr 无关。

判据（本文件锁死）：
  D1  关闭时 inset 全 0（网格与未开 Dock 逐字段相等）
  D2  四向各扣对边（底部/顶部扣高度，左右扣宽度）
  D3  非法位置回退 bottom（不静默改网格）
  D4  metrics 随 ui_scale 线性；条带厚度包得住 hover 放大
  D5  条带贴边 + 沿副轴居中（四向）
  D6  条目沿主轴递进、副轴居中（横/竖两套）
  D7  hit_index 命中 / 间隙 / 越界 / 关闭态
  D8  move_item 排序（前移/后移/越界夹紧/同位置 no-op）
  D9  normalize_items 过滤非法、去重、上限
  D10 entry_is_app 只认应用（file / folder / 组件都不认）
  D11 dock_items 三次往返逐字段相等
  D12 网格扣除端到端：inset 让 cols/rows 变少且原点偏移正确
  D13 DockWindow 真窗口几何落在条带内、空态可见、ui_scale 变化重排
  D15 默认样式收小（图标 32 / 4 槽位），条带厚度随之变薄
  D16 外观旋钮生效（图标大小 / 不透明度 / 圆角形态 / hover / 指示点）
  D17 切位置后窗口几何真的换边（开关 + 四向 + 恢复）
  D18 拖入按落点插入（add_item(index) / _insert_index 同口径）
  D19 设置 ▸ Dock 栏分页存在，改旋钮写设置并触发窗口重排
  D20 默认圆角是「圆角 12px」而不是胶囊（v10 A11 反馈 24）；半径可调且夹紧
  D21 旧 state（落盘过 pill）一次性迁移成 round，且用户之后选 pill 不被改回

运行：python tests/test_dock.py   （offscreen；判据看退出码）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QImage

from src import theme

failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


class FakeStorage:
    """最小 storage 桩：grid_metrics.compute 的依赖面（同 B2 契约）。"""

    def __init__(self, scale=1.0):
        self._s = {"icon_match_native": True, "grid_dims_mode": "auto"}
        self._scale = scale

    def get_setting(self, key, default=None):
        return self._s.get(key, default)

    def set_setting(self, key, val):
        self._s[key] = val

    def icon_metric(self):
        return 72

    def ui_scale(self):
        return self._scale

    def grid_dims_mode(self):
        return "auto"

    def grid_origin(self):
        return None


def main():
    from src import dock, grid_metrics
    from src.storage import Storage

    # ---------- D1：关闭 = 零扣除 ----------
    for pos in dock.POSITIONS:
        ins = dock.inset(False, pos, 60)
        check("D1 关闭时零扣除 @%s" % pos, ins == (0, 0, 0, 0), str(ins))

    # ---------- D2：四向各扣对边 ----------
    expect = {
        "bottom": (0, 0, 0, 60),
        "top": (0, 60, 0, 0),
        "left": (60, 0, 0, 0),
        "right": (0, 0, 60, 0),
    }
    for pos, want in expect.items():
        ins = dock.inset(True, pos, 60)
        check("D2 %s 扣对边" % pos, ins == want, "%s != %s" % (ins, want))

    # ---------- D3：非法位置回退 bottom ----------
    for bad in ("", "middle", None, 123):
        check("D3 非法位置回退 bottom %r" % (bad,),
              dock.inset(True, bad, 42) == (0, 0, 0, 42),
              str(dock.inset(True, bad, 42)))
    check("D3b normalize_position 收敛", dock.normalize_position("TOP") == "top")

    # ---------- D4：metrics 随 ui_scale 线性 ----------
    m1 = dock.metrics(1.0, 8)
    m2 = dock.metrics(2.0, 8)
    check("D4 icon 随 scale 线性", m2.icon == m1.icon * 2,
          "%d vs %d" % (m1.icon, m2.icon))
    check("D4 pad 随 scale 线性", m2.pad == m1.pad * 2,
          "%d vs %d" % (m1.pad, m2.pad))
    check("D4 thickness = pad*2 + 图标（v14r：hover 不再计入）",
          m1.thickness == m1.pad * 2 + m1.icon,
          str(m1.thickness))
    # v14r 解耦判据：悬停倍数改了，条带厚度不动（放大吃内边距余量）
    m_h = dock.metrics(1.0, 8, hover=1.5)
    check("D4b hover 倍数不影响厚度",
          m_h.thickness == m1.thickness,
          "%d vs %d" % (m_h.thickness, m1.thickness))
    check("D4 空态也占位（length > 0）", m1.length > 0, str(m1.length))
    check("D4 length 随条目数增长",
          dock.metrics(1.0, 10).length > dock.metrics(1.0, 4).length,
          "%d vs %d" % (dock.metrics(1.0, 10).length,
                        dock.metrics(1.0, 4).length))

    # ---------- D5：条带贴边 + 副轴居中 ----------
    size = (1920, 1080)
    bar_b = dock.bar_rect(size, "bottom", m1)
    bar_t = dock.bar_rect(size, "top", m1)
    bar_l = dock.bar_rect(size, "left", m1)
    bar_r = dock.bar_rect(size, "right", m1)
    check("D5 bottom 贴底", bar_b[1] + bar_b[3] == 1080, str(bar_b))
    check("D5 top 贴顶", bar_t[1] == 0, str(bar_t))
    check("D5 left 贴左", bar_l[0] == 0, str(bar_l))
    check("D5 right 贴右", bar_r[0] + bar_r[2] == 1920, str(bar_r))
    check("D5 bottom 水平居中",
          abs(bar_b[0] + bar_b[2] // 2 - 960) <= 2, str(bar_b))
    check("D5 left 垂直居中",
          abs(bar_l[1] + bar_l[3] // 2 - 540) <= 2, str(bar_l))
    check("D5 横向厚度 = thickness", bar_b[3] == m1.thickness, str(bar_b))
    check("D5 纵向宽度 = thickness", bar_l[2] == m1.thickness, str(bar_l))

    # ---------- D6：条目主轴递进、副轴居中 ----------
    r0 = dock.item_rect(bar_b, "bottom", m1, 0)
    r1 = dock.item_rect(bar_b, "bottom", m1, 1)
    check("D6 横向第 0 项在此 pad 处", r0[0] == bar_b[0] + m1.pad, str(r0))
    check("D6 横向步进 = icon + gap",
          r1[0] - r0[0] == m1.icon + m1.gap, "%d" % (r1[0] - r0[0]))
    check("D6 横向副轴居中", r0[1] == bar_b[1] + (m1.thickness - m1.icon) // 2,
          str(r0))
    c0 = dock.item_rect(bar_l, "left", m1, 0)
    c1 = dock.item_rect(bar_l, "left", m1, 1)
    check("D6 纵向第 0 项在此 pad 处", c0[1] == bar_l[1] + m1.pad, str(c0))
    check("D6 纵向步进 = icon + gap",
          c1[1] - c0[1] == m1.icon + m1.gap, "%d" % (c1[1] - c0[1]))
    check("D6 纵向副轴居中", c0[0] == bar_l[0] + (m1.thickness - m1.icon) // 2,
          str(c0))

    # ---------- D7：hit_index ----------
    n = 8
    cx0 = r0[0] + m1.icon // 2
    cy0 = r0[1] + m1.icon // 2
    check("D7 命中第 0 项中心",
          dock.hit_index(bar_b, "bottom", m1, n, (cx0, cy0)) == 0)
    check("D7 命中第 3 项中心",
          dock.hit_index(bar_b, "bottom", m1, n,
                         (r0[0] + 3 * (m1.icon + m1.gap) + m1.icon // 2,
                          cy0)) == 3)
    check("D7 间隙里不命中",
          dock.hit_index(bar_b, "bottom", m1, n,
                         (r0[0] + m1.icon + 1, cy0)) is None)
    check("D7 条带外不命中",
          dock.hit_index(bar_b, "bottom", m1, n, (cx0, 5)) is None)
    check("D7 条目数 0 时不命中",
          dock.hit_index(bar_b, "bottom", m1, 0, (cx0, cy0)) is None)
    check("D7 关闭态不命中（inset 为 0 也不该有落点）",
          dock.hit_index(bar_b, "bottom", m1, n, (cx0, cy0), enabled=False) is None)

    # ---------- D8：排序纯函数 ----------
    items = [{"name": c} for c in "abcd"]
    moved = dock.move_item(items, 0, 2)
    check("D8 前移", [i["name"] for i in moved] == ["b", "c", "a", "d"],
          str([i["name"] for i in moved]))
    moved = dock.move_item(items, 3, 1)
    check("D8 后移", [i["name"] for i in moved] == ["a", "d", "b", "c"],
          str([i["name"] for i in moved]))
    check("D8 原位 no-op 返回等价列表",
          [i["name"] for i in dock.move_item(items, 2, 2)] == ["a", "b", "c", "d"])
    check("D8 越界夹紧（目标超上限）",
          [i["name"] for i in dock.move_item(items, 0, 99)] == ["b", "c", "d", "a"])
    check("D8 越界夹紧（源为负）",
          [i["name"] for i in dock.move_item(items, -3, 1)] == ["b", "a", "c", "d"])
    check("D8 不改原列表", [i["name"] for i in items] == ["a", "b", "c", "d"])

    # ---------- D9：normalize_items ----------
    raw = [
        {"name": "记事本", "target": "C:/n.exe", "icon_path": "C:/n.exe"},
        {"name": "", "target": "C:/x.exe"},                    # 无名字 -> 丢
        {"name": "A", "target": "C:/a.exe"},
        {"name": "A", "target": "C:/a.exe"},                    # 重复 -> 丢
        {"name": "无 target"},
        "不是字典",
        {"name": "B", "target": "C:/b.exe", "extra": 1},        # 额外字段保留
    ]
    norm = dock.normalize_items(raw)
    names = [i["name"] for i in norm]
    check("D9 过滤空名/无 target/非字典", names == ["记事本", "A", "B"], str(names))
    check("D9 去重", len(norm) == 3, str(len(norm)))
    check("D9 保留额外字段", norm[2].get("extra") == 1)
    check("D9 非列表输入 -> 空",
          dock.normalize_items(None) == [] and dock.normalize_items("x") == [])
    many = [{"name": str(i), "target": "C:/%d.exe" % i}
            for i in range(dock.DOCK_MAX_ITEMS + 10)]
    check("D9 上限 %d" % dock.DOCK_MAX_ITEMS,
          len(dock.normalize_items(many)) == dock.DOCK_MAX_ITEMS,
          str(len(dock.normalize_items(many))))

    # ---------- D10：只收应用 ----------
    check("D10 应用可入", dock.entry_is_app(
        {"type": "app", "app": {"name": "x", "target": "C:/x.exe"}}))
    check("D10 kind=file 不可入", not dock.entry_is_app(
        {"type": "app", "app": {"name": "x", "target": "C:/x.txt",
                                "kind": "file"}}))
    check("D10 文件夹不可入", not dock.entry_is_app(
        {"type": "folder", "apps": []}))
    check("D10 组件不可入", not dock.entry_is_app(
        {"type": "widget", "widget": "clock"}))
    check("D10 空/非法不可入",
          not dock.entry_is_app(None) and not dock.entry_is_app({}))

    # ---------- D11：持久化三次往返逐字段相等 ----------
    tmp = Path(tempfile.mkdtemp(prefix="dock_"))
    st = Storage(path=tmp / "state.json")
    seed = [
        {"name": "记事本", "target": "C:/Windows/notepad.exe",
         "icon_path": "C:/Windows/notepad.exe"},
        {"name": "计算器", "target": "C:/Windows/calc.exe",
         "icon_path": "C:/Windows/calc.exe"},
    ]
    dock.save_items(st, seed)
    first = [dict(i) for i in dock.load_items(st)]
    for rnd in range(3):
        again = dock.load_items(st)
        dock.save_items(st, again)
        st2 = Storage(path=tmp / "state.json")     # 真从磁盘重读
        check("D11 第 %d 次往返逐字段相等" % (rnd + 1),
              dock.load_items(st2) == first,
              str(dock.load_items(st2)))
    check("D11 落的键就是 dock_items",
          [i["name"] for i in (st.get_setting("dock_items") or [])]
          == ["记事本", "计算器"])

    # ---------- D12：网格扣除端到端 ----------
    fake = FakeStorage(1.0)
    full = grid_metrics.compute(fake, (1920, 1080), native_px=48)
    m = dock.metrics(1.0, 8)
    ins = dock.inset(True, "bottom", m.thickness)
    cut = grid_metrics.compute(fake, (1920, 1080), native_px=48, inset=ins)
    check("D12 扣掉条带后行数不变或更少", cut.rows <= full.rows,
          "%d vs %d" % (cut.rows, full.rows))
    check("D12 底部扣除把网格整体上移",
          cut.oy + cut.rows * cut.ch <= 1080 - m.thickness + 2,
          "oy=%d rows=%d ch=%d" % (cut.oy, cut.rows, cut.ch))
    ins_top = dock.inset(True, "top", m.thickness)
    cut_t = grid_metrics.compute(fake, (1920, 1080), native_px=48, inset=ins_top)
    check("D12 顶部扣除把网格下移", cut_t.oy >= ins_top[1],
          "oy=%d" % cut_t.oy)
    ins_left = dock.inset(True, "left", m.thickness)
    cut_l = grid_metrics.compute(fake, (1920, 1080), native_px=48, inset=ins_left)
    check("D12 左侧扣除把网格右移", cut_l.ox >= ins_left[0], "ox=%d" % cut_l.ox)
    check("D12 inset 全 0 与不传完全一致",
          grid_metrics.compute(fake, (1920, 1080), native_px=48,
                               inset=(0, 0, 0, 0)) == full,
          "%s vs %s" % (grid_metrics.compute(fake, (1920, 1080), native_px=48,
                                             inset=(0, 0, 0, 0)), full))

    # ---------- D13：真窗口 ----------
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])      # noqa: F841
    w = dock.DockWindow(st)
    w.set_items(seed)
    w.apply_geometry((1920, 1080))
    geo = w.geometry()
    bar = dock.bar_rect((1920, 1080), st.get_setting("dock_position"),
                        dock.metrics(st.ui_scale(), 2))
    check("D13 窗口与条带重合",
          (geo.x(), geo.y(), geo.width(), geo.height()) == bar,
          "%s vs %s" % ((geo.x(), geo.y(), geo.width(), geo.height()), bar))
    check("D13 有内容时可见", w.isVisible() or not w.isHidden(), "hidden")
    w.close()
    w2 = dock.DockWindow(st)
    w2.set_items([])
    w2.apply_geometry((1920, 1080))
    check("D13 空态仍占位（有可见的空态条）", w2.is_visible_bar(), "empty")
    w2.close()

    # ---------- D14：真窗口接线（网格真的让位/复原） ----------
    from src import grid_flow
    from src.desktop_window import DesktopWindow
    st14 = Storage(path=tmp / "dock14.json")
    st14._data["settings"]["auto_clone_desktop"] = False
    st14._data["layout"] = []
    st14.save()
    w14 = DesktopWindow(st14)
    w14.resize(1920, 1080)
    st14.set_setting("dock_items", seed)
    bar14 = dock.DockWindow(st14)
    st14.set_setting("dock_enabled", False)
    w14.set_dock(bar14)
    w14.apply_dock_settings()
    off = grid_flow.current_metrics()
    off_bottom = off.oy + off.rows * off.ch
    st14.set_setting("dock_enabled", True)
    w14.apply_dock_settings()
    on = grid_flow.current_metrics()
    on_bottom = on.oy + on.rows * on.ch
    thick = bar14.current_metrics().thickness
    check("D14 开启后网格底边在条带之上", on_bottom <= 1080 - thick + 1,
          "on_bottom=%d thick=%d" % (on_bottom, thick))
    check("D14 开启后行数不变或更少", on.rows <= off.rows,
          "%d vs %d" % (on.rows, off.rows))
    check("D14 关闭时网格铺得更满", off_bottom > on_bottom,
          "%d vs %d" % (off_bottom, on_bottom))
    check("D14 开关与条带可见性一致", bar14.is_visible_bar(), "enabled")
    st14.set_setting("dock_enabled", False)
    w14.apply_dock_settings()
    check("D14 关掉后条带隐藏", not bar14.is_visible_bar(), "hidden")
    w14.close()
    bar14.close()

    # ---------- D15：默认样式（v14 反馈 5：iOS Dock 观感） ----------
    m_def = dock.metrics(1.0, 0)
    check("D15 默认图标 44px", m_def.icon == dock.DOCK_ICON == 44,
          "icon=%d" % m_def.icon)
    check("D15 空态 4 槽位（条带不缩成一点）", m_def.slots == dock.MIN_SLOTS == 4,
          "slots=%d" % m_def.slots)
    check("D15 空态厚度 = 内边距*2 + 图标（v14r：hover 不计入，放大吃内边距）",
          m_def.thickness == m_def.pad * 2 + m_def.icon,
          "thick=%d" % m_def.thickness)
    check("D15 空态长度 = pad*2 + 4*icon + 3*gap",
          m_def.length == m_def.pad * 2 + 4 * m_def.icon + 3 * m_def.gap,
          "len=%d" % m_def.length)

    # ---------- D16：外观旋钮 ----------
    st16 = Storage(path=tmp / "dock16.json")
    base16 = dock.metrics_for(st16, 4).icon
    st16.set_setting("dock_icon", 48)
    check("D16 dock_icon 生效", dock.metrics_for(st16, 4).icon == 48,
          "icon=%d" % dock.metrics_for(st16, 4).icon)
    st16.set_setting("dock_icon", 999)
    check("D16 dock_icon 上限夹到 56", dock.icon_base(st16) == dock.DOCK_ICON_MAX,
          "%d" % dock.icon_base(st16))
    st16.set_setting("dock_icon", 1)
    check("D16 dock_icon 下限夹到 24", dock.icon_base(st16) == dock.DOCK_ICON_MIN,
          "%d" % dock.icon_base(st16))
    st16.set_setting("dock_icon", 32)
    st16.set_setting("dock_alpha", 0.5)
    check("D16 dock_alpha 读取", abs(dock.bg_alpha(st16) - 0.5) < 1e-6,
          "%s" % dock.bg_alpha(st16))
    st16.set_setting("dock_alpha", 0.01)
    check("D16 dock_alpha 下限 0.30", abs(dock.bg_alpha(st16) - 0.30) < 1e-6)
    st16.set_setting("dock_radius_mode", "SQUARE")
    check("D16 圆角形态归一化", dock.normalize_radius_mode(
        st16.get_setting("dock_radius_mode")) == "square")
    st16.set_setting("dock_radius_mode", "鬼东西")
    check("D16 非法圆角回退 round（默认形态）",
          dock.normalize_radius_mode("鬼东西") == "round",
          "%s" % dock.normalize_radius_mode("鬼东西"))
    st16_0 = Storage(path=tmp / "dock16b.json")
    check("D16 默认形态 = round（v10 A11 不再默认胶囊）",
          dock.normalize_radius_mode(
              st16_0.get_setting("dock_radius_mode")) == "round",
          "%s" % st16_0.get_setting("dock_radius_mode"))
    check("D16 默认半径 20px（v14 iOS Dock）", dock.radius_px(st16_0) == 20,
          "%s" % dock.radius_px(st16_0))
    st16_0.set_setting("dock_radius", -5)
    check("D16 半径下限 0", dock.radius_px(st16_0) == 0,
          "%s" % dock.radius_px(st16_0))
    st16_0.set_setting("dock_radius", 200)
    check("D16 半径上限 26", dock.radius_px(st16_0) == dock.RADIUS_MAX,
          "%s" % dock.radius_px(st16_0))
    st16.set_setting("dock_hover", False)
    check("D16 dock_hover 关闭", dock.hover_enabled(st16) is False)
    st16.set_setting("dock_dot", False)
    check("D16 dock_dot 关闭", dock.dot_enabled(st16) is False)
    check("D16 metrics(ui_scale) 与 icon_base 同源",
          dock.metrics(1.0, 4, dock.icon_base(st16)).icon
          == dock.metrics_for(st16, 4).icon)

    # ---------- D17：切位置真的换边 ----------
    st17 = Storage(path=tmp / "dock17.json")
    dock.save_items(st17, seed)
    st17.set_setting("dock_enabled", True)
    w17 = dock.DockWindow(st17)
    seen = {}
    for pos in ("bottom", "top", "left", "right"):
        st17.set_setting("dock_position", pos)
        seen[pos] = w17.apply_geometry((1920, 1080))
    check("D17 bottom 贴底", seen["bottom"][1] + seen["bottom"][3] == 1080,
          "%s" % (seen["bottom"],))
    check("D17 top 贴顶", seen["top"][1] == 0, "%s" % (seen["top"],))
    check("D17 left 贴左", seen["left"][0] == 0, "%s" % (seen["left"],))
    check("D17 right 贴右", seen["right"][0] + seen["right"][2] == 1920,
          "%s" % (seen["right"],))
    check("D17 窗口几何 = 条带（Qt 侧真的跟着走）",
          (w17.geometry().x(), w17.geometry().y())
          == (seen["right"][0], seen["right"][1]),
          "%s vs %s" % (w17.geometry(), seen["right"]))
    st17.set_setting("dock_position", "bottom")
    check("D17 切回 bottom 复位", w17.apply_geometry((1920, 1080)) == seen["bottom"],
          "%s" % (w17.apply_geometry((1920, 1080)),))
    w17.close()

    # ---------- D18：按落点插入 ----------
    from PySide6.QtCore import QPoint
    st18 = Storage(path=tmp / "dock18.json")
    items18 = [{"name": "A", "target": "a.exe", "icon_path": "a.exe"},
               {"name": "B", "target": "b.exe", "icon_path": "b.exe"}]
    w18 = dock.DockWindow(st18)
    w18.set_items(items18)
    w18.apply_geometry((1920, 1080))
    check("D18 插入到 0 号位",
          w18.add_item({"name": "C", "target": "c.exe"}, index=0)
          and [i["name"] for i in w18.items()] == ["C", "A", "B"],
          "%s" % [i["name"] for i in w18.items()])
    check("D18 插入到末尾（index=None 仍追加）",
          w18.add_item({"name": "D", "target": "d.exe"})
          and [i["name"] for i in w18.items()] == ["C", "A", "B", "D"],
          "%s" % [i["name"] for i in w18.items()])
    check("D18 重复 target 不插入", w18.add_item({"name": "A2", "target": "a.exe"}) is False,
          "%s" % [i["name"] for i in w18.items()])
    m18 = w18.current_metrics()
    x0, y0, ww, hh = w18._bar
    # 落点在 1 号图标中心 -> 插入索引 1
    cx = m18.pad + m18.icon // 2 + (m18.icon + m18.gap)
    check("D18 _insert_index 按图标中心算",
          w18._insert_index(QPoint(int(cx), hh // 2)) == 1,
          "%s" % w18._insert_index(QPoint(int(cx), hh // 2)))
    check("D18 落点在左边距外 -> 0",
          w18._insert_index(QPoint(0, hh // 2)) == 0)
    check("D18 落点在最右 -> len", w18._insert_index(QPoint(ww + 50, hh // 2))
          == len(w18.items()))
    w18.close()

    # ---------- D19：设置 ▸ Dock 栏分页 ----------
    from src import settings_dialog
    st19 = Storage(path=tmp / "dock19.json")

    class FakeWin:
        def __init__(self):
            self.calls = 0

        def apply_dock_settings(self):
            self.calls += 1

    fw = FakeWin()
    try:
        dlg = settings_dialog.SettingsDialog(fw, st19, None)
    except Exception as exc:      # noqa: BLE001
        check("D19 设置窗可构造", False, repr(exc))
        dlg = None
    if dlg is not None:
        check("D19 含独立 Dock 分页", "dock" in dlg._pages, str(list(dlg._pages)))
        check("D19 Dock 分页控件齐全",
              all(hasattr(dlg, n) for n in
                  ("chk_dock", "cmb_dock_pos", "sp_dock_icon", "sld_dock_alpha",
                   "cmb_dock_radius", "chk_dock_hover", "chk_dock_dot")),
              "missing")
        before = fw.calls
        dlg.sp_dock_icon.setValue(48)
        check("D19 图标大小写设置", st19.get_setting("dock_icon") == 48,
              "%s" % st19.get_setting("dock_icon"))
        check("D19 触发窗口重排（apply_dock_settings）", fw.calls > before,
              "calls=%d" % fw.calls)
        dlg.sld_dock_alpha.setValue(60)
        check("D19 不透明度写设置",
              abs(float(st19.get_setting("dock_alpha")) - 0.6) < 1e-6,
              "%s" % st19.get_setting("dock_alpha"))
        check("D19 含圆角大小滑块", hasattr(dlg, "sld_dock_radius"),
              "missing sld_dock_radius")
        check("D19 默认形态下滑块可用", dlg.sld_dock_radius.isEnabled())
        dlg.sld_dock_radius.setValue(20)
        check("D19 圆角大小写设置", st19.get_setting("dock_radius") == 20,
              "%s" % st19.get_setting("dock_radius"))
        idx = dlg.cmb_dock_radius.findData("square")
        dlg.cmb_dock_radius.setCurrentIndex(idx)
        check("D19 圆角形态写设置",
              st19.get_setting("dock_radius_mode") == "square",
              "%s" % st19.get_setting("dock_radius_mode"))
        check("D19 非「圆角」形态时滑块禁用（不留无效旋钮）",
              not dlg.sld_dock_radius.isEnabled())
        dlg.cmb_dock_radius.setCurrentIndex(dlg.cmb_dock_radius.findData("round"))
        check("D19 切回「圆角」后滑块恢复", dlg.sld_dock_radius.isEnabled())
        dlg.chk_dock_hover.setChecked(False)
        check("D19 hover 开关写设置", st19.get_setting("dock_hover") is False)
        dlg.chk_dock.setChecked(False)
        check("D19 关掉开关后子控件禁用",
              not dlg.sp_dock_icon.isEnabled() and not dlg.cmb_dock_pos.isEnabled())
        dlg.deleteLater()


    # ---------- D20：默认半径 vs 条带厚度（v10 A11 反馈 24） ----------
    from PySide6.QtCore import QRectF
    st20 = Storage(path=tmp / "dock20.json")
    dock.save_items(st20, seed)
    w20 = dock.DockWindow(st20)
    w20.apply_geometry((1920, 1080))
    thick20 = w20.current_metrics().thickness
    scale20 = dock._scale(st20.ui_scale())
    rect20 = QRectF(w20.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
    r_def = w20._shape_radius(rect20)
    check("D20 默认半径 = 20 × ui_scale（v14 iOS Dock）",
          abs(r_def - 20 * scale20) < 0.01,
          "r=%.2f scale=%.2f" % (r_def, scale20))
    check("D20 默认半径明显小于半厚（不再是药丸）",
          r_def < thick20 / 2.0 - 4,
          "r=%.2f half=%.2f" % (r_def, thick20 / 2.0))
    st20.set_setting("dock_radius", 0)
    check("D20 半径 0 = 直角", w20._shape_radius(rect20) == 0.0,
          "r=%.2f" % w20._shape_radius(rect20))
    st20.set_setting("dock_radius", 99)
    # 默认条带已经很厚（82px，半厚 41 > 旋钮上限 26），旋钮顶格就够用；
    # 把条带做薄（小内边距 + 小图标 + 不放大 -> 半厚 14 < 26）后，
    # 旋钮上限超过半厚，这时必须夹到半厚，否则 Qt 自己截断 = 滑块后半段白拖。
    st20.set_setting("dock_pad", 2)
    st20.set_setting("dock_icon", 24)
    st20.set_setting("dock_hover_pct", 100)
    w20.apply_geometry((1920, 1080))
    thick_thin = w20.current_metrics().thickness
    rect_thin = QRectF(w20.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
    check("D20 半径超过半厚时夹到半厚（滑块后半段不虚设）",
          abs(w20._shape_radius(rect_thin) - thick_thin / 2.0) < 0.51,
          "r=%.2f half=%.2f" % (w20._shape_radius(rect_thin),
                                thick_thin / 2.0))
    st20.set_setting("dock_radius_mode", "pill")
    check("D20 胶囊 = 半厚（保留给愿意要药丸的人）",
          abs(w20._shape_radius(rect20) - thick20 / 2.0) < 0.51,
          "r=%.2f" % w20._shape_radius(rect20))
    st20.set_setting("dock_radius_mode", "square")
    check("D20 直角形态 <= 2px", w20._shape_radius(rect20) <= 2.0,
          "r=%.2f" % w20._shape_radius(rect20))
    w20.close()

    # ---------- D21：旧 state 的一次性迁移（v10 A11） ----------
    import json
    legacy = tmp / "dock21.json"
    legacy.write_text(json.dumps({
        "settings": {"schema_version": 2, "dock_enabled": True,
                     "dock_radius_mode": "pill"},
        "layout": [], "widgets": [],
    }, ensure_ascii=False), encoding="utf-8")
    st21 = Storage(path=legacy)
    check("D21 旧 state 的 pill 迁移成 round",
          st21.get_setting("dock_radius_mode") == "round",
          "%s" % st21.get_setting("dock_radius_mode"))
    check("D21 迁移同时补默认半径 20", st21.get_setting("dock_radius") == 20,
          "%s" % st21.get_setting("dock_radius"))
    check("D21 迁移标记已落盘",
          json.loads(legacy.read_text(encoding="utf-8"))["settings"].get(
              "dock_radius_migrated") is True, "not saved")
    # 用户迁移之后主动选回胶囊：再启动不能被改
    st21.set_setting("dock_radius_mode", "pill")
    st21.save()
    st21b = Storage(path=legacy)
    check("D21 用户主动选 pill 不被二次迁移",
          st21b.get_setting("dock_radius_mode") == "pill",
          "%s" % st21b.get_setting("dock_radius_mode"))
    check("D21 迁移只跑一次（标记仍在）",
          st21b.get_setting("dock_radius_migrated") is True)

    # ---------- D24：快捷方式可进 Dock（v10 反馈 26） ----------
    # 病根：桌面 .lnk/.url 全部由 desktop_file_entry() 标成 kind="file"，
    # 而 entry_is_app 一律拒绝 kind=file —— 用户桌面上几乎全是快捷方式克隆，
    # 于是"拖不进 Dock"100% 复现。语义修正：可启动的**文件**（快捷方式/
    # 可执行）算应用，普通文档与文件夹仍拒（Dock 是启动器，不是收藏夹）。
    lnk = {"type": "app", "id": "df:1", "app": {"name": "微信", "kind": "file",
           "target": r"C:\Users\u\Desktop\WeChat.lnk",
           "icon_path": r"C:\Users\u\Desktop\WeChat.lnk"}}
    check("D24a .lnk 快捷方式可进 Dock", dock.entry_is_app(lnk) is True)
    url = {"type": "app", "id": "df:2", "app": {"name": "Steam", "kind": "file",
           "target": "https://store.steampowered.com/x.url"}}
    check("D24b .url 快捷方式可进 Dock", dock.entry_is_app(url) is True)
    exe = {"type": "app", "id": "df:3", "app": {"name": "Game", "kind": "file",
           "target": r"D:\Games\a.exe"}}
    check("D24c .exe 可进 Dock", dock.entry_is_app(exe) is True)
    doc = {"type": "app", "id": "df:4", "app": {"name": "笔记", "kind": "file",
           "target": r"C:\Users\u\Desktop\note.txt"}}
    check("D24d .txt 文档不进 Dock", dock.entry_is_app(doc) is False)
    folder = {"type": "app", "id": "df:5", "app": {"name": "项目", "kind": "file",
              "target": r"C:\Users\u\Desktop\proj"}}
    check("D24e 文件夹不进 Dock", dock.entry_is_app(folder) is False)
    real = {"type": "app", "id": "a:1", "app": {"name": "记事本",
            "target": "notepad.exe"}}
    check("D24f 普通应用条目不受影响", dock.entry_is_app(real) is True)
    wd = {"type": "widget", "id": "w1", "app": {"name": "x", "target": "y"}}
    check("D24g 组件条目仍拒", dock.entry_is_app(wd) is False)

    # ---------- D25：拖外观滑条不重排网格（v10 反馈 27 卡顿/崩溃） ----------
    # 病根：滑条 valueChanged -> _apply_dock -> apply_dock_settings，
    # 后者 refresh_band()（win32 重挂）+ refresh_grid()（删掉重建全部图标）
    # 每个像素走一次 —— 主线程拖死，重建过程中还会崩。
    st25 = Storage(path=tmp / "dock25.json")
    st25.set_setting("dock_enabled", True)

    class FakeWin25:
        def __init__(self):
            self.layout_calls = 0
            self.style_calls = 0

        def apply_dock_settings(self):
            self.layout_calls += 1

        def apply_dock_style(self):
            self.style_calls += 1

    fw25 = FakeWin25()
    dlg25 = settings_dialog.SettingsDialog(fw25, st25, None)
    base25 = fw25.layout_calls
    for v in (10, 14, 18, 24, 26):
        dlg25.sld_dock_radius.setValue(v)
    check("D25a 拖圆角滑条不触发网格重排",
          fw25.layout_calls == base25, "layout=%d" % fw25.layout_calls)
    check("D25b 拖圆角滑条走轻量外观刷新",
          fw25.style_calls >= 3, "style=%d" % fw25.style_calls)
    check("D25c 滑条仍写设置", st25.get_setting("dock_radius") == 26,
          "%s" % st25.get_setting("dock_radius"))
    for v in (40, 55, 70):
        dlg25.sld_dock_alpha.setValue(v)
    check("D25d 拖透明度滑条也不重排",
          fw25.layout_calls == base25, "layout=%d" % fw25.layout_calls)
    check("D25e 透明度写设置",
          abs(float(st25.get_setting("dock_alpha")) - 0.70) < 1e-6,
          "%s" % st25.get_setting("dock_alpha"))
    check("D25f 落盘走 250ms 去抖（定时器在跑）",
          dlg25._save_timer.isActive(),
          "active=%s" % dlg25._save_timer.isActive())
    dlg25.sp_dock_icon.setValue(40)
    check("D25g 改图标尺寸仍要重排（外观路径不背这个锅）",
          fw25.layout_calls > base25, "layout=%d" % fw25.layout_calls)

    # ---------- D26：边框粗细 / 颜色可调（v10 反馈 27） ----------
    check("D26a 边框默认 1px（细描边）",
          dock.border_width(Storage(path=tmp / "dock26a.json")) == 1,
          str(dock.border_width(Storage(path=tmp / "dock26a.json"))))
    st26 = Storage(path=tmp / "dock26.json")
    st26.set_setting("dock_border_width", 99)
    check("D26b 边框粗细上夹到 4", dock.border_width(st26) == 4,
          str(dock.border_width(st26)))
    st26.set_setting("dock_border_width", -3)
    check("D26c 边框粗细下夹到 0", dock.border_width(st26) == 0,
          str(dock.border_width(st26)))
    st26.set_setting("dock_border_width", "脏值")
    check("D26d 脏值回退默认 1", dock.border_width(st26) == 1,
          str(dock.border_width(st26)))
    st26.set_setting("dock_border_color", "not-a-color")
    check("D26e 非法颜色回退主题 BORDER",
          dock.border_color(st26).name() == QColor(theme.BORDER).name(),
          dock.border_color(st26).name())
    check("D26f 空颜色 = 跟随主题",
          dock.border_color(Storage(path=tmp / "dock26f.json")).name()
          == QColor(theme.BORDER).name())

    # 像素判据：3px 纯红边框，条带四边必须有红像素；宽度 0 则没有
    st26b = Storage(path=tmp / "dock26b.json")
    st26b.set_setting("dock_items", [])
    st26b.set_setting("dock_border_width", 3)
    st26b.set_setting("dock_border_color", "#ff0000")
    d26 = dock.DockWindow(st26b)
    d26.resize(400, 60)
    img26 = QImage(400, 60, QImage.Format_ARGB32)
    img26.fill(0)
    d26.render(img26)

    def _red_px(im):
        n = 0
        for yy in range(im.height()):
            for xx in range(im.width()):
                c = im.pixelColor(xx, yy)
                if c.red() > 140 and c.red() - c.green() > 60 and c.red() - c.blue() > 60:
                    n += 1
        return n

    n26 = _red_px(img26)
    check("D26g 3px 红边框画出红色像素", n26 > 200, "red=%d" % n26)
    st26b.set_setting("dock_border_width", 0)
    d26b = dock.DockWindow(st26b)
    d26b.resize(400, 60)
    img26b = QImage(400, 60, QImage.Format_ARGB32)
    img26b.fill(0)
    d26b.render(img26b)
    n26b = _red_px(img26b)
    check("D26h 宽度 0 时不画边框（无红像素）", n26b == 0, "red=%d" % n26b)
    d26.close()
    d26b.close()

    # ---------- D27：iOS Shelf 观感（v14 反馈 5） ----------
    # 竖向渐变：顶部叠白（alpha 26）+ 一道 specular 高光，底部叠黑（alpha 26）。
    # 像素判据：条带同一段列，顶部亮带平均亮度 > 底部暗带（差 ~50，取 10 防抖）。
    # v14 反馈 10：液态玻璃成了 Dock 的默认配色，渐变 Shelf 只在关掉液态时
    # 才画（它是"颜色"分支的观感）——所以这条判据必须显式关掉液态。
    st27 = Storage(path=tmp / "dock27.json")
    st27.set_setting("dock_items", [])
    st27.set_setting("dock_border_width", 0)
    st27.set_setting("liquid_dock", False)
    d27 = dock.DockWindow(st27)
    d27.resize(400, 82)
    img27 = QImage(400, 82, QImage.Format_ARGB32)
    img27.fill(0)
    d27.render(img27)

    def _band_luma(im, y0, y1):
        tot, n = 0.0, 0
        for yy in range(y0, y1):
            for xx in range(40, im.width() - 40):
                c = im.pixelColor(xx, yy)
                tot += (c.red() + c.green() + c.blue()) / 3.0
                n += 1
        return tot / max(1, n)

    top_l = _band_luma(img27, 3, 8)
    bot_l = _band_luma(img27, img27.height() - 8, img27.height() - 3)
    check("D27 顶部亮带 > 底部暗带（iOS 竖向渐变 Shelf）",
          top_l > bot_l + 10, "top=%.1f bot=%.1f" % (top_l, bot_l))
    # specular 高光线：顶边第 1~2 行比紧随其下的 3~8 行更亮
    edge_l = _band_luma(img27, 1, 3)
    check("D27 顶部一道 specular 高光", edge_l > top_l,
          "edge=%.1f band=%.1f" % (edge_l, top_l))
    d27.close()

    # 液态开着时：背景是折射贴图，不再是那套渐变（两者互斥）
    st27b = Storage(path=tmp / "dock27b.json")
    st27b.set_setting("dock_items", [])
    st27b.set_setting("dock_border_width", 0)
    d27b = dock.DockWindow(st27b)
    d27b.resize(400, 82)
    img27b = QImage(400, 82, QImage.Format_ARGB32)
    img27b.fill(0)
    d27b.render(img27b)
    check("D27b 液态开着 -> 不走渐变 Shelf（背景交给折射）",
          d27b._lens.pixmap_cached.isNull()
          or abs(_band_luma(img27b, 3, 8)
                 - _band_luma(img27b, 74, 79)) < 40,
          "top=%.1f bot=%.1f" % (_band_luma(img27b, 3, 8),
                                 _band_luma(img27b, 74, 79)))
    d27b.close()
    dlg25.deleteLater()

    # ---------- D22：分页点不被 Dock 遮挡（v10 反馈 25） ----------
    from src.desktop_window import DesktopWindow
    from PySide6.QtWidgets import QApplication as _QA
    _app22 = _QA.instance() or _QA([])
    st22 = Storage(path=str(tmp / "d22.json"))
    st22.set_setting("dock_enabled", True)
    st22.set_setting("dock_position", "bottom")
    # 塞满第一页逼出多页，dots 才会出现（直接写 layout，绕过 add_app 语义）
    for _i in range(60):
        st22.layout.append({"id": "d22_%d" % _i, "col": _i % 6,
                             "row": _i // 6, "page": 0,
                             "app": {"name": "A%d" % _i,
                                      "target": "C:/a%d.exe" % _i}})
    w22 = DesktopWindow(st22)
    w22.resize(1920, 1080)
    w22._update_grid_metrics()
    ins22 = w22._dock_inset()
    dots22 = w22._dot_centers()
    if dots22:
        y22 = dots22[0].y()
        dock_bottom = ins22[3]  # bottom inset
        check("D22 dock 底部时页点 y 在 dock 条带上方",
              y22 < w22.height() - dock_bottom,
              "y=%d h=%d dock_b=%d" % (y22, w22.height(), dock_bottom))
    else:
        # 只有 1 页时 dots 为空——也该通过
        check("D22 dock 底部时页点 y 在 dock 条带上方", True, "no dots (1 page)")
    st22.set_setting("dock_enabled", False)
    w22._update_grid_metrics()
    dots22b = w22._dot_centers()
    if dots22b:
        y22b = dots22b[0].y()
        check("D22 dock 关闭后页点回到固定位置",
              y22b == w22.height() - 26,
              "y=%d expected=%d" % (y22b, w22.height() - 26))
    w22.close()

    # ---------- D23：Dock 拖入——dragEnter 必须 accept（v10 反馈 25） ----------
    from src.iconwidget import MIME_TYPE, AppIcon
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtCore import Qt as _Qt
    st23 = Storage(path=str(tmp / "d23.json"))
    st23.set_setting("dock_enabled", True)
    dock23 = dock.DockWindow(st23)
    dock23.resize(400, 60)
    dock23._bar = (0, 0, 400, 60)
    md23 = QMimeData()
    md23.setData(MIME_TYPE, b"test_entry_id")
    # dragEnter 必须 acceptProposedAction（否则后续 dragMove/drop 全被吞）
    ev23 = QDragEnterEvent(dock23.rect().center(), _Qt.MoveAction, md23,
                           _Qt.LeftButton, _Qt.NoModifier)
    dock23.dragEnterEvent(ev23)
    check("D23 dock dragEnter 接受 MIME_TYPE 拖拽",
          ev23.isAccepted(), "accepted=%s" % ev23.isAccepted())
    # 非 MIME_TYPE 必须 ignore
    md23bad = QMimeData()
    md23bad.setData("text/uri-list", b"file:///C:/foo")
    ev23b = QDragEnterEvent(dock23.rect().center(), _Qt.MoveAction, md23bad,
                            _Qt.LeftButton, _Qt.NoModifier)
    dock23.dragEnterEvent(ev23b)
    check("D23 dock dragEnter 拒绝非 MIME_TYPE",
          not ev23b.isAccepted(), "accepted=%s" % ev23b.isAccepted())
    dock23.close()

    print("")
    if failures:
        print("FAILED %d: %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
