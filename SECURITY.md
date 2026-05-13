# 安全策略

## 报告漏洞

如果你发现安全漏洞，**请不要直接开 public issue**。

请通过 GitHub Security Advisory 私下报告：
1. 进入仓库 → Security 标签 → "Report a vulnerability"
2. 描述漏洞 + 复现步骤 + 影响范围
3. 我们会在 7 天内首次响应

或邮件联系：通过 GitHub profile 的联系方式。

## 受影响版本

只对当前 main 分支 + 最近 1 个 release 提供安全修复。

## 数据安全约束（设计原则）

PH 的代码里**不会**：

- 把 API key 写死在源码
- 收集 / 上报 telemetry 到任何外部服务器
- 自动上传你的提示词到任何远程地址

PH 的代码里**会**：

- 读 `os.getenv(<key_env>)` 拿 API key（你的 .env 或环境变量）
- 把你的提示词存到本地 `~/.prompt-help/`（你完全可控）
- 可选地自动 `git push` 到**你指定**的私仓地址（你在 `prompt-help link-remote <url>` 时配置；不指定就只本地）

## 第三方依赖

主要外部 API：
- DeepSeek API（你提供 key 才会调用）
- Anthropic Claude Code CLI 子进程（你本机已装才会调用）
- OpenAI Codex CLI 子进程（同上）

PH 不会绕过这些 CLI 自己跟 Anthropic / OpenAI 服务器对话。

## 已知不安全场景

- `prompt-help install-plugin` 把 `.py` 文件软链到 `~/.claude/plugins/prompt-help/`，Claude Code 启动时会执行。审查 `prompt_help/plugin/` 目录所有代码后再装
- 自动同步到 GitHub 私仓时如果误设成 public 仓库，提示词会公开。**首次 push 前确认 visibility**
