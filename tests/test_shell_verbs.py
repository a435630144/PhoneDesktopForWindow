# -*- coding: utf-8 -*-
"""v14j：原生右键菜单复刻（shell_verbs）——注册表动词 → 菜单动作。

  S1 tokenize/substitute：引号成组、%1 带引号替换、%W 目录、其余 %x 丢弃
  S2 黑名单：open/runas 系不出现；DDE 动作跳过
  S3 MUI 间接字符串：SHLoadIndirectString 解出显示名（本机 exefile 探针）
  S4 非文件/无扩展名 → 空

运行：python tests/test_shell_verbs.py   （offscreen，只读注册表）
"""
import os
import sys
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
