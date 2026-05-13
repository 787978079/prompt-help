# 贡献指南

感谢你考虑给 Prompt Help 提交代码或反馈！

## 报问题（Issue）

报 bug 时请附上：

- 操作系统 + Python 版本（`python --version`）
- PH 版本（`prompt-help --version`，或截图首页底部 `v0.1`）
- 复现步骤
- 期望行为 vs 实际行为
- 相关日志 `~/.prompt-help/logs/hooks.log`（已脱敏后）

## 提交 PR

### 环境

```powershell
git clone https://github.com/787978079/prompt-help.git
cd prompt-help
pip install -e ".[dev,gui]"
pytest          # 应该全绿
ruff check .    # 不要新增 warning
```

### 改动规则

1. **不许把 API key / 真实邮箱 / 个人路径写进代码或 commit**
   - key 统一从 `os.getenv(cfg.llm.api_key_env)` 读
   - 写测试时 mock key 用 `sk-` 前缀但是明显假值（如 `sk-1234567890abcdef...`）

2. **永不阻塞 Claude Code hook**
   - 所有 hook 走 `_runtime.safe_main`，异常吞到 `~/.prompt-help/logs/hooks.log`，永远 exit 0

3. **文件系统是真相源**
   - SQLite 索引可随时 `prompt-help reindex` 重建
   - 不要在 indexer 上加只在数据库里存的"独家字段"——frontmatter 必须能完整恢复

4. **GUI 改动**
   - 起 `python -m prompt_help.gui.app` 实际跑一次
   - 加 `tools/snap_all_pages.py` 截图证明改动

5. **测试**
   - 新功能必须带 pytest case
   - 不写实际启动 Qt 的测试时设 `QT_QPA_PLATFORM=offscreen`

### 提 PR 前 self-check

- [ ] `pytest` 全绿
- [ ] `ruff check .` 不新增 warning
- [ ] PR 描述里说清"为什么改"，不只说"改了什么"
- [ ] 截图证明 GUI 改动有效（如适用）
- [ ] 没有 secrets / 个人邮箱 / 写死的本机路径

## 行为准则

请保持友善、专业。不接受人身攻击 / 歧视性语言 / spam。

## 开发约定

详见 [CLAUDE.md](./CLAUDE.md) — 项目级开发约定（模块边界、命名风格、坑点）。
