# cn-k12-kg

中国 K12 数学知识图谱（自建数据）+ 可自动验算的题库 + 先修诊断引擎。

**当前状态：23个册次、477个教材导航节点；342个核心点均有完整讲解，684项已登记核心目标均有专属自检；另有10个细目标教学单元、76道分阶段任务和18项综合实践。既定内容与工程缺口已清零。** 内容保留AI作者初稿状态，教师独立审核与真实学生教学效果验证按用户要求暂缓。交付及验收见 [项目完成记录](docs/completion-2026-10-26.md)。

这个项目**不是** `cn-math-taxonomy` 的复制品，也没有使用它的任何数据。只借鉴了字段结构和"2 题判定 + 先修回溯"的算法思路。原因见 [docs/decisions.md](docs/decisions.md)。

> **当前状态与维护范围**见 [`docs/roadmap.md`](docs/roadmap.md)。题库状态可运行 `tools/status_report.py` 核对；材料覆盖与完成缺口由 `tools/coverage_report.py` 核对。

## 覆盖范围

| 学段 | 册次 | 章/单元 | 知识点 | 已填 | 粒度 |
|---|---|---|---|---|---|
| 小学 | 12（一上—六下） | 74 | 203 | **203** | 课时 / 单元 |
| 初中 | 6（七上—九下） | 34 | 127 | **127** | 小节 |
| 高中（人教 A 版） | 5（必修一—选必三） | 18 | 147 | **147** | 小节 |
| 合计 | 23 | 129 | 477 | **477** | — |

小学 12 册已齐（含 2027 春版的下册），初中九下也已建到节级。

**按册次实际粒度建点，不搞一刀切**：一上、一下、二上、三上、三下有真实课时条目，按课时建（如「1～5 的认识」「十几减 9」）；二下、四上、五上、六上、四下、五下、六下只到单元或节级，就按那一级建，绝不凭想象补课时。活动类条目（小讲堂、数学游戏、☆ 主题实践、曹冲称象的故事、编制学号等）一律不建点。

**靠不住的是“看起来细”的目录**：网上流传的“四下新教材课时”大多是 2012 版内容（四则运算、观察物体二、运算定律），与教育部审定 2024 版的正式单元（除数是两位数的除法、混合运算和运算律、三角形的认识…）完全不同。这类资料一律不采信，宁可停在单元级。

**课程版本不是一套**：小学、初中是 2024 版新课标教材，高中仍是 2019 版（原始映射基线为课标2017版2020修订）。每条知识点都带教材版次字段 `curriculum_version`，校验器会比对所在册次。2025课标修订对照另存 `data/curriculum_comparisons/`，与教材版次分开。

## 它解决什么问题

项目把教材导航、先修关系、讲解和练习连起来，为教师和学习者提供补学线索。部分节点仍合并多个技能，两次作答不能代表整个单元的全部能力。

- 只有符合证据策略的题才能参与诊断；记忆题的跨学段干扰项默认隔离。
- 两次可用证据支持的正确作答形成暂时掌握判断；只沿 hard 先修作推断，推断状态与直接测得的掌握分开。
- 一对一错给出不稳线索和巩固任务；证据不足、题池耗尽和次数限制均显式标明。
- 薄弱回溯只经过必要先修，遇到已掌握节点停止；未核查先修的原因保留为候选。
- 单题筛查和教学练习不输出确定根因；练习成绩与初始诊断分开。

`tools/simulate.py` 用假设学生进行敏感性分析，并报告根因 precision/recall 等指标。
旧版本的模拟百分比保留在 `docs/decisions.md` 中，不作为本版本对真实学生的准确率。
题意正确、条件成立、目标对齐、教师独立审核及真实迁移表现都需要各自的证据，
不能由 verify 算式一致或题量达标替代。

> 交接给他人时，先看 [`docs/handoff.md`](docs/handoff.md)：运行方式、编辑入口、学习数据与验收方法。
> 需要外部补的数据用 [`inbox/README.md`](inbox/README.md) 的模板对接收回。

## 快速开始

```bash
git clone https://github.com/ecofree/cn-k12-kg.git
cd cn-k12-kg

python tools/release_check.py      # 全部必需验收 + SQLite构建 + 哈希清单
# 报告：build/regression_report.md；详细日志：build/release-check/

python tools/simulate.py --students 500      # 用虚拟学生校准判定规则（不依赖外部数据）
```

查询与诊断：

```bash
python tools/query.py stats                  # 总览（按学段/册次看进度）
python tools/query.py ancestors m7a.5.2      # 全部先修（递归 CTE）
python tools/query.py path m7a.5.2           # 从零补到它的学习路径
python tools/query.py chapter 七上            # 按先修顺序列出全册
python tools/query.py diagnose m7a.5.2 --answers w,w,c
python tools/query.py screen 七上 --answers c,w,c,...
```

## 框架先行的工作流

知识点有四个状态，`validate.py` 按状态决定检查力度：

| review_status | 含义 | 校验要求 |
|---|---|---|
| `skeleton` | 只有目录结构 | 只查 id/章节/领域/版本；不要求先修边与题目 |
| `draft` | 内容已填，未经审核 | 要求描述、掌握标准、先修边或 root_reason、至少 2 题 |
| `reviewed` | 教师已审 | 同 draft，另需审核记录 |
| `rejected` | 作废 | 仍会被交叉引用检查拦住 |

```bash
python tools/fetch_toc.py --cache /tmp/kgref   # 1. 抓目录草稿（三学段用不同解析器）
python tools/build_outline.py                  # 2. 整理成 outline.json（目录唯一真源）
python tools/scaffold.py --dry-run             # 3. 看要补哪些骨架点
python tools/scaffold.py                       #    幂等补点
python tools/scaffold.py --sync                #    目录改了章节名时，同步到骨架点
python tools/rebuild_data.py --write           # 4. 按登记源重建全部字段
python tools/release_check.py                  # 5. 全量验收
```

每一册的内容写在 `data/content_sources.json` 显式登记的源文件中（知识点描述、先修边、题目）；应用题由登记的生成器补充，最后统一执行选项显示顺序转换。`rebuild_data.py` 生成三个主数据文件，并检查全部字段。历史补丁只作追溯，未登记的文件不能参与构建。

扩一册的顺序固定为：**目录 → 骨架点 → 描述与掌握标准 → 先修边 → 题目 → 校验**。

## Web 端（本地）

```bash
python web/server.py            # http://127.0.0.1:8765
```

- 首页提供细目标教学单元与教材目录搜索；每个教材点可分别进入讲解、练习与检查。
- 核心讲解包含直观、理由、适用条件、反例与分步例题，以及逐项目标自检、参考过程和检查点；速查参考与完整讲解分别标注。
- 判题与会话状态由服务端维护；题面不下发 `answer`/`verify`/`explanation`。
- 练习即时反馈；检查、独立迁移和复测在整组完成后展示解析。过程题按量规等待复核，不自动判定掌握。
- 结果页区分补学线索、不稳、已有先修证据的起点和证据不足；可回看讲解并换题检查。
- 深链：`/?unit=fraction_equivalence` 打开单元，`/?teach=m7a.5.2` 看讲解，`/?target=m7a.5.2` 开测，`/?volume=七上` 整册抽查，`/?q=导数` 搜索。

## 目录结构

匿名学习记录默认保存在 var/learning.sqlite，可由 CNKG_LEARNING_DB 指定；
内容库 build/kg.sqlite 可重复重建，两者独立。学习记录页支持恢复与导出，
重复提交不会重复计分，旧内容版本不按新答案重算。
教师复核和试点操作见 [教师复核与小规模试点](docs/teacher-review-and-pilot.md)。

```
data/
  content/           显式登记的23册内容源（bx1 = 必修一，bx2 = 必修二）
  content_sources.json 来源、生成器及选项顺序转换的构建登记
  topics.json        知识点（477 个，全部 draft）
  dependencies.json  先修边（589 条，含 hard/soft 与理由）
  questions.json     自有题库（3568题：数值2257／短答161／选择1150；数值题强制带verify）
  questions_ext.json 可选外部题库，不随源码包分发，运行时按实际文件和证据策略统计
  stage_policy.json  三学段独立策略；顶部 `_口径` 定义三套统计口径，改动前先读
  standards.json     课标条款（78 条，含有定位的原文摘录与项目摘要；AI核对，待教师确认）
  outline.json       教材目录（23 册 129 章/单元 477 叶子 + 课时）
  outline.draft.json 抓取原始结果（build_outline.py 的输入）
  outline.override.json 人工核对的补充目录（8 册），优先于 draft
  teach/             350张完整卡（342核心+8工具）、50张速查参考
  learning_units/    10个细目标教学单元：解释、独立任务、过程量规及版本指纹
  projects/          原目录18项综合实践材料、步骤与量规
  curriculum_versions.json 旧基线与2025修订版的登记状态
  curriculum_comparisons/ 78项逐条对照、来源哈希、新旧定位及材料跟进
  manifest.json      计数 + 每个文件的 SHA-256
cnkg/
  graph.py           图结构：祖先集合、拓扑排序、课程顺序
  engine.py          诊断引擎：出题、判定、回溯、汇总
  learning.py        教学单元校验、内容指纹、安全题面与任务判分
  verify.py          verify 表达式沙箱（AST 白名单）
  store.py           SQLite 装载与递归 CTE 查询
schema/              三个 JSON Schema
tools/
  validate.py        全量校验（按 review_status 分级）
  fetch_toc.py       抓教材目录（小学/初中/高中三套解析规则）
  build_outline.py   目录草稿 -> outline.json
  scaffold.py        补骨架点 / 同步结构字段
  apply_content.py   按册合并内容（data/content/*.json -> 三个数据文件）
  build_db.py        建库 + manifest
  verify_questions.py 题库核验
  map_kc.py          外部知识点体系映射（为 XES3G5M 校准做准备）
  query.py           CLI：统计 / 祖先 / 路径 / 诊断 / 筛查
  simulate.py        蒙特卡洛校准：虚拟学生 + 判定准确率对比
  ladder_experiment.py 难度阶梯实验：多种子对照，判断补 standard 题值不值
  export_inbox.py    生成对外交接的 CSV 模板（inbox/）
  ingest.py          校验并吸收外部填好的 CSV
  balance_options.py 确定性排列选项显示位置，保留稳定选项ID及正确内容（见D19）
  rebuild_data.py    从登记源重建，检查全部字段一致性
  release_check.py   一次运行来源、内容、算术、回归及发布验收
  repair_answer_keys.py 答案键重建：按候选约束生成待确认清单
  apply_answer_key_fix.py 写回重建后的答案键并同步内容源
  ingest_bundle.py   接收外部回传包（证据分层，不改诊断数据）
  status_report.py   现状快照：覆盖/题库/复核/图/外部数据，输出 build/status.json
  check_round2.py    验收外部第二轮回传包
  build_review_page.py 生成题目复核工作台（web/static/review.html）
  find_duplicates.py 近似重复题检测（题干+选项 / 仅题干 两条通道）
  apply_graph_fix.py 按清单修正先修边，并同步内容源文件
tests/               核心回归与可选外部数据集成测试
web/                 本地 Web 服务与单页前端
build/               kg.sqlite、verification_report.json（可删除重建）
```

## 数据模型

```json
{
  "id": "m7a.5.2",
  "name": "解一元一次方程",
  "volume": "七上", "chapter": "第五章 一元一次方程", "section": "5.2 解一元一次方程",
  "grade": 7, "semester": "上",
  "domain": "数与代数", "theme": "方程与不等式", "type": "procedural",
  "description": "一般步骤：去分母、去括号、移项、合并同类项、系数化为 1。",
  "mastery_criteria": ["能按步骤解一元一次方程", "能发现并修正去分母时的漏乘错误"],
  "standards": [{"id": "cms-2022:4.NA.06", "confidence": "exact"}],
  "curriculum_version": "2024", "edition": "人教版", "review_status": "draft"
}
```

**id 规则**

| 学段 | 形式 | 例子 |
|---|---|---|
| 小学 | `m<年级><a/b>.<单元>.1` | `m1a.3.1` = 一上第三单元 |
| 初中 | `m<年级><a/b>.<章>.<节>[.<子节>]` | `m7a.5.2` |
| 高中 | `mh<册 1-5>.<章>.<节>[.<子节>]` | `mh5.5.1.2` = 选必三第 5 章 5.1.2 |

id 是唯一主键，跨学段重名不会冲突——按 name 做 join 必错。

先修边：`{topicId, prerequisiteId, strength: hard|soft, reason}`，读作"topicId 依赖 prerequisiteId"。
题目：`{id, topicId, difficulty, type, prompt, choices?, answer, verify?, explanation}`，数值题**必须**带 `verify`。

## 已知限制

**2026-10-24 答案键事故（已修）**：`balance_options.py` 旋转方向写反，216 道选择题的答案指向了错误选项；外部复核发现「答案与解析矛盾」后才追出。已全部重建、抽检 20/20 正确，并把穷举用例写进 `tests/test_balance.py`。这条留在这里而不是删掉：它说明「为了统计特征去移动正确项」有多危险，见 `docs/decisions.md` D19。

1. **粒度不齐**：小学 5 册到课时，二下/四上/五上/六上到节或单元，四下/五下/六下只到单元；初中九下到节，其余到小节。
2. **单元级点的内部顺序缺失**：如四下的「三角形」把特性、分类、内角和合在一个点里，无法定位到底哪一类不会。
3. **数据是 AI 生成初稿**（`review_status: draft`），先修边和题目未经一线教师审核。
4. **课标与摘要必须区分**：78条保留旧版 `verbatim_text` 摘录和定位，`summary` 仍是作者概述。2025修订已取得全文并完成这78项摘录的逐条AI对照及受影响材料修订；该范围不等于整部课标所有文字已做旧新认证。
5. **domain/theme/type 是关键词启发式**：骨架点的领域、主题、类型由章名/节名推断，填内容时必须复核。
6. **课时只对齐了初中**：小学、高中没有官方课时数据，字段留空；七下、八下各差 3 课时，记在 `allocated_hours` 未硬凑。

## 完成状态与质量边界

前三批依次修正来源与判定、教学与学生流程、覆盖统计与版本治理。本次继续补齐了当时列出的全部制作缺口：

| 验收范围 | 当前状态 |
| --- | --- |
| 核心完整讲解 | 342/342；274张缺失卡已补齐，旧卡逐目标增强 |
| 已登记核心目标材料 | 684/684，每项目标有具体解释、自检、参考过程与可观察检查点 |
| 练习数量 | 各核心点达到小学4／初中6／高中8道可用练习；工具点达到其登记目标 |
| 应用任务 | 原33个待补核心点已补66道任务；当前待办0 |
| 综合实践 | 原目录18/18都有具体活动材料；原15项待开发已完成 |
| 课标修订对照 | 原78项摘录全部完成2025版AI对照，相关内容变化已落实 |

总共保留350张完整卡、50张速查卡、10个细目标教学单元和76道分阶段任务。
684是已登记核心目标的分母，不用它代表整部课程所有可拆分的细技能。数值题和有限短答可自动检查；
证明、探究与建模过程采用开放自检及量规，不以一个数值答案宣布整个目标已掌握。

```bash
python tools/release_check.py
python tools/coverage_report.py --check --require-complete --output build/coverage-report.json
python tools/curriculum_compare.py
```

完整交付记录见 [项目完成记录](docs/completion-2026-10-26.md)；
课标范围与证据见 [版本工作流](docs/curriculum-version-workflow.md)。
原先的待办清单保存在有日期的历史记录中，不再作为当前状态展示。

**按用户要求暂缓两项：独立教师审核、真实学生教学效果验证。** 内容仍保留 `draft`，
相关审核与实测状态真实保留；它们不阻断本轮内容与技术验收。

外部题库默认不加载；竞赛板块候选不能自动成为课后推荐。缺失几何条件的tal-j-256已隔离。
设计难度仍属于作者设计等级；局部计算题没有凭借题量增加升级为整目标诊断证据。
具体可练／可诊断题量由 `tools/status_report.py` 按当前证据策略重算，证据不足时仍会明确提示。

## License

- **代码**（`cnkg/`、`tools/`、`web/`）：MIT。
- **本项目原创数据**（知识点表、先修关系、自有题、单点讲解卡、
  速查卡和原创学习任务）：CC BY 4.0，署名「cn-k12-kg」。
- **第三方数据不随仓库分发**：`data/questions_ext.json`（CMM / smse / 高考真题）
  的来源分散、许可未逐一确认，已在 `.gitignore` 中排除；重建方式见
  `data/README.md`。`data/challenge/tal.json` 来自 TAL-SCQ5K-CN（MIT），
  仅做格式转换，原题与答案未改动。
