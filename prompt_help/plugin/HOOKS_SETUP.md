# Hook 安装说明

如果 `prompt-help install-plugin` 后 Claude Code 没有自动加载 hooks，请把以下片段
合并到你的 `~/.claude/settings.json`（注意保持已有 hooks 不被覆盖）：

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python ~/.claude/plugins/prompt-help/hooks/stop.py"
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python ~/.claude/plugins/prompt-help/hooks/session_start.py"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python ~/.claude/plugins/prompt-help/hooks/user_prompt_submit.py"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python ~/.claude/plugins/prompt-help/hooks/pre_compact.py"
          }
        ]
      }
    ]
  }
}
```

## Windows 路径

Windows 用户把命令路径替换为绝对路径，例如：

```
python C:/Users/<你>/.claude/plugins/prompt-help/hooks/stop.py
```

## 验证

1. 重启 Claude Code
2. 跑 `/prompt-list`，应能看到（即使空库也不报错）
3. 在任意项目里跟 Claude 对话；当 assistant 回 "完美"、"搞定" 之类的成功信号且你最近有
   一条 200+ 字的结构化提示词时，会看到 [prompt-help · 检测到值得保存的提示词 ...] 的
   系统提醒
4. `cd` 到含 package.json/pyproject.toml 的项目并启动 Claude，session 开始时会显示
   匹配的提示词列表

## 排查

- hook 报错都写在 `~/.prompt-help/logs/hooks.log`
- 完全关闭推送：编辑 `~/.prompt-help/config.toml` 把 `mining.enabled = false`、
  `trap_recall.enabled = false`
