# 稳定技能与统一任务目录

`data/catalog.json` 是正式派生文件。教材节点继续用于导航，稳定技能 ID 用于关联可观察目标；旧教材 ID、题目 ID 和历史记录均保留。

## 作者来源与登记

| 来源 | 维护内容 |
|---|---|
| `data/skills/registry.json` | 固定技能 ID、教材位置到技能的映射、目标 ID 到技能的映射 |
| `data/skills/catalog-bindings.json` | 旧任务的明确答案契约、题族、变体，以及自检和项目步骤的固定 ID |
| `data/teach/` | 讲解、例题与目标自检参考 |
| `data/learning_units/` | 学习目标、先修单元和目标、例题及分阶段任务 |
| `data/projects/` | 实践步骤、作品要求与过程量规 |
| `data/questions.json` | 当前正式题目构建结果；题目本体仍从既有作者源重建 |

技能 ID 在首次登记时分配并提交，以后不根据标题、年级、教材位置或排序重新生成。修改目标顺序时必须同步核对 placement；编译器会拒绝旧下标悄悄指向新目标。若目标含义改变，优先新增细目标，并通过 `refines_objective_ids` 指回原目标，保留历史语义。

所有旧 `mastery_criteria` 登记为 `legacy_composite`。这表示暂未完成细化，不能把 954 个旧目标计为 954 项已穷尽的原子技能。新单元区分 `composite` 与 `observable`，覆盖报告按粒度分别列出。

新增题目或单元任务时明确填写 `answer_spec`、`family_id`、`variant_id`。答案契约见 `schema/answer-spec.schema.json`。精确数值保留有理数相等；近似值必须明确舍入或误差，表达形式要求单独声明。复杂过程任务使用 `type: rubric`，不由关键词或答案相似度自动判分。

每条绑定通过 `answer_spec_source` 指定唯一契约来源。`binding` 表示契约只在绑定中保存；`author` 表示只在任务作者 JSON 中保存，绑定不复制答案契约。双份契约或指定来源缺失都会阻止编译。新题和新学习任务采用 `author`，旧任务保留明确迁移后的 `binding`；Graph、CLI、Web 和数据库均使用编译契约。

新增例题同样填写 `family_id` 与 `variant_id`。只有换数或换表面情境、数学结构不变的任务保留同一族，不能用新 ID 把练过的套路包装为独立迁移题。

## 登记、编译与检查

在已安装 `requirements-dev.txt` 的项目虚拟环境中运行：

```sh
python tools/build_project.py --release
```

单独处理登记与目录时可以运行：

```sh
python tools/register_catalog.py --check
python tools/register_catalog.py --write
python tools/compile_catalog.py --write
python tools/compile_catalog.py --check
```

登记命令只为新来源分配身份，不改写既有技能、删除旧映射或猜测新题评分方式。修改作者源后，先查看登记与编译差异，再提交作者源、注册表和派生目录。`--check` 不修改文件；缺少登记、悬空引用、循环先修关系、错误答案契约及过期编译结果都会失败。

`build/catalog-report.json` 记录覆盖数量、旧目标与细目标的区别、隔离题、缺目标映射的题目和政策迁移差异。该报告是结构与内容来源检查，不是教学效果结论。

## 对齐与准入

没有 `tested_criterion_indices` 的旧题仅保留 `alignment: topic_only`，其 `skill_ids` 为空。不能因为题目挂在某个教材节下，就自动认为它考查这一节的所有目标。

明确绑定旧目标下标的题目及自检使用 `explicit_component`。这些旧目标通常包含多个行为，一道题只提供其中一部分证据。单元和项目步骤按作者的 `objective_ids` 映射为 `explicit`，仍不自动得出完整掌握结论。

被旧题质量政策隔离的题目保留 ID 与出处，但 `availability: quarantined`、`purpose: []`，不可进入练习或检查。编译器比较加入明确答案契约前后的准入政策，不会自动补签独立审核。元数据迁移若扩大准入会停止编译；降低准入则明确列入报告。

## 材料曝光与题族

`material.family_ids` 只列材料中已经提供的例题或自检参考所属的题族。`assessment_family_ids` 单独列关联评价任务的族；这些任务不能仅因同属一个单元就被视为已经展示答案。

已明确绑定的实际重复包括圆半径为 3、面积为 28.26 的例题与题库题，以及正方体棱长为 3、表面积为 54 的自检参考与题库题。同结构换数题保留相同族。

未完成数学结构分族的旧题使用 `family_status: unclassified`。不同占位 ID 不证明任务独立；所有编译记录保持 `independent_evidence: false`，族记录保持 `independence_established: false`。后续可在不更改任务 ID 的情况下逐步补齐真实族关系。

## 运行接口与版本

`load_catalog()` 返回 `Catalog`，其 `skills`、`placements`、`tasks`、`materials`、`families`、`units`、`projects` 为按 ID 索引的字典。`task()`、`skill()`、`material()`、`placement()`、`unit()` 返回可安全用于会话快照的副本；`self_check(topic_id, criterion_index)` 与 `project_steps(project_id)` 隐藏新注册 ID 的具体格式。

`tasks` 保留全部历史追溯记录，`active_tasks` 与 `retired_tasks` 分别索引可用和停用任务。默认 Graph 题库与 SQLite `questions` 表排除停用题；停用原记录另存于显式追溯索引和 `retired_questions` 表。作者来源、冻结档案和统一构建命令详见 [作者工作流](authoring-workflow.md)。

`tasks_for_skill(skill_id, purpose)` 默认排除隔离题。`public_task()` 隐藏参考答案、作者验算、解析、答案契约与量规示例；服务端在合法作答或明确展开参考后再提供相应反馈。

每项任务和材料都有 `resource_version`。单元版本只包含本单元、相关技能与任务，以及实际引用的 SVG；修改不相关单元不会使它失效。全目录另外提供 `content_sha256` 和作者源指纹，加载时可以检查编译是否过期。学习记录的源内容快照哈希与目录资源版本承担不同用途，应分别保存。
