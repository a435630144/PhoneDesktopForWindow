# -*- coding: utf-8 -*-
"""v15c：大文件夹（2×2 瓷砖）的落点边界与占格冲突。

审核报告 P2-3 的口径是「大文件夹拖到页底会跨页」，实测比这更宽：

  * 拖到**最后一列**：`big_extra_cells()` 贴边直接返回 None，右列与下行
    三格**根本不登记占用**——瓷砖压着别人，别人也压着瓷砖；
  * 拖到**最后一页的最后一行**：派生格落到下一页（`page = row // rpp`），
    2×2 被页边界切成两半；
  * 拖到**别人头上**：`_free_moves()` 只看主格，2×2 里其余三格上的图标
    原地不动，落位后直接叠在瓷砖底下。

判据（与多选 `multi_move` 同一套页边界语义）：

  B1–B6  `clamp_big_cell` 纯函数：右/下/上（第 2 页）/双轴同时夹紧，
         非大文件夹原样返回，列数不足 1 列时不崩
  B7–B8  窗口 `_free_moves`：目标格被夹到网格内、且整块留在落点所在页
  B9     2×2 内被压住的图标被挪出块外（不叠、不丢）
  B10    落位后布局不变量：格子唯一 + 块内 4 格都在同一页
  B11    `_drag_cell`（预览/红框/松手共用）同样夹紧——预览与提交不漂移

运行：python tests/test_big_folder_bounds.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src import grid_flow  # noqa: E402
from src.desktop_window import DesktopWindow  # noqa: E402
from src.storage import Storage  # noqa: E402

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def _entries():
    return [
        {"id": "f", "type": "folder", "name": "大文件夹", "big": True,
         "col": 0, "row": 0,
         "apps": [{"name": "m%d" % i, "kind": "app", "target": "m%d" % i}
                  for i in range(12)]},
        {"id": "x", "type": "app", "col": 3, "row": 2,
         "app": {"name": "X", "kind": "app", "path": "C:/x/x.exe"}},
        {"id": "y", "type": "app", "col": 4, "row": 5,
         "app": {"name": "Y", "kind": "app", "path": "C:/x/y.exe"}},
    ]


def _machine(tmp, name="big"):
    st = Storage(path=str(tmp / (name + ".json")))
    st._data["settings"]["auto_arrange"] = False
    st._data["layout"] = _entries()
    st.save()
    return st


def _apply(st, moves):
    by = {e["id"]: e for e in st.layout}
    for eid, (c, r) in moves.items():
        by[eid]["col"], by[eid]["row"] = c, r
    return by


def _block_cells(entry):
    c, r = int(entry["col"]), int(entry["row"])
    return {(c, r), (c + 1, r), (c, r + 1), (c + 1, r + 1)}


def main(app, tmp):
    st = _machine(tmp)
    grid_flow.set_grid(st, (1600, 1000))
    cols = int(st.columns)
    rpp = int(grid_flow.rows_per_page())

    # ---------- B1–B6 纯函数 ----------
    check("B1 右边界：col 夹到 cols-2",
          grid_flow.clamp_big_cell((cols - 1, 2), cols, (0, rpp - 1)) == (cols - 2, 2),
          str(grid_flow.clamp_big_cell((cols - 1, 2), cols, (0, rpp - 1))))
    check("B2 页底：row 夹到页底-1",
          grid_flow.clamp_big_cell((2, rpp - 1), cols, (0, rpp - 1)) == (2, rpp - 2),
          str(grid_flow.clamp_big_cell((2, rpp - 1), cols, (0, rpp - 1))))
    check("B3 第 2 页页底：仍留在第 2 页",
          grid_flow.clamp_big_cell((2, 2 * rpp - 1), cols, (rpp, 2 * rpp - 1))
          == (2, 2 * rpp - 2),
          str(grid_flow.clamp_big_cell((2, 2 * rpp - 1), cols, (rpp, 2 * rpp - 1))))
    check("B4 双轴同时夹紧",
          grid_flow.clamp_big_cell((cols - 1, rpp - 1), cols, (0, rpp - 1))
          == (cols - 2, rpp - 2))
    check("B5 页顶：已在页内不动",
          grid_flow.clamp_big_cell((2, rpp), cols, (rpp, 2 * rpp - 1)) == (2, rpp))
    check("B6 只有 1 列时不崩、夹到 0",
          grid_flow.clamp_big_cell((5, 0), 1, (0, rpp - 1))[0] == 0,
          str(grid_flow.clamp_big_cell((5, 0), 1, (0, rpp - 1))))

    win = DesktopWindow(st)
    win.show()
    app.processEvents()

    # ---------- B7 右边界 ----------
    moves = win._free_moves("f", (cols - 1, 2))
    check("B7 拖到最后一列：夹到 cols-2", moves.get("f") == (cols - 2, 2), str(moves))

    # ---------- B8 页底 ----------
    moves = win._free_moves("f", (2, rpp - 1))
    got = moves.get("f")
    check("B8 拖到页底：整块留在本页",
          got == (2, rpp - 2) and got[1] + 1 <= rpp - 1, "%s (rpp=%d)" % (got, rpp))

    # ---------- B9 块内被压住的图标要让开 ----------
    st2 = _machine(tmp, "big2")
    grid_flow.set_grid(st2, (1600, 1000))
    win2 = DesktopWindow(st2)
    win2.show()
    app.processEvents()
    moves = win2._free_moves("f", (2, 2))     # x 正在 (3, 2)，落在 2×2 内
    block = {(2, 2), (3, 2), (2, 3), (3, 3)}
    check("B9 块内图标被移出（不叠在瓷砖下）",
          "x" in moves and moves["x"] not in block, str(moves))
    by = _apply(st2, moves)
    check("B9b 被挪走的图标不丢、落到空格",
          by["x"]["col"] is not None
          and all((int(e["col"]), int(e["row"])) != (by["x"]["col"], by["x"]["row"])
                  for k, e in by.items() if k != "x"),
          str((by["x"]["col"], by["x"]["row"])))

    # ---------- B10 不变量 ----------
    cells = [(int(e["col"]), int(e["row"])) for e in st2.layout]
    check("B10 落位后格子唯一", len(cells) == len(set(cells)), str(cells))
    fcell = (int(by["f"]["col"]), int(by["f"]["row"]))
    block = _block_cells(by["f"])
    pages = {grid_flow.page_of(r) for _c, r in block}
    check("B10b 2×2 四格同页", len(pages) == 1, str(block))
    check("B10c 2×2 四格都在网格内",
          all(0 <= c < cols and r >= 0 for c, r in block), str(block))
    check("B10d 主格没被自己挤走", fcell == (2, 2), str(fcell))

    # ---------- B11 预览/落点共用同一夹紧 ----------
    m = grid_flow.current_metrics()
    pt = QPoint(m.ox + (cols - 1) * m.cw + m.cw // 2,
                m.oy + 2 * m.ch + m.ch // 2)
    win._drag_entry_id = "f"
    cell = win._drag_cell(pt)
    check("B11 _drag_cell 夹紧（预览红框与提交同源）",
          cell == (cols - 2, 2), str(cell))

    # ---------- B12–B14 多选整块里含大文件夹 ----------
    # 旧实现把大文件夹当 1 格平移：整块贴右/贴页底时 2×2 出界，且块内
    # 派生格上的非选中图标不参与让位（落位后叠在瓷砖底下）。
    def _multi(entries, target, sel):
        return grid_flow.multi_move(entries, cols, "x", target, sel,
                                    page_rows=(0, rpp - 1))

    def _layout_ok(entries, moves):
        by = {e["id"]: e for e in entries}
        for eid, cell in (moves or {}).items():
            by[eid]["col"], by[eid]["row"] = cell
        cells = []
        for e in by.values():
            cells.extend(_block_cells(e) if e.get("big")
                         else [(int(e["col"]), int(e["row"]))])
        return (len(cells) == len(set(cells)), by)

    base = _entries()
    moves = _multi([dict(e) for e in base], (3, 4), {"x", "f"})
    ok, by = _layout_ok([dict(e) for e in base], moves)
    check("B12 多选含大文件夹：无重叠", ok, str(moves))
    check("B12b 瓷砖整块留在本页",
          all(0 <= r <= rpp - 1 for _c, r in _block_cells(by["f"])),
          str(_block_cells(by["f"])))

    moves = _multi([dict(e) for e in base], (cols - 1, 2), {"x", "f"})
    ok, by = _layout_ok([dict(e) for e in base], moves)
    check("B13 贴右：瓷砖夹到 cols-2",
          int(by["f"]["col"]) <= cols - 2 and ok,
          "f.col=%s ok=%s" % (by["f"]["col"], ok))

    # y 放在瓷砖要落的 2×2 里，看它是否被让开
    dirty = _entries()
    dirty[2]["col"], dirty[2]["row"] = 3, 3      # 在瓷砖将落的 2×2 内
    moves = _multi([dict(e) for e in dirty], (5, 4), {"x", "f"})
    ok, by = _layout_ok([dict(e) for e in dirty], moves)
    check("B14 块内非选中图标被让开", ok and "y" in (moves or {}), str(moves))

    # ---------- C1–C4 布局整理路径（compact / tidy / pack） ----------
    # 这些入口共用"占格"概念，旧实现里只有 pack/tidy 认 2×2 的右边，
    # 且派生格不与 used 比对——重新扫描（compact）会把新文件补进瓷砖底下。
    def _compact_case(name, entries):
        st_c = Storage(path=str(tmp / (name + ".json")))
        st_c._data["settings"]["auto_arrange"] = False
        st_c._data["layout"] = entries
        st_c.save()
        grid_flow.set_grid(st_c, (1600, 1000))
        st_c.compact_layout()
        return st_c

    c1 = _compact_case("c1", [
        {"id": "f", "type": "folder", "big": True, "col": 0, "row": 0,
         "apps": [{"name": "m", "kind": "app", "target": "m"}]},
        {"id": "p", "type": "app", "col": 1, "row": 1,
         "app": {"name": "P", "kind": "app", "path": "C:/x/p.exe"}},
    ])
    fc = [e for e in c1.layout if e["id"] == "f"][0]
    pc = [e for e in c1.layout if e["id"] == "p"][0]
    check("C1 compact：补位图标不进 2×2",
          (int(pc["col"]), int(pc["row"])) not in _block_cells(fc), str(pc))

    c2 = _compact_case("c2", [
        {"id": "f", "type": "folder", "big": True, "col": cols - 1, "row": 0,
         "apps": [{"name": "m", "kind": "app", "target": "m"}]},
    ])
    fc2 = [e for e in c2.layout if e["id"] == "f"][0]
    check("C2 compact：贴右列被夹到 cols-2",
          int(fc2["col"]) <= cols - 2, "col=%s" % fc2["col"])

    c3 = _compact_case("c3", [
        {"id": "a", "type": "app", "col": 0, "row": 0,
         "app": {"name": "A", "kind": "app", "path": "C:/x/a.exe"}},
        {"id": "f", "type": "folder", "big": True, "col": 1, "row": 0,
         "apps": [{"name": "m", "kind": "app", "target": "m"}]},
        {"id": "b", "type": "app", "col": 2, "row": 0,
         "app": {"name": "B", "kind": "app", "path": "C:/x/b.exe"}},
    ])
    cells_c3 = []
    for e in c3.layout:
        cells_c3.extend(_block_cells(e) if e.get("big")
                        else [(int(e["col"]), int(e["row"]))])
    check("C3 compact：整份布局无重叠", len(cells_c3) == len(set(cells_c3)),
          str(cells_c3))

    st_t = Storage(path=str(tmp / "tidy.json"))
    st_t._data["settings"]["auto_arrange"] = True
    st_t._data["layout"] = [
        {"id": "a", "type": "app", "col": 0, "row": 0,
         "app": {"name": "A", "kind": "app", "path": "C:/x/a.exe"}},
        {"id": "f", "type": "folder", "big": True, "col": 0, "row": 5,
         "apps": [{"name": "m", "kind": "app", "target": "m"}]},
        {"id": "b", "type": "app", "col": 3, "row": 0,
         "app": {"name": "B", "kind": "app", "path": "C:/x/b.exe"}},
    ]
    st_t.save()
    grid_flow.set_grid(st_t, (1600, 1000))
    st_t.tidy_layout()
    blocks = {grid_flow.page_of(r) for _c, r in
              _block_cells([e for e in st_t.layout if e["id"] == "f"][0])}
    check("C4 tidy：瓷砖不跨页", len(blocks) == 1, str(blocks))
    cells_t = []
    for e in st_t.layout:
        cells_t.extend(_block_cells(e) if e.get("big")
                       else [(int(e["col"]), int(e["row"]))])
    check("C4b tidy：整份布局无重叠", len(cells_t) == len(set(cells_t)),
          str(cells_t))

    win.close()
    win2.close()


def run():
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="bigbounds_"))
    main(app, tmp)
    print("\n%d checks, %d failed" % (total[0], len(failures)))
    if failures:
        print("FAILED:", ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
