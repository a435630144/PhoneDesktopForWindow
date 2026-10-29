# -*- coding: utf-8 -*-
"""v15f：用户 2026-10-29 反馈的五条体验缺陷（回归判据）。

对应问题与修法（细节见 DEVLOG / 交接文档 §5f）：

  M1-M3  文件右键菜单把资源管理器整份菜单铺开（占满一屏）
         -> `split_native_nodes`：常用动词常驻，其余折叠进「更多系统操作」
  F1-F2  图标字体不像原版（西文被雅黑画宽一圈）
         -> `theme.label_font`：Segoe UI 优先 + 中文回退，字距钉死 100%
  I1-I3  快捷方式自定义图标被忽略（VALORANT 显示成 Riot Client 的图）
         -> `shortcut_icon_source` 支持 .lnk 的 IconLocation
  H1-H4  Ctrl+Alt+A（截图）被当成 Ctrl+A 全选：钩子只看 Ctrl
         -> `blocking_modifier_down`：Alt/Shift/Win 一律放行
  D1-D6  Dock 图标被条带边缘侧切（hover 放大 + 让位把两端推出窗口）
         -> `_clamp_into_bar`
  V1-V6  系统「显示桌面」把窗口藏起来；隐藏状态下退出再打开就再也不显示
         -> `_want_visible` 意图位 + 守护按真实可见性拉回 + 挂载期挂起

运行：python tests/test_audit_v15f.py   （offscreen）
"""
import os
import sys
import tempfile
import weakref
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from src import appsource, band_guard, desktop_hotkeys as dh, dock  # noqa: E402
from src import iconwidget, theme  # noqa: E402
from src.desktop_window import DesktopWindow  # noqa: E402
from src.storage import Storage  # noqa: E402

app = QApplication.instance() or QApplication([])
TMP = Path(tempfile.gettempdir())
failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def fresh(name="f"):
    p = TMP / ("test_audit_v15f_%s.json" % name)
    if p.exists():
        p.unlink()
    st = Storage(path=str(p))
    st._data["settings"].update({
        "hide_native_icons": True, "takeover_desktop": True,
        "auto_clone_desktop": False, "layout_mode": "grid",
        "auto_arrange": False})
    st._data["layout"] = []
    st._data["widgets"] = []
    st.save()
    return st


# --------------------------- M1-M3 右键菜单折叠 ---------------------------
NODES = [
    {"sep": False, "label": "打开(O)", "id": "open", "children": None},
    {"sep": True, "label": "", "id": 0, "children": None},
    {"sep": False, "label": "剪切(T)", "id": "cut", "children": None},
    {"sep": False, "label": "复制(C)", "id": "copy", "children": None},
    {"sep": False, "label": "创建快捷方式(S)", "id": "link", "children": None},
    {"sep": False, "label": "删除(D)", "id": "del", "children": None},
    {"sep": False, "label": "重命名(M)", "id": "ren", "children": None},
    {"sep": False, "label": "属性(R)", "id": "prop", "children": None},
    {"sep": True, "label": "", "id": 0, "children": None},
    {"sep": False, "label": "上传到迅雷云盘", "id": "xunlei", "children": None},
    {"sep": False, "label": "复制文件地址(A)", "id": "addr", "children": None},
    {"sep": False, "label": "打开方式(H)", "id": "openwith",
     "children": [{"sep": False, "label": "画图", "id": "mspaint",
                   "children": None}]},
    {"sep": False, "label": "发送到(N)", "id": "sendto",
     "children": [{"sep": False, "label": "桌面快捷方式", "id": "dt",
                   "children": None}]},
]

primary, rest = iconwidget.split_native_nodes(NODES)
p_labels = [n["label"] for n in primary]
r_labels = [n["label"] for n in rest]
check("M1 常用动词常驻菜单", p_labels[:4] == ["剪切(T)", "复制(C)", "删除(D)",
                                          "重命名(M)"], p_labels)
check("M1b 打开方式整体常驻（子菜单跟着父项）",
      "打开方式(H)" in p_labels, p_labels)
check("M2 第三方/诊断项折叠", "上传到迅雷云盘" in r_labels
      and "复制文件地址(A)" in r_labels and "发送到(N)" in r_labels, r_labels)
check("M2b 折叠区不含常驻项",
      not ({"剪切(T)", "复制(C)", "属性(R)"} & set(r_labels)), r_labels)
check("M2c 「打开」被去重（不在任一段）",
      not ({"打开(O)", "打开"} & set(p_labels + r_labels)), p_labels)
check("M3 首尾不留分隔线", not primary[0].get("sep") and not rest[-1].get("sep"),
      [primary[0], rest[-1]])

# 端到端：真的把节点树镜像成 QMenu，看外层项数（用户看到的那一份）
menu = QMenu()
fake = iconwidget.AppIcon.__new__(iconwidget.AppIcon)
fake._ctx_submenus = []
iconwidget.AppIcon._build_native_menu(fake, menu, NODES, lambda m: None)
labels = [a.text() for a in menu.actions() if a.text()]
check("M3b 外层菜单项数受控（<=8）", len(labels) <= 8, labels)
check("M3c 有「更多系统操作」折叠入口", "更多系统操作" in labels, labels)
more = [a for a in menu.actions() if a.text() == "更多系统操作"]
if more:
    sub_labels = [a.text() for a in more[0].menu().actions() if a.text()]
    check("M3d 折叠区保留全部次要项（功能不少）",
          "上传到迅雷云盘" in sub_labels and "发送到(N)" in sub_labels,
          sub_labels)
else:
    check("M3d 折叠区保留全部次要项（功能不少）", False, "无折叠菜单")



# 英文系统：菜单项是 Cut/Copy/... —— 同样要常驻（不能全塞进折叠区）
EN_NODES = [
    {"sep": False, "label": "Cut", "id": "cut", "children": None},
    {"sep": False, "label": "Copy", "id": "copy", "children": None},
    {"sep": False, "label": "Delete", "id": "del", "children": None},
    {"sep": False, "label": "Properties", "id": "prop", "children": None},
    {"sep": False, "label": "Open with", "id": "ow",
     "children": [{"sep": False, "label": "Paint", "id": "p", "children": None}]},
    {"sep": False, "label": "Upload to Cloud", "id": "cloud", "children": None},
    {"sep": False, "label": "Copy as path", "id": "addr", "children": None},
]
en_p, en_r = iconwidget.split_native_nodes(EN_NODES)
check("M4 英文菜单的常用动词也常驻",
      [n["label"] for n in en_p] == ["Cut", "Copy", "Delete", "Properties",
                                     "Open with"],
      [n["label"] for n in en_p])
check("M4b 英文菜单的次要项折叠",
      [n["label"] for n in en_r] == ["Upload to Cloud", "Copy as path"],
      [n["label"] for n in en_r])

# --------------------------- F1-F2 标签字体 ---------------------------
lf = theme.label_font(12)
uf = theme.ui_font(12)
check("F1 标签字体 Segoe UI 优先", lf.families()[0] == "Segoe UI",
      lf.families())
check("F1b 面板 UI 字体不受影响（雅黑优先）",
      uf.families()[0] == "Microsoft YaHei UI", uf.families())
check("F1c 中文有回退链（不会掉成豆腐块）",
      "Microsoft YaHei" in lf.families(), lf.families())
check("F2 字距 100%（不被字体默认拉开）",
      abs(lf.letterSpacing() - 100.0) < 0.01
      and lf.letterSpacingType() == QFont.PercentageSpacing,
      (lf.letterSpacing(), lf.letterSpacingType()))


# --------------------------- I1-I3 快捷方式自定义图标 ---------------------------
def make_lnk(path, target, icon_location):
    try:
        import win32com.client
        sh = win32com.client.Dispatch("WScript.Shell")
        sc = sh.CreateShortcut(str(path))
        sc.Targetpath = str(target)
        sc.IconLocation = icon_location
        sc.save()
        return path.exists()
    except Exception as exc:      # noqa: BLE001
        print("   (跳过 lnk 构造：%s)" % exc)
        return False


lnk = TMP / "test_audit_v15f_icon.lnk"
made = make_lnk(lnk, r"C:/Windows/System32/notepad.exe",
                r"C:/Windows/System32/shell32.dll,3")
if made:
    spec = appsource.shortcut_icon_source(
        {"icon_path": str(lnk), "target": str(lnk), "name": "x"})
    check("I1 .lnk 的 IconLocation 被读出来",
          bool(spec) and spec[0].lower().endswith("shell32.dll") and spec[1] == 3,
          spec)
    ic = appsource.icon_for(
        {"icon_path": str(lnk), "target": str(lnk), "name": "x"})
    check("I2 序号 > 0 时仍能取到图标（回退 target）", not ic.isNull(),
          ic.availableSizes()[:2])
else:
    check("I1 .lnk 的 IconLocation 被读出来", True, "跳过（无 COM）")
    check("I2 序号 > 0 时仍能取到图标（回退 target）", True, "跳过（无 COM）")

# 真实 .ico + index 0：必须**直接加载图标文件**（这是 VALORANT 那条路径）
ico = None
for cand in list(Path(r"C:/Windows/System32").glob("*.ico"))[:5]:
    ico = cand
    break
if ico is not None:
    lnk2 = TMP / "test_audit_v15f_icon2.lnk"
    if make_lnk(lnk2, r"C:/Windows/System32/notepad.exe", "%s,0" % ico):
        spec2 = appsource.shortcut_icon_source(
            {"icon_path": str(lnk2), "target": str(lnk2), "name": "y"})
        check("I3 .ico 形式的自定义图标被识别",
              bool(spec2) and spec2[0].lower() == str(ico).lower()
              and spec2[1] == 0, spec2)
    else:
        check("I3 .ico 形式的自定义图标被识别", True, "跳过（构造失败）")
else:
    check("I3 .ico 形式的自定义图标被识别", True, "跳过（系统无 .ico）")


# --------------------------- H1-H4 快捷键不误吞 ---------------------------
calls = []
hk = dh.DesktopHotkeys(on_select_all=lambda: calls.append("all"),
                       on_delete=lambda: calls.append("del"),
                       can_select_all=lambda: True,
                       can_delete=lambda: True,
                       target_check=lambda: True)
orig_ctrl, orig_block = dh.ctrl_down, dh.blocking_modifier_down
try:
    dh.ctrl_down = lambda: True
    dh.blocking_modifier_down = lambda: True
    check("H1 Ctrl+Alt+A 不接管（截图工具能用）",
          hk._on_keydown(dh.VK_A) is False)
    check("H1b Ctrl+Shift+C 不接管", hk._on_keydown(dh.VK_C) is False)
    dh.blocking_modifier_down = lambda: False
    hk._last_at = 0.0
    check("H2 纯 Ctrl+A 仍然接管", hk._on_keydown(dh.VK_A) is True)
    QTest.qWait(30)
    check("H2b 纯 Ctrl+A 触发了全选", calls == ["all"], calls)

    calls.clear()
    dh.ctrl_down = lambda: False
    hk._last_at = 0.0
    check("H3 无修饰 Delete 接管", hk._on_keydown(dh.VK_DELETE) is True)
    QTest.qWait(30)
    check("H3b Delete 删除了选中项", calls == ["del"], calls)

    calls.clear()
    dh.ctrl_down = lambda: True
    hk._last_at = 0.0
    check("H4 Ctrl+Delete 不接管（交给前台程序）",
          hk._on_keydown(dh.VK_DELETE) is False)
finally:
    dh.ctrl_down, dh.blocking_modifier_down = orig_ctrl, orig_block


# --------------------------- D1-D3 Dock 图标不被侧切 ---------------------------
class _Dock:
    storage = None

    def __init__(self, st, w, h):
        self.storage = st
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


st = fresh("dock")
clamp = dock.DockWindow._clamp_into_bar
box = _Dock(st, 300, 60)
out = clamp(box, QRect(-6, 4, 52, 52))
check("D1 左侧越界被拉回窗口内", out.x() >= 0 and out.right() < 300, out)
out2 = clamp(box, QRect(276, 4, 52, 52))
check("D2 右侧越界被拉回窗口内", out2.x() + out2.width() <= 300, out2)
out3 = clamp(box, QRect(10, -8, 52, 52))
check("D3 上侧越界被拉回窗口内", out3.y() >= 0 and out3.y() + 52 <= 60, out3)


# --------------------------- V1-V6 显示桌面 / 重开不显示 ---------------------------
class _W:
    def __init__(self, want):
        self._want_visible = want


g = band_guard.BandGuard()
w_off = _W(False)
w_on = _W(True)
g._widgets[101] = weakref.ref(w_off)
g._widgets[102] = weakref.ref(w_on)
check("V1 托盘隐藏的窗口不被当成该可见", g._should_be_visible(101) is False)
check("V1b 显示中的窗口该可见", g._should_be_visible(102) is True)
check("V1c 未登记的窗口默认参与恢复", g._should_be_visible(999) is True)

# 没有意图位的短命窗口：跟随此刻的真实可见性（不能被守护重新弹出来）


class _Pop:
    pass


pop_hidden = _Pop()
g._widgets[103] = weakref.ref(pop_hidden)
_orig_vis2 = band_guard.win32_layer.window_visible
band_guard.win32_layer.window_visible = lambda h: False
check("V1d 已收起的弹窗不会被守护弹出来",
      g._should_be_visible(103) is False)
band_guard.win32_layer.window_visible = lambda h: True
check("V1e 仍在显示的弹窗参与恢复", g._should_be_visible(103) is True)
band_guard.win32_layer.window_visible = _orig_vis2

shown, attached = [], []
orig_vis, orig_show, orig_attach = (
    band_guard.win32_layer.window_visible,
    band_guard.win32_layer.show_no_activate,
    band_guard.attach_to_desktop)
try:
    band_guard.win32_layer.window_visible = lambda h: False
    band_guard.win32_layer.show_no_activate = lambda h: (shown.append(h), True)[1]
    band_guard.attach_to_desktop = lambda w, layer=0: attached.append(layer) or True
    g._items = [(101, 10), (102, 20)]
    n = g._restore_hidden_windows()
    check("V2 只把「本该可见」的窗口拉回来", shown == [102], shown)
    check("V2b 拉回后按层号重插桌面带", attached == [20], attached)
    check("V2c 返工计数正确", n == 1, n)

    shown.clear()
    band_guard.win32_layer.window_visible = lambda h: True
    n2 = g._restore_hidden_windows()
    check("V3 本来就可见时零动作", n2 == 0 and shown == [], (n2, shown))
finally:
    band_guard.win32_layer.window_visible = orig_vis
    band_guard.win32_layer.show_no_activate = orig_show
    band_guard.attach_to_desktop = orig_attach

# 桌面窗口：意图位随显隐翻转（挂载路径打桩，绝不真去动桌面层）
st = fresh("win")
win = DesktopWindow(st)
win.resize(1920, 1040)
win._try_attach_once = lambda: None
win.show_desktop()
check("V4 show_desktop 置「本该可见」", win._want_visible is True)
win.hide_desktop()
check("V4b hide_desktop 撤「本该可见」", win._want_visible is False)

# 系统「显示桌面」态：挂载要挂起（不消耗重试次数），否则隐藏中退出再打开
# 会一路掉进悬浮模式
import src.desktop_window as dw_mod  # noqa: E402

scheduled = []


class _FakeTimer:
    @staticmethod
    def singleShot(ms, fn):
        scheduled.append((ms, fn))


orig_timer, orig_topmost = dw_mod.QTimer, dw_mod.win32_layer.progman_is_topmost
orig_noact = dw_mod.win32_layer.show_no_activate
orig_native = dw_mod.win32_layer.set_native_icons_visible
win2 = DesktopWindow(fresh("win2"))          # 先建窗口（QTimer 还不能打桩）
try:
    dw_mod.QTimer = _FakeTimer
    dw_mod.win32_layer.progman_is_topmost = lambda: True
    dw_mod.win32_layer.show_no_activate = lambda h: False
    win2._attach_retries = 2
    win2._attach_wait = 0
    win2._want_visible = True
    win2._try_attach_once()
    check("V5 显示桌面态挂起挂载（等它退出该态）",
          len(scheduled) == 1 and win2._attach_wait == 1
          and win2._attach_retries == 2, (scheduled, win2._attach_wait))

    # 用户在等待期间把桌面收起来了：延迟重试不能再把窗口弹出来
    win2._want_visible = False
    win2._attach_wait = 0
    win2._try_attach_once()
    check("V5b 已收起桌面时延迟重试不再弹窗", win2._attach_wait == 0,
          win2._attach_wait)
    win2._want_visible = True

    shown_native = []
    dw_mod.win32_layer.set_native_icons_visible = (
        lambda v: shown_native.append(v) or True)
    win2._enter_floating_mode()
    check("V6 挂载失败兜底时把原生图标还回去", shown_native == [True],
          shown_native)
finally:
    dw_mod.QTimer = orig_timer
    dw_mod.win32_layer.progman_is_topmost = orig_topmost
    dw_mod.win32_layer.show_no_activate = orig_noact
    dw_mod.win32_layer.set_native_icons_visible = orig_native


# --------------------------- D4-D6 真实 Dock 窗口：hover 满格也不越界 ---------------------------
st_d = fresh("dockwin")
st_d._data["settings"].update({
    "dock_enabled": True, "dock_position": "bottom",
    "dock_pad": 4, "dock_pad_top": 4, "dock_pad_bottom": 4,
    "dock_pad_left": 4, "dock_pad_right": 4,
    "dock_border_width": 2, "dock_hover": 1.35,
    "dock_items": [{"id": "a%d" % i, "name": "应用 %d" % i,
                    "target": "C:/Windows/System32/notepad.exe",
                    "icon_path": "C:/Windows/System32/notepad.exe"}
                   for i in range(6)],
})
st_d.save()
dw = dock.DockWindow(st_d)
dw._avail = (900, 200)
dw.apply_geometry((900, 200))
bad = []
for hi in (0, 2, 5):
    dw._hover_index = hi
    dw._hover_t = 1.0
    for i in range(len(dw._items)):
        rect, size = dw._target_rect(i)
        if (rect.x() < 0 or rect.y() < 0 or rect.right() >= dw.width()
                or rect.bottom() >= dw.height()):
            bad.append((hi, i, (rect.x(), rect.y(), rect.width()),
                        (dw.width(), dw.height())))
check("D4 hover 在任意项时每个图标都落在条带内", not bad, bad[:3])
check("D5 hover 项确实变大了（效果还在）",
      dw._target_rect(5)[1] > dw._m.icon, (dw._target_rect(5)[1], dw._m.icon))

dw._hover_index = None
dw._hover_t = 0.0
rect0, size0 = dw._target_rect(0)
check("D6 无 hover 时保持原位（不做无谓位移）",
      rect0.width() <= dw._m.icon + 2 and rect0.x() >= 0, (rect0.x(), rect0.width()))

print("-" * 56)
print("%d/%d checks, %d FAIL" % (total[0] - len(failures), total[0],
                                 len(failures)))
if failures:
    print("FAILED:", ", ".join(failures))
sys.stdout.flush()
os._exit(1 if failures else 0)
