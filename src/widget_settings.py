# -*- coding: utf-8 -*-
"""组件设置面板（v8 C8 / v9 V1 扩展）。

组件不自己写设置界面：在注册表的 `WidgetSpec.settings` 里声明
`FieldSpec` 列表，本模块按声明生成表单。好处是

  - 所有组件的设置长得一样（标签宽度、控件间距、按钮位置统一）；
  - "有没有设置"就是"声明列表空不空"，右键菜单据此决定是否出现入口
    （不留灰项——灰项等于"点不动"，用户会以为坏了）；
  - 新组件加设置 = 加一行 FieldSpec，不写 UI 代码。

v9 起按 `FieldSpec.group` 分组：组件自己的字段归「组件设置」，通用外观
（背景色/透明度/文字色/强调色/圆角）归「外观」——用户一眼能分清
"这个组件特有的" 与 "所有组件都有的"。

存储位置：`entry["settings"][key]`。组件自己实现 `on_settings_changed()`
决定要不要立即重绘 / 重拉数据（天气就是重拉）。
"""
from __future__ import annotations

from typing import Dict, List, Optional
from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from . import i18n
from . import theme
from .ui_choice import ComboBox
from .widget_registry import FieldSpec, WidgetSpec
from .widget_style import MAX_ALPHA, MIN_ALPHA, liquid_enabled

LABEL_W = 96
DEFAULT_GROUP = "组件设置"

DLG_QSS = (
    "QDialog{background: %s;}"
    "QLabel{color: %s; font-size: 13px; background: transparent;}"
    "QLabel#hint{color: %s; font-size: 11px;}"
    "QLabel#group{color: %s; font-size: 12px; font-weight: 600;}"
    "QCheckBox{color: %s; font-family: %s; font-size: 13px;}"
    "QSpinBox{color: %s; background: %s; border: 1px solid %s;"
    " border-radius: 7px; padding: 4px 6px; font-size: 12px;}"
    "QSpinBox::up-button, QSpinBox::down-button{width: 14px; border: none;"
    " background: transparent;}"
    "QLineEdit{color: %s; background: %s; border: 1px solid %s;"
    " border-radius: 8px; padding: 6px 10px; font-size: 13px;}"
    "QToolTip{background: rgb(38,38,44); color: rgb(230,232,236);"
    " border: 1px solid rgba(255,255,255,30); border-radius: 6px;"
    " padding: 4px 8px; font-size: 12px;}"
) % (theme.rgba(theme.SURFACE_POPUP), theme.rgba(theme.TEXT),
     theme.rgba(theme.TEXT_FAINT), theme.rgba(theme.TEXT_DIM),
     theme.rgba(theme.TEXT), ", ".join('"%s"' % f for f in theme.FONT_FAMILIES),
     theme.rgba(theme.TEXT), theme.rgba(theme.SURFACE_RAISED),
     theme.rgba(theme.BORDER),
     theme.rgba(theme.TEXT), theme.rgba(theme.SURFACE_RAISED),
     theme.rgba(theme.BORDER))


def field_label(f) -> str:
    """字段标签（`field.<key>.label` 覆盖，缺省用声明里的中文）。"""
    return i18n.TL("field.%s.label" % f.key, f.label)


def field_hint(f) -> str:
    return i18n.TL("field.%s.hint" % f.key, f.hint)


def field_group(group: str) -> str:
    return i18n.TL("field.group." + group, group)


def current_values(spec: WidgetSpec, settings: Optional[Dict]) -> Dict:
    """把 entry 里已有的设置与声明默认值合并（缺的用默认）。"""
    s = settings if isinstance(settings, dict) else {}
    out = {}
    for f in spec.settings:
        if f.key == "bg_liquid":
            # 推导值（没设过颜色 = 默认液态），不能让面板显示"关"但实际在画液态
            out[f.key] = liquid_enabled(s)
        else:
            out[f.key] = s.get(f.key, f.default)
    return out


def apply_values(entry: dict, values: Dict, storage) -> None:
    """把值写进 entry["settings"] 并落盘（其它键原样保留）。"""
    merged = dict(entry.get("settings") or {})
    merged.update(values)
    entry["settings"] = merged
    try:
        storage.save()
    except Exception:      # noqa: BLE001
        pass


def apply_to_widget(widget, spec: WidgetSpec, values: Dict, storage) -> None:
    """写入并通知组件刷新（组件库/测试都可直接调，不必开对话框）。"""
    apply_values(widget.entry, values, storage)
    cb = getattr(widget, "on_settings_changed", None)
    if callable(cb):
        cb()


def groups_of(spec: WidgetSpec) -> Dict[str, List[FieldSpec]]:
    """按 group 分组（保持声明顺序；组顺序 = 首次出现顺序）。"""
    out: Dict[str, List[FieldSpec]] = {}
    for f in spec.settings:
        out.setdefault(f.group or DEFAULT_GROUP, []).append(f)
    return out


class ColorField(QWidget):
    """颜色选择：色块按钮 + 「清除」。空值 = 跟随主题。"""

    # v10 A5（反馈 11）：实时预览需要"值变了"的信号。旧实现只有
    # `set_value()`，对话框没法知道用户选了新颜色。
    changed = Signal(str)

    def __init__(self, value="", parent=None):
        super().__init__(parent)
        self._value = self._norm(value)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.btn = QPushButton()
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setFixedHeight(26)
        self.btn.clicked.connect(self._pick)
        lay.addWidget(self.btn, 1)
        self.clear_btn = QPushButton(i18n.T("button.clear"))
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.setFixedHeight(26)
        self.clear_btn.setFixedWidth(52)
        self.clear_btn.setStyleSheet(theme.BUTTON_QSS)
        self.clear_btn.clicked.connect(lambda: self.set_value(""))
        lay.addWidget(self.clear_btn)
        self._sync()

    @staticmethod
    def _norm(value) -> str:
        if not isinstance(value, str):
            return ""
        from PySide6.QtGui import QColor
        s = value.strip()
        return s.upper() if QColor(s).isValid() else ""

    def _pick(self):
        from PySide6.QtGui import QColor
        start = QColor(self._value) if self._value else QColor(theme.ACCENT)
        c = QColorDialog.getColor(start, self, i18n.T("button.choose_color"))
        if c.isValid():
            self.set_value(c.name().upper())

    def _sync(self):
        from PySide6.QtGui import QColor
        if self._value:
            c = QColor(self._value)
            # 色块用背景色表达，文字用对比色，深浅两色都看得清
            lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
            fg = "#101216" if lum > 150 else "#F2F3F5"
            self.btn.setText(self._value)
            self.btn.setStyleSheet(
                "QPushButton{background: %s; color: %s; border: 1px solid rgba(255,255,255,40);"
                " border-radius: 6px; font-size: 12px;}"
                "QPushButton:disabled{background: rgba(255,255,255,8);"
                " color: rgba(242,243,245,88);}" % (c.name(), fg))
        else:
            self.btn.setText(i18n.T("button.follow_theme"))
            self.btn.setStyleSheet(theme.BUTTON_QSS)
        self.clear_btn.setVisible(bool(self._value))

    def value(self) -> str:
        return self._value

    def set_value(self, value):
        self._value = self._norm(value)
        self._sync()
        self.changed.emit(self._value)

    # 兼容"控件里能读出字符串"的通用读法
    def text(self) -> str:
        return self._value


class FloatField(QWidget):
    """0~1 浮点：滑条 + 百分比文字（透明度用它）。"""

    # v10 A5（反馈 11）：见 ColorField.changed 的说明。
    changed = Signal(float)

    def __init__(self, value=1.0, lo=MIN_ALPHA, hi=MAX_ALPHA, parent=None):
        super().__init__(parent)
        self._lo, self._hi = float(lo), float(hi)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(self._lo * 100), int(self._hi * 100))
        self.slider.setSingleStep(5)
        self.slider.setPageStep(10)
        self.slider.setStyleSheet(theme.SLIDER_QSS)
        lay.addWidget(self.slider, 1)
        self.label = QLabel("")
        self.label.setFixedWidth(46)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.label.setStyleSheet("color: %s; font-size: 12px;" % theme.rgba(theme.TEXT_DIM))
        lay.addWidget(self.label)
        self.set_value(value)
        self.slider.valueChanged.connect(lambda _v: self._sync_label())

    def _sync_label(self):
        self.label.setText("%d%%" % self.slider.value())

    def value(self) -> float:
        return self.slider.value() / 100.0

    def set_value(self, value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = self._hi
        self.slider.setValue(int(round(max(self._lo, min(self._hi, v)) * 100)))
        self._sync_label()
        self.changed.emit(self.value())


class WidgetSettingsDialog(QDialog):
    """按 FieldSpec 生成的通用设置对话框（按 group 分段）。"""

    def __init__(self, spec: WidgetSpec, entry: dict, storage, parent=None,
                 widget=None):
        super().__init__(parent)
        self.spec = spec
        self.entry = entry
        self.storage = storage
        # v10 A5（反馈 11）：实时预览要推到**桌面上的那个组件**。调用方
        # （widget_base._open_settings）把 self 传进来；测试 / 无组件场景
        # 传 None，此时只写 entry 不通知组件。
        self._widget = widget
        # 打开时的快照：「取消」要把预览撤回去。深拷贝——settings 是嵌套 dict。
        self._snapshot = deepcopy(entry.get("settings") or {})
        self._controls: Dict[str, QWidget] = {}
        self._holders: Dict[str, QWidget] = {}
        self.setWindowTitle(i18n.T("widget.settings_title",
                                   name=spec.display_name()))
        self.setStyleSheet(DLG_QSS)
        self.setMinimumWidth(400)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title = QLabel(i18n.T("widget.settings_title",
                              name=spec.display_name()))
        title.setStyleSheet("color: %s; font-size: 15px; font-weight: 600;"
                            % theme.rgba(theme.TEXT))
        root.addWidget(title)

        vals = current_values(spec, entry.get("settings"))
        for group, fields in groups_of(spec).items():
            head = QLabel(field_group(group))
            head.setObjectName("group")
            root.addWidget(head)
            root.addLayout(self._build_form(fields, vals))
        # v10 A5：控件一改就实时预览（不落盘）。必须在表单建完之后连，
        # 否则 _build_control 里的 setChecked/setValue 会把初值当成"用户改动"。
        self._connect_live()
        # v14 反馈 10：液态玻璃开着时禁用颜色/不透明度（两者互斥）
        chk = self._controls.get("bg_liquid")
        if chk is not None:
            chk.toggled.connect(lambda _v: self._sync_liquid())
        self._sync_liquid()

        btns = QHBoxLayout()
        btn_reset = QPushButton(i18n.T("button.reset_default"))
        btn_reset.setStyleSheet(theme.BUTTON_QSS)
        btn_reset.clicked.connect(self._on_reset)
        btns.addWidget(btn_reset)
        btns.addStretch(1)
        btn_cancel = QPushButton(i18n.T("button.cancel"))
        btn_cancel.setStyleSheet(theme.BUTTON_QSS)
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton(i18n.T("button.apply"))
        btn_ok.setStyleSheet(theme.BUTTON_QSS)
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self._on_apply)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_ok)
        root.addLayout(btns)

    def _build_form(self, fields: List[FieldSpec], vals: Dict) -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        for f in fields:
            ctl = self._build_control(f, vals.get(f.key))
            self._controls[f.key] = ctl
            holder = QWidget()
            box = QVBoxLayout(holder)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(2)
            box.addWidget(ctl)
            hint_text = field_hint(f)
            if hint_text:
                hint = QLabel(hint_text)
                hint.setObjectName("hint")
                hint.setWordWrap(True)
                box.addWidget(hint)
            lab = QLabel(field_label(f))
            lab.setFixedWidth(LABEL_W)
            form.addRow(lab, holder)
            self._holders[f.key] = holder
        return form

    def _sync_liquid(self) -> None:
        """液态玻璃与背景色互斥：开着就把颜色/不透明度置灰（值保留）。"""
        chk = self._controls.get("bg_liquid")
        if chk is None:
            return
        on = bool(chk.isChecked())
        for key in ("bg_color", "bg_alpha"):
            holder = self._holders.get(key)
            if holder is not None:
                holder.setEnabled(not on)

    # ---------- 控件 ----------
    def _build_control(self, f: FieldSpec, value) -> QWidget:
        if f.kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(value))
            w.setStyleSheet(theme.CHECKBOX_QSS)
            return w
        if f.kind == "combo":
            w = ComboBox()
            for v, label in f.choices:
                w.addItem(i18n.TL("field.%s.%s" % (f.key, v), label), v)
            idx = 0
            for i, (v, _label) in enumerate(f.choices):
                if v == value:
                    idx = i
                    break
            if f.choices:
                w.setCurrentIndex(idx)
            return w
        if f.kind == "int":
            w = QSpinBox()
            w.setRange(-9999, 9999)
            try:
                w.setValue(int(value))
            except (TypeError, ValueError):
                pass
            w.setStyleSheet(theme.INPUT_QSS)
            return w
        if f.kind == "color":
            return ColorField(value)
        if f.kind == "float":
            return FloatField(value)
        w = QLineEdit()
        w.setText("" if value is None else str(value))
        w.setPlaceholderText(
            i18n.TL("field.%s.placeholder" % f.key, f.placeholder))
        w.setStyleSheet(theme.INPUT_QSS)
        return w

    def read_values(self) -> Dict:
        out = {}
        for f in self.spec.settings:
            c = self._controls[f.key]
            if f.kind == "bool":
                out[f.key] = bool(c.isChecked())
            elif f.kind == "combo":
                out[f.key] = c.currentData()
            elif f.kind == "int":
                out[f.key] = int(c.value())
            elif f.kind in ("color", "float"):
                out[f.key] = c.value()
            else:
                out[f.key] = c.text().strip()
        return out

    # ---------- v10 A5（反馈 11）：实时预览 ----------
    def _connect_live(self) -> None:
        """把每个控件的"值变了"信号接到实时预览。

        注意 ColorField / FloatField 旧实现只有 `set_value()` 没有信号，
        本版给它们补了 `changed`；Qt 内置控件（QCheckBox/QComboBox/
        QSpinBox/QLineEdit）各有各的信号名，逐一对应。
        """
        for f in self.spec.settings:
            c = self._controls[f.key]
            if f.kind == "bool":
                c.toggled.connect(lambda _v: self._live_apply())
            elif f.kind == "combo":
                c.currentIndexChanged.connect(lambda _i: self._live_apply())
            elif f.kind == "int":
                c.valueChanged.connect(lambda _v: self._live_apply())
            elif f.kind == "color":
                c.changed.connect(lambda _v: self._live_apply())
            elif f.kind == "float":
                c.changed.connect(lambda _v: self._live_apply())
            else:
                c.textChanged.connect(lambda _t: self._live_apply())

    def _live_apply(self) -> None:
        """控件改动 → 立即推到组件（**不落盘**，点「应用」才 save）。"""
        self._push(save=False)

    def _push(self, save: bool) -> None:
        """把当前控件值写进 entry 并通知组件刷新。"""
        merged = dict(self.entry.get("settings") or {})
        merged.update(self.read_values())
        self.entry["settings"] = merged
        cb = getattr(self._widget, "on_settings_changed", None)
        if callable(cb):
            cb()
        if save:
            try:
                self.storage.save()
            except Exception:      # noqa: BLE001
                pass

    def reject(self) -> None:      # noqa: N802  （Qt 命名）
        """取消：把打开时的快照写回去，预览当场撤掉。"""
        self.entry["settings"] = deepcopy(self._snapshot)
        cb = getattr(self._widget, "on_settings_changed", None)
        if callable(cb):
            cb()
        super().reject()

    # ---------- 动作 ----------
    def _on_reset(self):
        """恢复默认：把每个控件还原成 FieldSpec.default（不落盘，仍需「应用」）。"""
        for f in self.spec.settings:
            c = self._controls[f.key]
            if f.kind == "bool":
                c.setChecked(bool(f.default))
            elif f.kind == "combo":
                for i, (v, _l) in enumerate(f.choices):
                    if v == f.default:
                        c.setCurrentIndex(i)
                        break
            elif f.kind == "int":
                c.setValue(int(f.default))
            elif f.kind in ("color", "float"):
                c.set_value(f.default)
            else:
                c.setText("" if f.default is None else str(f.default))

    def _on_apply(self):
        self.apply()
        self.accept()

    def apply(self) -> bool:
        """写盘（拆出来便于测试：不弹窗也能验）。v10 A5 起同时推给组件。"""
        self._push(save=True)
        return True
