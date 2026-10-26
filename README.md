# cn-k12-kg

中国 K12 数学知识图谱（自建数据）+ 可自动验算的题库 + 先修诊断引擎。

**当前状态：23 个册次、477 个教材导航节点已有描述与题目，内容仍为 draft。** 目录覆盖不等于完整教学、细技能评价或教师审核。整改进度及实际验收见 [三批整改记录](docs/improvement-batches-2026-10-26.md)。

这个项目**不是** `cn-math-taxonomy` 的复制品，也没有使用它的任何数据。只借鉴了字段结构和"2 题判定 + 先修回溯"的算法思路。原因见 [docs/decisions.md](docs/decisions.md)。

> **现状与后期计划**见 [`docs/roadmap.md`](docs/roadmap.md)：实测数字、能承诺的边界、M1—M5 分阶段计划与验收条件。
> 所有数字由 `python tools/status_report.py` 生成，可自己跑一遍核对。

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

**课程版本不是一套**：小学、初中是 2024 版新课标教材，高中仍是 2019 版（课标 2017 版 2020 修订）。每条知识点都带 `curriculum_version`，校验器会比对所在册次。

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

> 交接给他人或外部协作者时，先看 [`docs/handoff.md`](docs/handoff.md)：当前状态、我能做的、卡住的三件事、验收标准。
> 需要外部补的数据用 [`inbox/README.md`](inbox/README.md) 的模板对接收回。

## 快速开始

```bash
git clone https://github.com/ecofree/cn-k12-kg.git
cd cn-k12-kg

python tools/validate.py           # 结构、环、目录覆盖、题库可验算性
python tools/build_db.py           # 建 SQLite + 生成带 SHA-256 的 manifest
python tools/verify_questions.py   # 题库答案核验
python -m unittest discover -s tests -v

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
python tools/apply_content.py --file data/content/bx1.json   # 4. 合并某一册的内容
python tools/validate.py                       # 5. 校验
```

每一册的内容单独写在 `data/content/<册>.json` 里（知识点补丁 + 先修边 + 题目），再由 `apply_content.py` 幂等合并进三个主数据文件。这样一册内容可以单独 review、单独回滚，不会和别的册打架。

扩一册的顺序固定为：**目录 → 骨架点 → 描述与掌握标准 → 先修边 → 题目 → 校验**。

## Web 端（本地）

```bash
python web/server.py            # http://127.0.0.1:8765
```

- 首页提供细目标教学单元与教材目录搜索；每个教材点可分别进入讲解、练习与检查。
- 讲解包含直观、理由、适用条件、反例与分步例题；速查参考与完整讲解分别标注。
- 判题与会话状态由服务端维护；题面不下发 `answer`/`verify`/`explanation`。
- 练习即时反馈；检查、独立迁移和复测在整组完成后展示解析。过程题按量规等待复核，不自动判定掌握。
- 结果页区分补学线索、不稳、已有先修证据的起点和证据不足；可回看讲解并换题检查。
- 深链：`/?unit=fraction_equivalence` 打开单元，`/?teach=m7a.5.2` 看讲解，`/?target=m7a.5.2` 开测，`/?volume=七上` 整册抽查，`/?q=导数` 搜索。

## 目录结构

```
data/
  content/           按册撰写的内容（bx1 = 必修一，bx2 = 必修二），apply_content.py 的输入
  topics.json        知识点（477 个，全部 draft）
  dependencies.json  先修边（589 条，含 hard/soft 与理由）
  questions.json     自有题库（2873 题：数值 1884 / 短答 102 / 选择 887；数值题强制带 verify）
  questions_ext.json 外部题库（23313 题：可诊断 9005，其余按准入等级留在库内）
  stage_policy.json  三学段独立策略；顶部 `_口径` 定义三套统计口径，改动前先读
  standards.json     课标条款（78 条，含有定位的原文摘录与项目摘要；AI核对，待教师确认）
  outline.json       教材目录（23 册 129 章/单元 477 叶子 + 课时）
  outline.draft.json 抓取原始结果（build_outline.py 的输入）
  outline.override.json 人工核对的补充目录（8 册），优先于 draft
  learning_units/    细目标教学单元：解释、独立任务、过程量规及版本指纹
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
  balance_options.py 打散选择题答案键（注意：分布均衡不是质量指标，见 D19）
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
4. **课标与摘要必须区分**：78 条均有 `verbatim_text` 原文摘录与页码定位，`verbatim_checked_by=ai`；`summary` 仍是作者概述。2025 日常修订版需单独登记与核对，不能把旧映射自动认定为新版一致。
5. **domain/theme/type 是关键词启发式**：骨架点的领域、主题、类型由章名/节名推断，填内容时必须复核。
6. **课时只对齐了初中**：小学、高中没有官方课时数据，字段留空；七下、八下各差 3 课时，记在 `allocated_hours` 未硬凑。

## 改进顺序和质量边界

实施顺序为正确性与发布门槛、教学样板与学生流程、覆盖扩展与实证基础。
具体变更及待完成验收见 [三批整改记录](docs/improvement-batches-2026-10-26.md)。

开始整改时，上游有小学 46 张、七上 22 张核心讲解卡，另有 8 张旧工具卡；
初中还缺 97 张、高中还缺 144 张核心讲解卡。638 道应用题的 topic 覆盖，
只表示存在题目，不证明实际测量了完整目标，也没有替代教师审核。

外部题库默认不加载；可选数据是否存在、可用证据等级、实际进入题池的数量由
运行时策略决定，不能将一次本机的外部题量写成 fresh clone 的保证。
竞赛题保持独立阅读用途，只有明确目标关联时才可作为当前课程的拓展。

真实学生实测和教师独立审核仍需组织实施。保持 draft 状态，记录具体版本、
作者、审核者及证据类型；不得将 AI 复核或模拟学生写成教师/学生实测。

## License

- **代码**（`cnkg/`、`tools/`、`web/`）：MIT。
- **本项目原创数据**（知识点表、先修关系、自有题、单点讲解卡、
  速查卡和原创学习任务）：CC BY 4.0，署名「cn-k12-kg」。
- **第三方数据不随仓库分发**：`data/questions_ext.json`（CMM / smse / 高考真题）
  的来源分散、许可未逐一确认，已在 `.gitignore` 中排除；重建方式见
  `data/README.md`。`data/challenge/tal.json` 来自 TAL-SCQ5K-CN（MIT），
  仅做格式转换，原题与答案未改动。
