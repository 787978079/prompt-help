; Prompt Help · Inno Setup installer（Phase 18 完善版）
;
; 用法：
;   1. 装 Inno Setup 6+（免费）：https://jrsoftware.org/isinfo.php
;   2. 先 pyinstaller prompt_help.spec 生成 dist/PromptHelp.exe
;   3. 用 Inno Setup Compiler 打开本文件（双击 .iss）
;   4. 点 "Compile"，产物在 dist/PromptHelp_Setup.exe
;
; 安装行为：
;   - PromptHelp.exe 装到 %LocalAppData%\Programs\PromptHelp\
;   - 开始菜单创建快捷方式 + 卸载入口
;   - 可选桌面快捷方式（默认勾选）
;   - 注册标准卸载入口（控制面板可见）
;   - 可选注册 .phzip 文件关联（双击打开 PH 自动导入）

#define MyAppName "Prompt Help"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "linguofeng"
#define MyAppExeName "PromptHelp.exe"

[Setup]
AppId={{B0EE7A3F-PROMP-HELP-VAULT-MGR-2026}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppComments=跨项目沉淀提示词 · 系统记忆 · 项目踩坑点
AppContact={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\PromptHelp
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=PromptHelp_Setup
SetupIconFile=icon.ico
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; 安装包元数据（控制面板「程序和功能」会读）
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}
VersionInfoCopyright=© 2026 {#MyAppPublisher}

[Languages]
Name: "chs"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce
Name: "associate_phzip"; Description: "把 .phzip 文件关联到 Prompt Help（双击导入分享包）"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; .phzip 文件关联（双击调起 PromptHelp.exe 并自动走 import 流程）
Root: HKCU; Subkey: "Software\Classes\.phzip"; ValueType: string; ValueName: ""; ValueData: "PromptHelp.SharePack"; Tasks: associate_phzip; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\PromptHelp.SharePack"; ValueType: string; ValueName: ""; ValueData: "Prompt Help 分享包"; Tasks: associate_phzip; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\PromptHelp.SharePack\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: associate_phzip
Root: HKCU; Subkey: "Software\Classes\PromptHelp.SharePack\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: associate_phzip

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
