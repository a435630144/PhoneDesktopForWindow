# -*- coding: utf-8 -*-
"""桌面带守护：周期性把桌面层窗口拉回"Progman 正上方"的桌面带。

触发场景：切显示器 / 壁纸、explorer 重启等可能把窗口挤出桌面带
（一旦漂移，组件就会盖在应用窗口之上）。只在检测到漂移时才重新插入，
正常情况每轮只做一次廉价的 z-order 遍历。

### 2026-10-19 重写：从「一位判定」到「层号堆栈」

旧实现把「在带内」定义成**紧贴 Progman 上方一位**，而带内同时有桌面窗口、
小组件、幕布、文件夹弹窗四个成员——每个窗口只有最底下那个满足判定，于是
每 1.5 秒全体重插一遍：

  - 小组件被插到桌面窗口**之下** → 全屏整理窗口的 HTCLIENT 吞掉点击
    （用户反馈「组件选不中、右键穿透到底层菜单」）；
  - 层级反复抖动 → 文件夹玻璃闪、焦点被桌面抢回（反馈「一移动鼠标就丢焦点」）。

现在每个窗口登记一个层号（desktop 10 < widget 20 < scrim 30 < popup 40），
守护只校验「相对顺序 + 紧贴 Progman」，被破坏时才整段重排。正常态
零 SetWindowPos。

关键边界——"显示桌面"（Win+D / 点任务栏末端）：
  此时 Windows 会把 Progman 提升到 z-order 前部（topmost 带）来盖住
  应用窗口，并**把所有顶层窗口一并藏起来**——我们也在其中。

  2026-10-20 用户反馈 F1：旧实现在该状态下**直接暂停干预**，注释假定
  "还原后 Windows 会自动恢复窗口的相对位置"。但窗口被藏起来这件事
  Windows 不会自己还回来：接管模式下原生图标已被隐藏，用户按 Win+D
  看到的是**完全空的桌面**。

  现在的行为：识别到 show desktop 态（Progman 的 WS_EX_TOPMOST 位置位）
  就主动 `ShowWindow(SW_SHOWNA)` 恢复可见，并把每个成员按层号插到
  Progman **正上方**（此刻它在 topmost 带，插其下方仍被全屏壁纸盖住）。
  全程不激活，键盘输入仍归用户当前应用。退出该态时立刻
  `enforce_band_order` 全量重排回桌面带——绝不留 topmost 残留。
"""
from PySide6.QtCore import QObject, QTimer

from . import win32_layer
from .win32_layer import attach_to_desktop

# 复活风暴防护：观察窗 120 秒内最多复活 8 次——桌面层若被第三方
# （桌面整理软件 / 显示桌面切换）反复拆毁，拉锯只会让窗口每 1.5 秒
# 闪一次；超过阈值改为恢复原生图标并干净退出。
_REVIVE_WINDOW_S = 120
_REVIVE_MAX = 8


class BandGuard(QObject):
    def __init__(self, parent=None, interval_ms: int = 1500, on_tick=None):
        super().__init__(parent)
        self._items = []  # [(hwnd, layer)]：本守护负责的窗口（共享全局层表）
        self._on_tick = on_tick  # 每轮额外回调（如保持原生图标隐藏）
        self._widgets = {}  # hwnd -> weakref(widget)：句柄被外部销毁后的复活依据
        self._revives = []  # 最近复活时刻（monotonic），防复活风暴
        self._progman = win32_layer._progman()
        self._in_show_desktop = False
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._check)

    # ---------- 跟踪管理 ----------
    def track(self, widget, below_hwnd: int = 0,
              layer: int = win32_layer.BAND_LAYER_DESKTOP):
        """登记一个要守护的窗口。

        签名兼容旧调用（第二个位置参数曾是 below_hwnd）：layer 关键字优先，
        below_hwnd 保留给极少数需要「插到某窗口下方」的历史路径（当前无）。
        """
        try:
            hwnd = int(widget.winId())
        except Exception:
            return
        if not hwnd:
            return
        if (hwnd, layer) not in self._items:
            self._items = [(h, ly) for h, ly in self._items if h != hwnd]
            self._items.append((hwnd, layer))
        try:
            import weakref
            self._widgets[hwnd] = weakref.ref(widget)
        except Exception:
            pass
        win32_layer.set_band_layer(hwnd, layer)
        if not self._timer.isActive():
            self._timer.start()

    def untrack(self, widget):
        try:
            hwnd = int(widget.winId())
        except Exception:
            return
        self._items = [(h, ly) for h, ly in self._items if h != hwnd]
        self._widgets.pop(hwnd, None)
        win32_layer.unregister_band_window(hwnd)
        if not self._items and self._timer.isActive():
            self._timer.stop()

    # ---------- 周期检查 ----------
    def _check(self):
        try:
            progman = win32_layer._progman()
            if not progman:
                return
            # explorer 重启等情况：Progman 句柄变化，全部重新挂载
            if progman != self._progman:
                self._progman = progman
                self._in_show_desktop = False
                win32_layer.enforce_band_order(progman)
                self._tick_hook()
                return

            # 2026-10-25 事故（用户实机）：桌面层窗口被外部销毁（explorer
            # 并未重启——疑似第三方桌面整理/显示桌面切换连带清理了跨进程
            # 子窗口），Qt 事件循环照常跑但所有原生窗口消失，进程变成
            # "无窗口僵尸"：不退出、不重建、原生图标停在隐藏态。这里先于
            # 一切检查做死亡检测：死句柄留在 _items 里，后续 enforce/
            # restore 全部空转，永远发现不了。
            if self._revive_dead_windows():
                return

            wins = win32_layer._enum_zorder()
            if progman not in wins:
                return

            # "显示桌面"（Win+D / 任务栏末端）判定：Progman 进入 topmost 带
            # （shell 把它提到最前以盖住应用窗口）。
            # 2026-10-20 用户反馈 F1：旧判定是「pidx < max(60, total*0.3)」
            # ——窗口总数少时会把正常态误判成 show desktop，进而把我们的
            # 窗口插进 topmost 带（会盖住应用窗口）。改读 WS_EX_TOPMOST 位。
            show_desktop = win32_layer.progman_is_topmost(progman)
            if show_desktop:
                # 2026-10-20 反馈 F1：这里**不能只暂停干预**。Show Desktop
                # 会把所有顶层窗口一起藏起来（我们也在其中），而接管模式下
                # 原生图标已被隐藏 → 用户看到完全空的桌面。改为主动恢复：
                # ShowWindow(SW_SHOWNA) + 插到 Progman 正上方（此刻它在
                # topmost 带，插其下方仍被全屏壁纸盖住）。全程不激活，
                # 键盘输入仍归用户当前应用。
                self._in_show_desktop = True
                win32_layer.restore_band_windows(self._items, progman)
                self._tick_hook()
                return
            if self._in_show_desktop:
                # 退出显示桌面：立刻全量重排回桌面带——绝不留 topmost 残留
                # （否则我们的窗口会漂到应用窗口之上）。
                self._in_show_desktop = False
                win32_layer.enforce_band_order(progman, wins)
                self._tick_hook()
                return

            # 交互型组件正被用户操作时跳过（激活态会自然浮在应用窗口之上，
            # 这是输入所必需的；失焦后下一轮自动回带）
            fg = win32_layer._user32.GetForegroundWindow()
            if fg and fg in [h for h, _ in self._items]:
                self._tick_hook()
                return
            win32_layer.enforce_band_order(progman, wins)
            self._tick_hook()
        except Exception:
            return

    def _tick_hook(self):
        if self._on_tick is None:
            return
        try:
            self._on_tick()
        except Exception:
            pass

    # ---------- 窗口死亡自愈（2026-10-25 事故补） ----------
    def _revive_dead_windows(self) -> bool:
        """检测并复活被外部销毁的受护窗口。本轮处理过死亡返回 True。

        正常轮次零成本（每个句柄一次 IsWindow）；只有句柄死亡才走复活：
        Qt 控件对象还活着的话 hide()+show() 会重建原生窗口，随后重新挂回
        桌面带并以新句柄登记——对用户等价于"桌面闪了一下又回来了"，
        而不是进程无声变僵尸、原生图标被永久扣在隐藏态。

        两个退路：
        - 复活风暴（观察窗内超过 _REVIVE_MAX 次）：桌面层在被第三方反复
          拆毁，拉锯无意义——恢复原生图标并干净退出（退出链上的
          aboutToQuit/atexit 会再恢复一次，幂等）。
        - 全部受护窗口死亡且无活控件可复活：同样恢复 + 退出。
        """
        import time as _time
        now = _time.monotonic()
        self._revives = [t for t in self._revives if now - t < _REVIVE_WINDOW_S]
        dead = [(h, ly) for h, ly in self._items
                if not win32_layer._user32.IsWindow(h)]
        if not dead:
            return False
        for hwnd, layer in dead:
            self._items = [(h, ly) for h, ly in self._items if h != hwnd]
            win32_layer.unregister_band_window(hwnd)
            ref = self._widgets.pop(hwnd, None)
            widget = ref() if ref is not None else None
            if widget is None:
                continue
            # 正在正常关闭途中的短命窗口（文件夹弹窗/幕布的收起动画，
            # `_closing=True`）不是"被杀"：只摘登记，不计复活、不拉起。
            # 2026-10-25 事故：每次 hover 开合都会制造两个死句柄，旧逻辑
            # 把它们当"被外部销毁"逐个 destroy()+show() 拉活，120 秒内第
            # 9 次就触发 _give_up_and_exit——用户看到"先回原生桌面再退出
            # 接管"。正常关闭的窗口绝不该进复活路径。
            if getattr(widget, "_closing", False):
                continue
            if len(self._revives) >= _REVIVE_MAX:
                self._give_up_and_exit()
                return True
            try:
                # 探针结论（tools 同款实验）：外部销毁后 hide()/show() 和
                # createWinId() 都会拿回**陈旧死句柄**不重建；只有
                # destroy()+show() 会真正重建原生窗口。
                widget.destroy()
                widget.show()
                attach_to_desktop(widget, layer=layer)
                self._revives.append(_time.monotonic())
                self.track(widget, layer=layer)
            except Exception:
                continue
        if not self._items and self._timer.isActive():
            self._give_up_and_exit()
        return True

    def _give_up_and_exit(self):
        """放弃拉锯：恢复原生图标，让应用走正常退出（不留在僵尸态）。"""
        try:
            self._items = []
            self._widgets = {}
            win32_layer.restore_native_icons()
        except Exception:
            pass
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.quit()
        except Exception:
            pass

    def _remount_all(self, progman):
        try:
            win32_layer.enforce_band_order(progman)
        except Exception:
            pass
