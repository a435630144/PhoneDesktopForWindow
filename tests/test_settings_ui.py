# -*- coding: utf-8 -*-
"""设置界面（2026-10-19 反馈 10：整页重构为 AirDeck 式苹果风）。

旧版的病：一列四张卡片、文字全用纯白硬堆层级、QSS 散落在每个控件上，
深色底上失去可读性层次。重构为「左侧导航 + 右栏分页 + 行式卡片」，
文字颜色与字号统一由 theme.SETTINGS_QSS 提供。

  U1 左侧栏 8 个导航项（通用 / 组件库 / Dock 栏 / 外观与图标 / 布局与网格 /
     模式 / 开发者 / 高级）——v11 反馈 32 新增「开发者」分页
  U2 点导航切页：stack 索引跟着变，页标题跟着变
  U3 卡片走 objectName=settingsCard，样式统一来自 SETTINGS_QSS
  U4 行标题 rowTitle / 说明 rowHint 两级文字，颜色在 SETTINGS_QSS 里定义
  U5 旧控件属性名一个不少（chk_dev / cmb_tier / sld_icon_fit …）；
     spin_icon_px 例外——v7 B2 尺寸单源后**已退役**（反馈 9/11）
  U6 「模式」页承载接管 / 自动克隆 / 自动同步 / 布局下拉
  U7 布局模式下拉初值来自 storage，切换调 window.set_layout_mode
  U8 内容可滚动（无横向滚动条）
  U9 下拉框 / 数字框走 SETTINGS_QSS 的深色规则（不写就黑底黑字）
  U10 翻页方向下拉默认左右（反馈 5），可切上下
  U11 「自动排列图标」开关落在 storage.auto_arrange（反馈 3）
  U12 高度不超过可用屏幕 92%

运行：python tests/test_settings_ui.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QLabel,
                               QPushButton, QScrollArea, QSlider, QSpinBox,
                               QStackedWidget, QWidget, QApplication)

from src import grid_flow, theme
from src.settings_dialog import SettingsDialog
from src.storage import Storage

TMP = Path(tempfile.gettempdir()) / "test_settings_ui.json"
failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


class FakeWindow:
    def __init__(self, storage):
        self.storage = storage
        self.attached = True
        self.mode_calls = []
        self.rebuilt = 0
        self.refreshed = 0

    def rebuild(self, stagger=False):
        self.rebuilt += 1

    def refresh_grid(self):
        self.refreshed += 1

    def apply_native_icons_setting(self):
        pass

    def set_layout_mode(self, mode):
        self.mode_calls.append(mode)
        self.storage.set_setting("layout_mode", mode)


def fresh(mode="grid"):
    if TMP.exists():
        TMP.unlink()
    st = Storage(path=str(TMP))
    st._data["settings"]["auto_clone_desktop"] = False
    st._data["settings"]["layout_mode"] = mode
    st.save()
    return st


def page_widgets(dlg, index):
    """第 index 页里的全部子控件（用于判断某个控件属于哪一页）。"""
    page = dlg.stack.widget(index)
    return {id(w) for w in page.findChildren(QWidget)} if page else set()


def main(app):
    st = fresh("grid")
    fw = FakeWindow(st)
    dlg = SettingsDialog(fw, st, layer=None)

    # ---------- U1 侧栏导航 ----------
    navs = [b for b in dlg.findChildren(QPushButton)
            if b.objectName() == "navItem"]
    # v14t：新增「文件夹」页（10 项）
    check("U1 左侧栏 10 个导航项", len(navs) == 10, "found=%d" % len(navs))
    check("U1 导航文案依次为 通用/组件库/Dock 栏/图标与标签/液态玻璃/布局与网格/文件夹/模式/高级/关于",
          [b.text() for b in navs]
          == ["通用", "组件库", "Dock 栏", "图标与标签", "液态玻璃",
              "布局与网格", "文件夹", "模式", "高级", "关于"],
          "%s" % [b.text() for b in navs])
    stack = dlg.findChildren(QStackedWidget)
    check("U1 右栏是 10 页堆栈", len(stack) == 1 and stack[0].count() == 10,
          "stacks=%d" % len(stack))

    # ---------- U2 切页 ----------
    navs[7].click()       # v14t：「文件夹」插入后「模式」是第 7 项（0 起算）
    app.processEvents()
    check("U2 点「模式」→ stack 第 7 页（v14t：文件夹页插在前面）",
          dlg.stack.currentIndex() == 7, "idx=%d" % dlg.stack.currentIndex())
    check("U2 页标题跟着变", dlg.lbl_page_title.text() == "模式",
          "%s" % dlg.lbl_page_title.text())
    check("U2 只有当前导航高亮",
          [b.isChecked() for b in navs]
          == [False, False, False, False, False, False, False, True, False, False],
          "%s" % [b.isChecked() for b in navs])
    navs[0].click()
    app.processEvents()

    # ---------- U3 卡片样式统一 ----------
    cards = [c for c in dlg.findChildren(QFrame)
             if c.objectName() == "settingsCard"]
    check("U3 有分组卡片（行式）", len(cards) >= 5, "found=%d" % len(cards))
    check("U3 卡片不再各自写内联 QSS（样式统一来自 SETTINGS_QSS）",
          all(c.styleSheet() == "" for c in cards),
          "inline=%d" % sum(1 for c in cards if c.styleSheet()))
    check("U3 SETTINGS_QSS 定义卡片底与圆角",
          "QFrame#settingsCard" in theme.SETTINGS_QSS
          and "border-radius: 12px" in theme.SETTINGS_QSS)
    check("U3 对话框只挂一份 SETTINGS_QSS",
          dlg.styleSheet() == theme.settings_qss())
    check("U3 下拉箭头走真图（QSS 三角 hack 会渲染成实心块）",
          "QComboBox::down-arrow{image: url(" in theme.settings_qss(),
          "%s" % theme.settings_qss()[-90:])

    # ---------- U4 两级文字 ----------
    titles = [c for c in dlg.findChildren(QLabel)
              if c.objectName() == "rowTitle"]
    hints = [c for c in dlg.findChildren(QLabel)
             if c.objectName() == "rowHint"]
    check("U4 行标题存在", len(titles) >= 8, "found=%d" % len(titles))
    check("U4 每行都有说明文字", len(hints) >= len(titles),
          "titles=%d hints=%d" % (len(titles), len(hints)))
    check("U4 文字颜色由 QSS 提供（不靠内联纯白）",
          all(lb.styleSheet() == "" for lb in titles)
          and "QLabel#rowTitle" in theme.SETTINGS_QSS
          and "QLabel#rowHint" in theme.SETTINGS_QSS)
    check("U4 标题与说明是不同层级（字号/颜色不同）",
          "font-size: 13px" in theme.SETTINGS_QSS
          and "font-size: 11px" in theme.SETTINGS_QSS)

    # ---------- U5 旧控件兼容 ----------
    need = ["chk_autostart", "cmb_tier", "sld_icon_fit", "chk_auto_cols",
            "spin_cols", "chk_autoclone",
            "chk_widgets", "chk_tray", "chk_dev", "btn_rescan", "btn_reset",
            "btn_close", "cmb_layout_mode", "chk_autosync",
            "cmb_page_axis", "chk_auto_arrange", "chk_match_native"]
    missing = [n for n in need if not hasattr(dlg, n)]
    check("U5 旧控件属性名全部保留", not missing, "missing=%s" % missing)
    check("U5 控件类型未被改坏",
          isinstance(dlg.chk_dev, QCheckBox) and isinstance(dlg.cmb_tier, QComboBox)
          and isinstance(dlg.sld_icon_fit, QSlider))
    # v7 B2（反馈 9）：无界像素 spin 退役——尺寸单源后它只会造成
    # "切模式图标突变"，留着就是第二个真相源
    check("U5b 无界像素 spin 已退役", not hasattr(dlg, "spin_icon_px"))

    # ---------- U6 模式页归属 ----------
    # v14t：「文件夹」页插在布局之后，模式页顺延到 7
    inside = page_widgets(dlg, 7)
    # 反馈 16：接管开关下线（启动即接管），模式页只留布局与同步项
    check("U6 接管开关已下线", not hasattr(dlg, "chk_takeover"))
    check("U6 自动克隆在「模式」页", id(dlg.chk_autoclone) in inside)
    check("U6 自动同步在「模式」页", id(dlg.chk_autosync) in inside)
    check("U6 布局模式下拉在「模式」页", id(dlg.cmb_layout_mode) in inside)

    # ---------- U8 v14i：悬浮不透明度滑条退役（兜底固定 0.95） ----------
    # 悬浮形态只是挂载失败的兜底，正常机器见不到；固定值见
    # desktop_window.FLOATING_OPACITY（判据在 test_opacity_setting.py）。
    check("U8 悬浮不透明度滑条已退役", not hasattr(dlg, "sld_opacity"))

    # ---------- U7 布局模式下拉 ----------
    check("U7 下拉初值 = storage 的 grid",
          dlg.cmb_layout_mode.currentData() == "grid")
    dlg.cmb_layout_mode.setCurrentIndex(dlg.cmb_layout_mode.findData("free"))
    app.processEvents()
    check("U7 切无界 → 调 window.set_layout_mode('free')",
          fw.mode_calls == ["free"], "calls=%s" % fw.mode_calls)
    check("U7 storage 落盘 layout_mode=free",
          st.get_setting("layout_mode") == "free")
    dlg.cmb_layout_mode.setCurrentIndex(dlg.cmb_layout_mode.findData("grid"))
    app.processEvents()

    # ---------- U8 可滚动 ----------
    scrolls = dlg.findChildren(QScrollArea)
    # 8 页里「组件库」自带滚动区（不能套第二层），所以外层是 7 个
    # v14t：10 页 - 组件库（自带滚动区不外裹） = 9 个 QScrollArea
    check("U8 每页内容装在可滚动区域", len(scrolls) == 9, "found=%d" % len(scrolls))
    check("U8 滚动区无横向滚动条（宽度自适应）",
          all(s.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
              for s in scrolls))

    # ---------- U9 深色输入控件 ----------
    check("U9 QSS 定义 QComboBox 深色表面",
          "QComboBox{" in theme.SETTINGS_QSS
          and "QComboBox QAbstractItemView" in theme.SETTINGS_QSS
          and "QComboBox::down-arrow" in theme.SETTINGS_QSS)
    check("U9 QSS 定义 QSpinBox 深色表面",
          "QSpinBox{" in theme.SETTINGS_QSS)
    check("U9 QSS 定义主按钮",
          "QPushButton#primaryButton" in theme.SETTINGS_QSS)

    # ---------- U10 翻页方向（反馈 5） ----------
    check("U10 翻页方向默认左右",
          dlg.cmb_page_axis.currentData() == "horizontal",
          "%s" % dlg.cmb_page_axis.currentData())
    dlg.cmb_page_axis.setCurrentIndex(
        dlg.cmb_page_axis.findData("vertical"))
    app.processEvents()
    check("U10 切上下写进 storage",
          st.get_setting("page_axis") == "vertical",
          "%s" % st.get_setting("page_axis"))
    dlg.cmb_page_axis.setCurrentIndex(
        dlg.cmb_page_axis.findData("horizontal"))
    app.processEvents()
    check("U10 切回左右写进 storage",
          st.get_setting("page_axis") == "horizontal",
          "%s" % st.get_setting("page_axis"))

    # ---------- U11 自动排列（反馈 3） ----------
    before = bool(st.get_setting("auto_arrange"))
    dlg.chk_auto_arrange.setChecked(not before)
    app.processEvents()
    check("U11 自动排列开关落 storage.auto_arrange",
          bool(st.get_setting("auto_arrange")) is (not before),
          "%s" % st.get_setting("auto_arrange"))
    dlg.chk_auto_arrange.setChecked(before)
    app.processEvents()

    # ---------- U13 网格卡片：双 spin + 联动（v7 §3.2） ----------
    check("U13 网格卡片有列数/行数两个 spin",
          dlg.spin_cols is not None and dlg.spin_rows is not None)
    m = grid_flow.current_metrics()
    if m is not None:
        cols_auto = m.cols_auto or m.cols
        rows_auto = m.rows_auto or m.rows
        check("U13 列数上限 = cols_auto",
              dlg.spin_cols.maximum() == cols_auto,
              "%s vs %s" % (dlg.spin_cols.maximum(), cols_auto))
        check("U13 行数上限 = rows_auto",
              dlg.spin_rows.maximum() == rows_auto,
              "%s vs %s" % (dlg.spin_rows.maximum(), rows_auto))
        check("U13 摘要含行列与格子尺寸",
              "列" in dlg.lbl_grid_dims.text()
              and "格子" in dlg.lbl_grid_dims.text(),
              dlg.lbl_grid_dims.text())
    check("U13 自动模式下两个 spin 禁用",
          dlg.chk_auto_cols.isChecked()
          and not dlg.spin_cols.isEnabled()
          and not dlg.spin_rows.isEnabled(),
          "auto=%s cols_en=%s" % (dlg.chk_auto_cols.isChecked(),
                                  dlg.spin_cols.isEnabled()))
    # 关自动 -> manual 落盘；行数只裁剪不改格子
    dlg.chk_auto_cols.setChecked(False)
    app.processEvents()
    check("U13 关自动写 grid_dims_mode=manual",
          st.grid_dims_mode() == "manual", st.grid_dims_mode())
    if m is not None and dlg.spin_rows.minimum() < dlg.spin_rows.maximum():
        cell_before = (m.cw, m.ch)
        dlg.spin_rows.setValue(dlg.spin_rows.maximum() - 1)
        app.processEvents()
        after = grid_flow.current_metrics()
        if after is not None:
            check("U13 改行数不改格子尺寸",
                  (after.cw, after.ch) == cell_before,
                  "%s vs %s" % ((after.cw, after.ch), cell_before))
    dlg.chk_auto_cols.setChecked(True)
    app.processEvents()
    check("U13 勾回自动写 grid_dims_mode=auto",
          st.grid_dims_mode() == "auto", st.grid_dims_mode())

    # ---------- U14 网格原点（v7 §3.2 / 反馈 21） ----------
    check("U14 原点控件齐全（开关 + X/Y + 重置）",
          all(getattr(dlg, n, None) is not None
              for n in ("chk_origin", "sp_ox", "sp_oy", "btn_reset_origin")),
          "%s" % [n for n in ("chk_origin", "sp_ox", "sp_oy",
                              "btn_reset_origin")
                  if getattr(dlg, n, None) is None])
    if getattr(dlg, "chk_origin", None) is not None:
        # 2026-10-20 反馈 5：原点是**开发者功能**，dev_mode 关时整组只读
        dlg.chk_dev.setChecked(False)
        app.processEvents()
        check("U14b 开发者关时原点整组禁用",
              not dlg.chk_origin.isEnabled()
              and not dlg.btn_reset_origin.isEnabled(),
              "chk_en=%s btn_en=%s" % (dlg.chk_origin.isEnabled(),
                                       dlg.btn_reset_origin.isEnabled()))
        dlg.chk_dev.setChecked(True)      # 以下断言在开发者模式打开下进行
        app.processEvents()
        check("U14 自动模式下原点开关关闭、spin 与按钮禁用",
              (not dlg.chk_origin.isChecked())
              and not dlg.sp_ox.isEnabled()
              and not dlg.sp_oy.isEnabled()
              and not dlg.btn_reset_origin.isEnabled(),
              "chk=%s x_en=%s btn_en=%s" % (dlg.chk_origin.isChecked(),
                                            dlg.sp_ox.isEnabled(),
                                            dlg.btn_reset_origin.isEnabled()))
        # 开 -> manual 两键落盘，spin 解禁
        dlg.chk_origin.setChecked(True)
        app.processEvents()
        check("U14 开手动定位写 grid_origin_mode=manual",
              st.grid_origin_mode() == "manual", st.grid_origin_mode())
        check("U14 开后 spin / 重置按钮解禁",
              dlg.sp_ox.isEnabled() and dlg.btn_reset_origin.isEnabled(),
              "x_en=%s btn_en=%s" % (dlg.sp_ox.isEnabled(),
                                     dlg.btn_reset_origin.isEnabled()))
        check("U14 开后两键同时写入",
              isinstance(st.get_setting("grid_origin"), list)
              and len(st.get_setting("grid_origin")) == 2,
              "%r" % (st.get_setting("grid_origin"),))
        # 改 X -> storage 立即跟随
        before_y = dlg.sp_oy.value()
        dlg.sp_ox.setValue(100)
        app.processEvents()
        check("U14 改 X 立刻落盘",
              st.grid_origin() == (100, before_y),
              "%r vs (100,%d)" % (st.grid_origin(), before_y))
        # spin 显示的是**生效值**：有快照时 = 夹紧后的 ox/oy；无快照（本测试的
        # FakeWindow 不算几何）时退回 storage 的存储值，不能显示无关常量
        expect = st.grid_origin() or grid_flow.origin()
        check("U14 spin 显示生效原点（无快照退 storage）",
              (dlg.sp_ox.value(), dlg.sp_oy.value()) == expect,
              "%s vs %s" % ((dlg.sp_ox.value(), dlg.sp_oy.value()), expect))
        # 重置 -> 两键清空 + 回居中显示
        dlg.btn_reset_origin.click()
        app.processEvents()
        check("U14 重置后 mode=auto", st.grid_origin_mode() == "auto",
              st.grid_origin_mode())
        check("U14 重置后 origin is None", st.grid_origin() is None,
              "%r" % (st.grid_origin(),))
        check("U14 重置后开关回自动态、按钮禁用",
              (not dlg.chk_origin.isChecked())
              and not dlg.btn_reset_origin.isEnabled(),
              "chk=%s btn_en=%s" % (dlg.chk_origin.isChecked(),
                                    dlg.btn_reset_origin.isEnabled()))
        check("U14 重置后 spin 回居中值",
              (dlg.sp_ox.value(), dlg.sp_oy.value()) == grid_flow.origin(),
              "%s vs %s" % ((dlg.sp_ox.value(), dlg.sp_oy.value()),
                            grid_flow.origin()))

    # ---------- U15 分页排序规则（v9 V2） ----------
    from src import page_rules as pr
    dlg.select_page("layout")
    app.processEvents()
    check("U15a 布局页有「每页单独设置」开关", hasattr(dlg, "chk_page_rules"))
    check("U15b 默认关闭（跟随全局）", dlg.chk_page_rules.isChecked() is False)
    total = max(1, grid_flow.page_count(st))
    check("U15c 每页一行下拉（页数与网格一致）",
          len(dlg._rule_combos) == total, "%d vs %d" % (len(dlg._rule_combos), total))
    check("U15d 未启用时下拉置灰",
          all(not c.isEnabled() for c in dlg._rule_combos.values()))
    dlg.chk_page_rules.setChecked(True)
    app.processEvents()
    check("U15e 打开后写入 storage",
          st.get_setting("page_rules_custom") is True)
    check("U15f 下拉变成可点",
          all(c.isEnabled() for c in dlg._rule_combos.values()))
    combo = dlg._rule_combos[0]
    combo.setCurrentIndex(combo.findData(pr.FREE))
    app.processEvents()
    check("U15g 选「自由摆放」落到 page_rules",
          pr.rule_of(st, 0) == pr.FREE, str(st.get_setting("page_rules")))
    check("U15h mode_for 对该页返回 False", pr.mode_for(st, 0) is False)
    combo = dlg._rule_combos[0]
    combo.setCurrentIndex(combo.findData(pr.INHERIT))
    app.processEvents()
    check("U15i 回到跟随全局会删键",
          "0" not in (st.get_setting("page_rules") or {}), str(st.get_setting("page_rules")))
    dlg.chk_page_rules.setChecked(False)
    app.processEvents()

    # ---------- U12 高度受控 ----------
    dlg.show()
    app.processEvents()
    avail = QGuiApplication.primaryScreen().availableGeometry().height()
    check("U12 高度不超过可用屏幕 92%",
          dlg.height() <= int(avail * 0.92) + 2,
          "h=%d avail=%d" % (dlg.height(), avail))
    dlg.hide()
    dlg.deleteLater()

    print("-" * 56)
    if failures:
        print("FAILED %d: %s" % (len(failures), failures))
        return 1
    print("ALL settings_ui PASSED")
    return 0


def run_main():
    app = QApplication([])
    try:
        code = main(app)
    except Exception:
        import traceback
        traceback.print_exc()
        failures.append("main() 异常")
        code = 1
    try:
        TMP.unlink()
    except Exception:
        pass
    return code


if __name__ == "__main__":
    sys.exit(run_main())
