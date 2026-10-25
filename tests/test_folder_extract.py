# -*- coding: utf-8 -*-
"""文件夹成员拖出丢失（2026-10-19 用户反馈）。

病根：文件夹打开期间 `ScrimWindow`（全屏幕布）挂在桌面带最顶，且
**没有实现任何 dragEnter/dropEvent**——从弹窗里把成员往外拖，光标一旦
离开弹窗就落在幕布上，合成 drop 被幕布静默吞掉，`_on_extract` 永不执行，
成员既没回桌面也没留在文件夹里（用户："拖拽出来后不显示"）。

契约：
  E1 幕布接受内部拖放并把 (mime, 全局落点) 转发出去
  E2 经幕布落下：成员进入 layout 且图标实例可见（不留在文件夹里）
  E3 落点即松手所在格（不再一律甩到第一个空格）
  E4 落点被占时退到空格，且不挤走原有图标
  E5 外部文件经幕布落下同样成立（urls 分支）

运行：python tests/test_folder_extract.py   （offscreen）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QMimeData, QPoint, Qt, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from src import grid_flow
from src.desktop_window import DesktopWindow
from src.folder_popup import ScrimWindow
from src.iconwidget import MIME_TYPE
from src.storage import Storage

STATE = Path(tempfile.gettempdir()) / "test_folder_extract_state.json"

failures = []


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def settle(app, ms=250):
    end = QTimer()
    end.setSingleShot(True)
    loop = QEventLoop()
    end.timeout.connect(loop.quit)
    end.start(ms)
    loop.exec()


def make_state():
    if STATE.exists():
        STATE.unlink()
    st = Storage(path=str(STATE))
    st._data["settings"].update({"hide_native_icons": False,
                                 "auto_clone_desktop": False,
                                 "takeover_desktop": True})
    st._data["widgets"] = []
    st._data["layout"] = [
        {"id": "a", "type": "app", "apps": [],
         "app": {"id": "a", "name": "A", "target": "a", "kind": "app"},
         "col": 0, "row": 0},
        {"id": "b", "type": "app", "apps": [],
         "app": {"id": "b", "name": "B", "target": "b", "kind": "app"},
         "col": 1, "row": 0},
        {"id": "f", "type": "folder",
         "app": {"id": "f", "name": "F", "kind": "folder"},
         "apps": [{"id": "m1", "name": "M1", "target": "m1", "kind": "app"},
                  {"id": "m2", "name": "M2", "target": "m2", "kind": "app"},
                  {"id": "m3", "name": "M3", "target": "m3", "kind": "app"}],
         "col": 4, "row": 2},
    ]
    st.save()
    return st


def mime_for(payload: str) -> QMimeData:
    md = QMimeData()
    md.setData(MIME_TYPE, payload.encode())
    return md


def drop_on(widget, md, gpos: QPoint):
    """把一次完整拖放（enter -> drop）打到指定部件上。"""
    local = widget.mapFromGlobal(gpos)
    enter = QDragEnterEvent(local, Qt.MoveAction, md, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(widget, enter)
    drop = QDropEvent(local, Qt.MoveAction, md, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(widget, drop)


def main(app):
    st = make_state()
    grid_flow.set_grid(st, (1920, 1040))
    grid_flow.set_page(0)
    w = DesktopWindow(st)
    w.resize(1920, 1040)
    w._update_grid_metrics()
    w.show()
    settle(app, 200)

    # ---------- E1：幕布转发 ----------
    scrim = ScrimWindow()
    got = {}
    scrim.dropReceived.connect(
        lambda md, gp: got.update(payload=md.data(MIME_TYPE).data().decode(),
                                  pos=(gp.x(), gp.y())))
    scrim.show()
    settle(app, 60)
    md = mime_for("M:f:0")
    drop_on(scrim, md, QPoint(600, 400))
    check("E1 幕布接收内部拖放并转发 (payload, 全局落点)",
          got.get("payload") == "M:f:0" and got.get("pos") == (600, 400),
          "%s" % (got,))
    scrim.close()

    # ---------- E2：经幕布落下，成员进入桌面且图标可见 ----------
    # 这里不手动接线：open_folder 内部必须自己把幕布的落点接到桌面窗口上
    w.open_folder("f")
    settle(app, 200)
    scrim = w._scrim
    check("E2 文件夹打开后幕布已创建", scrim is not None)
    if scrim is not None:
        # 落到一个明确空的格子（第 2 行第 0 列）
        tx, ty = grid_flow.cell_pos(st, 0, 2)
        gpos = w.mapToGlobal(QPoint(tx + 10, ty + 10))
        drop_on(scrim, mime_for("M:f:0"), gpos)
        settle(app, 250)
    entry = st.find_entry("m1")
    check("E2 成员已进入 layout", entry is not None)
    check("E2 成员已从文件夹移除",
          all(m.get("id") != "m1" for m in (st.find_entry("f") or {}).get("apps", [])))
    icon = w.icons.get("m1")
    check("E2 图标实例已创建且可见", icon is not None and not icon.isHidden(),
          "icon=%r hidden=%s" % (bool(icon), icon.isHidden() if icon else None))

    # ---------- E3：落点即松手所在格 ----------
    if entry is not None:
        check("E3 落点 = 松手格（0,2）",
              (int(entry.get("col", -1)), int(entry.get("row", -1))) == (0, 2),
              "got=(%s,%s)" % (entry.get("col"), entry.get("row")))

    # ---------- E5：弹窗稳态铺满整窗（"文件夹样式偶发异常"防线） ----------
    popup = w._popup
    check("E5 提取后弹窗仍打开", popup is not None)
    if popup is not None:
        settle(app, 450)      # 等开合/刷新动画跑完
        check("E5 稳态 zoom 归位到 1.0（不留中间缩放）",
              abs(float(popup._zoom) - 1.0) < 1e-6, "zoom=%s" % popup._zoom)
        img = popup.grab().toImage()
        mid = img.height() // 2
        la = img.pixelColor(3, mid).alpha()
        ra = img.pixelColor(img.width() - 4, mid).alpha()
        check("E5 玻璃面板覆盖整窗宽度（左右边缘均有内容）",
              la > 8 and ra > 8, "left=%d right=%d w=%d" % (la, ra, img.width()))

    # ---------- E4：落点被占时退空格，不挤走原有图标 ----------
    before_ab = {eid: (st.find_entry(eid)["col"], st.find_entry(eid)["row"])
                 for eid in ("a", "b")}
    ax, ay = grid_flow.cell_pos(st, 0, 0)
    w._on_extract("f:0", QPoint(ax + 5, ay + 5))   # 目标是 a 的格子
    settle(app, 150)
    got_m2 = st.find_entry("m2")
    occupied = {(int(e["col"]), int(e["row"])) for e in st.layout
                if e.get("id") not in ("m2",)}
    check("E4 落点被占时改落空格（不与既有图标同格）",
          got_m2 is not None
          and (int(got_m2["col"]), int(got_m2["row"])) not in occupied,
          "m2=(%s,%s)" % ((got_m2 or {}).get("col"), (got_m2 or {}).get("row")))
    check("E4 原有图标位置不变（提取不挤走无关图标）",
          all((st.find_entry(eid)["col"], st.find_entry(eid)["row"]) == before_ab[eid]
              for eid in ("a", "b")),
          "%s" % ([(eid, st.find_entry(eid)["col"], st.find_entry(eid)["row"])
                   for eid in ("a", "b")],))

    # ---------- E6：反馈 10——提取到只剩 1 个成员 → 解散 + 弹窗关闭 ----------
    # v14p：FrameDriver 按真实墙钟播收起动画（~200ms；旧 Qt 动画在
    # offscreen 会瞬完，等 150ms 就够——现在按动画时长等）
    settle(app, 450)
    check("E6 只剩 1 个成员时文件夹自动解除", st.find_entry("f") is None,
          "f=%s" % (st.find_entry("f"),))
    check("E6 弹窗随解散自动关闭", w._popup is None)
    check("E6 最后一个成员弹回桌面", st.find_entry("m3") is not None,
          "layout ids=%s" % [e.get("id") for e in st.layout])

    for icon in list(w.icons.values()):
        icon.close()
    w.close()

    print("-" * 56)
    if failures:
        print("FAILED %d: %s" % (len(failures), failures))
        return 1
    print("ALL folder_extract PASSED")
    return 0


def run_main():
    app = QApplication([])
    wd = QTimer()
    wd.setSingleShot(True)
    wd.timeout.connect(lambda: (print("!! 看门狗超时"), os._exit(3)))
    wd.start(60000)
    try:
        code = main(app)
    except Exception:
        import traceback
        traceback.print_exc()
        failures.append("main() 异常")
        code = 1
    return code


if __name__ == "__main__":
    sys.exit(run_main())
