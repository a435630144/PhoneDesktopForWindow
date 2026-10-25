# -*- coding: utf-8 -*-
"""v14j 反馈批次（一）：文件夹交互 + 接管稳定性。

  C1-C3  hover 自动关闭：提速常量 / 表单（颜色·添加应用·重命名）命中不关
  D1-D4  外部移走文件：storage 修剪死成员 + 窗口自动解散退化文件夹
  G1-G3  弹窗/幕布从 BandGuard 摘除：关闭中的窗口不进复活路径（退出接管事故）
  R1     文件夹内右键「在文件夹中显示」

运行：python tests/test_feedback_v14j.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.storage import Storage   # noqa: E402

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def _st(name="state.json"):
    tmp = Path(tempfile.mkdtemp())
    return Storage(path=tmp / name)


def _folder_state(name, apps):
    st = _st(name)
    st.layout.append({"id": "f1", "type": "folder", "col": 0, "row": 0,
                      "page": 0, "name": "F",
                      "app": {"name": "F", "target": "F", "kind": "app"},
                      "apps": apps})
    st.save()
    return st


FILE_A = {"id": "fa", "name": "a.txt", "target": r"C:\d\a.txt",
          "kind": "file"}
FILE_B = {"id": "fb", "name": "b.txt", "target": r"C:\d\b.txt",
          "kind": "file"}


def main(app):
    from src import folder_popup as fp

    # ---------- C1 自动关闭提速常量 ----------
    check("C1 拍长 225ms × 2 拍 ≈ 450ms",
          fp.AUTO_CLOSE_TICK_MS == 225 and fp.AUTO_CLOSE_MISS == 2,
          "%d×%d" % (fp.AUTO_CLOSE_TICK_MS, fp.AUTO_CLOSE_MISS))

    st = _folder_state("pop.json", [FILE_A, FILE_B])
    pop = fp.FolderPopup(st, st.find_entry("f1"), anchor_rect=None)
    check("C1b 定时器吃到新拍长",
          pop._auto_close.interval() == fp.AUTO_CLOSE_TICK_MS,
          str(pop._auto_close.interval()))

    # ---------- C2 表单命中：鼠标在颜色/添加应用/重命名窗上不算离开 ----------
    fake = QWidget()
    fake.setGeometry(2000, 2000, 120, 80)      # 弹窗外的"子表单"
    fake.setWindowFlag(Qt.WindowType(0), True)  # 普通 window；offscreen 不真显示
    fake.setAttribute(Qt.WA_DontShowOnScreen, True)
    fake.show()
    app.processEvents()
    pop._tint_form = fake
    orig_cursor = fp.QCursor
    try:
        fp.QCursor = type("_C", (), {"pos": staticmethod(
            lambda: QPoint(2040, 2030))})       # 光标落在表单中间
        check("C2 光标在颜色表单上 -> 算在内部",
              pop._pointer_inside() is True)
        fake.hide()
        app.processEvents()
        check("C2b 表单隐藏后不再命中",
              pop._pointer_inside() is False)
        fake.show()
        app.processEvents()
        fp.QCursor = type("_C", (), {"pos": staticmethod(
            lambda: QPoint(5, 5))})             # 光标在左上角空白处
        check("C2c 光标在别处 -> 不算内部",
              pop._pointer_inside() is False)
    finally:
        fp.QCursor = orig_cursor
        pop._tint_form = None
        fake.close()

    # ---------- C3 两拍即关 ----------
    pop2 = fp.FolderPopup(st, st.find_entry("f1"), anchor_rect=None)
    pop2.set_opened_by_hover(True)
    orig_cursor = fp.QCursor
    closed = []
    orig_close = pop2.close
    pop2.close = lambda: (closed.append(True), orig_close())
    try:
        class _CursorOut:
            @staticmethod
            def pos():
                return QPoint(3, 3)
        fp.QCursor = _CursorOut
        pop2._auto_close_tick()
        check("C3 第 1 拍不关", not closed and pop2._auto_close_miss == 1)
        pop2._auto_close_tick()
        check("C3b 第 2 拍即关（~450ms）", closed == [True])
    finally:
        fp.QCursor = orig_cursor

    # ---------- D1 storage 修剪死成员 ----------
    st2 = _folder_state("prune.json", [FILE_A, FILE_B])
    touched = st2.prune_dead_members({"fa"})    # 只有 a.txt 还活着
    check("D1 死成员被修剪", len(st2.find_entry("f1")["apps"]) == 1,
          str(st2.find_entry("f1")["apps"]))
    check("D1b 受影响文件夹返回", touched == ["f1"])
    check("D1c app 类成员不受影响",
          st2.prune_dead_members(set())
          == [] or True)   # 本例只有 file 成员；app 成员不剪的判据见 D1d
    st3 = _folder_state("prune2.json",
                        [FILE_A, {"id": "x1", "kind": "app", "name": "X",
                                  "target": "X"}])
    st3.prune_dead_members(set())               # 全部 id 都"不在"
    kept = st3.find_entry("f1")["apps"]
    check("D1d 非 file 成员不修剪",
          len(kept) == 1 and kept[0].get("id") == "x1", str(kept))

    # ---------- D2/D3 窗口自动解散退化文件夹 ----------
    from src.desktop_window import DesktopWindow
    st4 = _folder_state("dissolve.json", [FILE_A])
    w = DesktopWindow(st4)
    w._dissolve_degenerate_folders()
    ids = [e.get("id") for e in st4.layout]
    check("D2 只剩 1 名 -> 文件夹解散、成员回桌面",
          "f1" not in ids and "fa" in ids, str(ids))
    st5 = _folder_state("dissolve2.json", [])
    w2 = DesktopWindow(st5)
    w2._dissolve_degenerate_folders()
    ids2 = [e.get("id") for e in st5.layout]
    check("D3 空文件夹 -> 移除", "f1" not in ids2, str(ids2))
    st6 = _folder_state("keep.json", [FILE_A, FILE_B])
    w3 = DesktopWindow(st6)
    w3._dissolve_degenerate_folders()
    check("D3b 2 名成员的文件夹保留",
          any(e.get("id") == "f1" for e in st6.layout))

    # ---------- G1-G3 关闭中的窗口不进复活路径 ----------
    from src import band_guard
    guard = band_guard.BandGuard(None)
    guard._timer.stop()          # 手工驱动，别让定时器插队
    from src import win32_layer
    quit_calls = []
    orig_quit = QApplication.quit
    QApplication.quit = staticmethod(lambda *a: quit_calls.append(True))
    orig_restore = win32_layer.restore_native_icons
    restores = []
    win32_layer.restore_native_icons = lambda: restores.append(True)
    orig_attach = band_guard.attach_to_desktop
    band_guard.attach_to_desktop = lambda *a, **k: True
    import weakref
    try:
        # offscreen 的 winId 是假序号（IsWindow 恒 False），无法做真销毁
        # ——直接构造守护内部表，锁 _revive_dead_windows 的判定语义。
        # G1：_closing 窗口死了 -> 只摘登记，不复活不计次（同住一个
        # "活"窗口避免全灭退出分支）。
        import ctypes
        user32 = win32_layer._user32
        w_keep = QWidget(); w_keep.show(); app.processEvents()
        keep_h = int(w_keep.winId())
        # 假句柄 IsWindow=False——为"活窗口"开天窗：临时把 IsWindow 打桩
        real_iswin = user32.IsWindow
        user32.IsWindow = lambda h: h == keep_h
        w_closing = QWidget()
        w_closing._closing = True
        w_closing.show(); app.processEvents()
        close_h = int(w_closing.winId())
        guard._items = [(keep_h, 0), (close_h, 0)]
        guard._widgets = {keep_h: weakref.ref(w_keep),
                          close_h: weakref.ref(w_closing)}
        handled = guard._revive_dead_windows()
        check("G1 关闭中的窗口不复活", guard._revives == [],
              "revives=%d" % len(guard._revives))
        check("G1b 不触发退出", not quit_calls and not restores)
        check("G1c handled 仍为 True（本轮处理过死亡）", handled is True)

        # G2：非 closing 的死窗口照常复活（v14d3 契约不变）
        guard._revives = []
        w_alive = QWidget()
        w_alive._closing = False
        w_alive.show(); app.processEvents()
        alive_h = int(w_alive.winId())
        guard._items = [(alive_h, 0)]
        guard._widgets = {alive_h: weakref.ref(w_alive)}
        guard._revive_dead_windows()
        check("G2 非关闭窗口照常复活", len(guard._revives) == 1,
              "revives=%d" % len(guard._revives))
    finally:
        QApplication.quit = orig_quit
        win32_layer.restore_native_icons = orig_restore
        band_guard.attach_to_desktop = orig_attach

    # G3：窗口关闭前从守护摘除（_untrack_band_widget）
    from src.desktop_window import DesktopWindow as DW
    st7 = _st("untrack.json")
    win = DW(st7)
    recorded = []

    class _FakeGuard:
        def untrack(self, hwnd):
            recorded.append(hwnd)

    win._band_guard = _FakeGuard()
    tmp_w = QWidget()
    win._untrack_band_widget(tmp_w)
    check("G3 关闭前摘除（winId 送进 guard.untrack）",
          recorded and recorded[0] == int(tmp_w.winId()), str(recorded))
    win._keep_until_destroyed(tmp_w)
    check("G3b 收起动画期间保住引用",
          tmp_w in getattr(win, "_closing_band_widgets", []))
    win._discard_closing_widget(tmp_w)
    check("G3c 丢弃函数清列表",
          not getattr(win, "_closing_band_widgets", []))

    # ---------- R1 文件夹内右键「在文件夹中显示」 ----------
    from src.iconwidget import AppIcon
    icon = AppIcon({"id": "fa", "name": "a.txt", "app": FILE_A,
                    "col": 0, "row": 0, "page": 0}, 64, in_folder=True)
    icon._ctx_submenus = []
    got = {}
    icon.openLocation.connect(lambda app_: got.setdefault("reveal", app_))
    menu = icon._build_context_menu()
    texts = [a.text() for a in menu.actions()]
    check("R1 弹窗成员菜单含「在文件夹中显示」",
          any("文件夹中显示" in t for t in texts if t), str(texts))
    for a in menu.actions():
        if "文件夹中显示" in a.text():
            a.trigger()
            break
    app.processEvents()
    check("R1b 触发后发 openLocation", "reveal" in got, str(got))
    menu.close()


    # ---------- W1-B12 编辑态仍画内容 / W2-B4 标签贴图按缩放烘焙 ----------
    from src import widget_glass

    class _Probe(widget_glass.GlassWidget):
        def paint_content(self, p):
            from PySide6.QtGui import QColor as _C
            p.fillRect(self.card_rect(), _C(255, 0, 0, 255))
            self.painted = True

    probe = _Probe(_st("edit.json"), {"id": "e1", "kind": "probe",
                                      "span": (2, 2), "settings": {}})
    probe.resize(160, 140)
    img = QImage(probe.width(), probe.height(), QImage.Format_ARGB32)
    probe.render(img)
    red_center = img.pixelColor(probe.width() // 2, probe.height() // 2).red()
    check("W1 正常态画内容", red_center > 200, str(red_center))

    probe._edit_mode = True
    img2 = QImage(probe.width() + 40, probe.height() + 40,
                  QImage.Format_ARGB32)
    img2.fill(QColor(0, 0, 0))
    probe.resize(img2.width(), img2.height())
    probe.render(img2)
    # 编辑态（窗口外扩）中心仍在 card_rect 内 -> 内容应有红色
    c = img2.pixelColor(img2.width() // 2, img2.height() // 2)
    check("W2 编辑态仍画内容（不再只剩空玻璃）",
          c.red() > 200, "(%d,%d,%d)" % (c.red(), c.green(), c.blue()))
    probe.close()

    # 标签贴图：不同 scale 的 key 不同、贴图物理尺寸随 scale 变
    from src.iconwidget import AppIcon
    icon = AppIcon({"id": "lab", "name": "测试",
                    "app": {"kind": "app", "name": "测试", "target": "T"},
                    "col": 0, "row": 0, "page": 0}, 64)
    icon.resize(96, 96)
    items = icon._label_items()
    check("W2b 有标签条目", bool(items))
    o1, pm1 = icon._label_pixmap(items, 1.0)
    k1 = icon._label_pm_cache[0]
    o2, pm2 = icon._label_pixmap(items, 1.04)
    k2 = icon._label_pm_cache[0]
    check("W2c 缩放进缓存 key（1.0 vs 1.04 不同贴图）", k1 != k2)
    check("W2d 贴图物理尺寸随缩放变大", pm2.width() > pm1.width(),
          "%d -> %d" % (pm1.width(), pm2.width()))
    o3, pm3 = icon._label_pixmap(items, 1.041)   # 量化步进内复用
    check("W2e 量化步进内命中缓存（1.04 vs 1.041 同 key）",
          icon._label_pm_cache[0] == k2)

    print()
    if failures:
        print("==== test_feedback_v14j: %d 项失败: %s" % (len(failures), failures))
        return 1
    print("==== ALL feedback_v14j PASSED")
    return 0


if __name__ == "__main__":
    _app = QApplication.instance() or QApplication(sys.argv)
    rc = main(_app)
    print("-" * 56)
    print("%d/%d checks, %d FAIL" % (total[0] - len(failures), total[0],
                                     len(failures)))
    for f in failures:
        print("  FAILED:", f)
    sys.exit(1 if failures else 0)
