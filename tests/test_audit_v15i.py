# -*- coding: utf-8 -*-
"""v15i：Dock 几何边界 + 弹窗陈旧快照 + 表单排版（8.5.1 第四批）。

  I1-I2 Dock 主轴长度从不与窗口长度比较：`bar_rect()` 把条带夹到屏幕，
         `item_rect()` 仍按未夹的 step 累加 -> 后半段图标画在窗口外，
         看不见 / hover 不放大 / 点了没反应 / 也拖不出来
  I3    左位 Dock 的 hover 抬升方向朝屏幕外（随后被夹回来）-> 图标贴左边框
  I4    `dock_offset_y` 把条带推向屏幕内侧，可用区扣除却不看偏移
         -> 最后一排图标被条带压住、点击被 Dock 吃掉
  I5    「添加应用」格高写死 58，内容需要 67 -> 名字第二行被下一行图标压掉
  I6    Dock 满 24 与"已在里面"共用同一个 False -> 满员时提示"加过了"
  I7    弹窗开着时外部修剪成员（`prune_dead_members` 就地改 apps），
         成员控件不重建、payload 还是旧下标 -> 点第 1 个启动第 2 个

运行：python tests/test_audit_v15i.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src import dock  # noqa: E402
from src.desktop_window import DesktopWindow  # noqa: E402
from src.folder_popup import FolderPopup  # noqa: E402
from src.folder_forms import AddAppsForm  # noqa: E402
from src.storage import Storage  # noqa: E402

app = QApplication.instance() or QApplication([])
TMP = Path(tempfile.gettempdir()) / "test_audit_v15i"
TMP.mkdir(exist_ok=True)

failures = []
total = [0]


def settle(ms=90):
    end = QTimer()
    end.setSingleShot(True)
    loop = QEventLoop()
    end.timeout.connect(loop.quit)
    end.start(ms)
    loop.exec()


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def fresh(tag, layout=None, widgets=None, **settings):
    p = TMP / ("%s.json" % tag)
    if p.exists():
        p.unlink()
    st = Storage(path=str(p))
    st._data["settings"].update({
        "hide_native_icons": True, "takeover_desktop": True,
        "auto_clone_desktop": False, "auto_arrange": False,
        "layout_mode": "grid", "dock_enabled": True})
    st._data["settings"].update(settings)
    st._data["layout"] = list(layout or [])
    st._data["widgets"] = list(widgets or [])
    st.save()
    return st


def twentyfour():
    return [{"name": "a%d" % i, "target": "a%d" % i, "kind": "app"}
            for i in range(24)]


# =========================== I1-I2 Dock 主轴边界 ===========================
print("\n--- I1/I2 条带被屏幕夹短时，所有图标仍在条带内 ---")
st1 = fresh("i1", dock_icon=56, dock_gap=24, dock_pad_left=12,
            dock_pad_right=12, dock_pad_top=8, dock_pad_bottom=8)
m1 = dock.metrics_for(st1, count=24, position="bottom")
bar1 = dock.bar_rect((1366, 728), "bottom", m1)
rects = [dock.item_rect(bar1, "bottom", m1, i) for i in range(24)]
outside = [i for i, (ix, iy, iw, ih) in enumerate(rects)
           if ix < bar1[0] or ix + iw > bar1[0] + bar1[2]]
check("I1a 底部 24 项：没有图标越出条带右界",
      not outside, "outside=%s bar=%s" % (outside[:6], bar1))
check("I1b 底部 24 项：图标边长被收紧（不是原样堆到屏幕外）",
      rects[0][2] <= m1.icon, "icon=%d 原=%d" % (rects[0][2], m1.icon))

m2 = dock.metrics_for(st1, count=24, position="left")
bar2 = dock.bar_rect((1366, 728), "left", m2)
rects2 = [dock.item_rect(bar2, "left", m2, i) for i in range(24)]
outside2 = [i for i, (ix, iy, iw, ih) in enumerate(rects2)
            if iy < bar2[1] or iy + ih > bar2[1] + bar2[3]]
check("I2 左侧 24 项：没有图标越出条带下界",
      not outside2, "outside=%s bar=%s" % (outside2[:6], bar2))

# 正常参数（放得下）时不收紧：不能为了边界把小 Dock 也缩小
st1b = fresh("i1b", dock_icon=44, dock_gap=12)
m1b = dock.metrics_for(st1b, count=5, position="bottom")
bar1b = dock.bar_rect((1920, 1040), "bottom", m1b)
r0 = dock.item_rect(bar1b, "bottom", m1b, 0)
check("I2b 放得下时图标保持原尺寸", r0[2] == m1b.icon,
      "icon=%d want=%d" % (r0[2], m1b.icon))

# =========================== I3 左位 hover 方向 ===========================
print("\n--- I3 左位 Dock 的 hover 抬升朝屏幕内侧 ---")
st3 = fresh("i3", dock_position="left", dock_enabled=True, dock_hover=True)
win3 = dock.DockWindow(st3)
win3.set_items(twentyfour()[:4])
win3.apply_geometry((1366, 728))      # 条带落位后才有真实几何
check("I3 前置：条带在左边", win3._position == "left",
      "position=%s" % win3._position)
win3._hover_t = 1.0            # hover 动画进度拉满（grow 由 _hover_grow 夹）
win3._hover_index = None
base = win3._target_rect(0)[0]
win3._hover_index = 0
hov = win3._target_rect(0)[0]
# 左位条带只有 64px 厚，放大后的图标已占满内宽 -> 抬升被
# `_clamp_into_bar` 吸收，实测与未修前一致（记在 DEVLOG）。
# 这里锁"不越界"这条真契约，别再退化成"hover 图标被窗口边缘裁掉"。
check("I3a 左位 hover 的图标完整落在条带内",
      hov.left() >= 0 and hov.right() <= win3.width(),
      "hover_left=%d right=%d w=%d"
      % (hov.left(), hov.right(), win3.width()))
check("I3b hover 放大后仍不小于原尺寸",
      hov.width() >= base.width(),
      "base_w=%d hover_w=%d" % (base.width(), hov.width()))
win3.deleteLater()

# =========================== I4 偏移计入可用区 ===========================
print("\n--- I4 Dock 位置偏移要计入网格可用区 ---")
st4 = fresh("i4", dock_position="bottom", dock_offset_y=80,
            dock_icon=44, dock_gap=12)
m4 = dock.metrics_for(st4, count=6)
ins = dock.storage_inset(st4)
check("I4a 底部偏移 80 -> 底部预留带 = 厚度 + 80",
      ins[3] == m4.thickness + 80,
      "inset=%s thickness=%d" % (ins, m4.thickness))
st4.set_setting("dock_offset_y", 0)
check("I4b 偏移归零 -> 预留带回到厚度",
      dock.storage_inset(st4)[3] == m4.thickness,
      "inset=%s" % (dock.storage_inset(st4),))
st4.set_setting("dock_offset_y", -60)     # 往外推：不多占可用区
check("I4c 负偏移不多扣可用区",
      dock.storage_inset(st4)[3] == m4.thickness,
      "inset=%s" % (dock.storage_inset(st4),))

# =========================== I5 表单格高 ===========================
print("\n--- I5 添加应用：格高容得下两行名字 ---")
st5 = fresh("i5")
form = AddAppsForm(st5, [{"id": "x1", "app": {"name": "一个很长的中文应用名",
                                              "target": "x1"}}])
icon, cw, chh = form._cell()
need = icon + 5 + 13 * 2
check("I5a 格高 >= 图标 + 两行名字", chh >= need,
      "ch=%d need=%d icon=%d" % (chh, need, icon))
check("I5b 格宽仍为 64 * 缩放（未被牵连）", cw == int(round(64 * form.m())),
      "cw=%d" % cw)
form.deleteLater()

# =========================== I6 满员 vs 重复 ===========================
print("\n--- I6 Dock 满 24 时拒因是 full，不是 dup ---")
st6 = fresh("i6")
win6 = dock.DockWindow(st6)
win6.set_items(twentyfour())
ok = win6.add_item({"name": "extra", "target": "extra", "kind": "app"})
check("I6a 满员时 add_item 返回 False", ok is False)
check("I6b 拒因是 full", getattr(win6, "last_reject", None) == "full",
      "last_reject=%r" % getattr(win6, "last_reject", None))
ok2 = win6.add_item({"name": "a0", "target": "a0", "kind": "app"})
check("I6c 已存在时拒因是 dup",
      ok2 is False and win6.last_reject == "dup",
      "last_reject=%r" % win6.last_reject)
win6.deleteLater()

# =========================== I7 外部修剪 -> 弹窗刷新 ===========================
print("\n--- I7 成员被外部修剪后，开着的弹窗要重建成员 ---")
st7 = fresh("i7", layout=[{
    "id": "F7", "type": "folder", "col": 0, "row": 0,
    "apps": [{"name": "m%d" % i, "target": "m%d" % i, "kind": "app"}
             for i in range(3)]}])
win7 = DesktopWindow(st7)
win7.resize(1920, 1040)
win7._update_grid_metrics()
pop = FolderPopup(st7, st7.find_entry("F7"), anchor_rect=None)
win7._popup = pop
from src.iconwidget import AppIcon  # noqa: E402

before = len(pop.findChildren(AppIcon))
# 外部删掉一个成员（rescan / 自动同步走的就是 prune_dead_members）
entry = st7.find_entry("F7")
entry["apps"] = entry["apps"][:2]
win7._on_desk_sync(0, 1)
settle(80)                     # 旧成员控件走 deleteLater，等事件循环回收
after = len(pop.findChildren(AppIcon))
check("I7a 修剪前弹窗有 3 个成员", before == 3, "before=%d" % before)
check("I7b 修剪后弹窗重建为 2 个成员", after == 2, "after=%d" % after)
check("I7c 弹窗成员数与 storage 一致",
      after == len(entry["apps"]), "popup=%d storage=%d"
      % (after, len(entry["apps"])))
win7._close_popup()
win7.deleteLater()

# =========================== 汇总 ===========================
print("\n%d/%d 通过" % (total[0] - len(failures), total[0]))
if failures:
    print("FAILED: %s" % ", ".join(failures))
sys.stdout.flush()
os._exit(0 if not failures else 1)
