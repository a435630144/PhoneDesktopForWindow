# -*- coding: utf-8 -*-
"""配置备份 / 导出 / 导入（v14x / 8.3.1）。

## 为什么单独一个模块

`state.json` 是全程序唯一真相（布局 + 设置 + 组件表），但过去只能靠用户
自己去 `%APPDATA%\\PhoneDesktop\\` 翻文件——迁移换机、改坏了想回滚、给
别人抄一份好看的布局，三个高频诉求都没有入口。这里把「打一份包」和
「吃一份包」做成**纯函数层**（不 import Qt），设置页只负责选文件与提示。

## 格式（`.pdbak`，其实就是一个 JSON）

```json
{
  "kind": "phonedesktop.config",
  "format": 1,
  "app": "PhoneDesktop",
  "semver": "8.3.1",
  "pkg": "14x",
  "created_at": "2026-10-27 01:20",
  "counts": {"layout": 42, "folders": 3, "widgets": 6},
  "state": {"settings": {...}, "layout": [...], "widgets": [...]}
}
```

选 JSON 而不是 zip：体积本来就是几百 KB，可读、可 diff、可手工救，
也免了「解压炸弹 / zip-slip」这一整类安全问题。

**兼容裸 state.json**：用户直接拿 `%APPDATA%` 里的 state.json 来导入也
认（没有外层包装时按 `raw` 处理）。备份是为了救数据，不该因为格式挑剔
把人挡在门外。

## 错误用 code 不用文案

`BackupError.code` 是稳定标识（`not_json` / `not_backup` / `format_newer`
/ `bad_state` / `too_large`），界面层映射到 i18n 词条——这样同一份错误
在中英文界面下都能说人话，测试也不必断言中文文案。
"""
import copy
import datetime
import json
import os
import tempfile

KIND = "phonedesktop.config"
FORMAT = 1
SUFFIX = ".pdbak"
APP = "PhoneDesktop"

# 单份配置的正常体积在几百 KB 量级；16MB 已经宽到能装下任何真实布局，
# 又不至于让「误选了一个视频文件」把内存吃满。
MAX_BYTES = 16 * 1024 * 1024


class BackupError(Exception):
    """导入/导出失败。`code` 供界面映射 i18n，`detail` 供日志与提示补充。"""

    def __init__(self, code, detail=""):
        super().__init__("%s: %s" % (code, detail) if detail else code)
        self.code = code
        self.detail = detail


# ---------- 生成 ----------
def counts_of(state):
    """包里有什么：图标数 / 文件夹数 / 组件数（确认框里给人看）。"""
    layout = state.get("layout") if isinstance(state, dict) else None
    widgets = state.get("widgets") if isinstance(state, dict) else None
    layout = layout if isinstance(layout, list) else []
    widgets = widgets if isinstance(widgets, list) else []
    return {
        "layout": len(layout),
        "folders": sum(1 for e in layout
                       if isinstance(e, dict) and isinstance(e.get("apps"), list)),
        "widgets": len(widgets),
    }


def make_payload(state, *, semver="", pkg="", now=None, app=APP):
    """把一份 state 包成备份对象。`now` 可注入（测试要稳定时间戳）。"""
    if not isinstance(state, dict):
        raise BackupError("bad_state", "state 不是对象")
    stamp = now or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return {
        "kind": KIND,
        "format": FORMAT,
        "app": app,
        "semver": str(semver or ""),
        "pkg": str(pkg or ""),
        "created_at": stamp,
        "counts": counts_of(state),
        "state": copy.deepcopy(state),
    }


def _atomic_write_json(path, obj):
    """临时文件 + fsync + os.replace：写一半断电也不会留下半个包。"""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def export_file(storage, path, *, semver="", pkg="", now=None):
    """导出到 `path`，返回写出的 payload（界面拿 counts 报数）。"""
    state = storage.export_snapshot()
    payload = make_payload(state, semver=semver, pkg=pkg, now=now)
    try:
        _atomic_write_json(path, payload)
    except OSError as exc:
        raise BackupError("write_failed", str(exc))
    return payload


# ---------- 读取 ----------
def _load_json(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            raise BackupError("too_large", os.path.basename(path))
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except BackupError:
        raise
    except json.JSONDecodeError as exc:
        raise BackupError("not_json", "第 %s 行" % exc.lineno)
    except OSError as exc:
        raise BackupError("not_json", str(exc))


def read_file(path):
    """读一个备份文件，返回统一格式的 payload（裸 state.json 也收）。

    校验只做「结构上能用」这一层：kind / format / state 三件。内容是否
    合理交给 `Storage.import_state()`（那里会走与启动完全相同的清洗、
    迁移、归一化路径，不另写一套规则）。
    """
    raw = _load_json(path)
    if not isinstance(raw, dict):
        raise BackupError("not_backup", "顶层不是对象")

    # 裸 state.json：settings + layout 两个键就是它的指纹
    if "state" not in raw and isinstance(raw.get("settings"), dict) \
            and isinstance(raw.get("layout"), list):
        raw = {
            "kind": KIND, "format": FORMAT, "app": APP,
            "semver": "", "pkg": "", "created_at": "",
            "counts": counts_of(raw), "state": raw,
        }

    if raw.get("kind") != KIND:
        raise BackupError("not_backup", "kind=%r" % (raw.get("kind"),))
    try:
        fmt = int(raw.get("format") or 0)
    except (TypeError, ValueError):
        raise BackupError("not_backup", "format 非数字")
    if fmt > FORMAT:
        raise BackupError("format_newer", "format=%s" % fmt)
    if fmt < 1:
        raise BackupError("not_backup", "format=%s" % fmt)
    state = raw.get("state")
    if not isinstance(state, dict) or not isinstance(state.get("settings"), dict) \
            or not isinstance(state.get("layout"), list):
        raise BackupError("bad_state", "缺 settings/layout")
    if not isinstance(state.get("widgets"), list):
        state["widgets"] = []
    raw["format"] = fmt
    raw["counts"] = counts_of(state)
    return raw


def describe(payload):
    """给人看的一行摘要（确认框正文用）。"""
    c = payload.get("counts") or counts_of(payload.get("state") or {})
    return {
        "layout": c.get("layout", 0),
        "folders": c.get("folders", 0),
        "widgets": c.get("widgets", 0),
        "semver": payload.get("semver") or "",
        "pkg": payload.get("pkg") or "",
        "created_at": payload.get("created_at") or "",
    }
