---
description: 跨项目检索已保存的提示词
argument-hint: "<查询关键词>"
allowed-tools: Bash
---

帮用户检索 prompt-help 库里的提示词。

## 步骤

1. 跑 `prompt-help find "$ARGUMENTS" --top-k 10` 拿候选列表
2. 把表格原样展示给用户
3. 询问用户要看哪一条的完整内容（按编号 / 按标题）
4. 若用户选了，跑 `prompt-help show "<id 或 title>"` 把完整正文展示出来
5. 若用户想直接复用，把正文当作下一条消息的素材或建议用户复制

## 检索技巧

- 查询太宽时，建议加 `--scope global` / `--scope trap` 缩小范围
- 想看正文摘要：`prompt-help find "<query>" --body --top-k 5`
- 输出 JSON 给后续 pipeline：`prompt-help find "<query>" --json`
