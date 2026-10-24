# -*- coding: utf-8 -*-
"""v14 反馈 9（重做）：液态玻璃 = 透明背景 + 边缘折射。

语义（用户原话）："算作一种元素的背景色，背景色是透明的，在边缘处会模拟
底层壁纸的图象被液体表面折射产生一定的扭曲效果"。参考 GlassDesk 1.4.0
的 `TransitionGlass.hlsl`：只有边缘 band 内的底图被沿法线向内采样，
中部完全不动。

  L1-L4  参数纯函数（默认 / 表面开关 / 总开关 / 夹紧）
  L5-L9  折射几何：不抛 / 中部逐像素不变 / 变化只在带内 / 方向 = 向内放大 /
         纯色图不变（= 没有高光、没有色散）
  S1-S4  设置 ▸ 外观（强度 + 带宽 + 三表面）；布局页的悬停开关
  R1-R5  三表面接线（有底图才走折射；关闭时不走）

运行：python tests/test_liquid_glass.py   （offscreen；判据看退出码）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.storage import Storage  # noqa: E402

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def _st(name="state.json"):
    tmp = Path(tempfile.mkdtemp())
    return Storage(path=tmp / name)


def _pixmap(w, h, paint):
    img = QImage(int(w), int(h), QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0))
    p = QPainter(img)
    paint(p, w, h)
    p.end()
    return QPixmap.fromImage(img)


def _gradient_pm(w=200, h=160):
    """竖向渐变（上黑下白）：向内采样 = 取到更靠下 = 更亮，可判方向。"""
    def _p(p, w_, h_):
        for y in range(int(h_)):
            v = int(255 * y / max(1, h_ - 1))
            p.setPen(QColor(v, v, v))
            p.drawLine(0, y, int(w_), y)
    return _pixmap(w, h, _p)


def _flat_pm(w=160, h=120, v=128):
    return _pixmap(w, h, lambda p, w_, h_: p.fillRect(
        0, 0, int(w_), int(h_), QColor(v, v, v)))


def _folder_state(name, liquid=None):
    st = _st(name)
    st.layout.append({"id": "f1", "type": "folder", "col": 0, "row": 0,
                      "page": 0, "name": "F",
                      "app": {"name": "F", "target": "F", "kind": "app"},
                      "apps": [{"name": "m1", "target": "m1", "kind": "app"}]})
    if liquid is not None:
        st.set_setting("liquid_folder", liquid)
    return st


def main(app):
    from src import liquid_glass as lg

    # ---------- L1 默认 ----------
    st = _st()
    check("L1 总开关默认开", st.get_setting("liquid_glass") is True)
    for surface in ("folder", "dock", "widget"):
        check("L1%s 默认 %s 表面开" % (surface[0], surface),
              lg.surface_enabled(st, surface) is True)
    lens = lg.lens_style(st)
    check("L1d 默认强度 0.55", abs(lens.strength - 0.55) < 1e-9,
          str(lens.strength))
    check("L1e 默认带宽 22 DIP", abs(lens.band - 22.0) < 1e-9, str(lens.band))

    # ---------- L2 表面开关独立 ----------
    st2 = _st()
    st2.set_setting("liquid_dock", False)
    check("L2 关 dock 只关 dock",
          lg.surface_enabled(st2, "dock") is False
          and lg.surface_enabled(st2, "folder") is True
          and lg.surface_enabled(st2, "widget") is True)

    # ---------- L3 总开关一票否决 ----------
    st3 = _st()
    st3.set_setting("liquid_glass", False)
    check("L3 总开关关 -> 三个表面全关",
          not any(lg.surface_enabled(st3, s_) for s_ in
                  ("folder", "dock", "widget")))

    # ---------- L4 夹紧 + 脏值回退 ----------
    st4 = _st()
    st4.set_setting("liquid_strength", 999)
    st4.set_setting("liquid_band", 0)
    l4 = lg.lens_style(st4)
    check("L4 强度夹到 1.0", l4.strength == 1.0, str(l4.strength))
    check("L4b 带宽夹到下限 6", l4.band == float(lg.BAND_MIN), str(l4.band))
    st4b = _st()
    st4b.set_setting("liquid_strength", "abc")
    st4b.set_setting("liquid_band", 999)
    l4b = lg.lens_style(st4b)
    check("L4c 脏值回退默认 0.55", abs(l4b.strength - 0.55) < 1e-9)
    check("L4d 带宽夹到上限 40", l4b.band == float(lg.BAND_MAX), str(l4b.band))

    # ---------- L5 各尺寸不抛 ----------
    ok = True
    try:
        lg.refract(QPixmap(), 20, lens)                     # 空图
        lg.refract(_flat_pm(3, 3), 20, lens)                # 极小
        lg.refract(_flat_pm(200, 160), 0, lens)             # 直角
        lg.refract(_flat_pm(200, 160), 500, lens)           # 半径超大
        lg.refract(_flat_pm(200, 160), 20, lg.Lens(0, 0))   # 全零
        img = QImage(120, 90, QImage.Format_ARGB32)
        p = QPainter(img)
        ok = lg.draw_refracted(p, QRectF(0, 0, 120, 90), 16,
                               _flat_pm(120, 90), lg.border_color())
        p.end()
    except Exception as exc:                                 # noqa: BLE001
        ok = False
        print("   抛异常:", repr(exc))
    check("L5 refract/draw_refracted 各尺寸不抛", bool(ok))

    # ---------- L6 几何：中部不变 / 只在带内变 / 方向向内 ----------
    band = 20.0
    strong = lg.Lens(strength=1.0, band=band)
    src = _gradient_pm(200, 160)
    out = lg.refract(src, 20, strong, dpr=1.0, frost=0.0)
    si, oi = src.toImage(), out.toImage()
    check("L6 输出尺寸与输入一致",
          oi.width() == si.width() and oi.height() == si.height(),
          "%dx%d" % (oi.width(), oi.height()))

    def _lum(im, x, y):
        c = im.pixelColor(int(x), int(y))
        return (c.red() + c.green() + c.blue()) / 3.0

    # 中部（离边 > band+4）逐像素相等
    inner_bad = 0
    for y in range(26, oi.height() - 26, 3):
        for x in range(26, oi.width() - 26, 3):
            c0, c1 = si.pixelColor(x, y), oi.pixelColor(x, y)
            if (abs(c0.red() - c1.red()) > 2 or abs(c0.green() - c1.green()) > 2
                    or abs(c0.blue() - c1.blue()) > 2):
                inner_bad += 1
    check("L6b 中部像素完全不变（折射只在边缘）", inner_bad == 0,
          "bad=%d" % inner_bad)

    # 边缘带内有变化
    edge_diff = 0
    for y in range(0, 12):
        for x in range(20, 180):
            c0, c1 = si.pixelColor(x, y), oi.pixelColor(x, y)
            if abs(c0.red() - c1.red()) > 2:
                edge_diff += 1
    check("L6c 边缘带内底图发生位移", edge_diff > 200, "diff=%d" % edge_diff)

    # 方向：向内采样 = 梯度图在顶边变亮（取到更靠下的像素）
    up = _lum(oi, 100, 2) - _lum(si, 100, 2)
    check("L6d 顶边折射向内（梯度图变亮 = 放大内侧内容）", up > 12,
          "delta=%.1f" % up)
    # 底边同理更暗（取到更靠上的暗像素）——两侧对称说明法线方向对
    dn = _lum(oi, 100, oi.height() - 3) - _lum(si, 100, si.height() - 3)
    check("L6e 底边折射向内（变暗，与顶边对称）", dn < -4, "delta=%.1f" % dn)

    # ---------- L7 纯色图：折射后必须一像素不变（= 无高光 / 无色散） ----------
    flat = _flat_pm(180, 140, 120)
    fl_out = lg.refract(flat, 22, strong, dpr=1.0, frost=0.0).toImage()
    fl_in = flat.toImage()
    # 只比圆角矩形**内部**：圆角外的像素本来就该是透明的（那是形状，不是光学）
    from PySide6.QtGui import QPainterPath
    mask = QPainterPath()
    mask.addRoundedRect(QRectF(1, 1, 178, 138), 21, 21)
    bad = 0
    max_dev = 0
    for y in range(0, fl_out.height(), 2):
        for x in range(0, fl_out.width(), 2):
            if not mask.contains(QRectF(x, y, 1, 1)):
                continue
            a, b = fl_in.pixelColor(x, y), fl_out.pixelColor(x, y)
            dev = max(abs(a.red() - b.red()), abs(a.green() - b.green()),
                      abs(a.blue() - b.blue()))
            max_dev = max(max_dev, dev)
            if dev > 2:
                bad += 1
    check("L7 纯色图折射后不变（没有高光/色散/加亮）",
          bad == 0 and max_dev <= 2, "bad=%d maxdev=%d" % (bad, max_dev))
    check("L7b 描边只是淡边界（不是高光）", lg.border_color().alpha() <= 60,
          "alpha=%d" % lg.border_color().alpha())

    # ---------- L8 强度 0 = 直通 ----------
    again = lg.refract(flat, 22, lg.Lens(strength=0.0, band=22.0))
    check("L8 强度 0 时原样返回（零开销）", again is flat)

    # ---------- L9 dpr 缩放 ----------
    out2 = lg.refract(_gradient_pm(200, 160), 20, strong, dpr=2.0,
                      frost=0.0)
    check("L9 dpr=2 时输出仍是同一物理尺寸",
          out2.width() == 200 and out2.height() == 160,
          "%dx%d" % (out2.width(), out2.height()))

    # ---------- S1-S4 设置 ----------
    from src import settings_dialog

    class FakeWin:
        def __init__(self):
            self.calls = 0

        def apply_liquid_style(self):
            self.calls += 1

    fw = FakeWin()
    st5 = _st("settings.json")
    dlg = None
    try:
        dlg = settings_dialog.SettingsDialog(fw, st5, None, page="appearance")
    except Exception as exc:      # noqa: BLE001
        check("S1 设置窗可构造", False, repr(exc))
    if dlg is not None:
        names = ("chk_liquid", "chk_liquid_folder", "chk_liquid_dock",
                 "chk_liquid_widget", "sld_liquid_strength", "sld_liquid_band")
        missing = [n for n in names if not hasattr(dlg, n)]
        check("S1 外观页液态控件齐全", not missing, str(missing))
        check("S1b 旧的光学滑条已删除",
              not hasattr(dlg, "sld_liquid_dispersion")
              and not hasattr(dlg, "chk_liquid_shimmer"))
        if not missing:
            check("S1c 初值读自设置",
                  dlg.chk_liquid.isChecked() is True
                  and dlg.sld_liquid_strength.value() == 55
                  and dlg.sld_liquid_band.value() == 22,
                  "%d/%d" % (dlg.sld_liquid_strength.value(),
                             dlg.sld_liquid_band.value()))
            before = fw.calls
            dlg.sld_liquid_strength.setValue(80)
            check("S2 强度滑条写设置", st5.get_setting("liquid_strength") == 80,
                  str(st5.get_setting("liquid_strength")))
            dlg.sld_liquid_band.setValue(30)
            check("S2b 带宽滑条写设置", st5.get_setting("liquid_band") == 30,
                  str(st5.get_setting("liquid_band")))
            check("S2c 改动触发窗口重绘", fw.calls > before,
                  "calls=%d" % fw.calls)
            dlg._reset_liquid()
            check("S3 恢复默认 55 / 22",
                  st5.get_setting("liquid_strength") == 55
                  and st5.get_setting("liquid_band") == 22,
                  "%s/%s" % (st5.get_setting("liquid_strength"),
                             st5.get_setting("liquid_band")))
        check("S4 文件夹悬停开关在设置里（布局页文件夹卡）",
              hasattr(dlg, "chk_hover_folder"))
        if hasattr(dlg, "chk_hover_folder"):
            dlg.chk_hover_folder.setChecked(False)
            check("S4b 关掉悬停展开写设置",
                  st5.get_setting("folder_hover_expand") is False)
        dlg.deleteLater()

    # ---------- R1-R5 三表面接线 ----------
    orig_refract, orig_draw = lg.refract, lg.draw_refracted
    calls = {"refract": 0, "draw": 0}

    def _fake_refract(pm, radius, lens_, dpr=1.0):
        calls["refract"] += 1
        return pm

    def _fake_draw(p, rect, radius, pm, border=None):
        calls["draw"] += 1
        return orig_draw(p, rect, radius, pm, border)

    lg.refract = _fake_refract
    lg.draw_refracted = _fake_draw

    def _reset_calls():
        calls["refract"] = calls["draw"] = 0

    try:
        # R1 小组件：有底图 -> 走折射；没底图 -> 回退普通玻璃
        from src import widget_glass

        class _Probe(widget_glass.GlassWidget):
            def paint_content(self, p):
                pass

        entry = {"id": "p1", "kind": "probe", "span": (2, 2), "settings": {}}
        probe = _Probe(_st(), entry)
        probe.resize(160, 140)
        _reset_calls()
        probe.render(QImage(160, 140, QImage.Format_ARGB32))
        check("R1a 没抓底图时回退普通玻璃（不折射）", calls["draw"] == 0,
              "draw=%d" % calls["draw"])
        probe._lens_src = _flat_pm(160, 140)
        probe._lens_pm = QPixmap()
        probe._lens_key = None
        _reset_calls()
        probe.render(QImage(160, 140, QImage.Format_ARGB32))
        check("R1b 有底图时小组件背景走折射", calls["draw"] >= 1,
              "draw=%d" % calls["draw"])
        probe.close()

        st_w = _st()
        st_w.set_setting("liquid_widget", False)
        probe2 = _Probe(st_w, entry)
        probe2.resize(160, 140)
        probe2._lens_src = _flat_pm(160, 140)
        _reset_calls()
        probe2.render(QImage(160, 140, QImage.Format_ARGB32))
        check("R1c 组件开关关闭 -> 不折射", calls["draw"] == 0)
        probe2.close()

        # R2 Dock
        from src import dock as dockmod

        st_d = _st()
        st_d.set_setting("dock_items", [])
        d = dockmod.DockWindow(st_d)
        d.apply_geometry((900, 600))
        d._lens_src = _flat_pm(max(80, d.width()), max(60, d.height()))
        d._lens_pm = QPixmap()
        d._lens_key = None
        _reset_calls()
        d.render(QImage(max(80, d.width()), max(60, d.height()),
                        QImage.Format_ARGB32))
        check("R2a Dock 有底图时走折射", calls["draw"] >= 1,
              "draw=%d" % calls["draw"])
        d.close()

        st_d2 = _st()
        st_d2.set_setting("dock_items", [])
        st_d2.set_setting("liquid_dock", False)
        d2 = dockmod.DockWindow(st_d2)
        d2.apply_geometry((900, 600))
        d2._lens_src = _flat_pm(max(80, d2.width()), max(60, d2.height()))
        _reset_calls()
        d2.render(QImage(max(80, d2.width()), max(60, d2.height()),
                         QImage.Format_ARGB32))
        check("R2b Dock 开关关闭 -> 不折射", calls["draw"] == 0)
        d2.close()

        # R3 文件夹弹窗
        from src.folder_popup import FolderPopup

        st_f = _folder_state("pop.json")
        popup = FolderPopup(st_f, st_f.find_entry("f1"), anchor_rect=None)
        popup.resize(320, 240)
        popup._backdrop = _flat_pm(320, 240)
        popup._lens_pm = QPixmap()
        popup._lens_key = None
        _reset_calls()
        popup.render(QImage(320, 240, QImage.Format_ARGB32))
        check("R3a 文件夹弹窗背景走折射", calls["draw"] >= 1,
              "draw=%d" % calls["draw"])
        popup.close()

        st_f2 = _folder_state("pop2.json", liquid=False)
        popup2 = FolderPopup(st_f2, st_f2.find_entry("f1"), anchor_rect=None)
        popup2.resize(320, 240)
        popup2._backdrop = _flat_pm(320, 240)
        _reset_calls()
        popup2.render(QImage(320, 240, QImage.Format_ARGB32))
        check("R3b 文件夹开关关闭 -> 不折射", calls["draw"] == 0)
        popup2.close()
    finally:
        lg.refract = orig_refract
        lg.draw_refracted = orig_draw


if __name__ == "__main__":
    _app = QApplication.instance() or QApplication([])
    main(_app)
    print("-" * 56)
    print("%d/%d checks, %d FAIL" % (total[0] - len(failures), total[0],
                                     len(failures)))
    for f in failures:
        print("  FAILED:", f)
    sys.exit(1 if failures else 0)
