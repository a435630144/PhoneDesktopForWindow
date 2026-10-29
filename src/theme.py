# -*- coding: utf-8 -*-
"""设计 tokens：深色半透明玻璃面板风格（Windows 11）。

桌面带上的全部视觉元素（图标网格、文件夹、弹窗、小组件、桌面面板）
统一从这里取色 / 取字 / 取圆角，保证整体观感一致：

  - 表面：近黑深灰 + 高不透明度，壁纸若隐若现
  - 边框：白色低透明度细线，界定面板边界
  - 文字：三级层级（主 / 次 / 弱）
  - 强调：Windows 11 蓝（#4CC2FF），仅用于选中 / 进度 / 交互反馈
  - 字体：Microsoft YaHei UI（Win11 中文 UI 字体）
"""
from PySide6.QtGui import QColor, QFont

# ---------- 表面 ----------
SURFACE = QColor(30, 30, 36, 226)          # 主面板底（半透明，透出壁纸）
SURFACE_POPUP = QColor(28, 28, 34, 240)    # 弹窗底（更实，保证可读）
SURFACE_RAISED = QColor(255, 255, 255, 14)  # 面板内嵌元素（输入框 / 卡片）
BORDER = QColor(255, 255, 255, 26)         # 面板描边
BORDER_STRONG = QColor(255, 255, 255, 40)  # 强描边（选中态）

# ---------- 文字 ----------
TEXT = QColor(242, 243, 245)               # 主文字
TEXT_DIM = QColor(168, 173, 181)            # 次文字
TEXT_FAINT = QColor(140, 146, 155)          # 弱文字（占位 / 注释）
# 注：TEXT_FAINT 旧值 (120,126,135) 在深色面上对比度仅 4.05:1，低于 WCAG AA
# 对 <18px 文字要求的 4.5:1；提亮到 140 后为 5.3:1，过检（2026-10-18）。

# ---------- 强调 ----------
ACCENT = QColor(76, 194, 255)              # Windows 11 蓝
ACCENT_SOFT = QColor(76, 194, 255, 56)     # 选中底色
ACCENT_BORDER = QColor(76, 194, 255, 150)  # 选中描边
DANGER = QColor(232, 88, 88)               # 关闭按钮悬停

# ---------- 状态 ----------
HOVER = QColor(255, 255, 255, 20)          # 悬停底色
HOVER_BORDER = QColor(255, 255, 255, 36)

# ---------- 图标标签 ----------
LABEL_SIZE = 12
LABEL_COLOR = QColor(244, 245, 247)
LABEL_SHADOW = QColor(0, 0, 0, 190)

# ---------- 文件夹（安卓风深色圆角方形） ----------
FOLDER_BG = QColor(48, 48, 56, 205)
FOLDER_BG_TOP = QColor(66, 66, 77)         # 瓷砖渐变：顶部受光
FOLDER_BG_BOTTOM = QColor(40, 40, 49)      # 瓷砖渐变：底部沉实
FOLDER_HILIGHT = QColor(255, 255, 255, 60)  # 顶部 1px 内高光（与玻璃语言统一）
FOLDER_BORDER = QColor(255, 255, 255, 34)

# ---------- 文件夹背景色板（M5，指导文档 §4.2「背景色」D5） ----------
# 16 预设 + 「自动」（成员图标平均色）+ 「自定义」（取色盘）。
# entry["tint"] 存 "#RRGGBB"；图标渐变（_paint_folder）与弹窗背板共用同一
# 颜色（iOS 文件夹语言）。前 8 个是 v6 起的老色号（不能删——老 state 里
# 存的就是这些值），后 8 个是 2026-10-20 用户反馈 1「多增加几个预设」。
# 两行各 8 个正好排满一个色板表单。
FOLDER_TINTS = [
    # —— 第一行：饱和色（iOS 文件夹主色系）——
    QColor(94, 92, 230),   # 蓝紫（默认）
    QColor(232, 93, 116),  # 粉红
    QColor(240, 152, 62),  # 橙
    QColor(245, 205, 82),  # 黄
    QColor(88, 186, 120),  # 绿
    QColor(64, 176, 190),  # 青
    QColor(84, 132, 238),  # 蓝
    QColor(148, 108, 214), # 紫
    # —— 第二行：深浅两端 + 低饱和（覆盖"想低调一点"的需求）——
    QColor(196, 84, 72),   # 砖红
    QColor(214, 118, 178), # 洋红
    QColor(150, 126, 84),  # 卡其
    QColor(122, 196, 132), # 薄荷
    QColor(72, 140, 144),  # 墨绿
    QColor(108, 122, 158), # 石板蓝
    QColor(176, 176, 184), # 银灰
    QColor(60, 62, 72),    # 石墨（近黑）
]

# ---------- 圆角 ----------
RADIUS_PANEL = 14
RADIUS_CARD = 12
RADIUS_ITEM = 10

# ---------- 液态玻璃（苹果 Liquid Glass 风格 tokens） ----------
# 用于文件夹弹窗等浮层：背后内容模糊透出 + tint + 边缘高光。
# 实现见 src/effects.py（抓屏 -> 高斯模糊 -> 自绘，不用 acrylic API——
# acrylic 与 Qt 的 WA_TranslucentBackground+WS_EX_LAYERED 组合冲突）。
GLASS_RADIUS = 20            # 玻璃大圆角（液态感）
GLASS_TINT_ALPHA = 150       # 玻璃 tint 不透明度（中部）
GLASS_SPECULAR = 135         # 顶部高光峰值 alpha（液态玻璃的灵魂）
GLASS_BORDER = 52            # 玻璃描边 alpha

# ---------- 字体 ----------
FONT_FAMILIES = ["Microsoft YaHei UI", "Segoe UI", "Microsoft YaHei"]
# 图标标签专用（v15f）：Windows 桌面标签就是 **Segoe UI 9pt**，把 Segoe 放
# 第一位，西文用它、中文自动回退雅黑——这正是资源管理器/桌面的取字方式。
# 用雅黑打头的 FONT_FAMILIES 画标签时，拉丁字形会比系统宽一档、重一档，
# 用户看到的就是"字体还是不对、不像原版"。
LABEL_FONT_FAMILIES = ["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"]

# ---------- DPI ----------
_DPR_CACHE = None


def screen_dpr() -> float:
    """主屏 DPI 缩放比（1.0 = 100%，1.25 = 125%…）。

    **只给 device-pixel 资产生用**（`QPixmap` 的像素尺寸、画笔线宽等，
    如 `_combo_arrow_path`）。**不要**拿它乘逻辑像素值（部件尺寸、内边距、
    标签字号）——Qt6 的高 DPI 缩放已经把逻辑值渲染成物理值时乘过一次，
    再乘就是二次放大（v10 B2 / 反馈 2 的定量病根，见 storage.ui_scale）。

    Windows 缩放变化时图标/网格要跟着变大（用户反馈：图标大小没有随
    Windows 缩放比例变化——2026-10-18）。首次调用后缓存；多屏切换场景
    本应用目标为单屏 Win11，缓存足够。
    必须在 QApplication 创建之后调用（用到 primaryScreen）。
    """
    global _DPR_CACHE
    if _DPR_CACHE is None:
        try:
            from PySide6.QtGui import QGuiApplication
            _DPR_CACHE = max(1.0, QGuiApplication.primaryScreen().devicePixelRatio())
        except Exception:
            _DPR_CACHE = 1.0
    return _DPR_CACHE


def ui_font(pixel_size: int, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(pixel_size)
    f.setBold(bold)
    return f


def label_font(pixel_size: int, bold: bool = False) -> QFont:
    """桌面图标标签字体（v15f：复刻 Windows —— Segoe UI 优先 + 中文回退）。

    两个细节按系统桌面校准：
    - 字距 100%（Qt 默认按字体自身的 kerning；雅黑的 PercentageSpacing
      会被某些路径改成 105%，视觉上"字被拉开了"，这里显式钉死）；
    - 不做 stylistic 加粗——`bold=True` 只在用户主动开「加粗」时生效。
    """
    f = QFont()
    f.setFamilies(LABEL_FONT_FAMILIES)
    f.setPixelSize(pixel_size)
    f.setBold(bold)
    f.setLetterSpacing(QFont.PercentageSpacing, 100.0)
    return f


def rgba(color: QColor) -> str:
    return "rgba(%d, %d, %d, %d)" % (color.red(), color.green(), color.blue(), color.alpha())


# ---------- 常用 QSS（QWidget 系组件用） ----------
INPUT_QSS = (
    "QLineEdit{background: %s; color: rgb(242,243,245); border: 1px solid %s;"
    " border-radius: 10px; padding: 8px 12px 8px 34px; font-family: %s; font-size: 13px;}"
    "QLineEdit:focus{border: 1px solid %s;}"
) % (rgba(SURFACE_RAISED), rgba(BORDER), ", ".join('"%s"' % f for f in FONT_FAMILIES), rgba(ACCENT_BORDER))

LIST_QSS = (
    "QListWidget{background: %s; border: 1px solid %s; border-radius: 10px;"
    " color: rgb(242,243,245); font-family: %s; font-size: 13px; outline: none;}"
    "QListWidget::item{padding: 6px 8px; border-radius: 8px;}"
    "QListWidget::item:hover{background: %s;}"
    "QListWidget::item:selected{background: %s; color: rgb(242,243,245);}"
) % (rgba(SURFACE_RAISED), rgba(BORDER), ", ".join('"%s"' % f for f in FONT_FAMILIES),
     rgba(HOVER), rgba(ACCENT_SOFT))

BUTTON_QSS = (
    "QPushButton{background: %s; color: rgb(242,243,245); border: 1px solid %s;"
    " border-radius: 8px; padding: 6px 16px; font-family: %s; font-size: 13px;}"
    "QPushButton:hover{background: %s;}"
    "QPushButton:pressed{background: %s;}"
    # v14 反馈 10：禁用态必须看得出来（"液态玻璃开着时颜色不可调"）
    "QPushButton:disabled{background: rgba(255,255,255,6);"
    " color: rgba(242,243,245,88); border: 1px solid rgba(255,255,255,16);}"
) % (rgba(SURFACE_RAISED), rgba(BORDER), ", ".join('"%s"' % f for f in FONT_FAMILIES),
     rgba(HOVER), rgba(ACCENT_SOFT))

CHECKBOX_QSS = (
    "QCheckBox{color: rgb(242,243,245); font-family: %s; font-size: 13px;}"
    "QCheckBox::indicator{width: 16px; height: 16px; border-radius: 4px;"
    " border: 1px solid %s; background: %s;}"
    "QCheckBox::indicator:checked{background: %s; border: 1px solid %s;}"
) % (", ".join('"%s"' % f for f in FONT_FAMILIES), rgba(BORDER_STRONG),
     rgba(SURFACE_RAISED), rgba(ACCENT), rgba(ACCENT))

SLIDER_QSS = (
    "QSlider::groove:horizontal{height: 4px; background: %s; border-radius: 2px;}"
    "QSlider::handle:horizontal{width: 16px; height: 16px; margin: -6px 0;"
    " border-radius: 8px; background: rgb(76,194,255);}"
    # 禁用态：手柄退成灰，滑条整体"关掉"的感觉（v14 反馈 10）
    "QSlider::handle:horizontal:disabled{background: rgba(242,243,245,72);}"
    "QSlider::groove:horizontal:disabled{background: rgba(255,255,255,12);}"
) % rgba(SURFACE_RAISED)

SPIN_QSS = (
    "QSpinBox{background: %s; color: rgb(242,243,245); border: 1px solid %s;"
    " border-radius: 8px; padding: 4px 6px; font-family: %s; font-size: 13px;}"
) % (rgba(SURFACE_RAISED), rgba(BORDER), ", ".join('"%s"' % f for f in FONT_FAMILIES))

DIALOG_QSS = "QDialog{background: rgb(34, 34, 40);}"

# ---------- 设置卡片（v6 §7.3 分组卡片式重设计） ----------
# 卡片 = 深色底上更浅的一层（白色低 alpha + 描边 + RADIUS_CARD），不引入
# 新的灰色阶，保持与桌面带 / 弹窗同一套表面语言；标题用 ACCENT 主色——
# 层级靠「主色标题 + 卡片边界」表达，而不是把正文调得更白更亮。
CARD_BG = QColor(255, 255, 255, 10)
CARD_SPACING = 12          # 卡片之间（统一间距，替代各处魔法数）
CARD_PAD = 14              # 卡片内边距

CARD_QSS = (
    "QFrame#settingsCard{background: %s; border: 1px solid %s;"
    " border-radius: %dpx;}"
) % (rgba(CARD_BG), rgba(BORDER), RADIUS_CARD)

CARD_TITLE_QSS = (
    "color: %s; font-family: %s; font-size: 13px; font-weight: 600;"
) % (rgba(ACCENT), ", ".join('"%s"' % f for f in FONT_FAMILIES))

SCROLL_QSS = (
    "QScrollArea{background: transparent; border: none;}"
    "QScrollBar:vertical{background: transparent; width: 8px; margin: 2px;}"
    "QScrollBar::handle:vertical{background: %s; border-radius: 4px; min-height: 28px;}"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{height: 0;}"
) % rgba(BORDER_STRONG)

# 下拉框：不写 QSS 会回退成系统默认（深色对话框里是纯黑底 + 黑字，不可读，
# 2026-10-19 设置重设计截图目检抓到）。down-arrow 用 border 三角自绘，
# 避免依赖图片资源；弹出列表同样走深色表面，悬停/选中用 ACCENT。
COMBO_QSS = (
    "QComboBox{background: %s; color: rgb(242,243,245); border: 1px solid %s;"
    " border-radius: 8px; padding: 4px 10px; min-height: 22px;"
    " font-family: %s; font-size: 13px;}"
    "QComboBox:hover{border: 1px solid %s;}"
    "QComboBox::drop-down{width: 22px; border: none;}"
    "QComboBox::down-arrow{width: 0; height: 0; margin-right: 8px;"
    " border-left: 4px solid transparent; border-right: 4px solid transparent;"
    " border-top: 5px solid %s;}"
    "QComboBox QAbstractItemView{background: rgb(38,38,44); color: rgb(242,243,245);"
    " border: 1px solid %s; border-radius: 8px; padding: 4px; outline: none;"
    " selection-background-color: %s;}"
) % (rgba(SURFACE_RAISED), rgba(BORDER),
     ", ".join('"%s"' % f for f in FONT_FAMILIES), rgba(BORDER_STRONG),
     rgba(TEXT_DIM), rgba(BORDER), rgba(ACCENT_SOFT))

MENU_QSS = (
    "QMenu{background: rgb(38, 38, 44); color: rgb(242,243,245);"
    " border: 1px solid rgba(255,255,255,26); border-radius: 10px; padding: 6px;"
    " font-family: %s; font-size: 13px;}"
    "QMenu::item{padding: 6px 24px 6px 16px; border-radius: 6px;}"
    "QMenu::item:selected{background: rgba(76,194,255,46);}"
    "QMenu::separator{height: 1px; background: rgba(255,255,255,20); margin: 4px 8px;}"
) % ", ".join('"%s"' % f for f in FONT_FAMILIES)

# ---------- 设置窗口（2026-10-19 苹果/AirDeck 风格重构） ----------
# 结构：侧栏(#settingsSide) + 内容区(#settingsContent)。
# 文字层级靠字号/字重/颜色三档表达，不再是"全是白字"或"全是灰字"：
#   页标题 22px 600 主文字 / 副标题 13px 次级 / 行标题 14px 主文字 /
#   行说明 12px 弱文字（对比度 5.3:1，过 WCAG AA）。
SETTINGS_QSS = (
    "QDialog{background: rgb(22,22,26);}"
    "QWidget#settingsContent{background: rgb(22,22,26);}"
    "QFrame#settingsSide{background: rgb(30,30,35);"
    " border-right: 1px solid rgba(255,255,255,18);}"
    "QLabel#brandLogo{background: rgb(76,194,255); color: rgb(12,18,26);"
    " border-radius: 10px; font-size: 17px; font-weight: 700;"
    " font-family: %(fam)s;}"
    "QLabel#brandName{color: rgb(242,243,245); font-size: 14px; font-weight: 600;"
    " font-family: %(fam)s;}"
    "QLabel#brandSub{color: rgba(168,173,181,220); font-size: 10px;"
    " font-family: %(fam)s;}"
    "QLabel#sideVersion{color: rgba(140,146,155,220); font-size: 11px;"
    " font-family: %(fam)s;}"
    "QPushButton#navItem{text-align: left; padding: 9px 12px; border: none;"
    " border-radius: 9px; color: rgba(206,210,218,235); font-size: 13px;"
    " font-family: %(fam)s; background: transparent;}"
    "QPushButton#navItem:hover{background: rgba(255,255,255,16);"
    " color: rgb(242,243,245);}"
    "QPushButton#navItem:checked{background: rgb(46,120,246);"
    " color: rgb(255,255,255); font-weight: 600;}"
    "QLabel#pageTitle{color: rgb(246,247,250); font-size: 22px; font-weight: 600;"
    " font-family: %(fam)s;}"
    "QLabel#pageSubtitle{color: rgba(168,173,181,235); font-size: 13px;"
    " font-family: %(fam)s;}"
    "QFrame#settingsCard{background: rgba(255,255,255,9);"
    " border: 1px solid rgba(255,255,255,16); border-radius: 12px;}"
    "QLabel#cardTitle{color: rgba(150,156,166,240); font-size: 12px;"
    " font-weight: 600; font-family: %(fam)s;}"
    "QWidget#settingsRow{background: transparent; border: none;}"
    "QLabel#rowTitle{color: rgb(238,240,244); font-size: 13px;"
    " font-family: %(fam)s;}"
    "QLabel#rowHint{color: rgba(150,156,166,235); font-size: 11px;"
    " font-family: %(fam)s;}"
    "QLabel{color: rgb(238,240,244); background: transparent; font-family: %(fam)s;}"
    # 裸 QCheckBox（非 Switch）：跟随主题亮字，不走系统默认黑字
    "QCheckBox{color: rgb(238,240,244); font-family: %(fam)s; font-size: 13px;}"
    "QCheckBox::indicator{width: 16px; height: 16px; border-radius: 4px;"
    " border: 1px solid rgba(255,255,255,40); background: rgba(255,255,255,14);}"
    "QCheckBox::indicator:checked{background: rgb(46,120,246);"
    " border: 1px solid rgb(46,120,246);}"
    "QCheckBox:disabled{color: rgba(150,156,166,140);}"
    "QPushButton{background: rgba(255,255,255,16); color: rgb(240,242,246);"
    " border: 1px solid rgba(255,255,255,26); border-radius: 8px;"
    " padding: 6px 14px; font-size: 12px; font-family: %(fam)s;}"
    "QPushButton:hover{background: rgba(255,255,255,28);}"
    "QPushButton:pressed{background: rgba(255,255,255,10);}"
    # 侧栏左下角 GitHub 入口（v11 反馈 34）：图标按钮，悬停才亮边框
    "QPushButton#githubLink{background: rgba(255,255,255,8);"
    " border: 1px solid rgba(255,255,255,16); border-radius: 9px; padding: 0;}"
    "QPushButton#githubLink:hover{background: rgba(255,255,255,26);"
    " border-color: rgba(255,255,255,46);}"
    "QPushButton#githubLinkText{background: transparent; border: none;"
    " color: rgba(150,214,255,235); font-size: 10px; padding: 0;}"
    # 禁用态：自动模式下「重置网格位置」不该看起来能点（v7 原点组）
    "QPushButton:disabled{color: rgba(150,156,166,120);"
    " background: rgba(255,255,255,6); border-color: rgba(255,255,255,14);}"
    "QPushButton#primaryButton{background: rgb(46,120,246); border: none;"
    " color: white; font-weight: 600; padding: 7px 22px;}"
    "QPushButton#primaryButton:hover{background: rgb(64,138,255);}"
    # 卡片标题右侧的「重置」小按钮（v12 反馈 35）
    "QPushButton#resetBtn{background: rgba(255,255,255,10);"
    " color: rgba(150,214,255,220); border: 1px solid rgba(255,255,255,18);"
    " border-radius: 6px; padding: 2px 10px; font-size: 11px;"
    " font-family: %(fam)s;}"
    "QPushButton#resetBtn:hover{background: rgba(76,194,255,30);"
    " border-color: rgba(76,194,255,80); color: rgb(180,225,255);}"
    "QComboBox{background: rgba(255,255,255,13); color: rgb(240,242,246);"
    " border: 1px solid rgba(255,255,255,26); border-radius: 7px;"
    " padding: 4px 10px; min-height: 22px; font-size: 12px;"
    " font-family: %(fam)s; min-width: 132px;}"
    "QComboBox:hover{border: 1px solid rgba(255,255,255,60);}"
    "QComboBox::drop-down{width: 20px; border: none;}"
    "QComboBox::down-arrow{width: 0; height: 0; margin-right: 8px;"
    " border-left: 4px solid transparent; border-right: 4px solid transparent;"
    " border-top: 5px solid rgba(200,205,214,235);}"
    "QComboBox QAbstractItemView{background: rgb(38,38,44); color: rgb(242,243,245);"
    " border: 1px solid rgba(255,255,255,26); border-radius: 8px; padding: 4px;"
    " outline: none; selection-background-color: rgba(46,120,246,120);}"
    "QSpinBox{background: rgba(255,255,255,13); color: rgb(240,242,246);"
    " border: 1px solid rgba(255,255,255,26); border-radius: 7px;"
    " padding: 4px 6px; font-size: 12px; font-family: %(fam)s; min-width: 64px;}"
    "QSpinBox::up-button, QSpinBox::down-button{width: 14px; border: none;"
    " background: transparent;}"
    # 禁用态：自动模式下两个 spin 只是"展示区间"，必须看得出不可改
    "QSpinBox:disabled{color: rgba(150,156,166,140);"
    " background: rgba(255,255,255,6); border-color: rgba(255,255,255,14);}"
    "QComboBox:disabled{color: rgba(150,156,166,140);"
    " background: rgba(255,255,255,6); border-color: rgba(255,255,255,14);}"
    "QSlider::groove:horizontal{height: 4px; background: rgba(255,255,255,28);"
    " border-radius: 2px;}"
    "QSlider::sub-page:horizontal{background: rgb(46,120,246); border-radius: 2px;}"
    "QSlider::handle:horizontal{width: 14px; height: 14px; margin: -5px 0;"
    " border-radius: 7px; background: rgb(255,255,255);}"
    # 禁用态（v7 B2 反馈 9）：三档预设下"自定义比例"只读，必须看得出
    # 不能拖——不写 :disabled 时滑条外观与可用态完全一样。
    "QSlider::sub-page:horizontal:disabled{background: rgba(255,255,255,40);}"
    "QSlider::handle:horizontal:disabled{background: rgba(255,255,255,80);}"
    "QScrollArea{background: transparent; border: none;}"
    "QScrollBar:vertical{background: transparent; width: 8px; margin: 2px;}"
    "QScrollBar::handle:vertical{background: rgba(255,255,255,44);"
    " border-radius: 4px; min-height: 30px;}"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{height: 0;}"
    "QMessageBox{background: rgb(30,30,35);}"
    "QMessageBox QLabel{color: rgb(240,242,246); font-size: 13px;}"
    "QToolTip{background: rgb(38,38,44); color: rgb(230,232,236);"
    " border: 1px solid rgba(255,255,255,30); border-radius: 6px;"
    " padding: 4px 8px; font-size: 12px; font-family: %(fam)s;}"
) % {"fam": ", ".join('"%s"' % f for f in FONT_FAMILIES)}

# QSS 画三角（width:0 + 三边 border）在 Qt 的 subcontrol 上会被强制成实心
# 方块——下拉箭头变成一个白灰小块。正解是给 ::down-arrow 一张真图，这里
# 运行时按 DPR 画一张锐利的下折线 PNG 到 app data，再注入 QSS。
_ARROW_CACHE = None


def _combo_arrow_path() -> str:
    """生成（并缓存）下拉箭头 PNG 的 QSS 可用路径（正斜杠绝对路径）。"""
    global _ARROW_CACHE
    if _ARROW_CACHE is not None:
        return _ARROW_CACHE
    try:
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

        from .paths import data_file, ensure_app_dirs

        dpr = max(1.0, float(screen_dpr()))
        w, h = int(round(10 * dpr)), int(round(6 * dpr))
        pm = QPixmap(max(1, w), max(1, h))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(198, 204, 214, 240), max(1.4, 1.5 * dpr))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline([QPointF(1.6 * dpr, 1.8 * dpr),
                        QPointF(5.0 * dpr, 4.8 * dpr),
                        QPointF(8.4 * dpr, 1.8 * dpr)])
        p.end()
        ensure_app_dirs()
        path = data_file("ui_arrow_down.png")
        if not pm.save(str(path), "PNG"):
            _ARROW_CACHE = ""
        else:
            _ARROW_CACHE = str(path).replace("\\", "/")
    except Exception:
        _ARROW_CACHE = ""
    return _ARROW_CACHE


def settings_qss() -> str:
    """设置窗口最终 QSS：基础样式 + 运行时箭头图（图生成失败则退化）。"""
    arrow = _combo_arrow_path()
    if not arrow:
        return SETTINGS_QSS
    return SETTINGS_QSS + (
        "QComboBox::down-arrow{image: url(%s); width: 10px; height: 6px;"
        " border: none; margin-right: 9px;}" % arrow
    )
