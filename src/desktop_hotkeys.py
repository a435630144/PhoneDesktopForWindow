# -*- coding: utf-8 -*-
"""桌面快捷键：Ctrl+C / Ctrl+V（v11 反馈 31）。

## 为什么不能靠 Qt 的 keyPressEvent

桌面整理窗口带 `WS_EX_NOACTIVATE`（`Qt.WindowDoesNotAcceptFocus`）——它
**永远不会成为前台窗口**，这是桌面带窗口不抢焦点、不盖住应用窗口的前提
（见 `win32_layer` 模块头）。没有焦点就没有键盘事件，`keyPressEvent` 里
那些 Ctrl+A / Ctrl+Z 分支在真实使用中根本不会触发。

所以走 **WH_KEYBOARD_LL 低层键盘钩子**：钩子在系统把按键投递给任何窗口
之前先看到，我们自己判断"这一次该不该归桌面管"。

## 判据（三条同时成立才接管）

1. **指针在桌面上**：`WindowFromPoint` 命中本程序的桌面窗口（或原生
   Progman/WorkerW）。我们的窗口在 z-order 上位于所有应用窗口**之下**，
   所以"命中我们"本身就意味着指针底下没有应用窗口——用户看的就是桌面。
2. **前台不是别的应用**：前台窗口矩形不包含指针（点桌面不会激活我们，
   前台可能还停在上一个应用上——这正是"在资源管理器复制、点桌面粘贴"
   的常规流程，必须放行）。
3. **真的有事可做**：Ctrl+C 有选中项 / Ctrl+V 剪贴板里有本地文件。
   没有可做的事就 `CallNextHookEx` 放过去，绝不吞键。

三条都满足才 `return 1` 吞掉这次按键，其余一律放行——**宁可漏一次，
不可误吞一次**：误吞会破坏用户在其他程序里的复制粘贴。
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QObject, QTimer

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

VK_CONTROL = 0x11
VK_C = 0x43
VK_V = 0x56


class KBDLLHOOKSTRUCT(ctypes.Structure):
    """低层键盘钩子的按键信息（vkCode 才是这次真正按下的键）。

    不能用 `GetAsyncKeyState` 代替：钩子回调发生在系统把按键状态"落下"
    之前，此刻查询常常还是 0——那样 Ctrl+C 永远判不出来。
    """

    _fields_ = [("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]

# 原生桌面的两个宿主类名（点桌面时前台就是它们）
DESKTOP_CLASSES = ("Progman", "WorkerW")

# 最短重触发间隔（ms）：按住不放时键盘会重复发 KEYDOWN，粘贴一次就够，
# 连发会把同一个文件复制出好几份。
REPEAT_GUARD_MS = 350


def _user32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetForegroundWindow.argtypes = []
    u.WindowFromPoint.restype = wintypes.HWND
    u.WindowFromPoint.argtypes = [wintypes.POINT]
    u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    u.GetCursorPos.restype = wintypes.BOOL
    u.GetAncestor.restype = wintypes.HWND
    u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetClassNameW.restype = ctypes.c_int
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowRect.restype = wintypes.BOOL
    u.GetAsyncKeyState.restype = ctypes.c_short
    u.GetAsyncKeyState.argtypes = [ctypes.c_int]
    # 钩子三条：不声明 argtypes 时 ctypes 不知道怎么把回调函数指针转过去
    # （实测直接抛 TypeError -> install() 静默失败，快捷键完全没反应）。
    u.SetWindowsHookExW.restype = wintypes.HHOOK
    u.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                    ctypes.c_void_p, wintypes.DWORD]
    u.CallNextHookEx.restype = ctypes.c_ssize_t
    u.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                 ctypes.c_size_t, ctypes.c_ssize_t]
    u.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    u.UnhookWindowsHookEx.restype = wintypes.BOOL
    return u


GA_ROOT = 2


def _root(hwnd: int) -> int:
    try:
        return int(_user32().GetAncestor(wintypes.HWND(hwnd), GA_ROOT) or 0)
    except Exception:      # noqa: BLE001
        return int(hwnd or 0)


def _class_of(hwnd: int) -> str:
    try:
        buf = ctypes.create_unicode_buffer(256)
        if _user32().GetClassNameW(wintypes.HWND(hwnd), buf, 256):
            return buf.value
    except Exception:      # noqa: BLE001
        pass
    return ""


def cursor_pos():
    p = wintypes.POINT()
    try:
        if _user32().GetCursorPos(ctypes.byref(p)):
            return (int(p.x), int(p.y))
    except Exception:      # noqa: BLE001
        pass
    return None


def window_at(x: int, y: int) -> int:
    try:
        return int(_user32().WindowFromPoint(wintypes.POINT(int(x), int(y))) or 0)
    except Exception:      # noqa: BLE001
        return 0


def _rect_contains(hwnd: int, x: int, y: int) -> bool:
    r = wintypes.RECT()
    try:
        if not _user32().GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
            return False
    except Exception:      # noqa: BLE001
        return False
    return r.left <= x < r.right and r.top <= y < r.bottom


def ctrl_down() -> bool:
    """物理 Ctrl 是否按着（钩子只给键码，不给修饰键状态）。"""
    try:
        return bool(_user32().GetAsyncKeyState(VK_CONTROL) & 0x8000)
    except Exception:      # noqa: BLE001
        return False


def pointer_on_desktop(own_hwnds: Iterable[int] = ()) -> bool:
    """指针是否在"桌面"上（本程序桌面窗口 / 原生桌面）。

    我们的窗口永远在应用窗口之下，所以 `WindowFromPoint` 命中它 = 指针
    底下没有应用窗口。原生 Progman/WorkerW 也认（未挂载 / 仅原生模式）。
    """
    pos = cursor_pos()
    if pos is None:
        return False
    hwnd = _root(window_at(*pos))
    if not hwnd:
        return False
    own = {int(h) for h in own_hwnds if h}
    if hwnd in own:
        return True
    return _class_of(hwnd) in DESKTOP_CLASSES


def foreground_covers_pointer() -> bool:
    """前台应用窗口是否盖住指针（盖住 = 用户在看那个应用，别抢键）。"""
    pos = cursor_pos()
    if pos is None:
        return False
    fg = int(_user32().GetForegroundWindow() or 0)
    if not fg:
        return False
    if _class_of(fg) in DESKTOP_CLASSES:
        return False        # 前台就是桌面：不是"别的应用"
    return _rect_contains(fg, *pos)


def target_ok(own_hwnds: Iterable[int] = ()) -> bool:
    """当前这次按键是否"该归桌面管"（判据 1 + 2）。"""
    return pointer_on_desktop(own_hwnds) and not foreground_covers_pointer()


class DesktopHotkeys(QObject):
    """低层键盘钩子：桌面上的 Ctrl+C / Ctrl+V。

    `can_copy` / `can_paste` 由窗口提供（有选中项 / 剪贴板有本地文件），
    用来实现"没事可做就不吞键"。回调通过 `QTimer.singleShot(0, …)` 派发：
    钩子函数必须**立刻返回**，在里面做 Qt 操作会拖慢整个系统的键盘响应。
    """

    def __init__(self, parent=None, on_copy: Optional[Callable] = None,
                 on_paste: Optional[Callable] = None,
                 own_hwnds: Optional[Callable[[], Iterable[int]]] = None,
                 can_copy: Optional[Callable[[], bool]] = None,
                 can_paste: Optional[Callable[[], bool]] = None,
                 target_check: Optional[Callable[[], bool]] = None):
        super().__init__(parent)
        self.on_copy = on_copy or (lambda: None)
        self.on_paste = on_paste or (lambda: None)
        self._own_hwnds = own_hwnds or (lambda: ())
        self._can_copy = can_copy or (lambda: True)
        self._can_paste = can_paste or (lambda: True)
        self._target_check = target_check
        self._hook = None
        self._proc = None
        self._last_at = 0.0
        self._installed = False

    # ---------- 安装 / 卸载 ----------
    def install(self) -> bool:
        if self._installed:
            return self._hook is not None
        self._installed = True
        if os.name != "nt":
            return False
        try:
            u = _user32()
            # 低层键盘钩子：hMod 可为 NULL（钩子回调在本进程的消息循环里跑）
            proto = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                       ctypes.c_size_t, ctypes.c_ssize_t)
            self._proc = proto(self._hook_cb)
            self._hook = u.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        except Exception:      # noqa: BLE001 —— 装不上就退化成没有快捷键
            self._hook = None
        return bool(self._hook)

    def uninstall(self):
        if self._hook:
            try:
                _user32().UnhookWindowsHookEx(self._hook)
            except Exception:      # noqa: BLE001
                pass
        self._hook = None
        self._proc = None

    @property
    def installed(self) -> bool:
        return bool(self._hook)

    # ---------- 钩子 ----------
    def _hook_cb(self, n_code, w_param, l_param):
        try:
            if n_code == 0 and int(w_param) in (WM_KEYDOWN, WM_SYSKEYDOWN):
                info = KBDLLHOOKSTRUCT.from_address(int(l_param))
                if self._on_keydown(int(info.vkCode)):
                    return 1        # 已接管：吞掉，别投递给任何窗口
        except Exception:      # noqa: BLE001 —— 钩子里绝不许抛
            pass
        try:
            return _user32().CallNextHookEx(self._hook, n_code, w_param, l_param)
        except Exception:      # noqa: BLE001
            return 0

    def _on_keydown(self, vk: int = 0) -> bool:
        """处理一次 KEYDOWN；返回 True 表示"已接管（要吞键）"。

        独立成方法是为了能直接单测（真实钩子需要全局键盘事件才能触发）。
        `vk` 缺省时退化为状态查询（仅测试 / 非常规路径）。
        """
        if not ctrl_down():
            return False
        vk = int(vk) or self._pressed_vk()
        if vk not in (VK_C, VK_V):
            return False
        # S7：重复保护必须放在 target 判定**之后**——放在前面的话，桌面动作
        # 触发后的 350ms 窗口内用户切到别的应用按 Ctrl+C/V 会被无条件吞掉，
        # 违背本模块"宁可漏一次，不可误吞一次"的纪律
        try:
            target = (self._target_check() if self._target_check is not None
                      else target_ok(self._own_hwnds()))
        except Exception:      # noqa: BLE001
            target = False
        if not target:
            return False
        now = time.monotonic()
        if (now - self._last_at) * 1000.0 < REPEAT_GUARD_MS:
            return True                     # 长按重复：吞掉但不重复动作
        if vk == VK_C:
            try:
                if not self._can_copy():
                    return False
            except Exception:      # noqa: BLE001
                return False
            self._last_at = now
            QTimer.singleShot(0, self._do_copy)
            return True
        try:
            if not self._can_paste():
                return False
        except Exception:      # noqa: BLE001
            return False
        self._last_at = now
        QTimer.singleShot(0, self._do_paste)
        return True

    def _pressed_vk(self) -> int:
        """当前按下的键（钩子里从 KBDLLHOOKSTRUCT 取更准，这里用状态查询兜底）。"""
        try:
            for vk in (VK_C, VK_V):
                if _user32().GetAsyncKeyState(vk) & 0x8000:
                    return vk
        except Exception:      # noqa: BLE001
            pass
        return 0

    def _do_copy(self):
        try:
            self.on_copy()
        except Exception:      # noqa: BLE001
            pass

    def _do_paste(self):
        try:
            self.on_paste()
        except Exception:      # noqa: BLE001
            pass
