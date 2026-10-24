# -*- coding: utf-8 -*-
"""v14 反馈 1/4/8/11：文件夹悬停展开 · 默认第二页 · 添加表单密度 · 纯净模式。

契约：
  H1  文件夹图标悬停驻留 -> 展开（`_hover_expand_ok` 全放行时）
  H2  非文件夹 / 文件夹内部成员 / 拖动中 / 已按下 / 多选态 -> 不展开
  H3  该文件夹已开着 -> 不触（否则悬停一下反而把它关了）
  H4  设置 `folder_hover_expand=False` -> 不展开
  H5  悬停到点发独立信号 `folderHoverOpened`（点击打开与悬停打开分流）
  H6  悬停打开的弹窗：鼠标离开图标与弹窗 -> 连丢 3 拍自动收起
  H7  鼠标在弹窗内 / 拖动中 -> 不自动关；点击打开的弹窗永不自动关
  P1  首次启动补一页空白页（`ensure_second_page`），页点因此会出现
  P2  已有 2 页以上不补；用户删掉空白页后不再补（一次性标记）
  Z1  双击空白处 -> 纯净模式：图标/Dock/组件全隐，再双击恢复
  Z2  纯净模式不写盘（重启回到正常桌面）
  F1  添加应用表单：单行 4 列、格子更宽、名字两行

跑法：python tests/test_batch2_interactions.py     （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
ROOT = Path(__file__).resolve().parent.parent

from PySide6.QtCore import QPoint, QRect, Qt     # noqa: E402
from PySide6.QtWidgets import QApplication      # noqa: E402
from src import grid_flow                        # noqa: E402
from src import folder_forms                     # noqa: E402
from src.desktop_window import DesktopWindow     # noqa: E402
from src.iconwidget import AppIcon               # noqa: E402
from src.storage import Storage                  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def _folder_entry(eid="f1"):
    return {"id": eid, "type": "folder", "name": "文件夹", "col": 0, "row": 0,
            "apps": [{"id": "a1", "name": "A", "target": "C:\\a", "kind": "app"}]}


def _app_entry(eid="i1"):
    return {"id": eid, "type": "app", "col": 1, "row": 0,
            "app": {"id": "x", "name": "X", "target": "C:\\x", "kind": "file"}}


class _FakeWin:
    """最小宿主：只有 `_hover_expand_ok` 会碰的几个属性。"""

    def __init__(self, storage, popup_entry_id=None, selection=False):
        self.storage = storage
        self._popup = None if popup_entry_id is None else _FakePopup(popup_entry_id)
        self._selection_mode = selection


class _FakePopup:
    def __init__(self, entry_id):
        self.entry = {"id": entry_id}


class _FakePress:
    """最小鼠标事件（只有 mousePressEvent 会碰的三个方法）。"""

    def __init__(self, button, pos):
        self._b, self._p = button, pos

    def button(self):
        return self._b

    def pos(self):
        return self._p

    def globalPos(self):
        return self._p


def main(app):
    tmp = Path(tempfile.mkdtemp(prefix="test_batch2_"))
    st = Storage(path=str(tmp / "state.json"))
    st.save()

    # ---------- H：悬停展开 ----------
    folder_icon = AppIcon(_folder_entry(), 48)
    app_icon = AppIcon(_app_entry(), 48)
    member_icon = AppIcon(_folder_entry("f2"), 48, in_folder=True)

    folder_icon.window = lambda: _FakeWin(st)          # type: ignore[assignment]
    check("H1a 文件夹悬停放行", folder_icon._hover_expand_ok() is True)
    check("H1b 普通应用不展开", app_icon._hover_expand_ok() is False)
    app_icon.window = lambda: _FakeWin(st)             # type: ignore[assignment]
    check("H1c 普通应用（有宿主）仍不展开", app_icon._hover_expand_ok() is False)
    member_icon.window = lambda: _FakeWin(st)          # type: ignore[assignment]
    check("H1d 文件夹内部成员不展开", member_icon._hover_expand_ok() is False)

    folder_icon._press_pos = folder_icon.pos()          # 按下中
    check("H2a 按下中不展开", folder_icon._hover_expand_ok() is False)
    folder_icon._press_pos = None
    folder_icon._drag_loop = True
    check("H2b 拖动中不展开", folder_icon._hover_expand_ok() is False)
    folder_icon._drag_loop = False

    folder_icon.window = lambda: _FakeWin(st, selection=True)   # type: ignore[assignment]
    check("H2c 多选态不展开", folder_icon._hover_expand_ok() is False)
    folder_icon.window = lambda: _FakeWin(st, popup_entry_id="f1")  # type: ignore[assignment]
    check("H3 已开着的文件夹不触（否则悬停反而关掉）",
          folder_icon._hover_expand_ok() is False)
    folder_icon.window = lambda: _FakeWin(st, popup_entry_id="other")  # type: ignore[assignment]
    check("H3b 别的小组件开着不影响本文件夹", folder_icon._hover_expand_ok() is True)

    st.set_setting("folder_hover_expand", False)
    folder_icon.window = lambda: _FakeWin(st)          # type: ignore[assignment]
    check("H4 设置关掉后不展开", folder_icon._hover_expand_ok() is False)
    st.set_setting("folder_hover_expand", True)
    check("H4b 设置打开后恢复", folder_icon._hover_expand_ok() is True)

    # 驻留计时器：到点才展开，离开即撤
    fired = []
    folder_icon.folderHoverOpened.connect(lambda eid: fired.append(eid))
    folder_icon.enterEvent(None)
    check("H5 enter 即起表", folder_icon._hover_open.isActive())
    folder_icon.leaveEvent(None)
    check("H5b leave 撤表", not folder_icon._hover_open.isActive())
    folder_icon.enterEvent(None)
    folder_icon.mousePressEvent(_FakePress(Qt.LeftButton, folder_icon.pos()))
    check("H5c 按下撤表", not folder_icon._hover_open.isActive())

    # 悬停到点走独立信号：点击打开的弹窗不该被"离开即关"误伤
    folder_icon._press_pos = None          # H5c 按下留下的状态要清
    folder_icon._drag_loop = False
    folder_icon.window = lambda: _FakeWin(st)      # type: ignore[assignment]
    folder_icon._on_hover_open()
    check("H5d 悬停展开发 folderHoverOpened（不是 folderClicked）",
          fired == ["f1"], str(fired))

    # ---------- H6 / H7：悬停展开的自动关闭 ----------
    from src import folder_popup as fpmod

    class _Cursor:
        pt = None

        @staticmethod
        def pos():
            return _Cursor.pt

    st_h = Storage(path=str(tmp / "hover.json"))
    st_h.layout.append({"id": "hf", "type": "folder", "col": 0, "row": 0,
                        "page": 0, "name": "HF",
                        "app": {"name": "HF", "target": "HF", "kind": "app"},
                        "apps": [{"name": "m1", "target": "m1",
                                  "kind": "app"}]})
    st_h.save()
    popup = fpmod.FolderPopup(st_h, st_h.find_entry("hf"), anchor_rect=None)
    popup.resize(300, 220)
    orig_cursor = fpmod.QCursor
    fpmod.QCursor = _Cursor
    closed = []
    orig_close = popup.close
    popup.close = lambda: closed.append(1)      # type: ignore[assignment]
    try:
        popup.set_opened_by_hover(True)
        check("H6a 悬停打开 -> 自动关闭定时器启动",
              popup._auto_close.isActive())
        _Cursor.pt = QPoint(5, 5)               # 屏幕左上角：既不在图标也不在弹窗
        for _ in range(3):
            popup._auto_close_tick()
        check("H6b 连丢 3 拍 -> 自动关闭", len(closed) == 1, str(len(closed)))

        closed.clear()
        _Cursor.pt = popup.frameGeometry().center()
        for _ in range(5):
            popup._auto_close_tick()
        check("H6c 鼠标在弹窗内 -> 不关", not closed, str(len(closed)))

        # 锚点图标矩形内也不关
        closed.clear()
        popup._anchor_rect = QRect(popup.x() - 200, popup.y(), 80, 80)
        _Cursor.pt = popup._anchor_rect.center()
        for _ in range(5):
            popup._auto_close_tick()
        check("H6d 鼠标在文件夹图标上 -> 不关", not closed, str(len(closed)))

        # 拖动中不关（弹窗里把成员拖出去时鼠标必然离开弹窗）
        closed.clear()
        popup._dragging = True
        _Cursor.pt = QPoint(5, 5)
        for _ in range(5):
            popup._auto_close_tick()
        check("H7a 拖动成员时不自动关", not closed, str(len(closed)))
        popup._dragging = False

        # 点击打开的弹窗不自动关
        closed.clear()
        popup.set_opened_by_hover(False)
        check("H7b 点击打开 -> 定时器不启动", not popup._auto_close.isActive())
        for _ in range(5):
            popup._auto_close_tick()
        check("H7c 点击打开的弹窗永不自动关", not closed, str(len(closed)))
    finally:
        popup.close = orig_close                     # type: ignore[assignment]
        fpmod.QCursor = orig_cursor
        popup.close()

    # 集成：open_folder(by_hover=True) 真的把标记传给弹窗
    st_o = Storage(path=str(tmp / "open.json"))
    st_o.layout.append({"id": "fo", "type": "folder", "col": 0, "row": 0,
                        "page": 0, "name": "FO",
                        "app": {"name": "FO", "target": "FO", "kind": "app"},
                        "apps": [{"name": "m1", "target": "m1",
                                  "kind": "app"}]})
    st_o.save()
    win = DesktopWindow(st_o)
    win.open_folder("fo", by_hover=True)
    check("H7d 悬停打开时弹窗带标记",
          win._popup is not None and win._popup._opened_by_hover is True)
    win._close_popup()
    win.open_folder("fo")
    check("H7e 点击打开时无标记（不自动关）",
          win._popup is not None and win._popup._opened_by_hover is False)
    win._close_popup()
    win.close()

    # ---------- P：默认第二页 ----------
    st2 = Storage(path=str(tmp / "s2.json"))
    st2.save()
    check("P1a 初始只有一页", grid_flow.page_count(st2) == 1,
          str(grid_flow.page_count(st2)))
    st2.ensure_desktop_clone()
    # 旧断言 extra_pages==[1]（第二页必须空白）只在"本机桌面图标 ≤ 一页"
    # 时成立：图标已铺满两页的机器上 ensure_second_page 按"保证至少 2 页"
    # 的契约不补空白页（storage.ensure_second_page 文档语义），测试就变成
    # 了机器相关。这里改为锁契约本身：≥2 页、且播种后页数稳定（幂等）。
    pages1 = grid_flow.page_count(st2)
    check("P1b 播种后至少两页", pages1 >= 2, str(pages1))
    st2.ensure_desktop_clone()
    check("P1c 幂等（不反复补页）", grid_flow.page_count(st2) == pages1,
          str(grid_flow.page_count(st2)))
    check("P2a 幂等（不反复补）", grid_flow.page_count(st2) == pages1)
    st2.set_setting("extra_pages", [])
    st2.ensure_desktop_clone()
    check("P2b 用户删掉空白页后不再补", grid_flow.extra_pages(st2) == [],
          str(grid_flow.extra_pages(st2)))

    # ---------- Z：纯净模式 ----------
    st3 = Storage(path=str(tmp / "s3.json"))
    st3.ensure_desktop_clone()
    win = DesktopWindow(st3)
    n_icons = len(win.icons)
    check("Z0 桌面有图标", n_icons > 0, str(n_icons))
    win.toggle_zen()
    check("Z1a 纯净模式开：图标全隐",
          all(i.isHidden() for i in win.icons.values()))
    check("Z1b 纯净模式开：Dock 隐",
          getattr(win, "_dock", None) is None or win._dock.isHidden())
    check("Z1c 纯净模式不写盘",
          "zen" not in json_dump(st3.path), "")
    win.toggle_zen()
    # 旧断言"图标全显"只在单页桌面成立：多页机器上退出纯净后按分页规则
    # 其它页本就该隐藏（_cull_page_icons 的"禁半截"契约）。锁真实契约：
    # 当前页图标全显、其它页隐藏。
    def _zen_exit_ok():
        cur = grid_flow.current_page()
        for eid, ic in win.icons.items():
            e = st3.find_entry(eid)
            if e is None:
                continue
            want = grid_flow.entry_page(st3, e) == cur
            if want and ic.isHidden():
                return False
            if not want and ic.isVisible():
                return False
        return True
    check("Z2a 再触发恢复：按分页规则显隐（当前页全显）", _zen_exit_ok())
    check("Z2b 恢复后仍在第 0 页且页点可画",
          win._zen is False and len(win._dot_centers()) >= 2)

    # ---------- F：添加表单密度 ----------
    form = folder_forms.AddAppsForm(st3, [])
    check("F1 单行 4 列（不再 5 列硬挤）", form.COLS == 4, str(form.COLS))
    _icon, cw, _ch = form._cell()
    check("F2 格子更宽（>=60）", cw >= 60, str(cw))
    from PySide6.QtGui import QFontMetrics
    fm = QFontMetrics(app.font())
    lines = folder_forms._wrap_lines("这是一个很长的中文应用名称需要折行", fm, 58, 2)
    check("F3 名字两行", len(lines) == 2, str(lines))
    check("F4 末行省略（内容放不下时）",
          folder_forms._wrap_lines(
              "超级无敌长的中文应用名称一二三四五六七八九十十一十二十三十四十五十六十七",
              fm, 58, 2)[-1].endswith("…"))


def json_dump(path):
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        return "zen" if "\"zen" in raw else ""
    except Exception:      # noqa: BLE001
        return ""


if __name__ == "__main__":
    app = QApplication(sys.argv)
    main(app)
    print("----")
    print("failures=%d" % len(failures))
    sys.exit(1 if failures else 0)
