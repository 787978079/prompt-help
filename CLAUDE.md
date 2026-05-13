# CLAUDE.md — Prompt Help 项目开发约定

## 项目定位

跨项目提示词管理 + 对话挖掘 + 产品发现工具。**Phase 1 已实现：Vault + 手动捕获 + 积极推送 mining + trap 自动召回 + git 私仓同步。**

## 关键约束

- **Python 3.11+**，依赖见 pyproject.toml
- **不许把 API key 写进代码或 commit 进 git**。统一从 `os.getenv(cfg.llm.api_key_env)` 读，默认 `DEEPSEEK_API_KEY`
- **hooks 永不阻塞 Claude Code**：所有 hook 走 `_runtime.safe_main`，异常吞到 `~/.prompt-help/logs/hooks.log`，永远 exit 0
- **文件系统是真相源**，SQLite 索引可随时 `prompt-help reindex` 重建，不要依赖索引独有的字段
- **frontmatter 字段对齐 AGENTS.md / Cursor Rules 习惯**，方便后续导出兼容

## 模块边界

```
prompt_help/
├── gui/          ← PySide6 桌面 GUI（pip install -e ".[gui]" 启用）
│   ├── app.py            QApplication 入口 + 首次启动判断
│   ├── main_window.py    侧边栏 + QStackedWidget 主窗口
│   ├── theme.py          QSS 样式 + 调色板
│   ├── pages/            library / inbox / pm_mode / settings / help
│   ├── widgets/          prompt_editor 等组件
│   └── onboarding/       wizard（5 步向导）+ tour（4 步教程）
├── core/         ← 不依赖 Typer/CC/Qt，纯库；CLI、GUI、插件都调它
│   ├── config.py        配置 + .env 加载（dotenv 简版自实现）
│   ├── storage.py       Prompt dataclass + frontmatter 序列化 + git auto-commit
│   ├── indexer.py       SQLite FTS5 + 评分排序 + trap 召回 + projects 注册表
│   ├── transcript.py    Claude Code JSONL transcript 解析
│   ├── optimizer.py     DeepSeek API polish + diff 渲染
│   └── fingerprint.py   项目栈指纹 + Jaccard / overlap_coefficient / 栈重合度
├── cli/          ← Typer 入口；slash command 也通过它
│   ├── main.py          init / doctor / install-plugin / link-remote
│   ├── actions.py       save / find / show / list / find-traps（slash 复用）
│   ├── admin.py         import-claude-md / reindex / sync / prune / why-matched
│   │                     / match-project / register-project / list-projects
│   ├── inbox.py         inbox list / preview / approve / dismiss / clear
│   └── pm_mode.py       pm-mode start / set / get / list / brief / delete
│                         + prior-art-suggest / tech-risks-suggest
└── plugin/       ← Claude Code 插件资源（被 install-plugin 拷到 ~/.claude/plugins/prompt-help/）
    ├── commands/         /prompt-* slash 命令的 .md（含 /prompt-review）
    ├── hooks/            stop / session_start / user_prompt_submit / pre_compact
    ├── helpers.py        slash command 辅助函数（list_recent_user_messages 等）
    └── plugin.json       插件 manifest
```

## 开发回路

```powershell
# 安装可编辑模式
pip install -e ".[dev]"

# 跑测试
pytest

# lint
ruff check .

# 本地实际跑（小心：会真的写 ~/.prompt-help）
$env:PROMPT_HELP_VAULT_PATH = "D:\tmp\prompt-help-test"  # 先指向测试目录
prompt-help init --no-create-remote
prompt-help doctor
```

## 代码风格

- 函数级 docstring 用一句话；不需要的别写
- 不要写"什么代码做什么"的注释；只写为什么这样做、隐含约束、踩坑教训
- `try/except: pass` 仅在 hook 容错路径用，业务代码必须显式处理或上抛
- 公开类型用 dataclass；不用 Pydantic（避免引入额外重依赖）

## 加新功能时的检查清单

- [ ] 加了新 CLI 命令？在 `cli/main.py` 或 `cli/actions.py`/`admin.py` 注册
- [ ] 加了新 slash command？写一个 `plugin/commands/<name>.md`，跑一遍 install-plugin
- [ ] 加了新 hook？挂到 `plugin/plugin.json` + 在 `HOOKS_SETUP.md` 同步示例
- [ ] 改了存储格式？写个迁移函数或 `prompt-help reindex` 能直接重建
- [ ] 调了 LLM 的 prompt？在 `optimizer.py` 写明改前/改后区别
- [ ] 改了 mining 启发式？同步更新 README 路线图

## 路线图（按 plan 文件 harness-llm-llm-harness-vibe-wondrous-pancake.md）

- ✅ Phase 1：Vault + 手动捕获 + 积极推送 mining + trap 召回
- ✅ Phase 2：SessionStart auto-attach 增强 + `/prompt-review` 三餐式审查 + PreCompact 二次挖掘
- Phase 3：embedding rerank（vendor 启动时调研，首选 DeepSeek 复用 key）+ A/B 选优 + 批量导入 GitHub/URL
- ✅ Phase 4.1：PM-Mode 7 阶段访谈 + PRODUCT_BRIEF.md 装配
- ✅ Phase 4.2：MCP/skill 周脉搏（CLI 完整：sources/fetch/digest/show/mark-read；GUI 入口待补）
- ✅ Phase 5：桌面 GUI（PySide6）+ 首次启动向导 + 4 步 tooltip 教程

## 已知坑

- **CC 插件 manifest 格式仍在演进**：Phase 1 的 `plugin.json` 是当前 best guess，若 CC
  版本不识别，靠 `HOOKS_SETUP.md` 里的 settings.json 片段兜底
- **Windows 路径含空格**：`D:\My_Project\Prompt help\` 含空格，所有 subprocess 调用
  必须用 list 形式（不要拼字符串）；安装到 `~/.claude/plugins/prompt-help/`（无空格）
- **DeepSeek API 速率限制未做退避**：optimizer.py 失败直接 fallback 到原版，不重试。
  批量 import 时如果连续失败可暂停跑
