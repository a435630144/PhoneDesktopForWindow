# -*- coding: utf-8 -*-
"""一条命令走完发版：构建 → 安装包 → 推送 → 打 tag → 传资产 → 校验（v14x / 8.3.1）。

## 为什么要有这个脚本

8.3.0 出过两次同类事故：**发布页上的资产还是旧构建**（v14n 的 exe 挂在
8.3.0 的 release 上，而 main 已经到 v14v，中间六批修复用户一个都没拿到）。
原因是流程散在四条手工命令里，任何一条忘了跑、跑错了目录，页面看起来都
"发布成功"——但用户下载到的是旧包。审核报告把它列为 P0，这里把它收敛成
一条命令 + 一条校验。

## 顺序（每一步都可单独跳过，便于断点续跑）

```
python tools/release.py                 # 全流程
python tools/release.py --dry-run       # 只打印计划，不执行任何命令
python tools/release.py --skip-build    # 复用 dist/ 里已打好的包
python tools/release.py --skip-push     # 已推送过，只补资产
python tools/release.py --replace-assets  # 同名资产先删再传（重传同一字母包）
```

1. **读版本**：`src/version.py` 的 `SEMVER` / `VERSION` 是唯一来源；
   `scripts/installer.iss` 的 `MyAppVersion/BuildDir/OutBase` 必须与之一致
   （不一致直接拒绝，避免"包名与版本号打架"）。
2. **预检**：工作区必须干净（`--allow-dirty` 可跳过）、分支 main、
   `scripts/check_docs.py` 必须 ERROR 0。
3. **构建**：`scripts/build_portable.ps1`（**绝对路径**调用，坑 #86）→
   `dist/PhoneDesktop_v<字母>_Portable.zip`；`ISCC.exe` → `*_Setup.exe`。
   判据看**产物**不看退出码（坑 #80）：文件必须存在且比本次开跑时间新。
4. **推送**：先直连，失败换 `https_proxy=127.0.0.1:7890` 重试（两条互斥
   用法，别在走代理时再传 `-c http.proxy=`，那会把 env 代理清成空）。
5. **打 tag**：`<SEMVER>` 不存在才创建；已存在则复用（**删远端 tag 会把
   release 打回 draft**，别删）。
6. **发布**：`tools/publish_release.py`（幂等：已有 release 复用、已存在
   附件跳过）。`--replace-assets` 时先删同名附件再传。
7. **校验**：拉一次 release，逐条比对"附件名 + 字节数"与本地文件——
   这一步是防"发布成功但资产是旧的"的最后一道闸。

## 注意

- 认证走 git 凭据管理器的 github.com token（`publish_release.token()`），
  脚本不读环境变量、不落盘。
- 构建期间**不要**启动 dist 里的 exe（Windows 会锁文件，PyInstaller 覆盖失败）。
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
VERSION_PY = ROOT / "src" / "version.py"
ISS = ROOT / "scripts" / "installer.iss"
BUILD_PS1 = ROOT / "scripts" / "build_portable.ps1"
CHECK_DOCS = ROOT / "scripts" / "check_docs.py"
PROXY = "http://127.0.0.1:7890"


# ---------- 小工具 ----------
def say(msg=""):
    print(msg, flush=True)


def run(cmd, *, cwd=None, env=None, timeout=1800, check=True):
    """跑一条命令并回显；失败抛 RuntimeError（调用方决定怎么兜）。"""
    say("  $ %s" % " ".join(str(c) for c in cmd))
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(cwd or ROOT), env=env,
                          capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
    took = time.time() - t0
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        say("\n".join("    " + line for line in out.splitlines()[-12:]))
    if proc.returncode != 0:
        if err:
            say("\n".join("    ! " + line for line in err.splitlines()[-12:]))
        if check:
            raise RuntimeError("命令失败（exit %s）：%s" % (proc.returncode, cmd[0]))
    else:
        say("  ok（%.1fs）" % took)
    return proc


def read_version():
    src = VERSION_PY.read_text(encoding="utf-8")
    semver = re.search(r'^SEMVER\s*=\s*"([^"]+)"', src, re.M)
    letter = re.search(r'^VERSION\s*=\s*"([^"]+)"', src, re.M)
    if not semver or not letter:
        raise SystemExit("src/version.py 里找不到 SEMVER / VERSION")
    return semver.group(1), letter.group(1)


def read_iss():
    text = ISS.read_text(encoding="utf-8-sig")
    def grab(key):
        m = re.search(r'^#define\s+%s\s+"([^"]*)"' % key, text, re.M)
        return m.group(1) if m else ""
    return {"my_app_version": grab("MyAppVersion"),
            "build_dir": grab("BuildDir"),
            "out_base": grab("OutBase")}


def find_iscc():
    """ISCC 可能在系统级或**用户级**安装（本机就是后者：Inno 6 默认装到
    %LOCALAPPDATA%\\Programs\\Inno Setup 6，不在 Program Files 里）。"""
    local = os.environ.get("LOCALAPPDATA", "")
    cands = [os.environ.get("INNO_SETUP", ""),
             r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
             r"C:\Program Files\Inno Setup 6\ISCC.exe",
             os.path.join(local, "Programs", "Inno Setup 6", "ISCC.exe") if local else "",
             os.path.join(local, "Inno Setup 6", "ISCC.exe") if local else ""]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    found = shutil.which("ISCC") or shutil.which("iscc")
    return found or ""


def find_pwsh():
    for name in ("pwsh", "pwsh.exe", "powershell", "powershell.exe"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def git(*args, **kw):
    return run(["git"] + list(args), **kw)


def push_with_fallback(refspec):
    """直连 → 代理重试。返回用到的通道描述。"""
    try:
        git("push", "origin", refspec, timeout=180)
        return "direct"
    except RuntimeError:
        say("  直连失败，改走本机代理 %s 重试" % PROXY)
        env = dict(os.environ, https_proxy=PROXY, http_proxy=PROXY)
        git("push", "origin", refspec, env=env, timeout=300)
        return "proxy"


def publish_module():
    """把 tools/publish_release.py 当模块加载（tools 不是包）。"""
    path = ROOT / "tools" / "publish_release.py"
    spec = importlib.util.spec_from_file_location("publish_release", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser(description="一条命令发版（构建 + 推送 + 资产）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--skip-push", action="store_true")
    ap.add_argument("--replace-assets", action="store_true",
                    help="同名资产先删再传（重发同一个字母包时用）")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--notes", default="", help="默认 docs/release_notes/<SEMVER>.md")
    ap.add_argument("--name", default="", help="release 标题，默认 v<SEMVER>（v<字母>）")
    ap.add_argument("--target", default="main")
    args = ap.parse_args()

    semver, letter = read_version()
    iss = read_iss()
    build_dir = iss["build_dir"] or ("PhoneDesktop_v" + letter)
    out_base = iss["out_base"] or ("PhoneDesktop_v%s_Setup" % letter)
    portable_zip = DIST / ("%s_Portable.zip" % build_dir)
    setup_exe = DIST / ("%s.exe" % out_base)
    notes = Path(args.notes) if args.notes else (ROOT / "docs" / "release_notes" / ("%s.md" % semver))
    title = args.name or ("v%s（v%s）" % (semver, letter))

    say("=" * 62)
    say("版本：%s（%s）    构建目录：%s" % (semver, letter, build_dir))
    say("便携包：%s" % portable_zip.relative_to(ROOT))
    say("安装包：%s" % setup_exe.relative_to(ROOT))
    say("说明：%s" % (notes.relative_to(ROOT) if notes.exists() else "（缺失！）"))
    say("=" * 62)

    problems = []
    if iss["my_app_version"] != semver:
        problems.append("installer.iss MyAppVersion=%r ≠ SEMVER=%r"
                        % (iss["my_app_version"], semver))
    if not notes.exists():
        problems.append("release 说明文件不存在：%s" % notes)
    if problems:
        raise SystemExit("版本/文档不一致，先修好再来：\n  - " + "\n  - ".join(problems))

    # ---------- 预检 ----------
    if not args.dry_run:
        dirty = subprocess.run(["git", "status", "--porcelain"],
                               cwd=str(ROOT), capture_output=True, text=True,
                               encoding="utf-8", errors="replace").stdout
        if dirty.strip() and not args.allow_dirty:
            raise SystemExit("工作区有未提交改动，先提交（或加 --allow-dirty）：\n%s"
                             % dirty.strip()[:800])
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                cwd=str(ROOT), capture_output=True, text=True,
                                encoding="utf-8", errors="replace").stdout.strip()
        if branch != args.target:
            raise SystemExit("当前分支 %r，不是 %r" % (branch, args.target))
        say("[预检] check_docs …")
        proc = subprocess.run([sys.executable, str(CHECK_DOCS)], cwd=str(ROOT),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        tail = (proc.stdout or "").strip().splitlines()
        if proc.returncode != 0:
            say("\n".join("  " + ln for ln in tail[-8:]))
            raise SystemExit("check_docs 不通过（ERROR 必须为 0）")
        say("  ok：%s" % (tail[-1] if tail else "PASS"))

    started = time.time()

    # ---------- 构建 ----------
    if args.skip_build:
        say("[构建] 跳过（--skip-build）；产物存在性：zip=%s exe=%s"
            % (portable_zip.exists(), setup_exe.exists()))
    elif args.dry_run:
        say("[构建] 将执行 pwsh build_portable.ps1 -OutName %s" % build_dir)
        say("[构建] 将执行 ISCC /DBuildDir=%s /DOutBase=%s" % (build_dir, out_base))
    else:
        pwsh = find_pwsh()
        if not pwsh:
            raise SystemExit("找不到 pwsh / powershell")
        say("[构建] 便携包 …")
        # 注意：OutName 是**目录名**（PhoneDesktop_v14x），脚本自己拼
        # "<OutName>_Portable.zip"；别再给它加 "_Portable" 后缀——那样会出
        # 现 ..._Portable_Portable.zip（本批实际踩到）。
        run([pwsh, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(BUILD_PS1), "-OutName", build_dir],
            timeout=2400)
        iscc = find_iscc()
        if not iscc:
            raise SystemExit("找不到 ISCC.exe（装 Inno Setup 6，或设 INNO_SETUP）")
        say("[构建] 安装包 …")
        run([iscc, "/DBuildDir=%s" % build_dir, "/DOutBase=%s" % out_base,
             str(ISS)], timeout=1800)

    # 产物判据：存在 + 比开跑时间新（--skip-build 时不比时间）
    missing = [p for p in (portable_zip, setup_exe) if not p.exists()]
    if missing:
        if args.dry_run:
            say("[构建] 产物尚不存在（dry-run 不下结论）")
        else:
            raise SystemExit("产物缺失（别只看退出码，坑 #80）：%s"
                             % ", ".join(str(p) for p in missing))
    if not args.dry_run and not args.skip_build:
        stale = [p for p in (portable_zip, setup_exe)
                 if p.stat().st_mtime < started - 5]
        if stale:
            raise SystemExit("产物时间戳比本次构建还早，说明没被覆盖：%s"
                             % ", ".join(p.name for p in stale))
        say("[构建] 产物 ok：zip %.1f MB / exe %.1f MB"
            % (portable_zip.stat().st_size / 1048576,
               setup_exe.stat().st_size / 1048576))

    # ---------- 推送 + tag ----------
    if args.skip_push:
        say("[git] 跳过推送（--skip-push）")
    elif args.dry_run:
        say("[git] 将推送 main，然后确保 tag %s 存在并推送" % semver)
    else:
        say("[git] push main …")
        say("  通道：%s" % push_with_fallback(args.target))
        has_tag = subprocess.run(["git", "rev-parse", "-q", "--verify",
                                  "refs/tags/%s" % semver],
                                 cwd=str(ROOT), capture_output=True).returncode == 0
        if not has_tag:
            git("tag", "-a", semver, "-m", "%s（v%s）" % (semver, letter))
            say("[git] 已建 tag %s" % semver)
        say("[git] push tag …")
        say("  通道：%s" % push_with_fallback(semver))

    # ---------- 发布 ----------
    if args.dry_run:
        say("[发布] 将调用 publish_release %s --name %r --notes %s <zip> <exe>"
            % (semver, title, notes.name))
        say("[发布] dry-run 结束（未构建、未推送、未上传）")
        return 0

    pr = publish_module()
    tok = pr.token()
    if args.replace_assets:
        rel = pr.find_release(tok, semver)
        keep = {portable_zip.name, setup_exe.name}
        if rel:
            for a in rel.get("assets", []):
                if a["name"] in keep:
                    say("[发布] 删除同名旧资产：%s" % a["name"])
                    pr.call("DELETE", "%s/releases/assets/%d" % (pr.API, a["id"]), tok)
                    time.sleep(1)

    say("[发布] 上传资产 …")
    run([sys.executable, str(ROOT / "tools" / "publish_release.py"), semver,
         "--name", title, "--notes", str(notes), "--target", args.target,
         str(portable_zip), str(setup_exe)], timeout=1800)

    # ---------- 校验：附件名 + 字节数 ----------
    rel = pr.find_release(tok, semver)
    if not rel:
        raise SystemExit("发布后找不到 release %s" % semver)
    if rel.get("draft"):
        say("[校验] release 处于 draft，改回 published …")
        pr.call("PATCH", "%s/releases/%d" % (pr.API, rel["id"]), tok,
                payload=json.dumps({"draft": False}),
                headers={"Content-Type": "application/json"})
        rel = pr.find_release(tok, semver) or rel
    online = {a["name"]: a["size"] for a in rel.get("assets", [])}
    bad = []
    for local in (portable_zip, setup_exe):
        size = online.get(local.name)
        if size is None:
            bad.append("%s 未上传" % local.name)
        elif size != local.stat().st_size:
            bad.append("%s 大小不符（线上 %s / 本地 %s）"
                       % (local.name, size, local.stat().st_size))
    say("-" * 62)
    for name, size in sorted(online.items()):
        say("  线上资产：%-38s %8.1f MB" % (name, size / 1048576))
    if bad:
        raise SystemExit("发布校验失败：\n  - " + "\n  - ".join(bad))
    say("完成：%s" % rel.get("html_url"))
    say("tag=%s  assets=%d  用时 %.1fs" % (semver, len(online), time.time() - started))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        raise SystemExit("失败：%s" % exc)
