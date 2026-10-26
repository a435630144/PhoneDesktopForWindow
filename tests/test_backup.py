# -*- coding: utf-8 -*-
"""v14x / 8.3.1：配置备份导出与导入（src/backup.py + Storage.import_state）。

背景：state.json 是全程序唯一真相，但过去只能自己去
%APPDATA%\\PhoneDesktop\\ 里翻文件。本批加「导出配置 / 导入配置」，
判据是**往返逐字段相等**与**坏包不动现有配置**。

  B1  导出：包结构（kind/format/counts/state）与 UTF-8 可读
  B2  往返：导出 → 导入到另一台"机器" → settings/layout/widgets 逐字段相等
  B3  裸 state.json 也能被 recognise（不给外层包装）
  B4  坏 JSON -> not_json
  B5  不是本程序的包 -> not_backup
  B6  更高 format -> format_newer（提示先升级）
  B7  缺 settings/layout -> bad_state
  B8  超大文件 -> too_large（不读进内存）
  B9  导入前留档 state.json.pre-import-*
  B10 导入走与启动相同的清洗路径（垃圾条目被剔除）
  B11 导出原子写：目录里不留 .tmp
  B12 校验失败时现有配置一字不动
  B13 词表：backup.* 中英键集合相等且全部有值
  B14 describe() 报数正确（界面确认框的数据源）

运行：python tests/test_backup.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import backup  # noqa: E402
from src import i18n  # noqa: E402
from src.storage import Storage  # noqa: E402

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


RAW = {
    "settings": {
        "schema_version": 2,
        "grid_cols": 7,
        "grid_rows": 4,
        "liquid_glass": True,
        "liquid_strength": 61,
        "lang": "zh_CN",
        "takeover_desktop": True,
        "takeover_v7_forced": True,
    },
    "layout": [
        {"id": "a1", "type": "app", "col": 0, "row": 0,
         "app": {"name": "Alpha", "kind": "app", "path": "C:/x/a.exe"}},
        {"id": "f1", "type": "folder", "col": 1, "row": 0, "app": {},
         "apps": [{"id": "a2", "name": "Beta", "kind": "app",
                   "path": "C:/x/b.exe"}]},
    ],
    "widgets": [
        {"id": "w-clock", "type": "clock", "x": 100, "y": 80, "w": 220, "h": 140},
    ],
}


def _machine(dirname, raw=None):
    """造一台"机器"：独立 state.json + Storage。"""
    path = os.path.join(dirname, "state.json")
    if raw is not None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
    return Storage(path=path)


def main():
    tmp = tempfile.mkdtemp(prefix="pdbackup-")
    src_dir = os.path.join(tmp, "A")
    dst_dir = os.path.join(tmp, "B")
    os.makedirs(src_dir)
    os.makedirs(dst_dir)

    # ---------- B1 导出结构 ----------
    st_a = _machine(src_dir, RAW)
    out = os.path.join(tmp, "cfg.pdbak")
    payload = backup.export_file(st_a, out, semver="8.3.1", pkg="14x",
                                 now="2026-10-27 01:00")
    ok_struct = (payload.get("kind") == backup.KIND
                 and payload.get("format") == backup.FORMAT
                 and isinstance(payload.get("state"), dict))
    check("B1 导出包结构", ok_struct, payload.get("counts"))
    with open(out, "r", encoding="utf-8") as f:
        disk = json.load(f)
    check("B1b 落盘内容与返回一致",
          disk["state"]["settings"]["grid_cols"] == 7
          and disk["counts"]["layout"] == 2
          and disk["counts"]["folders"] == 1)

    # ---------- B2 往返逐字段相等 ----------
    st_b = _machine(dst_dir)                     # 全新机器
    got = backup.read_file(out)
    res = st_b.import_state(got["state"])
    same_layout = [e["id"] for e in st_a.layout] == [e["id"] for e in st_b.layout]
    same_set = all(st_a.get_setting(k) == st_b.get_setting(k)
                   for k in ("grid_cols", "grid_rows", "liquid_glass",
                             "liquid_strength", "lang"))
    same_widget = [e["id"] for e in st_a.widgets] == [e["id"] for e in st_b.widgets]
    check("B2 往返 layout 一致", same_layout,
          "%s vs %s" % ([e["id"] for e in st_a.layout],
                        [e["id"] for e in st_b.layout]))
    check("B2b 往返 settings 一致", same_set,
          "cols=%s" % st_b.get_setting("grid_cols"))
    check("B2c 往返 widgets 一致", same_widget)
    check("B2d 返回值报数", res["layout"] == 2 and res["widgets"] >= 1, res)

    # ---------- B3 裸 state.json ----------
    naked = os.path.join(tmp, "naked.json")
    with open(naked, "w", encoding="utf-8") as f:
        json.dump(RAW, f, ensure_ascii=False)
    nk = backup.read_file(naked)
    check("B3 裸 state.json 可识别",
          nk["kind"] == backup.KIND and nk["counts"]["layout"] == 2)

    # ---------- B4~B7 错误码 ----------
    def code_of(path):
        try:
            backup.read_file(path)
            return ""
        except backup.BackupError as exc:
            return exc.code
        except Exception:        # noqa: BLE001 —— 别的异常就是 bug
            return "WRONG-EXC"

    p = os.path.join(tmp, "bad.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write("{not json,,,")
    check("B4 坏 JSON -> not_json", code_of(p) == "not_json", code_of(p))

    with open(p, "w", encoding="utf-8") as f:
        json.dump({"hello": 1}, f)
    check("B5 别的文件 -> not_backup", code_of(p) == "not_backup", code_of(p))

    with open(p, "w", encoding="utf-8") as f:
        json.dump({"kind": backup.KIND, "format": 99,
                   "state": {"settings": {}, "layout": []}}, f)
    check("B6 未来格式 -> format_newer", code_of(p) == "format_newer", code_of(p))

    with open(p, "w", encoding="utf-8") as f:
        json.dump({"kind": backup.KIND, "format": 1,
                   "state": {"settings": {}}}, f)
    check("B7 缺 layout -> bad_state", code_of(p) == "bad_state", code_of(p))

    # ---------- B8 超大文件 ----------
    big = os.path.join(tmp, "big.pdbak")
    with open(big, "w", encoding="utf-8") as f:
        f.write("x" * 4096)
    old_max = backup.MAX_BYTES
    backup.MAX_BYTES = 1024
    try:
        check("B8 超大 -> too_large", code_of(big) == "too_large", code_of(big))
    finally:
        backup.MAX_BYTES = old_max

    # ---------- B9/B10 留档 + 清洗 ----------
    dirty = dict(RAW)
    dirty["layout"] = RAW["layout"] + ["garbage", 42, None]
    dirty["widgets"] = RAW["widgets"] + ["junk"]
    dirty["settings"] = dict(RAW["settings"], grid_cols=9)
    c_dir = os.path.join(tmp, "C")
    os.makedirs(c_dir)
    st_c = _machine(c_dir, RAW)
    before = open(st_c.path, encoding="utf-8").read()
    res2 = st_c.import_state(dirty)
    check("B9 导入前留档存在",
          bool(res2["backup"]) and os.path.exists(res2["backup"]),
          res2["backup"])
    check("B9b 留档内容 = 导入前的原文",
          bool(res2["backup"])
          and open(res2["backup"], encoding="utf-8").read() == before)
    check("B10 垃圾条目被清洗",
          [e["id"] for e in st_c.layout] == ["a1", "f1"]
          and all(isinstance(e, dict) for e in st_c.widgets),
          [e.get("id") if isinstance(e, dict) else e for e in st_c.layout])

    # ---------- B11 无 .tmp 残留 ----------
    leftovers = [n for n in os.listdir(tmp) if n.endswith(".tmp")]
    check("B11 导出不留临时文件", not leftovers, leftovers)

    # ---------- B12 校验失败不动现有配置 ----------
    d_dir = os.path.join(tmp, "D")
    os.makedirs(d_dir)
    st_d = _machine(d_dir, RAW)
    snap = open(st_d.path, encoding="utf-8").read()
    raised = False
    try:
        st_d.import_state({"layout": []})      # 缺 settings
    except ValueError:
        raised = True
    check("B12 非法导入抛错且文件未动",
          raised and open(st_d.path, encoding="utf-8").read() == snap)

    # ---------- B13 词表 ----------
    zh = json.load(open(str(Path(__file__).resolve().parent.parent
                           / "src/i18n/zh_CN.json"), encoding="utf-8"))
    en = json.load(open(str(Path(__file__).resolve().parent.parent
                           / "src/i18n/en.json"), encoding="utf-8"))
    zh_keys = {k for k in zh if k.startswith("backup.")}
    en_keys = {k for k in en if k.startswith("backup.")}
    missing_val = [k for k in zh_keys if not str(zh.get(k) or "").strip()]
    check("B13 backup.* 中英键集合相等", zh_keys == en_keys,
          "zh=%d en=%d" % (len(zh_keys), len(en_keys)))
    check("B13b backup.* 无空译文", not missing_val, missing_val)
    # 代码里用到的 code 都要有词条
    codes = {"not_json", "not_backup", "format_newer", "bad_state",
             "too_large", "write_failed"}
    check("B13c 错误码词条齐全",
          all(("backup.err.%s" % c) in zh for c in codes),
          [c for c in codes if ("backup.err.%s" % c) not in zh])

    # ---------- B14 describe ----------
    d = backup.describe(backup.read_file(out))
    check("B14 describe 报数",
          d["layout"] == 2 and d["folders"] == 1 and d["semver"] == "8.3.1"
          and d["created_at"] == "2026-10-27 01:00", d)

    # 词表里真能取到文案（不落到 key）
    i18n.set_language("zh_CN")
    t = i18n.T("backup.import.confirm.body", **d)
    check("B14b 确认框文案可格式化", "2" in t and "{" not in t, t[:60])

    # ---------- B15 运行态重载（真窗口，offscreen） ----------
    # 纯逻辑对了不代表界面能换：reload_from_storage 要先拆组件层、再重算
    # 网格、再按新表重建图标。这里用一个真 DesktopWindow 走一遍。
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from PySide6.QtWidgets import QApplication

        from src.desktop_window import DesktopWindow
        from src.widget_layer import WidgetLayer
        app = QApplication.instance() or QApplication([])
        e_dir = os.path.join(tmp, "E")
        os.makedirs(e_dir)
        st_e = _machine(e_dir, RAW)
        win = DesktopWindow(st_e)
        # main.py 装配时才注入组件层；测试里自己装一个，才能验"换表后重建"
        win.set_widget_layer(WidgetLayer(st_e))
        n_before = len(win.icons)
        w_before = len(win._widget_layer.widgets)
        one = {"settings": dict(RAW["settings"]),
               "layout": [RAW["layout"][0]], "widgets": []}
        st_e.import_state(one)
        win.reload_from_storage()
        app.processEvents()
        visible = [w for w in st_e.widgets if not w.get("hidden")]
        check("B15 导入后重载：图标跟新表",
              n_before == 2 and len(win.icons) == 1
              and list(win.icons) == ["a1"],
              "%d -> %d %s" % (n_before, len(win.icons), list(win.icons)))
        check("B15b 组件层按新表重建",
              len(win._widget_layer.widgets) == len(visible),
              "%d -> %d（表内 %d）" % (w_before,
                                       len(win._widget_layer.widgets),
                                       len(visible)))
        win.close()
    except Exception as exc:        # noqa: BLE001 —— 崩了就是 FAIL，不吞
        check("B15 重载不抛异常", False, repr(exc))

    print("\n%d checks, %d failed" % (total[0], len(failures)))
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
