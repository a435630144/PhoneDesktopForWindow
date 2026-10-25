# -*- coding: utf-8 -*-
"""外部组件加载器（v9 V3，指导文档_v9.md §4）。

目标：让用户按 `docs/组件开发指南.md` 写一个目录（或打成 zip），在组件库
里一键导入，就和内置组件一样出现在桌面。

设计取舍：
  - **目录即真相**：装了什么看 `程序目录/widgets/` 下有哪些子目录
    （v14 反馈 14：旧位置 %APPDATA% 与安装目录无关，卸载不干净），
    不另存一份"已安装列表"（两份必然漂移）。
  - **不信任插件**：zip 只解白名单后缀、拒绝绝对路径与 `..`（zip-slip）、
    限制体积；加载异常只写日志并跳过，绝不拖垮启动。
  - **不做沙箱**：插件是用户自己放进来的 Python 代码，能做的事和主程序
    一样（没有安全边界，靠"只装你信任的组件"约定）。文档里写明这一点。
  - **命名空间隔离**：插件不能覆盖内置类型（`register_plugin` 会拒绝），
    否则一个 zip 就能改掉时钟的行为。

插件目录结构：
    hello/
      manifest.json      # 见下
      widget.py          # 组件类 + 可选的 draw_preview
      README.md          # 可选

manifest.json（v1）：
    {
      "id": "hello",                 # 只允许 [a-z][a-z0-9_]*；不能与内置撞名
      "name": "示例组件",
      "summary": "一句话说明",
      "category": "示例",
      "spans": [[2, 2], [4, 2]],     # 第一个是默认跨度
      "module": "widget.py",
      "factory": "HelloWidget",       # 组件类名（GlassWidget 子类）
      "anchored": true,
      "interactive": false,
      "multi": false,
      "version": "1.0.0",
      "settings": [{"key": "...", "label": "...", "kind": "text", "default": ""}]
    }
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths
from . import widget_registry as reg
from .widget_registry import FieldSpec, WidgetSpec

MANIFEST = "manifest.json"
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")

# zip 里只允许这些后缀（可执行/脚本之外的东西一律拒绝，减少"解压即中招"面）
ALLOWED_EXT = {".py", ".json", ".md", ".txt", ".png", ".jpg", ".jpeg",
               ".gif", ".svg", ".ico", ".webp"}
MAX_FILE_BYTES = 2 * 1024 * 1024        # 单文件 2MB
MAX_TOTAL_BYTES = 20 * 1024 * 1024      # 整包 20MB

VALID_KINDS = {"text", "combo", "int", "bool", "color", "float"}

# 插件可以 import 的 SDK 子模块（`docs/组件开发指南.md` 列出的就是这些）。
# 只给"画与数据"相关的：不给 desktop_window / win32_layer 这类内部实现。
SDK_MODULES = ("theme", "widget_glass", "widget_style", "widget_base",
               "effects", "grid_flow", "storage", "weather_api",
               "widget_registry")

# v10 C1：weather_api 搬进 src/widgets/weather/ 后，SDK 别名
# `deskorg.weather_api` 的**真实路径**与名字不同——插件看到的名字不变。
SDK_PATHS = {"weather_api": "widgets.weather.weather_api"}


class PluginError(Exception):
    """插件格式/内容不合法（消息直接给用户看）。"""


# ---------------------------------------------------------------- 路径

def plugin_root() -> Path:
    """用户组件目录：**程序目录/widgets**（v14 反馈 14）。

    旧位置是 `%APPDATA%\\PhoneDesktop\\widgets`——与安装目录毫无关系，
    卸载软件时组件还留在系统盘，换盘/迁移也会丢。现在跟着程序走。
    一次性迁移见 `migrate_legacy_plugin_root()`（首次加载前调用）。
    """
    return paths.program_dir() / "widgets"


def legacy_plugin_root() -> Path:
    """改名前的组件目录（只有一次性迁移会用到）。"""
    return paths.data_file("widgets")


def migrate_legacy_plugin_root() -> bool:
    """把 %APPDATA% 下的旧组件目录搬到程序目录（一次性，幂等）。

    规矩与 `paths.migrate_legacy_data` 同源：新目录已有内容就不动；
    先复制后删除；复制失败清掉半成品并回答 False。
    """
    new, old = plugin_root(), legacy_plugin_root()
    if new.exists() or not old.is_dir():
        return False
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(old, new)
    except Exception:
        shutil.rmtree(new, ignore_errors=True)
        return False
    shutil.rmtree(old, ignore_errors=True)
    return True


def error_log() -> Path:
    return paths.data_file("plugin_error.log")


def _log_error(text: str) -> None:
    try:
        error_log().parent.mkdir(parents=True, exist_ok=True)
        # S12：日志轮转——超 256KB 截断（保留后半）
        try:
            log_p = error_log()
            if log_p.exists() and log_p.stat().st_size > 256 * 1024:
                with open(log_p, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                with open(log_p, "w", encoding="utf-8") as f:
                    f.writelines(lines[len(lines) // 2:])
        except Exception:  # noqa: BLE001
            pass
        with open(error_log(), "a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
    except Exception:      # noqa: BLE001 —— 日志失败不该影响主流程
        pass


# ---------------------------------------------------------------- 校验

def _need(cond, msg):
    if not cond:
        raise PluginError(msg)


def parse_manifest(data: dict) -> dict:
    """校验并归一化 manifest（纯数据，不碰文件系统）。返回规整后的 dict。"""
    _need(isinstance(data, dict), "manifest.json 必须是一个 JSON 对象")

    wid = str(data.get("id", "")).strip()
    _need(bool(ID_RE.match(wid)),
          "id 只能是小写字母开头、由 a-z / 0-9 / _ 组成（2~31 位）：%r" % wid)
    _need(not reg.is_builtin(wid), "id %r 与内置组件同名，请换一个" % wid)

    name = str(data.get("name", "")).strip()
    _need(bool(name), "缺少 name（组件显示名）")

    module = str(data.get("module", "widget.py")).strip() or "widget.py"
    _need("/" not in module and "\\" not in module and ".." not in module,
          "module 必须是同目录下的文件名：%r" % module)
    _need(module.endswith(".py"), "module 必须是 .py 文件：%r" % module)

    factory = str(data.get("factory", "")).strip()
    _need(bool(factory), "缺少 factory（组件类名）")

    spans = data.get("spans") or [[2, 2]]
    _need(isinstance(spans, (list, tuple)) and spans, "spans 必须是非空数组")
    norm_spans = []
    for s in spans:
        _need(isinstance(s, (list, tuple)) and len(s) == 2,
              "spans 的每一项必须是 [列, 行]：%r" % (s,))
        try:
            cs, rs = int(s[0]), int(s[1])
        except (TypeError, ValueError):
            raise PluginError("spans 必须是整数：%r" % (s,))
        _need(1 <= cs <= 12 and 1 <= rs <= 12, "spans 每项限制在 1~12：%r" % (s,))
        norm_spans.append((cs, rs))

    settings = []
    raw_settings = data.get("settings") or []
    _need(isinstance(raw_settings, list), "settings 必须是数组")
    for item in raw_settings:
        _need(isinstance(item, dict), "settings 的每一项必须是对象：%r" % (item,))
        key = str(item.get("key", "")).strip()
        _need(bool(key), "settings 缺少 key")
        kind = str(item.get("kind", "text")).strip() or "text"
        _need(kind in VALID_KINDS, "settings[%s] 的 kind 不认识：%r（可用 %s）"
              % (key, kind, "/".join(sorted(VALID_KINDS))))
        choices = item.get("choices") or []
        _need(isinstance(choices, list), "settings[%s].choices 必须是数组" % key)
        norm_choices = []
        for c in choices:
            _need(isinstance(c, (list, tuple)) and len(c) == 2,
                  "settings[%s].choices 每项必须是 [值, 文字]" % key)
            norm_choices.append((c[0], str(c[1])))
        settings.append(FieldSpec(
            key=key,
            label=str(item.get("label", key)),
            kind=kind,
            default=item.get("default", ""),
            choices=tuple(norm_choices),
            placeholder=str(item.get("placeholder", "")),
            hint=str(item.get("hint", "")),
            group=str(item.get("group", "")),
        ))

    return {
        "id": wid,
        "name": name,
        "summary": str(data.get("summary", "")).strip(),
        "category": str(data.get("category", "其他")).strip() or "其他",
        "spans": tuple(norm_spans),
        "module": module,
        "factory": factory,
        "anchored": bool(data.get("anchored", True)),
        "interactive": bool(data.get("interactive", False)),
        "multi": bool(data.get("multi", False)),
        "version": str(data.get("version", "")).strip(),
        "settings": tuple(settings),
    }


# ---------------------------------------------------------------- 导入模块

def ensure_sdk() -> None:
    """把主程序包注册成 `deskorg`，供插件 `from deskorg.widget_glass import ...`。

    为什么需要别名：插件的模块名是动态的（`deskorg_plugin_<id>`），既不能
    相对导入，也不该被要求知道主程序包叫什么（打包后是 `src`，开发时也是
    `src`，但这是实现细节）。给插件一个稳定的 SDK 名字，文档只写这一个。
    """
    pkg = __package__ or "src"
    root = sys.modules.get("deskorg")
    if root is None:
        root = importlib.import_module(pkg)
        sys.modules["deskorg"] = root
    # 子模块也要进 sys.modules：`from deskorg.widget_glass import X` 会去
    # import "deskorg.widget_glass"，不预注册就会按 deskorg.__path__ 重新
    # 加载一份**同名不同对象**的模块 —— issubclass(插件类, GlassWidget)
    # 直接判假（class 对象不是同一个），表现为"明明继承了却说过不是子类"。
    for sub in SDK_MODULES:
        key = "deskorg." + sub
        if key in sys.modules:
            continue
        try:
            mod = importlib.import_module(pkg + "." + SDK_PATHS.get(sub, sub))
        except Exception:      # noqa: BLE001 —— 拿不到就不暴露，别拦住加载
            continue
        sys.modules[key] = mod
        setattr(root, sub, mod)


def _import_module(path: Path, wid: str):
    """按文件路径导入插件模块（模块名带 id 前缀，避免与内置模块撞名）。"""
    ensure_sdk()
    mod_name = "deskorg_plugin_%s" % wid
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    _need(spec is not None and spec.loader is not None,
          "无法加载模块：%s" % path.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module       # dataclass / 相对导入需要它已注册
    spec.loader.exec_module(module)
    return module


def build_spec(folder: Path) -> WidgetSpec:
    """从插件目录构建 `WidgetSpec`（不注册；导入流程与调试器共用）。"""
    folder = Path(folder)
    man_path = folder / MANIFEST
    _need(man_path.is_file(), "缺少 %s" % MANIFEST)
    try:
        raw = json.loads(man_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PluginError("manifest.json 不是合法 JSON：%s" % exc)

    man = parse_manifest(raw)
    mod_path = folder / man["module"]
    _need(mod_path.is_file(), "缺少模块文件 %s" % man["module"])

    try:
        module = _import_module(mod_path, man["id"])
    except PluginError:
        raise
    except Exception as exc:      # noqa: BLE001 —— 任何导入错误都转成可读消息
        raise PluginError("模块导入失败：%s: %s" % (type(exc).__name__, exc))

    factory = getattr(module, man["factory"], None)
    _need(factory is not None, "模块里找不到 factory=%r" % man["factory"])
    _need(callable(factory), "factory=%r 不是可调用的类" % man["factory"])

    from .widget_glass import GlassWidget
    try:
        is_ok = isinstance(factory, type) and issubclass(factory, GlassWidget)
    except TypeError:
        is_ok = False
    _need(is_ok, "factory 必须是 widget_glass.GlassWidget 的子类")

    preview = getattr(module, "draw_preview", None)
    return WidgetSpec(
        type=man["id"],
        name=man["name"],
        summary=man["summary"],
        factory=factory,
        spans=man["spans"],
        category=man["category"],
        default_anchored=man["anchored"],
        interactive=man["interactive"],
        multi_instance=man["multi"],
        settings=man["settings"],
        preview=preview if callable(preview) else None,
        plugin=True,
        version=man["version"],
    )


def load_dir(folder: Path) -> WidgetSpec:
    """加载并注册一个插件目录（失败抛 PluginError，调用方决定是否吞）。"""
    spec = build_spec(Path(folder))
    reg.register_plugin(spec)
    return spec


def load_all() -> List[WidgetSpec]:
    """启动时加载用户组件；坏插件只记日志，不影响其它组件与启动。"""
    migrate_legacy_plugin_root()
    root = plugin_root()
    if not root.is_dir():
        return []
    out: List[WidgetSpec] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            out.append(load_dir(entry))
        except Exception as exc:      # noqa: BLE001
            _log_error("[load] %s: %s\n%s" % (entry.name, exc, traceback.format_exc()))
    return out


# ---------------------------------------------------------------- zip 导入

def _check_zip_members(zf: zipfile.ZipFile) -> List[zipfile.ZipInfo]:
    infos = [i for i in zf.infolist() if not i.is_dir()]
    _need(bool(infos), "压缩包是空的")
    total = 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        _need(not name.startswith("/") and ".." not in name.split("/"),
              "压缩包含非法路径（zip-slip）：%s" % name)
        _need(":" not in name.split("/")[0],
              "压缩包含绝对路径：%s" % name)
        ext = Path(name).suffix.lower()
        _need(ext in ALLOWED_EXT, "压缩包含不支持的文件类型：%s" % name)
        _need(info.file_size <= MAX_FILE_BYTES,
              "文件过大（限 2MB）：%s" % name)
        total += info.file_size
    _need(total <= MAX_TOTAL_BYTES, "压缩包解压后超过 20MB")
    return infos


def import_zip(zip_path) -> WidgetSpec:
    """导入一个组件 zip：解包 -> 校验 -> 注册。返回注册后的 spec。

    先解到临时目录再整体搬到 `widgets/<id>`：解到一半失败不会留下半个
    组件目录（半成品会被下次 `load_all` 当成坏插件反复报错）。
    """
    zip_path = Path(zip_path)
    _need(zip_path.is_file(), "文件不存在：%s" % zip_path)
    root = plugin_root()
    root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        infos = _check_zip_members(zf)
        names = [i.filename.replace("\\", "/") for i in infos]
        # 允许两种打包方式：manifest 在根，或整体套一层目录
        prefix = ""
        if MANIFEST not in names:
            tops = {n.split("/")[0] for n in names if "/" in n}
            if len(tops) == 1:
                cand = tops.pop() + "/"
                if cand + MANIFEST in names:
                    prefix = cand
        _need(prefix + MANIFEST in names,
              "压缩包里没有 %s（应位于根目录或唯一的顶层目录下）" % MANIFEST)

        tmp = Path(tempfile.mkdtemp(prefix="deskorg_widget_"))
        try:
            for info in infos:
                name = info.filename.replace("\\", "/")
                if prefix and not name.startswith(prefix):
                    continue
                rel = name[len(prefix):] if prefix else name
                if not rel:
                    continue
                target = (tmp / rel).resolve()
                _need(str(target).startswith(str(tmp.resolve())),
                      "压缩包含非法路径：%s" % name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())

            spec = build_spec(tmp)          # 先在临时目录校验（不含注册）
            dest = root / spec.type
            if dest.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.move(str(tmp), str(dest))
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)

    reg.register_plugin(spec)
    return spec


def uninstall(wtype: str) -> bool:
    """卸载插件：删目录 + 移出注册表。内置组件拒绝（返回 False）。"""
    if reg.is_builtin(wtype) or not reg.get(wtype):
        return False
    dest = plugin_root() / wtype
    if dest.is_dir():
        shutil.rmtree(dest, ignore_errors=True)
    return reg.unregister(wtype)


def installed_ids() -> List[str]:
    root = plugin_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())
