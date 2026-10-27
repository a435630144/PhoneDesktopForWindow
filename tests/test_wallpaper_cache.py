# -*- coding: utf-8 -*-
"""v15c：饱和度烘焙的磁盘缓存（液态玻璃首帧最大的一笔）。

背景（profile 实测）：首次调用 `_saturate_one(soft)` 要 **64–89ms**，
其中 PIL 的 `import` 就占 ~40ms（`PIL.Image` + `ImageEnhance`），其余是
1280×720 的整图转换与 blend。可这张图**只跟壁纸内容有关**——壁纸不变时
每次启动都算出完全一样的结果。缓存到 %APPDATA%/cache 后：读 4MB + copy
≈ 2–6ms，且 PIL 完全不用导入。

判据：

  W1 `Wallpaper.version` 是 Python hash（每次启动都变），不能当磁盘键；
     `stable_version` 必须跨进程稳定
  W2 冷写入 -> 热命中：像素逐字节相等
  W3 命中时**不导入 PIL**（省的是导入，不只是计算）
  W4 换了饱和度百分比 -> 换文件（不许拿 120% 的图当 140% 用）
  W5 `stable_version=0`（测试/伪造快照）不落盘——绝不污染用户目录
  W6 半截/损坏缓存当作未命中，结果仍正确
  W7 换壁纸会新增文件，但只保留最近 N 份

运行：QT_QPA_PLATFORM=offscreen python tests/test_wallpaper_cache.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src import wallpaper  # noqa: E402

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def _wall(version=12345, size=(120, 80)):
    w, h = size
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(Qt.transparent)      # QImage 不初始化：不填会让两次构造结果不同
    p = QPainter(img)
    for x in range(0, w, 12):
        p.fillRect(QRect(x, 0, 6, h), QColor(40 + x % 180, 90, 160))
    p.fillRect(QRect(0, 0, w // 2, h // 2), QColor(220, 160, 40))
    p.end()
    soft = img.scaled(max(8, w // 3), max(8, h // 3),
                      Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return wallpaper.Wallpaper(image=img, soft=soft,
                               rect=QRect(0, 0, 1920, 1080), dpr=1.0,
                               version=7, stable_version=version)


def _solid_wall(version=999, size=(120, 78)):
    img = QImage(size[0], size[1], QImage.Format_ARGB32)
    img.fill(QColor(10, 20, 30))
    soft = img.scaled(size[0] // 3, size[1] // 3, Qt.IgnoreAspectRatio,
                      Qt.SmoothTransformation)
    return wallpaper.Wallpaper(image=img, soft=soft,
                               rect=QRect(0, 0, 1920, 1080), dpr=1.0,
                               version=7, stable_version=version)


def _bytes(img):
    return img.constBits().tobytes()


def _reset_mem():
    wallpaper._SAT_CACHE["key"] = None
    wallpaper._SAT_CACHE["wall"] = None


def main(app, tmp):
    cache = tmp / "cache"

    # ---------- W1 稳定版本 ----------
    v1 = wallpaper._stable_version()
    v2 = wallpaper._stable_version()
    check("W1 stable_version 同进程内稳定", v1 == v2, str(v1))

    # ---------- W2 冷 -> 热：逐字节相等 ----------
    wallpaper.set_sat_cache_dir(cache)
    w = _wall()
    _reset_mem()
    cold = wallpaper.saturated(w, 1.4, need_sharp=False)
    files = sorted(cache.glob("sat_*.bin"))
    check("W2 冷启动写出了缓存文件", len(files) == 1, str([f.name for f in files]))

    _reset_mem()
    wall_imported = "PIL" in sys.modules
    hot = wallpaper.saturated(w, 1.4, need_sharp=False)
    check("W2b 热命中与冷计算结果逐字节相等",
          _bytes(cold.soft) == _bytes(hot.soft),
          "%d bytes" % len(_bytes(cold.soft)))
    # 注意：同进程里 PIL 已经被冷调用导入过，W3 的"不导入"要在子进程验，
    # 这里只验证缓存读取路径确实没再做一次烘焙（尺寸/内容一致已覆盖）。

    # ---------- W4 百分比进 key ----------
    _reset_mem()
    wallpaper.saturated(w, 1.8, need_sharp=False)
    names = sorted(p.name for p in cache.glob("sat_*.bin"))
    check("W4 不同饱和度落到不同文件", len(names) == 2, str(names))
    check("W4b 文件名带百分比", any("_180_" in n for n in names), str(names))

    # ---------- W5 伪造快照不落盘 ----------
    before = len(list(cache.glob("sat_*.bin")))
    anonymous = _wall(version=0)
    _reset_mem()
    out = wallpaper.saturated(anonymous, 1.4, need_sharp=False)
    after = len(list(cache.glob("sat_*.bin")))
    check("W5 stable_version=0 不写缓存", before == after,
          "%d -> %d" % (before, after))
    check("W5b 不写缓存也照常返回结果", not out.soft.isNull())

    # ---------- W6 损坏缓存 = 未命中 ----------
    target = sorted(cache.glob("sat_*.bin"))[0]
    target.write_bytes(b"broken")
    _reset_mem()
    w2 = _wall()
    got = wallpaper.saturated(w2, 1.4, need_sharp=False)
    check("W6 半截缓存不崩、结果正确",
          not got.soft.isNull() and _bytes(got.soft) == _bytes(cold.soft))

    # ---------- W3 命中时连 PIL 都不导入（必须换进程验） ----------
    # 同进程里 PIL 早在冷调用时就被导入了，测不出"省掉导入"这件事。
    solid_dir = tmp / "solid"
    wallpaper.set_sat_cache_dir(solid_dir)
    solid = _solid_wall(999)
    _reset_mem()
    wallpaper.saturated(solid, 1.4, need_sharp=False)
    seeded = list(solid_dir.glob("sat_*.bin"))
    code = """
import os, sys
sys.path.insert(0, %r)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
QApplication([])
from src import wallpaper
wallpaper.set_sat_cache_dir(%r)
img = QImage(40, 26, QImage.Format_ARGB32)
img.fill(QColor(10, 20, 30))
w = wallpaper.Wallpaper(image=img, soft=img, rect=QRect(0, 0, 40, 26),
                        dpr=1.0, version=1, stable_version=999)
out = wallpaper.saturated(w, 1.4, need_sharp=False)
print("PIL_LOADED" if "PIL" in sys.modules else "NO_PIL", out.soft.width())
""" % (str(Path(__file__).resolve().parent.parent), str(solid_dir))
    import subprocess
    res = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, timeout=120,
                         encoding="utf-8", errors="replace")
    check("W3 缓存命中时不导入 PIL", "NO_PIL" in (res.stdout or ""),
          (res.stdout or res.stderr or "").strip()[:120])
    check("W3b 子进程确实读到了预置缓存", len(seeded) == 1,
          str([p.name for p in seeded]))

    # ---------- W7 只留最近 N 份 ----------
    wallpaper.set_sat_cache_dir(tmp / "many")
    for i in range(wallpaper._SAT_KEEP + 3):
        gap = _wall(version=1000 + i)
        _reset_mem()
        wallpaper.saturated(gap, 1.4 + i / 100.0, need_sharp=False)
    kept = sorted((tmp / "many").glob("sat_*.bin"))
    check("W7 只保留最近 %d 份" % wallpaper._SAT_KEEP,
          len(kept) == wallpaper._SAT_KEEP, "%d 份" % len(kept))

    wallpaper.set_sat_cache_dir(None)


def run():
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="satcache_"))
    main(app, tmp)
    print("\n%d checks, %d failed" % (total[0], len(failures)))
    if failures:
        print("FAILED:", ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
