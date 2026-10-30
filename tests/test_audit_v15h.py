# -*- coding: utf-8 -*-
"""v15h：网格占位一致性（普通文件夹 / 大文件夹 / 组件 / 跨页）。

这一批的病根只有一条：**"这一格能不能放"有太多个各自为政的判定**。
同一个 2×2 瓷砖，`first_free_cell()` 知道它占 4 格，`first_free_cell_in_page()`
只认 1 格，`find_widget_slot()` 又只认 1 格；普通文件夹明明不是大文件夹，
`big_extra_cells()` 却一律返回 3 个派生格。于是：

  W1 普通文件夹被当成占 4 格 -> 每个文件夹右/下永远空三格（布局有洞）
  W2 大文件夹的派生格不进 `first_free_cell_in_page` -> 新图标塞进瓷砖底下
  W3 `find_widget_slot` 只看主格 -> 组件压在瓷砖右下两格上且永不挤开
  W4 `add_app` 挤走占用者时从 (0,0) 全局扫 -> 图标被扔到第 1 页瓷砖底下
  W5 右键「解散文件夹」不认页 -> 成员全丢到第 1 页（"凭空消失"）
  W6 组件挤位把图标挤到下一页后不收编 -> Ctrl+A 会选中看不见的图标
  W7 成员拖出的红框落点走全局扫描 -> 与实际落点不同源，红框画在屏外
  W8 多选拖动按主格夹紧 -> 2×2 成员的右/下半先飞出网格再回弹

运行：python tests/test_audit_v15h.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src import grid_flow  # noqa: E402
from src.desktop_window import DesktopWindow  # noqa: E402
from src.storage import Storage  # noqa: E402

app = QApplication.instance() or QApplication([])
TMP = Path(tempfile.gettempdir()) / "test_audit_v15h"
TMP.mkdir(exist_ok=True)
W, H = 1920, 1040

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def settle(ms=90):
    end = QTimer()
    end.setSingleShot(True)
    loop = QEventLoop()
    end.timeout.connect(loop.quit)
    end.start(ms)
    loop.exec()


def fresh(tag, layout=None, widgets=None, **settings):
    p = TMP / ("%s.json" % tag)
    if p.exists():
        p.unlink()
    st = Storage(path=str(p))
    st._data["settings"].update({
        "hide_native_icons": True, "takeover_desktop": True,
        "auto_clone_desktop": False, "auto_arrange": False,
        "layout_mode": "grid"})
    st._data["settings"].update(settings)
    st._data["layout"] = list(layout or [])
    st._data["widgets"] = list(widgets or [])
    st.save()
    return st


def folder(fid, col, row, big=False, n=3):
    return {"id": fid, "type": "folder", "big": big, "col": col, "row": row,
            "apps": [{"name": "%s%d" % (fid, i), "target": "%s%d" % (fid, i),
                      "kind": "app"} for i in range(n)]}


def appentry(eid, col, row):
    return {"id": eid, "type": "app", "col": col, "row": row,
            "app": {"name": eid, "target": eid, "kind": "app"}}


def new_window(st):
    w = DesktopWindow(st)
    w.resize(W, H)
    w._update_grid_metrics()
    settle()
    return w


# =========================== W1 普通文件夹不占派生格 ===========================
print("\n--- W1 普通文件夹只占 1 格 ---")
st1 = fresh("w1", layout=[folder("F1", 0, 0)])
check("W1a 普通文件夹没有派生格",
      grid_flow.folder_extra_cells(st1) == set(),
      "extra=%s" % sorted(grid_flow.folder_extra_cells(st1)))
check("W1b first_free_cell 落在右邻格 (1,0)",
      tuple(st1.first_free_cell()) == (1, 0),
      "got=%s" % (tuple(st1.first_free_cell()),))
check("W1c 页内空格同样是 (1,0)",
      tuple(grid_flow.first_free_cell_in_page(st1, 0)) == (1, 0),
      "got=%s" % (tuple(grid_flow.first_free_cell_in_page(st1, 0)),))

# =========================== W2 大文件夹占 4 格，三入口一致 ===========================
print("\n--- W2 大文件夹占满 4 格，所有寻位入口一致 ---")
st2 = fresh("w2", layout=[folder("B1", 0, 0, big=True)])
extra = set(grid_flow.folder_extra_cells(st2))
check("W2a 派生格 = 右/下/右下",
      extra == {(1, 0), (0, 1), (1, 1)}, "extra=%s" % sorted(extra))
check("W2b first_free_cell 跳过全部 4 格 -> (2,0)",
      tuple(st2.first_free_cell()) == (2, 0),
      "got=%s" % (tuple(st2.first_free_cell()),))
check("W2c first_free_cell_in_page 同样跳过 -> (2,0)",
      tuple(grid_flow.first_free_cell_in_page(st2, 0)) == (2, 0),
      "got=%s" % (tuple(grid_flow.first_free_cell_in_page(st2, 0)),))
slot = grid_flow.find_widget_slot(
    st2, {"id": "w1", "type": "clock", "anchored": True,
          "colspan": 2, "rowspan": 2}, page=0)
rect = {(slot[0] + dc, slot[1] + dr) for dc in (0, 1) for dr in (0, 1)}
check("W2d 组件寻位不压瓷砖派生格",
      not (rect & ({(0, 0)} | extra)), "slot=%s" % (slot,))

# =========================== W3 拖文件挤人：留同页 + 避派生格 ===========================
print("\n--- W3 add_app 挤走占用者时留在同页 ---")
st3 = fresh("w3", layout=[folder("B1", 0, 0, big=True),
                          appentry("x", 3, 17)])
win3 = new_window(st3)
rpp = grid_flow.rows_per_page()
page_x = 17 // rpp
st3.add_app({"name": "n", "target": "n", "kind": "app"}, 3, 17)
x = st3.find_entry("x")
check("W3a 被挤的图标仍留在原页",
      grid_flow.page_of(int(x["row"]), rpp) == page_x,
      "row=%d rpp=%d page=%d want=%d"
      % (int(x["row"]), rpp, grid_flow.page_of(int(x["row"]), rpp), page_x))
check("W3b 被挤的图标没有落到瓷砖派生格上",
      (int(x["col"]), int(x["row"])) not in (extra | {(0, 0)}),
      "cell=(%s,%s)" % (x["col"], x["row"]))

# =========================== W4 解散文件夹：成员留原页 ===========================
print("\n--- W4 右键解散文件夹：成员留在文件夹所在页 ---")
f_row = rpp * 1 + 2
st4 = fresh("w4", layout=[folder("F2", 3, f_row, n=3)])
win4 = new_window(st4)
win4._on_dissolve("F2")
members = [e for e in st4.layout if e.get("type") == "app"]
check("W4a 三名成员都还在", len(members) == 3, "n=%d" % len(members))
check("W4b 成员都落在文件夹所在页（page=%d）"
      % grid_flow.page_of(f_row, rpp),
      all(grid_flow.page_of(int(e["row"]), rpp)
          == grid_flow.page_of(f_row, rpp) for e in members),
      "rows=%s" % [int(e["row"]) for e in members])

# =========================== W5 挤位越页 -> 收编 ===========================
print("\n--- W5 组件挤位把图标挤到下一页后必须收编 ---")
st5 = fresh("w5", grid_dims_mode="manual", grid_cols=8)
win5 = new_window(st5)
m5 = grid_flow.current_metrics()
cols_n, rpp5 = m5.cols, m5.rows
# 先把整页铺满（含组件将要占的 4 格），再让组件落定到 (0,0)：
# 页内已无空格，4 个被挤的图标只能顺延到下一页的行
fill = [appentry("i%d_%d" % (r, c), c, r)
        for r in range(rpp5) for c in range(cols_n)]
st5._data["layout"] = fill
st5._data["widgets"] = [{"id": "W1", "type": "clock", "anchored": True,
                         "col": 0, "row": 0, "colspan": 2, "rowspan": 2}]
st5.save()
win5.refresh_grid()
settle(120)
moved = win5._displace_from_widgets()
check("W5a 页满时挤位确实产出了位移", bool(moved), "moved=%d" % len(moved))
if moved:
    win5.storage.save()
    win5._animate_icons_to_layout(moved)
    win5._cull_moved_icons(moved)
    settle(60)
off = [e for e in st5.layout
       if grid_flow.page_of(int(e["row"]), rpp5) != 0]
check("W5b 有图标被挤到下一页（场景成立）", bool(off), "n=%d" % len(off))
bad = [e["id"] for e in off
       if win5.icons.get(e["id"]) is not None
       and not win5.icons[e["id"]].isHidden()]
check("W5c 越页的图标已被收编（不在当前页可见集里）",
      not bad, "still_shown=%s" % bad)
check("W5d 当前页可见集不含越页图标",
      not ({e["id"] for e in off} & win5._selected), "")

# =========================== W6 红框落点与真实落点同源 ===========================
print("\n--- W6 成员拖出的红框落点 == 实际落点 ---")
st6 = fresh("w6")
win6 = new_window(st6)
rpp = grid_flow.rows_per_page()      # 窗口就绪后再算（前一个用例改过网格）
st6._data["layout"] = [folder("F3", 2, rpp + 2, n=2),
                       appentry("busy", 2, rpp + 3)]
st6.save()
win6.refresh_grid()
settle(100)
win6._page = 1
hit = (2, rpp + 3)                   # 已被占用 -> 两个入口都要给出"当前页空格"
preview = win6._extract_landing_cell(hit)
expect = grid_flow.first_free_cell_in_page(st6, 1)
check("W6a 红框落点落在当前页",
      grid_flow.page_of(int(preview[1]), rpp) == 1,
      "preview=%s page=%d" % (preview, grid_flow.page_of(int(preview[1]), rpp)))
check("W6b 红框落点 == 实际落点",
      (int(preview[0]), int(preview[1])) == tuple(expect),
      "preview=%s expect=%s" % (preview, expect))

# =========================== W7 多选夹紧按 footprint ===========================
print("\n--- W7 多选拖动：2×2 成员按真实占位夹紧 ---")
st7 = fresh("w7", layout=[folder("B2", 0, 0, big=True)])
win7 = new_window(st7)
win7._selected = {"B2"}             # 只选瓷砖：bbox 必须含它的右/下两格
win7._capture_multi_origin()
box = getattr(win7, "_multi_box", None)
check("W7a 多选盒 = 瓷砖真实占位 (0,1,0,1)",
      box == (0, 1, 0, 1), "box=%s" % (box,))

# =========================== 汇总 ===========================
print("\n%d/%d 通过" % (total[0] - len(failures), total[0]))
if failures:
    print("FAILED: %s" % ", ".join(failures))
sys.stdout.flush()
os._exit(0 if not failures else 1)
