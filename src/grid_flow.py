# -*- coding: utf-8 -*-
"""图标网格让位引擎（Live Reflow）——纯逻辑，零 Qt 依赖，可离线单测。

四个求解入口（2026-10-18 M2 起 preview 走 reflow_moves；2026-10-19 v6
§5.0 起多选拖动走 multi_move；v6 §5.2 起松手/预览落点走
cell_by_overlap）：

1. **reflow_moves（位移清单，指导文档 §2.2，单选主路径）**：
   iOS 局部让位——dragged 精确落到 target（含空格，修复 P2 病根 1），
   只有新旧位置之间（阅读序）的图标滑动一格，其余不动。返回
   {id: (col, row)} 最小位移集，由 AppIcon.animate_to 驱动。

2. reflow_order（阅读序置换，旧路径，保留兼容）：
   design.md 2.2「占用格集合上的置换」——占用格集合守恒前提下重排
   阅读序。dragged 落在第 k 个占用格上（空格场景落点不精确，P2 病根）。

3. **multi_move（多选位移清单，v6 §5.0）**：
   整块刚性平移——主条目落指针 cell（组件占位则行优先顺延），成员保持
   相对偏移，bbox 夹紧不聚列；非选中占据者交换到源格，绝不丢条目。

4. **cell_by_overlap（落点判定，v6 §5.2）**：
   拖动图标框 vs 当前页全部 cell 的相交面积最大者；并列（含拖出
   网格的全 0）取图标中心点所在格——替换"指针在哪格算哪格"的
   半截判定（反馈 7a）。

性质（tests/test_grid_flow.py + tests/test_multi_move.py 全量断言）：
  - reflow_moves：精确落点 / 最小位移 / 无重叠 / 无丢失 /
    占用集合 = 旧集合 - 老格 + target
  - reflow_order：占用格集合守恒 / 跨行 / 空格 / 占用格 / 原地统一处理
  - multi_move：形状保持 / 无叠格 / 数量守恒 / 边缘夹紧不聚列 /
    组件占位顺延 / 占据者交换不丢
  - cell_by_overlap：格内精确 / 跨格面积最大 / 并列走中心点 /
    网格外夹紧
  - O(n log n)，每次 dragMove 全量重算无压力

与 DesktopWindow 的关系：_cell()/_origin() 已下沉到本模块（cell_size/origin），
两边共用同一份几何定义，避免 DRY 违背导致的网格与图标错位。
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

Cell = Tuple[int, int]


# ---------- 阅读序 ----------
def _cell_of(entry: dict) -> Cell:
    return int(entry.get("col", 0)), int(entry.get("row", 0))


def reading_order(entries: Iterable[dict], cols: int) -> List[dict]:
    """按 (row, col) 稳定排序（同一行内从左到右，行间从上到下）。"""
    return sorted(entries, key=lambda e: (int(e.get("row", 0)), int(e.get("col", 0))))


def is_big_folder(storage, entry) -> bool:
    """该条目是否按大文件夹渲染（v14u 改 per-entry：entry["big"]=True）。

    storage 形参保留（调用点众多；判定本身只看条目）。"""
    try:
        if str(entry.get("type") or "") != "folder":
            return False
    except Exception:        # noqa: BLE001
        return False
    return bool(entry.get("big"))


def big_extra_cells(entry, cols: int):
    """大文件夹瓷砖除自身格外占用的 3 格（右/下/右下）。

    右边贴边（col+1 >= cols）放不下 2×2 时返回 None——pack 时跳到
    下一行再放。
    """
    col = int(entry.get("col", 0) or 0)
    row = int(entry.get("row", 0) or 0)
    if col + 1 >= max(1, int(cols)):
        return None
    return [(col + 1, row), (col, row + 1), (col + 1, row + 1)]


def folder_extra_cells(storage) -> set:
    """全部大文件夹的额外占格集合（网格模式；无界模式不占格）。"""
    out = set()
    cols = max(1, int(getattr(storage, "columns", 0) or 0))
    for e in storage.layout:
        if str(e.get("type") or "") != "folder":
            continue
        extra = big_extra_cells(e, cols)
        if extra:
            out.update(extra)
    return out


def occupied_cells(entries: Iterable[dict], cols: int,
                   storage=None) -> List[Cell]:
    """排序去重后的占用格集合。cols 仅用于签名对称（调用方已夹紧 col）。

    storage 传入时追加大文件夹的 3 个派生格（v14t）——拖拽落点/组件
    寻位等"什么格子能放"的判定全部继承，大瓷砖不会被压。
    """
    seen = set()
    for e in entries:
        seen.add(_cell_of(e))
    if storage is not None and not free_mode(storage):
        cols_i = max(1, int(cols))
        for e in entries:
            if str(e.get("type") or "") != "folder":
                continue
            extra = big_extra_cells(e, cols_i)
            if extra:
                seen.update(extra)
    return sorted(seen)


# ---------- 让位求解 ----------
def reflow_order(entries: Sequence[dict], cols: int, dragged_id: str,
                 target: Cell, extra_occupied: Iterable[Cell] = ()) -> Optional[List[str]]:
    """计算拖动 dragged_id 到 target 后的新阅读序（entry id 列表）。

    target 越界会被夹紧到 [0, cols-1] x [0, +inf)。
    dragged_id 不存在时返回 None（调用方应忽略本次预览）。
    原地拖动返回与原顺序相同的列表（恒等）。

    extra_occupied：额外的固定占用格集合（锚定小组件的占位，2026-10-18
    「小组件占据格子挤走图标」）。target 落在其中时，先就近解析到最近的
    非占用格——小组件的格子不给图标用。

    2026-10-18 M2 修复多行 scrambling：旧实现用 (col,row) 排序的
    occupied_cells 与 (row,col) 阅读序 zip，多行布局下序号错位——B/C
    这类跨行图标会被互换位置（P2「全体乱排」的隐藏帮凶；旧单测只测
    单行所以没抓到）。现统一改为阅读序配对 cells。
    """
    if cols < 1:
        return None
    by_id = {e.get("id"): e for e in entries}
    if dragged_id not in by_id:
        return None

    extra = set(extra_occupied or ())
    tcol = max(0, min(int(cols) - 1, int(target[0])))
    trow = max(0, int(target[1]))
    target = (tcol, trow)
    if target in extra:
        target = nearest_free_cell(target, set(occupied_cells(entries, cols)) | extra, cols)

    ordered = reading_order(entries, cols)
    order = [e["id"] for e in ordered]
    if dragged_id not in order:
        return None
    cells = [_cell_of(e) for e in ordered]  # 阅读序配对：cells[i] 即 order[i] 的占用格
    if len(set(cells)) != len(cells):
        return None  # 脏布局（重复占用格）：宁可不预览也别写坏
    rest = [i for i in order if i != dragged_id]

    # 统一插入规则：k = 阅读序中第一个 >= target 的占用格下标（(row,col) 空间比较）
    #   - target 被占用  -> k = 该格下标，dragged 正好落上目标格，占用者及其后整体后移
    #   - target 为空    -> k = 第一个严格大于 target 的格下标，dragged 落到该格
    #   - target == 老格 -> k = 老格下标，等价于恒等（移回去恢复原序）
    tkey = (target[1], target[0])
    k = len(cells)
    for idx, c in enumerate(cells):
        if (c[1], c[0]) >= tkey:
            k = idx
            break
    k = max(0, min(len(rest), k))
    return rest[:k] + [dragged_id] + rest[k:]


def reflow_moves(entries: Sequence[dict], cols: int, dragged_id: str,
                 target: Cell, rows: Optional[int] = None,
                 extra_occupied: Iterable[Cell] = ()) -> Optional[dict]:
    """计算拖动 dragged_id 到 target 后的**位移清单** {id: (col, row)}。

    iOS 局部让位（指导文档 §2.2，2026-10-18 M2）：与 reflow_order 的本质
    区别是语义从「重排阅读序」换成「位移清单」——dragged **精确落到
    target（含空格）**，只有新旧位置之间（阅读序）的图标滑动一格，
    其余一律不动。空格从此生效（修复 P2 病根 1：空格永不生效）。

    算法（2026-10-18 M2：全部在**阅读序空间**进行，cells[i] 即 order[i]
    的占用格——消除旧实现 (col,row)/(row,col) 双排序导致的跨行 scrambling）：
        order = 阅读序（含 dragged）；cells = 占用格（含 dragged 老格）
        d = dragged 在 order 中的下标；p = 阅读序中第一个 >= target 的下标
        p > d（向后移）：target 被占用 -> run = (d, p]；否则 run = (d, p)
                        run 内图标从 cells[i] 前移一格到 cells[i-1]
        p < d（向前移）：run = [p, d)，图标从 cells[i] 后移一格到 cells[i+1]
        dragged -> target；p == d 且 cells[p] == target：恒等，无位移

    性质（tests/test_grid_flow.py §13 全量断言）：
        - 占用集合 = 旧集合 - 老格 + target（空格精确落点，空位随拖动平移）
        - 永不重叠、永不丢图标、位移集合最小化（run 外图标不动）
        - O(n log n)；target 越界夹紧 [0, cols-1] x [0, rows-1]（rows=None 不限行）
        - target 落在 extra_occupied（小组件占位）时就近解析到最近可用格

    dragged_id 不存在 / 占用格数与图标数不符（脏布局）返回 None；
    原地恒等返回 {}。
    """
    if cols < 1:
        return None
    by_id = {e.get("id"): e for e in entries}
    if dragged_id not in by_id:
        return None

    ordered = reading_order(entries, cols)
    order = [e["id"] for e in ordered]
    cells = [_cell_of(e) for e in ordered]  # 阅读序配对：cells[i] 即 order[i] 的占用格
    if len(set(cells)) != len(cells) or dragged_id not in order:
        return None  # 脏布局（重复占用格）：宁可不预览也别写坏

    extra = set(extra_occupied or ())
    tcol = max(0, min(int(cols) - 1, int(target[0])))
    trow = max(0, int(target[1]))
    if rows is not None:
        trow = min(trow, max(0, int(rows) - 1))
    target = (tcol, trow)
    if target in extra:
        target = nearest_free_cell(target, set(cells) | extra, cols)

    d = order.index(dragged_id)
    # p = 阅读序中第一个 >= target 的占用格下标（(row,col) 空间比较；
    # 越界时为 len(cells)：追加队尾）
    tkey = (target[1], target[0])
    p = len(cells)
    for idx, c in enumerate(cells):
        if (c[1], c[0]) >= tkey:
            p = idx
            break

    moves = {}
    if p > d:
        # 向后移：target 被占用（cells[p] == target）时占用者也要动
        end = p if (p < len(cells) and cells[p] == target) else p - 1
        for i in range(d + 1, end + 1):
            moves[order[i]] = cells[i - 1]
        moves[dragged_id] = target
    elif p < d:
        # 向前移：[p, d) 内图标整体后移一格
        for i in range(p, d):
            moves[order[i]] = cells[i + 1]
        moves[dragged_id] = target
    elif cells[p] != target:
        # p == d 但 target 不是 dragged 老格（空格落在老格与前一个占用格之间）
        moves[dragged_id] = target
    # else：p == d 且 cells[p] == target —— 原地恒等，无位移
    return moves


def pack_cells(order_len: int, cols: int, blocked: Iterable[Cell] = (),
               start: Cell = (0, 0)) -> list:
    """从 start 起按阅读序给出 `order_len` 个**未被占**的格子。

    阅读序 = 「铺满整行再换行」：同一行内列递增，行尾回到列 0、行 +1。
    blocked（组件占位）整格跳过——组件是网格一等公民，图标不许压它。
    """
    cols = max(1, int(cols))
    used = set(blocked or ())
    out = []
    col = max(0, min(int(start[0]), cols - 1))
    row = max(0, int(start[1]))
    # 上界保护：blocked 最多占满若干行，给 2 倍余量足以排完（防死循环）
    limit = (row + order_len + len(used) + 2) * cols + cols
    guard = 0
    while len(out) < order_len:
        guard += 1
        if guard > limit:
            break
        if (col, row) not in used:
            out.append((col, row))
        col += 1
        if col >= cols:
            col, row = 0, row + 1
    return out


def auto_arrange_moves(entries: Sequence[dict], cols: int, dragged_id: str,
                       target: Cell, extra_occupied: Iterable[Cell] = (),
                       rpp: int = 0, auto_pages=None
                       ) -> Optional[dict]:
    """**自动排列**（反馈 4，v7e）：拖放后整桌密排的位移清单。

    与 `reflow_moves` 的分工（用户原话："自动排列的时候……其余图标应该
    自动排列铺满整行后再排列到第二行"）：
      - `reflow_moves` 只让位：把 dragged 插进阅读序、新旧位置之间滑动
        一格，**其余空格原样保留**（自由摆放语义）；
      - 本函数在自动排列下接管：dragged 插到目标处的阅读序位置后，
        该页图标从**该页首行**连续密排，中间不留洞。

    算法（纯函数）：
      1. 按 `rpp` 把条目分组到页（row // rpp）；**每页独立密排**；
      2. 目标页：除 dragged 外按阅读序排好，找到第一个比 target 靠后的
         图标，dragged 插在它前面（目标在队尾之外 = 追加）；
      3. 目标页从 `page*rpp` 起逐格分配，跳组件占位（含 extra_occupied）；
      4. dragged 从别的页拖来时，**原页也补洞**（该页若属于自动排列）。

    v9 V2 修正（BUG2：拖到下一页松手会跳回初始页）：
      旧实现用 `pack_cells(len+1, cols)` 从 **(0,0) 全局**密排，与页无关。
      拖到第 2 页时目标行在页 2，但密排结果全落在页 1——松手瞬间"全体
      回到初始页"。现在每页从自己的首行开始排，跨页拖动只影响"原页"
      与"目标页"两页。

    `auto_pages`：允许自动排列的页号集合（None = 全部页）。目标页不在
    集合内返回 None —— 调用方回退到 `reflow_moves`（自由摆放语义）。

    target 越界夹紧到 [0, cols-1]（行不限：可分页）。脏布局（重复占用格）
    返回 None —— 与 reflow_moves 同策：宁可不预览也别写坏。
    """
    if cols < 1:
        return None
    rpp = max(1, int(rpp) or rows_per_page())
    live = [e for e in entries if e.get("id") is not None]
    ids = [e.get("id") for e in live]
    if dragged_id not in ids:
        return None
    cells = [_cell_of(e) for e in live]
    if len(set(cells)) != len(cells):
        return None

    tcol = max(0, min(int(cols) - 1, int(target[0])))
    trow = max(0, int(target[1]))
    tpage = page_of(trow, rpp)
    if auto_pages is not None and tpage not in auto_pages:
        return None

    by_id = {e.get("id"): e for e in live}
    by_page: Dict[int, List[dict]] = {}
    for e in live:
        by_page.setdefault(page_of(int(e.get("row", 0) or 0), rpp), []).append(e)

    def _reading(items):
        return sorted(items, key=lambda x: (int(x.get("row", 0) or 0),
                                            int(x.get("col", 0) or 0)))

    def _blocked_in(page):
        return {c for c in (extra_occupied or ()) if page_of(c[1], rpp) == page}

    moves = {}

    # ---- 目标页：密排 ----
    src_page = page_of(int(by_id[dragged_id].get("row", 0) or 0), rpp)
    tgt = [e for e in by_page.get(tpage, []) if e.get("id") != dragged_id]
    tgt = _reading(tgt)
    slots = pack_cells(len(tgt) + 1, cols, _blocked_in(tpage),
                       start=(0, tpage * rpp))
    # 插入点：目标格的**页内**阅读序下标（密排世界里"拖到第 3 格"就落第 3 位）
    tkey = (trow - tpage * rpp, tcol)
    pos = len(slots) - 1
    for i, c in enumerate(slots):
        if (c[1] - tpage * rpp, c[0]) >= tkey:
            pos = i
            break
    order = [e["id"] for e in tgt]
    order.insert(pos, dragged_id)
    for eid, cell in zip(order, slots):
        e = by_id.get(eid)
        if e is None:
            continue
        if _cell_of(e) != cell:
            moves[eid] = cell

    # ---- 原页：补洞（跨页拖动才有意义；同页已在上一步处理好）----
    if src_page != tpage and (auto_pages is None or src_page in auto_pages):
        rest = [e for e in by_page.get(src_page, []) if e.get("id") != dragged_id]
        rest = _reading(rest)
        slots2 = pack_cells(len(rest), cols, _blocked_in(src_page),
                            start=(0, src_page * rpp))
        for e, cell in zip(rest, slots2):
            if _cell_of(e) != cell:
                moves[e["id"]] = cell
    return moves



def _forward_scan_cell(start: Cell, blocked: set, cols: int,
                       max_row: int) -> Cell:
    """从 start 起按阅读序找第一个非 blocked 格（v6 §5.0 顺延扫描）。

    「行优先扫描」的精确语义：同一行内从 start 列向右，到行尾后下一行
    从列 0 继续——target 被组件占位时主条目顺延到 (7,5) 而不是左侧
    等距空格（MM3 钉死：顺延 != 曼哈顿最近，而是阅读序向前）。
    扫描上限 max_row；找不到返回夹紧后的 start（兜底：叠一格好过
    丢一个条目）。
    """
    sc = max(0, min(int(start[0]), cols - 1))
    sr = max(0, int(start[1]))
    for row in range(sr, max(sr, int(max_row)) + 1):
        for col in range(sc if row == sr else 0, cols):
            if (col, row) not in blocked:
                return col, row
    return sc, sr


def multi_move(entries: Sequence[dict], cols: int, primary_id: str,
               target: Cell, selected, extra_occupied=()) -> Optional[dict]:
    """多选拖动的位移清单 {id: (col, row)}（v6 §5.0，2026-10-19 新增）。

    症状（反馈）：框选多个图标再移动，松手后**有的图标叠在同一个网格内**。
    规格（指导文档_网格与无界双模式_v6.md §5.0）：
      1. 主条目（拖的那个）精确落指针 cell；其余成员保持与原布局相同的
         相对偏移；
      2. 严禁两个成员落同一格——目标被占（含被另一个选中成员预定/已被
         组件占位）时顺延到最近空格（行优先扫描），不允许覆盖；
      3. 贴右/下边缘时 col/row 夹紧不得导致成员聚成一列（旧病根：逐成员
         max(0, min(cols-1, ...)) 各自夹紧，多个成员的 nc 全落 cols-1）；
      4. 目标格的非选中占据者：交换到源格；源格也被占则顺延，绝不丢条目。

    算法（纯函数，零 Qt 依赖；TDD：tests/test_multi_move.py）：
      a. 主条目落点 = 指针 cell 夹紧；落在组件占位格上则从该格起**行优先
         顺延**（组件格不给图标用；非选中图标不挡主条目——按规则 4 交换）；
      b. 整块**刚性平移**：成员 = 主条目落点 + 原相对偏移；bbox 夹紧进
         [0, cols-1] x [0, +inf)（贴右整体左移、贴左整体右移——形状与
         偏移守恒，不聚列不叠格）；
      c. 成员落点仍撞组件占位（主条目顺延后刚性格平移可能把别人推回组件
         格的罕见交织）：按阅读序逐个行优先顺延（主条目优先）；
      d. 非选中占据者交换到对应成员的**源格**；源格被占/在组件上/越界则
         行优先顺延——每个条目都有落点，绝不丢；
      e. 返回值含**全部**选中成员（含原地恒等的）+ 被交换/顺延的非选中
         图标；未受影响的条目不出现在字典里（MM1：无关图标 x 不在内）。

    与 reflow_moves 的分工：reflow 是单选 iOS 局部让位（递进语义）；
    multi_move 是多选整块平移（绝对落点 + 刚性形状）。auto_arrange 对
    多选无意义——整块平移两种模式一致（反馈只要求不叠格）。

    脏布局（占用格重复）/ primary 不在 selected / 不在布局 -> None
    （宁可不平移也别写坏，与 reflow_moves 同策）。
    """
    if cols < 1:
        return None
    by_id = {}
    for e in entries:
        by_id[e.get("id")] = e
    if primary_id not in by_id:
        return None
    sel = []
    for i in (selected or ()):
        if i in by_id and i not in sel:
            sel.append(i)
    if primary_id not in sel:
        return None
    cells = [_cell_of(e) for e in entries]
    if len(set(cells)) != len(cells):
        return None  # 脏布局（占用格重复）：宁可不平移也别写坏
    src = {i: _cell_of(by_id[i]) for i in sel}
    if len(set(src.values())) != len(sel):
        return None  # 选中成员占位格重复（相对偏移无定义）

    extra = set(extra_occupied or ())
    icon_cell = {}
    for e, c in zip(entries, cells):
        icon_cell[c] = e.get("id")
    sel_set = set(sel)
    max_row = max([r for _, r in cells] + [0]) + 2

    # a. 主条目落点：指针 cell 夹紧；落在组件占位上 -> 行优先顺延
    tcol = max(0, min(int(cols) - 1, int(target[0])))
    trow = max(0, int(target[1]))
    land = (tcol, trow)
    if land in extra:
        land = _forward_scan_cell(land, extra, cols, max(max_row, trow))

    # b. 整块刚性平移 + bbox 夹紧（贴右整体左移 / 贴左整体右移）
    pcol, prow = src[primary_id]
    want = {}
    for i in sel:
        want[i] = (land[0] + (src[i][0] - pcol), land[1] + (src[i][1] - prow))
    min_c = min(c for c, _ in want.values())
    max_c = max(c for c, _ in want.values())
    min_r = min(r for _, r in want.values())
    shift_c = -min_c if min_c < 0 else 0
    if max_c + shift_c > cols - 1:
        shift_c = (cols - 1) - max_c
    if min_c + shift_c < 0:          # 块宽于网格：左对齐兜底
        shift_c = -min_c
    shift_r = -min_r if min_r < 0 else 0
    finals = {i: (want[i][0] + shift_c, want[i][1] + shift_r) for i in sel}

    # c. 成员级组件占位顺延（主条目已在 a 解析；按阅读序，主条目优先）
    taken = set(finals.values())
    nomove = {c: i for c, i in icon_cell.items() if i not in sel_set}
    for i in sorted(sel, key=lambda x: (finals[x][1], finals[x][0],
                                        x != primary_id)):
        if finals[i] in extra:
            nc = _forward_scan_cell(finals[i], extra | taken | set(nomove),
                                    cols, max(max_row, finals[i][1]))
            taken.discard(finals[i])
            taken.add(nc)
            finals[i] = nc

    # 返回值含全部选中成员（含原地恒等）——调用方与 MM5 按数量守恒校验
    moves = {i: finals[i] for i in sel}

    # d. 非选中占据者交换到源格；源格不可用（被占/组件上/越界）则顺延
    occupied = set(finals.values())
    moving = sorted(c for c in nomove if c in occupied)
    staying = set(nomove) - set(moving)
    for c in moving:
        xid = nomove[c]
        member = next(i for i in sel if finals[i] == c)
        dest = src[member]
        if not (0 <= dest[0] < cols) or dest in extra \
                or dest in occupied or dest in staying:
            dest = _forward_scan_cell(c, extra | occupied | staying,
                                      cols, max(max_row, c[1]))
        moves[xid] = dest
        occupied.add(dest)
    return moves


def displace_cells(src_cells, blocked, cols: int, page_rows) -> dict:
    """给一批"被压住"的图标统一分配新格（**互不重复**）。

    2026-10-19 用户反馈 7b："元素排满后再移动小组件，图标会直接堆叠
    到一起"。病根是旧实现逐个调 nearest_free_cell，每次都用同一份静态
    `blocked`——两个图标会被分配到同一个空格上。

    规则（纯函数，**没有 Qt 依赖**，可单测）：
      1. 页内空格优先，**同行优先**（保持行归属），同行内按列距最近；
         同行无解才跨行，跨行时按曼哈顿距离取最近（同距阅读序）；
      2. 页内已满 -> 顺延到页底下方的新行（页码自然增长，条目不丢、
         也不会被分页裁掉）；
      3. 分配过的格子立刻进 taken，后续条目绝不会重复。

    为什么"同行优先"而不是纯曼哈顿：8 列的桌面里组件挡住左上角时，
    纯曼哈顿会把 (0,0) 的图标塞进组件正下方的 (0,2)——距离更近，但原来
    同一行的图标被拆到两行，视觉上就是用户反馈 7b 说的"排布混乱"。
    先守住行、再谈距离，图标阵才是整块平移而不是交叉打散。

    src_cells 为**原格序列**（决定分配顺序与就近偏好）；返回
    {原格: 新格}。blocked 需包含组件占位与其他不可用格。
    """
    cols = max(1, int(cols))
    lo, hi = int(page_rows[0]), int(page_rows[1])
    taken = set(blocked)
    free = [(c, r) for r in range(lo, hi + 1) for c in range(cols)
            if (c, r) not in taken]
    out = {}
    extra_row = hi + 1
    next_col = 0

    def _rank(cell, src):
        # 0 = 原地那行，1 = 其他行；再按行差、列距、阅读序
        return (0 if cell[1] == src[1] else 1,
                abs(cell[1] - src[1]),
                abs(cell[0] - src[0]),
                cell[1], cell[0])

    for src in src_cells:
        if free:
            dst = min(free, key=lambda c: _rank(c, src))
            free.remove(dst)
        else:
            if next_col >= cols:
                next_col = 0
                extra_row += 1
            dst = (next_col, extra_row)
            next_col += 1
        taken.add(dst)
        out[src] = dst
    return out


def nearest_free_cell(target: Cell, occupied: set, cols: int) -> Cell:
    """曼哈顿距离升序找第一个非占用格（同距时阅读序优先）。

    extra_occupied 场景下把落在小组件占位上的落点解析到最近可用格；
    行范围取占用格最大行 +2，保证屏幕外沿也有解。

    2026-10-18 M2：原实现按「|dr|=dist 方环」遍历，dist=1 就会访问
    曼哈顿距 2 的格子（如 (tc-1, tr+1)），与文档「曼哈顿距离升序」
    矛盾；改为真曼哈顿环（|dc|+|dr|=dist），环内按 (row, col) 排序。
    """
    occupied = set(occupied)
    max_row = max((r for _, r in occupied), default=0) + 2
    tc, tr = int(target[0]), int(target[1])
    for dist in range(0, cols + max_row + 4):
        ring = []
        for dc in range(-dist, dist + 1):
            dr = dist - abs(dc)
            for r in ({dr, -dr} if dr else (0,)):
                c, rr = tc + dc, tr + r
                if 0 <= c < cols and 0 <= rr <= max_row and (c, rr) not in occupied:
                    ring.append((c, rr))
        if ring:
            ring.sort(key=lambda c: (c[1], c[0]))  # 同距阅读序优先
            return ring[0]
    return max(0, min(tc, cols - 1)), tr


def apply_order(entries: Sequence[dict], order: Sequence[str]) -> None:
    """把 order（阅读序）按原占用格集合就地写回 col/row。

    前提：order 是 entries 的一个排列（reflow_order 的返回值即满足）。
    占用格按**阅读序**取出与 order 配对（2026-10-18 M2 修复：旧实现
    取 (col,row) 排序的集合，多行布局下与 (row,col) 阅读序序号错位，
    会把跨行图标互相 scrambling）。
    """
    if not order:
        return
    cells = [_cell_of(e) for e in reading_order(entries, 1 << 30)]
    if len(cells) != len(order) or len(set(cells)) != len(cells):
        return  # 防御：调用方传了不完整顺序 / 脏布局，宁可不写也别写坏布局
    by_id = {e.get("id"): e for e in entries}
    for eid, (c, r) in zip(order, cells):
        e = by_id.get(eid)
        if e is not None:
            e["col"], e["row"] = c, r


# ---------- 网格几何（与 DesktopWindow 共用，勿重复实现） ----------
_current_metrics = None  # 当前 GridMetrics 快照（M1：几何确定后由 set_grid 写入）


def set_grid(storage, screen_size, inset=None):
    """重算并缓存当前网格几何（M1，指导文档 §1：网格自适应铺满）。

    DesktopWindow 在启动 / 窗口几何变化 / DPI·分辨率·任务栏变化后调用。
    screen_size：availableGeometry 的 (W, H)。返回 GridMetrics 快照。

    inset：可用区四边内缩 `(left, top, right, bottom)`（v10 C3，Dock 栏）。
    由 `dock.storage_inset(storage)` 算出来——**本模块不认识 Dock**，
    只把四元组透传给 `grid_metrics.compute`（"可用区"只有一处定义）。

    2026-10-19（反馈 4）：settings["icon_match_native"] 开启时先把资源
    管理器**当前桌面图标边长**读出来传给 grid_metrics——网格与图标尺寸
    由此与原桌面一致（读不到则回退屏幕公式）。

    v10 B3：**换网格 = 换参照系**，翻页瞬态（`_flip`）在这里作废。翻页
    途中改了分辨率/Dock/窗口尺寸，本来就没有"同一帧画面"可言，落定才是
    正确结果；顺带堵住"上一条翻页动画没跑完就换窗口，新窗口所有位置都
    偏移半页"的跨窗泄漏（瞬态是模块级单例，与 `_current_page` 一样只服务
    一个桌面窗口）。
    """
    global _current_metrics, _flip
    _flip = None
    from . import grid_metrics  # 延迟导入：grid_metrics 依赖本模块 cell_size
    native = None
    try:
        if storage.get_setting("icon_match_native"):
            from . import win32_layer
            native = win32_layer.native_icon_size()
    except Exception:
        native = None
    _current_metrics = grid_metrics.compute(storage, screen_size,
                                            native_px=native, inset=inset)
    return _current_metrics


def current_metrics():
    """当前 GridMetrics 快照（未初始化时为 None，调用方回退历史默认）。"""
    return _current_metrics


def origin() -> Cell:
    """网格原点（桌面整理窗口控件坐标）。

    2026-10-18 M1（指导文档 §1，反馈 P1/P2）：不再硬编码——读
    DesktopWindow 写入的 GridMetrics 快照（按工作区四边等距铺满）；
    快照未就绪（挂载前 / 无头测试）时回退历史默认 (26, 22)。
    """
    m = _current_metrics
    if m is not None:
        return m.ox, m.oy
    return 26, 22


def cell_size(storage) -> Cell:
    """单元格尺寸——**快照优先**（DRY 单一来源）。

    2026-10-18 M1 前：与 AppIcon(setFixedSize(s+24m, s+40m)) 对齐的
    icon 派生值。2026-10-19 v6 批次 A（指导文档 §2.1）：GridMetrics
    的 cell 改屏幕驱动（grid_sizing.cell_for_screen，与 icon_size 解钩），
    cell_pos/pos_cell/snap_point/_cell_at 等一切几何换算必须跟随快照，
    否则出现"快照 cell 75x92 与派生 cell 96x114"双源漂移（T3 抓到）。
    快照未就绪（无头测试 / 挂载前）回退 icon 派生，保持旧行为。
    """
    m = _current_metrics
    if m is not None:
        return m.cw, m.ch
    s = int(storage.icon_metric())
    m = float(storage.ui_scale())
    return s + int(round(24 * m)), s + int(round(40 * m)) + 2


def snap_point(storage, x: int, y: int, screen_size: Tuple[int, int]) -> Cell:
    """把任意坐标吸附到最近的网格格点（当前页内），并夹紧在屏幕内。

    2026-10-18 M6：改走 pos_cell/cell_pos——页内局部行 + 列夹紧 +
    当前页偏移全在一处完成（此前这里的 round + clamp 与 DesktopWindow
    _cell_at 各写一份，分页后必然漂移）。
    """
    col, row = pos_cell(storage, x, y)
    return cell_pos(storage, col, row)


# ---------- 分页（2026-10-18 M6，指导文档 §7-M6） ----------
# 语义：存储里的 row 始终是**全局行**（零迁移——旧布局无需换算）；
# 页是纯视图概念：page = row // rows_per_page，像素 y 用页内局部行
# local_row = row % rows_per_page。翻页 = 改运行时当前页，全体位置
# 换算（cell_pos/pos_cell/snap_point/_entry_pos/_cell_at/widget）随之
# 整体偏移——当前页内容滑出屏、邻页内容滑入屏（iOS launcher 语言）。
_current_page = 0

# ---------- 轮播翻页状态（v10 B3，反馈 6） ----------
# 一个**标量**驱动整页平移：(target_page, direction, progress)。
# 翻页期间 `_current_page` 已经等于 target（语义/落盘立刻正确），位移由
# `flip_delta()` 覆盖成浮点页数、再由 `flip_px()` 换成像素。
#
# **瞬移只给绘制帧用，不进逻辑几何**（坑 #113）：`page_offset` 恒为整数页差，
# 所以 `cell_pos` / `pos_cell` / `cell_by_overlap` / 红框 / 让位预览在整段
# 滑动期间都不动；图标与组件靠 `flip_px()` 逐帧位移，两边读同一份标量，
# 因此同向、同速、同曲线地滑（单一真相源）。
#
# 旧实现（每图标各起一条弹簧 + 靠 anim.finished 隐藏）的三个病：
# 没有接续感、280ms 太快、离页图标在过冲帧仍露一角（"最右图标残留"）。
_flip = None        # [target, direction, progress] 或 None（不在翻页中）


def begin_flip(target: int, direction: int):
    """开始一段轮播翻页：target 页 + 方向（+1 下一页 / -1 上一页）。"""
    global _flip
    _flip = [max(0, int(target)), 1 if int(direction) >= 0 else -1, 0.0]


def set_flip_progress(v: float):
    """推进翻页进度（0 = 起点画面，1 = 终点画面）。"""
    if _flip is not None:
        _flip[2] = max(0.0, min(1.0, float(v)))


def end_flip():
    """结束翻页：位置派生回落到整数页差。"""
    global _flip
    _flip = None


def flip_state():
    """当前翻页状态 `(target, direction, progress)`；静止时为 None。"""
    return (int(_flip[0]), int(_flip[1]), float(_flip[2])) if _flip else None


def flip_delta(page: int) -> float:
    """该页当前的页间位移（**浮点页单位**）。

    静止态 = `page - current_page()`（整数，与旧实现逐值相等）；
    翻页中 = `(page - target) + dir × (1 - progress)`：

      滑入页（page == target）：dir → 0（从屏外滑进来）
      滑出页（page == target-dir）：0 → -dir（滑出屏外）
    """
    if _flip is None:
        return float(int(page) - _current_page)
    target, direction, progress = int(_flip[0]), int(_flip[1]), float(_flip[2])
    return (int(page) - target) + direction * (1.0 - progress)


def rows_per_page() -> int:
    """单屏行数 = GridMetrics.rows（M1 已按工作区算好，clamp 1..12）。

    快照未就绪（挂载前 / 无头测试）时回退历史默认 8（与 origin 时代
    的单屏行数同量级，保证 clamp 不会把 page 0 的条目算到屏外）。
    """
    m = _current_metrics
    if m is not None:
        return max(1, int(m.rows))
    return 8


def page_of(row: int, rpp: int = 0) -> int:
    """全局行 -> 页号（负行回退第 0 页）。"""
    rpp = max(1, int(rpp) or rows_per_page())
    return max(0, int(row)) // rpp


def local_row(row: int, rpp: int = 0) -> int:
    """全局行 -> 页内局部行（0..rpp-1）。"""
    rpp = max(1, int(rpp) or rows_per_page())
    return max(0, int(row)) % rpp


def entry_page(storage, entry) -> int:
    """条目所属页（**双坐标分派**，v7 B2 / 反馈 13）。

    网格模式：`page_of(row)`——row 是全局行，天然带页。
    无界模式：`entry["fpage"]`（无界页号）；老数据没有 fpage 时退回
    `page_of(row)`（迁移期不跳页）。

    为什么不能让无界模式继续拿 row 当页载体：row 是**网格坐标**，无界
    松手写 row 会把用户整理好的网格行号冲掉——这正是"切回网格一堆
    cell 重叠"的另一半病根（反馈 13）。
    """
    if free_mode(storage):
        raw = entry.get("fpage")
        if not isinstance(raw, bool):
            try:
                return max(0, int(raw))
            except (TypeError, ValueError):
                pass
    return page_of(int(entry.get("row", 0) or 0))


def page_row_range(page: int, rpp: int, rs: int = 1) -> Tuple[int, int]:
    """某页可容纳 rs 行跨度组件的全局行范围 [lo, hi]（组件不越页底）。"""
    rpp = max(1, int(rpp))
    lo = max(0, int(page)) * rpp
    hi = lo + rpp - max(1, int(rs))
    return lo, max(lo, hi)


def clamp_widget_cell(storage, col: int, row: int, cs: int, rs: int) -> Cell:
    """把组件占位夹紧到网格与页内（v6 §4，组件不越页底、不跨页半截）。

    列夹到 [0, columns-cs]；行夹到所属页（page_of(row)）的
    [page*rpp, page*rpp + rpp - rs]——便签 2×2 拖到页底最后一行时整块
    上移一行，底边不越出当前页可见区（span 夹紧的唯一实现，spawn /
    吸附落定 / 尺寸切换 / hover 预览共用，杜绝各写一份漂移）。
    """
    cols = storage.columns
    rpp = rows_per_page()
    col = max(0, min(int(col), max(0, int(cols) - int(cs))))
    lo, hi = page_row_range(page_of(int(row), rpp), rpp, rs)
    return col, max(lo, min(int(row), hi))


def widget_rect_cells(col: int, row: int, cs: int, rs: int) -> set:
    """组件占位的格子集合（col/row 左上角 + 跨度展开）。"""
    c0, r0 = int(col), int(row)
    return {(c0 + dc, r0 + dr)
            for dc in range(max(1, int(cs)))
            for dr in range(max(1, int(rs)))}


def widget_occupied_by_others(storage, exclude_id=None) -> set:
    """**其它**可见锚定组件占的格子（组件之间互斥的依据，2026-10-19）。

    与 widget_cells 的区别只有 exclude_id：拖动一个组件时必须排除它自己，
    否则它的旧占位会把自己挡住（原地微调都被判成冲突）。
    """
    cells = set()
    for w in storage.widgets:
        if w.get("hidden") or not w.get("anchored"):
            continue
        if exclude_id is not None and w.get("id") == exclude_id:
            continue
        if "col" not in w or "row" not in w:
            continue
        cs, rs = widget_span(storage, w)
        cells |= widget_rect_cells(w.get("col", 0), w.get("row", 0), cs, rs)
    return cells


def resolve_widget_cell(storage, col: int, row: int, cs: int, rs: int,
                        exclude_id=None) -> Cell:
    """组件落点消解：不与其它组件重叠的最近合法格（2026-10-19 用户反馈）。

    病根：`_snap_to_grid` 只夹紧网格边界，从不看别的组件——把便签拖到
    时钟身上松手，两者永久叠在一起（截图实锤）。

    语义（安卓小组件同语言）：
      - 目标格本身不冲突 -> 原样返回（不做多余挪动，零成本）
      - 冲突 -> 在同页可行位置里取**离目标最近**的一个（距离用格数平方，
        同距取扫描序），保证组件只是"被顶开一格"而不是跳到对角
      - 全页挤满 -> 退回夹紧后的原目标（displace 会兜底，不抛不崩）
    """
    blocked = widget_occupied_by_others(storage, exclude_id=exclude_id)
    cols = max(1, int(storage.columns))
    rpp = rows_per_page()
    c0, r0 = clamp_widget_cell(storage, col, row, cs, rs)
    if not (widget_rect_cells(c0, r0, cs, rs) & blocked):
        return c0, r0
    lo, hi = page_row_range(page_of(r0, rpp), rpp, rs)
    best, best_d = None, None
    for r in range(lo, hi + 1):
        for c in range(0, max(1, cols - int(cs) + 1)):
            if widget_rect_cells(c, r, cs, rs) & blocked:
                continue
            d = (c - c0) ** 2 + (r - r0) ** 2
            if best_d is None or d < best_d:
                best, best_d = (c, r), d
    return best if best is not None else (c0, r0)


def widget_cell_fits(storage, col: int, row: int, cs: int, rs: int,
                     exclude_id=None) -> bool:
    """给定组件矩形是否可落（不越界 + 不压别的组件）。编辑态缩放用。"""
    cols = max(1, int(storage.columns))
    rpp = rows_per_page()
    cs, rs = max(1, int(cs)), max(1, int(rs))
    if col < 0 or col + cs > cols:
        return False
    lo, hi = page_row_range(page_of(int(row), rpp), rpp, rs)
    if not (lo <= int(row) <= hi):
        return False
    blocked = widget_occupied_by_others(storage, exclude_id=exclude_id)
    return not (widget_rect_cells(col, row, cs, rs) & blocked)



def current_page() -> int:
    """运行时当前页（DesktopWindow 翻页时经 set_page 同步）。"""
    return _current_page


def set_page(page: int) -> int:
    """设置运行时当前页（夹到 >=0）。返回生效值。"""
    global _current_page
    _current_page = max(0, int(page))
    return _current_page


def page_number(max_row: int, rpp: int) -> int:
    """最大全局行号 -> 总页数（至少 1 页）。纯函数。"""
    return max(1, max(0, int(max_row)) // max(1, int(rpp)) + 1)


# ---------- 翻页方向（2026-10-19 用户反馈 5） ----------
# "现在的分页是上下逻辑，改成默认左右翻页，并在设置里可切换上下/左右"。
# 语义：**页容量不变**（一页仍是 cols × rows_per_page 个格），只有页与页
# 之间的滑行方向变化——左右模式下第 n 页整体排在当前页右侧一个屏宽处，
# 翻页时内容横向滑入滑出（iOS launcher 语言）。row 仍编码"第几页 + 页内行"，
# 零数据迁移。
PAGE_AXIS_HORIZONTAL = "horizontal"
PAGE_AXIS_VERTICAL = "vertical"


def page_axis(storage) -> str:
    """翻页方向：horizontal（默认）| vertical（设置里可切）。"""
    raw = str(storage.get_setting("page_axis") or "").strip().lower()
    return PAGE_AXIS_VERTICAL if raw == PAGE_AXIS_VERTICAL else PAGE_AXIS_HORIZONTAL


def page_canvas_w(storage) -> int:
    """单页横向跨度（像素）= 网格区宽 = cols × cw。

    左右翻页的页间滑行量；也是无界模式页与页的水平间距。
    """
    m = _current_metrics
    if m is not None:
        return max(1, int(m.cols)) * int(m.cw)
    cw, _ch = cell_size(storage)
    return max(1, int(storage.columns)) * cw


def page_offset(storage, page: int) -> Cell:
    """页间滑行偏移（像素）：(dx, dy)。

    vertical   -> (0, delta * rows*ch)
    horizontal -> (delta * 网格宽, 0)

    v10 B3：`delta` 保持**整数页差**（落定几何）。翻页动画的瞬移不在这里，
    走 `flip_px()`——逻辑几何（命中、落点、红框、让位预览）在整段滑动期间
    必须是静止的，否则指针停在边缘翻页时落点会随动画帧跳列。
    """
    delta = int(page) - _current_page
    if delta == 0:
        return 0, 0
    if page_axis(storage) == PAGE_AXIS_VERTICAL:
        return 0, int(round(delta * (rows_per_page() * cell_size(storage)[1])))
    return int(round(delta * page_canvas_w(storage))), 0


def flip_px(storage, page: int) -> Cell:
    """翻页**视觉瞬移**（像素）：该页此刻相对落定位置多滑了多少。

    只给动画帧用（`DesktopWindow._apply_flip_frame` / `WidgetLayer.apply_flip_frame`）。
    为什么单独一个函数、不并进 `page_offset`：翻页是**视图**动画，而
    `page_offset` 是**逻辑**几何（命中 / 落点 / 红框 / 让位预览都靠它）。
    两者混在一起时，指针停在边缘翻页、动画没跑完就松手，落点会随帧
    跳到别的列（`tests/test_edge_flip.py` F7c 抓到：目标第 3 列落成第 8 列）。
    逻辑几何全程静止、图标与组件靠本函数逐帧位移，两边各自正确。

    翻页中 = `flip_delta(page) - 整数页差`；静止态恒 (0, 0)。
    """
    d = flip_delta(page) - float(int(page) - _current_page)
    if d == 0:
        return 0, 0
    if page_axis(storage) == PAGE_AXIS_VERTICAL:
        return 0, int(round(d * (rows_per_page() * cell_size(storage)[1])))
    return int(round(d * page_canvas_w(storage))), 0


def page_count(storage, rpp: int = 0) -> int:
    """总页数：布局条目与锚定组件占位（含跨度）的最大全局行决定，
    至少 1 页；v6 §1.3 起再与空白页（extra_pages 最大值+1）取大。"""
    rpp = max(1, int(rpp) or rows_per_page())
    max_row = 0
    for e in getattr(storage, "layout", []):
        max_row = max(max_row, int(e.get("row", 0) or 0))
    for w in getattr(storage, "widgets", []):
        if not w.get("anchored") or w.get("hidden"):
            continue
        _cs, rs = widget_span(storage, w)
        max_row = max(max_row, int(w.get("row", 0) or 0) + rs - 1)
    n = page_number(max_row, rpp)
    # v7 B2：无界页号（fpage）单独算总页数——无界模式把元素摆到第 3 页时
    # 网格坐标 row 可能还在第 0 页（双坐标互不覆盖），只看 row 会漏页。
    for e in list(getattr(storage, "layout", [])) + list(getattr(storage, "widgets", [])):
        fp = e.get("fpage")
        if isinstance(fp, int) and not isinstance(fp, bool):
            n = max(n, int(fp) + 1)
    extras = extra_pages(storage)
    if extras:
        n = max(n, extras[-1] + 1)
    return n


def cell_pos(storage, col: int, row: int) -> Cell:
    """(col, 全局 row) -> 窗口控件坐标像素（列夹紧 + **相对当前页**行偏移）。

    所有「条目/组件 -> 像素」的换算统一走这里（DRY：DesktopWindow
    _entry_pos、widget_base、widget_layer._spawn 都是它的调用方）。

    M6：y = oy + (row - page*rpp) * ch——rel 可为负（上一页条目滑出
    屏顶）或 >= rpp（次页条目在屏下方），翻页时全体沿此式整体平移，
    天然形成「当前页滑出、邻页滑入」的 iOS launcher 动画。
    """
    cw, ch = cell_size(storage)
    ox, oy = origin()
    cols = storage.columns
    col = max(0, min(int(col), cols - 1))
    rpp = rows_per_page()
    page = page_of(int(row), rpp)
    local = max(0, int(row)) - page * rpp
    dx, dy = page_offset(storage, page)
    return ox + col * cw + dx, oy + local * ch + dy


def page_grid_center(storage) -> Cell:
    """网格区中心（v6 §6.2，指导文档 §6.2 反馈 8/9 居中项）。

    公式：(ox + cols*cw//2, oy + rows*ch//2)。网格区几何所有页相同
    （页只是行偏移的视图概念），故**不读 current_page**——文件夹弹窗
    中心对齐此点：弹窗是模态面板，从任意位置的文件夹图标打开都落在
    屏幕同一处，比「贴锚点图标」更符合预期，且与翻页状态解耦。
    """
    cw, ch = cell_size(storage)
    ox, oy = origin()
    cols = int(storage.columns)
    rows = rows_per_page()
    return ox + cols * cw // 2, oy + rows * ch // 2


# ---------- 双模式（v6 §1.3，指导文档_网格与无界双模式_v6.md） ----------
def layout_mode(storage) -> str:
    """当前布局模式（归一化）："grid"（默认）| "free"。"""
    m = str(storage.get_setting("layout_mode") or "grid").strip().lower()
    return "free" if m == "free" else "grid"


def free_mode(storage) -> bool:
    """是否无界模式（所有元素自由摆布、可相互重叠）。

    v6 §1.3 的显式切换（layout_mode="free"），外加 2026-10-19 反馈 3：
    **关闭「接管系统桌面」后自动按无界模式处理**——原生桌面图标已经
    恢复可见，网格强制对齐只会让两套图标叠在一起、组件被钉死在格点；
    用户要的是"这时就像普通悬浮层一样自由摆放"。

    判定表：
        接管 + grid -> 网格      接管 + free -> 自由
        未接管（任意）-> 自由     （显式 free 自然也是自由）
    """
    if layout_mode(storage) == "free":
        return True
    tk = getattr(storage, "takeover", None)
    if callable(tk):
        try:
            return not bool(tk())
        except Exception:
            return False
    return False


def page_canvas_h(storage) -> int:
    """无界模式单页画布高 = 网格区高（rows × ch）。

    与网格模式**同一页几何**——翻页滑行偏移量一致，goto_page / cull /
    sync_page 机制零改动；无界只是把 x 与页内 y 从格点上解绑
    （row 退化为纯页载体，见 to_free_coords）。
    """
    m = _current_metrics
    if m is not None:
        return max(1, int(m.rows)) * int(m.ch)
    _cw, ch = cell_size(storage)
    return rows_per_page() * ch


def _page_local_pos(storage, col: int, row: int, centered: bool = False) -> Cell:
    """所属页视角的格点像素（当前页偏移归零）——无界 fx/fy 的生成式。

    centered=True：图标 widget 比 cell 小时按居中留白补偿（反馈 1）——
    切无界的那一刻位置与网格模式视觉一致，不跳变。组件不用居中
    （跨格占位必须左上对齐格线）。
    """
    cw, ch = cell_size(storage)
    ox, oy = origin()
    cols = storage.columns
    rpp = rows_per_page()
    page = page_of(row, rpp)
    x = ox + max(0, min(int(col), cols - 1)) * cw
    y = oy + (max(0, int(row)) - page * rpp) * ch
    if centered:
        dx, dy = icon_center_offset(storage)
        x += dx
        y += dy
    return x, y


def icon_center_offset(storage, entry=None) -> Cell:
    """图标 widget 在 cell 内的居中留白 ((cw-w)//2, (ch-h)//2)（反馈 1）。

    2026-10-19 用户反馈："图标大小为中或者小的时候不应该位于网格左上角
    而是居中"——旧实现 entry_pos 直接返回 cell 左上角，小档（0.58×cell）
    时整套图标偏左上，留白全堆在右下。

    尺寸来源与 DesktopWindow._icon_layout 同源（grid_sizing.icon_layout ×
    当前快照 cell × 档位）。v7 B2 起档位改为 `storage.icon_fit()` 的
    实际占比（custom 档才对得上）；entry 传入时再乘它的 icon_scale
    （反馈 12：单图标大小覆盖）——否则放大后的图标仍然按 100% 居中，
    看起来是"往右下角长"。
    """
    if is_big_folder(storage, entry):
        return (0, 0)          # 大瓷砖锚定自身格左上（跨 2×2，不居中）
    from . import grid_sizing
    cw, ch = cell_size(storage)
    # 2026-10-20 反馈 3：占比**必须**与 DesktopWindow._icon_layout 同源。
    # 走 storage.resolved_icon_fit()（medium = native_px/cw 解算）；
    # 没有该方法的替身 storage（探针 / 单元测试）退回旧的档位串路径。
    resolved = getattr(storage, "resolved_icon_fit", None)
    fit = None
    if callable(resolved):
        try:
            fit = resolved(cw)
        except Exception:
            fit = None
    if fit is None:
        raw_fit = getattr(storage, "icon_fit", None)
        try:
            fit = raw_fit() if callable(raw_fit) else None
        except Exception:
            fit = None
    if fit is None:
        raw = getattr(storage, "icon_tier", None)
        try:
            fit = raw() if callable(raw) else (raw or grid_sizing.DEFAULT_TIER)
        except Exception:
            fit = grid_sizing.DEFAULT_TIER
    scale = 1.0
    if isinstance(entry, dict):
        try:
            scale = float(entry.get("icon_scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            scale = 1.0
    lay = grid_sizing.icon_layout(cw, ch, fit, scale=scale)
    return max(0, (cw - lay.widget_w) // 2), max(0, (ch - lay.widget_h) // 2)


def free_pos_cell(storage, x: int, y: int, page: int) -> Cell:
    """无界页内像素 -> (col, 全局 row)：按所属页反算（pos_cell 的页参数化版）。

    pos_cell 按**当前页**偏移反算；无界条目的 fx/fy 是页内坐标，必须
    按它自己的页反算，否则第 2 页的条目会被吸附回第 0 页。
    """
    cw, ch = cell_size(storage)
    ox, oy = origin()
    cols = storage.columns
    rpp = rows_per_page()
    col = int((x - ox) / max(1, cw)) if x >= ox else -1
    local = int((y - oy) / max(1, ch)) if y >= oy else -1
    local = max(0, min(local, rpp - 1))
    col = max(0, min(col, cols - 1))
    return col, max(0, int(page)) * rpp + local


def entry_pos(storage, entry) -> Cell:
    """v6 §1.3：条目 -> 窗口控件坐标（双模式唯一入口，取代 DesktopWindow
    _entry_pos 里的裸 cell_pos）。

    网格模式 = cell_pos(col, row)；无界模式 = (fx, fy) + 页间滑行偏移
    （与网格同一套页载体语义：row 只决定第几页，位置全自由）。
    无界条目缺 fx/fy（未经 to_free_coords 的历史数据 / 并发新增）时
    按所属页格点懒生成一次——视觉落在它的网格原位，不跳变。
    """
    if not free_mode(storage):
        x, y = cell_pos(storage, int(entry.get("col", 0)),
                        int(entry.get("row", 0)))
        # 反馈 1：widget 小于 cell 时在格内居中（不能贴左上角）
        dx, dy = icon_center_offset(storage, entry)
        return x + dx, y + dy
    if "fx" not in entry or "fy" not in entry:
        entry["fx"], entry["fy"] = _page_local_pos(
            storage, int(entry.get("col", 0) or 0),
            int(entry.get("row", 0) or 0), centered=True)
    page = entry_page(storage, entry)
    x = int(entry.get("fx", 0) or 0)
    y = int(entry.get("fy", 0) or 0)
    dx, dy = page_offset(storage, page)
    return x + dx, y + dy


def widget_pos(storage, entry) -> Cell:
    """v6 §1.3：组件 -> 窗口控件坐标（双模式）。

    锚定组件：网格模式 = 卡片位置（`widget_geom.card_pos`，格盒居中 ×
    填充比例，v10 A8）；无界模式 = (fx,fy) + 页间偏移（与图标同一套页
    载体语义）。悬浮组件两种模式都是 entry 的 x/y（屏幕角落 chrome，
    不参与翻页）。
    """
    if entry.get("anchored"):
        if free_mode(storage):
            page = entry_page(storage, entry)
            if "fx" not in entry or "fy" not in entry:
                # 反馈 3：未接管自动无界时，旧数据按原格点像素懒生成
                # （不能回退 x/y，否则组件跳到窗口左上角）
                entry["fx"], entry["fy"] = cell_pos(
                    storage, int(entry.get("col", 0) or 0),
                    int(entry.get("row", 0) or 0))
            x = int(entry.get("fx", entry.get("x", 0)) or 0)
            y = int(entry.get("fy", entry.get("y", 0)) or 0)
            dx, dy = page_offset(storage, page)
            return x + dx, y + dy
        from . import widget_geom      # 延迟导入：widget_geom 依赖本模块
        span = widget_span(storage, entry)
        return widget_geom.card_pos(storage, int(entry.get("col", 0)),
                                    int(entry.get("row", 0)), span)
    return int(entry.get("x", 0) or 0), int(entry.get("y", 0) or 0)


def to_free_coords(storage) -> int:
    """切无界：为缺 fx/fy 的条目/锚定组件生成页内像素坐标。

    生成式 = 所属页视角的格点（_page_local_pos）——切过去的那一刻
    视觉位置不变（无跳变），之后自由拖拽改 fx/fy。已有 fx/fy 的
    不重算（幂等：反复切换不丢用户摆好的位置）。返回换算个数。
    """
    n = 0
    for e in getattr(storage, "layout", []):
        if "fx" in e and "fy" in e:
            continue
        e["fx"], e["fy"] = _page_local_pos(
            storage, int(e.get("col", 0) or 0),
            int(e.get("row", 0) or 0), centered=True)
        e.setdefault("fpage", page_of(int(e.get("row", 0) or 0)))
        n += 1
    for w in getattr(storage, "widgets", []):
        if not w.get("anchored") or ("fx" in w and "fy" in w):
            continue
        x, y = _page_local_pos(
            storage, int(w.get("col", 0) or 0), int(w.get("row", 0) or 0))
        # v10 A8：组件的无界初值 = **卡片**位（格盒居中 × 填充比例），
        # 否则切过去的一瞬间卡片会向左上跳半个间隙。
        from . import widget_geom
        dx, dy = widget_geom.box_inset(storage, widget_span(storage, w))
        w["fx"], w["fy"] = x + dx, y + dy
        w.setdefault("fpage", page_of(int(w.get("row", 0) or 0)))
        n += 1
    return n


def to_grid_coords(storage) -> int:
    """切回网格：图标**只补缺**、组件**按无界位回算**，一律不删 fx/fy。

    旧实现（v6）按 `fx/fy` 反算吸附回最近格点，然后 `del fx/fy`——两件事
    都是破坏性的：

    1. 用户在无界模式摆好的位置当场销毁（下次进去重新从格点生成）；
    2. 吸附结果覆盖了整理好的 `col/row` → 切回来一堆 cell 叠在一起
       （v6 反馈 13 的截图就是这个）。

    v7 B2 改成"只补缺"，对**图标**是对的：两套布局并存，切回网格就该
    回到网格那一套（用户可能只是去无界临时摆一下）。

    2026-10-21 反馈 3（v7e）：**组件必须例外**。用户原话"从无界切换回
    网格会忘记小组件的坐标位置，导致 cell 重叠"。原因有两层：
      a) `widget_base._save_geom_free` 曾把 `row` 改写成 `page*rpp`
         （已修，改只写 fpage）；
      b) 即便 row 没被冲掉，无界里把组件拖到新位置后，`col/row` 仍是
         **上一次网格布局的旧格**——切回来组件凭空跳回旧位，而那一格
         可能已被图标占用 = "忘记坐标 + cell 重叠"。
    组件是**单实例、无"双布局"语义**的（用户看到的就是屏幕上那一个），
    所以切回网格按它**当前无界像素位**就近吸附，再消解组件间冲突。
    fx/fy 保留：下次进无界仍回到用户摆的像素位（只增不删不变）。

    返回**换算/补算个数**。
    """
    n = 0
    for e in getattr(storage, "layout", []):
        if "col" in e and "row" in e:
            continue
        if "fx" not in e or "fy" not in e:
            continue
        page = entry_page(storage, e)
        col, row = free_pos_cell(storage, int(e.get("fx", 0)),
                                 int(e.get("fy", 0)), page)
        e["col"], e["row"] = col, row
        n += 1
    for w in getattr(storage, "widgets", []):
        if not w.get("anchored"):
            continue
        if "fx" not in w or "fy" not in w:
            continue
        # 此刻 layout_mode 已是 grid，entry_page 会改读 row —— 必须显式
        # 取无界页（fpage），否则第 2 页的组件被吸附到第 0 页。
        page = _fpage_of(w)
        col, row = free_pos_cell(storage, int(w.get("fx", 0)),
                                 int(w.get("fy", 0)), page)
        cs, rs = widget_span(storage, w)
        col, row = clamp_widget_cell(storage, col, row, cs, rs)
        col, row = resolve_widget_cell(storage, col, row, cs, rs,
                                       exclude_id=w.get("id"))
        if (w.get("col"), w.get("row")) != (col, row):
            n += 1
        w["col"], w["row"] = col, row
        w["colspan"], w["rowspan"] = cs, rs
        w["x"], w["y"] = cell_pos(storage, col, row)
    return n


def _fpage_of(entry) -> int:
    """无界页号（缺 fpage 时退回 row 推导）——与 entry_page 的无界分支同源。

    独立出来是因为 to_grid_coords 在 `layout_mode` 已切回 grid **之后**
    才被调用，此时 entry_page 会走网格分支。
    """
    raw = entry.get("fpage")
    if not isinstance(raw, bool):
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    return page_of(int(entry.get("row", 0) or 0))


def extra_pages(storage) -> list:
    """无界模式空白页页码（settings["extra_pages"]，归一化排序去重）。"""
    raw = storage.get_setting("extra_pages") or []
    out = []
    try:
        items = list(raw)
    except TypeError:
        return []
    for v in items:
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n >= 0 and n not in out:
            out.append(n)
    return sorted(out)


def _free_taken(storage, page: int) -> list:
    """无界某页已占的矩形列表 [(x, y, w, h)]（条目 + 锚定组件）。"""
    cw, ch = cell_size(storage)
    out = []
    for e in getattr(storage, "layout", []):
        if entry_page(storage, e) != page:
            continue
        out.append((int(e.get("fx", 0) or 0), int(e.get("fy", 0) or 0),
                    int(e.get("fw", 0) or cw), int(e.get("fh", 0) or ch)))
    for w in getattr(storage, "widgets", []):
        if not w.get("anchored") or w.get("hidden"):
            continue
        if entry_page(storage, w) != page:
            continue
        cs, rs = widget_span(storage, w)
        out.append((int(w.get("fx", 0) or 0), int(w.get("fy", 0) or 0),
                    cs * cw, rs * ch))
    return out


def free_slot(storage, page: int, w: int, h: int) -> Cell:
    """无界模式新条目落点：该页画布内第一个不重叠的格点对齐位。

    扫描 lattice = cell（视觉仍与网格参照系对齐，不吹毛求疵地自由）；
    阅读序。全满回退画布左上角——无界模式允许重叠，不丢条目
    （与网格模式 first_free_cell_in_page 的全满回退同策）。
    """
    cw, ch = cell_size(storage)
    ox, oy = origin()
    cols = storage.columns
    canvas_h = page_canvas_h(storage)
    max_x = ox + cols * cw
    taken = _free_taken(storage, page)
    y = oy
    while y + h <= oy + canvas_h:
        x = ox
        while x + w <= max_x:
            if not any(x < tx + tw and x + w > tx and y < ty + th and y + h > ty
                       for tx, ty, tw, th in taken):
                return x, y
            x += cw
        y += ch
    return ox, oy


def pos_cell(storage, x: int, y: int) -> Cell:
    """像素坐标 -> (col, 全局 row)：列夹紧 [0, cols-1] + 当前页偏移。

    与 cell_pos 互逆（当前页内 round-trip）。局部行夹 [0, rpp-1]：
    落点限当前页（指导文档 §2.3「target 行夹紧在当前页，reflow 不
    跨页」）——拖到屏幕上/下边缘不会把目标落到邻页。

    2026-10-19 v6（F5 测试抓到的产品级缺陷）：格子判定从 round 改为
    **floor**——round 在格子中心（奇数格 1.5 处）会落到下一格
    （round(1.5)=2），指针所在格与判定格不一致；floor 即"指针落在
    哪格算哪格"（与 v6 §5.2 面积判定同向，面积判定留批次 C）。
    """
    cw, ch = cell_size(storage)
    ox, oy = origin()
    cols = storage.columns
    rpp = rows_per_page()
    if page_axis(storage) == PAGE_AXIS_HORIZONTAL:
        # 左右模式：x 只解析页内列（页偏移由 current_page 决定，不参与反算）
        col = int((x - ox) / max(1, cw)) if x >= ox else -1
        local = int((y - oy) / max(1, ch)) if y >= oy else -1
        local = max(0, min(local, rpp - 1))
        col = max(0, min(col, cols - 1))
        return col, current_page() * rpp + local
    col = int((x - ox) / max(1, cw)) if x >= ox else -1
    local = int((y - oy) / max(1, ch)) if y >= oy else -1
    local = max(0, min(local, rows_per_page() - 1))
    col = max(0, min(col, cols - 1))
    return col, current_page() * rows_per_page() + local


def cell_by_overlap(storage, rect) -> Cell:
    """v6 §5.2（反馈 7a）：拖动图标框与候选 cell 的**相交面积最大**者；
    面积并列（含全 0，图标被拖出网格）时取图标**中心点**所在格。

    rect = (x, y, w, h) 窗口控件坐标（与 cell_pos 同坐标系）。
    候选 = 当前页全部单元格（cols × rpp 穷举，常量级——比"只查指针
    邻域"更贴规格字面「所有候选 cell」，且天然覆盖拖出网格的场景）。

    与 pos_cell 的分工：pos_cell 答「指针在哪格」（无拖影的合成 drop /
    外部文件拖入的退化路径），本函数答「图标框压哪格」（松手判定 +
    实时预览落点）。二者在"图标完全在格内"时一致（O6）。
    """
    cols = int(storage.columns)
    cw, ch = cell_size(storage)
    ox, oy = origin()
    rpp = rows_per_page()
    lo, hi = page_row_range(current_page(), rpp, 1)
    x, y, w, h = rect
    best, best_area, ties = None, -1, 1
    for row in range(lo, hi + 1):
        cy = oy + (row - lo) * ch
        for col in range(cols):
            cx = ox + col * cw
            # 相交面积（QRect.intersected 的元组版——本模块零 Qt 依赖）
            iw = min(x + w, cx + cw) - max(x, cx)
            ih = min(y + h, cy + ch) - max(y, cy)
            area = iw * ih if (iw > 0 and ih > 0) else 0
            if area > best_area:
                best, best_area, ties = (col, row), area, 1
            elif area == best_area:
                ties += 1
    if best is None or ties > 1:
        # 并列：中心点所在格（pos_cell 内部夹紧进当前页）
        return pos_cell(storage, x + w // 2, y + h // 2)
    return best


def first_free_cell_in_page(storage, page: int):
    """当前页内第一个空格（列优先扫描；占用 = 图标 + 组件占位）。

    当前页满返回 None（调用方回退全局第一个空格）。供「添加应用」
    类操作把新条目放进用户眼前这一页（指导文档 §7-M6 落点限页）。
    """
    cols = storage.columns
    lo, hi = page_row_range(page, rows_per_page(), 1)
    blocked = set(occupied_cells(storage.layout, cols)) | widget_cells(storage)
    for row in range(lo, hi + 1):
        for col in range(cols):
            if (col, row) not in blocked:
                return col, row
    return None


# ---------- 锚定小组件占位（widget 作为网格一等公民） ----------
def widget_span(storage, widget: dict) -> Cell:
    """widget 占据几列几行。三级解析（2026-10-18 M3，指导文档 §3.2）：

    1. entry 已存 colspan/rowspan（用户在右键「组件尺寸」菜单切换过，
       持久化在 storage.widgets）——优先，保证视觉与存储一致；
    2. WIDGET_CATALOG 登记的默认跨度（clock = 2×2，决策 D3）——
       一等公民，不再 round 像素（银行家舍入 round(300/120)=2、
       round(160/142)=1 的坑根除，碰撞 = 视觉边界）；
    3. 未登记类型回退 legacy 像素换算（至少 1×1），兼容旧数据。
    """
    cs = widget.get("colspan")
    rs = widget.get("rowspan")
    if isinstance(cs, int) and isinstance(rs, int) and cs > 0 and rs > 0:
        return cs, rs
    from . import widget_catalog  # 延迟导入，模块级不建依赖边
    wtype = widget.get("type")
    if isinstance(wtype, str):
        span = widget_catalog.default_span(wtype)
        if span is not None:
            return span
    cw, ch = cell_size(storage)
    w = int(widget.get("w", 260))
    h = int(widget.get("h", 140))
    return max(1, int(round(w / max(1, cw)))), max(1, int(round(h / max(1, ch))))


def widget_cells(storage) -> set:
    """所有可见锚定 widget 占据的格子集合（从 storage.widgets 读，解耦：
    小组件只写 storage，DesktopWindow 直接读，无需实例引用）。

    v6 C2：无 col/row 的锚定组件（spawn 中、尚未分配占位）**不占格**——
    否则 find_widget_slot 把它自己算作占 (0,0)，首组件永远落不到左上角
    （幻影占位：便签被推去 (3,0)）。定位完成即写 col/row，天然只影响
    spawn 窗口内的这一次求解。
    """
    cells = set()
    for w in storage.widgets:
        if not w.get("anchored") or w.get("hidden"):
            continue
        if "col" not in w or "row" not in w:
            continue  # 未定位：无占位（幻影修复）
        col = max(0, int(w.get("col", 0)))
        row = max(0, int(w.get("row", 0)))
        cs, rs = widget_span(storage, w)
        for dc in range(cs):
            for dr in range(rs):
                cells.add((col + dc, row + dr))
    return cells


def find_widget_slot(storage, widget: dict, page: int = None) -> Cell:
    """为锚定 widget 找第一个容得下它的左上空格（排除已有 widget 占位
    与图标占位）。找不到时退回 (0, 0)（极端拥挤场景， displace 会兜底）。

    M6：page 指定时行扫描限制在该页行范围内（page_row_range，组件
    不越页底）；缺省用运行时当前页。
    """
    cs, rs = widget_span(storage, widget)
    blocked = widget_cells(storage)
    for e in storage.layout:
        blocked.add((int(e.get("col", 0)), int(e.get("row", 0))))
    cols = storage.columns
    lo, hi = page_row_range(current_page() if page is None else page,
                            rows_per_page(), rs)
    for row in range(lo, hi + 1):
        for col in range(0, cols - cs + 1):
            rect = {(col + dc, row + dr) for dc in range(cs) for dr in range(rs)}
            if not (rect & blocked):
                return col, row
    return 0, 0
