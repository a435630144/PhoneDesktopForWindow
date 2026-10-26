# -*- coding: utf-8 -*-
"""v14z / 8.3.3：多选拖动时整块与指针**逐像素**同步。

用户反馈（2026-10-27）：框选多个图标后拖动，"只有当前 hover 的图标跟随
鼠标瞬间移动，其余框选的图标也会移动，但是动画之后，不连贯"。

根因：被拖的那个图标由浮动拖影 `_DragGhost` 跟手（1:1），其余成员走的是
"按格预览 + 280ms 弹簧"（`animate_to`）。dragMove 的频率远高于 280ms，
弹簧每次刚起播就被下一格的目标打断 —— 成员永远在追赶，块看起来是散的。

修法：拖动期间成员位置 = **按下瞬间位置 + 指针位移**（与拖影同一个 gpos
序列），并按与布局同一份页边界夹紧；单元测试直接验像素等式与夹紧。

  F1 记录基准：_multi_origin 覆盖全部选中成员、bbox 正确
  F2 drag_delta：拖动前 None，拖动中 = 当前位置 - 按下位置
  F3 成员位移 = 指针位移（逐像素，不是按格）
  F4 主条目（拖影态）自身位置不动
  F5 右/下越界夹紧：整块停在页内（与 multi_move 的页边界同源）
  F6 同一格内移动也跟手（不依赖布局变化，即不依赖 dragMove 重算）
  F7 预览落地时不给成员叠让位弹簧（不出现两套位置源互相拉扯）
  F8 翻页后自动退回按格预览（像素跟随与页偏移不打架）

运行：python tests/test_multi_follow.py   （offscreen）
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


def _has_pos_anim(icon):
    return any(a.propertyName() == b"pos" for a in icon._anims)


def main(app, tmp):
    st = Storage(path=str(tmp / "follow.json"))
    st._data["settings"]["columns"] = 8
    st._data["settings"]["auto_arrange"] = False
    st._data["layout"] = [
        {"id": "a", "type": "app", "col": 1, "row": 1,
         "app": {"name": "A", "kind": "app", "path": "C:/x/a.exe"}},
        {"id": "b", "type": "app", "col": 2, "row": 1,
         "app": {"name": "B", "kind": "app", "path": "C:/x/b.exe"}},
        {"id": "c", "type": "app", "col": 1, "row": 2,
         "app": {"name": "C", "kind": "app", "path": "C:/x/c.exe"}},
        {"id": "d", "type": "app", "col": 2, "row": 2,
         "app": {"name": "D", "kind": "app", "path": "C:/x/d.exe"}},
        {"id": "z", "type": "app", "col": 6, "row": 0,
         "app": {"name": "Z", "kind": "app", "path": "C:/x/z.exe"}},
    ]
    st.save()
    grid_flow.set_grid(st, (1600, 1000))
    win = DesktopWindow(st)
    win.show()                    # 离屏也要 show：子部件的 isVisible() 依赖祖先可见
    app.processEvents()

    ids = ["a", "b", "c", "d"]
    win._selected = set(ids)
    win._drag_entry_id = "b"
    win._drag_active = True
    win._drag_multi = True
    win._capture_multi_origin()

    # ---------- F1 基准 ----------
    check("F1 基准覆盖全部选中成员",
          set(win._multi_origin) == set(ids), str(sorted(win._multi_origin)))
    check("F1b bbox = (1,2,1,2)", win._multi_box == (1, 2, 1, 2),
          str(win._multi_box))

    # ---------- F2 drag_delta ----------
    prim = win.icons["b"]
    check("F2 未开始拖动 -> None", prim.drag_delta() is None,
          str(prim.drag_delta()))
    prim._drag_loop = True
    prim._press_global = QPoint(800, 600)
    prim._last_gpos = QPoint(800, 600)
    check("F2b 刚按下 -> (0,0)", prim.drag_delta() == QPoint(0, 0),
          str(prim.drag_delta()))

    # ---------- F3 逐像素跟随 ----------
    origin = {k: QPoint(v) for k, v in win._multi_origin.items()}
    prim._last_gpos = QPoint(800 + 37, 600 - 23)      # 非整格位移
    win._follow_multi_drag()
    moved = {k: win.icons[k].pos() for k in ("a", "c", "d")}
    want = {k: QPoint(origin[k].x() + 37, origin[k].y() - 23) for k in moved}
    check("F3 成员位移 = 指针位移（37,-23，逐像素）", moved == want,
          "%s vs %s" % (moved, want))

    # ---------- F4 主条目自身不动（它在拖影里） ----------
    check("F4 主条目部件位置不动", win.icons["b"].pos() == origin["b"],
          "%s vs %s" % (win.icons["b"].pos(), origin["b"]))

    # ---------- F5 越界夹紧（右 / 页底同源） ----------
    cw, ch = grid_flow.cell_size(st)
    cw, ch = int(cw), int(ch)
    cols = int(st.columns)          # 列数来自 GridMetrics 快照，不是 settings
    rpp = grid_flow.rows_per_page()
    hi = rpp - 1
    prim._last_gpos = QPoint(800 + 20 * cw, 600 + 20 * ch)
    win._follow_multi_drag()
    check("F5 贴右夹紧：块最右列 = cols-1",
          win.icons["d"].pos().x() == origin["d"].x() + (cols - 1 - 2) * cw,
          "x=%d want=%d cols=%d cw=%d" % (win.icons["d"].pos().x(),
                                          origin["d"].x() + (cols - 1 - 2) * cw,
                                          cols, cw))
    check("F5b 贴底夹紧：块最下行 = 页底",
          win.icons["d"].pos().y() == origin["d"].y() + (hi - 2) * ch,
          "y=%d want=%d rpp=%d" % (win.icons["d"].pos().y(),
                                   origin["d"].y() + (hi - 2) * ch, rpp))

    # ---------- F6 同一格内也跟手（不经过布局重算） ----------
    prim._last_gpos = QPoint(800 + 3, 600 + 3)
    win._follow_multi_drag()
    check("F6 同格内微动也逐像素生效",
          win.icons["a"].pos() == QPoint(origin["a"].x() + 3,
                                         origin["a"].y() + 3),
          str(win.icons["a"].pos()))

    # ---------- F7 预览落地不给成员叠弹簧 ----------
    moves = grid_flow.multi_move(st.layout, cols, "b", (2, 2), set(ids),
                                 page_rows=(0, hi))
    win._preview_moves = None
    win._apply_preview_moves("b", moves or {"a": (1, 1)})
    app.processEvents()
    members_with_anim = [k for k in ids if k != "b"
                         and _has_pos_anim(win.icons[k])]
    check("F7 成员不播让位弹簧（只有像素跟随一套位置源）",
          not members_with_anim, str(members_with_anim))

    # ---------- F10 跟随由拖拽源信号驱动（指针压在别的图标上也不冻） ----------
    # dragMoveEvent 只在"合成事件发到桌面窗口"时到达；指针压住别的图标时
    # 事件发给那个图标，桌面窗口收不到——所以跟随必须挂 dragMoved。
    prim._last_gpos = QPoint(800 + 11, 600 + 7)
    prim.dragMoved.emit()
    check("F10 dragMoved 直接驱动整块跟随",
          win.icons["a"].pos() == QPoint(origin["a"].x() + 11,
                                         origin["a"].y() + 7),
          str(win.icons["a"].pos()))

    # ---------- F8 翻页后退回按格预览 ----------
    win._multi_page = 99          # 模拟"拖动中已翻页"
    prim._last_gpos = QPoint(800 + 50, 600 + 50)
    before = win.icons["a"].pos()
    win._follow_multi_drag()
    check("F8 翻页后停用像素跟随（交给翻页/格位系统）",
          win._multi_origin is None and win.icons["a"].pos() == before,
          "origin=%s" % (win._multi_origin,))

    # ---------- F9 命中测试排除"自己人" ----------
    # 成员跟着拖影走之后，拖影底下永远压着三个成员；不排除的话 dragEnter
    # 会发给成员（合成事件不再送到桌面窗口），让位预览直接死、松手还可能
    # 把整块拖进自己。
    from PySide6.QtWidgets import QWidget

    class _Ghost(QWidget):
        pass

    ghost = _Ghost(win)
    ghost.setGeometry(600, 600, 60, 60)
    ghost.show()
    prim._drag_ghost_w = ghost
    prim._drag_kids = None
    win.icons["a"].move(605, 605)       # 与拖影重叠
    prim._drag_ids = set()
    hit_raw, _ = prim._best_overlap_icon()
    prim._drag_ids = set(ids)
    hit_guarded, _ = prim._best_overlap_icon()
    check("F9 不排除时会命中自己人（证明这条防线是必要的）",
          hit_raw is not None and hit_raw.drag_payload in ids,
          "hit=%s" % (hit_raw.drag_payload if hit_raw else None))
    check("F9b 排除后不再命中任何选中成员",
          hit_guarded is None or hit_guarded.drag_payload not in ids,
          "hit=%s" % (hit_guarded.drag_payload if hit_guarded else None))

    win.close()


def run():
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="mfollow-"))
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
