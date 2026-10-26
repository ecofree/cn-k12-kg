# 项目交接（Quality V3，2026-10-26，北京时间）

本轮继续完成学习记录可靠性、统计课程深化、键盘操作与交付验收三批实施。当前具体数量见 [README](../README.md)，变更依据见 [V3实施台账](quality-v3-implementation.md)，此前范围见 [V2台账](quality-v2-implementation.md)。独立教师审核与真实学生教学效果验证按用户要求暂缓。

## 获取与运行

使用包含本轮提交的工作树、Git bundle或源码ZIP。在项目目录执行：

```bash
python -m pip install -r requirements-dev.txt
python tools/build_project.py --write
python web/server.py --port 8765
```

打开 <http://127.0.0.1:8765>。Web运行只需要Python标准库；构建与结构验收使用固定版本的JSON Schema依赖。源码ZIP不包含实际学习记录、运行数据库、node_modules或未登记外部题库。

可重建内容使用 `python tools/build_project.py --check` 检查。浏览器验收及发行包顺序见 [release-workflow.md](release-workflow.md)。内容与Python测试报告在 `build/regression_report.json`，浏览器报告在 `build/browser-check/report.json`；每份发行报告都绑定提交和发布文件清单。

## 编辑与构建

题源是23册 `data/content/` 和 `data/question_sources/application.json`；讲解、单元、实践分别直接编辑 `data/teach/`、`data/learning_units/`、`data/projects/`。稳定技能及旧任务契约位于 `data/skills/`。正式来源由 `data/content_sources.json` 登记。

编辑后执行 `python tools/build_project.py --write`。不要直接修派生的 `questions.json` 或 `catalog.json`，也不要再运行已封存的旧补全写入器。作者入口、契约归属、退役档案与兼容规则见 [authoring-workflow.md](authoring-workflow.md)。

教材位置、技能和任务保留各自稳定ID。当前74项细目标明确标记为可观察，旧复合目标仍如实保留复合身份。统计新目标中11项已明确细化旧课程目标，4项没有准确旧目标对应而保留未映射。题族、表征和认知要求分开登记；未分类旧题不会自动获得独立任务身份。

## 学习数据

匿名学习记录默认保存在 `var/learning.sqlite`，可用 `CNKG_LEARNING_DB` 指定其他位置。发布内容库是 `build/kg.sqlite`，两者独立；构建程序拒绝覆盖学习数据库。

逐目标自检、过程草稿、实践步骤、反思和自评已经持久化。保存有版本与冲突处理，刷新或重新进入可继续；学习记录导出包含完整作品修订。学生自评始终标注为学生报告，不自动改写判分或产生独立掌握结论。

学习库v3使用事务更新派生索引，原始会话、作品、历史修订和事件JSON保持不变。自查按全部量规项目统计；未检查项或只写备注会继续提示。导出schema 3包含已呈现题目的冻结题面、选项和图示，维持未呈现题及未公开反馈的隔离。该导出用于阅读和核查，不是可直接导入的数据库备份。

浏览器地址使用具体会话ID，刷新不再次创建检查。切换页面时先处理未保存内容；失败或冲突保留可见草稿和恢复入口。输入框须在成功恢复原记录后才启用。

旧会话保留题面、评分契约和图形快照。无关材料更新不会中断原会话；评分程序不兼容时会明确保留旧观察并说明续做限制。不要用新答案批量重判旧记录。

## 课标与审核

旧条款摘录与ID在 `standards.json`，2025修订比较记录在 `data/curriculum_comparisons/`。修订需保留来源、定位和内容指纹；AI核对和程序通过不自动升级为教师审核。

材料被提供、参考被展开、题目被呈现和实际作答分别记录。复测按同范围最近完成及相关活动计算实际间隔，较早结果页不能绕过近期活动。立即重复复测不推进复习阶梯；1／3／7天只是当前可调整的产品规则，没有被宣称为已验证最优间隔。

## 交付与历史

发行包含 `source.zip`、`current-branch.bundle`、`evidence.zip` 和 `delivery.json`。Git bundle只包含当前分支引用；学习记录和未绑定的旧报告不进入证据包。用 `delivery.json` 中的提交及SHA-256核对文件。

远程是否同步以实际Git引用为准，不能用本地提交或包名代替远端确认。远程凭据不可用时，完整bundle仍可导入已有仓库，或克隆为独立工作树；操作示例见发布文档。

旧交接与旧完成报告作为历史保留，不作为当前待办或当前数量。项目没有声称已获得独立教师认可或真实学生效果验证。
