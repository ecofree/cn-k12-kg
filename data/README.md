# 数据目录说明

这个目录分三类：**自有数据**（自产题、知识点、讲解卡）、**派生数据**
（由脚本从自有数据算出来）、**外部数据**（他方题库，不入 git）。

## 自有内容与登记来源（入仓）

| 文件 | 内容与来源 |
| --- | --- |
| `content_sources.json` | 23册主动内容源、应用题生成器、确定性选项变换的显式登记 |
| `content/*.json` | 当前有效的各册主题、先修边和基础/补充题；历史补丁位于归档区，不参与构建 |
| `topics.json`、`dependencies.json`、`questions.json` | 从登记源生成的477个主题、589条先修边及自有题库；用 `rebuild_data.py` 全字段校验 |
| `standards.json` | 78项旧基线课标摘录、定位与作者概述；AI核对，独立审核另记 |
| `curriculum_versions.json`、`curriculum_comparisons/` | 已取得2025全文的哈希、78项逐条比较、新旧页码及已落实的材料修订 |
| `outline.json` | 实际教材目录树，23册；活动导航与知识点分开 |
| `topic_tiers.json` | 342个core、135个tool；工具点参考不算核心完整讲解 |
| `coverage_backlog.json` | 当前应用任务待办为空，已完成33项保留具体资源引用 |
| `teach/*.json` | 350张完整卡：342核心+8工具；684项核心目标逐项解释、自检和量规 |
| `teach/clusters/*.json` | 50张速查卡，覆盖135个工具点及保留的33个已升级核心点参考 |
| `learning_units/` | 10个深入单元、30个细目标、76个分阶段任务 |
| `projects/` | 18项原目录实践活动的材料、步骤与量规 |
| `reviews.json` | 带作者/审核类型的复核记录；不能将AI自审解释为教师审核 |
| `challenge/tal.json` | TAL-SCQ5K-CN竞赛题，许可见下；其数量不计入自有练习题总数 |
| `challenge/links.json`、`challenge/alignments.json` | 板块候选与内容绑定审核分开；候选不自动成为课后推荐 |

原创教学材料由AI编写并做程序检查与交叉复查，保留draft状态。
独立教师审核与真实学生验证按本轮用户要求暂缓。

## 重建与发布

```bash
python tools/rebuild_data.py --write
python tools/release_check.py
```

不要只手改 `questions.json`：它会与来源不一致。改应用题时同步相应生成器，
改按册题时同步登记的主动源。选项变换只改变显示位置，正确答案绑定稳定ID；
任何内容变化会改变审核指纹，不能自动继承旧批准。

`manifest.json`包含主题、题库、登记源、构建代码、讲解、图形、实践与课标对照的哈希。
`kc_lexicon.json`等外部召回辅助数据不自动产生独立的知识点对齐证据。

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
`tests/` 中依赖未提供外部题库的用例会明确跳过；自有内容和核心功能照常验收。

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
即使打开，也按 `cnkg.evidence.question_policy` 的当前证据和用途标记准入；材料存在与可用于诊断不是同一状态。
详见 `cnkg/evidence.py` 与 `docs/decisions.md` D39/D40。
