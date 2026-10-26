; -*- coding: utf-8 -*-
; 掌镜桌面 / PhoneDesktop - Inno Setup 安装包脚本
; 用法（需先跑 scripts\build_portable.ps1 -OutName PhoneDesktop_v13a）：
;   "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" scripts/installer.iss
;
; 产物：dist\PhoneDesktop_v13a_Setup.exe
; 特性：默认安装到 %LOCALAPPDATA%\Programs（免管理员权限）、
;       开始菜单 + 桌面快捷方式、可选开机自启、卸载时清理用户数据（可选）。
;
; v13a 品牌与语言约定（用户定稿）：
;   中文官方名 = 掌镜桌面；英文名 = PhoneDesktop（exe / 数据目录不变）。
;   **界面文案一个语言走到底**——所有用户可见字符串走 {cm:...} / [Messages]，
;   禁止在 [Icons]/[Code] 里硬编码中文（此前「卸载 掌镜桌面」在英文安装器里
;   也是中文，属于中英混搭）。

#define MyAppName "掌镜桌面"
#define MyAppNameEn "PhoneDesktop"  ; exe 名 / 数据目录保持不变
#define MyAppVersion "8.3.3"
#define MyAppPublisher "随便玩玩ay"
#define MyAppExe "PhoneDesktop.exe"

; 打包目录名 / 产物名参数化：运行中的旧实例会锁住 dist\<BuildDir>，
; 换名重打时用 ISCC /DBuildDir=xxx /DOutBase=xxx 覆盖即可，不必改脚本。
#ifndef BuildDir
  #define BuildDir "PhoneDesktop_v14z"
#endif
#ifndef OutBase
  #define OutBase "PhoneDesktop_v14z_Setup"
#endif

[Setup]
; 免管理员安装：装到当前用户目录
PrivilegesRequired=lowest
AppId={{8A3F2C41-7D5E-4B9A-9E6F-DesktopOrganizer01}
; 显示名按当前语言取：中文「掌镜桌面」/ 英文 PhoneDesktop。
; AppId / DefaultDirName 不随语言变（覆盖升级与卸载记录稳定）。
AppName={cm:AppTitle}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; AppName 含 {cm:} 常量时 Inno 不再自动填 VersionInfo*，这里显式给编译期字面量。
VersionInfoDescription=掌镜桌面 安装程序
VersionInfoProductName=掌镜桌面
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={cm:AppTitle}
AllowNoIcons=yes
OutputDir=..\dist
OutputBaseFilename={#OutBase}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; v13a：向导视觉与新图标同源（tools/make_wizard_image.py）。
WizardSizePercent=120
; Inno 6 默认跳过欢迎页（DisableWelcomePage=auto）。产品图只挂在欢迎页/完成页上，
; 所以这里显式打开欢迎页，否则用户永远看不到它。
DisableWelcomePage=no
SetupIconFile=..\assets\app_icon.ico
WizardImageFile=..\assets\wizard.png
WizardSmallImageFile=..\assets\wizard_small.png
; v12c：按系统 UI 语言**自动选安装语言**，不再弹「选择安装语言」对话框。
;   LanguageDetectionMethod=uilanguage（默认值，显式写出）= GetUserDefaultUILanguage，
;   与应用侧 i18n.system_language() 同一口径；匹配顺序见官方 [Languages] 文档
;   （精确 LanguageID → 主语言 ID → 列表第一项兜底）。
;   ShowLanguageDialog=no = 不弹窗，直接用匹配到的语言。
;   仍可命令行覆盖：Setup.exe /LANG=english
; 依赖 languages\ChineseSimplified.isl 的 [LangOptions] LanguageID=$0804，
; 否则简体中文永远匹配不上（见该文件注释）。
LanguageDetectionMethod=uilanguage
ShowLanguageDialog=no
UninstallDisplayName={cm:AppTitle}
UninstallDisplayIcon={app}\{#MyAppExe}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
; Inno 官方不提供简体中文语言包，这里用官方的"多文件覆盖"机制：
; 先读英文底 Default.isl，再用我们自己的覆盖层盖掉实际会走到的消息。
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl,languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
; {cm:Name} 按当前语言取。Inno 的 & 是助记符，字面量 & 要写 &&。
; ---- 产品显示名（全界面唯一入口）----
chinesesimplified.AppTitle=掌镜桌面
english.AppTitle=PhoneDesktop
; ---- 开始菜单 / 卸载入口 ----
chinesesimplified.UninstallShortcut=卸载 掌镜桌面
english.UninstallShortcut=Uninstall PhoneDesktop
; ---- 任务页 ----
chinesesimplified.TaskGroup=可选任务：
chinesesimplified.TaskDesktopIcon=创建桌面快捷方式
chinesesimplified.TaskAutostart=开机自动启动
chinesesimplified.TaskClearData=清除旧版本的个人数据（布局与设置）
english.TaskGroup=Optional tasks:
english.TaskDesktopIcon=Create a desktop shortcut
english.TaskAutostart=Start with Windows
english.TaskClearData=Delete personal data from a previous version (layout && settings)
; ---- 完成页 / 运行 ----
chinesesimplified.RunApp=立即运行 掌镜桌面
english.RunApp=Launch PhoneDesktop now
; ---- 装前检测到程序在跑 ----
chinesesimplified.AppRunning=检测到 掌镜桌面 可能正在运行，建议先退出再安装。
english.AppRunning=PhoneDesktop appears to be running. Please close it, then continue.

[Tasks]
Name: "desktopicon"; Description: "{cm:TaskDesktopIcon}"
Name: "autostart"; Description: "{cm:TaskAutostart}"; Flags: unchecked
; v10 A2（反馈 3）：安装器只在卸载时清 %APPDATA%，覆盖安装会继承旧 state.json
; —— 用户在旧版里开过「开发者调试网格」，新版就带着满屏网格启动。
; 给一个装前清除的选项（默认不勾，避免误删用户布局）。
Name: "cleardata"; Description: "{cm:TaskClearData}"; Flags: unchecked

[Files]
; onedir 产物整体拷贝，保留子目录结构（与 build_portable.ps1 -OutName 对应）
Source: "..\dist\{#BuildDir}\PhoneDesktop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; v10 D1 改名（反馈 4）：AppId 不变（仍是同一个产品，覆盖升级走同一条记录），
; 但安装目录名换了。旧目录只放程序文件（用户数据在 %APPDATA%），整目录清掉。
Type: filesandordirs; Name: "{autopf}\DesktopOrganizerDemo"

[Icons]
; 全部走 {cm:...}，禁止硬编码中文快捷方式名（英文安装器曾出现「卸载 掌镜桌面」）。
Name: "{group}\{cm:AppTitle}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\{cm:UninstallShortcut}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{cm:AppTitle}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
; 开机自启任务：写 HKCU Run（与软件内设置保持一致，命令为安装后的实际路径）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "{#MyAppNameEn}"; \
    ValueData: """{app}\{#MyAppExe}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:RunApp}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清理用户数据（%APPDATA%\PhoneDesktop：布局与设置）。
; 改名前的旧目录也一起清（老用户升级后旧目录已被 v10 一次性迁移删掉，
; 这里兜住"从没跑过新版就卸载"的情况）。
Type: filesandordirs; Name: "{userappdata}\{#MyAppNameEn}"
Type: filesandordirs; Name: "{userappdata}\DesktopOrganizerDemo"

[Code]
// v12c：把安装器自动选出的语言写进应用数据目录，保证程序界面与安装向导同语。
// 写的是旁路文件 install_lang（旁路于 state.json，避免 Pascal 里改 JSON）。
// 应用侧 Storage 启动时合并进 settings.lang，优先级仍低于用户显式 language 设置。
procedure CurStepChanged(CurStep: TSetupStep);
var
  LangCode: String;
  DataDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    if ActiveLanguage = 'english' then
      LangCode := 'en'
    else
      LangCode := 'zh_CN';
    DataDir := ExpandConstant('{userappdata}\{#MyAppNameEn}');
    if not DirExists(DataDir) then
      ForceDirectories(DataDir);
    SaveStringToFile(DataDir + '\install_lang', LangCode, False);
  end;
end;

// 安装前如发现旧版便携包解压目录正在运行，提示关闭
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Wnd: HWND;
  DataDir: String;
begin
  Result := '';
  Wnd := FindWindowByClassName('Qt6103QWindowIcon');
  // 类名随 Qt 版本变化，仅作提示用途：找不到也不拦截
  if Wnd <> 0 then
    Result := ExpandConstant('{cm:AppRunning}');
  // v10 A2（反馈 3）：勾选「清除旧版本个人数据」时，装前删掉旧数据目录，
  // 让新版以默认设置启动（否则旧 state.json 的 dev_mode=True 会被继承）。
  if WizardIsTaskSelected('cleardata') then
  begin
    // 新旧两个目录都要清：只清新目录时，新版启动会从旧目录"迁移"回旧数据，
    // 用户勾了清除却还是旧配置（改名后的真实坑）。
    DataDir := ExpandConstant('{userappdata}\{#MyAppNameEn}');
    if DirExists(DataDir) then
      DelTree(DataDir, True, True, True);
    DataDir := ExpandConstant('{userappdata}\DesktopOrganizerDemo');
    if DirExists(DataDir) then
      DelTree(DataDir, True, True, True);
  end;
end;
