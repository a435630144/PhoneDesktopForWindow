# -*- coding: utf-8 -*-
"""v15a / 8.3.4：文件夹重命名输入框重做（不再越出弹窗）。

用户反馈（2026-10-27）："文件夹重命名的文本框需要重新设计，现在有点丑，
点击出现的文本框会超出 UI。"

旧实现：`BandInputBox` 固定 240×36，居中在 `_title_rect().center()` 上。
而标题带右侧要给三个按钮让出 86px，靠左的宽度只有 `width - 2*pad - 86`
——240px 的弹窗（最小宽度）标题带仅 126px，居中的 240px 框会左右各顶出
40+px，压在弹窗外面的壁纸上。观感上还是一个"贴在玻璃上的系统输入框"。

契约：
  R1  尺寸跟标题带走：宽 = 标题带宽，高 28~40（不再固定 240×36）
  R2  编辑器全局矩形完全落在弹窗矩形内（四边都在）
  R3  窄弹窗（1 个成员）也不越界，且仍有可用宽度（>=100）
  R4  编辑期间弹窗标题隐藏（不出现"标题 + 输入框"两份文字），提交/取消后恢复
  R5  贴近屏幕边缘时夹进可用屏幕矩形
  R6  观感：文字居中、与标题同族加粗、输入框自身无边框（自绘玻璃面）

运行：python tests/test_rename_band.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.folder_popup import BandInputBox, FolderPopup  # noqa: E402
from src.storage import Storage  # noqa: E402

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def _state(tmp, members):
    p = tmp / ("rename_%d.json" % members)
    if p.exists():
        p.unlink()
    st = Storage(path=str(p))
    st._data["settings"]["hide_native_icons"] = False
    st._data["settings"]["auto_clone_desktop"] = False
    st._data["widgets"] = []
    st._data["layout"] = [
        {"id": "f", "type": "folder", "name": "工作资料文件夹",
         "tint": "#4c8dff", "apps": [
             {"name": "m%d" % i, "target": "m%d" % i, "kind": "app"}
             for i in range(members)], "col": 1, "row": 1}]
    st.save()
    return st


def _popup(tmp, members):
    st = _state(tmp, members)
    return FolderPopup(st, st.find_entry("f"), anchor_rect=None)


def _global_rect(w):
    return QRect(w.mapToGlobal(w.rect().topLeft()), w.size())


def main(app, tmp):
    # ---------- 默认构造仍然可用（旧调用零改动） ----------
    plain = BandInputBox(text="名字", title="重命名")
    check("R0 旧构造签名仍可用", plain.width() > 0 and plain.height() > 0,
          "%dx%d" % (plain.width(), plain.height()))

    # ---------- 4 成员弹窗 ----------
    popup = _popup(tmp, 4)
    popup.show()
    app.processEvents()
    band = popup._title_rect()
    popup._open_rename()
    app.processEvents()
    box = popup._editor
    check("R1 编辑器存在", box is not None)
    if box is None:
        return
    check("R1a 宽 = 标题带宽（不再固定 240）",
          box.width() == max(120, band.width()),
          "w=%d band=%d" % (box.width(), band.width()))
    check("R1b 高在 28~40 之间", 28 <= box.height() <= 40, "h=%d" % box.height())

    pg, bg = _global_rect(popup), _global_rect(box)
    check("R2a 编辑器不越过弹窗左边", bg.left() >= pg.left(),
          "box=%s popup=%s" % (bg, pg))
    check("R2b 编辑器不越过弹窗右边", bg.right() <= pg.right(),
          "box=%s popup=%s" % (bg, pg))
    check("R2c 不越过顶边", bg.top() >= pg.top(), "box=%s" % (bg,))
    check("R2d 不越过底边", bg.bottom() <= pg.bottom(), "box=%s" % (bg,))
    check("R2e 在标题带内（垂直）", bg.center().y() >= pg.top()
          and bg.bottom() <= popup.mapToGlobal(band.bottomRight()).y() + 6,
          "box=%s band=%s" % (bg, band))

    # ---------- R4：标题隐藏 ----------
    check("R4a 编辑中标记 _renaming", getattr(popup, "_renaming", False) is True)
    check("R4b 标题文字在编辑时不可见", popup._title_visible() is False)
    box._commit()
    app.processEvents()
    check("R4c 提交后 _renaming 复位", getattr(popup, "_renaming", True) is False)
    check("R4d 提交后标题恢复", popup._title_visible() is True)
    check("R4e 提交后清掉编辑器引用", popup._editor is None)

    # 取消路径
    popup._open_rename()
    app.processEvents()
    box2 = popup._editor
    box2._cancel()
    app.processEvents()
    check("R4f 取消后 _renaming 复位", getattr(popup, "_renaming", True) is False)
    check("R4g 取消后标题恢复", popup._title_visible() is True)
    popup.close()

    # ---------- R3：窄弹窗（1 个成员） ----------
    p1 = _popup(tmp, 1)
    p1.show()
    app.processEvents()
    p1._open_rename()
    app.processEvents()
    b1 = p1._editor
    g1, gp1 = _global_rect(b1), _global_rect(p1)
    check("R3a 窄弹窗也不越界",
          g1.left() >= gp1.left() and g1.right() <= gp1.right(),
          "box=%s popup=%s" % (g1, gp1))
    check("R3b 仍有可用输入宽度（>=100）", b1.width() >= 100, "w=%d" % b1.width())
    p1.close()

    # ---------- R5：贴屏幕右缘 ----------
    p2 = _popup(tmp, 4)
    p2.show()
    app.processEvents()
    scr = app.primaryScreen().availableGeometry()
    p2.move(scr.right() - p2.width() + 1 - 2, p2.y())
    app.processEvents()
    p2._open_rename()
    app.processEvents()
    b2 = p2._editor
    g2 = _global_rect(b2)
    check("R5 编辑器夹进屏幕可用区",
          scr.contains(g2), "box=%s screen=%s" % (g2, scr))
    p2.close()

    # ---------- R6：观感 ----------
    check("R6a 文字居中", bool(box.line.alignment() & Qt.AlignHCenter),
          "%s" % int(box.line.alignment()))
    check("R6b 与标题同族加粗", box.line.font().bold())
    css = box.line.styleSheet()
    check("R6c 输入框自身无边框（玻璃面自绘）",
          "border: none" in css.replace("border:none", "border: none"),
          css[:80])
    # 选中态：半透明强调色（实心高亮会把名字糊成蓝带）
    hi = box.line.palette().color(QPalette.Highlight)
    check("R6d 选中态 = 半透明主题强调色",
          0 < hi.alpha() < 200 and hi.blue() > 150,
          "rgba(%d,%d,%d,%d)" % (hi.red(), hi.green(), hi.blue(), hi.alpha()))


def run():
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="rename-"))
    try:
        main(app, tmp)
    except Exception:
        import traceback
        traceback.print_exc()
        failures.append("main() 异常")
    print("-" * 56)
    print("%d checks, %d failed" % (total[0], len(failures)))
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())
