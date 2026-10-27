# -*- coding: utf-8 -*-
"""
掌镜桌面 - 第三方桌面美化软件（安卓式桌面 + 小组件 + Dock）

功能：
  - 类安卓桌面网格：桌面文件克隆（可选），图标可拖拽、双击启动
  - 类安卓文件夹：把一个图标拖到另一个图标上即生成文件夹，支持拖出 / 解散
  - 批量框选：整理模式下空白处拖出橡皮筋多选，可整体拖动
  - 桌面小组件：时钟、便签、桌面面板（搜索 / 快捷文件夹 / 磁盘 / 最近下载 / 天气）
  - 常驻右下角托盘、开机自启动、设置实时生效
  - 窗口挂载到桌面层（Progman 正上方的桌面带），像原生桌面一样
    位于壁纸之上、应用窗口之下
  - 双模式：默认保持系统原生桌面（右键 / 点击 / 框选全部原生）；
    设置里可开启桌面整理模式（隐藏原生图标，由整理桌面全盘接管）

运行：
  python main.py             正常启动
  python main.py --selftest  开发自测（启动 -> 截图 -> 退出）
"""
import atexit
import sys


def _install_crash_logger():
    """窗口化(--windowed)构建会把 stderr 静默丢弃，启动期/事件循环里的未捕获
    异常完全看不到，导致「exe 一闪而过 / 静默退出」却查不到原因。

    这里把 traceback 落盘到 crash.log，下次跑 exe 直接看该文件即可定位。
    """
    import os
    import tempfile
    import traceback
    import datetime

    base = os.environ.get("APPDATA")
    # 必须走 `paths.ensure_app_dirs()`：它会先做一次性数据迁移再建目录。
    # 这里若自己 makedirs，新目录先落地，迁移就会判定"新目录已存在"而跳过，
    # 用户升级后布局全丢（v10 D1 的真实坑，tests/P10 锁住）。
    try:
        from src.paths import ensure_app_dirs as _ensure
        d = str(_ensure())
    except Exception:
        d = os.path.join(base, "PhoneDesktop") if base else tempfile.gettempdir()
    log = os.path.join(d, "crash.log")

    def _rotate(path, cap=256 * 1024):
        """S12：超 256KB 截断重写（保留最新一半内容）。"""
        try:
            if os.path.getsize(path) <= cap:
                return
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[len(lines) // 2:])
        except Exception:
            pass

    def _hook(etype, exc, tb):
        try:
            _rotate(log)
            with open(log, "a", encoding="utf-8") as f:
                f.write("\n=== %s ===\n" % datetime.datetime.now().isoformat())
                traceback.print_exception(etype, exc, tb, file=f)
                f.write("crash.log 路径: %s\n" % log)
        except Exception:
            pass
        try:
            sys.__excepthook__(etype, exc, tb)
        except Exception:
            pass

    sys.excepthook = _hook


# 必须在**任何重依赖导入之前**安装：冻结包一旦缺模块，import 阶段就抛
# ImportError，而 --windowed 把 stderr 丢进虚空——v6f「exe 约 12s 静默退出、
# crash.log 一片空白」的最可能真凶，就是这种"死在日志钩子之前"的启动失败。
_install_crash_logger()

from PySide6.QtCore import Qt, QTimer            # noqa: E402
from PySide6.QtGui import QIcon                    # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

from src import autostart                          # noqa: E402
from src import onboarding                         # noqa: E402
from src import win32_layer                        # noqa: E402
from src.desktop_window import DesktopWindow       # noqa: E402
from src.dock import DockWindow, seed_from_layout  # noqa: E402
from src.paths import asset_path, ensure_app_dirs  # noqa: E402
from src import widget_plugin                     # noqa: E402
from src.single_instance import SingleInstance     # noqa: E402
from src import i18n                               # noqa: E402
from src.storage import Storage                    # noqa: E402
from src.tray import SystemTray                    # noqa: E402
from src.widget_layer import WidgetLayer           # noqa: E402


def _open_settings(window, storage, layer, page=None):
    """按需导入设置面板（v15c：冷启动省 ~130ms）。

    `src.settings_dialog` 会连带拉起 `src.updater`（requests/SSL 一整条链），
    而启动路径上**没有任何一处**需要它——只有用户真的点设置才需要。放在
    函数里导入，冻结包同样能被打包器静态发现（PyInstaller 扫 import 语句，
    不看它在哪一层）。
    """
    from src.settings_dialog import open_settings
    if page is None:
        return open_settings(window, storage, layer)
    return open_settings(window, storage, layer, page=page)


def _open_add_apps(window):
    """按需导入添加应用对话框（同理：启动时不建它，省 ~60ms 导入）。"""
    from src.add_apps_dialog import AddAppsDialog
    return AddAppsDialog(window).exec()


def main() -> int:
    ensure_app_dirs()

    app = QApplication(sys.argv)
    app.setApplicationName("PhoneDesktop")    # 不改：影响 %APPDATA% 数据目录路径
    app.setQuitOnLastWindowClosed(False)  # 隐藏窗口不等于退出，常驻托盘
    app.setWindowIcon(QIcon(asset_path("app_icon.png")))

    # 单实例守护：防止重复运行出现多个整理窗口。旧键只读兼容——旧版 exe 还开着时
    # 新版不许再起一个（两个进程同时接管桌面层会互相抢窗口）。
    guard = SingleInstance("PhoneDesktop_SingleInstance",
                           legacy_keys=("DesktopOrganizerDemo_SingleInstance",))
    if guard.is_running():
        return 0

    # 启动自愈：上次运行若崩溃（原生图标被隐藏时强杀），先恢复再继续
    win32_layer.restore_native_icons()
    # 退出兜底：正常退出 / 异常退出都恢复原生桌面图标
    atexit.register(win32_layer.restore_native_icons)
    # 2026-10-25 补：closeEvent→quitRequested→app.quit() 这条退出路径
    # 不经过托盘 _quit() 的显式恢复，atexit 实测也不会执行（冻结包 WM_CLOSE
    # 退出后原生图标仍隐藏）。aboutToQuit 是 Qt 一切正常退出的公共汇合点，
    # 恢复挂这里；restore 幂等，与托盘路径/看门狗多次调用无害。
    app.aboutToQuit.connect(win32_layer.restore_native_icons)

    storage = Storage()
    # v10 D3（反馈 15-a）：语言必须在**任何界面对象之前**定下来——托盘/设置/
    # 组件库的文案都是建对象时写进去的，晚一步设就会留下半中半英的窗口。
    i18n.set_language(storage)
    app.setApplicationDisplayName(i18n.T("app.name"))  # 显示名随语言切换
    # v9 V3：先加载用户导入的组件（外部 zip），再建组件层——否则已在布局里
    # 的插件组件会因为注册表还没有它而静默消失。
    widget_plugin.load_all()

    # 路线A（2026-10-18 用户拍板）：默认进入桌面整理模式——隐藏原生图标、
    # 桌面文件全部映射进网格。启动即无条件补扫一次（幂等，只增不减），
    # 保证升级/重装后网格不空（旧版 migrate_to_native_mode 会清空网格并
    # 关闭整理模式，已废弃为 no-op）。
    storage.ensure_desktop_clone()
    # v15c：Dock 首次填充要在**建窗口之前**——`_dock_inset()` 读 storage，
    # 条带厚度随条目数变化；晚填会让首次网格按"没有 Dock"算一遍，
    # `set_dock` 再按真实 inset 重建一次（100+ 图标，实测 +20~55ms）。
    seed_from_layout(storage)

    window = DesktopWindow(storage)
    window.show_desktop()

    layer = WidgetLayer(storage)
    # M6：翻页时组件与图标联动滑动（sync_page）
    window.set_widget_layer(layer)

    # v10 C3（反馈 17）：Dock 栏。首次启用用桌面上现成的应用填一次
    # （`dock_seeded` 只填一次——用户手动清空后不会被重新塞满），
    # 再交给窗口接线。
    dock_bar = DockWindow(storage)
    window.set_dock(dock_bar)
    # v11（反馈 31）：桌面上的 Ctrl+C / Ctrl+V。桌面窗口是 WS_EX_NOACTIVATE
    # （永不获得焦点），键盘事件到不了 keyPressEvent——只能挂低层键盘钩子，
    # 判据见 desktop_hotkeys 模块头（指针在我方桌面 + 前台不是别的应用 +
    # 真的有事可做，三条同时成立才吞键）。
    # v14（反馈 2）：钩子由窗口持有（设置里可关、关窗必撤），见
    # `DesktopWindow.setup_desktop_hotkeys`。
    window.setup_desktop_hotkeys()
    # 锚定小组件是网格一等公民：占位变化（落定/移动）时挤走被压住的图标
    layer.gridChanged.connect(window.on_widget_grid_changed)
    # M4：组件拖动实时预览（hover 按新占位矩形实时挤走图标，松手回退）
    layer.anchorHover.connect(window._on_widget_hover)
    layer.anchorDrop.connect(window._on_widget_drop)
    # 启动兜底：小组件 spawn 时分配的占位可能压住已有图标（旧 state 升级），
    # 此时信号还未连上，手动触发一次 displace
    window.on_widget_grid_changed()

    tray = SystemTray(window, storage, layer)
    # v9 V2：设置面板改单例 + 非模态（托盘/桌面右键/组件库共用同一个窗口）
    tray.openSettings = lambda: _open_settings(window, storage, layer)
    tray.openAddApps = lambda: _open_add_apps(window)
    tray.openWidgetCenter = lambda: _open_settings(window, storage, layer,
                                                   page="widgets")
    window.openSettingsRequested.connect(tray.openSettings)
    window.openAddAppsRequested.connect(tray.openAddApps)
    window.openWidgetCenterRequested.connect(tray.openWidgetCenter)
    window.quitRequested.connect(app.quit)
    tray.show()

    # 设置里开启了自启但注册表项丢失（如换目录运行）时自动修复
    autostart.sync_on_startup(storage)

    # v6 §7.4：桌面目录自动同步（QFileSystemWatcher + 1s debounce，
    # 增量增删不重排；设置里可关）
    window.set_auto_sync(bool(storage.get_setting("auto_sync_desktop")))
    app.aboutToQuit.connect(lambda: window.set_auto_sync(False))

    # 首次启动：跑一遍新手操作指引（v11b）。
    # 旧版这里弹一条托盘气泡，文案是"系统桌面保持原生…"——路线A 之后默认
    # 就是桌面整理模式，那段话已经和实际行为相反；而且气泡不能高亮、不能
    # 逐步讲，用户点掉就再也找不回来。现在换成可重看的聚光灯指引（入口在
    # 设置 ▸ 通用）。`first_run` 只保留作旧 state 的迁移标记。
    if storage.get_setting("first_run"):
        storage.set_setting("first_run", False)
        window.rebuild()
    if "--selftest" not in sys.argv[1:] and onboarding.should_show(storage):
        # 等首帧落定再弹：小组件还要 spawn、Dock 要按 inset 重排一次，
        # 太早取目标矩形会拿到空窗（高亮框画在错误位置）。
        QTimer.singleShot(1200, window.start_onboarding)

    if "--selftest" in sys.argv[1:]:
        from src.selftest import run_selftest
        run_selftest(app, window, layer)

    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.excepthook(*sys.exc_info())
        raise
