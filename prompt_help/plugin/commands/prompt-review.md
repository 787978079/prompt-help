---
description: 三餐式审查 mining 留下的候选提示词（一次最多 5 条）
allowed-tools: Bash, Read
---

帮用户批量过审 prompt-help inbox 里的提示词候选。

## 流程

1. **拉候选清单**（JSON）：
   ```bash
   prompt-help inbox list --json --limit 5
   ```
   解析返回的数组，每条含 `filename`, `confidence`, `suggested_title`, `origin`, `preview`。

2. **空检查**：列表为空就告诉用户"暂无候选"并退出。

3. **逐条展示并询问**（一次只展示 1 条，不要一次性把 5 条全堆出来）：
   - 展示：`#N (conf=X) [origin]  preview…`
   - 如果 `confidence < 0.4`，建议用户直接 dismiss（信号弱）
   - 选项：
     - **save**：进 4
     - **skip**：跑 `prompt-help inbox dismiss <filename>`，进下一条
     - **defer**：跳过，留待下次
     - **看完整**：跑 `prompt-help inbox preview <filename>` 再问

4. **save 流程**：一次性问完元数据
   - 标题（默认用 suggested_title 或正文首行；让用户改）
   - scope：`global` / `project:<name>` / `trap`
   - tags（可选，逗号分隔）
   - stack（可选，逗号分隔）
   - 若 scope=trap，还要问 triggers
   - 调：
     ```bash
     prompt-help inbox approve <filename> \
       --title "<title>" \
       --scope <scope> \
       [--project <name>] \
       --tags "<a,b>" \
       --stack "<x,y>" \
       [--triggers "<k1,k2>"] \
       --polish
     ```
   - approve 成功后会自动从 inbox 移除

5. **结束总结**：保存 N 条 / 跳过 M 条 / 留 K 条，提醒下次还可以 `/prompt-review`。

## 注意

- **一次最多 5 条**，避免审阅疲劳
- 重复内容（preview 与库里某条提示词高度相似）建议直接 dismiss
- 含敏感信息（API key、密码、token）的候选先警告再决定
