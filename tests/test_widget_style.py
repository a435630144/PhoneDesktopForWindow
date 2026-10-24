# -*- coding: utf-8 -*-
"""组件外观样式层（v9 V1，指导文档_v9.md §2.2）。

契约：
  S1  通用外观字段 = 5 项，且都归入「外观」分组
  S2  每个内置组件的 settings 都包含全部外观字段（漏一个 = 那个组件不能改色）
  S3  空设置 -> 主题默认（文字用 theme.TEXT，圆角用 GLASS_RADIUS）
  S4  文字颜色派生：dim / faint 与主色同色相、alpha 依次降低
  S5  背景色：custom_bg=True，且 tint_alpha 随 bg_alpha 线性缩放
  S6  bg_alpha 夹紧到 0.2~1.0（0 会让文字完全看不见）
  S7  圆角：0 -> 主题值；越界（1 / 100）夹到 [2,40]
  S8  强调色覆盖后 progress_color 等强调元素跟着变
  S9  paint_glass(tint_color=) 真的换了底色（像素红分量显著上升）
  S10 GlassWidget.style 缓存：on_settings_changed 后能读到新值
  S11 css() 输出 QSS 可用的 rgba()
  S12 非法颜色字符串不抛异常，回退主题

运行：python tests/test_widget_style.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from src import theme
from src import widget_registry as reg
from src import widget_style as ws
from src.storage import Storage

STATE = Path(tempfile.gettempdir()) / "test_widget_style_state.json"

CHECKS = []
failures = []


def check(name, cond, extra=""):
    CHECKS.append(name)
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def canvas(w, h):
    img = QImage(int(w), int(h), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    return img


def main():
    app = QApplication.instance() or QApplication([])

    # ---------- S1 字段声明 ----------
    fields = ws.style_fields()
    keys = [f.key for f in fields]
    check("S1a 通用外观字段是 6 项（v14 反馈 10 加 bg_liquid）",
          len(fields) == 6, str(keys))
    check("S1b 全部归入「外观」组", all(f.group == "外观" for f in fields))
    check("S1c 液态在最前，其后 bg_color/alpha/text/accent/radius",
          keys == ["bg_liquid", "bg_color", "bg_alpha", "text_color",
                   "accent_color", "radius"], str(keys))

    # ---------- S2 每个组件都有 ----------
    missing = []
    for spec in reg.all_specs():
        have = {f.key for f in spec.settings}
        for k in ws.STYLE_KEYS if hasattr(ws, "STYLE_KEYS") else keys:
            if k not in have:
                missing.append((spec.type, k))
    check("S2 每个内置组件都带全套外观字段", not missing, str(missing[:4]))

    # ---------- S3 默认 ----------
    d = ws.resolve({})
    check("S3a 空设置 -> 主题主文字", QColor(d.text) == QColor(theme.TEXT),
          str(d.text.getRgb()))
    check("S3b 空设置 -> 圆角 = 主题", abs(d.radius - theme.GLASS_RADIUS) < 0.01,
          str(d.radius))
    check("S3c 空设置无自定义底色", d.custom_bg is False and d.bg is None)

    # ---------- S4 文字颜色派生 ----------
    st = ws.resolve({"text_color": "#FF8800"})
    check("S4a 主文字取配置色", st.text.name().upper() == "#FF8800", st.text.name())
    check("S4b 主文字不透明", st.text.alpha() == 255, str(st.text.alpha()))
    check("S4c dim 同色相且更透明",
          st.dim.name().upper() == "#FF8800" and 100 < st.dim.alpha() < 255,
          "%s a=%d" % (st.dim.name(), st.dim.alpha()))
    check("S4d faint 比 dim 更透明", st.faint.alpha() < st.dim.alpha(),
          "%d < %d" % (st.faint.alpha(), st.dim.alpha()))

    # ---------- S5 / S6 背景 ----------
    b = ws.resolve({"bg_color": "#102030", "bg_alpha": 0.5})
    check("S5a 自定义背景生效", b.custom_bg and b.bg.name().upper() == "#102030",
          b.bg.name() if b.bg else "None")
    check("S5b tint_alpha 随 bg_alpha 缩放",
          abs(b.tint_alpha - int(round(theme.GLASS_TINT_ALPHA * 0.5))) <= 1,
          "%d vs %d" % (b.tint_alpha, theme.GLASS_TINT_ALPHA))
    b2 = ws.resolve({"bg_alpha": 1.0})
    check("S5c alpha=1 时等于主题 tint", b2.tint_alpha == theme.GLASS_TINT_ALPHA,
          str(b2.tint_alpha))
    check("S6a bg_alpha 低于下限被夹到 0.2",
          abs(ws.resolve({"bg_alpha": 0.0}).alpha - ws.MIN_ALPHA) < 1e-6)
    check("S6b bg_alpha 高于上限被夹到 1.0",
          abs(ws.resolve({"bg_alpha": 5}).alpha - 1.0) < 1e-6)

    # ---------- S7 圆角 ----------
    check("S7a 0 -> 主题圆角",
          abs(ws.resolve({"radius": 0}).radius - theme.GLASS_RADIUS) < 0.01)
    check("S7b 12 -> 12", abs(ws.resolve({"radius": 12}).radius - 12) < 0.01)
    check("S7c 过大夹到 40", abs(ws.resolve({"radius": 100}).radius - 40) < 0.01)

    # ---------- S8 强调色 ----------
    a = ws.resolve({"accent_color": "#22CC55"})
    check("S8a 强调色取配置值", a.accent.name().upper() == "#22CC55", a.accent.name())
    from src.widgets import monitor as mon
    check("S8b 监控进度色跟随强调色",
          mon.progress_color(10.0, a).name().upper() == "#22CC55",
          mon.progress_color(10.0, a).name())
    check("S8c 默认强调色仍是主题蓝",
          mon.progress_color(10.0).name().upper() == QColor(theme.ACCENT).name().upper())

    # ---------- S9 paint_glass 真的换底色 ----------
    from src.widget_glass import paint_glass

    def center_pixel(tint_color):
        img = canvas(120, 120)
        p = QPainter(img)
        paint_glass(p, QRectF(0, 0, 120, 120), 20, tint_color=tint_color)
        p.end()
        return QColor(img.pixel(60, 60))

    plain = center_pixel(None)
    warm = center_pixel(QColor(180, 40, 40))
    check("S9a 自定义底色让红分量显著上升",
          warm.red() > plain.red() + 30, "%d vs %d" % (warm.red(), plain.red()))
    check("S9b 默认底色仍是冷色（蓝 >= 红）", plain.blue() >= plain.red(),
          "%d/%d" % (plain.blue(), plain.red()))

    # ---------- S10 缓存失效 ----------
    if STATE.exists():
        STATE.unlink()
    st_obj = Storage(STATE)
    st_obj.set_setting("show_widgets", True)
    entry = {"id": "clock_s1", "type": "clock", "anchored": False,
             "x": 40, "y": 40, "w": 240, "h": 240, "settings": {}}
    st_obj.widgets.append(entry)
    st_obj.save()
    from src.widgets import clock as ck
    w = ck.ClockWidget(st_obj, entry)
    first = w.style.text.name().upper()
    entry["settings"]["text_color"] = "#FF0000"
    cached = w.style.text.name().upper()
    w.on_settings_changed()
    after = w.style.text.name().upper()
    check("S10a 未通知时仍是缓存值", cached == first, "%s" % cached)
    check("S10b on_settings_changed 后读到新颜色", after == "#FF0000", after)

    # ---------- S11 css ----------
    css = ws.css(QColor(10, 20, 30), 0.5)
    check("S11 css 输出 rgba()", css.startswith("rgba(10,20,30,") and css.endswith(")"),
          css)

    # ---------- S12 非法颜色 ----------
    bad = ws.resolve({"text_color": "not-a-color", "bg_color": "???", "radius": "x"})
    check("S12a 非法文字色回退主题",
          QColor(bad.text) == QColor(theme.TEXT), bad.text.name())
    check("S12b 非法背景色视为未设置", bad.custom_bg is False)
    check("S12c 非法圆角回退主题",
          abs(bad.radius - theme.GLASS_RADIUS) < 0.01, str(bad.radius))

    # ---------- 收尾 ----------
    w.close()
    w.deleteLater()
    app.processEvents()
    st_obj.widgets.clear()
    try:
        st_obj.save()
    except Exception:
        pass
    if STATE.exists():
        STATE.unlink()

    print("\n%d checks, %d failures" % (len(CHECKS), len(failures)))
    if failures:
        print("FAILED:", ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
