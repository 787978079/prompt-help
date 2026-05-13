"""提示词自动分类（T4）。

12 类（V1 锁定，按用户反馈中提到的"前端/后端/数据库..."细分）：
  前端、后端、数据库、DevOps、测试、重构、调试、文档、设计、AI/ML、项目管理、通用工程

实现：
  - rule_classify(text) → list[str]：关键词字典匹配，多分类
  - 兜底：所有规则都没命中时归"通用工程"
  - ai_classify_batch（V2 再做）：用 optimizer 后端批量分类
"""

from __future__ import annotations

import re
from typing import Iterable


CATEGORIES = (
    "前端", "后端", "数据库", "DevOps", "测试", "重构",
    "调试", "文档", "设计", "AI/ML", "项目管理", "通用工程",
)


# 每类的关键词字典（小写匹配；CJK 和英文都列出）
_RULES: dict[str, list[str]] = {
    "前端": [
        "react", "vue", "tailwind", "nextjs", "next.js", "vite", "webpack",
        "css", "scss", "less", "html", "dom", "frontend", "前端",
        "组件", "路由", "ui", "界面", "样式", "布局", "动画", "framer-motion",
        "tsx", "jsx", "shadcn", "lucide", "图标", "字体",
    ],
    "后端": [
        "api", "fastapi", "flask", "express", "django", "spring", "rails",
        "endpoint", "server", "route", "middleware", "rpc", "grpc", "graphql",
        "后端", "接口", "服务端", "rest", "websocket", "node.js",
    ],
    "数据库": [
        "sql", "sqlite", "postgres", "postgresql", "mysql", "mariadb",
        "mongodb", "mongo", "redis", "cassandra", "schema", "migration",
        "数据库", "索引", "查询", "事务", "orm", "prisma", "drizzle",
        "sqlalchemy", "pg", "fts5",
    ],
    "DevOps": [
        "docker", "kubernetes", "k8s", "helm", "terraform", "ansible",
        "github actions", "gitlab ci", "circleci", "deploy", "deployment",
        "ci/cd", "jenkins", "nginx", "pm2", "supervisor", "systemd",
        "部署", "运维", "上线", "热更新", "灰度",
    ],
    "测试": [
        "test", "tests", "pytest", "jest", "mocha", "vitest", "cypress",
        "playwright", "selenium", "puppeteer", "unit test", "integration test",
        "e2e", "单测", "测试", "覆盖率", "coverage", "mock", "fixture",
        "mocking", "stub", "tdd",
    ],
    "重构": [
        "refactor", "rename", "extract", "inline", "reorganize", "cleanup",
        "重构", "重命名", "抽取", "整理", "拆分", "合并", "消除重复",
        "改名", "重写",
    ],
    "调试": [
        "debug", "bug", "error", "exception", "stack trace", "stacktrace",
        "log", "logging", "trace", "breakpoint", "profile",
        "调试", "排查", "错误", "异常", "日志", "堆栈", "复现",
    ],
    "文档": [
        "doc", "docs", "documentation", "readme", "comment", "docstring",
        "tutorial", "guide", "manual",
        "文档", "注释", "教程", "说明", "指南", "手册", "用户文档",
    ],
    "设计": [
        "design", "ux", "ui design", "figma", "sketch", "prototype",
        "wireframe", "mockup",
        "设计", "原型", "视觉", "排版", "配色", "交互", "用户体验",
    ],
    "AI/ML": [
        "ai", "ml", "llm", "neural", "model", "embedding", "rag",
        "prompt", "prompt engineering", "fine-tune", "fine tuning",
        "chatgpt", "gpt", "claude", "deepseek", "anthropic", "openai",
        "transformer", "attention",
        "提示词", "模型", "微调", "向量", "嵌入", "智能体", "agent",
    ],
    "项目管理": [
        "plan", "roadmap", "milestone", "sprint", "kanban", "scrum",
        "epic", "story", "task tracking", "ticket", "issue tracker",
        "计划", "规划", "里程碑", "排期", "进度", "任务管理", "需求",
        "产品",
    ],
}


def rule_classify(text: str) -> list[str]:
    """关键词匹配多分类。所有规则都没命中时返回 ['通用工程']。"""
    if not text:
        return ["通用工程"]
    haystack = text.lower()
    matched: list[str] = []
    for cat, keywords in _RULES.items():
        for kw in keywords:
            kw_l = kw.lower()
            # 整词匹配（CJK / 英文短语都用 in 即可，不需要 word boundary）
            if kw_l in haystack:
                matched.append(cat)
                break
    return matched if matched else ["通用工程"]


def classify_with_existing_tags(text: str, tags: Iterable[str]) -> list[str]:
    """带 tags 提示的分类（tags 也参与匹配）。"""
    combined = text + " " + " ".join(tags)
    return rule_classify(combined)
