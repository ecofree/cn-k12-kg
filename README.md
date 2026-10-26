# cn-k12-kg

面向中国小学至高中数学的教材导航、讲解、练习与学习记录工具。项目可以本地运行；课程资料、评分契约和发布过程均有明确来源。

当前 Quality V4 按八项质量要求补齐基础教学、目标帮助、问题追踪、档案恢复和版本验收。资源包括 **23册、477个教材导航点、477张完整讲解卡、14个学习单元和21项实践活动**。全部954项主题目标具有自检材料；14个单元包含231道阶段任务、104项目标支持（含74个可观察目标与30个保留的复合目标）。当前登记共1,124项目标，角色和完成要求见 [课程范围](docs/quality-v4-scope.md)，实施证据见 [V4台账](docs/quality-v4-implementation.md)。

教师独立审核与真实学生教学效果验证按用户要求暂缓。内容保留真实的AI作者初稿状态；程序验收和合成浏览器流程不能替代这两类证据。

## 先运行

Python 3.10+ 可以运行本地Web。安装固定版本的构建依赖后重建内容库：

```bash
python -m pip install -r requirements-dev.txt
python tools/build_project.py --write
python web/server.py --port 8765
```

打开 <http://127.0.0.1:8765>。HTTP服务使用Python标准库，完整档案备份使用固定版本的 `cryptography`；公式字体和图片随源码提供。

源码ZIP可按上述命令构建和运行，也可执行 `python tools/build_project.py --check` 等不依赖Git历史的检查。完整固定版本验收会读取语义审查的真实Git基线；使用源码ZIP的维护者须先从 `current-branch.bundle` 克隆包含该基线历史的仓库，再执行下列验收。克隆方法见 [交付包使用说明](docs/release-workflow.md#使用交付包)。

在保留真实基线历史的Git仓库中，完整的内容、回归和浏览器验收还需要Node.js 20+：

```bash
npm ci
npx playwright install --with-deps chromium
python tools/release_check.py --with-browser
```

报告保存在 `build/regression_report.json`、`build/regression_report.md` 和 `build/browser-check/`。Linux上安装中文字体可使截图更清晰，例如 `fonts-noto-cjk`。浏览器测试使用独立临时学习库和合成作答，不接触实际学习记录。只运行核心内容与Python回归时，使用 `python tools/release_check.py`。

## 学习者可以做什么

- 选择小学、初中或高中，查看适合当前学段的单元、实践和教材目录；下次打开保留选择。
- 沿“分数的意义与等值 → 比与比例 → 一次函数”学习，也可以按先修关系回到公因数、方程等基础。低年级可选十框、十与一和凑十起始单元。
- 沿“读图与平均数 → 抽样与波动 → 相关与回归”学习统计，比较数据、辨析不恰当的结论，明确总体、样本和模型的适用范围。
- 阅读解释、适用条件、反例和图示，完成逐目标自检。自检文字自动保存，参考答案展开会留下曝光记录。
- 完成练习、迁移任务和后续复测。练习即时反馈，迁移与复测在整组完成后显示解析；实际间隔不足时明确提示。
- 保存过程题的推理、实践作品、反思和自评。历史修订保留，重新打开能继续；学生自评不会改写自动判分。
- 查看继续学习、待巩固和到期复习建议。未呈现题不足时转入可执行的复习练习，不生成空检查。
- 在“待解决事项”中查看跨会话和学段的旧困难，记录理由后暂缓或重开；从具体目标进入辨别、分层提示、指导和后续确认。
- 在十格、分数条、统计图和函数表图中操作并显式保存解释；操作记录不当作自动得分。
- 设置恢复码、下载加密完整备份；清理浏览器后找回原档案，重装后导入备份恢复。操作步骤见 [档案恢复](docs/profile-recovery.md)。
- 恢复旧会话，导出会话、作品及版本历史。内容更新后，已保存的题面、评分契约和SVG快照仍可解释旧记录。
- 刷新和前进／后退恢复具体会话。保存失败或发生修订冲突时，页面保留草稿与处理入口；选择题可使用键盘操作，保存和错误状态可以被辅助技术识别。

直接入口示例：

```text
/?unit=ten_frame_place_value
/?unit=fraction_equivalence
/?unit=data_reading_and_mean
/?unit=sampling_and_variation
/?unit=correlation_and_regression
/?teach=m7a.5.2
/?project=junior_track_design
/?target=m7a.5.2
/?volume=七上
/?scope=1
/?needs=1
/?profile=1
```

## 内容范围与计数口径

| 学段 | 册次 | 章／单元 | 教材导航点 |
| --- | ---: | ---: | ---: |
| 小学 | 12 | 77 | 203 |
| 初中 | 6 | 34 | 127 |
| 高中 | 5 | 18 | 147 |
| 合计 | 23 | 129 | 477 |

教材导航保留真实目录粒度与版本字段。小学、初中教材登记为2024版，高中教材登记为2019版；课标条款与2025修订对照单独存储。单元级教材位置不能直接代表一个可独立诊断的微技能。

| 资源 | 当前范围 | 解释 |
| --- | ---: | --- |
| 完整讲解 | 477张 | 342张核心、135张基础支持；另有50张辅助速查 |
| 主题目标自检 | 954项 | 684项核心、270项基础；逐目标解释、任务、量规及修订入口 |
| 深入学习单元 | 14个、231道阶段任务 | 104项目标支持，含208个辨别及指导微任务；56个例题 |
| 综合实践 | 21项、84个步骤任务 | 步骤记录、作品说明、反思、自评与修订历史 |
| 自有题完整档案 | 3,568条 | 包括489条退役记录，保留原ID和历史追溯 |
| 活跃题库 | 3,079道 | 默认Graph和SQLite题表；其中1,967道保留已登记的暂定诊断用途 |
| 稳定技能登记 | 1,124项 | 74项可观察、96项复合、954项旧版复合目标，粒度显式区分 |
| 统一任务目录 | 4,837项 | 题库、自检、单元题和实践步骤统一登记；489项退役 |
| 课标对照 | 78项摘录 | 保留旧版定位及2025修订的逐条AI核对，不代表整部课标逐字认证 |

这些分母来自当前登记资源，不作为整部K12课程所有细技能的总数。只换数字或情境表述不会成为新的独立题族。没有经过核对的旧题族保持未分类，不自动声称独立迁移覆盖。

V3的13道旧题修订了题面条件与解释，并同步11条派生核验字段；原ID、答案与练习／诊断用途保持不变。修订引起的自动证据分级变化单独核对，不自动升级诊断资格；审计记录位于 `data/migrations/quality-v3-corrections.json`。新统计目标中的11项通过14条明确关系细化旧课程目标，另有13条具体先修关系；没有准确旧目标对应的4项保持未映射，不靠同主题自动关联。具体内容与反例见 [统计内容说明](docs/quality-v3-content.md)。

随时重算当前统计：

```bash
python tools/status_report.py --json
python tools/quality_scope.py --check --require-complete
python tools/coverage_report.py --check --require-complete
python tools/check_learning_units.py --json
```

## 可靠判分与证据边界

自有题用途由 `data/task_admissions.json` 绑定内容版本。解析变长或措辞变化不会自动扩大诊断准入；原有策略迁移仅冻结暂定用途，不等于新增学科审核。

所有正式任务使用编译后的明确答案契约。精确数值使用有理数判等；近似答案显式声明容差或舍入规则。坐标和受限多项式允许合法等价表达，要求展开式或整数系数因式分解时单独约束形式。选择题使用稳定选项ID；Web、CLI与学习单元使用同一判分实现。

证明、探究和建模过程保留量规与参考过程，不用一个数值结果替代完整论证。两次答对、图谱先修推断和作者设计难度也不等于真实学生的永久掌握或实测难度。推断、直接作答、材料曝光与学生自查分别记录。

题源检索未命中只表示检索观察，不能推出“不考”。外部题的互证需要核对数学语义、答案与来源谱系；冲突记录隔离。可选外部数据和竞赛候选不进入默认学习路径。

## 内容作者的唯一编辑入口

| 要修改的资源 | 编辑位置 |
| --- | --- |
| 教材点、先修边和册内题 | `data/content_sources.json` 登记的23册 `data/content/` |
| 应用题 | `data/question_sources/application.json` |
| 讲解、例题与目标自检 | `data/teach/` |
| 学习单元与分阶段任务 | `data/learning_units/` |
| 实践活动、步骤与量规 | `data/projects/` |
| 数学图示 | `web/static/diagrams/` |
| 目标范围与题目用途 | `data/curriculum_scope.json`、`data/task_admissions.json`；变更须有明确审查 |
| 技能身份、教材映射和旧任务契约 | `data/skills/` |

修改作者文件后执行：

```bash
python tools/build_project.py --write
python tools/build_project.py --check
```

源文件、稳定身份登记、编译目录和内容库按照同一顺序构建。完整开发验收可运行 `python tools/build_project.py --release --with-browser`。最终打包要求先提交，再在干净提交上验收；详细顺序见 [构建与发布](docs/release-workflow.md)。

`questions.json`、`catalog.json`和内容数据库是派生物，不应单独手改。旧任务的答案契约归属绑定表，新任务的契约归属作者文件；编译器拒绝重复或缺失的契约。30个历史写入CLI已封存，其中25个冗余实现已缩为拒绝写入的兼容入口，旧应用题模块只提供正式JSON的只读兼容视图。

作者字段与迁移说明见 [作者工作流](docs/authoring-workflow.md)、[目录契约](docs/catalog-authoring.md)。旧ID、有效材料、历史题目和原始学习记录保留。

## 代码与数据分工

```text
data/content/              正式册次作者源
data/question_sources/     应用题与冻结退役来源
data/teach/                完整讲解与速查参考
data/learning_units/       14个单元及任务
data/projects/             21项实践活动
data/skills/               稳定技能、教材映射与任务绑定
data/catalog.json          编译后的统一资源目录
cnkg/answers.py            精确、近似与结构化判分
cnkg/catalog.py            资源编译、版本与统一查询
cnkg/learning_store.py     所有者隔离、会话、作品版本与曝光
cnkg/planner.py            透明的继续学习与复习规则
cnkg/learning_needs.py     全历史未解决事项与具体后续证据
cnkg/profile_recovery.py   加密备份与档案恢复
cnkg/instruction.py        目标辨别、分层提示与指导确认
cnkg/quality_scope.py      完整目标范围与材料对应
cnkg/change_review.py      语义差异、影响清单与审查绑定
cnkg/review_timing.py      同范围最近活动、实际间隔与复习资格
cnkg/session_content.py    会话使用的不可变内容与SVG快照
web/                       本地服务和界面
tools/build_project.py     正式构建入口
tools/release_check.py     内容、核心与可选浏览器验收
tools/package_release.py   与提交及验收证据绑定的发行包
tests/                     核心回归与浏览器学习流程
```

内容库 `build/kg.sqlite` 可以删除重建。学习记录默认位于 `var/learning.sqlite`，也可由 `CNKG_LEARNING_DB` 指定；它们不参与内容构建或发布打包。

CLI示例：

```bash
python tools/query.py stats
python tools/query.py ancestors m7a.5.2
python tools/query.py path m7a.5.2
python tools/query.py chapter 七上
python tools/query.py diagnose m7a.5.2 --answers w,w,c
```

## 维护与交接

本地使用已具备讲解、目标支持、待解决事项、草稿、作品、自评、复习、历史及可恢复备份的连续流程。跨浏览器凭恢复码找回与跨环境备份导入已实现；学校账号、云同步、多租户及公网部署属于后续产品范围。既有综合目标仍需在未来具体内容修订中逐项细化；不能因注册了稳定ID就声称所有目标都已原子化。

- [当前交接](docs/handoff.md)：运行、学习数据与交付方式。
- [V4实施台账](docs/quality-v4-implementation.md)：本轮三批与八项要求的实现依据。
- [语义审查](docs/semantic-change-review.md)：内容变更、影响清单和实际预览。
- [V3实施台账](docs/quality-v3-implementation.md)：上一轮改进及历史验收范围。
- [学习流程](docs/learning-workflow.md)：曝光、版本、计划与作品语义。
- [维护范围](docs/roadmap.md)：当前分母、暂缓事项与持续维护规则。
- [历史设计决策](docs/decisions.md)：包括选项显示转换事故与修复依据。

本仓库未使用 `cn-math-taxonomy` 数据，既往借鉴范围和取舍保存在设计决策中。历史完成报告保留当时范围与数量；当前状态以本页、实施台账和可重算统计为准。

## License

代码使用 [MIT](LICENSE)，原创教育资料使用 [CC BY 4.0](LICENSE-DATA-CC-BY-4.0.txt)，署名“cn-k12-kg”。第三方软件、字体、数据和课标摘录遵循各自来源说明，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及 `data/publication_assets.json`。未确认分发许可的可选外部题库不随源码包提供。
