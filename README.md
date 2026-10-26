# cn-k12-kg

中国 K12 数学知识图谱（自建数据）+ 可自动验算的题库 + 先修诊断引擎。

**当前状态：23 个册次、477 个知识点全部填完内容，无骨架残留。** 人教版数学小学 12 册、初中 6 册、高中 5 册已全部立好，每个点都具备描述、掌握标准、先修边与题目。

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

一份教材目录只告诉你"这学期学什么"，不告诉你"学不会时该往回补哪一层"。本项目把一章拆成可判定的最小知识点，用带 `hard/soft` 强度的先修边连成 DAG，再用尽量少的题定位薄弱根因：

- 两题都答对 → 判定掌握，并推断全部先修已掌握
- 一错一对（不论顺序） → "不稳"，并且**继续往回确认**（一次猜对不该把根因截断在中途）
- 两题都错 → "薄弱"，沿 **hard** 先修回溯（soft 只表示相关，不构成"不会它的原因"）

根因取"薄弱或不稳、且上游没有同样不稳的点"的那一层——只从薄弱里选，会漏掉"上游只有一个不稳点"这种最常见的情形。

**规则是量出来的，不是拍出来的**。`tools/simulate.py` 用已知掌握状态的虚拟学生跑蒙特卡洛（500 名学生，关键指标另用 5 个种子复核），有两种学生模型：

| 指标 | 布尔模型（会 / 不会） | 难度感知模型（会也可能做错难题） |
|---|---|---|
| 真实不会却被判"掌握"（漏诊） | 0.5% | 2.0% |
| 根因命中（沿因果链） | **83.6%** | **68.7%** |
| 三档判定准确率（会 / 不稳 / 薄弱） | — | 71.9% |
| 二档判定准确率（只分会 / 不会） | — | **85.5%** |
| 平均出题 | 5.0 题 | 6.0 题 |

**最重要的一条结论**：二档 85.5% 远高于三档 71.9%。也就是说，「会不会」判得准，「稳不稳」只是线索——这跟产品说明里的免责声明一致，不是客套话。

**两次口径修正**（都记在 `docs/decisions.md` D14）：

- 根因真值原来取"最高的未掌握祖先"，会把学生跳级掌握后、更远处的旧账也算成根因。改成"连续缺口链的顶端"后，无噪声条件下命中率从 87.4% 升到 98.5%——**引擎一直是对的，错的是尺子**。
- 难度感知模型下，能力过低的学生即使被标成"学过"也做不对基础题，真值改为"能演示才算会"，否则会冤枉引擎。

**难度阶梯不值得盲目补**。`tools/ladder_experiment.py` 用假设补题做了对照：给 449 个知识点各补一道 standard 题、答对后升一档确认，三档准确率反而从 71.9% 掉到 69.8%（±1.5% 噪声内），根因命中从 68.7% 掉到 66.9%，每题多问 1.1 题。所以默认关闭升档（`STEP_UP_ON_CORRECT = False`），也不写这 449 道题。

**题型结构仍然重要**：同一套规则，如果把所有题都当成四选一，漏诊率从 1.4% 升到 9.2%，平均猜测率暴露从 10.3% 升到 25.0%。所以每个知识点的入口题都保证至少一道非四选一，选题时优先低猜测题型。

这些是模型的敏感性分析，不是对真实学生的测量——虚拟学生的参数是假设，见 `docs/decisions.md` D12—D14。

> 交接给他人或外部协作者时，先看 [`docs/handoff.md`](docs/handoff.md)：当前状态、我能做的、卡住的三件事、验收标准。
> 需要外部补的数据用 [`inbox/README.md`](inbox/README.md) 的模板对接收回。

## 快速开始

```bash
cd F:/cn-k12-kg

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

- 首页按学段 / 册次分组：小学按单元平铺，初中高中按章折叠；骨架点显示「待建」且不可点
- 判题在服务端做，题面不下发 `answer`/`verify`/`explanation`
- 结果页给薄弱 / 不稳 / 根因 / 补学顺序 / 按章分布；未测过的祖先标「未测，需确认」
- 深链：`/?target=m7a.5.2` 开测，`/?volume=七上` 整册筛查，`/?q=导数` 直接带搜索
- 自测：`/?demo=wrong&target=m7a.5.2` 全部答错直达结果页

## 目录结构

```
data/
  content/           按册撰写的内容（bx1 = 必修一，bx2 = 必修二），apply_content.py 的输入
  topics.json        知识点（477 个，全部 draft）
  dependencies.json  先修边（586 条，含 hard/soft 与理由；跨学段 27 条）
  questions.json     自有题库（2585 题：数值 1598 / 短答 100 / 选择 887；数值题强制带 verify）
  questions_ext.json 外部题库（23313 题：可诊断 9005，其余按准入等级留在库内）
  stage_policy.json  三学段独立策略；顶部 `_口径` 定义三套统计口径，改动前先读
  standards.json     课标条款（78 条：小学 28 + 初中 33 + 高中 20，自撰摘要，标 verbatim=false）
  outline.json       教材目录（23 册 129 章/单元 477 叶子 + 课时）
  outline.draft.json 抓取原始结果（build_outline.py 的输入）
  outline.override.json 人工核对的补充目录（8 册），优先于 draft
  manifest.json      计数 + 每个文件的 SHA-256
cnkg/
  graph.py           图结构：祖先集合、拓扑排序、课程顺序
  engine.py          诊断引擎：出题、判定、回溯、汇总
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
tests/               304 个单元测试
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
4. **课标条款不是原文**：`standards.json` 是自撰摘要，`verbatim: false`、`page_or_ref: "待核"`。
5. **domain/theme/type 是关键词启发式**：骨架点的领域、主题、类型由章名/节名推断，填内容时必须复核。
6. **课时只对齐了初中**：小学、高中没有官方课时数据，字段留空；七下、八下各差 3 课时，记在 `allocated_hours` 未硬凑。

## 下一步

> 排序依据是 `docs/curriculum-redesign-2026-10-26.md`——**先读那份再动手**。
> 一句话：题库已经不是瓶颈，「讲」和「用」才是。小学、初中这一段讲和练都补齐了
> （小学 46 + 初中 119 = 165 个核心点，人各 2 道应用题），剩下的缺口全在高中 144 个点。

1. ~~小学 46 张核心卡~~ **已完成**：46/46，每张含「原理（why）」与
   「识别信号（when_to_use）」，`tools/teach_primary_{1..4}.py` 可复跑。
2. ~~小学速查卡~~ **已完成**：45 张覆盖 157 个工具点（连初高中共 50 张覆盖 168 个）。
   一张卡 = 一个「簇 × 册次」组，`covers` 自动填满，漏一个点就报错。
3. ~~小学与初中核心点的应用题~~ **已完成**：165 个点 / 350 道，逐题机器验算
   `verify`、证据等级、题干去重，见 `tools/write_application_items.py`。
   分工是「小学每点 2 道、不标 advanced；初中每点 standard + advanced 各一」。
   **剩余：高中 144 个核心点**（自有题口径；「99 个」那个数含外部题，别再用）。
4. **外部题按证据分级使用**：进诊断 7202 道，其中只有 689 道有独立证据
   （可复算或跨源互证），只有这 689 道能判"薄弱"。见 `cnkg/evidence.py`。
5. **升档：诊断不开，练习开**。按核心点算，自有库 standard 覆盖 7.1%、
   含外部证据题 61.8%；实测升档在诊断里是负收益（准确率 −1.3pp、漏诊 +4.3pp），
   所以诊断保持关闭，新增 `practice` 模式承载「基础对→应用追」。见 D43。
6. **TAL-SCQ5K-CN 已作为「挑战层」导入**：4912 道，按板块（不按课时级知识点）挂靠，
   309/309 核心点覆盖，`for_diagnosis: false`，永不进诊断。见 D41。
7. **真实答题数据与教师审核**：两者都是 0。现有 780 条复核全是 AI 自查且 100% pass，
   这不是质量证据，是自查偏松的信号。
8. **补齐 7 册的细目录**（二下、四上、五上、六上、四下、五下、六下）：等 2027 春版教材正式印发、电子课本网上线后，在 `data/outline.override.json` 里补叶子再跑 `build_outline.py` + `scaffold.py`，骨架自动扩容
9. **XES3G5M 校准**（865 个 KC 树、555 万条交互），先用 `tools/map_kc.py` 映射再抽检

## License

- **代码**（`cnkg/`、`tools/`、`web/`）：MIT。
- **本项目原创数据**（知识点表、先修关系、2585 道自有题、54 张核心讲解卡、
  50 张速查卡）：CC BY 4.0，署名「cn-k12-kg」。
- **第三方数据不随仓库分发**：`data/questions_ext.json`（CMM / smse / 高考真题）
  的来源分散、许可未逐一确认，已在 `.gitignore` 中排除；重建方式见
  `data/README.md`。`data/challenge/tal.json` 来自 TAL-SCQ5K-CN（MIT），
  仅做格式转换，原题与答案未改动。
