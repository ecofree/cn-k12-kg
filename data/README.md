# 数据目录说明

这个目录分三类：**自有数据**（自产题、知识点、讲解卡）、**派生数据**
（由脚本从自有数据算出来）、**外部数据**（他方题库，不入 git）。

## 自有数据（入仓）

| 文件 | 内容 | 谁写的 |
|---|---|---|
| `topics.json` | 477 个知识点，含册次、章节、描述、掌握标准 | 人工 + 目录对齐 |
| `dependencies.json` | 先修关系，诊断回溯靠它 | 人工 |
| `standards.json` | 课标条目与知识点映射 | 人工 |
| `outline.json` | 教材目录树 | `tools/build_outline.py` |
| `questions.json` | 2303 道自有题（基础题 + 记忆检验题 + 68 道应用题） | `tools/gen_*.py`、`tools/write_application_items.py` |
| `stage_policy.json` | 学段策略：题量、题型护栏、验收口径 | 人工 |
| `reviews.json`、`content/` | 人工复核记录与内容补充 | 人工 |
| `teach/*.json` | 309 张核心讲解卡 + 8 张早期工具点卡 | `tools/teach_primary_*.py`、`tools/write_teach_batch1.py` |
| `teach/clusters/*.json` | 50 张速查卡，覆盖 168 个工具点 | `tools/teach_brief_*.py` |
| `challenge/tal.json` | 4912 道竞赛挑战题（TAL-SCQ5K-CN，MIT） | `tools/import_tal.py` |
| `challenge/links.json` | 每个核心点挂 3 道挑战题的索引 | `tools/tal_link.py` |

## 派生数据（入仓，但可重建）

| 文件 | 由谁生成 | 说明 |
|---|---|---|
| `topic_tiers.json` | `tools/classify_tiers.py` | 309 核心点 / 168 工具点的分层结果 |
| `kc_lexicon.json` | `tools/build_lexicon.py` | 知识点词表，用于外部题召回 |

改了 `topics.json` 之后必须按顺序重跑：`classify_tiers.py` → `tal_link.py`，
否则分层与挑战题索引会跟知识点表脱节。

## 外部数据（**不入仓**）

| 文件 | 体积 | 为什么不入仓 |
|---|---|---|
| `questions_ext.json` | 64 MB | CMM / smse / 高考真题等第三方题库，许可见下方说明；体积也超出常规 git 仓库的合理范围 |
| `questions_ext.json.bak-*` | 56 MB | 批量改题前的备份，随时可删 |
| `external/astra_*` | 2 MB | 网页抓取的原始材料 |

**重建方式**（需要联网，按顺序）：

```bash
python tools/fetch_question_bank.py     # 下载原始题源到 inbox/dl/
python tools/import_question_bank.py    # 清洗、映射知识点，产出 questions_ext.json
python tools/import_smse.py             # 补充 smse 题
python tools/grade_ext.py --apply       # 五级证据分级，写回 evidence_level
python tools/validate.py                # 校验
```

缺失这些文件时：`tools/validate.py` 只校验自有题（给出警告），
`tests/` 里依赖外部题库的 8 个测试文件会自动跳过，不会报失败。

## 第三方数据许可

- **TAL-SCQ5K-CN**（`challenge/tal.json`）：MIT License，来自
  <https://huggingface.co/datasets/math-ai/TAL-SCQ5K-CN>。
  本仓库只做格式转换与板块挂靠，不改题干与答案。
- **CMM / smse / 高考真题**（`questions_ext.json`）：来源分散，许可状态未逐一确认，
  因此不随仓库分发。自用请自行下载。
- 讲解卡、自有题、知识点表为本项目原创，随仓库按 README 的 License 条款使用。

## 一条纪律

`questions_ext.json` 里的答案只有来源互证，没有独立复算。
所以它默认不进诊断（`CNKG_INCLUDE_EXTERNAL` 关闭），
即使打开也只加载 `evidence_level ∈ {verified, corroborated, explained}` 的题。
详见 `cnkg/evidence.py` 与 `docs/decisions.md` D39/D40。
