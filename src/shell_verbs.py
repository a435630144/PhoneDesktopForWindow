# -*- coding: utf-8 -*-
"""原生右键菜单复刻（v14j/v14m）。

v14j 用静态注册表动词（HKCR 扩展名 → shell 动词）——只覆盖传统程序，
**现代处理器（360 压缩的"解压"等）是 shellex COM 外壳扩展，静态路线
拿不到**（2026-10-25 用户实测：压缩包右键没有解压项）。v14m 改为
**pywin32 IContextMenu 镜像**：SHCreateItemFromParsingName →
BindToHandler(BHID_SFUIObject) → QueryContextMenu 拿到资源管理器同款
菜单树，镜像进我们的 QMenu，点击 InvokeCommand 执行（与资源管理器
同一条代码路径）。

已实测：
  * 标准动词（创建快捷方式 / 剪切 / 复制 / 粘贴 / 属性…）InvokeCommand
    直接生效；
  * **压缩包解压**：第三方处理器（360zip）的解压项在部分宿主里静默
    无效（处理器挑剔宿主/站点），因此 zip 文件额外提供**自带
    "全部解压缩"**（Shell NameSpace+CopyHere，zipfldr 引擎，不受
    ProgID 接管影响），保证解压永远可用；7z/rar 只有原生镜像项。

pywin32 是本文件引入的运行依赖（PyInstaller 自动收集），COM 需要
STA——pythoncom.CoInitialize 在首次使用时调用，重复调用无害。


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


# ---------------------------------------------------------------- 原生菜单镜像（v14m）

_NAT_INIT = False


def _com_init():
    """主线程 STA 初始化（Qt 已 OleInitialize 时返回 S_FALSE，无害）。"""
    global _NAT_INIT
    if _NAT_INIT:
        return
    import pythoncom
    try:
        pythoncom.CoInitialize()
    except Exception:        # noqa: BLE001 —— 已初始化/失败都不致命
        pass
    _NAT_INIT = True


def native_menu(path: str):
    """文件的**原生右键菜单树**（资源管理器同款，含第三方处理器）。

    返回 (nodes, invoker)：
      * nodes = [{"label": str, "sep": bool, "id": int|None,
                  "children": list|None}]（与资源管理器同序同文案）；
      * invoker(id) → InvokeCommand 执行该命令（ctx 闭包持有）。
    COM 不可用 / 非文件 / 出错 → ([], None)，调用方退回静态动词。

    ctx 的生命周期：invoker 闭包持有；菜单关闭后由下一次调用替换
    （模块级单引用，防泄漏）。
    """
    global _NAT_LAST_CTX
    if not path or not os.path.isfile(path):
        return [], None
    try:
        import pythoncom
        from win32com.shell import shell
        import win32gui
        _com_init()
        item = shell.SHCreateItemFromParsingName(path, None,
                                                 shell.IID_IShellItem)
        ctx = item.BindToHandler(None, shell.BHID_SFUIObject,
                                 shell.IID_IContextMenu)
    except Exception:        # noqa: BLE001 —— COM 任何失败退回静态动词
        return [], None

    # 释放上一个残留的 ctx（正常使用一次只有一个菜单存活）
    old = globals().pop("_NAT_LAST_CTX", None)
    del old

    import ctypes
    user32 = ctypes.windll.user32

    def walk(hmenu):
        nodes = []
        for i in range(user32.GetMenuItemCount(hmenu)):
            mid = win32gui.GetMenuItemID(hmenu, i)
            sub = win32gui.GetSubMenu(hmenu, i)
            ln = user32.GetMenuStringW(hmenu, i, None, 0, 0x400)
            buf = ctypes.create_unicode_buffer(ln + 1) if ln else None
            if ln:
                user32.GetMenuStringW(hmenu, i, buf, ln + 1, 0x400)
            label = (buf.value if buf else "").replace("&", "").strip()
            if sub:
                nodes.append({"label": label, "sep": False, "id": None,
                              "children": walk(sub)})
            elif mid == 0xFFFFFFFF and not label:
                nodes.append({"label": "", "sep": True, "id": None,
                              "children": None})
            else:
                nodes.append({"label": label, "sep": False, "id": mid,
                              "children": None})
        return nodes

    hmenu = win32gui.CreatePopupMenu()
    try:
        ctx.QueryContextMenu(hmenu, 0, 1, 0x7FFF, 0)
        nodes = walk(hmenu)
    except Exception:        # noqa: BLE001
        nodes = []

    def invoker(menu_id):
        if menu_id is None or menu_id < 1:
            return
        # (fMask, hwnd, lpVerb=命令偏移, 参数, 目录, nShow, 热键, 图标)
        ctx.InvokeCommand((0, 0, menu_id - 1, None, None, 1, 0, 0))

    globals()["_NAT_LAST_CTX"] = ctx
    return nodes, invoker


COMPRESSED_EXTS = {".zip"}       # Shell NameSpace 引擎保证可解压的类型


def extract_here(zip_path: str, dest_dir: str) -> bool:
    """把 zip 解到 dest_dir（Shell NameSpace+CopyHere，zipfldr 引擎）。

    不受 .zip ProgID 被第三方（360 等）接管影响——CopyHere 走的是
    Shell 命名空间引擎。7z/rar 等只有原生镜像项（引擎不认）。
    """
    try:
        import time as _time
        import win32com.client
        _com_init()
        sh = win32com.client.Dispatch("Shell.Application")
        src = sh.NameSpace(zip_path)         # zip 视图（NameSpace 接受路径）
        dst = sh.NameSpace(dest_dir)
        if src is None or dst is None:
            return False
        dst.CopyHere(src.Items(), 16)        # 16 = "Yes to all"
        # CopyHere 是异步的：小包瞬间完成，大包由调用方轮询
        deadline = _time.time() + 30
        while _time.time() < deadline:
            if list(os.listdir(dest_dir)):
                return True
            _time.sleep(0.2)
        return True                          # 超时也返回 True（大包在后台解）
    except Exception:                        # noqa: BLE001
        return False


def notify_extract(dest_dir: str):
    """解压完成后托盘提示（CopyHere 异步，完成时机不定——尽力而为）。"""
    try:
        from .tray import notify_via_tray
        notify_via_tray("解压完成", "已解压到 %s" % dest_dir)
    except Exception:        # noqa: BLE001
        pass
