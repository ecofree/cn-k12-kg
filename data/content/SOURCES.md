# 自有数据的生效来源

`data/content_sources.json` 是唯一来源清单。23 册内容文件分别保存该册的完整主题记录、以该册主题为目标的先修边，以及非应用生成题。每个主题、题目 ID 和先修边只能有一个生效来源；构建发现重复会失败，不按文件名或调用顺序决定谁覆盖谁。

应用题由清单中登记的 `tools/write_application_items.py` 与 `tools/app_items/` 批次生成。迁移基线为 477 个主题、586 条边和 2,873 道自有题，其中 638 道为应用生成题。这些数是迁移记录，不是禁止内容继续增长的上限。生成器的证据分级和验算代码也列为输入。

## 编辑与重建

通常直接编辑登记的册次文件；应用生成题编辑对应的 `tools/app_items/` 批次。然后执行：

```bash
python tools/rebuild_data.py --write
python tools/rebuild_data.py --check
python tools/validate.py
python tools/verify_questions.py
```

`--write` 只根据生效来源重建 `topics.json`、`dependencies.json` 和 `questions.json`；构建不读取这三个旧产物。册次按清单顺序，册内 ID 按数字自然顺序排列。`--check` 比较所有字段，包括选项、等价答案、验算表达式和证据元数据；失败时只打印少量样本，可用 `--report build/source-differences.json` 保存完整差异。

不覆盖主数据的复现检查：

```bash
python tools/rebuild_data.py --output-dir build/rebuilt-a
python tools/rebuild_data.py --output-dir build/rebuilt-b
```

相同来源、相同 Python 版本产生逐字节相同的三个 JSON 文件。输出写为 UTF-8 / LF；发布清单的 `sha256-lf-v1` 将 CRLF/CR 规范为 LF 后计算 hash。它是文本内容 hash，不是原始下载字节的 hash。`build_db.py` 会先检查主数据与来源一致，再构建数据库，并将生效来源、生成器输入与派生产物的 hash 记入 `manifest.json`。本地时钟不进入清单；可通过 `SOURCE_DATE_EPOCH` 指定固定发布时间。

## 兼容旧维护工具

`apply_fixes.py` 同时更新主题库和清单登记的非应用题来源，不修改退休补丁。它拒绝只修改应用生成题的产物；此类修订应改 `app_items` 后重建。`apply_content.py --file ...` 只接受生效来源，执行完整构建；`--update` 不再允许回放历史补丁。

旧工具有时先改主 JSON。只有明确要把这次修订提升为来源时，才执行以下迁移操作：

```bash
python tools/write_application_items.py
python tools/rebuild_data.py --sync-sources --reason "说明本次已检查的修订"
python tools/rebuild_data.py --write
python tools/rebuild_data.py --check
```

`--sync-sources` 不是检查的一部分，必须显式调用并写明原因。应用生成题必须先与生成器逐字段一致，否则同步失败；不能将对生成题产物的直接修改吸收到册次来源。同步原因会打印在输出中，应随修订保留在提交说明或维护记录里。

## 历史补丁与质量边界

迁移以 `b771cac73206498dcbe83d5f39132586f93982cc` 主数据中的修订结果为基线，解决此前内容源中的 77 个重定义题号。迁移前文件保存在 `archive/pre_manifest_2026-10-26/`，清单保存其 LF hash。已退休的旧路径也明确列入 `retired_sources`：即使旧工作区仍留有该文件，也不读入、不回放；修改退休文件会触发错误，提醒改动真正的生效来源。未登记的新顶层 JSON 同样会触发错误。

本次规范化证明“由哪些文件可以重复构建当前数据”，不证明材料已经通过独立学科审阅。CI 分别运行来源一致性、结构校验、算式核验和回归测试。算式与答案一致、AI 复核记录和教师学科审核是不同证据；状态报告分别标示它们。外部数据未提供时报告“未提供/未测”，不伪装成已经测得零覆盖或零作答。
