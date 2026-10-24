# -*- coding: utf-8 -*-
"""生成 README 用的功能展示图（输出到 `docs/images/`）。

一张图说一件事，全部来自**真实代码路径**，不是手绘示意图：

  01-desktop.png        桌面总览：网格图标 + 锚定小组件 + 分页点（封面图）
  02-widgets.png        七种小组件全家福
  03-style.png          外观自定义：同一组件三种配色 + 组件设置弹窗
  04-widget-center.png  设置 ▸ 组件库（融合后的分页）
  05-settings.png       设置 ▸ 布局：分页自动排列 + 图标尺寸
  06-mobile.png         手机模式：多选标记 + 批量菜单 + 图标右键菜单
  07-folder.png         安卓式文件夹：图标缩略 + 展开后的玻璃弹窗
  08-dock.png           Dock 栏：iOS 风格 Shelf + 悬停抬起 + 液态玻璃

## 为什么不是"截屏"

本机可能正跑着用户的实例（QSharedMemory 单实例键是全局的），而且截屏会
把用户的壁纸 / 真实桌面图标带进仓库。这里走**离屏渲染真实控件**：

  * 真实 QPA（**不要** `QT_QPA_PLATFORM=offscreen`：那套插件缺中文字体，
    汉字数字全渲染成方块，DEVLOG 坑 #60）+ `WA_DontShowOnScreen`；
  * 桌面窗口手动置 `attached = True`（真机挂载成功态，paintEvent 只画
    命中填充与页点，不画"悬浮模式"兜底白板）；
  * 把 `attach_to_desktop` 换成 noop —— 图不能真的去动用户桌面带。

## 为什么组件用 `render()` 而不是手画 `paint_glass + draw()`

v9 起组件的 `_paint_surface` 与 `paint_content` 就在真实控件里跑（编辑框、
关闭按钮、缩放手柄都在），`render()` 出来的就是用户看到的那个东西。
`grab()` 才不行——分层半透明窗 `grab()` 不含透明底（坑 #95），`render()`
带 `DrawChildren` 才对。

跑法：python tools/shot_readme.py [--only 01]
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt                    # noqa: E402  (path 已就绪)
from PySide6.QtWidgets import QApplication      # noqa: E402

import demo_apps                                # noqa: E402  (tools/ 同级)

OUT_DIR = ROOT / "docs" / "images"
STATE = Path(tempfile.gettempdir()) / "shot_readme_state.json"

# 画布逻辑尺寸（DPR=2 输出，实际像素翻倍）
W, H = 1440, 700
DPR = 2

# 演示用的桌面内容。图标来自 `assets/demo_icons/`（`tools/make_demo_icons.py`
# 从本机已装应用导出 256px PNG，清单见 `tools/demo_apps.py`）。
#
# 为什么不再直接写系统 exe：那样图里全是记事本 / 字符映射表 / DirectX 诊断，
# 一眼看不出这是"谁的桌面"，也看不出这程序到底在摆什么。换成微信 / QQ /
# 网易云音乐 / Steam 这些真实应用，封面图才立得住"像手机桌面一样摆应用"。
# 用 PNG 而不是 exe 路径是关键：`QFileIconProvider` 找不到文件时会**静默**
# 退回"空白文档"通用图标（旧版截图里一排蓝底白纸就是它），换台机器出图就废。
_SYS = "C:/Windows/System32/"      # 只给文件夹里的系统成员用

# 出图用的壁纸：Windows 自带的深色 Bloom（比自造渐变有层次，也更像真机）。
# 顺序即优先级；一个都找不到时 `_blank()` 退回程序生成的渐变。
WALLPAPERS = [
    "C:/Windows/Web/Wallpaper/ThemeA/img20.jpg",     # Win11 紫粉光环：深浅都有，玻璃有层次
    "C:/Windows/Web/Wallpaper/Windows/img19.jpg",    # 深色 Bloom（更接近默认桌面，对比略弱）
    "C:/Windows/Web/Wallpaper/Windows/img0.jpg",
]
_WALLPAPER_CACHE = {}

# `[(中文名, 图标路径)]`——`make_readme_anim.py` 按下标取用，保持元组形状
DEMO_APPS = demo_apps.tuples(demo_apps.DESKTOP)


# ---------------------------------------------------------------- 基础设施

def _qapp():
    from PySide6.QtWidgets import QApplication
    os.environ.pop("QT_QPA_PLATFORM", None)
    return QApplication.instance() or QApplication(sys.argv)


_WALL = None


def _wall(rect):
    """按窗口的**真实位置**从壁纸里裁一块，顶替抓屏。

    文件夹弹窗与小组件的玻璃背板都来自 `effects.grab_backdrop()`——真机上
    抓的是用户屏幕。出图脚本这么做等于把用户的桌面内容吸进仓库，所以整条
    链路换成同一张壁纸；**按 rect 位置裁**才能让"组件背后是壁纸的那一块"
    这件事成立（否则所有玻璃都透同一块颜色，看起来像一块黑板）。
    """
    global _WALL
    if _WALL is None:
        _WALL = _blank(W, H)
    from PySide6.QtGui import QPixmap
    pm = QPixmap.fromImage(_WALL)
    w = min(max(8, int(rect.width() * DPR)), pm.width())
    h = min(max(8, int(rect.height() * DPR)), pm.height())
    x0 = max(0, min(int(rect.x() * DPR), pm.width() - w))
    y0 = max(0, min(int(rect.y() * DPR), pm.height() - h))
    out = pm.copy(x0, y0, w, h)
    out.setDevicePixelRatio(DPR)
    return out


def _isolate():
    """把"动桌面"的两个入口换成 noop，并造一份干净的临时 state。

    不做这件事的后果：脚本会真的把图层挂进桌面带、隐藏用户的原生图标
    （DEVLOG 探针安全铁律：只读 / 不接管 / 不隐藏）。
    """
    import src.win32_layer as wl
    import src.widget_base as wb
    import src.effects as fx
    wl.attach_to_desktop = lambda *a, **k: False
    wb.attach_to_desktop = lambda *a, **k: False
    wl.enforce_band_order = lambda *a, **k: None
    # 弹窗玻璃背板：不抓屏，改喂假壁纸（见 _wall 的说明）
    fx.grab_backdrop = lambda rect: _wall(rect)


def _storage(layout=None, widgets=None, **settings):
    from src.storage import Storage
    from src import grid_flow
    if STATE.exists():
        STATE.unlink()
    st = Storage(str(STATE))
    st._data["settings"].update({
        "hide_native_icons": False,      # 出图脚本不碰原生图标
        "takeover_desktop": True,        # 整理模式（网格/页点才画）
        "auto_clone_desktop": False,
        "show_widgets": True,
        "widgets_only": False,
        # v14 反馈 9：液态玻璃的底图来自**实时抓屏**——出图时抓到的会是
        # 开发者自己的桌面，不能进仓库。配图一律走传统玻璃（观感同样代表
        # 产品），液态玻璃的效果单独用合成底图出图（tools/shot_liquid.py）。
        "liquid_glass": False,
    })
    st._data["settings"].update(settings)
    st._data["layout"] = list(layout or [])
    st._data["widgets"] = list(widgets or [])
    st.save()
    grid_flow.set_grid(st, (W, H))
    return st


def _wallpaper(w, h):
    """系统自带的 Windows 壁纸（按画布尺寸 cover 裁剪）。

    自造的"三团柔光渐变"看久了确实单调，而且不像真机。这里直接读
    `C:/Windows/Web/Wallpaper` 里的官方壁纸：深色 Bloom 底子最能衬出
    玻璃的层次，也最接近用户把桌面整理工具挂在真桌面上的样子。

    找不到文件（精简系统 / CI）时返回 None，调用方退回渐变——出图脚本
    不该因为少一张壁纸就跑不起来。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    tw, th = int(w * DPR), int(h * DPR)
    chosen = os.environ.get("SHOT_WALL")
    for path in ([chosen] if chosen else WALLPAPERS):
        p = Path(path)
        if not p.exists():
            continue
        src = QImage(str(p))
        if src.isNull():
            continue
        key = (path, tw, th)
        if key in _WALLPAPER_CACHE:
            return _WALLPAPER_CACHE[key]
        scaled = src.scaled(tw, th, Qt.KeepAspectRatioByExpanding,
                            Qt.SmoothTransformation)
        x = max(0, (scaled.width() - tw) // 2)
        y = max(0, (scaled.height() - th) // 2)
        _WALLPAPER_CACHE[key] = scaled.copy(x, y, tw, th)
        return _WALLPAPER_CACHE[key]
    return None


def _blank(w=W, h=H, wallpaper=True):
    """新画布 + 壁纸。壁纸只是让玻璃有东西可透，不要抢戏。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QRadialGradient
    tw, th = int(w * DPR), int(h * DPR)
    img = QImage(tw, th, QImage.Format_ARGB32_Premultiplied)
    if not wallpaper:
        img.setDevicePixelRatio(DPR)
        img.fill(QColor("#12141c"))
        return img

    shot = _wallpaper(w, h)
    if shot is not None:
        # 先按**物理像素**画（此时 DPR 还是 1，坐标不用换算），画完再标 DPR
        img.fill(QColor("#0d1018"))
        p = QPainter(img)
        p.drawImage(0, 0, shot)
        # 压一层极淡的深色：真实壁纸比自造渐变亮，图标标签与标题字才压得住
        p.fillRect(0, 0, tw, th, QColor(6, 9, 18, 34))
        p.end()
        img.setDevicePixelRatio(DPR)
        return img

    img.setDevicePixelRatio(DPR)
    img.fill(QColor("#12141c"))
    from PySide6.QtGui import QPainter as _P
    p = _P(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    base = QLinearGradient(0, 0, w * 0.85, h)
    base.setColorAt(0.00, QColor("#243c78"))
    base.setColorAt(0.38, QColor("#3b4f9c"))
    base.setColorAt(0.70, QColor("#6d3f78"))
    base.setColorAt(1.00, QColor("#241c33"))
    p.fillRect(0, 0, w, h, base)
    # 三团柔光：模拟壁纸的明暗起伏，玻璃的 tint 才有层次
    for cx, cy, r, col in (
        (w * 0.24, h * 0.12, w * 0.52, QColor(120, 180, 255, 92)),
        (w * 0.88, h * 0.88, w * 0.46, QColor(255, 130, 170, 70)),
        (w * 0.58, h * 0.46, w * 0.34, QColor(120, 255, 225, 34)),
    ):
        g = QRadialGradient(cx, cy, r)
        g.setColorAt(0.0, col)
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))
    p.end()
    return img


def _draw_widgets(canvas, layer, storage, targets=None, offset=(0, 0)):
    """把 layer 里已有的组件窗口渲染到 canvas（按各自 geometry）。

    用**已经建好的** WidgetLayer，不新建——重复建会多出一批窗口与定时器，
    进程退出时可能段错误（坑 #93）。
    """
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QApplication

    hidden = {x.get("id") for x in storage.widgets if x.get("hidden")}
    p = QPainter(canvas)
    p.setRenderHint(QPainter.Antialiasing, True)
    painted = []
    for eid, w in layer.widgets.items():
        if (targets is not None and eid not in targets) or eid in hidden:
            continue
        w.setAttribute(Qt.WA_DontShowOnScreen, True)
        w.show()
        QApplication.processEvents()
        p.drawPixmap(int(w.x()) + offset[0], int(w.y()) + offset[1], _snap(w))
        painted.append(eid)
    p.end()
    return painted


def _widgets_on(canvas, storage, targets=None):
    """建 layer 并把可见组件画到 canvas 上（单图场景的便捷入口）。"""
    from src.widget_layer import WidgetLayer
    layer = WidgetLayer(storage)
    _draw_widgets(canvas, layer, storage, targets)
    return layer


def _snap(widget, flags=None):
    """把一个窗口渲染成 QPixmap（含子部件）。

    `grab()` 不能用于分层半透明窗（不含透明底，坑 #95）；`render()` 走
    paintEvent，玻璃底、关闭按钮、缩放手柄、编辑框都在。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QWidget
    pm = QPixmap(widget.size() * DPR)
    pm.setDevicePixelRatio(DPR)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    widget.render(p, widget.rect().topLeft(), widget.rect(),
                  flags if flags is not None else
                  (QWidget.DrawWindowBackground | QWidget.DrawChildren))
    p.end()
    return pm


def _label(p, text, x, y, size=13, color="#AEB4C2", bold=False, w=900,
           shadow=False):
    """画一行说明文字。`shadow=True` 给压在壁纸亮区的字加一层暗底，
    否则浅灰字落在紫粉高光上几乎看不见（07 的说明就吃过这个亏）。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor
    from src import theme
    p.setFont(theme.ui_font(size, bold=bold))
    if shadow:
        p.setPen(QColor(0, 0, 0, 150))
        p.drawText(QRectF(x, y + 1, w, size + 12), Qt.AlignLeft | Qt.AlignVCenter,
                   text)
    p.setPen(QColor(color))
    p.drawText(QRectF(x, y, w, size + 12), Qt.AlignLeft | Qt.AlignVCenter, text)


def _save(img, name):
    """落盘。超宽图先下采样到 1920 逻辑宽——DPR=2 画出来是 3840，
    仓库里放这么大的 PNG 没意义；2→1 的超采样反而是最好的抗锯齿。"""
    from PySide6.QtCore import Qt
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    out = img
    if img.width() > 2100:
        out = img.scaledToWidth(1920, Qt.SmoothTransformation)
        out.setDevicePixelRatio(1.0)
    out.save(str(path))
    print("  -> %s（%d × %d）" % (path.relative_to(ROOT), out.width(), out.height()))
    return path


def _close_all(*objs):
    """收尾：控件必须比 QApplication 先死，否则解释器退出时段错误（坑 #93）。"""
    from PySide6.QtWidgets import QApplication
    for o in objs:
        try:
            if isinstance(o, (list, tuple)):
                _close_all(*o)
                continue
            o.close()
            o.deleteLater()
        except Exception:      # noqa: BLE001
            pass
    QApplication.processEvents()


# ---------------------------------------------------------------- 图 1：桌面总览

def _demo_icons():
    """演示图标条目：12 个应用铺满 3 行（4 列），左上角不留空。

    `entries()` 给的每个字典同时带 `target`（真实 exe，供"双击启动"链路用）
    和 `icon_path`（导出的 PNG，`appsource._qicon_for` 认图片文件直接加载）。
    """
    out = []
    for i, app in enumerate(demo_apps.entries(demo_apps.DESKTOP)):
        out.append({
            "id": "app%d" % i, "type": "app",
            "col": 1 + i % 4, "row": i // 4, "app": app,
        })
    # 一个文件夹：整理能力最直观的证据（2×2 迷你图标 + 液玻弹窗都是自绘的）。
    # `apps` 必须是**纯 app 字典**（无外层 {"id","type","app"} 包装）——
    # `_paint_folder` 与弹窗都直接把它当 app 用，包一层会让图标退成字母占位。
    folder = _folder_entry("folder1", demo_apps.FOLDER["name"],
                           demo_apps.folder_members(), "#39649F")
    folder.update({"col": 1, "row": 3})
    out.append(folder)
    return out


def shot_desktop():
    """封面图：整理模式下的桌面（网格图标 + 文件夹 + 锚定小组件 + 页点）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QWidget

    from src.desktop_window import DesktopWindow

    # 锚定（非悬浮）组件：只铺 tint 不画 × / 手柄，才是"融入桌面"的常态。
    # 高度按内容给：便签 4 行刚好放下正文（7 行满高会留一大片空底）；
    # 时钟与监控各 3 行、**中间空一整行**——贴在一起会被看成一整块。
    # 3 行也是监控保持 row 布局（三环横排 + 环下标签）的下限：4 列 × 2 行
    # 宽高比 1.86 会翻进 width 版，三环挤左半边反而更乱。
    widgets = [
        {"id": "w_notes", "type": "notes", "anchored": True,
         "col": 6, "row": 0, "colspan": 4, "rowspan": 4,
         "settings": {"bg_alpha": 0.8},
         "text": "周五之前\n1. 改 Readme 的功能截图\n2. 跑一遍全量回归\n"
                 "3. 把 v9a 发给体验用户\n4. 收一遍手机模式的操作反馈\n\n"
                 "备注\n· 每页规则等确认后再放开\n· 玻璃底在亮壁纸上再压一点\n"
                 "· 启动耗时目标 1.2s 以内\n· 文档截图换成本轮新图"},
        {"id": "w_clock", "type": "clock", "anchored": True,
         "col": 11, "row": 0, "colspan": 4, "rowspan": 3,
         "settings": {"bg_alpha": 0.8}},
        {"id": "w_monitor", "type": "monitor", "anchored": True,
         "col": 11, "row": 4, "colspan": 4, "rowspan": 3,
         "settings": {"bg_alpha": 0.8}},
    ]
    st = _storage(layout=_demo_icons(), widgets=widgets)

    win = DesktopWindow(st)
    win.attached = True          # 真机挂载成功态（不画"悬浮模式"兜底白板）
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.setGeometry(0, 0, W, H)
    win._update_grid_metrics()
    win.rebuild()
    win.show()
    QApplication.processEvents()

    img = _blank()
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    win.render(p, win.rect().topLeft(), win.rect(), QWidget.DrawChildren)
    p.end()

    layer = _widgets_on(img, st)
    _save(img, "01-desktop.png")
    _close_all(list(layer.widgets.values()), win)


# ---------------------------------------------------------------- 图 2：组件全家福

# (类型, 显示名, 宽, 高, 额外 entry 字段)
WIDGET_SHOWCASE = [
    ("clock", "时钟 · 可开关秒/日期", 300, 180, {"show_seconds": True, "show_date": True}),
    ("notes", "便签 · 可直接编辑", 300, 220,
     {"text": "9:30 站会\n改 Readme 截图\n周五发 v9a"}),
    ("monitor", "系统监控 · CPU/内存/磁盘", 300, 280, {}),
    ("todo", "待办清单 · 多实例", 300, 220,
     {"items": [{"id": "t1", "text": "写组件开发指南", "done": True},
                {"id": "t2", "text": "跑全量回归", "done": True},
                {"id": "t3", "text": "用户实机验收", "done": False}]}),
    ("calendar", "日历 · 月视图可翻月", 300, 280, {}),
    ("weather", "天气 · 实时 + 缓存降级", 300, 220, {}),
    ("panel", "桌面面板 · 搜索/文件夹/磁盘/下载", 300, 344, {}),
    ("music", "音乐 · 控制本机正在播放", 300, 220, {}),
]


def _no_chrome(w):
    """截图时摘掉悬浮态的 × 与缩放手柄。

    它们是真机 UI（鼠标移上去才用得到），但在"看组件长什么样"的图里
    只是噪音。给实例挂空矩形，`paintEvent` 里那两段绘制自然画不出东西
    （Qt 会在负坐标外裁掉），不用改产品代码。
    """
    from PySide6.QtCore import QRect
    w._close_rect = lambda: QRect()
    w._grip_rect = lambda: QRect()


def shot_widgets():
    """八种小组件，同一套玻璃语言（时钟/便签/音乐也在内）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QApplication

    from src.widget_layer import WidgetLayer

    entries = []
    for wtype, _name, w, h, extra in WIDGET_SHOWCASE:
        e = {"id": "s_" + wtype, "type": wtype, "w": w, "h": h,
             "anchored": False, "x": 0, "y": 0, "hidden": False}
        e.update(extra)
        entries.append(e)
    st = _storage(widgets=entries)
    layer = WidgetLayer(st)
    for eid, w in layer.widgets.items():
        w.setAttribute(Qt.WA_DontShowOnScreen, True)
        _no_chrome(w)
        w.show()
    # 音乐组件：截图时给它走一段进度，否则进度条是空的（0% 看着像坏了）。
    # `advance` 只有演示档才有；`_after_control()` 是点击后回读快照的同一个入口。
    mus = layer.widgets.get("s_music")
    if mus is not None:
        adv = getattr(getattr(mus, "source", None), "advance", None)
        if callable(adv):
            adv(78.0)
            mus._after_control()
    QApplication.processEvents()

    # 4 列 × 2 行；框高统一 300，组件在框内垂直居中
    pad_x, gap = 52, 26
    box_h, gap_y = 344, 74
    cell_w = (W - pad_x * 2 - gap * 3) // 4
    top = 128
    img = _blank(W, top + (box_h + gap_y) * 2 + 30)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    _label(p, "八种桌面小组件", pad_x, 40, 27, "#F4F7FD", True)
    _label(p, "同一套玻璃材质与排版，尺寸随跨度自适应；每个都能单独配背景色 / 透明度 / 文字色",
           pad_x, 82, 14, "#9DAABF", w=1100)

    for i, (wtype, name, w0, h0, _e) in enumerate(WIDGET_SHOWCASE):
        col, row = i % 4, i // 4
        x = pad_x + col * (cell_w + gap)
        y = top + row * (box_h + gap_y)
        wid = layer.widgets.get("s_" + wtype)
        if wid is None:
            continue
        pm = _snap(wid)
        # 只缩不放：放大组件会让字体栅格化失真
        scale = min(1.0, (cell_w - 8) / pm.width(), box_h / pm.height())
        tw = int(pm.width() * scale)
        th = int(pm.height() * scale)
        p.drawPixmap(int(x + (cell_w - tw) / 2), int(y + (box_h - th) / 2),
                     tw, th, pm)
        _label(p, name, x, y + box_h + 10, 15, "#CBD5E6", True, w=cell_w + 10)

    p.end()
    _save(img, "02-widgets.png")
    _close_all(list(layer.widgets.values()))


# ---------------------------------------------------------------- 图 3：外观自定义

STYLE_PRESETS = [
    ("默认 · 跟随主题", None),
    ("深蓝半透 · 背景色 + 透明度", {"bg_color": "#1E5A8A", "bg_alpha": 0.55}),
    ("暖橙 · 文字色 + 强调色", {"bg_color": "#7A4520", "bg_alpha": 0.92,
                                "text_color": "#FFF1DC", "accent_color": "#FF9A4A"}),
]


def shot_style():
    """外观自定义：同一组件三种皮肤 + 真实的字段面板。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QApplication

    from src.widget_layer import WidgetLayer
    from src.widget_registry import get as spec_of
    from src.widget_settings import WidgetSettingsDialog

    entries = []
    for i, (name, settings) in enumerate(STYLE_PRESETS):
        e = {"id": "st%d" % i, "type": "clock", "w": 300, "h": 180,
             "anchored": False, "x": 0, "y": 0, "hidden": False,
             "show_seconds": True, "show_date": True}
        if settings:
            e["settings"] = dict(settings)
        entries.append(e)
    st = _storage(widgets=entries)
    layer = WidgetLayer(st)
    for w in layer.widgets.values():
        w.setAttribute(Qt.WA_DontShowOnScreen, True)
        _no_chrome(w)
        w.show()
    QApplication.processEvents()

    # 弹窗：拿"暖橙那一条"当样本，字段里才有非默认值
    dlg = WidgetSettingsDialog(spec_of("clock"), layer.widgets and entries[2], st)
    dlg.setAttribute(Qt.WA_DontShowOnScreen, True)
    dlg.show()
    QApplication.processEvents()
    panel = _snap(dlg)

    img = _blank(W, 940)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    _label(p, "组件外观可自定义", 52, 38, 27, "#F4F7FD", True)
    _label(p, "背景色 · 透明度 · 文字色 · 强调色 · 圆角；留空 = 跟随主题，所有组件通用（含自己写的插件组件）",
           52, 80, 14, "#9DAABF", w=980)

    # 左：三种皮肤（同一份 draw()，只换 Style）
    y = 132
    for i, (name, _s) in enumerate(STYLE_PRESETS):
        wid = layer.widgets.get("st%d" % i)
        if wid is None:
            continue
        p.drawPixmap(52, y, _snap(wid))
        _label(p, name, 52, y + 188, 14, "#CBD5E6", True, w=340)
        y += 236

    # 右：真实设置弹窗（缩放到合适宽度）
    target_w = 560
    scale = target_w / panel.width()
    pw, ph = target_w, int(panel.height() * scale)
    p.drawPixmap(760, 132, pw, ph, panel)
    _label(p, "组件右键 ▸「组件设置…」（底部「外观」段）", 760, 132 + ph + 10,
           13, "#D7DEEA", w=620, shadow=True)

    p.end()
    _save(img, "03-style.png")
    dlg.close()
    _close_all(list(layer.widgets.values()))


# ---------------------------------------------------------------- 图 4/5：设置面板

def _settings_frame(page, widgets=(), scroll=None, settings=None):
    """打开设置面板的某个分页并抓图（返回 QPixmap + 需要收尾的对象）。

    设置窗口是普通 QWidget（不是分层窗），`grab()` 含子部件——组件库
    那种 `WA_DontShowOnScreen` + 真实 QPA 下 grab 反而更省事，不用逐
    个子部件 render（v8 的教训：手工合成会画成两层重叠影子）。
    """
    from PySide6.QtWidgets import QApplication

    from src.desktop_window import DesktopWindow
    from src.settings_dialog import SettingsDialog
    from src.widget_layer import WidgetLayer

    st = _storage(layout=_demo_icons(), widgets=list(widgets),
                  **(settings or {}))
    layer = WidgetLayer(st)
    win = DesktopWindow(st)
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    dlg = SettingsDialog(win, st, layer, page=page)
    dlg.setAttribute(Qt.WA_DontShowOnScreen, True)
    # 默认最小尺寸（860×560）下组件库只放得下两列卡片，右侧被切一半；
    # 拉宽到 1120 才像用户在正常窗口里看到的样子
    dlg.resize(1120, 760)
    dlg.show()
    QApplication.processEvents()
    # Switch（自绘开关）的圆钮位置是 QPropertyAnimation 驱动的，
    # processEvents 不推进动画时间——不等一下，`setChecked(True)` 的开关
    # 在截图里还停在"关"的位置上
    from PySide6.QtTest import QTest
    QTest.qWait(280)
    QApplication.processEvents()
    if scroll is not None:
        # 布局页上"分页自动排列"卡片在滚动区底部，不滚下去根本截不到
        from PySide6.QtWidgets import QScrollArea
        for sa in dlg.findChildren(QScrollArea):
            bar = sa.verticalScrollBar()
            bar.setValue(bar.maximum() if scroll == "bottom" else int(scroll))
        QApplication.processEvents()
    pm = dlg.grab()
    return pm, dlg, win, layer


def _settings_figure(page, title, subtitle, name, note="", scroll=None,
                     settings=None):
    from PySide6.QtGui import QPainter

    pm, dlg, win, layer = _settings_frame(page, _showcase_widgets(), scroll,
                                          settings)
    target_w = W - 104
    scale = target_w / pm.width()
    tw, th = target_w, int(pm.height() * scale)
    img = _blank(W, 132 + th + 56)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    _label(p, title, 52, 38, 27, "#F4F7FD", True)
    _label(p, subtitle, 52, 80, 14, "#9DAABF", w=1100)
    p.drawPixmap(52, 132, tw, th, pm)
    if note:
        _label(p, note, 52, 132 + th + 12, 13, "#93A0B8", w=1100)
    p.end()
    _save(img, name)
    dlg.close()
    _close_all(list(layer.widgets.values()), win)


def _showcase_widgets():
    return [
        {"id": "w_clock", "type": "clock", "anchored": True,
         "col": 6, "row": 0, "colspan": 4, "rowspan": 2},
        {"id": "w_monitor", "type": "monitor", "anchored": True,
         "col": 10, "row": 0, "colspan": 4, "rowspan": 3,
         "hidden": True},
    ]


def shot_widget_center():
    """设置 ▸ 组件库：分类 + 搜索 + 四态卡片 + 实时预览。"""
    _settings_figure(
        "widgets", "组件库就在设置面板里",
        "不再是独立窗口——分类浏览、搜索、四态卡片（未添加 / 已隐藏 / 显示中 / 已添加），"
        "卡片里的预览与桌面渲染同源",
        "04-widget-center.png",
        "页脚还有「组件目录…」与「导入组件…」：把别人写好的 zip 直接拖进来就能用")


def shot_settings_layout():
    """设置 ▸ 布局与网格：分页自动排列 + 图标尺寸。"""
    _settings_figure(
        "layout", "布局与网格",
        "列数 / 图标尺寸 / 网格原点 / 自动排列都能调；「分页自动排列」可以逐页决定"
        "哪一页走自动排、哪一页保持自由",
        "05-settings.png",
        "默认统一右键配置；打开「每页单独设置」后才按页分派（v9 修的跨页回弹就在这条链上）",
        scroll="bottom",
        settings={"page_rules_custom": True,
                  "page_rules": {"0": "auto", "1": "free"},
                  "extra_pages": [1]})


# ---------------------------------------------------------------- 图 6：手机模式

def _icon_chip(entry, selected, visible=True, size=96):
    """把一个图标渲染成透明底 QPixmap（只画子内容，不画窗口底色）。"""
    from PySide6.QtGui import QPainter, QPixmap, QRegion
    from PySide6.QtWidgets import QWidget

    from src.iconwidget import AppIcon

    icon = AppIcon(entry, size, parent=None)
    icon.setAttribute(Qt.WA_DontShowOnScreen, True)
    icon.show()
    QApplication.processEvents()
    icon.set_selection_visible(visible)
    icon.set_selected(selected)
    icon.update()
    QApplication.processEvents()
    pm = QPixmap(icon.size() * DPR)
    pm.setDevicePixelRatio(DPR)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    # 只画子内容：带 DrawWindowBackground 会把窗口底色也画上来（坑 #95）
    icon.render(p, icon.rect().topLeft(), icon.rect(), QWidget.DrawChildren)
    p.end()
    icon.close()
    return pm


def _menu_shot(menu):
    """菜单截图：`show()` 之后直接 `grab()`。

    这里反过来不能用 `render()`——QMenu 的项是在 polish/show 阶段才排版
    的，手工 render 只能拿到一张空白（第一版就是这么空的）。`grab()` 走
    真实 backing store，连 QSS 画的分隔线、滑条都在。
    """
    menu.setAttribute(Qt.WA_DontShowOnScreen, True)
    menu.show()
    QApplication.processEvents()
    pm = menu.grab()
    menu.close()
    return pm


def shot_mobile():
    """手机模式：多选标记 + 长按菜单 + 批量菜单。"""
    from PySide6.QtGui import QPainter

    from src.desktop_window import DesktopWindow

    st = _storage(layout=_demo_icons(), widgets=[], interaction_mode="mobile")
    win = DesktopWindow(st)
    win.attached = True
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.setGeometry(0, 0, W, H)
    win._update_grid_metrics()
    win.rebuild()
    win.show()
    QApplication.processEvents()

    entries = {e["id"]: e for e in st.layout}
    demo = [("app0", False, "未选中 · 空心圆"),
            ("app1", True, "已选中 · 对钩"),
            ("app2", True, "已选中 · 对钩")]
    chips = [(entries[eid], sel, cap) for eid, sel, cap in demo]

    # 批量菜单需要一个"选了 3 个"的状态
    win._selected = {"app1", "app2", "app3"}
    win._selection_mode = True
    batch = win._build_batch_menu()
    icon0 = win.icons.get("app0")
    long_menu = icon0._build_context_menu(long_press=True) if icon0 else None

    img = _blank(W, 528)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    _label(p, "手机习惯：长按出菜单，多选可批量整理", 52, 36, 26, "#F4F7FD", True)
    _label(p, "设置 ▸ 模式 ▸ 交互方式 = 「手机习惯」；单击直接启动，按住 450ms 出菜单，"
              "按住后继续滑就变成拖动", 52, 76, 14, "#9DAABF", w=1200)

    # 上：三个图标的多选态
    x = 60
    for entry, sel, cap in chips:
        chip = _icon_chip(entry, sel)
        p.drawPixmap(x, 124, chip)
        _label(p, cap, x - 8, 124 + chip.height() / DPR + 4, 12, "#D7DEEA",
               w=170, shadow=True)
        x += 172

    # 下：两个菜单（grab 出来的图自带 DPR，用逻辑尺寸定位）
    my = 292
    if long_menu is not None:
        pm = _menu_shot(long_menu)
        p.drawPixmap(60, my, pm)
        _label(p, "① 长按 450ms 出的菜单（首项「选择」进入多选）", 60,
               my + pm.height() / pm.devicePixelRatio() + 10, 13, "#93A0B8", w=400)
    pm2 = _menu_shot(batch)
    p.drawPixmap(470, my, pm2)
    _label(p, "② 选好之后右键出批量菜单", 470,
           my + pm2.height() / pm2.devicePixelRatio() + 10, 13, "#93A0B8", w=400)

    # 右侧：其余手机语义
    tips = ["· 单击图标直接启动（不用双击）",
            "· 长按后继续滑 = 取消菜单、转成拖动",
            "· 图标左上角空心圆 → 点一下变对钩",
            "· 点空白退出多选",
            "· 空白处左右滑直接翻页（不出选框）"]
    ty = 292
    for line in tips:
        _label(p, line, 900, ty, 14, "#E2E8F2", w=480, shadow=True)
        ty += 34

    p.end()
    _save(img, "06-mobile.png")
    _close_all(win)


# ---------------------------------------------------------------- 图 7：安卓式文件夹

# 成员数不同 -> 图标里的迷你缩略自动换排布（1 居中 / 2 并排 / 3 三角 / 4 田字）
# (文件夹名, 说明, 成员, 色板)
# 文件夹名按成员的真实用途起（社交 / 影音 / 办公 / 工具）——随便塞四个系统
# 工具的话，一行"文档"里放着计算器就很怪，读者第一眼就会分心
_FOLDER_SETS = [
    (name, note, demo_apps.tuples(slugs), tint)
    for name, note, slugs, tint in demo_apps.FOLDER_SETS
]

# 图 1 那个文件夹的完整成员（品牌应用 + 两个系统小工具）
_FOLDER_FULL = demo_apps.folder_members()

# 图 7 右下那个"满页"弹窗：9 个成员才排满 3 × 3
_POPUP_MEMBERS = demo_apps.tuples(demo_apps.POPUP_SLUGS)


def _folder_entry(fid, name, members, tint=None):
    """文件夹条目。`tint` 就是用户在色板里选的那个颜色，图标与弹窗共用。

    `apps` 必须是**纯 app 字典**（`{"name","target"}`）——`_paint_folder`
    与弹窗都直接把它当 app 交给 `appsource.draw_icon`，包一层外层结构会
    让缩略图全退成字母占位块。`app` 用空 dict 而非 None（坑 #39）。
    """
    e = {"id": fid, "type": "folder", "name": name, "app": {},
         "apps": [{"name": n, "target": path, "kind": "app"}
                  for n, path in members]}
    if tint:
        e["tint"] = tint
    return e


def _render_icon(entry, size=96):
    """单个 AppIcon 的透明底渲染（只画内容，不带窗口底色）。"""
    from PySide6.QtWidgets import QWidget

    from src.iconwidget import AppIcon
    icon = AppIcon(entry, size, parent=None)
    icon.setAttribute(Qt.WA_DontShowOnScreen, True)
    icon.show()
    QApplication.processEvents()
    pm = _snap(icon, QWidget.DrawChildren)
    icon.close()
    icon.deleteLater()
    return pm


def _render_popup(storage, entry):
    """真实 FolderPopup 的稳态图（透明底）。

    两个必须处理的地方：

      1. `__init__` 里 `_open_animate()` 会直接 `show()`——出图时临时把
         `show` 换掉，否则用户屏幕上会闪一个真弹窗；
      2. 成员是错峰入场的（`appear_in(120 + i*45)`，淡入 260ms），不等
         就跑会把透明的成员画出来。等足 760ms 再把 `_appear` 兜底归 1。
    """
    from PySide6.QtCore import QRect
    from PySide6.QtTest import QTest

    from src.folder_popup import FolderPopup
    from src.iconwidget import AppIcon

    # 给一个"锚点图标"的假位置：弹窗按 place_beside 落在画布左半区，
    # 玻璃背板就能裁到壁纸的深蓝区（不传的话按屏幕居中，可能裁到屏幕外）
    anchor = QRect(300, 200, 96, 96)
    orig = FolderPopup.show
    FolderPopup.show = lambda self: None
    try:
        popup = FolderPopup(storage, entry, anchor)
    finally:
        FolderPopup.show = orig
    try:
        getattr(popup, "_open_anim", None).stop()
    except Exception:       # noqa: BLE001  （动画可能已结束/被回收）
        pass
    popup._zoom = 1.0
    popup.setWindowOpacity(1.0)
    popup.setAttribute(Qt.WA_DontShowOnScreen, True)
    popup.show()
    QApplication.processEvents()
    QTest.qWait(760)
    for icon in popup.findChildren(AppIcon):
        icon._appear = 1.0
        icon.update()
    QApplication.processEvents()
    return popup, _snap(popup)


def shot_folder():
    """安卓式文件夹：图标缩略 + 展开后的玻璃弹窗（两种尺寸）。"""
    from PySide6.QtGui import QPainter

    entries = [_folder_entry("fd%d" % i, name, members, tint)
               for i, (name, _cap, members, tint) in enumerate(_FOLDER_SETS)]
    three = _folder_entry("fd_three", "常用", _FOLDER_FULL[:3], "#2C7E6B")
    full = _folder_entry("fd_full", "全部应用", _POPUP_MEMBERS, "#39649F")
    st = _storage(layout=entries + [three, full])

    img = _blank(W, 706)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    _label(p, "安卓式的桌面文件夹", 52, 36, 27, "#F4F7FD", True)
    _label(p, "图标里直接就是成员缩略（1 / 2 / 3 / 4 个自动换排布）；点开是玻璃弹窗，"
              "格子按成员数收缩、超过一页就左右翻页", 52, 78, 14, "#9DAABF", w=1240)

    # 上排：四种成员数的文件夹图标（图标自带的 label 就是文件夹名）
    x = 64
    for (_name, cap, _m, _t), entry in zip(_FOLDER_SETS, entries):
        pm = _render_icon(entry, 96)
        p.drawPixmap(x, 130, pm)
        ih = int(pm.deviceIndependentSize().height())
        _label(p, cap, x - 20, 130 + ih + 2, 12, "#CBD5E6", w=160)
        x += 172

    # 下排：两个真实弹窗——3 成员收缩成 3×2、9 成员满页 3×3
    py = 318
    # 文案里的数字**按成员真实数量算**：写死"9 个成员"的话，改一次成员表
    # 就会出现"图里 4 个图标、标题写 9 个"的自相矛盾
    for i, (entry, cap) in enumerate((
            (three, "%d 个成员 → 3 × 2（按数量收缩，最小 2 × 2）"
             % len(three["apps"])),
            (full, "%d 个成员 → 3 × 3 满页（再多就左右翻页）"
             % len(full["apps"])))):
        popup, pm = _render_popup(st, entry)
        px = 60 + i * 330
        p.drawPixmap(px, py, pm)
        ph = int(pm.deviceIndependentSize().height())
        _label(p, cap, px - 12, py + ph + 6, 13, "#D7DEEA", w=340, shadow=True)
        popup.close()

    # 右侧：这套语言里值得说清的四件事
    tips = ["· 右上角 ○ 换文件夹颜色，图标与弹窗同步变色",
            "· 从弹窗里把成员拖出去 = 放回桌面",
            "· 把桌面图标拖到文件夹图标上 = 并入",
            "· 弹窗里成员名直接显示，不用悬停猜",
            "· 一页装满了，左右滑动翻页（和桌面分页同一手感）"]
    ty = 330
    for line in tips:
        _label(p, line, 760, ty, 14, "#B9C4D6", w=640)
        ty += 36

    p.end()
    _save(img, "07-folder.png")


# ---------------------------------------------------------------- 图 8：Dock 栏

def shot_dock():
    """Dock 栏：iOS 风格 Shelf + 悬停抬起 + 液态玻璃（反馈 5 / 9）。

    两个真实 `DockWindow`：底部横向（7 个应用，第 4 个停在悬停放大态）
    + 左侧竖向（4 个应用）——位置四向可调用"左右各一个"演示，比空口说
    四向更直观。液态玻璃默认开着（v14 反馈 9），色散/透镜边直接入镜。

    **必须走 `apply_geometry()`**：图标位置由 `item_rect(bar, …)` 算，
    `bar` 是 `apply_geometry` 里才求出来的。只 `resize()` 的话 `_bar`
    还是 (0,0,0,0)，`(h - icon)//2` 变成 `(0 - 44)//2 = -22`——图标全部
    顶到条带上沿并截掉一半（2026-10-24 用户指出"图标没有居中"的真因）。
    """
    from PySide6.QtGui import QPainter

    from src import dock as dockmod

    st = _storage(dock_enabled=True, dock_position="bottom",
                  dock_items=demo_apps.entries(demo_apps.DESKTOP[:7]))

    d = dockmod.DockWindow(st)
    d.setAttribute(Qt.WA_DontShowOnScreen, True)
    orig_show = dockmod.DockWindow.show
    dockmod.DockWindow.show = lambda self: None      # 别在用户屏幕上闪一下
    try:
        d.apply_geometry((W, H))                    # 真实几何：算出 _bar + setGeometry
    finally:
        dockmod.DockWindow.show = orig_show
    d._hover_index = 3               # 第 4 个图标：停在"被悬停"的稳态
    d._set_hover_t(1.0)
    QApplication.processEvents()

    # 竖向小 Dock（左侧）：同一份代码换个位置就是另一根条带
    st_v = _storage(dock_enabled=True, dock_position="left",
                    dock_items=demo_apps.entries(demo_apps.DESKTOP[:4]))
    dv = dockmod.DockWindow(st_v)
    dv.setAttribute(Qt.WA_DontShowOnScreen, True)
    dockmod.DockWindow.show = lambda self: None
    try:
        dv.apply_geometry((W, H))
    finally:
        dockmod.DockWindow.show = orig_show
    QApplication.processEvents()

    # 渲染成 pixmap 再贴到画布：窗口自身尺寸 == 条带尺寸，图标才与背板对齐
    pm = _snap(d)
    pm_v = _snap(dv)

    img = _blank()
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    _label(p, "Dock 栏：手机底部的那一排常用应用", 52, 36, 27, "#F4F7FD", True)
    _label(p, "iOS 风格 Shelf——竖向渐变背板 + 顶部一道高光，悬停弹簧抬起、"
              "邻居让位；液态玻璃的光学层默认叠在上面", 52, 78, 14, "#9DAABF",
           w=1240)

    # 底部横向主 Dock（与右侧要点列表错开，不叠字）
    bw = int(d.size().width())
    bh = int(d.size().height())
    dx, dy = (W - bw) // 2, 430
    p.drawPixmap(int(dx), int(dy), pm)
    # 悬停标注：从真实 item_rect 反推第 4 个图标的中心（drawPixmap 偏移后）
    ix, _iy, iw, _ih = dockmod.item_rect(d._bar, d._position, d._m, 3)
    hx = dx + (ix - d._bar[0]) + iw // 2
    _label(p, "悬停抬起 · 邻居让位", hx - 110, dy + bh + 18, 14, "#D7DEEA",
           w=240, shadow=True)

    # 左侧竖向小 Dock
    vw, vh = int(dv.size().width()), int(dv.size().height())
    vx, vy = 96, 200
    p.drawPixmap(int(vx), int(vy), pm_v)
    _label(p, "同一根条带，四向摆放", vx - 26, vy + vh + 22, 13,
           "#CBD5E6", w=220, shadow=True)

    # 右侧：这个栏能做什么（按"用户会怎么用它"组织，不按代码结构）
    tips = ["· 把应用 / 快捷方式拖进条带 = 追加到末尾",
            "· 按住拖动 = 排序；拖出条带 = 移除",
            "· hover 弹簧放大（倍数可调），落点有高亮反馈",
            "· 设置 ▸ Dock 栏：大小 / 间隔 / 内边距 / 圆角 /",
            "   边框 / 不透明度 / 悬停倍数 / 指示点全可调",
            "· 屏幕位置四向 + 居中开关，偏移可微调",
            "· 开启后网格自动让出条带并重算行列（不重叠）",
            "· 液态玻璃 / 渐变 Shelf 可在设置里分别开关"]
    ty = 170
    for line in tips:
        _label(p, line, 780, ty, 14, "#B9C4D6", w=620)
        ty += 34

    p.end()
    _save(img, "08-dock.png")
    d.close()
    dv.close()


# ---------------------------------------------------------------- 主流程

SHOTS = [
    ("01", "01-desktop.png", "桌面总览", lambda: shot_desktop()),
    ("02", "02-widgets.png", "小组件全家福", lambda: shot_widgets()),
    ("03", "03-style.png", "外观自定义", lambda: shot_style()),
    ("04", "04-widget-center.png", "设置▸组件库", lambda: shot_widget_center()),
    ("05", "05-settings.png", "设置▸布局与网格", lambda: shot_settings_layout()),
    ("06", "06-mobile.png", "手机模式", lambda: shot_mobile()),
    ("07", "07-folder.png", "安卓式文件夹", lambda: shot_folder()),
    ("08", "08-dock.png", "Dock 栏", lambda: shot_dock()),
]


def main(argv):
    app = _qapp()
    want = None
    if "--only" in argv:
        want = argv[argv.index("--only") + 1]
    _isolate()
    print("出图到 %s" % OUT_DIR.relative_to(ROOT))
    for key, name, title, fn in SHOTS:
        if want and not key.startswith(want):
            continue
        print("[%s] %s" % (key, title))
        fn()
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
