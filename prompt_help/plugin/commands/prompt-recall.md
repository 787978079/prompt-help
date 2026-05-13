---
description: Inline 召回——自动按当前项目 + 关键词搜出最相关提示词并展示内容
argument-hint: "[关键词，可选]"
allowed-tools: Bash
---

把 prompt-help 库里**最相关的一条提示词正文**直接放出来，省去手工复制粘贴。

## 步骤

1. 取当前 cwd（`pwd` 或从对话上下文猜）
2. 跑 `prompt-help match-project --cwd "$CWD" --json` 看是否匹配到已登记项目
   - 若匹配到：得到 project_name
   - 若没匹配：跳过项目过滤
3. 根据用户参数 `$ARGUMENTS`：
   - 有关键词 → `prompt-help find "$ARGUMENTS" --top-k 5 --inline --json`
   - 无关键词 → `prompt-help find "" --project <name> --is-template --top-k 5 --inline --json`（取该项目最常用的通用模板）
4. 解析 JSON，按 score 排序，对 top 1：
   - 如果含占位符（正文里有 `[XXX]` / `{XXX}`） → 提示用户「这条含 N 个占位符：[...]，要填值吗？」
   - 否则 → **直接把正文展示给用户**（用 markdown 代码块包裹）
5. 同时显示 top 2-5 的标题让用户知道还有别的可选

## 关键约束

- **绝不要自己执行那条提示词**——只是召回展示
- 标题前缀显示来源（项目专属 / 通用模板 / 踩坑提醒）
- 若库为空，提示用户去 PH GUI 导入或写第一条
- 若 `prompt-help` 不在 PATH，告诉用户跑 `pip install -e ".[gui]"`

## 输出格式

```
📌 最相关：「<标题>」（用过 N 次 · 成功信号 K）
来源：<project_name 或 通用模板>

<正文>

—— 其他候选：
  · <title 2>
  · <title 3>
```
