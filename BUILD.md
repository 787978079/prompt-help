# 打包成单 .exe

让朋友 / 换电脑 不用装 Python 也能直接跑。

## 第一次打包

```powershell
# 1. 装 PyInstaller（如未装）
pip install pyinstaller

# 2. 跑打包（在项目根目录）
pyinstaller prompt_help.spec --noconfirm

# 3. 产物：dist/PromptHelp.exe（约 80-120MB，含 PySide6 + Qt 资源）
```

## 验证

```powershell
# 双击 dist/PromptHelp.exe 应该弹出 GUI；首次启动会进 setup 对话框
.\dist\PromptHelp.exe
```

第一次启动看不到窗口的话：

- 看任务栏有没有图标
- 用 `--console` 模式重打看错误（改 `prompt_help.spec` 里 `console=False` → `True`）
- 或临时跑 `python -m prompt_help.gui` 验证不是打包问题

## 分发给朋友

把 `dist/PromptHelp.exe` 单文件发给朋友，双击就能跑。**不需要装 Python**，不需要装 PySide6，不需要任何依赖。

朋友首次打开会走 setup 对话框：选 vault 路径（默认 `~/.prompt-help`） + 可选填 LLM API key。

## 给 .exe 加图标

`prompt_help.spec` 已配置 `icon="assets/icon.ico"`。如需重新生成：

```powershell
pip install Pillow
python assets/make_icon.py
pyinstaller prompt_help.spec --noconfirm
```

## 打成 Windows installer（.exe → 安装包）

让朋友双击安装到「程序」目录、自带卸载入口、桌面快捷方式：

```powershell
# 1. 装 Inno Setup（免费）
#    https://jrsoftware.org/isinfo.php

# 2. 用 Inno Setup Compiler 打开 assets/installer.iss

# 3. 点 Compile → 产物：dist/PromptHelp_Setup.exe（约 60MB）

# 朋友双击 PromptHelp_Setup.exe 安装；卸载在 Windows「应用」里。
```

## SmartScreen 警告

未数字签名的 .exe 在 Windows 10/11 首次运行时会被 SmartScreen 拦一下：

> "Windows protected your PC"

朋友需要点「More info → Run anyway」。永久去掉警告需要购买代码签名证书（年付 ¥600+），
个人用 / 小圈子分发可忽略。

## 常见问题

**Q：打包速度慢、产物大？**
A：`--onefile` 模式会把所有依赖装进单个 .exe，启动时解压到临时目录。改成 `--onedir`（默认）会快但产物是个文件夹。

**Q：能否再瘦身？**
A：`upx=True` 用 UPX 压缩可省 30-50%，但 Windows Defender 偶尔会误报；`exclude` 里再加用不到的子模块也能省一点。

**Q：跨平台（mac / Linux）？**
A：在哪个系统跑 PyInstaller，产物就是哪个系统的。要给朋友的 mac 打包，就在 mac 上跑一次。
