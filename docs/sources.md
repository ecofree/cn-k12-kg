# 当前来源与证据规则

教材导航的来源保存在 data/outline.json、outline.override.json 和各主题的教材版本字段；教材位置不等于独立能力身份。

课标摘录、定位与2025修订对照分别保存在 data/standards.json、data/curriculum_versions.json、data/curriculum_comparisons/。原始定位与来源哈希保留，不把AI作者核对冒充独立教师审核。

正式题库由 data/content_sources.json 登记的来源重建。自有内容、外部来源、题义/计算证据、可练用途和可诊断用途分别记录。来源不同不自动构成独立互证；数学符号、答案及来源谱系都必须核对。题源未检出只表示检索观察，不能推导“不考”。

当前作者来源为23册JSON与 `data/question_sources/application.json`，不再执行旧应用题生成器。489条退役题保留冻结档案与原ID，默认活跃题表排除这些记录；新增12条错目标题的逐题理由和原元数据保存在 `data/question_retirements.json`。源格式迁移与政策变更分别记录，详见 [authoring-workflow.md](authoring-workflow.md)。

许可与随包分发见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。运行与维护见 [handoff.md](handoff.md)，本轮实施与验收见 [quality-v2-implementation.md](quality-v2-implementation.md)。

旧来源研究与当时状态完整保存在 [历史快照](history/sources-before-quality-v2.md)。
