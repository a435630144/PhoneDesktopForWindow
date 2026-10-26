# -*- coding: utf-8 -*-
"""构建标识：让"我到底跑的是哪个包"这个问题可以在界面上直接回答。

由来（2026-10-19 事故）：用户报"拖拽白框还是没修好"，查下来他启动的是
14:01 的旧 onedir，而修复在 19:03 的新目录里——两个包并排在 `dist/` 下，
凭文件名毫无区分度，用户没有理由知道自己点错了。

于是每个包自带一个可自证的标识：
  - **构建时刻取文件 mtime**（冻结后 = exe，源码运行 = 本文件），
    不依赖打包脚本传参，也不可能被忘记更新；
  - `VERSION` 只作为口头沟通用的短标签，每次出包顺手改。

界面上以右键菜单底部一条灰色不可点项呈现：
`版本 4.0.0（v9a） · 构建 2026-10-21 23:50`。

命名规则（**2026-10-21 用户定稿，双轨制**）：

1. **对外语义版本 `SEMVER`**（安装包号 / Release / 界面主标）按改动量叠加：
   - **修少量问题** → patch +1（`4.0.0` → `4.0.1`）
   - **修一大批问题** → minor +1（`4.0.1` → `4.1.0`）
   - **新增功能的大改动** → major +1（`3.x` → `4.0.0`，即每个新功能迭代抬一位主版本）

2. **内部包标识 `VERSION`**（同一语义版本下区分候选包）：
   - **首数字 = 迭代系列**（v6 全是 6a/6b…、v7 全是 7a…、v8 从 8a 起）；
   - **小写字母 = 本迭代第几个包**。
   - 字母包是**验收轮次**，不单独对外发版；同一语义版本可以出 8a→8b→8c 三个字母包。

3. 两者必须同步：发版时 `SEMVER` 与 `installer.iss` 的 `MyAppVersion` 一致，
   由 `scripts/check_docs.py` 的 [7] 强制比对（不一致 = ERROR，不是 WARN）。
   语义主版本与迭代系列**成对递增**（v6 → 1.4.0、v7 → 2.0.0、v8 → 3.0.0、v9 → 4.0.0），
   但映射不是固定偏移，别用公式推算——**以本文件常量与安装脚本为准**（坑 #69 / #93）。
"""
import datetime
import os
import sys

VERSION = "15a"
# 对外语义版本。改这里就必须同批改 scripts/installer.iss 的 MyAppVersion。
SEMVER = "8.3.4"


def build_stamp() -> datetime.datetime | None:
    """返回构建时刻（exe / 源码文件 mtime）；拿不到返回 None。"""
    try:
        base = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
        return datetime.datetime.fromtimestamp(os.path.getmtime(base))
    except Exception:
        return None


def build_time() -> str:
    """构建时刻字符串（分钟精度）；失败时返回「未知」而不是抛异常。"""
    stamp = build_stamp()
    return stamp.strftime("%Y-%m-%d %H:%M") if stamp else "未知"


def label() -> str:
    """菜单里展示的完整标识：语义版本（给人看）+ 包标识（区分候选包）。

    例：`版本 4.0.0（v9a） · 构建 2026-10-21 23:50`。
    两个标识都要出现——语义版本回答"这是哪个正式版本"，
    包标识回答"我装的是这一版的第几个候选包"（2026-10-19 跑错包事故）。
    """
    raw = "版本 %s（v%s） · 构建 %s" % (SEMVER, VERSION, build_time())
    try:
        from . import i18n
        return i18n.TL("version.label", raw, semver=SEMVER,
                       pkg=VERSION, build=build_time())
    except Exception:      # noqa: BLE001 —— 版本串取不到不该让界面崩
        return raw
