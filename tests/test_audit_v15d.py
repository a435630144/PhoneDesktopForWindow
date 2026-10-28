# -*- coding: utf-8 -*-
"""v15d：功能深度审核批次的回归判据（2026-10-29）。

每条对应一个**已复现**的用户可感知缺陷，修法见 DEVLOG / 交接文档：

  A1  批量合并「应用 + 文件夹」：应用本身不能丢（旧实现先清 app 再取成员）
  A2  批量合并「应用 + 应用」：两个成员都在（守旧行为）
  A3  `sync_free_pages`：网格跨页移动后无界页号跟随
  A4  `sync_free_pages`：无界模式不反向覆盖（fpage 才是权威）
  A5  框选只选当前页可见图标（隐藏页的图标位置可能与当前页重叠）
  A6  翻页后丢掉落单页之外的选择；批量移页后选择跟着走
  A7  `assign_page_cells`：大文件夹拿满 2×2 整块，普通图标不落进瓷砖
  A8  `assign_page_cells`：页内放不下 -> leftovers，不静默叠格
  A9  `_free_moves`：2×2 整块不能压组件
  A10 `_free_moves`：普通图标不能把 2×2 换进 1 格
  A11 `_free_moves`：2×2 换到放不下的位置 -> 拒绝（不压邻居）
  A12 放大文件夹：贴最后一列自动内移，瓷砖不出屏
  A13 放大文件夹：压住的邻居被挤到空格，不重叠
  A14 自动排列：大文件夹是锚点，密排绕开 2×2（不压瓷砖）
  A15 粘贴部分失败：成功的那部分也要刷新桌面
  A16 编辑态格位反算用卡片原点（不是窗口原点，差一个 pad）
  A17 删除内置组件后重启不复活（墓碑）
  A18 `show_widgets=关` 时新 spawn 的组件必须隐藏
  A19 Dock 满员插入返回 False，不挤掉末尾那项
  A20 恢复默认设置保留 Dock 条目 / 天气城市等用户内容
  A21 文件夹弹窗刷新留在原地（不跳到屏幕中央）
  A22 文件夹已被解散时，弹窗的 ＋ 不能再吞桌面图标
  A23 「移出文件夹」优先落回当前页
  A24 125% 缩放下文件夹标题不与右上角按钮重叠
  A25 数值设置 0 是合法值（阴影/填充/边框调到 0 不再自愈）
  A26 大文件夹「移动到当前页」不误报空间不足
  A27 组件总显隐按页归属（不把其它页组件亮出来）

运行：python tests/test_audit_v15d.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QMimeData, QPoint, QRect, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src import appsource, dock, grid_flow, tray, widget_geom  # noqa: E402
from src.desktop_window import DesktopWindow  # noqa: E402
from src.folder_popup import FolderPopup  # noqa: E402
from src.storage import Storage  # noqa: E402
from src.widget_layer import WidgetLayer  # noqa: E402

app = QApplication.instance() or QApplication([])
W, H = 1920, 1040
TMP = Path(tempfile.gettempdir())
STATE = TMP / "test_audit_v15d_state.json"

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def fresh_state(layout, widgets=None, settings=None, name="a"):
    p = TMP / ("test_audit_v15d_%s.json" % name)
    if p.exists():
        p.unlink()
    st = Storage(path=str(p))
    st._data["settings"].update({
        "hide_native_icons": True, "takeover_desktop": True,
        "auto_clone_desktop": False, "layout_mode": "grid",
        "auto_arrange": False})
    for k, v in (settings or {}).items():
        st._data["settings"][k] = v
    st._data["layout"] = layout
    st._data["widgets"] = widgets if widgets is not None else []
    grid_flow.set_grid(st, (W, H))
    grid_flow.set_page(0)
    st.save()
    return st, p


def win_for(layout, widgets=None, settings=None, name="w"):
    st, p = fresh_state(layout, widgets, settings, name)
    win = DesktopWindow(st)
    win.resize(W, H)
    return st, win, p


def ae(i, c, r):
    return {"id": i, "type": "app", "col": c, "row": r,
            "app": {"name": i, "path": "C:/Windows/notepad.exe"}}


def folder(i, c, r, n=3, big=False):
    return {"id": i, "type": "folder", "col": c, "row": r, "app": {},
            "name": i, "big": big,
            "apps": [{"name": "%s-m%d" % (i, k), "path": "C:/x/%d.lnk" % k}
                     for k in range(n)]}


def overlaps(entries):
    """返回占用格重复的 [(格, id1, id2)]（大文件夹按 4 格算）。"""
    used, out = {}, []
    for e in entries:
        cells = (grid_flow.big_block_cells((e["col"], e["row"]))
                 if grid_flow.is_big_folder(None, e) else [(e["col"], e["row"])])
        for c in cells:
            if c in used:
                out.append((c, used[c], e.get("id")))
            used[c] = e.get("id")
    return out


# --------------------------- A1 / A2 批量合并 ---------------------------
st, win, _p = win_for([
    {"id": "a", "type": "app", "col": 0, "row": 0,
     "app": {"name": "A", "path": "C:/x/a.exe"}},
    folder("f", 1, 0, n=2),
], name="merge")
win._selected = {"a", "f"}
win._batch_merge()
f = st.find_entry("a")
names = [m.get("name") for m in (f or {}).get("apps", [])]
check("A1 应用+文件夹合并保留应用本体", f is not None and "A" in names, names)
check("A1b 三个成员都在", len(names) == 3, names)

st, win, _p = win_for([
    {"id": "a", "type": "app", "col": 0, "row": 0,
     "app": {"name": "A", "path": "C:/x/a.exe"}},
    {"id": "b", "type": "app", "col": 1, "row": 0,
     "app": {"name": "B", "path": "C:/x/b.exe"}},
], name="merge2")
win._selected = {"a", "b"}
win._batch_merge()
f = st.find_entry("a")
names = sorted(m.get("name") for m in (f or {}).get("apps", []))
check("A2 应用+应用合并仍是两个成员", names == ["A", "B"], names)

# --------------------------- A3 / A4 fpage ---------------------------
st, win, _p = win_for([ae("x", 0, 0)], name="fpage")
win.set_layout_mode("free")
win.set_layout_mode("grid")
e = st.find_entry("x")
e["col"], e["row"] = 0, grid_flow.rows_per_page()
st.save()
st.sync_free_pages({"x"})
check("A3 网格跨页后 fpage 跟随", int(st.find_entry("x")["fpage"]) == 1,
      st.find_entry("x").get("fpage"))

st.set_setting("layout_mode", "free")
before = int(st.find_entry("x").get("fpage"))
st.sync_free_pages({"x"})
check("A4 无界模式 sync 不改 fpage", int(st.find_entry("x").get("fpage")) == before)
st.set_setting("layout_mode", "grid")

# --------------------------- A5 / A6 选择 ---------------------------
rpp = grid_flow.rows_per_page()
st, win, _p = win_for([ae("p0", 0, 0), ae("p1", 0, rpp)], name="sel")
win.show()
app.processEvents()
win._select_in_rect(QRect(0, 0, W, H))
check("A5 框选不选隐藏页图标", win._selected == {"p0"}, sorted(win._selected))

st, win, _p = win_for([ae("q0", 0, 0), ae("q1", 0, rpp)], name="sel2")
win.show()
app.processEvents()
win._selected = {"q0"}
win.goto_page(1, animate=False)
check("A6 翻页丢掉落单页的选择", win._selected == set(), sorted(win._selected))

st, win, _p = win_for([ae("m0", 0, 0), ae("m1", 0, 1)], name="sel3")
win.show()
app.processEvents()
win._selected = {"m0", "m1"}
win._batch_move_to_page(1)
check("A6b 批量移页后选择跟着走", win._selected == {"m0", "m1"},
      sorted(win._selected))

# --------------------------- A7 / A8 移到分页 ---------------------------
cols = st.columns
st, p = fresh_state([folder("F", 0, 0, big=True), ae("v", 1, 0)],
                    name="pagecell")
rpp = grid_flow.rows_per_page()
st.add_app({"name": "victim", "path": "C:/x/v.lnk"}, 1, rpp)
st.save()
slots, left = grid_flow.assign_page_cells(
    st.layout, ["F"], 1, cols, rpp, extra_occupied=grid_flow.widget_cells(st))
cell = slots.get("F")
blk = set(grid_flow.big_block_cells(cell)) if cell else set()
victim_cell = next(((e["col"], e["row"]) for e in st.layout
                    if e.get("app", {}).get("name") == "victim"), None)
check("A7 大文件夹拿满 2×2 且不压人",
      cell is not None and len(blk) == 4 and victim_cell not in blk,
      "%r block=%r victim=%r" % (cell, sorted(blk), victim_cell))

# 目标页只剩 1 个空格：2×2 放不下 -> leftovers
st, p = fresh_state([folder("BIG", 0, rpp, big=True)] +
                    [ae("a%d" % i, i % cols, i // cols)
                     for i in range(cols * rpp - 1)], name="pagecell2")
slots2, left2 = grid_flow.assign_page_cells(
    st.layout, ["BIG"], 0, cols, rpp, extra_occupied=grid_flow.widget_cells(st))
check("A8 页内放不下 2×2 -> leftovers",
      slots2 == {} and left2 == ["BIG"], (slots2, left2))

# --------------------------- A9-A11 _free_moves ---------------------------
widgets = [{"id": "w1", "type": "clock", "anchored": True, "col": 1, "row": 0,
            "colspan": 1, "rowspan": 1, "x": 0, "y": 0}]
st, win, _p = win_for([folder("F", 2, 0, big=True)], widgets, name="fm1")
check("A9 2×2 整块不能压组件", win._free_moves("F", (0, 0)) == {})

st, win, _p = win_for([folder("F", 0, 0, big=True), ae("A", 2, 0)], name="fm2")
check("A10 普通图标不能把 2×2 换进 1 格", win._free_moves("A", (0, 0)) == {})

st, win, _p = win_for([folder("F", 0, 0, big=True), ae("A", 0, 2),
                       ae("C", 1, 2)], name="fm3")
moves = win._free_moves("A", (0, 0))
check("A11 2×2 换到放不下的位置被拒绝", moves == {}, moves)

# --------------------------- A12 / A13 放大 ---------------------------
st, win, _p = win_for([folder("F", cols - 1, 0, n=4)], name="big1")
win._on_folder_big_toggle("F", True)
icon = win.icons["F"]
g = icon.geometry()
check("A12 贴最后一列放大不出屏", g.x() + g.width() <= W,
      "x=%d w=%d W=%d" % (g.x(), g.width(), W))
check("A12b 放大后仍占完整 2×2",
      grid_flow.is_big_folder(st, st.find_entry("F"))
      and st.find_entry("F")["col"] <= cols - 2, st.find_entry("F").get("col"))

st, win, _p = win_for([folder("F", 0, 0, n=4), ae("N", 1, 0), ae("M", 0, 1)],
                      name="big2")
win._on_folder_big_toggle("F", True)
check("A13 放大后不压邻居", overlaps(st.layout) == [], overlaps(st.layout))
check("A13b 邻居还在布局里", {e["id"] for e in st.layout} == {"F", "N", "M"})

# --------------------------- A14 自动排列锚定 ---------------------------
st, win, _p = win_for([folder("F", 0, 0, big=True), ae("A", 2, 0),
                       ae("C", 0, 2), ae("D", 1, 2)],
                      settings={"auto_arrange": True}, name="auto")
moves = win._arrange_moves("A", (0, 0)) or {}
by = {e["id"]: e for e in st.layout}
for eid, (c, r) in moves.items():
    by[eid]["col"], by[eid]["row"] = c, r
check("A14 自动排列不把图标排进瓷砖", overlaps(st.layout) == [],
      "%r %r" % (overlaps(st.layout), moves))
check("A14b 瓷砖没被密排挪走",
      (by["F"]["col"], by["F"]["row"]) == (0, 0))

# --------------------------- A15 部分粘贴 ---------------------------
desk = TMP / "audit_v15d_desk"
desk.mkdir(exist_ok=True)
src_ok = TMP / "audit_v15d_src.txt"
src_ok.write_text("x", encoding="utf-8")
st, win, _p = win_for([ae("z", 0, 0)], name="paste")
calls = {"rescan": 0, "notify": []}
old_folders, old_notify = appsource._desktop_folders, tray.notify_via_tray
appsource._desktop_folders = lambda: [desk]
win.rescan_desktop = lambda *a, **k: calls.__setitem__("rescan", calls["rescan"] + 1)
tray.notify_via_tray = lambda title, body="": calls["notify"].append(title)
md = QMimeData()
md.setUrls([QUrl.fromLocalFile(str(src_ok)),
            QUrl.fromLocalFile(str(TMP / "audit_v15d_missing.txt"))])
QApplication.clipboard().setMimeData(md)
win._paste_files()
appsource._desktop_folders, tray.notify_via_tray = old_folders, old_notify
check("A15 部分粘贴成功也刷新桌面", calls["rescan"] >= 1, calls)
check("A15b 仍然提示部分成功", calls["notify"] == ["部分粘贴成功"], calls["notify"])

# --------------------------- A16 编辑态格位 ---------------------------
st, p = fresh_state([], widgets=[{"id": "clock", "type": "clock",
                                 "anchored": True, "col": 2, "row": 1,
                                 "colspan": 1, "rowspan": 1}], name="card")
from src.widgets.clock import ClockWidget  # noqa: E402
cw = ClockWidget(st, st.widgets[0])
cw._edit_mode = True
cw.move(400, 300)
pad = widget_geom.pad(st)
ox, oy = cw._card_origin()
check("A16 编辑态用卡片原点反算", (ox, oy) == (400 + pad, 300 + pad),
      "origin=%r pad=%r" % ((ox, oy), pad))
cw.close()

# --------------------------- A17 / A18 组件生命周期 ---------------------------
st, p = fresh_state([], widgets=[
    {"id": "clock", "type": "clock", "anchored": True, "col": 0, "row": 0,
     "colspan": 1, "rowspan": 1, "hidden": False},
], name="life")
layer = WidgetLayer(st)
check("A17 组件已 spawn", "clock" in layer.widgets)
layer.remove("clock")
st.save()
st2 = Storage(path=str(p))
check("A17b 删除内置组件重启不复活",
      all(w.get("id") != "clock" for w in st2.widgets),
      [w.get("id") for w in st2.widgets])
check("A17c 墓碑已落盘", "clock" in (st2.get_setting("widgets_removed") or []))
layer.deleteLater()

st, p = fresh_state([], widgets=[
    {"id": "clock", "type": "clock", "anchored": True, "col": 0, "row": 0,
     "colspan": 1, "rowspan": 1, "hidden": False},
], settings={"show_widgets": False}, name="hidden")
layer2 = WidgetLayer(st)
check("A18 总开关关闭时组件不显示",
      layer2.widgets.get("clock") is None
      or layer2.widgets["clock"].isHidden(),
      "widgets=%r" % list(layer2.widgets))
layer2.deleteLater()

# --------------------------- A19 Dock 满员 ---------------------------
items = [{"name": "app%d" % i, "target": "C:/x/%d.exe" % i} for i in range(24)]
st, p = fresh_state([], name="dock", settings={"dock_items": items})
bar = dock.DockWindow(st)
ok = bar.add_item({"name": "new", "target": "C:/x/new.exe"}, index=0)
check("A19 满员插入返回 False", ok is False)
check("A19b 原 24 项一个不少", len(bar._items) == 24, len(bar._items))
check("A19c 末尾那项没被挤掉",
      bar._items[-1].get("target") == "C:/x/23.exe",
      bar._items[-1].get("target"))
bar.deleteLater()

# --------------------------- A20 恢复默认保留内容 ---------------------------
st, p = fresh_state([], name="reset", settings={
    "dock_items": [{"name": "keep", "target": "C:/x/keep.exe"}],
    "dock_seeded": True, "weather_city": "杭州",
    "label_font_scale": 120})
st.reset_settings()
check("A20 重置保留 Dock 条目",
      st.get_setting("dock_items")[0]["name"] == "keep")
check("A20b 重置保留天气城市", st.get_setting("weather_city") == "杭州")
check("A20c 普通配置项照常回默认", st.get_setting("label_font_scale") == 100,
      st.get_setting("label_font_scale"))

# --------------------------- A21 / A22 弹窗 ---------------------------
folder_entry = folder("F", 0, 0, n=3)
st, p = fresh_state([folder_entry], name="popup")
grid_flow.set_page(0)
pop = FolderPopup(st, st.find_entry("F"), anchor_rect=None)
pop.move(240, 200)
pop.entry["apps"].append({"name": "extra", "path": "C:/x/e.lnk"})
st.save()
pop.refresh()
app.processEvents()
check("A21 刷新后弹窗不跳位置", (pop.x(), pop.y()) == (240, 200),
      (pop.x(), pop.y()))

st, p = fresh_state([folder("G", 0, 0, n=2), ae("deskicon", 1, 0)], name="ghost")
pop2 = FolderPopup(st, st.find_entry("G"), anchor_rect=None)
st.remove_entry("G")
st.save()
ok = pop2._add_member_by_id("deskicon")
check("A22 幽灵文件夹不能吞桌面图标", ok is False)
check("A22b 桌面图标还在布局里", st.find_entry("deskicon") is not None)
pop2.close()

# --------------------------- A23 移出落当前页 ---------------------------
rpp = grid_flow.rows_per_page()
st, win, _p = win_for([folder("H", 0, rpp, n=2)], name="remove")
win.show()
app.processEvents()
win.goto_page(1, animate=False)
win._on_remove_member("H:0")
moved = next((e for e in st.layout if e.get("app", {}).get("name") == "H-m0"), None)
check("A23 移出的成员落在当前页",
      moved is not None and grid_flow.page_of(int(moved["row"])) == 1,
      None if moved is None else (moved["col"], moved["row"]))

# --------------------------- A24 标题不与按钮重叠 ---------------------------
st, p = fresh_state([folder("T", 0, 0, n=3)], settings={"icon_scale": 1.25},
                    name="title")
pop3 = FolderPopup(st, st.find_entry("T"), anchor_rect=None)
title = pop3._title_rect()
style_btn = pop3._header_rects()["style"]
check("A24 125% 标题右边界在按钮左侧",
      title.right() < style_btn.left(),
      "title=%r btn=%r" % (title, style_btn))
pop3.close()

# --------------------------- A25 数值设置 0 值 ---------------------------
class _FakeStore:
    def __init__(self, val):
        self.val = val

    def get_setting(self, key):
        return self.val


from src.settings_dialog import _num_setting  # noqa: E402
check("A25 滑条 0 是合法值", _num_setting(_FakeStore(0), "x", 75) == 0)
check("A25b 缺失值才回默认", _num_setting(_FakeStore(None), "x", 75) == 75)
check("A25c 坏值回默认", _num_setting(_FakeStore("oops"), "x", 75) == 75)

# --------------------------- A26 移动到当前页（大文件夹） ---------------------------
st, win, _p = win_for([folder("SELF", 0, 0, big=True), ae("n", 4, 0)],
                      name="samepage")
win.show()
app.processEvents()
win._selected = {"SELF"}
win._batch_move_to_page(0)
e = st.find_entry("SELF")
check("A26 大文件夹移到当前页不误报空间不足",
      e is not None and (e["col"], e["row"]) == (0, 0),
      None if e is None else (e["col"], e["row"]))

# --------------------------- A27 组件总显隐按页 ---------------------------
rpp = grid_flow.rows_per_page()
st, p = fresh_state([], widgets=[
    {"id": "c0", "type": "clock", "anchored": True, "col": 0, "row": 0,
     "colspan": 1, "rowspan": 1, "hidden": False},
    {"id": "c1", "type": "clock", "anchored": True, "col": 0, "row": rpp,
     "colspan": 1, "rowspan": 1, "hidden": False},
], name="showpage")
grid_flow.set_page(0)
layer3 = WidgetLayer(st)
layer3.set_visible(True)
check("A27 总显隐不把其它页组件亮出来",
      layer3.widgets["c0"].isVisible() and not layer3.widgets["c1"].isVisible(),
      "c0=%r c1=%r" % (layer3.widgets["c0"].isVisible(),
                       layer3.widgets["c1"].isVisible()))
layer3.deleteLater()

print("-" * 56)
print("%d/%d checks, %d FAIL" % (total[0] - len(failures), total[0], len(failures)))
if failures:
    print("FAILED:", ", ".join(failures))
sys.stdout.flush()
# Qt 顶层窗（组件/弹窗/Dock）在解释器退出阶段析构顺序不确定，实测偶发
# 段错误（判据看退出码，会变成假失败）。用例已全部跑完，直接硬退出。
os._exit(1 if failures else 0)
