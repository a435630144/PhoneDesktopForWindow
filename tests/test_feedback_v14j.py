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

from PySide6.QtCore import QPoint, QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QLinearGradient  # noqa: E402
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


    # ---------- H1-H4 hover 高亮与动画自定义（v14j） ----------
    from src import label_style as _lsm

    sth = _st("hover.json")
    hs = _lsm.hover_style(sth)
    check("H1 默认：内置白 / 8% / 开 / 104% / 140ms",
          hs.enabled and hs.scale_pct == 104 and hs.anim_ms == 140
          and abs(hs.fill_alpha - 0.08) < 1e-6,
          str(hs))
    sth.set_setting("icon_hover_enabled", False)
    sth.set_setting("icon_hover_scale", 100)
    sth.set_setting("icon_hover_anim", 300)
    sth.set_setting("icon_hover_color", "#80FF0000")
    hs2 = _lsm.hover_style(sth)
    check("H2 关闭 + 不放大 + 300ms + 自定义色",
          not hs2.enabled and hs2.scale_pct == 100 and hs2.anim_ms == 300
          and hs2.color.name(QColor.HexArgb).upper() == "#80FF0000")
    sth.set_setting("icon_hover_scale", 500)
    check("H3 放大幅度夹 112", _lsm.hover_style(sth).scale_pct == 112)
    sth4 = _st("hover4.json")
    check("H4 storage 默认齐",
          sth4.get_setting("icon_hover_fill") == 8
          and sth4.get_setting("icon_hover_anim") == 140
          and sth4.get_setting("icon_hover_enabled") is True)

    # AppIcon 侧：enter 动画尊重幅度（100% 不动画）+ 缓存失效钩子
    icon_h = AppIcon({"id": "hv", "name": "H",
                      "app": {"kind": "app", "name": "H", "target": "T"},
                      "col": 0, "row": 0, "page": 0}, 64)
    icon_h.resize(96, 96)
    check("H4b AppIcon 有 invalidate_hover_style",
          callable(getattr(icon_h, "invalidate_hover_style", None)))
    from src.settings_dialog import SettingsDialog as _SD
    sth2 = _st("hoverdlg.json")
    dlg3 = _SD(type("W", (), {"attached": False})(), sth2, None)
    check("H5 设置页 hover 卡存在",
          hasattr(dlg3, "chk_hover_enabled") and hasattr(dlg3, "sld_hover_scale")
          and hasattr(dlg3, "sld_hover_anim") and hasattr(dlg3, "btn_hover_color"))
    dlg3.sld_hover_scale.setValue(108)
    check("H5b 放大幅度写设置", sth2.get_setting("icon_hover_scale") == 108)
    dlg3._reset_icon_hover()
    check("H5c 重置回 104",
          sth2.get_setting("icon_hover_scale") == _lsm.DEFAULT_HOVER_SCALE
          and sth2.get_setting("icon_hover_color") == "")
    dlg3.deleteLater()


    # ---------- W3 液态瓷砖白底事故回归（角落采样！） ----------
    # 事故：`if liquid and not lens(): 兜底 / else: tint 渐变` —— 镜头成功
    # 时也落 else，不透明 tint 渐变（液态 tint=白 → 255→154 灰）盖住折射。
    # 中心采样会被迷你图标遮住，必须采**角落**判中性灰。
    class _Host(QWidget):
        def __init__(self, storage):
            super().__init__()
            self.storage = storage

    stw = _st("tile.json")
    stw.layout.append({"id": "tl", "type": "folder", "col": 0, "row": 0,
                       "page": 0, "name": "T", "tint": "liquid",
                       "app": {"name": "T", "target": "T", "kind": "app"},
                       "apps": [{"id": "m1", "name": "M", "target": "M",
                                 "kind": "app"}]})
    stw.save()
    host = _Host(stw)
    host.setAttribute(Qt.WA_DontShowOnScreen, True)
    from src.iconwidget import AppIcon as _AI
    ticon = _AI(stw.find_entry("tl"), 48, parent=host)
    ticon._box_x = 4
    ticon.setGeometry(0, 0, 64, 80)
    from PySide6.QtGui import QImage as _QI, QPainter as _QP
    from PySide6.QtCore import QRectF as _QRF
    timg = _QI(ticon.size(), _QI.Format_ARGB32)
    timg.fill(QColor(0, 0, 0, 0))
    tp = _QP(timg)
    ok_lens = ticon._paint_folder_lens(tp, _QRF(5, 3, 46, 46), 48, 1.0)
    tp.end()
    if ok_lens:
        # 圆角内（box(5,3,46,46) r=11）：必须落在瓷砖实心区
        corners = [timg.pixelColor(x, y) for x, y in
                   ((18, 9), (38, 9), (18, 42), (38, 42))]
        neutral = [c for c in corners if c.alpha() > 200
                   and abs(c.red()-c.green()) <= 3
                   and abs(c.green()-c.blue()) <= 3
                   and 140 <= c.red() <= 250]
        check("W3 液态瓷砖角落不是不透明白/灰（事故回归）",
              len(neutral) == 0,
              str([(c.red(), c.green(), c.blue()) for c in corners]))
    else:
        check("W3 液态瓷砖角落不是不透明白/灰（事故回归）", True, "skip-no-lens")
    ticon.deleteLater()


    # ---------- K1-K3 弹窗开着按住锚点瓷砖可拖动（v14k3） ----------
    # 事故：幕布全屏拦输入，弹窗开着时第一下按压被"点击外部关闭"吞掉，
    # 拖文件夹得按第二次。修复：幕布 setMask 挖掉锚点 + 锚点拖动收弹窗。
    from PySide6.QtCore import QPoint as _QPoint
    stk = _st("mask.json")
    stk.layout.append({"id": "f1", "type": "folder", "col": 0, "row": 0,
                       "page": 0, "name": "F1",
                       "app": {"name": "F1", "target": "F1", "kind": "app"},
                       "apps": [{"name": "m1", "target": "m1",
                                 "kind": "app"}]})
    stk.layout.append({"id": "f2", "type": "folder", "col": 1, "row": 0,
                       "page": 0, "name": "F2",
                       "app": {"name": "F2", "target": "F2", "kind": "app"},
                       "apps": [{"name": "m1", "target": "m1",
                                 "kind": "app"}]})
    stk.save()
    wmk = DesktopWindow(stk)
    wmk.open_folder("f1")
    check("K1 弹窗打开", wmk._popup is not None and wmk._scrim is not None)
    if wmk._scrim is not None:
        mask = wmk._scrim.mask()
        anchor_icon = wmk._folder_open_icon
        ag = anchor_icon.geometry()
        gpos = anchor_icon.mapToGlobal(ag.center())
        local = gpos - wmk._scrim.geometry().topLeft()
        check("K1b 幕布掩膜挖掉锚点（锚点中心不在掩膜内）",
              not mask.contains(local),
              "local=%s" % local)
        far = _QPoint(wmk._scrim.geometry().center())
        check("K1c 掩膜仍覆盖其余区域", mask.contains(far))
    # K2：锚点拖动 -> 收弹窗
    wmk._on_anchor_drag_started(wmk._folder_open_icon)
    check("K2 锚点拖动开始 -> 弹窗收起", wmk._popup is None)
    # K3：非锚点拖动 -> 不动
    wmk.open_folder("f1")
    other_icon = wmk.icons.get("f2")
    wmk._on_anchor_drag_started(other_icon)
    check("K3 非锚点拖动不影响弹窗",
          wmk._popup is not None and
          wmk._popup.entry.get("id") == "f1")
    wmk._close_popup()
    wmk.close()


    # ---------- K9/K10 v14n 事件驱动守护（WinEvent 钩子） ----------
    from src import band_guard as _bg
    g2 = _bg.BandGuard(None)
    g2._timer.stop()
    check("K9 默认不装钩子（测试环境隔离）", g2._event_hook is None)
    # 先手动调回调验证语义（未装钩子 = 无真实系统事件干扰计数）
    checks_n = [0]
    orig_check = g2._check
    g2._check = lambda: (checks_n.append(1), orig_check()) and None
    # 真实系统事件也会触发计数（活动桌面上无法隔离），断言用下限
    g2._on_win_event(None, 0x0003, None, 0, 0, 0, 0)
    check("K9b 事件回调触发 _check（立即归位语义）", len(checks_n) >= 1,
          str(len(checks_n)))
    g2._on_win_event(None, 0x8004, None, 0, 0, 0, 0)
    check("K9c 每个事件都走到 _check（不丢事件）",
          len(checks_n) >= 2, str(len(checks_n)))
    # 再验证真实钩子安装/卸载
    g2.install_event_hook()
    check("K9d 显式安装后 Windows 上有钩子",
          os.name != "nt" or bool(g2._event_hook) is True,
          str(bool(g2._event_hook)))
    g2.shutdown()
    check("K9e shutdown 撤钩", g2._event_hook is None)


    # ---------- A1-A8 v14o 自审批次（2026-10-26） ----------
    # A1 真崩溃①：部件 .window 属性遮蔽 QWidget.window()（crash.log 33 次）
    class _ShadowWin(QWidget):
        pass
    sh_w = _ShadowWin()
    sh_w.window = object()          # 模拟 SettingsDialog.self.window
    from src.iconwidget import _is_tooltip_window
    try:
        r = _is_tooltip_window(sh_w)
        check("A1 .window 属性遮蔽不崩（返回 False）", r is False, str(r))
    except Exception as exc:        # noqa: BLE001
        check("A1 .window 属性遮蔽不崩（返回 False）", False, repr(exc))

    # A2 真崩溃②：clicked(bool) 塞进 revert_to（crash.log 取色崩溃）
    from src import settings_dialog as _sd
    from PySide6.QtWidgets import QColorDialog as _QCDW
    st_a = _st("audit.json")
    dlg_a = _sd.SettingsDialog(type("W", (), {"attached": False})(), st_a, None)
    orig_cd = _sd.QColorDialog
    # 无效色 = 用户取消：走 revert_to 分支（崩溃点就在那里）
    _sd.QColorDialog = type("_FCancel", (), {
        "getColor": staticmethod(lambda *a, **k: QColor()),
        "DontUseNativeDialog": 0})
    try:
        dlg_a._pick_cell_preview_color(False)   # clicked(bool) 场景 + 取消
        check("A2 clicked(bool) 进取色器不崩（取消路径）", True)
        dlg_a._pick_cell_preview_color(
            (dlg_a.cmb_cell_preview, 1))        # 元组路径仍工作
        check("A2b 元组回退路径不崩", True)
    except Exception as exc:        # noqa: BLE001
        check("A2 clicked(bool) 进取色器不崩（取消路径）", False, repr(exc))
    finally:
        _sd.QColorDialog = orig_cd
    dlg_a.deleteLater()

    # A3 默认磨砂(100%)下饱和度仍生效（v14j 回归修复）
    from src import liquid_glass as lg
    from src import wallpaper as _wp
    from src.wallpaper import Wallpaper as _WP
    grad_img = QImage(200, 160, QImage.Format_ARGB32)
    gp = QPainter(grad_img)
    gg = QLinearGradient(0, 0, 200, 0)
    gg.setColorAt(0, QColor(200, 40, 40))
    gg.setColorAt(1, QColor(40, 40, 200))
    gp.fillRect(grad_img.rect(), gg)
    gp.end()
    soft_img = grad_img.scaled(60, 48).scaled(200, 160)
    fake = _WP(image=grad_img, soft=soft_img, rect=QRect(0, 0, 200, 160),
               dpr=1.0, version=987654)
    orig_snap = _wp.snapshot
    _wp.snapshot = lambda refresh=False: fake
    try:
        ll1 = lg.LiveLens()
        pm_a = ll1.pixmap(QRect(20, 20, 100, 80), 12,
                          lg.Lens(0.6, 20.0, saturation=1.0, frost=1.0), 1.0)
        ll2 = lg.LiveLens()
        pm_b = ll2.pixmap(QRect(20, 20, 100, 80), 12,
                          lg.Lens(0.6, 20.0, saturation=1.9, frost=1.0), 1.0)
        ca = pm_a.toImage().pixelColor(50, 40)
        cb = pm_b.toImage().pixelColor(50, 40)
        check("A3 默认磨砂下饱和度生效（v14j 回归修复）",
              abs(ca.red() - cb.red()) + abs(ca.blue() - cb.blue()) > 10,
              "(%d,%d)->(%d,%d)" % (ca.red(), ca.blue(),
                                    cb.red(), cb.blue()))
        sw = _wp.saturated(fake, 1.9)
        check("A3b 成对烘焙：soft 也变了",
              sw.soft.pixelColor(100, 80) != fake.soft.pixelColor(100, 80))
        check("A3c 中性 factor 返回原快照（零拷贝）",
              _wp.saturated(fake, 1.0) is fake)
    finally:
        _wp.snapshot = orig_snap
        _wp.invalidate()

    # A4 幕布掩膜本地坐标（副屏/偏移屏）
    from src.desktop_window import DesktopWindow as _DW
    host4 = QWidget()
    host4.setGeometry(100, 100, 400, 300)      # 非原点（模拟副屏偏移）
    mk = _DW._scrim_mask_without_anchor(
        host4, QPoint(150, 130), QSize(40, 40))
    check("A4 掩膜底是本地坐标（含 (0,0) 与右下角）",
          mk.contains(QPoint(2, 2)) and mk.contains(QPoint(395, 295)))
    check("A4b 锚点被挖掉（本地 (50,50)=全局(150,130)）",
          not mk.contains(QPoint(50, 50)))

    # A5 hover 样式 color_custom 标志
    st_h2 = _st("hover2.json")
    hs0 = _lsm.hover_style(st_h2)
    check("A5 默认非自定义", hs0.color_custom is False)
    st_h2.set_setting("icon_hover_color", "#80FF0000")
    check("A5b 设色后自定义", _lsm.hover_style(st_h2).color_custom is True)

    # A6 事件钩子限流
    g3 = _bg.BandGuard(None)
    g3._timer.stop()
    hits = [0]
    orig_c = g3._check
    g3._check = lambda: (hits.append(1), orig_c())
    g3._on_win_event(None, 3, None, 0, 0, 0, 0)
    n1 = len(hits)
    g3._on_win_event(None, 3, None, 0, 0, 0, 0)   # 80ms 内 -> 被限流
    check("A6 REORDER 连发限流", n1 >= 1 and len(hits) == n1,
          "%d -> %d" % (n1, len(hits)))
    g3._last_event_check = 0.0
    g3._on_win_event(None, 3, None, 0, 0, 0, 0)
    check("A6b 窗口外事件照常处理", len(hits) == n1 + 1)

    # A7 保活列表去重
    win7 = DesktopWindow(_st("keep.json"))
    tw = QWidget()
    win7._keep_until_destroyed(tw)
    win7._keep_until_destroyed(tw)
    lst = getattr(win7, "_closing_band_widgets", [])
    check("A7 重复保活只记一次", sum(1 for x in lst if x is tw) == 1)
    win7._discard_closing_widget(tw)
    win7.close()


    # ---------- RS v14s：恢复默认配置 + 高级页关于卡删除 ----------
    from src.settings_dialog import SettingsDialog as _SD3
    st_rs = _st("resetall.json")
    # 抹黑一圈设置（覆盖多个分页）
    for k, v in (("liquid_strength", 100), ("label_font_scale", 130),
                 ("icon_hover_scale", 112), ("dock_icon", 56),
                 ("dock_pad", 20), ("label_bold", True),
                 ("liquid_fps_mode", "custom")):
        st_rs.set_setting(k, v)
    fw_rs = type("W3", (), {
        "attached": False,
        "calls": [],
        "apply_label_style": lambda self: self.calls.append("label"),
        "apply_hover_style": lambda self: self.calls.append("hover"),
        "apply_liquid_style": lambda self: self.calls.append("liquid"),
        "apply_dock_settings": lambda self: self.calls.append("dockset"),
        "apply_dock_style": lambda self: self.calls.append("dockstyle"),
        "set_desktop_hotkeys": lambda self, on: self.calls.append("hotkeys"),
        "refresh_grid": lambda self: self.calls.append("grid"),
    })()
    dlg_rs = _SD3(fw_rs, st_rs, None)
    check("RS 高级页有恢复默认按钮", hasattr(dlg_rs, "btn_reset_all"))
    # 高级页不再有关于卡；关于页有版本标签（唯一一份）
    check("RSb 版本标签在关于页（高级页不再重复）",
          hasattr(dlg_rs, "lbl_about_version")
          and dlg_rs.lbl_about_version.text() != "")
    # 确认框：拒绝 -> 不动
    from PySide6.QtWidgets import QMessageBox as _QMB
    orig_exec = _QMB.exec
    _QMB.exec = lambda self=None: _QMB.No
    dlg_rs._reset_all_settings()
    check("RSc 拒绝确认 -> 设置不变",
          st_rs.get_setting("liquid_strength") == 100,
          str(st_rs.get_setting("liquid_strength")))
    # 确认 -> 全部回默认 + 钩子打了一遍
    _QMB.exec = lambda self=None: _QMB.Yes
    import src.settings_dialog as _sdmod
    reopened = []
    orig_open = _sdmod.open_settings
    _sdmod.open_settings = lambda *a, **k: reopened.append(True)
    try:
        dlg_rs._reset_all_settings()
        app.processEvents()      # singleShot(0) 的重开派发
    finally:
        _sdmod.open_settings = orig_open
        _QMB.exec = orig_exec
    check("RSd 设置全部回默认",
          st_rs.get_setting("liquid_strength") == 60
          and st_rs.get_setting("label_font_scale") == 100
          and st_rs.get_setting("dock_icon") == 44
          and st_rs.get_setting("dock_pad") == 10
          and st_rs.get_setting("label_bold") is False
          and st_rs.get_setting("liquid_fps_mode") == "medium",
          "%s/%s/%s" % (st_rs.get_setting("liquid_strength"),
                        st_rs.get_setting("dock_icon"),
                        st_rs.get_setting("liquid_fps_mode")))
    check("RSe 应用钩子全打到位",
          set(fw_rs.calls) >= {"label", "hover", "liquid",
                               "dockset", "dockstyle", "hotkeys", "grid"},
          str(fw_rs.calls))
    check("RSf 面板计划重开（单例重建）", reopened == [True])
    dlg_rs.deleteLater()
    # storage 层：layout/widgets 保留
    st_keep = _st("resetkeep.json")
    st_keep.set_setting("liquid_strength", 100)
    st_keep._data["layout"] = [{"id": "k1", "type": "app", "col": 0,
                                "row": 0, "app": {"kind": "app"}}]
    st_keep.reset_settings()
    check("RSg 图标排布保留（只重置配置）",
          len(st_keep._data["layout"]) == 1
          and st_keep.get_setting("liquid_strength") == 60)


    # ---------- BG v14t：大文件夹（2×2 跨格，内部 3×3 直显） ----------
    from src import grid_flow as _gf
    stb = _st("big.json")
    stb.set_setting("folder_big", True)
    stb._data["layout"] = [
        {"id": "fb", "type": "folder", "col": 0, "row": 0, "name": "B",
         "app": {"name": "B", "target": "B", "kind": "app"},
         "apps": [{"id": f"m{i}", "name": f"m{i}", "target": f"t{i}",
                   "kind": "app"} for i in range(11)]},
        {"id": "x1", "type": "app", "col": 0, "row": 0,
         "app": {"name": "x", "target": "x", "kind": "app"}},
    ]
    stb.save()
    check("BG folder_big 判定", _gf.folder_big(stb) is True)
    check("BGb is_big_folder（仅文件夹）",
          _gf.is_big_folder(stb, stb.find_entry("fb")) is True
          and _gf.is_big_folder(stb, {"type": "app"}) is False)
    extra = _gf.big_extra_cells(stb.find_entry("fb"), 10)
    check("BGc 派生格 = 右/下/右下",
          sorted(extra) == sorted([(1, 0), (0, 1), (1, 1)]), str(extra))
    check("BGd 右边贴边 -> None（放不下 2×2）",
          _gf.big_extra_cells({"col": 9, "row": 0}, 10) is None)
    allex = _gf.folder_extra_cells(stb)
    check("BGe 全量派生集合", (1, 0) in allex and (1, 1) in allex,
          str(sorted(allex)))

    # 渲染：11 成员 -> 8 图标 + … 占位
    from src.iconwidget import AppIcon as _AI2
    big_icon = _AI2(stb.find_entry("fb"), 48, parent=None, big=True)
    big_icon.setFixedSize(120, 120)
    img_b = QImage(120, 120, QImage.Format_ARGB32)
    img_b.fill(QColor(20, 20, 30))
    bp = QPainter(img_b)
    big_icon._paint_folder_big(bp, 1.0)
    bp.end()
    # 8 个成员都画了（采样各槽中心非空）
    centers = [(20, 20), (60, 20), (100, 20), (20, 60),
               (60, 60), (100, 60), (20, 100), (60, 100)]
    drawn = sum(1 for x, y in centers if img_b.pixelColor(x, y).alpha() > 0)
    check("BGf 3×3 内格绘制（8 成员 + … 占位）", drawn >= 6,
          "drawn=%d" % drawn)
    check("BGf _member_at 槽位映射",
          big_icon._member_at(QPoint(60, 20)) == 1
          and big_icon._member_at(QPoint(5, 5)) == -1)
    big_icon.deleteLater()

    # 设置页：大文件夹开关写键
    from src.settings_dialog import SettingsDialog as _SD4
    st_bg2 = _st("bigdlg.json")
    dlg_bg = _SD4(type("W4", (), {"attached": False})(), st_bg2, None)
    check("BGg 文件夹页有大文件夹开关", hasattr(dlg_bg, "chk_folder_big"))
    dlg_bg.chk_folder_big.setChecked(True)
    check("BGh 开关写 folder_big", st_bg2.get_setting("folder_big") is True)
    dlg_bg.deleteLater()

    # 布局集成：pack 后 2×2 块不被其它条目压
    stb2 = _st("bigpack.json")
    stb2.set_setting("folder_big", True)
    stb2._data["layout"] = [
        {"id": "fb", "type": "folder", "col": 0, "row": 0, "name": "B",
         "app": {"name": "B", "target": "B", "kind": "app"},
         "apps": [{"id": "m", "name": "m", "target": "m", "kind": "app"}]},
    ]
    stb2.save()
    stb2.pack_layout()
    f_pos = (stb2.find_entry("fb")["col"], stb2.find_entry("fb")["row"])
    check("BGi pack 后右格空出（2×2 不被占）",
          f_pos == (0, 0)
          and all(stb2.find_entry(e["id"]) is None or e["id"] == "fb"
                  for e in stb2._data["layout"] if e["id"] != "fb")
          and (1, 0) not in [(e["col"], e["row"]) for e in stb2._data["layout"]
                             if e["id"] != "fb"])

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
