# -*- coding: utf-8 -*-
"""v7a 实机 17 项反馈的 **P0 修理批**（只修坏掉的 / 放错位置的，不动架构）。

背景见 `docs/反馈研判_v7_1.md` §3：17 条里真正"坏了"的只有几条，
其余是 v7 尚未开工的 B2/B3/B4/B6 批次。本文件只锁 P0 这五条：

  F1 网格原点属**开发者功能**：控件在设置页「开发者」页（v11 反馈 32 从
     「高级」搬过去），且 dev_mode 关闭时不可配置
  F2 桌面右键不再提供：原点两项 / 「添加应用…」 / 版本号
  F3 版本号改挂设置页「关于」（v6c 跑错包事故的取证设施，不能直接删）
  F4 行内输入框：**点别处即提交**（不必按 Enter；NOACTIVATE 桌面带里焦点不转移，
     单靠 focusOutEvent 收不到）
  F5 拖动松手**不再放大回弹**（无界模式位置没变，1.06 的弹跳格外突兀）

运行：python tests/test_p0_fixes.py   （offscreen）
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src import grid_sizing as gs
from src import version
from src.desktop_window import DesktopWindow
from src.storage import Storage

W, H = 1920, 1040
STATE = Path(tempfile.gettempdir()) / "test_p0_state.json"
failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def fresh_store(**settings):
    if STATE.exists():
        STATE.unlink()
    st = Storage(path=str(STATE))
    for k, v in settings.items():
        st.set_setting(k, v)
    st.save()
    return st


def menu_texts(menu):
    out = []
    for act in menu.actions():
        if act.isSeparator():
            out.append("---")
        else:
            out.append(act.text())
        sub = act.menu()
        if sub is not None:
            out.extend("  " + t for t in menu_texts(sub))
    return out


# ---------------------------------------------------------------- F1 / F3
def test_settings(st):
    from src.settings_dialog import SettingsDialog

    class FakeWindow:
        storage = st
        attached = True

        def rebuild(self, stagger=False):
            pass

        def refresh_grid(self):
            pass

    # dev_mode 默认关
    st.set_setting("dev_mode", False)
    st.save()
    dlg = SettingsDialog(FakeWindow(), st, layer=None)
    dlg.show()
    QApplication.processEvents()

    names = ("chk_origin", "sp_ox", "sp_oy", "btn_reset_origin")
    check("F1 原点控件齐全", all(getattr(dlg, n, None) is not None for n in names),
          "%s" % [n for n in names if getattr(dlg, n, None) is None])

    # v11 反馈 32：开发者选项单独成页，网格原点随整组搬过去（「高级」只留
    # 维护 / 更新 / 关于）。断言随之改成"在开发者页、且不在其它页"。
    dev_page = dlg._pages.get("advanced")   # v14h：开发者页并入高级页
    layout_page = dlg._pages.get("layout")
    if dev_page is not None and getattr(dlg, "chk_origin", None) is not None:
        check("F1 原点控件在「开发者」页",
              dev_page.isAncestorOf(dlg.chk_origin),
              "parent=%s" % type(dlg.chk_origin.parent()).__name__)
        check("F1 原点控件不在「布局与网格」页",
              layout_page is None or not layout_page.isAncestorOf(dlg.chk_origin))
        check("F1 dev_mode 关闭时原点控件全部禁用",
              not dlg.chk_origin.isEnabled() and not dlg.sp_ox.isEnabled()
              and not dlg.sp_oy.isEnabled()
              and not dlg.btn_reset_origin.isEnabled(),
              "chk=%s x=%s btn=%s" % (dlg.chk_origin.isEnabled(),
                                      dlg.sp_ox.isEnabled(),
                                      dlg.btn_reset_origin.isEnabled()))
        # 打开开发者模式 -> 原点开关可操作（但 X/Y 还disabled：
        # 没进手动模式就没有可调的东西，两级门槛都要过）
        dlg.chk_dev.setChecked(True)
        QApplication.processEvents()
        check("F1 打开开发者模式后原点开关可操作",
              dlg.chk_origin.isEnabled(), "chk_en=%s" % dlg.chk_origin.isEnabled())
        check("F1 未进手动模式时 X/Y 仍禁用",
              not dlg.sp_ox.isEnabled() and not dlg.btn_reset_origin.isEnabled(),
              "x=%s btn=%s" % (dlg.sp_ox.isEnabled(),
                               dlg.btn_reset_origin.isEnabled()))
        dlg.chk_origin.setChecked(True)
        QApplication.processEvents()
        check("F1 开发者开 + 手动开后 X/Y 与重置可编辑",
              dlg.sp_ox.isEnabled() and dlg.sp_oy.isEnabled()
              and dlg.btn_reset_origin.isEnabled(),
              "x=%s y=%s btn=%s" % (dlg.sp_ox.isEnabled(),
                                    dlg.sp_oy.isEnabled(),
                                    dlg.btn_reset_origin.isEnabled()))
        check("F1 打开开发者模式不擅自改写原点数据",
              st.grid_origin_mode() in ("auto", "manual"),
              st.grid_origin_mode())
    else:
        check("F1 设置页取不到 developer 页或原点控件", False, "%s" % (dev_page,))

    # F3 版本号落在设置页「关于」
    got = getattr(dlg, "lbl_about_version", None)
    check("F3 设置页有版本号标签", got is not None,
          "缺 lbl_about_version")
    if got is not None:
        check("F3 版本号文本含当前 VERSION",
              version.VERSION in got.text(), "%r" % got.text())
    dlg.close()


# ---------------------------------------------------------------- F2
def test_context_menu(st):
    w = DesktopWindow(st)
    w.resize(W, H)
    w._update_grid_metrics()
    QApplication.processEvents()
    menu = w._build_context_menu()
    texts = menu_texts(menu)
    joined = " | ".join(texts)

    check("F2 右键不再有「定位网格原点」", "定位网格原点" not in joined, joined)
    check("F2 右键不再有「重置网格位置」", "重置网格位置" not in joined, joined)
    check("F2 右键不再有「添加应用」", "添加应用" not in joined, joined)
    check("F2 右键不再显示版本号", version.VERSION not in joined, joined)
    check("F2 右键仍有「设置…」", "设置" in joined, joined[:120])
    w.close()


# ---------------------------------------------------------------- F4
def test_inline_input():
    from src.folder_popup import BandInputBox

    host = QWidget()
    host.resize(400, 300)
    host.show()
    other = QWidget()
    other.resize(120, 80)
    other.show()
    QApplication.processEvents()

    box = BandInputBox(text="壁纸", title="重命名")
    got = []
    box.committed.connect(got.append)
    box.show_at(host.rect().center())
    QApplication.processEvents()
    box.line.setText("新名字")
    QTest.mouseClick(other, Qt.LeftButton)
    QApplication.processEvents()
    check("F4 点别处即提交（不必 Enter）", got == ["新名字"], "got=%r" % (got,))

    # 空输入 -> 取消，不发 committed（原有语义不能丢）
    box2 = BandInputBox(text="壁纸", title="重命名")
    got2 = []
    cancels = []
    box2.committed.connect(got2.append)
    box2.cancelled.connect(lambda: cancels.append(1))
    box2.show_at(host.rect().center())
    QApplication.processEvents()
    box2.line.setText("")
    QTest.mouseClick(other, Qt.LeftButton)
    QApplication.processEvents()
    check("F4b 空输入点别处视为取消", not got2 and cancels, "got=%r" % (got2,))
    host.close()
    other.close()


# ---------------------------------------------------------------- F5
def test_release_bounce():
    from src.iconwidget import AppIcon

    def entry(eid, name):
        return {"id": eid, "type": "app",
                "app": {"id": eid, "name": name, "path": "", "kind": "file"}}

    lay = gs.icon_layout(84, 91, "medium")
    host = QWidget()
    host.resize(900, 600)
    host.show()
    ic = AppIcon(entry("a", "A"), 0, layout=lay)
    ic.setParent(host)
    ic.move(100, 100)
    ic.show()
    QApplication.processEvents()

    ic._press_scale = 0.94          # 模拟拖动中的按下缩放
    ic._after_drag()
    samples = []
    for _ in range(14):
        QTest.qWait(25)
        samples.append(ic._press_scale)
    peak = max(samples) if samples else 0.0
    check("F5 松手后不放大（峰值不超过 1.0）", peak <= 1.005,
          "peak=%.3f samples=%s" % (peak, [round(s, 3) for s in samples[:8]]))
    tail = samples[-1] if samples else 0.0
    check("F5b 松手动画最终回到 1.0", abs(tail - 1.0) <= 0.01, "tail=%.3f" % tail)
    host.close()


# ---------------------------------------------------------------- F6
def test_extract_member(mode):
    """反馈 2：文件夹成员拖出后"消失"（真回归 bug）。

    逻辑层先复现全链路：pop 成员 -> add_app -> sync_structure 之后，
    storage 与 self.icons 都必须有这个条目，且它的位置要落在**可见区域**内
    （"消失了"最常见的真身就是条目在、但坐标跑到屏幕外/别的页）。
    """
    st = fresh_store()
    st._data["settings"]["layout_mode"] = mode
    st._data["settings"]["widgets"] = []
    st._data["layout"] = [{
        "id": "f1", "type": "folder", "app": {},
        "apps": [
            {"id": "app-1", "name": "A", "path": "", "kind": "file"},
            {"id": "app-2", "name": "B", "path": "", "kind": "file"},
        ],
        "col": 0, "row": 0,
    }]
    st.save()
    w = DesktopWindow(st)
    w.resize(W, H)
    w._update_grid_metrics()
    QApplication.processEvents()

    w._on_extract("M:f1:0")
    QApplication.processEvents()

    tag = "[%s] " % mode
    got = st.find_entry("app-1")
    check(tag + "F6 拖出后条目回到 layout", got is not None,
          "layout ids=%s" % [e.get("id") for e in st.layout])
    check(tag + "F6b 桌面出现该图标", "app-1" in w.icons,
          "icons=%s" % list(w.icons))
    icon = w.icons.get("app-1")
    if icon is not None:
        pos = icon.pos()
        vis = 0 <= pos.x() < W and 0 <= pos.y() < H
        check(tag + "F6c 图标位置在可见区域内", vis,
              "pos=(%d,%d) W,H=(%d,%d)" % (pos.x(), pos.y(), W, H))
        # isVisible() 受父窗口 show 状态影响（测试里 host 未 show），
        # 用 isHidden() 判定"是否被显式隐藏"才是 cull 的真信号
        check(tag + "F6d 图标未被显式隐藏（未被分页 cull）", not icon.isHidden(),
              "hidden=%s row=%s rpp=%s"
              % (icon.isHidden(), got.get("row") if got else None,
                 __import__("src.grid_flow", fromlist=["x"]).rows_per_page()))
    f1 = st.find_entry("f1")
    # 2026-10-21 反馈 10：拖出后只剩 1 个成员 → 文件夹自动解除。
    # 旧断言「文件夹还在、还剩 1 个」正是本轮要改掉的行为。
    check(tag + "F6e 只剩 1 个成员 → 文件夹自动解除", f1 is None,
          "f1=%s" % (f1,))
    check(tag + "F6f 最后一个成员也弹回桌面",
          st.find_entry("app-2") is not None,
          "layout ids=%s" % [e.get("id") for e in st.layout])
    w.close()
    QApplication.processEvents()


# ---------------------------------------------------------------- F7 / F8
def test_unique_ownership():
    """反馈 2 的**真根因**：同一元素同时在桌面与文件夹。

    `seed_desktop_clone()` / 增量同步只拿顶层条目做 diff，而虚拟文件夹
    成员在磁盘桌面上依然存在 -> 一次 rescan 就把它们重复播回桌面。
    重复之后从文件夹拖出成员 = add_app 命中同 id 直接返回 = "拖出来没了"。
    """
    from src import appsource

    st = fresh_store()
    # 造一个"已在文件夹里"的 app，其 target 同时出现在桌面扫描结果里
    probe = appsource.scan_desktop_files()
    if not probe:
        check("F7 本机桌面为空，跳过唯一性测试", True)
        return
    f0 = probe[0]
    st._data["layout"] = [
        {"id": "f1", "type": "folder", "app": {},
         "apps": [dict(f0)], "col": 0, "row": 0},
    ]
    st._data["settings"]["desktop_clone_seeded"] = True
    st.save()

    added = st.seed_desktop_clone()
    ids = [e.get("id") for e in st.layout]
    dup = [i for i in ids if i == f0["id"]]
    check("F7 rescan 不把文件夹成员播回桌面", not dup,
          "added=%d dup=%s" % (added, dup))
    check("F7b 成员仍在文件夹里且只有一份",
          len(st.find_entry("f1")["apps"]) == 1,
          "%d" % len(st.find_entry("f1")["apps"]))

    # F8 历史脏数据：构造"桌面 + 文件夹"重复 -> 加载时自动清洗
    if STATE.exists():
        STATE.unlink()
    st2 = Storage(path=str(STATE))
    st2._data["layout"] = [
        {"id": "f1", "type": "folder", "app": {},
         "apps": [{"id": "dup-1", "name": "重复", "target": "T"}],
         "col": 0, "row": 0},
        {"id": "dup-1", "type": "app",
         "app": {"id": "dup-1", "name": "重复", "target": "T"},
         "col": 2, "row": 0},
        {"id": "keep-1", "type": "app",
         "app": {"id": "keep-1", "name": "正常", "target": "K"},
         "col": 3, "row": 0},
    ]
    st2.save()

    st3 = Storage(path=str(STATE))       # 重新加载 = 走 _normalize_layout
    top_ids = [e.get("id") for e in st3.layout]
    check("F8 加载时清掉桌面上的重复条目", "dup-1" not in top_ids, "%s" % top_ids)
    check("F8b 正常条目不受影响", "keep-1" in top_ids, "%s" % top_ids)
    check("F8c 文件夹成员保留",
          "dup-1" in [m.get("id") for m in st3.find_entry("f1")["apps"]],
          "%s" % [m.get("id") for m in st3.find_entry("f1")["apps"]])


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    st = fresh_store()
    test_settings(st)
    test_context_menu(st)
    test_inline_input()
    test_release_bounce()
    test_extract_member("grid")
    test_extract_member("free")
    test_unique_ownership()

    print("-" * 56)
    if failures:
        print("FAILED %d: %s" % (len(failures), failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    crashed = None
    try:
        rc = main()
    except Exception:
        traceback.print_exc()
        rc = 1
        crashed = "exception"
    finally:
        if crashed or rc != 0:
            os._exit(rc if rc else 1)
    sys.exit(rc)
