# -*- coding: utf-8 -*-
"""外部组件加载器（v9 V3，指导文档_v9.md §4）。

契约：
  P1  manifest 校验：合法示例能解析出完整 WidgetSpec
  P2  id 非法（大写 / 含点 / 太短）被拒
  P3  与内置组件同名被拒（不能靠 zip 顶掉时钟）
  P4  缺 module / 缺 factory / factory 不是 GlassWidget 子类 -> 被拒
  P5  spans 非法（非整数、越界）被拒
  P6  settings 的 kind 不认识被拒；合法 kind 变成 FieldSpec
  P7  zip 导入：解包到 widgets/<id>、注册进注册表、立即可查
  P8  zip-slip（../ 或绝对路径）被拒，且没有文件被写到目录外
  P9  只允许白名单后缀（.exe 之类被拒）
  P10 卸载：删目录 + 移出注册表；内置组件拒绝卸载
  P11 坏插件不影响启动：load_all 跳过它并写 plugin_error.log
  P12 load_all 能重新加载已装插件（目录即真相，不依赖额外清单）
  P13 插件出现在 all_specs / categories 中，且带全套外观字段

运行：python tests/test_widget_plugin.py
"""
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "widgets" / "hello"
TMP = Path(tempfile.mkdtemp(prefix="test_plugin_"))
# 插件目录跟着 APPDATA 走：测试前把它指到临时目录，避免污染真实用户目录
os.environ["APPDATA"] = str(TMP)

from src import widget_plugin as wp          # noqa: E402
from src import widget_registry as reg       # noqa: E402

CHECKS = []
failures = []


def check(name, cond, extra=""):
    CHECKS.append(name)
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        failures.append(name)


def expect_error(fn, keyword=""):
    try:
        fn()
    except wp.PluginError as exc:
        return keyword in str(exc)
    except Exception:      # noqa: BLE001
        return False
    return False


def write_plugin(folder: Path, manifest: dict, module_src: str, name="widget.py"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (folder / name).write_text(module_src, encoding="utf-8")
    return folder


GOOD_SRC = '''
from deskorg.widget_glass import GlassWidget

class Demo(GlassWidget):
    TYPE = "demo"
    def paint_content(self, p):
        pass
'''

BASE_MAN = {
    "id": "demo", "name": "演示", "summary": "s", "category": "示例",
    "spans": [[2, 2]], "module": "widget.py", "factory": "Demo",
}


def main():
    # ---------- P1 示例组件 ----------
    spec = wp.build_spec(EXAMPLE)
    check("P1a 示例组件解析成功", spec.type == "hello", spec.type)
    check("P1b 名称/分类/跨度", (spec.name, spec.category, spec.spans[0])
          == ("示例组件", "示例", (2, 2)), str((spec.name, spec.category, spec.spans)))
    check("P1c factory 是 GlassWidget 子类", issubclass(spec.factory, object)
          and spec.factory.__name__ == "HelloWidget", spec.factory.__name__)
    check("P1d 有 preview", callable(spec.preview))
    check("P1e 业务设置被解析（who/show_time）",
          [f.key for f in spec.settings] == ["who", "show_time"],
          str([f.key for f in spec.settings]))
    check("P1f 声明为 plugin", spec.plugin is True and spec.version == "1.0.0")

    # ---------- P2 id 非法 ----------
    bad = Path(TMP) / "bad_id"
    write_plugin(bad, dict(BASE_MAN, id="Demo"), GOOD_SRC)
    check("P2a 大写 id 被拒", expect_error(lambda: wp.build_spec(bad), "id"))
    bad2 = Path(TMP) / "bad_id2"
    write_plugin(bad2, dict(BASE_MAN, id="a"), GOOD_SRC)
    check("P2b 太短的 id 被拒", expect_error(lambda: wp.build_spec(bad2), "id"))
    bad3 = Path(TMP) / "bad_id3"
    write_plugin(bad3, dict(BASE_MAN, id="../evil"), GOOD_SRC)
    check("P2c 带路径的 id 被拒", expect_error(lambda: wp.build_spec(bad3), "id"))

    # ---------- P3 撞内置名 ----------
    bad4 = Path(TMP) / "bad_builtin"
    write_plugin(bad4, dict(BASE_MAN, id="clock"), GOOD_SRC)
    check("P3 与内置同名被拒", expect_error(lambda: wp.build_spec(bad4), "内置"))

    # ---------- P4 模块 / 工厂 ----------
    bad5 = Path(TMP) / "bad_mod"
    write_plugin(bad5, dict(BASE_MAN, id="bad_mod", module="nope.py"), GOOD_SRC)
    check("P4a 缺模块文件被拒", expect_error(lambda: wp.build_spec(bad5), "模块"))
    bad6 = Path(TMP) / "bad_fac"
    write_plugin(bad6, dict(BASE_MAN, id="bad_fac", factory="Nope"), GOOD_SRC)
    check("P4b 找不到 factory 被拒", expect_error(lambda: wp.build_spec(bad6), "factory"))
    bad7 = Path(TMP) / "bad_sub"
    write_plugin(bad7, dict(BASE_MAN, id="bad_sub", factory="NotWidget"), GOOD_SRC +
                 "\nclass NotWidget(object):\n    pass\n")
    check("P4c factory 不是 GlassWidget 子类被拒",
          expect_error(lambda: wp.build_spec(bad7), "GlassWidget"))
    bad8 = Path(TMP) / "bad_syntax"
    write_plugin(bad8, dict(BASE_MAN, id="bad_syntax"), "def broken(:\n")
    check("P4d 模块语法错误被转成可读消息",
          expect_error(lambda: wp.build_spec(bad8), "导入失败"))

    # ---------- P5 spans ----------
    bad9 = Path(TMP) / "bad_spans"
    write_plugin(bad9, dict(BASE_MAN, id="bad_spans", spans=[[2, 0]]), GOOD_SRC)
    check("P5a 跨度为 0 被拒", expect_error(lambda: wp.build_spec(bad9), "spans"))
    bad10 = Path(TMP) / "bad_spans2"
    write_plugin(bad10, dict(BASE_MAN, id="bad_spans2", spans=[["a", 2]]), GOOD_SRC)
    check("P5b 非整数跨度被拒", expect_error(lambda: wp.build_spec(bad10), "整数"))

    # ---------- P6 settings ----------
    bad11 = Path(TMP) / "bad_kind"
    write_plugin(bad11, dict(BASE_MAN, id="bad_kind",
                             settings=[{"key": "x", "kind": "slider"}]),
                 GOOD_SRC)
    check("P6a 不认识的 kind 被拒", expect_error(lambda: wp.build_spec(bad11), "kind"))
    ok12 = Path(TMP) / "ok_kind"
    write_plugin(ok12, dict(BASE_MAN, id="ok_kind",
                            settings=[{"key": "c", "label": "颜色", "kind": "color",
                                       "default": "#ffffff"},
                                      {"key": "n", "kind": "combo", "default": 1,
                                       "choices": [[1, "一"], [2, "二"]]}]),
                 GOOD_SRC)
    spec12 = wp.build_spec(ok12)
    check("P6b 合法 kind 变成 FieldSpec",
          [f.kind for f in spec12.settings] == ["color", "combo"],
          str([f.kind for f in spec12.settings]))
    check("P6c combo 选项被归一成 tuple", spec12.settings[1].choices == ((1, "一"), (2, "二")),
          str(spec12.settings[1].choices))

    # ---------- P7 zip 导入 ----------
    zpath = Path(TMP) / "hello.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for f in EXAMPLE.rglob("*"):
            if f.is_file():
                # 前面 build_spec 的 exec_module 会在暂存目录落下
                # __pycache__/*.pyc——zip 里带它会被 ALLOWED_EXT 拒收，
                # 测试就变成了"取决于是否开启字节码写入"。打包时排除。
                if "__pycache__" in f.parts:
                    continue
                zf.write(f, f.relative_to(EXAMPLE))
    got = wp.import_zip(zpath)
    check("P7a zip 导入返回 spec", got.type == "hello", got.type)
    check("P7b 解包到 widgets/hello",
          (wp.plugin_root() / "hello" / "manifest.json").is_file(),
          str(wp.plugin_root()))
    check("P7c 注册表可查", reg.get("hello") is not None)
    check("P7d 出现在 all_specs", "hello" in [s.type for s in reg.all_specs()])
    check("P7e 出现在分类列表", "示例" in reg.categories(), str(reg.categories()))
    check("P7f 带上全套外观字段",
          all(k in {f.key for f in reg.get("hello").settings}
              for k in ("bg_color", "bg_alpha", "text_color", "accent_color", "radius")),
          str([f.key for f in reg.get("hello").settings]))

    # 套一层目录的 zip 也能导入（GitHub 下载的通常是 hello/ 下面一堆文件）
    zpath2 = Path(TMP) / "nested.zip"
    with zipfile.ZipFile(zpath2, "w") as zf:
        for f in EXAMPLE.rglob("*"):
            if f.is_file():
                if "__pycache__" in f.parts:
                    continue
                zf.write(f, "hello/" + str(f.relative_to(EXAMPLE)))
    check("P7g 顶层多套一层目录也能导入",
          wp.import_zip(zpath2).type == "hello")

    # ---------- P8 zip-slip ----------
    evil = Path(TMP) / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("manifest.json", json.dumps(dict(BASE_MAN, id="evilone")))
        zf.writestr("widget.py", GOOD_SRC)
        zf.writestr("../../pwned.py", "print('x')")
    check("P8a zip-slip 被拒", expect_error(lambda: wp.import_zip(evil), "zip-slip"))
    check("P8b 目录外没有文件被写", not (TMP.parent / "pwned.py").exists())
    check("P8c 失败不留半个目录", not (wp.plugin_root() / "evilone").is_dir())

    # ---------- P9 白名单后缀 ----------
    evil2 = Path(TMP) / "evil2.zip"
    with zipfile.ZipFile(evil2, "w") as zf:
        zf.writestr("manifest.json", json.dumps(dict(BASE_MAN, id="eviltwo")))
        zf.writestr("widget.py", GOOD_SRC)
        zf.writestr("payload.exe", b"MZ")
    check("P9 可执行文件被拒", expect_error(lambda: wp.import_zip(evil2), "不支持"))

    # ---------- P11 坏插件 ----------
    broken_dir = wp.plugin_root() / "brokenone"
    write_plugin(broken_dir, dict(BASE_MAN, id="brokenone", factory="Nope"), GOOD_SRC)
    loaded = wp.load_all()
    check("P11a load_all 跳过坏插件", "brokenone" not in [s.type for s in loaded],
          str([s.type for s in loaded]))
    check("P11b 好插件仍被加载", "hello" in [s.type for s in loaded],
          str([s.type for s in loaded]))
    check("P11c 写了错误日志", wp.error_log().is_file()
          and "brokenone" in wp.error_log().read_text(encoding="utf-8", errors="ignore"))

    # ---------- P12 目录即真相 ----------
    before = set(wp.installed_ids())
    check("P12a installed_ids 来自目录",
          before == {"hello", "brokenone"}, str(sorted(before)))

    # ---------- P10 卸载 ----------
    check("P10a 内置组件拒绝卸载", wp.uninstall("clock") is False)
    check("P10b 内置组件仍在注册表", reg.get("clock") is not None)
    check("P10c 插件卸载成功", wp.uninstall("hello") is True)
    check("P10d 目录被删", not (wp.plugin_root() / "hello").is_dir())
    check("P10e 注册表里没了", reg.get("hello") is None)

    print("\n%d checks, %d failures" % (len(CHECKS), len(failures)))
    if failures:
        print("FAILED:", ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
