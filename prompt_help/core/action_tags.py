"""动作类型四字标签：给提示词标注"做什么类型的事"。

跟 `tags_csv`（python / ui / test 这种技术栈关键词）和 `categories_csv`
（前端/后端/...12 类）都正交：action_tag 只描述**任务动作类型**，每条 prompt
归 0 或 1 个标签，方便在我的库里按"我现在要找设计优化类的"维度筛。

14 个固定标签 + 空（未标）。
"""

from __future__ import annotations

import re
from typing import Iterable

# ----- 14 个固定标签（不带 emoji） -----
TAG_DESIGN = "设计优化"
TAG_ENV = "环境检查"
TAG_REVIEW = "代码审查"
TAG_DEBUG = "调试排错"
TAG_TEST = "测试覆盖"
TAG_DOC = "文档撰写"
TAG_DATA = "数据处理"
TAG_REFACTOR = "重构改造"
TAG_PERF = "性能优化"
TAG_SECURITY = "安全检查"
TAG_DEPLOY = "部署发布"
TAG_PRODUCT = "产品发现"
TAG_KNOWLEDGE = "知识总结"
TAG_OTHER = "其他通用"

ALL_TAGS: list[str] = [
    TAG_DESIGN, TAG_ENV, TAG_REVIEW, TAG_DEBUG, TAG_TEST,
    TAG_DOC, TAG_DATA, TAG_REFACTOR, TAG_PERF, TAG_SECURITY,
    TAG_DEPLOY, TAG_PRODUCT, TAG_KNOWLEDGE, TAG_OTHER,
]

ALL_TAGS_SET: frozenset[str] = frozenset(ALL_TAGS)


_KEYWORDS: dict[str, tuple[str, ...]] = {
    TAG_DESIGN: (
        "ui ", "ui/ux", "界面", "布局", "样式", "组件", "spotlight",
        "tooltip", "新手引导", "tour", "可视化", "css", "tailwind",
        "前端样式", "交互", "用户体验", "页面", "前端", "react",
        "vue", "小程序前端", "h5", "渲染", "弹窗", "modal",
        "侧边栏", "导航", "首页", "首页滑动", "滑动",
        "推荐库 ui", "ui 重构", "ui 多", "emoji 表情",
        "专利图", "技术路线图", "图修订", "fig", "路线图",
        "图表", "图1", "图2", "图3", "图4",
    ),
    TAG_ENV: (
        "环境", "依赖", "状态污染", ".env", "环境变量", "配置文件",
        "端口", "服务依赖", "前置依赖", "envcheck", "环境检查",
        "前置环境", "服务运行", "docker-compose", "节点版本",
        "禁止杀", "node 进程", "win 异步", "windows 兼容",
        "异步兼容", "claude.md", "踩坑速查",
        "工作流约定",
        "asyncio", "事件循环",
        "中文编码", "utf-8 编码", "中文编码规范",
        "项目踩坑",
        "sys.platform", "set_event_loop_policy", "win32",
        "playwright 设置", "playwright 环境",
    ),
    TAG_REVIEW: (
        "review", "代码审查", "pr 评审", "代码评审", "code review",
        "审视", "把关", "code-review", "审查者", "code reviewer",
    ),
    TAG_DEBUG: (
        "bug", "报错", "调试", "排错", "异常", "stack trace",
        "traceback", "排查", "诊断", "为什么不", "为什么会",
        "debug ", "崩溃", "失败原因", "nul 报错", "索引错误",
        "git 索引", "缺陷", "退化", "根因", "硬编码", "为何还没",
        "失效", "缺测试", "测试问题", "全量备", "版本对比",
        "对比缺陷", "失败", "discovery turn", "撞 idle",
    ),
    TAG_TEST: (
        "pytest", "vitest", "测试覆盖", "单元测试", "集成测试",
        "e2e", "回归", "playwright", "test case", "测试用例",
        "tdd", "金案例", "对照测试", "真机模拟", "真机调试",
        "脚本验证", "脚手架", "手动验证",
        "真实评测", "用户体验测", "评测目前", "用户视角",
        "对软件进行评测", "评测",
        "金案例对标", "对标修复", "对标", "标准水平",
        "模拟用户", "提交进度核查", "进度核查",
    ),
    TAG_DOC: (
        "readme", "文档撰写", "docstring", "注释", "api doc",
        "技术文档", "使用说明", "写文档", "更新文档", "comment",
        "教程", "claude.md", "agents.md", "规范文档",
        "导航", "项目导航",
        "npm run dev", "npm run build", "dev server",
        "hot reload", "next.js 启动", "next.js 命令",
    ),
    TAG_DATA: (
        "sql", "数据库", "查询", "表结构", "schema", "数据迁移",
        "etl", "csv", "数据清洗", "json 解析", "数据分析",
        "数据导出", "图片", "下载脚本", "批量抓取", "采集", "爬取",
        "批量下载", "批量处理", "字段填写", "学术文献", "综述",
        "结构化", "字段提取", "信息提取", "标准流程", "标准化",
        "概览", "数据编号", "编号规范", "档案", "批量生成",
        "api 端点", "文献综述", "调研主题", "学术框架",
        "资源概览", "资源清单", "申报材料", "合规校验",
        "格式规范化", "章节保留", "章节增强", "章节生成",
        "调研数据", "调研报告", "数据契约", "json schema",
        "数据流",
        "非遗", "遗产", "物质文化遗产", "非物质文化遗产",
        "公开数据源", "数据源", "credentials",
        "归档", "manifests", "名录",
        "评定", "质量评分", "增强模式", "保留模式",
        "软著申报", "软著", "用户手册",
    ),
    TAG_REFACTOR: (
        "重构", "refactor", "抽象", "拆分", "解耦", "架构调整",
        "提取函数", "extract method", "代码结构", "整理代码",
        "重写", "改造", "ui 重构", "整体重构",
        "pipeline", "agent loop", "agent 运行时", "可靠性中间件",
        "中间件", "loopdetector", "tdd 验证", "三层", "两层",
        "四层", "abcd", "abcd pipeline",
        "修复第", "修复规划", "推前清理", "残留", "垃圾产物",
        "聚焦", "工作站", "技术契约", "数据契约",
        "桌面级", "深度优化",
        "discovery_agent", "discovery agent", "tool-use agent",
        "三档降级", "三档", "降级", "agent 循",
        "self.artifacts", "步骤间", "12 个步骤",
        "超时事件", "墙钟超时", "活性超时", "liveness",
        "重构建议",
    ),
    TAG_PERF: (
        "性能", "优化速度", "内存占用", "慢", "瓶颈", "perf",
        "profiling", "缓存", "querying 优化", "n+1", "卡顿",
    ),
    TAG_SECURITY: (
        "安全", "鉴权", "权限", "注入", "xss", "csrf", "漏洞",
        "敏感数据", "secret", "api key", "密码", "owasp",
        "验证码", "认证", "会话存储", "企业微信认证",
        "提交端", "自动提交",
    ),
    TAG_DEPLOY: (
        "部署", "发布", "ci/cd", " ci ", "打包", "上线",
        "docker build", "kubernetes", "github actions",
        "npm publish", "release", "推前清理", "巡检",
        "后台任务", "初始化项目", "项目初始化",
    ),
    TAG_PRODUCT: (
        "pm mode", "pm-mode", "需求", "用户访谈", "苏格拉底",
        "product brief", "用户故事", "产品发现", "市场",
        "竞品", "商业计划书", "国赛", "策划",
        "需求挖掘", "拟人化", "lt 软件",
        "产品方案", "课题申报", "申报书", "申报通知",
        "微信群", "机器人改造", "微信机器人", "企业微信",
        "全面接管", "仿真用户操作", "clawbot", "openclaw",
        "桌面级数据", "提示词管理工具", "深度调研",
        "提示词质量", "提示词工具", "桌面级数据工作站",
        "产品角度", "用户视角下", "用户体验",
        "改动有用", "客户", "建库", "提示词", "ph 改动",
        "业务场景", "扫码", "微信开发", "微信机器人开发",
        "聚焦微信", "禁用企业微信",
    ),
    TAG_KNOWLEDGE: (
        "复盘", "总结", "踩坑", "教训", "学习笔记", "心得",
        "经验", "回顾", "lesson learned", "post mortem",
        "复盘文档", "纪要", "协作约定",
        "六阶段", "协作流程", "6a 工作流", "六阶段交付",
        "历史教训", "主权红线", "原则", "纪律",
    ),
}


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]+|[一-鿿]+")


def rule_classify(text: str) -> str | None:
    """关键词规则识别。返回最匹配的标签或 None。

    每个标签按关键词命中次数计分；只有最高分 ≥ 2 才返回，避免单关键词误判。
    """
    if not text:
        return None
    low = text.lower()
    scores: dict[str, int] = {}
    for tag, words in _KEYWORDS.items():
        hits = 0
        for w in words:
            if w in low:
                hits += 1
        if hits > 0:
            scores[tag] = hits
    if not scores:
        return None
    best, n = max(scores.items(), key=lambda x: x[1])
    return best if n >= 2 else None


LLM_CLASSIFY_PROMPT = """\
你是提示词分类专家。判断下面这条提示词的"动作类型"，从 14 个候选中**必须选一个最贴近的**。

## 候选标签（精确名称 + 语义范围）

- 设计优化：UI / 视觉 / 组件 / 交互 / 新手引导 / spotlight / tooltip / 样式 / 排版 / 前端体验
- 环境检查:依赖管理 / 状态污染 / 配置 / 端口 / .env / 服务运行检查 / 前置环境
- 代码审查：PR review / 代码评审 / 风格审 / 把关
- 调试排错：bug 修复 / 报错排查 / 异常诊断 / "为什么不工作" / stack trace 分析
- 测试覆盖：单测 / 集成测试 / e2e / 回归 / pytest / vitest / 测试用例编写
- 文档撰写：README / API doc / 使用说明 / docstring / 注释 / 技术文档
- 数据处理：SQL / 数据库 schema / 数据迁移 / 数据分析 / ETL / json 解析 / 字段填写 / 表格生成 / 结构化数据
- 重构改造：架构调整 / 抽象提取 / 模块拆分 / 解耦 / refactor / 代码结构整理
- 性能优化：速度 / 内存 / DB 查询 / 卡顿 / 瓶颈 / 缓存 / N+1
- 安全检查：鉴权 / 权限 / 注入 / xss / csrf / 漏洞 / 敏感数据 / api key 管理
- 部署发布：CI/CD / Docker / 打包 / 上线 / GitHub Actions / release
- 产品发现：PM-Mode / 需求挖掘 / 用户访谈 / brief / 竞品分析 / 用户故事 / 产品定位
- 知识总结：复盘 / 教训 / 学习笔记 / 心得 / 经验沉淀 / post-mortem
- 其他通用：**仅当真的不沾边时用**（如角色扮演、娱乐 prompt、不知所云的）

## 严格规则

1. **必须从 14 个标签中选一个**——只输出标签的精确中文名（如「设计优化」）
2. **避免懒选「其他通用」**——大多数 prompt 都能归到 13 个具体标签之一
3. 不要输出 markdown、解释、引号、标点；只输出 4 字标签名
"""


def build_llm_prompt() -> str:
    return LLM_CLASSIFY_PROMPT


def llm_classify(cfg, body: str, *, mode: str = "auto") -> str:
    """调 LLM 分类。失败 / 输出不在 14 标签中 → 返回 "其他通用"。"""
    try:
        from . import optimizer
        result = optimizer._run(
            cfg, body[:1500],
            system_prompt=build_llm_prompt(),
            mode=mode,
            wrap_user_in_xml=True,
        )
        if not result.success:
            return TAG_OTHER
        raw = (result.optimized or "").strip().splitlines()[0].strip()
        raw = raw.strip("「」\"'`*【】 ")
        if raw in ALL_TAGS_SET:
            return raw
        for tag in ALL_TAGS:
            if raw and (raw in tag or tag.startswith(raw)):
                return tag
        return TAG_OTHER
    except Exception:
        return TAG_OTHER


def classify(cfg, body: str, *, use_llm: bool = True) -> str:
    """主入口：规则优先 + LLM fallback。"""
    rule_hit = rule_classify(body)
    if rule_hit:
        return rule_hit
    if not use_llm:
        return TAG_OTHER
    return llm_classify(cfg, body)


def iter_all_tags() -> Iterable[str]:
    return iter(ALL_TAGS)
