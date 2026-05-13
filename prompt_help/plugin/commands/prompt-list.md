---
description: 浏览全库或某个 scope 的提示词
argument-hint: "[可选：global | project:<name> | trap]"
allowed-tools: Bash
---

列出 prompt-help 库里的提示词。

## 解析参数

- `$ARGUMENTS` 为空 → 跑 `prompt-help list --limit 50`
- `$ARGUMENTS` 是 `global` / `trap` → 跑 `prompt-help list --scope <s>`
- `$ARGUMENTS` 是 `project:<name>` → 跑 `prompt-help list --scope project --project <name>`

把表格输出给用户，并提示可以用 `/prompt-show <title>` 看完整内容、`/prompt-find <query>` 检索。
