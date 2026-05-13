"""Phase 7 引导步骤定义。

全局 tour（首次启动自动跑）：7 步，覆盖 PH 的核心导航与关键操作。
每个 step 的 target_object_name 必须与对应 widget 的 setObjectName 对齐。
"""

from __future__ import annotations

from typing import Callable

from .spotlight_tour import TourStep


def build_global_tour(navigate_to: Callable[[str], None]) -> list[TourStep]:
    """生成全局 tour 步骤列表。

    navigate_to(target)：在 step 进入时跳到对应主页，确保目标 widget 可见。
    """
    return [
        TourStep(
            id="welcome",
            title="欢迎使用 Prompt Help",
            desc=(
                "1 分钟带你看一遍——它能保存你写过的好提示词、"
                "踩过的坑、想清楚再开做的产品。\n\n"
                "全程可以点「跳过」，之后在「帮助」里能重新打开。"
            ),
            placement="center",
            on_step_enter=lambda: navigate_to("home"),
        ),
        TourStep(
            id="sidebar_home",
            title="首页",
            desc="任何时候点这里回到主页，看「上手 5 步」清单 + 库的统计 + 最近热门提示词。",
            target_object_name="tour_nav_home",
            placement="right",
            on_step_enter=lambda: navigate_to("home"),
        ),
        TourStep(
            id="sidebar_library",
            title="我的库",
            desc=(
                "你自己的提示词都存这里：通用 / 项目专属 / 踩坑提醒 / 待审 四个 tab。"
                "顶部可按分类筛选、按热门/使用次数排序。"
            ),
            target_object_name="tour_nav_library",
            placement="right",
            on_step_enter=lambda: navigate_to("library"),
        ),
        TourStep(
            id="library_import",
            title="导入提示词",
            desc=(
                "「从 Claude 历史导入」自动挖你过去的会话；"
                "「从文件导入」支持 .md/.json/.jsonl/.txt/.zip，"
                "点 ? 看每种格式的具体要求。"
            ),
            target_object_name="tour_library_import_bar",
            placement="bottom",
            on_step_enter=lambda: navigate_to("library"),
        ),
        TourStep(
            id="sidebar_public",
            title="推荐库",
            desc=(
                "外部公开提示词源（awesome-claude-prompts、cursorrules、中文版 ChatGPT 等）。"
                "勾选批量加进我的库，每张卡片有「🇨🇳 复制中文版」按钮按需翻译。"
            ),
            target_object_name="tour_nav_public",
            placement="right",
            on_step_enter=lambda: navigate_to("public"),
        ),
        TourStep(
            id="sidebar_pm",
            title="产品发现",
            desc=(
                "想做新产品时来这里。LLM 用苏格拉底反问帮你想清楚要建什么、"
                "为什么建、技术死穴在哪——三维度都到 7/10 自动生成 brief + user stories + risks + decisions 四件套。"
            ),
            target_object_name="tour_nav_pm",
            placement="right",
            on_step_enter=lambda: navigate_to("pm"),
        ),
        TourStep(
            id="settings_gear",
            title="设置 & 帮助",
            desc=(
                "右上角齿轮进设置（填 API key、调挖掘频率），"
                "侧栏「❓ 帮助」看完整文档。\n\n"
                "本引导随时可在帮助页重启。"
            ),
            target_object_name="tour_top_settings",
            placement="bottom",
            on_step_enter=lambda: navigate_to("home"),
        ),
    ]
