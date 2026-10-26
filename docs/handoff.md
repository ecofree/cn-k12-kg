# 项目交接（2026-10-26）

当前版本已完成既定内容与工程任务。独立教师审核、真实学生教学效果验证按用户要求暂缓。
具体数量、变更和验收证据见 [完成记录](completion-2026-10-26.md)；
旧交接文档已归档至 [2026-10-24 历史快照](history/handoff-2026-10-24.md)。

## 获取与运行

在包含本次提交的工作树或解压后的源码目录运行：

```bash
python -m pip install -r requirements-dev.txt
python tools/release_check.py
python web/server.py --port 8765
```

打开 `http://127.0.0.1:8765`。无需安装Web框架；Web运行依赖Python标准库，构建与验收使用固定版本的JSON Schema校验依赖。
验收报告位于 `build/regression_report.md` 和 `build/regression_report.json`，各项完整日志在 `build/release-check/`。
外部题库未提供时，相关集成用例明确跳过；它不阻止自有内容运行。

## 内容编辑入口

1. 修改 `data/content_sources.json` 登记的所属册次源，或 `tools/app_items/` 的应用题生成器。
2. 题目与教学卡共用的补全生成器位于 `tools/complete_primary.py`、`complete_junior.py`、`complete_senior_required.py`、`complete_senior_elective.py`。
3. 完整卡保存在 `data/teach/`，684个核心目标的逐项材料在 `objective_coverage`；不要只换标题。
4. 实践材料保存在 `data/projects/`，可独立修改；目录关联必须引用真实的原目录活动。
5. 修改题源后运行 `python tools/rebuild_data.py --write`；生成产物会按稳定ID保留正确答案，按实际显示位置平衡选项。
6. 重新生成受影响的速查卡，再运行 `python tools/release_check.py`。不要直接改 `questions.json` 以绕开来源一致性检查。

## 课标与审核

旧课标摘录与ID保留在 `standards.json`；2025修订版比较记录在 `data/curriculum_comparisons/`。
更新记录后运行 `python tools/curriculum_compare.py --write` 同步登记；该工具不会将AI比较升级为独立审核。
任何题文、选项或量规内容变动都会影响内容指纹，旧审核不得自动重签。

## 学习数据

匿名学习记录保存在 `var/learning.sqlite`，可用 `CNKG_LEARNING_DB` 指定其他路径。
发布内容库为 `build/kg.sqlite`，可重建；不得用重建内容库覆盖学习记录。
会话按匿名浏览器所有者隔离，重复提交幂等，续做和导出保留题目与评分版本快照。
逐目标开放自检的文本框仅用于当前页面记录，页面已提示离开前复制；正式练习与检查走持久化会话。

## 交付边界

当前分支和源码包的实现可以独立运行；是否已经同步到远程分支以实际Git提交与远程引用为准。
不使用来源不明的外部正确率，也不把演示会话当成真实学生验证。
