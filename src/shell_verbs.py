# -*- coding: utf-8 -*-
"""原生右键菜单复刻（v14j 反馈：按文件类型保留原生动词，如 zip→解压全部）。

原理：Windows 资源管理器的"右键菜单"本质是注册表里的一串 shell 动词——

    HKCR\\.zip          -> 默认值 = ProgID（如 CompressedFolder）
    HKCR\\<ProgID>\\shell\\<verb>\\command    （或 \\ShellNew 等特殊键）

每个 verb 的显示名在其默认值（`&Extract All...` 这类，`&` 是助记符），
command 是命令行（`%1` = 文件、`%L`/`"%1"` 同义、`%W` = 工作目录）。本模块
把常用扩展名的动词读出来，转成 QMenu 动作：点击 = 以该文件为参数启动
command（StartProcess，经 shell 语义补齐）。

设计约束：
  * 只读注册表，不执行任何枚举 COM（IContextMenu 全套要开 STA 线程 +
    QueryContextMenu，桌面窗口是 NOACTIVATE 工具窗，风险大于收益——
    注册表动词覆盖了解压/打印/编辑这类高频项）；
  * 内置危险/无意义动词黑名单（delete/rename/runas——我们有更好的本地
    语义；open 单列，避免重复）；
  * command 里的引号/转义按 cmd 规则拆参后 QProcess.startDetached；
    参数含 %1 的替换为目标文件路径（带引号包一层防空格）；
  * DDE 动词（command 键是 `["test.dll",-1,...]` 形态）直接跳过——
    没法可靠地从进程外发起。
"""
from __future__ import annotations

import os
import re

from PySide6.QtCore import QProcess

# 动词黑名单：删除/重命名有本地实现且语义更清楚（拖拽/右键既有项）；
# runas 系弹 UAC 对桌面美化工具太突兀；open 系由「打开」自带。
VERB_DENY = {"open", "openas", "openwithui", "delete", "rename",
             "runas", "runasuser", "printto"}
# 高频扩展名优先（全部扫也行，但菜单里塞 40 个动词没人看）
MAX_VERBS = 6

_VERB_LABEL_FALLBACK = {
    "extractall": "全部解压缩",
    "extract": "解压到当前文件夹",
    "extracthere": "解压到当前文件夹",
    "print": "打印",
    "edit": "编辑",
    "preview": "预览",
    "scan": "扫描",
}


def _progid(ext: str) -> str:
    """HKCR\\.ext 的默认 ProgID（带回退：UserChoice 系统自己会合并）。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ext) as k:
            v = winreg.QueryValueEx(k, "")[0]
            return str(v or "")
    except OSError:
        return ""


def _read_command(key_path: str) -> str:
    """读 shell\\<verb> 的 command（verb 直下或 command 子键）。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key_path) as k:
            try:
                return str(winreg.QueryValueEx(k, "")[0] or "")
            except OSError:
                pass
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            key_path + "\\command") as k:
            try:
                return str(winreg.QueryValueEx(k, "")[0] or "")
            except OSError:
                return ""
    except OSError:
        return ""


def _label(key_path: str, verb: str) -> str:
    """动词显示名：MUIVerb（现代键）→ verb 默认值（去 & 助记符）→ 内置中文表。

    空 label 常见于 `@shell32.dll,-50944` 这类**间接字符串**（MUI 引用），
    进程外解析需要 shlwapi SHLoadIndirectString——做了：解不出就丢该动词
    （显示 "@shell32.dll,-50944" 比没有菜单项更糟）。
    """
    raw = ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key_path) as k:
            try:
                raw = str(winreg.QueryValueEx(k, "MUIVerb")[0] or "")
            except OSError:
                try:
                    raw = str(winreg.QueryValueEx(k, "")[0] or "")
                except OSError:
                    raw = ""
    except OSError:
        raw = ""
    label = raw.replace("&", "").strip() if raw.strip() else ""
    if not label:
        label = _VERB_LABEL_FALLBACK.get(verb.lower(),
                                         verb.replace("&", ""))
    if label.startswith("@"):
        from PySide6.QtCore import QLibrary
        shlwapi = QLibrary("shlwapi.dll")
        try:
            fn = shlwapi.resolve("SHLoadIndirectString")
            if fn is not None:
                import ctypes
                proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_wchar_p,
                                           ctypes.c_wchar_p, ctypes.c_uint,
                                           ctypes.c_void_p)
                load = proto(fn)
                buf = ctypes.create_unicode_buffer(256)
                if load(label, buf, 256, None) == 0 and buf.value:
                    return buf.value.replace("&", "").strip()
        except Exception:        # noqa: BLE001
            pass
        finally:
            try:
                shlwapi.unload()
            except Exception:    # noqa: BLE001
                pass
        return ""                # 解不出 → 调用方丢弃该项
    return label


def _is_dde(cmd: str) -> bool:
    """`["dll",-1,...]` 形态 = DDE，进程外无法可靠发起。"""
    s = (cmd or "").strip()
    return s.startswith('["') or s.startswith("['")


def shell_verbs(path: str, limit: int = MAX_VERBS):
    """某文件的原生菜单动词（label, argv) 列表；不是文件/读不到则空。

    argv 的 %1/%L 已替换为文件路径（带引号），%W 换工作目录，其余 %x
    原样丢弃（没有对应语义）。
    """
    if not path or not os.path.isfile(path):
        return []
    ext = os.path.splitext(path)[1].lower()
    if not ext:
        return []
    progid = _progid(ext)
    out = []
    seen = set()
    roots = [p for p in (progid, ext) if p]
    try:
        import winreg
        for root in roots:
            shell_path = root + "\\shell"
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, shell_path) as k:
                    verbs = []
                    i = 0
                    while True:
                        try:
                            verbs.append(winreg.EnumKey(k, i))
                            i += 1
                        except OSError:
                            break
            except OSError:
                continue
            for verb in verbs:
                v = verb.strip()
                if not v or v.lower() in VERB_DENY or v.lower() in seen:
                    continue
                vp = shell_path + "\\" + verb
                cmd = _read_command(vp)
                if not cmd or _is_dde(cmd):
                    continue
                seen.add(v.lower())
                out.append((vp, _label(vp, v), cmd, v))
            if out:
                break
    except Exception:            # noqa: BLE001 —— 注册表任何意外都返回已有项
        pass
    results = []
    for _vp, label, cmd, _v in out[:max(1, limit)]:
        if not label:            # MUI 间接字符串解不出 → 丢弃（不显示乱码）
            continue
        argv = _tokenize(cmd)
        if not argv:
            continue
        results.append((label, _substitute(argv, path)))
    return [(l, a) for l, a in results if a]


def _tokenize(cmd: str):
    """按 Windows 命令行规则切 argv（引号成组；exe 可能带参数）。

    输入来自注册表 REG_SZ（原始字符串），非 raw 字符串里的反斜杠序列
    没有转义语义——这里是**字符级**扫描，不经过 Python 字面量解释。
    """
    args, cur, in_q = [], "", False
    for ch in cmd:
        if ch == '"':
            in_q = not in_q
            continue
        if ch == " " and not in_q:
            if cur:
                args.append(cur)
                cur = ""
            continue
        cur += ch
    if cur:
        args.append(cur)
    return args


def _substitute(argv, path: str):
    """%1/%L -> 文件；%W -> 目录；其余 %x 丢弃。返回替换后的 argv。"""
    quoted = '"%s"' % path
    workdir = os.path.dirname(path)
    out = []
    for a in argv:
        if re.search(r"%1|%L|%l", a):
            out.append(a.replace("%1", quoted).replace("%L", quoted)
                        .replace("%l", quoted))
        elif "%W" in a or "%w" in a:
            out.append(a.replace("%W", workdir).replace("%w", workdir))
        elif re.search(r"%\d", a):
            continue
        else:
            out.append(a)
    return out


def run_verb(argv) -> bool:
    """以文件为参数启动一个动词命令（不阻塞、失败静默返回 False）。"""
    if not argv:
        return False
    try:
        # exe 与参数分开传；工作目录用首参所在盘（命令行里一般是程序路径）
        QProcess.startDetached(argv[0], argv[1:])
        return True
    except Exception:            # noqa: BLE001
        return False
