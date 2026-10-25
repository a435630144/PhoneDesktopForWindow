# -*- coding: utf-8 -*-
"""v14 反馈 3 + 13：图标文字样式 sheet 与 cell 预览框样式。

  L1-L6  标签样式纯函数（默认 = 1:1 Windows 原生；脏值回退；夹紧）
  P1-P4  预览框样式纯函数（默认 = 现行红框；预设；夹紧）
  S1-S5  设置 ▸ 图标分页（控件齐全 / 写设置 / 触发重绘 / 重置）
  R1-R3  绘制接线（AppIcon 消费 label_style；_paint_highlight 消费 preview_style）

运行：python tests/test_label_style.py   （offscreen；判据看退出码）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src import theme  # noqa: E402
from src.storage import Storage  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def _st(name="state.json"):
    tmp = Path(tempfile.mkdtemp())
    return Storage(path=tmp / name)


def main(app):
    from src import label_style as ls

    # ---------- L1 默认 = Windows 原生 ----------
    st = _st()
    s = ls.label_style(st)
    check("L1 默认文字色 = 白（Windows 原生）",
          s.color.name() == "#f4f5f7", s.color.name())
    check("L1b 默认不加粗", s.bold is False)
    check("L1c 默认开阴影", s.shadow is True)
    check("L1d 默认阴影浓度 75%（与现行 190/255 同观感）",
          abs(s.shadow_alpha - 0.75) < 1e-9, str(s.shadow_alpha))
    check("L1e 默认阴影色 = 黑", s.shadow_color.name() == "#000000")
    check("L1f 默认不描边（Windows 没有文字描边）", s.outline is False)
    check("L1g 默认描边粗细 1", s.outline_width == 1)
    check("L1h 默认与 theme 常量一致（白字）",
          QColor(s.color) == QColor(theme.LABEL_COLOR))

    # ---------- L2 颜色读写 ----------
    st.set_setting("label_color", "#123456")
    check("L2 label_color 生效", ls.label_style(st).color.name() == "#123456")
    st.set_setting("label_color", "#ABCDEF")
    check("L2b 大写十六进制归一化小写",
          ls.label_style(st).color.name() == "#abcdef")

    # ---------- L3 脏值回退 ----------
    for bad in ("not-a-color", "", None, 12345, "#12", "#gggggg"):
        st.set_setting("label_color", bad)
        check("L3 脏值 %r 回退默认白" % (bad,),
              ls.label_style(st).color.name() == "#f4f5f7")

    # ---------- L4 加粗 ----------
    st.set_setting("label_bold", True)
    check("L4 label_bold 生效", ls.label_style(st).bold is True)
    st.set_setting("label_bold", "yes")     # 字符串不是布尔
    check("L4b 字符串脏值回退 False", ls.label_style(st).bold is False)
    st.set_setting("label_bold", 1)
    check("L4c 整数 1 视为 True", ls.label_style(st).bold is True)

    # ---------- L5 阴影开关 + 浓度夹紧 ----------
    st.set_setting("label_shadow", False)
    check("L5 label_shadow 可关", ls.label_style(st).shadow is False)
    st.set_setting("label_shadow", True)
    st.set_setting("label_shadow_alpha", 0)
    check("L5b 浓度下限 0", ls.label_style(st).shadow_alpha == 0.0)
    st.set_setting("label_shadow_alpha", 999)
    check("L5c 浓度上限 100%", ls.label_style(st).shadow_alpha == 1.0)
    st.set_setting("label_shadow_alpha", "oops")
    check("L5d 浓度脏值回退 75%",
          abs(ls.label_style(st).shadow_alpha - 0.75) < 1e-9)
    st.set_setting("label_shadow_color", "#334455")
    check("L5e 阴影色可改",
          ls.label_style(st).shadow_color.name() == "#334455")
    st.set_setting("label_shadow_color", "脏")
    check("L5f 阴影色脏值回退黑",
          ls.label_style(st).shadow_color.name() == "#000000")

    # ---------- L6 描边 ----------
    st.set_setting("label_outline", True)
    st.set_setting("label_outline_color", "#ff0000")
    st.set_setting("label_outline_width", 3)
    s6 = ls.label_style(st)
    check("L6 描边三件套生效",
          s6.outline is True and s6.outline_color.name() == "#ff0000"
          and s6.outline_width == 3)
    st.set_setting("label_outline_width", 99)
    check("L6b 描边粗细上夹 3", ls.label_style(st).outline_width == 3)
    st.set_setting("label_outline_width", 0)
    check("L6c 描边粗细下夹 1", ls.label_style(st).outline_width == 1)
    st.set_setting("label_outline_width", "x")
    check("L6d 描边粗细脏值回退 1", ls.label_style(st).outline_width == 1)

    # ---------- P1 预览框默认 = 现行红框 ----------
    st = _st()
    p = ls.preview_style(st)
    check("P1 默认色 = theme.DANGER（现行红框）",
          p.color.name() == QColor(theme.DANGER).name(), p.color.name())
    check("P1b 默认填充 8%", abs(p.fill_alpha - 0.08) < 1e-9, str(p.fill_alpha))
    check("P1c 默认粗细 2px", p.width == 2)
    check("P1d 默认圆角 8", p.radius == 8)

    # ---------- P2 颜色 ----------
    st.set_setting("cell_preview_color", "#00ff00")
    check("P2 cell_preview_color 生效",
          ls.preview_style(st).color.name() == "#00ff00")
    st.set_setting("cell_preview_color", "")
    check("P2b 空值 = 跟随主题 DANGER",
          ls.preview_style(st).color.name() == QColor(theme.DANGER).name())
    st.set_setting("cell_preview_color", "脏")
    check("P2c 脏值 = 跟随主题",
          ls.preview_style(st).color.name() == QColor(theme.DANGER).name())

    # ---------- P3 夹紧 ----------
    st.set_setting("cell_preview_fill", 999)
    check("P3 填充上限 40%", ls.preview_style(st).fill_alpha == 0.40)
    st.set_setting("cell_preview_fill", -5)
    check("P3b 填充下限 0", ls.preview_style(st).fill_alpha == 0.0)
    st.set_setting("cell_preview_fill", "x")
    check("P3c 填充脏值回退 8%",
          abs(ls.preview_style(st).fill_alpha - 0.08) < 1e-9)
    st.set_setting("cell_preview_width", 99)
    check("P3d 粗细上夹 4", ls.preview_style(st).width == 4)
    st.set_setting("cell_preview_width", 0)
    check("P3e 粗细下夹 1", ls.preview_style(st).width == 1)
    st.set_setting("cell_preview_radius", 99)
    check("P3f 圆角上限 16", ls.preview_style(st).radius == 16)
    st.set_setting("cell_preview_radius", -1)
    check("P3g 圆角下限 0", ls.preview_style(st).radius == 0)
    st.set_setting("cell_preview_radius", "x")
    check("P3h 圆角脏值回退 8", ls.preview_style(st).radius == 8)

    # ---------- P4 预设 ----------
    presets = {k: c for k, _l, c in ls.PREVIEW_PRESETS}
    check("P4 预设表四项（红/蓝/绿/橙）",
          set(presets) == {"danger", "accent", "success", "warn"}
          and all(c.isValid() for c in presets.values()),
          str(sorted(presets)))
    for name in presets:
        st2 = _st()
        ls.apply_preview_preset(st2, name)
        pv = ls.preview_style(st2)
        check("P4 预设 %s 写色 %s" % (name, pv.color.name()),
              pv.color.name() == presets[name].name(),
              "got %s want %s" % (pv.color.name(), presets[name].name()))
    st3d = _st()
    ls.apply_preview_preset(st3d, "不存在的预设")
    check("P4e 非法预设不动设置",
          ls.preview_style(st3d).color.name() == QColor(theme.DANGER).name())

    # ---------- S1-S5 设置分页 ----------
    from src import settings_dialog
    from types import SimpleNamespace

    class FakeWin:
        def __init__(self):
            self.calls = 0

        def apply_label_style(self):
            self.calls += 1

    fw = FakeWin()
    st_s = _st("settings.json")
    try:
        dlg = settings_dialog.SettingsDialog(fw, st_s, None, page="icons")
    except Exception as exc:      # noqa: BLE001
        check("S1 设置窗可构造", False, repr(exc))
        dlg = None
    if dlg is not None:
        check("S1 「图标」分页存在", "icons" in dlg._pages,
              str(list(dlg._pages)))
        names = ("btn_label_color", "chk_label_bold", "chk_label_shadow",
                 "sld_label_shadow", "chk_label_outline", "sp_label_outline_w",
                 "btn_label_outline_color", "cmb_cell_preview",
                 "btn_cell_preview_color", "sld_cell_preview_fill",
                 "sp_cell_preview_width", "sp_cell_preview_radius")
        missing = [n for n in names if not hasattr(dlg, n)]
        check("S2 图标分页控件齐全", not missing, str(missing))
        before = fw.calls
        # 直接走应用路径（颜色对话框需要交互，绕不过——用设置值 + 应用钩子验证）
        dlg._label_color = "#ff0000"
        dlg._apply_label_color()
        check("S3 文字色写设置", st_s.get_setting("label_color") == "#ff0000",
              str(st_s.get_setting("label_color")))
        check("S3b 改样式触发窗口重绘（apply_label_style）", fw.calls > before,
              "calls=%d" % fw.calls)
        dlg.chk_label_bold.setChecked(True)
        check("S3c 加粗写设置", st_s.get_setting("label_bold") is True)
        dlg.sld_label_shadow.setValue(30)
        check("S3d 阴影浓度写设置",
              st_s.get_setting("label_shadow_alpha") == 30,
              str(st_s.get_setting("label_shadow_alpha")))
        dlg.chk_label_outline.setChecked(True)
        dlg.sp_label_outline_w.setValue(2)
        check("S3e 描边写设置",
              st_s.get_setting("label_outline") is True
              and st_s.get_setting("label_outline_width") == 2)
        idx = dlg.cmb_cell_preview.findData("accent")
        dlg.cmb_cell_preview.setCurrentIndex(idx)
        check("S4 预览框预设写色 = ACCENT",
              st_s.get_setting("cell_preview_color")
              == QColor(theme.ACCENT).name(),
              str(st_s.get_setting("cell_preview_color")))
        dlg.sld_cell_preview_fill.setValue(20)
        dlg.sp_cell_preview_width.setValue(3)
        dlg.sp_cell_preview_radius.setValue(12)
        check("S4b 预览框三旋钮写设置",
              st_s.get_setting("cell_preview_fill") == 20
              and st_s.get_setting("cell_preview_width") == 3
              and st_s.get_setting("cell_preview_radius") == 12)
        dlg._reset_label_page()
        check("S5 恢复默认：白字 + 阴影 75% + 不描边 + 红框 8%/2/8",
              st_s.get_setting("label_color") == "#f4f5f7"
              and st_s.get_setting("label_bold") is False
              and st_s.get_setting("label_shadow") is True
              and st_s.get_setting("label_shadow_alpha") == 75
              and st_s.get_setting("label_outline") is False
              and st_s.get_setting("cell_preview_color") == ""
              and st_s.get_setting("cell_preview_fill") == 8
              and st_s.get_setting("cell_preview_width") == 2
              and st_s.get_setting("cell_preview_radius") == 8,
              json_dump(st_s))
        dlg.deleteLater()

    # ---------- R1 AppIcon 消费 label_style ----------
    from src.iconwidget import AppIcon
    red = ls.LabelStyle(color=QColor("#ff0000"), bold=False, shadow=False,
                        shadow_color=QColor("#000000"), shadow_alpha=0.0,
                        outline=False, outline_color=QColor("#000000"),
                        outline_width=1)
    ic = AppIcon({"id": "r1", "app": {"name": "测试图标", "target": "x"}}, 48)
    ic.set_label_style(red)
    ic.resize(ic.size())
    img = QImage(ic.size(), QImage.Format_ARGB32)
    img.fill(0)
    ic.render(img)
    n_red = 0
    for yy in range(img.height()):
        for xx in range(img.width()):
            c = img.pixelColor(xx, yy)
            if c.red() > 150 and c.red() - c.green() > 60 and c.red() - c.blue() > 60:
                n_red += 1
    check("R1 红色文字样式画出红字", n_red > 20, "red=%d" % n_red)
    ic.close()

    # ---------- R2 DesktopWindow.apply_label_style 热更新 ----------
    from src.desktop_window import DesktopWindow
    st_r = _st("r2.json")
    st_r._data["settings"]["hide_native_icons"] = True
    st_r._data["settings"]["auto_clone_desktop"] = False
    st_r._data["layout"] = [{"id": "r2a", "col": 0, "row": 0, "page": 0,
                             "app": {"name": "甲", "target": "C:/a.exe"}}]
    st_r.save()
    w = DesktopWindow(st_r)
    w.resize(1920, 1080)
    w.rebuild()
    blue = ls.LabelStyle(color=QColor("#0000ff"), bold=False, shadow=False,
                         shadow_color=QColor("#000000"), shadow_alpha=0.0,
                         outline=False, outline_color=QColor("#000000"),
                         outline_width=1)
    st_r.set_setting("label_color", "#0000ff")
    w.apply_label_style()
    got = list(w.icons.values())[0]._label_style
    check("R2 apply_label_style 把新样式推进每个图标",
          got.color.name() == "#0000ff", got.color.name())
    w.close()

    # ---------- R3 _paint_highlight 消费 preview_style ----------
    st_r3 = _st("r3.json")
    st_r3._data["settings"]["hide_native_icons"] = True
    st_r3._data["settings"]["auto_clone_desktop"] = False
    st_r3._data["layout"] = []
    st_r3.save()
    w3 = DesktopWindow(st_r3)
    w3.resize(1920, 1080)
    w3._update_grid_metrics()
    st_r3.set_setting("cell_preview_color", "#00ff00")
    st_r3.set_setting("cell_preview_width", 3)
    w3._hl_cell = (0, 0)
    w3._hl_span = (1, 1)
    w3._paint_highlight(None)
    img3 = QImage(w3.size(), QImage.Format_ARGB32)
    img3.fill(0)
    w3.render(img3)
    n_green = 0
    for yy in range(img3.height()):
        for xx in range(img3.width()):
            c = img3.pixelColor(xx, yy)
            if c.green() > 150 and c.green() - c.red() > 60 \
                    and c.green() - c.blue() > 60:
                n_green += 1
    check("R3 预览框按设置画成绿色", n_green > 100, "green=%d" % n_green)
    w3.close()

    # ---------- FS：图标字号缩放（v14q） ----------
    st_fs = _st("fs.json")
    check("FS 默认 100%", st_fs.get_setting("label_font_scale") == 100)
    stys = ls.label_style(st_fs)
    check("FSb 默认 font_scale=1.0", abs(stys.font_scale - 1.0) < 1e-6)
    st_fs.set_setting("label_font_scale", 130)
    check("FSc 130% -> 1.3",
          abs(ls.label_style(st_fs).font_scale - 1.3) < 1e-6)
    st_fs.set_setting("label_font_scale", 500)
    check("FSd 越界夹 140",
          abs(ls.label_style(st_fs).font_scale - 1.4) < 1e-6)

    # 图标侧：字号随样式热更新（基准 × 缩放）
    from src.iconwidget import AppIcon
    ic = AppIcon({"id": "fs1", "name": "字",
                  "app": {"kind": "app", "name": "字", "target": "T"},
                  "col": 0, "row": 0, "page": 0}, 64)
    base_px = ic._label_font_base
    px0 = ic._label_font_px
    check("FSe 100% 时字号=基准", px0 == base_px,
          "%d/%d" % (px0, base_px))
    st_fs.set_setting("label_font_scale", 130)
    ic.set_label_style(ls.label_style(st_fs))
    check("FSf 130% 热更新字号=基准×1.3",
          ic._label_font_px == max(9, min(40, int(round(base_px * 1.3)))),
          "base=%d now=%d" % (base_px, ic._label_font_px))

    # 设置页滑条写键 + 重置回 100
    from src.settings_dialog import SettingsDialog as _SD2
    st_dlg = _st("fs_dlg.json")
    dlg_fs = _SD2(type("W2", (), {"attached": False})(), st_dlg, None)
    check("FSg 设置页有字号滑条", hasattr(dlg_fs, "sld_label_font"))
    dlg_fs.sld_label_font.setValue(125)
    check("FSH 滑条写键", st_dlg.get_setting("label_font_scale") == 125,
          str(st_dlg.get_setting("label_font_scale")))
    dlg_fs._reset_label_text()
    check("FSi 重置回 100",
          st_dlg.get_setting("label_font_scale")
          == ls.DEFAULT_FONT_SCALE)
    dlg_fs.deleteLater()

    print("---------------------------------------------------------")
    if failures:
        print("FAILED %d: %s" % (len(failures), failures))
        return 1
    print("ALL label_style PASSED")
    return 0


def json_dump(st):
    import json
    return json.dumps({k: st.get_setting(k) for k in (
        "label_color", "label_bold", "label_shadow", "label_shadow_alpha",
        "label_outline", "cell_preview_color", "cell_preview_fill",
        "cell_preview_width", "cell_preview_radius")}, ensure_ascii=False)


if __name__ == "__main__":
    _app = QApplication.instance() or QApplication([])
    sys.exit(main(_app))
