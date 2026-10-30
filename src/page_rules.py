# -*- coding: utf-8 -*-
"""每页排序规则（v9 V2，指导文档_v9.md §3.3）。

背景：`auto_arrange` 原本是**全局**开关（右键 ▸ 查看 ▸ 自动排列图标）。
用户的实际诉求是"这一页想自动排，那一页想自己摆"——全局开关无论如何
都满足不了，而且拖动跨页时还暴露了另一个 BUG（见 `grid_flow.
auto_arrange_moves` 的按页密排）。

两档配置：
    page_rules_custom = False（默认）  -> 所有页跟随全局开关
    page_rules_custom = True           -> 逐页规则，缺省仍跟随全局
    page_rules = {"0": "auto", "1": "free"}

**唯一判定入口是 `mode_for()`**：`desktop_window` 的让位预览、推让、
提交三处都走它。以前三处各自读 `_auto_arrange()`，加一条规则就要改三遍，
必然漂移。
"""
from __future__ import annotations

AUTO = "auto"
FREE = "free"
INHERIT = ""            # 空 = 跟随全局

MODE_LABELS = ((INHERIT, "跟随全局"), (AUTO, "自动排列"), (FREE, "自由摆放"))


def label_key(value: str) -> str:
    """下拉项对应的 i18n 键（唯一拼法入口）。

    `INHERIT` 是**空串**（存储里就写 ""），直接拼 `"page_rules." + value`
    会得到 `"page_rules."`——词表里没有这个键，英文界面就漏出中文兜底
    （v15g）。以后再加模式，也只改这里。
    """
    return "page_rules." + (value or "inherit")


def custom_enabled(storage) -> bool:
    """是否启用了"每页单独设置"。"""
    return bool(storage.get_setting("page_rules_custom"))


def global_auto(storage) -> bool:
    """全局开关（右键 ▸ 查看 ▸ 自动排列图标 / 设置页总开关）。"""
    return bool(storage.get_setting("auto_arrange"))


def rules(storage) -> dict:
    raw = storage.get_setting("page_rules")
    return raw if isinstance(raw, dict) else {}


def rule_of(storage, page: int) -> str:
    """该页的显式规则（`AUTO` / `FREE` / `INHERIT`）。"""
    if not custom_enabled(storage):
        return INHERIT
    v = rules(storage).get(str(int(page)))
    return v if v in (AUTO, FREE) else INHERIT


def mode_for(storage, page: int) -> bool:
    """该页是否自动排列（True）/ 自由摆放（False）。"""
    r = rule_of(storage, page)
    if r == AUTO:
        return True
    if r == FREE:
        return False
    return global_auto(storage)


def set_mode(storage, page: int, value: str) -> None:
    """写一页的规则（`INHERIT` = 删掉该页的键，回到跟随全局）。"""
    v = value if value in (AUTO, FREE) else INHERIT
    data = dict(rules(storage))
    key = str(int(page))
    if v == INHERIT:
        data.pop(key, None)
    else:
        data[key] = v
    storage.set_setting("page_rules", data)


def set_custom(storage, on: bool) -> None:
    storage.set_setting("page_rules_custom", bool(on))


def auto_pages(storage, total: int) -> set:
    """自动排列的页号集合（传给 `grid_flow.auto_arrange_moves`）。

    `total` 由调用方给（`grid_flow.page_count` 需要 storage，这里不反向
    依赖 grid_flow，避免网格模块与规则模块互相引用）。
    """
    return {p for p in range(max(0, int(total))) if mode_for(storage, p)}


def describe(storage, page: int) -> str:
    """给设置页显示的一句话（v10 A1：只写规则名，不带括号补充）。

    曾经这里返回「跟随全局（自动排列）」——括号里的内容与下拉框文本重复，
    而全局实际结果在卡片顶部的「自动排列图标」开关上已经写着（用户 2026-10-22
    反馈 1：「不需要括号里的注释」）。要解释"这一页实际怎么排"请看
    `mode_for()`，它才是判定入口。
    """
    r = rule_of(storage, page)
    if r == AUTO:
        return "自动排列"
    if r == FREE:
        return "自由摆放"
    return "跟随全局"
