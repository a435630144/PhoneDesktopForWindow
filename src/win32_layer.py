# -*- coding: utf-8 -*-
"""Windows 桌面层挂载工具：主窗口与桌面小组件共用。

目标：把 Qt 窗口放进"桌面带"——位于壁纸之上、普通应用程序窗口之下。
用户点击 / 打开任何应用时，桌面带内容自动被应用窗口覆盖，不会被置顶。

实现方案（实测验证 2026-10-16）：
  不改变窗口父子关系（SetParent 会把 WS_POPUP 改成 WS_CHILD，
  Qt 半透明窗口依赖 WS_EX_LAYERED，改风格后直接不可见——已踩坑）；
  保持窗口为顶层窗口，用 SetWindowPos 把它插到 Progman（桌面窗口）
  正上方一位，即得到"壁纸之上、应用窗口之下"的桌面带。
  配合 Qt.WindowDoesNotAcceptFocus（WS_EX_NOACTIVATE），
  窗口永不被激活，因此永远不会盖到应用程序窗口前面。

  多显示器：Progman 横跨整个虚拟桌面，插入其上方对所有屏幕生效。

  部分系统在 Progman 上方还有承载原生桌面图标（SHELLDLL_DefView）的
  WorkerW——本方案插在 Progman 正上方，同样位于该图标层之下，行为一致。

实测踩坑记录（重要）：
1. 挂载前必须先 show()：未显示的窗口 IsWindowVisible 恒为 false。
2. 不要 SetParent：见上（分层子窗口不被支持，窗口直接消失）。
3. 不要 WindowStaysOnTopHint：那正是"组件覆盖所有窗口"Bug 的根因。
4. SetWindowPos(hwnd, Progman, ...) 语义：把 hwnd 插到 Progman 正上方
   （hWndInsertAfter 指"排在后面的窗口"，即位于其下）。
5. z-order 校验用 GetWindow(GW_HWNDNEXT) 从桌面窗口向下遍历，
   索引 0 为最顶层；窗口应紧邻 Progman 之上。
6. 第三方桌面软件（如腾讯桌面整理）会留下大量微型 WorkerW 残留，
   任何按"找 WorkerW 父窗口"的方案都不可靠——本方案不依赖 WorkerW。
"""
import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32
_user32.FindWindowW.restype = wintypes.HWND
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetDesktopWindow.restype = wintypes.HWND
_user32.GetDesktopWindow.argtypes = []
_user32.GetWindow.restype = wintypes.HWND
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
_user32.GetWindowLongW.restype = wintypes.LONG
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SendMessageTimeoutW.restype = wintypes.LPARAM
_user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                        wintypes.UINT, wintypes.UINT, ctypes.POINTER(wintypes.DWORD)]
_user32.ShowWindow.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

GA_PARENT = 1
GW_CHILD, GW_HWNDNEXT = 5, 2
SM_CXSCREEN, SM_CYSCREEN = 0, 1
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008

SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_NOZORDER = 0x0001, 0x0002, 0x0010, 0x0004
SW_HIDE, SW_SHOW = 0, 5
# SW_SHOWNA = 显示但**不激活**（"show no activate"）。用于「显示桌面」后把
# 被系统一并藏起来的带内窗口恢复可见，同时不抢走用户当前应用的焦点。
SW_SHOWNA = 8


def _class_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    if _user32.GetClassNameW(hwnd, buf, 256):
        return buf.value
    return ""


def _rect_of(hwnd):
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def _size_of(hwnd):
    r = _rect_of(hwnd)
    return (r.right - r.left, r.bottom - r.top)


def _enum_zorder() -> list:
    """按 z-order（从顶到底）枚举全部顶层窗口（GetWindow 遍历，顺序可靠）。"""
    out = []
    h = _user32.GetWindow(_user32.GetDesktopWindow(), GW_CHILD)
    while h:
        out.append(h)
        h = _user32.GetWindow(h, GW_HWNDNEXT)
    return out


def _screen_size():
    """主屏幕尺寸（GetSystemMetrics 虚拟屏幕指标在部分环境返回 0）。"""
    w = _user32.GetSystemMetrics(SM_CXSCREEN)
    h = _user32.GetSystemMetrics(SM_CYSCREEN)
    if w <= 0 or h <= 0:
        progman = _progman()
        if progman:
            return _size_of(progman)
        return (0, 0)
    return (w, h)


def _is_desktop_sized(hwnd) -> bool:
    """窗口是否达到桌面级尺寸：过滤第三方桌面软件残留的微型 WorkerW。"""
    try:
        w, h = _size_of(hwnd)
        sw, sh = _screen_size()
        if sw <= 0 or sh <= 0:
            return True  # 取不到屏幕尺寸时不加过滤
        return w >= sw * 0.5 and h >= sh * 0.5
    except Exception:
        return False


def _progman() -> int:
    return int(_user32.FindWindowW("Progman", None) or 0)


def _has_noactivate(hwnd) -> bool:
    ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    return bool(ex & WS_EX_NOACTIVATE)


def _is_topmost(hwnd) -> bool:
    ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    return bool(ex & WS_EX_TOPMOST)


def progman_is_topmost(progman: int = 0) -> bool:
    """Progman 是否位于 topmost 带 —— 「显示桌面」（Win+D）的可靠特征。

    正常态 Progman 垫在所有窗口之下（非 topmost）；Show Desktop 时 shell
    把它提到 topmost 带以便盖住应用窗口。

    2026-10-20 用户反馈 F1：旧判定是「Progman 落在 z-order 前 30%」
    （`pidx < max(60, total*0.3)`）——窗口总数少时会把**正常态误判**成
    show desktop，于是把我们的窗口插进 topmost 带（会盖住应用窗口）。
    改成直接读 Progman 的 `WS_EX_TOPMOST` 位，语义唯一且不依赖窗口总数。
    """
    progman = progman or _progman()
    if not progman:
        return False
    try:
        return _is_topmost(progman)
    except Exception:
        return False


def _insert_above(hwnd: int, above_of: int) -> bool:
    """把 hwnd 插到 above_of 正上方一位（不移动、不改大小、不激活）。

    注意 Win32 语义：SetWindowPos(h, insertAfter) 标准语义是把 h 放到
    insertAfter **下方**（hWndInsertAfter 是"前驱"）。Progman 是例外——
    实测 SetWindowPos(h, Progman) 会把 h 插到 Progman 正上方一位
    （attach_to_desktop 依赖该行为，见模块头注释 4）。
    对普通窗口要用 insert_above_window()。
    """
    return bool(_user32.SetWindowPos(
        hwnd, above_of, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))


GW_HWNDPREV = 3
HWND_TOP = 0


def insert_above_window(hwnd: int, above_of: int) -> bool:
    """把 hwnd 插到普通窗口 above_of 的正上方一位。

    实现：先取 above_of 的前驱（z-order 中位于其上的窗口），再把 hwnd
    插到该前驱下方（标准 SetWindowPos 语义）= above_of 上方。
    above_of 已是 z-order 最顶时退化为 HWND_TOP。
    用于 FolderPopup 插到整理窗口之上（2026-10-18：弹窗在整理窗口之下
    会被全窗口 HTCLIENT 的命中测试挡住，点不到）。
    """
    prev = int(_user32.GetWindow(above_of, GW_HWNDPREV) or 0)
    if prev:
        return _insert_above(hwnd, prev)
    return bool(_user32.SetWindowPos(
        hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))


def is_in_desktop_band(hwnd: int, progman: int = 0) -> bool:
    """hwnd 是否位于桌面带（Progman 正上方一位）。"""
    progman = progman or _progman()
    if not progman or not hwnd:
        return False
    wins = _enum_zorder()
    if hwnd not in wins or progman not in wins:
        return False
    return wins.index(hwnd) == wins.index(progman) - 1


def is_above(hwnd: int, other: int) -> bool:
    """hwnd 是否在 other 之上（z-order 索引更小）。"""
    wins = _enum_zorder()
    if hwnd not in wins or other not in wins:
        return False
    return wins.index(hwnd) < wins.index(other)


# ---------- 桌面带成员注册表（M5：弹窗「带内最顶」用） ----------
# attach_to_desktop 成功时登记 hwnd。band_topmost_hwnd() 供文件夹弹窗
# 找带内当前最顶成员（整理窗口 + 各小组件 + 幕布），把弹窗插到其上——
# 保证弹窗压得住桌面带内所有元素（指导文档 §4.2，决策 D5）。
# 关闭的窗口会从 z-order 枚举里消失，过期登记项由 band_topmost_hwnd
# 过滤；显式 unregister 供 close 路径调用。
_BAND_HWNDS = []

# ---------- 桌面带层级（2026-10-19 用户反馈 1/7 的根因） ----------
# 病根：旧守护只认「紧贴 Progman 上方一位」＝在带内，其余一律重插。桌面窗口、
# 各小组件、幕布、弹窗同时被跟踪时，每个窗口都只有最底下那个满足该判定，
# 于是每 1.5s 全体被重插一遍 —— 组件被压到桌面窗口之下（点不到、右键穿透到
# 桌面菜单）、层级反复抖动（文件夹玻璃闪、焦点被桌面抢回）。
#
# 现在改成**显式层号 + 稳定重排**：层号小的在下面；只有相对顺序真的被破坏
# 时才动 z-order（正常情况零操作，不再抖）。
BAND_LAYER_DESKTOP = 10   # 全屏整理窗口（图标层）
BAND_LAYER_WIDGET = 20    # 小组件（必须在图标层之上，否则整窗 HTCLIENT 会吞事件）
BAND_LAYER_DOCK = 25      # Dock 栏（v10 C3：压住组件，但被文件夹幕布/弹窗盖住）
BAND_LAYER_SCRIM = 30     # 文件夹幕布
BAND_LAYER_POPUP = 40     # 文件夹弹窗（带内最顶）

_BAND_LAYERS = {}         # hwnd -> layer


def set_band_layer(hwnd: int, layer: int):
    """登记桌面带成员及其层号（数值越小越靠下）。"""
    try:
        hwnd = int(hwnd)
    except (TypeError, ValueError):
        return
    if hwnd:
        _BAND_LAYERS[hwnd] = int(layer)
        if hwnd not in _BAND_HWNDS:
            _BAND_HWNDS.append(hwnd)


def band_layer(hwnd: int):
    return _BAND_LAYERS.get(int(hwnd))


def band_order(zorder=None) -> list:
    """按层号排出我们自己的桌面带窗口（自底向上）。"""
    wins = list(zorder) if zorder is not None else _enum_zorder()
    items = [(hw, ly) for hw, ly in _BAND_LAYERS.items()
             if hw in wins and _user32.IsWindowVisible(hw)]
    items.sort(key=lambda t: (t[1], wins.index(t[0])))
    return [hw for hw, _ in items]


def enforce_band_order(progman: int = 0, zorder=None) -> bool:
    """把桌面带窗口按层号重排成「Progman 上方连续一段」。

    只在顺序被破坏时动作（返回 True 表示做了重排）——正常态每轮只枚举一次
    z-order，零 SetWindowPos，避免旧实现的周期性抖动。
    """
    progman = progman or _progman()
    wins = list(zorder) if zorder is not None else _enum_zorder()
    if not progman or progman not in wins:
        return False
    order = band_order(wins)
    if not order:
        return False
    pidx = wins.index(progman)
    ok = wins.index(order[0]) == pidx - 1
    for prev, cur in zip(order, order[1:]):
        if not ok:
            break
        ok = wins.index(cur) == wins.index(prev) - 1
    if ok:
        return False
    prev = 0
    for hw in order:
        if prev:
            insert_above_window(hw, prev)
        else:
            _insert_above(hw, progman)
        prev = hw
    return True


def register_band_window(hwnd: int, layer: int = 0):
    """登记桌面带成员（attach_to_desktop 成功时调用）。

    layer 缺省时沿用已登记层号；都没有则按「最底层」处理——旧调用点
    （探针 / 外部脚本）不需要改动。
    """
    hwnd = int(hwnd)
    if not hwnd:
        return
    if layer:
        set_band_layer(hwnd, layer)
    elif hwnd not in _BAND_HWNDS:
        _BAND_HWNDS.append(hwnd)


def unregister_band_window(hwnd: int):
    """注销桌面带成员（窗口关闭路径调用）。"""
    try:
        _BAND_LAYERS.pop(int(hwnd), None)
    except (TypeError, ValueError):
        pass
    try:
        _BAND_HWNDS.remove(int(hwnd))
    except ValueError:
        pass


def band_topmost_hwnd(exclude_hwnd: int = 0, zorder=None) -> int:
    """桌面带内当前最顶成员的 hwnd（z-order 索引最小；排除 exclude）。

    zorder 可注入（单测用；缺省实时枚举）。过期登记（已关闭窗口）自动
    过滤。无成员返回 0（调用方回退主窗口）。
    """
    wins = list(zorder) if zorder is not None else _enum_zorder()
    best = 0
    for hw in list(_BAND_HWNDS):
        if hw == exclude_hwnd or hw not in wins:
            continue
        if best == 0 or wins.index(hw) < wins.index(best):
            best = hw
    return best


def restore_band_windows(items, progman: int = 0, zorder=None) -> int:
    """「显示桌面」态：把带内窗口恢复可见并压回 Progman 正上方。

    2026-10-20 用户反馈 F1：接管模式下按 Win+D / 点任务栏末端「显示桌面」，
    Show Desktop 会把**所有顶层窗口**一并藏起来（我们也在其中），而原生
    图标已被接管隐藏 → 用户看到**完全空的桌面**。

    这里对每个带内成员：
      1. 不可见就 `ShowWindow(SW_SHOWNA)`——显示但**不激活**，不抢用户
         当前应用的焦点（已可见时幂等无副作用，故不做状态判断缓存）；
      2. 按层号自底向上插到 Progman **正上方**：此刻 Progman 在 topmost
         带，插在它下方（普通带）仍会被全屏壁纸盖住，等于没恢复。

    items 为 [(hwnd, layer)]。返回被恢复可见的窗口数（0 = 本来就都可见）。
    """
    progman = progman or _progman()
    if not progman:
        return 0
    pairs = sorted([(int(h), int(ly)) for h, ly in items if h],
                   key=lambda t: t[1])
    if not pairs:
        return 0
    restored = 0
    prev = progman
    for hw, _ly in pairs:
        try:
            if not _user32.IsWindow(hw):
                continue
            if not _user32.IsWindowVisible(hw):
                _user32.ShowWindow(hw, SW_SHOWNA)
                restored += 1
        except Exception:
            continue
        # 层内相对顺序：首个插 Progman 正上方，其余插到上一个成员上方
        if prev == progman:
            _insert_above(hw, progman)
        else:
            insert_above_window(hw, prev)
        prev = hw
    return restored


def attach_to_desktop(widget, below_hwnd: int = 0, layer: int = 0) -> bool:
    """把 Qt 窗口放入桌面带（壁纸之上、应用窗口之下）。

    调用方必须已经 show() 过该窗口。below_hwnd 可选：给定另一个
    桌面带窗口句柄时，把本窗口插到它正下方（例如小组件放在图标层之下）。
    失败返回 False，调用方自行兜底（如悬浮模式）。
    """
    try:
        hwnd = int(widget.winId())
        progman = _progman()
        if not progman:
            return False
        if below_hwnd:
            # 先确保参照窗口自身在桌面带，否则会把本窗口一起带出桌面带
            if not is_in_desktop_band(below_hwnd, progman):
                if not _insert_above(below_hwnd, progman):
                    return False
            ok = _insert_above(hwnd, below_hwnd)
        else:
            ok = _insert_above(hwnd, progman)
        if not ok:
            return False
        # 校验：必须在 Progman 之上（紧邻其上，或下方隔着同带窗口）
        for _ in range(10):
            if is_above(hwnd, progman):
                break
            ctypes.windll.kernel32.Sleep(50)
        if is_above(hwnd, progman):
            register_band_window(hwnd, layer)
            return True
        return False
    except Exception:
        return False


def native_icon_size(default: int = 48) -> int:
    """资源管理器当前桌面图标边长（逻辑像素）——「原桌面图标多大」。

    来源（按可靠性排序）：
      1. `HKCU\\Software\\Microsoft\\Windows\\Shell\\Bags\\1\\Desktop\\IconSize`
         （explorer 自己记的值，逻辑像素：125% 缩放下 48 会渲染成 60 物理）；
      2. `SPI_GETICONMETRICS.iIconSize`（老系统 / Bags 缺失时的系统值）；
      3. 回退 default（Windows 默认「中图标」48）。
    夹到 [16, 256]，脏值不至于炸掉网格。
    """
    raw = 0
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Bags\1\Desktop") as k:
            raw = int(winreg.QueryValueEx(k, "IconSize")[0])
    except Exception:
        raw = 0
    if not (16 <= raw <= 256):
        try:
            import ctypes
            from ctypes import wintypes

            class _ICONMETRICS(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT),
                            ("iHorSpacing", ctypes.c_int),
                            ("iVertSpacing", ctypes.c_int),
                            ("iIconSize", ctypes.c_int),
                            ("iMenuFont", ctypes.c_int)]

            m = _ICONMETRICS()
            m.cbSize = ctypes.sizeof(_ICONMETRICS)
            ok = _user32.SystemParametersInfoW(0x002B, 0, ctypes.byref(m), 0)
            if ok and 16 <= int(m.iIconSize) <= 256:
                raw = int(m.iIconSize)
        except Exception:
            raw = 0
    if not (16 <= raw <= 256):
        raw = int(default)
    return max(16, min(256, int(raw)))


def native_icon_spacing(default=(75, 75)):
    """原生桌面的图标水平 / 垂直间距（逻辑像素，ICONMETRICS 单位）。

    仅用于「按原生密度铺网格」的参考；读不到返回 Windows 默认 75x75。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _ICONMETRICS(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT),
                        ("iHorSpacing", ctypes.c_int),
                        ("iVertSpacing", ctypes.c_int),
                        ("iIconSize", ctypes.c_int),
                        ("iMenuFont", ctypes.c_int)]

        m = _ICONMETRICS()
        m.cbSize = ctypes.sizeof(_ICONMETRICS)
        if _user32.SystemParametersInfoW(0x002B, 0, ctypes.byref(m), 0):
            h = max(16, int(m.iHorSpacing))
            v = max(16, int(m.iVertSpacing))
            return h, v
    except Exception:
        pass
    return int(default[0]), int(default[1])


def diagnose_desktop_layer() -> dict:
    """诊断桌面层结构（排查挂载 / 层级问题用）。"""
    progman = _progman()
    wins = _enum_zorder()
    workerws = [h for h in wins if _class_of(h) == "WorkerW"]
    pos = wins.index(progman) if progman in wins else -1
    band = [h for h in wins[:pos]] if pos >= 0 else []  # Progman 之上的窗口
    # 只统计窗口类名为 Qt 的（我们自己的窗口），其余是应用窗口
    qt_band = [h for h in band if "Qt" in _class_of(h)]
    return {
        "progman": progman,
        "progman_size": str(_size_of(progman)) if progman else None,
        "progman_visible": bool(progman and _user32.IsWindowVisible(progman)),
        "screen_size": _screen_size(),
        "total_toplevel": len(wins),
        "progman_zindex": pos,
        "workerw_total": len(workerws),
        "workerw_desktop_sized": len([h for h in workerws if _is_desktop_sized(h)]),
        "band_window_count": len(band),
        "band_qt_windows": qt_band,
        "native_icons_hidden": native_icons_hidden(),
        "third_party_overlays": find_third_party_overlays(),
        "third_party_overlays_visible": third_party_overlays_visible(),
    }


# ---------- 原生桌面图标隐藏 / 恢复 ----------
# 第三方桌面的标准做法：找到承载原生图标的 SHELLDLL_DefView（Progman 或
# WorkerW 的子窗口）并隐藏，桌面文件改由我们克隆渲染。
#
# **2026-10-19 修正（用户反馈"开启后背景全黑，看不到壁纸"）**：
# 不能隐藏整个 SHELLDLL_DefView——在部分系统（本机 Win11 build 26300 实测）
# DefView 参与壁纸绘制链，SW_HIDE 会连壁纸一起关掉，桌面只剩纯黑。
# 正确做法是只隐藏它内部的图标列表 SysListView32：图标消失、壁纸保留。
# 这条路径同时也是动态壁纸（Wallpaper Engine / Lively 等把渲染窗口挂在
# WorkerW 下）能继续工作的前提——容器还在，壁纸层才不会被一起摘掉。
#
# 退出 / 崩溃自愈时必须恢复，否则用户将看不到任何桌面图标。

_ICON_LIST_CLASS = "SysListView32"

# **2026-10-19 第二轮（用户反馈"壁纸有了，但原生图标怎么也出来了、挤到一起"）**：
# 只藏 SysListView32 还不够——第三方桌面整理软件会在 DefView 下再挂一个
# 全屏图层，自己画一份桌面图标。本机实测（腾讯桌面整理 DeskGo）：
#     SHELLDLL_DefView
#       ├─ TXMiniSkin      ← DesktopMgr64.exe，1536×864 全屏，自绘图标
#       └─ SysListView32   ← explorer 的图标列表
# 藏了后者，前者照旧渲染，两套图标错位叠在一起就是"挤到一起"。
# 实验（tools/probe_deskgoskin*.py）：隐藏 TXMiniSkin → 那套大图标整体消失、
# 壁纸正常、12 秒内不会被抢回。
#
# 识别规则：DefView 下「桌面级尺寸 + 非系统图标列表」的可见子窗口一律视为
# 第三方桌面层。系统控件（SysListView32 / SysHeader32 / WorkerW）排除；
# 微型残留窗口由尺寸过滤掉（不会误伤）。
_OVERLAY_SKIP_CLASSES = {_ICON_LIST_CLASS, "SysHeader32", "WorkerW"}

# 我们亲手藏掉的第三方图层：{hwnd: 类名}。恢复时只 SHOW 这些，
# 原本就隐藏的自绘层不去动它（尊重第三方自己的状态）。类名一并记着，
# 落盘后即使句柄被回收也能校验，不会误 SHOW 到别的窗口上。
_third_party_overlays = {}

_native_icons_hidden = False


def _iter_children(parent):
    h = _user32.GetWindow(parent, GW_CHILD)
    while h:
        yield int(h)
        h = _user32.GetWindow(h, GW_HWNDNEXT)


def _find_defview() -> int:
    """定位 SHELLDLL_DefView：先找 Progman 子窗口，再找各 WorkerW 子窗口。"""
    progman = _progman()
    if not progman:
        return 0
    for h in _iter_children(progman):
        if _class_of(h) == "SHELLDLL_DefView":
            return h
    h = _user32.GetWindow(_user32.GetDesktopWindow(), GW_CHILD)
    while h:
        if _class_of(h) == "WorkerW":
            for c in _iter_children(h):
                if _class_of(c) == "SHELLDLL_DefView":
                    return c
        h = _user32.GetWindow(h, GW_HWNDNEXT)
    return 0


def _find_icon_list(defview: int = 0) -> int:
    """DefView 里的图标列表 SysListView32（隐藏图标的最小目标）。"""
    dv = int(defview) or _find_defview()
    if not dv:
        return 0
    for c in _iter_children(dv):
        if _class_of(c) == _ICON_LIST_CLASS:
            return c
    return 0


def set_native_icons_visible(visible: bool) -> bool:
    """显示 / 隐藏系统原生桌面图标。返回操作后的实际状态是否符合预期。

    隐藏时**只摘图标列表**并确保 DefView 可见（壁纸随 DefView 一起存活）；
    只有找不到 SysListView32 的老系统才回退到隐藏整个 DefView。
    同时连第三方桌面整理软件自绘的全屏图标层一起藏（见上方注释与
    find_third_party_overlays），否则会与我们的网格图标叠在一起。
    """
    global _native_icons_hidden
    dv = _find_defview()
    if not dv:
        return bool(visible)  # 找不到 DefView 时视为原生图标本就可见
    target = _find_icon_list(dv) or dv
    # 先让容器可见——旧版本可能已把 DefView 藏了（升级上来的用户），
    # 不显式 SHOW 会一直黑屏；target == dv 时下一行会重新盖回 HIDE。
    _user32.ShowWindow(dv, SW_SHOW)
    _user32.ShowWindow(target, SW_SHOW if visible else SW_HIDE)
    if visible:
        _restore_third_party_overlays()
    else:
        _hide_third_party_overlays()
    _native_icons_hidden = not visible
    return bool(_user32.IsWindowVisible(target)) == bool(visible)


def native_icons_hidden() -> bool:
    return _native_icons_hidden


def find_third_party_overlays(defview: int = 0) -> list:
    """DefView 下由第三方桌面整理软件自绘的桌面级图层（如腾讯桌面整理 TXMiniSkin）。

    这些软件会自己再画一份桌面图标，只藏 SysListView32 时它们仍在原处
    渲染，和我们的网格图标错位叠在一起（用户反馈"原生图标怎么也出来了
    挤到一起了"）。返回 hwnd 列表（含当前已隐藏的，调用方自行判断可见性）。
    """
    dv = int(defview) or _find_defview()
    if not dv:
        return []
    out = []
    for c in _iter_children(dv):
        if _class_of(c) in _OVERLAY_SKIP_CLASSES:
            continue
        if not _is_desktop_sized(c):
            continue
        out.append(int(c))
    return out


def third_party_overlays_visible() -> bool:
    """是否有第三方桌面图标层当前可见（守护轮询用来发现「被抢回」）。"""
    try:
        return any(bool(_user32.IsWindowVisible(h))
                   for h in find_third_party_overlays())
    except Exception:
        return False


def native_icon_list_visible() -> bool:
    """图标列表此刻是否可见（真实查询，不读缓存标志）。

    守护轮询用：explorer 重启 / 重建列表后句柄会换，缓存标志仍为 True
    却已经露出图标——只有实查才能发现。
    """
    try:
        lv = _find_icon_list()
        return bool(lv and _user32.IsWindowVisible(lv))
    except Exception:
        return False


def _hide_third_party_overlays():
    _adopt_stale_overlay_state()
    for h in find_third_party_overlays():
        try:
            if _user32.IsWindowVisible(h):
                _third_party_overlays[h] = _class_of(h)
                _user32.ShowWindow(h, SW_HIDE)
        except Exception:
            continue
    _save_overlay_state()


def _restore_third_party_overlays():
    """只恢复「进来之前可见、被我们藏掉」的图层；原本隐藏的不动。

    先接管上个进程留下的记录（崩溃 / 被强杀时进程内字典已丢），
    否则外部恢复脚本、新进程都无从知道要 SHOW 谁。
    """
    _adopt_stale_overlay_state()
    for h in list(_third_party_overlays):
        try:
            if _user32.IsWindow(h) and not _user32.IsWindowVisible(h):
                _user32.ShowWindow(h, SW_SHOW)
        except Exception:
            pass
        _third_party_overlays.pop(h, None)
    _save_overlay_state()


# ---------- 跨进程自愈：被强杀时第三方图层不能永久消失 ----------
# 进程内字典挡不住 taskkill / 崩溃：新进程不知道上一个进程藏了谁，
# 用户会发现腾讯桌面整理的挂件再也回不来（与当年 DefView 黑屏同类事故）。
# 所以把"我们藏了谁（hwnd + 类名）"落盘，恢复路径 / 下次启动都能接上。
# 记录里带类名，防止句柄被系统回收后误 SHOW 到别的窗口上。
_OVERLAY_STATE_PATH = None   # 测试可注入临时路径


def _overlay_state_path() -> str:
    global _OVERLAY_STATE_PATH
    if _OVERLAY_STATE_PATH is None:
        from .paths import data_file
        _OVERLAY_STATE_PATH = str(data_file("hidden_overlays.json"))
    return _OVERLAY_STATE_PATH


def _save_overlay_state():
    import json
    import os
    path = _overlay_state_path()
    try:
        if not _third_party_overlays:
            if os.path.exists(path):
                os.remove(path)
            return
        data = {"windows": [{"hwnd": int(h), "cls": str(c)}
                            for h, c in _third_party_overlays.items()]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _load_overlay_state() -> list:
    import json
    import os
    path = _overlay_state_path()
    try:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        out = []
        for rec in raw.get("windows") or []:
            try:
                out.append((int(rec["hwnd"]), str(rec.get("cls") or "")))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _adopt_stale_overlay_state():
    """接手上一个进程留下的隐藏记录（窗口仍在且类名对得上才认）。"""
    for h, cls in _load_overlay_state():
        if h in _third_party_overlays:
            continue
        try:
            if _user32.IsWindow(h) and (not cls or _class_of(h) == cls):
                _third_party_overlays[h] = cls
        except Exception:
            continue


def restore_native_icons():
    """恢复原生桌面图标（退出清理 / 崩溃自愈，重复调用安全）。

    必须无条件恢复：_native_icons_hidden 是进程私有标志，跨进程调用
    （外部恢复脚本、verify 工具、其他实例）时恒为 False，带条件会直接
    跳过恢复——用户桌面将停留在无图标状态直到重启应用。
    SW_SHOW 对已可见窗口无害，幂等安全。
    """
    set_native_icons_visible(True)


# ---------------------------------------------------------------- 回收站删除（v14w）

def recycle_paths(paths) -> bool:
    """把本地路径送进回收站（SHFileOperationW，FOF_ALLOWUNDO）。

    「删除进回收站」语义的可选实现（设置 delete_to_recycle_bin，默认关
    保持「从整理桌面移除」的保守语义）。任一失败返回 False——调用方
    回退为仅移除条目。
    """
    import ctypes
    from ctypes import wintypes as wt

    paths = [str(p) for p in (paths or []) if p]
    if not paths:
        return False
    # 双 NUL 结尾的多串列表
    nul = chr(0)
    buf = "".join(p + nul for p in paths) + nul

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wt.HWND),
                    ("wFunc", ctypes.c_uint),
                    ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wt.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", ctypes.c_wchar_p)]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x4
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = buf
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    r = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return r == 0 and not op.fAnyOperationsAborted
