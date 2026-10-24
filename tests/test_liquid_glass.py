# -*- coding: utf-8 -*-
"""v14 反馈 9/10：液态玻璃 = 透明背景 + 边缘折射（**实时**）。

语义（用户原话）："算作一种元素的背景色，背景色是透明的，在边缘处会模拟
底层壁纸的图象被液体表面折射产生一定的扭曲效果"，而且"应该是实时计算的，
随着组件位置的同步，样式也会随着后面壁纸的图案动态变化"。

参考 GlassDesk 1.4.0 的 `TransitionGlass.hlsl`：只有边缘 band 内的底图被
位移，中部完全不动；位移沿外法线**向外采样**（边缘看到的是玻璃外面的世界
被压进来），这样映射单调、不会折叠出一圈色块。

  L1-L8  折射核心：默认参数 / 剖面 / 中部不动 / 只在带内变 / 向外采样 /
         纯色不变（= 没有高光、没有色散）/ 边界不抛
  L10-L13 v14e 四个可调光学层：饱和度（PIL）/ 磨砂混合 / 色散 /
         方向性高光——默认值下输出与 v14d 一致
  W1-W5  壁纸快照：覆盖范围 / 裁剪尺寸（含出界）/ 磨砂版 / 版本 / 无壁纸
  V1-V5  实时折射缓存：命中 / 移动即重算 / stale 判据 / 参数变化（含新参数）
  S1-S8  设置与配色：外观页只留总闸 + 两参数 / Dock 开关与置灰 /
         组件 bg_liquid 推导与置灰 / 文件夹色板的液态项
  R1-R3  三表面接线（液态开 -> 走折射；关 -> 走普通玻璃）

运行：python tests/test_liquid_glass.py   （offscreen；判据看退出码）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect, QRectF, Qt  # noqa: E402
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
    """竖向渐变（上黑下白）：行号即亮度，可反查采样自哪一行。"""
    def _p(p, w_, h_):
        for y in range(int(h_)):
            v = int(255 * y / max(1, h_ - 1))
            p.setPen(QColor(v, v, v))
            p.drawLine(0, y, int(w_), y)
    return _pixmap(w, h, _p)


def _flat_pm(w=160, h=120, v=128):
    return _pixmap(w, h, lambda p, w_, h_: p.fillRect(
        0, 0, int(w_), int(h_), QColor(v, v, v)))


def _folder_state(name, liquid=True):
    st = _st(name)
    st.layout.append({"id": "f1", "type": "folder", "col": 0, "row": 0,
                      "page": 0, "name": "F",
                      "app": {"name": "F", "target": "F", "kind": "app"},
                      "apps": [{"name": "m1", "target": "m1", "kind": "app"}]})
    if liquid:
        st.find_entry("f1")["tint"] = "liquid"
    st.save()
    return st


class _FakeWin:
    """设置面板只要求 window 上有这几个钩子。"""

    def __init__(self):
        self.calls = 0

    def apply_liquid_style(self):
        self.calls += 1

    def apply_dock_settings(self):
        self.calls += 1

    def apply_dock_style(self):
        self.calls += 1

    def apply_label_style(self):
        self.calls += 1


def main(app):
    from src import liquid_glass as lg

    # ---------- L1 默认 ----------
    st = _st()
    check("L1 总开关默认开", st.get_setting("liquid_glass") is True)
    lens = lg.lens_style(st)
    check("L1b 默认强度 60% / 带宽 24",
          abs(lens.strength - 0.6) < 1e-6 and abs(lens.band - 24) < 1e-6,
          "%.2f/%.1f" % (lens.strength, lens.band))
    # v14e：四个新光学层（liquid-glass-react）的出厂值
    check("L1f 默认饱和度 1.4 / 磨砂 1.0 / 色散 0 / 高光 0",
          abs(lens.saturation - 1.4) < 1e-6 and abs(lens.frost - 1.0) < 1e-6
          and lens.aberration == 0.0 and lens.rimlight == 0.0,
          "%.2f/%.2f/%.2f/%.2f" % (lens.saturation, lens.frost,
                                   lens.aberration, lens.rimlight))
    check("L1g 直接构造 Lens 保持中性（老代码拿到纯折射）",
          lg.Lens(0.6, 20.0).saturation == 1.0
          and lg.Lens(0.6, 20.0).frost == 1.0
          and lg.Lens(0.6, 20.0).aberration == 0.0
          and lg.Lens(0.6, 20.0).rimlight == 0.0)
    st.set_setting("liquid_saturation", 999)
    st.set_setting("liquid_frost", -5)
    lens_c = lg.lens_style(st)
    check("L1h 新参数越界被夹紧（200 / 0）",
          abs(lens_c.saturation - 2.0) < 1e-6
          and abs(lens_c.frost - 0.0) < 1e-6)
    check("L1c 默认 Dock 表面开", lg.surface_enabled(st, "dock") is True)
    st.set_setting("liquid_dock", False)
    check("L1d Dock 开关关闭 -> 该表面不启用",
          lg.surface_enabled(st, "dock") is False)
    st.set_setting("liquid_glass", False)
    check("L1e 总闸关闭 -> 一票否决", lg.surface_enabled(st, "dock") is False)

    # ---------- L2 剖面 ----------
    d0 = lg._displacement(0.0, 48.0, 1.0, 1.0)
    dmid = lg._displacement(24.0, 48.0, 1.0, 1.0)
    dend = lg._displacement(48.0, 48.0, 1.0, 1.0)
    check("L2 外沿位移最大 = 32*strength",
          abs(d0 - 32.0) < 0.01, "%.2f" % d0)
    check("L2b 内沿归零", abs(dend) < 0.01, "%.2f" % dend)
    check("L2c 单调递减", d0 > dmid > dend, "%.1f>%.1f>%.1f" % (d0, dmid, dend))
    d_dpr = lg._displacement(0.0, 48.0, 1.0, 2.0)
    check("L2d 位移按 DPR 放大（高 DPI 不吃半价）",
          abs(d_dpr - 2 * d0) < 0.01, "%.1f" % d_dpr)
    check("L2e 强度 0 -> 无位移", lg._displacement(0.0, 48.0, 0.0, 1.0) == 0.0)

    # ---------- L3/L4 中部不动、变化只在带内 ----------
    src = _gradient_pm(200, 160)
    # 几何判据一律关掉暗纱：暗纱是"可读性"那层，不属于折射
    out = lg.refract(src, 20, lg.Lens(0.6, 20.0), dpr=1.0, frost=0.0,
                     scrim=False)
    img_in, img_out = src.toImage(), out.toImage()
    check("L3 输出尺寸 = 输入尺寸",
          (img_out.width(), img_out.height()) == (200, 160),
          "%s" % [(img_out.width(), img_out.height())])
    mid_ok = True
    for y in range(30, 130):
        for x in range(30, 170, 3):
            if img_in.pixelColor(x, y) != img_out.pixelColor(x, y):
                mid_ok = False
                break
        if not mid_ok:
            break
    check("L3b 中部逐像素不变（透明玻璃，不叠色）", mid_ok)

    edge_diff = sum(1 for x in range(20, 180, 4)
                    if img_in.pixelColor(x, 2) != img_out.pixelColor(x, 2))
    check("L4 边缘带内有位移", edge_diff > 20, "%d/40" % edge_diff)
    far_diff = sum(1 for x in range(10, 190, 4)
                   if img_in.pixelColor(x, 26) != img_out.pixelColor(x, 26))
    check("L4b 带外（26px 深）不再变化", far_diff == 0, "%d" % far_diff)

    # ---------- L5 向外采样（边缘看到的是玻璃外面的世界） ----------
    m = 40
    grad = _gradient_pm(240, 240)
    # hairline=False：轮廓线会压在第一行上，这里只验采样方向
    out5 = lg.refract(grad, 0, lg.Lens(0.5, 20.0), dpr=1.0, frost=0.0,
                      margin=m, hairline=False, scrim=False).toImage()
    # 表面从 y=m 开始；顶部第 0 行应采到 m - 16（32*0.5）那一行
    got = out5.pixelColor(60, 0).red()
    want = m - int(round(32 * 0.5))
    check("L5 顶边采到表面**外面**的行（向外折射）",
          abs(got - want) <= 3, "got=%d want=%d" % (got, want))
    row_b = m + out5.height() - 1 + int(round(32 * 0.5))
    want_b = int(255 * row_b / 239)          # 渐变按 239 归一
    got_b = out5.pixelColor(60, out5.height() - 1).red()
    check("L5b 底边同样向外采", abs(got_b - want_b) <= 3,
          "got=%d want=%d" % (got_b, want_b))

    # ---------- L6 纯色图不变（没有高光 / 色散 / 加亮） ----------
    flat = _flat_pm(180, 140, 120)
    fo = lg.refract(flat, 22, lg.Lens(0.9, 24.0), dpr=1.0, frost=0.0,
                    scrim=False).toImage()
    fi = flat.toImage()
    max_dev = 0
    for y in range(26, 140 - 26, 3):        # 圆角外是透明，不参与比较
        for x in range(26, 180 - 26, 3):
            a, b = fi.pixelColor(x, y), fo.pixelColor(x, y)
            max_dev = max(max_dev, abs(a.red() - b.red()),
                          abs(a.green() - b.green()), abs(a.blue() - b.blue()))
    check("L6 纯色图折射后逐像素不变（无高光/彩边）", max_dev <= 2,
          "max_dev=%d" % max_dev)

    # ---------- L9 自适应暗纱：亮壁纸压一点，暗壁纸不动 ----------
    bright = _flat_pm(160, 120, 220)
    bo = lg.refract(bright, 20, lg.Lens(0.6, 24.0), dpr=1.0,
                    frost=0.0).toImage()
    bv = bo.pixelColor(80, 60).red()
    check("L9 亮壁纸有一层极淡暗纱（白字才读得出来）", bv < 216,
          "220 -> %d" % bv)
    dark = _flat_pm(160, 120, 60)
    do_ = lg.refract(dark, 20, lg.Lens(0.6, 24.0), dpr=1.0,
                     frost=0.0).toImage()
    dv = do_.pixelColor(80, 60).red()
    check("L9b 暗壁纸只加极淡暗纱（≤6/255，保住透明感）", 53 <= dv <= 60,
          "60 -> %d" % dv)

    # ---------- L10-L13 v14e 四个可调光学层 ----------
    try:
        from PIL import Image as _PILImage          # noqa: F401
        has_pil = True
    except Exception:                               # noqa: BLE001
        has_pil = False

    def _colored_pm(w=200, h=160):
        """红->绿横向渐变（有饱和度可调，也有红蓝通道可分）。"""
        def _p(p, w_, h_):
            for x in range(int(w_)):
                p.setPen(QColor(int(255 * x / max(1, w_ - 1)),
                                90, int(255 - 255 * x / max(1, w_ - 1))))
                p.drawLine(x, 0, x, int(h_))
        return _pixmap(w, h, _p)

    colored = _colored_pm()
    if not has_pil:
        check("L10 PIL 缺席：饱和度跳过（不崩）", True, "skip")
    else:
        sat_pm = lg.refract(colored, 20, lg.Lens(0.6, 20.0, saturation=2.0),
                            dpr=1.0, frost=0.0, scrim=False).toImage()
        neu_pm = lg.refract(colored, 20, lg.Lens(0.6, 20.0),
                            dpr=1.0, frost=0.0, scrim=False).toImage()
        c = sat_pm.pixelColor(100, 80)
        n = neu_pm.pixelColor(100, 80)
        check("L10 饱和度 200% 中部色彩更艳（R 升 G 降）",
              c.red() > n.red() and c.green() < n.green(),
              "(%d,%d,%d)->(%d,%d,%d)" % (n.red(), n.green(), n.blue(),
                                          c.red(), c.green(), c.blue()))
        check("L10b 中性饱和度 = 原图逐像素（frost 0 时）",
              neu_pm.pixelColor(100, 80)
              == colored.toImage().pixelColor(100, 80))

    # 磨砂：清晰/磨砂混合，两端等于原图，中间介于两者之间
    stripes = _pixmap(180, 140, lambda p, w_, h_: [
        p.fillRect(x, 0, 3, int(h_), QColor(240, 40, 40) if (x // 3) % 2
                   else QColor(20, 20, 40)) for x in range(0, int(w_), 6)])
    sharp_q = stripes.toImage()
    soft_q = sharp_q.scaled(20, 16, Qt.IgnoreAspectRatio,
                            Qt.SmoothTransformation).scaled(
                                sharp_q.width(), sharp_q.height(),
                                Qt.IgnoreAspectRatio,
                                Qt.SmoothTransformation)
    f1 = lg._blend_frost(sharp_q, soft_q, 1.0)
    f0 = lg._blend_frost(sharp_q, soft_q, 0.0)
    fh = lg._blend_frost(sharp_q, soft_q, 0.5)
    check("L11 磨砂 1.0 -> 原磨砂图（v14d 前的固定行为）",
          f1.pixelColor(90, 70) == soft_q.pixelColor(90, 70))
    check("L11b 磨砂 0 -> 清晰原图",
          f0.pixelColor(90, 70) == sharp_q.pixelColor(90, 70))
    mid = fh.pixelColor(90, 70)
    s_, o_ = soft_q.pixelColor(90, 70), sharp_q.pixelColor(90, 70)
    check("L11c 磨砂 0.5 介于两者之间",
          min(s_.red(), o_.red()) <= mid.red() <= max(s_.red(), o_.red()),
          "%d in [%d,%d]" % (mid.red(), min(s_.red(), o_.red()),
                             max(s_.red(), o_.red())))

    # 色散：默认 0 输出不变；开 1.0 后只影响最外圈、中部不动
    ab0 = lg.refract(colored, 20, lg.Lens(0.6, 24.0), dpr=1.0, frost=0.0,
                     scrim=False, hairline=False).toImage()
    ab1 = lg.refract(colored, 20, lg.Lens(0.6, 24.0, aberration=1.0),
                     dpr=1.0, frost=0.0, scrim=False,
                     hairline=False).toImage()
    edge_diff = sum(1 for x in range(30, 170, 4)
                    if ab0.pixelColor(x, 0) != ab1.pixelColor(x, 0))
    check("L12 色散改变最外圈（红蓝错位）", edge_diff > 10, "%d/35" % edge_diff)
    mid_same = all(ab0.pixelColor(x, y) == ab1.pixelColor(x, y)
                   for y in range(40, 120, 8) for x in range(40, 160, 8))
    check("L12b 色散不动中部", mid_same)

    # 高光：顶行被提亮、中部不动（上亮下暗的方向性内描边）
    rim0 = lg.refract(colored, 20, lg.Lens(0.6, 24.0), dpr=1.0, frost=0.0,
                      scrim=False, hairline=False).toImage()
    rim1 = lg.refract(colored, 20, lg.Lens(0.6, 24.0, rimlight=1.0),
                      dpr=1.0, frost=0.0, scrim=False,
                      hairline=False).toImage()
    top_gain = rim1.pixelColor(100, 1).value() - rim0.pixelColor(100, 1).value()
    bot_gain = (rim1.pixelColor(100, rim1.height() - 2).value()
                - rim0.pixelColor(100, rim0.height() - 2).value())
    check("L13 高光提亮顶边", top_gain > 8, "+%d" % top_gain)
    check("L13b 底边远弱于顶边", bot_gain < top_gain - 4,
          "top+%d bot+%d" % (top_gain, bot_gain))
    mid_same = all(rim0.pixelColor(x, y) == rim1.pixelColor(x, y)
                   for y in range(40, 120, 8) for x in range(40, 160, 8))
    check("L13c 高光不动中部", mid_same)

    # ---------- L7/L8 边界 ----------
    tiny = _pixmap(4, 4, lambda p, w_, h_: p.fillRect(0, 0, 4, 4,
                                                      QColor(10, 20, 30)))
    try:
        pm = lg.refract(tiny, 4, lg.Lens(0.6, 24.0))
        ok = not pm.isNull()
    except Exception:      # noqa: BLE001
        ok = False
    check("L7 极小图不抛（4×4）", ok)
    off = lg.refract(src, 20, lg.Lens(0.0, 20.0), dpr=1.0, frost=0.0)
    check("L8 强度 0 -> 原样返回",
          off.toImage().pixelColor(10, 10) == img_in.pixelColor(10, 10))
    try:
        lg.refract(None, 10, lg.Lens(0.5, 20.0))
        pm = lg.refract(QPixmap(), 10, lg.Lens(0.5, 20.0))
        check("L8b 空底图返回空（调用方兜底）", pm.isNull())
    except Exception:      # noqa: BLE001
        check("L8b 空底图返回空（调用方兜底）", False)

    # ---------- W1-W5 壁纸快照 ----------
    from src import wallpaper as wp

    snap = wp.snapshot()
    if snap is None:
        check("W1 本机有壁纸快照（读不到就跳过后续 W）", True, "skip")
    else:
        check("W1 快照覆盖虚拟桌面",
              snap.rect.width() > 0 and snap.image.width() > 0,
              "%dx%d" % (snap.image.width(), snap.image.height()))
        r = QRect(snap.rect.x() + 50, snap.rect.y() + 40, 120, 90)
        c = snap.crop(r)
        dpr = snap.dpr
        check("W2 裁剪尺寸 = 矩形 × DPR",
              abs(c.width() - int(round(120 * dpr))) <= 1
              and abs(c.height() - int(round(90 * dpr))) <= 1,
              "%dx%d dpr=%.2f" % (c.width(), c.height(), dpr))
        far = QRect(snap.rect.x() - 60, snap.rect.y() - 60, 120, 90)
        cf = snap.crop(far)
        check("W2b 出界裁剪也保持同样尺寸（补透明，不错位）",
              (cf.width(), cf.height()) == (c.width(), c.height()),
              "%dx%d" % (cf.width(), cf.height()))
        check("W2c 出界部分透明", cf.pixelColor(2, 2).alpha() == 0,
              "a=%d" % cf.pixelColor(2, 2).alpha())
        s = snap.soft_crop(r, (c.width(), c.height()))
        check("W3 磨砂裁剪缩放到目标尺寸",
              (s.width(), s.height()) == (c.width(), c.height()),
              "%dx%d" % (s.width(), s.height()))
        c1 = snap.crop(QRect(60, 60, 100, 80))
        c2 = snap.crop(QRect(160, 60, 100, 80))
        check("W3b 不同位置裁出不同图案",
              c1.pixelColor(20, 20) != c2.pixelColor(20, 20))

        v1 = wp.version()
        check("W4 version 稳定（同 key 多次调用不变）", v1 == wp.version(),
              str(v1))

    orig_reg = wp._registry
    try:
        wp._registry = lambda: ("", 10, False, QColor(0, 0, 0))
        wp.invalidate()
        check("W5 没有壁纸 -> snapshot None（调用方走抓屏兜底）",
              wp.snapshot() is None)
    finally:
        wp._registry = orig_reg
        wp.invalidate()

    # ---------- V1-V4 实时折射缓存 ----------
    live = lg.LiveLens()
    rect_a = QRect(40, 40, 180, 140)
    rect_b = QRect(200, 120, 180, 140)
    lens_v = lg.Lens(0.6, 24.0)
    p1 = live.pixmap(rect_a, 20, lens_v, 1.0)
    p2 = live.pixmap(rect_a, 20, lens_v, 1.0)
    check("V1 同一矩形命中缓存（不重复算）",
          (not p1.isNull()) and p1.cacheKey() == p2.cacheKey())
    check("V1b 输出尺寸 = 表面尺寸",
          (p1.width(), p1.height()) == (rect_a.width(), rect_a.height()),
          "%dx%d" % (p1.width(), p1.height()))
    p3 = live.pixmap(rect_b, 20, lens_v, 1.0)
    check("V2 位置一变立刻重算（跟着壁纸走）",
          p3.cacheKey() != p1.cacheKey())
    check("V2b 内容随位置不同（同一块玻璃换了地方图案也换）",
          p1.toImage().pixelColor(90, 70) != p3.toImage().pixelColor(90, 70))
    check("V3 stale 对旧矩形为真、对新矩形为假",
          live.stale(rect_a, 20, lens_v) and
          not live.stale(rect_b, 20, lens_v))
    p4 = live.pixmap(rect_b, 20, lg.Lens(0.9, 24.0), 1.0)
    check("V4 参数变化 -> 重算", p4.cacheKey() != p3.cacheKey())
    check("V4b 强度变化不改输出尺寸",
          (p4.width(), p4.height()) == (p3.width(), p3.height()))
    # v14e：新光学参数也进缓存 key（改设置后无需重开表面即重算）
    lens_sat = lg.Lens(0.9, 24.0, saturation=1.8)
    check("V5 stale 对新参数敏感（饱和度变了 = 过期）",
          live.stale(rect_b, 20, lens_sat))
    p5 = live.pixmap(rect_b, 20, lens_sat, 1.0)
    check("V5b 饱和度变化 -> 重算", p5.cacheKey() != p4.cacheKey())
    lens_frz = lg.Lens(0.9, 24.0, frost=0.2)
    p6 = live.pixmap(rect_b, 20, lens_frz, 1.0)
    check("V5c 磨砂变化 -> 重算", p6.cacheKey() != p5.cacheKey())
    check("V5d stale 同参数为假",
          not live.stale(rect_b, 20, lens_frz))

    # ---------- S1-S4 设置面板 ----------
    from src import settings_dialog

    st5 = _st("dlg.json")
    fw = _FakeWin()
    dlg = settings_dialog.SettingsDialog(fw, st5, None)
    check("S1 外观页保留总开关", hasattr(dlg, "chk_liquid"))
    check("S1b 三表面开关已从外观页移除（改为各元素自己的配色）",
          not hasattr(dlg, "chk_liquid_folder")
          and not hasattr(dlg, "chk_liquid_widget"))
    check("S2 强度默认值落到滑条",
          dlg.sld_liquid_strength.value() == lg.DEFAULT_STRENGTH,
          str(dlg.sld_liquid_strength.value()))
    before = fw.calls
    dlg.sld_liquid_strength.setValue(80)
    check("S2b 强度滑条写设置", st5.get_setting("liquid_strength") == 80,
          str(st5.get_setting("liquid_strength")))
    dlg.sld_liquid_band.setValue(30)
    check("S2c 带宽滑条写设置", st5.get_setting("liquid_band") == 30,
          str(st5.get_setting("liquid_band")))
    check("S2d 改动推窗口重绘", fw.calls > before, "calls=%d" % fw.calls)
    # v14e：四个新滑条
    check("S2e 饱和度滑条默认 140",
          dlg.sld_liquid_saturation.value() == lg.DEFAULT_SATURATION,
          str(dlg.sld_liquid_saturation.value()))
    dlg.sld_liquid_saturation.setValue(180)
    dlg.sld_liquid_frost.setValue(30)
    dlg.sld_liquid_aberration.setValue(40)
    dlg.sld_liquid_rimlight.setValue(50)
    check("S2f 新滑条写设置",
          st5.get_setting("liquid_saturation") == 180
          and st5.get_setting("liquid_frost") == 30
          and st5.get_setting("liquid_aberration") == 40
          and st5.get_setting("liquid_rimlight") == 50,
          "%s/%s/%s/%s" % (st5.get_setting("liquid_saturation"),
                           st5.get_setting("liquid_frost"),
                           st5.get_setting("liquid_aberration"),
                           st5.get_setting("liquid_rimlight")))
    dlg.chk_liquid.setChecked(False)
    check("S2g 总闸关闭 -> 新滑条置灰",
          not dlg.sld_liquid_saturation.isEnabled()
          and not dlg.sld_liquid_frost.isEnabled()
          and not dlg.sld_liquid_aberration.isEnabled()
          and not dlg.sld_liquid_rimlight.isEnabled())
    dlg.chk_liquid.setChecked(True)
    dlg._reset_liquid()
    check("S3 恢复默认 %d / %d" % (lg.DEFAULT_STRENGTH, lg.DEFAULT_BAND),
          st5.get_setting("liquid_strength") == lg.DEFAULT_STRENGTH
          and st5.get_setting("liquid_band") == lg.DEFAULT_BAND,
          "%s/%s" % (st5.get_setting("liquid_strength"),
                     st5.get_setting("liquid_band")))
    check("S3c 恢复默认也覆盖四个新参数",
          st5.get_setting("liquid_saturation") == lg.DEFAULT_SATURATION
          and st5.get_setting("liquid_frost") == lg.DEFAULT_FROST
          and st5.get_setting("liquid_aberration") == lg.DEFAULT_ABERRATION
          and st5.get_setting("liquid_rimlight") == lg.DEFAULT_RIMLIGHT)
    check("S3b 文件夹悬停开关仍在布局页", hasattr(dlg, "chk_hover_folder"))

    check("S4 Dock 分页有液态玻璃开关", hasattr(dlg, "chk_dock_liquid"))
    if hasattr(dlg, "chk_dock_liquid"):
        dlg.chk_dock_liquid.setChecked(True)
        check("S4b 开启 -> 写 liquid_dock=True",
              st5.get_setting("liquid_dock") is True)
        check("S4c 开启 -> 不透明度置灰",
              not dlg.sld_dock_alpha.isEnabled())
        dlg.chk_dock_liquid.setChecked(False)
        check("S4d 关闭 -> 写 liquid_dock=False",
              st5.get_setting("liquid_dock") is False)
        check("S4e 关闭 -> 不透明度恢复可调",
              dlg.sld_dock_alpha.isEnabled())
    dlg.deleteLater()

    # ---------- S5/S6 组件配色 ----------
    from src import widget_style

    check("S5 没设过颜色 -> 默认液态",
          widget_style.liquid_enabled({}) is True)
    check("S5b 设过背景色 -> 默认走颜色（尊重老选择）",
          widget_style.liquid_enabled({"bg_color": "#FF0000"}) is False)
    check("S5c 显式开液态 -> 覆盖颜色",
          widget_style.liquid_enabled({"bg_color": "#FF0000",
                                       "bg_liquid": True}) is True)
    sty = widget_style.resolve({"bg_color": "#FF0000", "bg_liquid": True})
    check("S5d 液态时 Style 不带底色（互斥）",
          sty.liquid and sty.bg is None and not sty.custom_bg)
    sty2 = widget_style.resolve({"bg_color": "#FF0000", "bg_liquid": False})
    check("S5e 关掉液态 -> 颜色生效",
          (not sty2.liquid) and sty2.bg is not None
          and sty2.bg.name().upper() == "#FF0000")

    from src import widget_registry, widget_settings

    spec = widget_registry.get("clock")
    entry = {"id": "w1", "type": "clock", "settings": {}}
    vals = widget_settings.current_values(spec, entry["settings"])
    check("S6 面板里液态默认勾上（未设过颜色）", vals.get("bg_liquid") is True)
    dlg2 = widget_settings.WidgetSettingsDialog(spec, entry, st5)
    chk = dlg2._controls.get("bg_liquid")
    check("S6b 组件设置里有液态开关", chk is not None)
    if chk is not None:
        check("S6c 液态开 -> 背景色置灰",
              not dlg2._holders["bg_color"].isEnabled())
        check("S6d 液态开 -> 不透明度置灰",
              not dlg2._holders["bg_alpha"].isEnabled())
        chk.setChecked(False)
        check("S6e 关掉液态 -> 颜色可调",
              dlg2._holders["bg_color"].isEnabled()
              and dlg2._holders["bg_alpha"].isEnabled())
        check("S6f 关掉液态写进 entry",
              entry["settings"].get("bg_liquid") is False)
    dlg2.deleteLater()

    # ---------- S7/S8 文件夹色板 ----------
    from src import folder_forms, folder_tint

    form = folder_forms.TintForm(_st("tint.json"), current=None)
    keys = [k for k, _c in form._entries()]
    check("S7 色板第一项是液态玻璃", bool(keys) and keys[0] == folder_tint.LIQUID,
          str(keys[:3]))
    check("S7b 色板仍保留 16 个预设色",
          sum(1 for _k, c in form._entries() if isinstance(c, QColor)) == 16)
    got = []
    form.tintPicked.connect(lambda v: got.append(v))
    form.on_pick(folder_tint.LIQUID)
    check("S7c 点液态 -> 发出 'liquid'", got == [folder_tint.LIQUID], str(got))
    form.deleteLater()

    e = {"tint": "liquid"}
    check("S8 folder_liquid 判定", folder_tint.folder_liquid(e) is True)
    check("S8b 液态时 tint 回白色（选中态基色）",
          folder_tint.folder_tint(e).name().upper() == "#FFFFFF")
    check("S8c 普通颜色不算液态",
          folder_tint.folder_liquid({"tint": "#5E5CE6"}) is False)

    # ---------- R1-R3 三表面接线 ----------
    orig_refract, orig_draw = lg.refract, lg.draw_refracted
    calls = {"draw": 0}

    def _fake_refract(*a, **kw):
        return orig_refract(*a, **kw)

    def _fake_draw(p, rect, radius, pm, border=None):
        calls["draw"] += 1
        return orig_draw(p, rect, radius, pm, border)

    lg.refract = _fake_refract
    lg.draw_refracted = _fake_draw
    try:
        from src import widget_glass

        class _Probe(widget_glass.GlassWidget):
            def paint_content(self, p):
                pass

        entry = {"id": "p1", "kind": "probe", "span": (2, 2), "settings": {}}
        probe = _Probe(_st("probe.json"), entry)
        probe.resize(160, 140)
        calls["draw"] = 0
        probe.render(QImage(160, 140, QImage.Format_ARGB32))
        check("R1 小组件默认走折射", calls["draw"] >= 1,
              "draw=%d" % calls["draw"])
        probe.close()

        entry2 = {"id": "p2", "kind": "probe", "span": (2, 2),
                  "settings": {"bg_liquid": False}}
        probe2 = _Probe(_st("probe2.json"), entry2)
        probe2.resize(160, 140)
        calls["draw"] = 0
        probe2.render(QImage(160, 140, QImage.Format_ARGB32))
        check("R1b 液态关掉 -> 走普通玻璃", calls["draw"] == 0,
              "draw=%d" % calls["draw"])
        probe2.close()

        from src import dock as dockmod

        st_d = _st("dock.json")
        st_d.set_setting("dock_items", [])
        d = dockmod.DockWindow(st_d)
        d.apply_geometry((900, 600))
        calls["draw"] = 0
        d.render(QImage(max(80, d.width()), max(60, d.height()),
                        QImage.Format_ARGB32))
        check("R2 Dock 默认走折射", calls["draw"] >= 1,
              "draw=%d" % calls["draw"])
        d.close()

        st_d2 = _st("dock2.json")
        st_d2.set_setting("dock_items", [])
        st_d2.set_setting("liquid_dock", False)
        d2 = dockmod.DockWindow(st_d2)
        d2.apply_geometry((900, 600))
        calls["draw"] = 0
        d2.render(QImage(max(80, d2.width()), max(60, d2.height()),
                         QImage.Format_ARGB32))
        check("R2b Dock 开关关闭 -> 不折射", calls["draw"] == 0)
        d2.close()

        from src.folder_popup import FolderPopup

        st_f = _folder_state("pop.json", liquid=True)
        popup = FolderPopup(st_f, st_f.find_entry("f1"), anchor_rect=None)
        popup.resize(320, 240)
        calls["draw"] = 0
        popup.render(QImage(320, 240, QImage.Format_ARGB32))
        check("R3 文件夹配色=液态 -> 弹窗背景折射", calls["draw"] >= 1,
              "draw=%d" % calls["draw"])
        popup.close()

        st_f2 = _folder_state("pop2.json", liquid=False)
        popup2 = FolderPopup(st_f2, st_f2.find_entry("f1"), anchor_rect=None)
        popup2.resize(320, 240)
        calls["draw"] = 0
        popup2.render(QImage(320, 240, QImage.Format_ARGB32))
        check("R3b 文件夹选了颜色 -> 普通玻璃", calls["draw"] == 0,
              "draw=%d" % calls["draw"])
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
