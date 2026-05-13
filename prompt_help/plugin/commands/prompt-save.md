---
description: 保存当前会话的好提示词，自动 polish + diff 确认
argument-hint: "[可选：标题]"
allowed-tools: Bash, Read
---

你的任务是帮用户保存一条值得复用的提示词到 prompt-help 库。

## 步骤

1. **读取最近用户消息**：
   - 调 `Bash` 跑 `python -m prompt_help.plugin.helpers list_recent_user_messages 8`，拿到最近 8 条用户消息
   - 用编号列表展示给用户，请用户选 1 条或合并多条（默认选最近 1 条）
   - 如果用户在 `$ARGUMENTS` 里给了具体内容，直接用用户给的内容，跳过这一步

2. **询问元数据**（一次性问完）：
   - 标题（一句话概括，10-30 字）
   - scope：`global`（推荐，跨项目）/ `project:<name>`（项目专属）/ `trap:<keyword>`（踩坑提醒，需要触发关键词）
   - tags（逗号分隔，可选；常用 tag：`playwright`, `ui`, `python`, `nextjs`, `git`, `test`, `api`）
   - stack（适用技术栈，可选；常用：`nextjs`, `react`, `python`, `fastapi`, `playwright`）
   - 若 scope 是 trap，还要问 triggers（用户消息里出现哪些关键词时自动召回，逗号分隔）

3. **保存**：
   - 拼出命令并跑：
     ```bash
     printf '%s' "<提示词正文>" | prompt-help save \
       --title "<标题>" \
       --scope <global|project|trap> \
       [--project <name>] \
       --tags "<a,b,c>" \
       --stack "<x,y>" \
       [--triggers "<k1,k2>"] \
       --polish
     ```
   - 注意：正文用 `printf '%s' '...' | prompt-help save ...` 通过 stdin 传，避免 shell 转义问题
   - 命令默认 `--polish` 自动用优化版；如果用户想看 diff 决定，加 `--polish-confirm`

4. **报告**：
   - 把 `prompt-help save` 的输出原样转给用户
   - 提醒：以后用 `/prompt-find` 跨项目检索

## 注意

- 不要自己发明提示词。只保存用户实际写过的话或他们明确指定的内容。
- 如果用户的消息含敏感信息（API key、密码、私人 token），先警告再决定是否保存。
- scope 如果用户说不清，默认 global。
