# -*- coding: utf-8 -*-
"""v6 §5.0：多选拖动不得叠格——grid_flow.multi_move 纯逻辑单测。

规格（指导文档_网格与无界双模式_v6.md §5.0）：
  1. 主条目（拖的那个）精确落指针 cell；其余成员保持与原布局相同的相对偏移；
  2. 严禁两个成员落同一格——目标被占（含被另一个选中成员预定/已被组件
     占位）时顺延到最近空格（行优先扫描），不允许覆盖；
  3. 贴右/下边缘时 col/row 夹紧不得导致成员聚成一列（旧 bug 根因：夹紧后
     多个成员的 nc 全落到同一个 cols-1）；
  4. 目标格的非选中占据者：交换到源格；源格也被占则顺延，绝不丢条目；
  5. 验收：叠格/右边缘/组件占位/形状保持 4 类场景 + 随机不变量。

运行：python tests/test_multi_move.py   （offscreen）
"""
import os
import sys
import random
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import grid_flow  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


class _St:
    def __init__(self, cols):
        self._cols = cols

    def get_setting(self, k, d=None):
        return {"columns": self._cols}.get(k, d)

    @property
    def columns(self):
        return self._cols

    @property
    def layout(self):
        return []

    @property
    def widgets(self):
        return []


def L(spec):
    """spec: [(id, col, row)] -> layout 列表。"""
    return [{"id": i, "col": c, "row": r} for i, c, r in spec]


def main():
    st = _St(10)
    COLS = 10

    # ---------- MM1：形状保持（2x2 块整体平移）----------
    layout = L([("a", 0, 0), ("b", 1, 0), ("c", 0, 1), ("d", 1, 1),
                ("x", 9, 9)])
    moves = grid_flow.multi_move(layout, COLS, "a", (5, 5),
                                 {"a", "b", "c", "d"})
    check("MM1 主条目精确落 (5,5)", moves and moves["a"] == (5, 5),
          "got %s" % (moves,))
    check("MM1 形状保持（2x2 块整体平移）",
          moves == {"a": (5, 5), "b": (6, 5), "c": (5, 6), "d": (6, 6)},
          "got %s" % (moves,))

    # ---------- MM2：目标被占 -> 非选中占据者交换到源格 ----------
    layout = L([("a", 0, 0), ("b", 1, 0), ("c", 0, 1), ("d", 1, 1),
                ("x", 5, 5)])
    moves = grid_flow.multi_move(layout, COLS, "a", (5, 5),
                                 {"a", "b", "c", "d"})
    check("MM2 主条目落 (5,5)", moves and moves["a"] == (5, 5))
    check("MM2 占据者 x 交换到源格 (0,0)", moves.get("x") == (0, 0),
          "got %s" % (moves,))
    check("MM2 块其余成员保持形状",
          (moves.get("b"), moves.get("c"), moves.get("d"))
          == ((6, 5), (5, 6), (6, 6)), "got %s" % (moves,))

    # ---------- MM3：组件占位格 -> 冲突成员顺延，不覆盖 ----------
    layout = L([("a", 0, 0), ("b", 1, 0), ("c", 0, 1), ("d", 1, 1)])
    extra = {(5, 5), (6, 5)}          # 组件占位盖住块右上两格
    moves = grid_flow.multi_move(layout, COLS, "a", (5, 5),
                                 {"a", "b", "c", "d"},
                                 extra_occupied=extra)
    check("MM3 主条目顺延到空格 (7,5)", moves and moves["a"] == (7, 5),
          "got %s" % (moves,))
    check("MM3 无成员落组件占位格",
          not any(moves[k] in extra for k in ("a", "b", "c", "d")),
          "got %s" % (moves,))
    check("MM3 成员互不重叠（含顺延后）",
          len(set(moves[k] for k in ("a", "b", "c", "d"))) == 4,
          "got %s" % (moves,))

    # ---------- MM4：右边缘夹紧不聚成一列 ----------
    layout = L([("a", 0, 0), ("b", 1, 0), ("c", 0, 1), ("d", 1, 1)])
    moves = grid_flow.multi_move(layout, COLS, "a", (9, 3),
                                 {"a", "b", "c", "d"})
    check("MM4 贴右整体左移保持 2x2",
          moves == {"a": (8, 3), "b": (9, 3), "c": (8, 4), "d": (9, 4)},
          "got %s" % (moves,))
    # 贴左同理：整块刚性右移。早期 Red 版曾把期望写成"主条目钉在
    # (0,3)、其余成员镜像到右侧"——那与规格 1「其余成员保持与原布局
    # 相同的相对偏移」矛盾（a 原本在 b 左侧，镜像后跑到右侧）。边缘处
    # 主条目落点与相对偏移不可兼得时，**形状与偏移守恒优先**：整块夹紧
    # 平移，主条目随块右移一格（与贴右场景同构——那边主条目同样随块
    # 左移一格， (9,3) 落点让位给块内右侧成员）。
    moves2 = grid_flow.multi_move(layout, COLS, "b", (0, 3),
                                  {"a", "b", "c", "d"})
    check("MM4 贴左整体右移保持 2x2",
          moves2 == {"a": (0, 3), "b": (1, 3), "c": (0, 4), "d": (1, 4)},
          "got %s" % (moves2,))

    # ---------- MM5：绝不丢条目 + 无重叠（随机 200 轮）----------
    rnd = random.Random(20261019)
    bad = 0
    for _ in range(200):
        n = rnd.randint(2, 6)
        sel = {"s%d" % i for i in range(n)}
        others = ["o%d" % i for i in range(rnd.randint(0, 4))]
        spec = [("s%d" % i, rnd.randrange(COLS), rnd.randrange(6))
                for i in range(n)]
        spec += [(o, rnd.randrange(COLS), rnd.randrange(6)) for o in others]
        # 去重（同格条目会模拟"已被占"场景，但选中成员间不允许同格——
        # 构造时若撞车重掷）
        seen, ok_spec = set(), []
        for i, c, r in spec:
            if (c, r) in seen:
                continue
            seen.add((c, r))
            ok_spec.append((i, c, r))
        if not any(i.startswith("s") for i, _, _ in ok_spec):
            continue
        layout = L(ok_spec)
        primary = next(i for i, _, _ in ok_spec if i.startswith("s"))
        tgt = (rnd.randrange(COLS), rnd.randrange(8))
        moves = grid_flow.multi_move(layout, COLS, primary, tgt, sel)
        if moves is None:
            continue
        sel_moves = {k: v for k, v in moves.items() if k in sel}
        others_moves = {k: v for k, v in moves.items() if k not in sel}
        all_cells = list(sel_moves.values()) + list(others_moves.values())
        # 不变量：选中成员互不重叠、数量守恒、无成员落窗外
        if len(set(sel_moves.values())) != len(sel_moves):
            bad += 1
        if len(sel_moves) != len([i for i, _, _ in ok_spec
                                  if i.startswith("s")]):
            bad += 1
        if any(c < 0 or c >= COLS for c, _ in all_cells):
            bad += 1
    check("MM5 随机 200 轮不变量（无叠格/不丢/不越界）", bad == 0,
          "bad=%d" % bad)

    # ---------- MM7：贴页底整块上移（不得跨页）----------
    # 2026-10-27 用户反馈：框选 2×2 四个图标，抓住右上角那个拖到最底下一行
    # 松手，块内**下面两个**跑到下一个分页去了。病根：multi_move 只夹了
    # 左右与上边界（min_r < 0），没夹页底——2 行高的块落到底行就溢出。
    # 规格与水平同构：贴边整块夹紧平移，形状/相对偏移守恒。
    layout = L([("a", 1, 2), ("b", 2, 2), ("c", 1, 3), ("d", 2, 3),
                ("x", 1, 4), ("y", 2, 4)])
    moves = grid_flow.multi_move(layout, 8, "b", (2, 4), {"a", "b", "c", "d"},
                                 page_rows=(0, 4))
    check("MM7 贴页底整块上移（块内全部留在本页）",
          moves == {"a": (1, 3), "b": (2, 3), "c": (1, 4), "d": (2, 4),
                    "x": (1, 2), "y": (2, 2)},
          "got %s" % (moves,))
    check("MM7b 无成员跨页（row <= 页底 4）",
          all(r <= 4 for _, r in moves.values()),
          "%s" % sorted({r for _, r in moves.values()}))

    # ---------- MM8：第二页同样不得越到上一页 ----------
    # 旧代码只夹 min_r < 0（全局 0 行），在第 2 页顶部拖就会窜回第 1 页。
    layout = L([("a", 1, 7), ("b", 2, 7), ("c", 1, 8), ("d", 2, 8)])
    moves = grid_flow.multi_move(layout, 8, "a", (1, 5), {"a", "b", "c", "d"},
                                 page_rows=(5, 9))
    check("MM8 第 2 页顶部整块下移（不窜回上一页）",
          moves == {"a": (1, 5), "b": (2, 5), "c": (1, 6), "d": (2, 6)},
          "got %s" % (moves,))
    check("MM8b 无成员越到上一页（row >= 页顶 5）",
          all(r >= 5 for _, r in moves.values()))

    # ---------- MM9：块比一页还高 -> 对齐页顶、允许溢出（不丢条目）----------
    layout = L([("s%d" % i, 0, i) for i in range(6)])
    moves = grid_flow.multi_move(layout, 8, "s0", (0, 3),
                                 {"s%d" % i for i in range(6)},
                                 page_rows=(0, 4))
    check("MM9 超高块对齐页顶不丢成员",
          moves is not None and len(moves) == 6
          and min(r for _, r in moves.values()) == 0,
          "got %s" % (moves,))

    # ---------- MM10：右下角同时夹紧（水平 + 垂直）----------
    layout = L([("a", 0, 0), ("b", 1, 0), ("c", 0, 1), ("d", 1, 1)])
    moves = grid_flow.multi_move(layout, 8, "b", (7, 4),
                                 {"a", "b", "c", "d"}, page_rows=(0, 4))
    check("MM10 右下角整块左上移，形状保持且不跨页",
          moves == {"a": (6, 3), "b": (7, 3), "c": (6, 4), "d": (7, 4)},
          "got %s" % (moves,))

    # ---------- MM6：无效输入 ----------
    check("MM6 primary 不在 selected -> None",
          grid_flow.multi_move(L([("a", 0, 0)]), COLS, "a", (1, 0),
                               {"b"}) is None)
    check("MM6 primary 不在布局 -> None",
          grid_flow.multi_move(L([("a", 0, 0)]), COLS, "zz", (1, 0),
                               {"zz"}) is None)

    print("-" * 60)
    if failures:
        print("FAILED %d: %s" % (len(failures), failures))
        return 1
    print("ALL multi_move PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
