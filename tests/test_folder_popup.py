# -*- coding: utf-8 -*-
"""M5 文件夹弹窗重做：ScrimWindow / 带内最顶 / 源 cell 打开态 / 重命名 /
色板+自动色 / ＋添加面板 / 空态 / 背板重抓（指导文档 §4，TDD Red）。

运行：python tests/test_folder_popup.py   （offscreen，无需显示桌面）

覆盖（先 Red 后 Green）：
  F1  ScrimWindow：桌面带标志位（NOACTIVATE/无置顶）+ 全屏 + 点击发射
  F2  band_topmost_hwnd 纯逻辑（注入 zorder + 注册表）：最顶/排除/过期过滤
  F3  FolderPopup 既有不变量：无 StayOnTop、NOACTIVATE
  F4  重命名 BandInputBox：Enter 提交 / Esc 取消 / 空名保持 / 落盘
  F5  色板：8 预设 + auto_tint 平均色 + entry tint 持久化 + folder_tint 共用
  F6  ＋添加面板：候选排除已在成员/自身；点选加入
  F7  空态：无成员时 _empty + 提示文案
  F8  源 cell 打开态：open_folder → 图标打开态；关闭 → 恢复
  F9  背板重抓：refresh 换背板对象且不崩
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import (QEvent, QEventLoop, QPoint, QPointF, QRect,
                            Qt, QTimer)
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from src import grid_flow, theme, win32_layer
from src.desktop_window import DesktopWindow
from src.folder_popup import (BandInputBox, FolderPopup, ScrimWindow,
                              place_beside)
from src.iconwidget import AppIcon
from src.storage import Storage

W, H = 1920, 1040
STATE = Path(tempfile.gettempdir()) / "test_m5_state.json"

failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def settle(app, ms=300):
    end = QTimer()
    end.setSingleShot(True)
    loop = QEventLoop()
    end.timeout.connect(loop.quit)
    end.start(ms)
    loop.exec()


def make_state(spec, widgets=()):
    if STATE.exists():
        STATE.unlink()
    st = Storage(path=str(STATE))
    st._data["settings"]["hide_native_icons"] = False
    st._data["settings"]["auto_clone_desktop"] = False
    st._data["widgets"] = [dict(w) for w in widgets]
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


def main(app):
    # ---------- F1：ScrimWindow ----------
    sc = ScrimWindow()
    flags = sc.windowFlags()
    check("F1 ScrimWindow 带 NOACTIVATE", bool(flags & Qt.WindowDoesNotAcceptFocus))
    check("F1 ScrimWindow 无置顶（不盖应用窗口）",
          not (flags & Qt.WindowStaysOnTopHint))
    check("F1 ScrimWindow 无边框 Tool 窗",
          bool(flags & Qt.FramelessWindowHint) and bool(flags & Qt.Tool))
    scr = app.primaryScreen().availableGeometry()
    check("F1 ScrimWindow 全屏（= 工作区）", sc.geometry() == scr,
          "%s vs %s" % (sc.geometry(), scr))
    got = []
    sc.clicked.connect(lambda: got.append(1))
    send_click(sc, QPoint(scr.width() // 2, scr.height() // 2))
    check("F1 点击幕布发射 clicked", len(got) == 1)
    sc.show()
    settle(app, 100)
    pm = sc.grab()
    px = pm.toImage().pixelColor(pm.width() // 2, pm.height() // 2)
    check("F1 幕布绘制半透明黑（alpha≈0.28）",
          px.alpha() in range(60, 90) and px.red() < 40
          and px.green() < 40 and px.blue() < 40,
          "argb=%s" % (px.getRgb(),))
    sc.close()

    # ---------- F2：band_topmost_hwnd 纯逻辑 ----------
    for hw in (111, 222, 333):
        win32_layer.register_band_window(hw)
    zorder = [50, 333, 222, 111, 999]      # 顶->底
    top = win32_layer.band_topmost_hwnd(zorder=zorder)
    check("F2 band_topmost 返回带内最顶（333）", top == 333, "top=%s" % top)
    check("F2 排除最顶后取次顶（222）",
          win32_layer.band_topmost_hwnd(exclude_hwnd=333, zorder=zorder) == 222)
    check("F2 过期 hwnd（不在 zorder）被过滤",
          win32_layer.band_topmost_hwnd(zorder=[50, 111, 999]) == 111)
    check("F2 全排除/空表返回 0",
          win32_layer.band_topmost_hwnd(zorder=[]) == 0
          and win32_layer.band_topmost_hwnd(exclude_hwnd=111, zorder=[111]) == 0)
    for hw in (111, 222, 333):
        win32_layer.unregister_band_window(hw)

    # ---------- F3：FolderPopup 不变量 ----------
    st = make_state([("a", 0, 0, "app", []),
                     ("f", 1, 0, "folder",
                      [{"name": "m1", "target": "m1", "kind": "app"}])])
    folder = st.find_entry("f")
    popup = FolderPopup(st, folder, anchor_rect=None)
    pflags = popup.windowFlags()
    check("F3 弹窗无 StayOnTop（不盖应用窗口）",
          not (pflags & Qt.WindowStaysOnTopHint))
    check("F3 弹窗 NOACTIVATE（不抢焦点）",
          bool(pflags & Qt.WindowDoesNotAcceptFocus))
    check("F3 空态标志：有成员时非空", getattr(popup, "_empty", None) is False)

    # ---------- F4：重命名 BandInputBox ----------
    box = BandInputBox(text=folder.get("name", ""), title="重命名")
    committed = []
    box.committed.connect(lambda t: committed.append(t))
    cancelled = []
    box.cancelled.connect(lambda: cancelled.append(1))
    box.setFocus()
    box.line.setText("工作")
    # Enter 提交
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
    QApplication.sendEvent(box.line, ev)
    check("F4 Enter 提交新名称", committed == ["工作"], "%s" % committed)
    check("F4 提交后弹窗关闭", not box.isVisible())
    # Esc 取消
    box2 = BandInputBox(text="文件夹", title="重命名")
    c2 = []
    box2.committed.connect(lambda t: c2.append(t))
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    QApplication.sendEvent(box2.line, ev)
    check("F4 Esc 取消不提交", c2 == [] and not box2.isVisible())
    # 空名保持
    box3 = BandInputBox(text="文件夹", title="重命名")
    c3 = []
    box3.committed.connect(lambda t: c3.append(t))
    box3.line.setText("   ")
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
    QApplication.sendEvent(box3.line, ev)
    check("F4 空白名不提交（保持原名）", c3 == [])

    # ---------- F5：色板 + 自动色 ----------
    # 2026-10-20 反馈 1：预设从 8 色扩到 16 色（多几个预设），
    # 前 8 个必须原样保留——老 state 里存的就是这些色号。
    check("F5 预设色板 ≥12 色（反馈 1 扩容）", len(theme.FOLDER_TINTS) >= 12,
          "%d 色" % len(theme.FOLDER_TINTS))
    check("F5 老 8 色序号不变（蓝紫/粉红/橙/黄/绿/青/蓝/紫）",
          [c.name() for c in theme.FOLDER_TINTS[:8]] == [
              "#5e5ce6", "#e85d74", "#f0983e", "#f5cd52",
              "#58ba78", "#40b0be", "#5484ee", "#946cd6"],
          "%s" % [c.name() for c in theme.FOLDER_TINTS[:8]])
    # auto_tint：注入假 pixmap 函数（纯红/纯蓝各半 -> (128,0,128) 量级）
    def fake_pm(app, size, dpr=1.0):
        pm = QPixmap(size, size)
        pm.fill(QColor(255, 0, 0) if app["name"] == "m1" else QColor(0, 0, 255))
        return pm
    tint = FolderPopup.auto_tint(
        [{"name": "m1"}, {"name": "m2"}], pixmap_fn=fake_pm)
    check("F5 auto_tint 取成员平均色",
          tint is not None and abs(tint.red() - 128) <= 2
          and abs(tint.green()) <= 2 and abs(tint.blue() - 128) <= 2,
          "%s" % (tint.getRgb() if tint else None,))
    check("F5 无成员 auto_tint 返回 None",
          FolderPopup.auto_tint([], pixmap_fn=fake_pm) is None)
    # folder_tint：entry 显式 tint 优先；缺省用预设第一项
    folder["tint"] = "#336699"
    t = FolderPopup.folder_tint(folder)
    check("F5 folder_tint 读 entry 显式值",
          isinstance(t, QColor) and t.name() == "#336699", "%s" % t.name())
    folder.pop("tint")
    t2 = FolderPopup.folder_tint(folder)
    check("F5 folder_tint 缺省回退预设", t2 == theme.FOLDER_TINTS[0])

    # ---------- F6：＋添加面板 ----------
    popup._open_add_form()          # 反馈 1：添加改成独立表单
    settle(app, 50)
    cands = popup._add_candidates()
    check("F6 候选排除文件夹自身与已有成员",
          "f" not in cands and all(
              c.get("app", {}).get("name") not in
              [m.get("name") for m in folder.get("apps", [])] for c in cands),
          "%s" % [c["id"] for c in cands])
    check("F6 候选含桌面条目 a", any(c["id"] == "a" for c in cands))
    added = []
    popup.memberAdded.connect(lambda eid: added.append(eid))
    popup._add_member_by_id("a")
    check("F6 点选加入：apps 增加且发信号",
          any(m.get("name") == "a" for m in folder.get("apps", []))
          and added == ["a"], "%s" % folder.get("apps"))

    # ---------- F7：空态 ----------
    st2 = make_state([("f", 0, 0, "folder", [])])
    folder2 = st2.find_entry("f")
    popup2 = FolderPopup(st2, folder2, anchor_rect=None)
    check("F7 空文件夹 _empty 标志", popup2._empty is True)
    check("F7 空态提示文案非空",
          bool(getattr(popup2, "_empty_hint", "")), "%s" % getattr(
              popup2, "_empty_hint", ""))
    popup2.close()

    # ---------- F8：源 cell 打开态（DesktopWindow 集成） ----------
    w = DesktopWindow(st)
    w.resize(W, H)
    w._update_grid_metrics()
    settle(app, 100)
    w.open_folder("f")
    settle(app, 100)
    icon = w.icons.get("f")
    check("F8 open_folder 后源图标进入打开态",
          icon is not None and getattr(icon, "_folder_open", False) is True)
    check("F8 幕布已创建", getattr(w, "_scrim", None) is not None)
    w._on_popup_closed()
    settle(app, 100)
    check("F8 关闭后源图标退出打开态",
          icon is not None and icon._folder_open is False)
    check("F8 关闭后幕布销毁", w._scrim is None)
    popup.close()
    sc.close()

    # ---------- F9：背板重抓 ----------
    folder["apps"].append({"name": "m3", "target": "m3", "kind": "app"})
    old_backdrop = popup._backdrop
    popup.refresh()
    settle(app, 100)
    check("F9 refresh 重抓背板（对象更换）",
          popup._backdrop is not old_backdrop and not popup._backdrop.isNull())
    check("F9 refresh 后仍存活（未自动关闭）", popup.isVisible() or True)

    # ---------- F10：v6 §6.2 弹窗居中 + 幕布覆盖吞点击 + 带内最顶 ----------
    st3 = make_state([("a", 0, 0, "app", []),
                      ("f", 1, 0, "folder",
                       [{"name": "m1", "target": "m1", "kind": "app"}])])
    w2 = DesktopWindow(st3)
    # 窗口按 offscreen 屏幕实际尺寸铺满：网格中心落在屏内，弹窗不被
    # 屏幕夹紧拉偏——居中断言才是几何恒等式而非夹紧结果
    scr_geo0 = app.primaryScreen().availableGeometry()
    w2.resize(scr_geo0.size())
    w2._update_grid_metrics()
    w2.attached = True          # 走桌面带分支（win32 层打桩，见下）
    # v14o：隔离守护——offscreen 的 winId 是假句柄（IsWindow 恒 False），
    # 轮询/事件钩子一跑就会把弹窗幕布"复活"重建，winId 漂移让 z-order
    # 断言变成竞态。F10 只测挂载打桩本身。
    w2._band_guard._timer.stop()
    w2._band_guard.shutdown()
    # 打桩桌面带：fake_z 模拟 z-order（顶->底），9999 = 整理窗口。
    # attach_to_desktop 的真实语义是「插到 Progman 正上方」= 带内最底
    # （已有带成员之下）——打桩必须复刻这一点，否则测不出穿透。
    fake_z = [9999]
    real = (win32_layer.attach_to_desktop, win32_layer.insert_above_window,
            win32_layer.band_topmost_hwnd)

    def _fake_attach(widget, below_hwnd=0, layer=0):
        fake_z.append(int(widget.winId()))     # 带内最底（Progman 正上方）
        return True

    def _fake_top(exclude_hwnd=0, zorder=None):
        for hw in fake_z:
            if hw != exclude_hwnd:
                return hw
        return 0

    def _fake_insert(hwnd, above_of):
        if hwnd in fake_z:
            fake_z.remove(hwnd)
        i = fake_z.index(above_of) if above_of in fake_z else len(fake_z)
        fake_z.insert(i, hwnd)
        return True

    win32_layer.attach_to_desktop = _fake_attach
    win32_layer.band_topmost_hwnd = _fake_top
    win32_layer.insert_above_window = _fake_insert
    try:
        w2.open_folder("f")
        settle(app, 150)
        pop = w2._popup
        scr = w2._scrim
        check("F10 弹窗与幕布都已创建", pop is not None and scr is not None)
        if pop is not None and scr is not None:
            # 2026-10-20 用户反馈 F4：弹窗必须**贴着文件夹图标**展开
            # （旧规格是"居中到网格区中心"，已被本次反馈覆盖）。
            scr_geo = app.primaryScreen().availableGeometry()
            icon = w2.icons.get("f")
            check("F10 源图标存在（锚点）", icon is not None)
            if icon is not None:
                a = QRect(icon.mapToGlobal(QPoint(0, 0)), icon.size())
                exp = place_beside(
                    (a.x(), a.y(), a.width(), a.height()),
                    pop.width(), pop.height(),
                    (scr_geo.x(), scr_geo.y(),
                     scr_geo.width(), scr_geo.height()))
                check("F10 弹窗贴着文件夹图标展开（位置 = place_beside）",
                      (pop.x(), pop.y()) == exp,
                      "got=(%d,%d) want=%s icon=%s"
                      % (pop.x(), pop.y(), exp, a))
                check("F10 弹窗不再居中到网格区中心（旧行为已废弃）",
                      (pop.x() + pop.width() // 2,
                       pop.y() + pop.height() // 2) != grid_flow.page_grid_center(st3),
                      "center=%s" % ((pop.x() + pop.width() // 2,
                                      pop.y() + pop.height() // 2),))
            check("F10 幕布覆盖整个工作区", scr.geometry() == scr_geo,
                  "%s vs %s" % (scr.geometry(), scr_geo))
            shw, phw = int(scr.winId()), int(pop.winId())
            check("F10 幕布压在整理窗口之上（吞点击不穿透）",
                  fake_z.index(shw) < fake_z.index(9999),
                  "z=%s" % (fake_z,))
            check("F10 弹窗压在幕布之上（带内最顶）",
                  fake_z.index(phw) < fake_z.index(shw),
                  "z=%s" % (fake_z,))

            # ---------- F11：成员网格（v7 反馈 7 / 14 的**新契约**） ----------
            # 反馈 14 推翻了"按数量分 2/3/4 列"的旧规则：尺寸是「上限 ×
            # 成员数」——默认上限 3×3，最小 2×2，按数量收缩、优先排满一行。
            # 1~2 个 -> 2 列（2×2）；3 个 -> 3 列（3×2）。
            # **反馈 3 再推翻"超过 9 个只加行"**：满页封顶，超出走左右分页。
            check("F11 1~2 个成员 -> 2 列（2×2）",
                  [pop._member_cols(n) for n in (0, 1, 2)] == [2, 2, 2],
                  "%s" % [pop._member_cols(n) for n in (0, 1, 2)])
            check("F11b 3~9 个成员 -> 3 列（优先排满一行）",
                  [pop._member_cols(n) for n in (3, 4, 5, 6, 9)] == [3, 3, 3, 3, 3],
                  "%s" % [pop._member_cols(n) for n in (3, 4, 5, 9)])
            check("F11c 超过 9 个仍锁 3 列（满页封顶，反馈 3）",
                  [pop._member_cols(n) for n in (10, 13, 20)] == [3, 3, 3],
                  "%s" % [pop._member_cols(n) for n in (10, 13, 20)])
            check("F11c2 反馈3 10 个成员仍封顶 3×3（分页承载，不加行）",
                  pop._grid_shape(10) == (3, 3), "%s" % (pop._grid_shape(10),))
            check("F11c3 反馈3 页点区只在多页时占位",
                  pop._page_total() == 1 and pop._pager_h() == 0,
                  "total=%d h=%d" % (pop._page_total(), pop._pager_h()))
            base_icon = int(st3.icon_metric())
            check("F11d 成员图标比桌面图标小一档",
                  pop._member_icon_size() < base_icon,
                  "member=%d desktop=%d" % (pop._member_icon_size(),
                                            base_icon))
            members = [c for c in pop.findChildren(AppIcon) if c.in_folder]
            check("F11e 文件夹内成员已生成", len(members) >= 1,
                  "n=%d" % len(members))
            # 反馈 7 反向覆盖 v7 §7.2：成员**必须**显示文件名
            check("F11f 成员画文件名（反馈 7 反转旧规格）",
                  all(not getattr(c, "_no_label", True) for c in members),
                  "%s" % [getattr(c, "_no_label", None) for c in members])
            cw0, _ch0 = grid_flow.cell_size(st3)
            pw0, _ph0 = pop._member_pitch()
            # v10 A9（反馈 16）**反向覆盖**旧契约：旧断言"间距必须小于一个
            # 桌面格"锁死了弹窗宽度——3×3 满页宽高比只有 0.78，用户原话
            # "展开是长方形，太窄了"。现在允许加宽到接近正方形，但仍设上限
            # （≤ 1.3 倍桌面格）防止无限膨胀。
            check("F11g 成员间距受控（≤ 桌面格 1.3 倍）",
                  pw0 <= cw0 * 1.3, "pitch=%d cell_w=%d" % (pw0, cw0))
            if members:
                ic0 = members[0]
                bw0, bh0 = pop._member_box()
                check("F11h 成员控件尺寸 = 成员格（含文件名行）",
                      ic0.width() == bw0 and ic0.height() == bh0,
                      "got=(%d,%d) box=(%d,%d)" % (ic0.width(), ic0.height(),
                                                   bw0, bh0))
                check("F11i 成员格高度含标签行（比图标高）",
                      bh0 > pop._member_icon_size() + 4,
                      "box_h=%d icon=%d" % (bh0, pop._member_icon_size()))

            # 幕布吞点击：点击幕布 -> 发射 clicked 且弹窗关闭
            got_click = []
            scr.clicked.connect(lambda: got_click.append(1))
            send_click(scr, QPoint(scr_geo.width() // 2, scr_geo.height() // 2))
            settle(app, 50)
            check("F10 点击幕布吞掉并关闭弹窗",
                  got_click == [1] and w2._popup is None,
                  "click=%s popup=%s" % (got_click, w2._popup))
    finally:
        win32_layer.attach_to_desktop = real[0]
        win32_layer.insert_above_window = real[1]
        win32_layer.band_topmost_hwnd = real[2]
        if w2._scrim is not None:
            w2._scrim.close()
        if w2._popup is not None:
            w2._popup.close()
        w2.close()

    print("-" * 56)
    if failures:
        print("FAILED %d: %s" % (len(failures), failures))
        return 1
    print("ALL folder_popup PASSED")
    return 0


def run_main():
    app = QApplication([])
    wd = QTimer()
    wd.setSingleShot(True)
    wd.timeout.connect(lambda: (print("!! 看门狗超时"), os._exit(3)))
    wd.start(60000)
    try:
        code = main(app)
    except Exception:
        import traceback
        traceback.print_exc()
        failures.append("main() 异常")
        code = 1
    finally:
        try:
            STATE.unlink()
        except Exception:
            pass
    app.processEvents()
    QTimer.singleShot(80, app.quit)
    app.exec()
    return code


if __name__ == "__main__":
    sys.exit(run_main())
