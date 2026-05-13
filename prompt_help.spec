# PyInstaller spec for Prompt Help GUI（双击即跑的 .exe）。
#
# 用法：
#   pip install pyinstaller
#   pyinstaller prompt_help.spec
# 产物：dist/PromptHelp.exe（含 PySide6 + data + plugin 资源）

# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 跟着进 .exe 的资源（YAML、md、json）
datas = []
datas += collect_data_files("prompt_help", includes=["**/*.yaml", "**/*.md", "**/*.json"])
# qtawesome 字体文件（fa6 / mdi / phosphor / codicon 等 ttf）必须打进 .exe，
# 否则打包后图标全显示成方块。
datas += collect_data_files("qtawesome")
# P18：把 logo PNG 打进去供 About 对话框用
datas += [("assets/icon-256.png", "assets")]

# 隐式 import（PyInstaller 静态扫描可能漏）
# P21：qtawesome 的 iconic_font/animation/styles 子模块必须显式 hidden 加进去，
# 否则打包后 qta.icon() 找不到 IconicFont 类，矢量图标全显示空白
hidden = (
    collect_submodules("prompt_help")
    + collect_submodules("qtawesome")
    + [
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "qtpy",
        "openai",
        "git",
        "yaml",
        "ulid",
        "tomli_w",
    ]
)

a = Analysis(
    ["prompt_help/gui/__main__.py"],
    pathex=[str(Path(".").resolve())],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tests",
        "pytest",
        "pytest_qt",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PromptHelp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                # GUI 模式（不弹控制台黑窗）
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",       # P18：黑底圆角 + 白色 Ph + 金色高光点
    version="assets/version_info.txt",  # P18：Windows .exe 右键属性的版本号 / 公司 / 版权
)
