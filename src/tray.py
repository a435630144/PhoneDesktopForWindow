# -*- coding: utf-8 -*-
"""系统托盘：常驻右下角，菜单控制整理窗口 / 小组件 / 开机自启 / 设置 / 退出。

另提供 notify_via_tray()：供 desktop_window 等模块发托盘通知（延迟绑定实例）。
"""
import os
import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import autostart
from . import i18n
from . import theme
from . import win32_layer
from .paths import asset_path
from .widget_layer import WIDGET_NAMES

_tray_instance = None


def widget_name(wtype) -> str:
    """小组件菜单里的条目名（内置走 i18n，插件用自己声明的中文名）。"""
    from . import widget_registry
    spec = widget_registry.get(wtype)
    if spec is not None:
        return spec.display_name()
    return WIDGET_NAMES.get(wtype, wtype or "")


def notify_via_tray(title: str, msg: str, msecs: int = 3500):
    """全局托盘通知（无实例时静默丢弃）。"""
    if _tray_instance is not None:
        _tray_instance.notify(title, msg, msecs)


def arm_exit_watchdog(delay: float = 2.0):
    """退出看门狗：Qt 在 Windows 上退出时若托盘气泡仍在播放会死锁（已知问题），
    起一个守护线程兜底强制退出。退出前先恢复原生桌面图标。"""
    def _worker():
        time.sleep(delay)
        win32_layer.restore_native_icons()
        os._exit(0)
    threading.Thread(target=_worker, daemon=True).start()


class SystemTray:
    def __init__(self, window, storage, layer):
        global _tray_instance
        self.window = window
        self.storage = storage
        self.layer = layer
        self.openSettings = None
        self.openWidgetCenter = None   # 外部注入：打开设置对话框
        self.openAddApps = None    # 外部注入：打开添加应用对话框
        self.tray = QSystemTrayIcon(QIcon(asset_path("app_icon.png")))
        self.tray.setToolTip(i18n.T("tray.tooltip"))
        self.menu = QMenu()
        self.menu.setStyleSheet(theme.MENU_QSS)
        # 每次弹出前重建：勾选态 / 模式文案 / 仅小组件下的入口增减都随
        # 当前设置走，杜绝"菜单还是上次构建时的旧状态"。
        self.menu.aboutToShow.connect(self._build_menu)
        self._widget_actions = {}
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.window.visibilityChanged.connect(self._sync_checks)
        _tray_instance = self

    # ---------- 菜单 ----------
    def _build_menu(self):
        m = self.menu
        m.clear()
        # 2026-10-21 反馈 9：「显示原生桌面 / 小组件模式」两个条目移除——
        # 模式控制统一收口到设置页（仅小组件模式 / 显示桌面小组件），
        # 托盘只留纯动作。窗口显隐改由双击托盘图标完成（_on_activated）。

        act_add = QAction(i18n.T("tray.add_apps"), m)
        act_add.triggered.connect(self._add_apps)
        m.addAction(act_add)

        act_rescan = QAction(i18n.T("tray.rescan"), m)
        act_rescan.triggered.connect(self._rescan)
        m.addAction(act_rescan)

        # 布局切换：仅小组件模式下组件必须无界，入口不出现（窗口侧还有
        # set_layout_mode 护栏双保险）。
        if not bool(self.storage.get_setting("widgets_only")):
            from . import grid_flow
            self.act_layout = QAction(
                i18n.T("tray.to_free") if not grid_flow.free_mode(self.storage)
                else i18n.T("tray.to_grid"), m)
            self.act_layout.triggered.connect(self._toggle_layout_mode)
            m.addAction(self.act_layout)

        self.widget_menu = m.addMenu(i18n.T("tray.widgets"))
        self.widget_menu.setStyleSheet(theme.MENU_QSS)
        self._fill_widget_menu()

        self.act_autostart = QAction(i18n.T("tray.autostart"), m, checkable=True)
        self.act_autostart.setChecked(autostart.is_enabled())
        self.act_autostart.triggered.connect(self._toggle_autostart)
        m.addAction(self.act_autostart)

        act_settings = QAction(i18n.T("tray.settings"), m)
        act_settings.triggered.connect(lambda: self.openSettings and self.openSettings())
        m.addAction(act_settings)

        m.addSeparator()
        act_quit = QAction(i18n.T("tray.quit"), m)
        act_quit.triggered.connect(self._quit)
        m.addAction(act_quit)

    def _fill_widget_menu(self):
        self.widget_menu.clear()
        self._widget_actions.clear()
        for entry in self.storage.widgets:
            name = widget_name(entry.get("type"))
            act = QAction(name, self.widget_menu, checkable=True)
            act.setChecked(not entry.get("hidden"))
            act.triggered.connect(lambda checked, eid=entry["id"]: self.layer.toggle_entry(eid, checked))
            self.widget_menu.addAction(act)
            self._widget_actions[entry["id"]] = act
        self.widget_menu.addSeparator()
        act_new = QAction(i18n.T("tray.new_note"), self.widget_menu)
        act_new.triggered.connect(self.layer.add_note)
        self.widget_menu.addAction(act_new)
        # v8 C3：组件库入口（托盘是三个入口里最常用的一个）
        act_center = QAction(i18n.T("tray.center"), self.widget_menu)
        act_center.triggered.connect(
            lambda: self.openWidgetCenter and self.openWidgetCenter())
        self.widget_menu.addAction(act_center)

    # ---------- 动作 ----------
    def _toggle_window(self):
        self.window.toggle()
        self._sync_checks()

    def _add_apps(self):
        if self.openAddApps:
            self.openAddApps()

    def _rescan(self):
        added, removed = self.window.rescan_desktop()
        self.notify(i18n.T("tray.notify.rescan.title"),
                    i18n.T("tray.notify.rescan.body", added=added, removed=removed))

    def _toggle_layout_mode(self):
        """v6 §1.3：网格 <-> 无界 切换（托盘入口；切换后刷新菜单文案）。"""
        from . import grid_flow
        self.window.set_layout_mode(
            "grid" if grid_flow.free_mode(self.storage) else "free")
        self._build_menu()
        self.notify(i18n.T("tray.notify.layout.title"),
                    i18n.T("tray.notify.layout.free")
                    if grid_flow.free_mode(self.storage)
                    else i18n.T("tray.notify.layout.grid"))

    def _toggle_autostart(self, checked):
        autostart.set_enabled(checked)
        self.storage.set_setting("autostart", bool(checked))

    def _quit(self):
        # 先恢复原生桌面图标，再走退出流程（atexit 还会兜底一次）
        win32_layer.restore_native_icons()
        arm_exit_watchdog()
        # B14：便签/待办的防抖定时器退出后不会再触发——先把控件里的
        # 最新内容刷进 entry 再落盘，否则丢最后 0.3~0.6 秒的输入
        layer = getattr(self.window, "_widget_layer", None)
        if layer is not None:
            try:
                layer.flush_pending()
            except Exception:
                pass
        self.storage.save()
        self.window.hide_desktop()
        QApplication.quit()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._toggle_window()
        elif reason == QSystemTrayIcon.Trigger and not self.window.isVisible():
            self.window.show_desktop()

    # ---------- 状态同步 ----------
    def _sync_checks(self):
        self.act_autostart.setChecked(autostart.is_enabled())
        for entry in self.storage.widgets:
            act = self._widget_actions.get(entry["id"])
            if act is not None:
                act.setChecked(not entry.get("hidden"))

    def show(self):
        self.tray.show()

    def notify(self, title: str, msg: str, msecs: int = 4000):
        self.tray.showMessage(title, msg, QSystemTrayIcon.Information, msecs)
