# -*- coding: utf-8 -*-
"""v14j：原生右键菜单复刻（shell_verbs）——注册表动词 → 菜单动作。

  S1 tokenize/substitute：引号成组、%1 带引号替换、%W 目录、其余 %x 丢弃
  S2 黑名单：open/runas 系不出现；DDE 动作跳过
  S3 MUI 间接字符串：SHLoadIndirectString 解出显示名（本机 exefile 探针）
  S4 非文件/无扩展名 → 空
  S5-S8 v14m COM 镜像：native_menu 出原生树（zip 含解压项）/
         extract_here 真解压 / 节点结构 / COM 失败回空

运行：python tests/test_shell_verbs.py   （offscreen，只读注册表）
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication   # noqa: E402

from src import shell_verbs   # noqa: E402

failures = []
total = [0]


def check(name, cond, extra=""):
    total[0] += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def main(app):
    # S1
    argv = shell_verbs._tokenize('"C:\Program Files\App\a.exe" /x "%1"')
    check("S1 引号成组", len(argv) == 3 and argv[0] == "C:\Program Files\App\a.exe",
          str(argv))
    sub = shell_verbs._substitute(["prog", "%1", "-o%W", "%2"],
                                  r"C:\d\my file.zip")
    check("S1b %1 带引号、%W 目录、%2 丢弃",
          sub == ["prog", '"C:\d\my file.zip"', "-oC:\d"],
          str(sub))

    # S2 本机 exefile：open/runas 系全在黑名单 → notepad.exe 应无动词
    notepad = r"C:\Windows\notepad.exe" if os.path.isfile(
        r"C:\Windows\notepad.exe") else None
    if notepad:
        check("S2 黑名单动词不出现", shell_verbs.shell_verbs(notepad) == [],
              str(shell_verbs.shell_verbs(notepad)))

    # S3 MUI 解引用链路（exefile\shell\open 的默认值就是 @shell32.dll,-50944）
    label = shell_verbs._label("exefile\shell\openwithui", "openwithui")
    check("S3 MUI 解出中文显示名或空", label == "" or not label.startswith("@"),
          str(label))

    # S4 边界
    check("S4 非文件空", shell_verbs.shell_verbs(r"C:\no\such\file.zip") == [])
    check("S4b 无扩展名空",
          shell_verbs.shell_verbs(os.path.join(os.environ.get("WINDIR",
                                                              r"C:\Windows"),
                                               "System32")) == [])


    # ---------- S5-S8 v14m COM 镜像与解压 ----------
    import zipfile as _zf
    zpath = os.path.join(tempfile.gettempdir(), "test_shell_verbs.zip")
    with _zf.ZipFile(zpath, "w") as z:
        z.writestr("inner.txt", "hello")
    out_dir = os.path.join(tempfile.gettempdir(), "test_shell_verbs_out")
    import shutil as _sh
    _sh.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)

    nodes, invoker = shell_verbs.native_menu(zpath)
    check("S5 native_menu 出节点", bool(nodes), "%d nodes" % len(nodes))
    if nodes:
        def _flat(ns):
            for n in ns:
                yield n["label"]
                if n["children"]:
                    yield from _flat(n["children"])
        labels = list(_flat(nodes))
        check("S6 zip 菜单含解压类项（原生处理器镜像）",
              any("解压" in l or "Extract" in l for l in labels),
              str(labels[:8]))
        strip = lambda l: l.split("(")[0].strip()
        check("S6b 含系统动词（剪切/复制/属性）",
              any(strip(l) in ("剪切", "复制", "属性") for l in labels))
        check("S6c invoker 可调用", callable(invoker))
    # v14n：extract_here 只发起（CopyHere 异步），完成检测交给 watch
    ok = shell_verbs.extract_here(zpath, out_dir)
    got = {"done": None}
    shell_verbs.watch_extract(out_dir, [], lambda d: got.update(done=d),
                              timeout_s=8.0)
    deadline = __import__("time").time() + 10
    while got["done"] is None and __import__("time").time() < deadline:
        app.processEvents()
        __import__("time").sleep(0.05)
    check("S7 extract_here 真解压",
          ok and got["done"] is True
          and os.path.isfile(os.path.join(out_dir, "inner.txt")),
          str(got))
    _sh.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    # COM 失败路径：不存在文件回空
    n2, iv2 = shell_verbs.native_menu(r"C:\no\such\file.zip")
    check("S8 非文件 -> 空树", n2 == [] and iv2 is None)

    print()
    if failures:
        print("==== %d failed: %s" % (len(failures), failures))
        return 1
    print("==== ALL shell_verbs PASSED")
    return 0


if __name__ == "__main__":
    _app = QApplication.instance() or QApplication(sys.argv)
    rc = main(_app)
    print("%d/%d checks" % (total[0] - len(failures), total[0]))
    sys.exit(rc)
