# -*- coding: utf-8 -*-
"""配置与布局持久化：JSON 存放于 %APPDATA%/PhoneDesktop/state.json（v10 D1 改名）。

layout 中每个条目：
  {"id": 唯一id, "type": "app" | "folder",
   "app":  {...}                 # type=app 时的应用字典（含 kind: app/file）
   "apps": [{...}, ...]          # type=folder 时的成员应用列表
   "col": 网格列, "row": 网格行}

桌面文件克隆条目：app.kind == "file"，id 为 "df:" + 路径哈希，
重扫不会重复，移除只动布局不删文件。
"""
import json
import os
import tempfile
import uuid
from copy import deepcopy

from .paths import data_file

# 档位判定容差：fit 落在预设 ±0.02 内视为该档，否则是"自定义比例"
FIT_TOLERANCE = 0.02


def tier_for_fit(fit):
    """图标占比 -> 最近的三档预设名；不在任何预设附近 -> None（自定义）。

    v7 §3.1：菜单「图标大小」的勾选状态由 `icon_fit` 反查得到。
    medium 的预设值是 0.57（= 本机 native 48 / cell 84），换屏后由
    `grid_sizing.fit_for()` 用 `native_px/cw` 解算，这里只做菜单勾选判定。
    """
    if fit is None or isinstance(fit, bool):
        return None
    try:
        f = float(fit)
    except (TypeError, ValueError):
        return None
    for name, ref in (("medium", 0.57), ("small", 0.42), ("large", 0.82)):
        if abs(f - ref) <= FIT_TOLERANCE:
            return name
    return None


def _as_positive_int(raw):
    """settings 里的正整数（"auto"/None/非法 -> None）。"""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


DEFAULT_SETTINGS = {
    "schema_version": 2,     # 状态结构版本：2 = 路线A（全量接管）。旧版(1)state
                            # 首次加载时一次性升级，见 Storage._migrate
    "first_run": True,         # 是否首次运行（旧键；v11b 起一次性气泡换成新手引导）
    # v11b：已完成的新手指引版本。0 = 从未看完；小于 onboarding.ONBOARDING_VERSION
    # 就再弹一次（加步骤时版本 +1，老用户只补看新增内容，不会每次启动都打扰）。
    "onboarding_version": 0,
    # v10 D3（反馈 15-a）：界面语言。"auto" = 安装器选的语言 -> 系统语言；
    # 也可显式写 "zh_CN" / "en"。解析唯一入口 `i18n.resolve()`。
    "language": "auto",
    "autostart": False,        # 开机自启动
    "icon_size": 72,           # 图标边长基准（像素，100% 缩放下的逻辑值）
    "icon_scale": 1.0,         # 用户自定义图标缩放（在 DPI 感知之上再乘，0.6~2.0）
    # ---- v6 批次 A（指导文档_网格与无界双模式_v6.md §8）----
    "icon_tier": "medium",     # 图标三档（网格模式）：small .58 / medium .74 / large 1.0
    # ---- v7 批次 B1（指导文档_v7.md §3.1 / §3.2 / §3.4）----
    "icon_fit": None,          # 图标在格子里的占比（0.30~0.90）；None = 按
                               # icon_tier 映射（medium 跟随 native_px/cell_w）
    "grid_dims_mode": "auto",  # "auto" = 按原生图标尺寸算行列（默认）
                               # "manual" = 用 grid_cols / grid_rows
    "grid_cols": None,         # manual 时的目标列数（上限 = cols_auto）
    "grid_rows": None,         # manual 时的目标行数（只裁剪，不改 cell）
    "grid_origin_mode": "auto",  # "auto" = 居中；"manual" = 用 grid_origin
    "grid_origin": None,       # [x, y]，逻辑 px；manual 时生效
    "layout_mode": "grid",     # 布局模式：grid 网格（默认）| free 无界
    "icon_px": 96,             # 无界模式图标像素（网格模式置灰，由档位决定）
    # v9 V2：每页排序规则。默认关 = 所有页跟随 auto_arrange；
    # 打开后 page_rules 逐页覆盖（缺省仍跟随全局）。判定唯一入口
    # `page_rules.mode_for()`。
    "page_rules_custom": False,
    "page_rules": {},
    # v9 V4：交互方式。"windows" = 单击选中/双击打开/框选（默认，现行语义）；
    # "mobile" = 单击直接启动、长按出菜单、滑动翻页（手机语义）。
    "interaction_mode": "windows",
    "auto_arrange": False,     # 自动排列：False=自由移动（落 target 原位留空），
                               # True=拖动后 reflow 递进（旧 M2 行为）。
                               # 2026-10-19 反馈 3：改为右键「查看 ▸ 自动排列
                               # 图标」的**持久开关**——先打开，再始终生效。
    # 2026-10-19 用户反馈 4/5（新增）
    "icon_match_native": True,  # 图标本体对齐系统桌面：读 explorer 的
                                # IconSize 反推 cell（接管后大小一致）
    "page_axis": "horizontal",  # 翻页方向：horizontal（默认，左右）| vertical
    "extra_pages": [],         # 无界模式空白页页码列表（如 [3,5]）
    # v14 反馈 4：是否已补过"默认第二页"（一次性。用户删掉空白页后不补）
    "blank_page_seeded": False,
    "takeover_desktop": True,  # 「接管系统桌面」合并开关（替代 hide_native_icons
                               # 语义入口；旧键仍兼容读取）。
                               # v7 反馈 16 起**界面不再提供开关**：启动即接管，
                               # 加载时一次性强制为 True（见 _load 的迁移块）。
    "takeover_v7_forced": False,  # 上述一次性迁移的标记位
    # v7 反馈 14：文件夹默认展开尺寸（上限，[列, 行]，最小 2×2）。
    # 单个文件夹可在自己的 entry["frame"] 里覆盖（右键「文件夹大小」）。
    "folder_frame_default": [3, 3],
    # v14 反馈 1：文件夹悬停自动展开（260ms 驻留）。手机上"瞄一眼"的语义，
    # 关掉后回到纯点击打开。
    "folder_hover_expand": True,
    # ---- v14 反馈 3 / 13：图标文字样式 + cell 预览框样式 ----
    # 出厂值 = 1:1 还原 Windows 原生（白字 + 一层柔和黑阴影，无描边不加粗），
    # 读取/夹紧/脏值回退的唯一入口是 `label_style.py`，这里只放缺省。
    "label_color": "#f4f5f7",       # 文字色（Windows 原生白）
    "label_bold": False,            # 加粗（Windows 原生不加粗）
    "label_shadow": True,           # 阴影开关（Windows 原生有柔和阴影）
    "label_shadow_color": "#000000",
    "label_shadow_alpha": 75,       # 阴影浓度 %（0~100）
    "label_outline": False,         # 文字描边（Windows 原生无描边）
    "label_outline_color": "#000000",
    "label_outline_width": 1,       # 描边粗细 1~3
    "label_font_scale": 100,       # 图标字号缩放 %（70~140，v14q）
    # v14j：图标悬停高亮与动画（读取唯一入口 label_style.hover_style）
    "icon_hover_color": "",         # "" = 跟内置白（theme.HOVER_BORDER）
    "icon_hover_fill": 8,           # 悬停底填充 %（0~40）
    "icon_hover_enabled": True,     # 关 = 悬停不画高亮底/边
    "icon_hover_scale": 104,        # 悬停放大 %（100 = 关闭放大动画）
    "icon_hover_anim": 140,         # 悬停动画时长 ms（80~400）
    # 反馈 13：拖动落点预览框（原硬编码红框 + 8% 红底）。空色 = 跟随主题。
    "cell_preview_color": "",       # "" = theme.DANGER
    "cell_preview_fill": 8,         # 填充浓度 %（0~40）
    "cell_preview_width": 2,        # 边框粗细 1~4
    "cell_preview_radius": 8,       # 圆角 0~16
    # ---- v14 反馈 9/10：液态玻璃（Liquid Glass） ----
    # 语义：元素背景是**透明**的，只有边缘一圈带内的底图被折射（模拟玻璃
    # 厚度把后面的壁纸压缩/弯折）。不叠色、不画高光、不描彩边。
    # 反馈 10 起它是**每个元素自己的配色**：文件夹在色板里选、组件在自己
    # 的设置里开关、Dock 用 `liquid_dock`；`liquid_glass` 是总闸。
    # 读取/夹紧的唯一入口是 `liquid_glass.py`，这里只放缺省。
    "liquid_glass": True,           # 总开关（一票否决）
    "liquid_dock": True,            # Dock 栏的液态玻璃（其余元素各写各的）
    "liquid_strength": 60,          # 折射强度 %（0 = 关掉折射，0~100）
    "liquid_band": 24,              # 折射带宽度 DIP（6~40）
    # v14e：liquid-glass-react 的四个可调光学层（读取/夹紧仍走 liquid_glass.py）
    "liquid_saturation": 140,       # 玻璃下饱和度 %（100~200；140 = lgr 默认）
    "liquid_frost": 100,            # 磨砂程度 %（0 = 清透玻璃，100 = 全磨砂）
    "liquid_aberration": 0,         # 边缘色散 %（0 = 关；默认不做彩边）
    "liquid_rimlight": 0,           # 边缘高光 %（0 = 关；默认不描高光）
    "liquid_fps_mode": "medium",    # 拖动重算频率档位（low=30/medium=45/high=60/custom）
    "liquid_fps_custom": 60,        # 自定义 fps（24~240；建议=显示器刷新率）
    "auto_sync_desktop": True,  # 监听桌面目录自动同步（v6 §7.4：新增/删除/
                                # 重命名 debounce 1s 后增量更新，不重排已有布局）
    "columns": "auto",         # 每行图标数："auto"=按工作区尺寸自动计算（M1 GridMetrics，
                               # 指导文档 §1：四边等距铺满）；或整数（>=2）手动覆盖列数
                               # （设置高级选项，rows 始终自动）
                               # （v14i：`opacity` 键退役——悬浮兜底窗改用固定值，
                               #   见 desktop_window.FLOATING_OPACITY；老 state.json 里的
                               #   残留键无人读取，无害）
    "show_widgets": True,      # 是否显示桌面小组件
    # v10 A9（反馈 9）：天气**全局唯一**配置——「天气」组件与面板里的
    # 天气卡共用一份城市 / 联网开关（旧版各存各的，于是两个界面两个温度）。
    "weather_city": "",
    "weather_online": True,
    # v10 A8（反馈 21）：组件卡片占跨度的比例（0.6~1.0，默认 0.90）。
    # 1.0 = 填满整格——并排的两个组件边缘会贴死（视觉上连成一块），
    # 所以默认留 10% 的呼吸缝。换算见 `widget_geom`（唯一入口）。
    "widget_fill": 0.90,
    # ---- v10 C3（反馈 17）：Dock 栏 ----
    # 默认开启：用户点名要的新功能，装上就该看到；关闭时 inset 全 0，
    # 网格立刻恢复满工作区（`dock.storage_inset` 是唯一扣除入口）。
    "dock_enabled": True,
    "dock_position": "bottom",  # bottom（默认）| top | left | right
    "dock_items": [],           # [{"name","target","icon_path"}]，规范化见 dock.py
    "dock_seeded": False,       # 首次启用是否已用桌面现成应用填过（只填一次）
    # Dock 两侧布置网格（新）：开启后水平 Dock 不再整边扣除网格，
    # Dock 实际占住的格子禁放（dock.side_grid_blocked），两侧空区可摆图标
    "dock_side_grid": False,
    # v10 A9（反馈 24）：Dock 外观（全部是"全局旋钮"，不进 dock_items）
    # v14 反馈 5：默认样式改成 iOS Dock 观感（更大图标 / 更松间距 / 更厚内边距 /
    # 更圆角 / 更透背景 / 更强 hover）。出厂值在这里，用户改过的值在各自
    # state.json 里，不受影响。
    "dock_icon": 44,            # 图标逻辑边长 24~56（100% 缩放下的基准）
    "dock_gap": 12,             # v11：图标间距 0~24（逻辑 px，100% 基准）
    "dock_pad": 10,             # v11：条带内边距 2~20——决定"条带有多厚"
    # v12 反馈 35：四向内边距（未设时回退 dock_pad）
    "dock_pad_top": None,
    "dock_pad_bottom": None,
    "dock_pad_left": None,
    "dock_pad_right": None,
    # v12 反馈 35：屏幕位置偏移与居中
    "dock_offset_x": 0,
    "dock_offset_y": 0,
    "dock_center": True,
    "dock_hover_pct": 140,      # v11：hover 放大百分比 100~150（100 = 不放大）
    "dock_alpha": 0.62,         # 条带背景不透明度 0.30~1.0（iOS 玻璃 Shelf 更透）
    "dock_radius_mode": "round",  # round（指定半径，默认）| pill（胶囊）| square（直角）
    "dock_radius": 20,           # round 形态的半径 0~26（100% 缩放基准）
    # 一次性迁移标记（v10 A11）：A9 的默认值是 pill，用户只要动过 Dock 设置
    # 就已经被 `_apply_dock` 落盘成 "pill"——光改默认值看不见效果。
    "dock_radius_migrated": False,
    "dock_hover": True,         # hover 放大 + 邻近让位
    "dock_dot": True,           # hover 项下方的小圆点
    # v10 反馈 27：边框可调（0 = 不画边框；颜色空 = 跟随主题 BORDER）
    "dock_border_width": 1,     # 0~4 逻辑像素
    "dock_border_color": "",    # "#rrggbb" / ""（跟随主题）
    # v7 反馈 9「仅小组件模式」：开 → 记住当前布局模式并切无界（组件
    # 不占格 = 组件无界）、隐藏全部图标、停画页点/空态提示；关 → 恢复
    # 记住的模式与图标。托盘/右键里的小组件模式开关同步下线，入口唯一
    # （设置 ▸ 通用 ▸ 仅小组件模式）。
    "widgets_only": False,
    "widgets_only_prev_mode": None,  # 进仅小组件前的布局模式（grid/free）
    "minimize_to_tray": True,  # 关闭窗口时隐藏到托盘（否则退出程序）
    # 2026-10-18 产品方向调整（用户拍板"路线A：隐藏原图标全部映射"）：
    # 默认即进入桌面整理模式——隐藏原生图标、桌面文件全部映射进网格、
    # 安卓式滑动让位 / 文件夹 / 小组件占位全部可用。原生桌面右键等损失
    # 由自绘桌面右键菜单补齐（desktop_window.contextMenuEvent）。
    "hide_native_icons": True,     # 桌面整理模式：隐藏原生图标由整理桌面接管
    "auto_clone_desktop": True,    # 自动把桌面文件克隆进网格（路线A：全量映射）
    "desktop_clone_seeded": False,  # 是否已把桌面文件克隆进网格
    "desktop_clone_v2": False,      # 是否已按 Shell 已知目录（OneDrive 重定向）补扫过
    "native_mode_migrated": False,  # 旧版状态是否已迁移（现仅作历史标记）
    # 开发者模式（2026-10-18）：桌面渲染网格可视化边框（边界/网格线/原点
    # 十字/几何信息文字），供排查布局问题。**2026-10-19 改为默认关闭**——
    # 用户反馈"桌面被盖了一层、壁纸看不见"，满屏调试网格正是元凶之一
    # （另一处是隐藏 DefView 连带壁纸，见 win32_layer）。排查布局时在
    # 右键菜单「查看 ▸ 显示网格调试信息」里临时打开即可。
    "dev_mode": False,
    # v11（反馈 32）：开发者选项拆成分页 + 分项开关。`dev_mode` 变成**总闸**，
    # 下面四个是各自叠加层——旧实现整个 dev_mode 一个开关，开了就是满屏
    # 网格 + 红底 + 帧率 + 窗口巡检，想只看帧率做不到。默认全开：
    # 总闸一打开的行为与旧版完全一致（零迁移成本）。
    "dev_grid": True,            # 网格线 / 原点十字 / 几何信息文字
    "dev_widget_collision": True,  # 组件碰撞体积红底 + 卡片/窗口框
    "dev_perf": True,            # 帧率 / 峰值 / 绘制面积读数
    "dev_window_check": True,    # 组件真实窗口 vs 卡片一致性巡检
    # v10 A2（反馈 3）："首次加载已把 dev_mode 关掉"的一次性标记。
    # 覆盖安装不清 %APPDATA%，旧版遗留的 True 会被新版继承 → 开局一张调试网格。
    "dev_mode_v10_reset": False,
    # v14 反馈 2：桌面 Ctrl+C / Ctrl+V 快捷键总开关。关掉即**卸载**低层键盘
    # 钩子——有用户反馈"用软件之后鼠标速度异常"，虽然程序从不修改系统鼠标
    # 参数（见 tests/test_reveal_path.py 的 M1 守卫），但全局钩子意味着每次
    # 按键都要 round-trip 进本进程；给一个开关，怀疑时先关掉再观察。
    "desktop_hotkeys": True,
}

DEFAULT_WIDGETS = [
    # 不预设 x/y：首次创建时由 WidgetLayer 按类型放到合适位置
    # clock 默认锚定（非悬浮）：占据网格格子、融入壁纸（2026-10-18 用户要求
    # "小组件占据格子位置，挤走其他对象"）。可交互组件（notes/panel 含
    # 输入框）保持悬浮——锚定一律 NOACTIVATE，无法获得键盘焦点。
    # M3：锚定 clock 的 w/h 在 spawn 时被 widget_catalog 推导值覆盖
    # （2×2 格 = span × cell），此处旧像素值仅作悬浮态回退，不再生效
    {"id": "clock", "type": "clock", "w": 300, "h": 160, "hidden": False,
     "anchored": True},
    {"id": "notes", "type": "notes", "w": 300, "h": 300, "hidden": True, "text": ""},
    {"id": "panel", "type": "panel", "w": 400, "h": 620, "hidden": False},
]


def entry_app(entry) -> dict:
    """取条目的 `app` 字典；文件夹条目 / 脏数据统一返回 `{}`。

    2026-10-20：旧版把应用条目升级成文件夹时写 `target["app"] = None`，
    于是 state.json 里 folder 条目是 `{"app": null, "apps": [...]}`。
    `e.get("app", {})` 的默认值**只在键不存在时生效**——键存在且值为
    null 时返回 None，链式 `.get("kind")` 当场 AttributeError。
    读取 app 一律走这里，别再手写 `e.get("app", {}).get(...)`。
    """
    if not isinstance(entry, dict):
        return {}
    app = entry.get("app")
    return app if isinstance(app, dict) else {}


class Storage:
    def __init__(self, path=None):
        self.path = str(path or data_file("state.json"))
        self._data = {
            "settings": deepcopy(DEFAULT_SETTINGS),
            "layout": [],
            "widgets": deepcopy(DEFAULT_WIDGETS),
        }
        self._load()
        self._merge_install_lang()

    def _merge_install_lang(self):
        """安装器写下的语言旁路文件 → settings.lang（v12c）。

        安装器在 ssPostInstall 写 `{appdata}/PhoneDesktop/install_lang`
        （内容 `zh_CN` / `en`，见 scripts/installer.iss）。不直接改 state.json
        是因为 Inno 的 Pascal 里拼 JSON 易碎，旁路文件原子且可测。

        优先级（i18n.resolve 唯一入口，这里只负责把旁路值收进 settings）：
          用户显式 language > 本文件 > 系统语言。
        每次启动都读：重装/覆盖安装换了语言，下一次启动即生效。
        """
        sidecar = os.path.join(os.path.dirname(self.path), "install_lang")
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                value = f.read().strip()
        except Exception:
            return
        if value in ("zh_CN", "en"):
            self._data["settings"]["lang"] = value

    # ---------- 读写 ----------
    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            # B9：解析失败先备份原件再回退默认——否则回退后第一次 save()
            # 就会拿"默认值+桌面重扫"把用户原布局永久覆盖
            self._backup_corrupt()
            return  # 文件损坏时回退默认值，不崩溃
        if not isinstance(raw, dict):
            self._backup_corrupt()
            return
        self._sanitize_raw(raw)
        # 先记录文件里的真实 schema 版本（合并后会被默认值 2 覆盖，读不到旧值）
        raw_schema = 1
        if isinstance(raw.get("settings"), dict):
            try:
                raw_schema = int(raw["settings"].get("schema_version", 1))
            except (TypeError, ValueError):
                raw_schema = 1
        raw_settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
        for key in ("settings", "layout", "widgets"):
            val = raw.get(key)
            if isinstance(val, type(self._data[key])):
                if isinstance(val, dict):
                    self._data[key].update(val)
                else:
                    self._data[key] = val
        self._migrate(raw_schema)
        # v6 §7.2：takeover_desktop 是 v6 新增键。旧 state 没有它时，
        # DEFAULT_SETTINGS 的 True 会静默覆盖老用户「关掉整理模式」的选择——
        # 必须按 hide_native_icons 一次性初始化。反之新键存在则以新键为准，
        # 把兼容键同步成同一语义（_on_guard_tick 等老路径仍读 hide_native_icons）。
        if "takeover_desktop" not in raw_settings:
            self._data["settings"]["takeover_desktop"] = bool(
                self._data["settings"].get("hide_native_icons", True))
        else:
            self._data["settings"]["hide_native_icons"] = bool(
                self._data["settings"]["takeover_desktop"])
        # v7 反馈 16：接管开关下线（启动即接管）。历史 state 里若被关掉过，
        # 一次性改回 True——右键 / 托盘 / 设置页三处入口同时移除后，用户
        # 再也没有打开它的地方，留着 False 就是"程序看起来坏了"。
        # 标记位保证只做一次，不反复覆盖后续的手工调整。
        if not self._data["settings"].get("takeover_v7_forced"):
            self._data["settings"]["takeover_desktop"] = True
            self._data["settings"]["hide_native_icons"] = True
            self._data["settings"]["takeover_v7_forced"] = True
        # v7 反馈 9：仅小组件 + 不显示小组件 = 空桌面死局。设置页有两条
        # 防线（开模式时组件开关跟着开、关组件时自动退出模式），这里兜底
        # 手改 / 历史 state，并把被无界化掉的布局模式一并恢复。
        _s = self._data["settings"]
        if _s.get("widgets_only") and not _s.get("show_widgets", True):
            _s["widgets_only"] = False
            if (_s.get("widgets_only_prev_mode") == "grid"
                    and _s.get("layout_mode") == "free"):
                _s["layout_mode"] = "grid"
        # v10 A2（反馈 3）：**安装后不要默认开开发者调试网格**。默认值本来
        # 就是 False，但安装器只在*卸载*时清 %APPDATA%，覆盖安装会继承旧
        # state.json —— 用户在旧版里手动开过 dev_mode，新版就"带着网格启动"。
        # 一次性标记保证只改这一次，之后用户的手动开关不被覆盖。
        if not _s.get("dev_mode_v10_reset"):
            _s["dev_mode"] = False
            _s["dev_mode_v10_reset"] = True
        self._normalize_layout()
        # v7 §10.3：旧键 columns（整数 = 手动列数）一次性迁移进 grid_cols。
        # 判据用**文件里的原始 settings**（合并默认值后 grid_dims_mode 必然存在）。
        if "grid_dims_mode" not in raw_settings:
            legacy = raw_settings.get("columns")
            n = None if isinstance(legacy, str) else _as_positive_int(legacy)
            if n is not None:
                self._data["settings"]["grid_cols"] = n
                self._data["settings"]["grid_dims_mode"] = "manual"
                self._data["settings"]["columns"] = "auto"
                self.save()

    def _normalize_layout(self):
        """治愈历史脏数据：文件夹条目的 `app` 必须是 dict，不能是 None。

        旧版建文件夹写 `target["app"] = None`（2026-10-20 起改为 `{}`）。
        在入口处一次性归一化，save 后落盘自然写回 `{}`——已在用的 state.json
        无需手工修，也不会在下一次合并新文件夹时把 None 重新写出去。

        2026-10-20 追加：再治愈「同一元素同时在桌面与文件夹」。
        成因是 `seed_desktop_clone()` / 增量同步只拿**顶层条目**做 diff，
        而虚拟文件夹里的图标在磁盘桌面上依然存在，于是一次 rescan 就把
        它们重复播回桌面。后果是从文件夹拖出成员时 `add_app()` 命中同 id
        直接 return（不新增），成员却已从文件夹 pop 掉 —— 用户看到
        "拖出来就消失了"（反馈 2）。这里按"保留文件夹成员、删桌面重复"处理：
        用户把东西收进文件夹就是这个意图，桌面那份才是多余的。
        """
        for e in self._data.get("layout") or []:
            if isinstance(e, dict) and not isinstance(e.get("app"), dict):
                if "app" in e:
                    e["app"] = {}
        if self.dedupe_folder_members():
            self.save()

    def _backup_corrupt(self):
        """把损坏的 state.json 改名留档（.corrupt-<时间戳>），保住用户原始数据。"""
        try:
            import datetime
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            bak = "%s.corrupt-%s" % (self.path, stamp)
            if not os.path.exists(bak):
                os.replace(self.path, bak)
        except Exception:
            pass

    @staticmethod
    def _sanitize_raw(raw):
        """B3：清洗"JSON 合法但元素类型不对"的结构损坏。

        手改 / 半写入 / 旧版本 bug 可能让 layout/widgets 混入非 dict 项
        （或整个键不是列表）。后续 _migrate/folder_member_ids 会对元素裸调
        .get()，不清洗就是启动即崩（AttributeError）。类型不对的键视同
        缺失交给默认值；列表只保留 dict 项，文件夹成员同理。
        """
        for key in ("layout", "widgets"):
            val = raw.get(key)
            if isinstance(val, list):
                raw[key] = [x for x in val if isinstance(x, dict)]
            elif key in raw:
                del raw[key]
        for e in raw.get("layout") or []:
            apps = e.get("apps")
            if isinstance(apps, list):
                e["apps"] = [m for m in apps if isinstance(m, dict)]
            elif "apps" in e:
                del e["apps"]

    def _migrate(self, raw_schema: int = 2):
        """旧版本 state.json 兼容：
        1. 补齐新增的默认小组件（如桌面面板）；
        2. schema_version 1 -> 2 一次性升级（路线A）：2026-10-18 之前的
           state 里 hide_native_icons/auto_clone_desktop 为 false（旧默认
           "保持系统原生桌面"），直接升级软件用户会看到"原生图标还在、
           文件没映射"——正是本轮三张截图反馈的现象。升级时开启整理模式
           + 自动播种；**只升一次**，之后尊重用户在设置里的手动选择。
        """
        existing = {w.get("id") for w in self._data["widgets"]}
        for w in DEFAULT_WIDGETS:
            if w.get("id") not in existing:
                self._data["widgets"].append(deepcopy(w))
        if raw_schema < 2:
            self._data["settings"]["schema_version"] = 2
            self._data["settings"]["hide_native_icons"] = True
            self._data["settings"]["auto_clone_desktop"] = True
            self.save()
        # 3. v10 A11：Dock 默认圆角 pill -> round(12px)。只跑一次，且只在
        #    "从未主动选择过形态"时改（此刻值是 pill 或缺失）——之后用户
        #    自己选回胶囊不会被再改。
        st = self._data["settings"]
        if not st.get("dock_radius_migrated"):
            if str(st.get("dock_radius_mode") or "").strip().lower() in ("", "pill"):
                st["dock_radius_mode"] = "round"
            if st.get("dock_radius") is None:
                st["dock_radius"] = 12
            st["dock_radius_migrated"] = True
            self.save()

    def save(self):
        """原子写入，避免写一半损坏。"""
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())  # 掉电/强杀时优先留旧文件而非 0 字节新文件
            os.replace(tmp, self.path)
            tmp = None
        except Exception as exc:
            # M1：写失败（磁盘满 / 杀软锁文件）不能无声吞——布局此刻只在
            # 内存里。落一条日志方便排查；tmp 清掉不残留。
            try:
                self._log_save_error(exc)
            except Exception:
                pass
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    @staticmethod
    def _log_save_error(exc):
        log = str(data_file("storage_error.log"))
        try:
            if os.path.exists(log) and os.path.getsize(log) > 262144:
                os.unlink(log)  # 简单轮转：超 256KB 重写，防无限增长
            import datetime
            with open(log, "a", encoding="utf-8") as f:
                f.write("[%s] save failed: %r\n" % (
                    datetime.datetime.now().isoformat(timespec="seconds"), exc))
        except Exception:
            pass

    # ---------- 访问器 ----------
    @property
    def settings(self) -> dict:
        return self._data["settings"]

    @property
    def layout(self) -> list:
        return self._data["layout"]

    @property
    def widgets(self) -> list:
        return self._data["widgets"]

    @property
    def columns(self) -> int:
        """当前网格列数：优先 GridMetrics 快照（M1 起的默认路径，
        指导文档 §1）；快照未就绪时回退 settings 手动值
        （"auto" -> 静态兜底 10）。"""
        from . import grid_flow
        m = grid_flow.current_metrics()
        if m is not None:
            return m.cols
        raw = self._data["settings"].get("columns", 10)
        if isinstance(raw, str) or raw is None:
            return 10
        return max(2, int(raw))

    def get_setting(self, key):
        return self._data["settings"].get(key, DEFAULT_SETTINGS.get(key))

    def set_setting(self, key, value):
        """写设置。**旧键 `columns` 做兼容转发**（v7 §10.3 迁移）：

        历史代码/测试仍会写 `columns = 6` 或 `"auto"`，v7 起列数的权威键是
        `grid_dims_mode` + `grid_cols`，这里把旧写法翻译过去，避免"写了一个
        键、读的是另一个"的语义漂移。
        """
        self._data["settings"][key] = value
        if key == "columns":
            s = self._data["settings"]
            if isinstance(value, str) and value.strip().lower() in ("", "auto"):
                s["grid_dims_mode"] = "auto"
            else:
                n = _as_positive_int(value)
                if n is not None:
                    s["grid_cols"] = n
                    s["grid_dims_mode"] = "manual"
        self.save()

    # ---------- 接管系统桌面（v6 §7.2 唯一模式开关） ----------
    def takeover(self) -> bool:
        """「接管系统桌面」开关：勾选 = 整理窗口接管桌面（隐藏原生图标、
        框选 / 自己的右键菜单）；取消 = 仅悬浮/退出，原生桌面照常可用。

        唯一语义入口。`hide_native_icons` 降级为兼容键（旧 state / 旧测试
        仍可读写），仅在 takeover_desktop 缺失时回退读取。
        """
        s = self._data.get("settings") or {}
        if "takeover_desktop" in s:
            return bool(s["takeover_desktop"])
        return bool(s.get("hide_native_icons", True))

    def set_takeover(self, value: bool):
        """写接管开关：两个键同步落笔，避免「设置里关了、老代码仍读到开」的
        语义漂移（桌面窗口的 _on_guard_tick 仍在巡检 hide_native_icons）。"""
        s = self._data.setdefault("settings", {})
        s["takeover_desktop"] = bool(value)
        s["hide_native_icons"] = bool(value)
        self.save()

    # ---------- 尺寸缩放（用户缩放单一旋钮；dpr 由 Qt 用一次） ----------
    #
    # v10 B2 / 反馈 2 的定量结论（B1 四格实测，docs/实施计划_v10.md §B1）：
    # Qt6 的高 DPI 缩放**已经生效**——125% 下 primaryScreen().geometry()
    # 是 1536×864（1920/1.25），即「逻辑值 × dpr = 物理值」Qt 已经做过一次。
    # 所以这里再乘 screen_dpr() 就是把系统缩放算两遍：徽标 17 逻辑
    # → 21 逻辑 → 26 物理（×1.5625），而 cell 只有一次（84 → 105）。
    # 病根不是「dpr 太大」，而是**乘了两次**；修法是让 dpr 只存在一次。
    def icon_metric(self) -> int:
        """图标绘制边长（**逻辑 px**）= 基准 × 用户缩放（dpr 不进来）。

        125% 下 72 逻辑由 Qt 渲染成 90 物理——仍随 Windows 缩放同比变大
        （2026-10-18 诉求保留），只是不再叠成 112(=72×1.25×1.25)。
        """
        base = int(self.get_setting("icon_size"))
        return max(24, int(round(base * self.user_icon_scale())))

    def user_icon_scale(self) -> float:
        """用户自定义缩放系数（设置滑条，0.6~2.0，默认 1.0）。"""
        try:
            return min(2.0, max(0.6, float(self.get_setting("icon_scale"))))
        except (TypeError, ValueError):
            return 1.0

    def ui_scale(self) -> float:
        """图标外围 chrome（留白/标签字号/行高/内边距/徽标）的**唯一旋钮**。

        语义 = "chrome 相对逻辑基准的倍数"，值 = 用户图标缩放。
        - dpr 不参与：Qt 已经把逻辑值放大成物理值一次（见上方注释）；
        - cell 也不吃它：cell 由资源管理器原生图标边长反解，四格实测恒
          84×91 逻辑，本来就是对的（B1 数据）。
        """
        return self.user_icon_scale()

    def icon_chrome_scale(self) -> float:
        """deprecated：转发 `ui_scale()`。

        B2 起所有调用点已改叫 `ui_scale()`；保留本名只为插件 SDK 与旧测试
        的向后兼容（语义不再是 "dpr × user"）。v11 可删。
        """
        return self.ui_scale()

    def icon_tier(self) -> str:
        """图标三档（v6 A2，指导文档 §2.2）：small/medium/large，归一化。

        网格模式下 widget 整体 = 档位比例 × cell（grid_sizing.icon_layout），
        与 icon_size/icon_scale 解钩；无界模式不用此档位。
        """
        from . import grid_sizing  # 延迟导入：grid_sizing 零 Qt 依赖
        return grid_sizing.parse_tier(self.get_setting("icon_tier"))

    # ---------- v7 B1：图标占比与网格行列 ----------
    def icon_fit(self):
        """图标在格子里的占比（v7 §3.1）；None 表示"跟随档位"。

        v7c（反馈 3）：**档位是唯一真相源**。三档预设时忽略 `icon_fit`
        里的残留浮点值——旧实现"显式占比优先"留下了两个旋钮，一旦
        `icon_fit` 被写成某个旧浮点（历史版本 / 手拖滑条后档位又被改），
        启动就按旧值渲染，"中"档看着小一号；用户点一次「中」写回 None
        才恢复。现在：预设档 -> 按档位取值（medium 为 None，由
        `resolved_icon_fit()` 用 native_px/cell_w 解算）；只有「自定义」
        档才读 `icon_fit` 本身。
        """
        from . import grid_sizing
        tier = self.icon_tier()
        if tier != grid_sizing.TIER_CUSTOM:
            return grid_sizing.parse_fit(tier)
        raw = self.get_setting("icon_fit")
        if raw is None:
            return None
        return grid_sizing.parse_fit(raw)

    def resolved_icon_fit(self, cw=None) -> float:
        """**实际生效**的图标占比（0.30~0.90），永远有值。

        medium 档的占比 = `native_px / cell_w`（"图标与原桌面一致"的回归
        锁），必须由本函数解算，**不允许调用方各自写死 0.57**：
        旧实现里 `icon_layout()` 拿不到 native 就退常量 0.57，而
        `settings_dialog._effective_fit()` 走 `fit_for()`——换屏（native
        60 / cell 100）时两者差 3px，正是"默认档位看着小一号"的来源。

        cw 传当前 cell 宽（GridMetrics 快照）；不传时回退 `cell_size()`。
        """
        from . import grid_sizing
        fit = self.icon_fit()
        if fit is not None:
            return fit
        native = 0
        try:
            from . import win32_layer
            native = int(win32_layer.native_icon_size())
        except Exception:
            native = 0
        if not cw:
            try:
                from . import grid_flow
                cw = int(grid_flow.cell_size(self)[0])
            except Exception:
                cw = 0
        return grid_sizing.fit_for(native, int(cw or 0), "medium")

    def apply_icon_fit(self, fit, tier=None):
        """**唯一写入点**：同时写 `icon_fit` 与 `icon_tier`。

        `icon_tier` 供右键菜单勾选（`tier_for_fit` 反查最近预设）；
        v7 B2（反馈 9）起可以显式传 `tier="custom"`——用户手拖滑条到
        0.42/0.82 附近时，`tier_for_fit` 会反查成 small/large，但用户
        选的是「自定义」档，勾选状态必须跟着档位而不是数值。
        """
        from . import grid_sizing
        f = grid_sizing.parse_fit(fit)
        s = self._data.setdefault("settings", {})
        s["icon_fit"] = f
        if tier is not None:
            s["icon_tier"] = grid_sizing.parse_tier(tier)
        else:
            s["icon_tier"] = tier_for_fit(f) or grid_sizing.DEFAULT_TIER
        self.save()

    def grid_dims_mode(self) -> str:
        """网格行列模式："auto"（默认）| "manual"。"""
        raw = self.get_setting("grid_dims_mode")
        return "manual" if str(raw).strip().lower() == "manual" else "auto"

    def grid_cols(self):
        """manual 模式的目标列数；None / 非法 -> None（回自动）。"""
        return _as_positive_int(self.get_setting("grid_cols"))

    def grid_rows(self):
        """manual 模式的目标行数；None / 非法 -> None（回自动）。"""
        return _as_positive_int(self.get_setting("grid_rows"))

    def grid_origin_mode(self) -> str:
        """原点模式："auto"（居中，默认）| "manual"（用 grid_origin）。"""
        raw = self.get_setting("grid_origin_mode")
        return "manual" if str(raw).strip().lower() == "manual" else "auto"

    def grid_origin(self):
        """manual 原点的 (x, y)；未设置/非法 -> None（回居中）。"""
        if self.grid_origin_mode() != "manual":
            return None
        raw = self.get_setting("grid_origin")
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            try:
                return int(raw[0]), int(raw[1])
            except (TypeError, ValueError):
                return None
        return None

    def set_grid_origin(self, mode, x=None, y=None):
        """**唯一写入点**：原点两键一起写（v7 §3.2 / 反馈 21）。

        - `set_grid_origin("manual", 100, 50)` -> 手动定位（非整数按 0 处理，
          真正的越界夹紧留给 `grid_sizing.solve_dims`，那里才有网格几何）；
        - `set_grid_origin("auto")` -> 等价 `reset_grid_origin()`。

        两键必须同时写：只写 origin 不改 mode，`grid_origin()` 会返回 None，
        用户看到"设了没生效"；反之只改 mode 会用到上一次的旧坐标。
        """
        s = self._data.setdefault("settings", {})
        if str(mode).strip().lower() != "manual":
            s["grid_origin_mode"] = "auto"
            s["grid_origin"] = None
            self.save()
            return None
        try:
            ox, oy = int(x or 0), int(y or 0)
        except (TypeError, ValueError):
            ox, oy = 0, 0
        s["grid_origin_mode"] = "manual"
        s["grid_origin"] = [ox, oy]
        self.save()
        return ox, oy

    def reset_grid_origin(self):
        """重置到居中（**唯一**把两键一起清空的入口）。

        注意"重置"不等于回到 (26, 22) 边距——居中值由 `solve_dims(origin=None)`
        按当前行列与工作区现算，格子越大居中越靠里。
        """
        return self.set_grid_origin("auto")

    # ---------- 布局辅助 ----------
    def find_entry(self, entry_id):
        for e in self._data["layout"]:
            if e.get("id") == entry_id:
                return e
        return None

    def remove_entry(self, entry_id):
        self._data["layout"] = [e for e in self._data["layout"] if e.get("id") != entry_id]

    def compact_layout(self):
        """最小修正：只修越界与重叠，**不重排顺序**。

        2026-10-18 用户反馈"app 排序还是强制水平的，我要自由排布"：
        旧实现按 (row,col) 排序后顺序铺满，会把用户手工摆放的稀疏布局
        抹平。现在：
          - col 越界（>=columns）夹紧回最后列
          - 同格多条目只保留第一个，其余挪到第一个空格
          - 顺序、空行、空列全部保留——用户想把图标放哪就放哪
        显式「整理布局」（托盘/右键菜单）走 tidy_layout() 才顺序铺满。

        v6 §1.3：无界模式下 col/row 只是**页载体**（位置全在 fx/fy），
        重写它们会打乱页归属——直接跳过。
        """
        from . import grid_flow  # 延迟导入：grid_flow 依赖 storage 的访问器
        if grid_flow.free_mode(self):
            return
        cols = self.columns
        used = set()
        # 2026-10-19 反馈 5：补位也要避开小组件占位，否则重叠时被挤出来的
        # 图标会落到时钟/便签的格子上（用户："整理布局会让组件和图标重叠"）
        widget_used = set(grid_flow.widget_cells(self))
        widget_used |= self._side_blocked()   # dock_side_grid：Dock 占格同样禁放
        for e in sorted(self._data["layout"],
                        key=lambda x: (x.get("row", 0), x.get("col", 0))):
            col = max(0, min(cols - 1, int(e.get("col", 0))))
            row = max(0, int(e.get("row", 0)))
            if (col, row) in used or (col, row) in widget_used:
                col, row = self._first_free_cell(extra_used=used | widget_used)
            used.add((col, row))
            e["col"], e["row"] = col, row

    def tidy_layout(self, pages=None):
        """显式整理：按 (row, col) 排序后顺序铺满为连续网格（用户主动触发）。

        v6 §1.3：无界模式没有网格可铺——跳过（用户要整齐就切回网格模式）。

        2026-10-19 反馈 5：**必须避开小组件占位**。旧实现直接 `i % columns`
        铺满前 N 格，图标压在时钟/便签/面板上——整理一次桌面就花一次。
        与 pack_layout 同一条避让语义（组件是一等公民，占位不可覆盖）。

        v9 V2：`pages` 给定时**逐页整理**（只动这些页，每页从自己的首行
        起排）；`None` = 沿用旧的全局铺满。每页规则开启后，"自动排列"
        只该整理自动页，自由页一格不动。
        """
        from . import grid_flow
        if grid_flow.free_mode(self):
            return
        cols = max(2, int(self.columns))
        used = set(grid_flow.widget_cells(self))   # 组件占位（不可覆盖）
        used |= self._side_blocked()               # dock_side_grid：Dock 占格禁放
        if pages is not None:
            rpp = grid_flow.rows_per_page()
            selected = {max(0, int(p)) for p in pages}
            for page in sorted(selected):
                group = [e for e in self._data["layout"]
                         if grid_flow.page_of(int(e.get("row", 0) or 0), rpp) == page]
                group.sort(key=lambda e: (e.get("row", 0), e.get("col", 0)))
                col, row = 0, page * rpp
                for e in group:
                    while (col, row) in used:
                        col += 1
                        if col >= cols:
                            col, row = 0, row + 1
                    e["col"], e["row"] = col, row
                    used.add((col, row))
                    col += 1
                    if col >= cols:
                        col, row = 0, row + 1
            return
        entries = sorted(self._data["layout"],
                         key=lambda e: (e.get("row", 0), e.get("col", 0)))
        col = row = 0
        for e in entries:
            while (col, row) in used:
                col += 1
                if col >= cols:
                    col, row = 0, row + 1
            e["col"], e["row"] = col, row
            used.add((col, row))
            col += 1
            if col >= cols:
                col, row = 0, row + 1

    def free_icon_box(self):
        """无界模式图标 widget 尺寸（逻辑 px）——**与网格模式同源**。

        v7 B2 起图标尺寸单源（cell × 占比，`grid_sizing.icon_layout`），
        这里必须跟着同一式算：`free_slot` 用它给新条目找不重叠的空位，
        尺寸偏小会让新图标和邻居叠在一起。旧实现按 `icon_px` 复刻
        legacy chrome 尺寸，统一尺寸后就失配了。
        """
        from . import grid_flow, grid_sizing
        m = grid_flow.current_metrics()
        if m is not None:
            lay = grid_sizing.icon_layout(m.cw, m.ch, self.icon_fit())
        else:
            cw, ch = grid_flow.cell_size(self)
            lay = grid_sizing.icon_layout(cw, ch, self.icon_fit())
        return lay.widget_w, lay.widget_h

    def occupant(self, col, row, exclude_id=None):
        for e in self._data["layout"]:
            if e.get("id") == exclude_id:
                continue
            if int(e.get("col", -1)) == col and int(e.get("row", -1)) == row:
                return e
        return None

    def _side_blocked(self) -> set:
        """dock_side_grid 开启时被 Dock 条带占住的格子（禁放集）。

        关闭 / 垂直 Dock / 网格未就绪时为空集；失败静默退化为不禁
        （禁放是功能增强，不能拖垮摆放主路径）。
        """
        try:
            from . import dock as _dock
            return _dock.side_grid_blocked(self)
        except Exception:      # noqa: BLE001
            return set()

    def _first_free_cell(self, exclude_id=None, extra_used=()):
        used = {(e.get("col", 0), e.get("row", 0)) for e in self._data["layout"] if e.get("id") != exclude_id}
        used |= set(extra_used or ())
        cols = self.columns
        col = row = 0
        while (col, row) in used:
            col += 1
            if col >= cols:
                col, row = 0, row + 1
        return col, row

    def first_free_cell(self, extra_used=()):
        """第一个空格（排除 widget 占位 + Dock 禁放格 + 额外占用）。"""
        from . import grid_flow  # 延迟导入：grid_flow 依赖 storage 的尺寸访问器
        used = set(grid_flow.widget_cells(self)) | self._side_blocked()
        if extra_used:
            used |= set(extra_used)
        return self._first_free_cell(extra_used=used)

    def add_app(self, app: dict, col=None, row=None) -> dict:
        """把应用 / 桌面文件添加到网格（默认第一个空格；指定格子被占时挤走占用者）。

        app 自带稳定 id（df: 哈希 / sm: 路径 / sys-xxx）时直接用作条目 id，
        同一应用重复添加返回已有条目，不产生副本。

        v6 §1.3 无界模式：落点走 free_slot（当前页画布内首个不重叠的
        格点对齐位），位置写 fx/fy；col/row 退化为页载体（row = 页号×rpp）。
        显式指定 col/row 的调用（拖入文件等）在无界模式下按像素解释，
        由 _page_local_pos 同式换算成 fx/fy。
        """
        entry_id = app.get("id") or uuid.uuid4().hex[:8]
        existing = self.find_entry(entry_id)
        if existing is not None:
            return existing
        from . import grid_flow  # 延迟导入：grid_flow 依赖 storage 的访问器
        if grid_flow.free_mode(self):
            page = grid_flow.current_page()
            rpp = grid_flow.rows_per_page()
            if col is None or row is None:
                w, h = self.free_icon_box()
                fx, fy = grid_flow.free_slot(self, page, w, h)
            else:
                fx, fy = grid_flow._page_local_pos(self, int(col), int(row))
            entry = {
                "id": entry_id,
                "type": "app",
                "app": app,
                "apps": [],
                "col": 0,
                "row": page * rpp,      # 网格坐标：无界新增按当前页首行落格
                "fpage": page,          # 无界页号（v7 B2 起与 row 分离）
                "fx": int(fx),
                "fy": int(fy),
            }
            self._data["layout"].append(entry)
            return entry
        if col is None or row is None:
            col, row = self.first_free_cell()
        else:
            if (int(col), int(row)) in self._side_blocked():
                # 落点在 Dock 禁放格上（拖文件到 Dock 区域）：改投第一个空格
                col, row = self.first_free_cell()
            else:
                occ = self.occupant(col, row)
                if occ is not None:
                    # B8：被挤走的图标找新格必须与 first_free_cell 同样避开
                    # 小组件占位——否则可能正好落在时钟/便签的格子上，
                    # 图标与组件重叠（compact_layout 是带 widget_used 的）
                    from . import grid_flow
                    nc, nr = self._first_free_cell(
                        exclude_id=occ["id"],
                        extra_used=(set(grid_flow.widget_cells(self))
                                    | self._side_blocked()))
                    occ["col"], occ["row"] = nc, nr
        entry = {
            "id": entry_id,
            "type": "app",
            "app": app,
            "apps": [],
            "col": col,
            "row": row,
        }
        self._data["layout"].append(entry)
        return entry

    def reset_layout(self):
        """重置：清空网格（不删任何文件），由调用方决定是否重新克隆。"""
        self._data["layout"] = []
        self.save()

    # ---------- 文件夹尺寸（v7 反馈 14） ----------
    def folder_frame_default(self):
        """全局默认文件夹尺寸上限 (cols, rows)，缺省 3×3。"""
        from . import folder_layout
        return folder_layout.frame_pair(
            self.get_setting("folder_frame_default"),
            folder_layout.DEFAULT_FRAME)

    def folder_frame(self, entry):
        """单个文件夹的实际尺寸上限：entry["frame"] 优先，否则全局默认。

        返回值是**上限**，实际格数还要按成员数收缩（folder_layout.grid_shape）。
        """
        from . import folder_layout
        return folder_layout.frame_pair(
            (entry or {}).get("frame"), self.folder_frame_default())

    def set_folder_frame(self, entry_id, cols: int, rows: int) -> bool:
        """设置单个文件夹的尺寸上限；与全局默认相同时删掉覆盖键。

        返回是否真的改动了数据（调用方据此决定要不要刷新弹窗）。
        """
        from . import folder_layout
        entry = self.find_entry(entry_id)
        if entry is None or entry.get("type") != "folder":
            return False
        pair = folder_layout.frame_pair([cols, rows], self.folder_frame_default())
        if pair == self.folder_frame_default():
            entry.pop("frame", None)
        else:
            entry["frame"] = [pair[0], pair[1]]
        self.save()
        return True

    def folder_member_ids(self) -> set:
        """所有虚拟文件夹成员的应用 id。

        虚拟文件夹是**布局概念**，磁盘上文件还在桌面目录里——所以
        「重新扫描 / 自动同步」必须排除这些 id，否则会把已收纳的图标
        又播回桌面，造成"同一元素同时在桌面和文件夹"（2026-10-20 反馈 2/8）。
        """
        out = set()
        for e in self._data.get("layout", []):
            if e.get("type") != "folder":
                continue
            for m in e.get("apps") or []:
                if m and m.get("id"):
                    out.add(m["id"])
        return out

    def dedupe_folder_members(self) -> int:
        """同一 app id 同时在桌面与文件夹 -> 删掉桌面那份，保留文件夹成员。

        不修这个的直接后果：从文件夹拖出成员时 `add_app()` 命中同 id 的
        桌面条目 -> 直接 return（不新增），而成员已从文件夹 pop 掉 --
        视觉上就是"拖出来就消失了"。
        """
        dup = self.folder_member_ids()
        if not dup:
            return 0
        lay = self._data.get("layout", [])
        before = len(lay)
        self._data["layout"] = [
            e for e in lay
            if not (e.get("type") == "app" and e.get("id") in dup)
        ]
        return before - len(self._data["layout"])

    def seed_desktop_clone(self, replace: bool = False):
        """把桌面文件克隆进网格（replace=True 先清空；已存在的跳过）。

        「已存在」要**同时**看顶层条目和文件夹成员：虚拟文件夹里的图标
        在磁盘桌面上依然存在，只看顶层会让 rescan 把它们重复播回来。
        """
        from . import appsource  # 延迟导入，避免模块级循环依赖
        if replace:
            self._data["layout"] = []
        existing_targets = set()
        existing_ids = set()
        for e in self._data["layout"]:
            if e.get("type") == "app":
                a = e.get("app") or {}
                if a.get("target"):
                    existing_targets.add(a["target"])
                if e.get("id"):
                    existing_ids.add(e["id"])
            for m in e.get("apps") or []:
                if not m:
                    continue
                if m.get("target"):
                    existing_targets.add(m["target"])
                if m.get("id"):
                    existing_ids.add(m["id"])
        added = 0
        for f in appsource.scan_desktop_files():
            if f["target"] in existing_targets or f["id"] in existing_ids:
                continue
            self.add_app(f)
            added += 1
        self.set_setting("desktop_clone_seeded", True)
        self.save()
        return added

    def pack_layout(self):
        """阅读序紧凑重排（v6 §3.1，播种铺满）。

        所有条目按 (row, col) 排序后从第 0 页起点依次填满，不留空洞；
        组件占位格自动避开。**仅播种 /「重新扫描桌面文件」时调用**——
        加载时不调用，尊重用户自由移动后的留空（§3.2：auto_arrange
        =False 默认，拖完的空格是用户的，不是缺陷）。

        v6 §1.3：无界模式位置在 fx/fy，pack 重写 col/row 会打乱页归属
        与视觉位置——跳过（新条目落点由 add_app 的自由分支负责）。
        """
        from . import grid_flow  # 延迟导入，避免模块级循环依赖
        if grid_flow.free_mode(self):
            return 0
        cols = max(2, int(self.columns))
        used = set(grid_flow.widget_cells(self))   # 组件占位（不可覆盖）
        used |= self._side_blocked()               # dock_side_grid：Dock 占格禁放
        entries = sorted(self._data["layout"],
                         key=lambda e: (int(e.get("row", 0) or 0),
                                        int(e.get("col", 0) or 0)))
        col = row = 0
        for e in entries:
            while (col, row) in used:
                col += 1
                if col >= cols:
                    col, row = 0, row + 1
            e["col"], e["row"] = col, row
            col += 1
            if col >= cols:
                col, row = 0, row + 1
        return len(entries)

    def prune_dead_members(self, live_ids):
        """文件夹成员存活校验（v14j）：磁盘上已消失的文件成员从 apps 剔除。

        外部（资源管理器）把文件移走/删除时，顶层条目有增量同步清理，但
        文件夹成员从不修剪——成员数不减，"只剩一个自动解散"永远触发不了
        （2026-10-25 用户反馈：移走文件后文件夹不自行解除）。返回受影响
        的文件夹 id 列表；调用方（窗口层）负责对 ≤1 成员触发行解散。
        """
        touched = []
        for e in self._data["layout"]:
            if e.get("type") != "folder":
                continue
            apps = e.get("apps") or []
            kept = [m for m in apps
                    if not (m and m.get("kind") == "file"
                            and m.get("id") not in live_ids)]
            if len(kept) != len(apps):
                e["apps"] = kept
                touched.append(e.get("id"))
        return touched

    def rescan_desktop(self, repack: bool = False) -> int:
        """重新扫描桌面：补新增、清丢失（同名路径哈希消失的条目）。

        2026-10-19 v6 §3.1：末尾从 compact_layout（最小修正）改为
        pack_layout（阅读序铺满）——「重新扫描」是用户主动要求的整齐
        操作，与加载时的"尊重留空"区分开。

        2026-10-19 反馈 5 修正：**默认不再铺满**（repack=False）。右键
        「新建文本文档 / 文件夹」内部也走 rescan，铺满会让"加个文件
        桌面就整体重排一遍"。只有显式「整理布局」/ repack=True 才重排；
        默认走 compact_layout 的最小修正（只修越界与重叠，尊重留空）。
        """
        from . import appsource
        live = {f["id"] for f in appsource.scan_desktop_files()}
        self.prune_dead_members(live)       # v14j：文件夹死成员同步修剪
        before = len(self._data["layout"])
        # 文件夹内的成员也可能来自桌面文件：解散语义不变，只清理顶层 df 条目
        self._data["layout"] = [
            e for e in self._data["layout"]
            if not (e.get("type") == "app" and e.get("app", {}).get("kind") == "file"
                    and e.get("id") not in live)
        ]
        added = self.seed_desktop_clone()
        if repack:
            self.pack_layout()
        else:
            self.compact_layout()
        self.save()
        return added, before - (len(self._data["layout"]) - added)

    def sync_desktop_incremental(self):
        """自动同步（v6 §7.4）：文件系统事件触发的增量 diff。

        与 rescan_desktop 的关键区别：**不调 pack_layout**。事件是被动发生的
        （下载保存 / 删除 / 重命名），不该把用户摆好的图标重排一遍；新增条目
        走 add_app 的落点逻辑（网格 = 首个空位；无界 = free_slot）。

        1:1 增删视为「重命名」，新条目继承旧条目的位置（改个名不该换位置）；
        2:2 及以上不做配对，避免把无关文件错配到别人位置上。

        返回 (added, removed_ids)。
        """
        from . import appsource
        files = appsource.scan_desktop_files()
        live_ids = {f["id"] for f in files}
        self.prune_dead_members(live_ids)   # v14j：文件夹死成员增量修剪

        moved, removed_ids, kept = [], [], []
        for e in self._data["layout"]:
            app = e.get("app") or {}
            is_desktop_file = (e.get("type") == "app"
                               and app.get("kind") == "file")
            if is_desktop_file and e.get("id") not in live_ids:
                moved.append((e.get("col"), e.get("row"),
                              e.get("fx"), e.get("fy")))
                removed_ids.append(e.get("id"))
                continue
            kept.append(e)
        if removed_ids:
            self._data["layout"] = kept

        existing = set()
        existing_ids = set()
        for e in kept:
            if e.get("type") == "app":
                a = e.get("app") or {}
                if a.get("target"):
                    existing.add(a["target"])
                if e.get("id"):
                    existing_ids.add(e["id"])
            for m in e.get("apps") or []:
                if not m:
                    continue
                if m.get("target"):
                    existing.add(m["target"])
                if m.get("id"):
                    existing_ids.add(m["id"])
        new_ids = []
        for f in files:
            if f["target"] in existing or f["id"] in existing_ids:
                continue
            entry = self.add_app(f)
            if entry is not None and entry.get("id") not in new_ids:
                new_ids.append(entry.get("id"))

        # 重命名：恰好 1 删 1 增 → 继承位置（双模式各写各的坐标）
        if len(new_ids) == 1 and len(moved) == 1:
            col, row, fx, fy = moved[0]
            for e in self._data["layout"]:
                if e.get("id") != new_ids[0]:
                    continue
                if fx is not None and fy is not None:
                    e["fx"], e["fy"] = int(fx), int(fy)
                if col is not None and row is not None:
                    e["col"], e["row"] = int(col), int(row)
                break

        if new_ids or removed_ids:
            self.save()
        return len(new_ids), removed_ids

    def ensure_desktop_clone(self, force: bool = False):
        """启动 / 开启整理模式时调用：把桌面文件播种进网格。

        2026-10-18 路线A 拍板后的语义：
          - **接管系统桌面（takeover_desktop，v6 §7.2 唯一模式开关）开启
            且网格为空 -> 无条件播种**：路线A 承诺"桌面文件全部映射"，
            空布局 + 接管 = 用户面对空桌面 = 文件丢失观感（probe16 G2 抓到
            旧实现里历史标志 + auto_clone 双重拦截会导致播种被跳过）。
            **不依赖 desktop_clone_seeded/v2 历史标志**（旧版标志残留曾导致
            重新开启接管时网格不恢复，是"加载的文件缺少很多"的根因之一）
          - 布局非空时按 auto_clone_desktop 决定：开=启动补播新增；
            关=尊重用户的手动删除（删过的条目重启不回来）
          - force=True：用户主动操作（开启接管 / 重新扫描），无视
            auto_clone 与空布局判断，强制补扫
          - 每次调用都补扫一次，幂等：已有条目跳过、只增不减（丢失文件的
            清理交给显式「重新扫描桌面文件」，不碰用户布局）
        """
        if not (force or self.takeover()):
            return 0
        empty = not self._data["layout"]
        if not force and not empty and not self.get_setting("auto_clone_desktop"):
            return 0
        added = self.seed_desktop_clone()
        self.ensure_second_page()
        return added

    def ensure_second_page(self):
        """保证至少有 2 页（v14 反馈 4：默认给一页空白页）。

        只有一页时页点不画（`_dot_centers` total<=1 返回空），新手指引的
        「分页」那一步也会被跳过——用户根本学不到"可以左右翻页"。默认补一页
        空白页，页点出现、翻页手势有处可用。

        一次性（`blank_page_seeded`）：用户主动删掉空白页后不再补，否则
        「删除空白页」变成删不掉。
        """
        if self.get_setting("blank_page_seeded"):
            return False
        from . import grid_flow
        try:
            if grid_flow.page_count(self) >= 2:
                self.set_setting("blank_page_seeded", True)
                return False
            self.set_setting("extra_pages", [1])
            self.set_setting("blank_page_seeded", True)
            return True
        except Exception:      # noqa: BLE001 —— 补页失败不影响启动
            return False

    def migrate_to_native_mode(self):
        """已废弃（2026-10-18 路线A 拍板后保留空实现兼容旧调用）。

        历史：Day2 曾把"自动克隆 + 隐藏原生图标"的旧状态迁移为原生模式
        （关整理模式 + 清空网格）。用户明确选择路线A（隐藏原图标全部映射）
        后该迁移反变为破坏行为——会偷偷关掉用户刚开好的整理模式并清空其
        布局。现不再做任何事；旧 state 的 hide_native_icons 若为 True 则
        原样保留，由 ensure_desktop_clone(force=True) 补播。
        """
        return False
