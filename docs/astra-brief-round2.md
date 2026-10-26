# 第二轮任务：K12 数学知识图谱（2026-10-24）

你上一轮回传的核查包已被全部采信。这份文档是第二轮要你做的事，附 `to-astra-round2.zip`。
**如果你只能做一件事，做 P0。**

---

## 0. 背景（一段话）

这是一个中国 K12 数学知识图谱：477 个知识点、570 条带 hard/soft 强度的先修边、1077 道题，按教材目录组织，用一个 2 题封顶的自适应引擎定位薄弱根因。上一轮你核对了 160 道题的抽样、865 个 XES3G5M KC、78 条课标、85 行教材目录。

**你的核查直接触发了一次重大修复**：你报的 5 条「答案与解析矛盾」追下去，发现我的选项打散脚本旋转方向写反，**216 道选择题的答案指错了选项**。现已全部重建并抽检。这是本轮所有变更的来源。

---

## 1. 上一轮结论的处理情况（供你确认）

| 你报的 | 数量 | 我的处理 |
|---|---|---|
| 复核 fail | 33 | 全部修正；逐条记在 `changes/changelog.json` 的 `correction_ids` |
| skip（缺选项/缺 accept） | 68 | **是我的导出缺陷**，已补 `review_sidecar.json` |
| 六上「生活中的负数」是活动 | 1 | 已改为非编号活动，圆恢复为第四单元 |
| KC 映射 181/865（20.9%） | — | 接受这个数字，入候选层，未用于校准 |
| 课标逐字核准 0/78 | — | 接受，`verbatim` 保持 false |
| `question_stats.csv` 为空 | — | 保持为空 |

**重要**：216 道题的答案键已改，`changes/answer_key_changes.csv` 列了每一道的新旧对照。凡在这份清单里的题，**你上一轮的结论一律不能继承**。

---

## 2. 本轮三件事

### P0（最高优先）· 100 条 KC 映射抽检

**输入**：`kc_review_100.csv`（100 行，来自你自己上一轮的固定种子抽样：high 57 条 + medium 43 条）
**参考**：`topics.csv`（477 个本地知识点，只读）

**要填的列**：

| 列 | 取值 | 要求 |
|---|---|---|
| `adjudication` | `accept` / `reject` / `uncertain` | 必填，不许空 |
| `relation` | `equivalent` / `narrower_or_partial` / `partial` / `unrelated` | 必填 |
| `reviewer_id` | 你的标识 | 必填 |
| `evidence` | 自由文本 | ≥20 字，**必须**引用 KC 全路径或题例 id 中的具体证据 |
| `note` | 自由文本 | 可选，写争议点 |

**判断要求**（这是本轮唯一需要你做语义判断的地方）：

1. **不能只看名字**。必须读 `kc_routes` 的完整层级路径。反例：KC 43「认识线段」位于「几何计数」路径下，不是基本线段概念。
2. `accept` 只表示「教学内容可对应」，**不表示**同年级、同难度、掌握等价。这三件事不要合并。
3. 内部矛盾要标出来：如果 KC 路径显示它是竞赛拓展内容，而 `topic_id` 指向课内基础点，标 `reject` 或 `uncertain` 并写明。
4. `relation` 与 `adjudication` 独立：可以 `accept` + `narrower_or_partial`（最常见的情形）。

**输出**：填好的 `kc_review_100.csv`，加一份 `kc_review_summary.json`：

```json
{
  "total": 100,
  "by_confidence": {"high": {"n": 57, "accept": 0, "reject": 0, "uncertain": 0},
                    "medium": {"n": 43, "accept": 0, "reject": 0, "uncertain": 0}},
  "by_relation": {"equivalent": 0, "narrower_or_partial": 0, "partial": 0, "unrelated": 0},
  "sampling_rule": "固定种子 20261024，high 抽 57/103、medium 抽 43/78",
  "limitations": ["……你看到的任何限制，如实写"]
}
```

**验收标准（我会跑）**：

- 100 行 `adjudication` 全部非空且在枚举内；
- 每条 `accept`/`reject` 的 `evidence` ≥20 字，且包含 `kc_routes` 片段或题例 id；
- `summary` 的分母与 57/43 一致；
- **不得**把这份抽检的 accept 率写成「映射准确率」而不说明抽样规则；不得用我的映射结果反过来证明一致率。

---

### P1 · 课标逐字核准（78 条）

**输入**：`standards.csv`（78 行，`current_summary` 是我写的概述，`verbatim_text` 为空）
**参考**：你上一轮生成的 `standards_index.json` 里的 `entries`（每条带 `source_id`、`source_url`、`printed_pages`、`pdf_pages_1based`、`locator`、`match_status`）

**要做**：对每条填 `verbatim_text` + `page_or_ref` + `verbatim`。

| 情况 | 怎么填 |
|---|---|
| 能拿到 PDF 且能逐字提取 | 填原文；`verbatim=true`；`page_or_ref` 写版本 + 印刷页 + PDF 页 |
| 是扫描件、需 OCR 但没有 OCR 能力 | `verbatim_text` 留空，`verbatim=false`，`page_or_ref` 末尾加 `[needs_ocr]` |
| 拿不到该版本 PDF | 同上，末尾加 `[not_accessible]` |

**验收标准**：

- 78 行每行要么有非空 `verbatim_text`，要么 `page_or_ref` 带 `[needs_ocr]` 或 `[not_accessible]`；
- `verbatim=true` 的行必须同时有 `verbatim_text` 和印刷页号；
- **不许**把「找到了对应条款」写成「已核准原文」——这是两个状态；
- 义教 2022 与 2025 日常修订版分开标注，不能把旧版内容标成新版。

---

### P2 · 160 题用完整选项复审

**输入**：`review.csv`（9 列协议不变）+ `review_sidecar.json`（**新增**：每题的全部选项、`option_id`、`correct_option_ids`、`accept`、`verify`、`full_question_sha256`）

上一轮 44 道选择题因缺选项、57 道短答因缺 accept 只能 skip。这次信息齐了，请重审。

**要填**：`verdict`（pass/fail/skip）+ `note`。
**判 fail 时**，`note` 必须以机器可读前缀开头：

```
correct_option_ids=o3; 其余选项的问题：……
```

- 用 `option_id`（`o1`…`o4`），**不要用 A/B/C/D**——选项会重排，字母不稳定；
- 若题目本身有缺陷（选项都不对、题干歧义），写 `correct_option_ids=none; 原因……`。

**验收标准**：

- 160 行 `verdict` 全部非空；
- 每个 fail 的 `note` 以 `correct_option_ids=` 开头；
- 引用 `full_question_sha256` 说明你审的是哪一版（避免审了旧版）。

---

## 3. 硬规则（违反了这批数据就不能用）

1. **`id` 一个字符都不能改。**「平均数」在小学和初中各有一个，靠名字回填必错。
2. **宁可留空，不要猜。** 空值我当"未提供"，填错我当"事实"。
3. **不改列名、不增删列。** 需要解释写在 `note` / `evidence`。
4. **不要用选项字母**，一律用 `option_id`。
5. **不要把 KC 的层级父子关系当成教学先修关系。** 那是分类从属，不是"没学会 A 就学不会 B"。

---

## 4. 明确不要做的事

- 不要重算或"修正"答案键——本轮已经重建，你若认为某题仍错，按 P2 的格式报，不要直接给新字母。
- 不要改 `topics.csv`（477 行只读）。
- 不要用外部数据（XES3G5M 的题目正确率、其他项目的数据）填 `question_stats.csv`。那个文件必须留空，直到有本项目真实作答。
- 不要为了凑覆盖率把 `uncertain` 改成 `accept`。20.9% 这个数字我已经接受，凑高没有意义。

---

## 5. 如果你做不到

在 `kc_review_summary.json` 的 `limitations`、或对应 CSV 行的 `note` 里**直接写明做不到的原因**，不要用编造的数据填补。

已知的三类合理"做不到"：

- 拿不到义教 2022 的原始 PDF（只有扫描件且无 OCR）→ 标 `[needs_ocr]`
- 拿不到 2024 审定版四下/五下/六下的教材页 → 在 `outline.csv` 对应行 `source` 里写 `[edition_unavailable]`
- 无法判断某个 KC 是否对应 → `adjudication=uncertain` + 写明缺什么信息才能判断

---

## 6. 交付物清单

| 文件 | 说明 |
|---|---|
| `kc_review_100.csv` | P0 填好的抽检表 |
| `kc_review_summary.json` | P0 汇总（按上面的 schema） |
| `standards.csv` | P1 填好的课标表 |
| `review.csv` | P2 填好的复核表 |
| `review_result.json`（可选） | 若你希望结构化返回 fail 的 `correct_option_ids` |
| `README.md` | 你做了什么、没做什么、为什么 |

返回时请说明**复核主体是人还是 AI**，我会分别标 `teacher-reviewed` / `ai-reviewed`，不混在一起。

**回传格式**：上面几个文件放进一个 `inbox/` 目录打包即可（文件名不要改）。

**回传前请自检**：包内已附 `check_round2.py`，它只读你这几个 CSV，不依赖我的项目：

```bash
python check_round2.py --bundle .
```

它会逐条告诉你缺什么（未填 adjudication、evidence 太短、fail 的 note 没有 `correct_option_ids=` 前缀等）。
退出码 0 表示可以交；非 0 时按提示补齐即可，**不要为了过检查去编内容**——做不到就标 `[needs_ocr]` / `uncertain`。

---

## 附：包内文件

```
kc_review_100.csv        100 行待填（P0）
topics.csv               477 个知识点，只读
standards.csv            78 行待填（P1）
review.csv               160 行待填（P2，9 列协议）
review_sidecar.json      160 题的完整选项/accept/哈希（P2 必读）
outline.csv              85 行目录（如需核对结构）
changes/answer_key_changes.csv   216 道题的新旧答案对照
changes/changelog.json   本轮全部变更与"旧结论不得继承"的题号清单
check_round2.py          回传前自检脚本（只读 CSV）
```
