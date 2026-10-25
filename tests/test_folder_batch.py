# -*- coding: utf-8 -*-
"""v7 反馈批 · 文件夹（反馈 7 / 8 / 14）。

用户 17 条反馈里跟文件夹有关的三条，必须**同批**做（都改弹窗度量）：

  G1  尺寸模型（#14）：默认 3×3、最小 2×2、按数量收缩、优先排满一行；
      右键单设 + 设置页改全局默认
  G2  文件名（#7）：文件夹成员显示文件名（推翻 v7 §7.2「紧凑阵不显示」）
  G3  右上角按钮组（#8）：○ = 文件夹样式、＋ = 快速添加、× = 关闭；
      底部工具条取消；两者各开**独立表单**（2026-10-20 反馈 1）——
      不再内联堆叠、换色不再重排弹窗（成员不重放动画）
  G4  归属唯一（#8）：＋ 的候选只能是当前在桌面上的元素；同一元素不能
      同时属于桌面和多个文件夹

运行：python tests/test_folder_batch.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import QApplication

from src import folder_layout
from src.desktop_window import DesktopWindow
from src.folder_popup import FolderPopup
from src.iconwidget import AppIcon
from src.settings_dialog import SettingsDialog
from src.storage import Storage

W, H = 1920, 1040
STATE = Path(tempfile.gettempdir()) / "test_folder_batch_state.json"
failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def settle(app, ms=120):
    end = QTimer()
    end.setSingleShot(True)
    loop = QEventLoop()
    end.timeout.connect(loop.quit)
    end.start(ms)
    loop.exec()


def make_state(spec):
    if STATE.exists():
        STATE.unlink()
    st = Storage(path=str(STATE))
    st._data["settings"]["hide_native_icons"] = False
    st._data["settings"]["auto_clone_desktop"] = False
    st._data["widgets"] = []
    st._data["layout"] = [
        {"id": i, "type": t,
         "app": {"name": i, "target": i, "kind": "app"}, "apps": apps,
         "col": c, "row": r}
        for i, c, r, t, apps in spec]
    st.save()
    return st


def send_click(w, local):
    g = QPointF(w.pos()) + QPointF(local)
    for etype in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        ev = QMouseEvent(etype, QPointF(local), g, Qt.LeftButton,
                         Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(w, ev)


def menu_texts(menu):
    out = []
    for act in menu.actions():
        out.append("---" if act.isSeparator() else act.text())
        sub = act.menu()
        if sub is not None:
            out.extend("  " + t for t in menu_texts(sub))
    return out


# ---------------------------------------------------------------- G1 纯函数
def test_model():
    check("G1 默认上限 3×3", folder_layout.DEFAULT_FRAME == (3, 3))
    check("G1 2 个成员 -> 2×2（用户例子）",
          folder_layout.grid_shape(2) == (2, 2), "%s" % (folder_layout.grid_shape(2),))
    check("G1 3 个成员 -> 3×2（用户例子）",
          folder_layout.grid_shape(3) == (3, 2), "%s" % (folder_layout.grid_shape(3),))
    check("G1 1 个成员仍是 2×2（最小框）",
          folder_layout.grid_shape(1) == (2, 2))
    check("G1 空文件夹 2×2", folder_layout.grid_shape(0) == (2, 2))
    check("G1 9 个成员铺满 3×3",
          folder_layout.grid_shape(9) == (3, 3))
    # 反馈 3：**满页即封顶**——超过容量不再加行（旧契约 grid_shape(10)==(3,4)
    # 会让弹窗一路长高顶出屏幕），多出来的成员由 page_count/page_items 分页
    check("G1 反馈3 10 个成员封顶 3×3（不再加行）",
          folder_layout.grid_shape(10) == (3, 3),
          "%s" % (folder_layout.grid_shape(10),))
    check("G1 反馈3 20 个成员仍封顶 3×3",
          folder_layout.grid_shape(20) == (3, 3))
    check("G1 反馈3 上限改 2×2 后压到 2 列且封顶",
          folder_layout.grid_shape(9, (2, 2)) == (2, 2),
          "%s" % (folder_layout.grid_shape(9, (2, 2)),))
    check("G1 反馈3 容量 capacity(3,3)=9 / (2,2)=4",
          folder_layout.capacity((3, 3)) == 9
          and folder_layout.capacity((2, 2)) == 4)
    check("G1 反馈3 页数 ceil(n/容量)，空夹也算 1 页",
          folder_layout.page_count(0) == 1
          and folder_layout.page_count(9) == 1
          and folder_layout.page_count(10) == 2
          and folder_layout.page_count(20, (2, 2)) == 5,
          "%s %s" % (folder_layout.page_count(10),
                     folder_layout.page_count(20, (2, 2))))
    check("G1 反馈3 页号夹紧（越界回末页/首页）",
          folder_layout.clamp_page(9, 10) == 1
          and folder_layout.clamp_page(-3, 10) == 0
          and folder_layout.clamp_page(0, 3) == 0)
    check("G1 反馈3 page_items 带全局 offset（payload 用它取成员）",
          folder_layout.page_items(list(range(20)), 1)[1] == 9
          and folder_layout.page_items(list(range(20)), 1)[0] == list(range(9, 18))
          and folder_layout.page_items(list(range(20)), 2)[0] == [18, 19],
          "%s" % (folder_layout.page_items(list(range(20)), 1),))
    check("G1 反馈3 空列表任何页都是空切片",
          folder_layout.page_items([], 3) == ([], 0))
    check("G1 上限改 4×4 后 3 个成员仍优先排满一行",
          folder_layout.grid_shape(3, (4, 4))[0] == 3,
          "%s" % (folder_layout.grid_shape(3, (4, 4)),))
    check("G1 非法上限被夹到 2..6",
          folder_layout.frame_pair([0, 99]) == (2, 6)
          and folder_layout.frame_pair(None) == (3, 3),
          "%s %s" % (folder_layout.frame_pair([0, 99]),
                     folder_layout.frame_pair(None)))


# ---------------------------------------------------------------- G1 存储
def test_storage():
    st = make_state([("f", 0, 0, "folder", [])])
    check("G1 存储默认 3×3", st.folder_frame_default() == (3, 3),
          "%s" % (st.folder_frame_default(),))
    f = st.find_entry("f")
    check("G1 文件夹无覆盖时读全局默认", st.folder_frame(f) == (3, 3))
    check("G1 单文件夹可设尺寸", st.set_folder_frame("f", 4, 3)
          and f.get("frame") == [4, 3], "%s" % (f.get("frame"),))
    check("G1 单文件夹设置立即生效", st.folder_frame(f) == (4, 3))
    st.set_setting("folder_frame_default", [2, 2])
    st.set_folder_frame("f", 2, 2)
    check("G1 与全局默认相同则删掉覆盖键",
          "frame" not in f, "%s" % (f.get("frame"),))
    check("G1 全局默认生效", st.folder_frame(f) == (2, 2))
    check("G1 非文件夹条目不能设尺寸",
          st.set_folder_frame("nope", 3, 3) is False)


# ---------------------------------------------------------------- G2 / G3 弹窗
def test_popup(app):
    st = make_state([
        ("a", 1, 0, "app", []),
        ("b", 2, 0, "app", []),
        ("c", 3, 0, "app", []),
        ("d", 4, 0, "app", []),
        ("f", 5, 0, "folder",
         [{"id": "m1", "name": "m1", "target": "m1", "kind": "app"}]),
        ("g", 6, 0, "folder",
         [{"id": "b", "name": "b", "target": "b", "kind": "app"}]),
    ])
    folder = st.find_entry("f")
    pop = FolderPopup(st, folder, anchor_rect=None)
    pop.show()
    settle(app)

    # G2 文件名
    members = [c for c in pop.findChildren(AppIcon) if c.in_folder]
    check("G2 成员存在", len(members) == 1, "n=%d" % len(members))
    if members:
        ic = members[0]
        check("G2 成员显示文件名（_no_label=False）", ic._no_label is False)
        check("G2 成员格高度含标签行",
              ic.height() > ic.icon_size + 2,
              "h=%d icon=%d" % (ic.height(), ic.icon_size))
        bw, bh = pop._member_box()
        check("G2 弹窗成员格与图标控件同尺寸",
              (ic.width(), ic.height()) == (bw, bh),
              "icon=(%d,%d) box=(%d,%d)" % (ic.width(), ic.height(), bw, bh))

    # G3 右上角按钮组
    hdr = pop._header_rects()
    check("G3 右上角三按钮齐全（× / ＋ / ○）",
          set(hdr) == {"close", "add", "style"}, "%s" % list(hdr))
    rects = list(hdr.values())
    check("G3 三个按钮互不重叠",
          all(not rects[i].intersects(rects[j])
              for i in range(len(rects)) for j in range(i + 1, len(rects))))
    check("G3 三个按钮都在窗口内",
          all(pop.rect().contains(r) for r in rects),
          "%s vs %s" % ([str(r) for r in rects], pop.rect()))
    check("G3 底部工具条已取消", pop._bar_rects() == [])
    check("G3 关闭按钮在组内最右",
          hdr["close"].left() > hdr["add"].left() > hdr["style"].left())

    # ○ 打开**独立**颜色表单（反馈 1：不再内联、弹窗不长高、成员不重排）
    h0, y0, w0 = pop.height(), pop.y(), pop.width()
    send_click(pop, hdr["style"].center())
    settle(app)
    form = pop._tint_form
    check("G3 ○ 打开独立颜色表单", form is not None and form.isVisible(),
          "%r" % (form,))
    check("G3 换色不再改变弹窗尺寸（不再重排成员）",
          (pop.height(), pop.width()) == (h0, w0),
          "h %d->%d w %d->%d" % (h0, pop.height(), w0, pop.width()))
    if form is not None:
        entries = form._entries()
        colors = [c for k, c in entries if isinstance(c, QColor)]
        check("G3 颜色预设 ≥ 12 个（反馈 1：多增加几个预设）",
              len(colors) >= 12, "presets=%d" % len(colors))
        check("G3 颜色表单含「自动」与「自定义」",
              any(k == "auto" for k, _c in entries)
              and any(k == "custom" for k, _c in entries))
        check("G3 颜色表单不覆盖弹窗",
              not form.frameGeometry().intersects(pop.frameGeometry()),
              "%s vs %s" % (form.frameGeometry(), pop.frameGeometry()))
        # 换色不应触发弹窗成员重建：成员控件实例保持不变
        before = [c for c in pop.findChildren(AppIcon)]
        pop._set_tint(QColor(232, 93, 116))
        settle(app)
        after = [c for c in pop.findChildren(AppIcon)]
        check("G3 换色不重建成员图标（不重放动画）",
              len(before) == len(after)
              and all(a is b for a, b in zip(before, after)),
              "before=%d after=%d" % (len(before), len(after)))
    send_click(pop, pop._header_rects()["style"].center())
    settle(app)
    check("G3 再点 ○ 收起颜色表单",
          pop._tint_form is None or not pop._tint_form.isVisible())

    # ＋ 打开独立的「添加应用」表单
    send_click(pop, pop._header_rects()["add"].center())
    settle(app)
    addf = pop._add_form
    check("G3b ＋ 打开独立添加表单", addf is not None and addf.isVisible(),
          "%r" % (addf,))
    if addf is not None:
        check("G3b 添加表单不覆盖弹窗",
              not addf.frameGeometry().intersects(pop.frameGeometry()))
        cells = addf._layout_cache
        check("G3b 添加表单候选格互不重叠",
              all(not cells[i][0].intersects(cells[j][0])
                  for i in range(len(cells)) for j in range(i + 1, len(cells))),
              "cells=%d" % len(cells))
        check("G3b 添加表单候选格都在表单内",
              all(addf.rect().contains(r) for r, _eid in cells),
              "cells=%d rect=%s" % (len(cells), addf.rect()))
    send_click(pop, pop._header_rects()["add"].center())
    settle(app)
    check("G3b 再点 ＋ 收起添加表单",
          pop._add_form is None or not pop._add_form.isVisible())

    # G4 候选唯一性
    cands = [e["id"] for e in pop._add_candidates()]
    check("G4 候选不含任何文件夹的成员（b 在 g 里）", "b" not in cands,
          "%s" % cands)
    check("G4 候选不含本文件夹成员（m1 不在布局里，天然排除）",
          "m1" not in cands, "%s" % cands)
    check("G4 候选只含桌面上的应用条目（无文件夹条目）",
          all(st.find_entry(c).get("type") == "app" for c in cands)
          and "f" not in cands and "g" not in cands, "%s" % cands)
    check("G4 桌面上的普通应用在候选里", {"a", "c", "d"} <= set(cands),
          "%s" % cands)
    # 尝试把一个已经是别人成员的元素塞进来 -> 拒绝
    check("G4 已是其它文件夹成员的元素不能加入",
          pop._add_member_by_id("b") is False)
    check("G4 拒绝后本文件夹成员数不变", len(folder.get("apps", [])) == 1)
    check("G4 桌面元素可加入", pop._add_member_by_id("a") is True
          and len(folder.get("apps", [])) == 2)
    pop.close()

    # 右键菜单：文件夹大小子菜单
    w = DesktopWindow(st)
    w.resize(W, H)
    w._update_grid_metrics()
    settle(app, 150)
    icon = w.icons.get("f")
    check("G4b 文件夹图标已生成", icon is not None)
    if icon is not None:
        # v14u：「文件夹大小」子菜单移入弹窗空白右键（内部尺寸布局）；
        # 瓷砖右键换成「放大（大文件夹）」/「缩小」per-folder 切换
        texts = menu_texts(icon._build_context_menu())
        joined = " | ".join(texts)
        check("G4b 文件夹右键有「放大（大文件夹）」",
              "放大" in joined, joined)
        check("G4b 右键不再有「文件夹大小」子菜单",
              "文件夹大小" not in joined, joined)
    w.close()


# ---------------------------------------------------------------- G1 设置页
def test_settings(app):
    st = make_state([("a", 0, 0, "app", [])])

    class FakeWindow:
        storage = st
        attached = True

        def refresh_grid(self):
            pass

    dlg = SettingsDialog(FakeWindow(), st, layer=None)
    dlg.show()
    settle(app)
    check("G1 设置页有文件夹列数", getattr(dlg, "sp_folder_cols", None) is not None)
    check("G1 设置页有文件夹行数", getattr(dlg, "sp_folder_rows", None) is not None)
    if getattr(dlg, "sp_folder_cols", None) is not None:
        check("G1 设置页默认值 3×3",
              dlg.sp_folder_cols.value() == 3 and dlg.sp_folder_rows.value() == 3,
              "%d×%d" % (dlg.sp_folder_cols.value(), dlg.sp_folder_rows.value()))
        dlg.sp_folder_cols.setValue(4)
        dlg.sp_folder_rows.setValue(4)
        settle(app)
        check("G1 改设置页立即写入全局默认",
              st.folder_frame_default() == (4, 4),
              "%s" % (st.folder_frame_default(),))
    dlg.close()


def main(app):
    test_model()
    test_storage()
    test_popup(app)
    test_settings(app)
    print("-" * 56)
    if failures:
        print("FAILED %d: %s" % (len(failures), failures))
        return 1
    print("ALL folder_batch PASSED")
    return 0


if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    sys.exit(main(app))
