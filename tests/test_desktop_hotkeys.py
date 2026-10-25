# -*- coding: utf-8 -*-
"""桌面 Ctrl+C / Ctrl+V（v11 反馈 31）。

用户原话：「桌面现在不支持 ctrl+c 和 ctrl+v 的快捷键，比如我系统 D 盘复制了
一个文件，但是无法触发快捷键粘贴到桌面。」

背景：桌面窗口是 `WS_EX_NOACTIVATE`（永不获得焦点），键盘事件进不了
`keyPressEvent`——只能挂 WH_KEYBOARD_LL 低层钩子。本文件锁住"**什么时候
该接管**"，因为误吞一次会破坏用户在别的程序里的复制粘贴。

契约：
  K1  指针在本程序桌面窗口上 -> 算"在桌面上"
  K2  原生 Progman/WorkerW 也认（未挂载 / 仅原生模式）
  K3  前台应用盖住指针 -> 不接管（用户在看那个应用）
  K4  target_ok = K1/K2 且 非 K3
  K5  Ctrl 没按 -> 不接管；Ctrl+C/V 之外 -> 不接管
  K6  没有可复制/可粘贴的内容 -> 不接管（宁可漏一次不可误吞）
  K7  接管时动作走延迟派发（钩子必须立刻返回），并吞键一次
  K8  长按重复：只做一次动作，后续重复键吞掉但不重复执行
  K9  卸载幂等；非 Windows 上 install() 返回 False
  K10 copy_selection 只把**真实存在的文件**写进剪贴板（本地文件 URL）
  K11 paste_from_clipboard 转发到 _paste_files（右键「粘贴」同一条路）
  K12-K17 v14k4：Delete（无 Ctrl，删选中）/ Ctrl+A（全选当前页）/
         Ctrl+Z（撤销布局）接入钩子——原 keyPressEvent 死代码分支；
         缺省不接线时不吞键；长按节流同 C/V

运行：python tests/test_desktop_hotkeys.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src import desktop_hotkeys as dh
from src.desktop_window import DesktopWindow
from src.storage import Storage

failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def main(app):
    # ---------- K1 / K2 指针判定 ----------
    old_cursor, old_at, old_root, old_cls = (
        dh.cursor_pos, dh.window_at, dh._root, dh._class_of)
    old_u32 = dh._user32
    try:
        dh.cursor_pos = lambda: (100, 100)
        dh._root = lambda h: int(h)
        dh.window_at = lambda x, y: 4444
        dh._class_of = lambda h: "Qt5152QWindowIcon"
        check("K1 指针命中本程序桌面窗口 -> 在桌面上",
              dh.pointer_on_desktop([4444]) is True)
        check("K1b 指针命中别的窗口 -> 不在桌面上",
              dh.pointer_on_desktop([9999]) is False)
        dh.window_at = lambda x, y: 7777
        dh._class_of = lambda h: "Progman"
        check("K2 原生 Progman 也认", dh.pointer_on_desktop([]) is True)
        dh._class_of = lambda h: "WorkerW"
        check("K2b 原生 WorkerW 也认", dh.pointer_on_desktop([]) is True)
        dh._class_of = lambda h: "Chrome_WidgetWin_1"
        check("K2c 浏览器窗口不认", dh.pointer_on_desktop([]) is False)
        dh.cursor_pos = lambda: None
        check("K2d 取不到指针 -> False（不接管）",
              dh.pointer_on_desktop([4444]) is False)

        # ---------- K3 前台遮挡 ----------
        dh.cursor_pos = lambda: (100, 100)
        dh._user32 = lambda: SimpleNamespace(
            GetForegroundWindow=lambda: 1234)
        dh._class_of = lambda h: "Chrome_WidgetWin_1" if h == 1234 else "Progman"
        dh._rect_contains = lambda h, x, y: True
        check("K3 前台应用盖住指针 -> 视为在看应用",
              dh.foreground_covers_pointer() is True)
        dh._class_of = lambda h: "Progman"
        check("K3b 前台就是桌面 -> 不算遮挡",
              dh.foreground_covers_pointer() is False)
        dh._class_of = lambda h: "Chrome_WidgetWin_1" if h == 1234 else "Progman"
        dh._rect_contains = lambda h, x, y: False
        check("K3c 前台应用不含指针 -> 不算遮挡",
              dh.foreground_covers_pointer() is False)

        # ---------- K4 ----------
        dh.pointer_on_desktop = lambda own=(): True
        dh.foreground_covers_pointer = lambda: True
        check("K4 指针在桌面但前台盖住 -> 不接管", dh.target_ok([]) is False)
        dh.foreground_covers_pointer = lambda: False
        check("K4b 两条都满足 -> 接管", dh.target_ok([]) is True)
        dh.pointer_on_desktop = lambda own=(): False
        check("K4c 指针不在桌面 -> 不接管", dh.target_ok([]) is False)
    finally:
        dh.cursor_pos, dh.window_at, dh._root, dh._class_of = (
            old_cursor, old_at, old_root, old_cls)
        dh._user32 = old_u32

    # ---------- K5 / K6 / K7 / K8 钩子判定 ----------
    calls = []
    hk = dh.DesktopHotkeys(
        on_copy=lambda: calls.append("copy"),
        on_paste=lambda: calls.append("paste"),
        own_hwnds=lambda: [],
        can_copy=lambda: True,
        can_paste=lambda: True,
        target_check=lambda: False,
    )
    orig_ctrl = dh.ctrl_down
    try:
        dh.ctrl_down = lambda: False
        check("K5 Ctrl 没按 -> 不接管", hk._on_keydown(dh.VK_V) is False)
        dh.ctrl_down = lambda: True
        check("K5b Ctrl+A 之类不接管", hk._on_keydown(0x41) is False)
        check("K5c 目标不是桌面 -> 不接管", hk._on_keydown(dh.VK_C) is False)
        check("K5d 不接管时不动剪贴板", calls == [])

        hk._target_check = lambda: True
        hk._can_copy = lambda: False
        check("K6 没有选中项 -> Ctrl+C 不接管",
              hk._on_keydown(dh.VK_C) is False)
        hk._can_copy = lambda: True
        hk._can_paste = lambda: False
        check("K6b 剪贴板里没有文件 -> Ctrl+V 不接管",
              hk._on_keydown(dh.VK_V) is False)
        check("K6c 不接管时没有任何动作", calls == [])

        hk._can_paste = lambda: True
        hk._last_at = 0.0
        check("K7 条件齐备 -> 接管并吞键", hk._on_keydown(dh.VK_V) is True)
        check("K7b 动作延迟派发（钩子内不干活）", calls == [],
              str(calls))
        QTest.qWait(30)
        check("K7c 事件循环里动作执行了", calls == ["paste"], str(calls))

        hk._last_at = 0.0
        hk._on_keydown(dh.VK_C)
        QTest.qWait(30)
        hk._on_keydown(dh.VK_C)          # 立刻再来一次 = 长按重复
        QTest.qWait(30)
        check("K8 长按重复只执行一次（第二次被节流）",
              calls.count("copy") == 1, str(calls))
        check("K8b 重复键仍然吞掉（不会漏给别的窗口）",
              hk._last_at > 0)
    finally:
        dh.ctrl_down = orig_ctrl

    # ---------- K9 安装 / 卸载 ----------
    hk2 = dh.DesktopHotkeys()
    ok = hk2.install()
    check("K9 install 返回布尔（拿不到钩子也不抛）", isinstance(ok, bool))
    if os.name == "nt":
        check("K9b Windows 上装上了低层钩子", ok is True)
    hk2.uninstall()
    hk2.uninstall()      # 幂等
    check("K9c 卸载后 installed = False", hk2.installed is False)

    # ---------- K10 复制选中项 ----------
    st_path = Path(tempfile.gettempdir()) / "test_hotkeys_state.json"
    if st_path.exists():
        st_path.unlink()
    st = Storage(path=str(st_path))
    real = Path(tempfile.gettempdir()) / "pd_hotkey_sample.txt"
    real.write_text("hello", encoding="utf-8")
    st._data["layout"] = [
        {"id": "a1", "type": "app", "col": 0, "row": 0,
         "app": {"name": "sample", "target": str(real), "kind": "file"}},
        {"id": "a2", "type": "app", "col": 1, "row": 0,
         "app": {"name": "missing", "target": str(real) + ".nope",
                 "kind": "file"}},
    ]
    fake_self = SimpleNamespace(_selected={"a1", "a2"}, storage=st)
    ok_copy = DesktopWindow.copy_selection(fake_self)
    md = QApplication.clipboard().mimeData()
    urls = [u.toLocalFile() for u in md.urls()] if md else []
    want = Path(str(real)).as_posix()      # QUrl 的本地路径用正斜杠
    check("K10 copy_selection 成功", ok_copy is True)
    check("K10b 只写真实存在的文件（不存在的目标被跳过）",
          urls == [want], "%s != %s" % (urls, [want]))
    check("K10c 写的是本地文件 URL（资源管理器也认 paste）",
          urls and urls[0] == want)
    fake_empty = SimpleNamespace(_selected=set(), storage=st)
    check("K10d 没有选中项 -> 返回 False（不接管快捷键）",
          DesktopWindow.copy_selection(fake_empty) is False)
    # offscreen 平台的已知坑：进程退出时 QClipboard 仍持有我们塞进去的
    # QMimeData，平台插件析构顺序会踩空（rc=139）。清一次即安全——这只影响
    # 无 X/无剪贴板服务的测试环境，真实 Windows 平台不受影响。
    QApplication.clipboard().clear()
    app.processEvents()

    # ---------- K11 粘贴转发 ----------
    hit = []
    fake_paste = SimpleNamespace(_paste_files=lambda: hit.append(1))
    DesktopWindow.paste_from_clipboard(fake_paste)
    check("K11 paste_from_clipboard 转发到 _paste_files", hit == [1])

    if st_path.exists():
        st_path.unlink()
    # ---------- K12-K17 v14k4：Delete / Ctrl+A / Ctrl+Z ----------
    calls2 = []
    hk3 = dh.DesktopHotkeys(
        on_delete=lambda: calls2.append("delete"),
        on_select_all=lambda: calls2.append("selectall"),
        on_undo=lambda: calls2.append("undo"),
        own_hwnds=lambda: [],
        can_delete=lambda: True,
        can_select_all=lambda: True,
        can_undo=lambda: True,
        target_check=lambda: True,
    )
    try:
        dh.ctrl_down = lambda: False
        hk3._last_at = 0.0
        check("K12 Delete（无 Ctrl）条件齐备 -> 接管",
              hk3._on_keydown(dh.VK_DELETE) is True)
        QTest.qWait(30)
        check("K12b 删除动作派发", calls2 == ["delete"], str(calls2))
        dh.ctrl_down = lambda: True     # Ctrl+Delete：Ctrl 组合里没有它
        hk3._last_at = 0.0
        check("K12c Ctrl+Delete 不接管（放给系统）",
              hk3._on_keydown(dh.VK_DELETE) is False and calls2 == ["delete"])
        dh.ctrl_down = lambda: True
        hk3._last_at = 0.0
        check("K13 Ctrl+A 条件齐备 -> 接管", hk3._on_keydown(dh.VK_A) is True)
        hk3._last_at = 0.0
        check("K13b Ctrl+Z 条件齐备 -> 接管", hk3._on_keydown(dh.VK_Z) is True)
        QTest.qWait(30)
        check("K13c 全选/撤销派发",
              calls2 == ["delete", "selectall", "undo"], str(calls2))
        hk3._can_undo = lambda: False
        hk3._last_at = 0.0
        check("K14 没有可撤销 -> Ctrl+Z 不接管",
              hk3._on_keydown(dh.VK_Z) is False)
        hk3._can_delete = lambda: False
        dh.ctrl_down = lambda: False
        hk3._last_at = 0.0
        check("K15 没有选中项 -> Delete 不接管",
              hk3._on_keydown(dh.VK_DELETE) is False)
        check("K15b 不接管时无动作", calls2[-1] != "delete")
        hk3._can_delete = lambda: True
        hk3._last_at = 0.0
        hk3._on_keydown(dh.VK_DELETE)
        QTest.qWait(30)                  # 第一次派发执行
        before = calls2.count("delete")
        check("K16 长按 Delete 只删一次（节流吞重复）",
              hk3._on_keydown(dh.VK_DELETE) is True and
              calls2.count("delete") == before, str(calls2))
    finally:
        dh.ctrl_down = orig_ctrl

    # ---------- K17 窗口接线（回调落到窗口方法） ----------
    st_h = Storage(path=str(Path(tempfile.gettempdir()) / "test_hotkeys_w.json"))
    wh = DesktopWindow(st_h)
    wh.setup_desktop_hotkeys()
    check("K17 钩子接了删除/全选/撤销回调",
          wh._hotkeys is not None
          and callable(wh._hotkeys.on_delete)
          and callable(wh._hotkeys.on_select_all)
          and callable(wh._hotkeys.on_undo)
          and wh._hotkeys._can_undo() is False)
    wh._hotkeys.uninstall()
    wh.close()

    if real.exists():
        real.unlink()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    main(app)
    print("-" * 56)
    if failures:
        print("FAILED %d: %s" % (len(failures), failures))
        sys.exit(1)
    print("ALL hotkeys PASSED")
