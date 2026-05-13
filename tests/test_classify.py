"""T4 自动分类单测。"""

from prompt_help.core import classify


def test_frontend_classified():
    cats = classify.rule_classify("用 React + Tailwind 写一个组件")
    assert "前端" in cats


def test_database_classified():
    cats = classify.rule_classify("把数据从 PostgreSQL 迁到 SQLite")
    assert "数据库" in cats


def test_test_classified():
    cats = classify.rule_classify("跑 pytest 看哪条用例失败")
    assert "测试" in cats


def test_devops_classified():
    cats = classify.rule_classify("用 Docker 部署到 k8s 集群")
    assert "DevOps" in cats


def test_ai_ml_classified():
    cats = classify.rule_classify("调用 Claude API 做 prompt 工程")
    assert "AI/ML" in cats


def test_multi_category():
    """同时匹配多类。"""
    cats = classify.rule_classify(
        "用 React 前端 + FastAPI 后端 + PostgreSQL 数据库做个全栈应用"
    )
    assert "前端" in cats
    assert "后端" in cats
    assert "数据库" in cats


def test_fallback_to_general():
    cats = classify.rule_classify("总结一下今天的会议内容")
    assert cats == ["通用工程"]


def test_chinese_keywords():
    cats = classify.rule_classify("帮我审查这段代码的错误处理和异常路径")
    assert "调试" in cats


def test_classify_with_tags_helps():
    """tags 也参与匹配。"""
    text = "把这段代码改成更优雅的写法"
    cats = classify.classify_with_existing_tags(text, ["refactor", "python"])
    assert "重构" in cats
