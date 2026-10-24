# -*- coding: utf-8 -*-
"""组件注册表：组件的"静态事实"只在这里定义一次（v8 C1，指导文档_v8.md §2.1）。

背景：v7 之前一个组件要在四个地方登记——
`widget_layer.WIDGET_TYPES`（类型→类）、`widget_layer.WIDGET_NAMES`（类型→中文名）、
`widget_catalog.WIDGET_CATALOG`（跨度）、`WidgetLayer._place_default`（默认落位分支）。
组件库、托盘、设置页又各自再读一遍，于是"加一个组件"变成"改四个文件 + 记得改 UI"。

v8 起改为：**一份 `WidgetSpec` 声明一切静态事实**，其余模块只查询注册表。
`widget_catalog` 退化为薄壳（保留函数签名，内部转调本模块），
`widget_layer.WIDGET_TYPES` / `WIDGET_NAMES` 由本模块派生——老调用点一行不改。

依赖方向（防循环）：
    本模块 → widgets_*（具体组件类）→ widget_base → grid_flow / theme
    widget_catalog → 本模块（函数内延迟导入，因为它被 grid_flow 引用）
    widget_layer → 本模块

加入新组件只需两步：
    1. 写 `src/widgets_xxx.py`（`draw()` 纯函数 + `XxxWidget(GlassWidget)`）；
    2. 在 `REGISTRY` 末尾追加一条 `WidgetSpec(...)`。
无需改 widget_layer / 托盘 / 组件库 / 设置页。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

Span = Tuple[int, int]



@dataclass(frozen=True)
class FieldSpec:
    """组件设置项声明（纯数据，不含 Qt）。

    `widget_settings.py` 按它生成表单控件；`Entry.settings` 按 `key` 存值。
    kind ∈ {"text", "combo", "int", "bool", "color", "float"}（v9 加了后两个：
    颜色选择器与 0~1 滑条，见 `widget_style`）。

    `group`：表单里的分组标题（空 = 归入「组件设置」）。v9 起外观字段
    统一归「外观」，组件库与设置页就都能预期"前几项是组件自己的，
    后几项是所有组件都有的"。
    """

    key: str
    label: str
    kind: str = "text"
    default: object = ""
    choices: Tuple[Tuple[object, str], ...] = ()   # ((值, 显示文字), ...)
    placeholder: str = ""
    hint: str = ""
    group: str = ""


# 通用外观字段（v9 V1）：所有组件共享这 5 项，由 `_with_style` 追加。
# 定义放在注册表里而不是 `widget_style` —— 否则 widget_style 需要
# widget_registry.FieldSpec，而注册表构造时又要 STYLE_FIELDS，成环。
# `widget_style` 反向从这里 import（单向）。
# v10 A4（反馈 10）：hint 只留"留空会怎样"这一句新增信息；
# 「选色后换成该色 / 次文字派生 / 强调色用在哪」都是标签自明或实现细节。
STYLE_FIELDS = (
    # v14 反馈 10：液态玻璃是**一种配色**，与背景色同级、互斥。开着时
    # 背景由壁纸实时折射（颜色/不透明度自动禁用），关掉才回到背景色。
    # 默认 None = 按"是否设过背景色"推导（见 widget_style.liquid_enabled）。
    FieldSpec("bg_liquid", "液态玻璃", "bool", None,
              hint="背景透出并折射壁纸；开启时颜色不可调", group="外观"),
    FieldSpec("bg_color", "背景颜色", "color", "",
              hint="留空 = 跟随主题玻璃", group="外观"),
    FieldSpec("bg_alpha", "背景不透明度", "float", 1.0,
              hint="20%~100%，越低越透出壁纸", group="外观"),
    FieldSpec("text_color", "文字颜色", "color", "",
              hint="留空 = 跟随主题主文字", group="外观"),
    FieldSpec("accent_color", "强调色", "color", "",
              hint="留空 = 跟随主题蓝", group="外观"),
    FieldSpec("radius", "圆角", "int", 0,
              hint="0 = 跟随主题；范围 0~40", group="外观"),
)

STYLE_KEYS = tuple(f.key for f in STYLE_FIELDS)


@dataclass(frozen=True)
class WidgetSpec:
    """一种组件的声明。字段都是"不会随运行变化"的事实。"""

    type: str
    name: str
    summary: str
    factory: Callable
    spans: Tuple[Span, ...]
    category: str = "其他"
    default_anchored: bool = True
    interactive: bool = False        # 含输入框 → 去掉 NOACTIVATE，可获键盘焦点
    multi_instance: bool = False     # 是否允许同时放多个
    settings: Tuple[FieldSpec, ...] = ()
    preview: Optional[Callable] = None   # (painter, rect, dpr) -> None，缺省走兜底卡
    plugin: bool = False             # True = 外部导入（可卸载、来源是用户目录）
    version: str = ""                # 插件自己的版本号（内置留空）
    # v10 D3（反馈 15-a）：内置组件的名字/说明走 i18n key；插件没有 key，
    # 直接用 `name`/`summary`（用户自己写的中文，不该被翻译）。
    name_key: str = ""
    summary_key: str = ""

    @property
    def default_span(self) -> Span:
        return self.spans[0]

    def display_name(self) -> str:
        """界面显示名（跟随当前语言）。"""
        if not self.name_key:
            return self.name
        from . import i18n
        return i18n.T(self.name_key)

    def display_summary(self) -> str:
        if not self.summary_key:
            return self.summary
        from . import i18n
        return i18n.T(self.summary_key)


def _build_registry() -> Dict[str, WidgetSpec]:
    # 延迟到函数内导入：组件模块会 import widget_base，而 widget_base 在
    # 方法内延迟引用 widget_catalog → 本模块。函数内导入让模块级依赖保持单向。
    from .widgets.clock import ClockWidget, draw_preview as _preview_clock
    from .widgets.notes import NotesWidget, draw_preview as _preview_notes
    from .widgets.panel import DesktopPanel
    from .widgets.monitor import MonitorWidget, draw_preview as _preview_monitor
    from .widgets.calendar import CalendarWidget, draw_preview as _preview_calendar
    from .widgets.weather import WeatherWidget, draw_preview as _preview_weather
    from .widgets.todo import TodoWidget, draw_preview as _preview_todo
    from .widgets.music import MusicWidget, draw_preview as _preview_music

    specs = [
        WidgetSpec(
            type="clock",
            name="时钟",
            summary="时间与日期，随格子大小自动缩放字号",
            name_key="widget.clock",
            summary_key="widget.clock.summary",
            factory=ClockWidget,
            spans=((2, 2),),
            category="时间",
            default_anchored=True,
            settings=(
                FieldSpec("show_seconds", "显示秒", "bool", True),
                FieldSpec("show_date", "显示日期", "bool", True),
            ),
            preview=_preview_clock,
        ),
        WidgetSpec(
            type="notes",
            name="便签",
            summary="随手记，输入即自动保存",
            name_key="widget.notes",
            summary_key="widget.notes.summary",
            factory=NotesWidget,
            spans=((2, 2),),
            category="效率",
            default_anchored=False,   # 含输入框：锚定态 NOACTIVATE 拿不到焦点
            interactive=True,
            multi_instance=True,
            preview=_preview_notes,
        ),
        WidgetSpec(
            type="panel",
            name="桌面面板",
            summary="常用应用与文件夹的集合面板",
            name_key="widget.panel",
            summary_key="widget.panel.summary",
            factory=DesktopPanel,
            spans=((3, 4),),
            category="效率",
            default_anchored=True,
            # v10 A9（反馈 9）：面板 = 可组合的信息面板，每一块单独开关。
            # 天气卡不再画示例数据（订阅共享天气源），所以"显示天气"是
            # "要不要这张卡"，不是"用真数据还是假数据"。
            settings=(
                FieldSpec("show_search", "显示搜索框", "bool", True),
                FieldSpec("show_folders", "显示快捷文件夹", "bool", True),
                FieldSpec("show_disk", "显示磁盘卡片", "bool", True),
                FieldSpec("show_recent", "显示最近下载", "bool", True),
                FieldSpec("show_weather", "显示天气卡片", "bool", True,
                          hint="与「天气」组件共用同一份城市与数据"),
            ),
        ),
        WidgetSpec(
            type="monitor",
            name="系统监控",
            summary="CPU / 内存 / 磁盘实时占用，纯本地不联网",
            name_key="widget.monitor",
            summary_key="widget.monitor.summary",
            factory=MonitorWidget,
            # v12 反馈 36：2x1 紧凑横排（替代旧 2x2 圆环）+ 4x2 宽版三列
            spans=((2, 1), (4, 2)),
            category="系统",
            default_anchored=True,
            preview=_preview_monitor,
        ),
        WidgetSpec(
            type="todo",
            name="待办清单",
            summary="勾选式待办，回车即加，自动保存",
            name_key="widget.todo",
            summary_key="widget.todo.summary",
            factory=TodoWidget,
            spans=((2, 2), (2, 4)),
            category="效率",
            default_anchored=False,   # 含输入框，锚定态拿不到键盘焦点
            interactive=True,
            multi_instance=True,
            preview=_preview_todo,
        ),
        WidgetSpec(
            type="calendar",
            name="日历",
            summary="月视图，左右翻月，点标题回到今天",
            name_key="widget.calendar",
            summary_key="widget.calendar.summary",
            factory=CalendarWidget,
            spans=((2, 2), (3, 3)),
            category="时间",
            default_anchored=True,
            settings=(
                FieldSpec("week_start", "每周起始", "combo", 0,
                          choices=((0, "周一"), (6, "周日"))),
            ),
            preview=_preview_calendar,
        ),
        WidgetSpec(
            type="weather",
            name="天气",
            summary="当前天气与未来三天，断网看缓存",
            name_key="widget.weather",
            summary_key="widget.weather.summary",
            factory=WeatherWidget,
            spans=((2, 2), (4, 2)),
            category="信息",
            default_anchored=True,
            multi_instance=False,
            settings=(
                FieldSpec("city", "城市", "text", "",
                          placeholder="例如：北京"),
                FieldSpec("online", "允许联网", "bool", True,
                          hint="关闭后只显示上次缓存"),
            ),
            preview=_preview_weather,
        ),
        WidgetSpec(
            type="music",
            name="音乐",
            summary="控制本机正在播放的音乐，不挑播放器",
            name_key="widget.music",
            summary_key="widget.music.summary",
            factory=MusicWidget,
            spans=((3, 2), (4, 2)),
            category="媒体",
            default_anchored=True,
            multi_instance=False,
            preview=_preview_music,
        ),
    ]
    return {s.type: s for s in specs}


def _with_style(specs: List[WidgetSpec]) -> List[WidgetSpec]:
    """给每个组件追加"通用外观字段"（v9 V1，本模块的 `STYLE_FIELDS`）。

    放在这里而不是逐个组件写：外观是**所有组件**的能力，漏一个组件
    就等于"这个组件不能改颜色"，用户只会觉得坏了。
    组件自己的字段在前（先配业务），外观在后（分组标题隔开）。
    """
    return [with_style(spec) for spec in specs]


def with_style(spec: WidgetSpec) -> WidgetSpec:
    """给一个组件补齐通用外观字段（已有的同名字段不重复添加）。

    内置组件在注册表构造时走这里；插件在 `register_plugin` 时走同一个
    函数——"外观是所有人的能力"不能只对内建生效。
    """
    have = {f.key for f in spec.settings}
    extra = tuple(f for f in STYLE_FIELDS if f.key not in have)
    if not extra:
        return spec
    return replace(spec, settings=tuple(spec.settings) + extra)


REGISTRY: Dict[str, WidgetSpec] = {s.type: s for s in
                                   _with_style(list(_build_registry().values()))}

# 外部导入的组件（`widget_plugin` 加载后写这里）。单独一张表：内置组件
# 不可卸载，插件可卸载，两者在 UI 上的动作不同，混在一张表里就必须
# 靠 `plugin` 标记反查——一张表一件事更不容易错。
PLUGINS: Dict[str, WidgetSpec] = {}


# ---------------------------------------------------------------- 查询 API

def get(wtype: str) -> Optional[WidgetSpec]:
    """按类型取声明（内置 + 外部插件）；未登记返回 None。"""
    if not isinstance(wtype, str):
        return None
    return REGISTRY.get(wtype) or PLUGINS.get(wtype)


def all_specs() -> List[WidgetSpec]:
    """全部组件，内置在前、插件在后（= 组件库默认展示顺序）。"""
    return list(REGISTRY.values()) + list(PLUGINS.values())


def names() -> Dict[str, str]:
    """类型 → 中文名（`widget_layer.WIDGET_NAMES` 由它派生）。"""
    return {t: s.name for t, s in list(REGISTRY.items()) + list(PLUGINS.items())}


def types() -> List[str]:
    return list(REGISTRY.keys()) + list(PLUGINS.keys())


def spans(wtype: str) -> List[Span]:
    """可用跨度列表（第一项为默认）；未登记返回 []（对齐旧 catalog 语义）。"""
    spec = get(wtype)
    return [tuple(s) for s in spec.spans] if spec else []


def default_span(wtype: str) -> Optional[Span]:
    spec = get(wtype)
    return tuple(spec.spans[0]) if spec else None


def categories() -> List[str]:
    """分类列表，按"首次登记顺序"去重（组件库左栏顺序）。"""
    out: List[str] = []
    for s in all_specs():
        if s.category not in out:
            out.append(s.category)
    return out


def specs_in(category: str) -> List[WidgetSpec]:
    """某分类下的组件；`category` 传 "全部" 或空串返回全部。"""
    if not category or category == "全部":
        return all_specs()
    return [s for s in all_specs() if s.category == category]


def register(spec: WidgetSpec) -> None:
    """登记/覆盖一个**内置**组件声明（测试用；插件请走 `register_plugin`）。"""
    REGISTRY[spec.type] = spec


def register_plugin(spec: WidgetSpec) -> None:
    """登记一个外部组件（`widget_plugin` 加载后调用）。

    独占命名空间规则：与内置类型撞名 -> 不允许（内置是产品的一部分，
    不能被用户 zip 顶掉）；与已装插件撞名 -> 覆盖（重装/升级）。
    """
    if spec.type in REGISTRY:
        raise ValueError("组件类型 %r 与内置组件冲突" % spec.type)
    PLUGINS[spec.type] = replace(with_style(spec), plugin=True)


def unregister(wtype: str) -> bool:
    """移除一个**插件**声明；内置组件拒绝（返回 False）。"""
    if wtype in PLUGINS:
        del PLUGINS[wtype]
        return True
    return False


def is_builtin(wtype: str) -> bool:
    return wtype in REGISTRY


def set_spans(wtype: str, spans_) -> None:
    """改某组件的可用跨度（测试/诊断用；正式能力请改 REGISTRY 里的声明）。

    `WidgetSpec` 是 frozen dataclass，只能替换不能原地改——这里用
    `dataclasses.replace` 生成新声明并覆盖，避免调用方各自 new 一遍丢字段。
    """
    from dataclasses import replace
    spec = get(wtype)
    if spec is None:
        raise KeyError(wtype)
    REGISTRY[wtype] = replace(spec, spans=tuple(tuple(s) for s in spans_))
