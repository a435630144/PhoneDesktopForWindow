# -*- coding: utf-8 -*-
"""应用来源：内置系统入口 + 开始菜单扫描 + 桌面文件克隆。

桌面文件克隆（df: 前缀 id）：
  原生桌面图标被隐藏后，由本模块枚举 用户桌面 + 公共桌面 的全部条目，
  生成带稳定 id（路径哈希）的克隆条目，第三方桌面完全掌控其摆放与样式。
  条目不删除文件：移除克隆仅影响布局，文件始终留在桌面文件夹中。
"""
import ctypes
import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from PySide6.QtCore import QFileInfo, QSize, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import QFileIconProvider

# Shell 已知目录 GUID（SHGetKnownFolderPath，正确处理 OneDrive 重定向 / 本地化）
_KNOWN_FOLDER_GUIDS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "public_desktop": "{C4AA340D-F20F-4863-AFEF-F87EF2E6BA25}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


def shell_known_folder(name: str) -> str:
    """通过 Shell API 取系统已知目录；失败返回空串。"""
    guid_str = _KNOWN_FOLDER_GUIDS.get(name)
    if not guid_str:
        return ""
    try:
        guid = _GUID()
        # uuid.bytes_le 与 Windows GUID 内存布局一致（Data1/2/3 小端 + Data4 原样）
        ctypes.memmove(ctypes.byref(guid), uuid.UUID(guid_str).bytes_le, 16)
        path_ptr = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(path_ptr))
        if hr == 0 and path_ptr.value:
            return path_ptr.value
    except Exception:
        pass
    return ""

# 内置系统入口：target 为可启动字符串（命令 / 协议 / shell CLSID）
BUILTIN_APPS = [
    {"id": "sys-pc",       "name": "此电脑",     "target": "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}", "builtin": True},
    {"id": "sys-recycle",  "name": "回收站",     "target": "::{645FF040-5081-101B-9F08-00AA002F954E}", "builtin": True},
    {"id": "sys-explorer", "name": "资源管理器", "target": "explorer", "icon_path": "%SystemRoot%\\explorer.exe", "builtin": True},
    {"id": "sys-control",  "name": "控制面板",   "target": "control", "icon_path": "%SystemRoot%\\System32\\control.exe", "builtin": True},
    {"id": "sys-settings", "name": "Windows 设置", "target": "ms-settings:", "icon_path": "%SystemRoot%\\ImmersiveControlPanel\\SystemSettings.exe", "builtin": True},
    {"id": "sys-notepad",  "name": "记事本",     "target": "notepad", "icon_path": "%SystemRoot%\\System32\\notepad.exe", "builtin": True},
    {"id": "sys-calc",     "name": "计算器",     "target": "calc", "icon_path": "%SystemRoot%\\System32\\calc.exe", "builtin": True},
    {"id": "sys-mspaint",  "name": "画图",       "target": "mspaint", "icon_path": "%SystemRoot%\\System32\\mspaint.exe", "builtin": True},
    {"id": "sys-cmd",      "name": "命令提示符", "target": "cmd", "icon_path": "%SystemRoot%\\System32\\cmd.exe", "builtin": True},
    {"id": "sys-edge",     "name": "Edge 浏览器", "target": "microsoft-edge:", "icon_path": "%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe", "builtin": True},
]

_SKIP_KEYWORDS = ("卸载", "uninstall", "帮助", "help", "readme", "说明文档")


def _expand(p: str) -> str:
    return os.path.expandvars(p) if "%" in p else p


def _desktop_folders() -> list:
    """桌面目录：Shell 已知目录 API 优先（正确处理 OneDrive 重定向 / 本地化）。

    Shell 解析成功时**不再**混入 home/Desktop 等兜底目录：OneDrive 接管桌面后
    C:\\Users\\xxx\\Desktop 是残留空壳，原生桌面并不显示它——混入会产生重复
    条目（2026-10-18 用户反馈"加载的文件不完整/有重复"）。仅当 Shell 整体
    失败时才退回中英文目录名兜底。
    """
    dirs, seen = [], set()

    def _add(p):
        if not p:
            return
        try:
            d = Path(p)
            if not d.exists():
                return
        except Exception:
            return
        k = str(d).lower()
        if k not in seen:
            seen.add(k)
            dirs.append(d)

    for name in ("desktop", "public_desktop"):
        _add(shell_known_folder(name))
    if not dirs:  # Shell 全失败才兜底
        home = Path.home()
        pub = os.environ.get("PUBLIC", r"C:\Users\Public")
        for d in (home / "Desktop", home / "桌面", Path(pub) / "Desktop"):
            _add(d)
    return dirs


def _file_id(path: str) -> str:
    """桌面文件稳定 id：路径小写哈希（同一文件永远同一 id，重扫不重复）。"""
    return "df:" + hashlib.md5(path.lower().encode("utf-8")).hexdigest()[:12]


def desktop_file_entry(path) -> dict:
    """把桌面文件 / 文件夹 / 快捷方式转换成布局条目用的 app 字典。"""
    p = str(path)
    name = Path(p).stem or Path(p).name
    return {"id": _file_id(p), "name": name, "target": p, "icon_path": p, "kind": "file"}


def scan_desktop_files() -> list:
    """枚举桌面 + 公共桌面全部条目：文件夹优先，其余按名称排序。"""
    out, seen = [], set()
    for root in _desktop_folders():
        try:
            items = list(root.iterdir())
        except Exception:
            continue
        items.sort(key=lambda x: (not x.is_dir(), x.name.lower()))
        for it in items:
            if it.name.startswith("$") or it.name.lower() == "desktop.ini":
                continue
            key = str(it).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(desktop_file_entry(it))
    return out


def on_desktop(path: str) -> bool:
    """路径是否已经位于某个桌面目录下。"""
    p = Path(path).resolve() if os.path.exists(path) else Path(path)
    return any(_is_under(p, d) for d in _desktop_folders())


def _is_under(p: Path, folder: Path) -> bool:
    try:
        p.resolve().relative_to(folder.resolve())
        return True
    except Exception:
        return False


def move_to_desktop(path: str) -> str:
    """把任意文件移入用户桌面（重名自动加序号），返回新路径。"""
    dest_dir = _desktop_folders()[0] if _desktop_folders() else Path.home() / "Desktop"
    src = Path(path)
    dest = dest_dir / src.name
    n = 2
    while dest.exists():
        dest = dest_dir / "%s (%d)%s" % (src.stem, n, src.suffix)
        n += 1
    shutil.move(str(src), str(dest))
    return str(dest)


def _is_command_target(target: str) -> bool:
    """target 是不是「没有文件位置」的壳层入口（命令 / 协议 / CLSID）。

    这类条目（`ms-settings:`、`explorer`、`::{GUID}`、`steam://…`）本来就
    不对应磁盘上的文件，硬要 `explorer /select,"ms-settings:"` 只会让资源
    管理器打不开目标、退到默认位置（= 文档库），用户看到的就是"路径指向
    了电脑文档"。
    """
    t = (target or "").strip()
    if not t:
        return True
    if t.startswith("::{"):                    # shell 命名空间（此电脑 / 回收站）
        return True
    if "://" in t:                              # 协议入口（steam:// / https://）
        return True
    if ":" in t and t[1:3] != ":\\":             # ms-settings: / shell: / edge:
        head = t.split(":", 1)[0]
        if (head and not head.startswith("\\") and "/" not in head
                and len(head) > 1 and len(head) <= 24):
            return True                          # 单字母是盘符（C:foo），不算
    if os.sep not in t and "/" not in t:        # 裸命令（explorer / control / notepad）
        return True
    return False


def reveal_path_for(app: dict):
    """右键「在文件夹中显示」应该定位到的路径（None = 没有文件位置）。

    用户诉求（反馈 6）：三条路径规则——
      1. 普通文件 → 文件自己（定位到它所在文件夹并选中）；
      2. 快捷方式（.lnk）→ **原文件**（解析 .lnk 目标，指到 exe 所在目录）；
      3. 本来就在桌面上的文件 → 桌面文件夹（上面两条已经覆盖：桌面文件的
         target 就在桌面目录下）。
    `.url` 的真实目标常是协议（steam:// / http://），没有文件夹可定位，
    退到 .url 文件自己。坏链接同样退到 .lnk 自己——至少让用户看见它。
    """
    target = str(app.get("target") or "")
    if not target or _is_command_target(target):
        return None
    p = _expand(target)
    if p.lower().endswith(".lnk"):
        tgt = shortcut_target_path(app)
        return _expand(tgt) if tgt else p
    return p


def _select_in_folder(folder: str, name: str) -> bool:
    """在资源管理器里打开 folder 并选中 name（SHOpenFolderAndSelectItems）。

    不走 `explorer /select,"…"` 命令行：那条路会把参数引号转义后塞给
    explorer，路径解析失败时资源管理器不报错、直接打开默认位置（文档库），
    表现为"所有路径都指向电脑文档"。Shell API 没有这层字符串解析，选中态
    由壳层自己算。
    """
    try:
        import ctypes.wintypes as wintypes
        shell32 = ctypes.windll.shell32
        shell32.ILCreateFromPathW.restype = wintypes.HANDLE
        shell32.ILCreateFromPathW.argtypes = [wintypes.LPCWSTR]
        shell32.SHOpenFolderAndSelectItems.argtypes = [
            wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p, wintypes.ULONG]
        shell32.ILFree.argtypes = [wintypes.HANDLE]
        full = os.path.join(folder, name)
        pidl = shell32.ILCreateFromPathW(full)
        if not pidl:
            return False
        try:
            return int(shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)) == 0
        finally:
            shell32.ILFree(pidl)
    except Exception:
        return False


def _open_folder(path: str) -> bool:
    """打开一个目录（资源管理器窗口）。"""
    try:
        if os.path.isdir(path):
            os.startfile(path)
            return True
    except Exception:
        pass
    return False


def reveal_in_explorer(path: str):
    """在资源管理器中定位文件（选中状态）。

    目录 → 直接打开；文件 → 打开所在文件夹并选中它。文件已经不在（被移走 /
    删除）时退到所在文件夹，文件夹也没了才退到桌面——**绝不**退到文档库。
    返回是否成功派发（调用方据此决定要不要给用户提示）。
    """
    p = _expand(str(path or ""))
    if not p:
        return False
    if os.path.isdir(p):
        return _open_folder(p)
    parent = os.path.dirname(p)
    if not os.path.isdir(parent):
        folders = _desktop_folders()
        parent = folders[0] if folders else os.path.expanduser("~")
    if _select_in_folder(parent, os.path.basename(p)):
        return True
    return _open_folder(parent)


def create_on_desktop(name: str, is_dir: bool = False) -> str:
    """在桌面目录创建文件夹 / 空文件（重名自动加序号），返回新路径。

    路线A 桌面右键菜单的「新建文件夹 / 新建文本文档」后端：
    原生右键被我们接管后，这两项必须真实可用（真创建 + 通知 shell
    刷新），否则用户会以为功能坏了。
    """
    folders = _desktop_folders()
    if not folders:
        raise RuntimeError("找不到桌面目录")
    dest_dir = folders[0]
    target = dest_dir / name
    n = 2
    while target.exists():
        if is_dir:
            target = dest_dir / "%s (%d)" % (name, n)
        else:
            target = dest_dir / "%s (%d)%s" % (Path(name).stem, n, Path(name).suffix)
        n += 1
    if is_dir:
        target.mkdir(parents=True)
    else:
        target.write_text("", encoding="utf-8")
    _notify_shell_change(str(target))
    return str(target)


def _notify_shell_change(path: str):
    """SHChangeNotify 通知 shell 刷新（原生图标层还可见时立即生效）。"""
    try:
        import ctypes
        from ctypes import wintypes
        shell32 = ctypes.windll.shell32
        SHCNE_CREATE = 0x00000002
        SHCNF_PATHW = 0x0005
        shell32.SHChangeNotify.argtypes = [wintypes.LONG, wintypes.UINT,
                                            wintypes.LPCWSTR, wintypes.LPCWSTR]
        shell32.SHChangeNotify(SHCNE_CREATE, SHCNF_PATHW, path, None)
    except Exception:
        pass


def _start_menu_dirs():
    dirs = []
    appdata = os.environ.get("APPDATA", "")
    programdata = os.environ.get("PROGRAMDATA", "")
    if appdata:
        dirs.append(Path(appdata) / "Microsoft/Windows/Start Menu/Programs")
    if programdata:
        dirs.append(Path(programdata) / "Microsoft/Windows/Start Menu/Programs")
    return [d for d in dirs if d.exists()]


def _desktop_dirs():
    home = Path.home()
    pub = os.environ.get("PUBLIC", r"C:\Users\Public")
    return [d for d in (home / "Desktop", home / "桌面", Path(pub) / "Desktop") if d.exists()]


def scan_installed_apps(limit: int = 300) -> list:
    """扫描开始菜单快捷方式 + 桌面文件，返回应用字典列表。"""
    apps, seen = [], set()
    for root in _start_menu_dirs():
        for lnk in sorted(root.rglob("*.lnk")):
            if len(apps) >= limit:
                break
            name = lnk.stem
            low = name.lower()
            if any(k in low for k in _SKIP_KEYWORDS):
                continue
            if name in seen:
                continue
            seen.add(name)
            apps.append({"id": "sm:" + str(lnk), "name": name,
                         "target": str(lnk), "icon_path": str(lnk)})
    for root in _desktop_dirs():
        try:
            files = sorted(root.iterdir())
        except Exception:
            continue
        for f in files:
            if f.suffix.lower() not in (".lnk", ".exe", ".url", ".bat"):
                continue
            if f.stem in seen:
                continue
            seen.add(f.stem)
            apps.append({"id": "dt:" + str(f), "name": f.stem,
                         "target": str(f), "icon_path": str(f)})
    return apps


def all_apps() -> list:
    """内置 + 扫描的全部应用（添加应用对话框用）。"""
    return [dict(a) for a in BUILTIN_APPS] + scan_installed_apps()


def launch(app: dict):
    """启动应用 / 系统入口（命令、协议、CLSID、.lnk 均可由 ShellExecute 处理）。"""
    target = app.get("target", "")
    if not target:
        return
    try:
        os.startfile(target)
    except Exception:
        pass


_ICON_PROVIDER = QFileIconProvider()
_ICON_CACHE = {}
_PIXMAP_CACHE = {}
_CACHE_MAX = 512               # P2-5：无上限缓存在多尺寸/多壁纸场景会膨胀


def _cache_put(cache: dict, key, value) -> None:
    """插入 + 简易防膨胀（超限清半——键空间有限，够用且无锁）。"""
    if len(cache) >= _CACHE_MAX:
        for k in list(cache.keys())[: len(cache) // 2]:
            cache.pop(k, None)
    cache[key] = value
# 二次缩放缓存（P2，2026-10-26）：draw_icon 对缓存 pixmap 做 contain 缩放
# 时（目标逻辑尺寸≠源逻辑尺寸），hover/脉冲动画每帧都会重跑
# SmoothTransformation。按（源 pixmap cacheKey, 目标尺寸, dpr）缓存结果
# ——pixmap 实例来自 _PIXMAP_CACHE（稳定），cacheKey 即稳定身份。
_RSCALE_CACHE = {}
_RSCALE_CACHE_MAX = 1024

# `icon_path` 指向图片文件时直接加载（见 `_qicon_for`）。含 SVG 是因为
# Windows 11 的 shell 能认，Qt 侧只有装了 imageformats 插件才认——认不了
# 会退回下面的 shell 分支，不会崩。
_IMAGE_SUFFIXES = (".png", ".ico", ".jpg", ".jpeg", ".bmp", ".gif", ".webp",
                   ".svg")

# ---------- 快捷方式徽标（v6 §7.1，反馈 10） ----------
# 壳层把快捷方式箭头 overlay 直接烘焙进 .lnk 图标 pixmap：图标放大后
# 箭头占到边长 ~35% 且带柔和阴影发虚（原生只是左下小角标，见 DEVLOG
# §7.1 规格）。本项目改为：取**目标**的干净图标 + 自绘 25% 锐利徽章。
SHORTCUT_SUFFIXES = (".lnk", ".url")


def is_shortcut(app: dict) -> bool:
    """条目是否是快捷方式（桌面 .lnk/.url，或开始菜单 .lnk）。"""
    p = (app.get("icon_path") or app.get("target") or "").lower()
    return p.endswith(SHORTCUT_SUFFIXES)


def _path_exists(p: str) -> bool:
    try:
        return os.path.exists(p)
    except Exception:
        return False


def shortcut_target_path(app: dict):
    """解析快捷方式的目标路径（目标存在才返回，否则 None）。

    .lnk 走 QFileInfo.symLinkTarget()——Qt 内部即 IShellLink 解析，
    零新增依赖（本机实测 6/6 全部解析到存在的 exe）。失败（坏链接 /
    .url 非 COM 结构 / 目标已删）返回 None，调用方回退壳层 overlay
    图标保持现状。
    """
    p = app.get("icon_path") or app.get("target") or ""
    if not p.lower().endswith(".lnk"):
        return None
    try:
        tgt = QFileInfo(p).symLinkTarget()
    except Exception:
        return None
    if tgt and _path_exists(tgt):
        return tgt
    return None


def shortcut_icon_source(app: dict):
    """.url 文件内部 IconFile / IconIndex 指定的图标（返回 (路径, 序号) / None）。

    2026-10-19 用户反馈："雀魂麻将 和 异形工厂2 的图标丢失图像、变成空白"。
    这两个都是 Steam 的 `.url`：
        [InternetShortcut]
        URL=steam://rungameid/1329410
        IconFile=D:\\...\\Steam\\steam\\games\\<hash>.ico
        IconIndex=0
    壳层对 `.url` 只会给一张通用"链接"白页图，看着就像空白图标；真实图标
    写在 IconFile 里（Steam 把游戏图标缓存成 .ico）。这里先解析出来再取。
    IconIndex 不为 0 时 QIcon 无法按序号取，交由调用方回退壳层。
    """
    p = app.get("icon_path") or app.get("target") or ""
    if not p.lower().endswith(".url"):
        return None
    text = _read_shortcut_text(p)
    if not text:
        return None
    icon_file, index = None, 0
    for line in text.splitlines():
        key, sep, val = line.partition("=")
        if not sep:
            continue
        key = key.strip().lower()
        val = val.strip()
        if key == "iconfile":
            icon_file = val
        elif key == "iconindex":
            try:
                index = int(val)
            except ValueError:
                index = 0
    if not icon_file:
        return None
    return (_expand(os.path.expandvars(icon_file)), index)


def _read_shortcut_text(path: str) -> str:
    """读 .url/.lnk 文本：先按 BOM 判 UTF-16，再依次尝试 utf-8 / gbk。"""
    try:
        with open(path, "rb") as f:
            raw = f.read(64 * 1024)
    except OSError:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            return ""
    for enc in ("utf-8-sig", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def _qicon_for(app: dict) -> QIcon:
    """取应用图标 QIcon：shell 路径（含 CLSID）直接交给 QFileIconProvider，
    失败才生成字母占位图标。

    v6 §7.1：快捷方式取**目标**的图标——壳层 .lnk 图标烘焙了超大软箭头
    overlay（见 is_shortcut 注释）；目标解析失败才用 .lnk 本身。
    v6e：`.url` 先看文件内的 IconFile（Steam 游戏图标在那里）。
    v10：`icon_path` 指向**图片文件**时直接 `QIcon(path)` 加载（见下）。
    """
    path = _expand(app.get("icon_path") or app.get("target", ""))
    if not path:
        return QIcon()
    # 图片文件本身就是图标。交给 QFileIconProvider 只会拿到"图片文件类型"
    # 的图标（照片应用那个），图标内容全丢——所以必须自己 load。
    # 用途：README 截图用导出好的第三方应用 PNG（tools/demo_apps.py）。
    if path.lower().endswith(_IMAGE_SUFFIXES):
        key = (path,)
        if key not in _ICON_CACHE:
            _cache_put(_ICON_CACHE, key, QIcon(path))
        if not _ICON_CACHE[key].isNull():
            return _ICON_CACHE[key]
        # 图坏了/文件不在：往下走 shell 分支，至少还能拿到文件类型图标
    if is_shortcut(app):
        if path.lower().endswith(".url"):
            spec = shortcut_icon_source(app)
            if spec and spec[1] == 0 and spec[0] and _path_exists(spec[0]):
                # IconFile 本身就是图标资源（Steam 的 .ico）：直接加载能拿到
                # 全部内嵌尺寸（16..256），比绕壳层再缩放清晰得多
                key = (spec[0],)
                if key not in _ICON_CACHE:
                    ic = QIcon(spec[0])
                    _ICON_CACHE[key] = ic
                if not _ICON_CACHE[key].isNull():
                    return _ICON_CACHE[key]
                path = spec[0]
        else:
            tgt = shortcut_target_path(app)
            if tgt:
                path = _expand(tgt)
    key = (path,)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    icon = QIcon()
    try:
        # 不检查 exists()：QFileIconProvider 能解析 ::{GUID} 等 shell 命名空间路径，
        # 此电脑 / 回收站 的真实图标就是这样拿到的（exists() 会把它们误判为字母占位）
        icon = _ICON_PROVIDER.icon(QFileInfo(path))
    except Exception:
        icon = QIcon()
    _cache_put(_ICON_CACHE, key, icon)
    return icon


def icon_for(app: dict, size: int = 48) -> QIcon:
    """取应用图标（QIcon）。优先系统图标，失败时字母占位。"""
    icon = _qicon_for(app)
    if not icon.isNull():
        return icon
    return QIcon(_letter_pixmap(app.get("name", "?")[:1], size))


def _source_pixmap_for(icon: QIcon, target_px: int) -> QPixmap:
    """挑「够用的最小一档」源 pixmap（v6e 清晰度）。

    旧实现永远取 max(sizes)（256 jumbo）再 Smooth 缩到 55px 左右——
    4~5 倍下采样，边缘被插值糊掉，用户反馈"不如原图标清晰"。
    Windows 自己是挑不小于目标、且最接近的那一档（图标为每个尺寸单独
    调过墨量），所以这里按物理宽度升序找第一个 ≥ target×0.8 的档——
    0.8 对应 1.25 倍放大上限（125% 缩放下 Windows 正是这样用 48 档画
    60px）。全都小于目标时才退回最大档。
    """
    sizes = sorted(icon.availableSizes(), key=lambda s: (s.width(), s.height()))
    if not sizes:
        return icon.pixmap(QSize(target_px, target_px))
    floor_px = target_px * 0.8
    fallback = None
    for s in sizes:
        pm = icon.pixmap(s)
        if pm.isNull():
            continue
        fallback = pm
        if pm.width() >= floor_px:
            return pm
    return fallback if fallback is not None else icon.pixmap(QSize(target_px, target_px))


def icon_pixmap(app: dict, logical_size: int, dpr: float = 1.0) -> QPixmap:
    """高清图标 pixmap：逻辑尺寸 = logical_size，物理像素 = logical_size * dpr。

    QIcon.pixmap(size) 在 Windows 上对大图标源会返回带 devicePixelRatio 的
    jumbo（256）pixmap，若调用方按物理宽度计算布局就会错位——这里统一归一化：
    取**够用的最小档**（见 _source_pixmap_for）-> 缩放到目标物理像素 ->
    设置 DPR，绘制时按逻辑尺寸居中即 crisp。
    """
    key = (app.get("icon_path") or app.get("target", ""), app.get("name", ""),
           logical_size, round(dpr, 2))
    if key in _PIXMAP_CACHE:
        return _PIXMAP_CACHE[key]
    dpr = max(dpr, 1.0)
    target_px = max(16, int(round(logical_size * dpr)))
    icon = _qicon_for(app)
    pm = QPixmap()
    if not icon.isNull():
        src = _source_pixmap_for(icon, target_px)
        if not src.isNull():
            if src.width() != target_px:
                src = src.scaled(target_px, target_px,
                                 Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm = QPixmap(src)
            pm.setDevicePixelRatio(dpr)
    if pm.isNull():
        pm = _letter_pixmap(app.get("name", "?")[:1], target_px)
        pm.setDevicePixelRatio(dpr)
    # v6e（用户："左下角好像有快捷方式的箭头，不需要"）：不再自绘任何徽标。
    # 图标就是图标本身——桌面视觉统一，也省掉一层绘制。
    _cache_put(_PIXMAP_CACHE, key, pm)
    return pm


def draw_icon(painter, app: dict, rect, dpr: float = 1.0) -> int:
    """把应用图标按 contain + 居中画进 `rect`（图标绘制**唯一入口**）。

    v7 B5 反馈 13（2026-10-21）：旧绘制点各自算偏移、用 pixmap 的**实际**
    像素尺寸落笔——源图小于请求尺寸时（`KeepAspectRatio` 缩完只有几十
    像素的第三方图标），图标就缩在槽位左上角，肉眼就是"加入文件夹后变得
    非常小"。这里统一三件事：
      1. contain 缩放（小图**强制上采样**，不允许原尺寸落笔）；
      2. 按缩放后的逻辑尺寸居中到 rect（非方形源图也不偏）；
      3. dpr 归一化：pixmap 的 devicePixelRatio 不参与布局计算。

    返回实际绘制的逻辑边长（测试用它断言"请求 56 就真画 ~56"）。
    """
    w, h = int(rect.width()), int(rect.height())
    if w <= 0 or h <= 0:
        return 0
    target = max(1, min(w, h))
    pm = icon_pixmap(app, target, dpr)
    if pm.isNull():
        return 0
    ratio = pm.devicePixelRatio() or 1.0
    lw, lh = pm.width() / ratio, pm.height() / ratio
    if lw <= 0 or lh <= 0:
        return 0
    scale = min(w / lw, h / lh)
    dw, dh = max(1, int(round(lw * scale))), max(1, int(round(lh * scale)))
    if (dw, dh) != (int(round(lw)), int(round(lh))):
        # P2（2026-10-26）：按最终尺寸缓存二次缩放结果——动画帧零缩放开销
        rkey = (pm.cacheKey(), dw, dh, round(dpr, 2))
        cached = _RSCALE_CACHE.get(rkey)
        if cached is None:
            pm = pm.scaled(max(1, int(round(dw * dpr))),
                           max(1, int(round(dh * dpr))),
                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            if len(_RSCALE_CACHE) >= _RSCALE_CACHE_MAX:
                _RSCALE_CACHE.clear()      # 简单防膨胀（键空间本身有限）
            _RSCALE_CACHE[rkey] = pm
        else:
            pm = cached
    x = int(round(rect.x() + (w - dw) / 2.0))
    y = int(round(rect.y() + (h - dh) / 2.0))
    painter.drawPixmap(x, y, pm)
    return max(dw, dh)


def _letter_pixmap(ch: str, size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    hue = (sum(ord(c) for c in ch) * 47) % 360
    p.setBrush(QBrush(QColor.fromHsv(hue, 150, 205)))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(pm.rect(), size // 5, size // 5)
    p.setPen(QColor(255, 255, 255))
    font = QFont()
    font.setPixelSize(int(size * 0.5))
    font.setBold(True)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignCenter, ch)
    p.end()
    return pm
