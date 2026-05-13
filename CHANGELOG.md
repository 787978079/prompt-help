# Changelog

所有重要变更按 Phase 时间线整理。格式参考 [Keep a Changelog](https://keepachangelog.com/)。

## v1.0.0 (2026-05-11) - 首个稳定版

完整产品形态，分发就绪。**128 单元测试全过**，跨 19 个 Phase 持续打磨。

### Phase 18-19：成熟化 + 性能
- 重新设计 logo（黑底圆角 + Ph 字 + 金色高光点）+ 多尺寸 .ico
- About 对话框 + .exe Windows 元数据 + Inno Setup 安装包完善
- 修 `find_optimized_pair` 每选行扫 100+ 文件的卡顿（用 SQL 列代替读盘）
- 首页 4 个 refresh 异步分批避免一次性阻塞主线程
- ProjectRecallCard fingerprint 5 分钟内存缓存

### Phase 17：刷新本机项目
- 「⟳ 刷新本机项目」一键同步所有项目 CLAUDE.md 等到库
- 增量对比：新增 / 更新 / 跳过相同
- `scan_roots.json` 持久化扫描根目录配置
- 设置页加根目录管理 section

### Phase 16：死 UI 修复
- 上手 5 步「展开」按钮卡死修复
- ProjectRecallCard 加 GUI 登记项目入口
- PM-Mode LLM 线程崩溃时按钮锁死 → 加 QTimer 看门狗 60s/90s 自动恢复
- GeneralizeDialog / ImportExternalDialog 失败时按钮死锁修复

### Phase 15：协作 + 完整 dashboard
- 团队 channel 订阅：朋友的 git 仓库自动 pull 到「待审」
- 完整统计 tab：4 大数字 + 分类条形图 + Top 20 + 未使用清单
- plugin SessionStart 召回时通用模板优先 + 🎯 标记

### Phase 14：团队离线协作
- 增量包导出（manifest schema v2 + sync_id + body_hash）
- 智能合并三策略：保留本地 / 覆盖 / 双留为副本
- 冲突解决 UI（diff 预览 + 全局策略下拉）
- PM-Mode 苏格拉底质量打磨（4 few-shot 示例 + 错误友好分类）
- A/B 同源对比 + 一键归并
- 跨设备 git 同步状态栏 + 一键 pull+push

### Phase 13：闭环补完
- 自查 4 个真问题修：SQL LIKE 转义 / 右键菜单清多选 / InboxView 分享 / Path.cwd() fallback
- 复制时自动展开 `[[ref]]` 引用
- import_zip GUI 入口统一到 share 库函数

### Phase 12：跨项目召回 + Prompt 互引
- 首页「当前项目相关」卡片（cwd 检测 + 指纹匹配 + last_active_project fallback）
- `[[标题]]` 互引 + 详情区显示「引用了 / 被引用」
- 首页底部统计 banner（5 格信息密度高）
- 多选批量分享 ZIP（GUI 入口）
- 翻译缓存统计 GUI 入口 / PM 对话导出 markdown / 右键复制副本

### Phase 11：止血 + 高频功能
- 8 处止血 BUG 修复（checklist 回调 / GeneralizeDialog 无改动卡死 / PM finish 按钮 / 推荐库批量预热缓存 / thread cancellation / 字段继承 / 替换确认 / HTML parser）
- Variables 占位符执行（复制时弹小表单填值）
- 「这次有用」/「不够好」一键反馈影响 success_signal 排序
- Inline 召回 `/prompt-recall` slash command + CLI --inline 模式

### Phase 10：UI 风格回归 + 矢量图标
- 回归 ElevenLabs 黑白极简风格
- 整合 qtawesome（4 万矢量图标库）替换 emoji
- 全页面 emoji 按钮 → 矢量图标

### Phase 9：表格列重设计 + 推荐库
- 表格列：名称 / 描述 / 类型 / 参考来源 / 用过
- description / source_ref 字段（支持 LLM 生成描述）
- 推荐库默认中文显示（缓存预热 + 批量翻译）
- 从网页/文件导入：LLM 自动识别其中的 prompt

### Phase 8：通用模板为核心
- 「我的库」3 tab 重构：🎯 通用模板 / 📦 原始材料 / 📥 待审
- GeneralizeDialog：左右 diff + 三选项（保留 / 替换 / 双留）
- inbox「保留并通用化」一键产出模板
- 首页突出通用模板数

### Phase 7：产品完整性
- Wizard 路径自动探测 + 失败诊断
- 首页「上手 5 步」OnboardingChecklist
- PM-Mode 彻底重写为 LLM 主导对话（动态问题生成 + 三维度评分 + 4 件套产出）
- 推荐库 4 个源 + Output Language 模式
- 推荐库导入网页/文件

### Phase 1-6：基础（vault / CLI / GUI / wizard / spotlight / 数据干净化 / 多工具适配）

---

## 单元测试演进
- v0.1：86 测试
- v1.0：128 测试（+42）覆盖 placeholders / references / pm_dialog / translation_cache / public_library 等核心模块

## 软件指标
- 源码 ~12000 行 Python
- PySide6 桌面 GUI
- .exe 打包后 67MB（含 PySide6 + qtawesome 字体 + 数据资源）
- 支持 Win11 / Win10
- Python 3.11+

---

## 协议

MIT License · © 2026 linguofeng
