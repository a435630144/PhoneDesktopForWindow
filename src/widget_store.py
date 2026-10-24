# -*- coding: utf-8 -*-
"""组件商店客户端（v10 A10）：从 gitee 取清单，拿不到就换 github。

## 为什么需要"两个镜像 + 兜底"

组件仓库同时放在 gitee 与 github（`Component/` 目录单独一个仓库）。国内直连
gitee 通常几十毫秒，github raw 经常几秒甚至超时——所以**默认先问 gitee**，
失败或超时才换 github；成功后把"这次用的镜像"记下来，下次先问它（避免每次
都白等一遍超时）。

## 三层结构（本模块只负责第一、二层）

    镜像清单      index.json         （本模块 fetch_index，缓存到 data_file）
    组件包        <type>-<ver>.zip    （本模块 download，校验 sha256）
    安装 / 卸载   widget_plugin       （本模块只转调，不自己解压）

## 安全

  - 只信任 `index.json` 里列出的包名与 sha256；包本身仍走
    `widget_plugin.import_zip()` 的整套校验（白名单后缀 / 拒绝 zip-slip /
    manifest 必填字段），商店不是绕过校验的后门；
  - 下载先落临时文件、校验通过才改名，断电/断网不会留下半个 zip。

测试只 monkeypatch `_http_get`（唯一网络出口），不碰真实网络。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.request
from typing import Dict, List, Optional, Tuple

from . import paths
from . import widget_plugin
from . import widget_registry

# 组件仓库（`Component/` 目录对应仓库）的原始文件地址。
# 分支名两边不同：gitee 老仓库默认 master，github 是 main。
# 顺序即优先级：gitee（国内直连最快）-> github raw -> jsDelivr（GitHub 的 CDN
# 镜像，国内常能通）。实测本机直连 raw.githubusercontent.com 会被 TLS 层掐断，
# 所以"只留 github"不够，第三条线路是给"gitee 抽风 + github 被墙"准备的。
MIRRORS: Tuple[Tuple[str, str], ...] = (
    ("gitee",
     "https://gitee.com/suibianwanwanay/phone-desktop-for-windows-office-component/raw/master"),
    ("github",
     "https://raw.githubusercontent.com/a435630144/PhoneDesktopForWindows-OfficeComponent/main"),
    ("jsdelivr",
     "https://cdn.jsdelivr.net/gh/a435630144/PhoneDesktopForWindows-OfficeComponent@main"),
)
INDEX_NAME = "index.json"

# 连接 / 读取超时（秒）：gitee 挂了也不该让设置页转半分钟
TIMEOUT = 6.0
# 清单缓存有效期：过期后后台静默刷新，界面先用缓存
CACHE_TTL = 30 * 60


class StoreError(Exception):
    """商店不可用（两个镜像都失败）——消息可直接展示给用户。"""


# ---------------------------------------------------------------- 网络

def _http_get(url: str, timeout: float = TIMEOUT) -> bytes:
    """唯一的网络出口（测试 monkeypatch 这里，不碰真实网络）。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": "PhoneDesktop/%s" % _app_version(),
                      "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310
        return resp.read()


def _app_version() -> str:
    try:
        from . import version
        return str(getattr(version, "SEMVER", "") or "0")
    except Exception:      # noqa: BLE001
        return "0"


def cache_file():
    return paths.data_file("store_index.json")


def _load_cache() -> Optional[dict]:
    try:
        with open(cache_file(), "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else None
    except Exception:      # noqa: BLE001 —— 缓存坏了就当没有
        return None


def _save_cache(data: dict) -> None:
    try:
        path = cache_file()
        os.makedirs(os.path.dirname(str(path)), exist_ok=True)
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:      # noqa: BLE001 —— 写不进去不该影响浏览
        pass


# ---------------------------------------------------------------- 清单

def mirror_order() -> List[Tuple[str, str]]:
    """镜像顺序：上次成功的排最前，其余按声明顺序。"""
    cached = _load_cache() or {}
    last = str(cached.get("mirror") or "")
    out = [m for m in MIRRORS if m[0] == last]
    out += [m for m in MIRRORS if m[0] != last]
    return out


def fetch_index(force: bool = False) -> dict:
    """拉商店清单；两个镜像都失败抛 `StoreError`（带每个镜像的失败原因）。

    成功时写缓存（含 `mirror` / `fetched_at`），并在返回的 dict 里补
    `_mirror` 字段供界面显示"当前走的是哪条线路"。
    """
    errors = []
    for name, base in mirror_order():
        url = "%s/%s" % (base.rstrip("/"), INDEX_NAME)
        try:
            raw = _http_get(url)
        except Exception as exc:      # noqa: BLE001 —— 逐个镜像降级
            errors.append("%s：%s" % (name, _friendly_net(exc)))
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:      # noqa: BLE001
            errors.append("%s：清单不是合法 JSON（%s）" % (name, exc))
            continue
        if not isinstance(data, dict) or not isinstance(data.get("widgets"), list):
            errors.append("%s：清单缺 widgets 数组" % name)
            continue
        data["_mirror"] = name
        _save_cache({"mirror": name, "fetched_at": time.time(),
                     "index": {k: v for k, v in data.items() if k != "_mirror"}})
        return data
    raise StoreError("商店暂时打不开（还在用上次缓存的清单）：\n· "
                     + "\n· ".join(errors or ["无可用镜像"]))


def cached_index() -> dict:
    """上次成功的清单（没有则返回空清单）；界面首屏用它，不等网络。"""
    cached = _load_cache() or {}
    idx = cached.get("index")
    if isinstance(idx, dict) and isinstance(idx.get("widgets"), list):
        idx = dict(idx)
        idx["_mirror"] = cached.get("mirror") or ""
        idx["_fetched_at"] = cached.get("fetched_at") or 0
        return idx
    return {"widgets": [], "_mirror": "", "_fetched_at": 0}


def cached_mirror() -> str:
    return str((_load_cache() or {}).get("mirror") or "")


def cache_age() -> float:
    """缓存年龄（秒）；没有缓存返回 +inf。"""
    ts = (cached_index().get("_fetched_at") or 0)
    if not ts:
        return float("inf")
    return max(0.0, time.time() - float(ts))


# ---------------------------------------------------------------- 包

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def package_url(entry: dict, base: str) -> str:
    name = str(entry.get("package") or "").strip()
    if not name:
        raise StoreError("清单条目缺 package 字段：%s" % entry.get("type"))
    return "%s/%s" % (base.rstrip("/"), name.lstrip("/"))


# 清单里的 `package` 是"仓库内相对路径"。历史上出现过两种写法：只写文件名
# （zip 实际在 dist/ 下 → 根目录 404）和写 packages/ 子目录。客户端每个都
# 试一遍——为一次清单书写错误让用户买不了组件不值得（v10 反馈 28）。
PACKAGE_PREFIXES = ("", "dist/", "packages/")


def package_urls(entry: dict, base: str) -> List[str]:
    """一条线路上依次尝试的候选 URL（保序去重）。

    路径里已带目录（新清单写 `dist/x.zip`）时不再拼前缀——那是作者明确的
    位置，乱猜只会多打三次 404。
    """
    name = str(entry.get("package") or "").strip().lstrip("/")
    if not name:
        raise StoreError("清单条目缺 package 字段：%s" % entry.get("type"))
    base = base.rstrip("/")
    cands = ["%s/%s" % (base, name)]
    if "/" not in name:
        for pre in PACKAGE_PREFIXES[1:]:
            cands.append("%s/%s%s" % (base, pre, name))
    return cands


def _friendly_net(exc) -> str:
    """把 urllib 的原始异常翻成用户能懂的一句话。

    旧实现把 `WinError 10054 远程主机强迫关闭了一个现有的连接` 原样糊在
    对话框里——用户看不出"是网络还是软件坏了"。
    """
    reason = getattr(exc, "reason", None)
    text = str(reason if reason is not None else exc)
    low = text.lower()
    if any(k in low for k in ("10054", "10053", "connection reset",
                              "connection aborted", "远程主机强迫")):
        return "连接被重置（网络或代理受限）"
    if "timed out" in low or "timeout" in low:
        return "连接超时"
    if "certificate" in low or "ssl" in low or "tls" in low:
        return "TLS 握手失败（可能被拦截）"
    if "getaddrinfo" in low or "name or service" in low:
        return "域名解析失败（可能没联网）"
    if "404" in low:
        return "清单指向的组件包不存在（仓库未同步）"
    return text


def download(entry: dict) -> str:
    """下载组件包到临时目录并校验 sha256；返回本地路径（调用方负责清理）。

    v10 反馈 28：旧实现每个镜像只试**一个** URL，清单里 package 少写
    `dist/` 就全线 404。现在每条线路按 `package_urls` 的候选依次试，
    只有"每个候选都 404"才判定该镜像没有这个包。
    """
    want = str(entry.get("sha256") or "").strip().lower()
    if not want:
        # S2：sha256 缺失一律拒装——清单和 zip 同源，缺校验时信任链只剩
        # "镜像账号 + TLS"，任一仓库被盗即供应链 RCE。宁可装不了也不能放行
        raise StoreError(
            "商店清单没有提供该组件的 sha256 校验值，已拒绝安装。\n"
            "（这是防投毒保护；可到组件仓库核对清单或稍后重试）")
    errors = []
    for name, base in mirror_order():
        try:
            urls = package_urls(entry, base)
        except StoreError:
            raise
        all_404 = True
        net_err = ""
        for url in urls:
            try:
                data = _http_get(url, timeout=max(TIMEOUT, 20.0))
            except Exception as exc:      # noqa: BLE001
                code = getattr(exc, "code", None)
                if code == 404:
                    continue              # 换个候选路径再试
                net_err = _friendly_net(exc)
                all_404 = False
                break
            all_404 = False
            fd, tmp = tempfile.mkstemp(prefix="pd_pkg_", suffix=".zip")
            os.close(fd)
            try:
                with open(tmp, "wb") as f:
                    f.write(data)
                got = _sha256(tmp)
                if want and got != want:
                    errors.append("%s：包已损坏/版本不符（sha256 校验失败）" % name)
                    os.unlink(tmp)
                    break
                return tmp
            except Exception as exc:      # noqa: BLE001
                errors.append("%s：%s" % (name, _friendly_net(exc)))
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                break
        if all_404:
            errors.append("%s：清单指向的组件包在仓库里不存在（404）" % name)
        elif net_err:
            errors.append("%s：%s" % (name, net_err))
    raise StoreError(
        "组件包下载失败（三条线路都没成功）：\n· "
        + "\n· ".join(errors or ["无可用镜像"])
        + "\n\n可尝试：① 检查网络/代理后点「刷新」重试；"
          "② 到组件仓库手动下载 zip，用「导入组件…」安装。")


def install(entry: dict):
    """下载 + 安装一个商店组件（走 `widget_plugin.import_zip` 的全部校验）。"""
    if not isinstance(entry, dict) or not entry.get("type"):
        raise StoreError("清单条目非法")
    tmp = download(entry)
    try:
        spec = widget_plugin.import_zip(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return spec


def uninstall(wtype: str) -> bool:
    """卸载一个已安装的商店组件（内置组件拒绝）。"""
    return widget_plugin.uninstall(wtype)


# ---------------------------------------------------------------- 状态

def installed_version(wtype: str) -> str:
    spec = widget_registry.PLUGINS.get(wtype)
    return str(getattr(spec, "version", "") or "") if spec else ""


def _newer(a: str, b: str) -> bool:
    """版本比较（点分数字；非数字段按字符串比）。`a` 比 `b` 新返回 True。"""
    def parts(v):
        out = []
        for seg in str(v or "").split("."):
            try:
                out.append(int(seg))
            except (TypeError, ValueError):
                out.append(seg)
        return out
    pa, pb = parts(a), parts(b)
    for x, y in zip(pa, pb):
        if x == y:
            continue
        if isinstance(x, int) and isinstance(y, int):
            return x > y
        return str(x) > str(y)
    return len(pa) > len(pb)


def card_state(entry: dict) -> str:
    """商店卡片状态：builtin / installed / update / available。"""
    wtype = str(entry.get("type") or "")
    if widget_registry.is_builtin(wtype):
        return "builtin"          # 内置组件不在商店里覆盖（产品规则：内置优先）
    cur = installed_version(wtype)
    if not cur:
        return "available"
    if _newer(str(entry.get("version") or ""), cur):
        return "update"
    return "installed"


def summary() -> dict:
    """给设置页顶部的一行状态。"""
    idx = cached_index()
    return {"mirror": idx.get("_mirror") or cached_mirror(),
            "count": len(idx.get("widgets") or []),
            "age": cache_age(),
            "installed": len(widget_registry.PLUGINS)}
