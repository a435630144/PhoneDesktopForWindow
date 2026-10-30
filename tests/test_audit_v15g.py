# -*- coding: utf-8 -*-
"""v15g：设置面板「显示值 / 落盘 / 依赖态」一致性（8.5.1 第三批）。

这一批全是**控件与真相源不一致**的类别，外加一处真性能账：

  G1 Dock 液态开关读的是派生值（总闸 AND liquid_dock），写的却是原键
     -> 总闸关着时动一下 Dock，用户从没关过的 liquid_dock 被写成 False 且
        再也恢复不了（"液态玻璃莫名其妙没了"）
  G2 改列数 / 行数 / 原点后立刻回读**旧几何快照**，步进器被弹回旧值
     -> 桌面按新值重排了，面板显示的还是旧值（用户以为没生效，反复点）
  G3 「恢复默认配置 / 导入备份」把 language 写回 auto，但不重解析界面语言
     -> 面板重开仍是旧语言，下拉显示「自动」，重启才一致
  G4 「标签文字」重置漏同步描边颜色按钮（文字色同步了，描边色没同步）
  G5 `page_rules.INHERIT` 是空串，拼出 key `"page_rules."` -> 英文界面漏译
  G6 三个父开关（图标悬停 / 落点预览 / 文件夹悬停）关闭后子控件仍可操作
  G7 `set_setting()` 内部同步 save()（含 fsync），"250ms 合并落盘"空转
     -> 拖 Dock 不透明度每像素一次全量写盘 + fsync

判据口径统一为「面板显示值 == 实际生效值 / 控件可用态 == 语义依赖」，
不锁实现细节。

运行：python tests/test_audit_v15g.py   （offscreen）
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QMessageBox  # noqa: E402

from src import grid_flow, i18n, liquid_glass, page_rules  # noqa: E402
from src.desktop_window import DesktopWindow  # noqa: E402
import src.settings_dialog as sd  # noqa: E402
from src.settings_dialog import SettingsDialog  # noqa: E402
from src.storage import Storage  # noqa: E402

app = QApplication.instance() or QApplication([])
TMP = Path(tempfile.gettempdir()) / "test_audit_v15g"
TMP.mkdir(exist_ok=True)

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def settle(ms=120):
    end = QTimer()
    end.setSingleShot(True)
    loop = QEventLoop()
    end.timeout.connect(loop.quit)
    end.start(ms)
    loop.exec()


def fresh(tag="a", **settings):
    p = TMP / ("state_%s.json" % tag)
    if p.exists():
        p.unlink()
    st = Storage(path=str(p))
    st._data["settings"].update({
        "hide_native_icons": True, "takeover_desktop": True,
        "auto_clone_desktop": False, "auto_arrange": False,
        "dock_enabled": True, "layout_mode": "grid"})
    st._data["settings"].update(settings)
    st._data["layout"] = []
    st._data["widgets"] = []
    st.save()
    return st


class FakeWindow:
    """设置面板只依赖"窗口"这一契约：缺方法就跳过，不该崩。"""

    def __init__(self, storage):
        self.storage = storage
        self.attached = False
        self.refresh_calls = 0

    def refresh_grid(self):
        self.refresh_calls += 1

    def rebuild(self):
        self.refresh_calls += 1

    def update(self):
        pass

    def apply_dock_settings(self):
        pass

    def apply_dock_style(self):
        pass

    def apply_dock_metrics(self, *a):
        pass

    def apply_hover_style(self):
        pass

    def apply_label_style(self):
        pass

    def apply_liquid_style(self):
        pass


# =========================== G1 Dock 液态开关 ===========================
print("\n--- G1 Dock 液态开关：显示原键、总闸关时禁用 ---", flush=True)
st = fresh("g1", liquid_dock=True, liquid_glass=False)
dlg = SettingsDialog(FakeWindow(st), st, None, page="dock")
box = dlg.chk_dock_liquid
check("G1a 总闸关 + liquid_dock=True -> 开关显示开（原键，不是派生值）",
      box.isChecked() is True, "checked=%r" % box.isChecked())
check("G1b 总闸关 -> 开关禁用（写了也不生效，不给无效旋钮）",
      box.isEnabled() is False, "enabled=%r" % box.isEnabled())
check("G1c 总闸关 -> 不透明度可调（此时没有液态，互斥不成立）",
      dlg.sld_dock_alpha.isEnabled() is True,
      "enabled=%r" % dlg.sld_dock_alpha.isEnabled())

# 总闸关着，用户去动 Dock 的图标大小 -> 不能把 liquid_dock 写坏
dlg.sp_dock_icon.setValue(int(dlg.sp_dock_icon.value()) + 2)
check("G1d 总闸关时改 Dock 其它项，不写liquid_dock=False",
      bool(st.get_setting("liquid_dock")) is True,
      "liquid_dock=%r" % st.get_setting("liquid_dock"))

# 总闸打开 -> 开关恢复可编辑，不透明度按"生效值"置灰
st.set_setting("liquid_glass", True)
dlg._apply_liquid_master(True)
check("G1e 总闸打开 -> 开关可编辑", box.isEnabled() is True)
check("G1f 液态生效 -> 不透明度置灰（互斥仍成立）",
      dlg.sld_dock_alpha.isEnabled() is False)
dlg.chk_dock_liquid.setChecked(False)
check("G1g 关掉 Dock 液态 -> 不透明度恢复可调",
      dlg.sld_dock_alpha.isEnabled() is True
      and st.get_setting("liquid_dock") is False)
dlg.deleteLater()
dlg = None

# =========================== G2 步进器回读 ===========================
print("\n--- G2 行列 / 原点步进器：落地后必须回读真值 ---", flush=True)
W, H = 1920, 1040
st2 = fresh("g2", grid_dims_mode="manual", grid_cols=8, grid_rows=5,
            dev_mode=True)
win = DesktopWindow(st2)
win.resize(W, H)
win._update_grid_metrics()
settle(80)
dlg2 = SettingsDialog(win, st2, None, page="layout")
if not dlg2.chk_auto_cols.isChecked():
    dlg2.spin_cols.setValue(5)
    settle(320)          # 60ms 防抖 + 一次重建
    m = grid_flow.current_metrics()
    check("G2a 列数步进器显示 == 实际生效列数",
          dlg2.spin_cols.value() == m.cols == 5,
          "spin=%d live=%d" % (dlg2.spin_cols.value(), m.cols))
    # 超过上限 -> 被夹紧，面板要显示夹紧后的值（不是用户输入的 999）
    dlg2.spin_cols.setValue(999)
    settle(320)
    m = grid_flow.current_metrics()
    check("G2b 越界列数被夹紧后，面板显示夹紧值",
          dlg2.spin_cols.value() == m.cols and m.cols < 999,
          "spin=%d live=%d" % (dlg2.spin_cols.value(), m.cols))
else:
    check("G2a 前置：手动列数模式", False, "chk_auto_cols 仍勾着")

# 原点：越界输入 -> 生效值夹紧，面板跟着显示生效值
dlg2.chk_origin.setChecked(True)
dlg2.sp_ox.setValue(12345)
settle(320)
ox, oy = grid_flow.origin()
check("G2c 原点步进器显示 == 实际生效原点",
      dlg2.sp_ox.value() == ox,
      "spin=%d live=%d" % (dlg2.sp_ox.value(), ox))
dlg2.deleteLater()
win.deleteLater()
dlg2 = win = None

# =========================== G3 重置 / 导入切语言 ===========================
print("\n--- G3 恢复默认：language 回 auto 后必须重解析界面语言 ---", flush=True)
st3 = fresh("g3")
i18n.set_language("en")
old_open = sd.open_settings
_old_ask = SettingsDialog._ask_yes_no
try:
    # 直接替换确认框：绕过 exec 的 QMessageBox 在析构时会让进程
    # 以 0xC0000409 退出（实测 1/3），别用 patch exec 的写法
    SettingsDialog._ask_yes_no = lambda self, *a, **k: True
    sd.open_settings = lambda *a, **k: None      # 不真的重开面板
    dlg3 = SettingsDialog(FakeWindow(st3), st3, None, page="advanced")
    check("G3a 前置：界面是英文", i18n.language() == "en",
          "lang=%s" % i18n.language())
    dlg3._reset_all_settings()
    want = i18n.system_language()
    check("G3b 恢复默认后运行态语言 == 系统语言（不再是英文）",
          i18n.language() == want,
          "lang=%s want=%s" % (i18n.language(), want))
    check("G3c storage 里 language 也回到 auto",
          not st3.get_setting("language")
          or str(st3.get_setting("language")) == "auto",
          "language=%r" % st3.get_setting("language"))
finally:
    SettingsDialog._ask_yes_no = _old_ask
    sd.open_settings = old_open
    i18n.set_language("zh_CN")

# =========================== G4 标签描边色重置 ===========================
print("\n--- G4 标签文字重置：描边色按钮要跟着回位 ---", flush=True)
st4 = fresh("g4", label_outline=True, label_outline_color="#ff0000",
            label_outline_width=2)
dlg4 = SettingsDialog(FakeWindow(st4), st4, None, page="icons")
dlg4._reset_label_text()
from src import label_style  # noqa: E402

dflt = label_style.default_label_style()
check("G4a 重置后 _label_outline_color == 默认色",
      str(getattr(dlg4, "_label_outline_color", "")).lower()
      == dflt.outline_color.name().lower(),
      "_label_outline_color=%r want=%s"
      % (getattr(dlg4, "_label_outline_color", None),
         dflt.outline_color.name()))
check("G4b 重置后描边色按钮文字 == 默认色",
      dflt.outline_color.name().upper() in dlg4.btn_label_outline_color.text(),
      "btn=%r" % dlg4.btn_label_outline_color.text())
dlg4.deleteLater()
dlg4 = None

# =========================== G5 page_rules 本地化 ===========================
print("\n--- G5 每页规则下拉：英文界面不能漏出中文 ---", flush=True)
i18n.set_language("en")
st5 = fresh("g5", page_rules_custom=True)
dlg5 = SettingsDialog(FakeWindow(st5), st5, None, page="layout")
combos = [c for c in dlg5._rules_holder.findChildren(QComboBox)]
check("G5a 每页规则下拉存在", len(combos) > 0, "n=%d" % len(combos))
if combos:
    first = combos[0].itemText(0)
    want_txt = i18n.T("page_rules.inherit")
    check("G5b 首项（跟随全局）走词表，不是中文兜底",
          first == want_txt and first != "跟随全局",
          "item=%r want=%r" % (first, want_txt))
    keys = i18n.all_keys("en")
    missing = [v for v, _t in page_rules.MODE_LABELS
               if ("page_rules." + (v or "inherit")) not in keys]
    check("G5c 三个规则项都有英文词条", not missing, "missing=%s" % missing)
i18n.set_language("zh_CN")
dlg5.deleteLater()
dlg5 = None

# =========================== G6 父开关禁用子控件 ===========================
print("\n--- G6 父开关关闭 -> 子控件一并禁用（不留能点但无效的旋钮） ---", flush=True)
st6 = fresh("g6", icon_hover_enabled=True, cell_preview_enabled=True,
            folder_hover_expand=True)
dlg6 = SettingsDialog(FakeWindow(st6), st6, None, page="icons")

dlg6.chk_hover_enabled.setChecked(False)
hover_kids = ["sld_hover_fill", "sld_hover_scale", "sld_hover_anim",
              "btn_hover_color"]
check("G6a 关图标悬停 -> 填充/放大/动画/颜色全禁用",
      all(getattr(dlg6, n).isEnabled() is False for n in hover_kids),
      "states=%s" % [getattr(dlg6, n).isEnabled() for n in hover_kids])
dlg6.chk_hover_enabled.setChecked(True)
check("G6b 重新打开 -> 子控件恢复可调",
      all(getattr(dlg6, n).isEnabled() is True for n in hover_kids))

dlg6.chk_cell_preview_enabled.setChecked(False)
preview_kids = ["sld_cell_preview_fill", "sp_cell_preview_width",
                "sp_cell_preview_radius", "btn_cell_preview_color",
                "cmb_cell_preview"]
check("G6c 关落点预览 -> 浓度/粗细/圆角/颜色/预设全禁用",
      all(getattr(dlg6, n).isEnabled() is False for n in preview_kids),
      "states=%s" % [getattr(dlg6, n).isEnabled() for n in preview_kids])
dlg6.chk_cell_preview_enabled.setChecked(True)

dlg6.chk_hover_folder.setChecked(False)
folder_kids = ["sld_folder_open", "sld_folder_close"]
check("G6d 关文件夹悬停 -> 展开/关闭时长禁用",
      all(getattr(dlg6, n).isEnabled() is False for n in folder_kids),
      "states=%s" % [getattr(dlg6, n).isEnabled() for n in folder_kids])
dlg6.chk_hover_folder.setChecked(True)
check("G6e 重新打开 -> 时长滑条恢复可调",
      all(getattr(dlg6, n).isEnabled() is True for n in folder_kids))
dlg6.deleteLater()
dlg6 = None

# =========================== G7 批量落盘 ===========================
print("\n--- G7 连续输入合并落盘：拖动不该每像素一次 fsync ---", flush=True)
st7 = fresh("g7", dock_enabled=True, dock_alpha=0.85)
dlg7 = SettingsDialog(FakeWindow(st7), st7, None, page="dock")
dlg7.show()
calls = []
_orig_save = st7.save


def _counting_save(*a, **k):
    calls.append(1)
    return _orig_save(*a, **k)


st7.save = _counting_save

n0 = len(calls)
dlg7.sld_dock_alpha.setValue(60)
n1 = len(calls)
check("G7a 一次外观改动 <= 1 次写盘", n1 - n0 <= 1,
      "writes=%d" % (n1 - n0))

for v in (62, 64, 66, 68):
    dlg7.sld_dock_alpha.setValue(v)
n2 = len(calls)
check("G7b 连续拖动仍不逐次写盘（累计 <= 1）", n2 - n0 <= 1,
      "writes=%d" % (n2 - n0))

dlg7.hide()          # 关面板 -> 兜底落盘
n3 = len(calls)
check("G7c 关闭面板时兜底落盘", n3 > n2, "writes=%d" % (n3 - n2))
disk = json.loads(Path(st7.path).read_text(encoding="utf-8"))
check("G7d 落盘内容是最新值（dock_alpha=0.68）",
      abs(float(disk["settings"].get("dock_alpha", 0)) - 0.68) < 1e-6,
      "disk=%r" % disk["settings"].get("dock_alpha"))

# 默认行为不变：普通一次 set_setting 仍然立刻落盘（其它调用方依赖它）
before = len(calls)
st7.set_setting("dock_gap", 17)
disk = json.loads(Path(st7.path).read_text(encoding="utf-8"))
check("G7e set_setting 默认仍同步落盘（契约不变）",
      len(calls) - before == 1 and disk["settings"].get("dock_gap") == 17,
      "writes=%d gap=%r" % (len(calls) - before,
                            disk["settings"].get("dock_gap")))

# =========================== 汇总 ===========================
print("\n%d/%d 通过" % (total[0] - len(failures), total[0]))
if failures:
    print("FAILED: %s" % ", ".join(failures))
sys.stdout.flush()
# 这里走 `sys.exit` 而不是 `os._exit`：本用例建了真实 DesktopWindow +
# 多个 SettingsDialog，`os._exit`（TerminateProcess）跳过 Qt 的收尾，
# Windows 上有 1/3 概率以 0xC0000409 退出——判据看退出码就成假失败。
# 实测 `sys.exit` 连跑 10 次全 0。（v15d 那批反过来：组件/弹窗的析构
# 阶段段错误，必须 `os._exit`。哪种收尾稳，取决于用例建了什么对象。）
sys.exit(0 if not failures else 1)
