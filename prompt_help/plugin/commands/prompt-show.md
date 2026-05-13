---
description: 显示一条提示词的完整内容
argument-hint: "<id 或 title 或关键词>"
allowed-tools: Bash
---

展示用户指定的提示词完整内容。

跑：
```bash
prompt-help show "$ARGUMENTS"
```

如果未命中，工具会按关键词模糊搜，取第一条。

把输出展示给用户。如果用户想用这条提示词，建议：
- 直接复制内容用作下一条消息的素材
- 或者参考其结构改写成更贴合当前场景的版本
