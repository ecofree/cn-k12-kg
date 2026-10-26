# 作者源、构建与历史追溯

正式编辑入口是登记的 JSON。Python 负责编译、检查和发布，不再保存另一套可覆盖当前资料的题目或讲解正文。维护者可以从作者文件重新生成题库、统一目录和内容数据库。

## 每种内容的维护位置

| 内容 | 作者文件 | 生成结果或读取方式 |
| --- | --- | --- |
| 教材节点、先修关系、册内基础题 | `data/content_sources.json` 登记的 23 册 `data/content/*.json` | `topics.json`、`dependencies.json`、`questions.json` |
| 应用题 | `data/question_sources/application.json` | 与册内题合并后统一转换选项显示顺序 |
| 完整讲解、目标自检和速查参考 | `data/teach/` | Web 材料和目录曝光绑定 |
| 学习单元、例题、分阶段任务、图示引用 | `data/learning_units/`、`web/static/diagrams/` | `data/catalog.json` 中的单元、任务和材料 |
| 实践活动、步骤与作品量规 | `data/projects/` | 项目页面、固定步骤任务和作品记录 |
| 固定技能身份和教材位置映射 | `data/skills/registry.json` | 稳定技能及 placement；不按标题或排序重新编号 |
| 旧任务契约、固定自检/步骤 ID、题族绑定 | `data/skills/catalog-bindings.json` | 编译后的明确任务契约和材料曝光集合 |
| 退役题目档案 | 清单中 `status: retired` 的 `data/question_sources/*.json` | 完整追溯题库与停用索引；不进入活跃题库 |

`data/questions.json`、`data/catalog.json`、`build/kg.sqlite` 和 `data/manifest.json` 都是派生文件。不要只修派生文件；下一次重建会重新读取作者源，而且一致性检查会拒绝来源不符的修改。

## 一条命令完成作者源到发布内容的构建

在项目虚拟环境中安装 `requirements-dev.txt` 后，运行：

```sh
python tools/build_project.py --release
```

该命令依次重建登记题源、为新增资源分配一次性 ID、编译目录，再运行全部发布验收与内容库构建。任何步骤失败都会停止后续步骤，返回非零退出码。构建顺序及结果写入 `build/authoring-build.json`；发布门禁的逐项日志和报告在 `build/release-check/`、`build/regression_report.json` 和 `build/regression_report.md`。

日常编辑也可以分开运行：

```sh
python tools/build_project.py --write  # 重建、登记、编译、内容检查与内容库
python tools/build_project.py --check  # 检查现有来源/产物，不重写作者源或内容库
```

`--write` 适合在提交前检查作者改动；完整交付使用 `--release`。内容库及兼容内容清单生成后，最后生成全部发布文件的原始字节清单，再进入验收。已经安装 Playwright 与 Chromium 时，用 `python tools/build_project.py --release --with-browser` 同时验收十二条浏览器学习与内容预览流程。检查命令仍会生成检查报告，这些报告属于可重建的构建输出。

构建只处理发布内容。匿名学习记录位于 `var/learning.sqlite` 或 `CNKG_LEARNING_DB` 指定位置；它们既不是构建输入，也不是可删除重建的输出。内容库构建会拒绝覆盖含学习会话或事件表的数据库，并在新库构建失败时保留原内容库。

## 新增和修订任务

1. 在所属作者 JSON 中新增任务，明确写出固定 `id`、题目、答案或过程量规、`answer_spec`、`family_id` 和 `variant_id`。题号不能依赖数组位置；换序不会重新编号。
2. 用 `objective_ids` 或经过核对的题目目标绑定声明实际考查的目标。没有证据时保留 `topic_only`，不要把所有教材目标自动挂到每道题上。
3. 数值题明确区分精确答案与近似答案。需要舍入时声明位数；有理数保留精确分数。坐标、简单多项式和因式分解按受限结构判等，指定表达形式单独约束。
4. 核对例题、反例、自检参考和任务是否属于相同数学结构。只换数字、表面情境或阶段名称的任务保留同一题族；已经展示的同族不能作为未曝光证据。
5. 运行构建命令，查看作者文件、注册表和派生目录的差异，一并提交。既有 ID 保留；目标语义变化用新的细目标表达，并保留与原目标的关系。

更多字段规则见 [稳定技能与统一任务目录](catalog-authoring.md) 和 `schema/answer-spec.schema.json`。

### 每个答案契约只有一个所有者

每条绑定通过 `answer_spec_source` 显式指定评分契约的来源：

- `binding`：旧任务的明确契约保存在绑定记录中，作者任务不得另存第二份 `answer_spec`。
- `author`：契约保存在作者任务自身，绑定只保留来源声明，不复制评分内容。

新增题目和学习任务使用 `author`；旧任务可以保留已核对的 `binding` 契约。迁移所有者时一次删除旧位置的重复内容，保持实际契约不变。编译器会拒绝双份或缺失的契约；运行中的 Graph、CLI、Web 和 SQLite 都使用编译结果，不再为正式任务根据题干猜测评分规则。

无目录的临时旧数据夹和可选外部题保留原兼容行为，不会冒用自有题的契约或宣称已完成新协议登记。

## 隔离、退役和稳定历史

当前追溯题库保留 3,568 个旧题 ID，其中 477 道不良记忆题与 12 道已确认错目标题进入冻结退役来源。默认 Graph 和 SQLite `questions` 表只包含 3,079 道活跃题；可诊断题为 1,967 道。是否可练、可诊断仍由题目证据政策分别判断。

12 道错目标题的算术答案本身正确，但与“5 以内加法”“5 以内减法”或“0 的认识和加减法”不符。`data/question_retirements.json` 保存逐题依据、原元数据、修改字段和前后政策；审核类型明确为 AI 作者核对。题干、答案、解析、原教材归属和原 ID 保留，没有把旧题直接改挂到未经验证的目标。

完整 `questions.json` 先对含退役档案的原有集合执行选项显示转换，再在运行时和数据库中排除停用记录。这样退役不会连带改变其他题的选项显示位置。`data/authoring_migration.json` 记录迁移基线、内容指纹和明确的政策修订；作者源格式迁移本身没有改变数学内容。

追溯入口是 `Catalog.retired_tasks`、`Graph.retired_questions_by_id` 和 SQLite `retired_questions` 表。它们保留原始题目、ID、契约或目录版本，以供旧记录解释和事故排查。退役来源有固定内容哈希；想修复并重新发布时，应建立明确的内容修订及准入依据，不能直接更改冻结档案或自动补签独立审核。

## 已封存的旧写入命令

`tools/legacy_writers.json` 列出 30 个已封存的历史写入入口，包括 `scaffold.py`、`apply_*`、`complete_*`、`teach_*` 写入器和 `write_application_items.py`。直接运行这些命令会在读取或写入当前内容之前停止，并显示新的编辑入口。

历史应用题生成代码保存在 `tools/archive/` 的文本档案中。原 `tools/app_items/` 模块仅从正式 JSON 提供只读兼容视图；它们不再分配题号，也不参与正式发布构建。少数旧纯函数留给迁移事故回归，不构成另一个作者来源。旧文档中的写入命令只描述当时的历史，当前工作流以本文为准。

第一批已删除 `data/content/` 根目录中的 15 份重复历史补丁，保留 `data/content/archive/pre_manifest_2026-10-26/` 中的原归档。对应记录是 `data/migrations/quality-v2-cleanup.json`；本批继续保持这一状态。23 册当前作者文件与退役题源完整保留。

编译、数学验算和工程测试的通过说明发布内容可重建、契约有效、已检查的规则成立。教师独立审核与真实学生教学效果验证仍按用户要求暂缓，状态不会因构建成功自动提升。

## V4 语义审查与显式准入

每次改变目标、条件、答案、量规、图示或题族，按 [语义审查流程](semantic-change-review.md) 生成真实差异和影响清单，填写绑定内容的审查记录，再实际预览受影响页面。`build_project.py` 不会自动写一份语义通过记录。范围变化先审查角色及目标分母，再显式执行 `quality_scope.py --write-manifest`；不能为绕过缺材料门禁而删目标。

题库用途登记位于 `data/task_admissions.json`。首次迁移只固定历史暂定用途，禁止覆盖重跑。题目语义变化后，使用 `tools/task_admissions.py --record-id ... --purpose ... --evidence-level ... --reviewer ... --reason ...` 逐项核对并登记；需说明题义、答案、条件与观察边界。工具检查答案契约与必要数值复算，不凭解析长度晋级；AI作者登记仍保留教师待审核。

课程服务加载启动时的一致内容快照；作者修改、注册及编译后须重启服务。正在进行的旧学习记录仍按自身冻结内容处理。
